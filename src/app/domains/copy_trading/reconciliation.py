from datetime import datetime
from decimal import Decimal

from app.domains.copy_trading.engine import SignalAction


OPEN_ACTIONS = {
    SignalAction.open_market.value,
    SignalAction.place_pending.value,
    SignalAction.additional_tp.value,
}


def broker_result_matches_intent(intent, data: dict, copied) -> bool:
    client_order_id = getattr(intent, "client_order_id", None)
    if client_order_id:
        if data.get("client_order_id") != client_order_id:
            return False
    action = intent.request_payload.get("action")
    positions = data.get("positions") or []
    orders = data.get("orders") or []
    history_orders = data.get("history_orders") or []
    deals = data.get("deals") or []
    if action in OPEN_ACTIONS:
        return bool(positions or orders or history_orders or deals)
    if copied is None:
        return False
    position_id = str(copied.broker_position_id or "")
    order_id = str(copied.broker_order_id or "")
    if action in {SignalAction.modify_sl_tp.value, SignalAction.break_even.value}:
        expected_sl = intent.request_payload.get("stop_loss") or (
            intent.request_payload.get("entry")
            if action == SignalAction.break_even.value
            else None
        )
        expected_tp = intent.request_payload.get("take_profit")
        for position in positions:
            if str(position.get("ticket")) != position_id:
                continue
            sl_matches = expected_sl is None or Decimal(str(position.get("sl"))) == Decimal(str(expected_sl))
            tp_matches = expected_tp is None or Decimal(str(position.get("tp"))) == Decimal(str(expected_tp))
            return sl_matches and tp_matches
        return False
    if action == SignalAction.full_close.value:
        return not any(str(item.get("ticket")) == position_id for item in positions)
    if action == SignalAction.cancel_pending.value:
        return not any(str(item.get("ticket")) == order_id for item in orders)
    if action == SignalAction.partial_close.value:
        return bool(deals)
    return False


def should_retry_after_reconcile(intent, *, submission_started: bool) -> bool:
    action = intent.request_payload.get("action")
    if action in OPEN_ACTIONS:
        return not submission_started
    return True


def apply_broker_snapshot(
    trade,
    *,
    positions: list[dict],
    orders: list[dict],
    observed_at: datetime,
    client_order_id: str | None = None,
) -> bool:
    changed = False
    if trade.lifecycle_state == "open":
        position = next(
            (
                item
                for item in positions
                if str(item.get("ticket")) == str(trade.broker_position_id)
            ),
            None,
        )
        if position is None:
            trade.lifecycle_state = "closed"
            trade.current_volume = Decimal("0")
            changed = True
        else:
            values = {
                "current_volume": Decimal(str(position.get("volume", 0))),
                "stop_loss": Decimal(str(position.get("sl", 0))) if position.get("sl") else None,
                "take_profit": Decimal(str(position.get("tp", 0))) if position.get("tp") else None,
            }
            for field, value in values.items():
                if getattr(trade, field) != value:
                    setattr(trade, field, value)
                    changed = True
    elif trade.lifecycle_state == "pending":
        exists = any(
            str(item.get("ticket")) == str(trade.broker_order_id)
            for item in orders
        )
        if not exists:
            expected_comment = f"cpid:{client_order_id[:20]}" if client_order_id else None
            activated = next(
                (
                    item
                    for item in positions
                    if expected_comment
                    and str(item.get("comment", "")) == expected_comment
                ),
                None,
            )
            if activated:
                trade.lifecycle_state = "open"
                trade.broker_position_id = str(activated.get("ticket"))
                trade.current_volume = Decimal(str(activated.get("volume", 0)))
                trade.stop_loss = Decimal(str(activated.get("sl"))) if activated.get("sl") else None
                trade.take_profit = Decimal(str(activated.get("tp"))) if activated.get("tp") else None
            else:
                trade.lifecycle_state = "cancelled"
            changed = True
    trade.broker_synced_at = observed_at
    return changed
