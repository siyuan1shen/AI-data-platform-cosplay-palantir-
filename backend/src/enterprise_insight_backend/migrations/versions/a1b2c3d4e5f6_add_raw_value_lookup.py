"""add indexed scalar lookup tokens for raw source records

Revision ID: a1b2c3d4e5f6
Revises: f7a1c2d3e4f5
Create Date: 2026-09-16 12:00:00.000000
"""

import json
from collections.abc import Sequence
from datetime import UTC, datetime
from typing import Any

import sqlalchemy as sa
from alembic import op

revision: str = "a1b2c3d4e5f6"
down_revision: str | Sequence[str] | None = "f7a1c2d3e4f5"
branch_labels: str | Sequence[str] | None = None
depends_on: str | Sequence[str] | None = None


def upgrade() -> None:
    op.create_table(
        "raw_record_values",
        sa.Column("id", sa.String(length=36), nullable=False),
        sa.Column("project_id", sa.String(length=36), nullable=False),
        sa.Column("raw_record_id", sa.String(length=36), nullable=False),
        sa.Column("field_path", sa.String(length=500), nullable=False),
        sa.Column("value_text", sa.String(length=1000), nullable=False),
        sa.Column("value_kind", sa.String(length=32), nullable=False),
        sa.Column("created_at", sa.DateTime(timezone=True), nullable=False),
        sa.ForeignKeyConstraint(["project_id"], ["projects.id"], ondelete="CASCADE"),
        sa.ForeignKeyConstraint(["raw_record_id"], ["raw_records.id"], ondelete="CASCADE"),
        sa.PrimaryKeyConstraint("id"),
        sa.UniqueConstraint(
            "raw_record_id",
            "field_path",
            "value_text",
            name="uq_raw_record_value_path_text",
        ),
    )
    op.create_index(
        "ix_raw_record_value_project_text",
        "raw_record_values",
        ["project_id", "value_text", "raw_record_id"],
    )
    op.create_index(
        "ix_raw_record_value_record",
        "raw_record_values",
        ["raw_record_id"],
    )
    op.create_index(
        "ix_raw_record_values_project_id",
        "raw_record_values",
        ["project_id"],
    )
    op.create_index(
        "ix_raw_record_values_value_text",
        "raw_record_values",
        ["value_text"],
    )
    op.create_index(
        "ix_source_identity_lookup",
        "source_identities",
        ["project_id", "source_system_id", "source_asset", "source_record_key", "status"],
    )
    op.create_index(
        "ix_observation_assertion_source_lookup",
        "observation_assertions",
        ["project_id", "status", "source_asset", "source_record_key", "field_key"],
    )
    op.create_index(
        "ix_observation_assertion_entity_time",
        "observation_assertions",
        ["project_id", "entity_id", "status", "observed_at"],
    )

    _backfill_sqlite_values()


def downgrade() -> None:
    op.drop_index(
        "ix_observation_assertion_entity_time",
        table_name="observation_assertions",
    )
    op.drop_index(
        "ix_observation_assertion_source_lookup",
        table_name="observation_assertions",
    )
    op.drop_index("ix_source_identity_lookup", table_name="source_identities")
    op.drop_index("ix_raw_record_values_value_text", table_name="raw_record_values")
    op.drop_index("ix_raw_record_values_project_id", table_name="raw_record_values")
    op.drop_index("ix_raw_record_value_record", table_name="raw_record_values")
    op.drop_index("ix_raw_record_value_project_text", table_name="raw_record_values")
    op.drop_table("raw_record_values")


def _backfill_sqlite_values() -> None:
    bind = op.get_bind()
    if bind.dialect.name != "sqlite":
        return

    records = bind.execute(
        sa.text(
            "SELECT id, project_id, payload, source_record_key "
            "FROM raw_records ORDER BY id"
        )
    ).mappings()
    now = datetime.now(UTC)
    for record in records:
        payload = record["payload"]
        if isinstance(payload, str):
            try:
                payload = json.loads(payload)
            except json.JSONDecodeError:
                payload = {}
        values = _scalar_values(payload)
        source_key = record["source_record_key"]
        if source_key:
            values.append(("$.__record_key__", str(source_key).strip().casefold()))
        seen: set[tuple[str, str]] = set()
        for field_path, value_text in values:
            if (field_path, value_text) in seen:
                continue
            seen.add((field_path, value_text))
            bind.execute(
                sa.text(
                    "INSERT OR IGNORE INTO raw_record_values "
                    "(id, project_id, raw_record_id, field_path, value_text, "
                    "value_kind, created_at) VALUES "
                    "(:id, :project_id, :raw_record_id, :field_path, :value_text, "
                    ":value_kind, :created_at)"
                ),
                {
                    "id": _stable_id(record["id"], field_path, value_text),
                    "project_id": record["project_id"],
                    "raw_record_id": record["id"],
                    "field_path": field_path,
                    "value_text": value_text,
                    "value_kind": "SCALAR",
                    "created_at": now,
                },
            )


def _scalar_values(value: Any, path: str = "$") -> list[tuple[str, str]]:
    if isinstance(value, dict):
        result: list[tuple[str, str]] = []
        for key, nested in value.items():
            result.extend(_scalar_values(nested, f"{path}.{key}"))
        return result
    if isinstance(value, list):
        result = []
        for ordinal, nested in enumerate(value):
            result.extend(_scalar_values(nested, f"{path}[{ordinal}]"))
        return result
    if value is None:
        return []
    text = str(value).strip().casefold()
    return [(path, text)] if text and len(text) <= 1000 else []


def _stable_id(raw_record_id: str, field_path: str, value_text: str) -> str:
    import hashlib

    return hashlib.sha256(
        f"{raw_record_id}\0{field_path}\0{value_text}".encode()
    ).hexdigest()[:36]
