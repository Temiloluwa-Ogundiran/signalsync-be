"""migrate_metaapi_accounts_to_headless_mt5

Revision ID: c6d7e8f9a0b1
Revises: 7f1c2a9d4b6e
Create Date: 2026-06-06 23:05:00.000000
"""

from typing import Sequence, Union

from alembic import op


# revision identifiers, used by Alembic.
revision: str = "c6d7e8f9a0b1"
down_revision: Union[str, Sequence[str], None] = "7f1c2a9d4b6e"
branch_labels: Union[str, Sequence[str], None] = None
depends_on: Union[str, Sequence[str], None] = None


def upgrade() -> None:
    op.execute(
        """
        UPDATE trading_accounts
        SET sync_provider = 'headless_mt5'
        WHERE sync_provider = 'metaapi'
        """
    )
    op.execute(
        """
        ALTER TABLE trading_accounts
        ALTER COLUMN sync_provider
        SET DEFAULT 'headless_mt5'
        """
    )


def downgrade() -> None:
    op.execute(
        """
        ALTER TABLE trading_accounts
        ALTER COLUMN sync_provider
        SET DEFAULT 'metaapi'
        """
    )
