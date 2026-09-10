"""add typed semantic datasets and query run audit

Revision ID: 1b72d9a4f053
Revises: 0a61c8f3e942
Create Date: 2026-09-07 07:00:00.000000
"""

from collections.abc import Sequence

import sqlalchemy as sa
from alembic import op

revision: str = "1b72d9a4f053"
down_revision: str | Sequence[str] | None = "0a61c8f3e942"
branch_labels: str | Sequence[str] | None = None
depends_on: str | Sequence[str] | None = None


def upgrade() -> None:
    op.create_table(
        "semantic_datasets",
        sa.Column("id", sa.String(length=36), nullable=False),
        sa.Column("project_id", sa.String(length=36), nullable=False),
        sa.Column("key", sa.String(length=100), nullable=False),
        sa.Column("name", sa.String(length=300), nullable=False),
        sa.Column("description", sa.Text()),
        sa.Column("root_type_key", sa.String(length=64), nullable=False),
        sa.Column("columns", sa.JSON(), nullable=False),
        sa.Column("status", sa.String(length=32), nullable=False),
        sa.Column("revision", sa.Integer(), nullable=False),
        sa.Column("created_at", sa.DateTime(timezone=True), nullable=False),
        sa.Column("updated_at", sa.DateTime(timezone=True), nullable=False),
        sa.ForeignKeyConstraint(["project_id"], ["projects.id"], ondelete="CASCADE"),
        sa.PrimaryKeyConstraint("id"),
        sa.UniqueConstraint("project_id", "key", name="uq_semantic_dataset_key"),
    )
    _indexes("semantic_datasets", ["project_id", "root_type_key", "status"])
    op.create_table(
        "semantic_query_runs",
        sa.Column("id", sa.String(length=36), nullable=False),
        sa.Column("project_id", sa.String(length=36), nullable=False),
        sa.Column("dataset_id", sa.String(length=36), nullable=False),
        sa.Column("query_snapshot_id", sa.String(length=36), nullable=False),
        sa.Column("status", sa.String(length=32), nullable=False),
        sa.Column("row_count", sa.Integer(), nullable=False),
        sa.Column("plan", sa.JSON(), nullable=False),
        sa.Column("warnings", sa.JSON(), nullable=False),
        sa.Column("created_at", sa.DateTime(timezone=True), nullable=False),
        sa.ForeignKeyConstraint(["project_id"], ["projects.id"], ondelete="CASCADE"),
        sa.ForeignKeyConstraint(
            ["dataset_id"], ["semantic_datasets.id"], ondelete="CASCADE"
        ),
        sa.ForeignKeyConstraint(
            ["query_snapshot_id"], ["query_snapshots.id"], ondelete="RESTRICT"
        ),
        sa.PrimaryKeyConstraint("id"),
    )
    _indexes(
        "semantic_query_runs",
        ["project_id", "dataset_id", "query_snapshot_id", "status"],
    )


def downgrade() -> None:
    op.drop_table("semantic_query_runs")
    op.drop_table("semantic_datasets")


def _indexes(table: str, columns: list[str]) -> None:
    with op.batch_alter_table(table, schema=None) as batch_op:
        for column in columns:
            batch_op.create_index(batch_op.f(f"ix_{table}_{column}"), [column])
