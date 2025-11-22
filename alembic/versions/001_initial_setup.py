"""initial_schema_with_telegram

Revision ID: 001_initial_setup
Revises: 
Create Date: 2025-11-22 12:00:00.000000

"""
from typing import Sequence, Union

from alembic import op
import sqlalchemy as sa
from sqlalchemy.engine.reflection import Inspector


# revision identifiers, used by Alembic.
revision: str = '001_initial_setup'
down_revision: Union[str, None] = None
branch_labels: Union[str, Sequence[str], None] = None
depends_on: Union[str, Sequence[str], None] = None


def upgrade() -> None:
    # Bind connection to get inspector
    conn = op.get_bind()
    inspector = Inspector.from_engine(conn)
    tables = inspector.get_table_names()

    # 1. Create tables if they don't exist
    if 'categories' not in tables:
        op.create_table('categories',
            sa.Column('id', sa.Integer(), autoincrement=True, nullable=False),
            sa.Column('name', sa.String(length=255), nullable=False),
            sa.Column('mostaql_url', sa.Text(), nullable=False),
            sa.Column('last_scraped_at', sa.TIMESTAMP(), nullable=True),
            sa.Column('scrape_failures', sa.Integer(), nullable=True),
            sa.PrimaryKeyConstraint('id')
        )

    if 'users' not in tables:
        op.create_table('users',
            sa.Column('id', sa.Integer(), autoincrement=True, nullable=False),
            sa.Column('email', sa.String(length=255), nullable=False),
            sa.Column('verified', sa.Boolean(), nullable=True),
            sa.Column('token', sa.String(length=255), nullable=False),
            sa.Column('created_at', sa.TIMESTAMP(), nullable=True),
            sa.Column('token_issued_at', sa.TIMESTAMP(), nullable=True),
            sa.Column('unsubscribed', sa.Boolean(), nullable=True),
            sa.Column('telegram_chat_id', sa.String(length=64), nullable=True),
            sa.Column('last_notified_at', sa.TIMESTAMP(), nullable=True),
            sa.PrimaryKeyConstraint('id'),
            sa.UniqueConstraint('email'),
            sa.UniqueConstraint('token'),
            sa.UniqueConstraint('telegram_chat_id')
        )
        op.create_index('idx_users_token', 'users', ['token'], unique=False)
        op.create_index('idx_users_verified', 'users', ['verified', 'unsubscribed'], unique=False)
    else:
        # If users table exists, check for telegram_chat_id column
        columns = [c['name'] for c in inspector.get_columns('users')]
        if 'telegram_chat_id' not in columns:
            with op.batch_alter_table('users', schema=None) as batch_op:
                batch_op.add_column(sa.Column('telegram_chat_id', sa.String(length=64), nullable=True))
                batch_op.create_unique_constraint('uq_users_telegram_chat_id', ['telegram_chat_id'])

    if 'jobs' not in tables:
        op.create_table('jobs',
            sa.Column('id', sa.Integer(), autoincrement=True, nullable=False),
            sa.Column('title', sa.Text(), nullable=False),
            sa.Column('url', sa.Text(), nullable=False),
            sa.Column('content_hash', sa.String(length=64), nullable=False),
            sa.Column('category_id', sa.Integer(), nullable=False),
            sa.Column('scraped_at', sa.TIMESTAMP(), nullable=True),
            sa.ForeignKeyConstraint(['category_id'], ['categories.id'], ),
            sa.PrimaryKeyConstraint('id'),
            sa.UniqueConstraint('url')
        )
        op.create_index('idx_jobs_category', 'jobs', ['category_id', 'scraped_at'], unique=False)
        op.create_index('idx_jobs_hash', 'jobs', ['content_hash'], unique=False)

    if 'notifications' not in tables:
        op.create_table('notifications',
            sa.Column('id', sa.Integer(), autoincrement=True, nullable=False),
            sa.Column('user_id', sa.Integer(), nullable=False),
            sa.Column('job_id', sa.Integer(), nullable=False),
            sa.Column('sent_at', sa.TIMESTAMP(), nullable=True),
            sa.Column('status', sa.String(length=20), nullable=True),
            sa.Column('error_message', sa.Text(), nullable=True),
            sa.ForeignKeyConstraint(['job_id'], ['jobs.id'], ondelete='CASCADE'),
            sa.ForeignKeyConstraint(['user_id'], ['users.id'], ondelete='CASCADE'),
            sa.PrimaryKeyConstraint('id')
        )
        op.create_index('idx_notifications_status', 'notifications', ['status', 'sent_at'], unique=False)

    if 'scraper_logs' not in tables:
        op.create_table('scraper_logs',
            sa.Column('id', sa.Integer(), autoincrement=True, nullable=False),
            sa.Column('category_id', sa.Integer(), nullable=True),
            sa.Column('status', sa.String(length=20), nullable=False),
            sa.Column('jobs_found', sa.Integer(), nullable=True),
            sa.Column('duration_seconds', sa.Float(), nullable=True),
            sa.Column('error_message', sa.Text(), nullable=True),
            sa.Column('scraped_at', sa.TIMESTAMP(), nullable=True),
            sa.ForeignKeyConstraint(['category_id'], ['categories.id'], ),
            sa.PrimaryKeyConstraint('id')
        )

    if 'user_categories' not in tables:
        op.create_table('user_categories',
            sa.Column('user_id', sa.Integer(), nullable=False),
            sa.Column('category_id', sa.Integer(), nullable=False),
            sa.Column('created_at', sa.TIMESTAMP(), nullable=True),
            sa.ForeignKeyConstraint(['category_id'], ['categories.id'], ondelete='CASCADE'),
            sa.ForeignKeyConstraint(['user_id'], ['users.id'], ondelete='CASCADE'),
            sa.PrimaryKeyConstraint('user_id', 'category_id')
        )
        op.create_index('idx_user_categories_user', 'user_categories', ['user_id'], unique=False)


def downgrade() -> None:
    # We generally don't need to implement strict downgrades for this initial hybrid migration
    # But standard practice would be to drop tables.
    # For safety in this "patching" context, we'll leave it empty or just drop the column if we knew we added it.
    pass

