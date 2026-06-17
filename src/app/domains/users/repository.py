import uuid
from datetime import datetime, timedelta
from typing import Optional
from uuid import UUID

from sqlalchemy import select, update as sa_update
from sqlalchemy.orm import Session

from app.domains.users.models import User


def get_by_id(db: Session, user_id: UUID) -> Optional[User]:
    stmt = select(User).where(User.id == user_id, User.is_deleted.is_(False))
    return db.execute(stmt).scalar_one_or_none()


def get_by_email(db: Session, email: str) -> Optional[User]:
    stmt = select(User).where(User.email == email.lower(), User.is_deleted.is_(False))
    return db.execute(stmt).scalar_one_or_none()


def create(
    db: Session,
    *,
    email: str,
    hashed_password: str,
    display_name: Optional[str] = None,
) -> User:
    user = User(
        email=email.lower(),
        hashed_password=hashed_password,
        display_name=display_name,
    )
    db.add(user)
    db.flush()
    return user


def touch_last_active_at_if_stale(
    db: Session,
    *,
    user_id: UUID,
    observed_at: datetime,
    min_interval_seconds: int,
) -> bool:
    cutoff = observed_at - timedelta(seconds=min_interval_seconds)
    stmt = (
        sa_update(User)
        .where(
            User.id == user_id,
            (User.last_active_at.is_(None) | (User.last_active_at < cutoff)),
        )
        .values(last_active_at=observed_at)
    )
    result = db.execute(stmt)
    return bool(result.rowcount)
