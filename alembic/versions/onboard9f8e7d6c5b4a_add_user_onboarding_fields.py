"""add_user_onboarding_fields

Revision ID: onboard9f8e7d6c5b4a
Revises: d2e3f4a5b6c7
Create Date: 2026-06-20 00:00:00.000000

Adds the post-signup onboarding columns to `users`. No backfill: existing users
get onboarding_completed=false on purpose, so they also see the one-time
onboarding flow on their next login (we want the data).
"""
from typing import Sequence, Union

from alembic import op
import sqlalchemy as sa


# revision identifiers, used by Alembic.
revision: str = "onboard9f8e7d6c5b4a"
down_revision: Union[str, Sequence[str], None] = "d2e3f4a5b6c7"
branch_labels: Union[str, Sequence[str], None] = None
depends_on: Union[str, Sequence[str], None] = None


def upgrade() -> None:
    """Upgrade schema."""
    bind = op.get_bind()
    inspector = sa.inspect(bind)
    existing = {col["name"] for col in inspector.get_columns("users")}

    if "onboarding_completed" not in existing:
        op.add_column(
            "users",
            sa.Column(
                "onboarding_completed",
                sa.Boolean(),
                nullable=False,
                server_default=sa.false(),
            ),
        )
    if "onboarding_completed_at" not in existing:
        op.add_column(
            "users",
            sa.Column("onboarding_completed_at", sa.DateTime(timezone=True), nullable=True),
        )
    if "trading_experience" not in existing:
        op.add_column("users", sa.Column("trading_experience", sa.String(length=32), nullable=True))
    if "primary_goal" not in existing:
        op.add_column("users", sa.Column("primary_goal", sa.String(length=32), nullable=True))
    if "referral_source" not in existing:
        op.add_column("users", sa.Column("referral_source", sa.String(length=32), nullable=True))


def downgrade() -> None:
    """Downgrade schema."""
    bind = op.get_bind()
    inspector = sa.inspect(bind)
    existing = {col["name"] for col in inspector.get_columns("users")}

    for col in (
        "referral_source",
        "primary_goal",
        "trading_experience",
        "onboarding_completed_at",
        "onboarding_completed",
    ):
        if col in existing:
            op.drop_column("users", col)
