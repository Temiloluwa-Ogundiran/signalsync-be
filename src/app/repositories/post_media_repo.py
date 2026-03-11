import uuid
from typing import Optional

from sqlalchemy import select
from sqlalchemy.orm import Session

from app.models.post import PostMedia, PostMediaType


def create(
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


def get_by_post_id(db: Session, post_id: uuid.UUID) -> Optional[PostMedia]:
    stmt = select(PostMedia).where(PostMedia.post_id == post_id)
    return db.execute(stmt).scalar_one_or_none()


def delete(db: Session, post_media: PostMedia) -> None:
    db.delete(post_media)
    db.flush()
