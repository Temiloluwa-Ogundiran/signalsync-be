from types import SimpleNamespace
from unittest.mock import MagicMock, call, patch
import uuid
from datetime import datetime, timezone

import app.models  # noqa: F401
import pytest
from app.core.config import settings
from app.domains.copy_trading.models import (
    CopyActivityLevel,
    CopyRoute,
    CopyRouteState,
    CopyTradingConnection,
    ParsedAction,
    RouteAssemblyState,
    SignalConversation,
    SignalConversationState,
    TelegramSource,
    TelegramSourceType,
    TradeIntent,
    TradeIntentState,
)
from app.domains.copy_trading.generations import (
    mark_generation_completed,
    mark_generation_failed,
    mark_generation_submitted,
)
from app.domains.copy_trading.delivery import DeliveryDisposition
from app.domains.copy_trading.streams import CopyEvent, StreamName
from app.domains.copy_trading.workers import (
    _handle_deleted_message,
    _load_existing_conversation_for_correlation,
    _safe_activity_title,
    _reconciliation_accepts,
    _route_accepts_message,
    _release_source_lock,
    _select_copied_trade,
    _source_lock,
    expire_signal_threads,
)
from app.domains.copy_trading.metaapi_execution import execution_handler
from app.domains.copy_trading.metaapi_execution import (
    _connection_lock,
    _release_connection_lock,
    warm_active_copy_connections,
)
from redis.exceptions import LockNotOwnedError


def event(event_type: str, payload: dict) -> CopyEvent:
    return CopyEvent.new(
        stream=StreamName.execution_intents,
        event_type=event_type,
        correlation_id="corr-1",
        payload=payload,
        idempotency_key=f"{event_type}:1",
    )


def session_with_get(values: dict[type, object]) -> tuple[MagicMock, MagicMock]:
    session_local = MagicMock()
    db = session_local.return_value.__enter__.return_value
    db.get.side_effect = lambda model, _identifier: values.get(model)
    return session_local, db


def test_connection_lock_outlives_metaapi_connection_timeout() -> None:
    client = MagicMock()
    connection_id = uuid.uuid4()

    _connection_lock(client, connection_id)

    client.lock.assert_called_once_with(
        f"copy:connection-lock:{connection_id}",
        timeout=max(300, settings.METAAPI_CONNECTION_TIMEOUT_SECONDS * 2 + 60),
        blocking_timeout=10,
    )


def test_expired_connection_lock_does_not_fail_completed_delivery() -> None:
    lock = MagicMock()
    lock.release.side_effect = LockNotOwnedError("expired")

    _release_connection_lock(lock, connection_id=uuid.uuid4())

    lock.release.assert_called_once()


def test_source_lock_has_enough_lease_for_parser_and_database_work() -> None:
    client = MagicMock()
    source_id = uuid.uuid4()

    _source_lock(client, source_id)

    client.lock.assert_called_once_with(
        f"copy:source-lock:{source_id}",
        timeout=max(
            120,
            int(
                settings.COPY_TRADING_AI_TIMEOUT_SECONDS
                * (settings.COPY_TRADING_AI_MAX_RETRIES + 1)
                + 30
            ),
        ),
        blocking_timeout=5,
    )


def test_expired_source_lock_does_not_turn_success_into_retry() -> None:
    lock = MagicMock()
    lock.release.side_effect = LockNotOwnedError("expired")

    _release_source_lock(lock, source_id=uuid.uuid4())

    lock.release.assert_called_once()


@patch("app.domains.copy_trading.metaapi_execution.get_metaapi_runtime")
@patch("app.domains.copy_trading.metaapi_execution.SessionLocal")
@patch.object(settings, "COPY_TRADING_METAAPI_ENABLED", True)
def test_active_copy_connections_are_warmed_before_signal_delivery(
    session_local, get_runtime
) -> None:
    db = session_local.return_value.__enter__.return_value
    db.execute.return_value.scalars.return_value = ["account-1", "account-2"]
    runtime = get_runtime.return_value

    count = warm_active_copy_connections()

    assert count == 2
    assert runtime.acquire.call_args_list == [call("account-1"), call("account-2")]


def test_retried_signal_delivery_reuses_existing_conversation_by_correlation() -> None:
    source_id = uuid.uuid4()
    conversation = SimpleNamespace(
        id=uuid.uuid4(),
        source_id=source_id,
        correlation_id="duplicate-correlation",
        state=SignalConversationState.active,
    )
    db = MagicMock()
    db.execute.return_value.scalar_one_or_none.return_value = conversation

    result = _load_existing_conversation_for_correlation(
        db,
        source_id=source_id,
        correlation_id="duplicate-correlation",
    )

    assert result is conversation
    db.execute.assert_called_once()


@patch("app.domains.copy_trading.metaapi_execution.SessionLocal")
def test_execution_retries_when_intent_is_not_committed_yet(session_local):
    intent_id = uuid.uuid4()
    db = session_local.return_value.__enter__.return_value
    db.get.return_value = None

    result = execution_handler(event("intent.execute", {"intent_id": str(intent_id)}), MagicMock())

    assert result.disposition == DeliveryDisposition.retry
    assert result.error_code == "INTENT_NOT_VISIBLE"


def test_activity_title_is_truncated_to_database_limit() -> None:
    title = _safe_activity_title("Failed: " + ("x" * 500))

    assert title.startswith("Failed: ")
    assert len(title) <= 200


@patch("app.domains.copy_trading.metaapi_execution.SessionLocal")
def test_paused_route_fails_intent_without_crashing(session_local):
    intent_id = uuid.uuid4()
    intent = SimpleNamespace(
        id=intent_id,
        user_id=uuid.uuid4(),
        route_id=uuid.uuid4(),
        connection_id=uuid.uuid4(),
        parsed_action_id=uuid.uuid4(),
        state=TradeIntentState.created,
        last_error_code=None,
        broker_result={},
    )
    route = SimpleNamespace(state=CopyRouteState.paused)
    db = session_local.return_value.__enter__.return_value
    db.execute.return_value.scalar_one_or_none.return_value = None
    db.get.side_effect = lambda model, _identifier: {
        TradeIntent: intent,
        CopyRoute: route,
        CopyTradingConnection: SimpleNamespace(),
        ParsedAction: SimpleNamespace(),
    }.get(model)
    lock = MagicMock()
    lock.acquire.return_value = True
    client = MagicMock()
    client.lock.return_value = lock

    execution_handler(event("intent.execute", {"intent_id": str(intent_id)}), client)

    assert intent.state == TradeIntentState.failed
    assert intent.last_error_code == "AUTOMATION_PAUSED"
    db.commit.assert_called_once()
    lock.release.assert_called_once()


@patch.object(settings, "COPY_TRADING_GLOBAL_PAUSED", True)
@patch("app.domains.copy_trading.metaapi_execution.SessionLocal")
def test_server_global_pause_blocks_broker_execution(session_local):
    intent_id = uuid.uuid4()
    intent = SimpleNamespace(
        id=intent_id,
        user_id=uuid.uuid4(),
        route_id=uuid.uuid4(),
        connection_id=uuid.uuid4(),
        parsed_action_id=uuid.uuid4(),
        state=TradeIntentState.created,
        last_error_code=None,
        broker_result={},
    )
    route = SimpleNamespace(state=CopyRouteState.active)
    db = session_local.return_value.__enter__.return_value
    db.execute.return_value.scalar_one_or_none.return_value = None
    db.get.side_effect = lambda model, _identifier: {
        TradeIntent: intent,
        CopyRoute: route,
        CopyTradingConnection: SimpleNamespace(),
        ParsedAction: SimpleNamespace(),
    }.get(model)
    lock = MagicMock()
    lock.acquire.return_value = True
    client = MagicMock()
    client.lock.return_value = lock

    execution_handler(event("intent.execute", {"intent_id": str(intent_id)}), client)

    assert intent.state == TradeIntentState.failed
    assert intent.last_error_code == "AUTOMATION_PAUSED"


@patch("app.domains.copy_trading.workers.SessionLocal")
def test_deleted_message_handler_does_not_run_broker_reconciliation(session_local):
    db = session_local.return_value.__enter__.return_value
    db.execute.return_value.scalars.return_value = []

    _handle_deleted_message(
        CopyEvent.new(
            stream=StreamName.telegram_messages,
            event_type="message.deleted",
            correlation_id="corr-delete",
            payload={
                "source_id": str(uuid.uuid4()),
                "message_id": 42,
            },
            idempotency_key="delete:42",
        )
    )

    db.commit.assert_called_once()


def test_group_messages_default_to_admin_authors_only():
    source = SimpleNamespace(source_type=TelegramSourceType.group)
    strict_route = SimpleNamespace(process_all_group_authors=False)
    permissive_route = SimpleNamespace(process_all_group_authors=True)

    assert _route_accepts_message(
        strict_route,
        source,
        {"sender_is_admin": True},
    )
    assert not _route_accepts_message(
        strict_route,
        source,
        {"sender_is_admin": False},
    )
    assert _route_accepts_message(
        permissive_route,
        source,
        {"sender_is_admin": False},
    )


def test_channel_messages_are_not_subject_to_group_author_filter():
    source = SimpleNamespace(source_type=TelegramSourceType.channel)
    route = SimpleNamespace(process_all_group_authors=False)

    assert _route_accepts_message(route, source, {"sender_is_admin": False})


def test_management_action_uses_most_recent_matching_symbol():
    older_gold = SimpleNamespace(
        signal_symbol="XAUUSD",
        created_at=1,
    )
    recent_euro = SimpleNamespace(
        signal_symbol="EURUSD",
        created_at=3,
    )
    recent_gold = SimpleNamespace(
        signal_symbol="XAUUSDm",
        created_at=2,
    )

    selected = _select_copied_trade(
        [recent_euro, recent_gold, older_gold],
        {"symbol": "XAUUSD"},
    )

    assert selected is recent_gold


def test_management_action_without_symbol_is_ambiguous_with_multiple_trades():
    older = SimpleNamespace(signal_symbol="EURUSD", created_at=1)
    recent = SimpleNamespace(signal_symbol="GBPUSD", created_at=2)

    assert _select_copied_trade([older, recent], {}) is None


def test_management_action_without_symbol_uses_only_available_trade():
    trade = SimpleNamespace(signal_symbol="EURUSD", created_at=1)

    assert _select_copied_trade([trade], {}) is trade


@patch("app.domains.copy_trading.workers._activity")
@patch("app.domains.copy_trading.workers.SessionLocal")
def test_expired_incomplete_signal_is_marked_missed(
    session_local,
    record_activity,
):
    route_id = uuid.uuid4()
    conversation_id = uuid.uuid4()
    assembly = SimpleNamespace(
        route_id=route_id,
        conversation_id=conversation_id,
        state="assembling",
        context={"direction": "buy", "symbol": "XAUUSD"},
    )
    conversation = SimpleNamespace(
        id=conversation_id,
        correlation_id="corr-expired",
        legacy_thread_id=uuid.uuid4(),
    )
    route = SimpleNamespace()
    db = session_local.return_value.__enter__.return_value
    db.execute.return_value = MagicMock(scalars=MagicMock(return_value=[assembly]))
    db.get.side_effect = lambda model, identifier: (
        route if identifier == route_id else conversation
    )

    count = expire_signal_threads(datetime.now(timezone.utc))

    assert count == 1
    assert assembly.state.value == "expired"
    assert assembly.completed_at is not None
    assert assembly.terminal_reason == "REQUIRED_DETAILS_TIMEOUT"
    db.flush.assert_called_once()
    record_activity.assert_called_once_with(
        db,
        route=route,
        correlation_id="corr-expired",
        action="signal.expired",
        title="Incomplete signal expired",
        level=CopyActivityLevel.info,
        details={"direction": "buy", "symbol": "XAUUSD"},
    )
    db.commit.assert_called_once()


def test_reconciliation_does_not_confirm_open_from_old_route_history():
    intent = SimpleNamespace(
        request_payload={"action": "open_market"},
        submitted_at=datetime.fromtimestamp(200, tz=timezone.utc),
    )
    data = {
        "positions": [{"ticket": 77, "time": 100}],
        "orders": [],
        "history_orders": [],
        "deals": [],
    }

    assert not _reconciliation_accepts(intent, data, None)


def test_reconciliation_confirms_open_from_fresh_broker_evidence():
    intent = SimpleNamespace(
        request_payload={"action": "open_market"},
        submitted_at=datetime.fromtimestamp(200, tz=timezone.utc),
    )
    data = {
        "positions": [{"ticket": 77, "time": 201}],
        "orders": [],
        "history_orders": [],
        "deals": [],
    }

    assert _reconciliation_accepts(intent, data, None)


def test_submitted_opening_generation_enters_executing_state() -> None:
    intent_id = uuid.uuid4()
    now = datetime.now(timezone.utc)
    assembly = SimpleNamespace(
        state=RouteAssemblyState.ready,
        opening_intent_id=None,
        accepted_at=None,
        completed_at=None,
        terminal_reason=None,
    )

    mark_generation_submitted(assembly, intent_id, now)

    assert assembly.state == RouteAssemblyState.executing
    assert assembly.opening_intent_id == intent_id
    assert assembly.accepted_at == now


def test_confirmed_opening_generation_becomes_completed() -> None:
    now = datetime.now(timezone.utc)
    assembly = SimpleNamespace(
        state=RouteAssemblyState.executing,
        completed_at=None,
        terminal_reason="old",
    )

    mark_generation_completed(assembly, now)

    assert assembly.state == RouteAssemblyState.completed
    assert assembly.completed_at == now
    assert assembly.terminal_reason is None


def test_permanent_opening_failure_terminates_generation() -> None:
    now = datetime.now(timezone.utc)
    assembly = SimpleNamespace(
        state=RouteAssemblyState.executing,
        completed_at=None,
        terminal_reason=None,
    )

    mark_generation_failed(assembly, "INVALID_VOLUME", now)

    assert assembly.state == RouteAssemblyState.failed
    assert assembly.completed_at == now
    assert assembly.terminal_reason == "INVALID_VOLUME"
