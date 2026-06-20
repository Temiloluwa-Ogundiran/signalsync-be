import uuid
from datetime import datetime
from decimal import Decimal
from typing import Optional

from pydantic import BaseModel, Field, model_validator

from app.domains.copy_trading.models import (
    CopyActivityLevel,
    CopyRouteState,
    LotDistribution,
    MinimumFields,
    TakeProfitMode,
)


UNSAFE_MINIMUM_FIELDS = {
    MinimumFields.direction_symbol,
    MinimumFields.direction_symbol_entry,
    MinimumFields.direction_symbol_sl,
    MinimumFields.direction_symbol_tp,
}


class CopyRouteCreate(BaseModel):
    source_id: uuid.UUID
    target_account_id: uuid.UUID
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
    account_id: uuid.UUID
    max_lot: Decimal
    is_paused: bool
    created_at: datetime
    updated_at: datetime

    model_config = {"from_attributes": True}


class CopyRouteResponse(BaseModel):
    id: uuid.UUID
    source_id: uuid.UUID
    target_account_id: uuid.UUID
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
    account_id: Optional[uuid.UUID]
    correlation_id: str
    action: str
    level: CopyActivityLevel
    title: str
    body: Optional[str]
    parsed_details: dict
    broker_details: dict
    created_at: datetime

    model_config = {"from_attributes": True}
