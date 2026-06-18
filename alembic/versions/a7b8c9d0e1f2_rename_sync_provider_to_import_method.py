"""rename sync_provider -> import_method and reshape enum

Renames the trading_accounts.sync_provider column to import_method, renames the
enum type syncproviderenum -> importmethodenum, renames value csv_import ->
csv_upload, and adds a new 'manual' value. auto_sync is unchanged.

Revision ID: a7b8c9d0e1f2
Revises: f3a9c1d2e4b7
Create Date: 2026-06-18 23:59:00.000000
"""

from typing import Sequence, Union

from alembic import op


# revision identifiers, used by Alembic.
revision: str = "a7b8c9d0e1f2"
down_revision: Union[str, Sequence[str], None] = "f3a9c1d2e4b7"
branch_labels: Union[str, Sequence[str], None] = None
depends_on: Union[str, Sequence[str], None] = None


def upgrade() -> None:
    # Rename the column.
    op.execute(
        "ALTER TABLE trading_accounts RENAME COLUMN sync_provider TO import_method"
    )

    # Drop the default before reshaping the type.
    op.execute("ALTER TABLE trading_accounts ALTER COLUMN import_method DROP DEFAULT")

    # Build the new enum (csv_import -> csv_upload, add manual).
    op.execute(
        "CREATE TYPE importmethodenum AS ENUM ('auto_sync', 'csv_upload', 'manual')"
    )
    op.execute(
        """
        ALTER TABLE trading_accounts
        ALTER COLUMN import_method TYPE importmethodenum
        USING (
            CASE import_method::text
                WHEN 'csv_import' THEN 'csv_upload'
                ELSE 'auto_sync'
            END
        )::importmethodenum
        """
    )

    # Drop the old type and restore the default on the new one.
    op.execute("DROP TYPE syncproviderenum")
    op.execute(
        "ALTER TABLE trading_accounts ALTER COLUMN import_method SET DEFAULT 'auto_sync'"
    )


def downgrade() -> None:
    op.execute("ALTER TABLE trading_accounts ALTER COLUMN import_method DROP DEFAULT")

    # Recreate the previous enum. 'manual' rows (if any) fall back to auto_sync.
    op.execute("CREATE TYPE syncproviderenum AS ENUM ('auto_sync', 'csv_import')")
    op.execute(
        """
        ALTER TABLE trading_accounts
        ALTER COLUMN import_method TYPE syncproviderenum
        USING (
            CASE import_method::text
                WHEN 'csv_upload' THEN 'csv_import'
                ELSE 'auto_sync'
            END
        )::syncproviderenum
        """
    )

    op.execute("DROP TYPE importmethodenum")
    op.execute(
        "ALTER TABLE trading_accounts RENAME COLUMN import_method TO sync_provider"
    )
    op.execute(
        "ALTER TABLE trading_accounts ALTER COLUMN sync_provider SET DEFAULT 'auto_sync'"
    )
