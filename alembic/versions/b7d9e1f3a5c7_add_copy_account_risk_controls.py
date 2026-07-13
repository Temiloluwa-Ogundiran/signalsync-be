"""add copy account risk controls

Revision ID: b7d9e1f3a5c7
Revises: f2c5d7e9a1b3
Create Date: 2026-07-13
"""

from alembic import op
import sqlalchemy as sa
from sqlalchemy.dialects import postgresql


revision = "b7d9e1f3a5c7"
down_revision = "f2c5d7e9a1b3"
branch_labels = None
depends_on = None


def upgrade() -> None:
    op.add_column(
        "copy_account_policies",
        sa.Column("max_lot_per_trade", sa.Numeric(12, 4), nullable=False, server_default="100"),
    )
    op.add_column(
        "copy_account_policies",
        sa.Column("max_open_positions", sa.Integer(), nullable=False, server_default="100"),
    )
    op.add_column(
        "copy_account_policies",
        sa.Column("daily_loss_limit", sa.Numeric(20, 2), nullable=True),
    )
    op.add_column(
        "copy_account_policies",
        sa.Column("max_drawdown_percent", sa.Numeric(6, 2), nullable=True),
    )
    op.add_column(
        "copy_account_policies",
        sa.Column(
            "allowed_symbols",
            postgresql.JSONB(),
            nullable=False,
            server_default=sa.text("'[]'::jsonb"),
        ),
    )
    op.add_column(
        "copy_account_policies",
        sa.Column(
            "blocked_symbols",
            postgresql.JSONB(),
            nullable=False,
            server_default=sa.text("'[]'::jsonb"),
        ),
    )
    op.add_column(
        "copy_account_policies",
        sa.Column(
            "market_signal_max_age_seconds",
            sa.Integer(),
            nullable=False,
            server_default="30",
        ),
    )
    op.add_column(
        "copy_account_policies",
        sa.Column("daily_equity_anchor", sa.Numeric(20, 2), nullable=True),
    )
    op.add_column(
        "copy_account_policies",
        sa.Column("daily_equity_anchor_date", sa.Date(), nullable=True),
    )
    op.add_column(
        "copy_account_policies",
        sa.Column("peak_equity", sa.Numeric(20, 2), nullable=True),
    )
    op.create_check_constraint(
        "ck_copy_account_policy_max_lot_per_trade_positive",
        "copy_account_policies",
        "max_lot_per_trade > 0",
    )
    op.create_check_constraint(
        "ck_copy_account_policy_max_open_positions_positive",
        "copy_account_policies",
        "max_open_positions > 0",
    )
    op.create_check_constraint(
        "ck_copy_account_policy_daily_loss_positive",
        "copy_account_policies",
        "daily_loss_limit IS NULL OR daily_loss_limit > 0",
    )
    op.create_check_constraint(
        "ck_copy_account_policy_drawdown_range",
        "copy_account_policies",
        "max_drawdown_percent IS NULL OR "
        "(max_drawdown_percent > 0 AND max_drawdown_percent <= 100)",
    )
    op.create_check_constraint(
        "ck_copy_account_policy_signal_age_range",
        "copy_account_policies",
        "market_signal_max_age_seconds >= 1 AND market_signal_max_age_seconds <= 3600",
    )


def downgrade() -> None:
    for constraint in (
        "ck_copy_account_policy_signal_age_range",
        "ck_copy_account_policy_drawdown_range",
        "ck_copy_account_policy_daily_loss_positive",
        "ck_copy_account_policy_max_open_positions_positive",
        "ck_copy_account_policy_max_lot_per_trade_positive",
    ):
        op.drop_constraint(constraint, "copy_account_policies", type_="check")
    for column in (
        "peak_equity",
        "daily_equity_anchor_date",
        "daily_equity_anchor",
        "market_signal_max_age_seconds",
        "blocked_symbols",
        "allowed_symbols",
        "max_drawdown_percent",
        "daily_loss_limit",
        "max_open_positions",
        "max_lot_per_trade",
    ):
        op.drop_column("copy_account_policies", column)
