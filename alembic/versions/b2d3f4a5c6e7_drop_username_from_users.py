"""drop_username_from_users

Removes the `username` column (and its unique index) from `users`. Usernames
are no longer collected at sign-up or surfaced anywhere — `display_name` is the
user's display identity (in posts and stream member lists). Login has always
been by email, so dropping username does not affect authentication.

Revision ID: b2d3f4a5c6e7
Revises: a1c2e3d4f5b6
Create Date: 2026-06-16 00:00:00.000000

"""

from typing import Sequence, Union

import sqlalchemy as sa
from alembic import op

revision: str = "b2d3f4a5c6e7"
down_revision: Union[str, Sequence[str], None] = "a1c2e3d4f5b6"
branch_labels: Union[str, Sequence[str], None] = None
depends_on: Union[str, Sequence[str], None] = None


def upgrade() -> None:
    bind = op.get_bind()
    inspector = sa.inspect(bind)
    columns = {c["name"] for c in inspector.get_columns("users")}
    indexes = {i["name"] for i in inspector.get_indexes("users")}

    if "ix_users_username" in indexes:
        op.drop_index(op.f("ix_users_username"), table_name="users")
    if "username" in columns:
        op.drop_column("users", "username")


def downgrade() -> None:
    # Recreate the column nullable first so existing rows don't violate NOT NULL,
    # backfill from email (guaranteed unique), then enforce the original
    # NOT NULL + unique index.
    op.add_column(
        "users",
        sa.Column("username", sa.String(length=50), nullable=True),
    )
    op.execute("UPDATE users SET username = LEFT(email, 50) WHERE username IS NULL")
    op.alter_column("users", "username", nullable=False)
    op.create_index(op.f("ix_users_username"), "users", ["username"], unique=True)
