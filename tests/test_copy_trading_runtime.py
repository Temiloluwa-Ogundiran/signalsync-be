from decimal import Decimal
from unittest.mock import AsyncMock, MagicMock, patch
import uuid

import pytest

from app.domains.copy_trading.engine import (
    ParsedSignal,
    RouteExecutionPolicy,
    SignalAction,
    build_tp_legs,
    validate_signal,
)
from app.domains.copy_trading.security import SessionCipher
from app.domains.copy_trading.streams import CopyEvent, StreamName
from app.domains.copy_trading.symbols import BrokerSymbol, resolve_symbol
from app.domains.copy_trading.models import (
    AutomationConfidence,
    ChannelMessageSample,
    ChannelProfile,
    CopiedTrade,
    ParsedAction,
    SignalThread,
    SymbolMapping,
    TradeIntent,
    TelegramSourceState,
)
from app.domains.copy_trading.worker_runtime import (
    _build_learning_prompt,
    _learning_source_outcome,
    _learn_source_with_timeout,
    learning_handler,
    purge_expired_samples,
    recover_learning_sources,
    StreamWorker,
    TelegramSessionRuntime,
)


def test_copy_event_round_trips_with_stable_identity():
    event = CopyEvent.new(
        stream=StreamName.telegram_messages,
        event_type="message.created",
        correlation_id="corr-1",
        payload={"chat_id": -1001, "message_id": 42},
        idempotency_key="connection:chat:42",
    )

    restored = CopyEvent.from_fields(event.to_fields())

    assert restored == event
    assert restored.idempotency_key == "connection:chat:42"


def test_session_cipher_encrypts_and_round_trips_telegram_session(fernet_key):
    cipher = SessionCipher(fernet_key)

    encrypted = cipher.encrypt("telegram-string-session")

    assert encrypted != "telegram-string-session"
    assert cipher.decrypt(encrypted) == "telegram-string-session"


def test_signal_validation_rejects_stale_market_signal():
    signal = ParsedSignal(
        action=SignalAction.open_market,
        symbol="XAUUSD",
        direction="buy",
        stop_loss=Decimal("2310"),
        take_profits=[Decimal("2350")],
        age_seconds=31,
        confidence=0.99,
    )

    result = validate_signal(signal, RouteExecutionPolicy())

    assert result.accepted is False
    assert result.reason == "Signal is too old for immediate entry."


def test_split_tp_legs_preserves_total_lot():
    legs = build_tp_legs(
        fixed_lot=Decimal("0.09"),
        take_profits=[Decimal("1.1"), Decimal("1.2"), Decimal("1.3")],
        mode="all",
        distribution="split_total",
    )

    assert [leg.lot for leg in legs] == [Decimal("0.03")] * 3
    assert sum(leg.lot for leg in legs) == Decimal("0.09")


def test_symbol_resolution_uses_largest_contract_for_equal_normalized_match():
    symbols = [
        BrokerSymbol(name="XAUUSD.a", contract_size=Decimal("10"), spread=10, trade_mode=4),
        BrokerSymbol(name="XAUUSDm", contract_size=Decimal("100"), spread=20, trade_mode=4),
    ]

    selected = resolve_symbol("XAUUSD", symbols)

    assert selected.name == "XAUUSDm"


def test_complete_copy_trading_domain_tables_are_declared():
    assert {model.__tablename__ for model in (
        ChannelProfile,
        ChannelMessageSample,
        SignalThread,
        ParsedAction,
        TradeIntent,
        CopiedTrade,
        SymbolMapping,
    )} == {
        "channel_profiles",
        "channel_message_samples",
        "signal_threads",
        "parsed_actions",
        "trade_intents",
        "copied_trades",
        "symbol_mappings",
    }


def test_learning_prompt_is_bounded_for_busy_groups():
    samples = [
        {"message_id": index, "text": "X" * 4000, "date": "2026-06-21T00:00:00+00:00"}
        for index in range(250)
    ]

    prompt = _build_learning_prompt(samples)

    assert len(prompt) <= 60_000
    assert '"message_id": 0' in prompt


def test_low_confidence_learning_is_advisory_not_blocking():
    state, reason = _learning_source_outcome(
        confidence=AutomationConfidence.low,
        image_primary=False,
    )

    assert state == TelegramSourceState.ready
    assert reason is None


def test_image_primary_learning_remains_unsupported():
    state, reason = _learning_source_outcome(
        confidence=AutomationConfidence.high,
        image_primary=True,
    )

    assert state == TelegramSourceState.unsupported
    assert "image signals" in reason.lower()


def test_stream_worker_dead_letters_handler_failures_and_keeps_consuming():
    worker = object.__new__(StreamWorker)
    worker.client = MagicMock()
    worker.client.get.return_value = None
    worker.stream = StreamName.learning_jobs
    worker.group = "copy-learning"
    worker.handler = MagicMock(side_effect=RuntimeError("boom"))
    event = CopyEvent.new(
        stream=StreamName.learning_jobs,
        event_type="source.learn",
        correlation_id="corr-dead-letter",
        payload={"source_id": str(uuid.uuid4())},
        idempotency_key="learn:dead-letter",
    )

    worker._process_message("1-0", event.to_fields())

    worker.client.xadd.assert_called_once()
    stream, fields = worker.client.xadd.call_args.args
    assert stream == StreamName.dead_letters.value
    assert fields["error"] == "boom"
    worker.client.xack.assert_called_once_with(
        StreamName.learning_jobs.value,
        "copy-learning",
        "1-0",
    )


def test_stream_worker_marks_event_processed_only_after_handler_succeeds():
    worker = object.__new__(StreamWorker)
    worker.client = MagicMock()
    worker.client.get.return_value = None
    worker.stream = StreamName.learning_jobs
    worker.group = "copy-learning"
    worker.handler = MagicMock()
    event = CopyEvent.new(
        stream=StreamName.learning_jobs,
        event_type="source.learn",
        correlation_id="corr-success",
        payload={"source_id": str(uuid.uuid4())},
        idempotency_key="learn:success",
    )

    worker._process_message("2-0", event.to_fields())

    worker.handler.assert_called_once()
    worker.client.setex.assert_called_once_with(
        "copy:processed:copy-learning:learn:success",
        604800,
        event.event_id,
    )


def test_telegram_worker_refreshes_dialogs_from_live_session():
    connection_id = str(uuid.uuid4())
    request_id = str(uuid.uuid4())
    runtime = object.__new__(TelegramSessionRuntime)
    runtime.redis = MagicMock()
    runtime.clients = {
        f"connection:{connection_id}": MagicMock(),
    }
    runtime._cache_dialogs = AsyncMock(
        return_value=[
            {
                "chat_id": -1001,
                "title": "Joined today",
                "username": None,
                "source_type": "group",
                "is_admin": False,
            }
        ]
    )
    event = CopyEvent.new(
        stream=StreamName.telegram_commands,
        event_type="dialogs.refresh",
        correlation_id=request_id,
        payload={
            "connection_id": connection_id,
            "request_id": request_id,
        },
        idempotency_key=f"dialogs-refresh:{request_id}",
    )

    __import__("asyncio").run(runtime.handle(event))

    runtime._cache_dialogs.assert_awaited_once_with(
        connection_id,
        runtime.clients[f"connection:{connection_id}"],
    )
    key, ttl, value = runtime.redis.setex.call_args.args
    assert key == f"copy:telegram:dialogs-response:{request_id}"
    assert ttl == 30
    assert "Joined today" in value


@patch("app.domains.copy_trading.worker_runtime._mark_learning_failed")
@patch("app.domains.copy_trading.worker_runtime.asyncio.run")
def test_learning_handler_marks_source_failed_instead_of_crashing(run, mark_failed):
    source_id = uuid.uuid4()
    def fail(coroutine):
        coroutine.close()
        raise RuntimeError("Telegram history request failed")

    run.side_effect = fail
    event = CopyEvent.new(
        stream=StreamName.learning_jobs,
        event_type="source.learn",
        correlation_id="learn-1",
        payload={"source_id": str(source_id)},
        idempotency_key=f"learn:{source_id}",
    )

    client = MagicMock()
    client.lock.return_value.acquire.return_value = True

    learning_handler(event, client)

    mark_failed.assert_called_once_with(
        source_id,
        "Channel analysis failed. Try analyzing the channel again.",
        "Telegram history request failed",
    )
    client.lock.return_value.release.assert_called_once()


@patch("app.domains.copy_trading.worker_runtime.SessionLocal")
def test_expired_learning_samples_are_purged(session_local):
    db = session_local.return_value.__enter__.return_value

    purge_expired_samples()

    db.execute.assert_called_once()
    statement = str(db.execute.call_args.args[0])
    assert "DELETE FROM channel_message_samples" in statement
    assert "expires_at" in statement
    db.commit.assert_called_once()


@patch("app.domains.copy_trading.worker_runtime.RedisStreamBus")
@patch("app.domains.copy_trading.worker_runtime.SessionLocal")
def test_recover_learning_sources_republishes_stranded_jobs(session_local, bus_class):
    source = MagicMock(id=uuid.uuid4(), state=TelegramSourceState.learning)
    session_local.return_value.__enter__.return_value.execute.return_value.scalars.return_value = [source]
    bus = bus_class.return_value

    recover_learning_sources(MagicMock())

    published = bus.publish.call_args.args[0]
    assert published.event_type == "source.learn"
    assert published.payload == {"source_id": str(source.id)}
    assert published.idempotency_key.startswith(f"recover-learn:{source.id}:")


@patch("app.domains.copy_trading.worker_runtime._learn_source")
def test_learning_timeout_prevents_indefinite_loading(learn_source):
    async def never_finishes(_source_id):
        await __import__("asyncio").sleep(60)

    learn_source.side_effect = never_finishes

    with pytest.raises(TimeoutError):
        __import__("asyncio").run(
            _learn_source_with_timeout(uuid.uuid4(), timeout_seconds=0.01)
        )


@pytest.fixture
def fernet_key():
    return "roLlHRZXRXpcMgc52s8FRneJOfs52P7G98Ibee1w-Uo="
