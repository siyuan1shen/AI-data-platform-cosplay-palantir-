"""add default model profile

Revision ID: c5c3d8c93e01
Revises: 9a821cf6d930
Create Date: 2026-09-06 12:00:00.000000
"""

from collections.abc import Sequence

import sqlalchemy as sa
from alembic import op

revision: str = "c5c3d8c93e01"
down_revision: str | Sequence[str] | None = "9a821cf6d930"
branch_labels: str | Sequence[str] | None = None
depends_on: str | Sequence[str] | None = None


def upgrade() -> None:
    with op.batch_alter_table("model_profiles", schema=None) as batch_op:
        batch_op.add_column(
            sa.Column("is_default", sa.Boolean(), nullable=False, server_default=sa.false())
        )
        batch_op.create_index(
            batch_op.f("ix_model_profiles_is_default"), ["is_default"], unique=False
        )


def downgrade() -> None:
    with op.batch_alter_table("model_profiles", schema=None) as batch_op:
        batch_op.drop_index(batch_op.f("ix_model_profiles_is_default"))
        batch_op.drop_column("is_default")
