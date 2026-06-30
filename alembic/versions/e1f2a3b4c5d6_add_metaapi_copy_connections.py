"""add independent MetaApi copy connections

Revision ID: e1f2a3b4c5d6
Revises: d0e1f2a3b4c5
Create Date: 2026-06-30
"""

import sqlalchemy as sa
from alembic import op
from sqlalchemy.dialects import postgresql


revision = "e1f2a3b4c5d6"
down_revision = "d0e1f2a3b4c5"
branch_labels = None
depends_on = None


connection_states = (
    "submitted",
    "provisioning",
    "deploying",
    "connecting",
    "synchronizing",
    "ready",
    "invalid_credentials",
    "server_not_found",
    "provisioning_failed",
    "broker_disconnected",
    "synchronization_failed",
    "trading_disabled",
    "deleting",
    "deleted",
)
connection_state = postgresql.ENUM(
    *connection_states,
    name="copytradingconnectionstateenum",
    create_type=False,
)


def _drop_constraints_for_column(table: str, column: str, kind: str) -> None:
    inspector = sa.inspect(op.get_bind())
    getter = {
        "foreignkey": inspector.get_foreign_keys,
        "unique": inspector.get_unique_constraints,
    }[kind]
    for constraint in getter(table):
        if column in constraint.get("constrained_columns", constraint.get("column_names", [])):
            op.drop_constraint(constraint["name"], table, type_=kind)


def upgrade() -> None:
    bind = op.get_bind()
    connection_state.create(bind, checkfirst=True)
    op.create_table(
        "copy_trading_connections",
        sa.Column("id", postgresql.UUID(as_uuid=True), primary_key=True),
        sa.Column("user_id", postgresql.UUID(as_uuid=True), nullable=False),
        sa.Column("display_name", sa.String(length=120), nullable=False),
        sa.Column("broker_login", sa.String(length=64), nullable=False),
        sa.Column("broker_server", sa.String(length=160), nullable=False),
        sa.Column("platform", sa.String(length=8), server_default="mt5", nullable=False),
        sa.Column("encrypted_trader_password", sa.Text(), nullable=True),
        sa.Column("metaapi_account_id", sa.String(length=100), nullable=True),
        sa.Column("provisioning_transaction_id", sa.String(length=100), nullable=False),
        sa.Column("state", connection_state, server_default="submitted", nullable=False),
        sa.Column("last_error_code", sa.String(length=100), nullable=True),
        sa.Column("last_error_message", sa.Text(), nullable=True),
        sa.Column("symbol_catalog_fingerprint", sa.String(length=64), nullable=True),
        sa.Column("symbol_catalog_refreshed_at", sa.DateTime(timezone=True), nullable=True),
        sa.Column("last_health_at", sa.DateTime(timezone=True), nullable=True),
        sa.Column("created_at", sa.DateTime(timezone=True), server_default=sa.text("now()"), nullable=False),
        sa.Column("updated_at", sa.DateTime(timezone=True), server_default=sa.text("now()"), nullable=False),
        sa.ForeignKeyConstraint(["user_id"], ["users.id"], ondelete="CASCADE"),
        sa.UniqueConstraint("metaapi_account_id"),
        sa.UniqueConstraint("provisioning_transaction_id"),
        sa.UniqueConstraint("user_id", "broker_login", "broker_server"),
    )
    op.create_index(
        "ix_copy_trading_connections_user_id", "copy_trading_connections", ["user_id"]
    )

    # Legacy routes cannot execute until a user explicitly links a ready MetaApi connection.
    op.execute(
        """
        UPDATE copy_routes
        SET paused_from_state = CASE WHEN state <> 'paused' THEN state ELSE paused_from_state END,
            state = 'paused'
        """
    )

    _drop_constraints_for_column("copy_routes", "target_account_id", "unique")
    op.drop_index("ix_copy_routes_target_account_id", table_name="copy_routes")
    op.alter_column("copy_routes", "target_account_id", new_column_name="legacy_target_account_id")
    op.add_column("copy_routes", sa.Column("target_connection_id", postgresql.UUID(as_uuid=True), nullable=True))
    op.create_foreign_key(
        "fk_copy_routes_target_connection",
        "copy_routes",
        "copy_trading_connections",
        ["target_connection_id"],
        ["id"],
        ondelete="SET NULL",
    )
    op.create_index("ix_copy_routes_target_connection_id", "copy_routes", ["target_connection_id"])
    op.create_unique_constraint(
        "uq_copy_route_connection", "copy_routes", ["user_id", "source_id", "target_connection_id"]
    )

    _drop_constraints_for_column("copy_account_policies", "account_id", "unique")
    op.drop_index("ix_copy_account_policies_account_id", table_name="copy_account_policies")
    op.alter_column("copy_account_policies", "account_id", new_column_name="legacy_account_id")
    op.add_column("copy_account_policies", sa.Column("connection_id", postgresql.UUID(as_uuid=True), nullable=True))
    op.create_foreign_key(
        "fk_copy_account_policy_connection",
        "copy_account_policies",
        "copy_trading_connections",
        ["connection_id"],
        ["id"],
        ondelete="CASCADE",
    )
    op.create_index("ix_copy_account_policies_connection_id", "copy_account_policies", ["connection_id"])
    op.create_unique_constraint(
        "uq_copy_account_policy_connection", "copy_account_policies", ["user_id", "connection_id"]
    )

    op.alter_column("copy_activity_events", "account_id", new_column_name="legacy_account_id")
    op.add_column("copy_activity_events", sa.Column("connection_id", postgresql.UUID(as_uuid=True), nullable=True))
    op.create_foreign_key(
        "fk_copy_activity_connection",
        "copy_activity_events",
        "copy_trading_connections",
        ["connection_id"],
        ["id"],
        ondelete="SET NULL",
    )

    op.drop_index("ix_trade_intent_account_state", table_name="trade_intents")
    op.drop_index("ix_trade_intents_account_id", table_name="trade_intents")
    op.alter_column("trade_intents", "account_id", new_column_name="legacy_account_id")
    op.add_column("trade_intents", sa.Column("connection_id", postgresql.UUID(as_uuid=True), nullable=True))
    op.create_foreign_key(
        "fk_trade_intent_connection",
        "trade_intents",
        "copy_trading_connections",
        ["connection_id"],
        ["id"],
        ondelete="SET NULL",
    )
    op.create_index("ix_trade_intents_connection_id", "trade_intents", ["connection_id"])
    op.create_index("ix_trade_intents_legacy_account_id", "trade_intents", ["legacy_account_id"])
    op.create_index("ix_trade_intent_connection_state", "trade_intents", ["connection_id", "state"])

    op.add_column("copied_trades", sa.Column("connection_id", postgresql.UUID(as_uuid=True), nullable=True))
    op.create_foreign_key(
        "fk_copied_trade_connection",
        "copied_trades",
        "copy_trading_connections",
        ["connection_id"],
        ["id"],
        ondelete="SET NULL",
    )
    op.create_index("ix_copied_trades_connection_id", "copied_trades", ["connection_id"])

    op.execute("DELETE FROM symbol_mappings")
    _drop_constraints_for_column("symbol_mappings", "account_id", "unique")
    _drop_constraints_for_column("symbol_mappings", "account_id", "foreignkey")
    op.drop_index("ix_symbol_mappings_account_id", table_name="symbol_mappings")
    op.drop_column("symbol_mappings", "account_id")
    op.add_column("symbol_mappings", sa.Column("connection_id", postgresql.UUID(as_uuid=True), nullable=False))
    op.create_foreign_key(
        "fk_symbol_mapping_connection",
        "symbol_mappings",
        "copy_trading_connections",
        ["connection_id"],
        ["id"],
        ondelete="CASCADE",
    )
    op.create_index("ix_symbol_mappings_connection_id", "symbol_mappings", ["connection_id"])
    op.create_unique_constraint(
        "uq_symbol_mapping_connection",
        "symbol_mappings",
        ["route_id", "connection_id", "normalized_signal_symbol"],
    )


def downgrade() -> None:
    op.execute("DELETE FROM symbol_mappings")
    op.drop_constraint("uq_symbol_mapping_connection", "symbol_mappings", type_="unique")
    op.drop_index("ix_symbol_mappings_connection_id", table_name="symbol_mappings")
    op.drop_constraint("fk_symbol_mapping_connection", "symbol_mappings", type_="foreignkey")
    op.drop_column("symbol_mappings", "connection_id")
    op.add_column("symbol_mappings", sa.Column("account_id", postgresql.UUID(as_uuid=True), nullable=False))
    op.create_foreign_key(None, "symbol_mappings", "trading_accounts", ["account_id"], ["id"], ondelete="CASCADE")
    op.create_index("ix_symbol_mappings_account_id", "symbol_mappings", ["account_id"])
    op.create_unique_constraint(None, "symbol_mappings", ["route_id", "account_id", "normalized_signal_symbol"])

    op.drop_index("ix_copied_trades_connection_id", table_name="copied_trades")
    op.drop_constraint("fk_copied_trade_connection", "copied_trades", type_="foreignkey")
    op.drop_column("copied_trades", "connection_id")

    op.drop_index("ix_trade_intent_connection_state", table_name="trade_intents")
    op.drop_index("ix_trade_intents_connection_id", table_name="trade_intents")
    op.drop_index("ix_trade_intents_legacy_account_id", table_name="trade_intents")
    op.drop_constraint("fk_trade_intent_connection", "trade_intents", type_="foreignkey")
    op.drop_column("trade_intents", "connection_id")
    op.alter_column("trade_intents", "legacy_account_id", new_column_name="account_id")
    op.create_index("ix_trade_intents_account_id", "trade_intents", ["account_id"])
    op.create_index("ix_trade_intent_account_state", "trade_intents", ["account_id", "state"])

    op.drop_constraint("fk_copy_activity_connection", "copy_activity_events", type_="foreignkey")
    op.drop_column("copy_activity_events", "connection_id")
    op.alter_column("copy_activity_events", "legacy_account_id", new_column_name="account_id")

    op.drop_constraint("uq_copy_account_policy_connection", "copy_account_policies", type_="unique")
    op.drop_index("ix_copy_account_policies_connection_id", table_name="copy_account_policies")
    op.drop_constraint("fk_copy_account_policy_connection", "copy_account_policies", type_="foreignkey")
    op.drop_column("copy_account_policies", "connection_id")
    op.alter_column("copy_account_policies", "legacy_account_id", new_column_name="account_id")
    op.create_index("ix_copy_account_policies_account_id", "copy_account_policies", ["account_id"])
    op.create_unique_constraint(None, "copy_account_policies", ["user_id", "account_id"])

    op.drop_constraint("uq_copy_route_connection", "copy_routes", type_="unique")
    op.drop_index("ix_copy_routes_target_connection_id", table_name="copy_routes")
    op.drop_constraint("fk_copy_routes_target_connection", "copy_routes", type_="foreignkey")
    op.drop_column("copy_routes", "target_connection_id")
    op.alter_column("copy_routes", "legacy_target_account_id", new_column_name="target_account_id")
    op.create_index("ix_copy_routes_target_account_id", "copy_routes", ["target_account_id"])
    op.create_unique_constraint(None, "copy_routes", ["user_id", "source_id", "target_account_id"])

    op.drop_index("ix_copy_trading_connections_user_id", table_name="copy_trading_connections")
    op.drop_table("copy_trading_connections")
    connection_state.drop(op.get_bind(), checkfirst=True)
