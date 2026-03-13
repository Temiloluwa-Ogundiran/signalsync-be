import enum
import uuid
from datetime import datetime, timezone
from decimal import Decimal
from typing import List, Optional

from sqlalchemy import Boolean, DateTime, Enum, ForeignKey, Numeric, String, Text
from sqlalchemy.dialects.postgresql import JSONB, UUID
from sqlalchemy.orm import Mapped, mapped_column, relationship

from app.core.database import Base


class StreamPrivacy(str, enum.Enum):
    public = "public"
    private = "private"
    paid = "paid"


class Stream(Base):
    __tablename__ = "streams"

    id: Mapped[uuid.UUID] = mapped_column(
        UUID(as_uuid=True),
        primary_key=True,
        default=uuid.uuid4,
    )

    owner_id: Mapped[uuid.UUID] = mapped_column(
        UUID(as_uuid=True),
        ForeignKey("users.id", ondelete="CASCADE"),
        nullable=False,
        index=True,
    )

    name: Mapped[str] = mapped_column(String(120))
    description: Mapped[Optional[str]] = mapped_column(Text, nullable=True)

    privacy: Mapped[StreamPrivacy] = mapped_column(
        Enum(StreamPrivacy, values_callable=lambda x: [e.value for e in x], name="streamprivacyenum"),
        default=StreamPrivacy.public,
        nullable=False,
    )

    forum_enabled: Mapped[bool] = mapped_column(Boolean, default=True)

    # Paid streams
    price: Mapped[Optional[Decimal]] = mapped_column(
        Numeric(12, 2), nullable=True, default=None
    )

    # Media
    avatar_url: Mapped[Optional[str]] = mapped_column(String, nullable=True, default=None)
    banner_url: Mapped[Optional[str]] = mapped_column(String, nullable=True, default=None)

    tags: Mapped[Optional[dict]] = mapped_column(JSONB, nullable=True)

    # Join approval (private streams)
    require_join_approval: Mapped[bool] = mapped_column(Boolean, default=False, nullable=False)

    is_default: Mapped[bool] = mapped_column(Boolean, default=False, nullable=False)

    # Soft delete
    is_deleted: Mapped[bool] = mapped_column(Boolean, default=False, nullable=False)
    deleted_at: Mapped[Optional[datetime]] = mapped_column(
        DateTime(timezone=True), nullable=True, default=None
    )

    created_at: Mapped[datetime] = mapped_column(
        DateTime(timezone=True),
        default=lambda: datetime.now(timezone.utc),
        nullable=False,
    )

    # Relationships
    owner: Mapped["User"] = relationship(back_populates="streams")  # noqa: F821

    posts: Mapped[List["Post"]] = relationship(  # noqa: F821
        back_populates="stream",
        cascade="all, delete-orphan",
    )

    members: Mapped[List["StreamMember"]] = relationship(  # noqa: F821
        back_populates="stream",
        cascade="all, delete-orphan",
    )
