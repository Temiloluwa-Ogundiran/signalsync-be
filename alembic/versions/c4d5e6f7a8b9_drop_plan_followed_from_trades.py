"""drop plan_followed from trades

Removes the unused plan_followed column. It was only ever written by the demo
generator and never exposed via any API or surfaced in the UI.

Revision ID: c4d5e6f7a8b9
Revises: b2c3d4e5f6a8
Create Date: 2026-06-17 15:00:00.000000

"""
from typing import Sequence, Union

from alembic import op
import sqlalchemy as sa

# revision identifiers, used by Alembic.
revision: str = 'c4d5e6f7a8b9'
down_revision: Union[str, Sequence[str], None] = 'b2c3d4e5f6a8'
branch_labels: Union[str, Sequence[str], None] = None
depends_on: Union[str, Sequence[str], None] = None


def upgrade() -> None:
    """Upgrade schema."""
    bind = op.get_bind()
    inspector = sa.inspect(bind)
    cols = {c["name"] for c in inspector.get_columns("trades")}
    if "plan_followed" in cols:
        op.drop_column("trades", "plan_followed")


def downgrade() -> None:
    """Downgrade schema."""
    op.add_column("trades", sa.Column("plan_followed", sa.Boolean(), nullable=True))
