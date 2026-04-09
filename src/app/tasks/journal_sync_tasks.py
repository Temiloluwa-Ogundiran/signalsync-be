import logging
import uuid
from datetime import datetime, timezone

from app.core.celery_app import celery_app
from app.core.config import settings
from app.core.database import SessionLocal
from app.domains.accounts import repository as account_repo
from app.domains.accounts.metaapi import is_transient_metaapi_error
from app.domains.accounts.models import SyncProvider, TradingAccountStatus
from app.domains.accounts.sync import sync_account_deals

logger = logging.getLogger(__name__)


@celery_app.task(name="journal.sync_all_accounts")
def sync_all_accounts() -> dict:
    cycle_started = datetime.now(timezone.utc)
    attempted = 0
    succeeded = 0
    failed = 0
    skipped = 0
    inserted_total = 0
    touched_dates_total = 0

    with SessionLocal() as lock_db:
        lock_acquired = account_repo.try_acquire_cycle_lock(lock_db)
        if not lock_acquired:
            logger.info("Journal sync cycle skipped: previous cycle still running")
            return {
                "attempted": 0,
                "succeeded": 0,
                "failed": 0,
                "skipped": 0,
                "inserted_trades": 0,
                "touched_dates": 0,
                "overlap_skipped": True,
            }

        try:
            with SessionLocal() as db:
                account_ids = [
                    account.id for account in account_repo.list_syncable_accounts(db)
                ]

            logger.info("Journal sync cycle candidates=%s", len(account_ids))

            for account_id in account_ids:
                attempted += 1
                try:
                    with SessionLocal() as db:
                        account = account_repo.get_account_by_id(db, account_id)
                        if account is None or account.status in {
                            TradingAccountStatus.disconnected,
                        }:
                            skipped += 1
                            continue

                        result = sync_account_deals(db, account=account)

                    succeeded += 1
                    inserted_total += result.inserted_trades
                    touched_dates_total += result.touched_trading_dates
                except Exception as exc:  # noqa: BLE001
                    failed += 1
                    logger.exception("Journal sync failed for account_id=%s", account_id)
                    with SessionLocal() as db:
                        account = account_repo.get_account_by_id(db, account_id)
                        if account is not None:
                            if is_transient_metaapi_error(exc):
                                account_repo.set_account_sync_warning(
                                    db,
                                    account,
                                    f"Transient MetaAPI sync error: {str(exc)[:450]}",
                                )
                            else:
                                account_repo.set_account_sync_error(
                                    db, account, str(exc)[:500]
                                )
                            db.commit()
        finally:
            account_repo.release_cycle_lock(lock_db)

    duration_seconds = (datetime.now(timezone.utc) - cycle_started).total_seconds()
    logger.info(
        (
            "Journal celery cycle complete | attempted=%s succeeded=%s failed=%s "
            "skipped=%s inserted_trades=%s touched_dates=%s duration_seconds=%.2f"
        ),
        attempted,
        succeeded,
        failed,
        skipped,
        inserted_total,
        touched_dates_total,
        duration_seconds,
    )

    return {
        "attempted": attempted,
        "succeeded": succeeded,
        "failed": failed,
        "skipped": skipped,
        "inserted_trades": inserted_total,
        "touched_dates": touched_dates_total,
        "duration_seconds": duration_seconds,
        "overlap_skipped": False,
    }


@celery_app.task(name="journal.sync_account")
def sync_account(account_id: str) -> dict:
    try:
        account_uuid = uuid.UUID(account_id)
    except ValueError:
        return {"status": "skipped", "reason": "invalid_account_id"}

    with SessionLocal() as db:
        account = account_repo.get_account_by_id(db, account_uuid)
        if account is None:
            return {"status": "skipped", "reason": "account_not_found"}

        if account.status in {TradingAccountStatus.disconnected}:
            return {"status": "skipped", "reason": "account_disconnected"}

        try:
            result = sync_account_deals(db, account=account)
            return {
                "status": "ok",
                "inserted_trades": result.inserted_trades,
                "touched_dates": result.touched_trading_dates,
            }
        except Exception as exc:  # noqa: BLE001
            if is_transient_metaapi_error(exc):
                account_repo.set_account_sync_warning(
                    db,
                    account,
                    f"Transient MetaAPI sync error: {str(exc)[:450]}",
                )
            else:
                account_repo.set_account_sync_error(db, account, str(exc)[:500])
            db.commit()
            logger.exception("Journal sync failed for single account_id=%s", account_id)
            if is_transient_metaapi_error(exc):
                return {
                    "status": "timeout",
                    "reason": "MetaAPI timeout/transient error. Please retry.",
                }
            return {"status": "error", "reason": str(exc)[:200]}


@celery_app.task(name="journal.sync_all_mt5_accounts")
def sync_all_mt5_accounts() -> dict:
    """
    Periodic Celery beat task: triggers the headless MT5 microservice for every
    trading account that uses sync_provider=headless_mt5.

    Each trigger is a fire-and-forget HTTP POST to the MT5 service.
    Results are delivered asynchronously via the /accounts/webhook/mt5-sync endpoint.
    """
    import httpx
    from app.domains.accounts.sync import _try_acquire_account_sync_lock, _release_account_sync_lock

    if not settings.MT5_SERVICE_URL or not settings.MT5_SERVICE_SHARED_SECRET:
        logger.info("MT5 sync skipped: MT5_SERVICE_URL or MT5_SERVICE_SHARED_SECRET not configured.")
        return {"skipped": True, "reason": "mt5_service_not_configured"}

    cycle_started = datetime.now(timezone.utc)
    triggered = 0
    skipped = 0
    failed = 0

    with SessionLocal() as db:
        all_accounts = account_repo.list_syncable_accounts(db)
        mt5_accounts = [
            a for a in all_accounts if a.sync_provider == SyncProvider.headless_mt5
        ]

    logger.info("MT5 sync cycle | candidates=%s", len(mt5_accounts))

    for account in mt5_accounts:
        if account.status == TradingAccountStatus.disconnected:
            skipped += 1
            continue

        try:
            from app.shared.utils.encryption import decrypt_secret
            investor_password = decrypt_secret(account.encrypted_investor_password)
        except Exception:  # noqa: BLE001
            logger.warning("Failed to decrypt password for account_id=%s — skipping.", account.id)
            skipped += 1
            continue

        last_sync_ts = int(account.last_synced_at.timestamp()) if account.last_synced_at else 0
        known_copy_magics = list(account.copy_magic_numbers or [])

        payload = {
            "account_id": str(account.id),
            "broker_server": account.broker_server,
            "login_id": int(account.broker_login),
            "investor_password": investor_password,
            "last_sync_timestamp": last_sync_ts,
            "known_copy_magics": known_copy_magics,
            "mode": "sync",
        }

        try:
            with httpx.Client(timeout=10.0) as client:
                response = client.post(
                    f"{settings.MT5_SERVICE_URL.rstrip('/')}/sync",
                    json=payload,
                    headers={"X-Shared-Secret": settings.MT5_SERVICE_SHARED_SECRET},
                )
                response.raise_for_status()
            triggered += 1
        except Exception as exc:  # noqa: BLE001
            failed += 1
            logger.error(
                "MT5 sync trigger failed | account_id=%s error=%s",
                account.id,
                str(exc)[:200],
            )

    duration_seconds = (datetime.now(timezone.utc) - cycle_started).total_seconds()
    logger.info(
        "MT5 sync cycle complete | triggered=%s skipped=%s failed=%s duration=%.2fs",
        triggered,
        skipped,
        failed,
        duration_seconds,
    )

    return {
        "triggered": triggered,
        "skipped": skipped,
        "failed": failed,
        "duration_seconds": duration_seconds,
    }
