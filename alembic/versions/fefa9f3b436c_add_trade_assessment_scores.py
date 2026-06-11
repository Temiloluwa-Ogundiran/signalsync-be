"""add_trade_assessment_scores

Revision ID: fefa9f3b436c
Revises: 4da4bd35145f
Create Date: 2026-05-27 19:41:15.310796

"""
from typing import Sequence, Union

from alembic import op
import sqlalchemy as sa


# revision identifiers, used by Alembic.
revision: str = 'fefa9f3b436c'
down_revision: Union[str, Sequence[str], None] = '4da4bd35145f'
branch_labels: Union[str, Sequence[str], None] = None
depends_on: Union[str, Sequence[str], None] = None


def upgrade() -> None:
    """Upgrade schema."""
    bind = op.get_bind()
    inspector = sa.inspect(bind)
    existing_columns = {col["name"] for col in inspector.get_columns("trade_journals")}

    if "execution_quality" not in existing_columns:
        op.add_column('trade_journals', sa.Column('execution_quality', sa.Integer(), nullable=True))
    if "setup_quality" not in existing_columns:
        op.add_column('trade_journals', sa.Column('setup_quality', sa.Integer(), nullable=True))
    if "discipline_score" not in existing_columns:
        op.add_column('trade_journals', sa.Column('discipline_score', sa.Integer(), nullable=True))


def downgrade() -> None:
    """Downgrade schema."""
    bind = op.get_bind()
    inspector = sa.inspect(bind)
    existing_columns = {col["name"] for col in inspector.get_columns("trade_journals")}

    if "discipline_score" in existing_columns:
        op.drop_column('trade_journals', 'discipline_score')
    if "setup_quality" in existing_columns:
        op.drop_column('trade_journals', 'setup_quality')
    if "execution_quality" in existing_columns:
        op.drop_column('trade_journals', 'execution_quality')
