import json
from decimal import Decimal
from typing import Optional
from uuid import UUID

from fastapi import APIRouter, Depends, File, Form, HTTPException, Response, UploadFile, status
from sqlalchemy.orm import Session

from app.api.deps import get_current_user
from app.core.database import get_db
from app.models.stream import StreamPrivacy
from app.models.user import User
from app.schemas.stream import ForumToggleRequest, StreamResponse
from app.schemas.stream_member import ApproveRejectRequest, JoinRequestResponse, MemberListResponse, StreamMemberResponse
from app.services import stream_service

router = APIRouter(prefix="/streams", tags=["streams"])


@router.post(
    "",
    response_model=StreamResponse,
    status_code=status.HTTP_201_CREATED,
    summary="Create a new stream",
    description=(
        "Creates a stream owned by the authenticated user. "
        "Send as `multipart/form-data`. "
        "For paid streams, `price` is required and must be > 0. "
        "`require_join_approval` can only be `true` when `privacy` is `private`."
    ),
)
def create_stream(
    # ── required fields ──────────────────────────────────────────────────────
    name: str = Form(..., min_length=1, max_length=120),
    privacy: StreamPrivacy = Form(StreamPrivacy.public),
    # ── optional fields ───────────────────────────────────────────────────────
    description: Optional[str] = Form(None),
    forum_enabled: bool = Form(True),
    # Tags supplied as a JSON array string: '["forex", "signals"]'
    tags: Optional[str] = Form(
        None,
        description='JSON array of tag strings, e.g. ["forex","signals"]',
    ),
    price: Optional[Decimal] = Form(None),
    require_join_approval: bool = Form(False),
    # ── image uploads ────────────────────────────────────────────────────────
    avatar: Optional[UploadFile] = File(None, description="Stream display picture"),
    banner: Optional[UploadFile] = File(None, description="Stream banner image"),
    # ── dependencies ─────────────────────────────────────────────────────────
    current_user: User = Depends(get_current_user),
    db: Session = Depends(get_db),
) -> StreamResponse:
    # Parse tags from JSON string → list[str]
    parsed_tags: Optional[list[str]] = None
    if tags is not None:
        try:
            parsed_tags = json.loads(tags)
            if not isinstance(parsed_tags, list) or not all(
                isinstance(t, str) for t in parsed_tags
            ):
                raise ValueError
        except (ValueError, TypeError):
            from fastapi import HTTPException

            raise HTTPException(
                status_code=status.HTTP_422_UNPROCESSABLE_ENTITY,
                detail="'tags' must be a JSON array of strings, e.g. [\"forex\",\"signals\"].",
            )

    return stream_service.create_stream(
        db,
        current_user=current_user,
        name=name,
        description=description,
        privacy=privacy,
        forum_enabled=forum_enabled,
        tags=parsed_tags,
        price=price,
        require_join_approval=require_join_approval,
        avatar_file=avatar if avatar and avatar.filename else None,
        banner_file=banner if banner and banner.filename else None,
    )


@router.post(
    "/{stream_id}/follow",
    response_model=StreamMemberResponse,
    status_code=status.HTTP_201_CREATED,
    summary="Follow a stream",
    description=(
        "Follow a stream. Public and private (no-approval) streams become active "
        "immediately. Private streams with approval enabled create a pending request. "
        "Paid streams return 402 until payment is supported."
    ),
)
def follow_stream(
    stream_id: UUID,
    current_user: User = Depends(get_current_user),
    db: Session = Depends(get_db),
) -> StreamMemberResponse:
    return stream_service.follow_stream(db, stream_id=stream_id, current_user=current_user)


@router.delete(
    "/{stream_id}/follow",
    status_code=status.HTTP_204_NO_CONTENT,
    summary="Unfollow a stream",
)
def unfollow_stream(
    stream_id: UUID,
    current_user: User = Depends(get_current_user),
    db: Session = Depends(get_db),
) -> Response:
    stream_service.unfollow_stream(db, stream_id=stream_id, current_user=current_user)
    return Response(status_code=status.HTTP_204_NO_CONTENT)


@router.get(
    "/{stream_id}/join-requests",
    response_model=list[JoinRequestResponse],
    status_code=status.HTTP_200_OK,
    summary="List pending join requests (owner only)",
)
def list_join_requests(
    stream_id: UUID,
    current_user: User = Depends(get_current_user),
    db: Session = Depends(get_db),
) -> list[JoinRequestResponse]:
    return stream_service.list_join_requests(
        db, stream_id=stream_id, current_user=current_user
    )


@router.get(
    "/{stream_id}/members",
    response_model=list[MemberListResponse],
    status_code=status.HTTP_200_OK,
    summary="List active stream members",
    description=(
        "Returns `user_id`, `username`, `avatar_url` for all callers. "
        "Stream owners additionally receive `status` and `joined_at` per member. "
        "Public streams: any authenticated user can call this. "
        "Private/paid streams: owner or active members only."
    ),
)
def get_stream_members(
    stream_id: UUID,
    current_user: User = Depends(get_current_user),
    db: Session = Depends(get_db),
) -> list[MemberListResponse]:
    return stream_service.get_stream_members(
        db, stream_id=stream_id, current_user=current_user
    )


@router.patch(
    "/{stream_id}/join-requests/{user_id}",
    response_model=StreamMemberResponse | None,
    status_code=status.HTTP_200_OK,
    summary="Approve or reject a join request (owner only)",
    description=(
        "Pass `{\"action\": \"approve\"}` to approve or `{\"action\": \"reject\"}` to reject. "
        "Approved requests become active members. Rejected requests are removed. "
        "Returns the updated membership on approve, null on reject."
    ),
)
def handle_join_request(
    stream_id: UUID,
    user_id: UUID,
    payload: ApproveRejectRequest,
    current_user: User = Depends(get_current_user),
    db: Session = Depends(get_db),
) -> StreamMemberResponse | None:
    return stream_service.handle_join_request(
        db,
        stream_id=stream_id,
        requesting_user_id=user_id,
        payload=payload,
        current_user=current_user,
    )


@router.delete(
    "/{stream_id}/members/{user_id}",
    status_code=status.HTTP_204_NO_CONTENT,
    summary="Remove a member from a stream (owner only)",
    description=(
        "Removes an active or pending member from the stream. "
        "Pass `ban=true` to permanently ban the user — they will be blocked from rejoining. "
        "Only the stream owner can call this endpoint."
    ),
)
def remove_member(
    stream_id: UUID,
    user_id: UUID,
    ban: bool = False,
    current_user: User = Depends(get_current_user),
    db: Session = Depends(get_db),
) -> Response:
    stream_service.remove_member(
        db,
        stream_id=stream_id,
        target_user_id=user_id,
        ban=ban,
        current_user=current_user,
    )
    return Response(status_code=status.HTTP_204_NO_CONTENT)


@router.patch(
    "/{stream_id}/forum",
    response_model=StreamResponse,
    status_code=status.HTTP_200_OK,
    summary="Enable or disable the forum for a stream (owner only)",
    description=(
        "Sets `forum_enabled` on the stream. "
        "Pass `{\"forum_enabled\": false}` to disable or `true` to re-enable. "
        "Only the stream owner can call this endpoint."
    ),
)
def toggle_forum(
    stream_id: UUID,
    payload: ForumToggleRequest,
    current_user: User = Depends(get_current_user),
    db: Session = Depends(get_db),
) -> StreamResponse:
    return stream_service.toggle_forum(
        db,
        stream_id=stream_id,
        payload=payload,
        current_user=current_user,
    )
