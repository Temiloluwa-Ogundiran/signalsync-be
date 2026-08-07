import uuid
from datetime import datetime
from decimal import Decimal
from typing import Optional

from sqlalchemy import func, or_, select
from sqlalchemy.orm import Session

from app.domains.copy_trading.models import (
    CopyAccountPolicy,
    CopyActivityEvent,
    CopyActivityLevel,
    CopyRoute,
    CopyRouteState,
    CopyTradingUserSettings,
    CopyTradingConnection,
    TelegramConnectionState,
    TelegramSource,
    TelegramConnection,
    ChannelProfile,
)


def create_copy_connection(
    db: Session, *, connection: CopyTradingConnection
) -> CopyTradingConnection:
    db.add(connection)
    db.flush()
    return connection


def get_copy_connection_for_user(
    db: Session, *, connection_id: uuid.UUID, user_id: uuid.UUID
) -> Optional[CopyTradingConnection]:
    stmt = select(CopyTradingConnection).where(
        CopyTradingConnection.id == connection_id,
        CopyTradingConnection.user_id == user_id,
    )
    return db.execute(stmt).scalar_one_or_none()


def get_copy_connection_by_identity(
    db: Session,
    *,
    user_id: uuid.UUID,
    broker_login: str,
    broker_server: str,
) -> Optional[CopyTradingConnection]:
    stmt = select(CopyTradingConnection).where(
        CopyTradingConnection.user_id == user_id,
        CopyTradingConnection.broker_login == broker_login,
        func.lower(CopyTradingConnection.broker_server) == broker_server.casefold(),
    )
    return db.execute(stmt).scalar_one_or_none()


def list_copy_connections_for_user(
    db: Session, *, user_id: uuid.UUID
) -> list[CopyTradingConnection]:
    stmt = (
        select(CopyTradingConnection)
        .where(CopyTradingConnection.user_id == user_id)
        .order_by(CopyTradingConnection.created_at.desc())
    )
    return list(db.execute(stmt).scalars().all())


def pause_routes_for_connection(db: Session, *, connection_id: uuid.UUID) -> None:
    routes = db.execute(
        select(CopyRoute).where(CopyRoute.target_connection_id == connection_id)
    ).scalars()
    for route in routes:
        if route.state != CopyRouteState.paused:
            route.paused_from_state = route.state
            route.state = CopyRouteState.paused


def mark_routes_target_unavailable(db: Session, *, connection_id: uuid.UUID) -> None:
    routes = db.execute(
        select(CopyRoute).where(
            CopyRoute.target_connection_id == connection_id,
            CopyRoute.state == CopyRouteState.active,
        )
    ).scalars()
    for route in routes:
        route.paused_from_state = route.state
        route.state = CopyRouteState.target_unavailable


def restore_routes_after_target_recovery(db: Session, *, connection_id: uuid.UUID) -> None:
    routes = db.execute(
        select(CopyRoute).where(
            CopyRoute.target_connection_id == connection_id,
            CopyRoute.state == CopyRouteState.target_unavailable,
        )
    ).scalars()
    for route in routes:
        route.state = route.paused_from_state or CopyRouteState.ready
        route.paused_from_state = None


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
    db: Session, *, connection_id: uuid.UUID, user_id: uuid.UUID
) -> Optional[CopyAccountPolicy]:
    stmt = select(CopyAccountPolicy).where(
        CopyAccountPolicy.connection_id == connection_id,
        CopyAccountPolicy.user_id == user_id,
    )
    return db.execute(stmt).scalar_one_or_none()


def get_or_create_account_policy(
    db: Session, *, connection_id: uuid.UUID, user_id: uuid.UUID
) -> CopyAccountPolicy:
    policy = get_account_policy(db, connection_id=connection_id, user_id=user_id)
    if policy is None:
        policy = CopyAccountPolicy(connection_id=connection_id, user_id=user_id)
        db.add(policy)
        db.flush()
    return policy


def list_account_policies(
    db: Session, *, user_id: uuid.UUID
) -> list[CopyAccountPolicy]:
    stmt = (
        select(CopyAccountPolicy)
        .join(CopyTradingConnection, CopyTradingConnection.id == CopyAccountPolicy.connection_id)
        .where(
            CopyAccountPolicy.user_id == user_id,
            CopyTradingConnection.user_id == user_id,
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


def get_route_by_source_and_connection(
    db: Session,
    *,
    user_id: uuid.UUID,
    source_id: uuid.UUID,
    connection_id: uuid.UUID,
) -> Optional[CopyRoute]:
    stmt = select(CopyRoute).where(
        CopyRoute.user_id == user_id,
        CopyRoute.source_id == source_id,
        CopyRoute.target_connection_id == connection_id,
    )
    return db.execute(stmt).scalar_one_or_none()


def list_routes_for_user(db: Session, *, user_id: uuid.UUID) -> list[CopyRoute]:
    stmt = (
        select(CopyRoute)
        .where(CopyRoute.user_id == user_id)
        .order_by(CopyRoute.created_at.desc())
    )
    return list(db.execute(stmt).scalars().all())


def max_fixed_lot_for_connection(
    db: Session, *, user_id: uuid.UUID, connection_id: uuid.UUID
) -> Optional[Decimal]:
    stmt = select(func.max(CopyRoute.fixed_lot)).where(
        CopyRoute.user_id == user_id,
        CopyRoute.target_connection_id == connection_id,
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
    level: CopyActivityLevel | None = None,
    source_id: uuid.UUID | None = None,
    connection_id: uuid.UUID | None = None,
    search: str | None = None,
) -> list[CopyActivityEvent]:
    stmt = select(CopyActivityEvent).where(CopyActivityEvent.user_id == user_id)
    if before is not None:
        stmt = stmt.where(CopyActivityEvent.created_at < before)
    if level is not None:
        stmt = stmt.where(CopyActivityEvent.level == level)
    if source_id is not None:
        stmt = stmt.where(CopyActivityEvent.source_id == source_id)
    if connection_id is not None:
        stmt = stmt.where(CopyActivityEvent.connection_id == connection_id)
    if search:
        term = f"%{search.strip()}%"
        stmt = stmt.where(or_(CopyActivityEvent.title.ilike(term), CopyActivityEvent.body.ilike(term)))
    stmt = stmt.order_by(CopyActivityEvent.created_at.desc()).limit(limit)
    return list(db.execute(stmt).scalars().all())


def list_connections_for_user(db: Session, *, user_id: uuid.UUID) -> list[TelegramConnection]:
    stmt = (
        select(TelegramConnection)
        .where(
            TelegramConnection.user_id == user_id,
            TelegramConnection.state != TelegramConnectionState.pending,
        )
        .order_by(TelegramConnection.created_at.desc())
    )
    return list(db.execute(stmt).scalars().all())


def get_connection_for_user(db: Session, *, connection_id: uuid.UUID, user_id: uuid.UUID) -> Optional[TelegramConnection]:
    return db.execute(select(TelegramConnection).where(TelegramConnection.id == connection_id, TelegramConnection.user_id == user_id)).scalar_one_or_none()


def list_sources_for_user(db: Session, *, user_id: uuid.UUID) -> list[tuple[TelegramSource, Optional[ChannelProfile]]]:
    stmt = select(TelegramSource, ChannelProfile).outerjoin(ChannelProfile, ChannelProfile.id == TelegramSource.profile_id).where(TelegramSource.user_id == user_id).order_by(TelegramSource.created_at.desc())
    return list(db.execute(stmt).all())
