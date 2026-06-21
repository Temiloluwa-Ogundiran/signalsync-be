"""The deterministic Guard rules engine.

Pure function of (config, previous memory, tick) -> (next memory, AccountState).
No I/O, no clock reads, no randomness — so unit tests can assert breach fires at
the exact firm equity.

The engine never *acts*; it only decides. Persistence, alerts and the surfaces are
downstream consumers of the emitted AccountState. This is the single source of
truth for all buffer math — the frontend renders these numbers, it never recomputes.
"""

from dataclasses import dataclass, field
from decimal import Decimal
from typing import Dict, List, Optional

from ..account import GuardConfig
from ..enums import (
    Basis,
    ConsistencyBasis,
    DailyAnchor,
    DailyType,
    DrawdownAnchorRef,
    DrawdownType,
    RuleKind,
    Status,
    TradingDayRule,
    worst,
)
from ..money import safe_ratio
from ..spec import FirmRuleSpec
from ..state import (
    AccountState,
    Buffer,
    Challenge,
    ConsistencyState,
    PassPlan,
    Violation,
)
from ..tick import Tick
from .dayclock import firm_day_key

# Status thresholds on "fraction of allowance consumed". A breach (ratio>=1) is
# CRITICAL. (LOCKED exists in the enum for forward-compat; v1 is read-only and
# never sets it.)
CAUTION_AT = Decimal("0.50")
WARNING_AT = Decimal("0.75")
CRITICAL_AT = Decimal("0.90")


@dataclass
class EngineMemory:
    """Everything the engine must remember between ticks.

    Kept tiny on purpose: closed-day results, the running peak, and the current
    firm-day anchor — not full tick history.
    """

    day_key: Optional[str] = None
    day_anchor: Decimal = Decimal(0)        # daily-loss reference for the current day
    day_start_equity: Decimal = Decimal(0)  # equity at this day's reset
    day_peak_equity: Decimal = Decimal(0)   # highest equity this firm-day (trailing daily)
    peak: Decimal = Decimal(0)              # for trailing DD
    dd_floor_locked: bool = False           # trailing-then-lock latch
    daily_results: Dict[str, Decimal] = field(default_factory=dict)  # day_key -> realized pnl
    trading_days: set = field(default_factory=set)
    traded_today_seen: bool = False

    def copy(self) -> "EngineMemory":
        return EngineMemory(
            day_key=self.day_key,
            day_anchor=self.day_anchor,
            day_start_equity=self.day_start_equity,
            day_peak_equity=self.day_peak_equity,
            peak=self.peak,
            dd_floor_locked=self.dd_floor_locked,
            daily_results=dict(self.daily_results),
            trading_days=set(self.trading_days),
            traded_today_seen=self.traded_today_seen,
        )


class Engine:
    """Stateless processor; all state lives in the EngineMemory you pass in."""

    def __init__(self, config: GuardConfig):
        self.config = config
        self.spec: FirmRuleSpec = config.spec
        self.size: Decimal = config.size

    def init_memory(self, opening_balance: Optional[Decimal] = None) -> EngineMemory:
        bal = opening_balance if opening_balance is not None else self.size
        return EngineMemory(peak=self._peak_seed(bal), day_anchor=bal,
                            day_start_equity=bal)

    def _peak_seed(self, balance: Decimal) -> Decimal:
        return balance

    # -- main entry -------------------------------------------------------------

    def process(self, mem: EngineMemory, tick: Tick) -> "EngineResult":
        mem = mem.copy()
        self._roll_day(mem, tick)
        self._update_peak(mem, tick)

        daily, daily_v, daily_soft = self._daily_buffer(mem, tick)
        max_dd, dd_v = self._max_dd_buffer(mem, tick)
        pers_daily, pers_daily_v = self._personal_daily_buffer(mem, tick, daily)
        pers_dd, pers_dd_v = self._personal_dd_buffer(mem, tick, max_dd)

        challenge = self._challenge(mem, tick)

        violations = [v for v in (daily_v, dd_v, pers_daily_v, pers_dd_v) if v]

        status = self._status(daily, max_dd, pers_daily, pers_dd, violations,
                              challenge, daily_soft)

        state = AccountState(
            account_id=self.config.id,
            ts=tick.ts,
            equity=tick.equity,
            balance=tick.balance,
            peak=mem.peak,
            day_anchor=mem.day_anchor,
            status=status,
            daily=daily,
            max_dd=max_dd,
            challenge=challenge,
            violations=violations,
            personal_daily=pers_daily,
            personal_dd=pers_dd,
        )
        return EngineResult(memory=mem, state=state)

    # -- day rollover -----------------------------------------------------------

    def _roll_day(self, mem: EngineMemory, tick: Tick) -> None:
        dl = self.spec.daily_loss
        key = firm_day_key(tick.ts, dl.reset_hour, dl.reset_tz, dl.reset_minute)
        if mem.day_key == key:
            # Same day: accumulate realized result, sticky "traded today".
            mem.traded_today_seen = mem.traded_today_seen or tick.traded_today
            mem.daily_results[key] = tick.day_realized_pnl
            # Trailing daily: the floor follows the day's highest equity.
            if tick.equity > mem.day_peak_equity:
                mem.day_peak_equity = tick.equity
            self._maybe_count_trading_day(mem, key, tick)
            return

        # New firm-day. The previous day's realized result already sits in the map.
        mem.day_key = key
        mem.traded_today_seen = tick.traded_today
        mem.day_start_equity = tick.equity
        mem.day_peak_equity = tick.equity  # trailing-daily peak resets each firm-day
        # Daily anchor per firm rule: start-of-day balance, or higher of bal/eq.
        if dl.anchor == DailyAnchor.HIGHER_OF_BALANCE_EQUITY:
            mem.day_anchor = max(tick.balance, tick.equity)
        else:
            mem.day_anchor = tick.balance
        mem.daily_results[key] = tick.day_realized_pnl
        self._maybe_count_trading_day(mem, key, tick)

    def _maybe_count_trading_day(self, mem: EngineMemory, key: str, tick: Tick) -> None:
        md = self.spec.min_days
        if md is None:
            if tick.traded_today:
                mem.trading_days.add(key)
            return
        if md.day_counts_if == TradingDayRule.ANY_TRADE:
            if tick.traded_today or mem.traded_today_seen:
                mem.trading_days.add(key)
        else:  # RESULT_MOVES_X
            threshold = md.moves_pct * self.size
            if abs(tick.day_realized_pnl) >= threshold and threshold > 0:
                mem.trading_days.add(key)

    def _update_peak(self, mem: EngineMemory, tick: Tick) -> None:
        dd = self.spec.max_drawdown
        if dd.type != DrawdownType.TRAILING:
            return
        # Once the trail has locked, the floor is frozen at initial balance and the
        # peak must stop climbing — further gains do not move the floor.
        if mem.dd_floor_locked:
            return
        ref = self._dd_observed(mem, tick)
        if ref > mem.peak:
            mem.peak = ref
        # Trailing-then-lock: once the trailing floor would reach initial balance,
        # freeze it there (the floor stops climbing).
        if dd.locks_at_initial:
            floor = mem.peak - dd.pct * self.size
            if floor >= self.size:
                mem.dd_floor_locked = True
                # pin peak so floor == initial size exactly
                mem.peak = self.size + dd.pct * self.size

    def _dd_observed(self, mem: EngineMemory, tick: Tick) -> Decimal:
        ref = self.spec.max_drawdown.anchor_ref
        if ref == DrawdownAnchorRef.PEAK_BALANCE:
            return tick.balance
        return tick.equity  # PEAK_EQUITY (and INITIAL_BALANCE never trails)

    # -- daily loss -------------------------------------------------------------

    def _daily_buffer(self, mem: EngineMemory, tick: Tick):
        """Returns (buffer, hard_violation, soft_breached).

        STATIC: floor = day anchor - allowance (fixed for the firm-day).
        TRAILING: floor = day's highest equity - allowance (follows the peak up,
        never down, resets next firm-day).
        ``soft_breached`` is True when consumption reaches ``soft_pct`` of the
        allowance but the hard floor has not been hit (a firm "pause for the day").
        """
        dl = self.spec.daily_loss
        allowance = dl.pct * self.size
        if dl.type == DailyType.TRAILING:
            reference = mem.day_peak_equity
        else:
            reference = mem.day_anchor
        floor = reference - allowance
        current = tick.equity if dl.basis == Basis.EQUITY else tick.balance
        room = current - floor
        ratio = safe_ratio(allowance - room, allowance)
        buf = Buffer(floor=floor, room=room, ratio=ratio, allowance=allowance)
        v = None
        soft = False
        if room <= 0:
            v = Violation(RuleKind.DAILY_LOSS, True,
                          f"Daily loss limit hit: {dl.basis.value.lower()} "
                          f"{current} at/below floor {floor}.")
        elif dl.soft_pct > 0 and ratio >= dl.soft_pct:
            # Soft breach: paused for the day, account survives, resumes at reset.
            soft = True
        return buf, v, soft

    # -- max drawdown -----------------------------------------------------------

    def _max_dd_buffer(self, mem: EngineMemory, tick: Tick):
        dd = self.spec.max_drawdown
        allowance = dd.pct * self.size
        if dd.type == DrawdownType.STATIC:
            base = self.size  # INITIAL_BALANCE
            floor = base - allowance
        else:
            floor = mem.peak - allowance
        current = tick.equity  # DD is always measured on equity (real value)
        room = current - floor
        ratio = safe_ratio(allowance - room, allowance) if allowance > 0 else Decimal(1)
        buf = Buffer(floor=floor, room=room, ratio=ratio, allowance=allowance)
        v = None
        if room <= 0:
            v = Violation(RuleKind.MAX_DRAWDOWN, True,
                          f"Max drawdown limit hit: equity {current} "
                          f"at/below floor {floor}.")
        return buf, v

    # -- personal (amber) layer -------------------------------------------------

    def _personal_daily_buffer(self, mem: EngineMemory, tick: Tick, firm_daily: Buffer):
        p = self.config.personal
        if p.daily_frac >= 1:
            return None, None
        # Stricter-only: personal allowance is a fraction of the firm allowance.
        allowance = firm_daily.allowance * p.daily_frac
        floor = mem.day_anchor - allowance
        # The clamp invariant: personal floor must never sit below the firm floor.
        floor = max(floor, firm_daily.floor)
        dl = self.spec.daily_loss
        current = tick.equity if dl.basis == Basis.EQUITY else tick.balance
        room = current - floor
        eff_allow = mem.day_anchor - floor
        ratio = safe_ratio(eff_allow - room, eff_allow)
        buf = Buffer(floor=floor, room=room, ratio=ratio, allowance=eff_allow)
        v = None
        if room <= 0 and firm_daily.room > 0:
            v = Violation(RuleKind.PERSONAL_DAILY, False,
                          f"Personal daily limit reached at {current} "
                          f"(floor {floor}); firm line not yet hit.")
        return buf, v

    def _personal_dd_buffer(self, mem: EngineMemory, tick: Tick, firm_dd: Buffer):
        p = self.config.personal
        if p.dd_frac >= 1:
            return None, None
        allowance = firm_dd.allowance * p.dd_frac
        floor = firm_dd.floor + (firm_dd.allowance - allowance)  # tighter floor
        floor = max(floor, firm_dd.floor)
        current = tick.equity
        room = current - floor
        eff_allow = allowance
        ratio = safe_ratio(eff_allow - room, eff_allow)
        buf = Buffer(floor=floor, room=room, ratio=ratio, allowance=eff_allow)
        v = None
        if room <= 0 and firm_dd.room > 0:
            v = Violation(RuleKind.PERSONAL_DRAWDOWN, False,
                          f"Personal drawdown limit reached at {current} "
                          f"(floor {floor}); firm line not yet hit.")
        return buf, v

    # -- challenge / consistency / pass plan ------------------------------------

    def _challenge(self, mem: EngineMemory, tick: Tick) -> Challenge:
        target = self.spec.profit_target.pct * self.size
        # Profit measured on balance vs initial (realized), the firm's basis.
        profit = tick.balance - self.size
        to_go = target - profit
        days_traded = len(mem.trading_days)
        days_owed = max(0, (self.spec.min_days.count if self.spec.min_days else 0)
                        - days_traded)

        consistency = self._consistency(mem, profit, target)
        plan = self._pass_plan(mem, profit, target, to_go, days_owed, consistency)
        return Challenge(profit=profit, target=target, to_go=to_go,
                         days_traded=days_traded, days_owed=days_owed,
                         consistency=consistency, plan=plan)

    def _consistency(self, mem: EngineMemory, profit: Decimal,
                     target: Decimal) -> Optional[ConsistencyState]:
        rule = self.spec.consistency
        if rule is None:
            return None
        wins = [v for v in mem.daily_results.values() if v > 0]
        biggest = max(wins) if wins else Decimal(0)
        base = target if rule.basis == ConsistencyBasis.TARGET else max(profit, Decimal(0))
        ceiling = rule.cap * base
        share = safe_ratio(biggest, base) if base > 0 else Decimal(0)

        # Firms apply the consistency check at pass/payout time, against total profit
        # *then*. So a single early winning day is trivially ~100% of a tiny total —
        # not a real risk yet. We compute share/ceiling accurately for the surface,
        # but damp the *alarm tier* until profit nears the target, where the cap
        # actually decides pass/fail. The ceiling matters most as the single-day cap
        # the trader must respect on the way up; that's surfaced via the pass plan
        # regardless of tier.
        progress = safe_ratio(max(profit, Decimal(0)), target) if target > 0 \
            else Decimal(0)
        st = self._consistency_tier(share, rule.cap, progress)
        return ConsistencyState(biggest_day=biggest, share=share, cap=rule.cap,
                                ceiling=ceiling, state=st)

    @staticmethod
    def _consistency_tier(share: Decimal, cap: Decimal,
                          progress: Decimal) -> Status:
        if cap <= 0:
            return Status.HEALTHY
        over = share / cap  # 1.0 == exactly at the cap
        # Weight the alarm by how close the account is to the target: early on, an
        # over-cap biggest day is informational; near target it is decisive.
        weighted = over * (Decimal("0.25") + Decimal("0.75") * progress)
        if weighted >= 1:
            return Status.CRITICAL
        if weighted >= Decimal("0.8"):
            return Status.WARNING
        if weighted >= Decimal("0.5"):
            return Status.CAUTION
        return Status.HEALTHY

    def _pass_plan(self, mem: EngineMemory, profit: Decimal, target: Decimal,
                   to_go: Decimal, days_owed: int,
                   consistency: Optional[ConsistencyState]) -> Optional[PassPlan]:
        if to_go <= 0:
            return PassPlan(band_lo=Decimal(0), band_hi=Decimal(0),
                            days_left=None, ceil_day=self._day_ceiling(consistency),
                            on_track=True)
        # Spread the remaining profit over at least the days we still owe (so we
        # don't suggest a pace that fails min-days), padded by one buffer day.
        days_left = max(days_owed, 1)
        spread_days = days_left + 1
        band_lo = to_go / Decimal(spread_days)
        band_hi = to_go / Decimal(days_left)
        ceil_day = self._day_ceiling(consistency)
        if ceil_day > 0:
            band_hi = min(band_hi, ceil_day)
            band_lo = min(band_lo, ceil_day)
        on_track = band_lo <= ceil_day or ceil_day == 0
        return PassPlan(band_lo=band_lo, band_hi=band_hi, days_left=days_left,
                        ceil_day=ceil_day, on_track=on_track)

    def _day_ceiling(self, consistency: Optional[ConsistencyState]) -> Decimal:
        """Most a single day may earn without tripping consistency. 0 = unbounded."""
        if consistency is None:
            return Decimal(0)
        return max(consistency.ceiling - consistency.biggest_day, Decimal(0)) \
            if consistency.ceiling > 0 else Decimal(0)

    # -- status tiering ---------------------------------------------------------

    def _status(self, daily: Buffer, max_dd: Buffer,
                pers_daily: Optional[Buffer], pers_dd: Optional[Buffer],
                violations: List[Violation], challenge: Challenge,
                daily_soft: bool = False) -> Status:
        if any(v.is_firm for v in violations):
            return Status.CRITICAL
        tiers = [self._tier(daily.ratio), self._tier(max_dd.ratio)]
        for b in (pers_daily, pers_dd):
            if b is not None:
                tiers.append(self._tier(b.ratio))
        if challenge.consistency is not None:
            tiers.append(challenge.consistency.state)
        if any(v for v in violations):  # personal-only violation
            tiers.append(Status.WARNING)
        if daily_soft:
            # Soft breach = paused for the day; sits just below a hard breach.
            tiers.append(Status.PAUSED)
        return worst(*tiers)

    @staticmethod
    def _tier(ratio: Decimal) -> Status:
        if ratio >= 1:
            return Status.CRITICAL
        if ratio >= CRITICAL_AT:
            return Status.CRITICAL
        if ratio >= WARNING_AT:
            return Status.WARNING
        if ratio >= CAUTION_AT:
            return Status.CAUTION
        return Status.HEALTHY


@dataclass(frozen=True)
class EngineResult:
    memory: EngineMemory
    state: AccountState
