from dataclasses import dataclass
from datetime import date, datetime, timedelta, timezone
from decimal import Decimal
import logging
import uuid
from typing import Any, Optional

from fastapi import HTTPException, status
from sqlalchemy.orm import Session

from app.domains.accounts.models import SyncProvider, TradingAccount, TradeDirection, TradeSession, TradeSource
from app.domains.accounts import repository as account_repo
from app.core.config import settings
from app.shared.utils.timezone import classify_session, normalize_broker_datetime_to_utc, to_account_local_date


logger = logging.getLogger(__name__)


@dataclass
class SyncResult:
    inserted_trades: int
    touched_trading_dates: int


_OPEN_TIMESTAMP_KEYS = (
    "opened_at",
    "openedAt",
    "openedTime",
    "openTime",
    "open_time_msc",
    "openTimeMsc",
    "open_time",
    "positionOpenTime",
    "positionOpenDate",
    "positionTime",
    "entryTime",
    "entry_time",
    "position_open_time",
)
_CLOSE_TIMESTAMP_KEYS = (
    "closed_at",
    "closeTime",
    "close_time",
    "doneTime",
    "time",
)


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


def _extract_timestamp_with_normalized_keys(
    deal: dict[str, Any], keys: tuple[str, ...]
) -> datetime:
    try:
        return _pick_timestamp(deal, keys)
    except ValueError:
        normalized_key_map = {
            "".join(ch for ch in str(key).lower() if ch.isalnum()): key
            for key in deal.keys()
        }

        for desired in keys:
            normalized_desired = "".join(ch for ch in str(desired).lower() if ch.isalnum())
            actual_key = normalized_key_map.get(normalized_desired)
            if actual_key is None:
                continue
            value = deal.get(actual_key)
            if not value:
                continue
            if isinstance(value, datetime):
                return value
            if isinstance(value, str):
                iso_value = value.replace("Z", "+00:00")
                return datetime.fromisoformat(iso_value)

        raise


def _is_trade_deal(deal: dict[str, Any]) -> bool:
    deal_type = str(deal.get("type") or deal.get("dealType") or "").upper()
    deal_entry = str(deal.get("entryType") or deal.get("entry") or "").upper()

    if deal_type not in {"BUY", "SELL", "DEAL_TYPE_BUY", "DEAL_TYPE_SELL"}:
        return False

    if deal_entry:
        return deal_entry in {
            "DEAL_ENTRY_OUT",
            "DEAL_ENTRY_OUT_BY",
            "DEAL_ENTRY_INOUT",
            "OUT",
            "OUT_BY",
            "INOUT",
        }

    return False


def _extract_broker_trade_id(deal: dict[str, Any]) -> str:
    return str(
        deal.get("broker_trade_id") or deal.get("id") or deal.get("ticket") or ""
    ).strip()


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


def _extract_mt5_enrichment(deal: dict[str, Any]) -> dict[str, Any]:
    """Extract MT5-specific enrichment fields from a deal dict."""
    raw_source = deal.get("trade_source")
    trade_source: Optional[TradeSource] = None
    if raw_source in ("personal", "copied"):
        trade_source = TradeSource(raw_source)

    def _nullable_decimal(key: str) -> Optional[Decimal]:
        val = deal.get(key)
        return _as_decimal(val) if val is not None else None

    return {
        "sl": _nullable_decimal("sl"),
        "tp": _nullable_decimal("tp"),
        "magic_number": _as_int(deal.get("magic_number")) if deal.get("magic_number") is not None else None,
        "position_id": str(deal.get("position_id") or "") or None,
        "trade_source": trade_source,
        "mfe": _nullable_decimal("mfe"),
        "mae": _nullable_decimal("mae"),
    }


def ingest_closed_deals(
    db: Session,
    *,
    account: TradingAccount,
    deals: list[dict[str, Any]],
    extra_touched_dates: set[date] | None = None,
    mt5_enriched: bool = False,
) -> SyncResult:
    touched_dates: set[date] = set(extra_touched_dates or set())
    inserted = 0
    updated = 0
    skipped_non_trade = 0
    skipped_missing_broker_id = 0
    skipped_missing_open_timestamp = 0
    skipped_missing_close_timestamp = 0

    rows_to_upsert = []

    for deal in deals:
        if not _is_trade_deal(deal):
            skipped_non_trade += 1
            continue

        broker_trade_id = _extract_broker_trade_id(deal)
        if not broker_trade_id:
            skipped_missing_broker_id += 1
            continue

        try:
            closed_raw = _extract_timestamp_with_normalized_keys(deal, _CLOSE_TIMESTAMP_KEYS)
        except ValueError:
            skipped_missing_close_timestamp += 1
            logger.warning(
                "Skipping deal without close timestamp | account_id=%s broker_trade_id=%s",
                account.id,
                broker_trade_id,
            )
            continue

        try:
            opened_raw = _extract_timestamp_with_normalized_keys(deal, _OPEN_TIMESTAMP_KEYS)
        except ValueError:
            skipped_missing_open_timestamp += 1
            logger.warning(
                "Skipping deal without open timestamp | account_id=%s broker_trade_id=%s available_keys=%s",
                account.id,
                broker_trade_id,
                sorted(str(key) for key in deal.keys()),
            )
            continue

        # MT5 payloads already carry UTC timestamps — skip broker offset correction.
        if mt5_enriched:
            opened_at_utc = opened_raw if opened_raw.tzinfo else opened_raw.replace(tzinfo=timezone.utc)
            closed_at_utc = closed_raw if closed_raw.tzinfo else closed_raw.replace(tzinfo=timezone.utc)
        else:
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

        # Extract MT5 enrichment fields when the trade payload includes them.
        enrichment = _extract_mt5_enrichment(deal) if mt5_enriched else {}

        row = {
            "id": uuid.uuid4(),
            "account_id": account.id,
            "broker_trade_id": broker_trade_id,
            "symbol": symbol,
            "direction": direction,
            "open_price": open_price,
            "close_price": close_price,
            "volume": volume,
            "profit": profit,
            "commission": commission,
            "swap": swap,
            "net_profit": net_profit,
            "duration_seconds": max(0, _as_int((closed_at_utc - opened_at_utc).total_seconds())),
            "session": session_value,
            "opened_at": opened_at_utc,
            "closed_at": closed_at_utc,
            "is_manual": False,
            "is_missed": False,
            "created_at": datetime.now(timezone.utc),
            **enrichment,
        }
        rows_to_upsert.append(row)

    inserted, updated, affected_closed_ats = account_repo.bulk_upsert_closed_trades(db, rows=rows_to_upsert)
    for closed_at_utc in affected_closed_ats:
        touched_dates.add(to_account_local_date(closed_at_utc, account.timezone))

    for trading_date in touched_dates:
        account_repo.rebuild_daily_stats_for_date(
            db,
            account_id=account.id,
            trading_date=trading_date,
            account_timezone=account.timezone,
        )

    account_repo.set_account_last_synced_at(db, account, datetime.now(timezone.utc))
    db.commit()

    logger.info(
        (
            "Journal ingest complete | account_id=%s received=%s inserted=%s "
            "updated=%s skipped_non_trade=%s skipped_missing_broker_id=%s "
            "skipped_missing_open_timestamp=%s skipped_missing_close_timestamp=%s touched_dates=%s"
        ),
        account.id,
        len(deals),
        inserted,
        updated,
        skipped_non_trade,
        skipped_missing_broker_id,
        skipped_missing_open_timestamp,
        skipped_missing_close_timestamp,
        len(touched_dates),
    )

    return SyncResult(inserted_trades=inserted, touched_trading_dates=len(touched_dates))


def ingest_mt5_deals(
    db: Session,
    *,
    account: TradingAccount,
    deals: list[dict[str, Any]],
    extra_touched_dates: set[date] | None = None,
) -> SyncResult:
    """
    Ingest a list of closed deals from the headless MT5 microservice callback.

    The payload schema matches models.schemas.MT5DealPayload from headless-mt5-service.
    Each deal has been pre-processed by the microservice (MFE/MAE, SL/TP, trade_source).

    This function maps the MT5 payload fields to the internal Trade model and delegates
    to ingest_closed_deals with the enriched data attached.
    """
    mapped_deals: list[dict[str, Any]] = []

    for deal in deals:
        mapped: dict[str, Any] = {
            # Core identification — broker_trade_id maps from MT5 ticket
            "broker_trade_id": str(deal.get("ticket") or ""),
            "symbol": str(deal.get("symbol") or "UNKNOWN"),
            # Direction: MT5 payload uses "buy"/"sell" directly
            "direction": str(deal.get("direction") or "buy").lower(),
            # Prices
            "open_price": deal.get("price_in"),
            "close_price": deal.get("price_out") or deal.get("price"),
            "volume": deal.get("volume"),
            # Financials — gross_profit maps to profit; net is computed in ingest_closed_deals
            "profit": deal.get("gross_profit") or deal.get("profit"),
            "commission": deal.get("commission"),
            "swap": deal.get("swap"),
            # Timestamps — MT5 service sends ISO-format datetime strings
            "opened_at": deal.get("time_setup") or deal.get("opened_at"),
            "time": deal.get("time_closed") or deal.get("closed_at"),
            # MT5-specific enrichment
            "position_id": str(deal.get("position_id") or ""),
            "sl": deal.get("sl"),
            "tp": deal.get("tp"),
            "magic_number": deal.get("magic_number"),
            "trade_source": deal.get("trade_source"),
            "mfe": deal.get("mfe"),
            "mae": deal.get("mae"),
            # Mark as a closed OUT deal so _is_trade_deal() passes
            "type": "SELL" if str(deal.get("direction") or "").lower() == "sell" else "BUY",
            "entryType": "DEAL_ENTRY_OUT",
        }
        mapped_deals.append(mapped)

    return ingest_closed_deals(
        db,
        account=account,
        deals=mapped_deals,
        extra_touched_dates=extra_touched_dates,
        mt5_enriched=True,
    )


def ingest_mt5_snapshots(
    db: Session,
    *,
    account: TradingAccount,
    snapshots: list[dict[str, Any]],
) -> int:
    """Persist MT5 account snapshots as day-level account snapshots."""
    upserted = 0
    for snapshot in snapshots:
        captured = snapshot.get("captured_at")
        if not captured:
            continue
        if isinstance(captured, str):
            captured_dt = datetime.fromisoformat(captured.replace("Z", "+00:00"))
        elif isinstance(captured, datetime):
            captured_dt = captured
        else:
            continue

        if captured_dt.tzinfo is None:
            captured_dt = captured_dt.replace(tzinfo=timezone.utc)
        snapshot_date = to_account_local_date(captured_dt, account.timezone)

        account_repo.upsert_account_snapshot_for_date(
            db,
            account_id=account.id,
            snapshot_date=snapshot_date,
            balance=_as_decimal(snapshot.get("balance")),
            equity=_as_decimal(snapshot.get("equity")),
            floating_pnl=_as_decimal(snapshot.get("floating_pnl")),
        )
        upserted += 1

    return upserted


def ingest_mt5_core_history_result(
    db: Session,
    *,
    account: TradingAccount,
    result: dict[str, Any],
    closed_from_utc: datetime | None = None,
    closed_to_utc_exclusive: datetime | None = None,
    authoritative: bool = False,
) -> SyncResult:
    """
    Ingest normalized history result from mt5-core.
    """
    deals = result.get("deals") or []
    snapshot = result.get("snapshot")
    broker_offset = int(result.get("broker_offset_seconds") or 0)

    # Save broker offset if it changed
    if broker_offset != account.broker_utc_offset:
        account.broker_utc_offset = broker_offset
        db.flush()

    # Ingest snapshot if present
    snapshot_count = 0
    if snapshot:
        snapshot_count = ingest_mt5_snapshots(db, account=account, snapshots=[snapshot])

    deleted_dates: set[date] = set()
    if authoritative:
        valid_broker_trade_ids = {
            str(deal.get("ticket") or "").strip()
            for deal in deals
            if str(deal.get("ticket") or "").strip()
        }
        _, deleted_dates = account_repo.delete_trades_outside_valid_broker_ids_in_window(
            db,
            account_id=account.id,
            closed_from_utc=closed_from_utc,
            closed_to_utc_exclusive=closed_to_utc_exclusive,
            account_timezone=account.timezone,
            valid_broker_trade_ids=valid_broker_trade_ids,
        )

    # Ingest deals using ingest_mt5_deals
    sync_result = ingest_mt5_deals(
        db,
        account=account,
        deals=deals,
        extra_touched_dates=deleted_dates,
    )
    return sync_result


async def sync_account_deals_mt5(
    db: Session,
    account: TradingAccount,
    lookback_days: Optional[int] = None,
) -> SyncResult:
    from app.domains.accounts.mt5_core_client import Mt5CoreClient
    from app.shared.utils.encryption import decrypt_secret
    
    if not account_repo.try_acquire_account_sync_lock(db, account.id):
        raise HTTPException(
            status_code=status.HTTP_409_CONFLICT,
            detail="Sync already in progress for this account.",
        )

    # 1. Decrypt investor password
    try:
        try:
            investor_password = decrypt_secret(account.encrypted_investor_password)
        except ValueError as exc:
            raise HTTPException(
                status_code=status.HTTP_500_INTERNAL_SERVER_ERROR,
                detail="Failed to decrypt account credentials.",
            ) from exc

        # 2. Determine from_time
        effective_lookback_days = lookback_days or settings.INITIAL_SYNC_LOOKBACK_DAYS
        cleanup_floor = datetime.now(timezone.utc) - timedelta(days=effective_lookback_days)
        if account.last_synced_at is None:
            from_time = cleanup_floor
        else:
            from_time = min(account.last_synced_at, cleanup_floor)

        # 3. Call mt5-core client
        client = Mt5CoreClient()
        sync_result = await client.submit_history_sync(
            account_id=str(account.id),
            from_time=from_time,
            credentials={
                "login": account.broker_login,
                "password": investor_password,
                "server": account.broker_server,
                "broker": account.broker_name,
            }
        )

        # 4. Ingest normalized results
        return ingest_mt5_core_history_result(
            db,
            account=account,
            result=sync_result,
            closed_from_utc=from_time,
            closed_to_utc_exclusive=None,
            authoritative=True,
        )
    finally:
        account_repo.release_account_sync_lock(db, account.id)


