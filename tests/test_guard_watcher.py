"""Pure-logic tests for the watcher's memory (de)serialization and the
escalation-only alert gate. No DB, no network."""

from datetime import datetime, timezone
from decimal import Decimal

from app.domains.guard import alerts as guard_alerts
from app.domains.guard.account import GuardConfig
from app.domains.guard.engine import Engine
from app.domains.guard.spec import (
    DailyLossRule,
    FirmRuleSpec,
    MaxDrawdownRule,
    ProfitTargetRule,
)
from app.domains.guard.tick import Tick
from app.domains.guard.watcher import _memory_from_json, _memory_to_json


def _engine():
    spec = FirmRuleSpec(
        firm="t", version="v",
        daily_loss=DailyLossRule.of(5),
        max_drawdown=MaxDrawdownRule.of(10),
        profit_target=ProfitTargetRule.of(8),
    )
    return Engine(GuardConfig.of("a", spec, Decimal("100000")))


def test_memory_roundtrip_survives_json():
    eng = _engine()
    mem = eng.init_memory()
    res = eng.process(mem, Tick.of(
        datetime(2026, 6, 1, 12, tzinfo=timezone.utc),
        balance=Decimal("100000"), equity=Decimal("99000"),
        traded_today=True, day_realized_pnl=Decimal("-1000")))
    restored = _memory_from_json(_memory_to_json(res.memory))
    assert restored.day_key == res.memory.day_key
    assert restored.peak == res.memory.peak
    assert restored.daily_results == res.memory.daily_results
    assert restored.trading_days == res.memory.trading_days
    assert restored.dd_floor_locked == res.memory.dd_floor_locked


def test_escalation_gate_only_fires_upward():
    assert guard_alerts._escalated(None, "CAUTION") is True
    assert guard_alerts._escalated("CAUTION", "WARNING") is True
    assert guard_alerts._escalated("WARNING", "CRITICAL") is True
    assert guard_alerts._escalated("CRITICAL", "BREACHED") is True
    # repeats and de-escalations do NOT fire.
    assert guard_alerts._escalated("WARNING", "WARNING") is False
    assert guard_alerts._escalated("CRITICAL", "CAUTION") is False
    assert guard_alerts._escalated("CAUTION", "HEALTHY") is False
    assert guard_alerts._escalated(None, "HEALTHY") is False


def test_standing_breached_beats_status():
    eng = _engine()
    mem = eng.init_memory()
    res = eng.process(mem, Tick.of(
        datetime(2026, 6, 1, 12, tzinfo=timezone.utc),
        balance=Decimal("100000"), equity=Decimal("100000")))
    res = eng.process(res.memory, Tick.of(
        datetime(2026, 6, 1, 13, tzinfo=timezone.utc),
        balance=Decimal("100000"), equity=Decimal("94000")))  # below daily floor
    assert guard_alerts._standing(res.state) == "BREACHED"
