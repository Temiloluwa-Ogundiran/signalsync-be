from decimal import Decimal
import inspect
from types import SimpleNamespace
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
from app.domains.copy_trading.delivery import DeliveryResult
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
    TelegramConnectionState,
    TradeIntent,
)
from app.domains.copy_trading.worker_runtime import (
    StreamWorker,
    TelegramSessionRuntime,
    _phone_code_delivery_message,
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


def test_phone_auth_explains_when_telegram_delivers_code_in_app():
    sent_code = MagicMock()
    sent_code.type = type("SentCodeTypeApp", (), {})()

    message = _phone_code_delivery_message(sent_code)

    assert "verified Telegram chat" in message
    assert "not send this code by SMS" in message


def test_telegram_runtime_claims_single_owner_lease_before_connecting():
    runtime = object.__new__(TelegramSessionRuntime)
    runtime.redis = MagicMock()
    runtime.redis.set.return_value = True

    token = runtime._try_claim_runtime_ownership()

    assert token
    runtime.redis.set.assert_called_once_with(
        TelegramSessionRuntime.RUNTIME_OWNERSHIP_KEY,
        token,
        nx=True,
        ex=TelegramSessionRuntime.RUNTIME_OWNERSHIP_TTL_SECONDS,
    )


def test_telegram_runtime_does_not_connect_when_another_owner_is_active():
    runtime = object.__new__(TelegramSessionRuntime)
    runtime.redis = MagicMock()
    runtime.redis.set.return_value = False

    assert runtime._try_claim_runtime_ownership() is None


def test_telegram_runtime_renews_and_releases_only_its_own_lease():
    runtime = object.__new__(TelegramSessionRuntime)
    runtime.redis = MagicMock()
    runtime.redis.eval.side_effect = [1, 1]

    assert runtime._refresh_runtime_ownership("owner-token") is True
    runtime._release_runtime_ownership("owner-token")

    renew = runtime.redis.eval.call_args_list[0]
    release = runtime.redis.eval.call_args_list[1]
    assert renew.args[1:] == (
        1,
        TelegramSessionRuntime.RUNTIME_OWNERSHIP_KEY,
        "owner-token",
        TelegramSessionRuntime.RUNTIME_OWNERSHIP_TTL_SECONDS,
    )
    assert release.args[1:] == (
        1,
        TelegramSessionRuntime.RUNTIME_OWNERSHIP_KEY,
        "owner-token",
    )


@patch("telethon.sessions.StringSession.save", return_value="new-session")
@patch("app.domains.copy_trading.worker_runtime.SessionLocal")
def test_reauthentication_keeps_auth_status_when_temporary_connection_is_removed(
    session_local,
    _save_session,
    fernet_key,
):
    auth_id = uuid.uuid4()
    user_id = uuid.uuid4()
    existing_id = uuid.uuid4()
    temporary = SimpleNamespace(id=auth_id, user_id=user_id)
    existing = SimpleNamespace(
        id=existing_id,
        user_id=user_id,
        telegram_user_id=123,
        display_name=None,
        username=None,
        encrypted_session=None,
        state=TelegramConnectionState.reauthentication_required,
        reauthentication_reason="expired",
        last_heartbeat_at=None,
    )
    attempt = SimpleNamespace(connection_id=auth_id)
    db = session_local.return_value.__enter__.return_value
    db.get.return_value = temporary
    db.execute.side_effect = [
        MagicMock(scalar_one_or_none=MagicMock(return_value=existing)),
        MagicMock(scalar_one_or_none=MagicMock(return_value=attempt)),
    ]

    runtime = object.__new__(TelegramSessionRuntime)
    runtime.cipher = SessionCipher(fernet_key)
    runtime._auth_update = MagicMock()
    runtime._attach_updates = AsyncMock()
    runtime._refresh_dialog_cache = AsyncMock()
    client = MagicMock()
    client.get_me = AsyncMock(
        return_value=SimpleNamespace(
            id=123,
            first_name="Temiloluwa",
            last_name=None,
            username="nerdit0",
        )
    )

    __import__("asyncio").run(runtime._finalize(str(auth_id), client))

    assert attempt.connection_id == existing_id
    db.flush.assert_called_once()
    db.delete.assert_called_once_with(temporary)
    runtime._auth_update.assert_called_once_with(
        str(auth_id),
        state="ready",
        message="Telegram connected",
        connection_id=str(existing_id),
    )


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


def test_symbol_resolution_rejects_ambiguous_suffix_matches():
    symbols = [
        BrokerSymbol(name="XAUUSD.a", contract_size=Decimal("10"), spread=10, trade_mode=4),
        BrokerSymbol(name="XAUUSDm", contract_size=Decimal("100"), spread=20, trade_mode=4),
    ]

    with pytest.raises(ValueError, match="ambiguous"):
        resolve_symbol("XAUUSD", symbols)


def test_symbol_resolution_accepts_a_single_broker_suffix():
    symbols = [
        BrokerSymbol(name="XAUUSD.a", contract_size=Decimal("100"), trade_mode=4),
    ]

    assert resolve_symbol("XAUUSD", symbols).name == "XAUUSD.a"


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


def test_stream_worker_dead_letters_permanent_handler_failures_and_keeps_consuming():
    worker = object.__new__(StreamWorker)
    worker.client = MagicMock()
    worker.client.get.return_value = None
    worker.stream = StreamName.telegram_messages
    worker.group = "copy-signal"
    worker.handler = MagicMock(
        return_value=DeliveryResult.dead_letter("TEST_FAILURE", "boom")
    )
    worker._pending_attempts = MagicMock(return_value=1)
    worker._persist_dead_letter = MagicMock()
    event = CopyEvent.new(
        stream=StreamName.telegram_messages,
        event_type="message.created",
        correlation_id="corr-dead-letter",
        payload={"source_id": str(uuid.uuid4())},
        idempotency_key="signal:dead-letter",
    )

    worker._process_message("1-0", event.to_fields())

    worker._persist_dead_letter.assert_called_once()
    worker.client.xack.assert_called_once_with(
        StreamName.telegram_messages.value,
        "copy-signal",
        "1-0",
    )


def test_stream_worker_marks_event_processed_only_after_handler_succeeds():
    worker = object.__new__(StreamWorker)
    worker.client = MagicMock()
    worker.client.get.return_value = None
    worker.stream = StreamName.telegram_messages
    worker.group = "copy-signal"
    worker.handler = MagicMock(return_value=DeliveryResult.success())
    worker._pending_attempts = MagicMock(return_value=1)
    worker._persist_dead_letter = MagicMock()
    event = CopyEvent.new(
        stream=StreamName.telegram_messages,
        event_type="message.created",
        correlation_id="corr-success",
        payload={"source_id": str(uuid.uuid4())},
        idempotency_key="signal:success",
    )

    worker._process_message("2-0", event.to_fields())

    worker.handler.assert_called_once()
    worker.client.setex.assert_called_once_with(
        "copy:processed:copy-signal:signal:success",
        604800,
        event.event_id,
    )


def test_telegram_worker_refreshes_dialogs_from_live_session():
    connection_id = str(uuid.uuid4())
    request_id = str(uuid.uuid4())
    runtime = object.__new__(TelegramSessionRuntime)
    runtime.redis = MagicMock()
    cached_dialogs = (
        '[{"chat_id":-1000,"title":"Cached group","username":null,'
        '"source_type":"group","is_admin":false}]'
    )

    # dialogs key returns the cache; the freshness marker is absent so the
    # throttle lets the background refresh run.
    def _redis_get(key):
        if key.startswith("copy:telegram:dialogs-fresh:"):
            return None
        return cached_dialogs

    runtime.redis.get.side_effect = _redis_get
    runtime.dialog_refresh_inflight = set()
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
    assert "Cached group" in value


def test_telegram_worker_forced_dialog_refresh_returns_fresh_cache():
    connection_id = str(uuid.uuid4())
    request_id = str(uuid.uuid4())
    runtime = object.__new__(TelegramSessionRuntime)
    runtime.redis = MagicMock()
    runtime.dialog_refresh_inflight = set()
    runtime.clients = {
        f"connection:{connection_id}": MagicMock(),
    }
    fresh_dialogs = [
        {
            "chat_id": -1002,
            "title": "Joined today",
            "username": None,
            "source_type": "group",
            "is_admin": False,
        }
    ]

    async def _refresh(*_args, **_kwargs):
        runtime.redis.get.return_value = __import__("json").dumps(fresh_dialogs)

    runtime._refresh_dialog_cache = AsyncMock(side_effect=_refresh)
    event = CopyEvent.new(
        stream=StreamName.telegram_commands,
        event_type="dialogs.refresh",
        correlation_id=request_id,
        payload={
            "connection_id": connection_id,
            "request_id": request_id,
            "force_refresh": True,
        },
        idempotency_key=f"dialogs-refresh:{request_id}",
    )

    async def _run():
        await runtime.handle(event)
        await __import__("asyncio").sleep(0)

    __import__("asyncio").run(_run())

    runtime._refresh_dialog_cache.assert_awaited_once_with(
        connection_id,
        runtime.clients[f"connection:{connection_id}"],
        force=True,
    )
    key, ttl, value = runtime.redis.setex.call_args.args
    assert key == f"copy:telegram:dialogs-response:{request_id}"
    assert ttl == 30
    assert "Joined today" in value


@patch("app.domains.copy_trading.worker_runtime.SessionLocal")
def test_telegram_worker_refreshes_attached_connection_heartbeats(session_local):
    connection_id = uuid.uuid4()
    runtime = object.__new__(TelegramSessionRuntime)
    runtime.clients = {f"connection:{connection_id}": MagicMock()}
    db = session_local.return_value.__enter__.return_value

    runtime._refresh_connection_heartbeats()

    db.execute.assert_called_once()
    db.commit.assert_called_once()


@patch("app.domains.copy_trading.worker_runtime.SessionLocal")
def test_telegram_worker_schedules_automatic_reconnect_for_transient_disconnect(session_local):
    connection_id = uuid.uuid4()
    runtime = object.__new__(TelegramSessionRuntime)
    client = MagicMock()
    client.is_connected.return_value = False
    client.disconnect = AsyncMock()
    runtime.clients = {f"connection:{connection_id}": client}
    runtime.message_publishers = {str(connection_id): AsyncMock()}
    connection = MagicMock()
    db = session_local.return_value.__enter__.return_value
    db.get.return_value = connection

    __import__("asyncio").run(runtime._validate_connection_health())

    assert runtime.clients == {}
    assert runtime.message_publishers == {}
    assert connection.state == TelegramConnectionState.disconnected
    assert "reconnecting automatically" in connection.reauthentication_reason
    db.commit.assert_called_once()


@patch("app.domains.copy_trading.worker_runtime.SessionLocal")
def test_telegram_worker_only_requests_reauthentication_when_authorization_is_invalid(
    session_local,
):
    connection_id = uuid.uuid4()
    runtime = object.__new__(TelegramSessionRuntime)
    client = MagicMock()
    client.is_connected.return_value = True
    client.is_user_authorized = AsyncMock(return_value=False)
    client.disconnect = AsyncMock()
    runtime.clients = {f"connection:{connection_id}": client}
    runtime.message_publishers = {str(connection_id): AsyncMock()}
    connection = MagicMock()
    db = session_local.return_value.__enter__.return_value
    db.get.return_value = connection

    __import__("asyncio").run(runtime._validate_connection_health())

    assert connection.state == TelegramConnectionState.reauthentication_required
    assert "Reconnect Telegram" in connection.reauthentication_reason
    db.commit.assert_called_once()


@patch("telethon.TelegramClient")
@patch("app.domains.copy_trading.worker_runtime.SessionLocal")
def test_telegram_restore_keeps_saved_session_for_transient_network_failure(
    session_local,
    telegram_client,
):
    connection = SimpleNamespace(
        id=uuid.uuid4(),
        encrypted_session="encrypted-session",
        state=TelegramConnectionState.ready,
        reauthentication_reason=None,
    )
    db = session_local.return_value.__enter__.return_value
    db.execute.return_value.scalars.return_value = [connection]
    db.get.return_value = connection
    client = telegram_client.return_value
    client.connect = AsyncMock(side_effect=ConnectionError("network unavailable"))
    client.disconnect = AsyncMock()
    runtime = object.__new__(TelegramSessionRuntime)
    runtime.clients = {}
    runtime.cipher = MagicMock()
    runtime.cipher.decrypt.return_value = ""
    runtime._attach_updates = AsyncMock()

    __import__("asyncio").run(runtime.restore())

    assert connection.state == TelegramConnectionState.disconnected
    assert "reconnecting automatically" in connection.reauthentication_reason
    runtime._attach_updates.assert_not_awaited()


@patch("telethon.TelegramClient")
@patch("app.domains.copy_trading.worker_runtime.SessionLocal")
def test_telegram_restore_reuses_saved_session_and_returns_ready(
    session_local,
    telegram_client,
):
    connection = SimpleNamespace(
        id=uuid.uuid4(),
        encrypted_session="encrypted-session",
        state=TelegramConnectionState.disconnected,
        reauthentication_reason="reconnecting",
        last_heartbeat_at=None,
    )
    db = session_local.return_value.__enter__.return_value
    db.execute.return_value.scalars.return_value = [connection]
    db.get.return_value = connection
    client = telegram_client.return_value
    client.connect = AsyncMock()
    client.is_user_authorized = AsyncMock(return_value=True)
    runtime = object.__new__(TelegramSessionRuntime)
    runtime.clients = {}
    runtime.cipher = MagicMock()
    runtime.cipher.decrypt.return_value = ""
    runtime._attach_updates = AsyncMock()

    __import__("asyncio").run(runtime.restore())

    runtime._attach_updates.assert_awaited_once_with(str(connection.id), client)
    assert connection.state == TelegramConnectionState.ready
    assert connection.reauthentication_reason is None
    assert connection.last_heartbeat_at is not None


def test_telegram_worker_recovers_missed_basic_group_messages_in_order():
    connection_id = str(uuid.uuid4())
    source_id = str(uuid.uuid4())
    runtime = object.__new__(TelegramSessionRuntime)
    runtime.redis = MagicMock()
    runtime.redis.get.return_value = "94926"
    runtime.source_recovery_cache_at = float("inf")
    runtime.source_recovery_cache = {
        connection_id: [(source_id, -5572947762)],
    }
    publisher = AsyncMock()
    client = MagicMock()
    client.is_connected.return_value = True

    async def messages(*_args, **_kwargs):
        for message_id in (94927, 94928, 94929):
            message = MagicMock()
            message.id = message_id
            yield message

    client.iter_messages = messages
    runtime.clients = {f"connection:{connection_id}": client}
    runtime.message_publishers = {connection_id: publisher}

    recovered = __import__("asyncio").run(
        runtime._recover_missed_source_messages(basic_groups_only=True)
    )

    assert recovered == 3
    assert [call.args[1].id for call in publisher.await_args_list] == [
        94927,
        94928,
        94929,
    ]


def test_telegram_fast_recovery_leaves_channels_event_driven():
    connection_id = str(uuid.uuid4())
    runtime = object.__new__(TelegramSessionRuntime)
    runtime.redis = MagicMock()
    runtime.source_recovery_cache_at = float("inf")
    runtime.source_recovery_cache = {
        connection_id: [(str(uuid.uuid4()), -1005572947762)],
    }
    client = MagicMock()
    client.is_connected.return_value = True
    client.iter_messages = MagicMock()
    runtime.clients = {f"connection:{connection_id}": client}
    runtime.message_publishers = {connection_id: AsyncMock()}

    recovered = __import__("asyncio").run(
        runtime._recover_missed_source_messages(basic_groups_only=True)
    )

    assert recovered == 0
    client.iter_messages.assert_not_called()


def test_telegram_restore_does_not_compete_with_signal_recovery():
    source = inspect.getsource(TelegramSessionRuntime.restore)

    assert "catch_up" not in source
    assert "_refresh_dialog_cache" not in source


@pytest.fixture
def fernet_key():
    return "roLlHRZXRXpcMgc52s8FRneJOfs52P7G98Ibee1w-Uo="
