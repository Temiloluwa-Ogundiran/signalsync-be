import uuid
from datetime import date
from typing import Optional

from fastapi import APIRouter, Depends, File, Form, Query, UploadFile
from sqlalchemy.orm import Session

from app.api.deps import get_current_user
from app.core.database import get_db
from app.models.trade import TradeDirection, TradeSession
from app.models.user import User
from app.models.journal_message import JournalMessageType
from app.schemas.journal_message import JournalMessageResponse
from app.schemas.journal_trade import JournalTradeListResponse, JournalTradeResponse
from app.services import journal_service, journal_trade_service

router = APIRouter(prefix="/journal/trades", tags=["journal-trades"])


@router.get("", response_model=JournalTradeListResponse)
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
    trades = journal_trade_service.list_account_trades(
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


@router.get("/{trade_id}/journal", response_model=list[JournalMessageResponse])
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


@router.post("/{trade_id}/messages", response_model=JournalMessageResponse)
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
