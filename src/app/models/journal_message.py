import enum
import uuid
from datetime import datetime, timezone
from typing import List, Optional

from sqlalchemy import Boolean, CheckConstraint, DateTime, Enum, ForeignKey, Index, Integer, String, Text
from sqlalchemy.dialects.postgresql import ARRAY, JSONB, UUID
from sqlalchemy.orm import Mapped, mapped_column, relationship

from app.core.database import Base


class JournalMessageType(str, enum.Enum):
    text = "text"
    voice = "voice"
    image = "image"
    system = "system"
    prompt = "prompt"
    ai_response = "ai_response"


class JournalMessage(Base):
    __tablename__ = "journal_messages"
    __table_args__ = (
        CheckConstraint(
            "((daily_journal_id IS NOT NULL AND trade_journal_id IS NULL) OR "
            "(trade_journal_id IS NOT NULL AND daily_journal_id IS NULL))",
            name="ck_journal_messages_exactly_one_context",
        ),
        Index("ix_jm_daily_journal_id", "daily_journal_id"),
        Index("ix_jm_trade_journal_id", "trade_journal_id"),
    )

    id: Mapped[uuid.UUID] = mapped_column(
        UUID(as_uuid=True),
        primary_key=True,
        default=uuid.uuid4,
    )

    daily_journal_id: Mapped[Optional[uuid.UUID]] = mapped_column(
        UUID(as_uuid=True),
        ForeignKey("daily_journals.id", ondelete="CASCADE"),
        nullable=True,
    )
    trade_journal_id: Mapped[Optional[uuid.UUID]] = mapped_column(
        UUID(as_uuid=True),
        ForeignKey("trade_journals.id", ondelete="CASCADE"),
        nullable=True,
    )
    author_id: Mapped[Optional[uuid.UUID]] = mapped_column(
        UUID(as_uuid=True),
        ForeignKey("users.id", ondelete="SET NULL"),
        nullable=True,
        index=True,
    )

    message_type: Mapped[JournalMessageType] = mapped_column(
        Enum(
            JournalMessageType,
            values_callable=lambda x: [e.value for e in x],
            name="journalmessagetypeenum",
        ),
        nullable=False,
    )

    content: Mapped[Optional[str]] = mapped_column(Text, nullable=True)
    tags: Mapped[List[str]] = mapped_column(ARRAY(Text), nullable=False, default=list)
    system_data: Mapped[Optional[dict]] = mapped_column(JSONB, nullable=True)

    audio_storage_path: Mapped[Optional[str]] = mapped_column(String, nullable=True)
    audio_duration_seconds: Mapped[Optional[int]] = mapped_column(Integer, nullable=True)

    is_edited: Mapped[bool] = mapped_column(Boolean, nullable=False, default=False)
    edited_at: Mapped[Optional[datetime]] = mapped_column(DateTime(timezone=True), nullable=True)

    created_at: Mapped[datetime] = mapped_column(
        DateTime(timezone=True),
        default=lambda: datetime.now(timezone.utc),
        nullable=False,
        index=True,
    )

    daily_journal: Mapped[Optional["DailyJournal"]] = relationship(back_populates="messages")  # noqa: F821
    trade_journal: Mapped[Optional["TradeJournal"]] = relationship(back_populates="messages")  # noqa: F821
    author: Mapped[Optional["User"]] = relationship(back_populates="journal_messages")  # noqa: F821
    attachments: Mapped[List["JournalAttachment"]] = relationship(  # noqa: F821
        back_populates="message",
        cascade="all, delete-orphan",
    )
