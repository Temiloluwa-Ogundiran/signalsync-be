import uuid
from datetime import datetime
from decimal import Decimal
from typing import Optional

from pydantic import BaseModel, ConfigDict, Field, field_validator


class AffiliateCommissionItem(BaseModel):
    id: uuid.UUID
    currency: str
    commission_base: Decimal
    commission_rate: Decimal
    commission_amount: Decimal
    reversed_amount: Decimal
    status: str
    release_at: datetime
    created_at: datetime


class AffiliateDashboardResponse(BaseModel):
    code: str
    referral_url: str
    status: str
    effective_commission_rate: Decimal
    rate_source: str
    referrals: int
    paid_referrals: int
    pending_balance: Decimal
    available_balance: Decimal
    paid_balance: Decimal
    minimum_payout: Decimal
    commission_hold_days: int
    recurring_months: int
    commissions: list[AffiliateCommissionItem]


class AdminAffiliateSettingsResponse(BaseModel):
    default_commission_rate: Decimal
    commission_hold_days: int
    recurring_months: int
    minimum_payout: Decimal
    custom_rate_users: int


class UpdateAffiliateSettingsRequest(BaseModel):
    model_config = ConfigDict(extra="forbid")

    default_commission_rate: Decimal = Field(ge=0, le=50, decimal_places=2)
    commission_hold_days: int = Field(ge=0, le=180)
    recurring_months: int = Field(ge=1, le=60)
    minimum_payout: Decimal = Field(ge=0, le=10000, decimal_places=2)
    clear_individual_overrides: bool = False
    reason: str = Field(min_length=3, max_length=500)


class UpdateAffiliateRateRequest(BaseModel):
    model_config = ConfigDict(extra="forbid")

    commission_rate: Optional[Decimal] = Field(default=None, ge=0, le=50, decimal_places=2)
    reason: str = Field(min_length=3, max_length=500)


class AdminAffiliateItem(BaseModel):
    user_id: uuid.UUID
    email: str
    display_name: Optional[str]
    code: str
    status: str
    commission_rate_override: Optional[Decimal]
    effective_commission_rate: Decimal
    referrals: int
    created_at: datetime


class AdminAffiliatePage(BaseModel):
    items: list[AdminAffiliateItem]


class ValidateReferralResponse(BaseModel):
    valid: bool


class ReferralMetadata(BaseModel):
    referral_code: Optional[str] = Field(default=None, min_length=4, max_length=16)
    referral_source_detail: Optional[str] = Field(default=None, max_length=64)
    referral_campaign: Optional[str] = Field(default=None, max_length=100)

    @field_validator("referral_code")
    @classmethod
    def normalize_code(cls, value: Optional[str]) -> Optional[str]:
        return value.strip().upper() if value else None
