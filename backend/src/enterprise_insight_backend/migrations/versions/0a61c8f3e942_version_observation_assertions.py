"""version materialized observations and attach exact lineage

Revision ID: 0a61c8f3e942
Revises: f50b7e2d8a31
Create Date: 2026-09-07 06:00:00.000000
"""

from collections.abc import Sequence

import sqlalchemy as sa
from alembic import op

revision: str = "0a61c8f3e942"
down_revision: str | Sequence[str] | None = "f50b7e2d8a31"
branch_labels: str | Sequence[str] | None = None
depends_on: str | Sequence[str] | None = None


def upgrade() -> None:
    with op.batch_alter_table("observation_assertions", schema=None) as batch_op:
        batch_op.drop_constraint("uq_observation_assertion_source_field", type_="unique")
        batch_op.add_column(sa.Column("raw_record_id", sa.String(length=36)))
        batch_op.add_column(sa.Column("semantic_mapping_id", sa.String(length=36)))
        batch_op.add_column(sa.Column("materialization_run_id", sa.String(length=36)))
        batch_op.add_column(sa.Column("version", sa.Integer(), nullable=True))
        batch_op.add_column(sa.Column("supersedes_id", sa.String(length=36)))
        batch_op.add_column(sa.Column("updated_at", sa.DateTime(timezone=True)))

    connection = op.get_bind()
    connection.execute(
        sa.text(
            "UPDATE observation_assertions SET version = 1, updated_at = created_at "
            "WHERE version IS NULL"
        )
    )
    with op.batch_alter_table("observation_assertions", schema=None) as batch_op:
        batch_op.alter_column("version", existing_type=sa.Integer(), nullable=False)
        batch_op.alter_column(
            "updated_at", existing_type=sa.DateTime(timezone=True), nullable=False
        )
        batch_op.create_foreign_key(
            "fk_observation_assertions_raw_record_id",
            "raw_records",
            ["raw_record_id"],
            ["id"],
            ondelete="SET NULL",
        )
        batch_op.create_foreign_key(
            "fk_observation_assertions_semantic_mapping_id",
            "semantic_mappings",
            ["semantic_mapping_id"],
            ["id"],
            ondelete="SET NULL",
        )
        batch_op.create_foreign_key(
            "fk_observation_assertions_materialization_run_id",
            "materialization_runs",
            ["materialization_run_id"],
            ["id"],
            ondelete="SET NULL",
        )
        batch_op.create_foreign_key(
            "fk_observation_assertions_supersedes_id",
            "observation_assertions",
            ["supersedes_id"],
            ["id"],
            ondelete="SET NULL",
        )
        batch_op.create_unique_constraint(
            "uq_observation_assertion_materialized_field",
            ["materialization_run_id", "raw_record_id", "entity_id", "field_key"],
        )
        for column in (
            "raw_record_id",
            "semantic_mapping_id",
            "materialization_run_id",
            "supersedes_id",
        ):
            batch_op.create_index(
                batch_op.f(f"ix_observation_assertions_{column}"), [column]
            )


def downgrade() -> None:
    with op.batch_alter_table("observation_assertions", schema=None) as batch_op:
        for column in (
            "supersedes_id",
            "materialization_run_id",
            "semantic_mapping_id",
            "raw_record_id",
        ):
            batch_op.drop_index(batch_op.f(f"ix_observation_assertions_{column}"))
        batch_op.drop_constraint(
            "uq_observation_assertion_materialized_field", type_="unique"
        )
        for name in (
            "fk_observation_assertions_supersedes_id",
            "fk_observation_assertions_materialization_run_id",
            "fk_observation_assertions_semantic_mapping_id",
            "fk_observation_assertions_raw_record_id",
        ):
            batch_op.drop_constraint(name, type_="foreignkey")
        batch_op.create_unique_constraint(
            "uq_observation_assertion_source_field",
            [
                "source_document_id",
                "source_asset",
                "source_record_key",
                "entity_id",
                "field_key",
            ],
        )
        for column in (
            "updated_at",
            "supersedes_id",
            "version",
            "materialization_run_id",
            "semantic_mapping_id",
            "raw_record_id",
        ):
            batch_op.drop_column(column)
