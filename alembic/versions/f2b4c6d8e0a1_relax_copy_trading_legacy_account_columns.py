"""relax copy trading legacy account columns

Revision ID: f2b4c6d8e0a1
Revises: f2a3b4c5d6e7
Create Date: 2026-07-07
"""

from alembic import op


revision = "f2b4c6d8e0a1"
down_revision = "f2a3b4c5d6e7"
branch_labels = None
depends_on = None


def upgrade() -> None:
    op.alter_column("copy_account_policies", "legacy_account_id", nullable=True)
    op.alter_column("trade_intents", "legacy_account_id", nullable=True)


def downgrade() -> None:
    op.alter_column("trade_intents", "legacy_account_id", nullable=False)
    op.alter_column("copy_account_policies", "legacy_account_id", nullable=False)
