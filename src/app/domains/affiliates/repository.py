import uuid
from datetime import datetime

from sqlalchemy import func, select, update
from sqlalchemy.orm import Session

from app.domains.affiliates.models import (
    AffiliateCommission,
    AffiliateProfile,
    AffiliateSetting,
    CommissionStatus,
    ReferralAttribution,
)
from app.domains.users.models import User


def get_settings(db: Session) -> AffiliateSetting | None:
    return db.get(AffiliateSetting, 1)


def get_profile(db: Session, user_id: uuid.UUID) -> AffiliateProfile | None:
    return db.get(AffiliateProfile, user_id)


def get_profile_by_code(db: Session, code: str) -> AffiliateProfile | None:
    return db.execute(
        select(AffiliateProfile).where(AffiliateProfile.code == code.upper())
    ).scalar_one_or_none()


def code_exists(db: Session, code: str) -> bool:
    return db.execute(
        select(AffiliateProfile.user_id).where(AffiliateProfile.code == code)
    ).first() is not None


def get_attribution_for_user(
    db: Session, referred_user_id: uuid.UUID
) -> ReferralAttribution | None:
    return db.execute(
        select(ReferralAttribution).where(
            ReferralAttribution.referred_user_id == referred_user_id
        )
    ).scalar_one_or_none()


def count_qualifying_commissions(db: Session, referred_user_id: uuid.UUID) -> int:
    return int(
        db.execute(
            select(func.count(AffiliateCommission.id)).where(
                AffiliateCommission.referred_user_id == referred_user_id,
                AffiliateCommission.status != CommissionStatus.reversed.value,
            )
        ).scalar_one()
        or 0
    )


def get_commission_by_invoice(
    db: Session, provider_invoice_id: str
) -> AffiliateCommission | None:
    return db.execute(
        select(AffiliateCommission).where(
            AffiliateCommission.provider_invoice_id == provider_invoice_id
        )
    ).scalar_one_or_none()


def get_commission_for_reversal(
    db: Session, *, payment_id: str | None, invoice_id: str | None
) -> AffiliateCommission | None:
    if invoice_id:
        item = get_commission_by_invoice(db, invoice_id)
        if item:
            return item
    if payment_id:
        return db.execute(
            select(AffiliateCommission)
            .where(AffiliateCommission.provider_payment_id == payment_id)
            .order_by(AffiliateCommission.created_at.desc())
        ).scalars().first()
    return None


def release_due_commissions(db: Session, *, user_id: uuid.UUID, now: datetime) -> int:
    result = db.execute(
        update(AffiliateCommission)
        .where(
            AffiliateCommission.affiliate_user_id == user_id,
            AffiliateCommission.status == CommissionStatus.pending.value,
            AffiliateCommission.release_at <= now,
        )
        .values(status=CommissionStatus.available.value, updated_at=now)
    )
    return int(result.rowcount or 0)


def list_commissions(
    db: Session, *, user_id: uuid.UUID, limit: int = 100
) -> list[AffiliateCommission]:
    return list(
        db.execute(
            select(AffiliateCommission)
            .where(AffiliateCommission.affiliate_user_id == user_id)
            .order_by(AffiliateCommission.created_at.desc())
            .limit(limit)
        ).scalars()
    )


def dashboard_counts(db: Session, *, user_id: uuid.UUID) -> tuple[int, int]:
    referrals = int(
        db.execute(
            select(func.count(ReferralAttribution.id)).where(
                ReferralAttribution.affiliate_user_id == user_id
            )
        ).scalar_one()
        or 0
    )
    paid_referrals = int(
        db.execute(
            select(func.count(func.distinct(AffiliateCommission.referred_user_id))).where(
                AffiliateCommission.affiliate_user_id == user_id,
                AffiliateCommission.status != CommissionStatus.reversed.value,
            )
        ).scalar_one()
        or 0
    )
    return referrals, paid_referrals


def list_affiliates(db: Session, *, limit: int = 200) -> list[tuple[AffiliateProfile, User]]:
    return list(
        db.execute(
            select(AffiliateProfile, User)
            .join(User, User.id == AffiliateProfile.user_id)
            .order_by(User.created_at.desc())
            .limit(limit)
        ).all()
    )
