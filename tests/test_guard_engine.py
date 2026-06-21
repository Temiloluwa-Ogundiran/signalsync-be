"""Unit tests for the Partna Guard rules engine — the entire G0 correctness gate.

These assert the buffer math is exact at each firm line, across the modelling
choices the correctness checklist warns about: daily basis/anchor, static vs
trailing drawdown, trailing-then-lock, consistency, min-days, and the personal
stricter-only clamp. No DB, no network — pure Decimal math.
"""

from datetime import datetime, timezone
from decimal import Decimal

import pytest

from app.domains.guard.account import GuardConfig, PersonalRules
from app.domains.guard.engine import Engine
from app.domains.guard.enums import (
    Basis,
    ConsistencyBasis,
    DailyAnchor,
    DrawdownAnchorRef,
    DrawdownType,
    Status,
    TradingDayRule,
)
from app.domains.guard.spec import (
    ConsistencyRule,
    DailyLossRule,
    FirmRuleSpec,
    MaxDrawdownRule,
    MinDaysRule,
    ProfitTargetRule,
)
from app.domains.guard.tick import Tick

SIZE = Decimal("100000")


def _spec(**overrides) -> FirmRuleSpec:
    base = dict(
        firm="test",
        version="v1",
        daily_loss=DailyLossRule.of(5, basis=Basis.EQUITY,
                                    anchor=DailyAnchor.DAY_START_BALANCE),
        max_drawdown=MaxDrawdownRule.of(10, type=DrawdownType.STATIC),
        profit_target=ProfitTargetRule.of(8),
    )
    base.update(overrides)
    return FirmRuleSpec(**base)


def _engine(spec: FirmRuleSpec, personal: PersonalRules | None = None) -> Engine:
    cfg = GuardConfig.of("acc-1", spec, SIZE,
                         personal=personal or PersonalRules())
    return Engine(cfg)


def _ts(hour=12, day=1):
    return datetime(2026, 6, day, hour, 0, tzinfo=timezone.utc)


def _run(engine: Engine, *ticks: Tick):
    mem = engine.init_memory()
    state = None
    for t in ticks:
        res = engine.process(mem, t)
        mem, state = res.memory, res.state
    return mem, state


# -- daily loss: floor, room, breach exactly at the line ----------------------

def test_daily_floor_static_balance_anchor():
    eng = _engine(_spec())
    # day-start balance 100k, 5% => floor at 95,000.
    _, st = _run(eng, Tick.of(_ts(), balance=SIZE, equity=SIZE))
    assert st.daily.floor == Decimal("95000")
    assert st.daily.allowance == Decimal("5000")


def test_daily_breach_fires_exactly_at_floor():
    eng = _engine(_spec())
    mem = eng.init_memory()
    res = eng.process(mem, Tick.of(_ts(), balance=SIZE, equity=SIZE))  # set day anchor
    # equity at exactly the floor -> breached (room <= 0).
    res = eng.process(res.memory, Tick.of(_ts(13), balance=SIZE, equity=Decimal("95000")))
    assert res.state.daily.room == Decimal("0")
    assert res.state.breached is True
    # one cent above the floor -> safe.
    res2 = eng.process(res.memory, Tick.of(_ts(14), balance=SIZE, equity=Decimal("95000.01")))
    assert res2.state.daily.room == Decimal("0.01")
    assert res2.state.breached is False


def test_daily_anchor_higher_of_balance_equity():
    spec = _spec(daily_loss=DailyLossRule.of(
        5, basis=Basis.EQUITY, anchor=DailyAnchor.HIGHER_OF_BALANCE_EQUITY))
    eng = _engine(spec)
    # equity floating above balance at reset -> anchor uses the higher (equity).
    _, st = _run(eng, Tick.of(_ts(), balance=SIZE, equity=Decimal("100500")))
    assert st.day_anchor == Decimal("100500")
    assert st.daily.floor == Decimal("95500")  # 100500 - 5000


def test_daily_basis_balance_vs_equity():
    spec = _spec(daily_loss=DailyLossRule.of(5, basis=Basis.BALANCE,
                                             anchor=DailyAnchor.DAY_START_BALANCE))
    eng = _engine(spec)
    mem = eng.init_memory()
    res = eng.process(mem, Tick.of(_ts(), balance=SIZE, equity=SIZE))
    # equity dives but balance holds -> BALANCE basis does not breach.
    res = eng.process(res.memory, Tick.of(_ts(13), balance=SIZE, equity=Decimal("94000")))
    assert res.state.daily.room > 0  # not breached on balance basis


# -- max drawdown: static, trailing, trailing-then-lock -----------------------

def test_static_dd_floor_fixed_from_initial():
    eng = _engine(_spec(max_drawdown=MaxDrawdownRule.of(10, type=DrawdownType.STATIC)))
    # profit doesn't move a static floor: stays at 90,000.
    _, st = _run(eng,
                 Tick.of(_ts(), balance=SIZE, equity=SIZE),
                 Tick.of(_ts(13), balance=Decimal("108000"), equity=Decimal("108000")))
    assert st.max_dd.floor == Decimal("90000")


def test_trailing_dd_follows_peak_equity():
    spec = _spec(max_drawdown=MaxDrawdownRule.of(
        10, type=DrawdownType.TRAILING, anchor_ref=DrawdownAnchorRef.PEAK_EQUITY))
    eng = _engine(spec)
    _, st = _run(eng,
                 Tick.of(_ts(), balance=SIZE, equity=SIZE),
                 Tick.of(_ts(13), balance=Decimal("105000"), equity=Decimal("105000")))
    # peak 105k, 10% allowance 10,000 => trailing floor at 95,000.
    assert st.peak == Decimal("105000")
    assert st.max_dd.floor == Decimal("95000")


def test_trailing_then_lock_freezes_floor_at_initial():
    spec = _spec(max_drawdown=MaxDrawdownRule.of(
        10, type=DrawdownType.TRAILING, anchor_ref=DrawdownAnchorRef.PEAK_EQUITY,
        locks_at_initial=True))
    eng = _engine(spec)
    # climb past +10% so the trailing floor reaches initial balance and locks.
    _, st = _run(eng,
                 Tick.of(_ts(), balance=SIZE, equity=SIZE),
                 Tick.of(_ts(13), balance=Decimal("112000"), equity=Decimal("112000")))
    assert st.max_dd.floor == Decimal("100000")  # frozen at initial size
    # further gains must NOT move the locked floor.
    res = eng.process(eng.init_memory(), Tick.of(_ts(), balance=SIZE, equity=SIZE))
    res = eng.process(res.memory, Tick.of(_ts(13), balance=Decimal("112000"), equity=Decimal("112000")))
    res = eng.process(res.memory, Tick.of(_ts(14), balance=Decimal("120000"), equity=Decimal("120000")))
    assert res.state.max_dd.floor == Decimal("100000")


# -- consistency + pass plan ---------------------------------------------------

def test_consistency_ceiling_and_share():
    spec = _spec(consistency=ConsistencyRule.of("0.40", basis=ConsistencyBasis.TARGET))
    eng = _engine(spec)
    # target = 8% of 100k = 8000; cap 40% => single-day ceiling 3200.
    mem = eng.init_memory()
    # day 1: realize +2000.
    res = eng.process(mem, Tick.of(_ts(day=1), balance=Decimal("102000"),
                                   equity=Decimal("102000"), traded_today=True,
                                   day_realized_pnl=Decimal("2000")))
    cons = res.state.challenge.consistency
    assert cons is not None
    assert cons.ceiling == Decimal("3200")          # 0.40 * 8000
    assert cons.biggest_day == Decimal("2000")
    assert cons.share == Decimal("0.25")            # 2000 / 8000 (TARGET basis)


def test_pass_plan_band_and_on_track():
    spec = _spec(min_days=MinDaysRule.of(4))
    eng = _engine(spec)
    _, st = _run(eng, Tick.of(_ts(), balance=SIZE, equity=SIZE, traded_today=True))
    plan = st.challenge.plan
    assert plan is not None
    # min_days=4, this tick traded -> 1 day traded, 3 owed. days_left=3, spread=4.
    assert st.challenge.days_owed == 3
    assert plan.days_left == 3
    assert plan.band_lo == Decimal("2000")          # 8000 / 4 (spread)
    assert plan.band_hi == Decimal("8000") / Decimal("3")   # 8000 / 3 days_left


def test_passed_requires_target_and_min_days():
    spec = _spec(min_days=MinDaysRule.of(2))
    eng = _engine(spec)
    # hit target on a single day -> profit ok but min-days owed -> not passed.
    _, st = _run(eng, Tick.of(_ts(day=1), balance=Decimal("108000"),
                              equity=Decimal("108000"), traded_today=True,
                              day_realized_pnl=Decimal("8000")))
    assert st.challenge.to_go <= 0
    assert st.challenge.days_owed == 1
    assert st.challenge.to_dict()["passed"] is False


# -- min trading days ----------------------------------------------------------

def test_min_days_any_trade_counts_each_traded_day():
    spec = _spec(min_days=MinDaysRule.of(3, day_counts_if=TradingDayRule.ANY_TRADE))
    eng = _engine(spec)
    _, st = _run(eng,
                 Tick.of(_ts(day=1), balance=SIZE, equity=SIZE, traded_today=True),
                 Tick.of(_ts(day=2), balance=SIZE, equity=SIZE, traded_today=True))
    assert st.challenge.days_traded == 2
    assert st.challenge.days_owed == 1


# -- personal stricter-only clamp ---------------------------------------------

def test_personal_daily_floor_never_below_firm_floor():
    eng = _engine(_spec(), personal=PersonalRules.of(daily_frac="0.5"))
    _, st = _run(eng, Tick.of(_ts(), balance=SIZE, equity=SIZE))
    assert st.personal_daily is not None
    # personal allowance is half the firm's 5000 => floor at 97,500 (tighter).
    assert st.personal_daily.floor == Decimal("97500")
    assert st.personal_daily.floor >= st.daily.floor


def test_personal_clamp_floor_cannot_exceed_firm():
    # daily_frac clamps to <= 1; even a request for 2.0 gives a personal floor that
    # equals (never looser than) the firm floor.
    eng = _engine(_spec(), personal=PersonalRules.of(daily_frac="2.0"))
    _, st = _run(eng, Tick.of(_ts(), balance=SIZE, equity=SIZE))
    # frac clamped to 1 -> personal layer suppressed (>=1 returns None).
    assert st.personal_daily is None


def test_personal_amber_trips_before_firm_red():
    eng = _engine(_spec(), personal=PersonalRules.of(daily_frac="0.5"))
    mem = eng.init_memory()
    res = eng.process(mem, Tick.of(_ts(), balance=SIZE, equity=SIZE))
    # drop to 97,400: below personal floor (97,500) but above firm floor (95,000).
    res = eng.process(res.memory, Tick.of(_ts(13), balance=SIZE, equity=Decimal("97400")))
    personal_v = [v for v in res.state.violations if not v.is_firm]
    firm_v = [v for v in res.state.violations if v.is_firm]
    assert personal_v and not firm_v
    assert res.state.breached is False


# -- status tiering ------------------------------------------------------------

def test_status_tiers_on_consumed_ratio():
    eng = _engine(_spec())
    mem = eng.init_memory()
    res = eng.process(mem, Tick.of(_ts(), balance=SIZE, equity=SIZE))
    anchor_mem = res.memory
    # allowance 5000. consume 60% (-3000) -> CAUTION (>=0.50).
    res = eng.process(anchor_mem, Tick.of(_ts(13), balance=SIZE, equity=Decimal("97000")))
    assert res.state.status == Status.CAUTION
    # consume 80% (-4000) -> WARNING (>=0.75).
    res = eng.process(anchor_mem, Tick.of(_ts(14), balance=SIZE, equity=Decimal("96000")))
    assert res.state.status == Status.WARNING
    # consume 95% (-4750) -> CRITICAL (>=0.90).
    res = eng.process(anchor_mem, Tick.of(_ts(15), balance=SIZE, equity=Decimal("95250")))
    assert res.state.status == Status.CRITICAL


def test_spec_validate_rejects_loose_daily():
    with pytest.raises(ValueError):
        _spec(daily_loss=DailyLossRule.of(12),
              max_drawdown=MaxDrawdownRule.of(10)).validate()
