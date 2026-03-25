import uuid
import logging

import httpx
from fastapi import HTTPException, status
from sqlalchemy.exc import IntegrityError
from sqlalchemy.orm import Session

from app.core.celery_app import celery_app
from app.core.config import settings
from app.models.user import User
from app.repositories import trading_account_repo
from app.schemas.journal_account import JournalAccountConnectRequest
from app.services.journal_sync_service import sync_account_deals
from app.services.metaapi_service import MetaApiProvisioningError, metaapi_service
from app.utils.encryption import encrypt_secret
from app.utils.timezone import validate_timezone_name


logger = logging.getLogger(__name__)


def _format_provisioning_detail(message: str, code: str | None) -> str:
    if code:
        return f"MetaAPI rejected account provisioning ({code}): {message}"
    return f"MetaAPI rejected account provisioning: {message}"


def connect_account(
    db: Session,
    *,
    current_user: User,
    payload: JournalAccountConnectRequest,
):
    if not settings.METAAPI_TOKEN:
        raise HTTPException(
            status_code=status.HTTP_503_SERVICE_UNAVAILABLE,
            detail="MetaAPI is not configured.",
        )

    validate_timezone_name(payload.timezone)
    broker_name = (payload.broker_name or "").strip() or payload.broker_server

    try:
        meta_account_id = metaapi_service.provision_account(
            broker_name=broker_name,
            broker_login=payload.broker_login,
            broker_server=payload.broker_server,
            platform=payload.platform.value,
            investor_password=payload.investor_password,
            trader_password=payload.trader_password,
            display_name=payload.display_name,
        )
    except MetaApiProvisioningError as exc:
        logger.warning(
            "MetaAPI provisioning rejected | status=%s code=%s server=%s login=%s",
            exc.status_code,
            exc.code,
            payload.broker_server,
            payload.broker_login,
        )
        if exc.status_code in {400, 401, 403, 404}:
            raise HTTPException(
                status_code=status.HTTP_400_BAD_REQUEST,
                detail=_format_provisioning_detail(exc.message, exc.code),
            ) from exc
        raise HTTPException(
            status_code=status.HTTP_502_BAD_GATEWAY,
            detail=_format_provisioning_detail(exc.message, exc.code),
        ) from exc
    except httpx.HTTPStatusError as exc:
        error_status = exc.response.status_code
        if error_status in {400, 401, 403, 404}:
            raise HTTPException(
                status_code=status.HTTP_400_BAD_REQUEST,
                detail="Broker credentials were rejected by MetaAPI.",
            ) from exc
        raise HTTPException(
            status_code=status.HTTP_502_BAD_GATEWAY,
            detail="MetaAPI provisioning failed.",
        ) from exc
    except Exception as exc:
        raise HTTPException(
            status_code=status.HTTP_502_BAD_GATEWAY,
            detail="MetaAPI provisioning failed.",
        ) from exc

    existing = trading_account_repo.get_by_user_and_meta_account_id(
        db,
        user_id=current_user.id,
        meta_account_id=meta_account_id,
    )
    if existing is not None and not existing.is_deleted:
        raise HTTPException(status_code=status.HTTP_409_CONFLICT, detail="Account already connected.")

    try:
        encrypted_investor_password = encrypt_secret(payload.investor_password)
        encrypted_trader_password = (
            encrypt_secret(payload.trader_password)
            if payload.trader_password
            else None
        )
    except ValueError as exc:
        error_text = str(exc)
        if "Fernet key must be 32 url-safe base64-encoded bytes" in error_text:
            detail = "Server encryption key format is invalid. Regenerate ENCRYPTION_KEY using Fernet.generate_key()."
        else:
            detail = "Server encryption key is not configured."
        raise HTTPException(
            status_code=status.HTTP_503_SERVICE_UNAVAILABLE,
            detail=detail,
        ) from exc

    if existing is not None and existing.is_deleted:
        account = trading_account_repo.reactivate(
            db,
            account=existing,
            meta_account_id=meta_account_id,
            broker_name=broker_name,
            broker_login=payload.broker_login,
            broker_server=payload.broker_server,
            encrypted_investor_password=encrypted_investor_password,
            encrypted_trader_password=encrypted_trader_password,
            account_type=payload.account_type,
            platform=payload.platform,
            currency=payload.currency,
            timezone=payload.timezone,
            broker_utc_offset=payload.broker_utc_offset,
            display_name=payload.display_name,
        )
    else:
        account = trading_account_repo.create(
            db,
            user_id=current_user.id,
            meta_account_id=meta_account_id,
            broker_name=broker_name,
            broker_login=payload.broker_login,
            broker_server=payload.broker_server,
            encrypted_investor_password=encrypted_investor_password,
            encrypted_trader_password=encrypted_trader_password,
            account_type=payload.account_type,
            platform=payload.platform,
            currency=payload.currency,
            timezone=payload.timezone,
            broker_utc_offset=payload.broker_utc_offset,
            display_name=payload.display_name,
        )

    try:
        db.commit()
    except IntegrityError as exc:
        db.rollback()
        raise HTTPException(status_code=status.HTTP_409_CONFLICT, detail="Account already connected.") from exc

    db.refresh(account)

    # First sync runs asynchronously so connect remains responsive.
    try:
        celery_app.send_task("journal.sync_account", kwargs={"account_id": str(account.id)})
    except Exception:  # noqa: BLE001
        logger.exception("Failed to enqueue first sync for account_id=%s", account.id)

    return account


def list_accounts(db: Session, *, current_user: User):
    return trading_account_repo.list_for_user(db, current_user.id)


def get_account(db: Session, *, current_user: User, account_id: uuid.UUID):
    account = trading_account_repo.get_by_id_for_user(db, account_id, current_user.id)
    if account is None:
        raise HTTPException(status_code=status.HTTP_404_NOT_FOUND, detail="Trading account not found.")
    return account


def disconnect_account(db: Session, *, current_user: User, account_id: uuid.UUID) -> None:
    account = get_account(db, current_user=current_user, account_id=account_id)
    trading_account_repo.soft_disconnect(db, account)
    db.commit()


def sync_account(db: Session, *, current_user: User, account_id: uuid.UUID):
    account = get_account(db, current_user=current_user, account_id=account_id)

    try:
        result = sync_account_deals(db, account=account)
    except HTTPException:
        raise
    except Exception as exc:
        trading_account_repo.set_sync_error(db, account, str(exc))
        db.commit()
        raise HTTPException(status_code=status.HTTP_502_BAD_GATEWAY, detail="Account sync failed.") from exc

    return {
        "inserted_trades": result.inserted_trades,
        "touched_trading_dates": result.touched_trading_dates,
    }
