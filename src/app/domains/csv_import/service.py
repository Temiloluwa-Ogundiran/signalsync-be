from datetime import datetime, timedelta, timezone
from decimal import Decimal
from io import BytesIO
from typing import Optional
from zoneinfo import ZoneInfo
import uuid

from fastapi import HTTPException, status
from sqlalchemy.orm import Session

from app.domains.accounts import repository as account_repo
from app.domains.accounts.models import (
    SyncProvider,
    TradingAccount,
    TradingAccountConnectionState,
    TradingAccountStatus,
    TradeSession,
)
from app.domains.accounts.schemas import AccountResponse
from app.domains.csv_import.parsers import get_parser
from app.domains.csv_import.schemas import (
    CSVConfirmResult,
    CSVParseError,
    CSVPreviewAccountMeta,
    CSVPreviewResponse,
    CSVPreviewTrade,
)
from app.domains.users.models import User
from app.shared.utils.timezone import classify_session, to_account_local_date


async def preview_import(
    file_content: bytes,
    platform_id: str,
    timezone_str: str,
) -> CSVPreviewResponse:
    """Parse file and return preview statistics without writing to the database."""
    parser = get_parser(platform_id)
    content = BytesIO(file_content)
    result = parser.parse(content, timezone_str)

    # Convert ParseResult objects to Pydantic schemas
    meta_schema = CSVPreviewAccountMeta(
        account_number=result.account_meta.account_number,
        currency=result.account_meta.currency,
        broker_server=result.account_meta.broker_server,
        account_type=result.account_meta.account_type,
        broker_name=result.account_meta.broker_name,
        starting_balance=result.account_meta.starting_balance,
        current_balance=result.account_meta.current_balance,
    )

    trade_schemas = [
        CSVPreviewTrade(
            broker_trade_id=t.broker_trade_id,
            symbol=t.symbol,
            direction=t.direction,
            opened_at=t.opened_at,
            closed_at=t.closed_at,
            open_price=t.open_price,
            close_price=t.close_price,
            volume=t.volume,
            profit=t.profit,
            commission=t.commission,
            swap=t.swap,
            sl=t.sl,
            tp=t.tp,
        )
        # Show first 50 trades in the list for UI preview
        for t in result.trades[:50]
    ]

    error_schemas = [
        CSVParseError(
            row_number=e.row_number,
            column=e.column,
            message=e.message,
            severity=e.severity,
        )
        for e in result.errors
    ]

    # Calculate summary metrics
    trades = result.trades
    if trades:
        sorted_trades = sorted(trades, key=lambda t: t.closed_at)
        date_from = sorted_trades[0].closed_at.date().isoformat()
        date_to = sorted_trades[-1].closed_at.date().isoformat()
        date_range = {"from": date_from, "to": date_to}
        total_profit = sum((t.profit + t.commission + t.swap) for t in trades)
        symbols = list(set(t.symbol for t in trades))
    else:
        date_range = None
        total_profit = Decimal("0")
        symbols = []

    summary = {
        "date_range": date_range,
        "total_profit": float(total_profit),
        "total_trades": len(trades),
        "symbols": symbols,
    }

    return CSVPreviewResponse(
        account_meta=meta_schema,
        trades=trade_schemas,
        trade_count=len(trades),
        errors=error_schemas,
        warnings=result.warnings,
        summary=summary,
    )


async def confirm_import(
    db: Session,
    file_content: bytes,
    platform_id: str,
    timezone_str: str,
    display_name: str,
    account_id: Optional[uuid.UUID],
    current_user: User,
) -> CSVConfirmResult:
    """Create or update a CSV account and import the parsed trades into database."""
    parser = get_parser(platform_id)
    content = BytesIO(file_content)
    result = parser.parse(content, timezone_str)

    if result.errors:
        # Check if there are strict blocking errors
        blocking_errors = [e for e in result.errors if e.severity == "error"]
        if blocking_errors:
            raise HTTPException(
                status_code=status.HTTP_400_BAD_REQUEST,
                detail=f"Cannot import file due to parsing errors: {blocking_errors[0].message}",
            )

    # 1. Fetch existing account or create a new CSV account
    if account_id:
        account = account_repo.get_account_by_id_for_user(db, account_id=account_id, user_id=current_user.id)
        if not account:
            raise HTTPException(
                status_code=status.HTTP_404_NOT_FOUND,
                detail="Trading account not found.",
            )
        if account.sync_provider != SyncProvider.csv_import:
            raise HTTPException(
                status_code=status.HTTP_400_BAD_REQUEST,
                detail="This account was connected via API. You cannot import CSV files directly into it.",
            )
        
        # Keep displaying name updated if modified
        if display_name and display_name.strip():
            account.display_name = display_name.strip()
    else:
        account = account_repo.create_csv_account(
            db,
            user_id=current_user.id,
            account_number=result.account_meta.account_number or "Imported",
            broker_name=result.account_meta.broker_name or "MetaTrader 5",
            broker_server=result.account_meta.broker_server or "CSVServer",
            platform=platform_id.upper(),
            currency=result.account_meta.currency or "USD",
            timezone=timezone_str,
            display_name=display_name.strip() if display_name else None,
            account_type=result.account_meta.account_type or "live",
        )

    # 2. Bulk insert trades with duplicate skipping
    inserted = 0
    skipped = 0
    touched_dates = set()

    for t in result.trades:
        net_profit = t.profit + t.commission + t.swap
        duration_seconds = max(0, int((t.closed_at - t.opened_at).total_seconds()))
        session = TradeSession(classify_session(t.opened_at))

        was_inserted = account_repo.upsert_closed_trade(
            db,
            account_id=account.id,
            broker_trade_id=t.broker_trade_id,
            symbol=t.symbol,
            direction=t.direction,
            open_price=t.open_price,
            close_price=t.close_price,
            volume=t.volume,
            profit=t.profit,
            commission=t.commission,
            swap=t.swap,
            net_profit=net_profit,
            duration_seconds=duration_seconds,
            session=session,
            opened_at=t.opened_at,
            closed_at=t.closed_at,
            sl=t.sl,
            tp=t.tp,
            position_id=t.position_id,
        )

        if was_inserted:
            inserted += 1
            local_date = to_account_local_date(t.closed_at, account.timezone)
            touched_dates.add(local_date)
        else:
            skipped += 1

    # 3. Rebuild DailyStats for all touched dates
    for trading_date in touched_dates:
        account_repo.delete_daily_stats_for_date(db, account_id=account.id, trading_date=trading_date)
        account_repo.rebuild_daily_stats_for_date(
            db,
            account_id=account.id,
            trading_date=trading_date,
            account_timezone=account.timezone,
        )

    # 4. Upsert account snapshots
    # If the parser returned daily snapshots, write all of them first
    if getattr(result, "daily_balances", None):
        for snap_date, bal in result.daily_balances.items():
            account_repo.upsert_account_snapshot_for_date(
                db,
                account_id=account.id,
                snapshot_date=snap_date,
                balance=bal,
                equity=bal,
                floating_pnl=Decimal("0"),
            )

    # Starting Balance Snapshot (dated 1 day before earliest trade)
    if result.account_meta.starting_balance is not None:
        if result.trades:
            earliest_trade = min(result.trades, key=lambda t: t.closed_at)
            start_date = to_account_local_date(earliest_trade.closed_at, account.timezone) - timedelta(days=1)
        else:
            start_date = datetime.now(ZoneInfo(account.timezone)).date() - timedelta(days=1)

        account_repo.upsert_account_snapshot_for_date(
            db,
            account_id=account.id,
            snapshot_date=start_date,
            balance=result.account_meta.starting_balance,
            equity=result.account_meta.starting_balance,
            floating_pnl=Decimal("0"),
        )

    # Current Balance Snapshot (dated today)
    if result.account_meta.current_balance is not None:
        today_date = datetime.now(ZoneInfo(account.timezone)).date()
        account_repo.upsert_account_snapshot_for_date(
            db,
            account_id=account.id,
            snapshot_date=today_date,
            balance=result.account_meta.current_balance,
            equity=result.account_meta.current_balance,
            floating_pnl=Decimal("0"),
        )

    # 5. Mark account as active & synced
    account.status = TradingAccountStatus.synced
    account.connection_state = TradingAccountConnectionState.ready
    account.is_data_ready_for_stats = True
    account.last_synced_at = datetime.now(timezone.utc)
    
    db.commit()
    db.refresh(account)

    return CSVConfirmResult(
        account=AccountResponse.model_validate(account),
        inserted=inserted,
        skipped=skipped,
        touched_dates=len(touched_dates),
    )
