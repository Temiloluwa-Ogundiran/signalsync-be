import enum
import uuid
from datetime import datetime, timezone
from typing import List, Optional

from sqlalchemy import Boolean, DateTime, Enum, ForeignKey, String, UniqueConstraint
from sqlalchemy.dialects.postgresql import UUID
from sqlalchemy.orm import Mapped, mapped_column, relationship

from app.core.database import Base


class TradingAccountStatus(str, enum.Enum):
    pending_sync = "pending_sync"
    synced = "synced"
    error = "error"
    disconnected = "disconnected"


class TradingAccountProvisioningStatus(str, enum.Enum):
    pending = "pending"
    provisioned = "provisioned"
    failed = "failed"


class TradingAccountType(str, enum.Enum):
    demo = "demo"
    live = "live"


class TradingPlatform(str, enum.Enum):
    mt4 = "MT4"
    mt5 = "MT5"


class TradingAccount(Base):
    __tablename__ = "trading_accounts"
    __table_args__ = (UniqueConstraint("user_id", "meta_account_id"),)

    id: Mapped[uuid.UUID] = mapped_column(
        UUID(as_uuid=True),
        primary_key=True,
        default=uuid.uuid4,
    )

    user_id: Mapped[uuid.UUID] = mapped_column(
        UUID(as_uuid=True),
        ForeignKey("users.id", ondelete="CASCADE"),
        nullable=False,
        index=True,
    )

    meta_account_id: Mapped[str] = mapped_column(String(64), nullable=False)
    broker_name: Mapped[str] = mapped_column(String(120), nullable=False)
    broker_login: Mapped[str] = mapped_column(String(64), nullable=False)
    broker_server: Mapped[str] = mapped_column(String(120), nullable=False)
    encrypted_investor_password: Mapped[str] = mapped_column(String, nullable=False)
    encrypted_trader_password: Mapped[Optional[str]] = mapped_column(String, nullable=True)

    account_type: Mapped[TradingAccountType] = mapped_column(
        Enum(
            TradingAccountType,
            values_callable=lambda x: [e.value for e in x],
            name="tradingaccounttypeenum",
        ),
        nullable=False,
    )

    platform: Mapped[TradingPlatform] = mapped_column(
        Enum(
            TradingPlatform,
            values_callable=lambda x: [e.value for e in x],
            name="tradingplatformenum",
        ),
        nullable=False,
    )

    currency: Mapped[str] = mapped_column(String(10), nullable=False, default="USD")
    timezone: Mapped[str] = mapped_column(String(50), nullable=False, default="UTC")
    broker_utc_offset: Mapped[int] = mapped_column(nullable=False, default=0)
    display_name: Mapped[Optional[str]] = mapped_column(String(120), nullable=True)

    status: Mapped[TradingAccountStatus] = mapped_column(
        Enum(
            TradingAccountStatus,
            values_callable=lambda x: [e.value for e in x],
            name="tradingaccountstatusenum",
        ),
        nullable=False,
        default=TradingAccountStatus.pending_sync,
    )

    last_synced_at: Mapped[Optional[datetime]] = mapped_column(
        DateTime(timezone=True),
        nullable=True,
        default=None,
    )
    sync_error_message: Mapped[Optional[str]] = mapped_column(String, nullable=True)

    provisioning_status: Mapped[TradingAccountProvisioningStatus] = mapped_column(
        Enum(
            TradingAccountProvisioningStatus,
            values_callable=lambda x: [e.value for e in x],
            name="tradingaccountprovisioningstatusenum",
        ),
        nullable=False,
        default=TradingAccountProvisioningStatus.provisioned,
    )
    provisioning_error_message: Mapped[Optional[str]] = mapped_column(String, nullable=True)

    is_deleted: Mapped[bool] = mapped_column(Boolean, default=False, nullable=False)

    created_at: Mapped[datetime] = mapped_column(
        DateTime(timezone=True),
        default=lambda: datetime.now(timezone.utc),
        nullable=False,
    )

    user: Mapped["User"] = relationship(back_populates="trading_accounts")  # noqa: F821
    trades: Mapped[List["Trade"]] = relationship(  # noqa: F821
        back_populates="account",
        cascade="all, delete-orphan",
    )
    snapshots: Mapped[List["AccountSnapshot"]] = relationship(  # noqa: F821
        back_populates="account",
        cascade="all, delete-orphan",
    )
    daily_stats: Mapped[List["DailyStats"]] = relationship(  # noqa: F821
        back_populates="account",
        cascade="all, delete-orphan",
    )
    daily_journals: Mapped[List["DailyJournal"]] = relationship(  # noqa: F821
        back_populates="account",
        cascade="all, delete-orphan",
    )
