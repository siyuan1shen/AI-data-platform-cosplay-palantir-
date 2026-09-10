"""pin connector extraction previews before confirmation

Revision ID: 6ac13f8d2e74
Revises: 5fb613e9d497
Create Date: 2026-09-07 16:20:00.000000
"""

from collections.abc import Sequence

import sqlalchemy as sa
from alembic import op

revision: str = "6ac13f8d2e74"
down_revision: str | Sequence[str] | None = "5fb613e9d497"
branch_labels: str | Sequence[str] | None = None
depends_on: str | Sequence[str] | None = None


def upgrade() -> None:
    with op.batch_alter_table("import_previews") as batch_op:
        batch_op.add_column(
            sa.Column(
                "preview_metadata",
                sa.JSON(),
                nullable=False,
                server_default=sa.text("'{}'"),
            )
        )


def downgrade() -> None:
    with op.batch_alter_table("import_previews") as batch_op:
        batch_op.drop_column("preview_metadata")
