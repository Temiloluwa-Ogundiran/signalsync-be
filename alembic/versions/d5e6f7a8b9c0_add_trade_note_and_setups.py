"""add trade note fields + setups table

Adds note_html/note_updated_at to trade_journals (per-trade plain-text note) and
a flat per-user `setups` table for the playbook-setup picker.

Revision ID: d5e6f7a8b9c0
Revises: c4d5e6f7a8b9
Create Date: 2026-06-17 16:00:00.000000

"""
from typing import Sequence, Union

from alembic import op
import sqlalchemy as sa

# revision identifiers, used by Alembic.
revision: str = 'd5e6f7a8b9c0'
down_revision: Union[str, Sequence[str], None] = 'c4d5e6f7a8b9'
branch_labels: Union[str, Sequence[str], None] = None
depends_on: Union[str, Sequence[str], None] = None


def upgrade() -> None:
    """Upgrade schema."""
    bind = op.get_bind()
    inspector = sa.inspect(bind)
    existing_tables = set(inspector.get_table_names())
    tj_cols = (
        {c["name"] for c in inspector.get_columns("trade_journals")}
        if "trade_journals" in existing_tables
        else set()
    )

    if "note_html" not in tj_cols:
        op.add_column("trade_journals", sa.Column("note_html", sa.Text(), nullable=True))
    if "note_updated_at" not in tj_cols:
        op.add_column(
            "trade_journals",
            sa.Column("note_updated_at", sa.DateTime(timezone=True), nullable=True),
        )

    if "setups" not in existing_tables:
        op.create_table(
            "setups",
            sa.Column("id", sa.UUID(), nullable=False),
            sa.Column("user_id", sa.UUID(), nullable=False),
            sa.Column("name", sa.String(length=64), nullable=False),
            sa.Column("position", sa.Integer(), nullable=False),
            sa.Column("created_at", sa.DateTime(timezone=True), nullable=False),
            sa.Column("updated_at", sa.DateTime(timezone=True), nullable=False),
            sa.ForeignKeyConstraint(["user_id"], ["users.id"], ondelete="CASCADE"),
            sa.PrimaryKeyConstraint("id"),
            sa.UniqueConstraint("user_id", "name", name="uq_setups_user_name"),
        )
        op.create_index(op.f("ix_setups_user_id"), "setups", ["user_id"], unique=False)


def downgrade() -> None:
    """Downgrade schema."""
    op.drop_index(op.f("ix_setups_user_id"), table_name="setups")
    op.drop_table("setups")
    op.drop_column("trade_journals", "note_updated_at")
    op.drop_column("trade_journals", "note_html")
