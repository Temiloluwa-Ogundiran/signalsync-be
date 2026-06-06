import uuid

from fastapi import APIRouter, Depends, HTTPException, Response, status
from sqlalchemy.orm import Session

from app.core.config import settings
from app.core.database import get_db
from app.domains.accounts import repository as account_repo
from app.domains.accounts import service as account_service
from app.domains.accounts.schemas import (
    AccountConnectRequest,
    AccountResponse,
    AccountUpdateRequest,
)
from app.domains.accounts.sync_orchestrator import orchestrate_mt5_sync
from app.domains.users.models import User
from app.shared.deps import get_current_user

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


@router.get("/{account_id}", response_model=AccountResponse)
def get_account(
    account_id: uuid.UUID,
    db: Session = Depends(get_db),
    current_user: User = Depends(get_current_user),
) -> AccountResponse:
    account = account_service.get_account(db, current_user=current_user, account_id=account_id)
    return AccountResponse.model_validate(account)


@router.delete("/{account_id}", status_code=status.HTTP_204_NO_CONTENT)
def disconnect_account(
    account_id: uuid.UUID,
    db: Session = Depends(get_db),
    current_user: User = Depends(get_current_user),
) -> Response:
    account_service.disconnect_account(db, current_user=current_user, account_id=account_id)
    return Response(status_code=status.HTTP_204_NO_CONTENT)


@router.post("/{account_id}/sync")
async def manual_sync(
    account_id: uuid.UUID,
    db: Session = Depends(get_db),
    current_user: User = Depends(get_current_user),
) -> dict:
    account = account_service.get_account(db, current_user=current_user, account_id=account_id)
    from app.domains.accounts.models import SyncProvider

    if account.sync_provider == SyncProvider.headless_mt5:
        result = await orchestrate_mt5_sync(
            db,
            account=account,
            trigger="manual",
        )
        if result.outcome == "success":
            return {
                "inserted_trades": result.inserted_trades,
                "touched_trading_dates": result.touched_trading_dates,
            }
        return {
            "status": result.outcome,
            "retry_after_seconds": result.retry_after_seconds,
            "message": result.message,
        }

    return account_service.sync_account(db, current_user=current_user, account_id=account_id)


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
