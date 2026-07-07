"""relax copy route legacy target account

Revision ID: f2c5d7e9a1b3
Revises: f2b4c6d8e0a1
Create Date: 2026-07-07
"""

from alembic import op


revision = "f2c5d7e9a1b3"
down_revision = "f2b4c6d8e0a1"
branch_labels = None
depends_on = None


def upgrade() -> None:
    op.alter_column("copy_routes", "legacy_target_account_id", nullable=True)


def downgrade() -> None:
    op.alter_column("copy_routes", "legacy_target_account_id", nullable=False)
