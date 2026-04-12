"""add_journal_reviewed_at_columns

Revision ID: d1e2f3a4b5c6
Revises: c2f4a8d6e9b1
Create Date: 2026-04-12 12:00:00.000000

"""

from typing import Sequence, Union

import sqlalchemy as sa
from alembic import op

revision: str = "d1e2f3a4b5c6"
down_revision: Union[str, Sequence[str], None] = "c2f4a8d6e9b1"
branch_labels: Union[str, Sequence[str], None] = None
depends_on: Union[str, Sequence[str], None] = None


def upgrade() -> None:
    op.add_column(
        "daily_journals",
        sa.Column("reviewed_at", sa.DateTime(timezone=True), nullable=True),
    )
    op.add_column(
        "trade_journals",
        sa.Column("reviewed_at", sa.DateTime(timezone=True), nullable=True),
    )


def downgrade() -> None:
    op.drop_column("trade_journals", "reviewed_at")
    op.drop_column("daily_journals", "reviewed_at")
