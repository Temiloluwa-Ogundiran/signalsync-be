from datetime import datetime, timezone
from decimal import Decimal
from types import SimpleNamespace
import uuid

import pytest

from app.domains.copy_trading.assembly import ConversationCandidate, choose_conversation, route_deadline
from app.domains.copy_trading.execution import calculate_signal_volume, client_order_id_for_key
from app.domains.copy_trading.reconciliation import apply_broker_snapshot
from tests.fakes.fake_mt5_broker import FakeMt5Broker


def test_signal_to_broker_reliability_scenario() -> None:
    now = datetime(2026, 6, 22, tzinfo=timezone.utc)
    eur = ConversationCandidate(uuid.uuid4(), 10, 10, "EURUSD", "buy", now)

    gold_choice = choose_conversation(
        reply_to_message_id=None,
        symbol="XAUUSD",
        direction="sell",
        candidates=[eur],
    )
    assert gold_choice.selected is None
    assert route_deadline(now, 90) > route_deadline(now, 30)
    assert calculate_signal_volume(
        fixed_lot=Decimal("0.10"),
        take_profit_count=3,
        take_profit_mode="all",
        distribution="split_total",
    ) == Decimal("0.10")

    broker = FakeMt5Broker()
    client_order_id = client_order_id_for_key("telegram:10:route:one:leg:one")
    broker.timeout_after_accepting_once()
    with pytest.raises(TimeoutError):
        broker.submit(client_order_id, symbol="EURUSD", volume=Decimal("0.10"))

    replay = broker.submit(client_order_id, symbol="EURUSD", volume=Decimal("0.10"))
    assert replay["idempotent_replay"] is True
    assert broker.order_count == 1

    copied = SimpleNamespace(
        broker_position_id=str(replay["ticket"]),
        broker_order_id=None,
        lifecycle_state="open",
        original_volume=Decimal("0.10"),
        current_volume=Decimal("0.10"),
        stop_loss=None,
        take_profit=None,
        broker_synced_at=None,
    )
    broker.modify(replay["ticket"], stop_loss=Decimal("1.08"), take_profit=Decimal("1.12"))
    broker.partial_close(replay["ticket"], Decimal("0.04"))
    apply_broker_snapshot(
        copied,
        positions=broker.positions(),
        orders=[],
        observed_at=now,
        client_order_id=client_order_id,
    )

    assert copied.current_volume == Decimal("0.06")
    assert copied.stop_loss == Decimal("1.08")
    assert copied.take_profit == Decimal("1.12")
