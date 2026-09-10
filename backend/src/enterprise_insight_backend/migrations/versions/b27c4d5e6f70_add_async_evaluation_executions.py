"""add durable async evaluation executions

Revision ID: b27c4d5e6f70
Revises: a91f6e0b7c24
"""

from collections.abc import Sequence

import sqlalchemy as sa
from alembic import op

revision: str = "b27c4d5e6f70"
down_revision: str | Sequence[str] | None = "a91f6e0b7c24"
branch_labels: str | Sequence[str] | None = None
depends_on: str | Sequence[str] | None = None


def upgrade() -> None:
    op.create_table(
        "evaluation_executions",
        sa.Column("id", sa.String(length=36), nullable=False),
        sa.Column("project_id", sa.String(length=36), nullable=False),
        sa.Column("suite_id", sa.String(length=36), nullable=False),
        sa.Column("evaluation_run_id", sa.String(length=36), nullable=True),
        sa.Column("model_profile_id", sa.String(length=36), nullable=True),
        sa.Column("label", sa.String(length=300), nullable=True),
        sa.Column("status", sa.String(length=32), nullable=False),
        sa.Column("total_cases", sa.Integer(), nullable=False),
        sa.Column("completed_cases", sa.Integer(), nullable=False),
        sa.Column("agent_run_ids", sa.JSON(), nullable=False),
        sa.Column("error", sa.JSON(), nullable=True),
        sa.Column("created_at", sa.DateTime(timezone=True), nullable=False),
        sa.Column("updated_at", sa.DateTime(timezone=True), nullable=False),
        sa.ForeignKeyConstraint(["evaluation_run_id"], ["evaluation_runs.id"], ondelete="SET NULL"),
        sa.ForeignKeyConstraint(["model_profile_id"], ["model_profiles.id"], ondelete="SET NULL"),
        sa.ForeignKeyConstraint(["project_id"], ["projects.id"], ondelete="CASCADE"),
        sa.ForeignKeyConstraint(["suite_id"], ["evaluation_suites.id"], ondelete="CASCADE"),
        sa.PrimaryKeyConstraint("id"),
    )
    for column in (
        "project_id",
        "suite_id",
        "evaluation_run_id",
        "model_profile_id",
        "status",
    ):
        op.create_index(
            f"ix_evaluation_executions_{column}",
            "evaluation_executions",
            [column],
            unique=False,
        )


def downgrade() -> None:
    op.drop_table("evaluation_executions")
