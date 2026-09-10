"""add semantic mapping lifecycle

Revision ID: 8c4e1a6d9b20
Revises: 7a3d9f02b6c4
Create Date: 2026-09-06 17:30:00.000000
"""

from collections.abc import Sequence

import sqlalchemy as sa
from alembic import op

revision: str = "8c4e1a6d9b20"
down_revision: str | Sequence[str] | None = "7a3d9f02b6c4"
branch_labels: str | Sequence[str] | None = None
depends_on: str | Sequence[str] | None = None


def upgrade() -> None:
    with op.batch_alter_table("semantic_mappings", schema=None) as batch_op:
        batch_op.add_column(
            sa.Column("revision", sa.Integer(), nullable=False, server_default="1")
        )
        batch_op.add_column(
            sa.Column("validation_report", sa.JSON(), nullable=False, server_default="{}")
        )


def downgrade() -> None:
    with op.batch_alter_table("semantic_mappings", schema=None) as batch_op:
        batch_op.drop_column("validation_report")
        batch_op.drop_column("revision")
