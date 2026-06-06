import logging
import uuid
from datetime import datetime, timedelta, timezone

from app.core.celery_app import celery_app
from app.core.config import settings
from app.core.database import SessionLocal
from app.domains.accounts import repository as account_repo
from app.domains.accounts.models import TradingAccountStatus
from app.domains.accounts.sync_orchestrator import orchestrate_mt5_sync
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
            account_repo.set_account_sync_error(db, account, str(exc)[:500])
            db.commit()
            logger.exception("Journal sync failed for single account_id=%s", account_id)
            return {"status": "error", "reason": str(exc)[:200]}


@celery_app.task(name="journal.sync_all_mt5_accounts")
def sync_all_mt5_accounts() -> dict:
    """
    Periodic Celery beat task: triggers the new mt5-core client sync flow
    for every trading account that uses sync_provider=headless_mt5.
    """
    import anyio

    cycle_started = datetime.now(timezone.utc)
    triggered = 0
    skipped = 0
    failed = 0
    inserted_total = 0

    with SessionLocal() as db:
        active_after = cycle_started - timedelta(minutes=settings.ACTIVE_USER_WINDOW_MINUTES)
        account_ids = [
            account.id
            for account in account_repo.list_active_mt5_sync_candidates(
                db,
                active_after=active_after,
                now=cycle_started,
            )
        ]

    logger.info("MT5 sync cycle | candidates=%s", len(account_ids))

    for account_id in account_ids:
        try:
            with SessionLocal() as db_session:
                acct = account_repo.get_account_by_id(db_session, account_id)
                if acct is None or acct.status == TradingAccountStatus.disconnected:
                    skipped += 1
                    continue

                async def _run_sync():
                    return await orchestrate_mt5_sync(
                        db_session,
                        account=acct,
                        trigger="recurring",
                    )

                res = anyio.run(_run_sync)
                if res.outcome == "success":
                    inserted_total += res.inserted_trades
                    triggered += 1
                else:
                    skipped += 1
        except Exception as exc:  # noqa: BLE001
            failed += 1
            logger.exception("MT5 celery sync failed for account_id=%s", account_id)

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
        "inserted_trades": inserted_total,
        "duration_seconds": duration_seconds,
    }
