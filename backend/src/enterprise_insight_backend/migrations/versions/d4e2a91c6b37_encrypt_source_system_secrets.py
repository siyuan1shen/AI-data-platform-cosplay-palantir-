"""add encrypted source-system credential storage

Revision ID: d4e2a91c6b37
Revises: b7296f0c31ad
"""

from collections.abc import Sequence

import sqlalchemy as sa
from alembic import op

revision: str = "d4e2a91c6b37"
down_revision: str | Sequence[str] | None = "b7296f0c31ad"
branch_labels: str | Sequence[str] | None = None
depends_on: str | Sequence[str] | None = None


def upgrade() -> None:
    # The migration deliberately does not access or create a key. Existing
    # plaintext credentials are encrypted by IntegrationService on first use,
    # where the stable local model-profile.key is available.
    op.add_column(
        "source_systems",
        sa.Column("encrypted_connection_secrets", sa.Text(), nullable=True),
    )


def downgrade() -> None:
    connection = op.get_bind()
    has_encrypted_values = connection.execute(
        sa.text(
            "SELECT 1 FROM source_systems "
            "WHERE encrypted_connection_secrets IS NOT NULL LIMIT 1"
        )
    ).first()
    if has_encrypted_values:
        raise RuntimeError(
            "Cannot remove encrypted source credentials without a safe re-entry plan."
        )
    op.drop_column("source_systems", "encrypted_connection_secrets")
