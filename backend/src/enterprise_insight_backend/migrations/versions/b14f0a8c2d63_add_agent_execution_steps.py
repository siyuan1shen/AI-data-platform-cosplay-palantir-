"""add persistent agent execution steps

Revision ID: b14f0a8c2d63
Revises: 9d5f2b7e0c31
Create Date: 2026-09-06 20:00:00.000000
"""

from collections.abc import Sequence

import sqlalchemy as sa
from alembic import op

revision: str = "b14f0a8c2d63"
down_revision: str | Sequence[str] | None = "9d5f2b7e0c31"
branch_labels: str | Sequence[str] | None = None
depends_on: str | Sequence[str] | None = None


def upgrade() -> None:
    op.create_table(
        "agent_steps",
        sa.Column("id", sa.String(length=36), nullable=False),
        sa.Column("project_id", sa.String(length=36), nullable=False),
        sa.Column("run_id", sa.String(length=36), nullable=False),
        sa.Column("position", sa.Integer(), nullable=False),
        sa.Column("kind", sa.String(length=32), nullable=False),
        sa.Column("tool_key", sa.String(length=100), nullable=True),
        sa.Column("status", sa.String(length=32), nullable=False),
        sa.Column("input_payload", sa.JSON(), nullable=False),
        sa.Column("output_payload", sa.JSON(), nullable=False),
        sa.Column("error", sa.JSON(), nullable=True),
        sa.Column("started_at", sa.DateTime(timezone=True), nullable=False),
        sa.Column("finished_at", sa.DateTime(timezone=True), nullable=True),
        sa.Column("created_at", sa.DateTime(timezone=True), nullable=False),
        sa.ForeignKeyConstraint(["project_id"], ["projects.id"], ondelete="CASCADE"),
        sa.ForeignKeyConstraint(["run_id"], ["agent_runs.id"], ondelete="CASCADE"),
        sa.PrimaryKeyConstraint("id"),
        sa.UniqueConstraint("run_id", "position", name="uq_agent_step_position"),
    )
    with op.batch_alter_table("agent_steps", schema=None) as batch_op:
        batch_op.create_index(batch_op.f("ix_agent_steps_project_id"), ["project_id"])
        batch_op.create_index(batch_op.f("ix_agent_steps_run_id"), ["run_id"])
        batch_op.create_index(batch_op.f("ix_agent_steps_kind"), ["kind"])
        batch_op.create_index(batch_op.f("ix_agent_steps_tool_key"), ["tool_key"])
        batch_op.create_index(batch_op.f("ix_agent_steps_status"), ["status"])


def downgrade() -> None:
    with op.batch_alter_table("agent_steps", schema=None) as batch_op:
        batch_op.drop_index(batch_op.f("ix_agent_steps_status"))
        batch_op.drop_index(batch_op.f("ix_agent_steps_tool_key"))
        batch_op.drop_index(batch_op.f("ix_agent_steps_kind"))
        batch_op.drop_index(batch_op.f("ix_agent_steps_run_id"))
        batch_op.drop_index(batch_op.f("ix_agent_steps_project_id"))
    op.drop_table("agent_steps")
