"""add_partna_guard_tables

Creates the Partna Guard schema: guard_accounts (one per Guard-enabled trading
account, holding the user-entered firm rules), guard_states (latest snapshot),
guard_daily_results (closed-day P&L for consistency + min-days), guard_ticks (short
rolling equity window for the chart), guard_alerts (sent-email log + de-dupe).

Revision ID: a1b2c3d4e5f6
Revises: f9a0b1c2d3e4
Create Date: 2026-06-21 12:00:00.000000
"""
from typing import Sequence, Union

import sqlalchemy as sa
from alembic import op
from sqlalchemy.dialects import postgresql

revision: str = "a1b2c3d4e5f6"
down_revision: Union[str, Sequence[str], None] = "f9a0b1c2d3e4"
branch_labels: Union[str, Sequence[str], None] = None
depends_on: Union[str, Sequence[str], None] = None


def upgrade() -> None:
    op.create_table(
        "guard_accounts",
        sa.Column("id", postgresql.UUID(as_uuid=True), nullable=False),
        sa.Column("trading_account_id", postgresql.UUID(as_uuid=True), nullable=False),
        sa.Column("user_id", postgresql.UUID(as_uuid=True), nullable=False),
        sa.Column("enabled", sa.Boolean(), nullable=False, server_default=sa.true()),
        sa.Column("size", sa.Numeric(18, 2), nullable=False),
        sa.Column("rule_spec_json", postgresql.JSONB(astext_type=sa.Text()), nullable=False),
        sa.Column("personal_json", postgresql.JSONB(astext_type=sa.Text()), nullable=True),
        sa.Column("contract_text", sa.String(), nullable=True),
        sa.Column("last_polled_at", sa.DateTime(timezone=True), nullable=True),
        sa.Column("connection_health", sa.String(16), nullable=False, server_default="ok"),
        sa.Column("poll_error_message", sa.String(), nullable=True),
        sa.Column("consecutive_poll_failures", sa.Integer(), nullable=False, server_default="0"),
        sa.Column("last_alert_tier", sa.String(16), nullable=True),
        sa.Column("created_at", sa.DateTime(timezone=True), nullable=False),
        sa.Column("updated_at", sa.DateTime(timezone=True), nullable=False),
        sa.ForeignKeyConstraint(["trading_account_id"], ["trading_accounts.id"], ondelete="CASCADE"),
        sa.ForeignKeyConstraint(["user_id"], ["users.id"], ondelete="CASCADE"),
        sa.PrimaryKeyConstraint("id"),
        sa.UniqueConstraint("trading_account_id"),
    )
    op.create_index("ix_guard_accounts_trading_account_id", "guard_accounts", ["trading_account_id"])
    op.create_index("ix_guard_accounts_user_id", "guard_accounts", ["user_id"])

    op.create_table(
        "guard_states",
        sa.Column("id", postgresql.UUID(as_uuid=True), nullable=False),
        sa.Column("guard_account_id", postgresql.UUID(as_uuid=True), nullable=False),
        sa.Column("ts", sa.DateTime(timezone=True), nullable=False),
        sa.Column("equity", sa.Numeric(18, 2), nullable=False),
        sa.Column("balance", sa.Numeric(18, 2), nullable=False),
        sa.Column("peak", sa.Numeric(18, 2), nullable=False),
        sa.Column("day_anchor", sa.Numeric(18, 2), nullable=False),
        sa.Column("status", sa.String(16), nullable=False),
        sa.Column("buffers_json", postgresql.JSONB(astext_type=sa.Text()), nullable=False),
        sa.Column("challenge_json", postgresql.JSONB(astext_type=sa.Text()), nullable=False),
        sa.Column("memory_json", postgresql.JSONB(astext_type=sa.Text()), nullable=False),
        sa.Column("updated_at", sa.DateTime(timezone=True), nullable=False),
        sa.ForeignKeyConstraint(["guard_account_id"], ["guard_accounts.id"], ondelete="CASCADE"),
        sa.PrimaryKeyConstraint("id"),
        sa.UniqueConstraint("guard_account_id"),
    )
    op.create_index("ix_guard_states_guard_account_id", "guard_states", ["guard_account_id"])

    op.create_table(
        "guard_daily_results",
        sa.Column("id", postgresql.UUID(as_uuid=True), nullable=False),
        sa.Column("guard_account_id", postgresql.UUID(as_uuid=True), nullable=False),
        sa.Column("result_date", sa.Date(), nullable=False),
        sa.Column("pnl", sa.Numeric(18, 2), nullable=False, server_default="0"),
        sa.Column("trade_count", sa.Integer(), nullable=False, server_default="0"),
        sa.Column("is_trading_day", sa.Boolean(), nullable=False, server_default=sa.false()),
        sa.ForeignKeyConstraint(["guard_account_id"], ["guard_accounts.id"], ondelete="CASCADE"),
        sa.PrimaryKeyConstraint("id"),
        sa.UniqueConstraint("guard_account_id", "result_date"),
    )
    op.create_index("ix_guard_daily_results_guard_account_id", "guard_daily_results", ["guard_account_id"])

    op.create_table(
        "guard_ticks",
        sa.Column("id", postgresql.UUID(as_uuid=True), nullable=False),
        sa.Column("guard_account_id", postgresql.UUID(as_uuid=True), nullable=False),
        sa.Column("ts", sa.DateTime(timezone=True), nullable=False),
        sa.Column("equity", sa.Numeric(18, 2), nullable=False),
        sa.ForeignKeyConstraint(["guard_account_id"], ["guard_accounts.id"], ondelete="CASCADE"),
        sa.PrimaryKeyConstraint("id"),
    )
    op.create_index("ix_guard_ticks_guard_account_id", "guard_ticks", ["guard_account_id"])

    op.create_table(
        "guard_alerts",
        sa.Column("id", postgresql.UUID(as_uuid=True), nullable=False),
        sa.Column("guard_account_id", postgresql.UUID(as_uuid=True), nullable=False),
        sa.Column("ts", sa.DateTime(timezone=True), nullable=False),
        sa.Column("tier", sa.String(16), nullable=False),
        sa.Column("kind", sa.String(32), nullable=False),
        sa.Column("channel", sa.String(16), nullable=False, server_default="email"),
        sa.Column("sent_ok", sa.Boolean(), nullable=False, server_default=sa.false()),
        sa.ForeignKeyConstraint(["guard_account_id"], ["guard_accounts.id"], ondelete="CASCADE"),
        sa.PrimaryKeyConstraint("id"),
    )
    op.create_index("ix_guard_alerts_guard_account_id", "guard_alerts", ["guard_account_id"])


def downgrade() -> None:
    op.drop_table("guard_alerts")
    op.drop_table("guard_ticks")
    op.drop_table("guard_daily_results")
    op.drop_table("guard_states")
    op.drop_table("guard_accounts")
