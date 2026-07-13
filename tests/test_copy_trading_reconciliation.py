from datetime import datetime, timedelta, timezone
from decimal import Decimal
from types import SimpleNamespace

from app.domains.copy_trading.execution import client_order_id_for_key
from app.domains.copy_trading.reconciliation import (
    apply_broker_snapshot,
    broker_result_matches_intent,
)
from app.domains.copy_trading.metaapi_execution import (
    _reconcile_sweep_bucket,
    _uncertain_intent_expired,
)
from app.core.config import settings


def test_client_order_id_is_stable_compact_and_comment_safe() -> None:
    first = client_order_id_for_key("telegram:message:route:leg")
    second = client_order_id_for_key("telegram:message:route:leg")

    assert first == second
    assert len(first) == 20
    assert first.isalnum()


def test_reconciliation_rejects_a_different_client_order_id() -> None:
    intent = SimpleNamespace(
        client_order_id="expected",
        request_payload={"action": "open_market"},
    )

    assert broker_result_matches_intent(
        intent,
        {"client_order_id": "different", "positions": [{"ticket": 1}]},
        None,
    ) is False


def test_snapshot_updates_current_volume_and_protective_levels() -> None:
    trade = SimpleNamespace(
        broker_position_id="42",
        broker_order_id=None,
        lifecycle_state="open",
        current_volume=Decimal("0.10"),
        stop_loss=None,
        take_profit=None,
        broker_synced_at=None,
    )

    changed = apply_broker_snapshot(
        trade,
        positions=[{"ticket": 42, "volume": 0.04, "sl": 1.08, "tp": 1.12}],
        orders=[],
        observed_at=datetime(2026, 6, 22, tzinfo=timezone.utc),
    )

    assert changed is True
    assert trade.current_volume == Decimal("0.04")
    assert trade.stop_loss == Decimal("1.08")
    assert trade.take_profit == Decimal("1.12")
    assert trade.broker_synced_at is not None


def test_snapshot_understands_metaapi_streaming_position_shape() -> None:
    trade = SimpleNamespace(
        broker_position_id="2955992556",
        broker_order_id="2955992556",
        lifecycle_state="open",
        current_volume=Decimal("0.01"),
        stop_loss=None,
        take_profit=None,
        broker_synced_at=None,
    )

    changed = apply_broker_snapshot(
        trade,
        positions=[{
            "id": "2955992556",
            "volume": 0.01,
            "stopLoss": 1.145,
            "takeProfit": 1.13,
            "clientId": "STRATEGY_POSITION_ORDER",
        }],
        orders=[],
        observed_at=datetime.now(timezone.utc),
        client_order_id="STRATEGY_POSITION_ORDER",
    )

    assert changed is True
    assert trade.lifecycle_state == "open"
    assert trade.current_volume == Decimal("0.01")
    assert trade.stop_loss == Decimal("1.145")
    assert trade.take_profit == Decimal("1.13")


def test_missing_open_position_is_closed_by_complete_snapshot() -> None:
    trade = SimpleNamespace(
        broker_position_id="42",
        broker_order_id=None,
        lifecycle_state="open",
        current_volume=Decimal("0.10"),
        stop_loss=None,
        take_profit=None,
        broker_synced_at=None,
    )

    apply_broker_snapshot(
        trade,
        positions=[],
        orders=[],
        observed_at=datetime.now(timezone.utc),
    )

    assert trade.lifecycle_state == "closed"
    assert trade.current_volume == Decimal("0")


def test_pending_order_activation_tracks_the_new_position() -> None:
    trade = SimpleNamespace(
        broker_position_id=None,
        broker_order_id="77",
        lifecycle_state="pending",
        current_volume=Decimal("0.10"),
        stop_loss=None,
        take_profit=None,
        broker_synced_at=None,
    )

    apply_broker_snapshot(
        trade,
        positions=[{
            "ticket": 99,
            "comment": "cpid:CLIENT123",
            "volume": 0.10,
            "sl": 1.08,
            "tp": 1.12,
        }],
        orders=[],
        observed_at=datetime.now(timezone.utc),
        client_order_id="CLIENT123",
    )

    assert trade.lifecycle_state == "open"
    assert trade.broker_position_id == "99"


def test_pending_order_activation_matches_metaapi_client_id() -> None:
    trade = SimpleNamespace(
        broker_position_id=None,
        broker_order_id="77",
        lifecycle_state="pending",
        current_volume=Decimal("0.10"),
        stop_loss=None,
        take_profit=None,
        broker_synced_at=None,
    )

    apply_broker_snapshot(
        trade,
        positions=[{
            "id": "99",
            "clientId": "CLIENT123",
            "volume": 0.10,
            "stopLoss": 1.08,
            "takeProfit": 1.12,
        }],
        orders=[],
        observed_at=datetime.now(timezone.utc),
        client_order_id="CLIENT123",
    )

    assert trade.lifecycle_state == "open"
    assert trade.broker_position_id == "99"


def test_uncertain_intent_expires_at_configured_deadline() -> None:
    now = datetime.now(timezone.utc)
    intent = SimpleNamespace(
        created_at=now
        - timedelta(seconds=settings.COPY_TRADING_UNCERTAIN_MAX_AGE_SECONDS)
    )

    assert _uncertain_intent_expired(intent, now) is True


def test_reconcile_sweep_key_bucket_advances_for_repeated_checks() -> None:
    now = datetime(2026, 7, 11, tzinfo=timezone.utc)

    first = _reconcile_sweep_bucket(now)
    second = _reconcile_sweep_bucket(now + timedelta(seconds=30))

    assert second > first
