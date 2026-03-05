from decimal import Decimal
from typing import Optional
from uuid import UUID

from fastapi import HTTPException, UploadFile, status
from sqlalchemy.orm import Session

from app.core.config import settings
from app.models.stream import StreamPrivacy
from app.models.stream_member import MemberStatus
from app.models.user import User
from app.repositories import stream_member_repo, stream_repo
from app.schemas.stream import StreamResponse
from app.schemas.stream_member import ApproveRejectRequest, MemberListResponse, StreamMemberResponse
from app.utils.storage import upload_image


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
    avatar_file: Optional[UploadFile],
    banner_file: Optional[UploadFile],
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

    # ── upload images if provided ────────────────────────────────────────────
    owner_prefix = str(current_user.id)

    avatar_url: Optional[str] = None
    if avatar_file is not None:
        avatar_url = upload_image(
            avatar_file,
            bucket=settings.SUPABASE_STREAM_AVATARS_BUCKET,
            prefix=owner_prefix,
        )

    banner_url: Optional[str] = None
    if banner_file is not None:
        banner_url = upload_image(
            banner_file,
            bucket=settings.SUPABASE_STREAM_BANNERS_BUCKET,
            prefix=owner_prefix,
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

    existing = stream_member_repo.get(db, user_id=current_user.id, stream_id=stream_id)
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

    member = stream_member_repo.create(
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

    member = stream_member_repo.get(db, user_id=current_user.id, stream_id=stream_id)
    if not member:
        raise HTTPException(
            status_code=status.HTTP_404_NOT_FOUND,
            detail="You are not following this stream.",
        )

    stream_member_repo.delete(db, member)
    db.commit()


def list_join_requests(
    db: Session, *, stream_id: UUID, current_user: User
) -> list[StreamMemberResponse]:
    stream = stream_repo.get_by_id(db, stream_id)
    if not stream:
        raise HTTPException(status_code=status.HTTP_404_NOT_FOUND, detail="Stream not found.")

    if stream.owner_id != current_user.id:
        raise HTTPException(
            status_code=status.HTTP_403_FORBIDDEN,
            detail="Only the stream owner can view join requests.",
        )

    pending = stream_member_repo.list_pending(db, stream_id=stream_id)
    return [StreamMemberResponse.model_validate(m) for m in pending]


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

    member = stream_member_repo.get(
        db, user_id=requesting_user_id, stream_id=stream_id
    )
    if not member or member.status != MemberStatus.pending:
        raise HTTPException(
            status_code=status.HTTP_404_NOT_FOUND,
            detail="No pending join request found for this user.",
        )

    if payload.action == "approve":
        updated = stream_member_repo.update_status(db, member, MemberStatus.active)
        db.commit()
        db.refresh(updated)
        return StreamMemberResponse.model_validate(updated)
    else:  # reject
        stream_member_repo.delete(db, member)
        db.commit()
        return None


def get_stream_members(
    db: Session,
    *,
    stream_id: UUID,
    current_user: User,
) -> list[MemberListResponse]:
    stream = stream_repo.get_by_id(db, stream_id)
    if not stream:
        raise HTTPException(status_code=status.HTTP_404_NOT_FOUND, detail="Stream not found.")

    is_owner = stream.owner_id == current_user.id

    # Private and paid streams: only the owner or active members may list members
    if stream.privacy != StreamPrivacy.public and not is_owner:
        membership = stream_member_repo.get(
            db, user_id=current_user.id, stream_id=stream_id
        )
        if not membership or membership.status != MemberStatus.active:
            raise HTTPException(
                status_code=status.HTTP_403_FORBIDDEN,
                detail="You must be an active member of this stream to view its members.",
            )

    members = stream_member_repo.list_active(db, stream_id=stream_id)
    return [
        MemberListResponse(
            user_id=m.user_id,
            username=m.user.username,
            avatar_url=m.user.avatar_url,
            status=m.status if is_owner else None,
            joined_at=m.joined_at if is_owner else None,
        )
        for m in members
    ]
