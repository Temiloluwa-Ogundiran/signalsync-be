import uuid
from datetime import datetime, timezone
from decimal import Decimal
from typing import Optional

from fastapi import HTTPException, status
from sqlalchemy.orm import Session

from app.domains.accounts import repository as account_repo
from app.domains.accounts.models import SyncProvider
from app.domains.journal import repository as journal_repo
from app.domains.journal.schemas import (
    JournalOpenPositionListResponse,
    JournalTradeListResponse,
    JournalTradeResponse,
)
from app.domains.users.models import User
from app.shared.utils.timezone import local_date_to_utc_range

from ._helpers import (
    _build_open_position_response,
    _build_trade_response,
    _estimate_starting_balance,
    _parse_optional_iso_datetime,
)


def list_account_trades(
    db: Session,
    *,
    current_user: User,
    account_id: uuid.UUID,
    from_date=None,
    to_date=None,
    symbol: Optional[str] = None,
    direction=None,
    session=None,
    limit: int = 50,
    cursor_trade_id: Optional[uuid.UUID] = None,
    include_manual: bool = True,
) -> list[JournalTradeResponse]:
    account = account_repo.get_account_by_id_for_user(db, account_id, current_user.id)
    if account is None:
        raise HTTPException(
            status_code=status.HTTP_404_NOT_FOUND, detail="Trading account not found."
        )

    closed_from_utc = None
    closed_to_utc_exclusive = None

    if from_date is not None:
        closed_from_utc, _ = local_date_to_utc_range(from_date, account.timezone)
    if to_date is not None:
        _, closed_to_utc_exclusive = local_date_to_utc_range(to_date, account.timezone)

    trades = account_repo.list_trades_by_account(
        db,
        account_id=account_id,
        closed_from_utc=closed_from_utc,
        closed_to_utc_exclusive=closed_to_utc_exclusive,
        symbol=symbol,
        direction=direction,
        session=session,
        limit=limit,
        cursor_trade_id=cursor_trade_id,
        include_manual=include_manual,
    )

    if not trades:
        return []

    base_models = [_build_trade_response(t, account_timezone=account.timezone) for t in trades]
    trade_ids = [t.id for t in trades]
    trade_reviewed_map = journal_repo.map_trade_reviewed_at_by_trade_ids(db, trade_ids=trade_ids)
    trade_rating_map = journal_repo.map_trade_journal_ratings_by_trade_ids(db, trade_ids=trade_ids)
    trade_assessments_map = journal_repo.map_trade_journal_assessments_by_trade_ids(db, trade_ids=trade_ids)

    for model in base_models:
        model.trade_reviewed_at = trade_reviewed_map.get(model.id)
        model.rating = trade_rating_map.get(model.id)
        assess = trade_assessments_map.get(model.id, {})
        model.execution_quality = assess.get("execution_quality")
        model.setup_quality = assess.get("setup_quality")
        model.discipline_score = assess.get("discipline_score")

    if closed_from_utc is None:
        return base_models

    starting_balance = _estimate_starting_balance(db, account=account)
    realized_before_window = account_repo.sum_trade_net_profit(
        db, account_id=account.id, closed_before_utc=closed_from_utc,
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

    for model in base_models:
        balance_before, roi = roi_by_trade_id.get(model.id, (None, None))
        model.balance_before_trade = balance_before
        model.net_roi_percent = roi

    return base_models


async def list_account_open_positions(
    db: Session,
    *,
    current_user: User,
    account_id: uuid.UUID,
    limit: int = 50,
) -> JournalOpenPositionListResponse:
    account = account_repo.get_account_by_id_for_user(db, account_id, current_user.id)
    if account is None:
        raise HTTPException(
            status_code=status.HTTP_404_NOT_FOUND, detail="Trading account not found."
        )

    if account.sync_provider != SyncProvider.headless_mt5:
        return JournalOpenPositionListResponse(as_of=None, items=[])

    from app.domains.accounts.mt5_core_client import (  # noqa: PLC0415
        Mt5CoreClient,
        Mt5CoreClientBackpressure,
        Mt5CoreClientError,
        Mt5CoreClientRateLimited,
        Mt5CoreClientTimeout,
    )
    from app.shared.utils.encryption import decrypt_secret  # noqa: PLC0415

    try:
        investor_password = decrypt_secret(account.encrypted_investor_password)
    except ValueError as exc:
        raise HTTPException(
            status_code=status.HTTP_500_INTERNAL_SERVER_ERROR,
            detail="Failed to decrypt account credentials.",
        ) from exc

    client = Mt5CoreClient()
    try:
        snapshot = await client.get_open_positions(
            credentials={
                "login": account.broker_login,
                "password": investor_password,
                "server": account.broker_server,
                "broker": account.broker_name,
            }
        )
    except Mt5CoreClientRateLimited as exc:
        raise HTTPException(
            status_code=status.HTTP_429_TOO_MANY_REQUESTS,
            detail={"code": exc.code, "message": str(exc)},
            headers={"Retry-After": str(exc.retry_after_seconds)} if exc.retry_after_seconds is not None else None,
        ) from exc
    except Mt5CoreClientBackpressure as exc:
        raise HTTPException(
            status_code=status.HTTP_503_SERVICE_UNAVAILABLE,
            detail={"code": exc.code, "message": str(exc)},
            headers={"Retry-After": str(exc.retry_after_seconds)} if exc.retry_after_seconds is not None else None,
        ) from exc
    except Mt5CoreClientTimeout as exc:
        raise HTTPException(
            status_code=status.HTTP_503_SERVICE_UNAVAILABLE,
            detail={"code": "MT5_CORE_TIMEOUT", "message": str(exc)},
        ) from exc
    except Mt5CoreClientError as exc:
        raise HTTPException(
            status_code=status.HTTP_502_BAD_GATEWAY,
            detail={"code": "MT5_CORE_ERROR", "message": str(exc)},
        ) from exc

    positions = [
        _build_open_position_response(item)
        for item in (snapshot.get("positions") or [])
        if str(item.get("position_id") or "").strip()
    ]
    positions.sort(
        key=lambda item: (
            item.opened_at or datetime.min.replace(tzinfo=timezone.utc),
            item.position_id,
        ),
        reverse=True,
    )
    as_of = _parse_optional_iso_datetime(snapshot.get("as_of"))
    return JournalOpenPositionListResponse(as_of=as_of, items=positions[:limit])
