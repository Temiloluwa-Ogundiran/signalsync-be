import uuid
from datetime import datetime, timezone
from decimal import Decimal
from typing import Optional

from fastapi import HTTPException, status
from sqlalchemy.orm import Session

from app.domains.accounts import repository as account_repo
from app.domains.accounts.models import (
    ImportMethod,
    TradingAccountConnectionState,
    TradingPlatform,
)
from app.domains.copy_trading import repository as repo
from app.domains.copy_trading.models import (
    CopyAccountPolicy,
    CopyActivityEvent,
    CopyActivityLevel,
    CopyRoute,
    CopyRouteState,
    CopyTradingUserSettings,
    TelegramSourceState,
)
from app.domains.copy_trading.schemas import (
    CopyAccountPolicyUpdate,
    CopyRouteCreate,
    CopyRouteUpdate,
    CopyTradingSettingsUpdate,
    UNSAFE_MINIMUM_FIELDS,
)
from app.domains.users.models import User


def magic_number_for_route(route_id: uuid.UUID) -> int:
    return (int.from_bytes(route_id.bytes[:4], "big") & 0x7FFFFFFF) or 1


def _owned_route(
    db: Session, *, current_user: User, route_id: uuid.UUID
) -> CopyRoute:
    route = repo.get_route_for_user(db, route_id=route_id, user_id=current_user.id)
    if route is None:
        raise HTTPException(status_code=status.HTTP_404_NOT_FOUND, detail="Copy route not found.")
    return route


def _owned_ready_mt5_account(db: Session, *, current_user: User, account_id: uuid.UUID):
    account = account_repo.get_account_by_id_for_user(db, account_id, current_user.id)
    if account is None:
        raise HTTPException(status_code=status.HTTP_404_NOT_FOUND, detail="Trading account not found.")
    if (
        account.platform != TradingPlatform.mt5
        or account.import_method != ImportMethod.auto_sync
        or account.connection_state != TradingAccountConnectionState.ready
        or account.is_archived
    ):
        raise HTTPException(
            status_code=status.HTTP_409_CONFLICT,
            detail="The target MT5 account must be connected and ready.",
        )
    return account


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
    account_id: Optional[uuid.UUID] = None,
    body: Optional[str] = None,
    parsed_details: Optional[dict] = None,
    broker_details: Optional[dict] = None,
    encrypted_raw_message: Optional[str] = None,
) -> CopyActivityEvent:
    event = CopyActivityEvent(
        user_id=user_id,
        route_id=route_id,
        source_id=source_id,
        account_id=account_id,
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
    account_id: uuid.UUID,
    payload: CopyAccountPolicyUpdate,
) -> CopyAccountPolicy:
    _owned_ready_mt5_account(db, current_user=current_user, account_id=account_id)
    policy = repo.get_or_create_account_policy(
        db, account_id=account_id, user_id=current_user.id
    )
    changes = payload.model_dump(exclude_unset=True)
    proposed_cap = changes.get("max_lot")
    if proposed_cap is not None:
        largest_route_lot = repo.max_fixed_lot_for_account(
            db, user_id=current_user.id, account_id=account_id
        )
        if largest_route_lot is not None and Decimal(proposed_cap) < Decimal(largest_route_lot):
            raise HTTPException(
                status_code=status.HTTP_409_CONFLICT,
                detail=(
                    "The account cap cannot be lower than the largest configured "
                    f"route lot ({largest_route_lot})."
                ),
            )
    for field, value in changes.items():
        setattr(policy, field, value)
    record_activity(
        db,
        user_id=current_user.id,
        account_id=account_id,
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
    account = _owned_ready_mt5_account(
        db, current_user=current_user, account_id=payload.target_account_id
    )
    existing = repo.get_route_by_source_and_account(
        db,
        user_id=current_user.id,
        source_id=source.id,
        account_id=account.id,
    )
    if existing is not None:
        raise HTTPException(
            status_code=status.HTTP_409_CONFLICT,
            detail="A copy route already exists for this source and account.",
        )
    policy = repo.get_account_policy(
        db, account_id=account.id, user_id=current_user.id
    ) or repo.get_or_create_account_policy(
        db, account_id=account.id, user_id=current_user.id
    )
    if payload.fixed_lot > policy.max_lot:
        raise HTTPException(
            status_code=status.HTTP_422_UNPROCESSABLE_CONTENT,
            detail=f"Fixed lot exceeds the account maximum of {policy.max_lot}.",
        )

    route_id = uuid.uuid4()
    values = payload.model_dump(exclude={"unsafe_minimum_confirmed"})
    route = CopyRoute(
        id=route_id,
        user_id=current_user.id,
        magic_number=magic_number_for_route(route_id),
        state=(
            CopyRouteState.ready
            if source.state in {TelegramSourceState.ready, TelegramSourceState.active}
            else CopyRouteState.draft
        ),
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
        account_id=route.target_account_id,
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
        "target_account_id": route.target_account_id,
        "fixed_lot": route.fixed_lot,
        "take_profit_mode": route.take_profit_mode,
        "lot_distribution": route.lot_distribution,
        "pending_orders_enabled": route.pending_orders_enabled,
        "minimum_fields": route.minimum_fields,
        "assembly_window_seconds": route.assembly_window_seconds,
        "process_all_group_authors": route.process_all_group_authors,
        "notify_success": route.notify_success,
        "notify_failure": route.notify_failure,
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
        db, account_id=route.target_account_id, user_id=current_user.id
    )
    if validated.fixed_lot > policy.max_lot:
        raise HTTPException(
            status_code=status.HTTP_422_UNPROCESSABLE_CONTENT,
            detail=f"Fixed lot exceeds the account maximum of {policy.max_lot}.",
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
        account_id=route.target_account_id,
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
            account_id=route.target_account_id,
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
        db, account_id=route.target_account_id, user_id=current_user.id
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
            account_id=route.target_account_id,
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
    _owned_ready_mt5_account(db, current_user=current_user, account_id=route.target_account_id)
    if source is None or source.state not in {TelegramSourceState.ready, TelegramSourceState.active}:
        raise HTTPException(status_code=status.HTTP_409_CONFLICT, detail="Channel learning must complete before activation.")
    route.state = CopyRouteState.active
    source.state = TelegramSourceState.active
    record_activity(db, user_id=current_user.id, route_id=route.id, source_id=route.source_id, account_id=route.target_account_id, correlation_id=str(uuid.uuid4()), action="route.activated", title="Automatic copying started", level=CopyActivityLevel.success)
    db.commit()
    db.refresh(route)
    return route


def list_activity(
    db: Session,
    *,
    current_user: User,
    limit: int,
    before: Optional[datetime],
) -> list[CopyActivityEvent]:
    return repo.list_activity_for_user(
        db, user_id=current_user.id, limit=limit, before=before
    )
