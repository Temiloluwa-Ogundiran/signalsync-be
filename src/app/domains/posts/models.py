import enum
import uuid
from datetime import datetime, timezone
from typing import Optional

from sqlalchemy import Boolean, DateTime, Enum, ForeignKey, Index, String, Text
from sqlalchemy.dialects.postgresql import JSONB, UUID
from sqlalchemy.orm import Mapped, mapped_column, relationship

from app.core.database import Base


class PostType(str, enum.Enum):
    signal = "signal"
    text = "text"
    education = "education"


class PostMediaType(str, enum.Enum):
    image = "image"
    video = "video"
    document = "document"


class Post(Base):
    __tablename__ = "posts"

    id: Mapped[uuid.UUID] = mapped_column(
        UUID(as_uuid=True),
        primary_key=True,
        default=uuid.uuid4,
    )

    stream_id: Mapped[uuid.UUID] = mapped_column(
        UUID(as_uuid=True),
        ForeignKey("streams.id", ondelete="CASCADE"),
        nullable=False,
        index=True,
    )

    author_id: Mapped[uuid.UUID] = mapped_column(
        UUID(as_uuid=True),
        ForeignKey("users.id", ondelete="CASCADE"),
        nullable=False,
        index=True,
    )

    type: Mapped[PostType] = mapped_column(
        Enum(PostType, values_callable=lambda x: [e.value for e in x], name="posttypeenum"),
        nullable=False,
    )

    # nullable — media-only posts are allowed
    content: Mapped[Optional[str]] = mapped_column(Text, nullable=True)

    # Only populated when type == signal
    trade_data: Mapped[Optional[dict]] = mapped_column(JSONB, nullable=True)

    # Threading — null means top-level post; non-null means a reply
    parent_post_id: Mapped[Optional[uuid.UUID]] = mapped_column(
        UUID(as_uuid=True),
        ForeignKey("posts.id", ondelete="CASCADE"),
        nullable=True,
        index=True,
    )

    # Soft delete
    is_deleted: Mapped[bool] = mapped_column(Boolean, default=False, nullable=False)
    deleted_at: Mapped[Optional[datetime]] = mapped_column(
        DateTime(timezone=True), nullable=True, default=None
    )

    created_at: Mapped[datetime] = mapped_column(
        DateTime(timezone=True),
        default=lambda: datetime.now(timezone.utc),
        nullable=False,
        index=True,
    )

    updated_at: Mapped[datetime] = mapped_column(
        DateTime(timezone=True),
        default=lambda: datetime.now(timezone.utc),
        onupdate=lambda: datetime.now(timezone.utc),
        nullable=False,
    )

    # Relationships
    stream: Mapped["Stream"] = relationship(back_populates="posts")  # noqa: F821
    author: Mapped["User"] = relationship(back_populates="posts")  # noqa: F821
    parent: Mapped[Optional["Post"]] = relationship(
        remote_side="Post.id",
        backref="replies",
    )
    media: Mapped[Optional["PostMedia"]] = relationship(
        back_populates="post",
        uselist=False,
        cascade="all, delete-orphan",
        lazy="joined",
    )


class PostMedia(Base):
    __tablename__ = "post_media"

    id: Mapped[uuid.UUID] = mapped_column(
        UUID(as_uuid=True),
        primary_key=True,
        default=uuid.uuid4,
    )

    post_id: Mapped[uuid.UUID] = mapped_column(
        UUID(as_uuid=True),
        ForeignKey("posts.id", ondelete="CASCADE"),
        nullable=False,
        unique=True,  # enforces the one-media-per-post constraint at DB level
        index=True,
    )

    # The S3 object key (used to generate presigned GET URLs).
    # Never expose this directly to clients — always sign first.
    storage_path: Mapped[str] = mapped_column(Text, nullable=False)

    media_type: Mapped[PostMediaType] = mapped_column(
        Enum(
            PostMediaType,
            values_callable=lambda x: [e.value for e in x],
            name="postmediatypeenum",
        ),
        nullable=False,
    )

    mime_type: Mapped[str] = mapped_column(String(100), nullable=False)

    original_filename: Mapped[Optional[str]] = mapped_column(String(255), nullable=True)

    created_at: Mapped[datetime] = mapped_column(
        DateTime(timezone=True),
        default=lambda: datetime.now(timezone.utc),
        nullable=False,
    )

    # Relationships
    post: Mapped["Post"] = relationship(back_populates="media")


# Composite indexes for common query patterns
Index("ix_posts_stream_created", Post.stream_id, Post.created_at)
Index("ix_posts_parent_created", Post.parent_post_id, Post.created_at)


class PostUpvote(Base):
    """One row per (user, post) pair — composite PK enforces uniqueness."""

    __tablename__ = "post_upvotes"
    __table_args__ = (Index("ix_post_upvotes_post_id", "post_id"),)

    user_id: Mapped[uuid.UUID] = mapped_column(
        UUID(as_uuid=True),
        ForeignKey("users.id", ondelete="CASCADE"),
        nullable=False,
        primary_key=True,
    )
    post_id: Mapped[uuid.UUID] = mapped_column(
        UUID(as_uuid=True),
        ForeignKey("posts.id", ondelete="CASCADE"),
        nullable=False,
        primary_key=True,
    )
    created_at: Mapped[datetime] = mapped_column(
        DateTime(timezone=True),
        default=lambda: datetime.now(timezone.utc),
        nullable=False,
    )
