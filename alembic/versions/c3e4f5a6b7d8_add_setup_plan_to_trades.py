"""add_setup_plan_to_trades

Adds `setup` (playbook setup name) and `plan_followed` to the trades table.
Populated by the demo-data generator; nullable for synced/real trades.

Revision ID: c3e4f5a6b7d8
Revises: b2d3f4a5c6e7
Create Date: 2026-06-16 00:00:00.000000

"""
from typing import Sequence, Union

from alembic import op
import sqlalchemy as sa


revision: str = "c3e4f5a6b7d8"
down_revision: Union[str, Sequence[str], None] = "b2d3f4a5c6e7"
branch_labels: Union[str, Sequence[str], None] = None
depends_on: Union[str, Sequence[str], None] = None


def upgrade() -> None:
    bind = op.get_bind()
    inspector = sa.inspect(bind)
    existing = {col["name"] for col in inspector.get_columns("trades")}

    if "setup" not in existing:
        op.add_column("trades", sa.Column("setup", sa.String(length=64), nullable=True))
    if "plan_followed" not in existing:
        op.add_column("trades", sa.Column("plan_followed", sa.Boolean(), nullable=True))


def downgrade() -> None:
    bind = op.get_bind()
    inspector = sa.inspect(bind)
    existing = {col["name"] for col in inspector.get_columns("trades")}

    if "plan_followed" in existing:
        op.drop_column("trades", "plan_followed")
    if "setup" in existing:
        op.drop_column("trades", "setup")
