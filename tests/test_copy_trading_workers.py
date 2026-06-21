from types import SimpleNamespace
from unittest.mock import MagicMock, patch
import uuid
from datetime import datetime, timezone

import app.models  # noqa: F401
from app.domains.copy_trading.models import (
    CopyRoute,
    CopyRouteState,
    ParsedAction,
    TelegramSource,
    TelegramSourceType,
    TradeIntent,
    TradeIntentState,
)
from app.domains.copy_trading.streams import CopyEvent, StreamName
from app.domains.copy_trading.workers import (
    _handle_deleted_message,
    _reconcile_intent,
    _reconciliation_accepts,
    _route_accepts_message,
    _select_copied_trade,
    execution_handler,
)
from app.domains.accounts.models import TradingAccount


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


@patch("app.domains.copy_trading.workers.SessionLocal")
def test_paused_route_fails_intent_without_crashing(session_local):
    intent_id = uuid.uuid4()
    intent = SimpleNamespace(
        id=intent_id,
        user_id=uuid.uuid4(),
        route_id=uuid.uuid4(),
        account_id=uuid.uuid4(),
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
        TradingAccount: SimpleNamespace(),
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


@patch("app.domains.copy_trading.workers.RedisStreamBus")
@patch("app.domains.copy_trading.workers._submit_mt5")
@patch("app.domains.copy_trading.workers.decrypt_secret", return_value="secret")
@patch("app.domains.copy_trading.workers.SessionLocal")
def test_reconcile_confirms_uncertain_intent(
    session_local,
    _decrypt,
    submit_mt5,
    bus_class,
):
    intent_id = uuid.uuid4()
    intent = SimpleNamespace(
        id=intent_id,
        route_id=uuid.uuid4(),
        account_id=uuid.uuid4(),
        state=TradeIntentState.uncertain,
        request_payload={"action": "open_market", "symbol": "EURUSD"},
        broker_result={},
        resolved_at=None,
        attempt_count=1,
    )
    route = SimpleNamespace(
        id=intent.route_id,
        user_id=uuid.uuid4(),
        source_id=uuid.uuid4(),
        target_account_id=intent.account_id,
        magic_number=9001,
    )
    account = SimpleNamespace(
        broker_login="123",
        broker_server="Broker-Server",
        encrypted_trader_password="encrypted",
        encrypted_investor_password=None,
    )
    db = session_local.return_value.__enter__.return_value
    db.get.side_effect = lambda model, _identifier: {
        TradeIntent: intent,
        CopyRoute: route,
        TradingAccount: account,
    }.get(model)
    submit_mt5.return_value = {
        "data": {
            "positions": [{"ticket": 77, "magic": 9001}],
            "orders": [],
            "history_orders": [],
            "deals": [],
        }
    }

    _reconcile_intent(
        event("intent.reconcile", {"intent_id": str(intent_id)}),
        MagicMock(),
    )

    assert intent.state == TradeIntentState.confirmed
    assert intent.broker_result["positions"][0]["ticket"] == 77
    assert intent.resolved_at is not None
    submit_mt5.assert_called_once()
    bus_class.return_value.publish.assert_not_called()


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


def test_management_action_without_symbol_uses_most_recent_trade():
    older = SimpleNamespace(signal_symbol="EURUSD", created_at=1)
    recent = SimpleNamespace(signal_symbol="GBPUSD", created_at=2)

    assert _select_copied_trade([older, recent], {}) is recent


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
