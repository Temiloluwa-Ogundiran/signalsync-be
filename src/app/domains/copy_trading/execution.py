import hashlib
import base64
import json
from decimal import Decimal

from app.domains.accounts.models import (
    ImportMethod,
    TradingAccountConnectionState,
    TradingPlatform,
)
from app.domains.copy_trading.engine import (
    SignalAction,
    TakeProfitLeg,
    build_tp_legs,
)


OPENING_ACTIONS = {
    SignalAction.open_market,
    SignalAction.place_pending,
    SignalAction.additional_tp,
}


def client_order_id_for_key(idempotency_key: str) -> str:
    digest = hashlib.sha256(idempotency_key.encode("utf-8")).digest()
    return base64.b32encode(digest).decode("ascii").rstrip("=")[:20]


def is_trade_ready(account) -> bool:
    return bool(
        account
        and account.platform == TradingPlatform.mt5
        and account.import_method == ImportMethod.auto_sync
        and account.connection_state == TradingAccountConnectionState.ready
        and not account.is_archived
        and account.encrypted_trader_password
    )


def calculate_signal_volume(
    *,
    fixed_lot: Decimal,
    take_profit_count: int,
    take_profit_mode: str,
    distribution: str,
) -> Decimal:
    selected_count = max(1, take_profit_count)
    if take_profit_mode in {"lowest", "highest"}:
        selected_count = 1
    if distribution == "split_total":
        return fixed_lot
    return fixed_lot * Decimal(selected_count)


def signal_volume_for_action(
    *,
    action: SignalAction,
    fixed_lot: Decimal,
    take_profit_count: int,
    take_profit_mode: str,
    distribution: str,
) -> Decimal:
    if action not in OPENING_ACTIONS:
        return Decimal("0")
    return calculate_signal_volume(
        fixed_lot=fixed_lot,
        take_profit_count=take_profit_count,
        take_profit_mode=take_profit_mode,
        distribution=distribution,
    )


def intent_legs_for_action(
    *,
    action: SignalAction,
    fixed_lot: Decimal,
    take_profits: list[Decimal],
    mode: str,
    distribution: str,
) -> list[TakeProfitLeg | None]:
    if action not in OPENING_ACTIONS:
        return [None]
    return build_tp_legs(
        fixed_lot=fixed_lot,
        take_profits=take_profits,
        mode=mode,
        distribution=distribution,
    ) or [None]


def ensure_exposure_within_limit(
    *, current_exposure: Decimal, signal_volume: Decimal, maximum: Decimal
) -> None:
    if signal_volume > maximum or current_exposure + signal_volume > maximum:
        raise ValueError(
            f"Copied exposure would exceed the account maximum of {maximum}."
        )


def catalog_fingerprint(symbols: list[dict]) -> str:
    relevant = [
        {
            "name": str(item.get("name", "")),
            "trade_mode": int(item.get("trade_mode", 0) or 0),
            "contract_size": str(item.get("contract_size", 0)),
            "volume_min": str(item.get("volume_min", 0)),
            "volume_max": str(item.get("volume_max", 0)),
            "volume_step": str(item.get("volume_step", 0)),
        }
        for item in symbols
    ]
    payload = json.dumps(
        sorted(relevant, key=lambda item: item["name"]),
        sort_keys=True,
        separators=(",", ":"),
    )
    return hashlib.sha256(payload.encode("utf-8")).hexdigest()
