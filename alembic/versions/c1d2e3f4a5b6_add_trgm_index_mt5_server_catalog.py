"""add pg_trgm GIN indexes for mt5_server_catalog search

Speeds up the server search, which uses ILIKE '%query%' on
canonical_server_name and normalized_server_key. A plain B-tree cannot serve a
leading-wildcard ILIKE, so those scans were sequential. Trigram GIN indexes
make them index-accelerated.

Revision ID: c1d2e3f4a5b6
Revises: b8c9d0e1f2a3
Create Date: 2026-06-19 00:00:00.000000

"""

from typing import Sequence, Union

from alembic import op

revision: str = "c1d2e3f4a5b6"
down_revision: Union[str, Sequence[str], None] = "b8c9d0e1f2a3"
branch_labels: Union[str, Sequence[str], None] = None
depends_on: Union[str, Sequence[str], None] = None


def upgrade() -> None:
    op.execute("CREATE EXTENSION IF NOT EXISTS pg_trgm")
    op.execute(
        "CREATE INDEX IF NOT EXISTS ix_mt5_server_catalog_name_trgm "
        "ON mt5_server_catalog USING gin (canonical_server_name gin_trgm_ops)"
    )
    op.execute(
        "CREATE INDEX IF NOT EXISTS ix_mt5_server_catalog_normkey_trgm "
        "ON mt5_server_catalog USING gin (normalized_server_key gin_trgm_ops)"
    )


def downgrade() -> None:
    op.execute("DROP INDEX IF EXISTS ix_mt5_server_catalog_normkey_trgm")
    op.execute("DROP INDEX IF EXISTS ix_mt5_server_catalog_name_trgm")
    # Leave the pg_trgm extension in place; other objects may depend on it.
