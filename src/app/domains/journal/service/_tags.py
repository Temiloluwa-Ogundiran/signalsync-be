import uuid
from typing import Optional

from fastapi import HTTPException, status
from sqlalchemy import select
from sqlalchemy.orm import Session

from app.domains.accounts.models import Trade
from app.domains.journal import repository as journal_repo
from app.domains.users.models import User


def list_user_tags_config(db: Session, user: User) -> list:
    return journal_repo.list_categories_with_options(db, user_id=user.id)


def create_custom_category(db: Session, user: User, title: str):
    title = title.strip()
    if not title:
        raise HTTPException(status_code=status.HTTP_400_BAD_REQUEST, detail="Category title cannot be empty.")
    if len(title) > 100:
        raise HTTPException(status_code=status.HTTP_400_BAD_REQUEST, detail="Category title cannot exceed 100 characters.")
    category = journal_repo.create_category(db, user_id=user.id, title=title)
    db.commit()
    db.refresh(category)
    return category


def delete_custom_category(db: Session, user: User, category_id: uuid.UUID) -> None:
    category = journal_repo.get_category_by_id(db, category_id)
    if not category:
        raise HTTPException(status_code=status.HTTP_404_NOT_FOUND, detail="Category not found.")
    if category.is_system or category.user_id != user.id:
        raise HTTPException(status_code=status.HTTP_403_FORBIDDEN, detail="You do not have permission to delete this category.")
    journal_repo.delete_category(db, user_id=user.id, category_id=category_id)
    db.commit()


def create_custom_option(
    db: Session,
    user: User,
    category_id: uuid.UUID,
    value: str,
    color: Optional[str] = None,
):
    category = journal_repo.get_category_by_id(db, category_id)
    if not category:
        raise HTTPException(status_code=status.HTTP_404_NOT_FOUND, detail="Category not found.")
    if not category.is_system and category.user_id != user.id:
        raise HTTPException(status_code=status.HTTP_403_FORBIDDEN, detail="You do not have permission to add options to this category.")
    value = value.strip()
    if not value:
        raise HTTPException(status_code=status.HTTP_400_BAD_REQUEST, detail="Option value cannot be empty.")
    if len(value) > 100:
        raise HTTPException(status_code=status.HTTP_400_BAD_REQUEST, detail="Option value cannot exceed 100 characters.")
    if color:
        color = color.strip()
        if not color.startswith("#") or len(color) != 7:
            raise HTTPException(status_code=status.HTTP_400_BAD_REQUEST, detail="Color must be a valid 7-character hex code (e.g. #3b82f6).")
    option = journal_repo.create_option(db, user_id=user.id, category_id=category_id, value=value, color=color)
    db.commit()
    db.refresh(option)
    return option


def delete_custom_option(db: Session, user: User, option_id: uuid.UUID) -> None:
    option = journal_repo.get_option_by_id(db, option_id)
    if not option:
        raise HTTPException(status_code=status.HTTP_404_NOT_FOUND, detail="Option not found.")
    if option.user_id is None or option.user_id != user.id:
        raise HTTPException(status_code=status.HTTP_403_FORBIDDEN, detail="You do not have permission to delete this option.")
    journal_repo.delete_option(db, user_id=user.id, option_id=option_id)
    db.commit()


def _validate_trade_ownership(db: Session, user_id: uuid.UUID, trade_id: uuid.UUID) -> Trade:
    trade = db.execute(select(Trade).where(Trade.id == trade_id)).scalar_one_or_none()
    if not trade:
        raise HTTPException(status_code=status.HTTP_404_NOT_FOUND, detail="Trade not found.")
    if trade.account.user_id != user_id:
        raise HTTPException(status_code=status.HTTP_403_FORBIDDEN, detail="You do not have permission to access this trade.")
    return trade


def get_trade_tags(db: Session, user: User, trade_id: uuid.UUID) -> list:
    _validate_trade_ownership(db, user_id=user.id, trade_id=trade_id)
    return journal_repo.get_trade_tag_options(db, trade_id=trade_id)


def update_trade_tags(db: Session, user: User, trade_id: uuid.UUID, option_ids: list[uuid.UUID]) -> list:
    _validate_trade_ownership(db, user_id=user.id, trade_id=trade_id)
    for opt_id in option_ids:
        option = journal_repo.get_option_by_id(db, opt_id)
        if not option:
            raise HTTPException(status_code=status.HTTP_404_NOT_FOUND, detail=f"Option {opt_id} not found.")
        category = journal_repo.get_category_by_id(db, option.category_id)
        if not category or (not category.is_system and category.user_id != user.id):
            raise HTTPException(status_code=status.HTTP_403_FORBIDDEN, detail=f"You do not have permission to use option {option.value}.")
    journal_repo.update_trade_tags(db, trade_id=trade_id, option_ids=option_ids)
    db.commit()
    return journal_repo.get_trade_tag_options(db, trade_id=trade_id)


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
