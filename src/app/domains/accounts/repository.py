import uuid
from datetime import date, datetime, timedelta, timezone
from decimal import Decimal
from typing import Optional

from sqlalchemy import Date, case, cast, func, or_, select
from sqlalchemy import delete as sa_delete
from sqlalchemy import text
from sqlalchemy import update as sa_update
from sqlalchemy.dialects.postgresql import insert as pg_insert
from sqlalchemy.orm import Session

from app.domains.accounts.models import (
    AccountSnapshot,
    ImportMethod,
    JournalBootstrapDispatch,
    Trade,
    TradeDirection,
    TradeSession,
    TradeSource,
    TradingAccount,
    TradingAccountConnectionState,
    TradingAccountStatus,
)
from app.domains.accounts.mt5_server_catalog import (
    Mt5ServerCatalog,
    normalize_mt5_server_key,
)
from app.shared.utils.timezone import classify_session

# ---------------------------------------------------------------------------
# TradingAccount
# ---------------------------------------------------------------------------

def search_mt5_servers(db: Session, *, query: str, limit: int = 25) -> list[Mt5ServerCatalog]:
    query = query.strip()
    normalized_query = normalize_mt5_server_key(query)

    stmt = select(Mt5ServerCatalog).where(Mt5ServerCatalog.active.is_(True))
    if query:
        like_query = f"%{query}%"
        filters = [Mt5ServerCatalog.canonical_server_name.ilike(like_query)]
        if normalized_query:
            filters.append(Mt5ServerCatalog.normalized_server_key.ilike(f"%{normalized_query}%"))
        stmt = stmt.where(or_(*filters))

        # Rank prefix matches (what the user typed comes first) above
        # substring-only matches; alphabetical within each tier.
        prefix_query = f"{query}%"
        rank = case(
            (Mt5ServerCatalog.canonical_server_name.ilike(prefix_query), 0),
            (
                Mt5ServerCatalog.normalized_server_key.ilike(f"{normalized_query}%")
                if normalized_query
                else False,
                1,
            ),
            else_=2,
        )
        stmt = stmt.order_by(rank.asc(), Mt5ServerCatalog.canonical_server_name.asc())
    else:
        stmt = stmt.order_by(Mt5ServerCatalog.canonical_server_name.asc())

    stmt = stmt.limit(limit)
    return list(db.execute(stmt).scalars().all())


def resolve_mt5_server_name(db: Session, server_name: str) -> str:
    trimmed = server_name.strip()
    if not trimmed:
        return trimmed

    exact_stmt = select(Mt5ServerCatalog).where(
        Mt5ServerCatalog.active.is_(True),
        func.lower(Mt5ServerCatalog.canonical_server_name) == trimmed.lower(),
    )
    exact = db.execute(exact_stmt).scalar_one_or_none()
    if exact is not None:
        return exact.canonical_server_name

    normalized_key = normalize_mt5_server_key(trimmed)
    if not normalized_key:
        return trimmed

    normalized_stmt = select(Mt5ServerCatalog).where(
        Mt5ServerCatalog.active.is_(True),
        Mt5ServerCatalog.normalized_server_key == normalized_key,
    ).limit(2)
    matches = list(db.execute(normalized_stmt).scalars().all())
    if len(matches) == 1:
        return matches[0].canonical_server_name

    return trimmed

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
    import_method: ImportMethod = ImportMethod.auto_sync,
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
        import_method=import_method,
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
    
    # Reuse an existing row for this meta id (e.g. re-importing into an account
    # that was archived) instead of creating a duplicate.
    stmt = select(TradingAccount).where(
        TradingAccount.user_id == user_id,
        TradingAccount.meta_account_id == meta_account_id
    )
    account = db.execute(stmt).scalar_one_or_none()

    if account:
        account.is_archived = False
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
        import_method=ImportMethod.csv_upload,
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
    import_method: ImportMethod = ImportMethod.auto_sync,
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
    account.is_archived = False
    account.status = TradingAccountStatus.pending_sync
    account.connection_state = TradingAccountConnectionState.pending_verification
    account.is_data_ready_for_stats = False
    account.sync_error_message = None
    account.last_bootstrap_synced_at = None
    account.bootstrap_error_message = None
    account.import_method = import_method
    db.flush()
    return account


def get_account_by_id(db: Session, account_id: uuid.UUID) -> Optional[TradingAccount]:
    stmt = select(TradingAccount).where(
        TradingAccount.id == account_id,
    )
    return db.execute(stmt).scalar_one_or_none()


def get_account_by_id_for_user(
    db: Session, account_id: uuid.UUID, user_id: uuid.UUID
) -> Optional[TradingAccount]:
    stmt = select(TradingAccount).where(
        TradingAccount.id == account_id,
        TradingAccount.user_id == user_id,
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
        )
        .order_by(TradingAccount.created_at.desc())
    )
    return list(db.execute(stmt).scalars().all())


def count_closed_trades_for_accounts(
    db: Session, *, account_ids: list[uuid.UUID]
) -> dict[uuid.UUID, int]:
    if not account_ids:
        return {}

    stmt = (
        select(Trade.account_id, func.count(Trade.id))
        .where(Trade.account_id.in_(account_ids))
        .group_by(Trade.account_id)
    )
    return {account_id: int(count) for account_id, count in db.execute(stmt).all()}


def get_latest_closed_trade_at(
    db: Session,
    *,
    account_id: uuid.UUID,
) -> Optional[datetime]:
    stmt = select(func.max(Trade.closed_at)).where(Trade.account_id == account_id)
    return db.execute(stmt).scalar_one_or_none()


def schedule_bootstrap_dispatch(
    db: Session, *, account_id: uuid.UUID
) -> JournalBootstrapDispatch:
    item = db.execute(
        select(JournalBootstrapDispatch).where(
            JournalBootstrapDispatch.account_id == account_id
        )
    ).scalar_one_or_none()
    now = datetime.now(timezone.utc)
    if item is None:
        item = JournalBootstrapDispatch(account_id=account_id, available_at=now)
        db.add(item)
    else:
        item.available_at = now
        item.dispatched_at = None
        item.last_error = None
    db.flush()
    return item


def claim_bootstrap_dispatches(
    db: Session, *, limit: int = 100, account_id: uuid.UUID | None = None
) -> list[JournalBootstrapDispatch]:
    stmt = (
        select(JournalBootstrapDispatch)
        .where(
            JournalBootstrapDispatch.dispatched_at.is_(None),
            JournalBootstrapDispatch.available_at <= datetime.now(timezone.utc),
        )
        .order_by(JournalBootstrapDispatch.available_at.asc())
        .with_for_update(skip_locked=True)
        .limit(limit)
    )
    if account_id is not None:
        stmt = stmt.where(JournalBootstrapDispatch.account_id == account_id)
    return list(db.execute(stmt).scalars().all())


def try_account_bootstrap_lock(db: Session, *, account_id: uuid.UUID) -> bool:
    key = account_id.int & ((1 << 63) - 1)
    return bool(
        db.execute(
            text("SELECT pg_try_advisory_xact_lock(:key)"), {"key": key}
        ).scalar_one()
    )


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
    outcome: str = "success",
) -> None:
    account.last_synced_at = synced_at
    account.last_sync_attempted_at = synced_at
    account.next_sync_not_before = next_sync_not_before
    account.last_sync_outcome = outcome
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
    db: Session,
    account: TradingAccount,
    synced_at: datetime,
    sync_outcome: str = "success",
) -> None:
    account.connection_state = TradingAccountConnectionState.ready
    account.is_data_ready_for_stats = True
    account.last_synced_at = synced_at
    account.last_sync_attempted_at = synced_at
    account.last_sync_outcome = sync_outcome
    account.consecutive_sync_failures = 0
    account.next_sync_not_before = None
    account.sync_error_message = None
    account.last_bootstrap_synced_at = synced_at
    account.bootstrap_error_message = None
    db.flush()


def mark_account_verification_failed(
    db: Session, account: TradingAccount, message: str
) -> None:
    account.status = TradingAccountStatus.error
    account.connection_state = TradingAccountConnectionState.verification_failed
    account.is_data_ready_for_stats = False
    account.sync_error_message = message
    account.bootstrap_error_message = None
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


def archive_account(db: Session, account: TradingAccount) -> None:
    # Archive: stop syncing but keep the account (and its history). The row stays
    # in place; the FE shows archived accounts muted. There is no soft-delete —
    # permanent delete physically removes the row.
    account.is_archived = True
    db.flush()


def unarchive_account(db: Session, account: TradingAccount) -> None:
    """Reverse of archive_account: resume syncing without re-verifying credentials.
    The stored credentials and history are untouched, so flipping the flag back is
    enough to bring the account live again."""
    account.is_archived = False
    db.flush()


def hard_delete_account(db: Session, account: TradingAccount) -> None:
    """Permanently remove the account. Cascades to its trades, snapshots, and
    daily journals via the model's delete-orphan relationships."""
    db.delete(account)
    db.flush()


# ---------------------------------------------------------------------------
# Sync lock (per-account advisory lock to prevent overlapping syncs)
# ---------------------------------------------------------------------------


def try_acquire_account_sync_lock(db: Session, account_id: uuid.UUID) -> bool:
    # Transaction-scoped advisory lock: auto-released when the surrounding
    # transaction commits or rolls back. This is PgBouncer-safe under transaction
    # pooling — unlike pg_advisory_lock, there is no separate unlock statement that
    # could land on a different pooled server connection. The single commit that
    # releases this lock is owned by orchestrate_mt5_sync; ingest_closed_deals must
    # therefore NOT commit mid-sync (see #7), or the lock releases early.
    stmt = text("SELECT pg_try_advisory_xact_lock(hashtext(:lock_key))")
    return bool(
        db.execute(
            stmt,
            {"lock_key": f"trading-account-sync:{account_id}"},
        ).scalar()
    )


def is_account_sync_locked(db: Session, account_id: uuid.UUID) -> bool:
    """True if another session currently holds the per-account sync advisory lock."""
    stmt = text("""
        SELECT EXISTS (
            SELECT 1 FROM pg_locks
            WHERE locktype = 'advisory' AND objid = hashtext(:lock_key)::oid AND granted
        )
    """)
    return bool(db.execute(stmt, {"lock_key": f"trading-account-sync:{account_id}"}).scalar())


# ---------------------------------------------------------------------------
# Trade
# ---------------------------------------------------------------------------

def get_trade_by_id(db: Session, trade_id: uuid.UUID) -> Optional[Trade]:
    stmt = select(Trade).where(Trade.id == trade_id)
    return db.execute(stmt).scalar_one_or_none()


def bulk_upsert_closed_trades(db: Session, *, rows: list[dict]) -> tuple[int, int, list[datetime]]:
    """rows: dicts with the exact Trade column names.
    Returns (inserted_count, updated_count, affected_closed_ats)."""
    if not rows:
        return 0, 0, []
    inserted = 0
    updated = 0
    affected_closed_ats = []
    CHUNK = 500
    for i in range(0, len(rows), CHUNK):
        chunk = rows[i : i + CHUNK]
        stmt = pg_insert(Trade).values(chunk)
        update_cols = {
            c: stmt.excluded[c]
            for c in ("symbol", "direction", "open_price", "close_price", "volume",
                      "profit", "commission", "swap", "net_profit", "duration_seconds",
                      "session", "opened_at", "closed_at")
        }
        # Preserve existing enrichment when the incoming value is NULL
        for c in ("sl", "tp", "magic_number", "position_id", "trade_source", "mfe", "mae"):
            update_cols[c] = func.coalesce(stmt.excluded[c], getattr(Trade, c))
        stmt = stmt.on_conflict_do_update(
            index_elements=["account_id", "broker_trade_id"], set_=update_cols
        ).returning(Trade.closed_at, text("(xmax = 0) AS was_inserted"))
        for closed_at, was_inserted in db.execute(stmt).all():
            affected_closed_ats.append(closed_at)
            if was_inserted:
                inserted += 1
            else:
                updated += 1
    return inserted, updated, affected_closed_ats


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
) -> list[Trade]:
    stmt = (
        select(Trade)
        .where(
            Trade.account_id == account_id,
            cast(func.timezone(account_timezone, Trade.closed_at), Date) == trading_date,
        )
    )
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
    stmt = sa_delete(Trade).where(Trade.account_id == account_id)
    if closed_from_utc is not None:
        stmt = stmt.where(Trade.closed_at >= closed_from_utc)
    if closed_to_utc_exclusive is not None:
        stmt = stmt.where(Trade.closed_at < closed_to_utc_exclusive)
    if valid_broker_trade_ids:
        stmt = stmt.where(Trade.broker_trade_id.not_in(sorted(valid_broker_trade_ids)))
    stmt = stmt.returning(cast(func.timezone(account_timezone, Trade.closed_at), Date))
    rows = db.execute(stmt).all()
    return len(rows), {r[0] for r in rows if r[0] is not None}



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


def get_latest_account_snapshot(
    db: Session,
    *,
    account_id: uuid.UUID,
) -> Optional[AccountSnapshot]:
    """Most recent persisted account snapshot (balance/equity) for an account."""
    stmt = (
        select(AccountSnapshot)
        .where(AccountSnapshot.account_id == account_id)
        .order_by(AccountSnapshot.snapshot_date.desc(), AccountSnapshot.id.desc())
        .limit(1)
    )
    return db.execute(stmt).scalar_one_or_none()


def get_earliest_snapshot(db: Session, account_id: uuid.UUID) -> Optional[AccountSnapshot]:
    stmt = (
        select(AccountSnapshot)
        .where(AccountSnapshot.account_id == account_id)
        .order_by(AccountSnapshot.snapshot_date.asc(), AccountSnapshot.id.asc())
        .limit(1)
    )
    return db.execute(stmt).scalar_one_or_none()


def get_latest_snapshots_for_accounts(
    db: Session,
    *,
    account_ids: list[uuid.UUID],
) -> dict[uuid.UUID, AccountSnapshot]:
    if not account_ids:
        return {}

    # Single query using Postgres DISTINCT ON to fetch the latest snapshot per
    # account, instead of one query per account (N+1). The leading ORDER BY column
    # must match the DISTINCT ON expression.
    stmt = (
        select(AccountSnapshot)
        .where(AccountSnapshot.account_id.in_(account_ids))
        .order_by(
            AccountSnapshot.account_id,
            AccountSnapshot.snapshot_date.desc(),
            AccountSnapshot.id.desc(),
        )
        .distinct(AccountSnapshot.account_id)
    )
    return {
        snapshot.account_id: snapshot
        for snapshot in db.execute(stmt).scalars().all()
    }




