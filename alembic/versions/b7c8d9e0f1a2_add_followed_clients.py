"""add followed Mostaql clients

Revision ID: b7c8d9e0f1a2
Revises: a4b7c9d2e5f1
Create Date: 2026-08-16 00:00:00.000000
"""

from typing import Sequence, Union

from alembic import op
import sqlalchemy as sa
from sqlalchemy import inspect


revision: str = "b7c8d9e0f1a2"
down_revision: Union[str, None] = "a4b7c9d2e5f1"
branch_labels: Union[str, Sequence[str], None] = None
depends_on: Union[str, Sequence[str], None] = None


def upgrade() -> None:
    inspector = inspect(op.get_bind())
    if "followed_clients" in inspector.get_table_names():
        return

    op.create_table(
        "followed_clients",
        sa.Column("id", sa.Integer(), autoincrement=True, nullable=False),
        sa.Column("user_id", sa.Integer(), nullable=False),
        sa.Column("profile_url", sa.Text(), nullable=False),
        sa.Column("label", sa.String(length=120), nullable=True),
        sa.Column("created_at", sa.TIMESTAMP(), nullable=True),
        sa.ForeignKeyConstraint(["user_id"], ["users.id"], ondelete="CASCADE"),
        sa.PrimaryKeyConstraint("id"),
        sa.UniqueConstraint("user_id", "profile_url", name="uq_followed_clients_user_profile"),
    )
    op.create_index(
        "idx_followed_clients_user",
        "followed_clients",
        ["user_id"],
        unique=False,
    )
    op.create_index(
        "idx_followed_clients_profile_url",
        "followed_clients",
        ["profile_url"],
        unique=False,
    )


def downgrade() -> None:
    inspector = inspect(op.get_bind())
    if "followed_clients" not in inspector.get_table_names():
        return
    op.drop_index("idx_followed_clients_profile_url", table_name="followed_clients")
    op.drop_index("idx_followed_clients_user", table_name="followed_clients")
    op.drop_table("followed_clients")
