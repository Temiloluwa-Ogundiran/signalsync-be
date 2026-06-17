import uuid
from datetime import date, datetime
from decimal import Decimal
from typing import Any, Literal, Optional

from pydantic import BaseModel, Field, computed_field, model_validator

from app.domains.accounts.models import (
    TradingAccountConnectionState,
    TradingAccountStatus,
    TradingAccountType,
    TradingPlatform,
    TradeDirection,
)
from app.domains.accounts.status import describe_account_sync_status


class AccountSyncStatusResponse(BaseModel):
    code: str
    severity: Literal["success", "info", "pending", "warning", "error"]
    headline: str
    detail: str
    action: Optional[str] = None


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


class AccountBalanceResponse(BaseModel):
    """Latest known account balance/equity from the most recent synced snapshot.

    All fields are null when the account has no snapshot yet (e.g. just connected
    and not synced). The account's realized trading P&L is separate and lives in
    the journal analytics summary.
    """

    account_id: uuid.UUID
    balance: Optional[float] = None
    equity: Optional[float] = None
    floating_pnl: Optional[float] = None
    as_of: Optional[date] = None


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
    last_sync_attempted_at: Optional[datetime] = None
    last_sync_outcome: Optional[str] = None
    next_sync_not_before: Optional[datetime] = None
    latest_balance: Optional[Decimal] = None
    latest_equity: Optional[Decimal] = None
    is_deleted: bool
    sync_provider: str
    created_at: datetime

    @computed_field  # type: ignore[prop-decorator]
    @property
    def is_demo(self) -> bool:
        """True for the seeded demo account (drives the demo banner + label)."""
        return self.meta_account_id == "DEMO-SEED"

    @computed_field  # type: ignore[prop-decorator]
    @property
    def sync_status(self) -> AccountSyncStatusResponse:
        status = describe_account_sync_status(
            connection_state=self.connection_state,
            sync_provider=self.sync_provider,
            last_sync_outcome=self.last_sync_outcome,
            sync_error_message=self.sync_error_message,
            bootstrap_error_message=self.bootstrap_error_message,
        )
        return AccountSyncStatusResponse(
            code=status.code,
            severity=status.severity,
            headline=status.headline,
            detail=status.detail,
            action=status.action,
        )

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


class AccountUpdateRequest(BaseModel):
    display_name: str = Field(..., min_length=1, max_length=120)


class ManualTradeCreateRequest(BaseModel):
    # Trade type
    is_missed: bool = False

    # Core — always required
    symbol: str = Field(..., min_length=1, max_length=20)
    direction: TradeDirection
    opened_at: datetime          # ISO 8601 with timezone
    open_price: Decimal = Field(..., gt=0)

    # Executed trade — required when is_missed=False
    volume: Optional[Decimal] = Field(None, gt=0)
    closed_at: Optional[datetime] = None
    close_price: Optional[Decimal] = Field(None, gt=0)
    net_profit: Optional[Decimal] = None
    commission: Optional[Decimal] = Decimal("0")   # optional, default 0
    swap: Optional[Decimal] = Decimal("0")          # optional, default 0

    # Risk levels — optional for executed, prominent for missed
    sl: Optional[Decimal] = None
    tp: Optional[Decimal] = None

    @model_validator(mode="after")
    def validate_executed_fields(self) -> "ManualTradeCreateRequest":
        if not self.is_missed:
            if self.volume is None:
                raise ValueError("volume is required for executed trades")
            if self.closed_at is None:
                raise ValueError("closed_at is required for executed trades")
            if self.close_price is None:
                raise ValueError("close_price is required for executed trades")
            if self.net_profit is None:
                raise ValueError("net_profit is required for executed trades")
        return self


class ManualTradeUpdateRequest(BaseModel):
    symbol: Optional[str] = Field(None, min_length=1, max_length=20)
    direction: Optional[TradeDirection] = None
    opened_at: Optional[datetime] = None
    closed_at: Optional[datetime] = None
    open_price: Optional[Decimal] = Field(None, gt=0)
    close_price: Optional[Decimal] = Field(None, gt=0)
    volume: Optional[Decimal] = Field(None, gt=0)
    net_profit: Optional[Decimal] = None
    commission: Optional[Decimal] = None
    swap: Optional[Decimal] = None
    sl: Optional[Decimal] = None
    tp: Optional[Decimal] = None
    is_missed: Optional[bool] = None
