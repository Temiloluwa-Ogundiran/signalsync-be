"""rebuild tags into groups/tags/trade_tags

Drops the legacy tag_categories / tag_options / trade_tag_selections tables and
creates the new tag_groups / tags / trade_tags schema with position ordering.

Revision ID: f7a8b9c0d1e2
Revises: e5f6a7b8c9d0
Create Date: 2026-06-17 12:00:00.000000

"""
from typing import Sequence, Union

from alembic import op
import sqlalchemy as sa

# revision identifiers, used by Alembic.
revision: str = 'f7a8b9c0d1e2'
down_revision: Union[str, Sequence[str], None] = 'e5f6a7b8c9d0'
branch_labels: Union[str, Sequence[str], None] = None
depends_on: Union[str, Sequence[str], None] = None


def upgrade() -> None:
    """Upgrade schema."""
    bind = op.get_bind()
    inspector = sa.inspect(bind)
    existing_tables = set(inspector.get_table_names())

    # --- Drop legacy tag tables (children first) ---
    if 'trade_tag_selections' in existing_tables:
        op.drop_table('trade_tag_selections')
    if 'tag_options' in existing_tables:
        op.drop_table('tag_options')
    if 'tag_categories' in existing_tables:
        op.drop_table('tag_categories')

    # --- Create new schema ---
    if 'tag_groups' not in existing_tables:
        op.create_table(
            'tag_groups',
            sa.Column('id', sa.UUID(), nullable=False),
            sa.Column('user_id', sa.UUID(), nullable=True),
            sa.Column('name', sa.String(length=100), nullable=False),
            sa.Column('position', sa.Integer(), nullable=False),
            sa.Column('is_system', sa.Boolean(), nullable=False),
            sa.Column('created_at', sa.DateTime(timezone=True), nullable=False),
            sa.Column('updated_at', sa.DateTime(timezone=True), nullable=False),
            sa.ForeignKeyConstraint(['user_id'], ['users.id'], ondelete='CASCADE'),
            sa.PrimaryKeyConstraint('id'),
        )
        op.create_index(op.f('ix_tag_groups_user_id'), 'tag_groups', ['user_id'], unique=False)

    if 'tags' not in existing_tables:
        op.create_table(
            'tags',
            sa.Column('id', sa.UUID(), nullable=False),
            sa.Column('group_id', sa.UUID(), nullable=False),
            sa.Column('user_id', sa.UUID(), nullable=True),
            sa.Column('name', sa.String(length=100), nullable=False),
            sa.Column('color', sa.String(length=7), nullable=True),
            sa.Column('position', sa.Integer(), nullable=False),
            sa.Column('is_system', sa.Boolean(), nullable=False),
            sa.Column('created_at', sa.DateTime(timezone=True), nullable=False),
            sa.Column('updated_at', sa.DateTime(timezone=True), nullable=False),
            sa.ForeignKeyConstraint(['group_id'], ['tag_groups.id'], ondelete='CASCADE'),
            sa.ForeignKeyConstraint(['user_id'], ['users.id'], ondelete='CASCADE'),
            sa.PrimaryKeyConstraint('id'),
        )
        op.create_index(op.f('ix_tags_group_id'), 'tags', ['group_id'], unique=False)
        op.create_index(op.f('ix_tags_user_id'), 'tags', ['user_id'], unique=False)

    if 'trade_tags' not in existing_tables:
        op.create_table(
            'trade_tags',
            sa.Column('trade_id', sa.UUID(), nullable=False),
            sa.Column('tag_id', sa.UUID(), nullable=False),
            sa.ForeignKeyConstraint(['tag_id'], ['tags.id'], ondelete='CASCADE'),
            sa.ForeignKeyConstraint(['trade_id'], ['trades.id'], ondelete='CASCADE'),
            sa.PrimaryKeyConstraint('trade_id', 'tag_id'),
        )


def downgrade() -> None:
    """Downgrade schema."""
    op.drop_table('trade_tags')
    op.drop_index(op.f('ix_tags_user_id'), table_name='tags')
    op.drop_index(op.f('ix_tags_group_id'), table_name='tags')
    op.drop_table('tags')
    op.drop_index(op.f('ix_tag_groups_user_id'), table_name='tag_groups')
    op.drop_table('tag_groups')

    # Recreate legacy tables (empty) for a clean rollback.
    op.create_table(
        'tag_categories',
        sa.Column('id', sa.UUID(), nullable=False),
        sa.Column('user_id', sa.UUID(), nullable=True),
        sa.Column('title', sa.String(length=100), nullable=False),
        sa.Column('is_system', sa.Boolean(), nullable=False),
        sa.Column('created_at', sa.DateTime(timezone=True), nullable=False),
        sa.Column('updated_at', sa.DateTime(timezone=True), nullable=False),
        sa.ForeignKeyConstraint(['user_id'], ['users.id'], ondelete='CASCADE'),
        sa.PrimaryKeyConstraint('id'),
    )
    op.create_index(op.f('ix_tag_categories_user_id'), 'tag_categories', ['user_id'], unique=False)
    op.create_table(
        'tag_options',
        sa.Column('id', sa.UUID(), nullable=False),
        sa.Column('category_id', sa.UUID(), nullable=False),
        sa.Column('user_id', sa.UUID(), nullable=True),
        sa.Column('value', sa.String(length=100), nullable=False),
        sa.Column('color', sa.String(length=7), nullable=True),
        sa.Column('created_at', sa.DateTime(timezone=True), nullable=False),
        sa.Column('updated_at', sa.DateTime(timezone=True), nullable=False),
        sa.ForeignKeyConstraint(['category_id'], ['tag_categories.id'], ondelete='CASCADE'),
        sa.ForeignKeyConstraint(['user_id'], ['users.id'], ondelete='CASCADE'),
        sa.PrimaryKeyConstraint('id'),
    )
    op.create_index(op.f('ix_tag_options_category_id'), 'tag_options', ['category_id'], unique=False)
    op.create_index(op.f('ix_tag_options_user_id'), 'tag_options', ['user_id'], unique=False)
    op.create_table(
        'trade_tag_selections',
        sa.Column('trade_id', sa.UUID(), nullable=False),
        sa.Column('option_id', sa.UUID(), nullable=False),
        sa.ForeignKeyConstraint(['option_id'], ['tag_options.id'], ondelete='CASCADE'),
        sa.ForeignKeyConstraint(['trade_id'], ['trades.id'], ondelete='CASCADE'),
        sa.PrimaryKeyConstraint('trade_id', 'option_id'),
    )
