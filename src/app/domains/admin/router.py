import uuid

from fastapi import APIRouter, Depends, Query, Request, status
from sqlalchemy.orm import Session

from app.core.config import settings
from app.core.database import get_db
from app.core.rate_limit import limiter
from app.domains.admin import service
from app.domains.admin.deps import require_admin, require_operator, require_technical_admin
from app.domains.admin.metrics import render_metrics
from app.domains.admin.schemas import (
    AdminAuditPage,
    AdminMutationResponse,
    AdminOverviewResponse,
    AdminSystemResponse,
    AdminUserDetail,
    AdminUserPage,
    ProductEventCreate,
    ProductEventResponse,
    SuspendUserRequest,
    UpdateUserRoleRequest,
)
from app.domains.users.models import PlatformRole, User
from app.shared.deps import get_optional_current_user


router = APIRouter(tags=["administration"])


@router.post("/events", response_model=ProductEventResponse, status_code=status.HTTP_202_ACCEPTED)
@limiter.limit(settings.RATE_LIMIT_ANALYTICS)
def collect_product_event(
    request: Request,
    payload: ProductEventCreate,
    current_user: User | None = Depends(get_optional_current_user),
    db: Session = Depends(get_db),
) -> ProductEventResponse:
    service.ingest_product_event(db, payload=payload, current_user=current_user)
    return ProductEventResponse()


@router.get("/metrics", include_in_schema=False)
def metrics(request: Request):
    return render_metrics(request)


@router.get("/admin/overview", response_model=AdminOverviewResponse)
def admin_overview(
    days: int = Query(default=30, ge=1, le=365),
    _: User = Depends(require_admin),
    db: Session = Depends(get_db),
) -> AdminOverviewResponse:
    return service.overview(db, days=days)


@router.get("/admin/users", response_model=AdminUserPage)
def admin_users(
    query: str | None = Query(default=None, max_length=255),
    user_status: str | None = Query(
        default=None, pattern="^(active|suspended|unverified|deleted)$"
    ),
    role: PlatformRole | None = None,
    cursor: str | None = None,
    limit: int = Query(default=50, ge=1, le=100),
    _: User = Depends(require_admin),
    db: Session = Depends(get_db),
) -> AdminUserPage:
    return service.list_users(
        db,
        query=query,
        user_status=user_status,
        role=role,
        cursor=cursor,
        limit=limit,
    )


@router.get("/admin/users/{user_id}", response_model=AdminUserDetail)
def admin_user_detail(
    user_id: uuid.UUID,
    _: User = Depends(require_admin),
    db: Session = Depends(get_db),
) -> AdminUserDetail:
    return service.get_user_detail(db, user_id=user_id)


@router.post("/admin/users/{user_id}/suspend", response_model=AdminMutationResponse)
def suspend_user(
    user_id: uuid.UUID,
    payload: SuspendUserRequest,
    actor: User = Depends(require_admin),
    db: Session = Depends(get_db),
) -> AdminMutationResponse:
    service.suspend_user(db, actor=actor, user_id=user_id, reason=payload.reason)
    return AdminMutationResponse(message="User suspended and active sessions revoked.")


@router.post("/admin/users/{user_id}/restore", response_model=AdminMutationResponse)
def restore_user(
    user_id: uuid.UUID,
    actor: User = Depends(require_admin),
    db: Session = Depends(get_db),
) -> AdminMutationResponse:
    service.restore_user(db, actor=actor, user_id=user_id)
    return AdminMutationResponse(message="User restored.")


@router.post("/admin/users/{user_id}/revoke-sessions", response_model=AdminMutationResponse)
def revoke_sessions(
    user_id: uuid.UUID,
    actor: User = Depends(require_admin),
    db: Session = Depends(get_db),
) -> AdminMutationResponse:
    service.revoke_user_sessions(db, actor=actor, user_id=user_id)
    return AdminMutationResponse(message="Active sessions revoked.")


@router.put("/admin/users/{user_id}/role", response_model=AdminMutationResponse)
def update_role(
    user_id: uuid.UUID,
    payload: UpdateUserRoleRequest,
    actor: User = Depends(require_admin),
    db: Session = Depends(get_db),
) -> AdminMutationResponse:
    service.update_user_role(
        db, actor=actor, user_id=user_id, role=payload.role, reason=payload.reason
    )
    return AdminMutationResponse(message="User role updated.")


@router.get("/admin/audit", response_model=AdminAuditPage)
def audit_events(
    cursor: str | None = None,
    limit: int = Query(default=50, ge=1, le=100),
    _: User = Depends(require_operator),
    db: Session = Depends(get_db),
) -> AdminAuditPage:
    return service.list_audit_events(db, cursor=cursor, limit=limit)


@router.get("/admin/system", response_model=AdminSystemResponse)
def system_overview(
    _: User = Depends(require_technical_admin),
    db: Session = Depends(get_db),
) -> AdminSystemResponse:
    return service.system_overview(db)
