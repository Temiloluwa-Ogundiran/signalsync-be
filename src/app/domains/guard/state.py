"""AccountState — the single object emitted per tick.

Every surface (the awareness dashboard, the rules surface) and a future LLM coach
are read-models over this object plus the rules config. The engine emits exactly
one per tick; it is the whole API between the deterministic core and everything
downstream. The FE renders these numbers verbatim — it never recomputes floors.
"""

from dataclasses import dataclass, field
from datetime import datetime
from decimal import Decimal
from typing import List, Optional

from .enums import RuleKind, Status
from .money import q_money, q_ratio


@dataclass(frozen=True)
class Buffer:
    """Distance from current value to a floor — the core derived insight.

    ``room`` is money left before breach (can go negative once breached).
    ``ratio`` is fraction of the allowance consumed, 0..1 (1 = at/over the line).
    """

    floor: Decimal
    room: Decimal
    ratio: Decimal
    allowance: Decimal

    def to_dict(self) -> dict:
        return {
            "floor": float(q_money(self.floor)),
            "room": float(q_money(self.room)),
            "ratio": float(q_ratio(self.ratio)),
            "allowance": float(q_money(self.allowance)),
            "breached": self.room <= 0,
        }


@dataclass(frozen=True)
class Violation:
    kind: RuleKind
    is_firm: bool          # firm red line vs personal amber line
    message: str

    def to_dict(self) -> dict:
        return {"kind": self.kind.value, "is_firm": self.is_firm, "message": self.message}


@dataclass(frozen=True)
class ConsistencyState:
    biggest_day: Decimal
    share: Decimal         # biggest_day / base
    cap: Decimal           # fraction
    ceiling: Decimal       # max a single day may earn while staying consistent
    state: Status

    def to_dict(self) -> dict:
        return {
            "biggest_day": float(q_money(self.biggest_day)),
            "share": float(q_ratio(self.share)),
            "cap": float(q_ratio(self.cap)),
            "ceiling": float(q_money(self.ceiling)),
            "state": self.state.value,
        }


@dataclass(frozen=True)
class PassPlan:
    """Pacing toward the target — the differentiator that catches the
    profitable-but-denied trader."""

    band_lo: Decimal       # suggested daily gain, low end
    band_hi: Decimal       # suggested daily gain, high end
    days_left: Optional[int]
    ceil_day: Decimal      # most you may earn today without tripping consistency
    on_track: bool

    def to_dict(self) -> dict:
        return {
            "band_lo": float(q_money(self.band_lo)),
            "band_hi": float(q_money(self.band_hi)),
            "days_left": self.days_left,
            "ceil_day": float(q_money(self.ceil_day)),
            "on_track": self.on_track,
        }


@dataclass(frozen=True)
class Challenge:
    profit: Decimal
    target: Decimal
    to_go: Decimal
    days_traded: int
    days_owed: int
    consistency: Optional[ConsistencyState]
    plan: Optional[PassPlan]

    def to_dict(self) -> dict:
        return {
            "profit": float(q_money(self.profit)),
            "target": float(q_money(self.target)),
            "to_go": float(q_money(self.to_go)),
            "days_traded": self.days_traded,
            "days_owed": self.days_owed,
            "consistency": self.consistency.to_dict() if self.consistency else None,
            "plan": self.plan.to_dict() if self.plan else None,
            "passed": self.to_go <= 0 and self.days_owed == 0,
        }


@dataclass(frozen=True)
class AccountState:
    account_id: str
    ts: datetime
    equity: Decimal
    balance: Decimal
    peak: Decimal
    day_anchor: Decimal
    status: Status
    daily: Buffer
    max_dd: Buffer
    challenge: Challenge
    violations: List[Violation] = field(default_factory=list)
    # Personal (amber) buffers, after the stricter-only clamp.
    personal_daily: Optional[Buffer] = None
    personal_dd: Optional[Buffer] = None

    @property
    def breached(self) -> bool:
        return any(v.is_firm for v in self.violations)

    def to_dict(self) -> dict:
        return {
            "account_id": self.account_id,
            "ts": self.ts.isoformat(),
            "equity": float(q_money(self.equity)),
            "balance": float(q_money(self.balance)),
            "peak": float(q_money(self.peak)),
            "day_anchor": float(q_money(self.day_anchor)),
            "status": self.status.value,
            "buffers": {
                "daily": self.daily.to_dict(),
                "maxDD": self.max_dd.to_dict(),
                "personalDaily": self.personal_daily.to_dict() if self.personal_daily else None,
                "personalDD": self.personal_dd.to_dict() if self.personal_dd else None,
            },
            "challenge": self.challenge.to_dict(),
            "violations": [v.to_dict() for v in self.violations],
            "breached": self.breached,
        }
