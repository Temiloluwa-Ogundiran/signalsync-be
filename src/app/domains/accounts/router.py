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
)
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
    from app.domains.accounts.models import SyncProvider

    account = account_service.get_account(db, current_user=current_user, account_id=account_id)

    if account.sync_provider == SyncProvider.headless_mt5:
        from app.domains.accounts.sync import sync_account_deals_mt5
        from app.domains.accounts.models import TradingAccountConnectionState
        from datetime import datetime, timezone
        
        result = await sync_account_deals_mt5(db, account=account)
        if account.connection_state == TradingAccountConnectionState.bootstrap_failed:
            account_repo.mark_account_ready_for_stats(db, account, synced_at=datetime.now(timezone.utc))
        else:
            account_repo.set_account_last_synced_at(db, account, datetime.now(timezone.utc))
        db.commit()
        return {
            "inserted_trades": result.inserted_trades,
            "touched_trading_dates": result.touched_trading_dates,
        }

    return account_service.sync_account(db, current_user=current_user, account_id=account_id)
