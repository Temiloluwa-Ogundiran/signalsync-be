"""add_provisioning_fields_to_trading_accounts

Revision ID: 9f2c1d7e4a11
Revises: d9b1e7f4c2a8
Create Date: 2026-03-18 17:10:00.000000

"""
from typing import Sequence, Union

from alembic import op
import sqlalchemy as sa
from sqlalchemy.dialects import postgresql


# revision identifiers, used by Alembic.
revision: str = "9f2c1d7e4a11"
down_revision: Union[str, Sequence[str], None] = "d9b1e7f4c2a8"
branch_labels: Union[str, Sequence[str], None] = None
depends_on: Union[str, Sequence[str], None] = None


def upgrade() -> None:
    """Upgrade schema."""
    bind = op.get_bind()
    inspector = sa.inspect(bind)
    account_columns = {
        column["name"] for column in inspector.get_columns("trading_accounts")
    }

    op.execute(
        """
        DO $$ BEGIN
            CREATE TYPE tradingaccountprovisioningstatusenum AS ENUM ('pending', 'provisioned', 'failed');
        EXCEPTION
            WHEN duplicate_object THEN NULL;
        END $$;
        """
    )

    if "broker_login" not in account_columns:
        op.add_column(
            "trading_accounts",
            sa.Column("broker_login", sa.String(length=64), nullable=True),
        )
    if "broker_server" not in account_columns:
        op.add_column(
            "trading_accounts",
            sa.Column("broker_server", sa.String(length=120), nullable=True),
        )
    if "encrypted_investor_password" not in account_columns:
        op.add_column(
            "trading_accounts",
            sa.Column("encrypted_investor_password", sa.String(), nullable=True),
        )
    if "encrypted_trader_password" not in account_columns:
        op.add_column(
            "trading_accounts",
            sa.Column("encrypted_trader_password", sa.String(), nullable=True),
        )
    if "provisioning_status" not in account_columns:
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
    if "provisioning_error_message" not in account_columns:
        op.add_column(
            "trading_accounts",
            sa.Column("provisioning_error_message", sa.String(), nullable=True),
        )

    # Existing rows came from meta_account_id-only flow; keep them valid.
    op.execute(
        "UPDATE trading_accounts SET broker_login = meta_account_id WHERE broker_login IS NULL"
    )
    op.execute(
        "UPDATE trading_accounts SET broker_server = broker_name WHERE broker_server IS NULL"
    )
    op.execute(
        "UPDATE trading_accounts SET encrypted_investor_password = '' "
        "WHERE encrypted_investor_password IS NULL"
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
