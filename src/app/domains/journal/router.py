import uuid
from datetime import date
from typing import Optional

from fastapi import APIRouter, Depends, File, Form, Query, Response, UploadFile, status
from sqlalchemy.orm import Session

from app.core.database import get_db
from app.domains.accounts.models import TradeDirection, TradeSession
from app.domains.journal import service as journal_service
from app.domains.journal.models import JournalMessageType, JournalTemplateType
from app.domains.journal.schemas import (
    AdjacentTradedDatesResponse,
    AnalyticsCalendarResponse,
    AnalyticsBalanceHistoryResponse,
    AnalyticsDashboardResponse,
    AnalyticsEquityResponse,
    AnalyticsInstrumentsResponse,
    AnalyticsReportResponse,
    AnalyticsSessionsResponse,
    AnalyticsSetupsResponse,
    AnalyticsSummaryResponse,
    AnalyticsTimePerformanceResponse,
    AnalyticsTradeSourceResponse,
    DailyJournalFeedItemResponse,
    DailyJournalFeedResponse,
    DailyJournalResponse,
    JournalMessageResponse,
    JournalMessageUpdateRequest,
    JournalReviewedAtResponse,
    JournalTemplateCreateRequest,
    JournalTemplateResponse,
    JournalTradeListResponse,
    JournalTradeResponse,
)
from app.domains.users.models import User
from app.shared.deps import get_current_user

# Sub-routers for each journal feature area
trades_router = APIRouter(prefix="/journal/trades", tags=["journal-trades"])
daily_router = APIRouter(prefix="/journal/daily", tags=["journal-daily"])
messages_router = APIRouter(prefix="/journal/messages", tags=["journal-messages"])
templates_router = APIRouter(prefix="/journal/templates", tags=["journal-templates"])
analytics_router = APIRouter(prefix="/journal/analytics", tags=["journal-analytics"])


# ---------------------------------------------------------------------------
# Trades
# ---------------------------------------------------------------------------

@trades_router.get("", response_model=JournalTradeListResponse)
def list_journal_trades(
    account_id: uuid.UUID = Query(...),
    from_date: Optional[date] = Query(None),
    to_date: Optional[date] = Query(None),
    symbol: Optional[str] = Query(None, min_length=1, max_length=20),
    direction: Optional[TradeDirection] = Query(None),
    session: Optional[TradeSession] = Query(None),
    limit: int = Query(50, ge=1, le=200),
    cursor: Optional[uuid.UUID] = Query(None),
    db: Session = Depends(get_db),
    current_user: User = Depends(get_current_user),
) -> JournalTradeListResponse:
    trades = journal_service.list_account_trades(
        db,
        current_user=current_user,
        account_id=account_id,
        from_date=from_date,
        to_date=to_date,
        symbol=symbol,
        direction=direction,
        session=session,
        limit=limit + 1,
        cursor_trade_id=cursor,
    )

    has_more = len(trades) > limit
    if has_more:
        trades = trades[:limit]

    return JournalTradeListResponse(
        items=trades,
        next_cursor=trades[-1].id if has_more else None,
    )


@trades_router.get("/{trade_id}/journal", response_model=list[JournalMessageResponse])
def get_or_create_trade_journal(
    trade_id: uuid.UUID,
    db: Session = Depends(get_db),
    current_user: User = Depends(get_current_user),
) -> list[JournalMessageResponse]:
    _, messages = journal_service.get_or_create_trade_journal(
        db,
        trade_id=trade_id,
        current_user=current_user,
    )
    return [JournalMessageResponse.model_validate(m) for m in messages]


@trades_router.post("/{trade_id}/messages", response_model=JournalMessageResponse)
def create_trade_message(
    trade_id: uuid.UUID,
    message_type: JournalMessageType = Form(JournalMessageType.text),
    content: Optional[str] = Form(None),
    file: Optional[UploadFile] = File(None),
    db: Session = Depends(get_db),
    current_user: User = Depends(get_current_user),
) -> JournalMessageResponse:
    message = journal_service.create_trade_journal_message(
        db,
        trade_id=trade_id,
        current_user=current_user,
        message_type=message_type,
        content=content,
        file=file,
    )
    return JournalMessageResponse.model_validate(message)


@trades_router.post("/{trade_id}/review", response_model=JournalReviewedAtResponse)
def mark_trade_journal_reviewed(
    trade_id: uuid.UUID,
    db: Session = Depends(get_db),
    current_user: User = Depends(get_current_user),
) -> JournalReviewedAtResponse:
    return journal_service.mark_trade_journal_reviewed(
        db, trade_id=trade_id, current_user=current_user
    )


# ---------------------------------------------------------------------------
# Daily
# ---------------------------------------------------------------------------

@daily_router.get(
    "/{account_id}/adjacent-traded-dates",
    response_model=AdjacentTradedDatesResponse,
)
def get_adjacent_traded_dates(
    account_id: uuid.UUID,
    trading_date: date = Query(..., description="Reference local trading date (YYYY-MM-DD)."),
    db: Session = Depends(get_db),
    current_user: User = Depends(get_current_user),
) -> AdjacentTradedDatesResponse:
    return journal_service.get_adjacent_traded_dates(
        db,
        account_id=account_id,
        trading_date=trading_date,
        current_user=current_user,
    )


@daily_router.get("/{account_id}/feed", response_model=DailyJournalFeedResponse)
def list_daily_feed(
    account_id: uuid.UUID,
    limit: int = Query(20, ge=1, le=100),
    cursor: Optional[uuid.UUID] = Query(None),
    db: Session = Depends(get_db),
    current_user: User = Depends(get_current_user),
) -> DailyJournalFeedResponse:
    items = journal_service.list_daily_journal_feed(
        db,
        account_id=account_id,
        current_user=current_user,
        limit=limit + 1,
        cursor_daily_journal_id=cursor,
    )

    has_more = len(items) > limit
    if has_more:
        items = items[:limit]

    return DailyJournalFeedResponse(
        items=[
            DailyJournalFeedItemResponse(id=item.id, trading_date=item.trading_date)
            for item in items
        ],
        next_cursor=items[-1].id if has_more else None,
    )


@daily_router.get("/{account_id}/{trading_date}", response_model=DailyJournalResponse)
def get_or_create_daily_journal(
    account_id: uuid.UUID,
    trading_date: date,
    include_messages: bool = Query(True),
    db: Session = Depends(get_db),
    current_user: User = Depends(get_current_user),
) -> DailyJournalResponse:
    return journal_service.get_or_create_daily_journal(
        db,
        account_id=account_id,
        trading_date=trading_date,
        current_user=current_user,
        include_messages=include_messages,
    )


@daily_router.post("/{daily_journal_id}/messages", response_model=JournalMessageResponse)
def create_daily_message(
    daily_journal_id: uuid.UUID,
    message_type: JournalMessageType = Form(JournalMessageType.text),
    content: Optional[str] = Form(None),
    file: Optional[UploadFile] = File(None),
    db: Session = Depends(get_db),
    current_user: User = Depends(get_current_user),
) -> JournalMessageResponse:
    message = journal_service.create_daily_journal_message(
        db,
        daily_journal_id=daily_journal_id,
        current_user=current_user,
        message_type=message_type,
        content=content,
        file=file,
    )
    return JournalMessageResponse.model_validate(message)


@daily_router.post("/{daily_journal_id}/review", response_model=JournalReviewedAtResponse)
def mark_daily_journal_reviewed(
    daily_journal_id: uuid.UUID,
    db: Session = Depends(get_db),
    current_user: User = Depends(get_current_user),
) -> JournalReviewedAtResponse:
    return journal_service.mark_daily_journal_reviewed(
        db, daily_journal_id=daily_journal_id, current_user=current_user
    )


# ---------------------------------------------------------------------------
# Messages
# ---------------------------------------------------------------------------

@messages_router.patch("/{message_id}", response_model=JournalMessageResponse)
def update_journal_message(
    message_id: uuid.UUID,
    payload: JournalMessageUpdateRequest,
    db: Session = Depends(get_db),
    current_user: User = Depends(get_current_user),
) -> JournalMessageResponse:
    message = journal_service.update_message(
        db,
        message_id=message_id,
        current_user=current_user,
        content=payload.content,
    )
    return JournalMessageResponse.model_validate(message)


@messages_router.delete("/{message_id}", status_code=status.HTTP_204_NO_CONTENT)
def delete_journal_message(
    message_id: uuid.UUID,
    db: Session = Depends(get_db),
    current_user: User = Depends(get_current_user),
) -> Response:
    journal_service.delete_message(
        db, message_id=message_id, current_user=current_user
    )
    return Response(status_code=status.HTTP_204_NO_CONTENT)


# ---------------------------------------------------------------------------
# Templates
# ---------------------------------------------------------------------------

@templates_router.get("", response_model=list[JournalTemplateResponse])
def list_journal_templates(
    template_type: JournalTemplateType | None = Query(None),
    db: Session = Depends(get_db),
    current_user: User = Depends(get_current_user),
) -> list[JournalTemplateResponse]:
    templates = journal_service.list_templates(
        db,
        current_user=current_user,
        template_type=template_type,
    )
    return [JournalTemplateResponse.model_validate(t) for t in templates]


@templates_router.post(
    "", response_model=JournalTemplateResponse, status_code=status.HTTP_201_CREATED
)
def create_journal_template(
    payload: JournalTemplateCreateRequest,
    db: Session = Depends(get_db),
    current_user: User = Depends(get_current_user),
) -> JournalTemplateResponse:
    template = journal_service.create_template(
        db,
        current_user=current_user,
        payload=payload,
    )
    return JournalTemplateResponse.model_validate(template)


@templates_router.delete("/{template_id}", status_code=status.HTTP_204_NO_CONTENT)
def delete_journal_template(
    template_id: uuid.UUID,
    db: Session = Depends(get_db),
    current_user: User = Depends(get_current_user),
) -> Response:
    journal_service.delete_template(
        db, current_user=current_user, template_id=template_id
    )
    return Response(status_code=status.HTTP_204_NO_CONTENT)


# ---------------------------------------------------------------------------
# Analytics
# ---------------------------------------------------------------------------

@analytics_router.get("/summary", response_model=AnalyticsSummaryResponse)
def get_summary(
    account_id: uuid.UUID = Query(...),
    from_date: date | None = Query(None),
    to_date: date | None = Query(None),
    db: Session = Depends(get_db),
    current_user: User = Depends(get_current_user),
) -> AnalyticsSummaryResponse:
    return journal_service.get_analytics_summary(
        db,
        account_id=account_id,
        user_id=current_user.id,
        from_date=from_date,
        to_date=to_date,
    )


@analytics_router.get("/calendar", response_model=AnalyticsCalendarResponse)
def get_calendar(
    account_id: uuid.UUID = Query(...),
    from_date: date | None = Query(None),
    to_date: date | None = Query(None),
    db: Session = Depends(get_db),
    current_user: User = Depends(get_current_user),
) -> AnalyticsCalendarResponse:
    return journal_service.get_analytics_calendar(
        db,
        account_id=account_id,
        user_id=current_user.id,
        from_date=from_date,
        to_date=to_date,
    )


@analytics_router.get("/sessions", response_model=AnalyticsSessionsResponse)
def get_sessions(
    account_id: uuid.UUID = Query(...),
    from_date: date | None = Query(None),
    to_date: date | None = Query(None),
    db: Session = Depends(get_db),
    current_user: User = Depends(get_current_user),
) -> AnalyticsSessionsResponse:
    return journal_service.get_analytics_sessions(
        db,
        account_id=account_id,
        user_id=current_user.id,
        from_date=from_date,
        to_date=to_date,
    )


@analytics_router.get("/instruments", response_model=AnalyticsInstrumentsResponse)
def get_instruments(
    account_id: uuid.UUID = Query(...),
    from_date: date | None = Query(None),
    to_date: date | None = Query(None),
    db: Session = Depends(get_db),
    current_user: User = Depends(get_current_user),
) -> AnalyticsInstrumentsResponse:
    return journal_service.get_analytics_instruments(
        db,
        account_id=account_id,
        user_id=current_user.id,
        from_date=from_date,
        to_date=to_date,
    )


@analytics_router.get("/time-performance", response_model=AnalyticsTimePerformanceResponse)
def get_time_performance(
    account_id: uuid.UUID = Query(...),
    from_date: date | None = Query(None),
    to_date: date | None = Query(None),
    db: Session = Depends(get_db),
    current_user: User = Depends(get_current_user),
) -> AnalyticsTimePerformanceResponse:
    return journal_service.get_analytics_time_performance(
        db,
        account_id=account_id,
        user_id=current_user.id,
        from_date=from_date,
        to_date=to_date,
    )


@analytics_router.get("/equity", response_model=AnalyticsEquityResponse)
def get_equity(
    account_id: uuid.UUID = Query(...),
    from_date: date | None = Query(None),
    to_date: date | None = Query(None),
    db: Session = Depends(get_db),
    current_user: User = Depends(get_current_user),
) -> AnalyticsEquityResponse:
    return journal_service.get_analytics_equity(
        db,
        account_id=account_id,
        user_id=current_user.id,
        from_date=from_date,
        to_date=to_date,
    )


@analytics_router.get("/balance-history", response_model=AnalyticsBalanceHistoryResponse)
def get_balance_history(
    account_id: uuid.UUID = Query(...),
    from_date: date | None = Query(None),
    to_date: date | None = Query(None),
    granularity: str = Query("day", pattern="^(intraday|day)$"),
    db: Session = Depends(get_db),
    current_user: User = Depends(get_current_user),
) -> AnalyticsBalanceHistoryResponse:
    return journal_service.get_analytics_balance_history(
        db,
        account_id=account_id,
        user_id=current_user.id,
        from_date=from_date,
        to_date=to_date,
        granularity=granularity,
    )


@analytics_router.get("/setups", response_model=AnalyticsSetupsResponse)
def get_setups(
    account_id: uuid.UUID = Query(...),
    from_date: date | None = Query(None),
    to_date: date | None = Query(None),
    db: Session = Depends(get_db),
    current_user: User = Depends(get_current_user),
) -> AnalyticsSetupsResponse:
    return journal_service.get_analytics_setups(
        db,
        account_id=account_id,
        user_id=current_user.id,
        from_date=from_date,
        to_date=to_date,
    )


@analytics_router.get("/trade-sources", response_model=AnalyticsTradeSourceResponse)
def get_trade_sources(
    account_id: uuid.UUID = Query(...),
    from_date: date | None = Query(None),
    to_date: date | None = Query(None),
    db: Session = Depends(get_db),
    current_user: User = Depends(get_current_user),
) -> AnalyticsTradeSourceResponse:
    return journal_service.get_analytics_trade_sources(
        db,
        account_id=account_id,
        user_id=current_user.id,
        from_date=from_date,
        to_date=to_date,
    )


@analytics_router.get("/report", response_model=AnalyticsReportResponse)
def get_report(
    account_id: uuid.UUID = Query(...),
    from_date: date | None = Query(None),
    to_date: date | None = Query(None),
    db: Session = Depends(get_db),
    current_user: User = Depends(get_current_user),
) -> AnalyticsReportResponse:
    return journal_service.get_analytics_report(
        db,
        account_id=account_id,
        user_id=current_user.id,
        from_date=from_date,
        to_date=to_date,
    )


@analytics_router.get("/dashboard", response_model=AnalyticsDashboardResponse)
def get_dashboard(
    account_id: uuid.UUID | None = Query(None),
    from_date: date | None = Query(None),
    to_date: date | None = Query(None),
    recent_limit: int = Query(8, ge=1, le=50),
    db: Session = Depends(get_db),
    current_user: User = Depends(get_current_user),
) -> AnalyticsDashboardResponse:
    return journal_service.get_analytics_dashboard(
        db,
        account_id=account_id,
        user_id=current_user.id,
        from_date=from_date,
        to_date=to_date,
        recent_limit=recent_limit,
    )
