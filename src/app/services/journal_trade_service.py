import uuid
from datetime import date
from decimal import Decimal
from typing import Optional

from fastapi import HTTPException, status
from sqlalchemy.orm import Session

from app.models.trade import TradeDirection, TradeSession
from app.models.user import User
from app.repositories import trade_repo, trading_account_repo
from app.schemas.journal_trade import JournalTradeResponse
from app.services.metaapi_service import metaapi_service
from app.utils.timezone import local_date_to_utc_range


def _estimate_starting_balance(db: Session, *, account_id: uuid.UUID, meta_account_id: str) -> Decimal:
    try:
        account_info = metaapi_service.get_account_info(meta_account_id)
        current_balance = Decimal(str(account_info.get("balance") or 0))
        all_time_realized = trade_repo.sum_net_profit(db, account_id=account_id)
        return current_balance - all_time_realized
    except Exception:  # noqa: BLE001
        return Decimal("0")


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
) -> list[JournalTradeResponse]:
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

    trades = trade_repo.list_by_account(
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

    if not trades:
        return []

    base_models = [JournalTradeResponse.model_validate(trade) for trade in trades]
    if closed_from_utc is None:
        return base_models

    starting_balance = _estimate_starting_balance(
        db,
        account_id=account.id,
        meta_account_id=account.meta_account_id,
    )
    realized_before_window = trade_repo.sum_net_profit(
        db,
        account_id=account.id,
        closed_before_utc=closed_from_utc,
    )
    running_balance = starting_balance + realized_before_window

    roi_by_trade_id: dict[uuid.UUID, tuple[Decimal, Decimal]] = {}
    for trade in sorted(trades, key=lambda item: (item.closed_at, item.id)):
        balance_before_trade = running_balance
        net_roi_percent = Decimal("0")
        if balance_before_trade != 0:
            net_roi_percent = (trade.net_profit / balance_before_trade) * Decimal("100")

        roi_by_trade_id[trade.id] = (balance_before_trade, net_roi_percent)
        running_balance += trade.net_profit

    response_items: list[JournalTradeResponse] = []
    for item in base_models:
        enriched = item.model_copy()
        balance_before_trade, net_roi_percent = roi_by_trade_id[item.id]
        enriched.balance_before_trade = balance_before_trade
        enriched.net_roi_percent = net_roi_percent
        response_items.append(enriched)

    return response_items
