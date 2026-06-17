import uuid
from typing import Optional

from fastapi import HTTPException, status
from sqlalchemy import select
from sqlalchemy.orm import Session

from app.domains.accounts.models import Trade
from app.domains.journal import repository as journal_repo
from app.domains.users.models import User


# ---------------------------------------------------------------------------
# Tag groups
# ---------------------------------------------------------------------------

def list_user_tags_config(db: Session, user: User) -> list:
    return journal_repo.list_groups_with_tags(db, user_id=user.id)


def create_tag_group(db: Session, user: User, name: str):
    name = name.strip()
    if not name:
        raise HTTPException(status_code=status.HTTP_400_BAD_REQUEST, detail="Group name cannot be empty.")
    if len(name) > 100:
        raise HTTPException(status_code=status.HTTP_400_BAD_REQUEST, detail="Group name cannot exceed 100 characters.")
    group = journal_repo.create_group(db, user_id=user.id, name=name)
    db.commit()
    db.refresh(group)
    return group


def update_tag_group(db: Session, user: User, group_id: uuid.UUID, name: str):
    group = journal_repo.get_group_by_id(db, group_id)
    if not group:
        raise HTTPException(status_code=status.HTTP_404_NOT_FOUND, detail="Group not found.")
    if group.is_system or group.user_id != user.id:
        raise HTTPException(status_code=status.HTTP_403_FORBIDDEN, detail="You do not have permission to edit this group.")
    name = name.strip()
    if not name:
        raise HTTPException(status_code=status.HTTP_400_BAD_REQUEST, detail="Group name cannot be empty.")
    if len(name) > 100:
        raise HTTPException(status_code=status.HTTP_400_BAD_REQUEST, detail="Group name cannot exceed 100 characters.")
    group = journal_repo.update_group(db, group, name=name)
    db.commit()
    db.refresh(group)
    return group


def delete_tag_group(db: Session, user: User, group_id: uuid.UUID) -> None:
    group = journal_repo.get_group_by_id(db, group_id)
    if not group:
        raise HTTPException(status_code=status.HTTP_404_NOT_FOUND, detail="Group not found.")
    if group.is_system or group.user_id != user.id:
        raise HTTPException(status_code=status.HTTP_403_FORBIDDEN, detail="You do not have permission to delete this group.")
    journal_repo.delete_group(db, user_id=user.id, group_id=group_id)
    db.commit()


def reorder_tag_groups(db: Session, user: User, ids: list[uuid.UUID]) -> None:
    journal_repo.reorder_groups(db, user_id=user.id, ids=ids)
    db.commit()


# ---------------------------------------------------------------------------
# Tags
# ---------------------------------------------------------------------------

def create_tag(
    db: Session,
    user: User,
    group_id: uuid.UUID,
    name: str,
    color: Optional[str] = None,
):
    group = journal_repo.get_group_by_id(db, group_id)
    if not group:
        raise HTTPException(status_code=status.HTTP_404_NOT_FOUND, detail="Group not found.")
    if not group.is_system and group.user_id != user.id:
        raise HTTPException(status_code=status.HTTP_403_FORBIDDEN, detail="You do not have permission to add tags to this group.")
    name = name.strip()
    if not name:
        raise HTTPException(status_code=status.HTTP_400_BAD_REQUEST, detail="Tag name cannot be empty.")
    if len(name) > 100:
        raise HTTPException(status_code=status.HTTP_400_BAD_REQUEST, detail="Tag name cannot exceed 100 characters.")
    color = _normalize_color(color)
    tag = journal_repo.create_tag(db, user_id=user.id, group_id=group_id, name=name, color=color)
    db.commit()
    db.refresh(tag)
    return tag


def update_tag(
    db: Session,
    user: User,
    tag_id: uuid.UUID,
    name: Optional[str] = None,
    color: Optional[str] = None,
):
    tag = journal_repo.get_tag_by_id(db, tag_id)
    if not tag:
        raise HTTPException(status_code=status.HTTP_404_NOT_FOUND, detail="Tag not found.")
    if tag.user_id is None or tag.user_id != user.id:
        raise HTTPException(status_code=status.HTTP_403_FORBIDDEN, detail="You do not have permission to edit this tag.")
    if name is not None:
        name = name.strip()
        if not name:
            raise HTTPException(status_code=status.HTTP_400_BAD_REQUEST, detail="Tag name cannot be empty.")
        if len(name) > 100:
            raise HTTPException(status_code=status.HTTP_400_BAD_REQUEST, detail="Tag name cannot exceed 100 characters.")
    color = _normalize_color(color)
    tag = journal_repo.update_tag(db, tag, name=name, color=color)
    db.commit()
    db.refresh(tag)
    return tag


def delete_tag(db: Session, user: User, tag_id: uuid.UUID) -> None:
    tag = journal_repo.get_tag_by_id(db, tag_id)
    if not tag:
        raise HTTPException(status_code=status.HTTP_404_NOT_FOUND, detail="Tag not found.")
    if tag.user_id is None or tag.user_id != user.id:
        raise HTTPException(status_code=status.HTTP_403_FORBIDDEN, detail="You do not have permission to delete this tag.")
    journal_repo.delete_tag(db, user_id=user.id, tag_id=tag_id)
    db.commit()


def reorder_tags(db: Session, user: User, ids: list[uuid.UUID]) -> None:
    journal_repo.reorder_tags(db, user_id=user.id, ids=ids)
    db.commit()


def _normalize_color(color: Optional[str]) -> Optional[str]:
    if color is None:
        return None
    color = color.strip()
    if not color:
        return None
    if not color.startswith("#") or len(color) != 7:
        raise HTTPException(status_code=status.HTTP_400_BAD_REQUEST, detail="Color must be a valid 7-character hex code (e.g. #3b82f6).")
    return color


# ---------------------------------------------------------------------------
# Trade tags
# ---------------------------------------------------------------------------

def _validate_trade_ownership(db: Session, user_id: uuid.UUID, trade_id: uuid.UUID) -> Trade:
    trade = db.execute(select(Trade).where(Trade.id == trade_id)).scalar_one_or_none()
    if not trade:
        raise HTTPException(status_code=status.HTTP_404_NOT_FOUND, detail="Trade not found.")
    if trade.account.user_id != user_id:
        raise HTTPException(status_code=status.HTTP_403_FORBIDDEN, detail="You do not have permission to access this trade.")
    return trade


def get_trade_tags(db: Session, user: User, trade_id: uuid.UUID) -> list:
    _validate_trade_ownership(db, user_id=user.id, trade_id=trade_id)
    return journal_repo.get_trade_tags(db, trade_id=trade_id)


def update_trade_tags(db: Session, user: User, trade_id: uuid.UUID, tag_ids: list[uuid.UUID]) -> list:
    _validate_trade_ownership(db, user_id=user.id, trade_id=trade_id)
    for tag_id in tag_ids:
        tag = journal_repo.get_tag_by_id(db, tag_id)
        if not tag:
            raise HTTPException(status_code=status.HTTP_404_NOT_FOUND, detail=f"Tag {tag_id} not found.")
        if tag.user_id is not None and tag.user_id != user.id:
            raise HTTPException(status_code=status.HTTP_403_FORBIDDEN, detail=f"You do not have permission to use tag {tag.name}.")
    journal_repo.update_trade_tags(db, trade_id=trade_id, tag_ids=tag_ids)
    db.commit()
    return journal_repo.get_trade_tags(db, trade_id=trade_id)
