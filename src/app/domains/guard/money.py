"""Exact-money helpers for the Guard engine.

Buffer math at the line cannot tolerate binary-float drift: reporting a floor of
50,000.0000001 vs 49,999.9999999 is the difference between "safe" and "breached".
The whole engine works in ``Decimal`` and only crosses to float at the
serialization boundary (the API response).
"""

from decimal import Decimal, ROUND_HALF_UP
from typing import Union

Number = Union[int, str, Decimal, float]

# Money is rounded to cents for display/comparison; ratios to 4 dp.
CENTS = Decimal("0.01")
RATIO = Decimal("0.0001")


def money(value: Number) -> Decimal:
    """Coerce to a Decimal. Floats go through str() to avoid 0.1-style artifacts."""
    if isinstance(value, Decimal):
        return value
    if isinstance(value, float):
        return Decimal(str(value))
    return Decimal(value)


def pct(value: Number) -> Decimal:
    """A percentage given as e.g. 5 -> 0.05 fraction."""
    return money(value) / Decimal(100)


def q_money(value: Decimal) -> Decimal:
    return value.quantize(CENTS, rounding=ROUND_HALF_UP)


def q_ratio(value: Decimal) -> Decimal:
    return value.quantize(RATIO, rounding=ROUND_HALF_UP)


def safe_ratio(numerator: Decimal, denominator: Decimal) -> Decimal:
    """numerator/denominator clamped to [0, 1], 0 when denominator <= 0.

    Used for "how much of the allowance is consumed". 1.0 means at/over the line.
    """
    if denominator <= 0:
        return Decimal(1)
    r = numerator / denominator
    if r < 0:
        return Decimal(0)
    if r > 1:
        return Decimal(1)
    return r
