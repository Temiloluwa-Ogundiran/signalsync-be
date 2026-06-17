import uuid
from datetime import date
from typing import Optional

from fastapi import APIRouter, Depends, File, Form, Query, Response, UploadFile, status
from sqlalchemy.orm import Session

from app.core.database import get_db
from app.domains.accounts.models import TradeDirection, TradeSession
from app.domains.accounts.schemas import ManualTradeCreateRequest, ManualTradeUpdateRequest
from app.domains.journal import service as journal_service
from app.domains.journal.models import JournalMessageType, JournalTemplateType
from app.domains.journal.schemas import (
    AdjacentTradedDatesResponse,
    AnalyticsDashboardResponse,
    AnalyticsEquityCurveResponse,
    AnalyticsEvaluationResponse,
    AnalyticsCurveResponse,
    AnalyticsIntradayCurvesResponse,
    AnalyticsSummaryResponse,
    AnalyticsTimePerformanceResponse,
    DailyJournalFeedItemResponse,
    DailyJournalFeedResponse,
    DailyJournalResponse,
    DayNoteResponse,
    DayNoteUpdateRequest,
    JournalMessageResponse,
    JournalMessageUpdateRequest,
    JournalOpenPositionListResponse,
    JournalReviewedAtResponse,
    JournalTemplateCreateRequest,
    JournalTemplateResponse,
    JournalTradeListResponse,
    JournalTradeResponse,
    ReorderRequest,
    SetupCreateRequest,
    SetupResponse,
    TagCreateRequest,
    TagGroupCreateRequest,
    TagGroupResponse,
    TagGroupUpdateRequest,
    TagResponse,
    TagUpdateRequest,
    TradeAssessmentUpdateRequest,
    TradeNoteResponse,
    TradeNoteUpdateRequest,
    TradeRatingUpdateRequest,
    TradeSetupUpdateRequest,
    TradeTagUpdateRequest,
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
    include_manual: bool = Query(True),
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
        include_manual=include_manual,
    )

    has_more = len(trades) > limit
    if has_more:
        trades = trades[:limit]

    return JournalTradeListResponse(
        items=trades,
        next_cursor=trades[-1].id if has_more else None,
    )


@trades_router.get("/positions", response_model=JournalOpenPositionListResponse)
async def list_journal_open_positions(
    account_id: uuid.UUID = Query(...),
    limit: int = Query(20, ge=1, le=200),
    db: Session = Depends(get_db),
    current_user: User = Depends(get_current_user),
) -> JournalOpenPositionListResponse:
    return await journal_service.list_account_open_positions(
        db,
        current_user=current_user,
        account_id=account_id,
        limit=limit,
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


@trades_router.post("/manual", response_model=JournalTradeResponse, status_code=status.HTTP_201_CREATED)
def create_manual_trade(
    account_id: uuid.UUID = Query(...),
    payload: ManualTradeCreateRequest = ...,
    db: Session = Depends(get_db),
    current_user: User = Depends(get_current_user),
) -> JournalTradeResponse:
    return journal_service.create_manual_trade(
        db,
        account_id=account_id,
        payload=payload,
        current_user=current_user,
    )


@trades_router.patch("/manual/{trade_id}", response_model=JournalTradeResponse)
def update_manual_trade(
    trade_id: uuid.UUID,
    payload: ManualTradeUpdateRequest,
    db: Session = Depends(get_db),
    current_user: User = Depends(get_current_user),
) -> JournalTradeResponse:
    return journal_service.update_manual_trade(
        db,
        trade_id=trade_id,
        payload=payload,
        current_user=current_user,
    )


@trades_router.delete("/manual/{trade_id}", status_code=status.HTTP_204_NO_CONTENT)
def delete_manual_trade(
    trade_id: uuid.UUID,
    db: Session = Depends(get_db),
    current_user: User = Depends(get_current_user),
) -> Response:
    journal_service.delete_manual_trade(
        db,
        trade_id=trade_id,
        current_user=current_user,
    )
    return Response(status_code=status.HTTP_204_NO_CONTENT)


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
    include_manual: bool = Query(True),
    db: Session = Depends(get_db),
    current_user: User = Depends(get_current_user),
) -> DailyJournalResponse:
    return journal_service.get_or_create_daily_journal(
        db,
        account_id=account_id,
        trading_date=trading_date,
        current_user=current_user,
        include_messages=include_messages,
        include_manual=include_manual,
    )


@daily_router.get(
    "/{account_id}/{trading_date}/note", response_model=DayNoteResponse
)
def get_day_note(
    account_id: uuid.UUID,
    trading_date: date,
    db: Session = Depends(get_db),
    current_user: User = Depends(get_current_user),
) -> DayNoteResponse:
    return journal_service.get_day_note(
        db,
        account_id=account_id,
        trading_date=trading_date,
        current_user=current_user,
    )


@daily_router.put(
    "/{account_id}/{trading_date}/note", response_model=DayNoteResponse
)
def save_day_note(
    account_id: uuid.UUID,
    trading_date: date,
    payload: DayNoteUpdateRequest,
    db: Session = Depends(get_db),
    current_user: User = Depends(get_current_user),
) -> DayNoteResponse:
    return journal_service.save_day_note(
        db,
        account_id=account_id,
        trading_date=trading_date,
        note_html=payload.note_html,
        current_user=current_user,
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
    include_manual: bool = Query(True),
    db: Session = Depends(get_db),
    current_user: User = Depends(get_current_user),
) -> AnalyticsSummaryResponse:
    return journal_service.get_analytics_summary(
        db,
        account_id=account_id,
        user_id=current_user.id,
        from_date=from_date,
        to_date=to_date,
        include_manual=include_manual,
    )


@analytics_router.get("/time-performance", response_model=AnalyticsTimePerformanceResponse)
def get_time_performance(
    account_id: uuid.UUID = Query(...),
    from_date: date | None = Query(None),
    to_date: date | None = Query(None),
    time_basis: str = Query("close", pattern="^(open|close)$"),
    include_manual: bool = Query(True),
    db: Session = Depends(get_db),
    current_user: User = Depends(get_current_user),
) -> AnalyticsTimePerformanceResponse:
    return journal_service.get_analytics_time_performance(
        db,
        account_id=account_id,
        user_id=current_user.id,
        from_date=from_date,
        to_date=to_date,
        time_basis=time_basis,
        include_manual=include_manual,
    )


@analytics_router.get("/equity-curve", response_model=AnalyticsEquityCurveResponse)
def get_equity_curve(
    account_id: uuid.UUID = Query(...),
    from_date: date | None = Query(None),
    to_date: date | None = Query(None),
    include_manual: bool = Query(True),
    db: Session = Depends(get_db),
    current_user: User = Depends(get_current_user),
) -> AnalyticsEquityCurveResponse:
    return journal_service.get_analytics_equity_curve(
        db,
        account_id=account_id,
        user_id=current_user.id,
        from_date=from_date,
        to_date=to_date,
        include_manual=include_manual,
    )


@analytics_router.get(
    "/intraday-curves", response_model=AnalyticsIntradayCurvesResponse
)
def get_intraday_curves(
    account_id: uuid.UUID = Query(...),
    from_date: date | None = Query(None),
    to_date: date | None = Query(None),
    include_manual: bool = Query(True),
    db: Session = Depends(get_db),
    current_user: User = Depends(get_current_user),
) -> AnalyticsIntradayCurvesResponse:
    return journal_service.get_analytics_intraday_curves(
        db,
        account_id=account_id,
        user_id=current_user.id,
        from_date=from_date,
        to_date=to_date,
        include_manual=include_manual,
    )



@analytics_router.get("/curve", response_model=AnalyticsCurveResponse)
def get_curve(
    account_id: uuid.UUID = Query(...),
    from_date: date | None = Query(None),
    to_date: date | None = Query(None),
    granularity: str = Query(..., pattern="^(daily|intraday)$"),
    include_manual: bool = Query(True),
    db: Session = Depends(get_db),
    current_user: User = Depends(get_current_user),
) -> AnalyticsCurveResponse:
    """Unified curve endpoint: daily or intraday granularity.
    
    - granularity=daily: daily P&L bars + cumulative curve (range-scoped)
    - granularity=intraday: all days with per-trade points (one call, no N+1),
                           downsampled to ~20 per day, zero-baselined
    
    Uses deterministic sort: close_time → broker_trade_id (BIGINT, NULLS LAST) → id
    """
    return journal_service.get_analytics_curve(
        db,
        account_id=account_id,
        user_id=current_user.id,
        from_date=from_date,
        to_date=to_date,
        granularity=granularity,
        include_manual=include_manual,
    )

@analytics_router.get("/evaluation", response_model=AnalyticsEvaluationResponse)
def get_evaluation(
    account_id: uuid.UUID = Query(...),
    from_date: date | None = Query(None),
    to_date: date | None = Query(None),
    include_manual: bool = Query(True),
    db: Session = Depends(get_db),
    current_user: User = Depends(get_current_user),
) -> AnalyticsEvaluationResponse:
    return journal_service.get_analytics_evaluation(
        db,
        account_id=account_id,
        user_id=current_user.id,
        from_date=from_date,
        to_date=to_date,
        include_manual=include_manual,
    )


@analytics_router.get("/dashboard", response_model=AnalyticsDashboardResponse)
def get_dashboard(
    account_id: uuid.UUID | None = Query(None),
    from_date: date | None = Query(None),
    to_date: date | None = Query(None),
    recent_limit: int = Query(8, ge=1, le=50),
    time_basis: str = Query("close", pattern="^(open|close)$"),
    include_manual: bool = Query(True),
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
        time_basis=time_basis,
        include_manual=include_manual,
    )


# ---------------------------------------------------------------------------
# Tags
# ---------------------------------------------------------------------------

tags_router = APIRouter(tags=["journal-tags"])


@tags_router.get("/journal/tags/config", response_model=list[TagGroupResponse])
def get_tags_config(
    db: Session = Depends(get_db),
    current_user: User = Depends(get_current_user),
) -> list[TagGroupResponse]:
    groups = journal_service.list_user_tags_config(db, user=current_user)
    return [TagGroupResponse.model_validate(g) for g in groups]


@tags_router.post("/journal/tags/groups", response_model=TagGroupResponse, status_code=status.HTTP_201_CREATED)
def create_tag_group(
    payload: TagGroupCreateRequest,
    db: Session = Depends(get_db),
    current_user: User = Depends(get_current_user),
) -> TagGroupResponse:
    group = journal_service.create_tag_group(db, user=current_user, name=payload.name, color=payload.color)
    return TagGroupResponse.model_validate(group)


@tags_router.put("/journal/tags/groups/reorder", status_code=status.HTTP_204_NO_CONTENT)
def reorder_tag_groups(
    payload: ReorderRequest,
    db: Session = Depends(get_db),
    current_user: User = Depends(get_current_user),
) -> Response:
    journal_service.reorder_tag_groups(db, user=current_user, ids=payload.ids)
    return Response(status_code=status.HTTP_204_NO_CONTENT)


@tags_router.put("/journal/tags/groups/{group_id}", response_model=TagGroupResponse)
def update_tag_group(
    group_id: uuid.UUID,
    payload: TagGroupUpdateRequest,
    db: Session = Depends(get_db),
    current_user: User = Depends(get_current_user),
) -> TagGroupResponse:
    group = journal_service.update_tag_group(db, user=current_user, group_id=group_id, name=payload.name, color=payload.color)
    return TagGroupResponse.model_validate(group)


@tags_router.delete("/journal/tags/groups/{group_id}", status_code=status.HTTP_204_NO_CONTENT)
def delete_tag_group(
    group_id: uuid.UUID,
    db: Session = Depends(get_db),
    current_user: User = Depends(get_current_user),
) -> Response:
    journal_service.delete_tag_group(db, user=current_user, group_id=group_id)
    return Response(status_code=status.HTTP_204_NO_CONTENT)


@tags_router.post(
    "/journal/tags/groups/{group_id}/tags",
    response_model=TagResponse,
    status_code=status.HTTP_201_CREATED,
)
def create_tag(
    group_id: uuid.UUID,
    payload: TagCreateRequest,
    db: Session = Depends(get_db),
    current_user: User = Depends(get_current_user),
) -> TagResponse:
    tag = journal_service.create_tag(
        db, user=current_user, group_id=group_id, name=payload.name,
    )
    return TagResponse.model_validate(tag)


@tags_router.put("/journal/tags/reorder", status_code=status.HTTP_204_NO_CONTENT)
def reorder_tags(
    payload: ReorderRequest,
    db: Session = Depends(get_db),
    current_user: User = Depends(get_current_user),
) -> Response:
    journal_service.reorder_tags(db, user=current_user, ids=payload.ids)
    return Response(status_code=status.HTTP_204_NO_CONTENT)


@tags_router.put("/journal/tags/{tag_id}", response_model=TagResponse)
def update_tag(
    tag_id: uuid.UUID,
    payload: TagUpdateRequest,
    db: Session = Depends(get_db),
    current_user: User = Depends(get_current_user),
) -> TagResponse:
    tag = journal_service.update_tag(
        db, user=current_user, tag_id=tag_id, name=payload.name,
    )
    return TagResponse.model_validate(tag)


@tags_router.delete("/journal/tags/{tag_id}", status_code=status.HTTP_204_NO_CONTENT)
def delete_tag(
    tag_id: uuid.UUID,
    db: Session = Depends(get_db),
    current_user: User = Depends(get_current_user),
) -> Response:
    journal_service.delete_tag(db, user=current_user, tag_id=tag_id)
    return Response(status_code=status.HTTP_204_NO_CONTENT)


@tags_router.get("/journal/trades/{trade_id}/tags", response_model=list[TagResponse])
def get_trade_tags(
    trade_id: uuid.UUID,
    db: Session = Depends(get_db),
    current_user: User = Depends(get_current_user),
) -> list[TagResponse]:
    tags = journal_service.get_trade_tags(db, user=current_user, trade_id=trade_id)
    return [TagResponse.model_validate(t) for t in tags]


@tags_router.put("/journal/trades/{trade_id}/tags", response_model=list[TagResponse])
def update_trade_tags(
    trade_id: uuid.UUID,
    payload: TradeTagUpdateRequest,
    db: Session = Depends(get_db),
    current_user: User = Depends(get_current_user),
) -> list[TagResponse]:
    tags = journal_service.update_trade_tags(db, user=current_user, trade_id=trade_id, tag_ids=payload.tag_ids)
    return [TagResponse.model_validate(t) for t in tags]


@tags_router.put("/journal/trades/{trade_id}/rating")
def update_trade_rating(
    trade_id: uuid.UUID,
    payload: TradeRatingUpdateRequest,
    db: Session = Depends(get_db),
    current_user: User = Depends(get_current_user),
):
    rating = journal_service.update_trade_rating(db, user=current_user, trade_id=trade_id, rating=payload.rating)
    return {"rating": rating}


@tags_router.put("/journal/trades/{trade_id}/assessment")
def update_trade_assessment(
    trade_id: uuid.UUID,
    payload: TradeAssessmentUpdateRequest,
    db: Session = Depends(get_db),
    current_user: User = Depends(get_current_user),
):
    return journal_service.update_trade_assessment(
        db,
        user=current_user,
        trade_id=trade_id,
        execution_quality=payload.execution_quality,
        setup_quality=payload.setup_quality,
        discipline_score=payload.discipline_score,
    )


# ---------------------------------------------------------------------------
# Per-trade note (plain text)
# ---------------------------------------------------------------------------

@tags_router.get("/journal/trades/{trade_id}/note", response_model=TradeNoteResponse)
def get_trade_note(
    trade_id: uuid.UUID,
    db: Session = Depends(get_db),
    current_user: User = Depends(get_current_user),
) -> TradeNoteResponse:
    return TradeNoteResponse(
        **journal_service.get_trade_note(db, user=current_user, trade_id=trade_id)
    )


@tags_router.put("/journal/trades/{trade_id}/note", response_model=TradeNoteResponse)
def save_trade_note(
    trade_id: uuid.UUID,
    payload: TradeNoteUpdateRequest,
    db: Session = Depends(get_db),
    current_user: User = Depends(get_current_user),
) -> TradeNoteResponse:
    return TradeNoteResponse(
        **journal_service.save_trade_note(
            db, user=current_user, trade_id=trade_id, note_html=payload.note_html
        )
    )


# ---------------------------------------------------------------------------
# Setups (flat playbook names) + per-trade setup assignment
# ---------------------------------------------------------------------------

@tags_router.get("/journal/setups", response_model=list[SetupResponse])
def list_setups(
    db: Session = Depends(get_db),
    current_user: User = Depends(get_current_user),
) -> list[SetupResponse]:
    setups = journal_service.list_setups(db, user=current_user)
    return [SetupResponse.model_validate(s) for s in setups]


@tags_router.post("/journal/setups", response_model=SetupResponse, status_code=status.HTTP_201_CREATED)
def create_setup(
    payload: SetupCreateRequest,
    db: Session = Depends(get_db),
    current_user: User = Depends(get_current_user),
) -> SetupResponse:
    setup = journal_service.create_setup(db, user=current_user, name=payload.name)
    return SetupResponse.model_validate(setup)


@tags_router.delete("/journal/setups/{setup_id}", status_code=status.HTTP_204_NO_CONTENT)
def delete_setup(
    setup_id: uuid.UUID,
    db: Session = Depends(get_db),
    current_user: User = Depends(get_current_user),
) -> Response:
    journal_service.delete_setup(db, user=current_user, setup_id=setup_id)
    return Response(status_code=status.HTTP_204_NO_CONTENT)


@tags_router.put("/journal/trades/{trade_id}/setup")
def update_trade_setup(
    trade_id: uuid.UUID,
    payload: TradeSetupUpdateRequest,
    db: Session = Depends(get_db),
    current_user: User = Depends(get_current_user),
):
    setup = journal_service.update_trade_setup(
        db, user=current_user, trade_id=trade_id, setup=payload.setup
    )
    return {"setup": setup}
