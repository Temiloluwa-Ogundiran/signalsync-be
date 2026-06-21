import enum
import uuid
from datetime import datetime, timezone
from decimal import Decimal
from typing import Optional

from sqlalchemy import (
    BigInteger,
    Boolean,
    CheckConstraint,
    DateTime,
    Enum,
    Float,
    ForeignKey,
    Index,
    Numeric,
    String,
    Text,
    UniqueConstraint,
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


class CopyRouteState(str, enum.Enum):
    draft = "draft"
    ready = "ready"
    active = "active"
    paused = "paused"
    reauthentication_required = "reauthentication_required"
    unsupported = "unsupported"
    target_unavailable = "target_unavailable"


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


def enum_values(enum_type: type[enum.Enum]) -> list[str]:
    return [item.value for item in enum_type]


class CopyTradingUserSettings(Base):
    __tablename__ = "copy_trading_user_settings"

    user_id: Mapped[uuid.UUID] = mapped_column(
        UUID(as_uuid=True), ForeignKey("users.id", ondelete="CASCADE"), primary_key=True
    )
    is_paused: Mapped[bool] = mapped_column(Boolean, nullable=False, default=False)
    created_at: Mapped[datetime] = mapped_column(DateTime(timezone=True), default=utcnow, nullable=False)
    updated_at: Mapped[datetime] = mapped_column(
        DateTime(timezone=True), default=utcnow, onupdate=utcnow, nullable=False
    )


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
        UniqueConstraint("user_id", "account_id"),
        CheckConstraint("max_lot > 0", name="ck_copy_account_policy_max_lot_positive"),
    )

    id: Mapped[uuid.UUID] = mapped_column(UUID(as_uuid=True), primary_key=True, default=uuid.uuid4)
    user_id: Mapped[uuid.UUID] = mapped_column(
        UUID(as_uuid=True), ForeignKey("users.id", ondelete="CASCADE"), nullable=False, index=True
    )
    account_id: Mapped[uuid.UUID] = mapped_column(
        UUID(as_uuid=True),
        ForeignKey("trading_accounts.id", ondelete="CASCADE"),
        nullable=False,
        index=True,
    )
    max_lot: Mapped[Decimal] = mapped_column(
        Numeric(12, 4), nullable=False, default=Decimal("100.0000")
    )
    is_paused: Mapped[bool] = mapped_column(Boolean, nullable=False, default=False)
    created_at: Mapped[datetime] = mapped_column(DateTime(timezone=True), default=utcnow, nullable=False)
    updated_at: Mapped[datetime] = mapped_column(
        DateTime(timezone=True), default=utcnow, onupdate=utcnow, nullable=False
    )


class CopyRoute(Base):
    __tablename__ = "copy_routes"
    __table_args__ = (
        UniqueConstraint("user_id", "source_id", "target_account_id"),
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
    target_account_id: Mapped[uuid.UUID] = mapped_column(
        UUID(as_uuid=True),
        ForeignKey("trading_accounts.id", ondelete="CASCADE"),
        nullable=False,
        index=True,
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
    account_id: Mapped[Optional[uuid.UUID]] = mapped_column(
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
    __table_args__ = (UniqueConstraint("idempotency_key"), Index("ix_trade_intent_account_state", "account_id", "state"))

    id: Mapped[uuid.UUID] = mapped_column(UUID(as_uuid=True), primary_key=True, default=uuid.uuid4)
    user_id: Mapped[uuid.UUID] = mapped_column(UUID(as_uuid=True), ForeignKey("users.id", ondelete="CASCADE"), nullable=False, index=True)
    route_id: Mapped[uuid.UUID] = mapped_column(UUID(as_uuid=True), ForeignKey("copy_routes.id", ondelete="CASCADE"), nullable=False, index=True)
    account_id: Mapped[uuid.UUID] = mapped_column(UUID(as_uuid=True), ForeignKey("trading_accounts.id", ondelete="CASCADE"), nullable=False, index=True)
    parsed_action_id: Mapped[uuid.UUID] = mapped_column(UUID(as_uuid=True), ForeignKey("parsed_actions.id", ondelete="CASCADE"), nullable=False)
    idempotency_key: Mapped[str] = mapped_column(String(255), nullable=False)
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
    magic_number: Mapped[int] = mapped_column(BigInteger, nullable=False)
    route_comment: Mapped[str] = mapped_column(String(31), nullable=False)
    signal_symbol: Mapped[str] = mapped_column(String(64), nullable=False)
    broker_symbol: Mapped[str] = mapped_column(String(64), nullable=False)
    broker_order_id: Mapped[Optional[str]] = mapped_column(String(64), nullable=True, index=True)
    broker_deal_id: Mapped[Optional[str]] = mapped_column(String(64), nullable=True, index=True)
    broker_position_id: Mapped[Optional[str]] = mapped_column(String(64), nullable=True, index=True)
    lifecycle_state: Mapped[str] = mapped_column(String(32), nullable=False)
    detached_at: Mapped[Optional[datetime]] = mapped_column(DateTime(timezone=True), nullable=True)
    created_at: Mapped[datetime] = mapped_column(DateTime(timezone=True), default=utcnow, nullable=False)
    updated_at: Mapped[datetime] = mapped_column(DateTime(timezone=True), default=utcnow, onupdate=utcnow, nullable=False)


class SymbolMapping(Base):
    __tablename__ = "symbol_mappings"
    __table_args__ = (UniqueConstraint("route_id", "account_id", "normalized_signal_symbol"),)

    id: Mapped[uuid.UUID] = mapped_column(UUID(as_uuid=True), primary_key=True, default=uuid.uuid4)
    route_id: Mapped[uuid.UUID] = mapped_column(UUID(as_uuid=True), ForeignKey("copy_routes.id", ondelete="CASCADE"), nullable=False, index=True)
    account_id: Mapped[uuid.UUID] = mapped_column(UUID(as_uuid=True), ForeignKey("trading_accounts.id", ondelete="CASCADE"), nullable=False, index=True)
    normalized_signal_symbol: Mapped[str] = mapped_column(String(64), nullable=False)
    broker_symbol: Mapped[str] = mapped_column(String(64), nullable=False)
    selection_evidence: Mapped[dict] = mapped_column(JSONB, nullable=False, default=dict)
    catalog_version: Mapped[str] = mapped_column(String(64), nullable=False)
    created_at: Mapped[datetime] = mapped_column(DateTime(timezone=True), default=utcnow, nullable=False)
    updated_at: Mapped[datetime] = mapped_column(DateTime(timezone=True), default=utcnow, onupdate=utcnow, nullable=False)
