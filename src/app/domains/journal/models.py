import enum
import uuid
from datetime import date, datetime, timezone
from typing import List, Optional

from sqlalchemy import (
    Boolean,
    CheckConstraint,
    Date,
    DateTime,
    Enum,
    ForeignKey,
    Index,
    Integer,
    String,
    Text,
    UniqueConstraint,
)
from sqlalchemy.dialects.postgresql import ARRAY, JSONB, UUID
from sqlalchemy.orm import Mapped, mapped_column, relationship

from app.core.database import Base


class DailyJournal(Base):
    __tablename__ = "daily_journals"
    __table_args__ = (UniqueConstraint("account_id", "trading_date"),)

    id: Mapped[uuid.UUID] = mapped_column(
        UUID(as_uuid=True),
        primary_key=True,
        default=uuid.uuid4,
    )

    account_id: Mapped[uuid.UUID] = mapped_column(
        UUID(as_uuid=True),
        ForeignKey("trading_accounts.id", ondelete="CASCADE"),
        nullable=False,
        index=True,
    )
    trading_date: Mapped[date] = mapped_column(Date, nullable=False, index=True)

    created_at: Mapped[datetime] = mapped_column(
        DateTime(timezone=True),
        default=lambda: datetime.now(timezone.utc),
        nullable=False,
    )

    reviewed_at: Mapped[Optional[datetime]] = mapped_column(
        DateTime(timezone=True),
        nullable=True,
    )

    account: Mapped["TradingAccount"] = relationship(back_populates="daily_journals")  # noqa: F821
    trade_journals: Mapped[List["TradeJournal"]] = relationship(
        back_populates="daily_journal",
    )
    messages: Mapped[List["JournalMessage"]] = relationship(
        back_populates="daily_journal",
        cascade="all, delete-orphan",
    )


class TradeJournal(Base):
    __tablename__ = "trade_journals"

    id: Mapped[uuid.UUID] = mapped_column(
        UUID(as_uuid=True),
        primary_key=True,
        default=uuid.uuid4,
    )

    trade_id: Mapped[uuid.UUID] = mapped_column(
        UUID(as_uuid=True),
        ForeignKey("trades.id", ondelete="CASCADE"),
        nullable=False,
        unique=True,
        index=True,
    )
    daily_journal_id: Mapped[Optional[uuid.UUID]] = mapped_column(
        UUID(as_uuid=True),
        ForeignKey("daily_journals.id", ondelete="SET NULL"),
        nullable=True,
        index=True,
    )

    created_at: Mapped[datetime] = mapped_column(
        DateTime(timezone=True),
        default=lambda: datetime.now(timezone.utc),
        nullable=False,
    )

    reviewed_at: Mapped[Optional[datetime]] = mapped_column(
        DateTime(timezone=True),
        nullable=True,
    )
    rating: Mapped[Optional[int]] = mapped_column(Integer, nullable=True)

    trade: Mapped["Trade"] = relationship(back_populates="journal")  # noqa: F821
    daily_journal: Mapped[Optional["DailyJournal"]] = relationship(back_populates="trade_journals")
    messages: Mapped[List["JournalMessage"]] = relationship(
        back_populates="trade_journal",
        cascade="all, delete-orphan",
    )


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

    daily_journal: Mapped[Optional["DailyJournal"]] = relationship(back_populates="messages")
    trade_journal: Mapped[Optional["TradeJournal"]] = relationship(back_populates="messages")
    author: Mapped[Optional["User"]] = relationship(back_populates="journal_messages")  # noqa: F821
    attachments: Mapped[List["JournalAttachment"]] = relationship(
        back_populates="message",
        cascade="all, delete-orphan",
    )


class JournalAttachment(Base):
    __tablename__ = "journal_attachments"

    id: Mapped[uuid.UUID] = mapped_column(
        UUID(as_uuid=True),
        primary_key=True,
        default=uuid.uuid4,
    )

    message_id: Mapped[uuid.UUID] = mapped_column(
        UUID(as_uuid=True),
        ForeignKey("journal_messages.id", ondelete="CASCADE"),
        nullable=False,
        index=True,
    )

    storage_path: Mapped[str] = mapped_column(Text, nullable=False)
    media_type: Mapped[str] = mapped_column(String(20), nullable=False)
    mime_type: Mapped[str] = mapped_column(String(100), nullable=False)
    original_filename: Mapped[Optional[str]] = mapped_column(String(255), nullable=True)
    caption: Mapped[Optional[str]] = mapped_column(Text, nullable=True)

    created_at: Mapped[datetime] = mapped_column(
        DateTime(timezone=True),
        default=lambda: datetime.now(timezone.utc),
        nullable=False,
    )

    message: Mapped["JournalMessage"] = relationship(back_populates="attachments")


class JournalTemplateType(str, enum.Enum):
    daily = "daily"
    trade = "trade"


class JournalTemplate(Base):
    __tablename__ = "journal_templates"

    id: Mapped[uuid.UUID] = mapped_column(
        UUID(as_uuid=True),
        primary_key=True,
        default=uuid.uuid4,
    )

    name: Mapped[str] = mapped_column(String(120), nullable=False)

    template_type: Mapped[JournalTemplateType] = mapped_column(
        Enum(
            JournalTemplateType,
            values_callable=lambda x: [e.value for e in x],
            name="journaltemplatetypeenum",
        ),
        nullable=False,
    )

    is_system: Mapped[bool] = mapped_column(Boolean, nullable=False, default=False)
    owner_id: Mapped[Optional[uuid.UUID]] = mapped_column(
        UUID(as_uuid=True),
        ForeignKey("users.id", ondelete="CASCADE"),
        nullable=True,
        index=True,
    )
    questions: Mapped[list] = mapped_column(JSONB, nullable=False)

    created_at: Mapped[datetime] = mapped_column(
        DateTime(timezone=True),
        default=lambda: datetime.now(timezone.utc),
        nullable=False,
    )

    owner: Mapped[Optional["User"]] = relationship(back_populates="journal_templates")  # noqa: F821


class TagCategory(Base):
    __tablename__ = "tag_categories"

    id: Mapped[uuid.UUID] = mapped_column(
        UUID(as_uuid=True),
        primary_key=True,
        default=uuid.uuid4,
    )
    user_id: Mapped[Optional[uuid.UUID]] = mapped_column(
        UUID(as_uuid=True),
        ForeignKey("users.id", ondelete="CASCADE"),
        nullable=True,
        index=True,
    )
    title: Mapped[str] = mapped_column(String(100), nullable=False)
    is_system: Mapped[bool] = mapped_column(Boolean, default=False, nullable=False)
    created_at: Mapped[datetime] = mapped_column(
        DateTime(timezone=True),
        default=lambda: datetime.now(timezone.utc),
        nullable=False,
    )
    updated_at: Mapped[datetime] = mapped_column(
        DateTime(timezone=True),
        default=lambda: datetime.now(timezone.utc),
        onupdate=lambda: datetime.now(timezone.utc),
        nullable=False,
    )

    options: Mapped[List["TagOption"]] = relationship(
        back_populates="category",
        cascade="all, delete-orphan",
    )


class TagOption(Base):
    __tablename__ = "tag_options"

    id: Mapped[uuid.UUID] = mapped_column(
        UUID(as_uuid=True),
        primary_key=True,
        default=uuid.uuid4,
    )
    category_id: Mapped[uuid.UUID] = mapped_column(
        UUID(as_uuid=True),
        ForeignKey("tag_categories.id", ondelete="CASCADE"),
        nullable=False,
        index=True,
    )
    user_id: Mapped[Optional[uuid.UUID]] = mapped_column(
        UUID(as_uuid=True),
        ForeignKey("users.id", ondelete="CASCADE"),
        nullable=True,
        index=True,
    )
    value: Mapped[str] = mapped_column(String(100), nullable=False)
    color: Mapped[Optional[str]] = mapped_column(String(7), nullable=True)  # HEX code
    created_at: Mapped[datetime] = mapped_column(
        DateTime(timezone=True),
        default=lambda: datetime.now(timezone.utc),
        nullable=False,
    )
    updated_at: Mapped[datetime] = mapped_column(
        DateTime(timezone=True),
        default=lambda: datetime.now(timezone.utc),
        onupdate=lambda: datetime.now(timezone.utc),
        nullable=False,
    )

    category: Mapped["TagCategory"] = relationship(back_populates="options")


class TradeTagSelection(Base):
    __tablename__ = "trade_tag_selections"

    trade_id: Mapped[uuid.UUID] = mapped_column(
        UUID(as_uuid=True),
        ForeignKey("trades.id", ondelete="CASCADE"),
        primary_key=True,
    )
    option_id: Mapped[uuid.UUID] = mapped_column(
        UUID(as_uuid=True),
        ForeignKey("tag_options.id", ondelete="CASCADE"),
        primary_key=True,
    )

