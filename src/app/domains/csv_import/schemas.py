from pydantic import BaseModel, Field
from typing import Optional, Any
from decimal import Decimal
from datetime import datetime
import uuid
from app.domains.accounts.schemas import AccountResponse

class PlatformInfo(BaseModel):
    id: str
    name: str
    description: str
    supported_extensions: list[str]
    export_instructions: str
    max_file_size_mb: int

class CSVPreviewAccountMeta(BaseModel):
    account_number: Optional[str] = None
    currency: Optional[str] = None
    broker_server: Optional[str] = None
    account_type: Optional[str] = None
    broker_name: Optional[str] = None
    starting_balance: Optional[Decimal] = None
    current_balance: Optional[Decimal] = None

class CSVPreviewTrade(BaseModel):
    broker_trade_id: str
    symbol: str
    direction: str
    opened_at: datetime
    closed_at: datetime
    open_price: Decimal
    close_price: Decimal
    volume: Decimal
    profit: Decimal
    commission: Decimal
    swap: Decimal
    sl: Optional[Decimal] = None
    tp: Optional[Decimal] = None

class CSVParseError(BaseModel):
    row_number: int
    column: Optional[str] = None
    message: str
    severity: str

class CSVPreviewResponse(BaseModel):
    account_meta: CSVPreviewAccountMeta
    trades: list[CSVPreviewTrade]
    trade_count: int
    errors: list[CSVParseError]
    warnings: list[str]
    summary: dict[str, Any]

class CSVConfirmResult(BaseModel):
    account: AccountResponse
    inserted: int
    skipped: int
    touched_dates: int
