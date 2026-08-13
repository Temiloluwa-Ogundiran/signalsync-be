import enum
import uuid
from datetime import datetime, timezone
from decimal import Decimal
from typing import Optional

from sqlalchemy import DateTime, ForeignKey, Index, Integer, Numeric, String, UniqueConstraint
from sqlalchemy.dialects.postgresql import UUID
from sqlalchemy.orm import Mapped, mapped_column

from app.core.database import Base


def utcnow() -> datetime:
    return datetime.now(timezone.utc)


class AffiliateStatus(str, enum.Enum):
    active = "active"
    suspended = "suspended"


class CommissionStatus(str, enum.Enum):
    pending = "pending"
    available = "available"
    paid = "paid"
    reversed = "reversed"


class AffiliateSetting(Base):
    __tablename__ = "affiliate_settings"

    id: Mapped[int] = mapped_column(Integer, primary_key=True, default=1)
    default_commission_rate: Mapped[Decimal] = mapped_column(
        Numeric(5, 2), nullable=False, default=Decimal("20.00")
    )
    commission_hold_days: Mapped[int] = mapped_column(Integer, nullable=False, default=30)
    recurring_months: Mapped[int] = mapped_column(Integer, nullable=False, default=12)
    minimum_payout: Mapped[Decimal] = mapped_column(
        Numeric(12, 2), nullable=False, default=Decimal("25.00")
    )
    updated_by_user_id: Mapped[Optional[uuid.UUID]] = mapped_column(
        UUID(as_uuid=True), ForeignKey("users.id", ondelete="SET NULL"), nullable=True
    )
    created_at: Mapped[datetime] = mapped_column(DateTime(timezone=True), default=utcnow)
    updated_at: Mapped[datetime] = mapped_column(
        DateTime(timezone=True), default=utcnow, onupdate=utcnow
    )


class AffiliateProfile(Base):
    __tablename__ = "affiliate_profiles"
    __table_args__ = (Index("ix_affiliate_profiles_code", "code", unique=True),)

    user_id: Mapped[uuid.UUID] = mapped_column(
        UUID(as_uuid=True), ForeignKey("users.id", ondelete="CASCADE"), primary_key=True
    )
    code: Mapped[str] = mapped_column(String(16), nullable=False, unique=True)
    status: Mapped[str] = mapped_column(
        String(16), nullable=False, default=AffiliateStatus.active.value
    )
    commission_rate_override: Mapped[Optional[Decimal]] = mapped_column(
        Numeric(5, 2), nullable=True
    )
    created_at: Mapped[datetime] = mapped_column(DateTime(timezone=True), default=utcnow)
    updated_at: Mapped[datetime] = mapped_column(
        DateTime(timezone=True), default=utcnow, onupdate=utcnow
    )


class ReferralAttribution(Base):
    __tablename__ = "referral_attributions"
    __table_args__ = (
        UniqueConstraint("referred_user_id", name="uq_referral_attribution_referred_user"),
        Index("ix_referral_attribution_affiliate_created", "affiliate_user_id", "created_at"),
    )

    id: Mapped[uuid.UUID] = mapped_column(
        UUID(as_uuid=True), primary_key=True, default=uuid.uuid4
    )
    affiliate_user_id: Mapped[uuid.UUID] = mapped_column(
        UUID(as_uuid=True), ForeignKey("users.id", ondelete="RESTRICT"), nullable=False
    )
    referred_user_id: Mapped[uuid.UUID] = mapped_column(
        UUID(as_uuid=True), ForeignKey("users.id", ondelete="CASCADE"), nullable=False
    )
    referral_code: Mapped[str] = mapped_column(String(16), nullable=False)
    source: Mapped[Optional[str]] = mapped_column(String(64), nullable=True)
    campaign: Mapped[Optional[str]] = mapped_column(String(100), nullable=True)
    created_at: Mapped[datetime] = mapped_column(DateTime(timezone=True), default=utcnow)


class AffiliateCommission(Base):
    __tablename__ = "affiliate_commissions"
    __table_args__ = (
        UniqueConstraint("provider_invoice_id", name="uq_affiliate_commission_invoice"),
        Index("ix_affiliate_commission_affiliate_status", "affiliate_user_id", "status"),
        Index("ix_affiliate_commission_referred", "referred_user_id", "created_at"),
    )

    id: Mapped[uuid.UUID] = mapped_column(
        UUID(as_uuid=True), primary_key=True, default=uuid.uuid4
    )
    affiliate_user_id: Mapped[uuid.UUID] = mapped_column(
        UUID(as_uuid=True), ForeignKey("users.id", ondelete="RESTRICT"), nullable=False
    )
    referred_user_id: Mapped[uuid.UUID] = mapped_column(
        UUID(as_uuid=True), ForeignKey("users.id", ondelete="RESTRICT"), nullable=False
    )
    provider_invoice_id: Mapped[str] = mapped_column(String(100), nullable=False)
    provider_payment_id: Mapped[Optional[str]] = mapped_column(String(100), nullable=True, index=True)
    provider_subscription_id: Mapped[str] = mapped_column(String(100), nullable=False, index=True)
    currency: Mapped[str] = mapped_column(String(3), nullable=False, default="USD")
    commission_base: Mapped[Decimal] = mapped_column(Numeric(14, 2), nullable=False)
    commission_rate: Mapped[Decimal] = mapped_column(Numeric(5, 2), nullable=False)
    commission_amount: Mapped[Decimal] = mapped_column(Numeric(14, 2), nullable=False)
    reversed_amount: Mapped[Decimal] = mapped_column(
        Numeric(14, 2), nullable=False, default=Decimal("0.00")
    )
    status: Mapped[str] = mapped_column(
        String(16), nullable=False, default=CommissionStatus.pending.value
    )
    release_at: Mapped[datetime] = mapped_column(DateTime(timezone=True), nullable=False)
    reversed_at: Mapped[Optional[datetime]] = mapped_column(DateTime(timezone=True), nullable=True)
    reversal_reason: Mapped[Optional[str]] = mapped_column(String(255), nullable=True)
    paid_at: Mapped[Optional[datetime]] = mapped_column(DateTime(timezone=True), nullable=True)
    created_at: Mapped[datetime] = mapped_column(DateTime(timezone=True), default=utcnow)
    updated_at: Mapped[datetime] = mapped_column(
        DateTime(timezone=True), default=utcnow, onupdate=utcnow
    )
