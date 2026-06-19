import enum
import uuid
from datetime import date, datetime, timezone
from decimal import Decimal
from typing import List, Optional

from sqlalchemy import (
    BigInteger,
    Boolean,
    Date,
    DateTime,
    Enum,
    ForeignKey,
    Integer,
    Numeric,
    String,
    UniqueConstraint,
)
from sqlalchemy.dialects.postgresql import ARRAY, UUID
from sqlalchemy.orm import Mapped, mapped_column, relationship

from app.core.database import Base


class TradingAccountStatus(str, enum.Enum):
    pending_sync = "pending_sync"
    synced = "synced"
    error = "error"


class ImportMethod(str, enum.Enum):
    auto_sync = "auto_sync"  # broker-connected accounts synced automatically
    csv_upload = "csv_upload"  # trades imported from a CSV file
    manual = "manual"  # trades entered by hand


class TradingAccountProvisioningStatus(str, enum.Enum):
    pending = "pending"
    provisioned = "provisioned"
    failed = "failed"


class TradingAccountConnectionState(str, enum.Enum):
    pending_verification = "pending_verification"
    verification_failed = "verification_failed"
    bootstrapping = "bootstrapping"
    ready = "ready"
    bootstrap_failed = "bootstrap_failed"


class TradingAccountType(str, enum.Enum):
    demo = "demo"
    live = "live"


class TradingPlatform(str, enum.Enum):
    mt4 = "MT4"
    mt5 = "MT5"
    matchtrader = "MatchTrader"
    ctrader = "cTrader"


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

    # How this account's trades get into the app. Broker-connected accounts use auto_sync.
    import_method: Mapped[ImportMethod] = mapped_column(
        Enum(
            ImportMethod,
            values_callable=lambda x: [e.value for e in x],
            name="importmethodenum",
        ),
        nullable=False,
        default=ImportMethod.auto_sync,
    )

    # Magic numbers that belong to copy-trading subscriptions on this account.
    # Used by the headless MT5 service for trade-source classification.
    copy_magic_numbers: Mapped[Optional[List[int]]] = mapped_column(
        ARRAY(Integer), nullable=True, default=None
    )

    connection_state: Mapped[TradingAccountConnectionState] = mapped_column(
        Enum(
            TradingAccountConnectionState,
            values_callable=lambda x: [e.value for e in x],
            name="tradingaccountconnectionstateenum",
        ),
        nullable=False,
        default=TradingAccountConnectionState.pending_verification,
    )
    is_data_ready_for_stats: Mapped[bool] = mapped_column(
        Boolean,
        nullable=False,
        default=False,
    )
    last_bootstrap_synced_at: Mapped[Optional[datetime]] = mapped_column(
        DateTime(timezone=True),
        nullable=True,
        default=None,
    )
    bootstrap_error_message: Mapped[Optional[str]] = mapped_column(String, nullable=True)
    last_sync_attempted_at: Mapped[Optional[datetime]] = mapped_column(
        DateTime(timezone=True),
        nullable=True,
        default=None,
    )
    next_sync_not_before: Mapped[Optional[datetime]] = mapped_column(
        DateTime(timezone=True),
        nullable=True,
        default=None,
    )
    last_sync_outcome: Mapped[Optional[str]] = mapped_column(
        String(64),
        nullable=True,
    )
    consecutive_sync_failures: Mapped[int] = mapped_column(
        Integer,
        nullable=False,
        default=0,
    )

    # Archived = kept but no longer syncing; the FE shows it muted. There is no
    # soft-delete for accounts — permanent delete physically removes the row.
    is_archived: Mapped[bool] = mapped_column(Boolean, default=False, nullable=False)

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

    user: Mapped["User"] = relationship(back_populates="trading_accounts")  # noqa: F821
    trades: Mapped[List["Trade"]] = relationship(
        back_populates="account",
        cascade="all, delete-orphan",
    )
    snapshots: Mapped[List["AccountSnapshot"]] = relationship(
        back_populates="account",
        cascade="all, delete-orphan",
    )
    daily_journals: Mapped[List["DailyJournal"]] = relationship(  # noqa: F821
        back_populates="account",
        cascade="all, delete-orphan",
    )


class AccountSnapshot(Base):
    __tablename__ = "account_snapshots"
    __table_args__ = (UniqueConstraint("account_id", "snapshot_date"),)

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

    balance: Mapped[Decimal] = mapped_column(Numeric(18, 2), nullable=False)
    equity: Mapped[Decimal] = mapped_column(Numeric(18, 2), nullable=False)
    floating_pnl: Mapped[Decimal] = mapped_column(Numeric(14, 2), nullable=False)
    snapshot_date: Mapped[date] = mapped_column(Date, nullable=False, index=True)

    created_at: Mapped[datetime] = mapped_column(
        DateTime(timezone=True),
        default=lambda: datetime.now(timezone.utc),
        nullable=False,
    )

    account: Mapped["TradingAccount"] = relationship(back_populates="snapshots")


class TradeDirection(str, enum.Enum):
    buy = "buy"
    sell = "sell"


class TradeSource(str, enum.Enum):
    personal = "personal"
    copied = "copied"


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

    # --- MT5-enriched fields (nullable — trades without MT5 metadata will not have these) ---

    # Risk management levels at trade entry.
    sl: Mapped[Optional[Decimal]] = mapped_column(Numeric(18, 5), nullable=True)
    tp: Mapped[Optional[Decimal]] = mapped_column(Numeric(18, 5), nullable=True)

    # MT5 magic number (0 = manual, >0 = EA).
    magic_number: Mapped[Optional[int]] = mapped_column(BigInteger, nullable=True)

    # Broker position ID for grouping partial closes into one logical trade.
    position_id: Mapped[Optional[str]] = mapped_column(String(64), nullable=True, index=True)

    # Trade origin: 'personal' (manual / own EA) or 'copied' (copy trading).
    trade_source: Mapped[Optional[TradeSource]] = mapped_column(
        Enum(
            TradeSource,
            values_callable=lambda x: [e.value for e in x],
            name="tradesourceenum",
        ),
        nullable=True,
    )

    # Maximum Favorable Excursion — highest price reached during the trade.
    mfe: Mapped[Optional[Decimal]] = mapped_column(Numeric(18, 5), nullable=True)

    # Maximum Adverse Excursion — lowest price reached during the trade.
    mae: Mapped[Optional[Decimal]] = mapped_column(Numeric(18, 5), nullable=True)

    # Playbook setup name (e.g. "London breakout"). Populated for demo/seeded
    # trades; nullable for synced trades.
    setup: Mapped[Optional[str]] = mapped_column(String(64), nullable=True)

    created_at: Mapped[datetime] = mapped_column(
        DateTime(timezone=True),
        default=lambda: datetime.now(timezone.utc),
        nullable=False,
    )

    account: Mapped["TradingAccount"] = relationship(back_populates="trades")
    journal: Mapped[Optional["TradeJournal"]] = relationship(  # noqa: F821
        back_populates="trade",
        uselist=False,
        cascade="all, delete-orphan",
    )


