import uuid
from datetime import date
from typing import Optional

from fastapi import HTTPException, status
from sqlalchemy.orm import Session

from app.models.trade import Trade, TradeDirection, TradeSession
from app.models.user import User
from app.repositories import trade_repo, trading_account_repo
from app.utils.timezone import local_date_to_utc_range


def list_account_trades(
    db: Session,
    *,
    current_user: User,
    account_id: uuid.UUID,
    from_date: Optional[date] = None,
    to_date: Optional[date] = None,
    symbol: Optional[str] = None,
    direction: Optional[TradeDirection] = None,
    session: Optional[TradeSession] = None,
    limit: int = 50,
    cursor_trade_id: Optional[uuid.UUID] = None,
) -> list[Trade]:
    account = trading_account_repo.get_by_id_for_user(db, account_id, current_user.id)
    if account is None:
        raise HTTPException(status_code=status.HTTP_404_NOT_FOUND, detail="Trading account not found.")

    closed_from_utc = None
    closed_to_utc_exclusive = None

    if from_date is not None:
        closed_from_utc, _ = local_date_to_utc_range(from_date, account.timezone)

    if to_date is not None:
        _, to_date_end_exclusive = local_date_to_utc_range(to_date, account.timezone)
        closed_to_utc_exclusive = to_date_end_exclusive

    return trade_repo.list_by_account(
        db,
        account_id=account_id,
        closed_from_utc=closed_from_utc,
        closed_to_utc_exclusive=closed_to_utc_exclusive,
        symbol=symbol,
        direction=direction,
        session=session,
        limit=limit,
        cursor_trade_id=cursor_trade_id,
    )
