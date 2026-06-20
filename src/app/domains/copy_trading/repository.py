import uuid
from datetime import datetime
from decimal import Decimal
from typing import Optional

from sqlalchemy import func, select
from sqlalchemy.orm import Session

from app.domains.accounts.models import TradingAccount
from app.domains.copy_trading.models import (
    CopyAccountPolicy,
    CopyActivityEvent,
    CopyRoute,
    CopyTradingUserSettings,
    TelegramSource,
)


def get_or_create_user_settings(
    db: Session, *, user_id: uuid.UUID
) -> CopyTradingUserSettings:
    settings = db.get(CopyTradingUserSettings, user_id)
    if settings is None:
        settings = CopyTradingUserSettings(user_id=user_id)
        db.add(settings)
        db.flush()
    return settings


def get_source_for_user(
    db: Session, *, source_id: uuid.UUID, user_id: uuid.UUID
) -> Optional[TelegramSource]:
    stmt = select(TelegramSource).where(
        TelegramSource.id == source_id,
        TelegramSource.user_id == user_id,
    )
    return db.execute(stmt).scalar_one_or_none()


def get_account_policy(
    db: Session, *, account_id: uuid.UUID, user_id: uuid.UUID
) -> Optional[CopyAccountPolicy]:
    stmt = select(CopyAccountPolicy).where(
        CopyAccountPolicy.account_id == account_id,
        CopyAccountPolicy.user_id == user_id,
    )
    return db.execute(stmt).scalar_one_or_none()


def get_or_create_account_policy(
    db: Session, *, account_id: uuid.UUID, user_id: uuid.UUID
) -> CopyAccountPolicy:
    policy = get_account_policy(db, account_id=account_id, user_id=user_id)
    if policy is None:
        policy = CopyAccountPolicy(account_id=account_id, user_id=user_id)
        db.add(policy)
        db.flush()
    return policy


def list_account_policies(
    db: Session, *, user_id: uuid.UUID
) -> list[CopyAccountPolicy]:
    stmt = (
        select(CopyAccountPolicy)
        .join(TradingAccount, TradingAccount.id == CopyAccountPolicy.account_id)
        .where(
            CopyAccountPolicy.user_id == user_id,
            TradingAccount.user_id == user_id,
        )
        .order_by(CopyAccountPolicy.created_at.desc())
    )
    return list(db.execute(stmt).scalars().all())


def create_route(db: Session, *, route: CopyRoute) -> CopyRoute:
    db.add(route)
    db.flush()
    return route


def get_route_for_user(
    db: Session, *, route_id: uuid.UUID, user_id: uuid.UUID
) -> Optional[CopyRoute]:
    stmt = select(CopyRoute).where(
        CopyRoute.id == route_id,
        CopyRoute.user_id == user_id,
    )
    return db.execute(stmt).scalar_one_or_none()


def get_route_by_source_and_account(
    db: Session,
    *,
    user_id: uuid.UUID,
    source_id: uuid.UUID,
    account_id: uuid.UUID,
) -> Optional[CopyRoute]:
    stmt = select(CopyRoute).where(
        CopyRoute.user_id == user_id,
        CopyRoute.source_id == source_id,
        CopyRoute.target_account_id == account_id,
    )
    return db.execute(stmt).scalar_one_or_none()


def list_routes_for_user(db: Session, *, user_id: uuid.UUID) -> list[CopyRoute]:
    stmt = (
        select(CopyRoute)
        .where(CopyRoute.user_id == user_id)
        .order_by(CopyRoute.created_at.desc())
    )
    return list(db.execute(stmt).scalars().all())


def max_fixed_lot_for_account(
    db: Session, *, user_id: uuid.UUID, account_id: uuid.UUID
) -> Optional[Decimal]:
    stmt = select(func.max(CopyRoute.fixed_lot)).where(
        CopyRoute.user_id == user_id,
        CopyRoute.target_account_id == account_id,
    )
    return db.execute(stmt).scalar_one_or_none()


def create_activity(db: Session, *, event: CopyActivityEvent) -> CopyActivityEvent:
    db.add(event)
    db.flush()
    return event


def list_activity_for_user(
    db: Session,
    *,
    user_id: uuid.UUID,
    limit: int,
    before: Optional[datetime],
) -> list[CopyActivityEvent]:
    stmt = select(CopyActivityEvent).where(CopyActivityEvent.user_id == user_id)
    if before is not None:
        stmt = stmt.where(CopyActivityEvent.created_at < before)
    stmt = stmt.order_by(CopyActivityEvent.created_at.desc()).limit(limit)
    return list(db.execute(stmt).scalars().all())
