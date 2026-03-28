import uuid
from typing import Optional

from sqlalchemy import func, select
from sqlalchemy.orm import Session

from app.models.journal_message import JournalMessage
from app.models.trade_journal import TradeJournal


def get_by_id(db: Session, trade_journal_id: uuid.UUID) -> Optional[TradeJournal]:
    stmt = select(TradeJournal).where(TradeJournal.id == trade_journal_id)
    return db.execute(stmt).scalar_one_or_none()


def get_by_trade_id(db: Session, trade_id: uuid.UUID) -> Optional[TradeJournal]:
    stmt = select(TradeJournal).where(TradeJournal.trade_id == trade_id)
    return db.execute(stmt).scalar_one_or_none()


def create(
    db: Session,
    *,
    trade_id: uuid.UUID,
    daily_journal_id: Optional[uuid.UUID],
) -> TradeJournal:
    trade_journal = TradeJournal(trade_id=trade_id, daily_journal_id=daily_journal_id)
    db.add(trade_journal)
    db.flush()
    return trade_journal


def get_message_counts_for_trade_ids(
    db: Session,
    *,
    trade_ids: list[uuid.UUID],
) -> dict[uuid.UUID, int]:
    if not trade_ids:
        return {}

    stmt = (
        select(TradeJournal.trade_id, func.count(JournalMessage.id))
        .outerjoin(JournalMessage, JournalMessage.trade_journal_id == TradeJournal.id)
        .where(TradeJournal.trade_id.in_(trade_ids))
        .group_by(TradeJournal.trade_id)
    )

    return {trade_id: int(count) for trade_id, count in db.execute(stmt).all()}
