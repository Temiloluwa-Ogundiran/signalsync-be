from __future__ import annotations

from dataclasses import dataclass
from datetime import datetime, timezone
from typing import Literal

from sqlalchemy.orm import Session

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


async def orchestrate_mt5_sync(
    db: Session,
    *,
    account: TradingAccount,
    trigger: Literal["bootstrap", "manual", "recurring"],
    lookback_days: int | None = None,
) -> Mt5SyncExecutionResult:
    attempted_at = datetime.now(timezone.utc)
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
        account_repo.mark_sync_attention_required(
            db,
            account=account,
            outcome="invalid_credentials",
            message=str(exc),
            attempted_at=attempted_at,
        )
        db.commit()
        return Mt5SyncExecutionResult(
            outcome="invalid_credentials",
            message=str(exc),
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
    )
    db.commit()
    return Mt5SyncExecutionResult(
        outcome="success",
        inserted_trades=result.inserted_trades,
        touched_trading_dates=result.touched_trading_dates,
    )
