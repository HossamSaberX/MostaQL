"""add smart alert filters

Revision ID: a4b7c9d2e5f1
Revises: cffc3bde7727
Create Date: 2026-07-27 00:00:00.000000
"""
from typing import Sequence, Union

from alembic import op
import sqlalchemy as sa


revision: str = "a4b7c9d2e5f1"
down_revision: Union[str, None] = "cffc3bde7727"
branch_labels: Union[str, Sequence[str], None] = None
depends_on: Union[str, Sequence[str], None] = None


def upgrade() -> None:
    op.create_table(
        "client_verification_cache",
        sa.Column("id", sa.Integer(), autoincrement=True, nullable=False),
        sa.Column("profile_url", sa.Text(), nullable=False),
        sa.Column("identity_verified", sa.Boolean(), nullable=True),
        sa.Column("payment_verified", sa.Boolean(), nullable=True),
        sa.Column("checked_at", sa.TIMESTAMP(), nullable=False),
        sa.PrimaryKeyConstraint("id"),
        sa.UniqueConstraint("profile_url"),
    )
    op.create_index(
        "idx_client_verification_profile_url",
        "client_verification_cache",
        ["profile_url"],
        unique=False,
    )
    op.create_index(
        "idx_client_verification_checked_at",
        "client_verification_cache",
        ["checked_at"],
        unique=False,
    )

    with op.batch_alter_table("users") as batch_op:
        batch_op.add_column(
            sa.Column(
                "require_projects_in_progress",
                sa.Boolean(),
                nullable=False,
                server_default=sa.false(),
            )
        )
        batch_op.add_column(
            sa.Column(
                "require_ongoing_communications",
                sa.Boolean(),
                nullable=False,
                server_default=sa.false(),
            )
        )
        batch_op.add_column(sa.Column("min_budget_usd", sa.Float(), nullable=True))
        batch_op.add_column(
            sa.Column(
                "require_verified_client",
                sa.Boolean(),
                nullable=False,
                server_default=sa.false(),
            )
        )
        batch_op.add_column(
            sa.Column("max_project_age_minutes", sa.Integer(), nullable=True)
        )

    with op.batch_alter_table("jobs") as batch_op:
        batch_op.add_column(sa.Column("budget_min_usd", sa.Float(), nullable=True))
        batch_op.add_column(sa.Column("budget_max_usd", sa.Float(), nullable=True))
        batch_op.add_column(sa.Column("published_at", sa.TIMESTAMP(), nullable=True))
        batch_op.add_column(sa.Column("projects_in_progress", sa.Integer(), nullable=True))
        batch_op.add_column(sa.Column("ongoing_communications", sa.Integer(), nullable=True))
        batch_op.add_column(sa.Column("client_profile_url", sa.Text(), nullable=True))
        batch_op.add_column(sa.Column("client_identity_verified", sa.Boolean(), nullable=True))
        batch_op.add_column(sa.Column("client_payment_verified", sa.Boolean(), nullable=True))
        batch_op.add_column(
            sa.Column("client_verification_checked_at", sa.TIMESTAMP(), nullable=True)
        )
        batch_op.create_index("idx_jobs_published_at", ["published_at"], unique=False)
        batch_op.create_index(
            "idx_jobs_client_profile_url", ["client_profile_url"], unique=False
        )


def downgrade() -> None:
    with op.batch_alter_table("jobs") as batch_op:
        batch_op.drop_index("idx_jobs_client_profile_url")
        batch_op.drop_index("idx_jobs_published_at")
        batch_op.drop_column("client_verification_checked_at")
        batch_op.drop_column("client_payment_verified")
        batch_op.drop_column("client_identity_verified")
        batch_op.drop_column("client_profile_url")
        batch_op.drop_column("ongoing_communications")
        batch_op.drop_column("projects_in_progress")
        batch_op.drop_column("published_at")
        batch_op.drop_column("budget_max_usd")
        batch_op.drop_column("budget_min_usd")

    with op.batch_alter_table("users") as batch_op:
        batch_op.drop_column("max_project_age_minutes")
        batch_op.drop_column("require_verified_client")
        batch_op.drop_column("min_budget_usd")
        batch_op.drop_column("require_ongoing_communications")
        batch_op.drop_column("require_projects_in_progress")

    op.drop_index(
        "idx_client_verification_checked_at",
        table_name="client_verification_cache",
    )
    op.drop_index(
        "idx_client_verification_profile_url",
        table_name="client_verification_cache",
    )
    op.drop_table("client_verification_cache")
