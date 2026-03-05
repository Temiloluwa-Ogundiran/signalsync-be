import enum
import uuid
from datetime import datetime, timezone

from sqlalchemy import DateTime, Enum, ForeignKey
from sqlalchemy.dialects.postgresql import UUID
from sqlalchemy.orm import Mapped, mapped_column, relationship

from app.core.database import Base


class MemberStatus(str, enum.Enum):
    active = "active"
    pending = "pending"


class StreamMember(Base):
    __tablename__ = "stream_members"

    user_id: Mapped[uuid.UUID] = mapped_column(
        UUID(as_uuid=True),
        ForeignKey("users.id", ondelete="CASCADE"),
        primary_key=True,
        nullable=False,
    )

    stream_id: Mapped[uuid.UUID] = mapped_column(
        UUID(as_uuid=True),
        ForeignKey("streams.id", ondelete="CASCADE"),
        primary_key=True,
        nullable=False,
    )

    status: Mapped[MemberStatus] = mapped_column(
        Enum(
            MemberStatus,
            values_callable=lambda x: [e.value for e in x],
            name="memberstatusenum",
        ),
        nullable=False,
        default=MemberStatus.active,
    )

    joined_at: Mapped[datetime] = mapped_column(
        DateTime(timezone=True),
        default=lambda: datetime.now(timezone.utc),
        nullable=False,
    )

    # Relationships
    user: Mapped["User"] = relationship(back_populates="stream_memberships")  # noqa: F821
    stream: Mapped["Stream"] = relationship(back_populates="members")  # noqa: F821
