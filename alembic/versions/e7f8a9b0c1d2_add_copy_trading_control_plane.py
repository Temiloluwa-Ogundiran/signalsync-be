"""add copy trading control plane

Revision ID: e7f8a9b0c1d2
Revises: onboard9f8e7d6c5b4a
Create Date: 2026-06-20 00:00:00.000000
"""

from typing import Sequence, Union

from alembic import op
import sqlalchemy as sa
from sqlalchemy.dialects import postgresql


revision: str = "e7f8a9b0c1d2"
down_revision: Union[str, Sequence[str], None] = "onboard9f8e7d6c5b4a"
branch_labels: Union[str, Sequence[str], None] = None
depends_on: Union[str, Sequence[str], None] = None


telegram_connection_state = postgresql.ENUM(
    "pending", "ready", "reauthentication_required", "disconnected",
    name="telegramconnectionstateenum", create_type=False,
)
telegram_source_type = postgresql.ENUM(
    "channel", "group", name="telegramsourcetypeenum", create_type=False,
)
telegram_source_state = postgresql.ENUM(
    "draft", "learning", "ready", "active", "paused", "unsupported",
    name="telegramsourcestateenum", create_type=False,
)
copy_route_state = postgresql.ENUM(
    "draft", "ready", "active", "paused", "reauthentication_required",
    "unsupported", "target_unavailable", name="copyroutestateenum", create_type=False,
)
take_profit_mode = postgresql.ENUM(
    "all", "lowest", "highest", name="takeprofitmodeenum", create_type=False,
)
lot_distribution = postgresql.ENUM(
    "split_total", "fixed_each", name="lotdistributionenum", create_type=False,
)
minimum_fields = postgresql.ENUM(
    "direction_symbol", "direction_symbol_entry", "direction_symbol_sl",
    "direction_symbol_tp", "direction_symbol_sl_tp", name="minimumfieldsenum",
    create_type=False,
)
copy_activity_level = postgresql.ENUM(
    "info", "success", "warning", "error", name="copyactivitylevelenum",
    create_type=False,
)


def upgrade() -> None:
    bind = op.get_bind()
    for enum_type in (
        telegram_connection_state,
        telegram_source_type,
        telegram_source_state,
        copy_route_state,
        take_profit_mode,
        lot_distribution,
        minimum_fields,
        copy_activity_level,
    ):
        enum_type.create(bind, checkfirst=True)

    op.create_table(
        "copy_trading_user_settings",
        sa.Column("user_id", postgresql.UUID(as_uuid=True), nullable=False),
        sa.Column("is_paused", sa.Boolean(), server_default=sa.false(), nullable=False),
        sa.Column("created_at", sa.DateTime(timezone=True), server_default=sa.text("now()"), nullable=False),
        sa.Column("updated_at", sa.DateTime(timezone=True), server_default=sa.text("now()"), nullable=False),
        sa.ForeignKeyConstraint(["user_id"], ["users.id"], ondelete="CASCADE"),
        sa.PrimaryKeyConstraint("user_id"),
    )

    op.create_table(
        "telegram_connections",
        sa.Column("id", postgresql.UUID(as_uuid=True), nullable=False),
        sa.Column("user_id", postgresql.UUID(as_uuid=True), nullable=False),
        sa.Column("telegram_user_id", sa.BigInteger(), nullable=True),
        sa.Column("phone_hint", sa.String(length=32), nullable=True),
        sa.Column("encrypted_session", sa.Text(), nullable=True),
        sa.Column("state", telegram_connection_state, server_default="pending", nullable=False),
        sa.Column("last_heartbeat_at", sa.DateTime(timezone=True), nullable=True),
        sa.Column("created_at", sa.DateTime(timezone=True), server_default=sa.text("now()"), nullable=False),
        sa.Column("updated_at", sa.DateTime(timezone=True), server_default=sa.text("now()"), nullable=False),
        sa.ForeignKeyConstraint(["user_id"], ["users.id"], ondelete="CASCADE"),
        sa.PrimaryKeyConstraint("id"),
        sa.UniqueConstraint("user_id", "telegram_user_id"),
    )
    op.create_index("ix_telegram_connections_user_id", "telegram_connections", ["user_id"])

    op.create_table(
        "telegram_sources",
        sa.Column("id", postgresql.UUID(as_uuid=True), nullable=False),
        sa.Column("user_id", postgresql.UUID(as_uuid=True), nullable=False),
        sa.Column("connection_id", postgresql.UUID(as_uuid=True), nullable=False),
        sa.Column("telegram_chat_id", sa.BigInteger(), nullable=False),
        sa.Column("title", sa.String(length=255), nullable=False),
        sa.Column("username", sa.String(length=255), nullable=True),
        sa.Column("source_type", telegram_source_type, nullable=False),
        sa.Column("state", telegram_source_state, server_default="draft", nullable=False),
        sa.Column("unsupported_reason", sa.Text(), nullable=True),
        sa.Column("created_at", sa.DateTime(timezone=True), server_default=sa.text("now()"), nullable=False),
        sa.Column("updated_at", sa.DateTime(timezone=True), server_default=sa.text("now()"), nullable=False),
        sa.ForeignKeyConstraint(["connection_id"], ["telegram_connections.id"], ondelete="CASCADE"),
        sa.ForeignKeyConstraint(["user_id"], ["users.id"], ondelete="CASCADE"),
        sa.PrimaryKeyConstraint("id"),
        sa.UniqueConstraint("connection_id", "telegram_chat_id"),
    )
    op.create_index("ix_telegram_sources_connection_id", "telegram_sources", ["connection_id"])
    op.create_index("ix_telegram_sources_user_id", "telegram_sources", ["user_id"])

    op.create_table(
        "copy_account_policies",
        sa.Column("id", postgresql.UUID(as_uuid=True), nullable=False),
        sa.Column("user_id", postgresql.UUID(as_uuid=True), nullable=False),
        sa.Column("account_id", postgresql.UUID(as_uuid=True), nullable=False),
        sa.Column("max_lot", sa.Numeric(12, 4), server_default="100.0000", nullable=False),
        sa.Column("is_paused", sa.Boolean(), server_default=sa.false(), nullable=False),
        sa.Column("created_at", sa.DateTime(timezone=True), server_default=sa.text("now()"), nullable=False),
        sa.Column("updated_at", sa.DateTime(timezone=True), server_default=sa.text("now()"), nullable=False),
        sa.CheckConstraint("max_lot > 0", name="ck_copy_account_policy_max_lot_positive"),
        sa.ForeignKeyConstraint(["account_id"], ["trading_accounts.id"], ondelete="CASCADE"),
        sa.ForeignKeyConstraint(["user_id"], ["users.id"], ondelete="CASCADE"),
        sa.PrimaryKeyConstraint("id"),
        sa.UniqueConstraint("user_id", "account_id"),
    )
    op.create_index("ix_copy_account_policies_account_id", "copy_account_policies", ["account_id"])
    op.create_index("ix_copy_account_policies_user_id", "copy_account_policies", ["user_id"])

    op.create_table(
        "copy_routes",
        sa.Column("id", postgresql.UUID(as_uuid=True), nullable=False),
        sa.Column("user_id", postgresql.UUID(as_uuid=True), nullable=False),
        sa.Column("source_id", postgresql.UUID(as_uuid=True), nullable=False),
        sa.Column("target_account_id", postgresql.UUID(as_uuid=True), nullable=False),
        sa.Column("magic_number", sa.BigInteger(), nullable=False),
        sa.Column("state", copy_route_state, server_default="draft", nullable=False),
        sa.Column("paused_from_state", copy_route_state, nullable=True),
        sa.Column("fixed_lot", sa.Numeric(12, 4), nullable=False),
        sa.Column("take_profit_mode", take_profit_mode, server_default="all", nullable=False),
        sa.Column("lot_distribution", lot_distribution, server_default="fixed_each", nullable=False),
        sa.Column("pending_orders_enabled", sa.Boolean(), server_default=sa.true(), nullable=False),
        sa.Column("minimum_fields", minimum_fields, server_default="direction_symbol_sl_tp", nullable=False),
        sa.Column("assembly_window_seconds", sa.Integer(), nullable=True),
        sa.Column("process_all_group_authors", sa.Boolean(), server_default=sa.false(), nullable=False),
        sa.Column("notify_success", sa.Boolean(), server_default=sa.true(), nullable=False),
        sa.Column("notify_failure", sa.Boolean(), server_default=sa.true(), nullable=False),
        sa.Column("allow_sl_tp_updates", sa.Boolean(), server_default=sa.true(), nullable=False),
        sa.Column("allow_break_even", sa.Boolean(), server_default=sa.true(), nullable=False),
        sa.Column("allow_additional_tp", sa.Boolean(), server_default=sa.true(), nullable=False),
        sa.Column("allow_partial_close", sa.Boolean(), server_default=sa.true(), nullable=False),
        sa.Column("allow_full_close", sa.Boolean(), server_default=sa.true(), nullable=False),
        sa.Column("allow_pending_cancel", sa.Boolean(), server_default=sa.true(), nullable=False),
        sa.Column("unsafe_minimum_confirmed_at", sa.DateTime(timezone=True), nullable=True),
        sa.Column("created_at", sa.DateTime(timezone=True), server_default=sa.text("now()"), nullable=False),
        sa.Column("updated_at", sa.DateTime(timezone=True), server_default=sa.text("now()"), nullable=False),
        sa.CheckConstraint("fixed_lot > 0", name="ck_copy_route_fixed_lot_positive"),
        sa.CheckConstraint(
            "assembly_window_seconds IS NULL OR (assembly_window_seconds >= 1 AND assembly_window_seconds <= 600)",
            name="ck_copy_route_assembly_window",
        ),
        sa.ForeignKeyConstraint(["source_id"], ["telegram_sources.id"], ondelete="CASCADE"),
        sa.ForeignKeyConstraint(["target_account_id"], ["trading_accounts.id"], ondelete="CASCADE"),
        sa.ForeignKeyConstraint(["user_id"], ["users.id"], ondelete="CASCADE"),
        sa.PrimaryKeyConstraint("id"),
        sa.UniqueConstraint("magic_number"),
        sa.UniqueConstraint("user_id", "source_id", "target_account_id"),
    )
    op.create_index("ix_copy_routes_source_id", "copy_routes", ["source_id"])
    op.create_index("ix_copy_routes_target_account_id", "copy_routes", ["target_account_id"])
    op.create_index("ix_copy_routes_user_id", "copy_routes", ["user_id"])

    op.create_table(
        "copy_activity_events",
        sa.Column("id", postgresql.UUID(as_uuid=True), nullable=False),
        sa.Column("user_id", postgresql.UUID(as_uuid=True), nullable=False),
        sa.Column("route_id", postgresql.UUID(as_uuid=True), nullable=True),
        sa.Column("source_id", postgresql.UUID(as_uuid=True), nullable=True),
        sa.Column("account_id", postgresql.UUID(as_uuid=True), nullable=True),
        sa.Column("correlation_id", sa.String(length=64), nullable=False),
        sa.Column("action", sa.String(length=100), nullable=False),
        sa.Column("level", copy_activity_level, server_default="info", nullable=False),
        sa.Column("title", sa.String(length=200), nullable=False),
        sa.Column("body", sa.Text(), nullable=True),
        sa.Column("parsed_details", postgresql.JSONB(astext_type=sa.Text()), server_default=sa.text("'{}'::jsonb"), nullable=False),
        sa.Column("broker_details", postgresql.JSONB(astext_type=sa.Text()), server_default=sa.text("'{}'::jsonb"), nullable=False),
        sa.Column("encrypted_raw_message", sa.Text(), nullable=True),
        sa.Column("created_at", sa.DateTime(timezone=True), server_default=sa.text("now()"), nullable=False),
        sa.ForeignKeyConstraint(["account_id"], ["trading_accounts.id"], ondelete="SET NULL"),
        sa.ForeignKeyConstraint(["route_id"], ["copy_routes.id"], ondelete="SET NULL"),
        sa.ForeignKeyConstraint(["source_id"], ["telegram_sources.id"], ondelete="SET NULL"),
        sa.ForeignKeyConstraint(["user_id"], ["users.id"], ondelete="CASCADE"),
        sa.PrimaryKeyConstraint("id"),
    )
    op.create_index("ix_copy_activity_correlation", "copy_activity_events", ["correlation_id"])
    op.create_index("ix_copy_activity_events_user_id", "copy_activity_events", ["user_id"])
    op.create_index("ix_copy_activity_route_created", "copy_activity_events", ["route_id", "created_at"])
    op.create_index("ix_copy_activity_user_created", "copy_activity_events", ["user_id", "created_at"])


def downgrade() -> None:
    op.drop_table("copy_activity_events")
    op.drop_table("copy_routes")
    op.drop_table("copy_account_policies")
    op.drop_table("telegram_sources")
    op.drop_table("telegram_connections")
    op.drop_table("copy_trading_user_settings")

    bind = op.get_bind()
    for enum_type in (
        copy_activity_level,
        minimum_fields,
        lot_distribution,
        take_profit_mode,
        copy_route_state,
        telegram_source_state,
        telegram_source_type,
        telegram_connection_state,
    ):
        enum_type.drop(bind, checkfirst=True)
