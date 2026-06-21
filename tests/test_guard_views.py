"""Tests for the Guard read-models and rule_spec_json hydration."""

from datetime import datetime, timezone
from decimal import Decimal

from app.domains.guard.account import GuardConfig, PersonalRules
from app.domains.guard.engine import Engine
from app.domains.guard.spec import FirmRuleSpec
from app.domains.guard.tick import Tick
from app.domains.guard.views import copilot_view, monitor_view, rules_view

RULE_JSON = {
    "firm": "FTMO",
    "version": "user",
    "daily_loss": {"pct": 0.05, "basis": "EQUITY", "anchor": "HIGHER_OF_BALANCE_EQUITY",
                   "reset_hour": 0, "reset_tz": "Europe/Prague"},
    "max_drawdown": {"pct": 0.10, "type": "TRAILING", "anchor_ref": "PEAK_EQUITY",
                     "locks_at_initial": True},
    "profit_target": {"pct": 0.08},
    "min_days": {"count": 4, "day_counts_if": "ANY_TRADE"},
    "consistency": {"cap": 0.40, "basis": "TARGET"},
}


def _cfg(personal=None):
    spec = FirmRuleSpec.from_json(RULE_JSON)
    spec.validate()
    return GuardConfig.of("acc-1", spec, Decimal("100000"),
                          personal=personal or PersonalRules())


def _state(cfg):
    eng = Engine(cfg)
    mem = eng.init_memory()
    res = eng.process(mem, Tick.of(
        datetime(2026, 6, 1, 12, tzinfo=timezone.utc),
        balance=Decimal("100000"), equity=Decimal("99000")))
    return res.state


def test_from_json_hydrates_full_spec():
    spec = FirmRuleSpec.from_json(RULE_JSON)
    assert spec.daily_loss.pct == Decimal("0.05")
    assert spec.daily_loss.reset_tz == "Europe/Prague"
    assert spec.max_drawdown.locks_at_initial is True
    assert spec.min_days.count == 4
    assert spec.consistency.cap == Decimal("0.40")


def test_monitor_view_shape():
    v = monitor_view(_state(_cfg()))
    assert v["lines"]["daily"]["label"] == "Daily loss"
    assert "room" in v["lines"]["daily"]
    assert "consumed_pct" in v["lines"]["daily"]
    assert isinstance(v["nudge"], str)
    assert v["breached"] is False


def test_copilot_view_has_plan_and_consistency():
    v = copilot_view(_state(_cfg()))
    assert "plan" in v and v["plan"] is not None
    assert "consistency" in v and v["consistency"] is not None
    assert v["target"] == 8000.0


def test_rules_view_contract_is_readonly_and_disclaims():
    v = rules_view(_cfg())
    contract = v["contract"]
    assert "read-only" in contract
    assert "does not guarantee" in contract
    assert v["firm"]["daily_reset"] == "00:00 Europe/Prague"


def test_personal_lines_present_when_stricter():
    v = monitor_view(_state(_cfg(personal=PersonalRules.of(daily_frac="0.5"))))
    assert v["lines"]["personalDaily"] is not None
