"""Partna Guard HTTP endpoints. Auth via get_current_user; logic in the service."""

import uuid
from typing import List

from fastapi import APIRouter, Depends, Response, status
from sqlalchemy.orm import Session

from app.core.database import get_db
from app.domains.users.models import User
from app.shared.deps import get_current_user

from . import service
from .schemas import (
    GuardAccountResponse,
    GuardEnableRequest,
    GuardUpdateRequest,
)

router = APIRouter(prefix="/guard", tags=["guard"])


@router.get("/accounts", response_model=List[GuardAccountResponse])
def list_guard_accounts(
    db: Session = Depends(get_db),
    current_user: User = Depends(get_current_user),
) -> List[GuardAccountResponse]:
    return service.list_guards(db, current_user=current_user)


@router.post("/accounts", response_model=GuardAccountResponse,
             status_code=status.HTTP_201_CREATED)
def enable_guard(
    req: GuardEnableRequest,
    db: Session = Depends(get_db),
    current_user: User = Depends(get_current_user),
) -> GuardAccountResponse:
    return service.enable_guard(db, current_user=current_user, req=req)


@router.get("/accounts/{guard_id}", response_model=GuardAccountResponse)
def get_guard_account(
    guard_id: uuid.UUID,
    db: Session = Depends(get_db),
    current_user: User = Depends(get_current_user),
) -> GuardAccountResponse:
    return service.get_guard(db, current_user=current_user, guard_id=guard_id)


@router.patch("/accounts/{guard_id}", response_model=GuardAccountResponse)
def update_guard_account(
    guard_id: uuid.UUID,
    req: GuardUpdateRequest,
    db: Session = Depends(get_db),
    current_user: User = Depends(get_current_user),
) -> GuardAccountResponse:
    return service.update_guard(db, current_user=current_user, guard_id=guard_id, req=req)


@router.delete("/accounts/{guard_id}", status_code=status.HTTP_204_NO_CONTENT)
def disable_guard_account(
    guard_id: uuid.UUID,
    db: Session = Depends(get_db),
    current_user: User = Depends(get_current_user),
) -> Response:
    service.disable_guard(db, current_user=current_user, guard_id=guard_id)
    return Response(status_code=status.HTTP_204_NO_CONTENT)


@router.get("/accounts/{guard_id}/monitor", response_model=None)
def get_monitor(
    guard_id: uuid.UUID,
    db: Session = Depends(get_db),
    current_user: User = Depends(get_current_user),
):
    """The flat awareness payload, or null until the first poll lands."""
    return service.get_monitor(db, current_user=current_user, guard_id=guard_id)


@router.get("/accounts/{guard_id}/rules", response_model=None)
def get_rules(
    guard_id: uuid.UUID,
    db: Session = Depends(get_db),
    current_user: User = Depends(get_current_user),
):
    return service.get_rules(db, current_user=current_user, guard_id=guard_id)
