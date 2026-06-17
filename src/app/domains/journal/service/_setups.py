import uuid
from typing import Optional

from fastapi import HTTPException, status
from sqlalchemy import select
from sqlalchemy.orm import Session

from app.domains.accounts.models import Trade
from app.domains.journal import repository as journal_repo
from app.domains.users.models import User


def _validate_trade_ownership(db: Session, user_id: uuid.UUID, trade_id: uuid.UUID) -> Trade:
    trade = db.execute(select(Trade).where(Trade.id == trade_id)).scalar_one_or_none()
    if not trade:
        raise HTTPException(status_code=status.HTTP_404_NOT_FOUND, detail="Trade not found.")
    if trade.account.user_id != user_id:
        raise HTTPException(status_code=status.HTTP_403_FORBIDDEN, detail="You do not have permission to access this trade.")
    return trade


def list_setups(db: Session, user: User) -> list:
    return journal_repo.list_setups(db, user_id=user.id)


def _ensure_setup(db: Session, user_id: uuid.UUID, name: str):
    """Return the user's setup with this name, creating it if absent."""
    existing = journal_repo.get_setup_by_name(db, user_id=user_id, name=name)
    if existing:
        return existing
    return journal_repo.create_setup(db, user_id=user_id, name=name)


def create_setup(db: Session, user: User, name: str):
    name = name.strip()
    if not name:
        raise HTTPException(status_code=status.HTTP_400_BAD_REQUEST, detail="Setup name cannot be empty.")
    if len(name) > 64:
        raise HTTPException(status_code=status.HTTP_400_BAD_REQUEST, detail="Setup name cannot exceed 64 characters.")
    setup = _ensure_setup(db, user_id=user.id, name=name)
    db.commit()
    db.refresh(setup)
    return setup


def delete_setup(db: Session, user: User, setup_id: uuid.UUID) -> None:
    setup = journal_repo.get_setup_by_id(db, setup_id)
    if not setup:
        raise HTTPException(status_code=status.HTTP_404_NOT_FOUND, detail="Setup not found.")
    if setup.user_id != user.id:
        raise HTTPException(status_code=status.HTTP_403_FORBIDDEN, detail="You do not have permission to delete this setup.")
    journal_repo.delete_setup(db, user_id=user.id, setup_id=setup_id)
    db.commit()


def update_trade_setup(db: Session, user: User, trade_id: uuid.UUID, setup: Optional[str]) -> Optional[str]:
    """Set (or clear) the trade's setup. A new name is also added to the user's
    setup list so it shows up in the picker next time."""
    trade = _validate_trade_ownership(db, user_id=user.id, trade_id=trade_id)
    name = setup.strip() if setup else None
    if name:
        if len(name) > 64:
            raise HTTPException(status_code=status.HTTP_400_BAD_REQUEST, detail="Setup name cannot exceed 64 characters.")
        _ensure_setup(db, user_id=user.id, name=name)
    journal_repo.set_trade_setup(db, trade, setup=name or None)
    db.commit()
    return name or None
