"""Partna Guard ORM models.

Storage discipline: we keep the latest snapshot (guard_states), closed-day results
(guard_daily_results, for consistency + min-days), and a SHORT rolling tick window
(guard_ticks, for the intraday chart) — never full tick history. Ticks are pruned
continuously.
"""

import enum
import uuid
from datetime import date, datetime, timezone
from decimal import Decimal
from typing import Any, Dict, List, Optional

from sqlalchemy import (
    Boolean,
    Date,
    DateTime,
    ForeignKey,
    Integer,
    Numeric,
    String,
    UniqueConstraint,
)
from sqlalchemy.dialects.postgresql import JSONB, UUID
from sqlalchemy.orm import Mapped, mapped_column, relationship

from app.core.database import Base


class GuardConnectionHealth(str, enum.Enum):
    ok = "ok"
    offline = "offline"


class GuardAccount(Base):
    """One row per connected TradingAccount that has Guard enabled.

    Holds the user-entered firm rules (rule_spec_json) and the optional personal
    stricter-only clamp. The watcher polls only while ``enabled`` is true.
    """

    __tablename__ = "guard_accounts"
    __table_args__ = (UniqueConstraint("trading_account_id"),)

    id: Mapped[uuid.UUID] = mapped_column(
        UUID(as_uuid=True), primary_key=True, default=uuid.uuid4,
    )
    trading_account_id: Mapped[uuid.UUID] = mapped_column(
        UUID(as_uuid=True),
        ForeignKey("trading_accounts.id", ondelete="CASCADE"),
        nullable=False,
        index=True,
    )
    user_id: Mapped[uuid.UUID] = mapped_column(
        UUID(as_uuid=True),
        ForeignKey("users.id", ondelete="CASCADE"),
        nullable=False,
        index=True,
    )
    enabled: Mapped[bool] = mapped_column(Boolean, nullable=False, default=True)

    # The challenge starting balance — the % base for all rules.
    size: Mapped[Decimal] = mapped_column(Numeric(18, 2), nullable=False)
    # The user-entered FirmRuleSpec (percentages stored as fractions).
    rule_spec_json: Mapped[Dict[str, Any]] = mapped_column(JSONB, nullable=False)
    # Optional personal stricter-only clamp: {"daily_frac": .., "dd_frac": ..}.
    personal_json: Mapped[Optional[Dict[str, Any]]] = mapped_column(JSONB, nullable=True)
    contract_text: Mapped[Optional[str]] = mapped_column(String, nullable=True)

    last_polled_at: Mapped[Optional[datetime]] = mapped_column(
        DateTime(timezone=True), nullable=True, default=None,
    )
    connection_health: Mapped[GuardConnectionHealth] = mapped_column(
        String(16), nullable=False, default=GuardConnectionHealth.ok.value,
    )
    poll_error_message: Mapped[Optional[str]] = mapped_column(String, nullable=True)
    consecutive_poll_failures: Mapped[int] = mapped_column(
        Integer, nullable=False, default=0,
    )
    # Last alert tier we emailed on — drives escalation-only de-dupe.
    last_alert_tier: Mapped[Optional[str]] = mapped_column(String(16), nullable=True)

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

    state: Mapped[Optional["GuardState"]] = relationship(
        back_populates="guard_account",
        cascade="all, delete-orphan",
        uselist=False,
    )
    daily_results: Mapped[List["GuardDailyResult"]] = relationship(
        back_populates="guard_account",
        cascade="all, delete-orphan",
    )
    ticks: Mapped[List["GuardTick"]] = relationship(
        back_populates="guard_account",
        cascade="all, delete-orphan",
    )
    alerts: Mapped[List["GuardAlert"]] = relationship(
        back_populates="guard_account",
        cascade="all, delete-orphan",
    )


class GuardState(Base):
    """The latest AccountState snapshot, upserted each poll (latest only)."""

    __tablename__ = "guard_states"
    __table_args__ = (UniqueConstraint("guard_account_id"),)

    id: Mapped[uuid.UUID] = mapped_column(
        UUID(as_uuid=True), primary_key=True, default=uuid.uuid4,
    )
    guard_account_id: Mapped[uuid.UUID] = mapped_column(
        UUID(as_uuid=True),
        ForeignKey("guard_accounts.id", ondelete="CASCADE"),
        nullable=False,
        index=True,
    )
    ts: Mapped[datetime] = mapped_column(DateTime(timezone=True), nullable=False)
    equity: Mapped[Decimal] = mapped_column(Numeric(18, 2), nullable=False)
    balance: Mapped[Decimal] = mapped_column(Numeric(18, 2), nullable=False)
    peak: Mapped[Decimal] = mapped_column(Numeric(18, 2), nullable=False)
    day_anchor: Mapped[Decimal] = mapped_column(Numeric(18, 2), nullable=False)
    status: Mapped[str] = mapped_column(String(16), nullable=False)
    buffers_json: Mapped[Dict[str, Any]] = mapped_column(JSONB, nullable=False)
    challenge_json: Mapped[Dict[str, Any]] = mapped_column(JSONB, nullable=False)
    # The serialized EngineMemory so the watcher can resume without replaying ticks.
    memory_json: Mapped[Dict[str, Any]] = mapped_column(JSONB, nullable=False)
    updated_at: Mapped[datetime] = mapped_column(
        DateTime(timezone=True),
        default=lambda: datetime.now(timezone.utc),
        onupdate=lambda: datetime.now(timezone.utc),
        nullable=False,
    )

    guard_account: Mapped["GuardAccount"] = relationship(back_populates="state")


class GuardDailyResult(Base):
    """Closed-day realized P&L — durable; feeds consistency + min-days."""

    __tablename__ = "guard_daily_results"
    __table_args__ = (UniqueConstraint("guard_account_id", "result_date"),)

    id: Mapped[uuid.UUID] = mapped_column(
        UUID(as_uuid=True), primary_key=True, default=uuid.uuid4,
    )
    guard_account_id: Mapped[uuid.UUID] = mapped_column(
        UUID(as_uuid=True),
        ForeignKey("guard_accounts.id", ondelete="CASCADE"),
        nullable=False,
        index=True,
    )
    result_date: Mapped[date] = mapped_column(Date, nullable=False)
    pnl: Mapped[Decimal] = mapped_column(Numeric(18, 2), nullable=False, default=Decimal(0))
    trade_count: Mapped[int] = mapped_column(Integer, nullable=False, default=0)
    is_trading_day: Mapped[bool] = mapped_column(Boolean, nullable=False, default=False)

    guard_account: Mapped["GuardAccount"] = relationship(back_populates="daily_results")


class GuardTick(Base):
    """A point in the short rolling equity window for the intraday chart.

    NOT full history — pruned to the last N points per account each poll.
    """

    __tablename__ = "guard_ticks"

    id: Mapped[uuid.UUID] = mapped_column(
        UUID(as_uuid=True), primary_key=True, default=uuid.uuid4,
    )
    guard_account_id: Mapped[uuid.UUID] = mapped_column(
        UUID(as_uuid=True),
        ForeignKey("guard_accounts.id", ondelete="CASCADE"),
        nullable=False,
        index=True,
    )
    ts: Mapped[datetime] = mapped_column(DateTime(timezone=True), nullable=False)
    equity: Mapped[Decimal] = mapped_column(Numeric(18, 2), nullable=False)

    guard_account: Mapped["GuardAccount"] = relationship(back_populates="ticks")


class GuardAlert(Base):
    """Sent-alert log — de-dupe + audit. One row per email fired."""

    __tablename__ = "guard_alerts"

    id: Mapped[uuid.UUID] = mapped_column(
        UUID(as_uuid=True), primary_key=True, default=uuid.uuid4,
    )
    guard_account_id: Mapped[uuid.UUID] = mapped_column(
        UUID(as_uuid=True),
        ForeignKey("guard_accounts.id", ondelete="CASCADE"),
        nullable=False,
        index=True,
    )
    ts: Mapped[datetime] = mapped_column(
        DateTime(timezone=True),
        default=lambda: datetime.now(timezone.utc),
        nullable=False,
    )
    tier: Mapped[str] = mapped_column(String(16), nullable=False)
    kind: Mapped[str] = mapped_column(String(32), nullable=False)
    channel: Mapped[str] = mapped_column(String(16), nullable=False, default="email")
    sent_ok: Mapped[bool] = mapped_column(Boolean, nullable=False, default=False)

    guard_account: Mapped["GuardAccount"] = relationship(back_populates="alerts")
