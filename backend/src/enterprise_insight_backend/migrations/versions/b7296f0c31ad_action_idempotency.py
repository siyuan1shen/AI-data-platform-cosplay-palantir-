"""add idempotency for management action creation

Revision ID: b7296f0c31ad
Revises: f6a8c2d4019b
"""

from collections.abc import Sequence

import sqlalchemy as sa
from alembic import op

revision: str = "b7296f0c31ad"
down_revision: str | Sequence[str] | None = "f6a8c2d4019b"
branch_labels: str | Sequence[str] | None = None
depends_on: str | Sequence[str] | None = None


def upgrade() -> None:
    op.add_column("management_actions", sa.Column("idempotency_key", sa.String(200)))
    op.add_column("management_actions", sa.Column("idempotency_hash", sa.String(64)))
    op.create_index(
        "uq_management_action_project_idempotency",
        "management_actions",
        ["project_id", "idempotency_key"],
        unique=True,
    )


def downgrade() -> None:
    op.drop_index("uq_management_action_project_idempotency", table_name="management_actions")
    op.drop_column("management_actions", "idempotency_hash")
    op.drop_column("management_actions", "idempotency_key")
