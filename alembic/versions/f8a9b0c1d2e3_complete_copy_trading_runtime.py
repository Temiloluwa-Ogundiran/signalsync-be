"""complete copy trading runtime

Revision ID: f8a9b0c1d2e3
Revises: e7f8a9b0c1d2
Create Date: 2026-06-21 00:00:00
"""

from alembic import op
import sqlalchemy as sa
from sqlalchemy.dialects import postgresql


revision = "f8a9b0c1d2e3"
down_revision = "e7f8a9b0c1d2"
branch_labels = None
depends_on = None


confidence = postgresql.ENUM("low", "medium", "high", name="automationconfidenceenum", create_type=False)
thread_state = postgresql.ENUM("assembling", "complete", "validated", "executing", "succeeded", "expired", "skipped", "rejected", "failed", name="signalthreadstateenum", create_type=False)
intent_state = postgresql.ENUM("created", "submitted", "confirmed", "uncertain", "reconciling", "retryable", "failed", name="tradeintentstateenum", create_type=False)


def upgrade() -> None:
    bind = op.get_bind()
    for enum_type in (confidence, thread_state, intent_state):
        enum_type.create(bind, checkfirst=True)

    op.create_table(
        "channel_profiles",
        sa.Column("id", postgresql.UUID(as_uuid=True), primary_key=True),
        sa.Column("telegram_chat_id", sa.BigInteger(), nullable=False),
        sa.Column("parser_version", sa.String(64), nullable=False),
        sa.Column("signal_style", sa.String(100), nullable=False),
        sa.Column("recommended_assembly_window_seconds", sa.Integer(), server_default="90", nullable=False),
        sa.Column("confidence", confidence, nullable=False),
        sa.Column("confidence_score", sa.Float(), nullable=False),
        sa.Column("image_frequency", sa.Float(), server_default="0", nullable=False),
        sa.Column("image_primary", sa.Boolean(), server_default=sa.false(), nullable=False),
        sa.Column("supported_actions", postgresql.JSONB(), server_default=sa.text("'[]'::jsonb"), nullable=False),
        sa.Column("author_pattern", postgresql.JSONB(), server_default=sa.text("'{}'::jsonb"), nullable=False),
        sa.Column("analyzed_from", sa.DateTime(timezone=True), nullable=False),
        sa.Column("analyzed_to", sa.DateTime(timezone=True), nullable=False),
        sa.Column("sample_count", sa.Integer(), server_default="0", nullable=False),
        sa.Column("validated_at", sa.DateTime(timezone=True), nullable=False),
        sa.Column("created_at", sa.DateTime(timezone=True), server_default=sa.text("now()"), nullable=False),
        sa.Column("updated_at", sa.DateTime(timezone=True), server_default=sa.text("now()"), nullable=False),
        sa.UniqueConstraint("telegram_chat_id", "parser_version"),
    )
    op.create_index("ix_channel_profiles_telegram_chat_id", "channel_profiles", ["telegram_chat_id"])
    op.add_column("telegram_connections", sa.Column("display_name", sa.String(255), nullable=True))
    op.add_column("telegram_connections", sa.Column("username", sa.String(255), nullable=True))
    op.add_column("telegram_connections", sa.Column("is_paused", sa.Boolean(), server_default=sa.false(), nullable=False))
    op.add_column("telegram_connections", sa.Column("reauthentication_reason", sa.Text(), nullable=True))
    op.add_column("telegram_sources", sa.Column("profile_id", postgresql.UUID(as_uuid=True), nullable=True))
    op.add_column("telegram_sources", sa.Column("is_paused", sa.Boolean(), server_default=sa.false(), nullable=False))
    op.create_foreign_key("fk_telegram_sources_profile", "telegram_sources", "channel_profiles", ["profile_id"], ["id"], ondelete="SET NULL")

    op.create_table(
        "channel_message_samples",
        sa.Column("id", postgresql.UUID(as_uuid=True), primary_key=True),
        sa.Column("profile_id", postgresql.UUID(as_uuid=True), nullable=True),
        sa.Column("telegram_chat_id", sa.BigInteger(), nullable=False),
        sa.Column("telegram_message_id", sa.BigInteger(), nullable=False),
        sa.Column("encrypted_raw_message", sa.Text(), nullable=False),
        sa.Column("message_metadata", postgresql.JSONB(), server_default=sa.text("'{}'::jsonb"), nullable=False),
        sa.Column("purpose", sa.String(32), server_default="learning", nullable=False),
        sa.Column("expires_at", sa.DateTime(timezone=True), nullable=False),
        sa.Column("created_at", sa.DateTime(timezone=True), server_default=sa.text("now()"), nullable=False),
        sa.ForeignKeyConstraint(["profile_id"], ["channel_profiles.id"], ondelete="CASCADE"),
        sa.UniqueConstraint("telegram_chat_id", "telegram_message_id", "purpose"),
    )
    op.create_index("ix_channel_message_samples_expires_at", "channel_message_samples", ["expires_at"])
    op.create_index("ix_channel_message_samples_profile_id", "channel_message_samples", ["profile_id"])
    op.create_index("ix_channel_message_samples_telegram_chat_id", "channel_message_samples", ["telegram_chat_id"])

    op.create_table(
        "signal_threads",
        sa.Column("id", postgresql.UUID(as_uuid=True), primary_key=True),
        sa.Column("source_id", postgresql.UUID(as_uuid=True), nullable=False),
        sa.Column("correlation_id", sa.String(64), nullable=False, unique=True),
        sa.Column("state", thread_state, server_default="assembling", nullable=False),
        sa.Column("explicit_reference", sa.String(255), nullable=True),
        sa.Column("symbol", sa.String(64), nullable=True),
        sa.Column("direction", sa.String(16), nullable=True),
        sa.Column("context", postgresql.JSONB(), server_default=sa.text("'{}'::jsonb"), nullable=False),
        sa.Column("message_references", postgresql.JSONB(), server_default=sa.text("'[]'::jsonb"), nullable=False),
        sa.Column("assembly_deadline", sa.DateTime(timezone=True), nullable=False),
        sa.Column("created_at", sa.DateTime(timezone=True), server_default=sa.text("now()"), nullable=False),
        sa.Column("updated_at", sa.DateTime(timezone=True), server_default=sa.text("now()"), nullable=False),
        sa.ForeignKeyConstraint(["source_id"], ["telegram_sources.id"], ondelete="CASCADE"),
    )
    op.create_index("ix_signal_thread_source_state", "signal_threads", ["source_id", "state"])

    op.create_table(
        "parsed_actions",
        sa.Column("id", postgresql.UUID(as_uuid=True), primary_key=True),
        sa.Column("thread_id", postgresql.UUID(as_uuid=True), nullable=False),
        sa.Column("route_id", postgresql.UUID(as_uuid=True), nullable=False),
        sa.Column("telegram_message_id", sa.BigInteger(), nullable=False),
        sa.Column("action_type", sa.String(64), nullable=False),
        sa.Column("revision", sa.Integer(), server_default="1", nullable=False),
        sa.Column("model_name", sa.String(100), nullable=False),
        sa.Column("parser_version", sa.String(64), nullable=False),
        sa.Column("confidence", sa.Float(), nullable=False),
        sa.Column("payload", postgresql.JSONB(), nullable=False),
        sa.Column("validation_result", postgresql.JSONB(), server_default=sa.text("'{}'::jsonb"), nullable=False),
        sa.Column("created_at", sa.DateTime(timezone=True), server_default=sa.text("now()"), nullable=False),
        sa.ForeignKeyConstraint(["thread_id"], ["signal_threads.id"], ondelete="CASCADE"),
        sa.ForeignKeyConstraint(["route_id"], ["copy_routes.id"], ondelete="CASCADE"),
        sa.UniqueConstraint("thread_id", "telegram_message_id", "route_id", "action_type", "revision"),
    )
    op.create_index("ix_parsed_actions_route_id", "parsed_actions", ["route_id"])
    op.create_index("ix_parsed_actions_thread_id", "parsed_actions", ["thread_id"])

    op.create_table(
        "trade_intents",
        sa.Column("id", postgresql.UUID(as_uuid=True), primary_key=True),
        sa.Column("user_id", postgresql.UUID(as_uuid=True), nullable=False),
        sa.Column("route_id", postgresql.UUID(as_uuid=True), nullable=False),
        sa.Column("account_id", postgresql.UUID(as_uuid=True), nullable=False),
        sa.Column("parsed_action_id", postgresql.UUID(as_uuid=True), nullable=False),
        sa.Column("idempotency_key", sa.String(255), nullable=False, unique=True),
        sa.Column("state", intent_state, server_default="created", nullable=False),
        sa.Column("request_payload", postgresql.JSONB(), nullable=False),
        sa.Column("broker_result", postgresql.JSONB(), server_default=sa.text("'{}'::jsonb"), nullable=False),
        sa.Column("attempt_count", sa.Integer(), server_default="0", nullable=False),
        sa.Column("last_error_code", sa.String(100), nullable=True),
        sa.Column("submitted_at", sa.DateTime(timezone=True), nullable=True),
        sa.Column("resolved_at", sa.DateTime(timezone=True), nullable=True),
        sa.Column("created_at", sa.DateTime(timezone=True), server_default=sa.text("now()"), nullable=False),
        sa.Column("updated_at", sa.DateTime(timezone=True), server_default=sa.text("now()"), nullable=False),
        sa.ForeignKeyConstraint(["user_id"], ["users.id"], ondelete="CASCADE"),
        sa.ForeignKeyConstraint(["route_id"], ["copy_routes.id"], ondelete="CASCADE"),
        sa.ForeignKeyConstraint(["account_id"], ["trading_accounts.id"], ondelete="CASCADE"),
        sa.ForeignKeyConstraint(["parsed_action_id"], ["parsed_actions.id"], ondelete="CASCADE"),
    )
    op.create_index("ix_trade_intent_account_state", "trade_intents", ["account_id", "state"])
    op.create_index("ix_trade_intents_route_id", "trade_intents", ["route_id"])
    op.create_index("ix_trade_intents_user_id", "trade_intents", ["user_id"])

    op.create_table(
        "copied_trades",
        sa.Column("id", postgresql.UUID(as_uuid=True), primary_key=True),
        sa.Column("route_id", postgresql.UUID(as_uuid=True), nullable=False),
        sa.Column("thread_id", postgresql.UUID(as_uuid=True), nullable=False),
        sa.Column("intent_id", postgresql.UUID(as_uuid=True), nullable=False, unique=True),
        sa.Column("magic_number", sa.BigInteger(), nullable=False),
        sa.Column("route_comment", sa.String(31), nullable=False),
        sa.Column("signal_symbol", sa.String(64), nullable=False),
        sa.Column("broker_symbol", sa.String(64), nullable=False),
        sa.Column("broker_order_id", sa.String(64), nullable=True),
        sa.Column("broker_deal_id", sa.String(64), nullable=True),
        sa.Column("broker_position_id", sa.String(64), nullable=True),
        sa.Column("lifecycle_state", sa.String(32), nullable=False),
        sa.Column("detached_at", sa.DateTime(timezone=True), nullable=True),
        sa.Column("created_at", sa.DateTime(timezone=True), server_default=sa.text("now()"), nullable=False),
        sa.Column("updated_at", sa.DateTime(timezone=True), server_default=sa.text("now()"), nullable=False),
        sa.ForeignKeyConstraint(["route_id"], ["copy_routes.id"], ondelete="CASCADE"),
        sa.ForeignKeyConstraint(["thread_id"], ["signal_threads.id"], ondelete="CASCADE"),
        sa.ForeignKeyConstraint(["intent_id"], ["trade_intents.id"], ondelete="CASCADE"),
    )
    op.create_index("ix_copied_trade_route_lifecycle", "copied_trades", ["route_id", "lifecycle_state"])
    for column in ("broker_order_id", "broker_deal_id", "broker_position_id"):
        op.create_index(f"ix_copied_trades_{column}", "copied_trades", [column])

    op.create_table(
        "symbol_mappings",
        sa.Column("id", postgresql.UUID(as_uuid=True), primary_key=True),
        sa.Column("route_id", postgresql.UUID(as_uuid=True), nullable=False),
        sa.Column("account_id", postgresql.UUID(as_uuid=True), nullable=False),
        sa.Column("normalized_signal_symbol", sa.String(64), nullable=False),
        sa.Column("broker_symbol", sa.String(64), nullable=False),
        sa.Column("selection_evidence", postgresql.JSONB(), server_default=sa.text("'{}'::jsonb"), nullable=False),
        sa.Column("catalog_version", sa.String(64), nullable=False),
        sa.Column("created_at", sa.DateTime(timezone=True), server_default=sa.text("now()"), nullable=False),
        sa.Column("updated_at", sa.DateTime(timezone=True), server_default=sa.text("now()"), nullable=False),
        sa.ForeignKeyConstraint(["route_id"], ["copy_routes.id"], ondelete="CASCADE"),
        sa.ForeignKeyConstraint(["account_id"], ["trading_accounts.id"], ondelete="CASCADE"),
        sa.UniqueConstraint("route_id", "account_id", "normalized_signal_symbol"),
    )
    op.create_index("ix_symbol_mappings_route_id", "symbol_mappings", ["route_id"])
    op.create_index("ix_symbol_mappings_account_id", "symbol_mappings", ["account_id"])


def downgrade() -> None:
    op.drop_table("symbol_mappings")
    op.drop_table("copied_trades")
    op.drop_table("trade_intents")
    op.drop_table("parsed_actions")
    op.drop_table("signal_threads")
    op.drop_table("channel_message_samples")
    op.drop_constraint("fk_telegram_sources_profile", "telegram_sources", type_="foreignkey")
    op.drop_column("telegram_sources", "is_paused")
    op.drop_column("telegram_sources", "profile_id")
    for column in ("reauthentication_reason", "is_paused", "username", "display_name"):
        op.drop_column("telegram_connections", column)
    op.drop_table("channel_profiles")
    bind = op.get_bind()
    for enum_type in (intent_state, thread_state, confidence):
        enum_type.drop(bind, checkfirst=True)
