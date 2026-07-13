import enum
import uuid
from datetime import date, datetime, timezone
from decimal import Decimal
from typing import Optional

from sqlalchemy import (
    BigInteger,
    Boolean,
    CheckConstraint,
    Date,
    DateTime,
    Enum,
    Float,
    ForeignKey,
    Index,
    Numeric,
    String,
    Text,
    UniqueConstraint,
    text,
)
from sqlalchemy.dialects.postgresql import JSONB, UUID
from sqlalchemy.orm import Mapped, mapped_column

from app.core.database import Base


def utcnow() -> datetime:
    return datetime.now(timezone.utc)


class TelegramConnectionState(str, enum.Enum):
    pending = "pending"
    ready = "ready"
    reauthentication_required = "reauthentication_required"
    disconnected = "disconnected"


class TelegramSourceType(str, enum.Enum):
    channel = "channel"
    group = "group"


class TelegramSourceState(str, enum.Enum):
    draft = "draft"
    learning = "learning"
    ready = "ready"
    active = "active"
    paused = "paused"
    unsupported = "unsupported"
    advisory = "advisory"
    failed_retryable = "failed_retryable"
    unsupported_image_primary = "unsupported_image_primary"


class CopyRouteState(str, enum.Enum):
    draft = "draft"
    ready = "ready"
    active = "active"
    paused = "paused"
    reauthentication_required = "reauthentication_required"
    unsupported = "unsupported"
    target_unavailable = "target_unavailable"
    needs_attention = "needs_attention"


class CopyTradingConnectionState(str, enum.Enum):
    submitted = "submitted"
    provisioning = "provisioning"
    deploying = "deploying"
    connecting = "connecting"
    synchronizing = "synchronizing"
    ready = "ready"
    invalid_credentials = "invalid_credentials"
    server_not_found = "server_not_found"
    provisioning_failed = "provisioning_failed"
    broker_disconnected = "broker_disconnected"
    synchronization_failed = "synchronization_failed"
    trading_disabled = "trading_disabled"
    deleting = "deleting"
    deleted = "deleted"


class TakeProfitMode(str, enum.Enum):
    all = "all"
    lowest = "lowest"
    highest = "highest"


class LotDistribution(str, enum.Enum):
    split_total = "split_total"
    fixed_each = "fixed_each"


class MinimumFields(str, enum.Enum):
    direction_symbol = "direction_symbol"
    direction_symbol_entry = "direction_symbol_entry"
    direction_symbol_sl = "direction_symbol_sl"
    direction_symbol_tp = "direction_symbol_tp"
    direction_symbol_sl_tp = "direction_symbol_sl_tp"


class CopyActivityLevel(str, enum.Enum):
    info = "info"
    success = "success"
    warning = "warning"
    error = "error"


class AutomationConfidence(str, enum.Enum):
    low = "low"
    medium = "medium"
    high = "high"


class SignalThreadState(str, enum.Enum):
    assembling = "assembling"
    complete = "complete"
    validated = "validated"
    executing = "executing"
    succeeded = "succeeded"
    expired = "expired"
    skipped = "skipped"
    rejected = "rejected"
    failed = "failed"


class TradeIntentState(str, enum.Enum):
    created = "created"
    submitted = "submitted"
    confirmed = "confirmed"
    uncertain = "uncertain"
    reconciling = "reconciling"
    retryable = "retryable"
    failed = "failed"


class SignalConversationState(str, enum.Enum):
    active = "active"
    completed = "completed"
    expired = "expired"
    ambiguous = "ambiguous"


class RouteAssemblyState(str, enum.Enum):
    assembling = "assembling"
    ready = "ready"
    executing = "executing"
    completed = "completed"
    expired = "expired"
    skipped = "skipped"
    failed = "failed"


class TelegramAuthState(str, enum.Enum):
    pending = "pending"
    awaiting_code = "awaiting_code"
    awaiting_password = "awaiting_password"
    ready = "ready"
    failed = "failed"
    expired = "expired"


class DeadLetterState(str, enum.Enum):
    pending = "pending"
    replayed = "replayed"
    resolved = "resolved"


class WorkerHealthState(str, enum.Enum):
    healthy = "healthy"
    degraded = "degraded"
    failed = "failed"


def enum_values(enum_type: type[enum.Enum]) -> list[str]:
    return [item.value for item in enum_type]


class CopyTradingUserSettings(Base):
    __tablename__ = "copy_trading_user_settings"

    user_id: Mapped[uuid.UUID] = mapped_column(
        UUID(as_uuid=True), ForeignKey("users.id", ondelete="CASCADE"), primary_key=True
    )
    is_paused: Mapped[bool] = mapped_column(Boolean, nullable=False, default=False)


class CopyTradingConnection(Base):
    __tablename__ = "copy_trading_connections"
    __table_args__ = (
        UniqueConstraint("metaapi_account_id"),
        UniqueConstraint("provisioning_transaction_id"),
        UniqueConstraint("user_id", "broker_login", "broker_server"),
    )

    id: Mapped[uuid.UUID] = mapped_column(UUID(as_uuid=True), primary_key=True, default=uuid.uuid4)
    user_id: Mapped[uuid.UUID] = mapped_column(
        UUID(as_uuid=True), ForeignKey("users.id", ondelete="CASCADE"), nullable=False, index=True
    )
    display_name: Mapped[str] = mapped_column(String(120), nullable=False)
    broker_login: Mapped[str] = mapped_column(String(64), nullable=False)
    broker_server: Mapped[str] = mapped_column(String(160), nullable=False)
    platform: Mapped[str] = mapped_column(String(8), nullable=False, default="mt5")
    encrypted_trader_password: Mapped[Optional[str]] = mapped_column(Text, nullable=True)
    metaapi_account_id: Mapped[Optional[str]] = mapped_column(String(100), nullable=True)
    provisioning_transaction_id: Mapped[str] = mapped_column(String(100), nullable=False)
    state: Mapped[CopyTradingConnectionState] = mapped_column(
        Enum(
            CopyTradingConnectionState,
            values_callable=enum_values,
            name="copytradingconnectionstateenum",
        ),
        nullable=False,
        default=CopyTradingConnectionState.submitted,
    )
    last_error_code: Mapped[Optional[str]] = mapped_column(String(100), nullable=True)
    last_error_message: Mapped[Optional[str]] = mapped_column(Text, nullable=True)
    symbol_catalog_fingerprint: Mapped[Optional[str]] = mapped_column(String(64), nullable=True)
    symbol_catalog_refreshed_at: Mapped[Optional[datetime]] = mapped_column(
        DateTime(timezone=True), nullable=True
    )
    last_health_at: Mapped[Optional[datetime]] = mapped_column(DateTime(timezone=True), nullable=True)
    created_at: Mapped[datetime] = mapped_column(DateTime(timezone=True), default=utcnow, nullable=False)
    updated_at: Mapped[datetime] = mapped_column(
        DateTime(timezone=True), default=utcnow, onupdate=utcnow, nullable=False
    )
    is_paused: Mapped[bool] = mapped_column(Boolean, nullable=False, default=False)


class TelegramConnection(Base):
    __tablename__ = "telegram_connections"
    __table_args__ = (UniqueConstraint("user_id", "telegram_user_id"),)

    id: Mapped[uuid.UUID] = mapped_column(UUID(as_uuid=True), primary_key=True, default=uuid.uuid4)
    user_id: Mapped[uuid.UUID] = mapped_column(
        UUID(as_uuid=True), ForeignKey("users.id", ondelete="CASCADE"), nullable=False, index=True
    )
    telegram_user_id: Mapped[Optional[int]] = mapped_column(BigInteger, nullable=True)
    phone_hint: Mapped[Optional[str]] = mapped_column(String(32), nullable=True)
    encrypted_session: Mapped[Optional[str]] = mapped_column(Text, nullable=True)
    state: Mapped[TelegramConnectionState] = mapped_column(
        Enum(
            TelegramConnectionState,
            values_callable=enum_values,
            name="telegramconnectionstateenum",
        ),
        nullable=False,
        default=TelegramConnectionState.pending,
    )
    last_heartbeat_at: Mapped[Optional[datetime]] = mapped_column(DateTime(timezone=True), nullable=True)
    display_name: Mapped[Optional[str]] = mapped_column(String(255), nullable=True)
    username: Mapped[Optional[str]] = mapped_column(String(255), nullable=True)
    is_paused: Mapped[bool] = mapped_column(Boolean, nullable=False, default=False)
    reauthentication_reason: Mapped[Optional[str]] = mapped_column(Text, nullable=True)
    created_at: Mapped[datetime] = mapped_column(DateTime(timezone=True), default=utcnow, nullable=False)
    updated_at: Mapped[datetime] = mapped_column(
        DateTime(timezone=True), default=utcnow, onupdate=utcnow, nullable=False
    )


class TelegramSource(Base):
    __tablename__ = "telegram_sources"
    __table_args__ = (UniqueConstraint("connection_id", "telegram_chat_id"),)

    id: Mapped[uuid.UUID] = mapped_column(UUID(as_uuid=True), primary_key=True, default=uuid.uuid4)
    user_id: Mapped[uuid.UUID] = mapped_column(
        UUID(as_uuid=True), ForeignKey("users.id", ondelete="CASCADE"), nullable=False, index=True
    )
    connection_id: Mapped[uuid.UUID] = mapped_column(
        UUID(as_uuid=True),
        ForeignKey("telegram_connections.id", ondelete="CASCADE"),
        nullable=False,
        index=True,
    )
    telegram_chat_id: Mapped[int] = mapped_column(BigInteger, nullable=False)
    title: Mapped[str] = mapped_column(String(255), nullable=False)
    username: Mapped[Optional[str]] = mapped_column(String(255), nullable=True)
    source_type: Mapped[TelegramSourceType] = mapped_column(
        Enum(TelegramSourceType, values_callable=enum_values, name="telegramsourcetypeenum"),
        nullable=False,
    )
    state: Mapped[TelegramSourceState] = mapped_column(
        Enum(TelegramSourceState, values_callable=enum_values, name="telegramsourcestateenum"),
        nullable=False,
        default=TelegramSourceState.draft,
    )
    unsupported_reason: Mapped[Optional[str]] = mapped_column(Text, nullable=True)
    profile_id: Mapped[Optional[uuid.UUID]] = mapped_column(
        UUID(as_uuid=True), ForeignKey("channel_profiles.id", ondelete="SET NULL"), nullable=True
    )
    is_paused: Mapped[bool] = mapped_column(Boolean, nullable=False, default=False)
    created_at: Mapped[datetime] = mapped_column(DateTime(timezone=True), default=utcnow, nullable=False)
    updated_at: Mapped[datetime] = mapped_column(
        DateTime(timezone=True), default=utcnow, onupdate=utcnow, nullable=False
    )


class CopyAccountPolicy(Base):
    __tablename__ = "copy_account_policies"
    __table_args__ = (
        UniqueConstraint("user_id", "connection_id"),
        CheckConstraint("max_lot > 0", name="ck_copy_account_policy_max_lot_positive"),
        CheckConstraint(
            "max_lot_per_trade > 0",
            name="ck_copy_account_policy_max_lot_per_trade_positive",
        ),
        CheckConstraint(
            "max_open_positions > 0",
            name="ck_copy_account_policy_max_open_positions_positive",
        ),
        CheckConstraint(
            "daily_loss_limit IS NULL OR daily_loss_limit > 0",
            name="ck_copy_account_policy_daily_loss_positive",
        ),
        CheckConstraint(
            "max_drawdown_percent IS NULL OR "
            "(max_drawdown_percent > 0 AND max_drawdown_percent <= 100)",
            name="ck_copy_account_policy_drawdown_range",
        ),
        CheckConstraint(
            "market_signal_max_age_seconds >= 1 AND "
            "market_signal_max_age_seconds <= 3600",
            name="ck_copy_account_policy_signal_age_range",
        ),
    )

    id: Mapped[uuid.UUID] = mapped_column(UUID(as_uuid=True), primary_key=True, default=uuid.uuid4)
    user_id: Mapped[uuid.UUID] = mapped_column(
        UUID(as_uuid=True), ForeignKey("users.id", ondelete="CASCADE"), nullable=False, index=True
    )
    connection_id: Mapped[Optional[uuid.UUID]] = mapped_column(
        UUID(as_uuid=True),
        ForeignKey("copy_trading_connections.id", ondelete="CASCADE"),
        nullable=True,
        index=True,
    )
    legacy_account_id: Mapped[Optional[uuid.UUID]] = mapped_column(
        UUID(as_uuid=True), ForeignKey("trading_accounts.id", ondelete="CASCADE"), nullable=True
    )
    max_lot: Mapped[Decimal] = mapped_column(
        Numeric(12, 4), nullable=False, default=Decimal("100.0000")
    )
    max_lot_per_trade: Mapped[Decimal] = mapped_column(
        Numeric(12, 4), nullable=False, default=Decimal("100.0000")
    )
    max_open_positions: Mapped[int] = mapped_column(nullable=False, default=100)
    daily_loss_limit: Mapped[Optional[Decimal]] = mapped_column(Numeric(20, 2), nullable=True)
    max_drawdown_percent: Mapped[Optional[Decimal]] = mapped_column(
        Numeric(6, 2), nullable=True
    )
    allowed_symbols: Mapped[list] = mapped_column(JSONB, nullable=False, default=list)
    blocked_symbols: Mapped[list] = mapped_column(JSONB, nullable=False, default=list)
    market_signal_max_age_seconds: Mapped[int] = mapped_column(nullable=False, default=30)
    daily_equity_anchor: Mapped[Optional[Decimal]] = mapped_column(Numeric(20, 2), nullable=True)
    daily_equity_anchor_date: Mapped[Optional[date]] = mapped_column(Date, nullable=True)
    peak_equity: Mapped[Optional[Decimal]] = mapped_column(Numeric(20, 2), nullable=True)
    is_paused: Mapped[bool] = mapped_column(Boolean, nullable=False, default=False)
    created_at: Mapped[datetime] = mapped_column(DateTime(timezone=True), default=utcnow, nullable=False)
    updated_at: Mapped[datetime] = mapped_column(
        DateTime(timezone=True), default=utcnow, onupdate=utcnow, nullable=False
    )


class CopyRoute(Base):
    __tablename__ = "copy_routes"
    __table_args__ = (
        UniqueConstraint("user_id", "source_id", "target_connection_id"),
        UniqueConstraint("magic_number"),
        CheckConstraint("fixed_lot > 0", name="ck_copy_route_fixed_lot_positive"),
        CheckConstraint(
            "assembly_window_seconds IS NULL OR "
            "(assembly_window_seconds >= 1 AND assembly_window_seconds <= 600)",
            name="ck_copy_route_assembly_window",
        ),
    )

    id: Mapped[uuid.UUID] = mapped_column(UUID(as_uuid=True), primary_key=True, default=uuid.uuid4)
    user_id: Mapped[uuid.UUID] = mapped_column(
        UUID(as_uuid=True), ForeignKey("users.id", ondelete="CASCADE"), nullable=False, index=True
    )
    source_id: Mapped[uuid.UUID] = mapped_column(
        UUID(as_uuid=True), ForeignKey("telegram_sources.id", ondelete="CASCADE"), nullable=False, index=True
    )
    target_connection_id: Mapped[Optional[uuid.UUID]] = mapped_column(
        UUID(as_uuid=True),
        ForeignKey("copy_trading_connections.id", ondelete="SET NULL"),
        nullable=True,
        index=True,
    )
    legacy_target_account_id: Mapped[Optional[uuid.UUID]] = mapped_column(
        UUID(as_uuid=True), ForeignKey("trading_accounts.id", ondelete="SET NULL"), nullable=True
    )
    magic_number: Mapped[int] = mapped_column(BigInteger, nullable=False)
    state: Mapped[CopyRouteState] = mapped_column(
        Enum(CopyRouteState, values_callable=enum_values, name="copyroutestateenum"),
        nullable=False,
        default=CopyRouteState.draft,
    )
    paused_from_state: Mapped[Optional[CopyRouteState]] = mapped_column(
        Enum(CopyRouteState, values_callable=enum_values, name="copyroutestateenum"), nullable=True
    )
    fixed_lot: Mapped[Decimal] = mapped_column(Numeric(12, 4), nullable=False)
    take_profit_mode: Mapped[TakeProfitMode] = mapped_column(
        Enum(TakeProfitMode, values_callable=enum_values, name="takeprofitmodeenum"),
        nullable=False,
        default=TakeProfitMode.all,
    )
    lot_distribution: Mapped[LotDistribution] = mapped_column(
        Enum(LotDistribution, values_callable=enum_values, name="lotdistributionenum"),
        nullable=False,
        default=LotDistribution.fixed_each,
    )
    pending_orders_enabled: Mapped[bool] = mapped_column(Boolean, nullable=False, default=True)
    minimum_fields: Mapped[MinimumFields] = mapped_column(
        Enum(MinimumFields, values_callable=enum_values, name="minimumfieldsenum"),
        nullable=False,
        default=MinimumFields.direction_symbol_sl_tp,
    )
    assembly_window_seconds: Mapped[Optional[int]] = mapped_column(nullable=True)
    process_all_group_authors: Mapped[bool] = mapped_column(Boolean, nullable=False, default=False)
    notify_success: Mapped[bool] = mapped_column(Boolean, nullable=False, default=True)
    notify_failure: Mapped[bool] = mapped_column(Boolean, nullable=False, default=True)
    allow_sl_tp_updates: Mapped[bool] = mapped_column(Boolean, nullable=False, default=True)
    allow_break_even: Mapped[bool] = mapped_column(Boolean, nullable=False, default=True)
    allow_additional_tp: Mapped[bool] = mapped_column(Boolean, nullable=False, default=True)
    allow_partial_close: Mapped[bool] = mapped_column(Boolean, nullable=False, default=True)
    allow_full_close: Mapped[bool] = mapped_column(Boolean, nullable=False, default=True)
    allow_pending_cancel: Mapped[bool] = mapped_column(Boolean, nullable=False, default=True)
    unsafe_minimum_confirmed_at: Mapped[Optional[datetime]] = mapped_column(
        DateTime(timezone=True), nullable=True
    )
    created_at: Mapped[datetime] = mapped_column(DateTime(timezone=True), default=utcnow, nullable=False)
    updated_at: Mapped[datetime] = mapped_column(
        DateTime(timezone=True), default=utcnow, onupdate=utcnow, nullable=False
    )


class CopyActivityEvent(Base):
    __tablename__ = "copy_activity_events"
    __table_args__ = (
        Index("ix_copy_activity_user_created", "user_id", "created_at"),
        Index("ix_copy_activity_route_created", "route_id", "created_at"),
        Index("ix_copy_activity_correlation", "correlation_id"),
    )

    id: Mapped[uuid.UUID] = mapped_column(UUID(as_uuid=True), primary_key=True, default=uuid.uuid4)
    user_id: Mapped[uuid.UUID] = mapped_column(
        UUID(as_uuid=True), ForeignKey("users.id", ondelete="CASCADE"), nullable=False, index=True
    )
    route_id: Mapped[Optional[uuid.UUID]] = mapped_column(
        UUID(as_uuid=True), ForeignKey("copy_routes.id", ondelete="SET NULL"), nullable=True
    )
    source_id: Mapped[Optional[uuid.UUID]] = mapped_column(
        UUID(as_uuid=True), ForeignKey("telegram_sources.id", ondelete="SET NULL"), nullable=True
    )
    connection_id: Mapped[Optional[uuid.UUID]] = mapped_column(
        UUID(as_uuid=True),
        ForeignKey("copy_trading_connections.id", ondelete="SET NULL"),
        nullable=True,
    )
    legacy_account_id: Mapped[Optional[uuid.UUID]] = mapped_column(
        UUID(as_uuid=True), ForeignKey("trading_accounts.id", ondelete="SET NULL"), nullable=True
    )
    correlation_id: Mapped[str] = mapped_column(String(64), nullable=False)
    action: Mapped[str] = mapped_column(String(100), nullable=False)
    level: Mapped[CopyActivityLevel] = mapped_column(
        Enum(CopyActivityLevel, values_callable=enum_values, name="copyactivitylevelenum"),
        nullable=False,
        default=CopyActivityLevel.info,
    )
    title: Mapped[str] = mapped_column(String(200), nullable=False)
    body: Mapped[Optional[str]] = mapped_column(Text, nullable=True)
    parsed_details: Mapped[dict] = mapped_column(JSONB, nullable=False, default=dict)
    broker_details: Mapped[dict] = mapped_column(JSONB, nullable=False, default=dict)
    encrypted_raw_message: Mapped[Optional[str]] = mapped_column(Text, nullable=True)
    created_at: Mapped[datetime] = mapped_column(DateTime(timezone=True), default=utcnow, nullable=False)


class ChannelProfile(Base):
    __tablename__ = "channel_profiles"
    __table_args__ = (UniqueConstraint("telegram_chat_id", "parser_version"),)

    id: Mapped[uuid.UUID] = mapped_column(UUID(as_uuid=True), primary_key=True, default=uuid.uuid4)
    telegram_chat_id: Mapped[int] = mapped_column(BigInteger, nullable=False, index=True)
    parser_version: Mapped[str] = mapped_column(String(64), nullable=False)
    signal_style: Mapped[str] = mapped_column(String(100), nullable=False)
    recommended_assembly_window_seconds: Mapped[int] = mapped_column(nullable=False, default=90)
    confidence: Mapped[AutomationConfidence] = mapped_column(
        Enum(AutomationConfidence, values_callable=enum_values, name="automationconfidenceenum"),
        nullable=False,
    )
    confidence_score: Mapped[float] = mapped_column(Float, nullable=False)
    image_frequency: Mapped[float] = mapped_column(Float, nullable=False, default=0)
    image_primary: Mapped[bool] = mapped_column(Boolean, nullable=False, default=False)
    supported_actions: Mapped[list] = mapped_column(JSONB, nullable=False, default=list)
    author_pattern: Mapped[dict] = mapped_column(JSONB, nullable=False, default=dict)
    analyzed_from: Mapped[datetime] = mapped_column(DateTime(timezone=True), nullable=False)
    analyzed_to: Mapped[datetime] = mapped_column(DateTime(timezone=True), nullable=False)
    sample_count: Mapped[int] = mapped_column(nullable=False, default=0)
    validated_at: Mapped[datetime] = mapped_column(DateTime(timezone=True), nullable=False)
    created_at: Mapped[datetime] = mapped_column(DateTime(timezone=True), default=utcnow, nullable=False)
    updated_at: Mapped[datetime] = mapped_column(DateTime(timezone=True), default=utcnow, onupdate=utcnow, nullable=False)


class ChannelMessageSample(Base):
    __tablename__ = "channel_message_samples"
    __table_args__ = (UniqueConstraint("telegram_chat_id", "telegram_message_id", "purpose"),)

    id: Mapped[uuid.UUID] = mapped_column(UUID(as_uuid=True), primary_key=True, default=uuid.uuid4)
    profile_id: Mapped[Optional[uuid.UUID]] = mapped_column(UUID(as_uuid=True), ForeignKey("channel_profiles.id", ondelete="CASCADE"), nullable=True, index=True)
    telegram_chat_id: Mapped[int] = mapped_column(BigInteger, nullable=False, index=True)
    telegram_message_id: Mapped[int] = mapped_column(BigInteger, nullable=False)
    encrypted_raw_message: Mapped[str] = mapped_column(Text, nullable=False)
    message_metadata: Mapped[dict] = mapped_column(JSONB, nullable=False, default=dict)
    purpose: Mapped[str] = mapped_column(String(32), nullable=False, default="learning")
    expires_at: Mapped[datetime] = mapped_column(DateTime(timezone=True), nullable=False, index=True)
    created_at: Mapped[datetime] = mapped_column(DateTime(timezone=True), default=utcnow, nullable=False)


class SignalThread(Base):
    __tablename__ = "signal_threads"
    __table_args__ = (Index("ix_signal_thread_source_state", "source_id", "state"),)

    id: Mapped[uuid.UUID] = mapped_column(UUID(as_uuid=True), primary_key=True, default=uuid.uuid4)
    source_id: Mapped[uuid.UUID] = mapped_column(UUID(as_uuid=True), ForeignKey("telegram_sources.id", ondelete="CASCADE"), nullable=False, index=True)
    correlation_id: Mapped[str] = mapped_column(String(64), nullable=False, unique=True)
    state: Mapped[SignalThreadState] = mapped_column(Enum(SignalThreadState, values_callable=enum_values, name="signalthreadstateenum"), nullable=False, default=SignalThreadState.assembling)
    explicit_reference: Mapped[Optional[str]] = mapped_column(String(255), nullable=True)
    symbol: Mapped[Optional[str]] = mapped_column(String(64), nullable=True)
    direction: Mapped[Optional[str]] = mapped_column(String(16), nullable=True)
    context: Mapped[dict] = mapped_column(JSONB, nullable=False, default=dict)
    message_references: Mapped[list] = mapped_column(JSONB, nullable=False, default=list)
    assembly_deadline: Mapped[datetime] = mapped_column(DateTime(timezone=True), nullable=False)
    created_at: Mapped[datetime] = mapped_column(DateTime(timezone=True), default=utcnow, nullable=False)
    updated_at: Mapped[datetime] = mapped_column(DateTime(timezone=True), default=utcnow, onupdate=utcnow, nullable=False)


class SignalConversation(Base):
    __tablename__ = "signal_conversations"
    __table_args__ = (
        Index("ix_signal_conversation_source_state", "source_id", "state"),
        Index("ix_signal_conversation_reply_root", "source_id", "reply_root_message_id"),
    )

    id: Mapped[uuid.UUID] = mapped_column(UUID(as_uuid=True), primary_key=True, default=uuid.uuid4)
    source_id: Mapped[uuid.UUID] = mapped_column(UUID(as_uuid=True), ForeignKey("telegram_sources.id", ondelete="CASCADE"), nullable=False, index=True)
    legacy_thread_id: Mapped[uuid.UUID] = mapped_column(UUID(as_uuid=True), ForeignKey("signal_threads.id", ondelete="CASCADE"), nullable=False, unique=True)
    correlation_id: Mapped[str] = mapped_column(String(64), nullable=False, unique=True)
    state: Mapped[SignalConversationState] = mapped_column(Enum(SignalConversationState, values_callable=enum_values, name="signalconversationstateenum"), nullable=False, default=SignalConversationState.active)
    reply_root_message_id: Mapped[Optional[int]] = mapped_column(BigInteger, nullable=True)
    explicit_reference: Mapped[Optional[str]] = mapped_column(String(255), nullable=True)
    symbol: Mapped[Optional[str]] = mapped_column(String(64), nullable=True)
    direction: Mapped[Optional[str]] = mapped_column(String(16), nullable=True)
    context: Mapped[dict] = mapped_column(JSONB, nullable=False, default=dict)
    last_message_id: Mapped[int] = mapped_column(BigInteger, nullable=False)
    last_message_revision: Mapped[int] = mapped_column(nullable=False, default=1)
    created_at: Mapped[datetime] = mapped_column(DateTime(timezone=True), default=utcnow, nullable=False)
    updated_at: Mapped[datetime] = mapped_column(DateTime(timezone=True), default=utcnow, onupdate=utcnow, nullable=False)


class RouteSignalAssembly(Base):
    __tablename__ = "route_signal_assemblies"
    __table_args__ = (
        CheckConstraint(
            "generation > 0",
            name="ck_route_signal_assembly_generation_positive",
        ),
        Index("ix_route_signal_assembly_route_state", "route_id", "state"),
        Index(
            "uq_route_signal_active_conversation",
            "route_id",
            "conversation_id",
            unique=True,
            postgresql_where=text("state IN ('assembling','ready','executing')"),
        ),
    )

    id: Mapped[uuid.UUID] = mapped_column(UUID(as_uuid=True), primary_key=True, default=uuid.uuid4)
    conversation_id: Mapped[uuid.UUID] = mapped_column(UUID(as_uuid=True), ForeignKey("signal_conversations.id", ondelete="CASCADE"), nullable=False, index=True)
    route_id: Mapped[uuid.UUID] = mapped_column(UUID(as_uuid=True), ForeignKey("copy_routes.id", ondelete="CASCADE"), nullable=False, index=True)
    state: Mapped[RouteAssemblyState] = mapped_column(Enum(RouteAssemblyState, values_callable=enum_values, name="routeassemblystateenum"), nullable=False, default=RouteAssemblyState.assembling)
    context: Mapped[dict] = mapped_column(JSONB, nullable=False, default=dict)
    message_references: Mapped[list] = mapped_column(JSONB, nullable=False, default=list)
    generation: Mapped[int] = mapped_column(nullable=False, default=1)
    opening_action: Mapped[Optional[str]] = mapped_column(String(32), nullable=True)
    opening_intent_id: Mapped[Optional[uuid.UUID]] = mapped_column(
        UUID(as_uuid=True),
        ForeignKey("trade_intents.id", ondelete="SET NULL"),
        nullable=True,
    )
    terminal_reason: Mapped[Optional[str]] = mapped_column(Text, nullable=True)
    assembly_deadline: Mapped[datetime] = mapped_column(DateTime(timezone=True), nullable=False)
    accepted_at: Mapped[Optional[datetime]] = mapped_column(DateTime(timezone=True), nullable=True)
    completed_at: Mapped[Optional[datetime]] = mapped_column(
        DateTime(timezone=True),
        nullable=True,
    )
    created_at: Mapped[datetime] = mapped_column(DateTime(timezone=True), default=utcnow, nullable=False)
    updated_at: Mapped[datetime] = mapped_column(DateTime(timezone=True), default=utcnow, onupdate=utcnow, nullable=False)


class ParsedAction(Base):
    __tablename__ = "parsed_actions"
    __table_args__ = (UniqueConstraint("thread_id", "telegram_message_id", "route_id", "action_type", "revision"),)

    id: Mapped[uuid.UUID] = mapped_column(UUID(as_uuid=True), primary_key=True, default=uuid.uuid4)
    thread_id: Mapped[uuid.UUID] = mapped_column(UUID(as_uuid=True), ForeignKey("signal_threads.id", ondelete="CASCADE"), nullable=False, index=True)
    route_id: Mapped[uuid.UUID] = mapped_column(UUID(as_uuid=True), ForeignKey("copy_routes.id", ondelete="CASCADE"), nullable=False, index=True)
    telegram_message_id: Mapped[int] = mapped_column(BigInteger, nullable=False)
    action_type: Mapped[str] = mapped_column(String(64), nullable=False)
    revision: Mapped[int] = mapped_column(nullable=False, default=1)
    model_name: Mapped[str] = mapped_column(String(100), nullable=False)
    parser_version: Mapped[str] = mapped_column(String(64), nullable=False)
    confidence: Mapped[float] = mapped_column(Float, nullable=False)
    payload: Mapped[dict] = mapped_column(JSONB, nullable=False)
    validation_result: Mapped[dict] = mapped_column(JSONB, nullable=False, default=dict)
    created_at: Mapped[datetime] = mapped_column(DateTime(timezone=True), default=utcnow, nullable=False)


class TradeIntent(Base):
    __tablename__ = "trade_intents"
    __table_args__ = (
        UniqueConstraint("idempotency_key"),
        UniqueConstraint("client_order_id", name="uq_trade_intent_client_order_id"),
        Index("ix_trade_intent_connection_state", "connection_id", "state"),
    )

    id: Mapped[uuid.UUID] = mapped_column(UUID(as_uuid=True), primary_key=True, default=uuid.uuid4)
    user_id: Mapped[uuid.UUID] = mapped_column(UUID(as_uuid=True), ForeignKey("users.id", ondelete="CASCADE"), nullable=False, index=True)
    route_id: Mapped[uuid.UUID] = mapped_column(UUID(as_uuid=True), ForeignKey("copy_routes.id", ondelete="CASCADE"), nullable=False, index=True)
    connection_id: Mapped[Optional[uuid.UUID]] = mapped_column(UUID(as_uuid=True), ForeignKey("copy_trading_connections.id", ondelete="SET NULL"), nullable=True, index=True)
    legacy_account_id: Mapped[Optional[uuid.UUID]] = mapped_column(UUID(as_uuid=True), ForeignKey("trading_accounts.id", ondelete="SET NULL"), nullable=True, index=True)
    parsed_action_id: Mapped[uuid.UUID] = mapped_column(UUID(as_uuid=True), ForeignKey("parsed_actions.id", ondelete="CASCADE"), nullable=False)
    idempotency_key: Mapped[str] = mapped_column(String(255), nullable=False)
    client_order_id: Mapped[Optional[str]] = mapped_column(String(32), nullable=True)
    state: Mapped[TradeIntentState] = mapped_column(Enum(TradeIntentState, values_callable=enum_values, name="tradeintentstateenum"), nullable=False, default=TradeIntentState.created)
    request_payload: Mapped[dict] = mapped_column(JSONB, nullable=False)
    broker_result: Mapped[dict] = mapped_column(JSONB, nullable=False, default=dict)
    attempt_count: Mapped[int] = mapped_column(nullable=False, default=0)
    last_error_code: Mapped[Optional[str]] = mapped_column(String(100), nullable=True)
    submitted_at: Mapped[Optional[datetime]] = mapped_column(DateTime(timezone=True), nullable=True)
    resolved_at: Mapped[Optional[datetime]] = mapped_column(DateTime(timezone=True), nullable=True)
    created_at: Mapped[datetime] = mapped_column(DateTime(timezone=True), default=utcnow, nullable=False)
    updated_at: Mapped[datetime] = mapped_column(DateTime(timezone=True), default=utcnow, onupdate=utcnow, nullable=False)


class CopiedTrade(Base):
    __tablename__ = "copied_trades"
    __table_args__ = (Index("ix_copied_trade_route_lifecycle", "route_id", "lifecycle_state"),)

    id: Mapped[uuid.UUID] = mapped_column(UUID(as_uuid=True), primary_key=True, default=uuid.uuid4)
    route_id: Mapped[uuid.UUID] = mapped_column(UUID(as_uuid=True), ForeignKey("copy_routes.id", ondelete="CASCADE"), nullable=False, index=True)
    thread_id: Mapped[uuid.UUID] = mapped_column(UUID(as_uuid=True), ForeignKey("signal_threads.id", ondelete="CASCADE"), nullable=False, index=True)
    intent_id: Mapped[uuid.UUID] = mapped_column(UUID(as_uuid=True), ForeignKey("trade_intents.id", ondelete="CASCADE"), nullable=False, unique=True)
    connection_id: Mapped[Optional[uuid.UUID]] = mapped_column(UUID(as_uuid=True), ForeignKey("copy_trading_connections.id", ondelete="SET NULL"), nullable=True, index=True)
    magic_number: Mapped[int] = mapped_column(BigInteger, nullable=False)
    route_comment: Mapped[str] = mapped_column(String(31), nullable=False)
    signal_symbol: Mapped[str] = mapped_column(String(64), nullable=False)
    broker_symbol: Mapped[str] = mapped_column(String(64), nullable=False)
    broker_order_id: Mapped[Optional[str]] = mapped_column(String(64), nullable=True, index=True)
    broker_deal_id: Mapped[Optional[str]] = mapped_column(String(64), nullable=True, index=True)
    broker_position_id: Mapped[Optional[str]] = mapped_column(String(64), nullable=True, index=True)
    lifecycle_state: Mapped[str] = mapped_column(String(32), nullable=False)
    original_volume: Mapped[Optional[Decimal]] = mapped_column(Numeric(12, 4), nullable=True)
    current_volume: Mapped[Optional[Decimal]] = mapped_column(Numeric(12, 4), nullable=True)
    stop_loss: Mapped[Optional[Decimal]] = mapped_column(Numeric(20, 8), nullable=True)
    take_profit: Mapped[Optional[Decimal]] = mapped_column(Numeric(20, 8), nullable=True)
    broker_synced_at: Mapped[Optional[datetime]] = mapped_column(DateTime(timezone=True), nullable=True)
    detached_at: Mapped[Optional[datetime]] = mapped_column(DateTime(timezone=True), nullable=True)
    created_at: Mapped[datetime] = mapped_column(DateTime(timezone=True), default=utcnow, nullable=False)
    updated_at: Mapped[datetime] = mapped_column(DateTime(timezone=True), default=utcnow, onupdate=utcnow, nullable=False)


class SymbolMapping(Base):
    __tablename__ = "symbol_mappings"
    __table_args__ = (UniqueConstraint("route_id", "connection_id", "normalized_signal_symbol"),)

    id: Mapped[uuid.UUID] = mapped_column(UUID(as_uuid=True), primary_key=True, default=uuid.uuid4)
    route_id: Mapped[uuid.UUID] = mapped_column(UUID(as_uuid=True), ForeignKey("copy_routes.id", ondelete="CASCADE"), nullable=False, index=True)
    connection_id: Mapped[uuid.UUID] = mapped_column(UUID(as_uuid=True), ForeignKey("copy_trading_connections.id", ondelete="CASCADE"), nullable=False, index=True)
    normalized_signal_symbol: Mapped[str] = mapped_column(String(64), nullable=False)
    broker_symbol: Mapped[str] = mapped_column(String(64), nullable=False)
    selection_evidence: Mapped[dict] = mapped_column(JSONB, nullable=False, default=dict)
    catalog_version: Mapped[str] = mapped_column(String(64), nullable=False)
    created_at: Mapped[datetime] = mapped_column(DateTime(timezone=True), default=utcnow, nullable=False)
    updated_at: Mapped[datetime] = mapped_column(DateTime(timezone=True), default=utcnow, onupdate=utcnow, nullable=False)


class TelegramAuthAttempt(Base):
    __tablename__ = "telegram_auth_attempts"

    id: Mapped[uuid.UUID] = mapped_column(UUID(as_uuid=True), primary_key=True, default=uuid.uuid4)
    auth_id: Mapped[str] = mapped_column(String(64), nullable=False, unique=True)
    user_id: Mapped[uuid.UUID] = mapped_column(UUID(as_uuid=True), ForeignKey("users.id", ondelete="CASCADE"), nullable=False, index=True)
    connection_id: Mapped[Optional[uuid.UUID]] = mapped_column(UUID(as_uuid=True), ForeignKey("telegram_connections.id", ondelete="CASCADE"), nullable=True, index=True)
    method: Mapped[str] = mapped_column(String(16), nullable=False)
    state: Mapped[TelegramAuthState] = mapped_column(Enum(TelegramAuthState, values_callable=enum_values, name="telegramauthstateenum"), nullable=False, default=TelegramAuthState.pending)
    encrypted_state: Mapped[Optional[str]] = mapped_column(Text, nullable=True)
    message: Mapped[Optional[str]] = mapped_column(Text, nullable=True)
    expires_at: Mapped[datetime] = mapped_column(DateTime(timezone=True), nullable=False, index=True)
    created_at: Mapped[datetime] = mapped_column(DateTime(timezone=True), default=utcnow, nullable=False)
    updated_at: Mapped[datetime] = mapped_column(DateTime(timezone=True), default=utcnow, onupdate=utcnow, nullable=False)


class CopyDeadLetter(Base):
    __tablename__ = "copy_dead_letters"
    __table_args__ = (Index("ix_copy_dead_letter_state_created", "state", "created_at"),)

    id: Mapped[uuid.UUID] = mapped_column(UUID(as_uuid=True), primary_key=True, default=uuid.uuid4)
    user_id: Mapped[Optional[uuid.UUID]] = mapped_column(UUID(as_uuid=True), ForeignKey("users.id", ondelete="SET NULL"), nullable=True, index=True)
    source_stream: Mapped[str] = mapped_column(String(100), nullable=False)
    consumer_group: Mapped[str] = mapped_column(String(100), nullable=False)
    source_message_id: Mapped[str] = mapped_column(String(64), nullable=False)
    event_type: Mapped[str] = mapped_column(String(100), nullable=False)
    correlation_id: Mapped[str] = mapped_column(String(64), nullable=False, index=True)
    idempotency_key: Mapped[str] = mapped_column(String(255), nullable=False)
    event_payload: Mapped[dict] = mapped_column(JSONB, nullable=False)
    attempts: Mapped[int] = mapped_column(nullable=False, default=1)
    error_code: Mapped[str] = mapped_column(String(100), nullable=False)
    error_message: Mapped[str] = mapped_column(Text, nullable=False)
    state: Mapped[DeadLetterState] = mapped_column(Enum(DeadLetterState, values_callable=enum_values, name="deadletterstateenum"), nullable=False, default=DeadLetterState.pending)
    replayed_at: Mapped[Optional[datetime]] = mapped_column(DateTime(timezone=True), nullable=True)
    created_at: Mapped[datetime] = mapped_column(DateTime(timezone=True), default=utcnow, nullable=False)
    updated_at: Mapped[datetime] = mapped_column(DateTime(timezone=True), default=utcnow, onupdate=utcnow, nullable=False)


class CopyWorkerHealth(Base):
    __tablename__ = "copy_worker_health"
    __table_args__ = (UniqueConstraint("worker_role", "instance_id"),)

    id: Mapped[uuid.UUID] = mapped_column(UUID(as_uuid=True), primary_key=True, default=uuid.uuid4)
    worker_role: Mapped[str] = mapped_column(String(64), nullable=False, index=True)
    instance_id: Mapped[str] = mapped_column(String(128), nullable=False)
    state: Mapped[WorkerHealthState] = mapped_column(Enum(WorkerHealthState, values_callable=enum_values, name="workerhealthstateenum"), nullable=False, default=WorkerHealthState.healthy)
    heartbeat_at: Mapped[datetime] = mapped_column(DateTime(timezone=True), nullable=False, index=True)
    stream_lag: Mapped[int] = mapped_column(nullable=False, default=0)
    pending_count: Mapped[int] = mapped_column(nullable=False, default=0)
    last_error: Mapped[Optional[str]] = mapped_column(Text, nullable=True)
    metrics: Mapped[dict] = mapped_column(JSONB, nullable=False, default=dict)
    created_at: Mapped[datetime] = mapped_column(DateTime(timezone=True), default=utcnow, nullable=False)
    updated_at: Mapped[datetime] = mapped_column(DateTime(timezone=True), default=utcnow, onupdate=utcnow, nullable=False)
