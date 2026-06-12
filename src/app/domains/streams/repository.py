import uuid
from datetime import datetime
from dataclasses import dataclass
from decimal import Decimal
from typing import Optional
from uuid import UUID

from sqlalchemy import func, select
from sqlalchemy.orm import Session, joinedload

from app.domains.streams.models import MemberStatus, Stream, StreamMember, StreamPrivacy
from app.domains.users.models import User


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
    stmt = select(Stream).where(Stream.id == stream_id, Stream.is_deleted.is_(False))
    return db.execute(stmt).scalar_one_or_none()


def get_by_id_with_owner(db: Session, stream_id: UUID) -> Optional[Stream]:
    stmt = (
        select(Stream)
        .options(joinedload(Stream.owner))
        .where(Stream.id == stream_id, Stream.is_deleted.is_(False))
    )
    return db.execute(stmt).unique().scalar_one_or_none()


def get_default_by_owner(db: Session, owner_id: UUID) -> Optional[Stream]:
    stmt = select(Stream).where(
        Stream.owner_id == owner_id,
        Stream.is_default.is_(True),
        Stream.is_deleted.is_(False),
    )
    return db.execute(stmt).scalar_one_or_none()


def get_all_by_owner(db: Session, owner_id: UUID) -> list[Stream]:
    stmt = (
        select(Stream)
        .where(Stream.owner_id == owner_id, Stream.is_deleted.is_(False))
        .order_by(Stream.created_at.asc())
    )
    return list(db.execute(stmt).scalars())


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
        select(func.count(StreamMember.user_id))
        .where(
            StreamMember.stream_id == Stream.id,
            StreamMember.status == MemberStatus.active,
        )
        .correlate(Stream)
        .scalar_subquery()
    )

    is_following_subq = (
        select(func.count(StreamMember.user_id))
        .where(
            StreamMember.stream_id == Stream.id,
            StreamMember.user_id == current_user_id,
            StreamMember.status.in_([MemberStatus.active, MemberStatus.pending]),
        )
        .correlate(Stream)
        .scalar_subquery()
    )

    membership_status_subq = (
        select(StreamMember.status)
        .where(
            StreamMember.stream_id == Stream.id,
            StreamMember.user_id == current_user_id,
        )
        .correlate(Stream)
        .scalar_subquery()
    )

    stmt = (
        select(
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
            membership_status_subq.label("membership_status"),
        )
        .join(User, User.id == Stream.owner_id)
        .where(
            Stream.owner_id != exclude_owner_id,
            Stream.is_deleted.is_(False),
        )
        .order_by(Stream.created_at.desc())
        .offset(skip)
        .limit(limit)
    )
    rows = db.execute(stmt).all()

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


# ── StreamMember functions ────────────────────────────────────────────────────

def get_member(db: Session, *, user_id: UUID, stream_id: UUID) -> Optional[StreamMember]:
    stmt = select(StreamMember).where(
        StreamMember.user_id == user_id, StreamMember.stream_id == stream_id
    )
    return db.execute(stmt).scalar_one_or_none()


def create_member(
    db: Session,
    *,
    user_id: UUID,
    stream_id: UUID,
    status: MemberStatus,
) -> StreamMember:
    member = StreamMember(user_id=user_id, stream_id=stream_id, status=status)
    db.add(member)
    db.flush()
    return member


def update_member_status(
    db: Session, member: StreamMember, status: MemberStatus
) -> StreamMember:
    member.status = status
    db.flush()
    return member


def delete_member(db: Session, member: StreamMember) -> None:
    db.delete(member)
    db.flush()


def list_pending_members(db: Session, *, stream_id: UUID) -> list[StreamMember]:
    stmt = select(StreamMember).where(
        StreamMember.stream_id == stream_id,
        StreamMember.status == MemberStatus.pending,
    )
    return list(db.execute(stmt).scalars())


def list_active_members(db: Session, *, stream_id: UUID) -> list[StreamMember]:
    stmt = (
        select(StreamMember)
        .options(joinedload(StreamMember.user))
        .where(
            StreamMember.stream_id == stream_id,
            StreamMember.status == MemberStatus.active,
        )
    )
    return list(db.execute(stmt).unique().scalars())


def list_active_members_page(
    db: Session,
    *,
    stream_id: UUID,
    limit: int,
    cursor_user_id: UUID | None,
) -> list[StreamMember]:
    """Cursor-based page of active members, ordered by (joined_at ASC, user_id ASC)."""
    stmt = (
        select(StreamMember)
        .options(joinedload(StreamMember.user))
        .where(
            StreamMember.stream_id == stream_id,
            StreamMember.status == MemberStatus.active,
        )
        .order_by(StreamMember.joined_at.asc(), StreamMember.user_id.asc())
        .limit(limit)
    )
    if cursor_user_id is not None:
        cursor_stmt = select(StreamMember.joined_at, StreamMember.user_id).where(
            StreamMember.stream_id == stream_id,
            StreamMember.user_id == cursor_user_id,
        )
        row = db.execute(cursor_stmt).one_or_none()
        if row is not None:
            cursor_joined_at, cursor_uid = row
            stmt = stmt.where(
                (StreamMember.joined_at > cursor_joined_at)
                | (
                    (StreamMember.joined_at == cursor_joined_at)
                    & (StreamMember.user_id > cursor_uid)
                )
            )
    return list(db.execute(stmt).unique().scalars())


def list_pending_members_page(
    db: Session,
    *,
    stream_id: UUID,
    limit: int,
    cursor_user_id: UUID | None,
) -> list[StreamMember]:
    """Cursor-based page of pending join requests, ordered by (joined_at ASC, user_id ASC)."""
    stmt = (
        select(StreamMember)
        .where(
            StreamMember.stream_id == stream_id,
            StreamMember.status == MemberStatus.pending,
        )
        .order_by(StreamMember.joined_at.asc(), StreamMember.user_id.asc())
        .limit(limit)
    )
    if cursor_user_id is not None:
        cursor_stmt = select(StreamMember.joined_at, StreamMember.user_id).where(
            StreamMember.stream_id == stream_id,
            StreamMember.user_id == cursor_user_id,
        )
        row = db.execute(cursor_stmt).one_or_none()
        if row is not None:
            cursor_joined_at, cursor_uid = row
            stmt = stmt.where(
                (StreamMember.joined_at > cursor_joined_at)
                | (
                    (StreamMember.joined_at == cursor_joined_at)
                    & (StreamMember.user_id > cursor_uid)
                )
            )
    return list(db.execute(stmt).scalars())


def count_active_members(db: Session, *, stream_id: UUID) -> int:
    stmt = select(func.count(StreamMember.user_id)).where(
        StreamMember.stream_id == stream_id,
        StreamMember.status == MemberStatus.active,
    )
    return db.execute(stmt).scalar_one()


def ban_member(db: Session, *, user_id: UUID, stream_id: UUID) -> StreamMember:
    """Set a user's membership to banned, creating the row if it doesn't exist."""
    member = get_member(db, user_id=user_id, stream_id=stream_id)
    if member:
        member.status = MemberStatus.banned
        db.flush()
        return member
    member = StreamMember(user_id=user_id, stream_id=stream_id, status=MemberStatus.banned)
    db.add(member)
    db.flush()
    return member
