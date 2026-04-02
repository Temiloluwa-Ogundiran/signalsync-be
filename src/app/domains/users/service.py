from sqlalchemy.orm import Session

from app.domains.users import repository as user_repo


def is_username_available(db: Session, username: str) -> bool:
    """Return True if the username is not taken by any active user."""
    return user_repo.get_by_username(db, username) is None
