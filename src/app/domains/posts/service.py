import uuid
from typing import Optional

from fastapi import HTTPException, UploadFile, status
from sqlalchemy.orm import Session

from app.core.config import settings
from app.domains.posts.models import Post, PostMediaType
from app.domains.streams.models import MemberStatus, StreamPrivacy
from app.domains.users.models import User
from app.domains.posts import repository as post_repo
from app.domains.streams import repository as stream_repo
from app.domains.posts.schemas import (
    AuthorInfo,
    PostCreate,
    PostListResponse,
    PostMediaResponse,
    PostResponse,
    PostUpdate,
)
from app.shared.utils.storage import generate_signed_url, upload_media


# ---------------------------------------------------------------------------
# Internal helpers
# ---------------------------------------------------------------------------


def _build_response(
    db: Session,
    post: Post,
    *,
    current_user: User,
    parent_is_deleted: Optional[bool] = None,
) -> PostResponse:
    """Construct a PostResponse from a Post ORM object (author must be loaded)."""
    reply_count = post_repo.count_replies(db, post.id)
    upvote_count = post_repo.count_upvotes(db, post.id)
    has_upvoted = post_repo.get_upvote(db, user_id=current_user.id, post_id=post.id) is not None

    if post.parent_post_id is None:
        is_parent_deleted = False
    elif parent_is_deleted is not None:
        is_parent_deleted = parent_is_deleted
    else:
        parent = post_repo.get_by_id(db, post.parent_post_id)
        is_parent_deleted = parent is None or parent.is_deleted

    media_response: Optional[PostMediaResponse] = None
    if post.media is not None:
        signed_url, url_expires_at = generate_signed_url(
            bucket=settings.SUPABASE_POST_MEDIA_BUCKET,
            storage_path=post.media.storage_path,
            expires_in=settings.MEDIA_SIGNED_URL_TTL_SECONDS,
        )
        media_response = PostMediaResponse(
            id=post.media.id,
            url=signed_url,
            url_expires_at=url_expires_at,
            media_type=post.media.media_type,
            mime_type=post.media.mime_type,
            original_filename=post.media.original_filename,
        )

    return PostResponse(
        id=post.id,
        stream_id=post.stream_id,
        author_id=post.author_id,
        type=post.type,
        content=post.content,
        trade_data=post.trade_data,
        parent_post_id=post.parent_post_id,
        is_deleted=post.is_deleted,
        is_parent_deleted=is_parent_deleted,
        created_at=post.created_at,
        updated_at=post.updated_at,
        author=AuthorInfo.model_validate(post.author),
        media=media_response,
        reply_count=reply_count,
        upvote_count=upvote_count,
        has_upvoted=has_upvoted,
    )


def _check_stream_view_access(
    db: Session,
    stream_id: uuid.UUID,
    current_user: User,
) -> None:
    stream = stream_repo.get_by_id(db, stream_id)
    if stream is None or stream.is_deleted:
        raise HTTPException(status_code=status.HTTP_404_NOT_FOUND, detail="Stream not found.")

    if stream.privacy == StreamPrivacy.public:
        return

    if stream.owner_id == current_user.id:
        return

    member = stream_repo.get_member(db, user_id=current_user.id, stream_id=stream_id)
    if member is None or member.status != MemberStatus.active:
        raise HTTPException(
            status_code=status.HTTP_403_FORBIDDEN,
            detail="You must be an active member of this stream to interact with posts.",
        )


def _persist_media(
    db: Session,
    post_id: uuid.UUID,
    media_file: UploadFile,
    author_prefix: str,
) -> None:
    """Upload the media file and persist a PostMedia row."""
    storage_path, media_type, mime_type = upload_media(
        file=media_file,
        bucket=settings.SUPABASE_POST_MEDIA_BUCKET,
        prefix=author_prefix,
    )
    post_repo.create_media(
        db,
        post_id=post_id,
        storage_path=storage_path,
        media_type=media_type,
        mime_type=mime_type,
        original_filename=media_file.filename or None,
    )


# ---------------------------------------------------------------------------
# Public service functions
# ---------------------------------------------------------------------------


def create_post(
    db: Session,
    *,
    stream_id: uuid.UUID,
    current_user: User,
    data: PostCreate,
    media_file: Optional[UploadFile],
    media_storage_path: Optional[str] = None,
    media_type: Optional[str] = None,
    media_mime_type: Optional[str] = None,
    media_filename: Optional[str] = None,
) -> PostResponse:
    stream = stream_repo.get_by_id(db, stream_id)
    if stream is None or stream.is_deleted:
        raise HTTPException(status_code=status.HTTP_404_NOT_FOUND, detail="Stream not found.")

    if stream.owner_id != current_user.id:
        raise HTTPException(
            status_code=status.HTTP_403_FORBIDDEN,
            detail="Only the stream owner can post to this stream.",
        )

    has_media = bool(media_file) or bool(media_storage_path)
    if not data.content and not has_media:
        raise HTTPException(
            status_code=status.HTTP_422_UNPROCESSABLE_ENTITY,
            detail="A post must have content, a media attachment, or both.",
        )

    post = post_repo.create(
        db,
        stream_id=stream_id,
        author_id=current_user.id,
        type=data.type,
        content=data.content,
        trade_data=data.trade_data,
        parent_post_id=None,
    )

    if media_storage_path and media_type and media_mime_type:
        post_repo.create_media(
            db,
            post_id=post.id,
            storage_path=media_storage_path,
            media_type=PostMediaType(media_type),
            mime_type=media_mime_type,
            original_filename=media_filename,
        )
    elif media_file and media_file.filename:
        _persist_media(db, post.id, media_file, str(current_user.id))

    db.commit()
    db.refresh(post)
    return _build_response(db, post, current_user=current_user)


def create_reply(
    db: Session,
    *,
    parent_post_id: uuid.UUID,
    current_user: User,
    data: PostCreate,
    media_file: Optional[UploadFile],
) -> PostResponse:
    parent = post_repo.get_by_id(db, parent_post_id)
    if parent is None or parent.is_deleted:
        raise HTTPException(
            status_code=status.HTTP_404_NOT_FOUND, detail="Post not found."
        )

    if parent.parent_post_id is not None:
        raise HTTPException(
            status_code=status.HTTP_422_UNPROCESSABLE_ENTITY,
            detail="Cannot reply to a reply. Replies must target a top-level post.",
        )

    stream = stream_repo.get_by_id(db, parent.stream_id)
    if stream and not stream.forum_enabled and current_user.id != stream.owner_id:
        raise HTTPException(
            status_code=status.HTTP_403_FORBIDDEN,
            detail="Replies are disabled for this stream.",
        )

    _check_stream_view_access(db, parent.stream_id, current_user)

    if not data.content and not media_file:
        raise HTTPException(
            status_code=status.HTTP_422_UNPROCESSABLE_ENTITY,
            detail="A reply must have content, a media attachment, or both.",
        )

    post = post_repo.create(
        db,
        stream_id=parent.stream_id,
        author_id=current_user.id,
        type=data.type,
        content=data.content,
        trade_data=data.trade_data,
        parent_post_id=parent_post_id,
    )

    if media_file and media_file.filename:
        _persist_media(db, post.id, media_file, str(current_user.id))

    db.commit()
    db.refresh(post)
    return _build_response(db, post, current_user=current_user)


def get_post(db: Session, post_id: uuid.UUID, *, current_user: User) -> PostResponse:
    post = post_repo.get_by_id(db, post_id)
    if post is None or post.is_deleted:
        raise HTTPException(status_code=status.HTTP_404_NOT_FOUND, detail="Post not found.")
    return _build_response(db, post, current_user=current_user)


def list_stream_posts(
    db: Session,
    *,
    stream_id: uuid.UUID,
    current_user: User,
    cursor_post_id: Optional[uuid.UUID],
    limit: int,
) -> PostListResponse:
    stream = stream_repo.get_by_id(db, stream_id)
    if stream is None or stream.is_deleted:
        raise HTTPException(status_code=status.HTTP_404_NOT_FOUND, detail="Stream not found.")

    if stream.privacy != StreamPrivacy.public and stream.owner_id != current_user.id:
        member = stream_repo.get_member(db, user_id=current_user.id, stream_id=stream_id)
        if member is None or member.status != MemberStatus.active:
            raise HTTPException(
                status_code=status.HTTP_403_FORBIDDEN,
                detail="You must be an active member to view posts in this stream.",
            )

    posts = post_repo.list_by_stream(
        db,
        stream_id=stream_id,
        limit=limit + 1,
        cursor_post_id=cursor_post_id,
    )

    has_more = len(posts) > limit
    if has_more:
        posts = posts[:limit]

    items = [_build_response(db, p, current_user=current_user) for p in posts]
    next_cursor = posts[-1].id if has_more else None
    return PostListResponse(items=items, next_cursor=next_cursor)


def list_replies(
    db: Session,
    *,
    post_id: uuid.UUID,
    current_user: User,
    cursor_post_id: Optional[uuid.UUID],
    limit: int,
) -> PostListResponse:
    parent = post_repo.get_by_id(db, post_id)
    if parent is None or parent.is_deleted:
        raise HTTPException(status_code=status.HTTP_404_NOT_FOUND, detail="Post not found.")

    _check_stream_view_access(db, parent.stream_id, current_user)

    replies = post_repo.list_replies(
        db,
        parent_post_id=post_id,
        limit=limit + 1,
        cursor_post_id=cursor_post_id,
    )

    has_more = len(replies) > limit
    if has_more:
        replies = replies[:limit]

    items = [_build_response(db, r, current_user=current_user, parent_is_deleted=parent.is_deleted) for r in replies]
    next_cursor = replies[-1].id if has_more else None
    return PostListResponse(items=items, next_cursor=next_cursor)


def list_my_posts(
    db: Session,
    *,
    current_user: User,
    cursor_post_id: Optional[uuid.UUID],
    limit: int,
) -> PostListResponse:
    posts = post_repo.list_by_author(
        db,
        author_id=current_user.id,
        limit=limit + 1,
        cursor_post_id=cursor_post_id,
    )

    has_more = len(posts) > limit
    if has_more:
        posts = posts[:limit]

    items = [_build_response(db, p, current_user=current_user) for p in posts]
    next_cursor = posts[-1].id if has_more else None
    return PostListResponse(items=items, next_cursor=next_cursor)


def update_post(
    db: Session,
    *,
    post_id: uuid.UUID,
    current_user: User,
    data: PostUpdate,
) -> PostResponse:
    post = post_repo.get_by_id(db, post_id)
    if post is None or post.is_deleted:
        raise HTTPException(status_code=status.HTTP_404_NOT_FOUND, detail="Post not found.")

    if post.author_id != current_user.id:
        raise HTTPException(
            status_code=status.HTTP_403_FORBIDDEN,
            detail="Only the post author can edit this post.",
        )

    if data.content is not None:
        post_repo.update_content(db, post, data.content)

    db.commit()
    db.refresh(post)
    return _build_response(db, post, current_user=current_user)


def delete_post(
    db: Session,
    *,
    post_id: uuid.UUID,
    current_user: User,
) -> None:
    post = post_repo.get_by_id(db, post_id)
    if post is None or post.is_deleted:
        raise HTTPException(status_code=status.HTTP_404_NOT_FOUND, detail="Post not found.")

    stream = stream_repo.get_by_id(db, post.stream_id)
    is_stream_owner = stream is not None and stream.owner_id == current_user.id

    if post.author_id != current_user.id and not is_stream_owner:
        raise HTTPException(
            status_code=status.HTTP_403_FORBIDDEN,
            detail="Only the post author or stream owner can delete this post.",
        )

    post_repo.soft_delete(db, post)
    db.commit()


def upvote_post(
    db: Session,
    *,
    post_id: uuid.UUID,
    current_user: User,
) -> PostResponse:
    post = post_repo.get_by_id(db, post_id)
    if post is None or post.is_deleted:
        raise HTTPException(status_code=status.HTTP_404_NOT_FOUND, detail="Post not found.")

    existing = post_repo.get_upvote(db, user_id=current_user.id, post_id=post_id)
    if existing is None:
        _check_stream_view_access(db, post.stream_id, current_user)
        post_repo.create_upvote(db, user_id=current_user.id, post_id=post_id)
        db.commit()
        db.refresh(post)

    return _build_response(db, post, current_user=current_user)


def unupvote_post(
    db: Session,
    *,
    post_id: uuid.UUID,
    current_user: User,
) -> PostResponse:
    post = post_repo.get_by_id(db, post_id)
    if post is None or post.is_deleted:
        raise HTTPException(status_code=status.HTTP_404_NOT_FOUND, detail="Post not found.")

    existing = post_repo.get_upvote(db, user_id=current_user.id, post_id=post_id)
    if existing is not None:
        post_repo.delete_upvote(db, existing)
        db.commit()
        db.refresh(post)

    return _build_response(db, post, current_user=current_user)
