import uuid

from fastapi import HTTPException, status
from sqlalchemy.orm import Session

from app.domains.accounts import repository as account_repo
from app.domains.accounts.schemas import ManualTradeCreateRequest, ManualTradeUpdateRequest
from app.domains.journal import repository as journal_repo
from app.domains.journal.models import JournalMessageType
from app.domains.users.models import User

from ._journals import get_or_create_trade_journal


def create_manual_trade(
    db: Session,
    *,
    account_id: uuid.UUID,
    payload: ManualTradeCreateRequest,
    current_user: User,
):
    trade = account_repo.create_manual_trade(db, account_id=account_id, payload=payload)
    get_or_create_trade_journal(db, trade_id=trade.id, current_user=current_user)
    db.commit()
    db.refresh(trade)
    return trade


def update_manual_trade(
    db: Session,
    *,
    trade_id: uuid.UUID,
    payload: ManualTradeUpdateRequest,
    current_user: User,
):
    old_trade = account_repo.get_trade_by_id(db, trade_id)
    if not old_trade:
        raise HTTPException(status_code=status.HTTP_404_NOT_FOUND, detail="Trade not found.")

    try:
        trade = account_repo.update_manual_trade(db, trade_id=trade_id, user_id=current_user.id, payload=payload)
    except ValueError as e:
        raise HTTPException(status_code=status.HTTP_400_BAD_REQUEST, detail=str(e)) from e

    trade_journal = journal_repo.get_trade_journal_by_trade_id(db, trade_id)
    if trade_journal:
        messages = journal_repo.list_messages_by_trade_journal(db, trade_journal.id)
        system_msgs = [m for m in messages if m.message_type == JournalMessageType.system]
        if system_msgs:
            system_msgs[0].system_data = {
                "symbol": trade.symbol,
                "direction": trade.direction.value,
                "net_profit": str(trade.net_profit),
                "open_price": str(trade.open_price),
                "close_price": str(trade.close_price),
                "volume": str(trade.volume),
                "duration_seconds": trade.duration_seconds,
                "session": trade.session.value,
                "opened_at": trade.opened_at.isoformat(),
                "closed_at": trade.closed_at.isoformat(),
            }

    db.commit()
    db.refresh(trade)
    return trade


def delete_manual_trade(db: Session, *, trade_id: uuid.UUID, current_user: User):
    old_trade = account_repo.get_trade_by_id(db, trade_id)
    if not old_trade:
        raise HTTPException(status_code=status.HTTP_404_NOT_FOUND, detail="Trade not found.")

    try:
        account_repo.delete_manual_trade(db, trade_id=trade_id, user_id=current_user.id)
    except ValueError as e:
        raise HTTPException(status_code=status.HTTP_400_BAD_REQUEST, detail=str(e)) from e

    db.commit()
