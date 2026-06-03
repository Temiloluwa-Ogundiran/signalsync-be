"""add_csv_import_provider_and_platforms

Revision ID: 129bbf6654f9
Revises: 256e11dfea33
Create Date: 2026-06-02 08:11:06.425168

"""
from typing import Sequence, Union

from alembic import op
import sqlalchemy as sa


# revision identifiers, used by Alembic.
revision: str = '129bbf6654f9'
down_revision: Union[str, Sequence[str], None] = '256e11dfea33'
branch_labels: Union[str, Sequence[str], None] = None
depends_on: Union[str, Sequence[str], None] = None


def upgrade() -> None:
    """Upgrade schema."""
    op.execute("ALTER TYPE syncproviderenum ADD VALUE IF NOT EXISTS 'csv_import'")
    op.execute("ALTER TYPE tradingplatformenum ADD VALUE IF NOT EXISTS 'MatchTrader'")
    op.execute("ALTER TYPE tradingplatformenum ADD VALUE IF NOT EXISTS 'cTrader'")


def downgrade() -> None:
    """Downgrade schema."""
    pass
