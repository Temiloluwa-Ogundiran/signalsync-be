import uuid
import logging

from fastapi import HTTPException, status
from sqlalchemy.exc import IntegrityError
from sqlalchemy.orm import Session

from app.domains.accounts import repository as account_repo
from app.domains.accounts.models import SyncProvider, TradingAccount
from app.domains.accounts.schemas import AccountBalanceResponse, AccountConnectRequest
from app.domains.users.models import User
from app.shared.utils.encryption import encrypt_secret
from app.shared.utils.timezone import validate_timezone_name


logger = logging.getLogger(__name__)


def _mt5_invalid_credentials_message() -> str:
    return "MT5 authorization failed. Check the account number, broker server, and investor password."


def is_valid_mt5_verification_result(
    result: dict,
    *,
    requested_login: str,
    requested_server: str,
) -> bool:
    verified_login = str(result.get("login") or requested_login).strip()
    verified_server = str(result.get("server") or requested_server).strip()
    return (
        bool(result.get("verified"))
        and verified_login == str(requested_login).strip()
        and verified_server.lower() == str(requested_server).strip().lower()
    )


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

    # Generate or reuse the account ID. Verification happens asynchronously so
    # the request/response path stays sub-second instead of waiting on MT5/Wine.
    account_id_to_verify = str(existing.id) if existing is not None else str(uuid.uuid4())

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
    from app.tasks.journal_sync_tasks import bootstrap_account

    bootstrap_account.delay(str(account.id))

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


def get_account_balance(
    db: Session, *, current_user: User, account_id: uuid.UUID
) -> AccountBalanceResponse:
    """Latest known balance/equity for an account from its most recent snapshot.

    Returns null fields when the account has no snapshot yet (just connected,
    never synced).
    """
    # Reuse the same ownership guard as get_account (404s for non-owners).
    get_account(db, current_user=current_user, account_id=account_id)

    snapshot = account_repo.get_latest_account_snapshot(db, account_id=account_id)
    if snapshot is None:
        return AccountBalanceResponse(account_id=account_id)
    return AccountBalanceResponse(
        account_id=account_id,
        balance=float(snapshot.balance),
        equity=float(snapshot.equity),
        floating_pnl=float(snapshot.floating_pnl),
        as_of=snapshot.snapshot_date,
    )


def disconnect_account(
    db: Session, *, current_user: User, account_id: uuid.UUID
) -> None:
    account = get_account(db, current_user=current_user, account_id=account_id)
    account_repo.soft_disconnect_account(db, account)
    db.commit()


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


