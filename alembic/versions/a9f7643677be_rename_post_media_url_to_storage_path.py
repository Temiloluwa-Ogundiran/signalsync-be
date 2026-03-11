"""rename_post_media_url_to_storage_path

Revision ID: a9f7643677be
Revises: 035acc98f376
Create Date: 2026-03-10 14:28:35.818925

"""
from typing import Sequence, Union

from alembic import op
import sqlalchemy as sa


# revision identifiers, used by Alembic.
revision: str = 'a9f7643677be'
down_revision: Union[str, Sequence[str], None] = '035acc98f376'
branch_labels: Union[str, Sequence[str], None] = None
depends_on: Union[str, Sequence[str], None] = None


def upgrade() -> None:
    """Rename post_media.url → post_media.storage_path (zero data loss)."""
    op.alter_column('post_media', 'url', new_column_name='storage_path')


def downgrade() -> None:
    """Rename post_media.storage_path → post_media.url."""
    op.alter_column('post_media', 'storage_path', new_column_name='url')
