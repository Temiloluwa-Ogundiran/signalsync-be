"""drop streams and posts (social features removed)

Removes the social/community tables now that the backend is journaling-only:
post_upvotes, post_media, posts, stream_members, streams.

Revision ID: b2c3d4e5f6a8
Revises: a1b2c3d4e5f7
Create Date: 2026-06-17 14:00:00.000000

"""
from typing import Sequence, Union

from alembic import op
import sqlalchemy as sa
from sqlalchemy.dialects import postgresql

# revision identifiers, used by Alembic.
revision: str = 'b2c3d4e5f6a8'
down_revision: Union[str, Sequence[str], None] = 'a1b2c3d4e5f7'
branch_labels: Union[str, Sequence[str], None] = None
depends_on: Union[str, Sequence[str], None] = None


def upgrade() -> None:
    """Upgrade schema."""
    bind = op.get_bind()
    inspector = sa.inspect(bind)
    existing = set(inspector.get_table_names())

    # Drop children first to respect FK constraints.
    for table in ("post_upvotes", "post_media", "posts", "stream_members", "streams"):
        if table in existing:
            op.drop_table(table)

    # Drop now-orphaned enum types created for these tables (best-effort).
    bind.execute(sa.text("DROP TYPE IF EXISTS postmediatypeenum"))
    bind.execute(sa.text("DROP TYPE IF EXISTS posttypeenum"))
    bind.execute(sa.text("DROP TYPE IF EXISTS streamprivacyenum"))
    bind.execute(sa.text("DROP TYPE IF EXISTS memberstatusenum"))


def downgrade() -> None:
    """Downgrade schema.

    Recreates the social tables empty. Enums are recreated implicitly by the
    column definitions. This is a best-effort rollback for a removed feature.
    """
    op.create_table(
        'streams',
        sa.Column('id', sa.UUID(), nullable=False),
        sa.Column('owner_id', sa.UUID(), nullable=False),
        sa.Column('name', sa.String(length=120), nullable=False),
        sa.Column('description', sa.Text(), nullable=True),
        sa.Column('privacy', postgresql.ENUM('public', 'private', 'paid', name='streamprivacyenum'), nullable=False),
        sa.Column('forum_enabled', sa.Boolean(), nullable=False),
        sa.Column('price', sa.Numeric(precision=10, scale=2), nullable=True),
        sa.Column('avatar_url', sa.String(), nullable=True),
        sa.Column('banner_url', sa.String(), nullable=True),
        sa.Column('tags', postgresql.ARRAY(sa.Text()), nullable=True),
        sa.Column('require_join_approval', sa.Boolean(), nullable=False),
        sa.Column('is_default', sa.Boolean(), nullable=False),
        sa.Column('is_deleted', sa.Boolean(), nullable=False),
        sa.Column('deleted_at', sa.DateTime(timezone=True), nullable=True),
        sa.Column('created_at', sa.DateTime(timezone=True), nullable=False),
        sa.ForeignKeyConstraint(['owner_id'], ['users.id'], ondelete='CASCADE'),
        sa.PrimaryKeyConstraint('id'),
    )
    op.create_table(
        'stream_members',
        sa.Column('user_id', sa.UUID(), nullable=False),
        sa.Column('stream_id', sa.UUID(), nullable=False),
        sa.Column('status', postgresql.ENUM('active', 'pending', 'banned', name='memberstatusenum'), nullable=False),
        sa.Column('joined_at', sa.DateTime(timezone=True), nullable=False),
        sa.ForeignKeyConstraint(['stream_id'], ['streams.id'], ondelete='CASCADE'),
        sa.ForeignKeyConstraint(['user_id'], ['users.id'], ondelete='CASCADE'),
        sa.PrimaryKeyConstraint('user_id', 'stream_id'),
    )
    op.create_table(
        'posts',
        sa.Column('id', sa.UUID(), nullable=False),
        sa.Column('stream_id', sa.UUID(), nullable=False),
        sa.Column('author_id', sa.UUID(), nullable=False),
        sa.Column('parent_id', sa.UUID(), nullable=True),
        sa.Column('post_type', postgresql.ENUM('signal', 'text', 'education', name='posttypeenum'), nullable=False),
        sa.Column('content', sa.Text(), nullable=True),
        sa.Column('created_at', sa.DateTime(timezone=True), nullable=False),
        sa.ForeignKeyConstraint(['stream_id'], ['streams.id'], ondelete='CASCADE'),
        sa.ForeignKeyConstraint(['author_id'], ['users.id'], ondelete='CASCADE'),
        sa.PrimaryKeyConstraint('id'),
    )
    op.create_table(
        'post_media',
        sa.Column('id', sa.UUID(), nullable=False),
        sa.Column('post_id', sa.UUID(), nullable=False),
        sa.Column('media_type', postgresql.ENUM('image', 'video', 'document', name='postmediatypeenum'), nullable=False),
        sa.Column('storage_path', sa.String(), nullable=False),
        sa.Column('created_at', sa.DateTime(timezone=True), nullable=False),
        sa.ForeignKeyConstraint(['post_id'], ['posts.id'], ondelete='CASCADE'),
        sa.PrimaryKeyConstraint('id'),
    )
    op.create_table(
        'post_upvotes',
        sa.Column('post_id', sa.UUID(), nullable=False),
        sa.Column('user_id', sa.UUID(), nullable=False),
        sa.Column('created_at', sa.DateTime(timezone=True), nullable=False),
        sa.ForeignKeyConstraint(['post_id'], ['posts.id'], ondelete='CASCADE'),
        sa.ForeignKeyConstraint(['user_id'], ['users.id'], ondelete='CASCADE'),
        sa.PrimaryKeyConstraint('post_id', 'user_id'),
    )
