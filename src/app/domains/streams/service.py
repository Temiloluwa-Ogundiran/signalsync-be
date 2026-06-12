from decimal import Decimal
from typing import Optional
from uuid import UUID

from fastapi import HTTPException, status
from sqlalchemy.orm import Session

from app.domains.streams.models import MemberStatus, StreamPrivacy
from app.domains.users.models import User
from app.domains.streams import repository as stream_repo
from app.domains.streams.repository import DiscoverRow
from app.domains.streams.schemas import (
    ApproveRejectRequest,
    ForumToggleRequest,
    MemberListResponse,
    PaginatedJoinRequestResponse,
    PaginatedMemberListResponse,
    StreamDiscoverResponse,
    StreamDetailResponse,
    StreamMemberResponse,
    StreamResponse,
)


def create_stream(
    db: Session,
    *,
    current_user: User,
    name: str,
    description: Optional[str],
    privacy: StreamPrivacy,
    forum_enabled: bool,
    tags: Optional[list[str]],
    price: Optional[Decimal],
    require_join_approval: bool,
    avatar_url: Optional[str] = None,
    banner_url: Optional[str] = None,
) -> StreamResponse:
    # ── business rule: paid streams must have a positive price ───────────────
    if privacy == StreamPrivacy.paid:
        if price is None or price <= 0:
            raise HTTPException(
                status_code=status.HTTP_422_UNPROCESSABLE_ENTITY,
                detail="A positive price is required for paid streams.",
            )

    # ── business rule: join approval only makes sense on private streams ─────
    if require_join_approval and privacy != StreamPrivacy.private:
        raise HTTPException(
            status_code=status.HTTP_422_UNPROCESSABLE_ENTITY,
            detail="'require_join_approval' can only be enabled for private streams.",
        )

    # ── persist ──────────────────────────────────────────────────────────────
    stream = stream_repo.create(
        db,
        owner_id=current_user.id,
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
    db.commit()
    db.refresh(stream)

    return StreamResponse.model_validate(stream)


def get_stream_detail(
    db: Session,
    *,
    stream_id: UUID,
    current_user: User,
) -> StreamDetailResponse:
    stream = stream_repo.get_by_id_with_owner(db, stream_id)
    if not stream:
        raise HTTPException(status_code=status.HTTP_404_NOT_FOUND, detail="Stream not found.")

    is_owner = stream.owner_id == current_user.id

    if stream.privacy != StreamPrivacy.public and not is_owner:
        membership = stream_repo.get_member(
            db, user_id=current_user.id, stream_id=stream_id
        )
        if not membership or membership.status != MemberStatus.active:
            raise HTTPException(
                status_code=status.HTTP_403_FORBIDDEN,
                detail="You must be an active member to view this stream.",
            )

    follower_count = stream_repo.count_active_members(db, stream_id=stream_id)
    member = stream_repo.get_member(db, user_id=current_user.id, stream_id=stream_id)
    is_following = bool(member and member.status in [MemberStatus.active, MemberStatus.pending])

    from typing import cast
    tags = cast(Optional[list[str]], stream.tags) if isinstance(stream.tags, list) else None

    return StreamDetailResponse(
        id=stream.id,
        owner_id=stream.owner_id,
        name=stream.name,
        description=stream.description,
        privacy=stream.privacy,
        forum_enabled=stream.forum_enabled,
        tags=tags,
        price=stream.price,
        avatar_url=stream.avatar_url,
        banner_url=stream.banner_url,
        require_join_approval=stream.require_join_approval,
        is_default=stream.is_default,
        created_at=stream.created_at,
        owner_display_name=stream.owner.display_name if stream.owner else None,
        follower_count=follower_count,
        is_following=is_following,
    )


def follow_stream(
    db: Session, *, stream_id: UUID, current_user: User
) -> StreamMemberResponse:
    stream = stream_repo.get_by_id(db, stream_id)
    if not stream:
        raise HTTPException(status_code=status.HTTP_404_NOT_FOUND, detail="Stream not found.")

    if stream.owner_id == current_user.id:
        raise HTTPException(
            status_code=status.HTTP_400_BAD_REQUEST,
            detail="You cannot follow your own stream.",
        )

    if stream.privacy == StreamPrivacy.paid:
        raise HTTPException(
            status_code=status.HTTP_402_PAYMENT_REQUIRED,
            detail="This is a paid stream. Payment support is coming soon.",
        )

    existing = stream_repo.get_member(db, user_id=current_user.id, stream_id=stream_id)
    if existing and existing.status == MemberStatus.banned:
        raise HTTPException(
            status_code=status.HTTP_403_FORBIDDEN,
            detail="You are banned from this stream.",
        )
    if existing and existing.status == MemberStatus.active:
        raise HTTPException(
            status_code=status.HTTP_409_CONFLICT,
            detail="You are already following this stream.",
        )
    if existing and existing.status == MemberStatus.pending:
        raise HTTPException(
            status_code=status.HTTP_409_CONFLICT,
            detail="Your join request is already pending approval.",
        )

    needs_approval = (
        stream.privacy == StreamPrivacy.private and stream.require_join_approval
    )
    member_status = MemberStatus.pending if needs_approval else MemberStatus.active

    member = stream_repo.create_member(
        db,
        user_id=current_user.id,
        stream_id=stream_id,
        status=member_status,
    )
    db.commit()
    db.refresh(member)

    return StreamMemberResponse.model_validate(member)


def unfollow_stream(db: Session, *, stream_id: UUID, current_user: User) -> None:
    stream = stream_repo.get_by_id(db, stream_id)
    if not stream:
        raise HTTPException(status_code=status.HTTP_404_NOT_FOUND, detail="Stream not found.")

    member = stream_repo.get_member(db, user_id=current_user.id, stream_id=stream_id)
    if not member:
        raise HTTPException(
            status_code=status.HTTP_404_NOT_FOUND,
            detail="You are not following this stream.",
        )

    stream_repo.delete_member(db, member)
    db.commit()


def list_join_requests(
    db: Session,
    *,
    stream_id: UUID,
    current_user: User,
    limit: int = 50,
    cursor_user_id: UUID | None = None,
) -> PaginatedJoinRequestResponse:
    stream = stream_repo.get_by_id(db, stream_id)
    if not stream:
        raise HTTPException(status_code=status.HTTP_404_NOT_FOUND, detail="Stream not found.")

    if stream.owner_id != current_user.id:
        raise HTTPException(
            status_code=status.HTTP_403_FORBIDDEN,
            detail="Only the stream owner can view join requests.",
        )

    # Fetch one extra to determine whether a next page exists
    rows = stream_repo.list_pending_members_page(
        db, stream_id=stream_id, limit=limit + 1, cursor_user_id=cursor_user_id
    )
    has_more = len(rows) > limit
    page = rows[:limit]
    next_cursor = page[-1].user_id if has_more else None
    return PaginatedJoinRequestResponse(
        items=[StreamMemberResponse.model_validate(m) for m in page],
        next_cursor=next_cursor,
    )


def handle_join_request(
    db: Session,
    *,
    stream_id: UUID,
    requesting_user_id: UUID,
    payload: ApproveRejectRequest,
    current_user: User,
) -> StreamMemberResponse | None:
    stream = stream_repo.get_by_id(db, stream_id)
    if not stream:
        raise HTTPException(status_code=status.HTTP_404_NOT_FOUND, detail="Stream not found.")

    if stream.owner_id != current_user.id:
        raise HTTPException(
            status_code=status.HTTP_403_FORBIDDEN,
            detail="Only the stream owner can approve or reject join requests.",
        )

    member = stream_repo.get_member(
        db, user_id=requesting_user_id, stream_id=stream_id
    )
    if not member or member.status != MemberStatus.pending:
        raise HTTPException(
            status_code=status.HTTP_404_NOT_FOUND,
            detail="No pending join request found for this user.",
        )

    if payload.action == "approve":
        updated = stream_repo.update_member_status(db, member, MemberStatus.active)
        db.commit()
        db.refresh(updated)
        return StreamMemberResponse.model_validate(updated)
    else:  # reject
        stream_repo.delete_member(db, member)
        db.commit()
        return None


def get_stream_members(
    db: Session,
    *,
    stream_id: UUID,
    current_user: User,
    limit: int = 50,
    cursor_user_id: UUID | None = None,
) -> PaginatedMemberListResponse:
    stream = stream_repo.get_by_id(db, stream_id)
    if not stream:
        raise HTTPException(status_code=status.HTTP_404_NOT_FOUND, detail="Stream not found.")

    is_owner = stream.owner_id == current_user.id

    # Private and paid streams: only the owner or active members may list members
    if stream.privacy != StreamPrivacy.public and not is_owner:
        membership = stream_repo.get_member(
            db, user_id=current_user.id, stream_id=stream_id
        )
        if not membership or membership.status != MemberStatus.active:
            raise HTTPException(
                status_code=status.HTTP_403_FORBIDDEN,
                detail="You must be an active member of this stream to view its members.",
            )

    rows = stream_repo.list_active_members_page(
        db, stream_id=stream_id, limit=limit + 1, cursor_user_id=cursor_user_id
    )
    has_more = len(rows) > limit
    page = rows[:limit]
    next_cursor = page[-1].user_id if has_more else None
    return PaginatedMemberListResponse(
        items=[
            MemberListResponse(
                user_id=m.user_id,
                username=m.user.username,
                avatar_url=m.user.avatar_url,
                status=m.status if is_owner else None,
                joined_at=m.joined_at if is_owner else None,
            )
            for m in page
        ],
        next_cursor=next_cursor,
    )


def remove_member(
    db: Session,
    *,
    stream_id: UUID,
    target_user_id: UUID,
    ban: bool,
    current_user: User,
) -> None:
    stream = stream_repo.get_by_id(db, stream_id)
    if not stream:
        raise HTTPException(status_code=status.HTTP_404_NOT_FOUND, detail="Stream not found.")

    if stream.owner_id != current_user.id:
        raise HTTPException(
            status_code=status.HTTP_403_FORBIDDEN,
            detail="Only the stream owner can remove members.",
        )

    if target_user_id == current_user.id:
        raise HTTPException(
            status_code=status.HTTP_400_BAD_REQUEST,
            detail="The stream owner cannot remove themselves.",
        )

    member = stream_repo.get_member(db, user_id=target_user_id, stream_id=stream_id)
    if not member or member.status == MemberStatus.banned:
        raise HTTPException(
            status_code=status.HTTP_404_NOT_FOUND,
            detail="User is not a member of this stream.",
        )

    if ban:
        stream_repo.ban_member(db, user_id=target_user_id, stream_id=stream_id)
    else:
        stream_repo.delete_member(db, member)

    db.commit()


def toggle_forum(
    db: Session,
    *,
    stream_id: UUID,
    payload: ForumToggleRequest,
    current_user: User,
) -> StreamResponse:
    stream = stream_repo.get_by_id(db, stream_id)
    if not stream:
        raise HTTPException(status_code=status.HTTP_404_NOT_FOUND, detail="Stream not found.")

    if stream.owner_id != current_user.id:
        raise HTTPException(
            status_code=status.HTTP_403_FORBIDDEN,
            detail="Only the stream owner can change the forum setting.",
        )

    stream_repo.set_forum_enabled(db, stream, payload.forum_enabled)
    db.commit()
    db.refresh(stream)

    return StreamResponse.model_validate(stream)


def get_my_streams(db: Session, *, current_user: User) -> list[StreamResponse]:
    streams = stream_repo.get_all_by_owner(db, current_user.id)
    return [StreamResponse.model_validate(s) for s in streams]


def discover_streams(
    db: Session,
    *,
    current_user: User,
    skip: int = 0,
    limit: int = 20,
) -> list[StreamDiscoverResponse]:
    rows: list[DiscoverRow] = stream_repo.list_discover(
        db,
        exclude_owner_id=current_user.id,
        current_user_id=current_user.id,
        skip=skip,
        limit=limit,
    )
    return [
        StreamDiscoverResponse(
            id=r.id,
            name=r.name,
            description=r.description,
            privacy=r.privacy,
            tags=r.tags,
            avatar_url=r.avatar_url,
            banner_url=r.banner_url,
            owner_display_name=r.owner_display_name,
            follower_count=r.follower_count,
            is_following=r.is_following,
            membership_status=r.membership_status,
            created_at=r.created_at,
        )
        for r in rows
    ]
