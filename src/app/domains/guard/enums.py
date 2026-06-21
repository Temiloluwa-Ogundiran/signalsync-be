"""Enumerations for the Guard engine.

These name the exact modelling choices that, if gotten wrong, produce a
confidently-incorrect buffer, so they are explicit, never implied.
"""

from enum import Enum


class Status(str, Enum):
    """Account health tier, worst-of across all rules for a tick.

    PAUSED = a firm "soft breach": the day's trading is halted but the account
    survives and resumes at the next reset (e.g. The 5%ers, E8 2% soft breach).
    It sits just below a hard (account-ending) breach, which maps to CRITICAL.
    """

    HEALTHY = "HEALTHY"
    CAUTION = "CAUTION"
    WARNING = "WARNING"
    CRITICAL = "CRITICAL"
    PAUSED = "PAUSED"
    LOCKED = "LOCKED"

    @property
    def severity(self) -> int:
        return _STATUS_ORDER[self]


_STATUS_ORDER = {
    Status.HEALTHY: 0,
    Status.CAUTION: 1,
    Status.WARNING: 2,
    Status.CRITICAL: 3,
    Status.PAUSED: 4,
    Status.LOCKED: 5,
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


class DailyType(str, Enum):
    """Whether the daily floor is fixed for the day or trails intraday peak.

    STATIC = floor set once at the firm reset from ``anchor`` (start-of-day balance
    or higher-of-balance-equity).
    TRAILING = floor follows the day's *highest equity* up and never moves down,
    then resets at the next firm-day (E8 Signature, Goat Instant). This is the
    strictest daily model — every floating profit raises the floor.
    """

    STATIC = "STATIC"
    TRAILING = "TRAILING"


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
