"""add_banned_to_memberstatusenum

Revision ID: 8c18baa4fb20
Revises: 5039ce94f657
Create Date: 2026-03-06 19:03:28.864057

"""
from typing import Sequence, Union

from alembic import op
import sqlalchemy as sa


# revision identifiers, used by Alembic.
revision: str = '8c18baa4fb20'
down_revision: Union[str, Sequence[str], None] = '5039ce94f657'
branch_labels: Union[str, Sequence[str], None] = None
depends_on: Union[str, Sequence[str], None] = None


def upgrade() -> None:
    # PostgreSQL allows adding a new value to an existing native enum type
    # without recreating it. The IF NOT EXISTS guard makes reruns safe.
    op.execute("ALTER TYPE memberstatusenum ADD VALUE IF NOT EXISTS 'banned'")


def downgrade() -> None:
    # Postgres does not support removing values from an enum type.
    # To truly roll back, recreate the type without 'banned' and migrate
    # any rows using it first. For now we leave this as a no-op.
    pass
