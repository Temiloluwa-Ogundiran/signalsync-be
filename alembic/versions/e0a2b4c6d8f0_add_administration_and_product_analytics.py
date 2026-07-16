"""add administration and product analytics

Revision ID: e0a2b4c6d8f0
Revises: d9f1a3c5e7b9
Create Date: 2026-07-16
"""

from alembic import op
import sqlalchemy as sa
from sqlalchemy.dialects import postgresql


revision = "e0a2b4c6d8f0"
down_revision = "d9f1a3c5e7b9"
branch_labels = None
depends_on = None


def upgrade() -> None:
    role = postgresql.ENUM(
        "user", "admin", "technical_admin", "super_admin", name="platformroleenum"
    )
    role.create(op.get_bind(), checkfirst=True)
    op.add_column(
        "users",
        sa.Column(
            "platform_role",
            role,
            nullable=False,
            server_default="user",
        ),
    )
    op.add_column(
        "users",
        sa.Column("is_suspended", sa.Boolean(), nullable=False, server_default=sa.false()),
    )
    op.add_column("users", sa.Column("suspended_at", sa.DateTime(timezone=True)))
    op.add_column("users", sa.Column("suspension_reason", sa.Text()))
    op.create_index("ix_users_platform_role", "users", ["platform_role"])
    op.create_index("ix_users_is_suspended", "users", ["is_suspended"])

    op.create_table(
        "admin_audit_events",
        sa.Column("id", postgresql.UUID(as_uuid=True), primary_key=True),
        sa.Column(
            "actor_user_id",
            postgresql.UUID(as_uuid=True),
            sa.ForeignKey("users.id", ondelete="RESTRICT"),
            nullable=False,
        ),
        sa.Column(
            "target_user_id",
            postgresql.UUID(as_uuid=True),
            sa.ForeignKey("users.id", ondelete="SET NULL"),
        ),
        sa.Column("action", sa.String(64), nullable=False),
        sa.Column("reason", sa.Text()),
        sa.Column("details", postgresql.JSONB(), nullable=False),
        sa.Column("created_at", sa.DateTime(timezone=True), nullable=False),
    )
    op.create_index("ix_admin_audit_created", "admin_audit_events", ["created_at", "id"])
    op.create_index(
        "ix_admin_audit_target_created",
        "admin_audit_events",
        ["target_user_id", "created_at"],
    )

    op.create_table(
        "product_events",
        sa.Column("id", postgresql.UUID(as_uuid=True), primary_key=True),
        sa.Column(
            "user_id",
            postgresql.UUID(as_uuid=True),
            sa.ForeignKey("users.id", ondelete="SET NULL"),
        ),
        sa.Column("session_id", postgresql.UUID(as_uuid=True), nullable=False),
        sa.Column("event_name", sa.String(48), nullable=False),
        sa.Column("path", sa.String(255), nullable=False),
        sa.Column("referrer_host", sa.String(255)),
        sa.Column("metadata", postgresql.JSONB(), nullable=False),
        sa.Column("occurred_at", sa.DateTime(timezone=True), nullable=False),
        sa.Column("created_at", sa.DateTime(timezone=True), nullable=False),
    )
    op.create_index("ix_product_event_created", "product_events", ["created_at", "id"])
    op.create_index(
        "ix_product_event_session_created",
        "product_events",
        ["session_id", "created_at"],
    )
    op.create_index(
        "ix_product_event_user_created", "product_events", ["user_id", "created_at"]
    )
    op.create_index(
        "ix_product_event_name_created", "product_events", ["event_name", "created_at"]
    )


def downgrade() -> None:
    op.drop_table("product_events")
    op.drop_table("admin_audit_events")
    op.drop_index("ix_users_is_suspended", table_name="users")
    op.drop_index("ix_users_platform_role", table_name="users")
    op.drop_column("users", "suspension_reason")
    op.drop_column("users", "suspended_at")
    op.drop_column("users", "is_suspended")
    op.drop_column("users", "platform_role")
    postgresql.ENUM(name="platformroleenum").drop(op.get_bind(), checkfirst=True)
