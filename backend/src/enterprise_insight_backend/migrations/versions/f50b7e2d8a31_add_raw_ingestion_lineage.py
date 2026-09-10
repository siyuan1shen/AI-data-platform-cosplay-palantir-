"""add stable source assets, immutable raw batches and materialization lineage

Revision ID: f50b7e2d8a31
Revises: e49a6d1c7f20
Create Date: 2026-09-07 05:20:00.000000
"""

from collections.abc import Sequence

import sqlalchemy as sa
from alembic import op

revision: str = "f50b7e2d8a31"
down_revision: str | Sequence[str] | None = "e49a6d1c7f20"
branch_labels: str | Sequence[str] | None = None
depends_on: str | Sequence[str] | None = None


def upgrade() -> None:
    op.create_table(
        "source_assets",
        sa.Column("id", sa.String(length=36), nullable=False),
        sa.Column("project_id", sa.String(length=36), nullable=False),
        sa.Column("source_system_id", sa.String(length=36), nullable=False),
        sa.Column("asset_key", sa.String(length=500), nullable=False),
        sa.Column("name", sa.String(length=300), nullable=False),
        sa.Column("description", sa.Text()),
        sa.Column("schema_fingerprint", sa.String(length=64)),
        sa.Column("schema_fields", sa.JSON(), nullable=False),
        sa.Column("status", sa.String(length=32), nullable=False),
        sa.Column("revision", sa.Integer(), nullable=False),
        sa.Column("created_at", sa.DateTime(timezone=True), nullable=False),
        sa.Column("updated_at", sa.DateTime(timezone=True), nullable=False),
        sa.ForeignKeyConstraint(["project_id"], ["projects.id"], ondelete="CASCADE"),
        sa.ForeignKeyConstraint(
            ["source_system_id"], ["source_systems.id"], ondelete="CASCADE"
        ),
        sa.PrimaryKeyConstraint("id"),
        sa.UniqueConstraint(
            "source_system_id", "asset_key", name="uq_source_asset_system_key"
        ),
    )
    _indexes(
        "source_assets",
        ["project_id", "source_system_id", "asset_key", "schema_fingerprint", "status"],
    )
    op.create_table(
        "raw_batches",
        sa.Column("id", sa.String(length=36), nullable=False),
        sa.Column("project_id", sa.String(length=36), nullable=False),
        sa.Column("source_asset_id", sa.String(length=36), nullable=False),
        sa.Column("source_document_id", sa.String(length=36), nullable=False),
        sa.Column("content_sha256", sa.String(length=64), nullable=False),
        sa.Column("parser_version", sa.String(length=64), nullable=False),
        sa.Column("schema_fingerprint", sa.String(length=64), nullable=False),
        sa.Column("status", sa.String(length=32), nullable=False),
        sa.Column("record_count", sa.Integer(), nullable=False),
        sa.Column("error_count", sa.Integer(), nullable=False),
        sa.Column("created_at", sa.DateTime(timezone=True), nullable=False),
        sa.ForeignKeyConstraint(["project_id"], ["projects.id"], ondelete="CASCADE"),
        sa.ForeignKeyConstraint(
            ["source_asset_id"], ["source_assets.id"], ondelete="RESTRICT"
        ),
        sa.ForeignKeyConstraint(
            ["source_document_id"], ["source_documents.id"], ondelete="RESTRICT"
        ),
        sa.PrimaryKeyConstraint("id"),
        sa.UniqueConstraint(
            "source_asset_id", "content_sha256", name="uq_raw_batch_asset_content"
        ),
    )
    _indexes(
        "raw_batches",
        [
            "project_id",
            "source_asset_id",
            "source_document_id",
            "content_sha256",
            "schema_fingerprint",
            "status",
        ],
    )
    op.create_table(
        "raw_records",
        sa.Column("id", sa.String(length=36), nullable=False),
        sa.Column("project_id", sa.String(length=36), nullable=False),
        sa.Column("raw_batch_id", sa.String(length=36), nullable=False),
        sa.Column("row_number", sa.Integer(), nullable=False),
        sa.Column("source_locator", sa.String(length=500), nullable=False),
        sa.Column("source_record_key", sa.String(length=500)),
        sa.Column("payload", sa.JSON(), nullable=False),
        sa.Column("payload_sha256", sa.String(length=64), nullable=False),
        sa.Column("created_at", sa.DateTime(timezone=True), nullable=False),
        sa.ForeignKeyConstraint(["project_id"], ["projects.id"], ondelete="CASCADE"),
        sa.ForeignKeyConstraint(["raw_batch_id"], ["raw_batches.id"], ondelete="CASCADE"),
        sa.PrimaryKeyConstraint("id"),
        sa.UniqueConstraint("raw_batch_id", "row_number", name="uq_raw_record_batch_row"),
    )
    _indexes(
        "raw_records",
        ["project_id", "raw_batch_id", "source_record_key", "payload_sha256"],
    )
    op.create_table(
        "materialization_runs",
        sa.Column("id", sa.String(length=36), nullable=False),
        sa.Column("project_id", sa.String(length=36), nullable=False),
        sa.Column("raw_batch_id", sa.String(length=36), nullable=False),
        sa.Column("mapping_fingerprint", sa.String(length=64), nullable=False),
        sa.Column("mapping_ids", sa.JSON(), nullable=False),
        sa.Column("status", sa.String(length=32), nullable=False),
        sa.Column("records_processed", sa.Integer(), nullable=False),
        sa.Column("entities_created", sa.Integer(), nullable=False),
        sa.Column("identities_bound", sa.Integer(), nullable=False),
        sa.Column("observations_created", sa.Integer(), nullable=False),
        sa.Column("mappings_applied", sa.Integer(), nullable=False),
        sa.Column("output_entity_ids", sa.JSON(), nullable=False),
        sa.Column("output_identity_ids", sa.JSON(), nullable=False),
        sa.Column("output_assertion_ids", sa.JSON(), nullable=False),
        sa.Column("errors", sa.JSON(), nullable=False),
        sa.Column("created_at", sa.DateTime(timezone=True), nullable=False),
        sa.Column("finished_at", sa.DateTime(timezone=True)),
        sa.ForeignKeyConstraint(["project_id"], ["projects.id"], ondelete="CASCADE"),
        sa.ForeignKeyConstraint(["raw_batch_id"], ["raw_batches.id"], ondelete="CASCADE"),
        sa.PrimaryKeyConstraint("id"),
        sa.UniqueConstraint(
            "raw_batch_id",
            "mapping_fingerprint",
            name="uq_materialization_batch_mapping",
        ),
    )
    _indexes(
        "materialization_runs",
        ["project_id", "raw_batch_id", "mapping_fingerprint", "status"],
    )


def downgrade() -> None:
    for table in (
        "materialization_runs",
        "raw_records",
        "raw_batches",
        "source_assets",
    ):
        op.drop_table(table)


def _indexes(table: str, columns: list[str]) -> None:
    with op.batch_alter_table(table, schema=None) as batch_op:
        for column in columns:
            batch_op.create_index(batch_op.f(f"ix_{table}_{column}"), [column])
