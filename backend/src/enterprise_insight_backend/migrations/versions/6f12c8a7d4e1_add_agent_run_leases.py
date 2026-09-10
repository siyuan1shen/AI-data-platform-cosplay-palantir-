"""add agent run leases

Revision ID: 6f12c8a7d4e1
Revises: c5c3d8c93e01
Create Date: 2026-09-06 15:00:00.000000
"""

from collections.abc import Sequence

import sqlalchemy as sa
from alembic import op

revision: str = "6f12c8a7d4e1"
down_revision: str | Sequence[str] | None = "c5c3d8c93e01"
branch_labels: str | Sequence[str] | None = None
depends_on: str | Sequence[str] | None = None


def upgrade() -> None:
    with op.batch_alter_table("agent_runs", schema=None) as batch_op:
        batch_op.add_column(sa.Column("worker_id", sa.String(length=36), nullable=True))
        batch_op.add_column(
            sa.Column("lease_expires_at", sa.DateTime(timezone=True), nullable=True)
        )
        batch_op.add_column(sa.Column("heartbeat_at", sa.DateTime(timezone=True), nullable=True))
        batch_op.add_column(
            sa.Column("attempt_count", sa.Integer(), nullable=False, server_default="0")
        )
        batch_op.create_index(
            batch_op.f("ix_agent_runs_worker_id"), ["worker_id"], unique=False
        )
        batch_op.create_index(
            batch_op.f("ix_agent_runs_lease_expires_at"),
            ["lease_expires_at"],
            unique=False,
        )


def downgrade() -> None:
    with op.batch_alter_table("agent_runs", schema=None) as batch_op:
        batch_op.drop_index(batch_op.f("ix_agent_runs_lease_expires_at"))
        batch_op.drop_index(batch_op.f("ix_agent_runs_worker_id"))
        batch_op.drop_column("attempt_count")
        batch_op.drop_column("heartbeat_at")
        batch_op.drop_column("lease_expires_at")
        batch_op.drop_column("worker_id")
