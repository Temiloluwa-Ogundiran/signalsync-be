import uuid
from datetime import datetime, timezone
from decimal import Decimal
from typing import Optional

from fastapi import HTTPException, status
from sqlalchemy import text
from sqlalchemy.orm import Session

from app.domains.copy_trading import repository as repo
from app.domains.copy_trading.models import (
    CopyAccountPolicy,
    CopyActivityEvent,
    CopyActivityLevel,
    CopyRoute,
    CopyRouteState,
    CopyTradingUserSettings,
    CopyTradingConnection,
    CopyTradingConnectionState,
    TelegramSourceState,
)
from app.domains.copy_trading.schemas import (
    CopyAccountPolicyUpdate,
    CopyRouteCreate,
    CopyRouteUpdate,
    CopyTradingSettingsUpdate,
    CopyTradingConnectionCreate,
    UNSAFE_MINIMUM_FIELDS,
)
from app.domains.users.models import User
from app.core.config import settings
from app.domains.billing import service as billing_service
from app.domains.billing.entitlements import require_copy_account_capacity
from app.shared.utils.encryption import encrypt_secret


def _owned_copy_connection(
    db: Session, *, current_user: User, connection_id: uuid.UUID
) -> CopyTradingConnection:
    connection = repo.get_copy_connection_for_user(
        db, connection_id=connection_id, user_id=current_user.id
    )
    if connection is None:
        raise HTTPException(
            status_code=status.HTTP_404_NOT_FOUND,
            detail="Copy account connection not found.",
        )
    return connection


def create_copy_connection(
    db: Session, *, current_user: User, payload: CopyTradingConnectionCreate
) -> CopyTradingConnection:
    if settings.BILLING_ENFORCED:
        db.execute(
            text(
                "SELECT pg_advisory_xact_lock(hashtext('copy-capacity'), hashtext(:user_id))"
            ),
            {"user_id": str(current_user.id)},
        )
        subscription = billing_service.get_subscription(db, user_id=current_user.id)
        existing_count = sum(
            connection.state
            not in {CopyTradingConnectionState.deleting, CopyTradingConnectionState.deleted}
            for connection in repo.list_copy_connections_for_user(db, user_id=current_user.id)
        )
        require_copy_account_capacity(
            billing_service.effective_subscription(subscription) if subscription else None,
            current_account_count=existing_count,
        )
    connection = CopyTradingConnection(
        user_id=current_user.id,
        display_name=payload.display_name.strip(),
        broker_login=payload.broker_login,
        broker_server=payload.broker_server.strip(),
        platform=payload.platform,
        encrypted_trader_password=encrypt_secret(
            payload.trader_password.get_secret_value()
        ),
        provisioning_transaction_id=uuid.uuid4().hex,
        state=CopyTradingConnectionState.submitted,
    )
    repo.create_copy_connection(db, connection=connection)
    db.commit()
    db.refresh(connection)
    return connection


def list_copy_connections(
    db: Session, *, current_user: User
) -> list[CopyTradingConnection]:
    return repo.list_copy_connections_for_user(db, user_id=current_user.id)


def get_copy_connection(
    db: Session, *, current_user: User, connection_id: uuid.UUID
) -> CopyTradingConnection:
    return _owned_copy_connection(
        db, current_user=current_user, connection_id=connection_id
    )


def retry_copy_connection(
    db: Session, *, current_user: User, connection_id: uuid.UUID
) -> CopyTradingConnection:
    connection = _owned_copy_connection(
        db, current_user=current_user, connection_id=connection_id
    )
    if connection.state in {
        CopyTradingConnectionState.deleting,
        CopyTradingConnectionState.deleted,
    }:
        raise HTTPException(
            status_code=status.HTTP_409_CONFLICT,
            detail="Deleted copy account connections cannot be retried.",
        )
    connection.last_error_code = None
    connection.last_error_message = None
    connection.state = CopyTradingConnectionState.provisioning
    db.commit()
    db.refresh(connection)
    return connection


def delete_copy_connection(
    db: Session, *, current_user: User, connection_id: uuid.UUID
) -> CopyTradingConnection:
    connection = _owned_copy_connection(
        db, current_user=current_user, connection_id=connection_id
    )
    if connection.state != CopyTradingConnectionState.deleted:
        repo.pause_routes_for_connection(db, connection_id=connection.id)
        connection.state = CopyTradingConnectionState.deleting
        db.commit()
        db.refresh(connection)
    return connection


def magic_number_for_route(route_id: uuid.UUID) -> int:
    return (int.from_bytes(route_id.bytes[:4], "big") & 0x7FFFFFFF) or 1


def _owned_route(
    db: Session, *, current_user: User, route_id: uuid.UUID
) -> CopyRoute:
    route = repo.get_route_for_user(db, route_id=route_id, user_id=current_user.id)
    if route is None:
        raise HTTPException(status_code=status.HTTP_404_NOT_FOUND, detail="Copy route not found.")
    return route


def _owned_ready_copy_connection(
    db: Session, *, current_user: User, connection_id: uuid.UUID
) -> CopyTradingConnection:
    connection = _owned_copy_connection(
        db, current_user=current_user, connection_id=connection_id
    )
    if connection.state != CopyTradingConnectionState.ready:
        raise HTTPException(
            status_code=status.HTTP_409_CONFLICT,
            detail="The copy account connection must be ready.",
        )
    return connection


def record_activity(
    db: Session,
    *,
    user_id: uuid.UUID,
    action: str,
    title: str,
    level: CopyActivityLevel,
    correlation_id: str,
    route_id: Optional[uuid.UUID] = None,
    source_id: Optional[uuid.UUID] = None,
    connection_id: Optional[uuid.UUID] = None,
    body: Optional[str] = None,
    parsed_details: Optional[dict] = None,
    broker_details: Optional[dict] = None,
    encrypted_raw_message: Optional[str] = None,
) -> CopyActivityEvent:
    event = CopyActivityEvent(
        user_id=user_id,
        route_id=route_id,
        source_id=source_id,
        connection_id=connection_id,
        correlation_id=correlation_id,
        action=action,
        level=level,
        title=title,
        body=body,
        parsed_details=parsed_details or {},
        broker_details=broker_details or {},
        encrypted_raw_message=encrypted_raw_message,
    )
    return repo.create_activity(db, event=event)


def get_user_settings(db: Session, *, current_user: User) -> CopyTradingUserSettings:
    settings = repo.get_or_create_user_settings(db, user_id=current_user.id)
    db.commit()
    db.refresh(settings)
    return settings


def update_user_settings(
    db: Session, *, current_user: User, payload: CopyTradingSettingsUpdate
) -> CopyTradingUserSettings:
    settings = repo.get_or_create_user_settings(db, user_id=current_user.id)
    settings.is_paused = payload.is_paused
    record_activity(
        db,
        user_id=current_user.id,
        correlation_id=str(uuid.uuid4()),
        action="automation.paused" if payload.is_paused else "automation.resumed",
        title="Copy trading paused" if payload.is_paused else "Copy trading resumed",
        level=CopyActivityLevel.warning if payload.is_paused else CopyActivityLevel.success,
    )
    db.commit()
    db.refresh(settings)
    return settings


def list_account_policies(db: Session, *, current_user: User) -> list[CopyAccountPolicy]:
    return repo.list_account_policies(db, user_id=current_user.id)


def update_account_policy(
    db: Session,
    *,
    current_user: User,
    connection_id: uuid.UUID,
    payload: CopyAccountPolicyUpdate,
) -> CopyAccountPolicy:
    _owned_ready_copy_connection(db, current_user=current_user, connection_id=connection_id)
    policy = repo.get_or_create_account_policy(
        db, connection_id=connection_id, user_id=current_user.id
    )
    changes = payload.model_dump(exclude_unset=True)
    start_hour = changes.get("trading_start_hour_utc", policy.trading_start_hour_utc)
    end_hour = changes.get("trading_end_hour_utc", policy.trading_end_hour_utc)
    if (start_hour is None) != (end_hour is None):
        raise HTTPException(
            status_code=422,
            detail="Trading start and end hours must be set or cleared together.",
        )
    proposed_caps = {
        "max_lot": changes.get("max_lot"),
        "max_lot_per_trade": changes.get("max_lot_per_trade"),
    }
    if any(value is not None for value in proposed_caps.values()):
        largest_route_lot = repo.max_fixed_lot_for_connection(
            db, user_id=current_user.id, connection_id=connection_id
        )
        for field, proposed_cap in proposed_caps.items():
            if (
                proposed_cap is not None
                and largest_route_lot is not None
                and Decimal(proposed_cap) < Decimal(largest_route_lot)
            ):
                label = (
                    "total exposure"
                    if field == "max_lot"
                    else "per-trade size"
                )
                raise HTTPException(
                    status_code=status.HTTP_409_CONFLICT,
                    detail=(
                        f"The {label} limit cannot be lower than the largest "
                        f"configured route lot ({largest_route_lot})."
                    ),
                )
    for field, value in changes.items():
        setattr(policy, field, value)
    if "daily_loss_limit" in changes:
        policy.daily_equity_anchor = None
        policy.daily_equity_anchor_date = None
    if "max_drawdown_percent" in changes:
        policy.peak_equity = None
    record_activity(
        db,
        user_id=current_user.id,
        connection_id=connection_id,
        correlation_id=str(uuid.uuid4()),
        action="account_policy.updated",
        title="Copy trading account settings updated",
        level=CopyActivityLevel.info,
    )
    db.commit()
    db.refresh(policy)
    return policy


def list_routes(db: Session, *, current_user: User) -> list[CopyRoute]:
    return repo.list_routes_for_user(db, user_id=current_user.id)


def get_route(db: Session, *, current_user: User, route_id: uuid.UUID) -> CopyRoute:
    return _owned_route(db, current_user=current_user, route_id=route_id)


def create_route(
    db: Session, *, current_user: User, payload: CopyRouteCreate
) -> CopyRoute:
    source = repo.get_source_for_user(
        db, source_id=payload.source_id, user_id=current_user.id
    )
    if source is None:
        raise HTTPException(status_code=status.HTTP_404_NOT_FOUND, detail="Telegram source not found.")
    connection = _owned_ready_copy_connection(
        db, current_user=current_user, connection_id=payload.target_connection_id
    )
    existing = repo.get_route_by_source_and_connection(
        db,
        user_id=current_user.id,
        source_id=source.id,
        connection_id=connection.id,
    )
    if existing is not None:
        raise HTTPException(
            status_code=status.HTTP_409_CONFLICT,
            detail="A copy route already exists for this source and account.",
        )
    policy = repo.get_account_policy(
        db, connection_id=connection.id, user_id=current_user.id
    ) or repo.get_or_create_account_policy(
        db, connection_id=connection.id, user_id=current_user.id
    )
    if payload.fixed_lot > policy.max_lot or payload.fixed_lot > policy.max_lot_per_trade:
        raise HTTPException(
            status_code=422,
            detail=(
                "Fixed lot exceeds this account's configured trade or exposure limit."
            ),
        )

    route_id = uuid.uuid4()
    values = payload.model_dump(exclude={"unsafe_minimum_confirmed"})
    route = CopyRoute(
        id=route_id,
        user_id=current_user.id,
        magic_number=magic_number_for_route(route_id),
        state=CopyRouteState.ready,
        unsafe_minimum_confirmed_at=(
            datetime.now(timezone.utc)
            if payload.minimum_fields in UNSAFE_MINIMUM_FIELDS
            else None
        ),
        **values,
    )
    repo.create_route(db, route=route)
    record_activity(
        db,
        user_id=current_user.id,
        route_id=route.id,
        source_id=route.source_id,
        connection_id=route.target_connection_id,
        correlation_id=str(uuid.uuid4()),
        action="route.created",
        title="Copy route created",
        level=CopyActivityLevel.success,
    )
    db.commit()
    db.refresh(route)
    return route


def update_route(
    db: Session,
    *,
    current_user: User,
    route_id: uuid.UUID,
    payload: CopyRouteUpdate,
) -> CopyRoute:
    route = _owned_route(db, current_user=current_user, route_id=route_id)
    changes = payload.model_dump(exclude_unset=True)
    unsafe_confirmed = changes.pop("unsafe_minimum_confirmed", None)
    merged = {
        "source_id": route.source_id,
        "target_connection_id": route.target_connection_id,
        "fixed_lot": route.fixed_lot,
        "take_profit_mode": route.take_profit_mode,
        "lot_distribution": route.lot_distribution,
        "pending_orders_enabled": route.pending_orders_enabled,
        "minimum_fields": route.minimum_fields,
        "assembly_window_seconds": route.assembly_window_seconds,
        "process_all_group_authors": route.process_all_group_authors,
        "notify_success": route.notify_success,
        "notify_failure": route.notify_failure,
        "semantic_duplicate_window_seconds": route.semantic_duplicate_window_seconds,
        "allow_sl_tp_updates": route.allow_sl_tp_updates,
        "allow_break_even": route.allow_break_even,
        "allow_additional_tp": route.allow_additional_tp,
        "allow_partial_close": route.allow_partial_close,
        "allow_full_close": route.allow_full_close,
        "allow_pending_cancel": route.allow_pending_cancel,
        "unsafe_minimum_confirmed": bool(route.unsafe_minimum_confirmed_at),
    }
    merged.update(changes)
    if unsafe_confirmed is not None:
        merged["unsafe_minimum_confirmed"] = unsafe_confirmed
    validated = CopyRouteCreate.model_validate(merged)
    policy = repo.get_or_create_account_policy(
        db, connection_id=route.target_connection_id, user_id=current_user.id
    )
    if (
        validated.fixed_lot > policy.max_lot
        or validated.fixed_lot > policy.max_lot_per_trade
    ):
        raise HTTPException(
                status_code=422,
            detail=(
                "Fixed lot exceeds this account's configured trade or exposure limit."
            ),
        )
    for field in payload.model_fields_set - {"unsafe_minimum_confirmed"}:
        setattr(route, field, getattr(validated, field))
    if (
        "minimum_fields" in payload.model_fields_set
        and validated.minimum_fields in UNSAFE_MINIMUM_FIELDS
        and unsafe_confirmed
    ):
        route.unsafe_minimum_confirmed_at = datetime.now(timezone.utc)
    elif validated.minimum_fields not in UNSAFE_MINIMUM_FIELDS:
        route.unsafe_minimum_confirmed_at = None
    record_activity(
        db,
        user_id=current_user.id,
        route_id=route.id,
        source_id=route.source_id,
        connection_id=route.target_connection_id,
        correlation_id=str(uuid.uuid4()),
        action="route.updated",
        title="Copy route settings updated",
        level=CopyActivityLevel.info,
    )
    db.commit()
    db.refresh(route)
    return route


def pause_route(
    db: Session, *, current_user: User, route_id: uuid.UUID
) -> CopyRoute:
    route = _owned_route(db, current_user=current_user, route_id=route_id)
    if route.state != CopyRouteState.paused:
        route.paused_from_state = route.state
        route.state = CopyRouteState.paused
        record_activity(
            db,
            user_id=current_user.id,
            route_id=route.id,
            source_id=route.source_id,
            connection_id=route.target_connection_id,
            correlation_id=str(uuid.uuid4()),
            action="route.paused",
            title="Copying paused",
            level=CopyActivityLevel.warning,
        )
        db.commit()
        db.refresh(route)
    return route


def resume_route(
    db: Session, *, current_user: User, route_id: uuid.UUID
) -> CopyRoute:
    route = _owned_route(db, current_user=current_user, route_id=route_id)
    user_settings = repo.get_or_create_user_settings(db, user_id=current_user.id)
    policy = repo.get_account_policy(
        db, connection_id=route.target_connection_id, user_id=current_user.id
    )
    if user_settings.is_paused or (policy is not None and policy.is_paused):
        raise HTTPException(
            status_code=status.HTTP_409_CONFLICT,
            detail="Resume the global and account copy-trading controls first.",
        )
    if route.state == CopyRouteState.paused:
        route.state = route.paused_from_state or CopyRouteState.ready
        route.paused_from_state = None
        record_activity(
            db,
            user_id=current_user.id,
            route_id=route.id,
            source_id=route.source_id,
            connection_id=route.target_connection_id,
            correlation_id=str(uuid.uuid4()),
            action="route.resumed",
            title="Copying resumed",
            level=CopyActivityLevel.success,
        )
        db.commit()
        db.refresh(route)
    return route


def activate_route(db: Session, *, current_user: User, route_id: uuid.UUID) -> CopyRoute:
    route = _owned_route(db, current_user=current_user, route_id=route_id)
    source = repo.get_source_for_user(db, source_id=route.source_id, user_id=current_user.id)
    if route.target_connection_id is None:
        raise HTTPException(status_code=status.HTTP_409_CONFLICT, detail="Link a ready copy account before activating this route.")
    _owned_ready_copy_connection(db, current_user=current_user, connection_id=route.target_connection_id)
    if source is None:
        raise HTTPException(
            status_code=status.HTTP_404_NOT_FOUND,
            detail="Telegram source not found.",
        )
    route.state = CopyRouteState.active
    source.state = TelegramSourceState.active
    record_activity(db, user_id=current_user.id, route_id=route.id, source_id=route.source_id, connection_id=route.target_connection_id, correlation_id=str(uuid.uuid4()), action="route.activated", title="Automatic copying started", level=CopyActivityLevel.success)
    db.commit()
    db.refresh(route)
    return route


def delete_route(
    db: Session, *, current_user: User, route_id: uuid.UUID
) -> None:
    route = _owned_route(db, current_user=current_user, route_id=route_id)
    if route.state == CopyRouteState.active:
        raise HTTPException(
            status_code=status.HTTP_409_CONFLICT,
            detail="Pause the route before deleting it.",
        )
    record_activity(
        db,
        user_id=current_user.id,
        route_id=route.id,
        source_id=route.source_id,
        connection_id=route.target_connection_id,
        correlation_id=str(uuid.uuid4()),
        action="route.deleted",
        title="Copy route deleted",
        level=CopyActivityLevel.warning,
    )
    db.delete(route)
    db.commit()


def list_activity(
    db: Session,
    *,
    current_user: User,
    limit: int,
    before: Optional[datetime],
    level: CopyActivityLevel | None = None,
    source_id: uuid.UUID | None = None,
    connection_id: uuid.UUID | None = None,
    search: str | None = None,
) -> list[CopyActivityEvent]:
    return repo.list_activity_for_user(
        db, user_id=current_user.id, limit=limit, before=before, level=level, source_id=source_id, connection_id=connection_id, search=search
    )
