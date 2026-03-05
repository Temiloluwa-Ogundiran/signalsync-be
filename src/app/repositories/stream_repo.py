import uuid
from decimal import Decimal
from typing import Optional
from uuid import UUID

from sqlalchemy.orm import Session

from app.models.stream import Stream, StreamPrivacy


def create(
    db: Session,
    *,
    owner_id: UUID,
    name: str,
    description: Optional[str],
    privacy: StreamPrivacy,
    forum_enabled: bool,
    tags: Optional[list[str]],
    price: Optional[Decimal],
    avatar_url: Optional[str],
    banner_url: Optional[str],
    require_join_approval: bool,
) -> Stream:
    stream = Stream(
        owner_id=owner_id,
        name=name,
        description=description,
        privacy=privacy,
        forum_enabled=forum_enabled,
        tags=tags,
        price=price,
        avatar_url=avatar_url,
        banner_url=banner_url,
        require_join_approval=require_join_approval,
    )
    db.add(stream)
    db.flush()
    return stream


def get_by_id(db: Session, stream_id: UUID) -> Optional[Stream]:
    return (
        db.query(Stream)
        .filter(Stream.id == stream_id, Stream.is_deleted == False)  # noqa: E712
        .first()
    )
