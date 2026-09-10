"""complete scenario lifecycle

Revision ID: 7bd24a9e3f85
Revises: 6ac13f8d2e74
Create Date: 2026-09-07 17:10:00.000000
"""

from collections.abc import Sequence

import sqlalchemy as sa
from alembic import op

revision: str = "7bd24a9e3f85"
down_revision: str | Sequence[str] | None = "6ac13f8d2e74"
branch_labels: str | Sequence[str] | None = None
depends_on: str | Sequence[str] | None = None


def upgrade() -> None:
    with op.batch_alter_table("scenarios") as batch_op:
        batch_op.add_column(
            sa.Column("base_revision", sa.Integer(), nullable=False, server_default="0")
        )
        batch_op.add_column(
            sa.Column("applied_change_set_id", sa.String(length=36), nullable=True)
        )
        batch_op.create_foreign_key(
            "fk_scenarios_applied_change_set_id_change_sets",
            "change_sets",
            ["applied_change_set_id"],
            ["id"],
            ondelete="SET NULL",
        )
        batch_op.create_index(
            batch_op.f("ix_scenarios_applied_change_set_id"),
            ["applied_change_set_id"],
            unique=False,
        )
    op.execute(
        sa.text(
            "UPDATE scenarios SET base_revision = "
            "COALESCE((SELECT revision FROM projects WHERE projects.id = scenarios.project_id), 0)"
        )
    )


def downgrade() -> None:
    with op.batch_alter_table("scenarios") as batch_op:
        batch_op.drop_index(batch_op.f("ix_scenarios_applied_change_set_id"))
        batch_op.drop_constraint(
            "fk_scenarios_applied_change_set_id_change_sets", type_="foreignkey"
        )
        batch_op.drop_column("applied_change_set_id")
        batch_op.drop_column("base_revision")
