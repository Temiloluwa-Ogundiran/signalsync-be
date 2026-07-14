"""add copy production controls

Revision ID: d9f1a3c5e7b9
Revises: c8e0f2a4b6d8
Create Date: 2026-07-14
"""

from alembic import op
import sqlalchemy as sa
from sqlalchemy.dialects import postgresql


revision = "d9f1a3c5e7b9"
down_revision = "c8e0f2a4b6d8"
branch_labels = None
depends_on = None


def upgrade() -> None:
    op.add_column("copy_account_policies", sa.Column("max_spread_points", sa.Integer(), nullable=True))
    op.add_column("copy_account_policies", sa.Column("max_slippage_points", sa.Integer(), nullable=True))
    op.add_column("copy_account_policies", sa.Column("max_quote_age_seconds", sa.Integer(), nullable=False, server_default="10"))
    op.add_column("copy_account_policies", sa.Column("high_spread_behavior", sa.String(16), nullable=False, server_default="reject"))
    op.add_column("copy_account_policies", sa.Column("trading_start_hour_utc", sa.Integer(), nullable=True))
    op.add_column("copy_account_policies", sa.Column("trading_end_hour_utc", sa.Integer(), nullable=True))
    op.add_column("copy_routes", sa.Column("semantic_duplicate_window_seconds", sa.Integer(), nullable=False, server_default="30"))
    op.create_check_constraint("ck_copy_policy_quote_age", "copy_account_policies", "max_quote_age_seconds BETWEEN 1 AND 300")
    op.create_check_constraint("ck_copy_policy_spread_nonnegative", "copy_account_policies", "max_spread_points IS NULL OR max_spread_points >= 0")
    op.create_check_constraint("ck_copy_policy_slippage_nonnegative", "copy_account_policies", "max_slippage_points IS NULL OR max_slippage_points >= 0")
    op.create_check_constraint("ck_copy_policy_start_hour", "copy_account_policies", "trading_start_hour_utc IS NULL OR trading_start_hour_utc BETWEEN 0 AND 23")
    op.create_check_constraint("ck_copy_policy_end_hour", "copy_account_policies", "trading_end_hour_utc IS NULL OR trading_end_hour_utc BETWEEN 0 AND 23")
    op.create_check_constraint("ck_copy_policy_spread_behavior", "copy_account_policies", "high_spread_behavior IN ('reject', 'wait')")
    op.create_check_constraint("ck_copy_policy_trading_hours_pair", "copy_account_policies", "(trading_start_hour_utc IS NULL) = (trading_end_hour_utc IS NULL)")
    op.create_check_constraint("ck_copy_route_duplicate_window", "copy_routes", "semantic_duplicate_window_seconds BETWEEN 0 AND 3600")

    review_state = postgresql.ENUM(
        "pending",
        "approved",
        "ignored",
        name="signalreviewstateenum",
        create_type=False,
    )
    review_state.create(op.get_bind(), checkfirst=True)
    op.create_table(
        "copy_execution_metrics",
        sa.Column("id", postgresql.UUID(as_uuid=True), primary_key=True),
        sa.Column("user_id", postgresql.UUID(as_uuid=True), sa.ForeignKey("users.id", ondelete="CASCADE"), nullable=False),
        sa.Column("route_id", postgresql.UUID(as_uuid=True), sa.ForeignKey("copy_routes.id", ondelete="CASCADE"), nullable=False),
        sa.Column("intent_id", postgresql.UUID(as_uuid=True), sa.ForeignKey("trade_intents.id", ondelete="CASCADE"), nullable=False, unique=True),
        sa.Column("correlation_id", sa.String(64), nullable=False),
        sa.Column("action", sa.String(32), nullable=False),
        sa.Column("symbol", sa.String(64), nullable=True),
        sa.Column("status", sa.String(32), nullable=False),
        sa.Column("telegram_at", sa.DateTime(timezone=True), nullable=True),
        sa.Column("ingested_at", sa.DateTime(timezone=True), nullable=True),
        sa.Column("validated_at", sa.DateTime(timezone=True), nullable=True),
        sa.Column("submitted_at", sa.DateTime(timezone=True), nullable=True),
        sa.Column("resolved_at", sa.DateTime(timezone=True), nullable=True),
        sa.Column("ingestion_ms", sa.Integer(), nullable=True),
        sa.Column("assembly_ms", sa.Integer(), nullable=True),
        sa.Column("broker_ms", sa.Integer(), nullable=True),
        sa.Column("total_ms", sa.Integer(), nullable=True),
        sa.Column("created_at", sa.DateTime(timezone=True), nullable=False),
    )
    op.create_index("ix_copy_execution_metric_user_created", "copy_execution_metrics", ["user_id", "created_at"])
    op.create_index("ix_copy_execution_metric_route_created", "copy_execution_metrics", ["route_id", "created_at"])
    op.create_index("ix_copy_execution_metrics_correlation_id", "copy_execution_metrics", ["correlation_id"])

    op.create_table(
        "copy_signal_reviews",
        sa.Column("id", postgresql.UUID(as_uuid=True), primary_key=True),
        sa.Column("user_id", postgresql.UUID(as_uuid=True), sa.ForeignKey("users.id", ondelete="CASCADE"), nullable=False),
        sa.Column("route_id", postgresql.UUID(as_uuid=True), sa.ForeignKey("copy_routes.id", ondelete="CASCADE"), nullable=False),
        sa.Column("source_id", postgresql.UUID(as_uuid=True), sa.ForeignKey("telegram_sources.id", ondelete="CASCADE"), nullable=False),
        sa.Column("correlation_id", sa.String(64), nullable=False),
        sa.Column("encrypted_event_payload", sa.Text(), nullable=False),
        sa.Column("parsed_details", postgresql.JSONB(), nullable=False),
        sa.Column("candidates", postgresql.JSONB(), nullable=False),
        sa.Column("state", review_state, nullable=False, server_default="pending"),
        sa.Column("resolved_conversation_id", postgresql.UUID(as_uuid=True), sa.ForeignKey("signal_conversations.id", ondelete="SET NULL"), nullable=True),
        sa.Column("resolved_at", sa.DateTime(timezone=True), nullable=True),
        sa.Column("created_at", sa.DateTime(timezone=True), nullable=False),
        sa.Column("updated_at", sa.DateTime(timezone=True), nullable=False),
    )
    op.create_index("ix_copy_signal_review_user_state", "copy_signal_reviews", ["user_id", "state", "created_at"])
    op.create_index("ix_copy_signal_reviews_route_id", "copy_signal_reviews", ["route_id"])
    op.create_index("ix_copy_signal_reviews_source_id", "copy_signal_reviews", ["source_id"])
    op.create_index("ix_copy_signal_reviews_correlation_id", "copy_signal_reviews", ["correlation_id"])


def downgrade() -> None:
    op.drop_table("copy_signal_reviews")
    op.drop_table("copy_execution_metrics")
    op.execute("DROP TYPE IF EXISTS signalreviewstateenum")
    op.drop_constraint("ck_copy_route_duplicate_window", "copy_routes", type_="check")
    op.drop_column("copy_routes", "semantic_duplicate_window_seconds")
    for name in ("ck_copy_policy_trading_hours_pair", "ck_copy_policy_spread_behavior", "ck_copy_policy_end_hour", "ck_copy_policy_start_hour", "ck_copy_policy_slippage_nonnegative", "ck_copy_policy_spread_nonnegative", "ck_copy_policy_quote_age"):
        op.drop_constraint(name, "copy_account_policies", type_="check")
    for name in ("trading_end_hour_utc", "trading_start_hour_utc", "high_spread_behavior", "max_quote_age_seconds", "max_slippage_points", "max_spread_points"):
        op.drop_column("copy_account_policies", name)
