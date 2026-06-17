import uuid
from datetime import datetime, timezone
from typing import Optional

from sqlalchemy import and_, func, select, update
from sqlalchemy.orm import Session

from app.domains.notifications.models import Notification, NotificationType


def list_for_user(db: Session, user_id: uuid.UUID, limit: int = 50) -> list[Notification]:
    stmt = (
        select(Notification)
        .where(Notification.user_id == user_id)
        .order_by(Notification.created_at.desc())
        .limit(limit)
    )
    return list(db.execute(stmt).scalars())


def unread_count(db: Session, user_id: uuid.UUID) -> int:
    stmt = select(func.count()).select_from(Notification).where(
        and_(Notification.user_id == user_id, Notification.read_at.is_(None))
    )
    return int(db.execute(stmt).scalar_one())


def get_by_id(db: Session, notification_id: uuid.UUID) -> Notification | None:
    return db.execute(
        select(Notification).where(Notification.id == notification_id)
    ).scalar_one_or_none()


def create(
    db: Session,
    *,
    user_id: uuid.UUID,
    title: str,
    body: Optional[str] = None,
    link: Optional[str] = None,
    type: NotificationType = NotificationType.info,
) -> Notification:
    n = Notification(
        user_id=user_id,
        title=title,
        body=body,
        link=link,
        type=type,
    )
    db.add(n)
    db.flush()
    return n


def mark_read(db: Session, user_id: uuid.UUID, notification_id: uuid.UUID) -> bool:
    n = db.execute(
        select(Notification).where(
            and_(Notification.id == notification_id, Notification.user_id == user_id)
        )
    ).scalar_one_or_none()
    if not n:
        return False
    if n.read_at is None:
        n.read_at = datetime.now(timezone.utc)
        db.flush()
    return True


def mark_all_read(db: Session, user_id: uuid.UUID) -> int:
    result = db.execute(
        update(Notification)
        .where(
            and_(Notification.user_id == user_id, Notification.read_at.is_(None))
        )
        .values(read_at=datetime.now(timezone.utc))
    )
    db.flush()
    return result.rowcount or 0
