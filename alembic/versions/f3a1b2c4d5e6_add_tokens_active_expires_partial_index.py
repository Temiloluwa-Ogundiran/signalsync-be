"""add partial index on tokens.expires_at for active tokens

Revision ID: f3a1b2c4d5e6
Revises: ee637789eda3
Create Date: 2026-06-12 15:10:00.000000

"""
from typing import Sequence, Union

from alembic import op


# revision identifiers, used by Alembic.
revision: str = 'f3a1b2c4d5e6'
down_revision: Union[str, Sequence[str], None] = 'ee637789eda3'
branch_labels: Union[str, Sequence[str], None] = None
depends_on: Union[str, Sequence[str], None] = None


def upgrade() -> None:
    # CREATE INDEX CONCURRENTLY must run outside a transaction block.
    # autocommit_block() ends the surrounding Alembic transaction, executes,
    # then resumes — this is the canonical Alembic pattern for CONCURRENTLY DDL.
    with op.get_context().autocommit_block():
        op.execute(
            "CREATE INDEX CONCURRENTLY IF NOT EXISTS ix_tokens_active_expires_at "
            "ON tokens (expires_at) WHERE is_revoked = false"
        )


def downgrade() -> None:
    with op.get_context().autocommit_block():
        op.execute("DROP INDEX CONCURRENTLY IF EXISTS ix_tokens_active_expires_at")
