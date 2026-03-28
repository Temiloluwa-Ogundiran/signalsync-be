import uuid

from fastapi import APIRouter, Depends, Response, status
from sqlalchemy.orm import Session

from app.api.deps import get_current_user
from app.core.database import get_db
from app.models.user import User
from app.schemas.journal_account import JournalAccountConnectRequest, JournalAccountResponse
from app.services import journal_account_service

router = APIRouter(prefix="/journal/accounts", tags=["journal-accounts"])


@router.post("", response_model=JournalAccountResponse, status_code=status.HTTP_201_CREATED)
def connect_account(
    payload: JournalAccountConnectRequest,
    db: Session = Depends(get_db),
    current_user: User = Depends(get_current_user),
) -> JournalAccountResponse:
    account = journal_account_service.connect_account(db, current_user=current_user, payload=payload)
    return JournalAccountResponse.model_validate(account)


@router.get("", response_model=list[JournalAccountResponse])
def list_accounts(
    db: Session = Depends(get_db),
    current_user: User = Depends(get_current_user),
) -> list[JournalAccountResponse]:
    accounts = journal_account_service.list_accounts(db, current_user=current_user)
    return [JournalAccountResponse.model_validate(a) for a in accounts]


@router.get("/{account_id}", response_model=JournalAccountResponse)
def get_account(
    account_id: uuid.UUID,
    db: Session = Depends(get_db),
    current_user: User = Depends(get_current_user),
) -> JournalAccountResponse:
    account = journal_account_service.get_account(db, current_user=current_user, account_id=account_id)
    return JournalAccountResponse.model_validate(account)


@router.delete("/{account_id}", status_code=status.HTTP_204_NO_CONTENT)
def disconnect_account(
    account_id: uuid.UUID,
    db: Session = Depends(get_db),
    current_user: User = Depends(get_current_user),
) -> Response:
    journal_account_service.disconnect_account(db, current_user=current_user, account_id=account_id)
    return Response(status_code=status.HTTP_204_NO_CONTENT)


@router.post("/{account_id}/sync")
def manual_sync(
    account_id: uuid.UUID,
    db: Session = Depends(get_db),
    current_user: User = Depends(get_current_user),
) -> dict:
    return journal_account_service.sync_account(db, current_user=current_user, account_id=account_id)
