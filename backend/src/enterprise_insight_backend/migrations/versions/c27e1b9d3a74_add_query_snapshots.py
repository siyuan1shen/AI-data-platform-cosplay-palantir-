"""add immutable query snapshots

Revision ID: c27e1b9d3a74
Revises: b14f0a8c2d63
Create Date: 2026-09-06 20:30:00.000000
"""

from collections.abc import Sequence

import sqlalchemy as sa
from alembic import op

revision: str = "c27e1b9d3a74"
down_revision: str | Sequence[str] | None = "b14f0a8c2d63"
branch_labels: str | Sequence[str] | None = None
depends_on: str | Sequence[str] | None = None


def upgrade() -> None:
    op.create_table(
        "query_snapshots",
        sa.Column("id", sa.String(length=36), nullable=False),
        sa.Column("project_id", sa.String(length=36), nullable=False),
        sa.Column("model_scope", sa.String(length=32), nullable=False),
        sa.Column("publication_id", sa.String(length=36), nullable=True),
        sa.Column("project_revision", sa.Integer(), nullable=False),
        sa.Column("data_cutoff", sa.DateTime(timezone=True), nullable=False),
        sa.Column("graph_snapshot", sa.JSON(), nullable=False),
        sa.Column("manifest", sa.JSON(), nullable=False),
        sa.Column("created_at", sa.DateTime(timezone=True), nullable=False),
        sa.ForeignKeyConstraint(["project_id"], ["projects.id"], ondelete="CASCADE"),
        sa.ForeignKeyConstraint(["publication_id"], ["publications.id"], ondelete="RESTRICT"),
        sa.PrimaryKeyConstraint("id"),
    )
    with op.batch_alter_table("query_snapshots", schema=None) as batch_op:
        batch_op.create_index(batch_op.f("ix_query_snapshots_project_id"), ["project_id"])
        batch_op.create_index(batch_op.f("ix_query_snapshots_model_scope"), ["model_scope"])
        batch_op.create_index(batch_op.f("ix_query_snapshots_publication_id"), ["publication_id"])
        batch_op.create_index(batch_op.f("ix_query_snapshots_data_cutoff"), ["data_cutoff"])


def downgrade() -> None:
    with op.batch_alter_table("query_snapshots", schema=None) as batch_op:
        batch_op.drop_index(batch_op.f("ix_query_snapshots_data_cutoff"))
        batch_op.drop_index(batch_op.f("ix_query_snapshots_publication_id"))
        batch_op.drop_index(batch_op.f("ix_query_snapshots_model_scope"))
        batch_op.drop_index(batch_op.f("ix_query_snapshots_project_id"))
    op.drop_table("query_snapshots")
