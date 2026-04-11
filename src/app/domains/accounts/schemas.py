import uuid
from datetime import datetime
from typing import Any, Literal, Optional

from pydantic import BaseModel, Field

from app.domains.accounts.models import (
    TradingAccountConnectionState,
    TradingAccountStatus,
    TradingAccountType,
    TradingPlatform,
)


class AccountConnectRequest(BaseModel):
    broker_name: Optional[str] = Field(default=None, max_length=120)
    broker_login: str = Field(min_length=1, max_length=64)
    broker_server: str = Field(min_length=1, max_length=120)
    investor_password: str = Field(min_length=1, max_length=255)
    trader_password: Optional[str] = Field(default=None, min_length=1, max_length=255)
    account_type: TradingAccountType = TradingAccountType.live
    platform: Literal[TradingPlatform.mt5] = TradingPlatform.mt5
    currency: str = Field(default="USD", min_length=1, max_length=10)
    timezone: str = Field(default="UTC", min_length=1, max_length=50)
    broker_utc_offset: int = Field(default=0, ge=-1440, le=1440)
    display_name: Optional[str] = Field(default=None, max_length=120)


class AccountResponse(BaseModel):
    id: uuid.UUID
    user_id: uuid.UUID
    meta_account_id: str
    broker_name: str
    broker_login: str
    broker_server: str
    account_type: TradingAccountType
    platform: TradingPlatform
    currency: str
    timezone: str
    broker_utc_offset: int
    display_name: Optional[str]
    status: TradingAccountStatus
    connection_state: TradingAccountConnectionState
    is_data_ready_for_stats: bool
    last_synced_at: Optional[datetime]
    last_bootstrap_synced_at: Optional[datetime]
    sync_error_message: Optional[str]
    bootstrap_error_message: Optional[str]
    is_deleted: bool
    created_at: datetime

    model_config = {"from_attributes": True}


class MT5WebhookDeal(BaseModel):
    ticket: str
    position_id: str
    symbol: str
    direction: str
    volume: float
    price_in: float
    price_out: float
    sl: Optional[float] = None
    tp: Optional[float] = None
    gross_profit: float
    commission: float
    swap: float
    time_setup: Any
    time_closed: Any
    magic_number: int = 0
    deal_comment: str = ""
    trade_source: str
    mfe: Optional[float] = None
    mae: Optional[float] = None


class MT5WebhookSnapshot(BaseModel):
    captured_at: Any
    balance: float
    equity: float
    floating_pnl: float


class MT5WebhookPayload(BaseModel):
    account_id: uuid.UUID
    broker_server: str
    status: str = Field(..., description="'ok' | 'error' | 'empty'")
    result_type: Optional[str] = Field(
        default=None,
        description="'verified' | 'sync_complete' | 'invalid_credentials' | 'transient_error'",
    )
    error_message: Optional[str] = None
    summary: dict[str, Any] = Field(default_factory=dict)
    deals: list[MT5WebhookDeal] = Field(default_factory=list)
    snapshots: list[MT5WebhookSnapshot] = Field(default_factory=list)
