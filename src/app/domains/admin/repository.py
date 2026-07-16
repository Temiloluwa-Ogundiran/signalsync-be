import uuid
from datetime import datetime

from sqlalchemy import and_, func, or_, select
from sqlalchemy.orm import Session, aliased

from app.domains.accounts.models import TradingAccount
from app.domains.admin.models import AdminAuditEvent, ProductEvent
from app.domains.auth.models import Token, TokenType
from app.domains.copy_trading.models import (
    CopyExecutionMetric,
    CopyTradingConnection,
    CopyWorkerHealth,
)
from app.domains.users.models import PlatformRole, User


def create_product_event(
    db: Session,
    *,
    user_id: uuid.UUID | None,
    session_id: uuid.UUID,
    event_name: str,
    path: str,
    referrer_host: str | None,
    metadata: dict,
    occurred_at: datetime,
) -> ProductEvent:
    event = ProductEvent(
        user_id=user_id,
        session_id=session_id,
        event_name=event_name,
        path=path,
        referrer_host=referrer_host,
        event_metadata=metadata,
        occurred_at=occurred_at,
    )
    db.add(event)
    db.flush()
    return event


def create_audit_event(
    db: Session,
    *,
    actor_user_id: uuid.UUID,
    target_user_id: uuid.UUID | None,
    action: str,
    reason: str | None,
    details: dict,
) -> AdminAuditEvent:
    event = AdminAuditEvent(
        actor_user_id=actor_user_id,
        target_user_id=target_user_id,
        action=action,
        reason=reason,
        details=details,
    )
    db.add(event)
    db.flush()
    return event


def list_users(
    db: Session,
    *,
    query: str | None,
    status: str | None,
    role: PlatformRole | None,
    cursor_created_at: datetime | None,
    cursor_id: uuid.UUID | None,
    limit: int,
) -> list[tuple[User, int, int]]:
    trading_count = (
        select(func.count(TradingAccount.id))
        .where(TradingAccount.user_id == User.id, TradingAccount.is_archived.is_(False))
        .correlate(User)
        .scalar_subquery()
    )
    copy_count = (
        select(func.count(CopyTradingConnection.id))
        .where(CopyTradingConnection.user_id == User.id)
        .correlate(User)
        .scalar_subquery()
    )
    stmt = select(
        User,
        trading_count.label("trading_account_count"),
        copy_count.label("copy_account_count"),
    )
    if status == "deleted":
        stmt = stmt.where(User.is_deleted.is_(True))
    else:
        stmt = stmt.where(User.is_deleted.is_(False))
    if query:
        pattern = f"%{query.strip()}%"
        stmt = stmt.where(
            or_(User.email.ilike(pattern), User.display_name.ilike(pattern))
        )
    if status == "suspended":
        stmt = stmt.where(User.is_suspended.is_(True))
    elif status == "active":
        stmt = stmt.where(User.is_suspended.is_(False))
    elif status == "unverified":
        stmt = stmt.where(User.is_email_verified.is_(False))
    if role:
        stmt = stmt.where(User.platform_role == role)
    if cursor_created_at and cursor_id:
        stmt = stmt.where(
            or_(
                User.created_at < cursor_created_at,
                and_(User.created_at == cursor_created_at, User.id < cursor_id),
            )
        )
    stmt = stmt.order_by(User.created_at.desc(), User.id.desc()).limit(limit + 1)
    return [(row[0], int(row[1] or 0), int(row[2] or 0)) for row in db.execute(stmt).all()]


def get_user(db: Session, user_id: uuid.UUID) -> User | None:
    return db.execute(
        select(User).where(User.id == user_id, User.is_deleted.is_(False))
    ).scalar_one_or_none()


def get_user_account_counts(db: Session, user_id: uuid.UUID) -> tuple[int, int]:
    trading = db.scalar(
        select(func.count(TradingAccount.id)).where(
            TradingAccount.user_id == user_id,
            TradingAccount.is_archived.is_(False),
        )
    )
    copying = db.scalar(
        select(func.count(CopyTradingConnection.id)).where(
            CopyTradingConnection.user_id == user_id
        )
    )
    return int(trading or 0), int(copying or 0)


def list_user_trading_accounts(db: Session, user_id: uuid.UUID) -> list[TradingAccount]:
    return list(
        db.execute(
            select(TradingAccount)
            .where(
                TradingAccount.user_id == user_id,
                TradingAccount.is_archived.is_(False),
            )
            .order_by(TradingAccount.created_at.desc())
        ).scalars()
    )


def list_user_copy_accounts(
    db: Session, user_id: uuid.UUID
) -> list[CopyTradingConnection]:
    return list(
        db.execute(
            select(CopyTradingConnection)
            .where(CopyTradingConnection.user_id == user_id)
            .order_by(CopyTradingConnection.created_at.desc())
        ).scalars()
    )


def count_active_sessions(db: Session, user_id: uuid.UUID, now: datetime) -> int:
    value = db.scalar(
        select(func.count(Token.id)).where(
            Token.user_id == user_id,
            Token.type == TokenType.REFRESH,
            Token.is_revoked.is_(False),
            Token.expires_at > now,
        )
    )
    return int(value or 0)


def platform_totals(db: Session, *, active_since: datetime) -> dict[str, int]:
    row = db.execute(
        select(
            func.count(User.id),
            func.count(User.id).filter(User.last_active_at >= active_since),
            func.count(User.id).filter(User.is_email_verified.is_(True)),
            func.count(User.id).filter(User.onboarding_completed.is_(True)),
            func.count(User.id).filter(User.is_suspended.is_(True)),
        ).where(User.is_deleted.is_(False))
    ).one()
    trading_accounts = db.scalar(
        select(func.count(TradingAccount.id)).where(TradingAccount.is_archived.is_(False))
    )
    copy_accounts = db.scalar(select(func.count(CopyTradingConnection.id)))
    return {
        "users": int(row[0] or 0),
        "active_24h": int(row[1] or 0),
        "verified": int(row[2] or 0),
        "onboarded": int(row[3] or 0),
        "suspended": int(row[4] or 0),
        "trading_accounts": int(trading_accounts or 0),
        "copy_accounts": int(copy_accounts or 0),
    }


def signup_series(db: Session, *, since: datetime) -> list[tuple[datetime, int]]:
    day = func.date_trunc("day", User.created_at)
    return list(
        db.execute(
            select(day, func.count(User.id))
            .where(User.created_at >= since, User.is_deleted.is_(False))
            .group_by(day)
            .order_by(day)
        ).all()
    )


def product_session_summary(db: Session, *, since: datetime) -> dict[str, float | int]:
    session_rows = (
        select(
            ProductEvent.session_id.label("session_id"),
            func.count(ProductEvent.id).label("event_count"),
            func.count(ProductEvent.id)
            .filter(ProductEvent.event_name == "page_view")
            .label("page_views"),
            func.count(ProductEvent.id)
            .filter(ProductEvent.event_name == "session_engaged")
            .label("engagements"),
        )
        .where(ProductEvent.occurred_at >= since)
        .group_by(ProductEvent.session_id)
        .subquery()
    )
    row = db.execute(
        select(
            func.count(session_rows.c.session_id),
            func.count(session_rows.c.session_id).filter(
                and_(session_rows.c.page_views == 1, session_rows.c.engagements == 0)
            ),
        )
    ).one()
    sessions = int(row[0] or 0)
    bounced = int(row[1] or 0)
    return {
        "sessions": sessions,
        "bounced_sessions": bounced,
        "bounce_rate": round((bounced / sessions * 100), 2) if sessions else 0.0,
    }


def funnel_counts(db: Session, *, since: datetime) -> dict[str, int]:
    names = [
        "registration_started",
        "registration_completed",
        "login_succeeded",
        "onboarding_completed",
        "account_connected",
        "journal_synced",
        "copy_route_activated",
    ]
    rows = db.execute(
        select(ProductEvent.event_name, func.count(func.distinct(ProductEvent.session_id)))
        .where(ProductEvent.occurred_at >= since, ProductEvent.event_name.in_(names))
        .group_by(ProductEvent.event_name)
    ).all()
    return {name: int(count) for name, count in rows}


def list_latest_worker_health(db: Session) -> list[CopyWorkerHealth]:
    ranked = (
        select(
            CopyWorkerHealth.id.label("id"),
            func.row_number()
            .over(
                partition_by=CopyWorkerHealth.worker_role,
                order_by=CopyWorkerHealth.heartbeat_at.desc(),
            )
            .label("rank"),
        )
        .subquery()
    )
    return list(
        db.execute(
            select(CopyWorkerHealth)
            .join(ranked, ranked.c.id == CopyWorkerHealth.id)
            .where(ranked.c.rank == 1)
            .order_by(CopyWorkerHealth.worker_role)
        ).scalars()
    )


def recent_copy_metrics(db: Session, *, limit: int = 1000) -> list[CopyExecutionMetric]:
    return list(
        db.execute(
            select(CopyExecutionMetric)
            .where(CopyExecutionMetric.total_ms.is_not(None))
            .order_by(CopyExecutionMetric.created_at.desc())
            .limit(limit)
        ).scalars()
    )


def list_audit_events(
    db: Session,
    *,
    cursor_created_at: datetime | None,
    cursor_id: uuid.UUID | None,
    limit: int,
) -> list[tuple[AdminAuditEvent, str, str | None]]:
    actor = aliased(User)
    target = aliased(User)
    stmt = (
        select(AdminAuditEvent, actor.email, target.email)
        .join(actor, actor.id == AdminAuditEvent.actor_user_id)
        .outerjoin(target, target.id == AdminAuditEvent.target_user_id)
    )
    if cursor_created_at and cursor_id:
        stmt = stmt.where(
            or_(
                AdminAuditEvent.created_at < cursor_created_at,
                and_(
                    AdminAuditEvent.created_at == cursor_created_at,
                    AdminAuditEvent.id < cursor_id,
                ),
            )
        )
    stmt = stmt.order_by(
        AdminAuditEvent.created_at.desc(), AdminAuditEvent.id.desc()
    ).limit(limit + 1)
    return [(row[0], row[1], row[2]) for row in db.execute(stmt).all()]
