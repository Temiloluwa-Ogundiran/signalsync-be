"""add_user_auth_provider_fields

Adds `auth_provider` (email | google) and `has_usable_password` to `users`.

These let the app distinguish how an account signs in: email/password accounts
manage their email and password in-app, while Google accounts manage their email
at the provider and may have no usable password until they explicitly set one.

Existing rows are backfilled to the email/password defaults (auth_provider =
'email', has_usable_password = true) — the safe assumption for any account that
predates Google sign-in tracking, since they all had a password.

Revision ID: d4e5f6a7b8c9
Revises: c3e4f5a6b7d8
Create Date: 2026-06-17 00:00:00.000000

"""

from typing import Sequence, Union

import sqlalchemy as sa
from alembic import op

revision: str = "d4e5f6a7b8c9"
down_revision: Union[str, Sequence[str], None] = "c3e4f5a6b7d8"
branch_labels: Union[str, Sequence[str], None] = None
depends_on: Union[str, Sequence[str], None] = None


AUTH_PROVIDER_ENUM = sa.Enum("email", "google", name="authproviderenum")


def upgrade() -> None:
    bind = op.get_bind()
    inspector = sa.inspect(bind)
    columns = {c["name"] for c in inspector.get_columns("users")}

    # Create the enum type if it does not already exist (idempotent).
    AUTH_PROVIDER_ENUM.create(bind, checkfirst=True)

    if "auth_provider" not in columns:
        op.add_column(
            "users",
            sa.Column(
                "auth_provider",
                AUTH_PROVIDER_ENUM,
                nullable=False,
                server_default="email",
            ),
        )

    if "has_usable_password" not in columns:
        op.add_column(
            "users",
            sa.Column(
                "has_usable_password",
                sa.Boolean(),
                nullable=False,
                server_default=sa.true(),
            ),
        )


def downgrade() -> None:
    bind = op.get_bind()
    inspector = sa.inspect(bind)
    columns = {c["name"] for c in inspector.get_columns("users")}

    if "has_usable_password" in columns:
        op.drop_column("users", "has_usable_password")
    if "auth_provider" in columns:
        op.drop_column("users", "auth_provider")

    AUTH_PROVIDER_ENUM.drop(bind, checkfirst=True)
