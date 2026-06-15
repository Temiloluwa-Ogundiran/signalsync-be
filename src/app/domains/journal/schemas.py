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
    trading_date: Optional[date] = None
    is_manual: bool = False
    is_missed: bool = False
    balance_before_trade: Decimal | None = None
    net_roi_percent: Decimal | None = None
    created_at: datetime

    # MT5-enriched fields — None for trades without MT5 metadata
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

    trade_reviewed_at: Optional[datetime] = None
    rating: Optional[int] = None
    execution_quality: Optional[int] = None
    setup_quality: Optional[int] = None
    discipline_score: Optional[int] = None

    model_config = {"from_attributes": True}


class JournalTradeListResponse(BaseModel):
    items: list[JournalTradeResponse]
    next_cursor: Optional[uuid.UUID] = None


class JournalOpenPositionResponse(BaseModel):
    position_id: str
    symbol: str
    side: str
    volume: float
    floating_profit: float
    opened_at: Optional[datetime] = None
    open_price: float
    current_price: float
    sl: Optional[float] = None
    tp: Optional[float] = None
    magic: Optional[int] = None
    comment: Optional[str] = None


class JournalOpenPositionListResponse(BaseModel):
    as_of: Optional[datetime] = None
    items: list[JournalOpenPositionResponse]


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
    is_manual: bool = False
    is_missed: bool = False


class DailyJournalResponse(BaseModel):
    id: uuid.UUID
    trading_date: date
    account_timezone: str
    reviewed_at: Optional[datetime] = None
    note_html: Optional[str] = None
    note_updated_at: Optional[datetime] = None
    day_start_balance: Decimal | None = None
    day_end_balance: Decimal | None = None
    trade_chips: list[DailyTradeChipResponse]
    trades: list[JournalTradeResponse]
    messages: list[JournalMessageResponse]


class DayNoteUpdateRequest(BaseModel):
    """Save payload for the single daily note (HTML from the rich-text editor)."""

    note_html: Optional[str] = None


class DayNoteResponse(BaseModel):
    """The daily note for one account-local trading day."""

    trading_date: date
    note_html: Optional[str] = None
    note_updated_at: Optional[datetime] = None


class DailyJournalFeedItemResponse(BaseModel):
    id: uuid.UUID
    trading_date: date


class DailyJournalFeedResponse(BaseModel):
    items: list[DailyJournalFeedItemResponse]
    next_cursor: Optional[uuid.UUID] = None


class JournalReviewedAtResponse(BaseModel):
    reviewed_at: datetime


class AdjacentTradedDatesResponse(BaseModel):
    prev_date: Optional[date] = None
    next_date: Optional[date] = None


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
    has_journal_activity: bool = False


class AnalyticsCalendarResponse(BaseModel):
    month: str
    days: list[AnalyticsCalendarDayResponse]


class AnalyticsEquityCurvePointResponse(BaseModel):
    """One point on the cumulative net-realized-P&L curve (starts at zero)."""

    date: date
    cumulative_pnl: float
    daily_pnl: float


class AnalyticsEquityCurveResponse(BaseModel):
    points: list[AnalyticsEquityCurvePointResponse]


class AnalyticsIntradayCurvePointResponse(BaseModel):
    """One point on a single day's intraday running-P&L curve.

    `t` is the account-local close timestamp of the trade; `cumulative_pnl` is
    the running total of net P&L within that day up to and including this trade.
    """

    t: datetime
    cumulative_pnl: float


class AnalyticsIntradayCurveDayResponse(BaseModel):
    """One day's intraday curve: trades ordered by close time, accumulated."""

    date: date
    net_pnl: float  # day total = last point's cumulative_pnl
    points: list[AnalyticsIntradayCurvePointResponse]


class AnalyticsIntradayCurvesResponse(BaseModel):
    """Per-day intraday running-P&L curves for a date range, in one payload.

    Each day's `points` are ordered by close time with a running cumulative sum
    reset to zero at the start of the day — ready to render as a day sparkline.
    """

    days: list[AnalyticsIntradayCurveDayResponse]


class AnalyticsEvaluationResponse(BaseModel):
    """Detailed evaluation stats for the journal sidebar panel — all derived
    from closed trades. Dollar/count based (the FE renders a $ view)."""

    total_trades: int
    avg_profit_per_trading_day: float  # total net P&L / number of trading days
    biggest_winner: float  # largest single-trade net profit (0 if none)
    biggest_loser: float  # most negative single-trade net profit (0 if none)
    total_fees: float  # sum of commission + swap across trades
    avg_hold_seconds: float  # mean trade duration in seconds
    winrate_wo_be: float  # wins / (wins + losses) * 100, excluding breakeven
    roi: float  # total net P&L / starting balance * 100 (0 if no starting bal)
    max_drawdown_pct: float  # peak-to-trough drawdown as % of the running peak
    winning_days: int
    losing_days: int
    trades_per_day: float
    trades_per_week: float
    # Outcome of the most recent trades, oldest→newest: "W" win, "L" loss, "B" breakeven.
    recent_streak: list[str]


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


class AnalyticsTimePerformancePointResponse(BaseModel):
    bucket: str
    trade_count: int
    total_pnl: float
    win_rate: float
    avg_pnl: float


class AnalyticsTimePerformanceResponse(BaseModel):
    hourly: list[AnalyticsTimePerformancePointResponse]
    daily: list[AnalyticsTimePerformancePointResponse]


class AnalyticsDashboardResponse(BaseModel):
    summary: AnalyticsSummaryResponse
    calendar: AnalyticsCalendarResponse
    instruments: AnalyticsInstrumentsResponse
    time_performance: AnalyticsTimePerformanceResponse
    recent_trades: JournalTradeListResponse


# ---------------------------------------------------------------------------
# Tag schemas (merged from schemas_tags.py)
# ---------------------------------------------------------------------------

class TagOptionResponse(BaseModel):
    id: uuid.UUID
    category_id: uuid.UUID
    value: str
    color: Optional[str] = None

    model_config = {"from_attributes": True}


class TagCategoryResponse(BaseModel):
    id: uuid.UUID
    title: str
    is_system: bool
    options: list[TagOptionResponse] = []

    model_config = {"from_attributes": True}


class CategoryCreateRequest(BaseModel):
    title: str = Field(..., min_length=1, max_length=100)


class OptionCreateRequest(BaseModel):
    value: str = Field(..., min_length=1, max_length=100)
    color: Optional[str] = Field(default=None, max_length=7, pattern="^#([A-Fa-f0-9]{6})$")


class TradeTagUpdateRequest(BaseModel):
    option_ids: list[uuid.UUID]


class TradeRatingUpdateRequest(BaseModel):
    rating: int = Field(..., ge=1, le=5)


class TradeAssessmentUpdateRequest(BaseModel):
    execution_quality: Optional[int] = Field(default=None, ge=0, le=10)
    setup_quality: Optional[int] = Field(default=None, ge=0, le=10)
    discipline_score: Optional[int] = Field(default=None, ge=0, le=10)


# ---------------------------------------------------------------------------
# Unified Curve Endpoint (Phase 1)
# ---------------------------------------------------------------------------

class AnalyticsCurveDailyPointResponse(BaseModel):
    """One day in the daily P&L curve."""
    date: date
    daily_pnl: float  # P&L on this day alone
    cumulative_pnl: float  # cumulative from start of range


class AnalyticsCurveDailyResponse(BaseModel):
    """Daily P&L curve: one point per day, cumulative reset at range start."""
    points: list[AnalyticsCurveDailyPointResponse]


class AnalyticsCurveIntradayPointResponse(BaseModel):
    """One point on an intraday curve.

    Carries the sequence index `i`, the account-local close time `t` (so the
    client can plot by real time / uneven spacing), the closing trade's
    `symbol` (for the hover tooltip), and the running `cumulative_pnl`.
    """
    i: int  # sequence index within the day (0, 1, 2...)
    t: datetime  # account-local close time of this trade (baseline = first close)
    symbol: Optional[str] = None  # closing trade's symbol (None for the baseline)
    cumulative_pnl: float  # cumulative P&L within the day at this trade


class AnalyticsCurveIntradayDayResponse(BaseModel):
    """One day's intraday curve."""
    date: date
    net_pnl: float  # total for the day
    trades_count: int  # number of trades
    points: list[AnalyticsCurveIntradayPointResponse]


class AnalyticsCurveIntradayResponse(BaseModel):
    """Intraday curves for all days in range (one response, no per-day calls)."""
    days: list[AnalyticsCurveIntradayDayResponse]


class AnalyticsCurveResponse(BaseModel):
    """Union response for unified /curve endpoint."""
    # When granularity=daily, populate daily_curve; when granularity=intraday, populate intraday_curve.
    daily_curve: Optional[AnalyticsCurveDailyResponse] = None
    intraday_curve: Optional[AnalyticsCurveIntradayResponse] = None
