import uuid
from collections import defaultdict
from datetime import date, datetime
from decimal import Decimal
from typing import Optional

from pydantic import BaseModel, Field

from app.domains.accounts.models import TradeDirection, TradeSession, TradeSource
from app.domains.journal.models import JournalMessageType, JournalTemplateType


# ---------------------------------------------------------------------------
# Trade schemas
# ---------------------------------------------------------------------------

class JournalTradeResponse(BaseModel):
    id: uuid.UUID
    account_id: uuid.UUID
    broker_trade_id: str
    symbol: str
    direction: TradeDirection
    open_price: Decimal
    close_price: Decimal
    volume: Decimal
    profit: Decimal
    commission: Decimal
    swap: Decimal
    net_profit: Decimal
    duration_seconds: int
    session: TradeSession
    opened_at: datetime
    closed_at: datetime
    balance_before_trade: Decimal | None = None
    net_roi_percent: Decimal | None = None
    created_at: datetime

    # MT5-enriched fields — None for MetaAPI-sourced trades
    sl: Optional[Decimal] = None
    tp: Optional[Decimal] = None
    magic_number: Optional[int] = None
    position_id: Optional[str] = None
    trade_source: Optional[TradeSource] = None
    mfe: Optional[Decimal] = None
    mae: Optional[Decimal] = None

    # Derived analytics field — computed by the service layer when SL data is present
    r_multiple: Optional[float] = Field(
        None,
        description=(
            "R-multiple: net_profit / abs(open_price - sl). "
            "Only populated when the SL field is available and non-zero."
        ),
    )

    model_config = {"from_attributes": True}


class JournalTradeListResponse(BaseModel):
    items: list[JournalTradeResponse]
    next_cursor: Optional[uuid.UUID] = None


# ---------------------------------------------------------------------------
# Message / Attachment schemas
# ---------------------------------------------------------------------------

class JournalMessageCreateRequest(BaseModel):
    message_type: JournalMessageType = JournalMessageType.text
    content: Optional[str] = Field(default=None, max_length=5000)


class JournalMessageUpdateRequest(BaseModel):
    content: Optional[str] = Field(default=None, max_length=5000)


class JournalAttachmentResponse(BaseModel):
    id: uuid.UUID
    storage_path: str
    media_type: str
    mime_type: str
    original_filename: Optional[str]
    caption: Optional[str]
    signed_url: str
    signed_url_expires_at: datetime

    model_config = {"from_attributes": True}


class JournalMessageResponse(BaseModel):
    id: uuid.UUID
    daily_journal_id: Optional[uuid.UUID]
    trade_journal_id: Optional[uuid.UUID]
    author_id: Optional[uuid.UUID]
    message_type: JournalMessageType
    content: Optional[str]
    tags: list[str]
    system_data: Optional[dict]
    audio_storage_path: Optional[str]
    audio_duration_seconds: Optional[int]
    audio_url: Optional[str]
    audio_url_expires_at: Optional[datetime]
    attachments: list[JournalAttachmentResponse]
    is_edited: bool
    edited_at: Optional[datetime]
    created_at: datetime

    model_config = {"from_attributes": True}


# ---------------------------------------------------------------------------
# Daily journal schemas
# ---------------------------------------------------------------------------

class DailyTradeChipResponse(BaseModel):
    trade_id: uuid.UUID
    symbol: str
    direction: str
    net_profit: Decimal
    outcome: str
    journal_message_count: int


class DailyJournalResponse(BaseModel):
    id: uuid.UUID
    trading_date: date
    account_timezone: str
    trade_chips: list[DailyTradeChipResponse]
    messages: list[JournalMessageResponse]


class DailyJournalFeedItemResponse(BaseModel):
    id: uuid.UUID
    trading_date: date


class DailyJournalFeedResponse(BaseModel):
    items: list[DailyJournalFeedItemResponse]
    next_cursor: Optional[uuid.UUID] = None


# ---------------------------------------------------------------------------
# Template schemas
# ---------------------------------------------------------------------------

class JournalTemplateQuestion(BaseModel):
    id: int
    order: int
    text: str = Field(min_length=1, max_length=500)


class JournalTemplateCreateRequest(BaseModel):
    name: str = Field(min_length=1, max_length=120)
    template_type: JournalTemplateType
    questions: list[JournalTemplateQuestion] = Field(min_length=1)


class JournalTemplateResponse(BaseModel):
    id: uuid.UUID
    name: str
    template_type: JournalTemplateType
    is_system: bool
    owner_id: uuid.UUID | None
    questions: list[JournalTemplateQuestion]
    created_at: datetime

    model_config = {"from_attributes": True}


# ---------------------------------------------------------------------------
# Analytics schemas
# ---------------------------------------------------------------------------

class AnalyticsBestWorstDay(BaseModel):
    date: date
    pnl: Decimal


class AnalyticsSummaryResponse(BaseModel):
    total_trades: int
    win_rate: float
    profit_factor: float
    avg_win: float
    avg_loss: float
    avg_trade_duration_seconds: float
    total_net_pnl: float
    starting_balance: float
    net_pnl_percent: float
    max_drawdown: float
    best_day: Optional[AnalyticsBestWorstDay]
    worst_day: Optional[AnalyticsBestWorstDay]


class AnalyticsCalendarDayResponse(BaseModel):
    date: date
    trade_count: int
    total_pnl: float
    win_count: int
    loss_count: int
    outcome: str


class AnalyticsCalendarResponse(BaseModel):
    month: str
    days: list[AnalyticsCalendarDayResponse]


class AnalyticsSessionItemResponse(BaseModel):
    session: str
    trade_count: int
    win_rate: float
    total_pnl: float
    avg_pnl: float


class AnalyticsSessionsResponse(BaseModel):
    sessions: list[AnalyticsSessionItemResponse]


class AnalyticsInstrumentItemResponse(BaseModel):
    symbol: str
    trade_count: int
    total_pnl: float
    win_rate: float
    avg_pnl: float
    # MT5-enriched (None when trades don't have MFE/MAE data)
    avg_mfe: Optional[float] = None
    avg_mae: Optional[float] = None


class AnalyticsInstrumentsResponse(BaseModel):
    instruments: list[AnalyticsInstrumentItemResponse]


class AnalyticsTradeSourceItemResponse(BaseModel):
    trade_source: str
    trade_count: int
    win_rate: float
    total_pnl: float
    avg_pnl: float


class AnalyticsTradeSourceResponse(BaseModel):
    sources: list[AnalyticsTradeSourceItemResponse]


class AnalyticsEquityPointResponse(BaseModel):
    date: date
    balance: float
    equity: float
    floating_pnl: float


class AnalyticsEquityResponse(BaseModel):
    points: list[AnalyticsEquityPointResponse]


class AnalyticsSetupItemResponse(BaseModel):
    tag: str
    trade_count: int
    win_rate: float
    total_pnl: float


class AnalyticsSetupsResponse(BaseModel):
    setups: list[AnalyticsSetupItemResponse]


class AnalyticsReportResponse(BaseModel):
    summary: AnalyticsSummaryResponse
    sessions: AnalyticsSessionsResponse
    instruments: AnalyticsInstrumentsResponse
    setups: AnalyticsSetupsResponse
    trade_sources: AnalyticsTradeSourceResponse
