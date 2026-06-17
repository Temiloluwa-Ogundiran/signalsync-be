"""add_user_display_preferences

Adds `display_timezone` (nullable IANA tz string) to `users` — the preferred
timezone for rendering timestamps in the UI. Null = no preference (fall back to
account/browser tz).

Currency is intentionally NOT a user preference: monetary amounts are displayed
in each broker account's own currency (TradingAccount.currency).

Revision ID: e5f6a7b8c9d0
Revises: d4e5f6a7b8c9
Create Date: 2026-06-17 00:00:00.000000

"""

from typing import Sequence, Union

import sqlalchemy as sa
from alembic import op

revision: str = "e5f6a7b8c9d0"
down_revision: Union[str, Sequence[str], None] = "d4e5f6a7b8c9"
branch_labels: Union[str, Sequence[str], None] = None
depends_on: Union[str, Sequence[str], None] = None


def upgrade() -> None:
    bind = op.get_bind()
    inspector = sa.inspect(bind)
    columns = {c["name"] for c in inspector.get_columns("users")}

    if "display_timezone" not in columns:
        op.add_column(
            "users",
            sa.Column("display_timezone", sa.String(length=64), nullable=True),
        )


def downgrade() -> None:
    bind = op.get_bind()
    inspector = sa.inspect(bind)
    columns = {c["name"] for c in inspector.get_columns("users")}

    if "display_timezone" in columns:
        op.drop_column("users", "display_timezone")
