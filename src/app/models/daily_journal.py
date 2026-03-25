import uuid
from datetime import date, datetime, timezone
from typing import List

from sqlalchemy import Date, DateTime, ForeignKey, UniqueConstraint
from sqlalchemy.dialects.postgresql import UUID
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

    account: Mapped["TradingAccount"] = relationship(back_populates="daily_journals")  # noqa: F821
    trade_journals: Mapped[List["TradeJournal"]] = relationship(  # noqa: F821
        back_populates="daily_journal",
    )
    messages: Mapped[List["JournalMessage"]] = relationship(  # noqa: F821
        back_populates="daily_journal",
        cascade="all, delete-orphan",
    )
