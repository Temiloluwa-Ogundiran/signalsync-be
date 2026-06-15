"""drop_daily_stats_table

Removes the precomputed `daily_stats` aggregate table. Per-day trading
statistics (trade_count / win_count / loss_count / total_pnl, etc.) are now
computed on the fly from the `trades` table, so the denormalized cache and its
rebuild machinery are no longer needed.

Revision ID: a1c2e3d4f5b6
Revises: 7b8c9d0e1f2a
Create Date: 2026-06-15 00:00:00.000000

"""

from typing import Sequence, Union

import sqlalchemy as sa
from alembic import op

revision: str = "a1c2e3d4f5b6"
down_revision: Union[str, Sequence[str], None] = "7b8c9d0e1f2a"
branch_labels: Union[str, Sequence[str], None] = None
depends_on: Union[str, Sequence[str], None] = None


def upgrade() -> None:
    bind = op.get_bind()
    inspector = sa.inspect(bind)
    if "daily_stats" in inspector.get_table_names():
        op.drop_index(op.f("ix_daily_stats_trading_date"), table_name="daily_stats")
        op.drop_index(op.f("ix_daily_stats_account_id"), table_name="daily_stats")
        op.drop_table("daily_stats")


def downgrade() -> None:
    op.create_table(
        "daily_stats",
        sa.Column("id", sa.UUID(), nullable=False),
        sa.Column("account_id", sa.UUID(), nullable=False),
        sa.Column("trading_date", sa.Date(), nullable=False),
        sa.Column("trade_count", sa.Integer(), nullable=False),
        sa.Column("win_count", sa.Integer(), nullable=False),
        sa.Column("loss_count", sa.Integer(), nullable=False),
        sa.Column("total_pnl", sa.Numeric(precision=14, scale=2), nullable=False),
        sa.Column("total_commission", sa.Numeric(precision=14, scale=2), nullable=False),
        sa.Column("gross_win", sa.Numeric(precision=14, scale=2), nullable=False),
        sa.Column("gross_loss", sa.Numeric(precision=14, scale=2), nullable=False),
        sa.Column("best_trade_id", sa.UUID(), nullable=True),
        sa.Column("worst_trade_id", sa.UUID(), nullable=True),
        sa.Column("created_at", sa.DateTime(timezone=True), nullable=False),
        sa.ForeignKeyConstraint(["account_id"], ["trading_accounts.id"], ondelete="CASCADE"),
        sa.ForeignKeyConstraint(["best_trade_id"], ["trades.id"], ondelete="SET NULL"),
        sa.ForeignKeyConstraint(["worst_trade_id"], ["trades.id"], ondelete="SET NULL"),
        sa.PrimaryKeyConstraint("id"),
        sa.UniqueConstraint("account_id", "trading_date"),
    )
    op.create_index(op.f("ix_daily_stats_account_id"), "daily_stats", ["account_id"], unique=False)
    op.create_index(op.f("ix_daily_stats_trading_date"), "daily_stats", ["trading_date"], unique=False)
