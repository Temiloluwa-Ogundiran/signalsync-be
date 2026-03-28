from datetime import date
from decimal import Decimal
from typing import Optional

from pydantic import BaseModel


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


class AnalyticsInstrumentsResponse(BaseModel):
    instruments: list[AnalyticsInstrumentItemResponse]


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
