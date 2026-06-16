from __future__ import annotations

from dataclasses import dataclass
from datetime import datetime, timedelta, timezone
from typing import Literal

from fastapi import HTTPException, status
from sqlalchemy.orm import Session

from app.core.config import settings
from app.domains.accounts import repository as account_repo
from app.domains.accounts.models import TradingAccount
from app.domains.accounts.mt5_core_client import (
    Mt5CoreClientBackpressure,
    Mt5CoreClientError,
    Mt5CoreClientJobFailed,
    Mt5CoreClientRateLimited,
    Mt5CoreClientTimeout,
)
from app.domains.accounts.sync import sync_account_deals_mt5


@dataclass
class Mt5SyncExecutionResult:
    outcome: str
    inserted_trades: int = 0
    touched_trading_dates: int = 0
    retry_after_seconds: int | None = None
    message: str | None = None


def _seconds_until(target: datetime, now: datetime) -> int:
    return max(1, int((target - now).total_seconds() + 0.999))


def _build_guarded_result(
    *,
    outcome: str,
    retry_after_seconds: int | None,
    message: str,
) -> Mt5SyncExecutionResult:
    return Mt5SyncExecutionResult(
        outcome=outcome,
        retry_after_seconds=retry_after_seconds,
        message=message,
    )


def _mt5_invalid_credentials_message() -> str:
    return "MT5 authorization failed. Check the account number, broker server, and investor password."


def _check_manual_sync_admission(
    db: Session,
    *,
    account: TradingAccount,
    attempted_at: datetime,
) -> Mt5SyncExecutionResult | None:
    if account_repo.is_account_sync_locked(db, account.id):
        return _build_guarded_result(
            outcome="in_progress",
            retry_after_seconds=10,
            message="Sync already in progress for this account.",
        )

    if (
        account.next_sync_not_before is not None
        and account.next_sync_not_before > attempted_at
    ):
        retry_after_seconds = _seconds_until(
            account.next_sync_not_before,
            attempted_at,
        )
        return _build_guarded_result(
            outcome="cooldown",
            retry_after_seconds=retry_after_seconds,
            message="Manual sync is on cooldown for this account.",
        )

    window_start = attempted_at - timedelta(
        seconds=settings.MANUAL_SYNC_BURST_WINDOW_SECONDS
    )
    recent_attempts = account_repo.list_recent_sync_attempts_for_user(
        db,
        user_id=account.user_id,
        since=window_start,
    )
    if len(recent_attempts) >= settings.MANUAL_SYNC_BURST_MAX_ATTEMPTS:
        retry_after_seconds = _seconds_until(
            recent_attempts[0] + timedelta(seconds=settings.MANUAL_SYNC_BURST_WINDOW_SECONDS),
            attempted_at,
        )
        return _build_guarded_result(
            outcome="rate_limited",
            retry_after_seconds=retry_after_seconds,
            message="You have reached the manual sync limit for now. Please retry shortly.",
        )

    return None


def check_manual_sync_admission(
    db: Session,
    *,
    account: TradingAccount,
    attempted_at: datetime,
) -> Mt5SyncExecutionResult | None:
    """Public admission check for the API boundary (cooldown / burst limit).

    Returns a guard result to surface to the client, or None when the sync is
    admitted and may be enqueued.
    """
    return _check_manual_sync_admission(
        db,
        account=account,
        attempted_at=attempted_at,
    )


async def orchestrate_mt5_sync(
    db: Session,
    *,
    account: TradingAccount,
    trigger: Literal["bootstrap", "manual", "recurring"],
    lookback_days: int | None = None,
    enforce_admission: bool = True,
) -> Mt5SyncExecutionResult:
    """
    Run a sync for ``account``.

    When ``enforce_admission`` is False the caller is expected to have already
    run admission control (cooldown / burst limit) and recorded the attempt via
    :func:`check_manual_sync_admission` + ``set_sync_attempt_started`` — this is
    how the API hands work to the Celery worker without double-counting attempts.
    """
    attempted_at = datetime.now(timezone.utc)
    if enforce_admission and trigger == "manual":
        admission_result = _check_manual_sync_admission(
            db,
            account=account,
            attempted_at=attempted_at,
        )
        if admission_result is not None:
            return admission_result

    if enforce_admission:
        account_repo.set_sync_attempt_started(
            db,
            account=account,
            attempted_at=attempted_at,
        )

    try:
        result = await sync_account_deals_mt5(
            db,
            account=account,
            lookback_days=lookback_days,
        )
    except HTTPException as exc:
        if exc.status_code == status.HTTP_409_CONFLICT:
            db.rollback()
            return _build_guarded_result(
                outcome="in_progress",
                retry_after_seconds=10,
                message=str(exc.detail),
            )
        raise
    except Mt5CoreClientRateLimited as exc:
        account_repo.mark_sync_retryable(
            db,
            account=account,
            outcome="rate_limited",
            message=str(exc),
            retry_after_seconds=exc.retry_after_seconds,
            attempted_at=attempted_at,
        )
        db.commit()
        return Mt5SyncExecutionResult(
            outcome="rate_limited",
            retry_after_seconds=exc.retry_after_seconds,
            message=str(exc),
        )
    except Mt5CoreClientBackpressure as exc:
        account_repo.mark_sync_retryable(
            db,
            account=account,
            outcome="backpressure",
            message=str(exc),
            retry_after_seconds=exc.retry_after_seconds,
            attempted_at=attempted_at,
        )
        db.commit()
        return Mt5SyncExecutionResult(
            outcome="backpressure",
            retry_after_seconds=exc.retry_after_seconds,
            message=str(exc),
        )
    except Mt5CoreClientTimeout as exc:
        account_repo.mark_sync_retryable(
            db,
            account=account,
            outcome="timeout",
            message=str(exc),
            retry_after_seconds=None,
            attempted_at=attempted_at,
        )
        db.commit()
        return Mt5SyncExecutionResult(outcome="timeout", message=str(exc))
    except Mt5CoreClientJobFailed as exc:
        message = _mt5_invalid_credentials_message()
        account_repo.mark_account_verification_failed(
            db,
            account,
            message,
        )
        account_repo.mark_sync_attention_required(
            db,
            account=account,
            outcome="invalid_credentials",
            message=message,
            attempted_at=attempted_at,
        )
        db.commit()
        return Mt5SyncExecutionResult(
            outcome="invalid_credentials",
            message=message,
        )
    except Mt5CoreClientError as exc:
        account_repo.mark_sync_retryable(
            db,
            account=account,
            outcome="transient_error",
            message=str(exc),
            retry_after_seconds=None,
            attempted_at=attempted_at,
        )
        db.commit()
        return Mt5SyncExecutionResult(
            outcome="transient_error",
            message=str(exc),
        )

    account_repo.mark_sync_success(
        db,
        account=account,
        synced_at=attempted_at,
        next_sync_not_before=(
            attempted_at + timedelta(seconds=settings.MANUAL_SYNC_COOLDOWN_SECONDS)
            if trigger == "manual"
            else None
        ),
    )
    db.commit()
    return Mt5SyncExecutionResult(
        outcome="success",
        inserted_trades=result.inserted_trades,
        touched_trading_dates=result.touched_trading_dates,
    )
