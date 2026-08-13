import secrets
import string
import uuid
from datetime import datetime, timedelta, timezone
from decimal import Decimal, InvalidOperation, ROUND_HALF_UP
from typing import Any

from fastapi import HTTPException, status
from sqlalchemy import func, select, update
from sqlalchemy.exc import IntegrityError
from sqlalchemy.orm import Session

from app.core.config import settings as app_settings
from app.domains.admin import repository as admin_repo
from app.domains.affiliates import repository as repo
from app.domains.affiliates.models import (
    AffiliateCommission,
    AffiliateProfile,
    AffiliateSetting,
    AffiliateStatus,
    CommissionStatus,
    ReferralAttribution,
)
from app.domains.affiliates.schemas import (
    AdminAffiliateItem,
    AdminAffiliatePage,
    AdminAffiliateSettingsResponse,
    AffiliateCommissionItem,
    AffiliateDashboardResponse,
    UpdateAffiliateSettingsRequest,
)
from app.domains.billing import repository as billing_repo
from app.domains.users.models import User
from app.domains.users import repository as user_repo


MONEY = Decimal("0.01")
CODE_ALPHABET = "23456789ABCDEFGHJKLMNPQRSTUVWXYZ"


def utcnow() -> datetime:
    return datetime.now(timezone.utc)


def _money(value: Any) -> Decimal:
    try:
        return Decimal(str(value or "0")).quantize(MONEY, rounding=ROUND_HALF_UP)
    except (InvalidOperation, ValueError, TypeError) as exc:
        raise ValueError("Invalid monetary amount in billing event") from exc


def get_or_create_settings(db: Session) -> AffiliateSetting:
    config = repo.get_settings(db)
    if config:
        return config
    config = AffiliateSetting(id=1)
    db.add(config)
    db.flush()
    return config


def _new_code() -> str:
    return "".join(secrets.choice(CODE_ALPHABET) for _ in range(10))


def ensure_profile(db: Session, *, user_id: uuid.UUID) -> AffiliateProfile:
    existing = repo.get_profile(db, user_id)
    if existing:
        return existing
    for _ in range(8):
        code = _new_code()
        if not repo.code_exists(db, code):
            profile = AffiliateProfile(user_id=user_id, code=code)
            db.add(profile)
            db.flush()
            return profile
    raise RuntimeError("Could not generate a unique affiliate code")


def effective_rate(profile: AffiliateProfile, config: AffiliateSetting) -> Decimal:
    return Decimal(profile.commission_rate_override) if profile.commission_rate_override is not None else Decimal(config.default_commission_rate)


def validate_code(db: Session, code: str | None) -> bool:
    if not code:
        return False
    profile = repo.get_profile_by_code(db, code.strip().upper())
    return bool(profile and profile.status == AffiliateStatus.active.value)


def attribute_new_user(
    db: Session,
    *,
    referred_user: User,
    referral_code: str | None,
    source: str | None = None,
    campaign: str | None = None,
) -> ReferralAttribution | None:
    ensure_profile(db, user_id=referred_user.id)
    if not referral_code or repo.get_attribution_for_user(db, referred_user.id):
        return None
    profile = repo.get_profile_by_code(db, referral_code.strip().upper())
    if (
        profile is None
        or profile.status != AffiliateStatus.active.value
        or profile.user_id == referred_user.id
    ):
        return None
    attribution = ReferralAttribution(
        affiliate_user_id=profile.user_id,
        referred_user_id=referred_user.id,
        referral_code=profile.code,
        source=source,
        campaign=campaign,
    )
    db.add(attribution)
    db.flush()
    return attribution


def _subscription_id(data: dict[str, Any]) -> str | None:
    nested = data.get("subscription") if isinstance(data.get("subscription"), dict) else {}
    value = data.get("subscription_id") or nested.get("subscription_id") or nested.get("id")
    return str(value) if value else None


def _payment_id(data: dict[str, Any]) -> str | None:
    nested = data.get("payment") if isinstance(data.get("payment"), dict) else {}
    value = data.get("payment_id") or data.get("charge_id") or nested.get("payment_id") or nested.get("id")
    return str(value) if value else None


def create_commission_from_paid_invoice(db: Session, data: dict[str, Any]) -> AffiliateCommission | None:
    invoice_id = data.get("invoice_id") or data.get("id")
    subscription_id = _subscription_id(data)
    if not invoice_id or not subscription_id:
        return None
    invoice_id = str(invoice_id)
    if repo.get_commission_by_invoice(db, invoice_id):
        return None
    subscription = billing_repo.get_subscription_by_provider_id(
        db, provider_subscription_id=subscription_id
    )
    if subscription is None:
        return None
    attribution = repo.get_attribution_for_user(db, subscription.user_id)
    if attribution is None:
        return None
    profile = repo.get_profile(db, attribution.affiliate_user_id)
    if profile is None or profile.status != AffiliateStatus.active.value:
        return None
    config = get_or_create_settings(db)
    if repo.count_qualifying_commissions(db, subscription.user_id) >= config.recurring_months:
        return None

    base = _money(
        data.get("amount_collected")
        or data.get("amount_paid")
        or data.get("total_paid")
        or data.get("amount")
    )
    if base <= 0:
        return None
    rate = effective_rate(profile, config)
    amount = (base * rate / Decimal("100")).quantize(MONEY, rounding=ROUND_HALF_UP)
    item = AffiliateCommission(
        affiliate_user_id=attribution.affiliate_user_id,
        referred_user_id=subscription.user_id,
        provider_invoice_id=invoice_id,
        provider_payment_id=_payment_id(data),
        provider_subscription_id=subscription_id,
        currency=str(data.get("currency") or subscription.currency or "USD").upper(),
        commission_base=base,
        commission_rate=rate,
        commission_amount=amount,
        status=CommissionStatus.pending.value,
        release_at=utcnow() + timedelta(days=config.commission_hold_days),
    )
    db.add(item)
    db.flush()
    return item


def reverse_commission(db: Session, data: dict[str, Any], *, reason: str) -> AffiliateCommission | None:
    invoice_id = data.get("invoice_id")
    nested_payment = data.get("payment") if isinstance(data.get("payment"), dict) else {}
    item = repo.get_commission_for_reversal(
        db,
        payment_id=_payment_id(data) or nested_payment.get("id"),
        invoice_id=str(invoice_id) if invoice_id else None,
    )
    if item is None or item.status in {CommissionStatus.reversed.value, CommissionStatus.paid.value}:
        return None
    refund_base = _money(data.get("amount") or data.get("refund_amount") or item.commission_base)
    ratio = min(Decimal("1"), refund_base / Decimal(item.commission_base)) if item.commission_base else Decimal("1")
    reversed_amount = (Decimal(item.commission_amount) * ratio).quantize(MONEY, rounding=ROUND_HALF_UP)
    item.reversed_amount = max(Decimal(item.reversed_amount), reversed_amount)
    if item.reversed_amount >= item.commission_amount:
        item.status = CommissionStatus.reversed.value
    item.reversed_at = utcnow()
    item.reversal_reason = reason
    db.add(item)
    return item


def dashboard(db: Session, *, current_user: User) -> AffiliateDashboardResponse:
    profile = ensure_profile(db, user_id=current_user.id)
    config = get_or_create_settings(db)
    repo.release_due_commissions(db, user_id=current_user.id, now=utcnow())
    db.commit()
    items = repo.list_commissions(db, user_id=current_user.id)
    referrals, paid_referrals = repo.dashboard_counts(db, user_id=current_user.id)

    def total(status_value: str) -> Decimal:
        return sum(
            (Decimal(item.commission_amount) - Decimal(item.reversed_amount) for item in items if item.status == status_value),
            Decimal("0.00"),
        )

    return AffiliateDashboardResponse(
        code=profile.code,
        referral_url=f"{app_settings.FRONTEND_URL.rstrip('/')}/register?ref={profile.code}",
        status=profile.status,
        effective_commission_rate=effective_rate(profile, config),
        rate_source="individual" if profile.commission_rate_override is not None else "global",
        referrals=referrals,
        paid_referrals=paid_referrals,
        pending_balance=total(CommissionStatus.pending.value),
        available_balance=total(CommissionStatus.available.value),
        paid_balance=total(CommissionStatus.paid.value),
        minimum_payout=config.minimum_payout,
        commission_hold_days=config.commission_hold_days,
        recurring_months=config.recurring_months,
        commissions=[AffiliateCommissionItem.model_validate(item, from_attributes=True) for item in items],
    )


def admin_settings(db: Session) -> AdminAffiliateSettingsResponse:
    config = get_or_create_settings(db)
    custom = int(db.execute(select(func.count(AffiliateProfile.user_id)).where(AffiliateProfile.commission_rate_override.is_not(None))).scalar_one() or 0)
    return AdminAffiliateSettingsResponse(
        default_commission_rate=config.default_commission_rate,
        commission_hold_days=config.commission_hold_days,
        recurring_months=config.recurring_months,
        minimum_payout=config.minimum_payout,
        custom_rate_users=custom,
    )


def update_settings(
    db: Session, *, actor: User, payload: UpdateAffiliateSettingsRequest
) -> AdminAffiliateSettingsResponse:
    config = get_or_create_settings(db)
    before = {
        "default_commission_rate": str(config.default_commission_rate),
        "commission_hold_days": config.commission_hold_days,
        "recurring_months": config.recurring_months,
        "minimum_payout": str(config.minimum_payout),
    }
    config.default_commission_rate = payload.default_commission_rate
    config.commission_hold_days = payload.commission_hold_days
    config.recurring_months = payload.recurring_months
    config.minimum_payout = payload.minimum_payout
    config.updated_by_user_id = actor.id
    cleared = 0
    if payload.clear_individual_overrides:
        result = db.execute(update(AffiliateProfile).where(AffiliateProfile.commission_rate_override.is_not(None)).values(commission_rate_override=None, updated_at=utcnow()))
        cleared = int(result.rowcount or 0)
    admin_repo.create_audit_event(
        db,
        actor_user_id=actor.id,
        target_user_id=None,
        action="affiliate.settings.updated",
        reason=payload.reason,
        details={"before": before, "after": payload.model_dump(mode="json"), "overrides_cleared": cleared},
    )
    db.commit()
    return admin_settings(db)


def update_user_rate(
    db: Session, *, actor: User, user_id: uuid.UUID, rate: Decimal | None, reason: str
) -> None:
    if user_repo.get_by_id(db, user_id) is None:
        raise HTTPException(status_code=status.HTTP_404_NOT_FOUND, detail="Affiliate user was not found.")
    profile = ensure_profile(db, user_id=user_id)
    previous = profile.commission_rate_override
    profile.commission_rate_override = rate
    admin_repo.create_audit_event(
        db,
        actor_user_id=actor.id,
        target_user_id=user_id,
        action="affiliate.rate.updated",
        reason=reason,
        details={"previous_rate": str(previous) if previous is not None else None, "new_rate": str(rate) if rate is not None else None},
    )
    db.commit()


def admin_list(db: Session) -> AdminAffiliatePage:
    config = get_or_create_settings(db)
    items = []
    for profile, user in repo.list_affiliates(db):
        referrals, _ = repo.dashboard_counts(db, user_id=user.id)
        items.append(AdminAffiliateItem(
            user_id=user.id,
            email=user.email,
            display_name=user.display_name,
            code=profile.code,
            status=profile.status,
            commission_rate_override=profile.commission_rate_override,
            effective_commission_rate=effective_rate(profile, config),
            referrals=referrals,
            created_at=profile.created_at,
        ))
    return AdminAffiliatePage(items=items)
