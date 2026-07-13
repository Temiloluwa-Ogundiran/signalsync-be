"""clamp existing copy trade limits

Revision ID: c8e0f2a4b6d8
Revises: b7d9e1f3a5c7
Create Date: 2026-07-13
"""

from alembic import op


revision = "c8e0f2a4b6d8"
down_revision = "b7d9e1f3a5c7"
branch_labels = None
depends_on = None


def upgrade() -> None:
    op.execute(
        """
        UPDATE copy_account_policies
        SET max_lot_per_trade = LEAST(max_lot_per_trade, max_lot)
        WHERE max_lot_per_trade > max_lot
        """
    )


def downgrade() -> None:
    # The previous per-trade values cannot be recovered safely.
    pass
