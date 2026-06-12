import asyncio
import logging
import uuid

from app.core.celery_app import celery_app
from app.core.database import SessionLocal
from app.domains.accounts import repository as account_repo
from app.domains.accounts.models import TradingAccountStatus
from app.domains.accounts.sync_orchestrator import orchestrate_mt5_sync

logger = logging.getLogger(__name__)


@celery_app.task(name="journal.sync_account", bind=True, max_retries=3)
def sync_account(self, account_id: str) -> dict:
    """
    Execute an on-demand sync for a single account.

    Enqueued by the API (POST /accounts/{id}/sync). This is the ONLY sync entry
    point — there is no periodic/scheduled sync. Worker-pool concurrency bounds
    how many mt5-core jobs run at once, which is what protects the headless
    service from load. Mutual exclusion per account is enforced by the Postgres
    advisory lock inside the orchestrator, so it is safe across worker processes.
    """
    try:
        account_uuid = uuid.UUID(account_id)
    except ValueError:
        return {"status": "skipped", "reason": "invalid_account_id"}

    with SessionLocal() as db:
        account = account_repo.get_account_by_id(db, account_uuid)
        if account is None:
            return {"status": "skipped", "reason": "account_not_found"}
        if account.status == TradingAccountStatus.disconnected:
            return {"status": "skipped", "reason": "account_disconnected"}

        # Admission (cooldown/rate-limit/attempt recording) is performed by the API
        # before enqueueing, so the worker runs the sync directly.
        result = asyncio.run(
            orchestrate_mt5_sync(
                db,
                account=account,
                trigger="manual",
                enforce_admission=False,
            )
        )
        return {
            "status": result.outcome,
            "inserted_trades": result.inserted_trades,
            "touched_trading_dates": result.touched_trading_dates,
        }
