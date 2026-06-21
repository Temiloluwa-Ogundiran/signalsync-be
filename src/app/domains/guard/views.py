"""Read-models over AccountState — the surfaces the FE renders.

The rule: show derived insight, never re-display data the trader already has. Each
view is a pure projection of the one AccountState (+ config); none compute. A future
LLM coach would be a fourth such projection — it narrates, never computes.
"""

from typing import List, Optional

from .account import GuardConfig
from .nudges import nudge_for
from .state import AccountState


def monitor_view(state: AccountState) -> dict:
    """Live buffers — the distance-to-line nobody else shows. Not raw equity."""
    d = state.to_dict()
    return {
        "account_id": state.account_id,
        "ts": d["ts"],
        "status": d["status"],
        "equity": d["equity"],
        "balance": d["balance"],
        "peak": d["peak"],
        "lines": {
            "daily": _line(d["buffers"]["daily"], "Daily loss"),
            "maxDD": _line(d["buffers"]["maxDD"], "Max drawdown"),
            "personalDaily": _line(d["buffers"]["personalDaily"], "Personal daily")
            if d["buffers"]["personalDaily"] else None,
            "personalDD": _line(d["buffers"]["personalDD"], "Personal drawdown")
            if d["buffers"]["personalDD"] else None,
        },
        "breached": d["breached"],
        "nudge": nudge_for(state),
    }


def copilot_view(state: AccountState) -> dict:
    """The pass plan + consistency trajectory — catches the profitable-but-denied."""
    c = state.to_dict()["challenge"]
    return {
        "account_id": state.account_id,
        "ts": state.ts.isoformat(),
        "status": state.status.value,
        "target": c["target"],
        "profit": c["profit"],
        "to_go": c["to_go"],
        "passed": c["passed"],
        "days_traded": c["days_traded"],
        "days_owed": c["days_owed"],
        "consistency": c["consistency"],
        "plan": c["plan"],
        "nudge": nudge_for(state),
    }


def rules_view(config: GuardConfig) -> dict:
    """The config surface — firm spec (red line) + personal clamp (amber line).

    This is the screenshot-and-pin contract moment: a plain-English statement of
    exactly what the guard will do.
    """
    spec = config.spec
    p = config.personal
    return {
        "account_id": config.id,
        "size": float(config.size),
        "firm": {
            "name": spec.firm,
            "version": spec.version,
            "daily_loss_pct": float(spec.daily_loss.pct * 100),
            "daily_basis": spec.daily_loss.basis.value,
            "daily_reset": f"{spec.daily_loss.reset_hour:02d}:00 {spec.daily_loss.reset_tz}",
            "max_drawdown_pct": float(spec.max_drawdown.pct * 100),
            "max_drawdown_type": spec.max_drawdown.type.value,
            "profit_target_pct": float(spec.profit_target.pct * 100),
            "min_days": spec.min_days.count if spec.min_days else 0,
            "consistency_cap_pct": float(spec.consistency.cap * 100)
            if spec.consistency else None,
        },
        "personal": {
            "daily_frac": float(p.daily_frac),
            "dd_frac": float(p.dd_frac),
        },
        "contract": _contract(config),
    }


def _line(b: dict, label: str) -> dict:
    return {
        "label": label,
        "floor": b["floor"],
        "room": b["room"],
        "consumed_pct": round(b["ratio"] * 100, 1),
        "breached": b["breached"],
    }


def _contract(config: GuardConfig) -> str:
    spec, p = config.spec, config.personal
    daily = float(spec.daily_loss.pct * p.daily_frac * 100)
    dd = float(spec.max_drawdown.pct * p.dd_frac * 100)
    base = (f"Partna Guard watches your {spec.firm} account around the clock. "
            f"If your daily loss reaches {daily:.1f}% or your drawdown reaches "
            f"{dd:.1f}%, it will alert you immediately. "
            f"Guard is read-only — it warns, it does not close your trades. It does "
            f"not guarantee passing or prevent all losses; outages, gaps and news "
            f"spikes are disclaimed.")
    return base
