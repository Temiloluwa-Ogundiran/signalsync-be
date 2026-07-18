"""add Bachs subscriptions

Revision ID: f1b2c3d4e5f6
Revises: e0a2b4c6d8f0
Create Date: 2026-07-18 13:00:00
"""

from typing import Sequence, Union

import sqlalchemy as sa
from alembic import op
from sqlalchemy.dialects import postgresql


revision: str = "f1b2c3d4e5f6"
down_revision: Union[str, Sequence[str], None] = "e0a2b4c6d8f0"
branch_labels: Union[str, Sequence[str], None] = None
depends_on: Union[str, Sequence[str], None] = None


def upgrade() -> None:
    billing_plan = postgresql.ENUM(
        "journal", "copy", name="billingplanenum", create_type=False
    )
    billing_status = postgresql.ENUM(
        "active",
        "past_due",
        "unpaid",
        "canceled",
        name="billingstatusenum",
        create_type=False,
    )
    billing_plan.create(op.get_bind(), checkfirst=True)
    billing_status.create(op.get_bind(), checkfirst=True)

    op.create_table(
        "billing_subscriptions",
        sa.Column("id", postgresql.UUID(as_uuid=True), nullable=False),
        sa.Column("user_id", postgresql.UUID(as_uuid=True), nullable=False),
        sa.Column("provider_customer_id", sa.String(length=100), nullable=True),
        sa.Column("provider_subscription_id", sa.String(length=100), nullable=False),
        sa.Column("provider_product_id", sa.String(length=100), nullable=False),
        sa.Column("plan", billing_plan, nullable=False),
        sa.Column("status", billing_status, nullable=False),
        sa.Column("copy_account_limit", sa.Integer(), nullable=False),
        sa.Column("currency", sa.String(length=3), nullable=False),
        sa.Column("amount", sa.String(length=32), nullable=False),
        sa.Column("current_period_start", sa.DateTime(timezone=True), nullable=True),
        sa.Column("current_period_end", sa.DateTime(timezone=True), nullable=True),
        sa.Column("grace_ends_at", sa.DateTime(timezone=True), nullable=True),
        sa.Column("cancel_at_period_end", sa.Boolean(), nullable=False),
        sa.Column("canceled_at", sa.DateTime(timezone=True), nullable=True),
        sa.Column("pending_plan", billing_plan, nullable=True),
        sa.Column("pending_copy_account_limit", sa.Integer(), nullable=True),
        sa.Column("pending_effective_at", sa.DateTime(timezone=True), nullable=True),
        sa.Column("created_at", sa.DateTime(timezone=True), nullable=False),
        sa.Column("updated_at", sa.DateTime(timezone=True), nullable=False),
        sa.ForeignKeyConstraint(["user_id"], ["users.id"], ondelete="CASCADE"),
        sa.PrimaryKeyConstraint("id"),
        sa.UniqueConstraint("provider_subscription_id"),
        sa.UniqueConstraint("user_id"),
    )
    op.create_index(
        op.f("ix_billing_subscriptions_provider_customer_id"),
        "billing_subscriptions",
        ["provider_customer_id"],
    )
    op.create_index(
        op.f("ix_billing_subscriptions_user_id"),
        "billing_subscriptions",
        ["user_id"],
    )

    op.create_table(
        "billing_webhook_events",
        sa.Column("id", postgresql.UUID(as_uuid=True), nullable=False),
        sa.Column("provider_event_id", sa.String(length=100), nullable=False),
        sa.Column("event_type", sa.String(length=100), nullable=False),
        sa.Column("payload", postgresql.JSONB(astext_type=sa.Text()), nullable=False),
        sa.Column("processed_at", sa.DateTime(timezone=True), nullable=False),
        sa.Column("error", sa.Text(), nullable=True),
        sa.PrimaryKeyConstraint("id"),
    )
    op.create_index(
        op.f("ix_billing_webhook_events_provider_event_id"),
        "billing_webhook_events",
        ["provider_event_id"],
        unique=True,
    )


def downgrade() -> None:
    op.drop_index(
        op.f("ix_billing_webhook_events_provider_event_id"),
        table_name="billing_webhook_events",
    )
    op.drop_table("billing_webhook_events")
    op.drop_index(op.f("ix_billing_subscriptions_user_id"), table_name="billing_subscriptions")
    op.drop_index(
        op.f("ix_billing_subscriptions_provider_customer_id"),
        table_name="billing_subscriptions",
    )
    op.drop_table("billing_subscriptions")
    postgresql.ENUM(name="billingstatusenum").drop(op.get_bind(), checkfirst=True)
    postgresql.ENUM(name="billingplanenum").drop(op.get_bind(), checkfirst=True)
