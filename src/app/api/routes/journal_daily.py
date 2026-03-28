import uuid
from datetime import date
from typing import Optional

from fastapi import APIRouter, Depends, File, Form, Query, UploadFile
from sqlalchemy.orm import Session

from app.api.deps import get_current_user
from app.core.database import get_db
from app.models.user import User
from app.schemas.journal_daily import DailyJournalFeedItemResponse, DailyJournalFeedResponse, DailyJournalResponse
from app.models.journal_message import JournalMessageType
from app.schemas.journal_message import JournalMessageResponse
from app.services import journal_service

router = APIRouter(prefix="/journal/daily", tags=["journal-daily"])


@router.get("/{account_id}/{trading_date}", response_model=DailyJournalResponse)
def get_or_create_daily_journal(
    account_id: uuid.UUID,
    trading_date: date,
    db: Session = Depends(get_db),
    current_user: User = Depends(get_current_user),
) -> DailyJournalResponse:
    return journal_service.get_or_create_daily_journal(
        db,
        account_id=account_id,
        trading_date=trading_date,
        current_user=current_user,
    )


@router.post("/{daily_journal_id}/messages", response_model=JournalMessageResponse)
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


@router.get("/{account_id}/feed", response_model=DailyJournalFeedResponse)
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
        items=[DailyJournalFeedItemResponse(id=item.id, trading_date=item.trading_date) for item in items],
        next_cursor=items[-1].id if has_more else None,
    )
