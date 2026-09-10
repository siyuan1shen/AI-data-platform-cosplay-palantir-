"""version metric observations and add comparable periods

Revision ID: e49a6d1c7f20
Revises: d38f2c0e4b85
Create Date: 2026-09-07 04:30:00.000000
"""

from collections.abc import Sequence

import sqlalchemy as sa
from alembic import op

revision: str = "e49a6d1c7f20"
down_revision: str | Sequence[str] | None = "d38f2c0e4b85"
branch_labels: str | Sequence[str] | None = None
depends_on: str | Sequence[str] | None = None

EMPTY_DIMENSION_KEY = "44136fa355b3678a1146ad16f7e8649e94fb4fc21fe77e8310c060f61caaff8a"


def upgrade() -> None:
    with op.batch_alter_table("metric_observations", schema=None) as batch_op:
        batch_op.add_column(sa.Column("period_start", sa.DateTime(timezone=True)))
        batch_op.add_column(sa.Column("period_end", sa.DateTime(timezone=True)))
        batch_op.add_column(sa.Column("dimensions", sa.JSON(), nullable=True))
        batch_op.add_column(sa.Column("dimension_key", sa.String(length=64), nullable=True))
        batch_op.add_column(sa.Column("unit", sa.String(length=100)))
        batch_op.add_column(sa.Column("definition_revision", sa.Integer(), nullable=True))
        batch_op.add_column(sa.Column("version", sa.Integer(), nullable=True))
        batch_op.add_column(sa.Column("record_status", sa.String(length=32), nullable=True))
        batch_op.add_column(sa.Column("supersedes_id", sa.String(length=36)))
        batch_op.add_column(sa.Column("revision", sa.Integer(), nullable=True))
        batch_op.add_column(sa.Column("updated_at", sa.DateTime(timezone=True), nullable=True))

    connection = op.get_bind()
    rows = connection.execute(
        sa.text(
            "SELECT id, metric_definition_id, period_key, source, created_at "
            "FROM metric_observations ORDER BY created_at, id"
        )
    ).mappings()
    grouped: dict[tuple[str, str, str], list[str]] = {}
    for row in rows:
        grouped.setdefault(
            (row["metric_definition_id"], row["period_key"], row["source"]), []
        ).append(row["id"])
    for ids in grouped.values():
        previous_id: str | None = None
        for version, row_id in enumerate(ids, start=1):
            connection.execute(
                sa.text(
                    "UPDATE metric_observations SET dimensions = :dimensions, "
                    "dimension_key = :dimension_key, definition_revision = 1, "
                    "version = :version, record_status = :record_status, "
                    "supersedes_id = :supersedes_id, revision = 1, "
                    "updated_at = created_at WHERE id = :id"
                ),
                {
                    "dimensions": "{}",
                    "dimension_key": EMPTY_DIMENSION_KEY,
                    "version": version,
                    "record_status": "ACTIVE" if version == len(ids) else "SUPERSEDED",
                    "supersedes_id": previous_id,
                    "id": row_id,
                },
            )
            previous_id = row_id

    with op.batch_alter_table("metric_observations", schema=None) as batch_op:
        batch_op.alter_column("dimensions", existing_type=sa.JSON(), nullable=False)
        batch_op.alter_column(
            "dimension_key", existing_type=sa.String(length=64), nullable=False
        )
        batch_op.alter_column("definition_revision", existing_type=sa.Integer(), nullable=False)
        batch_op.alter_column("version", existing_type=sa.Integer(), nullable=False)
        batch_op.alter_column(
            "record_status", existing_type=sa.String(length=32), nullable=False
        )
        batch_op.alter_column("revision", existing_type=sa.Integer(), nullable=False)
        batch_op.alter_column(
            "updated_at", existing_type=sa.DateTime(timezone=True), nullable=False
        )
        batch_op.create_foreign_key(
            "fk_metric_observations_supersedes_id",
            "metric_observations",
            ["supersedes_id"],
            ["id"],
            ondelete="SET NULL",
        )
        batch_op.create_unique_constraint(
            "uq_metric_observation_version",
            ["metric_definition_id", "period_key", "dimension_key", "source", "version"],
        )
        for column in (
            "period_start",
            "period_end",
            "dimension_key",
            "record_status",
            "supersedes_id",
        ):
            batch_op.create_index(batch_op.f(f"ix_metric_observations_{column}"), [column])


def downgrade() -> None:
    with op.batch_alter_table("metric_observations", schema=None) as batch_op:
        for column in (
            "supersedes_id",
            "record_status",
            "dimension_key",
            "period_end",
            "period_start",
        ):
            batch_op.drop_index(batch_op.f(f"ix_metric_observations_{column}"))
        batch_op.drop_constraint("uq_metric_observation_version", type_="unique")
        batch_op.drop_constraint("fk_metric_observations_supersedes_id", type_="foreignkey")
        for column in (
            "updated_at",
            "revision",
            "supersedes_id",
            "record_status",
            "version",
            "definition_revision",
            "unit",
            "dimension_key",
            "dimensions",
            "period_end",
            "period_start",
        ):
            batch_op.drop_column(column)
