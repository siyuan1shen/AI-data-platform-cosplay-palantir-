"""add immutable scenario calculation runs

Revision ID: f7a1c2d3e4f5
Revises: e3f1a2b4c5d6
"""

from collections.abc import Sequence

import sqlalchemy as sa
from alembic import op

revision: str = "f7a1c2d3e4f5"
down_revision: str | Sequence[str] | None = "e3f1a2b4c5d6"
branch_labels: str | Sequence[str] | None = None
depends_on: str | Sequence[str] | None = None


def upgrade() -> None:
    op.create_table(
        "scenario_runs",
        sa.Column("id", sa.String(length=36), nullable=False),
        sa.Column("project_id", sa.String(length=36), nullable=False),
        sa.Column("scenario_id", sa.String(length=36), nullable=False),
        sa.Column("scenario_revision", sa.Integer(), nullable=False),
        sa.Column("baseline_revision", sa.Integer(), nullable=False),
        sa.Column("status", sa.String(length=32), nullable=False, server_default="SUCCEEDED"),
        sa.Column("input_snapshot", sa.JSON(), nullable=False),
        sa.Column("rule_snapshot", sa.JSON(), nullable=False),
        sa.Column("result", sa.JSON(), nullable=False),
        sa.Column("errors", sa.JSON(), nullable=False),
        sa.Column("created_by", sa.String(length=128), nullable=False),
        sa.Column("created_at", sa.DateTime(timezone=True), nullable=False),
        sa.ForeignKeyConstraint(["project_id"], ["projects.id"], ondelete="CASCADE"),
        sa.ForeignKeyConstraint(["scenario_id"], ["scenarios.id"], ondelete="CASCADE"),
        sa.PrimaryKeyConstraint("id"),
    )
    op.create_index("ix_scenario_runs_project_id", "scenario_runs", ["project_id"])
    op.create_index("ix_scenario_runs_scenario_id", "scenario_runs", ["scenario_id"])
    op.create_index("ix_scenario_runs_status", "scenario_runs", ["status"])
    op.create_index(
        "ix_scenario_runs_project_created", "scenario_runs", ["project_id", "created_at"]
    )
    op.create_index(
        "ix_scenario_runs_scenario_revision",
        "scenario_runs",
        ["scenario_id", "scenario_revision"],
    )


def downgrade() -> None:
    op.drop_index("ix_scenario_runs_scenario_revision", table_name="scenario_runs")
    op.drop_index("ix_scenario_runs_project_created", table_name="scenario_runs")
    op.drop_index("ix_scenario_runs_status", table_name="scenario_runs")
    op.drop_index("ix_scenario_runs_scenario_id", table_name="scenario_runs")
    op.drop_index("ix_scenario_runs_project_id", table_name="scenario_runs")
    op.drop_table("scenario_runs")
