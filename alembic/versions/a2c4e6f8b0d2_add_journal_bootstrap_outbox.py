"""add journal bootstrap outbox

Revision ID: a2c4e6f8b0d2
Revises: f1b2c3d4e5f6
Create Date: 2026-07-19 13:00:00
"""

from typing import Sequence, Union

import sqlalchemy as sa
from alembic import op
from sqlalchemy.dialects import postgresql


revision: str = "a2c4e6f8b0d2"
down_revision: Union[str, Sequence[str], None] = "f1b2c3d4e5f6"
branch_labels: Union[str, Sequence[str], None] = None
depends_on: Union[str, Sequence[str], None] = None


def upgrade() -> None:
    op.create_table(
        "journal_bootstrap_dispatches",
        sa.Column("id", postgresql.UUID(as_uuid=True), nullable=False),
        sa.Column("account_id", postgresql.UUID(as_uuid=True), nullable=False),
        sa.Column("attempt_count", sa.Integer(), nullable=False, server_default="0"),
        sa.Column("available_at", sa.DateTime(timezone=True), nullable=False),
        sa.Column("dispatched_at", sa.DateTime(timezone=True), nullable=True),
        sa.Column("last_error", sa.String(length=500), nullable=True),
        sa.Column("created_at", sa.DateTime(timezone=True), nullable=False),
        sa.Column("updated_at", sa.DateTime(timezone=True), nullable=False),
        sa.ForeignKeyConstraint(
            ["account_id"], ["trading_accounts.id"], ondelete="CASCADE"
        ),
        sa.PrimaryKeyConstraint("id"),
        sa.UniqueConstraint("account_id"),
    )
    op.create_index(
        "ix_journal_bootstrap_dispatches_account_id",
        "journal_bootstrap_dispatches",
        ["account_id"],
        unique=True,
    )
    op.create_index(
        "ix_journal_bootstrap_dispatch_pending",
        "journal_bootstrap_dispatches",
        ["available_at"],
        unique=False,
        postgresql_where=sa.text("dispatched_at IS NULL"),
    )


def downgrade() -> None:
    op.drop_index(
        "ix_journal_bootstrap_dispatch_pending",
        table_name="journal_bootstrap_dispatches",
    )
    op.drop_index(
        "ix_journal_bootstrap_dispatches_account_id",
        table_name="journal_bootstrap_dispatches",
    )
    op.drop_table("journal_bootstrap_dispatches")
