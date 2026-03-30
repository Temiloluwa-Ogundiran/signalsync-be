import uuid
from datetime import datetime
from typing import Optional

from sqlalchemy import select
from sqlalchemy.orm import Session

from app.models.trading_account import TradingAccount, TradingAccountStatus


def create(
    db: Session,
    *,
    user_id: uuid.UUID,
    meta_account_id: str,
    broker_name: str,
    broker_login: str,
    broker_server: str,
    encrypted_investor_password: str,
    encrypted_trader_password: Optional[str],
    account_type,
    platform,
    currency: str,
    timezone: str,
    broker_utc_offset: int,
    display_name: Optional[str],
) -> TradingAccount:
    account = TradingAccount(
        user_id=user_id,
        meta_account_id=meta_account_id,
        broker_name=broker_name,
        broker_login=broker_login,
        broker_server=broker_server,
        encrypted_investor_password=encrypted_investor_password,
        encrypted_trader_password=encrypted_trader_password,
        account_type=account_type,
        platform=platform,
        currency=currency,
        timezone=timezone,
        broker_utc_offset=broker_utc_offset,
        display_name=display_name,
        status=TradingAccountStatus.pending_sync,
    )
    db.add(account)
    db.flush()
    return account


def reactivate(
    db: Session,
    *,
    account: TradingAccount,
    meta_account_id: str,
    broker_name: str,
    broker_login: str,
    broker_server: str,
    encrypted_investor_password: str,
    encrypted_trader_password: Optional[str],
    account_type,
    platform,
    currency: str,
    timezone: str,
    broker_utc_offset: int,
    display_name: Optional[str],
) -> TradingAccount:
    account.meta_account_id = meta_account_id
    account.broker_name = broker_name
    account.broker_login = broker_login
    account.broker_server = broker_server
    account.encrypted_investor_password = encrypted_investor_password
    account.encrypted_trader_password = encrypted_trader_password
    account.account_type = account_type
    account.platform = platform
    account.currency = currency
    account.timezone = timezone
    account.broker_utc_offset = broker_utc_offset
    account.display_name = display_name
    account.is_deleted = False
    account.status = TradingAccountStatus.pending_sync
    account.sync_error_message = None
    db.flush()
    return account


def get_by_id(db: Session, account_id: uuid.UUID) -> Optional[TradingAccount]:
    stmt = select(TradingAccount).where(
        TradingAccount.id == account_id,
        TradingAccount.is_deleted.is_(False),
    )
    return db.execute(stmt).scalar_one_or_none()


def get_by_id_for_user(db: Session, account_id: uuid.UUID, user_id: uuid.UUID) -> Optional[TradingAccount]:
    stmt = select(TradingAccount).where(
        TradingAccount.id == account_id,
        TradingAccount.user_id == user_id,
        TradingAccount.is_deleted.is_(False),
    )
    return db.execute(stmt).scalar_one_or_none()


def get_by_user_and_meta_account_id(
    db: Session,
    *,
    user_id: uuid.UUID,
    meta_account_id: str,
) -> Optional[TradingAccount]:
    stmt = select(TradingAccount).where(
        TradingAccount.user_id == user_id,
        TradingAccount.meta_account_id == meta_account_id,
    )
    return db.execute(stmt).scalar_one_or_none()


def list_for_user(db: Session, user_id: uuid.UUID) -> list[TradingAccount]:
    stmt = (
        select(TradingAccount)
        .where(
            TradingAccount.user_id == user_id,
            TradingAccount.is_deleted.is_(False),
        )
        .order_by(TradingAccount.created_at.desc())
    )
    return list(db.execute(stmt).scalars().all())


def list_syncable_accounts(db: Session) -> list[TradingAccount]:
    stmt = select(TradingAccount).where(
        TradingAccount.is_deleted.is_(False),
        TradingAccount.status.in_(
            [
                TradingAccountStatus.pending_sync,
                TradingAccountStatus.synced,
                TradingAccountStatus.error,
            ]
        ),
    )
    return list(db.execute(stmt).scalars().all())


def set_last_synced_at(db: Session, account: TradingAccount, synced_at: datetime) -> None:
    account.last_synced_at = synced_at
    account.status = TradingAccountStatus.synced
    account.sync_error_message = None
    db.flush()


def set_sync_error(db: Session, account: TradingAccount, message: str) -> None:
    account.status = TradingAccountStatus.error
    account.sync_error_message = message
    db.flush()


def set_sync_warning(db: Session, account: TradingAccount, message: str) -> None:
    account.sync_error_message = message
    db.flush()


def soft_disconnect(db: Session, account: TradingAccount) -> None:
    account.status = TradingAccountStatus.disconnected
    account.is_deleted = True
    db.flush()
