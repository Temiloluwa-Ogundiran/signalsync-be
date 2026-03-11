import uuid
from typing import Optional
from uuid import UUID

from sqlalchemy.orm import Session

from app.models.user import User


def get_by_id(db: Session, user_id: UUID) -> Optional[User]:
    return db.query(User).filter(User.id == user_id, User.is_deleted == False).first()  # noqa: E712


def get_by_email(db: Session, email: str) -> Optional[User]:
    return (
        db.query(User)
        .filter(User.email == email.lower(), User.is_deleted == False)  # noqa: E712
        .first()
    )


def get_by_username(db: Session, username: str) -> Optional[User]:
    return (
        db.query(User)
        .filter(User.username == username, User.is_deleted == False)  # noqa: E712
        .first()
    )


def create(
    db: Session,
    *,
    username: str,
    email: str,
    hashed_password: str,
    display_name: Optional[str] = None,
) -> User:
    user = User(
        username=username,
        email=email.lower(),
        hashed_password=hashed_password,
        display_name=display_name,
    )
    db.add(user)
    db.flush()
    return user
