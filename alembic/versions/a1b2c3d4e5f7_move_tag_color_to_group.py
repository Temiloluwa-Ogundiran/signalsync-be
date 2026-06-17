"""move tag color from tags to tag_groups

Color now lives on the group; every tag in the group inherits it for display.

Revision ID: a1b2c3d4e5f7
Revises: f7a8b9c0d1e2
Create Date: 2026-06-17 13:00:00.000000

"""
from typing import Sequence, Union

from alembic import op
import sqlalchemy as sa

# revision identifiers, used by Alembic.
revision: str = 'a1b2c3d4e5f7'
down_revision: Union[str, Sequence[str], None] = 'f7a8b9c0d1e2'
branch_labels: Union[str, Sequence[str], None] = None
depends_on: Union[str, Sequence[str], None] = None


def upgrade() -> None:
    """Upgrade schema."""
    bind = op.get_bind()
    inspector = sa.inspect(bind)
    group_cols = {c["name"] for c in inspector.get_columns("tag_groups")} if "tag_groups" in inspector.get_table_names() else set()
    tag_cols = {c["name"] for c in inspector.get_columns("tags")} if "tags" in inspector.get_table_names() else set()

    if "color" not in group_cols:
        op.add_column("tag_groups", sa.Column("color", sa.String(length=7), nullable=True))
    if "color" in tag_cols:
        op.drop_column("tags", "color")


def downgrade() -> None:
    """Downgrade schema."""
    op.add_column("tags", sa.Column("color", sa.String(length=7), nullable=True))
    op.drop_column("tag_groups", "color")
