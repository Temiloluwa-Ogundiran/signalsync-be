from datetime import datetime, timezone
from typing import Protocol

from fastapi import HTTPException, status

from app.domains.billing.models import BillingPlan, BillingStatus


class SubscriptionLike(Protocol):
    plan: BillingPlan
    status: BillingStatus
    copy_account_limit: int
    current_period_end: datetime | None
    grace_ends_at: datetime | None


def _now(value: datetime | None) -> datetime:
    return value or datetime.now(timezone.utc)


def has_paid_access(subscription: SubscriptionLike | None, *, now: datetime | None = None) -> bool:
    if subscription is None:
        return False
    current = _now(now)
    if subscription.status == BillingStatus.active:
        return subscription.current_period_end is None or subscription.current_period_end > current
    if subscription.status == BillingStatus.past_due:
        return subscription.grace_ends_at is not None and subscription.grace_ends_at > current
    return False


def has_journal_access(subscription: SubscriptionLike | None, *, now: datetime | None = None) -> bool:
    return has_paid_access(subscription, now=now)


def has_copy_access(subscription: SubscriptionLike | None, *, now: datetime | None = None) -> bool:
    return bool(
        subscription
        and subscription.plan == BillingPlan.copy
        and subscription.copy_account_limit > 0
        and has_paid_access(subscription, now=now)
    )


def subscription_required_detail(required_plan: BillingPlan) -> dict[str, str]:
    return {
        "code": "subscription_required",
        "required_plan": required_plan.value,
        "message": f"An active {required_plan.value} subscription is required.",
    }


def require_copy_account_capacity(
    subscription: SubscriptionLike | None,
    *,
    current_account_count: int,
    now: datetime | None = None,
) -> None:
    if not has_copy_access(subscription, now=now):
        raise HTTPException(
            status_code=status.HTTP_402_PAYMENT_REQUIRED,
            detail=subscription_required_detail(BillingPlan.copy),
        )
    if current_account_count >= subscription.copy_account_limit:
        raise HTTPException(
            status_code=status.HTTP_403_FORBIDDEN,
            detail={
                "code": "copy_account_limit_reached",
                "message": "Your copy-trading account limit has been reached.",
                "account_limit": subscription.copy_account_limit,
            },
        )

__all__ = ["BillingPlan", "BillingStatus"]
