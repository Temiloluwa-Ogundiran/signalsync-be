"""FirmRuleSpec — firm rules modelled as data, never hardcoded per firm.

This is the moat. Every firm difference the correctness checklist lists is a field
here, so adding a firm is writing data, not writing code. In Guard v1 the spec is
hydrated from the user-entered ``rule_spec_json`` on a GuardAccount (see
``from_json``) — we do not curate a firm library.
"""

from dataclasses import dataclass, field
from decimal import Decimal
from typing import List, Optional

from .enums import (
    Basis,
    ConsistencyBasis,
    DailyAnchor,
    DailyType,
    DrawdownAnchorRef,
    DrawdownType,
    Phase,
    TradingDayRule,
)
from .money import money, pct


@dataclass(frozen=True)
class DailyLossRule:
    """The intraday floor that resets each firm day.

    ``basis`` = what current value is compared (balance vs equity).
    ``type`` = STATIC (floor fixed at reset from ``anchor``) or TRAILING (floor
    follows the day's highest equity up, resets next firm-day).
    ``anchor`` = how the day-start reference is set at reset (STATIC only).
    The floor = reference - pct * account_size, where reference is the anchor
    (STATIC) or the day's peak equity (TRAILING).
    ``soft_pct`` = optional soft-breach threshold as a fraction of the daily
    *allowance* consumed (0..1). Reaching it = PAUSED for the day (firm halts
    trading but the account survives), not a hard breach. 0 disables it.
    """

    pct: Decimal                      # fraction, e.g. 0.05
    basis: Basis = Basis.EQUITY
    type: DailyType = DailyType.STATIC
    anchor: DailyAnchor = DailyAnchor.DAY_START_BALANCE
    reset_hour: int = 0               # in reset_tz
    reset_minute: int = 0             # in reset_tz (firms reset at e.g. 16:59 EST)
    reset_tz: str = "UTC"             # the FIRM's clock, stored explicitly
    soft_pct: Decimal = Decimal(0)    # 0 = no soft breach

    @staticmethod
    def of(pct_value, **kw) -> "DailyLossRule":
        return DailyLossRule(pct=pct(pct_value), **kw)


@dataclass(frozen=True)
class MaxDrawdownRule:
    """The overall floor for the whole challenge.

    STATIC = fixed from initial balance.
    TRAILING = follows peak (equity or balance) up; if ``locks_at_initial`` the
    trail freezes at the initial balance once the trailing floor reaches it — i.e.
    once profit >= pct * size the floor stops climbing.
    """

    pct: Decimal
    type: DrawdownType = DrawdownType.STATIC
    anchor_ref: DrawdownAnchorRef = DrawdownAnchorRef.INITIAL_BALANCE
    locks_at_initial: bool = False

    @staticmethod
    def of(pct_value, **kw) -> "MaxDrawdownRule":
        return MaxDrawdownRule(pct=pct(pct_value), **kw)


@dataclass(frozen=True)
class ProfitTargetRule:
    pct: Decimal
    phase: Phase = Phase.EVALUATION

    @staticmethod
    def of(pct_value, **kw) -> "ProfitTargetRule":
        return ProfitTargetRule(pct=pct(pct_value), **kw)


@dataclass(frozen=True)
class MinDaysRule:
    count: int
    day_counts_if: TradingDayRule = TradingDayRule.ANY_TRADE
    moves_pct: Decimal = Decimal(0)   # for RESULT_MOVES_X: |day pnl| >= moves_pct*size

    @staticmethod
    def of(count, day_counts_if=TradingDayRule.ANY_TRADE, moves_pct=0) -> "MinDaysRule":
        return MinDaysRule(count=int(count), day_counts_if=day_counts_if,
                           moves_pct=pct(moves_pct))


@dataclass(frozen=True)
class ConsistencyRule:
    """No single day may make up more than ``cap`` of the measured base."""

    cap: Decimal                      # fraction, e.g. 0.40
    basis: ConsistencyBasis = ConsistencyBasis.TOTAL_PROFIT

    @staticmethod
    def of(cap_value, **kw) -> "ConsistencyRule":
        return ConsistencyRule(cap=money(cap_value), **kw)


@dataclass(frozen=True)
class FirmRuleSpec:
    """A complete firm ruleset. ``size`` is supplied per account, not here — the
    spec is percentages so it applies to any account size."""

    firm: str
    version: str
    daily_loss: DailyLossRule
    max_drawdown: MaxDrawdownRule
    profit_target: ProfitTargetRule
    min_days: Optional[MinDaysRule] = None
    consistency: Optional[ConsistencyRule] = None
    prohibited: List[str] = field(default_factory=list)
    sl_required: bool = False
    notes: str = ""

    @property
    def id(self) -> str:
        return f"{self.firm}@{self.version}"

    def validate(self) -> None:
        """Cheap sanity checks; the real correctness gate is the unit tests."""
        if not (0 < self.daily_loss.pct < 1):
            raise ValueError("daily_loss.pct must be a fraction in (0,1)")
        if not (0 < self.max_drawdown.pct < 1):
            raise ValueError("max_drawdown.pct must be a fraction in (0,1)")
        if self.daily_loss.pct >= self.max_drawdown.pct:
            # A daily floor looser than the overall floor would never bind first.
            raise ValueError("daily_loss should be tighter than max_drawdown")
        if self.consistency and not (0 < self.consistency.cap <= 1):
            raise ValueError("consistency.cap must be in (0,1]")
        if not (0 <= self.daily_loss.reset_hour <= 23):
            raise ValueError("reset_hour out of range")
        if not (0 <= self.daily_loss.reset_minute <= 59):
            raise ValueError("reset_minute out of range")
        if not (0 <= self.daily_loss.soft_pct < 1):
            raise ValueError("daily_loss.soft_pct must be in [0,1)")

    # -- (de)serialization to the GuardAccount.rule_spec_json shape -------------

    @staticmethod
    def from_json(data: dict) -> "FirmRuleSpec":
        """Hydrate from the user-entered rule_spec_json. Percentages are stored as
        fractions (0.05), not whole numbers — the FE form converts on the way in."""
        dl = data["daily_loss"]
        dd = data["max_drawdown"]
        pt = data["profit_target"]
        md = data.get("min_days")
        cons = data.get("consistency")
        return FirmRuleSpec(
            firm=data.get("firm", "user"),
            version=data.get("version", "user"),
            daily_loss=DailyLossRule(
                pct=money(dl["pct"]),
                basis=Basis(dl.get("basis", Basis.EQUITY.value)),
                type=DailyType(dl.get("type", DailyType.STATIC.value)),
                anchor=DailyAnchor(dl.get("anchor", DailyAnchor.DAY_START_BALANCE.value)),
                reset_hour=int(dl.get("reset_hour", 0)),
                reset_minute=int(dl.get("reset_minute", 0)),
                reset_tz=dl.get("reset_tz", "UTC"),
                soft_pct=money(dl.get("soft_pct", 0)),
            ),
            max_drawdown=MaxDrawdownRule(
                pct=money(dd["pct"]),
                type=DrawdownType(dd.get("type", DrawdownType.STATIC.value)),
                anchor_ref=DrawdownAnchorRef(
                    dd.get("anchor_ref", DrawdownAnchorRef.INITIAL_BALANCE.value)),
                locks_at_initial=bool(dd.get("locks_at_initial", False)),
            ),
            profit_target=ProfitTargetRule(
                pct=money(pt["pct"]),
                phase=Phase(pt.get("phase", Phase.EVALUATION.value)),
            ),
            min_days=MinDaysRule(
                count=int(md["count"]),
                day_counts_if=TradingDayRule(
                    md.get("day_counts_if", TradingDayRule.ANY_TRADE.value)),
                moves_pct=money(md.get("moves_pct", 0)),
            ) if md else None,
            consistency=ConsistencyRule(
                cap=money(cons["cap"]),
                basis=ConsistencyBasis(
                    cons.get("basis", ConsistencyBasis.TOTAL_PROFIT.value)),
            ) if cons else None,
            notes=data.get("notes", ""),
        )
