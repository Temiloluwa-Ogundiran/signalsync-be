from decimal import Decimal
from unittest.mock import MagicMock, patch
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
    _learn_source_with_timeout,
    learning_handler,
    recover_learning_sources,
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

    learning_handler(event, MagicMock())

    mark_failed.assert_called_once_with(
        source_id,
        "Channel analysis failed. Try analyzing the channel again.",
        "Telegram history request failed",
    )


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
