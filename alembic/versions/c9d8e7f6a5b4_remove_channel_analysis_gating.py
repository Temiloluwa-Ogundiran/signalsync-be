"""remove channel analysis gating

Revision ID: c9d8e7f6a5b4
Revises: a0b1c2d3e4f5
Create Date: 2026-06-22
"""

from alembic import op


revision = "c9d8e7f6a5b4"
down_revision = "a0b1c2d3e4f5"
branch_labels = None
depends_on = None


def upgrade() -> None:
    op.execute(
        """
        UPDATE telegram_sources
        SET state = 'ready',
            unsupported_reason = NULL,
            is_paused = FALSE
        WHERE state IN (
            'draft',
            'learning',
            'advisory',
            'failed_retryable',
            'unsupported',
            'unsupported_image_primary'
        )
        """
    )
    op.execute(
        """
        UPDATE copy_routes
        SET state = 'ready'
        WHERE state = 'draft'
        """
    )


def downgrade() -> None:
    pass
