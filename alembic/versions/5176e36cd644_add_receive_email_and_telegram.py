"""add_receive_email_and_telegram

Revision ID: 5176e36cd644
Revises: 001_initial_setup
Create Date: 2025-11-22 20:42:51.757871

"""
from typing import Sequence, Union

from alembic import op
import sqlalchemy as sa


# revision identifiers, used by Alembic.
revision: str = '5176e36cd644'
down_revision: Union[str, None] = '001_initial_setup'
branch_labels: Union[str, Sequence[str], None] = None
depends_on: Union[str, Sequence[str], None] = None


def upgrade() -> None:
    conn = op.get_bind()
    
    with op.batch_alter_table('users', schema=None) as batch_op:
        batch_op.add_column(sa.Column('receive_email', sa.Boolean(), nullable=True))
        batch_op.add_column(sa.Column('receive_telegram', sa.Boolean(), nullable=True))
    
    conn.execute(sa.text("UPDATE users SET receive_email = 1 WHERE receive_email IS NULL"))
    conn.execute(sa.text("UPDATE users SET receive_telegram = 1 WHERE receive_telegram IS NULL"))


def downgrade() -> None:
    with op.batch_alter_table('users', schema=None) as batch_op:
        batch_op.drop_column('receive_telegram')
        batch_op.drop_column('receive_email')

