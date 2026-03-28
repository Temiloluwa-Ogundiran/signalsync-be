import uuid
from datetime import datetime, timezone
from typing import List, Optional

from sqlalchemy import DateTime, ForeignKey
from sqlalchemy.dialects.postgresql import UUID
from sqlalchemy.orm import Mapped, mapped_column, relationship

from app.core.database import Base


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

    trade: Mapped["Trade"] = relationship(back_populates="journal")  # noqa: F821
    daily_journal: Mapped[Optional["DailyJournal"]] = relationship(back_populates="trade_journals")  # noqa: F821
    messages: Mapped[List["JournalMessage"]] = relationship(  # noqa: F821
        back_populates="trade_journal",
        cascade="all, delete-orphan",
    )
