from dataclasses import dataclass, field
from decimal import Decimal
from enum import Enum
from typing import Optional


class SignalAction(str, Enum):
    open_market = "open_market"
    place_pending = "place_pending"
    modify_sl_tp = "modify_sl_tp"
    break_even = "break_even"
    partial_close = "partial_close"
    full_close = "full_close"
    cancel_pending = "cancel_pending"
    additional_tp = "additional_tp"
    status_only = "status_only"


@dataclass(frozen=True)
class ParsedSignal:
    action: SignalAction
    symbol: Optional[str] = None
    direction: Optional[str] = None
    entry: Optional[Decimal] = None
    entry_high: Optional[Decimal] = None
    stop_loss: Optional[Decimal] = None
    take_profits: list[Decimal] = field(default_factory=list)
    close_fraction: Optional[Decimal] = None
    age_seconds: float = 0
    confidence: float = 0


@dataclass(frozen=True)
class RouteExecutionPolicy:
    minimum_fields: str = "direction_symbol_sl_tp"
    confidence_threshold: float = 0.75
    market_freshness_seconds: float = 30
    pending_orders_enabled: bool = True
    allow_sl_tp_updates: bool = True
    allow_break_even: bool = True
    allow_partial_close: bool = True
    allow_full_close: bool = True
    allow_pending_cancel: bool = True
    allow_additional_tp: bool = True


@dataclass(frozen=True)
class ValidationResult:
    accepted: bool
    reason: Optional[str] = None
    advisory: Optional[str] = None


@dataclass(frozen=True)
class TakeProfitLeg:
    take_profit: Decimal
    lot: Decimal


def validate_signal(signal: ParsedSignal, policy: RouteExecutionPolicy) -> ValidationResult:
    advisory = (
        "AI confidence is low."
        if signal.confidence < policy.confidence_threshold
        else None
    )
    if signal.action == SignalAction.status_only:
        return ValidationResult(
            False,
            "Status updates do not perform broker actions.",
            advisory,
        )
    if signal.action == SignalAction.place_pending and signal.entry is None:
        return ValidationResult(
            False,
            "Pending order is waiting for an entry price.",
            advisory,
        )
    if signal.action == SignalAction.additional_tp and not signal.take_profits:
        return ValidationResult(
            False,
            "Additional take profit is waiting for a target price.",
            advisory,
        )
    prices = [
        value
        for value in (
            signal.entry,
            signal.entry_high,
            signal.stop_loss,
            *signal.take_profits,
        )
        if value is not None
    ]
    if any(not value.is_finite() or value <= Decimal("0") for value in prices):
        return ValidationResult(
            False,
            "Trade prices must be positive finite numbers.",
            advisory,
        )
    if signal.stop_loss is not None and signal.take_profits:
        prices_conflict = (
            signal.direction == "buy"
            and any(target <= signal.stop_loss for target in signal.take_profits)
        ) or (
            signal.direction == "sell"
            and any(target >= signal.stop_loss for target in signal.take_profits)
        )
        if prices_conflict:
            return ValidationResult(
                False,
                "Stop loss and take profit conflict with trade direction.",
                advisory,
            )
    if (
        signal.entry is not None
        and signal.entry_high is not None
        and signal.entry > signal.entry_high
    ):
        return ValidationResult(
            False,
            "Entry range minimum cannot exceed its maximum.",
            advisory,
        )
    if signal.action == SignalAction.partial_close:
        if signal.close_fraction is None or not signal.close_fraction.is_finite():
            return ValidationResult(
                False,
                "Partial close needs a percentage between 0% and 100%.",
                advisory,
            )
        if not (Decimal("0") < signal.close_fraction <= Decimal("1")):
            return ValidationResult(
                False,
                "Partial close must be greater than 0% and at most 100%.",
                advisory,
            )
    if signal.action == SignalAction.open_market and signal.age_seconds > policy.market_freshness_seconds:
        return ValidationResult(False, "Signal is too old for immediate entry.", advisory)
    if signal.action == SignalAction.place_pending and not policy.pending_orders_enabled:
        return ValidationResult(False, "Pending orders are disabled for this route.", advisory)
    permission = {
        SignalAction.modify_sl_tp: policy.allow_sl_tp_updates,
        SignalAction.break_even: policy.allow_break_even,
        SignalAction.partial_close: policy.allow_partial_close,
        SignalAction.full_close: policy.allow_full_close,
        SignalAction.cancel_pending: policy.allow_pending_cancel,
        SignalAction.additional_tp: policy.allow_additional_tp,
    }.get(signal.action, True)
    if not permission:
        return ValidationResult(
            False,
            "This broker action is disabled for this route.",
            advisory,
        )
    if signal.action in {SignalAction.open_market, SignalAction.place_pending}:
        if signal.direction not in {"buy", "sell"}:
            return ValidationResult(
                False,
                "Signal is waiting for a buy or sell direction.",
                advisory,
            )
        required = {
            "direction_symbol": (signal.direction, signal.symbol),
            "direction_symbol_entry": (signal.direction, signal.symbol, signal.entry),
            "direction_symbol_sl": (signal.direction, signal.symbol, signal.stop_loss),
            "direction_symbol_tp": (signal.direction, signal.symbol, signal.take_profits),
            "direction_symbol_sl_tp": (
                signal.direction,
                signal.symbol,
                signal.stop_loss,
                signal.take_profits,
            ),
        }[policy.minimum_fields]
        if any(value is None or value == [] for value in required):
            return ValidationResult(
                False,
                "Signal is waiting for required trade details.",
                advisory,
            )
    return ValidationResult(True, advisory=advisory)


def build_tp_legs(
    *,
    fixed_lot: Decimal,
    take_profits: list[Decimal],
    mode: str,
    distribution: str,
) -> list[TakeProfitLeg]:
    if not take_profits:
        return []
    selected = take_profits
    if mode == "lowest":
        selected = [min(take_profits)]
    elif mode == "highest":
        selected = [max(take_profits)]
    if distribution == "split_total" and len(selected) > 1:
        lot = fixed_lot / Decimal(len(selected))
    else:
        lot = fixed_lot
    return [TakeProfitLeg(take_profit=tp, lot=lot) for tp in selected]
