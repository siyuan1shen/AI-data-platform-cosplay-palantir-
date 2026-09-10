"""link learning cases to action results

Revision ID: 8ce35b0f4a96
Revises: 7bd24a9e3f85
Create Date: 2026-09-07 18:00:00.000000
"""

from collections.abc import Sequence

import sqlalchemy as sa
from alembic import op

revision: str = "8ce35b0f4a96"
down_revision: str | Sequence[str] | None = "7bd24a9e3f85"
branch_labels: str | Sequence[str] | None = None
depends_on: str | Sequence[str] | None = None


def upgrade() -> None:
    with op.batch_alter_table("learning_cases") as batch_op:
        batch_op.add_column(
            sa.Column("source_action_invocation_id", sa.String(length=36), nullable=True)
        )
        batch_op.add_column(
            sa.Column(
                "source_action_observation_ids",
                sa.JSON(),
                nullable=False,
                server_default=sa.text("'[]'"),
            )
        )
        batch_op.add_column(
            sa.Column("source_scenario_id", sa.String(length=36), nullable=True)
        )
        batch_op.add_column(
            sa.Column(
                "origin_kind", sa.String(length=32), nullable=False, server_default="MANUAL"
            )
        )
        batch_op.create_foreign_key(
            "fk_learning_cases_source_action_invocation_id_action_invocations",
            "action_invocations",
            ["source_action_invocation_id"],
            ["id"],
            ondelete="SET NULL",
        )
        batch_op.create_foreign_key(
            "fk_learning_cases_source_scenario_id_scenarios",
            "scenarios",
            ["source_scenario_id"],
            ["id"],
            ondelete="SET NULL",
        )
        batch_op.create_index(
            batch_op.f("ix_learning_cases_source_action_invocation_id"),
            ["source_action_invocation_id"],
            unique=False,
        )
        batch_op.create_index(
            batch_op.f("ix_learning_cases_source_scenario_id"),
            ["source_scenario_id"],
            unique=False,
        )
        batch_op.create_index(
            batch_op.f("ix_learning_cases_origin_kind"), ["origin_kind"], unique=False
        )


def downgrade() -> None:
    with op.batch_alter_table("learning_cases") as batch_op:
        batch_op.drop_index(batch_op.f("ix_learning_cases_origin_kind"))
        batch_op.drop_index(batch_op.f("ix_learning_cases_source_scenario_id"))
        batch_op.drop_index(
            batch_op.f("ix_learning_cases_source_action_invocation_id")
        )
        batch_op.drop_constraint(
            "fk_learning_cases_source_scenario_id_scenarios", type_="foreignkey"
        )
        batch_op.drop_constraint(
            "fk_learning_cases_source_action_invocation_id_action_invocations",
            type_="foreignkey",
        )
        batch_op.drop_column("origin_kind")
        batch_op.drop_column("source_scenario_id")
        batch_op.drop_column("source_action_observation_ids")
        batch_op.drop_column("source_action_invocation_id")
