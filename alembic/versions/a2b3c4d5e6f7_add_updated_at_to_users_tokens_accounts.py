"""add updated_at to users, tokens, trading_accounts

Revision ID: a2b3c4d5e6f7
Revises: f3a1b2c4d5e6
Create Date: 2026-06-12 15:15:00.000000

"""
from typing import Sequence, Union

import sqlalchemy as sa
from alembic import op

# revision identifiers, used by Alembic.
revision: str = 'a2b3c4d5e6f7'
down_revision: Union[str, Sequence[str], None] = 'f3a1b2c4d5e6'
branch_labels: Union[str, Sequence[str], None] = None
depends_on: Union[str, Sequence[str], None] = None


def upgrade() -> None:
    # Add updated_at to each table, backfill from created_at so NOT NULL is safe.
    for table in ("users", "tokens", "trading_accounts"):
        op.add_column(
            table,
            sa.Column(
                "updated_at",
                sa.DateTime(timezone=True),
                nullable=True,
            ),
        )
        op.execute(f"UPDATE {table} SET updated_at = created_at WHERE updated_at IS NULL")
        op.alter_column(table, "updated_at", nullable=False)


def downgrade() -> None:
    for table in ("users", "tokens", "trading_accounts"):
        op.drop_column(table, "updated_at")
