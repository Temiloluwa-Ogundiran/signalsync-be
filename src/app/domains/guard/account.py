"""GuardConfig — the per-account configuration the engine needs.

Combines the firm spec, the account size, and the optional personal-rules layer.
This is the immutable config the engine reads; the moving state lives in
EngineMemory and AccountState. (In v1 Guard is read-only, so there is no
intervention mode / armed flag here — the engine only computes.)
"""

from dataclasses import dataclass, field
from decimal import Decimal

from .money import money
from .spec import FirmRuleSpec


@dataclass(frozen=True)
class PersonalRules:
    """The amber line — stricter-only clamp of the firm allowance.

    ``daily_frac`` / ``dd_frac`` express the personal limit as a fraction of the
    firm allowance, range [0.10, 1.00]. The clamp guarantees the personal floor can
    never sit outside (looser than) the firm floor — the invariant the whole product
    rests on.
    """

    daily_frac: Decimal = Decimal(1)
    dd_frac: Decimal = Decimal(1)

    @staticmethod
    def of(daily_frac=1, dd_frac=1) -> "PersonalRules":
        return PersonalRules(daily_frac=_clamp_frac(money(daily_frac)),
                             dd_frac=_clamp_frac(money(dd_frac)))


def _clamp_frac(f: Decimal) -> Decimal:
    """Stricter-only: a personal limit may consume 10%..100% of the firm allowance.

    >1 would be looser than the firm (a footgun) -> clamped to 1.
    <0.10 is almost certainly a fat-finger -> clamped up to 0.10.
    """
    lo, hi = Decimal("0.10"), Decimal(1)
    if f > hi:
        return hi
    if f < lo:
        return lo
    return f


@dataclass(frozen=True)
class GuardConfig:
    id: str
    spec: FirmRuleSpec
    size: Decimal                  # starting balance, the % base for all rules
    personal: PersonalRules = field(default_factory=PersonalRules)

    @staticmethod
    def of(id, spec: FirmRuleSpec, size, **kw) -> "GuardConfig":
        spec.validate()
        return GuardConfig(id=str(id), spec=spec, size=money(size), **kw)
