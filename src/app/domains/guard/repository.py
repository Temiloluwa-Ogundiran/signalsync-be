"""Guard data-access layer. Repositories flush, never commit (RULES.md).

Authorization is NOT done here — services load resources scoped by user. The
``_for_user`` helpers below return None when not owned (the service raises 404).
"""

import uuid
from datetime import date, datetime
from decimal import Decimal
from typing import Any, Dict, List, Optional

from sqlalchemy import delete, select
from sqlalchemy.orm import Session

from .models import (
    GuardAccount,
    GuardAlert,
    GuardDailyResult,
    GuardState,
    GuardTick,
)

# Keep the rolling equity window this many points per account (for the chart).
TICK_WINDOW = 120


# -- guard accounts -----------------------------------------------------------

def create_guard_account(
    db: Session,
    *,
    trading_account_id: uuid.UUID,
    user_id: uuid.UUID,
    size: Decimal,
    rule_spec_json: Dict[str, Any],
    personal_json: Optional[Dict[str, Any]],
    contract_text: Optional[str],
) -> GuardAccount:
    guard = GuardAccount(
        trading_account_id=trading_account_id,
        user_id=user_id,
        size=size,
        rule_spec_json=rule_spec_json,
        personal_json=personal_json,
        contract_text=contract_text,
    )
    db.add(guard)
    db.flush()
    return guard


def get_guard_account(db: Session, guard_id: uuid.UUID) -> Optional[GuardAccount]:
    return db.get(GuardAccount, guard_id)


def get_guard_account_for_user(
    db: Session, guard_id: uuid.UUID, user_id: uuid.UUID
) -> Optional[GuardAccount]:
    stmt = select(GuardAccount).where(
        GuardAccount.id == guard_id,
        GuardAccount.user_id == user_id,
    )
    return db.execute(stmt).scalar_one_or_none()


def get_guard_by_trading_account(
    db: Session, trading_account_id: uuid.UUID
) -> Optional[GuardAccount]:
    stmt = select(GuardAccount).where(
        GuardAccount.trading_account_id == trading_account_id
    )
    return db.execute(stmt).scalar_one_or_none()


def list_guard_accounts_for_user(
    db: Session, user_id: uuid.UUID
) -> List[GuardAccount]:
    stmt = (
        select(GuardAccount)
        .where(GuardAccount.user_id == user_id)
        .order_by(GuardAccount.created_at.desc())
    )
    return list(db.execute(stmt).scalars().all())


def list_enabled_guard_account_ids(db: Session) -> List[uuid.UUID]:
    stmt = select(GuardAccount.id).where(GuardAccount.enabled.is_(True))
    return list(db.execute(stmt).scalars().all())


def delete_guard_account(db: Session, guard: GuardAccount) -> None:
    db.delete(guard)
    db.flush()


# -- state (latest snapshot, upsert) ------------------------------------------

def get_state(db: Session, guard_id: uuid.UUID) -> Optional[GuardState]:
    stmt = select(GuardState).where(GuardState.guard_account_id == guard_id)
    return db.execute(stmt).scalar_one_or_none()


def upsert_state(
    db: Session,
    *,
    guard_id: uuid.UUID,
    ts: datetime,
    equity: Decimal,
    balance: Decimal,
    peak: Decimal,
    day_anchor: Decimal,
    status: str,
    buffers_json: Dict[str, Any],
    challenge_json: Dict[str, Any],
    memory_json: Dict[str, Any],
) -> GuardState:
    state = get_state(db, guard_id)
    if state is None:
        state = GuardState(guard_account_id=guard_id)
        db.add(state)
    state.ts = ts
    state.equity = equity
    state.balance = balance
    state.peak = peak
    state.day_anchor = day_anchor
    state.status = status
    state.buffers_json = buffers_json
    state.challenge_json = challenge_json
    state.memory_json = memory_json
    db.flush()
    return state


# -- daily results (closed-day P&L) -------------------------------------------

def list_daily_results(
    db: Session, guard_id: uuid.UUID
) -> List[GuardDailyResult]:
    stmt = (
        select(GuardDailyResult)
        .where(GuardDailyResult.guard_account_id == guard_id)
        .order_by(GuardDailyResult.result_date)
    )
    return list(db.execute(stmt).scalars().all())


def upsert_daily_result(
    db: Session,
    *,
    guard_id: uuid.UUID,
    result_date: date,
    pnl: Decimal,
    trade_count: int,
    is_trading_day: bool,
) -> GuardDailyResult:
    stmt = select(GuardDailyResult).where(
        GuardDailyResult.guard_account_id == guard_id,
        GuardDailyResult.result_date == result_date,
    )
    row = db.execute(stmt).scalar_one_or_none()
    if row is None:
        row = GuardDailyResult(guard_account_id=guard_id, result_date=result_date)
        db.add(row)
    row.pnl = pnl
    row.trade_count = trade_count
    row.is_trading_day = is_trading_day
    db.flush()
    return row


# -- ticks (short rolling window) ---------------------------------------------

def append_tick(
    db: Session, *, guard_id: uuid.UUID, ts: datetime, equity: Decimal
) -> None:
    db.add(GuardTick(guard_account_id=guard_id, ts=ts, equity=equity))
    db.flush()


def list_ticks(db: Session, guard_id: uuid.UUID) -> List[GuardTick]:
    stmt = (
        select(GuardTick)
        .where(GuardTick.guard_account_id == guard_id)
        .order_by(GuardTick.ts)
    )
    return list(db.execute(stmt).scalars().all())


def prune_ticks(db: Session, guard_id: uuid.UUID, keep: int = TICK_WINDOW) -> None:
    """Delete all but the most recent ``keep`` ticks for an account."""
    keep_ids = select(GuardTick.id).where(
        GuardTick.guard_account_id == guard_id
    ).order_by(GuardTick.ts.desc()).limit(keep).subquery()
    db.execute(
        delete(GuardTick).where(
            GuardTick.guard_account_id == guard_id,
            GuardTick.id.notin_(select(keep_ids)),
        )
    )
    db.flush()


# -- alerts -------------------------------------------------------------------

def record_alert(
    db: Session,
    *,
    guard_id: uuid.UUID,
    tier: str,
    kind: str,
    channel: str = "email",
    sent_ok: bool = False,
) -> GuardAlert:
    alert = GuardAlert(
        guard_account_id=guard_id,
        tier=tier,
        kind=kind,
        channel=channel,
        sent_ok=sent_ok,
    )
    db.add(alert)
    db.flush()
    return alert


def list_recent_alerts(
    db: Session, guard_id: uuid.UUID, limit: int = 30
) -> List[GuardAlert]:
    stmt = (
        select(GuardAlert)
        .where(GuardAlert.guard_account_id == guard_id)
        .order_by(GuardAlert.ts.desc())
        .limit(limit)
    )
    return list(db.execute(stmt).scalars().all())
