"""add_connection_state_readiness_fields

Revision ID: c2f4a8d6e9b1
Revises: b7c8d9e0f1a2
Create Date: 2026-04-09 12:30:00.000000
"""

from typing import Sequence, Union

import sqlalchemy as sa
from alembic import op
from sqlalchemy.dialects import postgresql

# revision identifiers, used by Alembic.
revision: str = "c2f4a8d6e9b1"
down_revision: Union[str, Sequence[str], None] = "b7c8d9e0f1a2"
branch_labels: Union[str, Sequence[str], None] = None
depends_on: Union[str, Sequence[str], None] = None


def upgrade() -> None:
    bind = op.get_bind()
    inspector = sa.inspect(bind)
    account_columns = {
        column["name"] for column in inspector.get_columns("trading_accounts")
    }

    op.execute(
        """
        DO $$ BEGIN
            CREATE TYPE tradingaccountconnectionstateenum AS ENUM (
                'pending_verification',
                'verification_failed',
                'bootstrapping',
                'ready',
                'bootstrap_failed'
            );
        EXCEPTION
            WHEN duplicate_object THEN NULL;
        END $$;
        """
    )

    if "connection_state" not in account_columns:
        op.add_column(
            "trading_accounts",
            sa.Column(
                "connection_state",
                postgresql.ENUM(
                    "pending_verification",
                    "verification_failed",
                    "bootstrapping",
                    "ready",
                    "bootstrap_failed",
                    name="tradingaccountconnectionstateenum",
                    create_type=False,
                ),
                nullable=False,
                server_default="ready",
            ),
        )
    if "is_data_ready_for_stats" not in account_columns:
        op.add_column(
            "trading_accounts",
            sa.Column(
                "is_data_ready_for_stats",
                sa.Boolean(),
                nullable=False,
                server_default=sa.text("true"),
            ),
        )
    if "last_bootstrap_synced_at" not in account_columns:
        op.add_column(
            "trading_accounts",
            sa.Column("last_bootstrap_synced_at", sa.DateTime(timezone=True), nullable=True),
        )
    if "bootstrap_error_message" not in account_columns:
        op.add_column(
            "trading_accounts",
            sa.Column("bootstrap_error_message", sa.String(), nullable=True),
        )

    op.alter_column("trading_accounts", "connection_state", server_default=None)
    op.alter_column("trading_accounts", "is_data_ready_for_stats", server_default=None)


def downgrade() -> None:
    op.drop_column("trading_accounts", "bootstrap_error_message")
    op.drop_column("trading_accounts", "last_bootstrap_synced_at")
    op.drop_column("trading_accounts", "is_data_ready_for_stats")
    op.drop_column("trading_accounts", "connection_state")
    op.execute("DROP TYPE IF EXISTS tradingaccountconnectionstateenum")
