"""add_active_user_sync_fields

Revision ID: 7f1c2a9d4b6e
Revises: 129bbf6654f9
Create Date: 2026-06-05 22:50:00.000000
"""

from typing import Sequence, Union

import sqlalchemy as sa
from alembic import op

# revision identifiers, used by Alembic.
revision: str = "7f1c2a9d4b6e"
down_revision: Union[str, Sequence[str], None] = "129bbf6654f9"
branch_labels: Union[str, Sequence[str], None] = None
depends_on: Union[str, Sequence[str], None] = None


def upgrade() -> None:
    op.add_column(
        "users",
        sa.Column("last_active_at", sa.DateTime(timezone=True), nullable=True),
    )
    op.add_column(
        "trading_accounts",
        sa.Column("last_sync_attempted_at", sa.DateTime(timezone=True), nullable=True),
    )
    op.add_column(
        "trading_accounts",
        sa.Column("next_sync_not_before", sa.DateTime(timezone=True), nullable=True),
    )
    op.add_column(
        "trading_accounts",
        sa.Column("last_sync_outcome", sa.String(length=64), nullable=True),
    )
    op.add_column(
        "trading_accounts",
        sa.Column(
            "consecutive_sync_failures",
            sa.Integer(),
            nullable=False,
            server_default="0",
        ),
    )
    op.alter_column(
        "trading_accounts",
        "consecutive_sync_failures",
        server_default=None,
    )


def downgrade() -> None:
    op.drop_column("trading_accounts", "consecutive_sync_failures")
    op.drop_column("trading_accounts", "last_sync_outcome")
    op.drop_column("trading_accounts", "next_sync_not_before")
    op.drop_column("trading_accounts", "last_sync_attempted_at")
    op.drop_column("users", "last_active_at")
