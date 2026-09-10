"""add approved source-to-source relation mappings

Revision ID: a91f6e0b7c24
Revises: 8ce35b0f4a96
Create Date: 2026-09-10 00:00:00.000000
"""

from collections.abc import Sequence

import sqlalchemy as sa
from alembic import op

revision: str = "a91f6e0b7c24"
down_revision: str | Sequence[str] | None = "8ce35b0f4a96"
branch_labels: str | Sequence[str] | None = None
depends_on: str | Sequence[str] | None = None


def upgrade() -> None:
    with op.batch_alter_table("materialization_runs") as batch_op:
        batch_op.add_column(
            sa.Column("relations_created", sa.Integer(), nullable=False, server_default="0")
        )
        batch_op.add_column(
            sa.Column(
                "output_relation_ids",
                sa.JSON(),
                nullable=False,
                server_default=sa.text("'[]'"),
            )
        )

    op.create_table(
        "semantic_relation_mappings",
        sa.Column("id", sa.String(length=36), nullable=False),
        sa.Column("project_id", sa.String(length=36), nullable=False),
        sa.Column("source_system_id", sa.String(length=36), nullable=False),
        sa.Column("source_asset", sa.String(length=500), nullable=False),
        sa.Column("source_type_key", sa.String(length=64), nullable=False),
        sa.Column("source_field", sa.String(length=500), nullable=False),
        sa.Column("relation_type_key", sa.String(length=64), nullable=False),
        sa.Column("source_role_key", sa.String(length=64), nullable=False),
        sa.Column("target_type_key", sa.String(length=64), nullable=False),
        sa.Column("target_role_key", sa.String(length=64), nullable=False),
        sa.Column("target_asset", sa.String(length=500), nullable=True),
        sa.Column("transform_expression", sa.Text(), nullable=True),
        sa.Column("status", sa.String(length=32), nullable=False, server_default="DRAFT"),
        sa.Column("revision", sa.Integer(), nullable=False, server_default="1"),
        sa.Column("validation_report", sa.JSON(), nullable=False, server_default=sa.text("'{}'")),
        sa.Column("created_at", sa.DateTime(timezone=True), nullable=False),
        sa.Column("updated_at", sa.DateTime(timezone=True), nullable=False),
        sa.ForeignKeyConstraint(["project_id"], ["projects.id"], ondelete="CASCADE"),
        sa.ForeignKeyConstraint(["source_system_id"], ["source_systems.id"], ondelete="CASCADE"),
        sa.PrimaryKeyConstraint("id"),
    )
    op.create_index(
        "ix_semantic_relation_mappings_project_id",
        "semantic_relation_mappings",
        ["project_id"],
    )
    op.create_index(
        "ix_semantic_relation_mappings_source_system_id",
        "semantic_relation_mappings",
        ["source_system_id"],
    )
    op.create_index(
        "ix_semantic_relation_mappings_source_asset",
        "semantic_relation_mappings",
        ["source_asset"],
    )
    op.create_index(
        "ix_semantic_relation_mappings_source_type_key",
        "semantic_relation_mappings",
        ["source_type_key"],
    )
    op.create_index(
        "ix_semantic_relation_mappings_relation_type_key",
        "semantic_relation_mappings",
        ["relation_type_key"],
    )
    op.create_index(
        "ix_semantic_relation_mappings_target_type_key",
        "semantic_relation_mappings",
        ["target_type_key"],
    )
    op.create_index(
        "ix_semantic_relation_mappings_target_asset",
        "semantic_relation_mappings",
        ["target_asset"],
    )
    op.create_index(
        "ix_semantic_relation_mappings_status",
        "semantic_relation_mappings",
        ["status"],
    )


def downgrade() -> None:
    op.drop_index("ix_semantic_relation_mappings_status", table_name="semantic_relation_mappings")
    for index_name in (
        "ix_semantic_relation_mappings_target_asset",
        "ix_semantic_relation_mappings_target_type_key",
        "ix_semantic_relation_mappings_relation_type_key",
        "ix_semantic_relation_mappings_source_type_key",
        "ix_semantic_relation_mappings_source_asset",
        "ix_semantic_relation_mappings_source_system_id",
        "ix_semantic_relation_mappings_project_id",
    ):
        op.drop_index(index_name, table_name="semantic_relation_mappings")
    op.drop_table("semantic_relation_mappings")
    with op.batch_alter_table("materialization_runs") as batch_op:
        batch_op.drop_column("output_relation_ids")
        batch_op.drop_column("relations_created")
