import uuid
from datetime import datetime
from decimal import Decimal

from sqlalchemy import func, select, text
from sqlalchemy.orm import Session

from app.models.account_snapshot import AccountSnapshot
from app.models.trade import Trade
from app.repositories import trade_repo


def list_trades_filtered(
    db: Session,
    *,
    account_id: uuid.UUID,
    closed_from_utc: datetime | None,
    closed_to_utc_exclusive: datetime | None,
) -> list[Trade]:
    stmt = select(Trade).where(Trade.account_id == account_id)

    if closed_from_utc is not None:
        stmt = stmt.where(Trade.closed_at >= closed_from_utc)
    if closed_to_utc_exclusive is not None:
        stmt = stmt.where(Trade.closed_at < closed_to_utc_exclusive)

    stmt = stmt.order_by(Trade.closed_at.asc(), Trade.id.asc())
    return list(db.execute(stmt).scalars().all())


def list_daily_pnl(
    db: Session,
    *,
    account_id: uuid.UUID,
    account_timezone: str,
    closed_from_utc: datetime | None,
    closed_to_utc_exclusive: datetime | None,
) -> list[tuple]:
    local_date_expr = func.date(func.timezone(account_timezone, Trade.closed_at))

    stmt = (
        select(
            local_date_expr.label("trading_date"),
            func.count(Trade.id).label("trade_count"),
            func.sum(Trade.net_profit).label("total_pnl"),
            func.count(Trade.id).filter(Trade.net_profit > 0).label("win_count"),
            func.count(Trade.id).filter(Trade.net_profit < 0).label("loss_count"),
        )
        .where(Trade.account_id == account_id)
        .group_by(local_date_expr)
        .order_by(local_date_expr)
    )

    if closed_from_utc is not None:
        stmt = stmt.where(Trade.closed_at >= closed_from_utc)
    if closed_to_utc_exclusive is not None:
        stmt = stmt.where(Trade.closed_at < closed_to_utc_exclusive)

    return list(db.execute(stmt).all())


def list_snapshots_filtered(
    db: Session,
    *,
    account_id: uuid.UUID,
    from_date,
    to_date,
) -> list[AccountSnapshot]:
    stmt = select(AccountSnapshot).where(AccountSnapshot.account_id == account_id)

    if from_date is not None:
        stmt = stmt.where(AccountSnapshot.snapshot_date >= from_date)
    if to_date is not None:
        stmt = stmt.where(AccountSnapshot.snapshot_date <= to_date)

    stmt = stmt.order_by(AccountSnapshot.snapshot_date.asc(), AccountSnapshot.id.asc())
    return list(db.execute(stmt).scalars().all())


def list_setups(
    db: Session,
    *,
    account_id: uuid.UUID,
    closed_from_utc: datetime | None,
    closed_to_utc_exclusive: datetime | None,
) -> list[tuple]:
    sql = """
    WITH tagged_trades AS (
        SELECT
            lower(unnest(jm.tags)) AS tag,
            t.id AS trade_id,
            t.net_profit AS net_profit
        FROM trades t
        JOIN trade_journals tj ON tj.trade_id = t.id
        JOIN journal_messages jm ON jm.trade_journal_id = tj.id
        WHERE t.account_id = :account_id
          AND cardinality(jm.tags) > 0
          AND (:closed_from_utc IS NULL OR t.closed_at >= :closed_from_utc)
          AND (:closed_to_utc_exclusive IS NULL OR t.closed_at < :closed_to_utc_exclusive)
    )
    SELECT
        tag,
        COUNT(DISTINCT trade_id) AS trade_count,
        COUNT(DISTINCT trade_id) FILTER (WHERE net_profit > 0) AS win_count,
        COALESCE(SUM(net_profit), 0) AS total_pnl
    FROM tagged_trades
    GROUP BY tag
    ORDER BY total_pnl DESC, tag ASC
    """

    return list(
        db.execute(
            text(sql),
            {
                "account_id": str(account_id),
                "closed_from_utc": closed_from_utc,
                "closed_to_utc_exclusive": closed_to_utc_exclusive,
            },
        ).all()
    )
