import uuid
from typing import Optional

from fastapi import APIRouter, Depends, File, Form, HTTPException, Response, UploadFile, status
from sqlalchemy.orm import Session

from app.api.deps import get_current_user
from app.core.database import get_db
from app.models.post import PostType
from app.models.user import User
from app.schemas.post import PostCreate, PostListResponse, PostResponse, PostUpdate
from app.services import post_service

# ---------------------------------------------------------------------------
# Two router groups — mirrors how streams.py is structured
# ---------------------------------------------------------------------------

# Stream-scoped: /streams/{stream_id}/posts
stream_posts_router = APIRouter(prefix="/streams/{stream_id}/posts", tags=["posts"])

# Post-scoped: /posts
posts_router = APIRouter(prefix="/posts", tags=["posts"])


# ---------------------------------------------------------------------------
# Stream-scoped endpoints
# ---------------------------------------------------------------------------


@stream_posts_router.post(
    "",
    response_model=PostResponse,
    status_code=status.HTTP_201_CREATED,
    summary="Create a post in a stream",
    description=(
        "Only the stream owner can create top-level posts. "
        "Send as `multipart/form-data`. "
        "Either `content` or `media` (or both) must be provided. "
        "For signal posts, `trade_data` must be a JSON object string."
    ),
)
def create_post(
    stream_id: uuid.UUID,
    type: PostType = Form(...),
    content: Optional[str] = Form(None),
    trade_data: Optional[str] = Form(
        None,
        description="JSON object string, e.g. {\"symbol\":\"BTCUSDT\",\"direction\":\"long\"}",
    ),
    media: Optional[UploadFile] = File(None, description="Optional media attachment (image, video, or PDF)"),
    current_user: User = Depends(get_current_user),
    db: Session = Depends(get_db),
) -> PostResponse:
    data = PostCreate.from_form(type=type, content=content, trade_data_raw=trade_data)
    return post_service.create_post(
        db,
        stream_id=stream_id,
        current_user=current_user,
        data=data,
        media_file=media if media and media.filename else None,
    )


@stream_posts_router.get(
    "",
    response_model=PostListResponse,
    summary="List posts in a stream",
    description=(
        "Returns top-level posts newest-first. "
        "Pass `cursor` (the `next_cursor` from the previous response) to page forward. "
        "Public streams: accessible to any authenticated user. "
        "Private/paid streams: active members and the owner only."
    ),
)
def list_stream_posts(
    stream_id: uuid.UUID,
    cursor: Optional[uuid.UUID] = None,
    limit: int = 20,
    current_user: User = Depends(get_current_user),
    db: Session = Depends(get_db),
) -> PostListResponse:
    if limit < 1 or limit > 100:
        raise HTTPException(
            status_code=status.HTTP_422_UNPROCESSABLE_ENTITY,
            detail="'limit' must be between 1 and 100.",
        )
    return post_service.list_stream_posts(
        db,
        stream_id=stream_id,
        current_user=current_user,
        cursor_post_id=cursor,
        limit=limit,
    )


# ---------------------------------------------------------------------------
# Post-scoped endpoints
# ---------------------------------------------------------------------------


@posts_router.get(
    "/{post_id}",
    response_model=PostResponse,
    summary="Get a single post",
)
def get_post(
    post_id: uuid.UUID,
    current_user: User = Depends(get_current_user),
    db: Session = Depends(get_db),
) -> PostResponse:
    return post_service.get_post(db, post_id, current_user=current_user)


@posts_router.post(
    "/{post_id}/replies",
    response_model=PostResponse,
    status_code=status.HTTP_201_CREATED,
    summary="Reply to a post",
    description=(
        "Any authenticated user may reply to posts in public streams. "
        "For private/paid streams, only active members and the owner may reply. "
        "Replies-to-replies are not supported."
    ),
)
def create_reply(
    post_id: uuid.UUID,
    type: PostType = Form(...),
    content: Optional[str] = Form(None),
    trade_data: Optional[str] = Form(None),
    media: Optional[UploadFile] = File(None, description="Optional media attachment"),
    current_user: User = Depends(get_current_user),
    db: Session = Depends(get_db),
) -> PostResponse:
    data = PostCreate.from_form(type=type, content=content, trade_data_raw=trade_data)
    return post_service.create_reply(
        db,
        parent_post_id=post_id,
        current_user=current_user,
        data=data,
        media_file=media if media and media.filename else None,
    )


@posts_router.get(
    "/{post_id}/replies",
    response_model=PostListResponse,
    summary="List replies to a post",
    description="Returns replies oldest-first (chronological). Cursor-paginated.",
)
def list_replies(
    post_id: uuid.UUID,
    cursor: Optional[uuid.UUID] = None,
    limit: int = 20,
    current_user: User = Depends(get_current_user),
    db: Session = Depends(get_db),
) -> PostListResponse:
    if limit < 1 or limit > 100:
        raise HTTPException(
            status_code=status.HTTP_422_UNPROCESSABLE_ENTITY,
            detail="'limit' must be between 1 and 100.",
        )
    return post_service.list_replies(
        db,
        post_id=post_id,
        current_user=current_user,
        cursor_post_id=cursor,
        limit=limit,
    )


@posts_router.patch(
    "/{post_id}",
    response_model=PostResponse,
    summary="Edit a post",
    description="Only the post author can edit. Only `content` is editable.",
)
def update_post(
    post_id: uuid.UUID,
    data: PostUpdate,
    current_user: User = Depends(get_current_user),
    db: Session = Depends(get_db),
) -> PostResponse:
    return post_service.update_post(db, post_id=post_id, current_user=current_user, data=data)


@posts_router.delete(
    "/{post_id}",
    status_code=status.HTTP_204_NO_CONTENT,
    summary="Delete a post",
    description="The post author or stream owner may soft-delete a post.",
)
def delete_post(
    post_id: uuid.UUID,
    current_user: User = Depends(get_current_user),
    db: Session = Depends(get_db),
) -> Response:
    post_service.delete_post(db, post_id=post_id, current_user=current_user)
    return Response(status_code=status.HTTP_204_NO_CONTENT)


@posts_router.put(
    "/{post_id}/upvote",
    response_model=PostResponse,
    summary="Upvote a post",
    description="Idempotent — calling this multiple times has no additional effect.",
)
def upvote_post(
    post_id: uuid.UUID,
    current_user: User = Depends(get_current_user),
    db: Session = Depends(get_db),
) -> PostResponse:
    return post_service.upvote_post(db, post_id=post_id, current_user=current_user)


@posts_router.delete(
    "/{post_id}/upvote",
    response_model=PostResponse,
    summary="Remove upvote from a post",
    description="Idempotent — safe to call even if not currently upvoted.",
)
def unupvote_post(
    post_id: uuid.UUID,
    current_user: User = Depends(get_current_user),
    db: Session = Depends(get_db),
) -> PostResponse:
    return post_service.unupvote_post(db, post_id=post_id, current_user=current_user)
