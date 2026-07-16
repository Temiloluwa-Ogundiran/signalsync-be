import base64
import json
import uuid
from datetime import datetime, timedelta, timezone

from fastapi import HTTPException
from sqlalchemy.orm import Session

from app.core.config import settings
from app.domains.admin import repository as repo
from app.domains.admin.schemas import (
    AdminAuditItem,
    AdminAuditPage,
    AdminCopyAccount,
    AdminOverviewResponse,
    AdminSystemResponse,
    AdminTradingAccount,
    AdminUserDetail,
    AdminUserPage,
    AdminUserSummary,
    ProductEventCreate,
)
from app.domains.auth import repository as token_repo
from app.domains.auth.models import TokenType
from app.domains.copy_trading.telemetry import percentile
from app.domains.users.models import PlatformRole, User


def _assert_can_manage(actor: User, target: User) -> None:
    if actor.id == target.id:
        raise HTTPException(status_code=409, detail="You cannot manage your own account.")
    if target.platform_role == PlatformRole.SUPER_ADMIN:
        raise HTTPException(status_code=403, detail="This account requires super administrator access.")
    if actor.platform_role == PlatformRole.ADMIN and target.platform_role != PlatformRole.USER:
        raise HTTPException(status_code=403, detail="Administrators can only manage user accounts.")


def _encode_cursor(created_at: datetime, item_id: uuid.UUID) -> str:
    raw = json.dumps([created_at.isoformat(), str(item_id)], separators=(",", ":"))
    return base64.urlsafe_b64encode(raw.encode()).decode().rstrip("=")


def _decode_cursor(cursor: str | None) -> tuple[datetime | None, uuid.UUID | None]:
    if not cursor:
        return None, None
    try:
        padded = cursor + "=" * (-len(cursor) % 4)
        created_raw, id_raw = json.loads(
            base64.urlsafe_b64decode(padded.encode()).decode()
        )
        created_at = datetime.fromisoformat(created_raw)
        return created_at, uuid.UUID(id_raw)
    except (ValueError, TypeError, json.JSONDecodeError) as exc:
        raise HTTPException(status_code=422, detail="Invalid pagination cursor.") from exc


def bootstrap_configured_roles(db: Session) -> None:
    assignments = (
        (settings.ADMIN_EMAILS, PlatformRole.ADMIN),
        (settings.TECHNICAL_ADMIN_EMAILS, PlatformRole.TECHNICAL_ADMIN),
        (settings.SUPER_ADMIN_EMAILS, PlatformRole.SUPER_ADMIN),
    )
    changed = False
    for raw_emails, role in assignments:
        for email in {item.strip().lower() for item in raw_emails.split(",") if item.strip()}:
            from app.domains.users import repository as user_repo

            user = user_repo.get_by_email(db, email)
            if user and user.platform_role != role:
                user.platform_role = role
                changed = True
    if changed:
        db.commit()


def ingest_product_event(
    db: Session, *, payload: ProductEventCreate, current_user: User | None
) -> None:
    now = datetime.now(timezone.utc)
    occurred_at = payload.occurred_at.astimezone(timezone.utc)
    if abs((now - occurred_at).total_seconds()) > 86400:
        raise HTTPException(status_code=422, detail="Event timestamp is outside the accepted window.")
    repo.create_product_event(
        db,
        user_id=current_user.id if current_user else None,
        session_id=payload.session_id,
        event_name=payload.event_name,
        path=payload.path,
        referrer_host=payload.referrer_host,
        metadata=payload.metadata,
        occurred_at=occurred_at,
    )
    db.commit()


def list_users(
    db: Session,
    *,
    query: str | None,
    user_status: str | None,
    role: PlatformRole | None,
    cursor: str | None,
    limit: int,
) -> AdminUserPage:
    cursor_created_at, cursor_id = _decode_cursor(cursor)
    rows = repo.list_users(
        db,
        query=query,
        status=user_status,
        role=role,
        cursor_created_at=cursor_created_at,
        cursor_id=cursor_id,
        limit=limit,
    )
    page = rows[:limit]
    items = [
        AdminUserSummary(
            id=user.id,
            email=user.email,
            display_name=user.display_name,
            platform_role=user.platform_role,
            is_email_verified=user.is_email_verified,
            is_suspended=user.is_suspended,
            onboarding_completed=user.onboarding_completed,
            trading_account_count=trading_count,
            copy_account_count=copy_count,
            last_active_at=user.last_active_at,
            created_at=user.created_at,
        )
        for user, trading_count, copy_count in page
    ]
    next_cursor = None
    if len(rows) > limit and page:
        next_cursor = _encode_cursor(page[-1][0].created_at, page[-1][0].id)
    return AdminUserPage(items=items, next_cursor=next_cursor)


def get_user_detail(db: Session, *, user_id: uuid.UUID) -> AdminUserDetail:
    user = repo.get_user(db, user_id)
    if user is None:
        raise HTTPException(status_code=404, detail="User not found.")
    trading_count, copy_count = repo.get_user_account_counts(db, user.id)
    trading_accounts = repo.list_user_trading_accounts(db, user.id)
    copy_accounts = repo.list_user_copy_accounts(db, user.id)
    return AdminUserDetail(
        id=user.id,
        email=user.email,
        display_name=user.display_name,
        platform_role=user.platform_role,
        is_email_verified=user.is_email_verified,
        is_suspended=user.is_suspended,
        onboarding_completed=user.onboarding_completed,
        trading_account_count=trading_count,
        copy_account_count=copy_count,
        last_active_at=user.last_active_at,
        created_at=user.created_at,
        auth_provider=user.auth_provider.value,
        suspended_at=user.suspended_at,
        suspension_reason=user.suspension_reason,
        active_sessions=repo.count_active_sessions(
            db, user.id, datetime.now(timezone.utc)
        ),
        trading_accounts=[
            AdminTradingAccount(
                id=account.id,
                display_name=account.display_name,
                broker_name=account.broker_name,
                broker_server=account.broker_server,
                broker_login=account.broker_login,
                status=account.status.value,
                connection_state=account.connection_state.value,
                last_synced_at=account.last_synced_at,
            )
            for account in trading_accounts
        ],
        copy_accounts=[
            AdminCopyAccount(
                id=account.id,
                display_name=account.display_name,
                broker_server=account.broker_server,
                broker_login=account.broker_login,
                state=account.state.value,
                is_paused=account.is_paused,
                last_health_at=account.last_health_at,
            )
            for account in copy_accounts
        ],
    )


def suspend_user(
    db: Session, *, actor: User, user_id: uuid.UUID, reason: str
) -> None:
    user = repo.get_user(db, user_id)
    if user is None:
        raise HTTPException(status_code=404, detail="User not found.")
    _assert_can_manage(actor, user)
    user.is_suspended = True
    user.suspended_at = datetime.now(timezone.utc)
    user.suspension_reason = reason
    token_repo.revoke_all_by_user_and_type(
        db, user_id=user.id, token_type=TokenType.REFRESH
    )
    repo.create_audit_event(
        db,
        actor_user_id=actor.id,
        target_user_id=user.id,
        action="user.suspended",
        reason=reason,
        details={},
    )
    db.commit()


def restore_user(db: Session, *, actor: User, user_id: uuid.UUID) -> None:
    user = repo.get_user(db, user_id)
    if user is None:
        raise HTTPException(status_code=404, detail="User not found.")
    _assert_can_manage(actor, user)
    user.is_suspended = False
    user.suspended_at = None
    user.suspension_reason = None
    repo.create_audit_event(
        db,
        actor_user_id=actor.id,
        target_user_id=user.id,
        action="user.restored",
        reason=None,
        details={},
    )
    db.commit()


def revoke_user_sessions(db: Session, *, actor: User, user_id: uuid.UUID) -> None:
    user = repo.get_user(db, user_id)
    if user is None:
        raise HTTPException(status_code=404, detail="User not found.")
    _assert_can_manage(actor, user)
    token_repo.revoke_all_by_user_and_type(
        db, user_id=user.id, token_type=TokenType.REFRESH
    )
    repo.create_audit_event(
        db,
        actor_user_id=actor.id,
        target_user_id=user.id,
        action="user.sessions_revoked",
        reason=None,
        details={},
    )
    db.commit()


def update_user_role(
    db: Session,
    *,
    actor: User,
    user_id: uuid.UUID,
    role: PlatformRole,
    reason: str,
) -> None:
    if actor.platform_role != PlatformRole.SUPER_ADMIN:
        raise HTTPException(status_code=403, detail="Super administrator access required.")
    user = repo.get_user(db, user_id)
    if user is None:
        raise HTTPException(status_code=404, detail="User not found.")
    _assert_can_manage(actor, user)
    previous = user.platform_role
    user.platform_role = role
    repo.create_audit_event(
        db,
        actor_user_id=actor.id,
        target_user_id=user.id,
        action="user.role_changed",
        reason=reason,
        details={"previous_role": previous.value, "new_role": role.value},
    )
    db.commit()


def overview(db: Session, *, days: int) -> AdminOverviewResponse:
    now = datetime.now(timezone.utc)
    since = now - timedelta(days=days)
    totals = repo.platform_totals(db, active_since=now - timedelta(hours=24))
    signups = [
        {"date": day.date().isoformat(), "count": int(count)}
        for day, count in repo.signup_series(db, since=since)
    ]
    funnel_counts = repo.funnel_counts(db, since=since)
    funnel_order = [
        "registration_started",
        "registration_completed",
        "login_succeeded",
        "onboarding_completed",
        "account_connected",
        "journal_synced",
        "copy_route_activated",
    ]
    funnel = [
        {"stage": stage, "sessions": funnel_counts.get(stage, 0)}
        for stage in funnel_order
    ]
    return AdminOverviewResponse(
        totals=totals,
        signups=signups,
        funnel=funnel,
        product=repo.product_session_summary(db, since=since),
    )


def system_overview(db: Session) -> AdminSystemResponse:
    now = datetime.now(timezone.utc)
    workers = repo.list_latest_worker_health(db)
    components = []
    for worker in workers:
        age = max(0, int((now - worker.heartbeat_at).total_seconds()))
        component_status = (
            "healthy"
            if worker.state.value == "healthy" and age <= 60
            else "degraded"
        )
        components.append(
            {
                "name": worker.worker_role,
                "status": component_status,
                "heartbeat_age_seconds": age,
                "stream_lag": worker.stream_lag,
                "pending": worker.pending_count,
                "last_error": worker.last_error,
            }
        )
    metrics = repo.recent_copy_metrics(db)
    values = [float(item.total_ms) for item in metrics if item.total_ms is not None]
    copy_latency = {
        "samples": len(values),
        "p50_ms": round(percentile(values, 0.5)) if values else None,
        "p95_ms": round(percentile(values, 0.95)) if values else None,
        "over_2s": sum(value > 2000 for value in values),
    }
    overall = "healthy" if all(item["status"] == "healthy" for item in components) else "degraded"
    return AdminSystemResponse(
        status=overall,
        components=components,
        copy_latency=copy_latency,
        grafana_url=settings.GRAFANA_URL or None,
    )


def list_audit_events(
    db: Session, *, cursor: str | None, limit: int
) -> AdminAuditPage:
    cursor_created_at, cursor_id = _decode_cursor(cursor)
    rows = repo.list_audit_events(
        db,
        cursor_created_at=cursor_created_at,
        cursor_id=cursor_id,
        limit=limit,
    )
    page = rows[:limit]
    items = [
        AdminAuditItem(
            id=event.id,
            actor_email=actor_email,
            target_email=target_email,
            action=event.action,
            reason=event.reason,
            details=event.details,
            created_at=event.created_at,
        )
        for event, actor_email, target_email in page
    ]
    next_cursor = None
    if len(rows) > limit and page:
        next_cursor = _encode_cursor(page[-1][0].created_at, page[-1][0].id)
    return AdminAuditPage(items=items, next_cursor=next_cursor)
