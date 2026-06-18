"""drop is_manual and is_missed from trades

Removes the manual-trades feature columns. All existing trades (including the
previously is_manual=True demo/seeded ones) become plain trades.

Revision ID: b8c9d0e1f2a3
Revises: a7b8c9d0e1f2
Create Date: 2026-06-19 00:30:00.000000
"""

from typing import Sequence, Union

from alembic import op
import sqlalchemy as sa


# revision identifiers, used by Alembic.
revision: str = "b8c9d0e1f2a3"
down_revision: Union[str, Sequence[str], None] = "a7b8c9d0e1f2"
branch_labels: Union[str, Sequence[str], None] = None
depends_on: Union[str, Sequence[str], None] = None


def upgrade() -> None:
    op.drop_column("trades", "is_missed")
    op.drop_column("trades", "is_manual")


def downgrade() -> None:
    op.add_column(
        "trades",
        sa.Column(
            "is_manual",
            sa.Boolean(),
            nullable=False,
            server_default=sa.text("false"),
        ),
    )
    op.add_column(
        "trades",
        sa.Column(
            "is_missed",
            sa.Boolean(),
            nullable=False,
            server_default=sa.text("false"),
        ),
    )
