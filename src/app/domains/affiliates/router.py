import uuid

from fastapi import APIRouter, Depends, Query
from sqlalchemy.orm import Session

from app.core.database import get_db
from app.domains.admin.deps import require_admin
from app.domains.affiliates import service
from app.domains.affiliates.schemas import (
    AdminAffiliatePage,
    AdminAffiliateSettingsResponse,
    AffiliateDashboardResponse,
    UpdateAffiliateRateRequest,
    UpdateAffiliateSettingsRequest,
    ValidateReferralResponse,
)
from app.domains.users.models import PlatformRole, User
from app.shared.deps import get_current_user


router = APIRouter(tags=["affiliates"])


@router.get("/affiliates/validate", response_model=ValidateReferralResponse)
def validate_referral(
    code: str = Query(min_length=4, max_length=16),
    db: Session = Depends(get_db),
) -> ValidateReferralResponse:
    return ValidateReferralResponse(valid=service.validate_code(db, code))


@router.get("/affiliates/me", response_model=AffiliateDashboardResponse)
def my_affiliate_dashboard(
    current_user: User = Depends(get_current_user),
    db: Session = Depends(get_db),
) -> AffiliateDashboardResponse:
    return service.dashboard(db, current_user=current_user)


@router.get("/admin/affiliates", response_model=AdminAffiliatePage)
def admin_affiliates(
    _: User = Depends(require_admin), db: Session = Depends(get_db)
) -> AdminAffiliatePage:
    return service.admin_list(db)


@router.get("/admin/affiliates/settings", response_model=AdminAffiliateSettingsResponse)
def get_admin_affiliate_settings(
    _: User = Depends(require_admin), db: Session = Depends(get_db)
) -> AdminAffiliateSettingsResponse:
    return service.admin_settings(db)


@router.put("/admin/affiliates/settings", response_model=AdminAffiliateSettingsResponse)
def put_admin_affiliate_settings(
    payload: UpdateAffiliateSettingsRequest,
    actor: User = Depends(require_admin),
    db: Session = Depends(get_db),
) -> AdminAffiliateSettingsResponse:
    if actor.platform_role != PlatformRole.SUPER_ADMIN:
        from fastapi import HTTPException
        raise HTTPException(status_code=403, detail="Super administrator access required.")
    return service.update_settings(db, actor=actor, payload=payload)


@router.put("/admin/affiliates/{user_id}/rate")
def put_affiliate_rate(
    user_id: uuid.UUID,
    payload: UpdateAffiliateRateRequest,
    actor: User = Depends(require_admin),
    db: Session = Depends(get_db),
) -> dict[str, bool]:
    if actor.platform_role != PlatformRole.SUPER_ADMIN:
        from fastapi import HTTPException
        raise HTTPException(status_code=403, detail="Super administrator access required.")
    service.update_user_rate(
        db, actor=actor, user_id=user_id, rate=payload.commission_rate, reason=payload.reason
    )
    return {"updated": True}
