"""add alert outbox timestamps, email delivery, word filters and one-time Telegram links

Revision ID: d4e5f6a7b8c9
Revises: b7c8d9e0f1a2
Create Date: 2026-11-15 00:00:00.000000

Alerts are now recorded as `pending` rows and sent in batches. Every `pending` row that
exists today is a leftover of the old send-at-once flow (some even have no channel), so they
are all marked `expired` and never sent. Adding columns is the only schema change;
`telegram_link_nonce` makes each Telegram link work once, and `verify_token` is the code that
only the confirmation email carries.
"""
from typing import Sequence, Union

from alembic import op
import sqlalchemy as sa


revision: str = "d4e5f6a7b8c9"
down_revision: Union[str, None] = "b7c8d9e0f1a2"
branch_labels: Union[str, Sequence[str], None] = None
depends_on: Union[str, Sequence[str], None] = None


def upgrade() -> None:
    with op.batch_alter_table("notifications") as batch_op:
        batch_op.add_column(sa.Column("created_at", sa.TIMESTAMP(), nullable=True))

    with op.batch_alter_table("users") as batch_op:
        batch_op.add_column(
            sa.Column(
                "email_delivery",
                sa.String(length=10),
                nullable=False,
                server_default="instant",
            )
        )
        batch_op.add_column(sa.Column("keywords_include", sa.Text(), nullable=True))
        batch_op.add_column(sa.Column("keywords_exclude", sa.Text(), nullable=True))
        batch_op.add_column(sa.Column("telegram_link_nonce", sa.String(length=32), nullable=True))
        batch_op.add_column(sa.Column("verify_token", sa.String(length=64), nullable=True))
        batch_op.create_index("ix_users_verify_token", ["verify_token"], unique=True)

    op.execute("UPDATE notifications SET status = 'expired' WHERE status = 'pending'")


def downgrade() -> None:
    # Rows expired by the upgrade stay expired: they were never going to be sent.
    with op.batch_alter_table("users") as batch_op:
        batch_op.drop_index("ix_users_verify_token")
        batch_op.drop_column("verify_token")
        batch_op.drop_column("telegram_link_nonce")
        batch_op.drop_column("keywords_exclude")
        batch_op.drop_column("keywords_include")
        batch_op.drop_column("email_delivery")

    with op.batch_alter_table("notifications") as batch_op:
        batch_op.drop_column("created_at")
