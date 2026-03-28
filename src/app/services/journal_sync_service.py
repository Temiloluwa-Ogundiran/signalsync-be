from dataclasses import dataclass
from datetime import date, datetime, timedelta, timezone
from decimal import Decimal
import logging
import threading
from typing import Any

from fastapi import HTTPException, status
from sqlalchemy.orm import Session

from app.models.trade import TradeDirection, TradeSession
from app.models.trading_account import TradingAccount
from app.core.config import settings
from app.repositories import daily_stats_repo, trade_repo, trading_account_repo
from app.services.metaapi_service import metaapi_service
from app.utils.timezone import classify_session, normalize_broker_datetime_to_utc, to_account_local_date


logger = logging.getLogger(__name__)


@dataclass
class SyncResult:
    inserted_trades: int
    touched_trading_dates: int


_sync_guard = threading.Lock()
_active_sync_accounts: set = set()


def _try_acquire_account_sync_lock(account_id) -> bool:
    with _sync_guard:
        if account_id in _active_sync_accounts:
            return False
        _active_sync_accounts.add(account_id)
        return True


def _release_account_sync_lock(account_id) -> None:
    with _sync_guard:
        _active_sync_accounts.discard(account_id)


def _as_decimal(value: Any) -> Decimal:
    if value is None:
        return Decimal("0")
    return Decimal(str(value))


def _as_int(value: Any) -> int:
    if value is None:
        return 0
    return int(value)


def _pick_timestamp(deal: dict[str, Any], keys: tuple[str, ...]) -> datetime:
    for key in keys:
        value = deal.get(key)
        if value:
            if isinstance(value, datetime):
                return value
            if isinstance(value, str):
                iso_value = value.replace("Z", "+00:00")
                return datetime.fromisoformat(iso_value)
    raise ValueError(f"Deal timestamp missing. Expected one of keys: {keys}")


def _is_trade_deal(deal: dict[str, Any]) -> bool:
    deal_type = str(deal.get("type") or deal.get("dealType") or "").upper()
    deal_entry = str(deal.get("entryType") or deal.get("entry") or "").upper()

    if deal_type not in {"BUY", "SELL", "DEAL_TYPE_BUY", "DEAL_TYPE_SELL"}:
        return False

    # Keep only closed/executed exits to avoid counting opening legs as completed trades.
    if deal_entry:
        return deal_entry in {"DEAL_ENTRY_OUT", "DEAL_ENTRY_OUT_BY", "DEAL_ENTRY_INOUT", "OUT", "OUT_BY", "INOUT"}

    return False


def _extract_broker_trade_id(deal: dict[str, Any]) -> str:
    return str(deal.get("broker_trade_id") or deal.get("id") or deal.get("ticket") or "").strip()


def _pick_price(deal: dict[str, Any], keys: tuple[str, ...]) -> Decimal:
    for key in keys:
        value = deal.get(key)
        if value is not None:
            return _as_decimal(value)
    return Decimal("0")


def _extract_direction(deal: dict[str, Any]) -> TradeDirection:
    raw = str(deal.get("direction") or deal.get("type") or "").lower()
    if "sell" in raw:
        return TradeDirection.sell
    return TradeDirection.buy


def ingest_closed_deals(
    db: Session,
    *,
    account: TradingAccount,
    deals: list[dict[str, Any]],
    extra_touched_dates: set[date] | None = None,
) -> SyncResult:
    touched_dates: set[date] = set(extra_touched_dates or set())
    inserted = 0
    skipped_non_trade = 0
    skipped_missing_broker_id = 0

    for deal in deals:
        if not _is_trade_deal(deal):
            skipped_non_trade += 1
            continue

        broker_trade_id = _extract_broker_trade_id(deal)
        if not broker_trade_id:
            skipped_missing_broker_id += 1
            continue

        opened_raw = _pick_timestamp(deal, ("opened_at", "openTime", "open_time", "time"))
        closed_raw = _pick_timestamp(deal, ("closed_at", "closeTime", "close_time", "doneTime", "time"))

        opened_at_utc = normalize_broker_datetime_to_utc(opened_raw, account.broker_utc_offset)
        closed_at_utc = normalize_broker_datetime_to_utc(closed_raw, account.broker_utc_offset)

        session_value = TradeSession(classify_session(opened_at_utc))
        direction = _extract_direction(deal)

        profit = _as_decimal(deal.get("profit"))
        commission = _as_decimal(deal.get("commission"))
        swap = _as_decimal(deal.get("swap"))
        net_profit = profit + commission + swap

        close_price = _pick_price(deal, ("close_price", "closePrice", "price"))
        open_price = _pick_price(deal, ("open_price", "openPrice"))
        if open_price == Decimal("0"):
            open_price = close_price

        symbol = str(deal.get("symbol") or "").strip() or "UNKNOWN"
        volume = _pick_price(deal, ("volume", "lots"))

        was_inserted = trade_repo.upsert_closed_trade(
            db,
            account_id=account.id,
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
            duration_seconds=max(0, _as_int((closed_at_utc - opened_at_utc).total_seconds())),
            session=session_value,
            opened_at=opened_at_utc,
            closed_at=closed_at_utc,
        )

        if was_inserted:
            inserted += 1
            touched_dates.add(to_account_local_date(closed_at_utc, account.timezone))

    for trading_date in touched_dates:
        daily_stats_repo.delete_for_trading_date(
            db,
            account_id=account.id,
            trading_date=trading_date,
        )
        daily_stats_repo.rebuild_for_trading_date(
            db,
            account_id=account.id,
            trading_date=trading_date,
            account_timezone=account.timezone,
        )

    trading_account_repo.set_last_synced_at(db, account, datetime.now(timezone.utc))
    db.commit()

    logger.info(
        (
            "Journal ingest complete | account_id=%s received=%s inserted=%s "
            "skipped_non_trade=%s skipped_missing_broker_id=%s touched_dates=%s"
        ),
        account.id,
        len(deals),
        inserted,
        skipped_non_trade,
        skipped_missing_broker_id,
        len(touched_dates),
    )

    return SyncResult(inserted_trades=inserted, touched_trading_dates=len(touched_dates))


def sync_account_deals(
    db: Session,
    *,
    account: TradingAccount,
    lookback_days: int | None = None,
) -> SyncResult:
    if not _try_acquire_account_sync_lock(account.id):
        raise HTTPException(
            status_code=status.HTTP_409_CONFLICT,
            detail="Sync already in progress for this account.",
        )

    try:
        # Always include at least a recent cleanup window to correct previously-ingested bad deals.
        effective_lookback_days = lookback_days or settings.INITIAL_SYNC_LOOKBACK_DAYS
        cleanup_floor = datetime.now(timezone.utc) - timedelta(days=effective_lookback_days)
        if account.last_synced_at is None:
            from_dt = cleanup_floor
        else:
            from_dt = min(account.last_synced_at, cleanup_floor)

        deals = metaapi_service.get_deals(
            account.meta_account_id,
            from_dt=from_dt,
            to_dt=None,
        )

        filtered_deals = [deal for deal in deals if _is_trade_deal(deal)]
        if not filtered_deals:
            logger.warning(
                (
                    "Journal sync fetched no qualifying closed trades | account_id=%s "
                    "fetched_deals=%s from_dt=%s"
                ),
                account.id,
                len(deals),
                from_dt.isoformat(),
            )
            return ingest_closed_deals(db, account=account, deals=[])

        valid_broker_trade_ids = {
            broker_trade_id
            for broker_trade_id in (_extract_broker_trade_id(deal) for deal in filtered_deals)
            if broker_trade_id
        }

        deleted_count, deleted_dates = trade_repo.delete_trades_outside_valid_broker_ids_in_window(
            db,
            account_id=account.id,
            closed_from_utc=from_dt,
            closed_to_utc_exclusive=None,
            account_timezone=account.timezone,
            valid_broker_trade_ids=valid_broker_trade_ids,
        )

        if deleted_count:
            logger.info(
                "Journal cleanup removed stale trades | account_id=%s deleted=%s affected_dates=%s",
                account.id,
                deleted_count,
                len(deleted_dates),
            )

        return ingest_closed_deals(
            db,
            account=account,
            deals=filtered_deals,
            extra_touched_dates=deleted_dates,
        )
    finally:
        _release_account_sync_lock(account.id)
