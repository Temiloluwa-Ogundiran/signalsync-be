import uuid
from datetime import date

from fastapi import APIRouter, Depends, Query
from sqlalchemy.orm import Session

from app.api.deps import get_current_user
from app.core.database import get_db
from app.models.user import User
from app.schemas.analytics import (
    AnalyticsCalendarResponse,
    AnalyticsEquityResponse,
    AnalyticsInstrumentsResponse,
    AnalyticsReportResponse,
    AnalyticsSessionsResponse,
    AnalyticsSetupsResponse,
    AnalyticsSummaryResponse,
)
from app.services import analytics_service

router = APIRouter(prefix="/journal/analytics", tags=["journal-analytics"])


@router.get("/summary", response_model=AnalyticsSummaryResponse)
def get_summary(
    account_id: uuid.UUID = Query(...),
    from_date: date | None = Query(None),
    to_date: date | None = Query(None),
    db: Session = Depends(get_db),
    current_user: User = Depends(get_current_user),
) -> AnalyticsSummaryResponse:
    return analytics_service.get_summary(
        db,
        account_id=account_id,
        user_id=current_user.id,
        from_date=from_date,
        to_date=to_date,
    )


@router.get("/calendar", response_model=AnalyticsCalendarResponse)
def get_calendar(
    account_id: uuid.UUID = Query(...),
    from_date: date | None = Query(None),
    to_date: date | None = Query(None),
    db: Session = Depends(get_db),
    current_user: User = Depends(get_current_user),
) -> AnalyticsCalendarResponse:
    return analytics_service.get_calendar(
        db,
        account_id=account_id,
        user_id=current_user.id,
        from_date=from_date,
        to_date=to_date,
    )


@router.get("/sessions", response_model=AnalyticsSessionsResponse)
def get_sessions(
    account_id: uuid.UUID = Query(...),
    from_date: date | None = Query(None),
    to_date: date | None = Query(None),
    db: Session = Depends(get_db),
    current_user: User = Depends(get_current_user),
) -> AnalyticsSessionsResponse:
    return analytics_service.get_sessions(
        db,
        account_id=account_id,
        user_id=current_user.id,
        from_date=from_date,
        to_date=to_date,
    )


@router.get("/instruments", response_model=AnalyticsInstrumentsResponse)
def get_instruments(
    account_id: uuid.UUID = Query(...),
    from_date: date | None = Query(None),
    to_date: date | None = Query(None),
    db: Session = Depends(get_db),
    current_user: User = Depends(get_current_user),
) -> AnalyticsInstrumentsResponse:
    return analytics_service.get_instruments(
        db,
        account_id=account_id,
        user_id=current_user.id,
        from_date=from_date,
        to_date=to_date,
    )


@router.get("/equity", response_model=AnalyticsEquityResponse)
def get_equity(
    account_id: uuid.UUID = Query(...),
    from_date: date | None = Query(None),
    to_date: date | None = Query(None),
    db: Session = Depends(get_db),
    current_user: User = Depends(get_current_user),
) -> AnalyticsEquityResponse:
    return analytics_service.get_equity(
        db,
        account_id=account_id,
        user_id=current_user.id,
        from_date=from_date,
        to_date=to_date,
    )


@router.get("/setups", response_model=AnalyticsSetupsResponse)
def get_setups(
    account_id: uuid.UUID = Query(...),
    from_date: date | None = Query(None),
    to_date: date | None = Query(None),
    db: Session = Depends(get_db),
    current_user: User = Depends(get_current_user),
) -> AnalyticsSetupsResponse:
    return analytics_service.get_setups(
        db,
        account_id=account_id,
        user_id=current_user.id,
        from_date=from_date,
        to_date=to_date,
    )


@router.get("/report", response_model=AnalyticsReportResponse)
def get_report(
    account_id: uuid.UUID = Query(...),
    from_date: date | None = Query(None),
    to_date: date | None = Query(None),
    db: Session = Depends(get_db),
    current_user: User = Depends(get_current_user),
) -> AnalyticsReportResponse:
    return analytics_service.get_report(
        db,
        account_id=account_id,
        user_id=current_user.id,
        from_date=from_date,
        to_date=to_date,
    )
