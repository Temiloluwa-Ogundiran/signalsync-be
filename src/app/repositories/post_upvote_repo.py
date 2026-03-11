import uuid
from typing import Optional

from sqlalchemy.orm import Session

from app.models.post import PostUpvote


def get(db: Session, *, user_id: uuid.UUID, post_id: uuid.UUID) -> Optional[PostUpvote]:
    return (
        db.query(PostUpvote)
        .filter(PostUpvote.user_id == user_id, PostUpvote.post_id == post_id)
        .first()
    )


def create(db: Session, *, user_id: uuid.UUID, post_id: uuid.UUID) -> PostUpvote:
    upvote = PostUpvote(user_id=user_id, post_id=post_id)
    db.add(upvote)
    return upvote


def delete(db: Session, upvote: PostUpvote) -> None:
    db.delete(upvote)


def count(db: Session, post_id: uuid.UUID) -> int:
    return db.query(PostUpvote).filter(PostUpvote.post_id == post_id).count()
