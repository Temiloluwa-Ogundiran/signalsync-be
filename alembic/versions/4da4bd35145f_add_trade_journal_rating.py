"""add_trade_journal_rating

Revision ID: 4da4bd35145f
Revises: 2d629be79bc7
Create Date: 2026-05-26 01:25:14.904730

"""
from typing import Sequence, Union

from alembic import op
import sqlalchemy as sa


# revision identifiers, used by Alembic.
revision: str = '4da4bd35145f'
down_revision: Union[str, Sequence[str], None] = '2d629be79bc7'
branch_labels: Union[str, Sequence[str], None] = None
depends_on: Union[str, Sequence[str], None] = None


def upgrade() -> None:
    """Upgrade schema."""
    bind = op.get_bind()
    inspector = sa.inspect(bind)
    existing_columns = {col["name"] for col in inspector.get_columns("trade_journals")}

    if "rating" not in existing_columns:
        op.add_column('trade_journals', sa.Column('rating', sa.Integer(), nullable=True))


def downgrade() -> None:
    """Downgrade schema."""
    bind = op.get_bind()
    inspector = sa.inspect(bind)
    existing_columns = {col["name"] for col in inspector.get_columns("trade_journals")}

    if "rating" in existing_columns:
        op.drop_column('trade_journals', 'rating')
