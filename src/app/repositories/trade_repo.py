import uuid
from datetime import date, datetime
from decimal import Decimal
from typing import Optional

from sqlalchemy import Date, cast, func, select
from sqlalchemy import delete as sa_delete
from sqlalchemy import update as sa_update
from sqlalchemy.dialects.postgresql import insert as pg_insert
from sqlalchemy.orm import Session

from app.models.trade import Trade, TradeDirection, TradeSession


def get_by_id(db: Session, trade_id: uuid.UUID) -> Optional[Trade]:
    stmt = select(Trade).where(Trade.id == trade_id)
    return db.execute(stmt).scalar_one_or_none()


def upsert_closed_trade(
    db: Session,
    *,
    account_id: uuid.UUID,
    broker_trade_id: str,
    symbol: str,
    direction: TradeDirection,
    open_price: Decimal,
    close_price: Decimal,
    volume: Decimal,
    profit: Decimal,
    commission: Decimal,
    swap: Decimal,
    net_profit: Decimal,
    duration_seconds: int,
    session: TradeSession,
    opened_at: datetime,
    closed_at: datetime,
) -> bool:
    stmt = (
        pg_insert(Trade)
        .values(
            account_id=account_id,
            broker_trade_id=broker_trade_id,
            symbol=symbol,
            direction=direction,
            open_price=open_price,
            close_price=close_price,
            volume=volume,
            profit=profit,
            commission=commission,
            swap=swap,
            net_profit=net_profit,
            duration_seconds=duration_seconds,
            session=session,
            opened_at=opened_at,
            closed_at=closed_at,
        )
        .on_conflict_do_nothing(index_elements=["account_id", "broker_trade_id"])
        .returning(Trade.id)
    )

    inserted_id = db.execute(stmt).scalar_one_or_none()
    return inserted_id is not None


def list_by_account(
    db: Session,
    *,
    account_id: uuid.UUID,
    closed_from_utc: Optional[datetime] = None,
    closed_to_utc_exclusive: Optional[datetime] = None,
    symbol: Optional[str] = None,
    direction: Optional[TradeDirection] = None,
    session: Optional[TradeSession] = None,
    limit: int = 50,
    cursor_trade_id: Optional[uuid.UUID] = None,
) -> list[Trade]:
    stmt = (
        select(Trade)
        .where(Trade.account_id == account_id)
        .order_by(Trade.closed_at.desc(), Trade.id.desc())
        .limit(limit)
    )

    if closed_from_utc is not None:
        stmt = stmt.where(Trade.closed_at >= closed_from_utc)
    if closed_to_utc_exclusive is not None:
        stmt = stmt.where(Trade.closed_at < closed_to_utc_exclusive)
    if symbol:
        stmt = stmt.where(Trade.symbol == symbol)
    if direction is not None:
        stmt = stmt.where(Trade.direction == direction)
    if session is not None:
        stmt = stmt.where(Trade.session == session)

    if cursor_trade_id is not None:
        cursor_stmt = select(Trade.closed_at, Trade.id).where(
            Trade.id == cursor_trade_id,
            Trade.account_id == account_id,
        )
        cursor_row = db.execute(cursor_stmt).one_or_none()
        if cursor_row is not None:
            cursor_closed_at, cursor_id = cursor_row
            stmt = stmt.where(
                (Trade.closed_at < cursor_closed_at)
                | ((Trade.closed_at == cursor_closed_at) & (Trade.id < cursor_id))
            )

    return list(db.execute(stmt).scalars().all())


def update_closed_trade(
    db: Session,
    *,
    account_id: uuid.UUID,
    broker_trade_id: str,
    symbol: str,
    direction: TradeDirection,
    open_price: Decimal,
    close_price: Decimal,
    volume: Decimal,
    profit: Decimal,
    commission: Decimal,
    swap: Decimal,
    net_profit: Decimal,
    duration_seconds: int,
    session: TradeSession,
    opened_at: datetime,
    closed_at: datetime,
) -> bool:
    stmt = (
        sa_update(Trade)
        .where(
            Trade.account_id == account_id,
            Trade.broker_trade_id == broker_trade_id,
        )
        .values(
            symbol=symbol,
            direction=direction,
            open_price=open_price,
            close_price=close_price,
            volume=volume,
            profit=profit,
            commission=commission,
            swap=swap,
            net_profit=net_profit,
            duration_seconds=duration_seconds,
            session=session,
            opened_at=opened_at,
            closed_at=closed_at,
        )
    )
    updated = db.execute(stmt)
    return bool(updated.rowcount)


def list_by_account_local_date(
    db: Session,
    *,
    account_id: uuid.UUID,
    trading_date: date,
    account_timezone: str,
) -> list[Trade]:
    stmt = (
        select(Trade)
        .where(
            Trade.account_id == account_id,
            cast(func.timezone(account_timezone, Trade.closed_at), Date) == trading_date,
        )
        .order_by(Trade.closed_at.asc(), Trade.id.asc())
    )
    return list(db.execute(stmt).scalars().all())


def sum_net_profit(
    db: Session,
    *,
    account_id: uuid.UUID,
    closed_before_utc: Optional[datetime] = None,
) -> Decimal:
    stmt = select(func.coalesce(func.sum(Trade.net_profit), Decimal("0"))).where(Trade.account_id == account_id)
    if closed_before_utc is not None:
        stmt = stmt.where(Trade.closed_at < closed_before_utc)

    value = db.execute(stmt).scalar_one()
    if isinstance(value, Decimal):
        return value
    return Decimal(str(value or 0))


def delete_trades_outside_valid_broker_ids_in_window(
    db: Session,
    *,
    account_id: uuid.UUID,
    closed_from_utc: Optional[datetime],
    closed_to_utc_exclusive: Optional[datetime],
    account_timezone: str,
    valid_broker_trade_ids: set[str],
) -> tuple[int, set[date]]:
    local_date_expr = cast(func.timezone(account_timezone, Trade.closed_at), Date)

    id_stmt = select(Trade.id).where(Trade.account_id == account_id)
    if closed_from_utc is not None:
        id_stmt = id_stmt.where(Trade.closed_at >= closed_from_utc)
    if closed_to_utc_exclusive is not None:
        id_stmt = id_stmt.where(Trade.closed_at < closed_to_utc_exclusive)

    if valid_broker_trade_ids:
        id_stmt = id_stmt.where(Trade.broker_trade_id.not_in(sorted(valid_broker_trade_ids)))

    ids_to_delete = list(db.execute(id_stmt).scalars().all())
    if not ids_to_delete:
        return 0, set()

    dates_stmt = select(local_date_expr).where(Trade.id.in_(ids_to_delete)).distinct()
    affected_dates = {row[0] for row in db.execute(dates_stmt).all() if row[0] is not None}

    deleted = db.execute(sa_delete(Trade).where(Trade.id.in_(ids_to_delete)))
    return int(deleted.rowcount or 0), affected_dates
