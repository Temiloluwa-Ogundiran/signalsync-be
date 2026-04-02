import uuid
from datetime import date, datetime
from decimal import Decimal
from typing import Optional

from sqlalchemy import Date, cast, func, select
from sqlalchemy import delete as sa_delete
from sqlalchemy import text
from sqlalchemy import update as sa_update
from sqlalchemy.dialects.postgresql import insert as pg_insert
from sqlalchemy.orm import Session

from app.domains.accounts.models import (
    Trade,
    TradeDirection,
    TradeSession,
    TradeSource,
    TradingAccount,
    TradingAccountStatus,
)

# ---------------------------------------------------------------------------
# TradingAccount
# ---------------------------------------------------------------------------

def create_account(
    db: Session,
    *,
    user_id: uuid.UUID,
    meta_account_id: str,
    broker_name: str,
    broker_login: str,
    broker_server: str,
    encrypted_investor_password: str,
    encrypted_trader_password: Optional[str],
    account_type,
    platform,
    currency: str,
    timezone: str,
    broker_utc_offset: int,
    display_name: Optional[str],
) -> TradingAccount:
    account = TradingAccount(
        user_id=user_id,
        meta_account_id=meta_account_id,
        broker_name=broker_name,
        broker_login=broker_login,
        broker_server=broker_server,
        encrypted_investor_password=encrypted_investor_password,
        encrypted_trader_password=encrypted_trader_password,
        account_type=account_type,
        platform=platform,
        currency=currency,
        timezone=timezone,
        broker_utc_offset=broker_utc_offset,
        display_name=display_name,
        status=TradingAccountStatus.pending_sync,
    )
    db.add(account)
    db.flush()
    return account


def reactivate_account(
    db: Session,
    *,
    account: TradingAccount,
    meta_account_id: str,
    broker_name: str,
    broker_login: str,
    broker_server: str,
    encrypted_investor_password: str,
    encrypted_trader_password: Optional[str],
    account_type,
    platform,
    currency: str,
    timezone: str,
    broker_utc_offset: int,
    display_name: Optional[str],
) -> TradingAccount:
    account.meta_account_id = meta_account_id
    account.broker_name = broker_name
    account.broker_login = broker_login
    account.broker_server = broker_server
    account.encrypted_investor_password = encrypted_investor_password
    account.encrypted_trader_password = encrypted_trader_password
    account.account_type = account_type
    account.platform = platform
    account.currency = currency
    account.timezone = timezone
    account.broker_utc_offset = broker_utc_offset
    account.display_name = display_name
    account.is_deleted = False
    account.status = TradingAccountStatus.pending_sync
    account.sync_error_message = None
    db.flush()
    return account


def get_account_by_id(db: Session, account_id: uuid.UUID) -> Optional[TradingAccount]:
    stmt = select(TradingAccount).where(
        TradingAccount.id == account_id,
        TradingAccount.is_deleted.is_(False),
    )
    return db.execute(stmt).scalar_one_or_none()


def get_account_by_id_for_user(
    db: Session, account_id: uuid.UUID, user_id: uuid.UUID
) -> Optional[TradingAccount]:
    stmt = select(TradingAccount).where(
        TradingAccount.id == account_id,
        TradingAccount.user_id == user_id,
        TradingAccount.is_deleted.is_(False),
    )
    return db.execute(stmt).scalar_one_or_none()


def get_account_by_user_and_meta_id(
    db: Session,
    *,
    user_id: uuid.UUID,
    meta_account_id: str,
) -> Optional[TradingAccount]:
    stmt = select(TradingAccount).where(
        TradingAccount.user_id == user_id,
        TradingAccount.meta_account_id == meta_account_id,
    )
    return db.execute(stmt).scalar_one_or_none()


def list_accounts_for_user(db: Session, user_id: uuid.UUID) -> list[TradingAccount]:
    stmt = (
        select(TradingAccount)
        .where(
            TradingAccount.user_id == user_id,
            TradingAccount.is_deleted.is_(False),
        )
        .order_by(TradingAccount.created_at.desc())
    )
    return list(db.execute(stmt).scalars().all())


def list_syncable_accounts(db: Session) -> list[TradingAccount]:
    stmt = select(TradingAccount).where(
        TradingAccount.is_deleted.is_(False),
        TradingAccount.status.in_(
            [
                TradingAccountStatus.pending_sync,
                TradingAccountStatus.synced,
                TradingAccountStatus.error,
            ]
        ),
    )
    return list(db.execute(stmt).scalars().all())


def set_account_last_synced_at(
    db: Session, account: TradingAccount, synced_at: datetime
) -> None:
    account.last_synced_at = synced_at
    account.status = TradingAccountStatus.synced
    account.sync_error_message = None
    db.flush()


def set_account_sync_error(
    db: Session, account: TradingAccount, message: str
) -> None:
    account.status = TradingAccountStatus.error
    account.sync_error_message = message
    db.flush()


def set_account_sync_warning(
    db: Session, account: TradingAccount, message: str
) -> None:
    account.sync_error_message = message
    db.flush()


def soft_disconnect_account(db: Session, account: TradingAccount) -> None:
    account.status = TradingAccountStatus.disconnected
    account.is_deleted = True
    db.flush()


# ---------------------------------------------------------------------------
# Sync lock (advisory lock to prevent overlapping sync cycles)
# ---------------------------------------------------------------------------

_JOURNAL_SYNC_CYCLE_LOCK_KEY = 91324051


def try_acquire_cycle_lock(db: Session) -> bool:
    stmt = text("SELECT pg_try_advisory_lock(:lock_key)")
    return bool(db.execute(stmt, {"lock_key": _JOURNAL_SYNC_CYCLE_LOCK_KEY}).scalar())


def release_cycle_lock(db: Session) -> None:
    stmt = text("SELECT pg_advisory_unlock(:lock_key)")
    db.execute(stmt, {"lock_key": _JOURNAL_SYNC_CYCLE_LOCK_KEY})


# ---------------------------------------------------------------------------
# Trade
# ---------------------------------------------------------------------------

def get_trade_by_id(db: Session, trade_id: uuid.UUID) -> Optional[Trade]:
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
    # MT5-enriched fields (optional — None for MetaAPI-sourced trades)
    sl: Optional[Decimal] = None,
    tp: Optional[Decimal] = None,
    magic_number: Optional[int] = None,
    position_id: Optional[str] = None,
    trade_source: Optional[TradeSource] = None,
    mfe: Optional[Decimal] = None,
    mae: Optional[Decimal] = None,
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
            sl=sl,
            tp=tp,
            magic_number=magic_number,
            position_id=position_id,
            trade_source=trade_source,
            mfe=mfe,
            mae=mae,
        )
        .on_conflict_do_nothing(index_elements=["account_id", "broker_trade_id"])
        .returning(Trade.id)
    )
    inserted_id = db.execute(stmt).scalar_one_or_none()
    return inserted_id is not None


def list_trades_by_account(
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
    # MT5-enriched fields (optional — None for MetaAPI-sourced trades)
    sl: Optional[Decimal] = None,
    tp: Optional[Decimal] = None,
    magic_number: Optional[int] = None,
    position_id: Optional[str] = None,
    trade_source: Optional[TradeSource] = None,
    mfe: Optional[Decimal] = None,
    mae: Optional[Decimal] = None,
) -> bool:
    update_values: dict = dict(
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
    # Only overwrite MT5 enrichment fields if non-None values are provided,
    # so a MetaAPI re-sync won't wipe out existing MT5 enrichment data.
    if sl is not None:
        update_values["sl"] = sl
    if tp is not None:
        update_values["tp"] = tp
    if magic_number is not None:
        update_values["magic_number"] = magic_number
    if position_id is not None:
        update_values["position_id"] = position_id
    if trade_source is not None:
        update_values["trade_source"] = trade_source
    if mfe is not None:
        update_values["mfe"] = mfe
    if mae is not None:
        update_values["mae"] = mae

    stmt = (
        sa_update(Trade)
        .where(
            Trade.account_id == account_id,
            Trade.broker_trade_id == broker_trade_id,
        )
        .values(**update_values)
    )
    updated = db.execute(stmt)
    return bool(updated.rowcount)


def list_trades_by_account_local_date(
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


def sum_trade_net_profit(
    db: Session,
    *,
    account_id: uuid.UUID,
    closed_before_utc: Optional[datetime] = None,
) -> Decimal:
    stmt = select(func.coalesce(func.sum(Trade.net_profit), Decimal("0"))).where(
        Trade.account_id == account_id
    )
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


# ---------------------------------------------------------------------------
# DailyStats
# ---------------------------------------------------------------------------

def delete_daily_stats_for_date(
    db: Session,
    *,
    account_id: uuid.UUID,
    trading_date: date,
) -> int:
    stmt = text(
        """
        DELETE FROM daily_stats
        WHERE account_id = :account_id
          AND trading_date = :trading_date
        """
    )
    result = db.execute(
        stmt,
        {
            "account_id": str(account_id),
            "trading_date": trading_date,
        },
    )
    return int(result.rowcount or 0)


def rebuild_daily_stats_for_date(
    db: Session,
    *,
    account_id: uuid.UUID,
    trading_date: date,
    account_timezone: str,
) -> None:
    stmt = text(
        """
        INSERT INTO daily_stats (
            id,
            account_id,
            trading_date,
            trade_count,
            win_count,
            loss_count,
            total_pnl,
            total_commission,
            gross_win,
            gross_loss,
            best_trade_id,
            worst_trade_id,
            created_at
        )
        SELECT
            gen_random_uuid(),
            :account_id,
            :trading_date,
            COUNT(*)::int,
            COUNT(*) FILTER (WHERE net_profit > 0)::int,
            COUNT(*) FILTER (WHERE net_profit < 0)::int,
            COALESCE(SUM(net_profit), 0),
            COALESCE(SUM(commission), 0),
            COALESCE(SUM(net_profit) FILTER (WHERE net_profit > 0), 0),
            COALESCE(SUM(net_profit) FILTER (WHERE net_profit < 0), 0),
            (
                SELECT t1.id
                FROM trades t1
                WHERE t1.account_id = :account_id
                  AND DATE(timezone(:account_timezone, t1.closed_at)) = :trading_date
                ORDER BY t1.net_profit DESC
                LIMIT 1
            ),
            (
                SELECT t2.id
                FROM trades t2
                WHERE t2.account_id = :account_id
                  AND DATE(timezone(:account_timezone, t2.closed_at)) = :trading_date
                ORDER BY t2.net_profit ASC
                LIMIT 1
            ),
            now()
        FROM trades t
        WHERE t.account_id = :account_id
          AND DATE(timezone(:account_timezone, t.closed_at)) = :trading_date
        ON CONFLICT (account_id, trading_date) DO UPDATE
        SET
            trade_count = EXCLUDED.trade_count,
            win_count = EXCLUDED.win_count,
            loss_count = EXCLUDED.loss_count,
            total_pnl = EXCLUDED.total_pnl,
            total_commission = EXCLUDED.total_commission,
            gross_win = EXCLUDED.gross_win,
            gross_loss = EXCLUDED.gross_loss,
            best_trade_id = EXCLUDED.best_trade_id,
            worst_trade_id = EXCLUDED.worst_trade_id
        """
    )
    db.execute(
        stmt,
        {
            "account_id": str(account_id),
            "trading_date": trading_date,
            "account_timezone": account_timezone,
        },
    )
