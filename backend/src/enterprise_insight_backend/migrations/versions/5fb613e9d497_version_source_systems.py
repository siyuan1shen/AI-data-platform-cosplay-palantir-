"""version source systems for safe reconfiguration

Revision ID: 5fb613e9d497
Revises: 4ea502d8c386
Create Date: 2026-09-07 13:00:00.000000
"""

from collections.abc import Sequence

import sqlalchemy as sa
from alembic import op

revision: str = "5fb613e9d497"
down_revision: str | Sequence[str] | None = "4ea502d8c386"
branch_labels: str | Sequence[str] | None = None
depends_on: str | Sequence[str] | None = None


def upgrade() -> None:
    with op.batch_alter_table("source_systems") as batch_op:
        batch_op.add_column(
            sa.Column("revision", sa.Integer(), nullable=False, server_default="1")
        )


def downgrade() -> None:
    with op.batch_alter_table("source_systems") as batch_op:
        batch_op.drop_column("revision")
