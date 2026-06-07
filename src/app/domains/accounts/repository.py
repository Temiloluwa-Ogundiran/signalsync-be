import uuid
from datetime import date, datetime, timedelta, timezone
from decimal import Decimal
from typing import Optional

from sqlalchemy import Date, cast, func, select
from sqlalchemy import delete as sa_delete
from sqlalchemy import text
from sqlalchemy import update as sa_update
from sqlalchemy.dialects.postgresql import insert as pg_insert
from sqlalchemy.orm import Session

from app.domains.accounts.models import (
    AccountSnapshot,
    SyncProvider,
    Trade,
    TradeDirection,
    TradeSession,
    TradeSource,
    TradingAccount,
    TradingAccountConnectionState,
    TradingAccountStatus,
)
from app.domains.users.models import User
from app.shared.utils.timezone import classify_session

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
    sync_provider: SyncProvider = SyncProvider.headless_mt5,
    id: Optional[uuid.UUID] = None,
) -> TradingAccount:
    account = TradingAccount(
        id=id or uuid.uuid4(),
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
        connection_state=TradingAccountConnectionState.pending_verification,
        is_data_ready_for_stats=False,
        last_bootstrap_synced_at=None,
        bootstrap_error_message=None,
        sync_provider=sync_provider,
    )
    db.add(account)
    db.flush()
    return account


def create_csv_account(
    db: Session,
    *,
    user_id: uuid.UUID,
    account_number: str,
    broker_name: str,
    broker_server: str,
    platform: str,
    currency: str,
    timezone: str,
    display_name: Optional[str],
    account_type: str,
) -> TradingAccount:
    """Create a TradingAccount for CSV imports (no API credentials)."""
    meta_account_id = f"csv:{platform}:{broker_server}:{account_number}"
    
    # Check if the account was previously deleted (is_deleted=True) or already exists
    stmt = select(TradingAccount).where(
        TradingAccount.user_id == user_id,
        TradingAccount.meta_account_id == meta_account_id
    )
    account = db.execute(stmt).scalar_one_or_none()
    
    if account:
        account.is_deleted = False
        account.display_name = display_name or account.display_name
        account.broker_name = broker_name
        account.broker_server = broker_server
        account.account_type = account_type
        account.platform = platform
        account.currency = currency
        account.timezone = timezone
        account.status = TradingAccountStatus.synced
        account.connection_state = TradingAccountConnectionState.ready
        account.is_data_ready_for_stats = True
        db.flush()
        return account

    account = TradingAccount(
        user_id=user_id,
        meta_account_id=meta_account_id,
        broker_name=broker_name,
        broker_login=account_number,
        broker_server=broker_server,
        encrypted_investor_password="csv_import_no_password",  # Placeholder
        account_type=account_type,
        platform=platform,
        currency=currency,
        timezone=timezone,
        display_name=display_name,
        sync_provider=SyncProvider.csv_import,
        status=TradingAccountStatus.synced,
        connection_state=TradingAccountConnectionState.ready,
        is_data_ready_for_stats=True,
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
    sync_provider: SyncProvider = SyncProvider.headless_mt5,
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
    account.connection_state = TradingAccountConnectionState.pending_verification
    account.is_data_ready_for_stats = False
    account.sync_error_message = None
    account.last_bootstrap_synced_at = None
    account.bootstrap_error_message = None
    account.sync_provider = sync_provider
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
        TradingAccount.sync_provider == SyncProvider.headless_mt5,
        TradingAccount.status.in_(
            [
                TradingAccountStatus.pending_sync,
                TradingAccountStatus.synced,
                TradingAccountStatus.error,
            ]
        ),
    )
    return list(db.execute(stmt).scalars().all())


def list_active_mt5_sync_candidates(
    db: Session,
    *,
    active_after: datetime,
    now: datetime,
) -> list[TradingAccount]:
    stmt = (
        select(TradingAccount)
        .join(User, User.id == TradingAccount.user_id)
        .where(
            TradingAccount.is_deleted.is_(False),
            TradingAccount.sync_provider == SyncProvider.headless_mt5,
            TradingAccount.connection_state.in_(
                [
                    TradingAccountConnectionState.ready,
                    TradingAccountConnectionState.bootstrap_failed,
                ]
            ),
            TradingAccount.status.in_(
                [
                    TradingAccountStatus.pending_sync,
                    TradingAccountStatus.synced,
                    TradingAccountStatus.error,
                ]
            ),
            User.last_active_at.is_not(None),
            User.last_active_at >= active_after,
            (
                TradingAccount.next_sync_not_before.is_(None)
                | (TradingAccount.next_sync_not_before <= now)
            ),
        )
        .order_by(
            TradingAccount.last_synced_at.asc().nullsfirst(),
            TradingAccount.created_at.asc(),
        )
    )
    return list(db.execute(stmt).scalars().all())


def set_account_last_synced_at(
    db: Session, account: TradingAccount, synced_at: datetime
) -> None:
    account.last_synced_at = synced_at
    account.status = TradingAccountStatus.synced
    account.sync_error_message = None
    db.flush()


def set_sync_attempt_started(
    db: Session,
    *,
    account: TradingAccount,
    attempted_at: datetime,
) -> None:
    account.last_sync_attempted_at = attempted_at
    db.flush()


def mark_sync_success(
    db: Session,
    *,
    account: TradingAccount,
    synced_at: datetime,
    next_sync_not_before: Optional[datetime] = None,
) -> None:
    account.last_synced_at = synced_at
    account.last_sync_attempted_at = synced_at
    account.next_sync_not_before = next_sync_not_before
    account.last_sync_outcome = "success"
    account.consecutive_sync_failures = 0
    account.status = TradingAccountStatus.synced
    account.sync_error_message = None
    db.flush()


def mark_sync_retryable(
    db: Session,
    *,
    account: TradingAccount,
    outcome: str,
    message: str,
    retry_after_seconds: int | None,
    attempted_at: datetime,
) -> None:
    account.last_sync_attempted_at = attempted_at
    account.last_sync_outcome = outcome
    account.consecutive_sync_failures = (account.consecutive_sync_failures or 0) + 1
    delay_seconds = retry_after_seconds if retry_after_seconds is not None else 60
    account.next_sync_not_before = attempted_at + timedelta(seconds=delay_seconds)
    account.sync_error_message = message
    db.flush()


def mark_sync_attention_required(
    db: Session,
    *,
    account: TradingAccount,
    outcome: str,
    message: str,
    attempted_at: datetime,
) -> None:
    account.last_sync_attempted_at = attempted_at
    account.last_sync_outcome = outcome
    account.sync_error_message = message
    db.flush()


def set_account_sync_error(
    db: Session, account: TradingAccount, message: str
) -> None:
    account.status = TradingAccountStatus.error
    account.sync_error_message = message
    db.flush()


def list_recent_sync_attempts_for_user(
    db: Session,
    *,
    user_id: uuid.UUID,
    since: datetime,
) -> list[datetime]:
    stmt = (
        select(TradingAccount.last_sync_attempted_at)
        .where(
            TradingAccount.user_id == user_id,
            TradingAccount.is_deleted.is_(False),
            TradingAccount.last_sync_attempted_at.is_not(None),
            TradingAccount.last_sync_attempted_at >= since,
        )
        .order_by(TradingAccount.last_sync_attempted_at.asc())
    )
    return [
        attempted_at
        for attempted_at in db.execute(stmt).scalars().all()
        if attempted_at is not None
    ]


def set_account_sync_warning(
    db: Session, account: TradingAccount, message: str
) -> None:
    account.sync_error_message = message
    db.flush()


def mark_account_bootstrapping(db: Session, account: TradingAccount) -> None:
    account.connection_state = TradingAccountConnectionState.bootstrapping
    account.is_data_ready_for_stats = False
    account.bootstrap_error_message = None
    db.flush()


def mark_account_ready_for_stats(
    db: Session, account: TradingAccount, synced_at: datetime
) -> None:
    account.connection_state = TradingAccountConnectionState.ready
    account.is_data_ready_for_stats = True
    account.last_bootstrap_synced_at = synced_at
    account.bootstrap_error_message = None
    db.flush()


def mark_account_verification_failed(
    db: Session, account: TradingAccount, message: str
) -> None:
    account.connection_state = TradingAccountConnectionState.verification_failed
    account.is_data_ready_for_stats = False
    account.bootstrap_error_message = message
    db.flush()


def mark_account_bootstrap_failed(
    db: Session, account: TradingAccount, message: str
) -> None:
    account.connection_state = TradingAccountConnectionState.bootstrap_failed
    account.is_data_ready_for_stats = False
    account.bootstrap_error_message = message
    db.flush()


def mark_account_pending_verification(db: Session, account: TradingAccount) -> None:
    account.connection_state = TradingAccountConnectionState.pending_verification
    account.is_data_ready_for_stats = False
    account.bootstrap_error_message = None
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


def try_acquire_account_sync_lock(db: Session, account_id: uuid.UUID) -> bool:
    stmt = text("SELECT pg_try_advisory_lock(hashtext(:lock_key))")
    return bool(
        db.execute(
            stmt,
            {"lock_key": f"trading-account-sync:{account_id}"},
        ).scalar()
    )


def release_account_sync_lock(db: Session, account_id: uuid.UUID) -> None:
    stmt = text("SELECT pg_advisory_unlock(hashtext(:lock_key))")
    db.execute(
        stmt,
        {"lock_key": f"trading-account-sync:{account_id}"},
    )


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
    # MT5-enriched fields (optional — None for trades without MT5 metadata)
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
    include_manual: bool = True,
) -> list[Trade]:
    stmt = (
        select(Trade)
        .where(Trade.account_id == account_id)
        .order_by(Trade.closed_at.desc(), Trade.id.desc())
        .limit(limit)
    )

    if not include_manual:
        stmt = stmt.where(Trade.is_manual.is_(False))
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
    # MT5-enriched fields (optional — None for trades without MT5 metadata)
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
    # so partial updates do not wipe out existing MT5 enrichment data.
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
    include_manual: bool = True,
) -> list[Trade]:
    stmt = (
        select(Trade)
        .where(
            Trade.account_id == account_id,
            cast(func.timezone(account_timezone, Trade.closed_at), Date) == trading_date,
        )
    )
    if not include_manual:
        stmt = stmt.where(Trade.is_manual.is_(False))
    stmt = stmt.order_by(Trade.closed_at.asc(), Trade.id.asc())
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
# Manual Trades
# ---------------------------------------------------------------------------

def create_manual_trade(
    db: Session,
    *,
    account_id: uuid.UUID,
    payload,  # ManualTradeCreateRequest
) -> Trade:
    # Generate unique broker_trade_id
    broker_trade_id = f"manual-{uuid.uuid4()}"
    
    # Session classification: auto-derived from opened_at UTC time
    opened_at_utc = payload.opened_at if payload.opened_at.tzinfo else payload.opened_at.replace(tzinfo=timezone.utc)
    session_value = TradeSession(classify_session(opened_at_utc))
    
    if payload.is_missed:
        closed_at_utc = opened_at_utc
        duration_seconds = 0
        close_price = payload.tp or payload.open_price
        net_profit = Decimal("0")
        commission = Decimal("0")
        swap = Decimal("0")
        profit = Decimal("0")
        volume = Decimal("0.01")  # placeholder
    else:
        closed_at_utc = payload.closed_at if payload.closed_at.tzinfo else payload.closed_at.replace(tzinfo=timezone.utc)
        duration_seconds = max(0, int((closed_at_utc - opened_at_utc).total_seconds()))
        close_price = payload.close_price
        net_profit = payload.net_profit
        commission = payload.commission or Decimal("0")
        swap = payload.swap or Decimal("0")
        profit = net_profit - commission - swap
        volume = payload.volume

    trade = Trade(
        id=uuid.uuid4(),
        account_id=account_id,
        broker_trade_id=broker_trade_id,
        symbol=payload.symbol,
        direction=payload.direction,
        open_price=payload.open_price,
        close_price=close_price,
        volume=volume,
        profit=profit,
        commission=commission,
        swap=swap,
        net_profit=net_profit,
        duration_seconds=duration_seconds,
        session=session_value,
        opened_at=opened_at_utc,
        closed_at=closed_at_utc,
        sl=payload.sl,
        tp=payload.tp,
        is_manual=True,
        is_missed=payload.is_missed,
    )
    db.add(trade)
    db.flush()
    return trade


def update_manual_trade(
    db: Session,
    *,
    trade_id: uuid.UUID,
    user_id: uuid.UUID,
    payload,  # ManualTradeUpdateRequest
) -> Trade:
    stmt = select(Trade).where(Trade.id == trade_id)
    trade = db.execute(stmt).scalar_one_or_none()
    if not trade:
        raise ValueError("Trade not found")
        
    if not trade.is_manual:
        raise ValueError("Cannot modify non-manual trades")
        
    # Validate ownership
    if trade.account.user_id != user_id:
        raise ValueError("Access denied")

    # Update fields if provided
    update_data = payload.model_dump(exclude_unset=True)
    
    for key, value in update_data.items():
        setattr(trade, key, value)
        
    # Recalculate derived fields if relevant
    if "is_missed" in update_data or trade.is_missed:
        if trade.is_missed:
            trade.closed_at = trade.opened_at
            trade.duration_seconds = 0
            trade.close_price = trade.tp or trade.open_price
            trade.net_profit = Decimal("0")
            trade.commission = Decimal("0")
            trade.swap = Decimal("0")
            trade.profit = Decimal("0")
            trade.volume = Decimal("0.01")
        else:
            if not trade.volume or trade.volume == Decimal("0.01"):
                trade.volume = Decimal("0.1")
            if trade.commission is None:
                trade.commission = Decimal("0")
            if trade.swap is None:
                trade.swap = Decimal("0")
            trade.profit = (trade.net_profit or Decimal("0")) - trade.commission - trade.swap
            if trade.opened_at and trade.closed_at:
                trade.duration_seconds = max(0, int((trade.closed_at - trade.opened_at).total_seconds()))

    else:
        # Normal executed trade updates
        if "opened_at" in update_data or "closed_at" in update_data:
            opened_at_utc = trade.opened_at if trade.opened_at.tzinfo else trade.opened_at.replace(tzinfo=timezone.utc)
            closed_at_utc = trade.closed_at if trade.closed_at.tzinfo else trade.closed_at.replace(tzinfo=timezone.utc)
            trade.opened_at = opened_at_utc
            trade.closed_at = closed_at_utc
            trade.duration_seconds = max(0, int((closed_at_utc - opened_at_utc).total_seconds()))
            trade.session = TradeSession(classify_session(opened_at_utc))
            
        if "net_profit" in update_data or "commission" in update_data or "swap" in update_data:
            trade.commission = trade.commission or Decimal("0")
            trade.swap = trade.swap or Decimal("0")
            trade.profit = (trade.net_profit or Decimal("0")) - trade.commission - trade.swap

    db.flush()
    return trade


def delete_manual_trade(
    db: Session,
    *,
    trade_id: uuid.UUID,
    user_id: uuid.UUID,
) -> None:
    stmt = select(Trade).where(Trade.id == trade_id)
    trade = db.execute(stmt).scalar_one_or_none()
    if not trade:
        raise ValueError("Trade not found")
        
    if not trade.is_manual:
        raise ValueError("Cannot delete non-manual trades")
        
    # Validate ownership
    if trade.account.user_id != user_id:
        raise ValueError("Access denied")

    db.delete(trade)
    db.flush()


# ---------------------------------------------------------------------------
# AccountSnapshot
# ---------------------------------------------------------------------------

def upsert_account_snapshot_for_date(
    db: Session,
    *,
    account_id: uuid.UUID,
    snapshot_date: date,
    balance: Decimal,
    equity: Decimal,
    floating_pnl: Decimal,
) -> None:
    stmt = (
        pg_insert(AccountSnapshot)
        .values(
            account_id=account_id,
            snapshot_date=snapshot_date,
            balance=balance,
            equity=equity,
            floating_pnl=floating_pnl,
        )
        .on_conflict_do_update(
            index_elements=["account_id", "snapshot_date"],
            set_={
                "balance": balance,
                "equity": equity,
                "floating_pnl": floating_pnl,
            },
        )
    )
    db.execute(stmt)


def get_latest_snapshot_on_or_before_date(
    db: Session,
    *,
    account_id: uuid.UUID,
    snapshot_date: date,
) -> Optional[AccountSnapshot]:
    stmt = (
        select(AccountSnapshot)
        .where(
            AccountSnapshot.account_id == account_id,
            AccountSnapshot.snapshot_date <= snapshot_date,
        )
        .order_by(AccountSnapshot.snapshot_date.desc(), AccountSnapshot.id.desc())
        .limit(1)
    )
    return db.execute(stmt).scalar_one_or_none()


def get_latest_account_snapshot_balance(
    db: Session,
    *,
    account_id: uuid.UUID,
) -> Optional[Decimal]:
    """Most recent persisted account balance from synced MT5 snapshots."""
    stmt = (
        select(AccountSnapshot)
        .where(AccountSnapshot.account_id == account_id)
        .order_by(AccountSnapshot.snapshot_date.desc(), AccountSnapshot.id.desc())
        .limit(1)
    )
    row = db.execute(stmt).scalar_one_or_none()
    if row is None:
        return None
    return row.balance


def get_latest_snapshots_for_accounts(
    db: Session,
    *,
    account_ids: list[uuid.UUID],
) -> dict[uuid.UUID, AccountSnapshot]:
    snapshots: dict[uuid.UUID, AccountSnapshot] = {}
    for account_id in account_ids:
        stmt = (
            select(AccountSnapshot)
            .where(AccountSnapshot.account_id == account_id)
            .order_by(AccountSnapshot.snapshot_date.desc(), AccountSnapshot.id.desc())
            .limit(1)
        )
        snapshot = db.execute(stmt).scalar_one_or_none()
        if snapshot is not None:
            snapshots[account_id] = snapshot
    return snapshots


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
                  AND t1.is_missed = FALSE
                  AND DATE(timezone(:account_timezone, t1.closed_at)) = :trading_date
                ORDER BY t1.net_profit DESC
                LIMIT 1
            ),
            (
                SELECT t2.id
                FROM trades t2
                WHERE t2.account_id = :account_id
                  AND t2.is_missed = FALSE
                  AND DATE(timezone(:account_timezone, t2.closed_at)) = :trading_date
                ORDER BY t2.net_profit ASC
                LIMIT 1
            ),
            now()
        FROM trades t
        WHERE t.account_id = :account_id
          AND t.is_missed = FALSE
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
