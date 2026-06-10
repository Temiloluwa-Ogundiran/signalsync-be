"""compat_legacy_provisioning_revision

Compatibility shim for an older deployed Alembic revision that no longer
exists in the repository. Some persisted environments are stamped at this
revision, so we keep it in the linear history to let `alembic upgrade head`
resume cleanly.

Revision ID: d9b1e7f4c2a8
Revises: 5cd9108794b9
Create Date: 2026-06-10 16:45:00.000000
"""

from typing import Sequence, Union


# revision identifiers, used by Alembic.
revision: str = "d9b1e7f4c2a8"
down_revision: Union[str, Sequence[str], None] = "5cd9108794b9"
branch_labels: Union[str, Sequence[str], None] = None
depends_on: Union[str, Sequence[str], None] = None


def upgrade() -> None:
    """Preserve the legacy revision ID so older databases can rejoin history."""


def downgrade() -> None:
    """No-op downgrade for compatibility shim."""
