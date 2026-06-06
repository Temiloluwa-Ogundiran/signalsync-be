import uuid
import logging
from datetime import datetime, timezone

from fastapi import HTTPException, status
from sqlalchemy.exc import IntegrityError
from sqlalchemy.orm import Session

from app.core.config import settings
from app.domains.accounts import repository as account_repo
from app.domains.accounts.models import SyncProvider, TradingAccount
from app.domains.accounts.schemas import AccountConnectRequest
from app.domains.accounts.sync import sync_account_deals, ingest_mt5_core_history_result
from app.domains.users.models import User
from app.shared.utils.encryption import encrypt_secret
from app.shared.utils.timezone import validate_timezone_name


logger = logging.getLogger(__name__)


def _build_pseudo_meta_account_id(
    *, broker_login: str, broker_server: str, platform: str
) -> str:
    return f"{platform}:{broker_server.strip()}:{broker_login.strip()}"


async def connect_account(
    db: Session,
    *,
    current_user: User,
    payload: AccountConnectRequest,
) -> TradingAccount:
    from datetime import timedelta
    from app.domains.accounts.mt5_core_client import (
        Mt5CoreClient,
        Mt5CoreClientBackpressure,
        Mt5CoreClientJobFailed,
        Mt5CoreClientRateLimited,
        Mt5CoreClientTimeout,
        Mt5CoreClientError,
    )
    from app.domains.accounts.models import TradingAccountConnectionState

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

    # Generate or reuse the account ID for verification boundary
    account_id_to_verify = str(existing.id) if existing is not None else str(uuid.uuid4())

    # Step 1: Verification Boundary (Call mt5-core verification job before saving)
    client = Mt5CoreClient()
    try:
        await client.verify_credentials(
            account_id=account_id_to_verify,
            login=payload.broker_login,
            password=payload.investor_password,
            server=payload.broker_server,
            broker=broker_name,
        )
    except Mt5CoreClientJobFailed as exc:
        raise HTTPException(
            status_code=status.HTTP_400_BAD_REQUEST,
            detail={
                "code": "INVALID_CREDENTIALS",
                "message": f"Credential verification failed: {str(exc)}",
            },
        )
    except Mt5CoreClientRateLimited as exc:
        raise HTTPException(
            status_code=status.HTTP_429_TOO_MANY_REQUESTS,
            detail={
                "code": exc.code,
                "message": str(exc),
            },
            headers={"Retry-After": str(exc.retry_after_seconds)}
            if exc.retry_after_seconds is not None
            else None,
        )
    except Mt5CoreClientBackpressure as exc:
        raise HTTPException(
            status_code=status.HTTP_503_SERVICE_UNAVAILABLE,
            detail={
                "code": exc.code,
                "message": str(exc),
            },
            headers={"Retry-After": str(exc.retry_after_seconds)}
            if exc.retry_after_seconds is not None
            else None,
        )
    except Mt5CoreClientTimeout as exc:
        raise HTTPException(
            status_code=status.HTTP_503_SERVICE_UNAVAILABLE,
            detail={
                "code": "MT5_CORE_TIMEOUT",
                "message": f"Credential verification timed out: {str(exc)}",
            },
        )
    except Mt5CoreClientError as exc:
        raise HTTPException(
            status_code=status.HTTP_503_SERVICE_UNAVAILABLE,
            detail={
                "code": "MT5_CORE_ERROR",
                "message": f"Credential verification failed: {str(exc)}",
            },
        )

    # Step 2: Successful verification becomes the persistence boundary.
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
            id=uuid.UUID(account_id_to_verify),
        )

    try:
        db.commit()
    except IntegrityError as exc:
        db.rollback()
        raise HTTPException(
            status_code=status.HTTP_409_CONFLICT, detail="Account already connected."
        ) from exc

    db.refresh(account)

    # Step 3: Transition to bootstrapping in a separate database transaction boundary
    account_repo.mark_account_bootstrapping(db, account)
    db.commit()

    # Step 4: Submit initial sync job to bootstrap account history
    from_time = datetime.now(timezone.utc) - timedelta(days=settings.INITIAL_SYNC_LOOKBACK_DAYS)
    try:
        sync_result = await client.submit_history_sync(
            account_id=str(account.id),
            from_time=from_time,
            credentials={
                "login": account.broker_login,
                "password": payload.investor_password,
                "server": account.broker_server,
                "broker": account.broker_name,
            },
        )
        ingest_mt5_core_history_result(db, account=account, result=sync_result)
        account_repo.mark_account_ready_for_stats(db, account, synced_at=datetime.now(timezone.utc))
        db.commit()
    except (Mt5CoreClientJobFailed, Mt5CoreClientTimeout, Mt5CoreClientError) as exc:
        # Warning Recovery: sync failure preserves the persisted account in bootstrap_failed
        logger.warning(
            "Initial post-verification history sync failed/timed out | account_id=%s error=%s",
            account.id,
            str(exc),
        )
        account_repo.mark_account_bootstrap_failed(db, account, str(exc)[:500])
        account_repo.set_account_sync_error(db, account, f"Initial sync failed: {str(exc)[:450]}")
        db.commit()

    return account


def list_accounts(db: Session, *, current_user: User) -> list[TradingAccount]:
    accounts = account_repo.list_accounts_for_user(db, current_user.id)
    if not accounts:
        return accounts

    latest_snapshots = account_repo.get_latest_snapshots_for_accounts(
        db,
        account_ids=[account.id for account in accounts],
    )
    for account in accounts:
        snapshot = latest_snapshots.get(account.id)
        account.latest_balance = snapshot.balance if snapshot is not None else None
        account.latest_equity = snapshot.equity if snapshot is not None else None
    return accounts


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

    if account.sync_provider != SyncProvider.headless_mt5:
        raise HTTPException(
            status_code=status.HTTP_400_BAD_REQUEST,
            detail="This account does not support broker sync.",
        )

    try:
        result = sync_account_deals(db, account=account)
    except HTTPException:
        raise
    except Exception as exc:
        account_repo.set_account_sync_error(db, account, str(exc)[:500])
        db.commit()
        raise HTTPException(
            status_code=status.HTTP_502_BAD_GATEWAY, detail="Account sync failed."
        ) from exc

    return {
        "inserted_trades": result.inserted_trades,
        "touched_trading_dates": result.touched_trading_dates,
    }


def update_account(
    db: Session, *, current_user: User, account_id: uuid.UUID, display_name: str
) -> TradingAccount:
    account = get_account(db, current_user=current_user, account_id=account_id)
    display_name = display_name.strip()
    if not display_name:
        raise HTTPException(
            status_code=status.HTTP_400_BAD_REQUEST,
            detail="Display name cannot be empty."
        )
    account.display_name = display_name
    db.commit()
    db.refresh(account)
    return account


