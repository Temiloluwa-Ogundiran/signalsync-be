import logging
import uuid
from datetime import datetime, timezone

from app.core.celery_app import celery_app
from app.core.database import SessionLocal
from app.models.trading_account import TradingAccountStatus
from app.repositories import sync_lock_repo, trading_account_repo
from app.services.journal_sync_service import sync_account_deals

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
        lock_acquired = sync_lock_repo.try_acquire_cycle_lock(lock_db)
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
                account_ids = [account.id for account in trading_account_repo.list_syncable_accounts(db)]

            for account_id in account_ids:
                attempted += 1
                try:
                    with SessionLocal() as db:
                        account = trading_account_repo.get_by_id(db, account_id)
                        if account is None or account.status in {
                            TradingAccountStatus.disconnected,
                            TradingAccountStatus.error,
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
                        account = trading_account_repo.get_by_id(db, account_id)
                        if account is not None:
                            trading_account_repo.set_sync_error(db, account, str(exc)[:500])
                            db.commit()
        finally:
            sync_lock_repo.release_cycle_lock(lock_db)

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
        account = trading_account_repo.get_by_id(db, account_uuid)
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
            trading_account_repo.set_sync_error(db, account, str(exc)[:500])
            db.commit()
            logger.exception("Journal sync failed for single account_id=%s", account_id)
            return {"status": "error", "reason": str(exc)[:200]}
