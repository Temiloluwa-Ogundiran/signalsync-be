"""harden copy trading runtime

Revision ID: a0b1c2d3e4f5
Revises: 9a4d7c2e5f81
Create Date: 2026-06-22 08:00:00
"""

from alembic import op
import sqlalchemy as sa
from sqlalchemy.dialects import postgresql


revision = "a0b1c2d3e4f5"
down_revision = "9a4d7c2e5f81"
branch_labels = None
depends_on = None


def _enum(name: str, *values: str):
    return postgresql.ENUM(*values, name=name, create_type=False)


conversation_state = _enum("signalconversationstateenum", "active", "completed", "expired", "ambiguous")
assembly_state = _enum("routeassemblystateenum", "assembling", "ready", "executing", "completed", "expired", "skipped", "failed")
auth_state = _enum("telegramauthstateenum", "pending", "awaiting_code", "awaiting_password", "ready", "failed", "expired")
dead_letter_state = _enum("deadletterstateenum", "pending", "replayed", "resolved")
worker_health_state = _enum("workerhealthstateenum", "healthy", "degraded", "failed")


def upgrade() -> None:
    bind = op.get_bind()
    for value in ("advisory", "failed_retryable", "unsupported_image_primary"):
        op.execute(f"ALTER TYPE telegramsourcestateenum ADD VALUE IF NOT EXISTS '{value}'")
    op.execute("ALTER TYPE copyroutestateenum ADD VALUE IF NOT EXISTS 'needs_attention'")
    for enum_type in (conversation_state, assembly_state, auth_state, dead_letter_state, worker_health_state):
        enum_type.create(bind, checkfirst=True)

    op.create_table(
        "signal_conversations",
        sa.Column("id", postgresql.UUID(as_uuid=True), primary_key=True),
        sa.Column("source_id", postgresql.UUID(as_uuid=True), nullable=False),
        sa.Column("legacy_thread_id", postgresql.UUID(as_uuid=True), nullable=False, unique=True),
        sa.Column("correlation_id", sa.String(64), nullable=False, unique=True),
        sa.Column("state", conversation_state, server_default="active", nullable=False),
        sa.Column("reply_root_message_id", sa.BigInteger(), nullable=True),
        sa.Column("explicit_reference", sa.String(255), nullable=True),
        sa.Column("symbol", sa.String(64), nullable=True),
        sa.Column("direction", sa.String(16), nullable=True),
        sa.Column("context", postgresql.JSONB(), server_default=sa.text("'{}'::jsonb"), nullable=False),
        sa.Column("last_message_id", sa.BigInteger(), nullable=False),
        sa.Column("last_message_revision", sa.Integer(), server_default="1", nullable=False),
        sa.Column("created_at", sa.DateTime(timezone=True), server_default=sa.text("now()"), nullable=False),
        sa.Column("updated_at", sa.DateTime(timezone=True), server_default=sa.text("now()"), nullable=False),
        sa.ForeignKeyConstraint(["source_id"], ["telegram_sources.id"], ondelete="CASCADE"),
        sa.ForeignKeyConstraint(["legacy_thread_id"], ["signal_threads.id"], ondelete="CASCADE"),
    )
    op.create_index("ix_signal_conversation_source_state", "signal_conversations", ["source_id", "state"])
    op.create_index("ix_signal_conversation_reply_root", "signal_conversations", ["source_id", "reply_root_message_id"])

    op.create_table(
        "route_signal_assemblies",
        sa.Column("id", postgresql.UUID(as_uuid=True), primary_key=True),
        sa.Column("conversation_id", postgresql.UUID(as_uuid=True), nullable=False),
        sa.Column("route_id", postgresql.UUID(as_uuid=True), nullable=False),
        sa.Column("state", assembly_state, server_default="assembling", nullable=False),
        sa.Column("context", postgresql.JSONB(), server_default=sa.text("'{}'::jsonb"), nullable=False),
        sa.Column("message_references", postgresql.JSONB(), server_default=sa.text("'[]'::jsonb"), nullable=False),
        sa.Column("assembly_deadline", sa.DateTime(timezone=True), nullable=False),
        sa.Column("accepted_at", sa.DateTime(timezone=True), nullable=True),
        sa.Column("created_at", sa.DateTime(timezone=True), server_default=sa.text("now()"), nullable=False),
        sa.Column("updated_at", sa.DateTime(timezone=True), server_default=sa.text("now()"), nullable=False),
        sa.ForeignKeyConstraint(["conversation_id"], ["signal_conversations.id"], ondelete="CASCADE"),
        sa.ForeignKeyConstraint(["route_id"], ["copy_routes.id"], ondelete="CASCADE"),
    )
    op.create_index("ix_route_signal_assembly_route_state", "route_signal_assemblies", ["route_id", "state"])
    op.create_index("ix_route_signal_assemblies_conversation_id", "route_signal_assemblies", ["conversation_id"])
    op.create_index("ix_route_signal_assemblies_route_id", "route_signal_assemblies", ["route_id"])
    op.create_index("uq_route_signal_active_conversation", "route_signal_assemblies", ["route_id", "conversation_id"], unique=True, postgresql_where=sa.text("state IN ('assembling','ready','executing')"))

    op.create_table(
        "telegram_auth_attempts",
        sa.Column("id", postgresql.UUID(as_uuid=True), primary_key=True),
        sa.Column("auth_id", sa.String(64), nullable=False, unique=True),
        sa.Column("user_id", postgresql.UUID(as_uuid=True), nullable=False),
        sa.Column("connection_id", postgresql.UUID(as_uuid=True), nullable=True),
        sa.Column("method", sa.String(16), nullable=False),
        sa.Column("state", auth_state, server_default="pending", nullable=False),
        sa.Column("encrypted_state", sa.Text(), nullable=True),
        sa.Column("message", sa.Text(), nullable=True),
        sa.Column("expires_at", sa.DateTime(timezone=True), nullable=False),
        sa.Column("created_at", sa.DateTime(timezone=True), server_default=sa.text("now()"), nullable=False),
        sa.Column("updated_at", sa.DateTime(timezone=True), server_default=sa.text("now()"), nullable=False),
        sa.ForeignKeyConstraint(["user_id"], ["users.id"], ondelete="CASCADE"),
        sa.ForeignKeyConstraint(["connection_id"], ["telegram_connections.id"], ondelete="CASCADE"),
    )
    op.create_index("ix_telegram_auth_attempts_user_id", "telegram_auth_attempts", ["user_id"])
    op.create_index("ix_telegram_auth_attempts_connection_id", "telegram_auth_attempts", ["connection_id"])
    op.create_index("ix_telegram_auth_attempts_expires_at", "telegram_auth_attempts", ["expires_at"])

    op.create_table(
        "copy_dead_letters",
        sa.Column("id", postgresql.UUID(as_uuid=True), primary_key=True),
        sa.Column("user_id", postgresql.UUID(as_uuid=True), nullable=True),
        sa.Column("source_stream", sa.String(100), nullable=False),
        sa.Column("consumer_group", sa.String(100), nullable=False),
        sa.Column("source_message_id", sa.String(64), nullable=False),
        sa.Column("event_type", sa.String(100), nullable=False),
        sa.Column("correlation_id", sa.String(64), nullable=False),
        sa.Column("idempotency_key", sa.String(255), nullable=False),
        sa.Column("event_payload", postgresql.JSONB(), nullable=False),
        sa.Column("attempts", sa.Integer(), server_default="1", nullable=False),
        sa.Column("error_code", sa.String(100), nullable=False),
        sa.Column("error_message", sa.Text(), nullable=False),
        sa.Column("state", dead_letter_state, server_default="pending", nullable=False),
        sa.Column("replayed_at", sa.DateTime(timezone=True), nullable=True),
        sa.Column("created_at", sa.DateTime(timezone=True), server_default=sa.text("now()"), nullable=False),
        sa.Column("updated_at", sa.DateTime(timezone=True), server_default=sa.text("now()"), nullable=False),
        sa.ForeignKeyConstraint(["user_id"], ["users.id"], ondelete="SET NULL"),
    )
    op.create_index("ix_copy_dead_letters_user_id", "copy_dead_letters", ["user_id"])
    op.create_index("ix_copy_dead_letters_correlation_id", "copy_dead_letters", ["correlation_id"])
    op.create_index("ix_copy_dead_letter_state_created", "copy_dead_letters", ["state", "created_at"])

    op.create_table(
        "copy_worker_health",
        sa.Column("id", postgresql.UUID(as_uuid=True), primary_key=True),
        sa.Column("worker_role", sa.String(64), nullable=False),
        sa.Column("instance_id", sa.String(128), nullable=False),
        sa.Column("state", worker_health_state, server_default="healthy", nullable=False),
        sa.Column("heartbeat_at", sa.DateTime(timezone=True), nullable=False),
        sa.Column("stream_lag", sa.Integer(), server_default="0", nullable=False),
        sa.Column("pending_count", sa.Integer(), server_default="0", nullable=False),
        sa.Column("last_error", sa.Text(), nullable=True),
        sa.Column("metrics", postgresql.JSONB(), server_default=sa.text("'{}'::jsonb"), nullable=False),
        sa.Column("created_at", sa.DateTime(timezone=True), server_default=sa.text("now()"), nullable=False),
        sa.Column("updated_at", sa.DateTime(timezone=True), server_default=sa.text("now()"), nullable=False),
        sa.UniqueConstraint("worker_role", "instance_id"),
    )
    op.create_index("ix_copy_worker_health_worker_role", "copy_worker_health", ["worker_role"])
    op.create_index("ix_copy_worker_health_heartbeat_at", "copy_worker_health", ["heartbeat_at"])

    op.add_column("trade_intents", sa.Column("client_order_id", sa.String(32), nullable=True))
    op.create_unique_constraint("uq_trade_intent_client_order_id", "trade_intents", ["client_order_id"])
    op.add_column("copied_trades", sa.Column("original_volume", sa.Numeric(12, 4), nullable=True))
    op.add_column("copied_trades", sa.Column("current_volume", sa.Numeric(12, 4), nullable=True))
    op.add_column("copied_trades", sa.Column("stop_loss", sa.Numeric(20, 8), nullable=True))
    op.add_column("copied_trades", sa.Column("take_profit", sa.Numeric(20, 8), nullable=True))
    op.add_column("copied_trades", sa.Column("broker_synced_at", sa.DateTime(timezone=True), nullable=True))


def downgrade() -> None:
    for column in ("broker_synced_at", "take_profit", "stop_loss", "current_volume", "original_volume"):
        op.drop_column("copied_trades", column)
    op.drop_constraint("uq_trade_intent_client_order_id", "trade_intents", type_="unique")
    op.drop_column("trade_intents", "client_order_id")
    op.drop_table("copy_worker_health")
    op.drop_table("copy_dead_letters")
    op.drop_table("telegram_auth_attempts")
    op.drop_table("route_signal_assemblies")
    op.drop_table("signal_conversations")
    bind = op.get_bind()
    for enum_type in (worker_health_state, dead_letter_state, auth_state, assembly_state, conversation_state):
        enum_type.drop(bind, checkfirst=True)
