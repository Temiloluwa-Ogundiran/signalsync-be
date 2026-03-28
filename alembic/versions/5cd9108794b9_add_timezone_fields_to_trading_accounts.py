"""add_timezone_fields_to_trading_accounts

Revision ID: 5cd9108794b9
Revises: 4db0be4616dc
Create Date: 2026-03-17 18:56:02.813479

"""
from typing import Sequence, Union

from alembic import op
import sqlalchemy as sa


# revision identifiers, used by Alembic.
revision: str = '5cd9108794b9'
down_revision: Union[str, Sequence[str], None] = '4db0be4616dc'
branch_labels: Union[str, Sequence[str], None] = None
depends_on: Union[str, Sequence[str], None] = None


def upgrade() -> None:
    """Upgrade schema."""
    op.add_column(
        "trading_accounts",
        sa.Column("timezone", sa.String(length=50), nullable=False, server_default="UTC"),
    )
    op.add_column(
        "trading_accounts",
        sa.Column("broker_utc_offset", sa.Integer(), nullable=False, server_default="0"),
    )

    op.execute("UPDATE trading_accounts SET timezone = 'UTC' WHERE timezone IS NULL")
    op.execute("UPDATE trading_accounts SET broker_utc_offset = 0 WHERE broker_utc_offset IS NULL")

    op.alter_column("trading_accounts", "timezone", server_default=None)
    op.alter_column("trading_accounts", "broker_utc_offset", server_default=None)


def downgrade() -> None:
    """Downgrade schema."""
    op.drop_column("trading_accounts", "broker_utc_offset")
    op.drop_column("trading_accounts", "timezone")
