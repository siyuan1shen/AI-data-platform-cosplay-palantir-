"""add identity and observation layers

Revision ID: 7a3d9f02b6c4
Revises: 6f12c8a7d4e1
Create Date: 2026-09-06 16:00:00.000000
"""

from collections.abc import Sequence

import sqlalchemy as sa
from alembic import op

revision: str = "7a3d9f02b6c4"
down_revision: str | Sequence[str] | None = "6f12c8a7d4e1"
branch_labels: str | Sequence[str] | None = None
depends_on: str | Sequence[str] | None = None


def upgrade() -> None:
    with op.batch_alter_table("entities", schema=None) as batch_op:
        batch_op.add_column(
            sa.Column(
                "design_membership",
                sa.String(length=32),
                nullable=False,
                server_default="MODELED",
            )
        )
        batch_op.create_index(
            batch_op.f("ix_entities_design_membership"),
            ["design_membership"],
            unique=False,
        )

    op.create_table(
        "source_identities",
        sa.Column("id", sa.String(length=36), nullable=False),
        sa.Column("project_id", sa.String(length=36), nullable=False),
        sa.Column("source_system_id", sa.String(length=36), nullable=False),
        sa.Column("source_asset", sa.String(length=500), nullable=False),
        sa.Column("source_record_key", sa.String(length=500), nullable=False),
        sa.Column("target_type_key", sa.String(length=64), nullable=False),
        sa.Column("entity_id", sa.String(length=36), nullable=False),
        sa.Column("status", sa.String(length=32), nullable=False),
        sa.Column("revision", sa.Integer(), nullable=False),
        sa.Column("created_at", sa.DateTime(timezone=True), nullable=False),
        sa.Column("updated_at", sa.DateTime(timezone=True), nullable=False),
        sa.ForeignKeyConstraint(["entity_id"], ["entities.id"], ondelete="RESTRICT"),
        sa.ForeignKeyConstraint(["project_id"], ["projects.id"], ondelete="CASCADE"),
        sa.ForeignKeyConstraint(
            ["source_system_id"], ["source_systems.id"], ondelete="CASCADE"
        ),
        sa.PrimaryKeyConstraint("id"),
        sa.UniqueConstraint(
            "project_id",
            "source_system_id",
            "source_asset",
            "source_record_key",
            "target_type_key",
            name="uq_source_identity_record",
        ),
    )
    for column in (
        "project_id",
        "source_system_id",
        "source_asset",
        "source_record_key",
        "target_type_key",
        "entity_id",
        "status",
    ):
        op.create_index(
            op.f(f"ix_source_identities_{column}"),
            "source_identities",
            [column],
            unique=False,
        )

    op.create_table(
        "observation_assertions",
        sa.Column("id", sa.String(length=36), nullable=False),
        sa.Column("project_id", sa.String(length=36), nullable=False),
        sa.Column("entity_id", sa.String(length=36), nullable=False),
        sa.Column("source_identity_id", sa.String(length=36), nullable=False),
        sa.Column("source_document_id", sa.String(length=36), nullable=False),
        sa.Column("fragment_id", sa.String(length=36), nullable=True),
        sa.Column("source_asset", sa.String(length=500), nullable=False),
        sa.Column("source_record_key", sa.String(length=500), nullable=False),
        sa.Column("field_key", sa.String(length=200), nullable=False),
        sa.Column("value", sa.JSON(), nullable=False),
        sa.Column("authority_priority", sa.Integer(), nullable=False),
        sa.Column("status", sa.String(length=32), nullable=False),
        sa.Column("observed_at", sa.DateTime(timezone=True), nullable=False),
        sa.Column("created_at", sa.DateTime(timezone=True), nullable=False),
        sa.ForeignKeyConstraint(["entity_id"], ["entities.id"], ondelete="CASCADE"),
        sa.ForeignKeyConstraint(
            ["fragment_id"], ["evidence_fragments.id"], ondelete="SET NULL"
        ),
        sa.ForeignKeyConstraint(["project_id"], ["projects.id"], ondelete="CASCADE"),
        sa.ForeignKeyConstraint(
            ["source_document_id"], ["source_documents.id"], ondelete="CASCADE"
        ),
        sa.ForeignKeyConstraint(
            ["source_identity_id"], ["source_identities.id"], ondelete="CASCADE"
        ),
        sa.PrimaryKeyConstraint("id"),
        sa.UniqueConstraint(
            "source_document_id",
            "source_asset",
            "source_record_key",
            "entity_id",
            "field_key",
            name="uq_observation_assertion_source_field",
        ),
    )
    for column in (
        "project_id",
        "entity_id",
        "source_identity_id",
        "source_document_id",
        "fragment_id",
        "source_asset",
        "source_record_key",
        "field_key",
        "status",
        "observed_at",
    ):
        op.create_index(
            op.f(f"ix_observation_assertions_{column}"),
            "observation_assertions",
            [column],
            unique=False,
        )


def downgrade() -> None:
    op.drop_table("observation_assertions")
    op.drop_table("source_identities")
    with op.batch_alter_table("entities", schema=None) as batch_op:
        batch_op.drop_index(batch_op.f("ix_entities_design_membership"))
        batch_op.drop_column("design_membership")
