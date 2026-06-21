"""Enumerations for the Guard engine.

These name the exact modelling choices that, if gotten wrong, produce a
confidently-incorrect buffer, so they are explicit, never implied.
"""

from enum import Enum


class Status(str, Enum):
    """Account health tier, worst-of across all rules for a tick."""

    HEALTHY = "HEALTHY"
    CAUTION = "CAUTION"
    WARNING = "WARNING"
    CRITICAL = "CRITICAL"
    LOCKED = "LOCKED"

    @property
    def severity(self) -> int:
        return _STATUS_ORDER[self]


_STATUS_ORDER = {
    Status.HEALTHY: 0,
    Status.CAUTION: 1,
    Status.WARNING: 2,
    Status.CRITICAL: 3,
    Status.LOCKED: 4,
}


def worst(*statuses: "Status") -> "Status":
    """Return the most severe status (LOCKED beats CRITICAL beats ...)."""
    return max(statuses, key=lambda s: s.severity) if statuses else Status.HEALTHY


class Basis(str, Enum):
    """What value a loss/drawdown is measured against on a given tick."""

    BALANCE = "BALANCE"
    EQUITY = "EQUITY"


class DailyAnchor(str, Enum):
    """How the daily loss floor is anchored at the firm's reset."""

    DAY_START_BALANCE = "DAY_START_BALANCE"
    HIGHER_OF_BALANCE_EQUITY = "HIGHER_OF_BALANCE_EQUITY"


class DrawdownType(str, Enum):
    STATIC = "STATIC"
    TRAILING = "TRAILING"


class DrawdownAnchorRef(str, Enum):
    INITIAL_BALANCE = "INITIAL_BALANCE"
    PEAK_EQUITY = "PEAK_EQUITY"
    PEAK_BALANCE = "PEAK_BALANCE"


class TradingDayRule(str, Enum):
    """What makes a calendar day count toward min-trading-days."""

    ANY_TRADE = "ANY_TRADE"
    RESULT_MOVES_X = "RESULT_MOVES_X"


class ConsistencyBasis(str, Enum):
    """What the single-day consistency cap is measured against."""

    TOTAL_PROFIT = "TOTAL_PROFIT"
    TARGET = "TARGET"


class Phase(str, Enum):
    EVALUATION = "EVALUATION"
    FUNDED = "FUNDED"


class RuleKind(str, Enum):
    DAILY_LOSS = "DAILY_LOSS"
    MAX_DRAWDOWN = "MAX_DRAWDOWN"
    CONSISTENCY = "CONSISTENCY"
    PROFIT_TARGET = "PROFIT_TARGET"
    MIN_DAYS = "MIN_DAYS"
    PERSONAL_DAILY = "PERSONAL_DAILY"
    PERSONAL_DRAWDOWN = "PERSONAL_DRAWDOWN"
