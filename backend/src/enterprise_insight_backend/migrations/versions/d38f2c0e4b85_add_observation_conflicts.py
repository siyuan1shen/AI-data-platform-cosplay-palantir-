"""add persistent observation conflict decisions

Revision ID: d38f2c0e4b85
Revises: c27e1b9d3a74
Create Date: 2026-09-07 03:40:00.000000
"""

from collections.abc import Sequence

import sqlalchemy as sa
from alembic import op

revision: str = "d38f2c0e4b85"
down_revision: str | Sequence[str] | None = "c27e1b9d3a74"
branch_labels: str | Sequence[str] | None = None
depends_on: str | Sequence[str] | None = None


def upgrade() -> None:
    op.create_table(
        "observation_conflicts",
        sa.Column("id", sa.String(length=36), nullable=False),
        sa.Column("project_id", sa.String(length=36), nullable=False),
        sa.Column("entity_id", sa.String(length=36), nullable=False),
        sa.Column("field_key", sa.String(length=200), nullable=False),
        sa.Column("candidate_assertion_ids", sa.JSON(), nullable=False),
        sa.Column("status", sa.String(length=32), nullable=False),
        sa.Column("resolution_kind", sa.String(length=32), nullable=True),
        sa.Column("chosen_assertion_id", sa.String(length=36), nullable=True),
        sa.Column("override_value", sa.JSON(), nullable=True),
        sa.Column("rationale", sa.Text(), nullable=True),
        sa.Column("resolved_by", sa.String(length=100), nullable=True),
        sa.Column("revision", sa.Integer(), nullable=False),
        sa.Column("created_at", sa.DateTime(timezone=True), nullable=False),
        sa.Column("updated_at", sa.DateTime(timezone=True), nullable=False),
        sa.ForeignKeyConstraint(["project_id"], ["projects.id"], ondelete="CASCADE"),
        sa.ForeignKeyConstraint(["entity_id"], ["entities.id"], ondelete="CASCADE"),
        sa.ForeignKeyConstraint(
            ["chosen_assertion_id"], ["observation_assertions.id"], ondelete="SET NULL"
        ),
        sa.PrimaryKeyConstraint("id"),
        sa.UniqueConstraint(
            "project_id", "entity_id", "field_key", name="uq_observation_conflict_field"
        ),
    )
    with op.batch_alter_table("observation_conflicts", schema=None) as batch_op:
        batch_op.create_index(batch_op.f("ix_observation_conflicts_project_id"), ["project_id"])
        batch_op.create_index(batch_op.f("ix_observation_conflicts_entity_id"), ["entity_id"])
        batch_op.create_index(batch_op.f("ix_observation_conflicts_field_key"), ["field_key"])
        batch_op.create_index(batch_op.f("ix_observation_conflicts_status"), ["status"])


def downgrade() -> None:
    with op.batch_alter_table("observation_conflicts", schema=None) as batch_op:
        batch_op.drop_index(batch_op.f("ix_observation_conflicts_status"))
        batch_op.drop_index(batch_op.f("ix_observation_conflicts_field_key"))
        batch_op.drop_index(batch_op.f("ix_observation_conflicts_entity_id"))
        batch_op.drop_index(batch_op.f("ix_observation_conflicts_project_id"))
    op.drop_table("observation_conflicts")
