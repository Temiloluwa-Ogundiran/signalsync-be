import uuid
from datetime import datetime, timezone
from typing import Optional

from sqlalchemy import func, select
from sqlalchemy.orm import Session, joinedload

from app.domains.posts.models import Post, PostMedia, PostMediaType, PostType, PostUpvote


def create(
    db: Session,
    *,
    stream_id: uuid.UUID,
    author_id: uuid.UUID,
    type: PostType,
    content: Optional[str],
    trade_data: Optional[dict],
    parent_post_id: Optional[uuid.UUID],
) -> Post:
    post = Post(
        stream_id=stream_id,
        author_id=author_id,
        type=type,
        content=content,
        trade_data=trade_data,
        parent_post_id=parent_post_id,
    )
    db.add(post)
    db.flush()  # populate post.id without committing
    return post


def get_by_id(db: Session, post_id: uuid.UUID) -> Optional[Post]:
    stmt = (
        select(Post)
        .where(Post.id == post_id)
        .options(
            joinedload(Post.author),
            joinedload(Post.media),
        )
    )
    return db.execute(stmt).unique().scalar_one_or_none()


def list_by_stream(
    db: Session,
    *,
    stream_id: uuid.UUID,
    limit: int,
    cursor_post_id: Optional[uuid.UUID],
) -> list[Post]:
    """
    Return top-level (non-deleted) posts for a stream, newest first.
    Uses keyset / cursor pagination: cursor points to the *last seen* post's id.
    """
    stmt = (
        select(Post)
        .where(
            Post.stream_id == stream_id,
            Post.parent_post_id.is_(None),
            Post.is_deleted.is_(False),
        )
        .options(
            joinedload(Post.author),
            joinedload(Post.media),
        )
        .order_by(Post.created_at.desc(), Post.id.desc())
        .limit(limit)
    )

    if cursor_post_id is not None:
        cursor_stmt = select(Post.created_at, Post.id).where(Post.id == cursor_post_id)
        cursor_row = db.execute(cursor_stmt).one_or_none()
        if cursor_row is not None:
            cursor_created_at, cursor_id = cursor_row
            stmt = stmt.where(
                (Post.created_at < cursor_created_at)
                | (
                    (Post.created_at == cursor_created_at)
                    & (Post.id < cursor_id)
                )
            )

    return list(db.execute(stmt).unique().scalars().all())


def list_replies(
    db: Session,
    *,
    parent_post_id: uuid.UUID,
    limit: int,
    cursor_post_id: Optional[uuid.UUID],
) -> list[Post]:
    """Return replies to a post, oldest first (chronological thread order)."""
    stmt = (
        select(Post)
        .where(
            Post.parent_post_id == parent_post_id,
            Post.is_deleted.is_(False),
        )
        .options(
            joinedload(Post.author),
            joinedload(Post.media),
        )
        .order_by(Post.created_at.asc(), Post.id.asc())
        .limit(limit)
    )

    if cursor_post_id is not None:
        cursor_stmt = select(Post.created_at, Post.id).where(Post.id == cursor_post_id)
        cursor_row = db.execute(cursor_stmt).one_or_none()
        if cursor_row is not None:
            cursor_created_at, cursor_id = cursor_row
            stmt = stmt.where(
                (Post.created_at > cursor_created_at)
                | (
                    (Post.created_at == cursor_created_at)
                    & (Post.id > cursor_id)
                )
            )

    return list(db.execute(stmt).unique().scalars().all())


def list_by_author(
    db: Session,
    *,
    author_id: uuid.UUID,
    limit: int,
    cursor_post_id: Optional[uuid.UUID],
) -> list[Post]:
    """
    Return top-level (non-deleted) posts for an author, newest first.
    Uses keyset / cursor pagination: cursor points to the *last seen* post's id.
    """
    stmt = (
        select(Post)
        .where(
            Post.author_id == author_id,
            Post.parent_post_id.is_(None),
            Post.is_deleted.is_(False),
        )
        .options(
            joinedload(Post.author),
            joinedload(Post.media),
        )
        .order_by(Post.created_at.desc(), Post.id.desc())
        .limit(limit)
    )

    if cursor_post_id is not None:
        cursor_stmt = select(Post.created_at, Post.id).where(Post.id == cursor_post_id)
        cursor_row = db.execute(cursor_stmt).one_or_none()
        if cursor_row is not None:
            cursor_created_at, cursor_id = cursor_row
            stmt = stmt.where(
                (Post.created_at < cursor_created_at)
                | (
                    (Post.created_at == cursor_created_at)
                    & (Post.id < cursor_id)
                )
            )

    return list(db.execute(stmt).unique().scalars().all())


def count_replies(db: Session, parent_post_id: uuid.UUID) -> int:
    stmt = select(func.count(Post.id)).where(
        Post.parent_post_id == parent_post_id,
        Post.is_deleted.is_(False),
    )
    return db.execute(stmt).scalar_one()


def update_content(db: Session, post: Post, content: str) -> Post:
    post.content = content
    db.flush()
    return post


def soft_delete(db: Session, post: Post) -> None:
    post.is_deleted = True
    post.deleted_at = datetime.now(timezone.utc)
    db.flush()


# ── PostMedia ────────────────────────────────────────────────────────────────

def create_media(
    db: Session,
    *,
    post_id: uuid.UUID,
    storage_path: str,
    media_type: PostMediaType,
    mime_type: str,
    original_filename: Optional[str],
) -> PostMedia:
    media = PostMedia(
        post_id=post_id,
        storage_path=storage_path,
        media_type=media_type,
        mime_type=mime_type,
        original_filename=original_filename,
    )
    db.add(media)
    db.flush()
    return media


def get_media_by_post_id(db: Session, post_id: uuid.UUID) -> Optional[PostMedia]:
    stmt = select(PostMedia).where(PostMedia.post_id == post_id)
    return db.execute(stmt).scalar_one_or_none()


def delete_media(db: Session, post_media: PostMedia) -> None:
    db.delete(post_media)
    db.flush()


# ── PostUpvote ───────────────────────────────────────────────────────────────

def get_upvote(db: Session, *, user_id: uuid.UUID, post_id: uuid.UUID) -> Optional[PostUpvote]:
    return (
        db.query(PostUpvote)
        .filter(PostUpvote.user_id == user_id, PostUpvote.post_id == post_id)
        .first()
    )


def create_upvote(db: Session, *, user_id: uuid.UUID, post_id: uuid.UUID) -> PostUpvote:
    upvote = PostUpvote(user_id=user_id, post_id=post_id)
    db.add(upvote)
    return upvote


def delete_upvote(db: Session, upvote: PostUpvote) -> None:
    db.delete(upvote)


def count_upvotes(db: Session, post_id: uuid.UUID) -> int:
    return db.query(PostUpvote).filter(PostUpvote.post_id == post_id).count()
