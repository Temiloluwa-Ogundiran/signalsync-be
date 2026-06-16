import uuid
from datetime import datetime, timezone

from fastapi import APIRouter, Depends, HTTPException, Response, status
from sqlalchemy.orm import Session

from app.core.database import get_db
from app.domains.accounts import repository as account_repo
from app.domains.accounts import service as account_service
from app.domains.accounts.schemas import (
    AccountBalanceResponse,
    AccountConnectRequest,
    AccountResponse,
    AccountUpdateRequest,
)
from app.domains.accounts.models import SyncProvider
from app.domains.accounts.sync_orchestrator import check_manual_sync_admission
from app.domains.users.models import User
from app.shared.deps import get_current_user
from app.tasks.journal_sync_tasks import sync_account as sync_account_task

router = APIRouter(prefix="/accounts", tags=["accounts"])


@router.post("", response_model=AccountResponse, status_code=status.HTTP_201_CREATED)
async def connect_account(
    payload: AccountConnectRequest,
    db: Session = Depends(get_db),
    current_user: User = Depends(get_current_user),
) -> AccountResponse:
    account = await account_service.connect_account(db, current_user=current_user, payload=payload)
    return AccountResponse.model_validate(account)


@router.get("", response_model=list[AccountResponse])
def list_accounts(
    db: Session = Depends(get_db),
    current_user: User = Depends(get_current_user),
) -> list[AccountResponse]:
    accounts = account_service.list_accounts(db, current_user=current_user)
    return [AccountResponse.model_validate(a) for a in accounts]


@router.delete("/demo", status_code=status.HTTP_204_NO_CONTENT)
def clear_demo(
    db: Session = Depends(get_db),
    current_user: User = Depends(get_current_user),
) -> Response:
    """Remove the user's seeded demo account and all its data (idempotent)."""
    from app.domains.demo.service import clear_demo_account

    clear_demo_account(db, current_user.id)
    db.commit()
    return Response(status_code=status.HTTP_204_NO_CONTENT)


@router.get("/{account_id}", response_model=AccountResponse)
def get_account(
    account_id: uuid.UUID,
    db: Session = Depends(get_db),
    current_user: User = Depends(get_current_user),
) -> AccountResponse:
    account = account_service.get_account(db, current_user=current_user, account_id=account_id)
    return AccountResponse.model_validate(account)


@router.get("/{account_id}/balance", response_model=AccountBalanceResponse)
def get_account_balance(
    account_id: uuid.UUID,
    db: Session = Depends(get_db),
    current_user: User = Depends(get_current_user),
) -> AccountBalanceResponse:
    return account_service.get_account_balance(
        db, current_user=current_user, account_id=account_id
    )


@router.delete("/{account_id}", status_code=status.HTTP_204_NO_CONTENT)
def disconnect_account(
    account_id: uuid.UUID,
    db: Session = Depends(get_db),
    current_user: User = Depends(get_current_user),
) -> Response:
    account_service.disconnect_account(db, current_user=current_user, account_id=account_id)
    return Response(status_code=status.HTTP_204_NO_CONTENT)


@router.post("/{account_id}/sync", status_code=status.HTTP_202_ACCEPTED)
async def manual_sync(
    account_id: uuid.UUID,
    db: Session = Depends(get_db),
    current_user: User = Depends(get_current_user),
) -> dict:
    """Enqueue an on-demand sync and return immediately (202).

    The actual mt5-core round-trip runs on a Celery worker so it never blocks the
    web event loop. Admission (per-user cooldown / burst limit) is enforced here so
    we don't flood the queue; the worker's bounded concurrency throttles mt5-core
    itself. The global per-IP limit applies on top as a safety net.
    """
    account = account_service.get_account(db, current_user=current_user, account_id=account_id)

    if account.sync_provider != SyncProvider.headless_mt5:
        raise HTTPException(
            status_code=status.HTTP_400_BAD_REQUEST,
            detail="This account does not support broker sync.",
        )

    attempted_at = datetime.now(timezone.utc)
    guard = check_manual_sync_admission(db, account=account, attempted_at=attempted_at)
    if guard is not None:
        return {
            "status": guard.outcome,
            "retry_after_seconds": guard.retry_after_seconds,
            "message": guard.message,
        }

    # Record the attempt now so rapid re-taps are counted against the burst limit
    # and concurrent requests observe it before the worker picks the job up.
    account_repo.set_sync_attempt_started(db, account=account, attempted_at=attempted_at)
    db.commit()

    sync_account_task.delay(str(account.id))
    return {"status": "queued", "account_id": str(account.id)}


@router.patch("/{account_id}", response_model=AccountResponse)
def update_account(
    account_id: uuid.UUID,
    payload: AccountUpdateRequest,
    db: Session = Depends(get_db),
    current_user: User = Depends(get_current_user),
) -> AccountResponse:
    account = account_service.update_account(
        db, current_user=current_user, account_id=account_id, display_name=payload.display_name
    )
    return AccountResponse.model_validate(account)
