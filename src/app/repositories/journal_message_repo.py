import uuid
from datetime import datetime, timezone
from typing import Optional

from sqlalchemy import select
from sqlalchemy.orm import Session

from app.models.journal_message import JournalMessage, JournalMessageType


def create(
    db: Session,
    *,
    daily_journal_id: Optional[uuid.UUID],
    trade_journal_id: Optional[uuid.UUID],
    author_id: Optional[uuid.UUID],
    message_type: JournalMessageType,
    content: Optional[str],
    tags: list[str],
    system_data: Optional[dict] = None,
    audio_storage_path: Optional[str] = None,
    audio_duration_seconds: Optional[int] = None,
) -> JournalMessage:
    message = JournalMessage(
        daily_journal_id=daily_journal_id,
        trade_journal_id=trade_journal_id,
        author_id=author_id,
        message_type=message_type,
        content=content,
        tags=tags,
        system_data=system_data,
        audio_storage_path=audio_storage_path,
        audio_duration_seconds=audio_duration_seconds,
    )
    db.add(message)
    db.flush()
    return message


def get_by_id(db: Session, message_id: uuid.UUID) -> Optional[JournalMessage]:
    stmt = select(JournalMessage).where(JournalMessage.id == message_id)
    return db.execute(stmt).scalar_one_or_none()


def list_by_trade_journal(db: Session, trade_journal_id: uuid.UUID) -> list[JournalMessage]:
    stmt = (
        select(JournalMessage)
        .where(JournalMessage.trade_journal_id == trade_journal_id)
        .order_by(JournalMessage.created_at.asc(), JournalMessage.id.asc())
    )
    return list(db.execute(stmt).scalars().all())


def list_by_daily_journal(db: Session, daily_journal_id: uuid.UUID) -> list[JournalMessage]:
    stmt = (
        select(JournalMessage)
        .where(JournalMessage.daily_journal_id == daily_journal_id)
        .order_by(JournalMessage.created_at.asc(), JournalMessage.id.asc())
    )
    return list(db.execute(stmt).scalars().all())


def update_content(db: Session, message: JournalMessage, content: Optional[str], tags: list[str]) -> JournalMessage:
    message.content = content
    message.tags = tags
    message.is_edited = True
    message.edited_at = datetime.now(timezone.utc)
    db.flush()
    return message


def delete(db: Session, message: JournalMessage) -> None:
    db.delete(message)
    db.flush()
