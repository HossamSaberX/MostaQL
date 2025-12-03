"""add_channel_to_notifications

Revision ID: cffc3bde7727
Revises: 03e7a317d2d7
Create Date: 2025-11-26 02:23:28.727370

"""
from typing import Sequence, Union

from alembic import op
import sqlalchemy as sa


# revision identifiers, used by Alembic.
revision: str = 'cffc3bde7727'
down_revision: Union[str, None] = '03e7a317d2d7'
branch_labels: Union[str, Sequence[str], None] = None
depends_on: Union[str, Sequence[str], None] = None


def upgrade() -> None:
    op.add_column('notifications', sa.Column('channel', sa.String(length=20), nullable=True))
    op.create_index('idx_notifications_channel', 'notifications', ['channel'])


def downgrade() -> None:
    op.drop_index('idx_notifications_channel', table_name='notifications')
    op.drop_column('notifications', 'channel')

