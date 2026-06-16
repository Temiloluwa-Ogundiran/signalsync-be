import asyncio
import logging
import uuid
from datetime import datetime, timezone

import redis as sync_redis

from app.core.celery_app import celery_app
from app.core.config import settings
from app.core.database import SessionLocal
from app.domains.accounts import repository as account_repo
from app.domains.accounts.models import TradingAccountStatus
from app.domains.accounts.mt5_core_client import (
    Mt5CoreClient,
    Mt5CoreClientError,
    Mt5CoreClientJobFailed,
    Mt5CoreClientTimeout,
)
from app.domains.accounts.service import (
    _mt5_invalid_credentials_message,
    is_valid_mt5_verification_result,
)
from app.domains.accounts.sync import ingest_mt5_snapshots, sync_account_deals_mt5
from app.domains.accounts.sync_orchestrator import orchestrate_mt5_sync
from app.shared.utils.encryption import decrypt_secret

logger = logging.getLogger(__name__)

_sync_redis: sync_redis.Redis | None = None


def _get_sync_redis() -> sync_redis.Redis:
    global _sync_redis
    if _sync_redis is None:
        _sync_redis = sync_redis.from_url(settings.AI_REDIS_URL, decode_responses=True)
    return _sync_redis


def _bump_data_version(account_id: str) -> None:
    """Increment per-account data_version so the AI tool cache is invalidated."""
    try:
        r = _get_sync_redis()
        key = f"acct:ver:{account_id}"
        new_ver = r.incr(key)
        logger.debug("Bumped data_version for account %s → %s", account_id, new_ver)
    except Exception:
        logger.warning("Could not bump data_version for account %s (Redis unavailable)", account_id)


def _account_uuid_or_skip(account_id: str) -> uuid.UUID | None:
    try:
        return uuid.UUID(account_id)
    except ValueError:
        return None


def _verification_snapshot(result: dict) -> dict:
    return {
        "captured_at": datetime.now(timezone.utc),
        "balance": result.get("balance"),
        "equity": result.get("equity"),
        "floating_pnl": 0,
    }


@celery_app.task(name="journal.bootstrap_account", bind=True, max_retries=0)
def bootstrap_account(self, account_id: str) -> dict:
    """
    Verify a newly connected account, persist a quick account snapshot, then run
    initial history sync in the background.

    This is deliberately outside the POST /accounts request path. Account
    creation returns as soon as encrypted credentials are stored, while MT5/Wine
    latency is reflected through connection_state polling.
    """
    _ = self
    account_uuid = _account_uuid_or_skip(account_id)
    if account_uuid is None:
        return {"status": "skipped", "reason": "invalid_account_id"}

    with SessionLocal() as db:
        account = account_repo.get_account_by_id(db, account_uuid)
        if account is None:
            return {"status": "skipped", "reason": "account_not_found"}
        if account.status == TradingAccountStatus.disconnected:
            return {"status": "skipped", "reason": "account_disconnected"}

        try:
            investor_password = decrypt_secret(account.encrypted_investor_password)
        except ValueError as exc:
            message = "Stored account credentials could not be decrypted."
            logger.warning(
                "Account bootstrap credential decrypt failed | account_id=%s error=%s",
                account.id,
                str(exc),
            )
            account_repo.mark_account_verification_failed(db, account, message)
            db.commit()
            return {"status": "verification_failed", "reason": "credential_decrypt_failed"}

        client = Mt5CoreClient()
        try:
            verification_result = asyncio.run(
                client.verify_credentials(
                    account_id=str(account.id),
                    login=account.broker_login,
                    password=investor_password,
                    server=account.broker_server,
                    broker=account.broker_name,
                )
            )
            if not is_valid_mt5_verification_result(
                verification_result,
                requested_login=account.broker_login,
                requested_server=account.broker_server,
            ):
                message = _mt5_invalid_credentials_message()
                account_repo.mark_account_verification_failed(db, account, message)
                db.commit()
                return {"status": "verification_failed", "reason": "invalid_credentials"}

            ingest_mt5_snapshots(
                db,
                account=account,
                snapshots=[_verification_snapshot(verification_result)],
            )
            account_repo.mark_account_bootstrapping(db, account)
            db.commit()
        except Mt5CoreClientJobFailed as exc:
            message = _mt5_invalid_credentials_message()
            logger.warning(
                "Account bootstrap verification failed | account_id=%s error=%s",
                account.id,
                str(exc),
            )
            account_repo.mark_account_verification_failed(db, account, message)
            db.commit()
            return {"status": "verification_failed", "reason": "invalid_credentials"}
        except (Mt5CoreClientTimeout, Mt5CoreClientError) as exc:
            message = f"Credential verification failed: {str(exc)[:450]}"
            logger.warning(
                "Account bootstrap verification unavailable | account_id=%s error=%s",
                account.id,
                str(exc),
            )
            account_repo.mark_account_verification_failed(db, account, message)
            db.commit()
            return {"status": "verification_failed", "reason": "verification_unavailable"}

        try:
            result = asyncio.run(
                sync_account_deals_mt5(
                    db,
                    account=account,
                    lookback_days=settings.INITIAL_SYNC_LOOKBACK_DAYS,
                )
            )
            account_repo.mark_account_ready_for_stats(
                db,
                account,
                synced_at=datetime.now(timezone.utc),
            )
            db.commit()
            if result.inserted_trades > 0:
                _bump_data_version(account_id)
            return {
                "status": "ready",
                "inserted_trades": result.inserted_trades,
                "touched_trading_dates": result.touched_trading_dates,
            }
        except Mt5CoreClientJobFailed as exc:
            message = _mt5_invalid_credentials_message()
            account_repo.mark_account_verification_failed(db, account, message)
            db.commit()
            return {"status": "verification_failed", "reason": "invalid_credentials"}
        except (Mt5CoreClientTimeout, Mt5CoreClientError) as exc:
            logger.warning(
                "Initial account history sync failed | account_id=%s error=%s",
                account.id,
                str(exc),
            )
            account_repo.mark_account_bootstrap_failed(db, account, str(exc)[:500])
            account_repo.set_account_sync_error(
                db,
                account,
                f"Initial sync failed: {str(exc)[:450]}",
            )
            db.commit()
            return {"status": "bootstrap_failed", "reason": str(exc)}


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
    account_uuid = _account_uuid_or_skip(account_id)
    if account_uuid is None:
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

        # Invalidate the AI tool cache for this account after a successful sync.
        # This ensures that the next AI query sees fresh trade data.
        if result.inserted_trades > 0:
            _bump_data_version(account_id)

        return {
            "status": result.outcome,
            "inserted_trades": result.inserted_trades,
            "touched_trading_dates": result.touched_trading_dates,
        }
