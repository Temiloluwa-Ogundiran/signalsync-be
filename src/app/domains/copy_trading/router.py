import uuid
from datetime import datetime
from typing import Optional

from fastapi import APIRouter, Depends, Query, status
from sqlalchemy.orm import Session

from app.core.database import get_db
from app.domains.copy_trading import service
from app.domains.copy_trading.schemas import (
    CopyAccountPolicyResponse,
    CopyAccountPolicyUpdate,
    CopyActivityResponse,
    CopyRouteCreate,
    CopyRouteResponse,
    CopyRouteUpdate,
    CopyTradingSettingsResponse,
    CopyTradingSettingsUpdate,
)
from app.domains.users.models import User
from app.shared.deps import get_current_user


router = APIRouter(prefix="/copy-trading", tags=["copy-trading"])


@router.get("/settings", response_model=CopyTradingSettingsResponse)
def get_settings(
    db: Session = Depends(get_db),
    current_user: User = Depends(get_current_user),
) -> CopyTradingSettingsResponse:
    settings = service.get_user_settings(db, current_user=current_user)
    return CopyTradingSettingsResponse.model_validate(settings)


@router.patch("/settings", response_model=CopyTradingSettingsResponse)
def update_settings(
    payload: CopyTradingSettingsUpdate,
    db: Session = Depends(get_db),
    current_user: User = Depends(get_current_user),
) -> CopyTradingSettingsResponse:
    settings = service.update_user_settings(db, current_user=current_user, payload=payload)
    return CopyTradingSettingsResponse.model_validate(settings)


@router.get("/account-policies", response_model=list[CopyAccountPolicyResponse])
def list_account_policies(
    db: Session = Depends(get_db),
    current_user: User = Depends(get_current_user),
) -> list[CopyAccountPolicyResponse]:
    policies = service.list_account_policies(db, current_user=current_user)
    return [CopyAccountPolicyResponse.model_validate(policy) for policy in policies]


@router.patch(
    "/account-policies/{account_id}", response_model=CopyAccountPolicyResponse
)
def update_account_policy(
    account_id: uuid.UUID,
    payload: CopyAccountPolicyUpdate,
    db: Session = Depends(get_db),
    current_user: User = Depends(get_current_user),
) -> CopyAccountPolicyResponse:
    policy = service.update_account_policy(
        db,
        current_user=current_user,
        account_id=account_id,
        payload=payload,
    )
    return CopyAccountPolicyResponse.model_validate(policy)


@router.get("/routes", response_model=list[CopyRouteResponse])
def list_routes(
    db: Session = Depends(get_db),
    current_user: User = Depends(get_current_user),
) -> list[CopyRouteResponse]:
    routes = service.list_routes(db, current_user=current_user)
    return [CopyRouteResponse.model_validate(route) for route in routes]


@router.post(
    "/routes", response_model=CopyRouteResponse, status_code=status.HTTP_201_CREATED
)
def create_route(
    payload: CopyRouteCreate,
    db: Session = Depends(get_db),
    current_user: User = Depends(get_current_user),
) -> CopyRouteResponse:
    route = service.create_route(db, current_user=current_user, payload=payload)
    return CopyRouteResponse.model_validate(route)


@router.get("/routes/{route_id}", response_model=CopyRouteResponse)
def get_route(
    route_id: uuid.UUID,
    db: Session = Depends(get_db),
    current_user: User = Depends(get_current_user),
) -> CopyRouteResponse:
    route = service.get_route(db, current_user=current_user, route_id=route_id)
    return CopyRouteResponse.model_validate(route)


@router.patch("/routes/{route_id}", response_model=CopyRouteResponse)
def update_route(
    route_id: uuid.UUID,
    payload: CopyRouteUpdate,
    db: Session = Depends(get_db),
    current_user: User = Depends(get_current_user),
) -> CopyRouteResponse:
    route = service.update_route(
        db, current_user=current_user, route_id=route_id, payload=payload
    )
    return CopyRouteResponse.model_validate(route)


@router.post("/routes/{route_id}/pause", response_model=CopyRouteResponse)
def pause_route(
    route_id: uuid.UUID,
    db: Session = Depends(get_db),
    current_user: User = Depends(get_current_user),
) -> CopyRouteResponse:
    route = service.pause_route(db, current_user=current_user, route_id=route_id)
    return CopyRouteResponse.model_validate(route)


@router.post("/routes/{route_id}/resume", response_model=CopyRouteResponse)
def resume_route(
    route_id: uuid.UUID,
    db: Session = Depends(get_db),
    current_user: User = Depends(get_current_user),
) -> CopyRouteResponse:
    route = service.resume_route(db, current_user=current_user, route_id=route_id)
    return CopyRouteResponse.model_validate(route)


@router.get("/activity", response_model=list[CopyActivityResponse])
def list_activity(
    limit: int = Query(default=50, ge=1, le=100),
    before: Optional[datetime] = None,
    db: Session = Depends(get_db),
    current_user: User = Depends(get_current_user),
) -> list[CopyActivityResponse]:
    events = service.list_activity(
        db,
        current_user=current_user,
        limit=limit,
        before=before,
    )
    return [CopyActivityResponse.model_validate(event) for event in events]
