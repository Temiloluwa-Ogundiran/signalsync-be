"""add_mt5_server_catalog

Revision ID: f6a7b8c9d0e1
Revises: e6f7a8b9c0d1
Create Date: 2026-06-18 00:00:00.000000

"""

from typing import Sequence, Union

import sqlalchemy as sa
from alembic import op

revision: str = "f6a7b8c9d0e1"
down_revision: Union[str, Sequence[str], None] = "e6f7a8b9c0d1"
branch_labels: Union[str, Sequence[str], None] = None
depends_on: Union[str, Sequence[str], None] = None


def upgrade() -> None:
    op.create_table(
        "mt5_server_catalog",
        sa.Column("id", sa.UUID(), nullable=False),
        sa.Column("canonical_server_name", sa.String(length=160), nullable=False),
        sa.Column("normalized_server_key", sa.String(length=160), nullable=False),
        sa.Column("source", sa.String(length=40), nullable=False, server_default="csv"),
        sa.Column("active", sa.Boolean(), nullable=False, server_default=sa.true()),
        sa.Column("created_at", sa.DateTime(timezone=True), nullable=False, server_default=sa.text("now()")),
        sa.Column("updated_at", sa.DateTime(timezone=True), nullable=False, server_default=sa.text("now()")),
        sa.PrimaryKeyConstraint("id"),
        sa.UniqueConstraint("canonical_server_name", name="uq_mt5_server_catalog_name"),
    )
    op.create_index(
        "ix_mt5_server_catalog_normalized_server_key",
        "mt5_server_catalog",
        ["normalized_server_key"],
    )
    op.create_index(
        "ix_mt5_server_catalog_active",
        "mt5_server_catalog",
        ["active"],
    )


def downgrade() -> None:
    op.drop_index("ix_mt5_server_catalog_active", table_name="mt5_server_catalog")
    op.drop_index("ix_mt5_server_catalog_normalized_server_key", table_name="mt5_server_catalog")
    op.drop_table("mt5_server_catalog")
