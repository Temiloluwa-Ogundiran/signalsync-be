"""enforce global Telegram account ownership

Revision ID: c5e6f7a8b9c1
Revises: b3d5f7a9c1e3
Create Date: 2026-08-20 00:00:00.000000
"""

from typing import Sequence, Union

from alembic import op
import sqlalchemy as sa


revision: str = "c5e6f7a8b9c1"
down_revision: Union[str, Sequence[str], None] = "b3d5f7a9c1e3"
branch_labels: Union[str, Sequence[str], None] = None
depends_on: Union[str, Sequence[str], None] = None


def upgrade() -> None:
    # Do not silently choose an owner if historical data already contains the
    # same Telegram identity under multiple users. Resolve that data explicitly
    # before enabling the global ownership invariant.
    op.execute(
        sa.text(
            """
            DO $$
            BEGIN
                IF EXISTS (
                    SELECT 1
                    FROM telegram_connections
                    WHERE telegram_user_id IS NOT NULL
                    GROUP BY telegram_user_id
                    HAVING COUNT(*) > 1
                ) THEN
                    RAISE EXCEPTION
                        'Cannot enforce global Telegram ownership: duplicate Telegram identities exist';
                END IF;
            END
            $$;
            """
        )
    )
    op.drop_constraint(
        "telegram_connections_user_id_telegram_user_id_key",
        "telegram_connections",
        type_="unique",
    )
    op.create_unique_constraint(
        "uq_telegram_connections_telegram_user_id",
        "telegram_connections",
        ["telegram_user_id"],
    )


def downgrade() -> None:
    op.drop_constraint(
        "uq_telegram_connections_telegram_user_id",
        "telegram_connections",
        type_="unique",
    )
    op.create_unique_constraint(
        "uq_telegram_connections_user_id_telegram_user_id",
        "telegram_connections",
        ["user_id", "telegram_user_id"],
    )
