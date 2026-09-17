"""add lookup indexes for exact and asset-scoped observations

Revision ID: b2c3d4e5f6a7
Revises: a1b2c3d4e5f6
Create Date: 2026-09-16 12:15:00.000000
"""

from collections.abc import Sequence

from alembic import op
from sqlalchemy import inspect

revision: str = "b2c3d4e5f6a7"
down_revision: str | Sequence[str] | None = "a1b2c3d4e5f6"
branch_labels: str | Sequence[str] | None = None
depends_on: str | Sequence[str] | None = None


def upgrade() -> None:
    existing = {
        item["name"]
        for item in inspect(op.get_bind()).get_indexes("observation_assertions")
    }
    if "ix_observation_assertion_source_key_lookup" not in existing:
        op.create_index(
            "ix_observation_assertion_source_key_lookup",
            "observation_assertions",
            ["project_id", "status", "source_record_key"],
        )
    if "ix_observation_assertion_asset_field_lookup" not in existing:
        op.create_index(
            "ix_observation_assertion_asset_field_lookup",
            "observation_assertions",
            ["project_id", "status", "source_asset", "field_key", "observed_at"],
        )


def downgrade() -> None:
    existing = {
        item["name"]
        for item in inspect(op.get_bind()).get_indexes("observation_assertions")
    }
    if "ix_observation_assertion_asset_field_lookup" in existing:
        op.drop_index(
            "ix_observation_assertion_asset_field_lookup",
            table_name="observation_assertions",
        )
    if "ix_observation_assertion_source_key_lookup" in existing:
        op.drop_index(
            "ix_observation_assertion_source_key_lookup",
            table_name="observation_assertions",
        )
