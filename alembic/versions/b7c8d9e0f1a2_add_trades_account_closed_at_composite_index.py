"""add_trades_account_closed_at_composite_index

Improve journal analytics and day-detail query performance by adding a
composite index aligned with the most common filter/order pattern:
account_id + closed_at + id.

Revision ID: b7c8d9e0f1a2
Revises: a1b2c3d4e5f6
Create Date: 2026-04-07 16:30:00.000000
"""

from typing import Sequence, Union

from alembic import op

# revision identifiers, used by Alembic.
revision: str = "b7c8d9e0f1a2"
down_revision: Union[str, Sequence[str], None] = "a1b2c3d4e5f6"
branch_labels: Union[str, Sequence[str], None] = None
depends_on: Union[str, Sequence[str], None] = None


def upgrade() -> None:
    op.create_index(
        "ix_trades_account_closed_at_id",
        "trades",
        ["account_id", "closed_at", "id"],
        unique=False,
    )


def downgrade() -> None:
    op.drop_index("ix_trades_account_closed_at_id", table_name="trades")
