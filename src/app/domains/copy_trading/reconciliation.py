from datetime import datetime
from decimal import Decimal

from app.domains.copy_trading.engine import SignalAction


def _broker_id(item: dict):
    return item.get("id") if item.get("id") is not None else item.get("ticket")


def _broker_value(item: dict, metaapi_key: str, legacy_key: str):
    value = item.get(metaapi_key)
    return value if value is not None else item.get(legacy_key)


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
            if str(_broker_id(position)) != position_id:
                continue
            actual_sl = _broker_value(position, "stopLoss", "sl")
            actual_tp = _broker_value(position, "takeProfit", "tp")
            sl_matches = expected_sl is None or Decimal(str(actual_sl)) == Decimal(str(expected_sl))
            tp_matches = expected_tp is None or Decimal(str(actual_tp)) == Decimal(str(expected_tp))
            return sl_matches and tp_matches
        return False
    if action == SignalAction.full_close.value:
        return not any(str(_broker_id(item)) == position_id for item in positions)
    if action == SignalAction.cancel_pending.value:
        return not any(str(_broker_id(item)) == order_id for item in orders)
    if action == SignalAction.partial_close.value:
        return bool(deals)
    return False


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
                if str(_broker_id(item)) == str(trade.broker_position_id)
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
                "stop_loss": Decimal(str(_broker_value(position, "stopLoss", "sl")))
                if _broker_value(position, "stopLoss", "sl")
                else None,
                "take_profit": Decimal(str(_broker_value(position, "takeProfit", "tp")))
                if _broker_value(position, "takeProfit", "tp")
                else None,
            }
            for field, value in values.items():
                if getattr(trade, field) != value:
                    setattr(trade, field, value)
                    changed = True
    elif trade.lifecycle_state == "pending":
        exists = any(
            str(_broker_id(item)) == str(trade.broker_order_id)
            for item in orders
        )
        if not exists:
            expected_comment = f"cpid:{client_order_id[:20]}" if client_order_id else None
            activated = next(
                (
                    item
                    for item in positions
                    if (
                        client_order_id
                        and item.get("clientId") == client_order_id
                    )
                    or (
                        expected_comment
                        and str(item.get("comment", "")) == expected_comment
                    )
                ),
                None,
            )
            if activated:
                trade.lifecycle_state = "open"
                trade.broker_position_id = str(_broker_id(activated))
                trade.current_volume = Decimal(str(activated.get("volume", 0)))
                stop_loss = _broker_value(activated, "stopLoss", "sl")
                take_profit = _broker_value(activated, "takeProfit", "tp")
                trade.stop_loss = Decimal(str(stop_loss)) if stop_loss else None
                trade.take_profit = Decimal(str(take_profit)) if take_profit else None
            else:
                trade.lifecycle_state = "cancelled"
            changed = True
    trade.broker_synced_at = observed_at
    return changed
