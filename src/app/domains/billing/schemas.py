from datetime import datetime
from typing import Literal, Optional

from pydantic import BaseModel, Field, HttpUrl

from app.domains.billing.models import BillingPlan, BillingStatus


class SubscriptionResponse(BaseModel):
    plan: Optional[BillingPlan] = None
    status: Optional[BillingStatus] = None
    has_journal_access: bool
    has_copy_access: bool
    copy_account_limit: int = 0
    current_period_end: Optional[datetime] = None
    grace_ends_at: Optional[datetime] = None
    cancel_at_period_end: bool = False
    pending_plan: Optional[BillingPlan] = None
    pending_copy_account_limit: Optional[int] = None
    pending_effective_at: Optional[datetime] = None


class CheckoutRequest(BaseModel):
    plan: BillingPlan
    copy_accounts: int = Field(default=1, ge=1, le=10)


class CheckoutResponse(BaseModel):
    action: Literal["checkout", "updated", "scheduled"]
    checkout_url: Optional[HttpUrl] = None
    subscription: SubscriptionResponse


class BillingMessageResponse(BaseModel):
    message: str
    subscription: SubscriptionResponse
