import uuid
from typing import Optional

from fastapi import HTTPException, status
from sqlalchemy.orm import Session

from app.domains.notifications import repository as repo
from app.domains.notifications.models import NotificationType
from app.domains.users.models import User


def list_notifications(db: Session, user: User, limit: int = 50) -> dict:
    items = repo.list_for_user(db, user_id=user.id, limit=limit)
    return {"items": items, "unread_count": repo.unread_count(db, user_id=user.id)}


def get_unread_count(db: Session, user: User) -> int:
    return repo.unread_count(db, user_id=user.id)


def mark_read(db: Session, user: User, notification_id: uuid.UUID) -> None:
    ok = repo.mark_read(db, user_id=user.id, notification_id=notification_id)
    if not ok:
        raise HTTPException(
            status_code=status.HTTP_404_NOT_FOUND, detail="Notification not found."
        )
    db.commit()


def mark_all_read(db: Session, user: User) -> int:
    count = repo.mark_all_read(db, user_id=user.id)
    db.commit()
    return count


def notify(
    db: Session,
    *,
    user_id: uuid.UUID,
    title: str,
    body: Optional[str] = None,
    link: Optional[str] = None,
    type: NotificationType = NotificationType.info,
):
    """Create a notification for a user. Callers own the commit so this can be
    bundled into an existing transaction (e.g. after a sync completes)."""
    return repo.create(
        db, user_id=user_id, title=title, body=body, link=link, type=type
    )
