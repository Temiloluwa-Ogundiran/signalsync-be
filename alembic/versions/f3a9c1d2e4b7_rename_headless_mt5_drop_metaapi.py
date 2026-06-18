"""rename headless_mt5 -> auto_sync and drop metaapi from syncproviderenum

Recreates syncproviderenum as ('auto_sync', 'csv_import'). Postgres has no
DROP VALUE, so the type is rebuilt: existing 'headless_mt5' and any leftover
'metaapi' rows map to 'auto_sync'; 'csv_import' is preserved.

Revision ID: f3a9c1d2e4b7
Revises: c6d7e8f9a0b1
Create Date: 2026-06-18 23:55:00.000000
"""

from typing import Sequence, Union

from alembic import op


# revision identifiers, used by Alembic.
revision: str = "f3a9c1d2e4b7"
down_revision: Union[str, Sequence[str], None] = "f6a7b8c9d0e1"
branch_labels: Union[str, Sequence[str], None] = None
depends_on: Union[str, Sequence[str], None] = None


def upgrade() -> None:
    # Drop the column default so the type swap isn't blocked by it.
    op.execute("ALTER TABLE trading_accounts ALTER COLUMN sync_provider DROP DEFAULT")

    # Build the new enum without metaapi/headless_mt5.
    op.execute("CREATE TYPE syncproviderenum_new AS ENUM ('auto_sync', 'csv_import')")

    # Swap the column over, mapping old values to the new ones.
    op.execute(
        """
        ALTER TABLE trading_accounts
        ALTER COLUMN sync_provider TYPE syncproviderenum_new
        USING (
            CASE sync_provider::text
                WHEN 'csv_import' THEN 'csv_import'
                ELSE 'auto_sync'
            END
        )::syncproviderenum_new
        """
    )

    # Replace the old type with the new one under the original name.
    op.execute("DROP TYPE syncproviderenum")
    op.execute("ALTER TYPE syncproviderenum_new RENAME TO syncproviderenum")

    # Restore the default on the new type.
    op.execute(
        "ALTER TABLE trading_accounts ALTER COLUMN sync_provider SET DEFAULT 'auto_sync'"
    )


def downgrade() -> None:
    op.execute("ALTER TABLE trading_accounts ALTER COLUMN sync_provider DROP DEFAULT")

    # Recreate the old enum (including the legacy values).
    op.execute(
        "CREATE TYPE syncproviderenum_old AS ENUM ('metaapi', 'headless_mt5', 'csv_import')"
    )

    # auto_sync maps back to headless_mt5 (we cannot tell which rows were metaapi).
    op.execute(
        """
        ALTER TABLE trading_accounts
        ALTER COLUMN sync_provider TYPE syncproviderenum_old
        USING (
            CASE sync_provider::text
                WHEN 'csv_import' THEN 'csv_import'
                ELSE 'headless_mt5'
            END
        )::syncproviderenum_old
        """
    )

    op.execute("DROP TYPE syncproviderenum")
    op.execute("ALTER TYPE syncproviderenum_old RENAME TO syncproviderenum")
    op.execute(
        "ALTER TABLE trading_accounts ALTER COLUMN sync_provider SET DEFAULT 'headless_mt5'"
    )
