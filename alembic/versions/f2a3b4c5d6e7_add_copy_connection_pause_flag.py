"""add copy connection pause flag

Revision ID: f2a3b4c5d6e7
Revises: e1f2a3b4c5d6
Create Date: 2026-06-30
"""

import sqlalchemy as sa
from alembic import op


revision = "f2a3b4c5d6e7"
down_revision = "e1f2a3b4c5d6"
branch_labels = None
depends_on = None


def _has_column(table: str, column: str) -> bool:
    inspector = sa.inspect(op.get_bind())
    return any(existing["name"] == column for existing in inspector.get_columns(table))


def upgrade() -> None:
    if not _has_column("copy_trading_connections", "is_paused"):
        op.add_column(
            "copy_trading_connections",
            sa.Column("is_paused", sa.Boolean(), server_default=sa.false(), nullable=False),
        )


def downgrade() -> None:
    if _has_column("copy_trading_connections", "is_paused"):
        op.drop_column("copy_trading_connections", "is_paused")
