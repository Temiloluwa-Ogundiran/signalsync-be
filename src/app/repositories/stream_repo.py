import uuid
from datetime import datetime
from dataclasses import dataclass
from decimal import Decimal
from typing import Optional
from uuid import UUID

from sqlalchemy import func
from sqlalchemy.orm import Session, joinedload

from app.models.stream import Stream, StreamPrivacy
from app.models.stream_member import MemberStatus, StreamMember
from app.models.user import User


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
    is_default: bool = False,
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
        is_default=is_default,
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


def get_by_id_with_owner(db: Session, stream_id: UUID) -> Optional[Stream]:
    return (
        db.query(Stream)
        .options(joinedload(Stream.owner))
        .filter(Stream.id == stream_id, Stream.is_deleted == False)  # noqa: E712
        .first()
    )


def get_default_by_owner(db: Session, owner_id: UUID) -> Optional[Stream]:
    return (
        db.query(Stream)
        .filter(
            Stream.owner_id == owner_id,
            Stream.is_default == True,  # noqa: E712
            Stream.is_deleted == False,  # noqa: E712
        )
        .first()
    )


def get_all_by_owner(db: Session, owner_id: UUID) -> list[Stream]:
    return (
        db.query(Stream)
        .filter(Stream.owner_id == owner_id, Stream.is_deleted == False)  # noqa: E712
        .order_by(Stream.created_at.asc())
        .all()
    )


def set_forum_enabled(db: Session, stream: Stream, enabled: bool) -> Stream:
    stream.forum_enabled = enabled
    db.flush()
    return stream


@dataclass(frozen=True)
class DiscoverRow:
    """Lightweight container for a discover-list result row."""

    id: uuid.UUID
    name: str
    description: Optional[str]
    privacy: StreamPrivacy
    tags: Optional[list[str]]
    avatar_url: Optional[str]
    banner_url: Optional[str]
    owner_display_name: Optional[str]
    follower_count: int
    created_at: datetime
    is_following: bool
    membership_status: Optional[MemberStatus]


def list_discover(
    db: Session,
    *,
    exclude_owner_id: UUID,
    current_user_id: UUID,
    skip: int = 0,
    limit: int = 20,
) -> list[DiscoverRow]:
    """Return all non-deleted streams not owned by exclude_owner_id."""
    follower_subq = (
        db.query(func.count(StreamMember.user_id))
        .filter(
            StreamMember.stream_id == Stream.id,
            StreamMember.status == MemberStatus.active,
        )
        .correlate(Stream)
        .scalar_subquery()
    )

    is_following_subq = (
        db.query(func.count(StreamMember.user_id))
        .filter(
            StreamMember.stream_id == Stream.id,
            StreamMember.user_id == current_user_id,
            StreamMember.status.in_([MemberStatus.active, MemberStatus.pending]),
        )
        .correlate(Stream)
        .scalar_subquery()
    )

    rows = (
        db.query(
            Stream.id,
            Stream.name,
            Stream.description,
            Stream.privacy,
            Stream.tags,
            Stream.avatar_url,
            Stream.banner_url,
            Stream.created_at,
            User.display_name.label("owner_display_name"),
            follower_subq.label("follower_count"),
            is_following_subq.label("is_following"),
            (
                db.query(StreamMember.status)
                .filter(
                    StreamMember.stream_id == Stream.id,
                    StreamMember.user_id == current_user_id,
                )
                .correlate(Stream)
                .scalar_subquery()
            ).label("membership_status"),
        )
        .join(User, User.id == Stream.owner_id)
        .filter(
            Stream.owner_id != exclude_owner_id,
            Stream.is_deleted == False,  # noqa: E712
        )
        .order_by(Stream.created_at.desc())
        .offset(skip)
        .limit(limit)
        .all()
    )

    return [
        DiscoverRow(
            id=r.id,
            name=r.name,
            description=r.description,
            privacy=r.privacy,
            tags=r.tags,
            avatar_url=r.avatar_url,
            banner_url=r.banner_url,
            created_at=r.created_at,
            owner_display_name=r.owner_display_name,
            follower_count=r.follower_count,
            is_following=bool(r.is_following),
            membership_status=r.membership_status,
        )
        for r in rows
    ]
