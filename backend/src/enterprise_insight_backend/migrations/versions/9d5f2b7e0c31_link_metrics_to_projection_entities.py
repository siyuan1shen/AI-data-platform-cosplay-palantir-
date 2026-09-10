"""link metrics to projection entities

Revision ID: 9d5f2b7e0c31
Revises: 8c4e1a6d9b20
Create Date: 2026-09-06 18:00:00.000000
"""

from collections.abc import Sequence
from datetime import UTC, datetime
from uuid import uuid4

import sqlalchemy as sa
from alembic import op

revision: str = "9d5f2b7e0c31"
down_revision: str | Sequence[str] | None = "8c4e1a6d9b20"
branch_labels: str | Sequence[str] | None = None
depends_on: str | Sequence[str] | None = None


def upgrade() -> None:
    with op.batch_alter_table("metric_definitions", schema=None) as batch_op:
        batch_op.add_column(sa.Column("entity_id", sa.String(length=36), nullable=True))

    bind = op.get_bind()
    metrics = sa.table(
        "metric_definitions",
        sa.column("id", sa.String()),
        sa.column("project_id", sa.String()),
        sa.column("entity_id", sa.String()),
        sa.column("key", sa.String()),
        sa.column("name", sa.String()),
        sa.column("scope", sa.String()),
        sa.column("direction", sa.String()),
        sa.column("unit", sa.String()),
        sa.column("target_value", sa.JSON()),
        sa.column("properties", sa.JSON()),
        sa.column("active", sa.Boolean()),
        sa.column("created_at", sa.DateTime(timezone=True)),
        sa.column("updated_at", sa.DateTime(timezone=True)),
    )
    entities = sa.table(
        "entities",
        sa.column("id", sa.String()),
        sa.column("project_id", sa.String()),
        sa.column("type_key", sa.String()),
        sa.column("stable_key", sa.String()),
        sa.column("name", sa.String()),
        sa.column("properties", sa.JSON()),
        sa.column("design_membership", sa.String()),
        sa.column("viewpoint", sa.String()),
        sa.column("evidence", sa.JSON()),
        sa.column("status", sa.String()),
        sa.column("revision", sa.Integer()),
        sa.column("created_at", sa.DateTime(timezone=True)),
        sa.column("updated_at", sa.DateTime(timezone=True)),
    )
    rows = bind.execute(sa.select(metrics)).mappings().all()
    touched_projects: set[str] = set()
    for metric in rows:
        stable_key = f"metric.{metric['key']}"
        existing = bind.execute(
            sa.select(entities.c.id, entities.c.type_key).where(
                entities.c.project_id == metric["project_id"],
                entities.c.stable_key == stable_key,
            )
        ).mappings().first()
        if existing is not None and existing["type_key"] == "metric":
            entity_id = existing["id"]
        else:
            if existing is not None:
                stable_key = f"metric_definition.{metric['id']}"
            id_in_use = bind.execute(
                sa.select(entities.c.id).where(entities.c.id == metric["id"])
            ).first()
            entity_id = metric["id"] if id_in_use is None else str(uuid4())
            properties = dict(metric["properties"] or {})
            properties.update(
                {
                    "scope": metric["scope"],
                    "direction": metric["direction"],
                    "target_value": metric["target_value"],
                    "active": bool(metric["active"]),
                }
            )
            if metric["unit"] is not None:
                properties["unit"] = metric["unit"]
            timestamp = metric["created_at"] or datetime.now(UTC)
            bind.execute(
                sa.insert(entities).values(
                    id=entity_id,
                    project_id=metric["project_id"],
                    type_key="metric",
                    stable_key=stable_key,
                    name=metric["name"],
                    properties=properties,
                    design_membership="MODELED",
                    viewpoint="DESIGNED",
                    evidence=[],
                    status="DRAFT",
                    revision=1,
                    created_at=timestamp,
                    updated_at=metric["updated_at"] or timestamp,
                )
            )
            touched_projects.add(metric["project_id"])
        bind.execute(
            sa.update(metrics)
            .where(metrics.c.id == metric["id"])
            .values(entity_id=entity_id)
        )

    projects = sa.table(
        "projects", sa.column("id", sa.String()), sa.column("revision", sa.Integer())
    )
    for project_id in touched_projects:
        bind.execute(
            sa.update(projects)
            .where(projects.c.id == project_id)
            .values(revision=projects.c.revision + 1)
        )

    with op.batch_alter_table("metric_definitions", schema=None) as batch_op:
        batch_op.alter_column("entity_id", existing_type=sa.String(length=36), nullable=False)
        batch_op.create_foreign_key(
            "fk_metric_definitions_entity_id_entities",
            "entities",
            ["entity_id"],
            ["id"],
            ondelete="RESTRICT",
        )
        batch_op.create_unique_constraint("uq_metric_definition_entity", ["entity_id"])
        batch_op.create_index(
            batch_op.f("ix_metric_definitions_entity_id"), ["entity_id"], unique=False
        )


def downgrade() -> None:
    with op.batch_alter_table("metric_definitions", schema=None) as batch_op:
        batch_op.drop_index(batch_op.f("ix_metric_definitions_entity_id"))
        batch_op.drop_constraint("uq_metric_definition_entity", type_="unique")
        batch_op.drop_constraint(
            "fk_metric_definitions_entity_id_entities", type_="foreignkey"
        )
        batch_op.drop_column("entity_id")
