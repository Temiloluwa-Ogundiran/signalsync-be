import uuid
import logging
from typing import Any
from datetime import datetime, timezone

import httpx
from fastapi import HTTPException, status
from sqlalchemy.exc import IntegrityError
from sqlalchemy.orm import Session

from app.core.config import settings
from app.domains.accounts import repository as account_repo
from app.domains.accounts.metaapi import is_transient_metaapi_error
from app.domains.accounts.models import SyncProvider, TradingAccount
from app.domains.accounts.schemas import AccountConnectRequest
from app.domains.accounts.sync import ingest_mt5_deals, sync_account_deals
from app.domains.users.models import User
from app.shared.utils.encryption import decrypt_secret, encrypt_secret
from app.shared.utils.timezone import validate_timezone_name


logger = logging.getLogger(__name__)


def _build_pseudo_meta_account_id(
    *, broker_login: str, broker_server: str, platform: str
) -> str:
    return f"{platform}:{broker_server.strip()}:{broker_login.strip()}"


def connect_account(
    db: Session,
    *,
    current_user: User,
    payload: AccountConnectRequest,
) -> TradingAccount:
    validate_timezone_name(payload.timezone)
    broker_name = (payload.broker_name or "").strip() or payload.broker_server
    meta_account_id = _build_pseudo_meta_account_id(
        broker_login=payload.broker_login,
        broker_server=payload.broker_server,
        platform=payload.platform.value,
    )

    existing = account_repo.get_account_by_user_and_meta_id(
        db,
        user_id=current_user.id,
        meta_account_id=meta_account_id,
    )
    if existing is not None and not existing.is_deleted:
        raise HTTPException(status_code=status.HTTP_409_CONFLICT, detail="Account already connected.")

    try:
        encrypted_investor_password = encrypt_secret(payload.investor_password)
        encrypted_trader_password = (
            encrypt_secret(payload.trader_password) if payload.trader_password else None
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
        account = account_repo.reactivate_account(
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
            sync_provider=SyncProvider.headless_mt5,
        )
    else:
        account = account_repo.create_account(
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
            sync_provider=SyncProvider.headless_mt5,
        )

    try:
        db.commit()
    except IntegrityError as exc:
        db.rollback()
        raise HTTPException(
            status_code=status.HTTP_409_CONFLICT, detail="Account already connected."
        ) from exc

    db.refresh(account)

    try:
        trigger_mt5_sync(db, account=account, mode="verify")
    except HTTPException as exc:
        logger.warning(
            "Failed to trigger verification sync | account_id=%s detail=%s",
            account.id,
            exc.detail,
        )

    return account


def list_accounts(db: Session, *, current_user: User) -> list[TradingAccount]:
    return account_repo.list_accounts_for_user(db, current_user.id)


def get_account(
    db: Session, *, current_user: User, account_id: uuid.UUID
) -> TradingAccount:
    account = account_repo.get_account_by_id_for_user(db, account_id, current_user.id)
    if account is None:
        raise HTTPException(
            status_code=status.HTTP_404_NOT_FOUND, detail="Trading account not found."
        )
    return account


def disconnect_account(
    db: Session, *, current_user: User, account_id: uuid.UUID
) -> None:
    account = get_account(db, current_user=current_user, account_id=account_id)
    account_repo.soft_disconnect_account(db, account)
    db.commit()


def sync_account(
    db: Session, *, current_user: User, account_id: uuid.UUID
) -> dict:
    account = get_account(db, current_user=current_user, account_id=account_id)

    try:
        result = sync_account_deals(db, account=account)
    except HTTPException:
        raise
    except Exception as exc:
        if is_transient_metaapi_error(exc):
            account_repo.set_account_sync_warning(
                db,
                account,
                f"Transient MetaAPI sync error: {str(exc)[:450]}",
            )
            db.commit()
            raise HTTPException(
                status_code=status.HTTP_503_SERVICE_UNAVAILABLE,
                detail="MetaAPI timed out while syncing. Please retry in a moment.",
            ) from exc
        account_repo.set_account_sync_error(db, account, str(exc)[:500])
        db.commit()
        raise HTTPException(
            status_code=status.HTTP_502_BAD_GATEWAY, detail="Account sync failed."
        ) from exc

    return {
        "inserted_trades": result.inserted_trades,
        "touched_trading_dates": result.touched_trading_dates,
    }


# ---------------------------------------------------------------------------
# MT5 headless service integration
# ---------------------------------------------------------------------------


def process_mt5_webhook(
    db: Session,
    *,
    account_id: uuid.UUID,
    broker_server: str,
    sync_status: str,
    deals: list[dict[str, Any]],
    error_message: str | None,
    result_type: str | None = None,
    summary: dict[str, Any] | None = None,
) -> dict:
    """
    Handle the async callback from the headless MT5 microservice.

    Called by the webhook receiver after validating the shared secret.
    Looks up the account, ingests deals, and updates account sync status.
    """
    account = account_repo.get_account_by_id(db, account_id)
    if account is None:
        logger.warning("MT5 webhook received for unknown account_id=%s", account_id)
        return {"status": "skipped", "reason": "account_not_found"}
    if broker_server and broker_server != account.broker_server:
        logger.warning(
            "MT5 webhook broker mismatch | account_id=%s expected=%s got=%s",
            account_id,
            account.broker_server,
            broker_server,
        )

    if sync_status == "error":
        reason = (result_type or "error").strip().lower()
        detail = (error_message or "unknown")[:500]
        if reason == "invalid_credentials":
            account_repo.mark_account_verification_failed(
                db, account, f"Credential verification failed: {detail}"
            )
            account_repo.set_account_sync_error(
                db, account, f"MT5 verification error: {detail}"
            )
            db.commit()
            return {"status": "error", "reason": detail, "result_type": reason}

        if reason == "transient_error":
            account_repo.mark_account_pending_verification(db, account)
            account_repo.set_account_sync_warning(
                db, account, f"Transient MT5 verification error: {detail}"
            )
            db.commit()
            return {"status": "retry", "reason": detail, "result_type": reason}

        account_repo.set_account_sync_error(
            db,
            account,
            f"MT5 sync error: {detail}",
        )
        account_repo.mark_account_bootstrap_failed(db, account, detail)
        db.commit()
        logger.error(
            "MT5 sync reported error | account_id=%s error=%s",
            account_id,
            error_message,
        )
        return {"status": "error", "reason": error_message}

    webhook_mode = (summary or {}).get("mode")
    if webhook_mode == "verify" and sync_status == "ok":
        account_repo.mark_account_bootstrapping(db, account)
        db.commit()
        try:
            trigger_mt5_sync(db, account=account, mode="sync")
        except HTTPException as exc:
            detail = str(exc.detail)[:500]
            account_repo.mark_account_bootstrap_failed(
                db, account, f"Bootstrap sync trigger failed: {detail}"
            )
            account_repo.set_account_sync_error(
                db, account, f"Bootstrap sync trigger failed: {detail}"
            )
            db.commit()
            return {"status": "error", "reason": detail, "result_type": "bootstrap_failed"}
        return {"status": "ok", "result_type": "verified"}

    if sync_status == "empty" or not deals:
        synced_at = datetime.now(timezone.utc)
        account_repo.set_account_last_synced_at(db, account, synced_at)
        account_repo.mark_account_ready_for_stats(db, account, synced_at=synced_at)
        db.commit()
        logger.info("MT5 sync returned empty result | account_id=%s", account_id)
        return {"status": "ok", "inserted_trades": 0, "touched_trading_dates": 0}

    try:
        result = ingest_mt5_deals(db, account=account, deals=deals)
        account_repo.mark_account_ready_for_stats(
            db, account, synced_at=datetime.now(timezone.utc)
        )
        db.commit()
    except Exception as exc:  # noqa: BLE001
        account_repo.set_account_sync_error(db, account, str(exc)[:500])
        account_repo.mark_account_bootstrap_failed(db, account, str(exc)[:500])
        db.commit()
        logger.exception("MT5 deal ingestion failed | account_id=%s", account_id)
        raise HTTPException(
            status_code=status.HTTP_500_INTERNAL_SERVER_ERROR,
            detail="Failed to ingest MT5 deals.",
        ) from exc

    return {
        "status": "ok",
        "inserted_trades": result.inserted_trades,
        "touched_trading_dates": result.touched_trading_dates,
        "summary": summary or {},
    }


def trigger_mt5_sync(db: Session, *, account: TradingAccount, mode: str = "sync") -> dict:
    """
    Send a sync request to the headless MT5 microservice for one account.

    Decrypts the investor password, builds the SyncRequest payload, and POSTs
    it to the configured MT5_SERVICE_URL. Returns 202 metadata on success.
    """
    if not settings.MT5_SERVICE_URL:
        raise HTTPException(
            status_code=status.HTTP_503_SERVICE_UNAVAILABLE,
            detail="MT5 sync service is not configured.",
        )

    try:
        investor_password = decrypt_secret(account.encrypted_investor_password)
    except Exception as exc:  # noqa: BLE001
        raise HTTPException(
            status_code=status.HTTP_500_INTERNAL_SERVER_ERROR,
            detail="Failed to decrypt account credentials.",
        ) from exc

    # Determine the delta timestamp: Unix seconds of the last sync, or 0 for first sync.
    last_sync_ts = (
        int(account.last_synced_at.timestamp()) if account.last_synced_at else 0
    )

    known_copy_magics: list[int] = list(account.copy_magic_numbers or [])
    try:
        login_id = int(account.broker_login)
    except ValueError as exc:
        raise HTTPException(
            status_code=status.HTTP_400_BAD_REQUEST,
            detail="Broker login must be numeric for MT5 sync.",
        ) from exc

    payload = {
        "account_id": str(account.id),
        "broker_server": account.broker_server,
        "login_id": login_id,
        "investor_password": investor_password,
        "last_sync_timestamp": last_sync_ts,
        "known_copy_magics": known_copy_magics,
        "mode": mode,
    }

    try:
        with httpx.Client(timeout=15.0) as client:
            response = client.post(
                f"{settings.MT5_SERVICE_URL.rstrip('/')}/sync",
                json=payload,
                headers={"X-Shared-Secret": settings.MT5_SERVICE_SHARED_SECRET},
            )
            response.raise_for_status()
        data = response.json()
        logger.info(
            "MT5 sync triggered | account_id=%s task_id=%s mode=%s",
            account.id,
            data.get("task_id"),
            mode,
        )
        return {"status": "queued", "task_id": data.get("task_id"), "mode": mode}
    except httpx.HTTPStatusError as exc:
        raise HTTPException(
            status_code=status.HTTP_502_BAD_GATEWAY,
            detail=f"MT5 service rejected sync request: {exc.response.status_code}",
        ) from exc
    except Exception as exc:  # noqa: BLE001
        raise HTTPException(
            status_code=status.HTTP_502_BAD_GATEWAY,
            detail="MT5 sync service is unreachable.",
        ) from exc
