import enum
import uuid
from datetime import datetime, timezone
from decimal import Decimal
from typing import Optional

from sqlalchemy import DateTime, Enum, ForeignKey, Integer, Numeric, String, UniqueConstraint
from sqlalchemy.dialects.postgresql import UUID
from sqlalchemy.orm import Mapped, mapped_column, relationship

from app.core.database import Base


class TradeDirection(str, enum.Enum):
    buy = "buy"
    sell = "sell"


class TradeSession(str, enum.Enum):
    asian = "asian"
    london = "london"
    new_york = "new_york"
    london_ny_overlap = "london_ny_overlap"
    off_hours = "off_hours"


class Trade(Base):
    __tablename__ = "trades"
    __table_args__ = (UniqueConstraint("account_id", "broker_trade_id"),)

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

    broker_trade_id: Mapped[str] = mapped_column(String(64), nullable=False)
    symbol: Mapped[str] = mapped_column(String(20), nullable=False)

    direction: Mapped[TradeDirection] = mapped_column(
        Enum(TradeDirection, values_callable=lambda x: [e.value for e in x], name="tradedirectionenum"),
        nullable=False,
    )

    open_price: Mapped[Decimal] = mapped_column(Numeric(18, 5), nullable=False)
    close_price: Mapped[Decimal] = mapped_column(Numeric(18, 5), nullable=False)
    volume: Mapped[Decimal] = mapped_column(Numeric(10, 2), nullable=False)
    profit: Mapped[Decimal] = mapped_column(Numeric(14, 2), nullable=False)
    commission: Mapped[Decimal] = mapped_column(Numeric(14, 2), nullable=False)
    swap: Mapped[Decimal] = mapped_column(Numeric(14, 2), nullable=False)
    net_profit: Mapped[Decimal] = mapped_column(Numeric(14, 2), nullable=False)

    duration_seconds: Mapped[int] = mapped_column(Integer, nullable=False)

    session: Mapped[TradeSession] = mapped_column(
        Enum(TradeSession, values_callable=lambda x: [e.value for e in x], name="tradesessionenum"),
        nullable=False,
    )

    opened_at: Mapped[datetime] = mapped_column(DateTime(timezone=True), nullable=False, index=True)
    closed_at: Mapped[datetime] = mapped_column(DateTime(timezone=True), nullable=False, index=True)

    created_at: Mapped[datetime] = mapped_column(
        DateTime(timezone=True),
        default=lambda: datetime.now(timezone.utc),
        nullable=False,
    )

    account: Mapped["TradingAccount"] = relationship(back_populates="trades")  # noqa: F821
    journal: Mapped[Optional["TradeJournal"]] = relationship(  # noqa: F821
        back_populates="trade",
        uselist=False,
        cascade="all, delete-orphan",
    )
