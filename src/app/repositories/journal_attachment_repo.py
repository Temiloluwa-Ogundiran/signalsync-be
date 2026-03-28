import uuid
from typing import Optional

from sqlalchemy import select
from sqlalchemy.orm import Session

from app.models.journal_attachment import JournalAttachment


def create(
    db: Session,
    *,
    message_id: uuid.UUID,
    storage_path: str,
    media_type: str,
    mime_type: str,
    original_filename: Optional[str],
    caption: Optional[str],
) -> JournalAttachment:
    attachment = JournalAttachment(
        message_id=message_id,
        storage_path=storage_path,
        media_type=media_type,
        mime_type=mime_type,
        original_filename=original_filename,
        caption=caption,
    )
    db.add(attachment)
    db.flush()
    return attachment


def list_by_message_id(db: Session, message_id: uuid.UUID) -> list[JournalAttachment]:
    stmt = (
        select(JournalAttachment)
        .where(JournalAttachment.message_id == message_id)
        .order_by(JournalAttachment.created_at.asc(), JournalAttachment.id.asc())
    )
    return list(db.execute(stmt).scalars().all())
