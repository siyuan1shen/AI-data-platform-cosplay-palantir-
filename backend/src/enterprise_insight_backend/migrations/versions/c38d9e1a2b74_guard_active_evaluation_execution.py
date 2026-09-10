"""guard one active evaluation execution per suite

Revision ID: c38d9e1a2b74
Revises: b27c4d5e6f70
"""

from collections.abc import Sequence

import sqlalchemy as sa
from alembic import op

revision: str = "c38d9e1a2b74"
down_revision: str | Sequence[str] | None = "b27c4d5e6f70"
branch_labels: str | Sequence[str] | None = None
depends_on: str | Sequence[str] | None = None


_ACTIVE_STATUS_PREDICATE = "status IN ('QUEUED', 'RUNNING', 'FINALIZING')"


def upgrade() -> None:
    """Prevent duplicate active evaluations across API processes."""
    op.create_index(
        "uq_evaluation_execution_active",
        "evaluation_executions",
        ["project_id", "suite_id"],
        unique=True,
        sqlite_where=sa.text(_ACTIVE_STATUS_PREDICATE),
        postgresql_where=sa.text(_ACTIVE_STATUS_PREDICATE),
    )


def downgrade() -> None:
    op.drop_index("uq_evaluation_execution_active", table_name="evaluation_executions")
