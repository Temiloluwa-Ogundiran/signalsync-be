"""add affiliate program

Revision ID: b3d5f7a9c1e3
Revises: a2c4e6f8b0d2
Create Date: 2026-08-13 12:00:00
"""

from typing import Sequence, Union

import sqlalchemy as sa
from alembic import op
from sqlalchemy.dialects import postgresql


revision: str = "b3d5f7a9c1e3"
down_revision: Union[str, Sequence[str], None] = "a2c4e6f8b0d2"
branch_labels: Union[str, Sequence[str], None] = None
depends_on: Union[str, Sequence[str], None] = None


def upgrade() -> None:
    op.create_table(
        "affiliate_settings",
        sa.Column("id", sa.Integer(), nullable=False),
        sa.Column("default_commission_rate", sa.Numeric(5, 2), nullable=False),
        sa.Column("commission_hold_days", sa.Integer(), nullable=False),
        sa.Column("recurring_months", sa.Integer(), nullable=False),
        sa.Column("minimum_payout", sa.Numeric(12, 2), nullable=False),
        sa.Column("updated_by_user_id", postgresql.UUID(as_uuid=True), nullable=True),
        sa.Column("created_at", sa.DateTime(timezone=True), nullable=False),
        sa.Column("updated_at", sa.DateTime(timezone=True), nullable=False),
        sa.ForeignKeyConstraint(["updated_by_user_id"], ["users.id"], ondelete="SET NULL"),
        sa.PrimaryKeyConstraint("id"),
    )
    op.create_table(
        "affiliate_profiles",
        sa.Column("user_id", postgresql.UUID(as_uuid=True), nullable=False),
        sa.Column("code", sa.String(16), nullable=False),
        sa.Column("status", sa.String(16), nullable=False),
        sa.Column("commission_rate_override", sa.Numeric(5, 2), nullable=True),
        sa.Column("created_at", sa.DateTime(timezone=True), nullable=False),
        sa.Column("updated_at", sa.DateTime(timezone=True), nullable=False),
        sa.ForeignKeyConstraint(["user_id"], ["users.id"], ondelete="CASCADE"),
        sa.PrimaryKeyConstraint("user_id"),
        sa.UniqueConstraint("code"),
    )
    op.create_index("ix_affiliate_profiles_code", "affiliate_profiles", ["code"], unique=True)
    op.create_table(
        "referral_attributions",
        sa.Column("id", postgresql.UUID(as_uuid=True), nullable=False),
        sa.Column("affiliate_user_id", postgresql.UUID(as_uuid=True), nullable=False),
        sa.Column("referred_user_id", postgresql.UUID(as_uuid=True), nullable=False),
        sa.Column("referral_code", sa.String(16), nullable=False),
        sa.Column("source", sa.String(64), nullable=True),
        sa.Column("campaign", sa.String(100), nullable=True),
        sa.Column("created_at", sa.DateTime(timezone=True), nullable=False),
        sa.ForeignKeyConstraint(["affiliate_user_id"], ["users.id"], ondelete="RESTRICT"),
        sa.ForeignKeyConstraint(["referred_user_id"], ["users.id"], ondelete="CASCADE"),
        sa.PrimaryKeyConstraint("id"),
        sa.UniqueConstraint("referred_user_id", name="uq_referral_attribution_referred_user"),
    )
    op.create_index(
        "ix_referral_attribution_affiliate_created",
        "referral_attributions",
        ["affiliate_user_id", "created_at"],
    )
    op.create_table(
        "affiliate_commissions",
        sa.Column("id", postgresql.UUID(as_uuid=True), nullable=False),
        sa.Column("affiliate_user_id", postgresql.UUID(as_uuid=True), nullable=False),
        sa.Column("referred_user_id", postgresql.UUID(as_uuid=True), nullable=False),
        sa.Column("provider_invoice_id", sa.String(100), nullable=False),
        sa.Column("provider_payment_id", sa.String(100), nullable=True),
        sa.Column("provider_subscription_id", sa.String(100), nullable=False),
        sa.Column("currency", sa.String(3), nullable=False),
        sa.Column("commission_base", sa.Numeric(14, 2), nullable=False),
        sa.Column("commission_rate", sa.Numeric(5, 2), nullable=False),
        sa.Column("commission_amount", sa.Numeric(14, 2), nullable=False),
        sa.Column("reversed_amount", sa.Numeric(14, 2), nullable=False),
        sa.Column("status", sa.String(16), nullable=False),
        sa.Column("release_at", sa.DateTime(timezone=True), nullable=False),
        sa.Column("reversed_at", sa.DateTime(timezone=True), nullable=True),
        sa.Column("reversal_reason", sa.String(255), nullable=True),
        sa.Column("paid_at", sa.DateTime(timezone=True), nullable=True),
        sa.Column("created_at", sa.DateTime(timezone=True), nullable=False),
        sa.Column("updated_at", sa.DateTime(timezone=True), nullable=False),
        sa.ForeignKeyConstraint(["affiliate_user_id"], ["users.id"], ondelete="RESTRICT"),
        sa.ForeignKeyConstraint(["referred_user_id"], ["users.id"], ondelete="RESTRICT"),
        sa.PrimaryKeyConstraint("id"),
        sa.UniqueConstraint("provider_invoice_id", name="uq_affiliate_commission_invoice"),
    )
    op.create_index(
        "ix_affiliate_commission_affiliate_status",
        "affiliate_commissions",
        ["affiliate_user_id", "status"],
    )
    op.create_index("ix_affiliate_commission_referred", "affiliate_commissions", ["referred_user_id", "created_at"])
    op.create_index("ix_affiliate_commissions_provider_payment_id", "affiliate_commissions", ["provider_payment_id"])
    op.create_index("ix_affiliate_commissions_provider_subscription_id", "affiliate_commissions", ["provider_subscription_id"])

    op.execute(
        """
        INSERT INTO affiliate_settings (
            id, default_commission_rate, commission_hold_days,
            recurring_months, minimum_payout, created_at, updated_at
        ) VALUES (1, 20.00, 30, 12, 25.00, CURRENT_TIMESTAMP, CURRENT_TIMESTAMP)
        """
    )
    op.execute(
        """
        INSERT INTO affiliate_profiles (user_id, code, status, created_at, updated_at)
        SELECT id, UPPER(SUBSTRING(MD5(id::text), 1, 10)), 'active', CURRENT_TIMESTAMP, CURRENT_TIMESTAMP
        FROM users
        ON CONFLICT DO NOTHING
        """
    )


def downgrade() -> None:
    op.drop_table("affiliate_commissions")
    op.drop_table("referral_attributions")
    op.drop_table("affiliate_profiles")
    op.drop_table("affiliate_settings")
