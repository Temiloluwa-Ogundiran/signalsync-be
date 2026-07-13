import hashlib
import base64
import json
from decimal import Decimal

from app.domains.copy_trading.engine import (
    SignalAction,
    TakeProfitLeg,
    build_tp_legs,
)
from app.domains.copy_trading.symbols import normalize_symbol


OPENING_ACTIONS = {
    SignalAction.open_market,
    SignalAction.place_pending,
    SignalAction.additional_tp,
}


def client_order_id_for_key(idempotency_key: str) -> str:
    digest = hashlib.sha256(idempotency_key.encode("utf-8")).digest()
    return base64.b32encode(digest).decode("ascii").rstrip("=")[:20]


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


def _matches_symbol_list(symbol: str, configured: list[str]) -> bool:
    normalized = normalize_symbol(symbol)
    return any(
        normalized.startswith(normalize_symbol(candidate))
        for candidate in configured
        if candidate.strip()
    )


def ensure_account_risk_within_limits(
    *,
    symbol: str,
    signal_volume: Decimal,
    current_exposure: Decimal,
    current_positions: int,
    equity: Decimal | None,
    daily_equity_anchor: Decimal | None,
    peak_equity: Decimal | None,
    max_lot_per_trade: Decimal,
    max_total_lot: Decimal,
    max_open_positions: int,
    daily_loss_limit: Decimal | None,
    max_drawdown_percent: Decimal | None,
    allowed_symbols: list[str],
    blocked_symbols: list[str],
) -> None:
    if signal_volume > max_lot_per_trade:
        raise ValueError(
            f"Trade size would exceed the per-trade maximum of {max_lot_per_trade}."
        )
    if current_exposure + signal_volume > max_total_lot:
        raise ValueError(
            f"Trade would exceed the total copied exposure limit of {max_total_lot}."
        )
    if current_positions >= max_open_positions:
        raise ValueError(
            f"Trade would exceed the open-position maximum of {max_open_positions}."
        )
    if allowed_symbols and not _matches_symbol_list(symbol, allowed_symbols):
        raise ValueError(f"{symbol} is not in this account's allowed-symbol list.")
    if _matches_symbol_list(symbol, blocked_symbols):
        raise ValueError(f"{symbol} is blocked for this account.")
    if equity is None and (
        daily_loss_limit is not None or max_drawdown_percent is not None
    ):
        raise ValueError(
            "Live account equity is unavailable, so equity-based safety limits cannot be checked."
        )
    if equity is None:
        return
    if (
        daily_loss_limit is not None
        and daily_equity_anchor is not None
        and daily_equity_anchor - equity >= daily_loss_limit
    ):
        raise ValueError(
            f"Account equity has reached the daily loss limit of {daily_loss_limit}."
        )
    if (
        max_drawdown_percent is not None
        and peak_equity is not None
        and peak_equity > 0
        and ((peak_equity - equity) / peak_equity) * Decimal("100")
        >= max_drawdown_percent
    ):
        raise ValueError(
            f"Account equity has reached the drawdown limit of {max_drawdown_percent}%."
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
