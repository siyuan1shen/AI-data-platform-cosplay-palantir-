"""add lifecycle deletion audit

Revision ID: c3d4e5f6a7b8
Revises: b2c3d4e5f6a7
Create Date: 2026-09-16 13:20:00.000000
"""

from collections.abc import Sequence

import sqlalchemy as sa
from alembic import op

revision: str = "c3d4e5f6a7b8"
down_revision: str | Sequence[str] | None = "b2c3d4e5f6a7"
branch_labels: str | Sequence[str] | None = None
depends_on: str | Sequence[str] | None = None


def upgrade() -> None:
    op.create_table(
        "lifecycle_deletion_audit",
        sa.Column("id", sa.String(length=36), nullable=False),
        sa.Column("project_id", sa.String(length=36), nullable=True),
        sa.Column("resource_kind", sa.String(length=64), nullable=False),
        sa.Column("resource_id", sa.String(length=100), nullable=False),
        sa.Column("deletion_mode", sa.String(length=16), nullable=False),
        sa.Column("actor_id", sa.String(length=128), nullable=False),
        sa.Column("reason", sa.String(length=2000), nullable=False),
        sa.Column("details", sa.JSON(), nullable=False),
        sa.Column("created_at", sa.DateTime(timezone=True), nullable=False),
        sa.ForeignKeyConstraint(["project_id"], ["projects.id"], ondelete="SET NULL"),
        sa.PrimaryKeyConstraint("id"),
    )
    with op.batch_alter_table("lifecycle_deletion_audit", schema=None) as batch_op:
        batch_op.create_index(
            "ix_lifecycle_deletion_audit_project_created",
            ["project_id", "created_at"],
            unique=False,
        )
        batch_op.create_index(
            "ix_lifecycle_deletion_audit_resource",
            ["resource_kind", "resource_id"],
            unique=False,
        )
        batch_op.create_index(
            "ix_lifecycle_deletion_audit_actor_id",
            ["actor_id"],
            unique=False,
        )
        batch_op.create_index(
            "ix_lifecycle_deletion_audit_resource_kind",
            ["resource_kind"],
            unique=False,
        )


def downgrade() -> None:
    op.drop_table("lifecycle_deletion_audit")
