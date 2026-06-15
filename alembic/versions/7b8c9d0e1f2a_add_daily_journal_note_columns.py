"""add_daily_journal_note_columns

Adds the single rich-text "daily note" per day to daily_journals:
- note_html: HTML content from the day-details note editor
- note_updated_at: last time the note was saved

Revision ID: 7b8c9d0e1f2a
Revises: 6a7b8c9d0e1f
Create Date: 2026-06-15 00:00:00.000000

"""

from typing import Sequence, Union

import sqlalchemy as sa
from alembic import op

revision: str = "7b8c9d0e1f2a"
down_revision: Union[str, Sequence[str], None] = "6a7b8c9d0e1f"
branch_labels: Union[str, Sequence[str], None] = None
depends_on: Union[str, Sequence[str], None] = None


def upgrade() -> None:
    bind = op.get_bind()
    inspector = sa.inspect(bind)
    columns = {column["name"] for column in inspector.get_columns("daily_journals")}

    if "note_html" not in columns:
        op.add_column(
            "daily_journals",
            sa.Column("note_html", sa.Text(), nullable=True),
        )
    if "note_updated_at" not in columns:
        op.add_column(
            "daily_journals",
            sa.Column("note_updated_at", sa.DateTime(timezone=True), nullable=True),
        )


def downgrade() -> None:
    op.drop_column("daily_journals", "note_updated_at")
    op.drop_column("daily_journals", "note_html")
