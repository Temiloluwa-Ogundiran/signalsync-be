"""widen_trade_symbol_to_255

Revision ID: f9a0b1c2d3e4
Revises: f8a9b0c1d2e3
Create Date: 2026-06-21 09:00:00.000000
"""
from typing import Sequence, Union

import sqlalchemy as sa
from alembic import op

revision: str = "f9a0b1c2d3e4"
down_revision: Union[str, Sequence[str], None] = "f8a9b0c1d2e3"
branch_labels: Union[str, Sequence[str], None] = None
depends_on: Union[str, Sequence[str], None] = None


def upgrade() -> None:
    op.alter_column(
        "trades",
        "symbol",
        existing_type=sa.String(length=20),
        type_=sa.String(length=255),
        existing_nullable=False,
    )


def downgrade() -> None:
    op.alter_column(
        "trades",
        "symbol",
        existing_type=sa.String(length=255),
        type_=sa.String(length=20),
        existing_nullable=False,
    )
