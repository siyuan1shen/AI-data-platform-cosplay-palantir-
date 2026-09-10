"""add stable management issue identity and occurrence history

Revision ID: 2c83e0b6a164
Revises: 1b72d9a4f053
Create Date: 2026-09-07 10:00:00.000000
"""

from collections.abc import Sequence

import sqlalchemy as sa
from alembic import op

revision: str = "2c83e0b6a164"
down_revision: str | Sequence[str] | None = "1b72d9a4f053"
branch_labels: str | Sequence[str] | None = None
depends_on: str | Sequence[str] | None = None


def upgrade() -> None:
    op.create_table(
        "management_issues",
        sa.Column("id", sa.String(length=36), nullable=False),
        sa.Column("project_id", sa.String(length=36), nullable=False),
        sa.Column("issue_key", sa.String(length=64), nullable=False),
        sa.Column("issue_family", sa.String(length=100), nullable=False),
        sa.Column("title", sa.String(length=300), nullable=False),
        sa.Column("affected_entity_ids", sa.JSON(), nullable=False),
        sa.Column("status", sa.String(length=32), nullable=False),
        sa.Column("management_feedback", sa.Text()),
        sa.Column("last_occurrence_signature", sa.String(length=64), nullable=False),
        sa.Column("occurrence_count", sa.Integer(), nullable=False),
        sa.Column("revision", sa.Integer(), nullable=False),
        sa.Column("first_seen_at", sa.DateTime(timezone=True), nullable=False),
        sa.Column("last_seen_at", sa.DateTime(timezone=True), nullable=False),
        sa.Column("created_at", sa.DateTime(timezone=True), nullable=False),
        sa.Column("updated_at", sa.DateTime(timezone=True), nullable=False),
        sa.ForeignKeyConstraint(["project_id"], ["projects.id"], ondelete="CASCADE"),
        sa.PrimaryKeyConstraint("id"),
        sa.UniqueConstraint("project_id", "issue_key", name="uq_management_issue_key"),
    )
    for column in ("project_id", "issue_key", "issue_family", "status"):
        op.create_index(
            op.f(f"ix_management_issues_{column}"),
            "management_issues",
            [column],
            unique=False,
        )
    with op.batch_alter_table("management_insights") as batch_op:
        batch_op.add_column(sa.Column("issue_id", sa.String(length=36)))
        batch_op.add_column(
            sa.Column("occurrence_number", sa.Integer(), nullable=False, server_default="1")
        )
        batch_op.add_column(sa.Column("occurrence_signature", sa.String(length=64)))
        batch_op.add_column(
            sa.Column("evidence_changed", sa.Boolean(), nullable=False, server_default=sa.false())
        )
        batch_op.create_foreign_key(
            "fk_management_insights_issue_id",
            "management_issues",
            ["issue_id"],
            ["id"],
            ondelete="CASCADE",
        )
        batch_op.create_index(
            op.f("ix_management_insights_issue_id"), ["issue_id"], unique=False
        )
        batch_op.create_index(
            op.f("ix_management_insights_occurrence_signature"),
            ["occurrence_signature"],
            unique=False,
        )
    op.create_table(
        "management_issue_feedback",
        sa.Column("id", sa.String(length=36), nullable=False),
        sa.Column("project_id", sa.String(length=36), nullable=False),
        sa.Column("issue_id", sa.String(length=36), nullable=False),
        sa.Column("insight_id", sa.String(length=36)),
        sa.Column("from_status", sa.String(length=32), nullable=False),
        sa.Column("to_status", sa.String(length=32), nullable=False),
        sa.Column("feedback", sa.Text()),
        sa.Column("provided_by", sa.String(length=100), nullable=False),
        sa.Column("created_at", sa.DateTime(timezone=True), nullable=False),
        sa.ForeignKeyConstraint(["project_id"], ["projects.id"], ondelete="CASCADE"),
        sa.ForeignKeyConstraint(["issue_id"], ["management_issues.id"], ondelete="CASCADE"),
        sa.ForeignKeyConstraint(
            ["insight_id"], ["management_insights.id"], ondelete="SET NULL"
        ),
        sa.PrimaryKeyConstraint("id"),
    )
    for column in ("project_id", "issue_id", "insight_id"):
        op.create_index(
            op.f(f"ix_management_issue_feedback_{column}"),
            "management_issue_feedback",
            [column],
            unique=False,
        )


def downgrade() -> None:
    op.drop_table("management_issue_feedback")
    with op.batch_alter_table("management_insights") as batch_op:
        batch_op.drop_index(op.f("ix_management_insights_occurrence_signature"))
        batch_op.drop_index(op.f("ix_management_insights_issue_id"))
        batch_op.drop_constraint("fk_management_insights_issue_id", type_="foreignkey")
        batch_op.drop_column("evidence_changed")
        batch_op.drop_column("occurrence_signature")
        batch_op.drop_column("occurrence_number")
        batch_op.drop_column("issue_id")
    op.drop_table("management_issues")
