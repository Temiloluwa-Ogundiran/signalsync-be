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


def update_trade_rating(db: Session, user: User, trade_id: uuid.UUID, rating: int) -> int:
    _validate_trade_ownership(db, user_id=user.id, trade_id=trade_id)
    tj, _ = journal_repo.get_or_create_trade_journal_by_trade_id(db, trade_id=trade_id, daily_journal_id=None)
    tj.rating = rating
    db.commit()
    return rating


def update_trade_assessment(
    db: Session,
    user: User,
    trade_id: uuid.UUID,
    execution_quality: Optional[int] = None,
    setup_quality: Optional[int] = None,
    discipline_score: Optional[int] = None,
) -> dict:
    _validate_trade_ownership(db, user_id=user.id, trade_id=trade_id)
    tj, _ = journal_repo.get_or_create_trade_journal_by_trade_id(db, trade_id=trade_id, daily_journal_id=None)
    if execution_quality is not None:
        tj.execution_quality = execution_quality
    if setup_quality is not None:
        tj.setup_quality = setup_quality
    if discipline_score is not None:
        tj.discipline_score = discipline_score
    db.commit()
    return {
        "execution_quality": tj.execution_quality,
        "setup_quality": tj.setup_quality,
        "discipline_score": tj.discipline_score,
    }
