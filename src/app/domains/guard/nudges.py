"""Deterministic templated nudges — the v1 "coach".

v1 uses deterministic templated nudges, not an LLM. The LLM coach is v2 and will
*narrate* this same AccountState — it never computes. Keep the language as
"guardrails that catch the slow bleeds", never "you can't blow up". Guard v1 is
read-only: we warn, we never claim to close the trade.
"""

from decimal import Decimal

from .enums import Status
from .money import q_money
from .state import AccountState


def nudge_for(state: AccountState) -> str:
    """One plain-English line describing the most important thing right now."""
    # Firm breach always wins. (Read-only: we report it, we cannot prevent it.)
    firm = [v for v in state.violations if v.is_firm]
    if firm:
        return "🔴 Firm line breached. " + firm[0].message
    personal = [v for v in state.violations if not v.is_firm]
    if personal:
        return "🟠 " + personal[0].message + " Stepping back here keeps the firm line intact."

    if state.status == Status.PAUSED:
        return ("⏸️ Soft breach — your firm pauses trading for the day here. The "
                "account survives; it resumes at the next reset. Stand down for today.")
    if state.status == Status.CRITICAL:
        tight = _tightest(state)
        return (f"⚠️ {tight} — only {_money(_room(state, tight))} of room left. "
                "Tighten up or stand down before the line.")
    if state.status == Status.WARNING:
        tight = _tightest(state)
        return f"🟡 {tight} getting close ({_money(_room(state, tight))} left). Ease off the size."
    if state.status == Status.CAUTION:
        return "🔵 Drawing down, still plenty of room. Trade your plan."

    # Healthy — coach toward the pass plan.
    plan = state.challenge.plan
    if plan and state.challenge.to_go > 0 and plan.band_hi > 0:
        return (f"🟢 On plan. {_money(state.challenge.to_go)} to target; "
                f"aim for ~{_money(plan.band_lo)}–{_money(plan.band_hi)}/day"
                + (f", and keep any single day under {_money(plan.ceil_day)} "
                   "for consistency." if plan.ceil_day > 0 else "."))
    if state.challenge.to_dict()["passed"]:
        return "🏆 Target hit and days satisfied — challenge passed. Protect it now."
    return "🟢 Healthy. Trade your plan."


def _tightest(state: AccountState) -> str:
    if state.daily.ratio >= state.max_dd.ratio:
        return "Daily loss limit"
    return "Max drawdown"


def _room(state: AccountState, which: str) -> Decimal:
    return state.daily.room if which.startswith("Daily") else state.max_dd.room


def _money(v: Decimal) -> str:
    return f"${q_money(v):,.2f}"
