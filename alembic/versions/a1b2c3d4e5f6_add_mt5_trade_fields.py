"""add_mt5_trade_fields

Adds MT5-enriched columns to the trades table (sl, tp, magic_number, position_id,
trade_source, mfe, mae) and MT5 sync fields to trading_accounts
(sync_provider, copy_magic_numbers).

Revision ID: a1b2c3d4e5f6
Revises: 9f2c1d7e4a11
Create Date: 2026-03-31 18:00:00.000000

"""
from typing import Sequence, Union

import sqlalchemy as sa
from alembic import op
from sqlalchemy.dialects import postgresql

# revision identifiers, used by Alembic.
revision: str = "a1b2c3d4e5f6"
down_revision: Union[str, Sequence[str], None] = "9f2c1d7e4a11"
branch_labels: Union[str, Sequence[str], None] = None
depends_on: Union[str, Sequence[str], None] = None


def upgrade() -> None:
    """Add MT5-specific fields to trades and trading_accounts tables."""
    bind = op.get_bind()
    inspector = sa.inspect(bind)
    trade_columns = {
        column["name"] for column in inspector.get_columns("trades")
    }
    account_columns = {
        column["name"] for column in inspector.get_columns("trading_accounts")
    }
    trade_indexes = {
        index["name"] for index in inspector.get_indexes("trades")
    }

    # Create new enum types safely (idempotent).
    op.execute("""
    DO $$ BEGIN
        CREATE TYPE tradesourceenum AS ENUM ('personal', 'copied');
    EXCEPTION
        WHEN duplicate_object THEN NULL;
    END $$;
    """)

    op.execute("""
    DO $$ BEGIN
        CREATE TYPE syncproviderenum AS ENUM ('metaapi', 'headless_mt5');
    EXCEPTION
        WHEN duplicate_object THEN NULL;
    END $$;
    """)

    # --- trades table ---
    if "sl" not in trade_columns:
        op.add_column(
            "trades",
            sa.Column("sl", sa.Numeric(18, 5), nullable=True, comment="Initial stop-loss price level"),
        )
    if "tp" not in trade_columns:
        op.add_column(
            "trades",
            sa.Column("tp", sa.Numeric(18, 5), nullable=True, comment="Take-profit price level"),
        )
    if "magic_number" not in trade_columns:
        op.add_column(
            "trades",
            sa.Column("magic_number", sa.Integer(), nullable=True, comment="MT5 magic number (0=manual, >0=EA)"),
        )
    if "position_id" not in trade_columns:
        op.add_column(
            "trades",
            sa.Column(
                "position_id",
                sa.String(64),
                nullable=True,
                comment="Broker position ID for grouping partial closes",
            ),
        )
    if "trade_source" not in trade_columns:
        op.add_column(
            "trades",
            sa.Column(
                "trade_source",
                sa.Enum("personal", "copied", name="tradesourceenum", create_type=False),
                nullable=True,
                comment="Trade origin: personal or copied",
            ),
        )
    if "mfe" not in trade_columns:
        op.add_column(
            "trades",
            sa.Column("mfe", sa.Numeric(18, 5), nullable=True, comment="Maximum Favorable Excursion (highest high)"),
        )
    if "mae" not in trade_columns:
        op.add_column(
            "trades",
            sa.Column("mae", sa.Numeric(18, 5), nullable=True, comment="Maximum Adverse Excursion (lowest low)"),
        )

    # Index on position_id for grouping queries.
    if "ix_trades_position_id" not in trade_indexes:
        op.create_index("ix_trades_position_id", "trades", ["position_id"], unique=False)

    # --- trading_accounts table ---
    if "sync_provider" not in account_columns:
        op.add_column(
            "trading_accounts",
            sa.Column(
                "sync_provider",
                sa.Enum("metaapi", "headless_mt5", name="syncproviderenum", create_type=False),
                nullable=False,
                server_default="metaapi",
                comment="Sync provider: metaapi (default) or headless_mt5",
            ),
        )
    if "copy_magic_numbers" not in account_columns:
        op.add_column(
            "trading_accounts",
            sa.Column(
                "copy_magic_numbers",
                postgresql.ARRAY(sa.Integer()),
                nullable=True,
                comment="MT5 magic numbers belonging to copy-trading subscriptions",
            ),
        )


def downgrade() -> None:
    """Remove MT5-specific fields."""

    # --- trading_accounts ---
    op.drop_column("trading_accounts", "copy_magic_numbers")
    op.drop_column("trading_accounts", "sync_provider")

    # --- trades ---
    op.drop_index("ix_trades_position_id", table_name="trades")
    op.drop_column("trades", "mae")
    op.drop_column("trades", "mfe")
    op.drop_column("trades", "trade_source")
    op.drop_column("trades", "position_id")
    op.drop_column("trades", "magic_number")
    op.drop_column("trades", "tp")
    op.drop_column("trades", "sl")

    op.execute("DROP TYPE IF EXISTS syncproviderenum")
    op.execute("DROP TYPE IF EXISTS tradesourceenum")
