"""Guard business logic. Services own the transaction (commit at public entry).

Authorization lives here: every public entry loads the guard account scoped by the
current user and raises 404 if not owned.
"""

import uuid
from decimal import Decimal
from typing import Optional

from fastapi import HTTPException, status
from sqlalchemy.orm import Session

from app.domains.accounts import repository as account_repo
from app.domains.users.models import User

from . import repository as guard_repo
from .account import GuardConfig, PersonalRules
from .models import GuardAccount
from .schemas import (
    GuardAccountResponse,
    GuardEnableRequest,
    GuardUpdateRequest,
)
from .spec import FirmRuleSpec
from .views import rules_view


# -- config hydration ---------------------------------------------------------

def build_config(guard: GuardAccount) -> GuardConfig:
    """Hydrate the engine config from a stored GuardAccount."""
    spec = FirmRuleSpec.from_json(guard.rule_spec_json)
    spec.validate()
    personal = PersonalRules()
    if guard.personal_json:
        personal = PersonalRules.of(
            daily_frac=guard.personal_json.get("daily_frac", 1),
            dd_frac=guard.personal_json.get("dd_frac", 1),
        )
    return GuardConfig.of(str(guard.id), spec, guard.size, personal=personal)


# -- ownership ----------------------------------------------------------------

def _load(db: Session, *, current_user: User, guard_id: uuid.UUID) -> GuardAccount:
    guard = guard_repo.get_guard_account_for_user(db, guard_id, current_user.id)
    if guard is None:
        raise HTTPException(status_code=status.HTTP_404_NOT_FOUND,
                            detail="Guard account not found.")
    return guard


# -- enable / configure -------------------------------------------------------

def enable_guard(
    db: Session, *, current_user: User, req: GuardEnableRequest
) -> GuardAccountResponse:
    """Enable Guard on one of the user's connected trading accounts."""
    account = account_repo.get_account_by_id_for_user(
        db, req.trading_account_id, current_user.id)
    if account is None:
        raise HTTPException(status_code=status.HTTP_404_NOT_FOUND,
                            detail="Trading account not found.")

    existing = guard_repo.get_guard_by_trading_account(db, req.trading_account_id)
    if existing is not None:
        raise HTTPException(status_code=status.HTTP_409_CONFLICT,
                            detail="Guard is already enabled on this account.")

    rule_spec_json = req.rule_spec.model_dump(mode="json")
    # Fail fast on an invalid spec before persisting.
    FirmRuleSpec.from_json(rule_spec_json).validate()

    personal_json = req.personal.model_dump(mode="json") if req.personal else None
    guard = guard_repo.create_guard_account(
        db,
        trading_account_id=req.trading_account_id,
        user_id=current_user.id,
        size=req.size,
        rule_spec_json=rule_spec_json,
        personal_json=personal_json,
        contract_text=req.contract_text,
    )
    db.commit()
    db.refresh(guard)
    return _to_response(db, guard, account.display_name)


def update_guard(
    db: Session, *, current_user: User, guard_id: uuid.UUID, req: GuardUpdateRequest
) -> GuardAccountResponse:
    guard = _load(db, current_user=current_user, guard_id=guard_id)
    if req.enabled is not None:
        guard.enabled = req.enabled
    if req.size is not None:
        guard.size = req.size
    if req.rule_spec is not None:
        rule_spec_json = req.rule_spec.model_dump(mode="json")
        FirmRuleSpec.from_json(rule_spec_json).validate()
        guard.rule_spec_json = rule_spec_json
    if req.personal is not None:
        guard.personal_json = req.personal.model_dump(mode="json")
    if req.contract_text is not None:
        guard.contract_text = req.contract_text
    db.commit()
    db.refresh(guard)
    return _to_response(db, guard)


def disable_guard(
    db: Session, *, current_user: User, guard_id: uuid.UUID
) -> None:
    guard = _load(db, current_user=current_user, guard_id=guard_id)
    guard_repo.delete_guard_account(db, guard)
    db.commit()


# -- reads --------------------------------------------------------------------

def list_guards(db: Session, *, current_user: User) -> list[GuardAccountResponse]:
    guards = guard_repo.list_guard_accounts_for_user(db, current_user.id)
    out: list[GuardAccountResponse] = []
    for g in guards:
        out.append(_to_response(db, g))
    return out

def get_guard(
    db: Session, *, current_user: User, guard_id: uuid.UUID
) -> GuardAccountResponse:
    guard = _load(db, current_user=current_user, guard_id=guard_id)
    return _to_response(db, guard)


def get_monitor(
    db: Session, *, current_user: User, guard_id: uuid.UUID
) -> dict:
    """Assemble the fat awareness-dashboard payload from the latest snapshot."""
    guard = _load(db, current_user=current_user, guard_id=guard_id)
    state_row = guard_repo.get_state(db, guard.id)
    if state_row is None:
        # No poll has landed yet — return an empty-but-valid shell.
        return {
            "monitor": None,
            "challenge": None,
            "positions": [],
            "ticks": [],
            "alerts": [],
            "connection_health": guard.connection_health,
        }
    ticks = guard_repo.list_ticks(db, guard.id)
    alerts = guard_repo.list_recent_alerts(db, guard.id)
    return {
        "monitor": {
            "account_id": str(guard.id),
            "ts": state_row.ts.isoformat(),
            "status": state_row.status,
            "equity": float(state_row.equity),
            "balance": float(state_row.balance),
            "peak": float(state_row.peak),
            "lines": state_row.buffers_json.get("lines"),
            "breached": state_row.buffers_json.get("breached", False),
            "nudge": state_row.buffers_json.get("nudge"),
        },
        "challenge": state_row.challenge_json,
        "positions": state_row.buffers_json.get("positions", []),
        "ticks": [{"ts": t.ts.isoformat(), "equity": float(t.equity)} for t in ticks],
        "alerts": [
            {"ts": a.ts.isoformat(), "tier": a.tier, "kind": a.kind, "sent_ok": a.sent_ok}
            for a in alerts
        ],
        "connection_health": guard.connection_health,
    }


def get_rules(
    db: Session, *, current_user: User, guard_id: uuid.UUID
) -> dict:
    guard = _load(db, current_user=current_user, guard_id=guard_id)
    config = build_config(guard)
    rules = rules_view(config)
    if guard.contract_text:
        rules["contract"] = guard.contract_text.strip() + "\n\n" + rules["contract"]
    return {"rules": rules}


# -- helpers ------------------------------------------------------------------

def _to_response(
    db: Session, guard: GuardAccount, display_name: Optional[str] = None
) -> GuardAccountResponse:
    state_row = guard_repo.get_state(db, guard.id)
    if display_name is None:
        acc = account_repo.get_account_by_id(db, guard.trading_account_id)
        display_name = acc.display_name if acc else None
    return GuardAccountResponse(
        id=guard.id,
        trading_account_id=guard.trading_account_id,
        enabled=guard.enabled,
        size=guard.size,
        connection_health=guard.connection_health,
        last_polled_at=guard.last_polled_at,
        status=state_row.status if state_row else None,
        display_name=display_name,
    )
