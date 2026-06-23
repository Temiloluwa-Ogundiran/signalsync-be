"""add copy execution generations

Revision ID: d0e1f2a3b4c5
Revises: c9d8e7f6a5b4
Create Date: 2026-06-23
"""

import sqlalchemy as sa
from alembic import op
from sqlalchemy.dialects import postgresql


revision = "d0e1f2a3b4c5"
down_revision = "c9d8e7f6a5b4"
branch_labels = None
depends_on = None


def upgrade() -> None:
    op.add_column(
        "route_signal_assemblies",
        sa.Column("generation", sa.Integer(), nullable=False, server_default="1"),
    )
    op.add_column(
        "route_signal_assemblies",
        sa.Column("opening_action", sa.String(length=32), nullable=True),
    )
    op.add_column(
        "route_signal_assemblies",
        sa.Column(
            "opening_intent_id",
            postgresql.UUID(as_uuid=True),
            nullable=True,
        ),
    )
    op.add_column(
        "route_signal_assemblies",
        sa.Column("terminal_reason", sa.Text(), nullable=True),
    )
    op.add_column(
        "route_signal_assemblies",
        sa.Column("completed_at", sa.DateTime(timezone=True), nullable=True),
    )
    op.create_check_constraint(
        "ck_route_signal_assembly_generation_positive",
        "route_signal_assemblies",
        "generation > 0",
    )
    op.create_foreign_key(
        "fk_route_signal_assemblies_opening_intent_id",
        "route_signal_assemblies",
        "trade_intents",
        ["opening_intent_id"],
        ["id"],
        ondelete="SET NULL",
    )
    op.alter_column(
        "route_signal_assemblies",
        "generation",
        server_default=None,
    )


def downgrade() -> None:
    op.drop_constraint(
        "fk_route_signal_assemblies_opening_intent_id",
        "route_signal_assemblies",
        type_="foreignkey",
    )
    op.drop_constraint(
        "ck_route_signal_assembly_generation_positive",
        "route_signal_assemblies",
        type_="check",
    )
    op.drop_column("route_signal_assemblies", "completed_at")
    op.drop_column("route_signal_assemblies", "terminal_reason")
    op.drop_column("route_signal_assemblies", "opening_intent_id")
    op.drop_column("route_signal_assemblies", "opening_action")
    op.drop_column("route_signal_assemblies", "generation")
