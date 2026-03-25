import uuid
from datetime import date
from typing import Optional

from sqlalchemy import select
from sqlalchemy.orm import Session

from app.models.daily_journal import DailyJournal


def get_by_id(db: Session, daily_journal_id: uuid.UUID) -> Optional[DailyJournal]:
    stmt = select(DailyJournal).where(DailyJournal.id == daily_journal_id)
    return db.execute(stmt).scalar_one_or_none()


def get_by_account_and_date(
    db: Session,
    *,
    account_id: uuid.UUID,
    trading_date: date,
) -> Optional[DailyJournal]:
    stmt = select(DailyJournal).where(
        DailyJournal.account_id == account_id,
        DailyJournal.trading_date == trading_date,
    )
    return db.execute(stmt).scalar_one_or_none()


def create(
    db: Session,
    *,
    account_id: uuid.UUID,
    trading_date: date,
) -> DailyJournal:
    daily_journal = DailyJournal(account_id=account_id, trading_date=trading_date)
    db.add(daily_journal)
    db.flush()
    return daily_journal


def list_feed(
    db: Session,
    *,
    account_id: uuid.UUID,
    limit: int,
    cursor_daily_journal_id: Optional[uuid.UUID],
) -> list[DailyJournal]:
    stmt = (
        select(DailyJournal)
        .where(DailyJournal.account_id == account_id)
        .order_by(DailyJournal.trading_date.desc(), DailyJournal.id.desc())
        .limit(limit)
    )

    if cursor_daily_journal_id is not None:
        cursor_stmt = select(DailyJournal.trading_date, DailyJournal.id).where(
            DailyJournal.id == cursor_daily_journal_id,
            DailyJournal.account_id == account_id,
        )
        cursor_row = db.execute(cursor_stmt).one_or_none()
        if cursor_row is not None:
            cursor_trading_date, cursor_id = cursor_row
            stmt = stmt.where(
                (DailyJournal.trading_date < cursor_trading_date)
                | ((DailyJournal.trading_date == cursor_trading_date) & (DailyJournal.id < cursor_id))
            )

    return list(db.execute(stmt).scalars().all())
