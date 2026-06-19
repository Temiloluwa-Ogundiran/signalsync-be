"""trading_accounts: add is_archived, drop is_deleted, remove disconnected status

Accounts no longer have a soft-delete. Archiving is now a boolean (is_archived);
permanent delete physically removes the row. The old "disconnected" status value
(which doubled as archive) is dropped from the enum, and any such rows become
archived + synced.

Revision ID: d2e3f4a5b6c7
Revises: c1d2e3f4a5b6
Create Date: 2026-06-19 00:00:00.000000

"""

from typing import Sequence, Union

import sqlalchemy as sa
from alembic import op

revision: str = "d2e3f4a5b6c7"
down_revision: Union[str, Sequence[str], None] = "c1d2e3f4a5b6"
branch_labels: Union[str, Sequence[str], None] = None
depends_on: Union[str, Sequence[str], None] = None

ENUM_NAME = "tradingaccountstatusenum"
OLD_VALUES = ("pending_sync", "synced", "error", "disconnected")
NEW_VALUES = ("pending_sync", "synced", "error")


def upgrade() -> None:
    # 1. Add is_archived (default false), backfilling from the old disconnected
    #    status, then drop is_deleted.
    op.add_column(
        "trading_accounts",
        sa.Column(
            "is_archived",
            sa.Boolean(),
            nullable=False,
            server_default=sa.false(),
        ),
    )
    op.execute(
        "UPDATE trading_accounts SET is_archived = true WHERE status = 'disconnected'"
    )
    # Move disconnected rows back to a real status before tightening the enum.
    op.execute(
        "UPDATE trading_accounts SET status = 'synced' WHERE status = 'disconnected'"
    )
    op.drop_column("trading_accounts", "is_deleted")

    # 2. Rebuild the status enum without 'disconnected'.
    op.execute(f"ALTER TYPE {ENUM_NAME} RENAME TO {ENUM_NAME}_old")
    new_enum = sa.Enum(*NEW_VALUES, name=ENUM_NAME)
    new_enum.create(op.get_bind())
    op.execute(
        f"ALTER TABLE trading_accounts ALTER COLUMN status TYPE {ENUM_NAME} "
        f"USING status::text::{ENUM_NAME}"
    )
    op.execute(f"DROP TYPE {ENUM_NAME}_old")


def downgrade() -> None:
    # Restore the enum with 'disconnected'.
    op.execute(f"ALTER TYPE {ENUM_NAME} RENAME TO {ENUM_NAME}_old")
    old_enum = sa.Enum(*OLD_VALUES, name=ENUM_NAME)
    old_enum.create(op.get_bind())
    op.execute(
        f"ALTER TABLE trading_accounts ALTER COLUMN status TYPE {ENUM_NAME} "
        f"USING status::text::{ENUM_NAME}"
    )
    op.execute(f"DROP TYPE {ENUM_NAME}_old")

    # Re-add is_deleted, mapping archived rows back to disconnected + deleted.
    op.add_column(
        "trading_accounts",
        sa.Column(
            "is_deleted",
            sa.Boolean(),
            nullable=False,
            server_default=sa.false(),
        ),
    )
    op.execute(
        "UPDATE trading_accounts SET status = 'disconnected', is_deleted = true "
        "WHERE is_archived = true"
    )
    op.drop_column("trading_accounts", "is_archived")
