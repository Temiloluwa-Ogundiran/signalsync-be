"""add_provisioning_fields_to_trading_accounts

Revision ID: 9f2c1d7e4a11
Revises: 5cd9108794b9
Create Date: 2026-03-18 17:10:00.000000

"""
from typing import Sequence, Union

from alembic import op
import sqlalchemy as sa
from sqlalchemy.dialects import postgresql


# revision identifiers, used by Alembic.
revision: str = "9f2c1d7e4a11"
down_revision: Union[str, Sequence[str], None] = "5cd9108794b9"
branch_labels: Union[str, Sequence[str], None] = None
depends_on: Union[str, Sequence[str], None] = None


def upgrade() -> None:
    """Upgrade schema."""
    op.execute(
        """
        DO $$ BEGIN
            CREATE TYPE tradingaccountprovisioningstatusenum AS ENUM ('pending', 'provisioned', 'failed');
        EXCEPTION
            WHEN duplicate_object THEN NULL;
        END $$;
        """
    )

    op.add_column(
        "trading_accounts",
        sa.Column("broker_login", sa.String(length=64), nullable=True),
    )
    op.add_column(
        "trading_accounts",
        sa.Column("broker_server", sa.String(length=120), nullable=True),
    )
    op.add_column(
        "trading_accounts",
        sa.Column("encrypted_investor_password", sa.String(), nullable=True),
    )
    op.add_column(
        "trading_accounts",
        sa.Column("encrypted_trader_password", sa.String(), nullable=True),
    )
    op.add_column(
        "trading_accounts",
        sa.Column(
            "provisioning_status",
            postgresql.ENUM(
                "pending",
                "provisioned",
                "failed",
                name="tradingaccountprovisioningstatusenum",
                create_type=False,
            ),
            nullable=False,
            server_default="provisioned",
        ),
    )
    op.add_column(
        "trading_accounts",
        sa.Column("provisioning_error_message", sa.String(), nullable=True),
    )

    # Existing rows came from meta_account_id-only flow; keep them valid.
    op.execute("UPDATE trading_accounts SET broker_login = meta_account_id WHERE broker_login IS NULL")
    op.execute("UPDATE trading_accounts SET broker_server = broker_name WHERE broker_server IS NULL")
    op.execute(
        "UPDATE trading_accounts SET encrypted_investor_password = '' WHERE encrypted_investor_password IS NULL"
    )

    op.alter_column("trading_accounts", "broker_login", nullable=False)
    op.alter_column("trading_accounts", "broker_server", nullable=False)
    op.alter_column("trading_accounts", "encrypted_investor_password", nullable=False)
    op.alter_column("trading_accounts", "provisioning_status", server_default=None)


def downgrade() -> None:
    """Downgrade schema."""
    op.drop_column("trading_accounts", "provisioning_error_message")
    op.drop_column("trading_accounts", "provisioning_status")
    op.drop_column("trading_accounts", "encrypted_trader_password")
    op.drop_column("trading_accounts", "encrypted_investor_password")
    op.drop_column("trading_accounts", "broker_server")
    op.drop_column("trading_accounts", "broker_login")

    op.execute("DROP TYPE IF EXISTS tradingaccountprovisioningstatusenum")
