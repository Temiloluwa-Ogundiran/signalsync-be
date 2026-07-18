import enum
import uuid
from datetime import datetime, timezone
from typing import Optional

from sqlalchemy import Boolean, DateTime, Enum, ForeignKey, Integer, String, Text, UniqueConstraint
from sqlalchemy.dialects.postgresql import JSONB, UUID
from sqlalchemy.orm import Mapped, mapped_column

from app.core.database import Base


def utcnow() -> datetime:
    return datetime.now(timezone.utc)


def enum_values(enum_type: type[enum.Enum]) -> list[str]:
    return [item.value for item in enum_type]


class BillingPlan(str, enum.Enum):
    journal = "journal"
    copy = "copy"


class BillingStatus(str, enum.Enum):
    active = "active"
    past_due = "past_due"
    unpaid = "unpaid"
    canceled = "canceled"


class BillingSubscription(Base):
    __tablename__ = "billing_subscriptions"
    __table_args__ = (
        UniqueConstraint("user_id"),
        UniqueConstraint("provider_subscription_id"),
    )

    id: Mapped[uuid.UUID] = mapped_column(UUID(as_uuid=True), primary_key=True, default=uuid.uuid4)
    user_id: Mapped[uuid.UUID] = mapped_column(
        UUID(as_uuid=True), ForeignKey("users.id", ondelete="CASCADE"), nullable=False, index=True
    )
    provider_customer_id: Mapped[Optional[str]] = mapped_column(String(100), nullable=True, index=True)
    provider_subscription_id: Mapped[str] = mapped_column(String(100), nullable=False)
    provider_product_id: Mapped[str] = mapped_column(String(100), nullable=False)
    plan: Mapped[BillingPlan] = mapped_column(
        Enum(BillingPlan, values_callable=enum_values, name="billingplanenum"), nullable=False
    )
    status: Mapped[BillingStatus] = mapped_column(
        Enum(BillingStatus, values_callable=enum_values, name="billingstatusenum"), nullable=False
    )
    copy_account_limit: Mapped[int] = mapped_column(Integer, nullable=False, default=0)
    currency: Mapped[str] = mapped_column(String(3), nullable=False, default="USD")
    amount: Mapped[str] = mapped_column(String(32), nullable=False)
    current_period_start: Mapped[Optional[datetime]] = mapped_column(DateTime(timezone=True), nullable=True)
    current_period_end: Mapped[Optional[datetime]] = mapped_column(DateTime(timezone=True), nullable=True)
    grace_ends_at: Mapped[Optional[datetime]] = mapped_column(DateTime(timezone=True), nullable=True)
    cancel_at_period_end: Mapped[bool] = mapped_column(Boolean, nullable=False, default=False)
    canceled_at: Mapped[Optional[datetime]] = mapped_column(DateTime(timezone=True), nullable=True)
    pending_plan: Mapped[Optional[BillingPlan]] = mapped_column(
        Enum(BillingPlan, values_callable=enum_values, name="billingplanenum", create_type=False),
        nullable=True,
    )
    pending_copy_account_limit: Mapped[Optional[int]] = mapped_column(Integer, nullable=True)
    pending_effective_at: Mapped[Optional[datetime]] = mapped_column(DateTime(timezone=True), nullable=True)
    created_at: Mapped[datetime] = mapped_column(DateTime(timezone=True), nullable=False, default=utcnow)
    updated_at: Mapped[datetime] = mapped_column(
        DateTime(timezone=True), nullable=False, default=utcnow, onupdate=utcnow
    )


class BillingWebhookEvent(Base):
    __tablename__ = "billing_webhook_events"

    id: Mapped[uuid.UUID] = mapped_column(UUID(as_uuid=True), primary_key=True, default=uuid.uuid4)
    provider_event_id: Mapped[str] = mapped_column(String(100), nullable=False, unique=True, index=True)
    event_type: Mapped[str] = mapped_column(String(100), nullable=False)
    payload: Mapped[dict] = mapped_column(JSONB, nullable=False, default=dict)
    processed_at: Mapped[datetime] = mapped_column(DateTime(timezone=True), nullable=False, default=utcnow)
    error: Mapped[Optional[str]] = mapped_column(Text, nullable=True)
