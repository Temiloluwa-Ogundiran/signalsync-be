import hmac
import uuid

from fastapi import APIRouter, Depends, Header, HTTPException, Response, status
from sqlalchemy.orm import Session

from app.core.config import settings
from app.core.database import get_db
from app.domains.accounts import service as account_service
from app.domains.accounts.schemas import (
    AccountConnectRequest,
    AccountResponse,
    MT5WebhookPayload,
)
from app.domains.users.models import User
from app.shared.deps import get_current_user

router = APIRouter(prefix="/accounts", tags=["accounts"])


def _verify_mt5_secret(x_shared_secret: str = Header(...)) -> None:
    """Constant-time comparison of the MT5 shared secret."""
    if not settings.MT5_SERVICE_SHARED_SECRET:
        raise HTTPException(
            status_code=status.HTTP_500_INTERNAL_SERVER_ERROR,
            detail="MT5 shared secret is not configured on this server.",
        )
    is_valid = hmac.compare_digest(
        x_shared_secret.encode("utf-8"),
        settings.MT5_SERVICE_SHARED_SECRET.encode("utf-8"),
    )
    if not is_valid:
        raise HTTPException(
            status_code=status.HTTP_401_UNAUTHORIZED,
            detail="Invalid shared secret.",
        )


@router.post("", response_model=AccountResponse, status_code=status.HTTP_201_CREATED)
def connect_account(
    payload: AccountConnectRequest,
    db: Session = Depends(get_db),
    current_user: User = Depends(get_current_user),
) -> AccountResponse:
    account = account_service.connect_account(db, current_user=current_user, payload=payload)
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
def manual_sync(
    account_id: uuid.UUID,
    db: Session = Depends(get_db),
    current_user: User = Depends(get_current_user),
) -> dict:
    from app.domains.accounts.models import SyncProvider

    account = account_service.get_account(db, current_user=current_user, account_id=account_id)

    if account.sync_provider == SyncProvider.headless_mt5:
        return account_service.trigger_mt5_sync(db, account=account)

    return account_service.sync_account(db, current_user=current_user, account_id=account_id)


# ---------------------------------------------------------------------------
# Headless MT5 webhook receiver
# ---------------------------------------------------------------------------

@router.post(
    "/webhook/mt5-sync",
    dependencies=[Depends(_verify_mt5_secret)],
    status_code=status.HTTP_200_OK,
    tags=["accounts-mt5"],
    summary="Receive MT5 sync callback from headless microservice",
    description=(
        "Server-to-server endpoint. Authenticated via X-Shared-Secret header. "
        "Ingests the enriched deal payload from the headless-mt5-service."
    ),
)
def mt5_sync_webhook(
    payload: MT5WebhookPayload,
    db: Session = Depends(get_db),
) -> dict:
    return account_service.process_mt5_webhook(
        db,
        account_id=payload.account_id,
        broker_server=payload.broker_server,
        sync_status=payload.status,
        deals=[d.model_dump() for d in payload.deals],
        error_message=payload.error_message,
        result_type=payload.result_type,
        summary=payload.summary,
    )
