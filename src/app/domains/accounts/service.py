import uuid
import logging

from fastapi import HTTPException, status
from sqlalchemy.exc import IntegrityError
from sqlalchemy.orm import Session

from app.domains.accounts import repository as account_repo
from app.core.config import settings
from app.domains.accounts.models import ImportMethod, TradingAccount
from app.domains.accounts.mt5_core_client import (
    Mt5CoreClient,
    Mt5CoreClientError,
    Mt5CoreClientJobFailed,
    Mt5CoreClientTransientJobFailed,
    Mt5CoreClientTimeout,
    Mt5CoreClientWorkerUnavailable,
)
from app.domains.accounts.schemas import (
    AccountBalanceResponse,
    AccountConnectRequest,
    TraderAccessRequest,
)
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
    broker_server = account_repo.resolve_mt5_server_name(db, payload.broker_server)
    broker_name = (payload.broker_name or "").strip() or broker_server
    meta_account_id = _build_pseudo_meta_account_id(
        broker_login=payload.broker_login,
        broker_server=broker_server,
        platform=payload.platform.value,
    )

    existing = account_repo.get_account_by_user_and_meta_id(
        db,
        user_id=current_user.id,
        meta_account_id=meta_account_id,
    )
    if existing is not None and not existing.is_archived:
        raise HTTPException(status_code=status.HTTP_409_CONFLICT, detail="Account already connected.")

    # Persist a pending account and let the existing bootstrap worker perform
    # credential verification and history import. MT5 account switches can take
    # several seconds; keeping them off the request path makes account creation
    # immediate while the connection state still reports the real outcome.
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

    if existing is not None and existing.is_archived:
        account = account_repo.reactivate_account(
            db,
            account=existing,
            meta_account_id=meta_account_id,
            broker_name=broker_name,
            broker_login=payload.broker_login,
            broker_server=broker_server,
            encrypted_investor_password=encrypted_investor_password,
            encrypted_trader_password=encrypted_trader_password,
            account_type=payload.account_type,
            platform=payload.platform,
            currency=payload.currency,
            timezone=payload.timezone,
            broker_utc_offset=payload.broker_utc_offset,
            display_name=payload.display_name,
            import_method=ImportMethod.auto_sync,
        )
    else:
        account = account_repo.create_account(
            db,
            user_id=current_user.id,
            meta_account_id=meta_account_id,
            broker_name=broker_name,
            broker_login=payload.broker_login,
            broker_server=broker_server,
            encrypted_investor_password=encrypted_investor_password,
            encrypted_trader_password=encrypted_trader_password,
            account_type=payload.account_type,
            platform=payload.platform,
            currency=payload.currency,
            timezone=payload.timezone,
            broker_utc_offset=payload.broker_utc_offset,
            display_name=payload.display_name,
            import_method=ImportMethod.auto_sync,
            id=uuid.UUID(account_id_to_verify),
        )

    account_repo.schedule_bootstrap_dispatch(db, account_id=account.id)
    try:
        db.commit()
    except IntegrityError as exc:
        db.rollback()
        raise HTTPException(
            status_code=status.HTTP_409_CONFLICT, detail="Account already connected."
        ) from exc

    db.refresh(account)
    from app.tasks.journal_sync_tasks import dispatch_bootstrap_account

    dispatch_bootstrap_account(account.id)

    return account


def _enrich_account_summaries(
    db: Session,
    accounts: list[TradingAccount],
) -> list[TradingAccount]:
    if not accounts:
        return accounts

    latest_snapshots = account_repo.get_latest_snapshots_for_accounts(
        db,
        account_ids=[account.id for account in accounts],
    )
    trade_counts = account_repo.count_closed_trades_for_accounts(
        db,
        account_ids=[account.id for account in accounts],
    )
    for account in accounts:
        snapshot = latest_snapshots.get(account.id)
        account.latest_balance = snapshot.balance if snapshot is not None else None
        account.latest_equity = snapshot.equity if snapshot is not None else None
        account.closed_trade_count = trade_counts.get(account.id, 0)
    return accounts


def list_accounts(db: Session, *, current_user: User) -> list[TradingAccount]:
    accounts = account_repo.list_accounts_for_user(db, current_user.id)
    return _enrich_account_summaries(db, accounts)


def get_account(
    db: Session, *, current_user: User, account_id: uuid.UUID
) -> TradingAccount:
    account = account_repo.get_account_by_id_for_user(db, account_id, current_user.id)
    if account is None:
        raise HTTPException(
            status_code=status.HTTP_404_NOT_FOUND, detail="Trading account not found."
        )
    return _enrich_account_summaries(db, [account])[0]


async def enable_trader_access(
    db: Session,
    *,
    current_user: User,
    account_id: uuid.UUID,
    payload: TraderAccessRequest,
) -> TradingAccount:
    account = get_account(
        db,
        current_user=current_user,
        account_id=account_id,
    )
    if account.is_archived or account.import_method != ImportMethod.auto_sync:
        raise HTTPException(
            status_code=status.HTTP_409_CONFLICT,
            detail="Only an active broker-connected MT5 account can enable full access.",
        )

    client = Mt5CoreClient(
        poll_timeout=settings.MT5_CORE_FAST_VERIFY_TIMEOUT_SECONDS,
        poll_interval=settings.MT5_CORE_FAST_VERIFY_POLL_INTERVAL_SECONDS,
    )
    try:
        result = await client.verify_credentials(
            account_id=str(account.id),
            login=account.broker_login,
            password=payload.trader_password,
            server=account.broker_server,
            broker=account.broker_name,
        )
    except Mt5CoreClientJobFailed as exc:
        raise HTTPException(
            status_code=status.HTTP_400_BAD_REQUEST,
            detail="MT5 rejected this password. Enter the full trading password, not the investor password.",
        ) from exc
    except Mt5CoreClientTimeout as exc:
        raise HTTPException(
            status_code=status.HTTP_504_GATEWAY_TIMEOUT,
            detail="MT5 verification did not finish in time. Please try again.",
        ) from exc
    except (
        Mt5CoreClientWorkerUnavailable,
        Mt5CoreClientTransientJobFailed,
        Mt5CoreClientError,
    ) as exc:
        raise HTTPException(
            status_code=status.HTTP_503_SERVICE_UNAVAILABLE,
            detail="MT5 verification is temporarily unavailable.",
        ) from exc

    if (
        not is_valid_mt5_verification_result(
            result,
            requested_login=account.broker_login,
            requested_server=account.broker_server,
        )
        or not bool(result.get("trade_allowed"))
    ):
        raise HTTPException(
            status_code=status.HTTP_400_BAD_REQUEST,
            detail="This credential does not provide full trading access. Enter the full trading password.",
        )

    account.encrypted_trader_password = encrypt_secret(payload.trader_password)
    db.commit()
    db.refresh(account)
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
    account_repo.archive_account(db, account)
    db.commit()


def unarchive_account(
    db: Session, *, current_user: User, account_id: uuid.UUID
) -> TradingAccount:
    """Reconnect a previously archived account, resuming syncing. Idempotent:
    unarchiving an already-active account is a no-op that returns it unchanged."""
    account = get_account(db, current_user=current_user, account_id=account_id)
    if account.is_archived:
        account_repo.unarchive_account(db, account)
        db.commit()
        db.refresh(account)
    return account


def delete_account(
    db: Session, *, current_user: User, account_id: uuid.UUID
) -> None:
    """Permanently delete the account and all of its trades, snapshots, and
    journals. Unlike disconnect_account (soft archive), this is irreversible."""
    account = get_account(db, current_user=current_user, account_id=account_id)
    account_repo.hard_delete_account(db, account)
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


