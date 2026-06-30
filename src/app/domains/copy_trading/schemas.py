import uuid
from datetime import datetime
from decimal import Decimal
from typing import Optional

from pydantic import BaseModel, Field, SecretStr, model_validator

from app.domains.copy_trading.models import (
    CopyActivityLevel,
    CopyRouteState,
    CopyTradingConnectionState,
    LotDistribution,
    MinimumFields,
    TakeProfitMode,
    AutomationConfidence,
    TelegramConnectionState,
    TelegramSourceState,
    TelegramSourceType,
)


UNSAFE_MINIMUM_FIELDS = {
    MinimumFields.direction_symbol,
    MinimumFields.direction_symbol_entry,
    MinimumFields.direction_symbol_sl,
    MinimumFields.direction_symbol_tp,
}


class CopyTradingConnectionCreate(BaseModel):
    display_name: str = Field(min_length=1, max_length=120)
    broker_login: str = Field(pattern=r"^\d+$", max_length=64)
    broker_server: str = Field(min_length=1, max_length=160)
    trader_password: SecretStr = Field(min_length=1, max_length=256)
    platform: str = Field(default="mt5", pattern=r"^mt5$")


class CopyTradingConnectionResponse(BaseModel):
    id: uuid.UUID
    user_id: uuid.UUID
    display_name: str
    broker_login: str
    broker_server: str
    platform: str
    metaapi_account_id: Optional[str]
    state: CopyTradingConnectionState
    last_error_code: Optional[str]
    last_error_message: Optional[str]
    symbol_catalog_refreshed_at: Optional[datetime]
    last_health_at: Optional[datetime]
    created_at: datetime
    updated_at: datetime

    model_config = {"from_attributes": True}


class CopyRouteCreate(BaseModel):
    source_id: uuid.UUID
    target_connection_id: uuid.UUID
    fixed_lot: Decimal = Field(gt=0, max_digits=12, decimal_places=4)
    take_profit_mode: TakeProfitMode = TakeProfitMode.all
    lot_distribution: LotDistribution = LotDistribution.fixed_each
    pending_orders_enabled: bool = True
    minimum_fields: MinimumFields = MinimumFields.direction_symbol_sl_tp
    assembly_window_seconds: Optional[int] = Field(default=None, ge=1, le=600)
    process_all_group_authors: bool = False
    notify_success: bool = True
    notify_failure: bool = True
    allow_sl_tp_updates: bool = True
    allow_break_even: bool = True
    allow_additional_tp: bool = True
    allow_partial_close: bool = True
    allow_full_close: bool = True
    allow_pending_cancel: bool = True
    unsafe_minimum_confirmed: bool = False

    @model_validator(mode="after")
    def validate_route_settings(self):
        if (
            self.lot_distribution == LotDistribution.split_total
            and self.take_profit_mode != TakeProfitMode.all
        ):
            raise ValueError("Split lot is available only when all take profits are enabled.")
        if self.minimum_fields in UNSAFE_MINIMUM_FIELDS and not self.unsafe_minimum_confirmed:
            raise ValueError("Unsafe minimum fields require explicit warning confirmation.")
        return self


class CopyRouteUpdate(BaseModel):
    fixed_lot: Optional[Decimal] = Field(default=None, gt=0, max_digits=12, decimal_places=4)
    take_profit_mode: Optional[TakeProfitMode] = None
    lot_distribution: Optional[LotDistribution] = None
    pending_orders_enabled: Optional[bool] = None
    minimum_fields: Optional[MinimumFields] = None
    assembly_window_seconds: Optional[int] = Field(default=None, ge=1, le=600)
    process_all_group_authors: Optional[bool] = None
    notify_success: Optional[bool] = None
    notify_failure: Optional[bool] = None
    allow_sl_tp_updates: Optional[bool] = None
    allow_break_even: Optional[bool] = None
    allow_additional_tp: Optional[bool] = None
    allow_partial_close: Optional[bool] = None
    allow_full_close: Optional[bool] = None
    allow_pending_cancel: Optional[bool] = None
    unsafe_minimum_confirmed: Optional[bool] = None

    @model_validator(mode="after")
    def validate_explicit_route_settings(self):
        if (
            self.lot_distribution == LotDistribution.split_total
            and self.take_profit_mode is not None
            and self.take_profit_mode != TakeProfitMode.all
        ):
            raise ValueError("Split lot is available only when all take profits are enabled.")
        if (
            self.minimum_fields in UNSAFE_MINIMUM_FIELDS
            and self.unsafe_minimum_confirmed is not True
        ):
            raise ValueError("Unsafe minimum fields require explicit warning confirmation.")
        return self


class CopyTradingSettingsUpdate(BaseModel):
    is_paused: bool


class CopyTradingSettingsResponse(BaseModel):
    user_id: uuid.UUID
    is_paused: bool
    created_at: datetime
    updated_at: datetime

    model_config = {"from_attributes": True}


class CopyAccountPolicyUpdate(BaseModel):
    max_lot: Optional[Decimal] = Field(default=None, gt=0, max_digits=12, decimal_places=4)
    is_paused: Optional[bool] = None


class CopyAccountPolicyResponse(BaseModel):
    id: uuid.UUID
    connection_id: uuid.UUID
    max_lot: Decimal
    is_paused: bool
    created_at: datetime
    updated_at: datetime

    model_config = {"from_attributes": True}


class CopyRouteResponse(BaseModel):
    id: uuid.UUID
    source_id: uuid.UUID
    target_connection_id: Optional[uuid.UUID]
    magic_number: int
    state: CopyRouteState
    fixed_lot: Decimal
    take_profit_mode: TakeProfitMode
    lot_distribution: LotDistribution
    pending_orders_enabled: bool
    minimum_fields: MinimumFields
    assembly_window_seconds: Optional[int]
    process_all_group_authors: bool
    notify_success: bool
    notify_failure: bool
    allow_sl_tp_updates: bool
    allow_break_even: bool
    allow_additional_tp: bool
    allow_partial_close: bool
    allow_full_close: bool
    allow_pending_cancel: bool
    unsafe_minimum_confirmed_at: Optional[datetime]
    created_at: datetime
    updated_at: datetime

    model_config = {"from_attributes": True}


class CopyActivityResponse(BaseModel):
    id: uuid.UUID
    route_id: Optional[uuid.UUID]
    source_id: Optional[uuid.UUID]
    connection_id: Optional[uuid.UUID]
    correlation_id: str
    action: str
    level: CopyActivityLevel
    title: str
    body: Optional[str]
    parsed_details: dict
    broker_details: dict
    created_at: datetime

    model_config = {"from_attributes": True}


class CopyActivityPageResponse(BaseModel):
    items: list[CopyActivityResponse]
    next_cursor: Optional[str] = None


class CopyHealthComponentResponse(BaseModel):
    role: str
    status: str
    heartbeat_at: Optional[datetime] = None
    stream_lag: int = 0
    pending_count: int = 0
    last_error: Optional[str] = None


class CopySystemHealthResponse(BaseModel):
    status: str
    ready: bool
    components: list[CopyHealthComponentResponse]
    issues: list[str]


class CopyLaunchReadinessResponse(BaseModel):
    ready: bool
    blockers: list[str]
    warnings: list[str]
    components: list[CopyHealthComponentResponse]
    stream_lag: int
    pending_events: int
    dead_letters: int
    oldest_uncertain_seconds: int
    oldest_active_intent_seconds: int
    global_paused: bool


class CopyDeadLetterResponse(BaseModel):
    id: uuid.UUID
    source_stream: str
    event_type: str
    correlation_id: str
    attempts: int
    error_code: str
    error_message: str
    state: str
    created_at: datetime

    model_config = {"from_attributes": True}


class TelegramPhoneAuthStart(BaseModel):
    phone: str = Field(min_length=7, max_length=32)


class TelegramCodeSubmit(BaseModel):
    code: str = Field(min_length=3, max_length=16)


class TelegramPasswordSubmit(BaseModel):
    password: str = Field(min_length=1, max_length=256)


class TelegramAuthResponse(BaseModel):
    auth_id: uuid.UUID
    method: str
    state: str
    qr_url: Optional[str] = None
    message: str


class TelegramConnectionResponse(BaseModel):
    id: uuid.UUID
    telegram_user_id: Optional[int]
    phone_hint: Optional[str]
    display_name: Optional[str]
    username: Optional[str]
    state: TelegramConnectionState
    is_paused: bool
    reauthentication_reason: Optional[str]
    last_heartbeat_at: Optional[datetime]
    created_at: datetime

    model_config = {"from_attributes": True}


class TelegramDialogResponse(BaseModel):
    chat_id: int
    title: str
    username: Optional[str] = None
    source_type: TelegramSourceType
    is_admin: bool = False


class TelegramSourceCreate(BaseModel):
    connection_id: uuid.UUID
    telegram_chat_id: int
    title: str = Field(min_length=1, max_length=255)
    username: Optional[str] = Field(default=None, max_length=255)
    source_type: TelegramSourceType


class ChannelProfileResponse(BaseModel):
    id: uuid.UUID
    signal_style: str
    recommended_assembly_window_seconds: int
    confidence: AutomationConfidence
    confidence_score: float
    image_frequency: float
    image_primary: bool
    supported_actions: list
    sample_count: int
    validated_at: datetime

    model_config = {"from_attributes": True}


class TelegramSourceResponse(BaseModel):
    id: uuid.UUID
    connection_id: uuid.UUID
    telegram_chat_id: int
    title: str
    username: Optional[str]
    source_type: TelegramSourceType
    state: TelegramSourceState
    is_paused: bool

    model_config = {"from_attributes": True}


class EmergencyActionRequest(BaseModel):
    action: str = Field(pattern="^(close_positions|cancel_pending|both)$")
    scope: str = Field(pattern="^(global|account|source|route)$")
    scope_id: Optional[uuid.UUID] = None
    confirmation: str

    @model_validator(mode="after")
    def validate_confirmation(self):
        if self.confirmation != "EMERGENCY":
            raise ValueError("Emergency action requires the confirmation word EMERGENCY.")
        if self.scope != "global" and self.scope_id is None:
            raise ValueError("The selected emergency scope requires an id.")
        return self
