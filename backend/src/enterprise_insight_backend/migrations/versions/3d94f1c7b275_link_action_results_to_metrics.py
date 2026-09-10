"""link action results to shared metric observations

Revision ID: 3d94f1c7b275
Revises: 2c83e0b6a164
Create Date: 2026-09-07 11:00:00.000000
"""

from collections.abc import Sequence

import sqlalchemy as sa
from alembic import op

revision: str = "3d94f1c7b275"
down_revision: str | Sequence[str] | None = "2c83e0b6a164"
branch_labels: str | Sequence[str] | None = None
depends_on: str | Sequence[str] | None = None


def upgrade() -> None:
    with op.batch_alter_table("action_observations") as batch_op:
        batch_op.add_column(
            sa.Column(
                "observation_kind",
                sa.String(length=32),
                nullable=False,
                server_default="QUALITATIVE",
            )
        )
        batch_op.add_column(sa.Column("metric_definition_id", sa.String(length=36)))
        batch_op.add_column(sa.Column("metric_observation_id", sa.String(length=36)))
        batch_op.add_column(sa.Column("period_key", sa.String(length=100)))
        batch_op.add_column(
            sa.Column("dimensions", sa.JSON(), nullable=False, server_default="{}")
        )
        batch_op.create_foreign_key(
            "fk_action_observations_metric_definition_id",
            "metric_definitions",
            ["metric_definition_id"],
            ["id"],
            ondelete="SET NULL",
        )
        batch_op.create_foreign_key(
            "fk_action_observations_metric_observation_id",
            "metric_observations",
            ["metric_observation_id"],
            ["id"],
            ondelete="SET NULL",
        )
        batch_op.create_index(
            op.f("ix_action_observations_metric_definition_id"),
            ["metric_definition_id"],
            unique=False,
        )
        batch_op.create_index(
            op.f("ix_action_observations_metric_observation_id"),
            ["metric_observation_id"],
            unique=False,
        )


def downgrade() -> None:
    with op.batch_alter_table("action_observations") as batch_op:
        batch_op.drop_index(op.f("ix_action_observations_metric_observation_id"))
        batch_op.drop_index(op.f("ix_action_observations_metric_definition_id"))
        batch_op.drop_constraint(
            "fk_action_observations_metric_observation_id", type_="foreignkey"
        )
        batch_op.drop_constraint(
            "fk_action_observations_metric_definition_id", type_="foreignkey"
        )
        batch_op.drop_column("dimensions")
        batch_op.drop_column("period_key")
        batch_op.drop_column("metric_observation_id")
        batch_op.drop_column("metric_definition_id")
        batch_op.drop_column("observation_kind")
