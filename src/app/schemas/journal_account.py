import uuid
from datetime import datetime
from typing import Optional

from pydantic import BaseModel, Field

from app.models.trading_account import TradingAccountStatus, TradingAccountType, TradingPlatform


class JournalAccountConnectRequest(BaseModel):
    broker_name: Optional[str] = Field(default=None, max_length=120)
    broker_login: str = Field(min_length=1, max_length=64)
    broker_server: str = Field(min_length=1, max_length=120)
    investor_password: str = Field(min_length=1, max_length=255)
    trader_password: Optional[str] = Field(default=None, min_length=1, max_length=255)
    account_type: TradingAccountType = TradingAccountType.live
    platform: TradingPlatform
    currency: str = Field(default="USD", min_length=1, max_length=10)
    timezone: str = Field(default="UTC", min_length=1, max_length=50)
    broker_utc_offset: int = Field(default=0, ge=-1440, le=1440)
    display_name: Optional[str] = Field(default=None, max_length=120)


class JournalAccountResponse(BaseModel):
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
    last_synced_at: Optional[datetime]
    sync_error_message: Optional[str]
    is_deleted: bool
    created_at: datetime

    model_config = {"from_attributes": True}
