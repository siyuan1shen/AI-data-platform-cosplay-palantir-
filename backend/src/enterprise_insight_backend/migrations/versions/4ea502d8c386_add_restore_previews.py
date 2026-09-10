"""add project restore preview state

Revision ID: 4ea502d8c386
Revises: 3d94f1c7b275
Create Date: 2026-09-07 12:00:00.000000
"""

from collections.abc import Sequence

import sqlalchemy as sa
from alembic import op

revision: str = "4ea502d8c386"
down_revision: str | Sequence[str] | None = "3d94f1c7b275"
branch_labels: str | Sequence[str] | None = None
depends_on: str | Sequence[str] | None = None


def upgrade() -> None:
    op.create_table(
        "restore_previews",
        sa.Column("id", sa.String(length=36), nullable=False),
        sa.Column("file_name", sa.String(length=500), nullable=False),
        sa.Column("package_sha256", sa.String(length=64), nullable=False),
        sa.Column("package_path", sa.String(length=1000), nullable=False),
        sa.Column("status", sa.String(length=32), nullable=False),
        sa.Column("summary", sa.JSON(), nullable=False),
        sa.Column("expires_at", sa.DateTime(timezone=True), nullable=False),
        sa.Column("consumed_at", sa.DateTime(timezone=True)),
        sa.Column("created_at", sa.DateTime(timezone=True), nullable=False),
        sa.PrimaryKeyConstraint("id"),
    )
    for column in ("package_sha256", "status", "expires_at"):
        op.create_index(
            op.f(f"ix_restore_previews_{column}"),
            "restore_previews",
            [column],
            unique=False,
        )


def downgrade() -> None:
    op.drop_table("restore_previews")
