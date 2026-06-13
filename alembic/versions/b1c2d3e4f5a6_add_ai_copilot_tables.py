"""add_ai_copilot_tables

Revision ID: b1c2d3e4f5a6
Revises: a2b3c4d5e6f7
Create Date: 2026-06-13 00:00:00.000000

Creates all tables for the Partna AI copilot domain:
  - ai_chat_sessions
  - ai_chat_messages
  - ai_usage
  - ai_user_memory
  - ai_agents
  - ai_insights

All indexes ship in this migration per RULES §3.
Retention: ai_chat_messages is unbounded; a purge job is required (see tasks).
LangGraph checkpoint tables are managed by AsyncPostgresSaver.setup() at startup.
"""
from typing import Sequence, Union

import sqlalchemy as sa
from alembic import op
from sqlalchemy.dialects import postgresql

revision: str = "b1c2d3e4f5a6"
down_revision: Union[str, None] = "a2b3c4d5e6f7"
branch_labels: Union[str, Sequence[str], None] = None
depends_on: Union[str, Sequence[str], None] = None


def upgrade() -> None:
    # ── Enum ─────────────────────────────────────────────────────────────────
    # Use a DO block so this is safe to re-run if the migration was previously
    # interrupted after the type was already created.
    op.execute(
        """
        DO $$ BEGIN
            CREATE TYPE aimessagerolee AS ENUM ('user', 'assistant');
        EXCEPTION
            WHEN duplicate_object THEN null;
        END $$;
        """
    )

    # ── ai_chat_sessions ─────────────────────────────────────────────────────
    op.create_table(
        "ai_chat_sessions",
        sa.Column("id", postgresql.UUID(as_uuid=True), primary_key=True),
        sa.Column("user_id", postgresql.UUID(as_uuid=True), nullable=False),
        sa.Column("account_id", postgresql.UUID(as_uuid=True), nullable=True),
        sa.Column("title", sa.String(255), nullable=True),
        sa.Column("context_type", sa.String(20), nullable=False, server_default="general"),
        sa.Column("context_ref", sa.String(64), nullable=True),
        sa.Column("is_deleted", sa.Boolean(), nullable=False, server_default="false"),
        sa.Column("last_message_at", sa.DateTime(timezone=True), nullable=True),
        sa.Column(
            "created_at",
            sa.DateTime(timezone=True),
            nullable=False,
            server_default=sa.text("NOW()"),
        ),
        sa.Column(
            "updated_at",
            sa.DateTime(timezone=True),
            nullable=False,
            server_default=sa.text("NOW()"),
        ),
        if_not_exists=True,
    )

    # Context-scoped uniqueness: one live session per (user, type, ref, account)
    op.create_index(
        "uq_ai_session_context",
        "ai_chat_sessions",
        ["user_id", "context_type", "context_ref", "account_id"],
        unique=True,
        postgresql_where=sa.text("is_deleted = false AND context_type <> 'general'"),
        if_not_exists=True,
    )
    # Recency list for history sidebar
    op.create_index(
        "ix_ai_sessions_user_recent",
        "ai_chat_sessions",
        ["user_id", sa.text("last_message_at DESC NULLS LAST")],
        postgresql_where=sa.text("is_deleted = false"),
        if_not_exists=True,
    )

    # ── ai_chat_messages ─────────────────────────────────────────────────────
    # postgresql.ENUM with create_type=False properly suppresses the dialect's
    # _on_table_create hook. sa.Enum with create_type=False does not in all paths.
    role_enum = postgresql.ENUM("user", "assistant", name="aimessagerolee", create_type=False)
    op.create_table(
        "ai_chat_messages",
        sa.Column("id", postgresql.UUID(as_uuid=True), primary_key=True),
        sa.Column(
            "session_id",
            postgresql.UUID(as_uuid=True),
            sa.ForeignKey("ai_chat_sessions.id", ondelete="CASCADE"),
            nullable=False,
        ),
        sa.Column("role", role_enum, nullable=False),
        sa.Column("content", sa.Text(), nullable=True),
        sa.Column("input_tokens", sa.Integer(), nullable=True),
        sa.Column("output_tokens", sa.Integer(), nullable=True),
        sa.Column("meta", postgresql.JSONB(), nullable=True),
        sa.Column(
            "created_at",
            sa.DateTime(timezone=True),
            nullable=False,
            server_default=sa.text("NOW()"),
        ),
        if_not_exists=True,
    )

    op.create_index(
        "ix_ai_messages_session",
        "ai_chat_messages",
        ["session_id", "created_at"],
        if_not_exists=True,
    )

    # ── ai_usage ─────────────────────────────────────────────────────────────
    op.create_table(
        "ai_usage",
        sa.Column("user_id", postgresql.UUID(as_uuid=True), nullable=False),
        sa.Column("period_month", sa.DateTime(timezone=True), nullable=False),
        sa.Column("credits_used", sa.Integer(), nullable=False, server_default="0"),
        sa.Column("message_count", sa.Integer(), nullable=False, server_default="0"),
        sa.Column("input_tokens", sa.BigInteger(), nullable=False, server_default="0"),
        sa.Column("output_tokens", sa.BigInteger(), nullable=False, server_default="0"),
        sa.Column("cost_cents", sa.Integer(), nullable=False, server_default="0"),
        sa.PrimaryKeyConstraint("user_id", "period_month"),
        if_not_exists=True,
    )

    # ── ai_user_memory ────────────────────────────────────────────────────────
    op.create_table(
        "ai_user_memory",
        sa.Column("user_id", postgresql.UUID(as_uuid=True), primary_key=True),
        sa.Column("profile", postgresql.JSONB(), nullable=False, server_default=sa.text("'{}'")),
        sa.Column("updated_at", sa.DateTime(timezone=True), nullable=False),
        if_not_exists=True,
    )

    # ── ai_agents ─────────────────────────────────────────────────────────────
    op.create_table(
        "ai_agents",
        sa.Column("id", postgresql.UUID(as_uuid=True), primary_key=True),
        sa.Column("user_id", postgresql.UUID(as_uuid=True), nullable=False),
        sa.Column("kind", sa.String(40), nullable=False),
        sa.Column("enabled", sa.Boolean(), nullable=False, server_default="true"),
        sa.Column("config", postgresql.JSONB(), nullable=False, server_default=sa.text("'{}'")),
        sa.Column("last_run_at", sa.DateTime(timezone=True), nullable=True),
        sa.Column(
            "created_at",
            sa.DateTime(timezone=True),
            nullable=False,
            server_default=sa.text("NOW()"),
        ),
        if_not_exists=True,
    )
    op.create_index("ix_ai_agents_user", "ai_agents", ["user_id", "kind"], if_not_exists=True)

    # ── ai_insights ───────────────────────────────────────────────────────────
    op.create_table(
        "ai_insights",
        sa.Column("id", postgresql.UUID(as_uuid=True), primary_key=True),
        sa.Column("user_id", postgresql.UUID(as_uuid=True), nullable=False),
        sa.Column("account_id", postgresql.UUID(as_uuid=True), nullable=True),
        sa.Column("kind", sa.String(40), nullable=False),
        sa.Column("payload", postgresql.JSONB(), nullable=False),
        sa.Column("data_version", sa.BigInteger(), nullable=False),
        sa.Column("generated_at", sa.DateTime(timezone=True), nullable=False),
        sa.Column("valid_until", sa.DateTime(timezone=True), nullable=True),
        if_not_exists=True,
    )
    op.create_index(
        "ix_ai_insights_user",
        "ai_insights",
        ["user_id", "account_id", "kind"],
        if_not_exists=True,
    )


def downgrade() -> None:
    op.drop_table("ai_insights")
    op.drop_table("ai_agents")
    op.drop_table("ai_user_memory")
    op.drop_table("ai_usage")
    op.drop_table("ai_chat_messages")
    op.drop_table("ai_chat_sessions")
    op.execute("DROP TYPE IF EXISTS aimessagerolee")
