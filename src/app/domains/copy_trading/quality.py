from dataclasses import dataclass
from datetime import datetime, timezone
from decimal import Decimal, InvalidOperation

from app.domains.copy_trading.symbols import BrokerSymbol


@dataclass(frozen=True)
class ExecutionQuality:
    spread_points: Decimal
    quote_age_seconds: float
    slippage_points: Decimal | None


class ExecutionQualityError(ValueError):
    def __init__(
        self,
        code: str,
        message: str,
        *,
        retryable: bool = False,
        user_action_required: bool = True,
    ) -> None:
        super().__init__(message)
        self.code = code
        self.retryable = retryable
        self.user_action_required = user_action_required


def _quote_decimal(value) -> Decimal:
    try:
        number = Decimal(str(value))
    except (InvalidOperation, TypeError, ValueError) as exc:
        raise ExecutionQualityError(
            "QUOTE_UNAVAILABLE",
            "A live broker quote is not available yet.",
            retryable=True,
            user_action_required=False,
        ) from exc
    if not number.is_finite():
        raise ExecutionQualityError(
            "QUOTE_UNAVAILABLE",
            "A live broker quote is not available yet.",
            retryable=True,
            user_action_required=False,
        )
    return number


def within_trading_hours(now: datetime, start: int | None, end: int | None) -> bool:
    if start is None or end is None or start == end:
        return True
    hour = now.astimezone(timezone.utc).hour
    if start < end:
        return start <= hour < end
    return hour >= start or hour < end


def check_execution_quality(
    *,
    price: dict,
    symbol: BrokerSymbol,
    direction: str,
    entry: Decimal | None,
    max_spread_points: int | None,
    max_slippage_points: int | None,
    max_quote_age_seconds: int,
    high_spread_behavior: str,
    trading_start_hour_utc: int | None,
    trading_end_hour_utc: int | None,
    now: datetime | None = None,
) -> ExecutionQuality:
    now = now or datetime.now(timezone.utc)
    if not within_trading_hours(now, trading_start_hour_utc, trading_end_hour_utc):
        raise ExecutionQualityError("OUTSIDE_TRADING_HOURS", "This account is outside its allowed trading hours.")
    bid = _quote_decimal(price.get("bid"))
    ask = _quote_decimal(price.get("ask"))
    point = symbol.point if symbol.point > 0 else Decimal("0.00001")
    timestamp = price.get("time") or price.get("brokerTime")
    if isinstance(timestamp, str):
        try:
            quote_at = datetime.fromisoformat(timestamp.replace("Z", "+00:00"))
        except ValueError as exc:
            raise ExecutionQualityError(
                "QUOTE_UNAVAILABLE",
                "A live broker quote is not available yet.",
                retryable=True,
                user_action_required=False,
            ) from exc
    elif isinstance(timestamp, datetime):
        quote_at = timestamp
    else:
        raise ExecutionQualityError(
            "QUOTE_TIME_MISSING",
            "A live broker quote is not available yet.",
            retryable=True,
            user_action_required=False,
        )
    quote_age = max(0.0, (now - quote_at.astimezone(timezone.utc)).total_seconds())
    if quote_age > max_quote_age_seconds:
        raise ExecutionQualityError(
            "STALE_QUOTE",
            "A fresh broker quote is not available yet.",
            retryable=True,
            user_action_required=False,
        )
    spread = (ask - bid) / point
    if max_spread_points is not None and spread > max_spread_points:
        raise ExecutionQualityError(
            "SPREAD_TOO_WIDE",
            f"Current spread is {spread:.1f} points; limit is {max_spread_points}.",
            retryable=high_spread_behavior == "wait",
        )
    market_price = ask if direction.lower() == "buy" else bid
    slippage = abs(market_price - entry) / point if entry is not None else None
    if max_slippage_points is not None and slippage is not None and slippage > max_slippage_points:
        raise ExecutionQualityError("SLIPPAGE_TOO_HIGH", f"Current price is {slippage:.1f} points away from the signal entry.")
    return ExecutionQuality(spread_points=spread, quote_age_seconds=quote_age, slippage_points=slippage)
