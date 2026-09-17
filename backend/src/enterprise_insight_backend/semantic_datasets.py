from __future__ import annotations

import builtins
import csv
import io
import json
from collections import defaultdict
from pathlib import Path
from typing import Any
from uuid import UUID

from sqlalchemy import select
from sqlalchemy.exc import IntegrityError
from sqlalchemy.orm import Session

from enterprise_insight_backend.config import Settings
from enterprise_insight_backend.errors import DomainError
from enterprise_insight_backend.exporting import atomic_write
from enterprise_insight_backend.models import (
    ExportJobRow,
    SemanticDatasetRow,
    SemanticQueryRunRow,
)
from enterprise_insight_backend.ontology import OntologyService
from enterprise_insight_backend.portfolio import PortfolioService
from enterprise_insight_backend.query_snapshots import QuerySnapshotService
from enterprise_insight_backend.schemas import (
    EntityView,
    ExportJobView,
    QueryModelScope,
    QuerySnapshotCreate,
    SemanticAggregation,
    SemanticDatasetColumn,
    SemanticDatasetCreate,
    SemanticDatasetExport,
    SemanticDatasetQuery,
    SemanticDatasetResult,
    SemanticDatasetUpdate,
    SemanticDatasetView,
    SemanticValueLayer,
    TypeKind,
)
from enterprise_insight_backend.service_utils import json_ready, require_revision


class SemanticDatasetService:
    """Execute typed graph paths without multiplying root rows through joins."""

    def __init__(self, session: Session, settings: Settings | None = None) -> None:
        self.session = session
        self.settings = settings
        self.portfolio = PortfolioService(session)
        self.ontology = OntologyService(session)
        self.snapshots = QuerySnapshotService(session)

    def list(self, project_id: UUID) -> builtins.list[SemanticDatasetView]:
        self.portfolio.require_project(project_id)
        rows = self.session.scalars(
            select(SemanticDatasetRow)
            .where(SemanticDatasetRow.project_id == str(project_id))
            .order_by(SemanticDatasetRow.name, SemanticDatasetRow.key)
        ).all()
        return [SemanticDatasetView.model_validate(row) for row in rows]

    def create(
        self, project_id: UUID, payload: SemanticDatasetCreate
    ) -> SemanticDatasetView:
        self.portfolio.require_project(project_id)
        self._validate(project_id, payload.root_type_key, payload.columns)
        row = SemanticDatasetRow(
            project_id=str(project_id),
            **json_ready(payload.model_dump(mode="json")),
        )
        self.session.add(row)
        try:
            self.session.flush()
        except IntegrityError as exc:
            raise DomainError(
                "SEMANTIC_DATASET_KEY_EXISTS",
                "语义数据集标识已存在。",
                status_code=409,
            ) from exc
        return SemanticDatasetView.model_validate(row)

    def update(
        self,
        project_id: UUID,
        dataset_id: UUID,
        payload: SemanticDatasetUpdate,
    ) -> SemanticDatasetView:
        row = self.require(project_id, dataset_id)
        require_revision(row.revision, payload.expected_revision, resource="语义数据集")
        values = payload.model_dump(exclude_unset=True, exclude={"expected_revision"})
        columns = payload.columns if payload.columns is not None else [
            SemanticDatasetColumn.model_validate(item) for item in row.columns
        ]
        self._validate(project_id, row.root_type_key, columns)
        for key, value in json_ready(values).items():
            setattr(row, key, value)
        row.revision += 1
        self.session.flush()
        return SemanticDatasetView.model_validate(row)

    def query(
        self,
        project_id: UUID,
        dataset_id: UUID,
        payload: SemanticDatasetQuery,
    ) -> SemanticDatasetResult:
        dataset = self.require(project_id, dataset_id)
        if dataset.status != "ACTIVE":
            raise DomainError(
                "SEMANTIC_DATASET_RETIRED", "语义数据集已退役。", status_code=409
            )
        columns = [SemanticDatasetColumn.model_validate(item) for item in dataset.columns]
        if payload.query_snapshot_id is None:
            snapshot = self.snapshots.create(
                project_id,
                QuerySnapshotCreate(
                    model_scope=QueryModelScope.DRAFT,
                    include_observations=True,
                ),
            )
            snapshot_id = snapshot.id
        else:
            self.snapshots.require(project_id, payload.query_snapshot_id)
            snapshot_id = payload.query_snapshot_id
        graph = self.snapshots.graph(project_id, snapshot_id)
        entities = {str(item.id): item for item in graph.entities}
        available_roots = sorted(
            (item for item in graph.entities if item.type_key == dataset.root_type_key),
            # A snapshot preserves entity creation order. Use it for pagination
            # so page boundaries remain intuitive even when human-readable
            # names use a locale whose collation is not available to Python.
            key=lambda item: (item.created_at, str(item.id)),
        )
        total_rows = len(available_roots)
        roots = available_roots[payload.offset : payload.offset + payload.limit]
        relation_index: dict[tuple[str, str, str], dict[str, set[str]]] = defaultdict(
            lambda: defaultdict(set)
        )
        required_relation_types = {
            step.relation_type_key
            for column in columns
            for step in column.path
        }
        for relation in graph.relations:
            if relation.type_key not in required_relation_types:
                continue
            participants_by_role: dict[str, builtins.list[str]] = defaultdict(list)
            for participant in relation.participants:
                participants_by_role[participant.role_key].append(str(participant.entity_id))
            for from_role, source_ids in participants_by_role.items():
                for to_role, role_target_ids in participants_by_role.items():
                    if from_role == to_role:
                        continue
                    edge = relation_index[(relation.type_key, from_role, to_role)]
                    for source_id in source_ids:
                        edge[source_id].update(role_target_ids)
        warnings: builtins.list[dict[str, Any]] = []
        rows: builtins.list[dict[str, Any]] = []
        for root in roots:
            result: dict[str, Any] = {"root_entity_id": str(root.id)}
            lineage: dict[str, builtins.list[str]] = {}
            for column in columns:
                target_ids = {str(root.id)}
                for step in column.path:
                    edge = relation_index[
                        (step.relation_type_key, step.from_role, step.to_role)
                    ]
                    target_ids = {
                        target
                        for source_id in target_ids
                        for target in edge.get(source_id, set())
                    }
                targets = [entities[item] for item in sorted(target_ids) if item in entities]
                values = [
                    self._value(item, column.property_key, column.value_layer)
                    for item in targets
                ]
                non_null = [item for item in values if item is not None]
                result[column.key] = self._aggregate(
                    column,
                    non_null,
                    target_ids,
                    root,
                    warnings,
                )
                lineage[column.key] = sorted(target_ids)
            if payload.include_lineage:
                result["_lineage"] = lineage
            rows.append(result)
        plan = {
            "root_type_key": dataset.root_type_key,
            "root_rows": total_rows,
            "offset": payload.offset,
            "limit": payload.limit,
            "returned_rows": len(rows),
            "next_offset": (
                payload.offset + len(rows)
                if payload.offset + len(rows) < total_rows
                else None
            ),
            "truncated": payload.offset + len(rows) < total_rows,
            "row_cardinality": "ONE_ROW_PER_DISTINCT_ROOT_ENTITY",
            "join_safety": "PATH_TARGETS_DEDUPLICATED_BY_ENTITY_ID",
            "columns": [item.model_dump(mode="json") for item in columns],
        }
        run = SemanticQueryRunRow(
            project_id=str(project_id),
            dataset_id=dataset.id,
            query_snapshot_id=str(snapshot_id),
            row_count=len(rows),
            plan=json_ready(plan),
            warnings=json_ready(warnings),
        )
        self.session.add(run)
        self.session.flush()
        return SemanticDatasetResult.model_validate(
            {
                "run_id": run.id,
                "dataset_id": dataset.id,
                "query_snapshot_id": snapshot_id,
                "total_rows": total_rows,
                "offset": payload.offset,
                "limit": payload.limit,
                "next_offset": plan["next_offset"],
                "truncated": plan["truncated"],
                "columns": columns,
                "rows": rows,
                "warnings": warnings,
                "plan": plan,
            }
        )

    def export(
        self,
        project_id: UUID,
        dataset_id: UUID,
        payload: SemanticDatasetExport,
    ) -> ExportJobView:
        if self.settings is None:
            raise RuntimeError("settings are required for semantic dataset export")
        result = self.query(
            project_id,
            dataset_id,
            SemanticDatasetQuery(
                query_snapshot_id=payload.query_snapshot_id,
                limit=5000,
                include_lineage=True,
            ),
        )
        job = ExportJobRow(
            project_id=str(project_id),
            format=payload.format,
            request_payload={
                "kind": "SEMANTIC_DATASET",
                "dataset_id": str(dataset_id),
                "query_run_id": str(result.run_id),
                "query_snapshot_id": str(result.query_snapshot_id),
            },
            status="RUNNING",
        )
        self.session.add(job)
        self.session.flush()
        path = self._export_path(job)
        path.parent.mkdir(parents=True, exist_ok=True)
        if payload.format == "json":
            atomic_write(
                path,
                lambda temporary_path: temporary_path.write_text(
                    json.dumps(result.model_dump(mode="json"), ensure_ascii=False, indent=2),
                    encoding="utf-8",
                ),
            )
        else:
            atomic_write(
                path,
                lambda temporary_path: temporary_path.write_text(
                    self._csv(result), encoding="utf-8-sig"
                ),
            )
        job.status = "COMPLETED"
        job.download_url = (
            f"{self.settings.api_prefix}/projects/{project_id}/exports/{job.id}/download"
        )
        self.session.flush()
        return ExportJobView.model_validate(job)

    def require(self, project_id: UUID | str, dataset_id: UUID | str) -> SemanticDatasetRow:
        row = self.session.get(SemanticDatasetRow, str(dataset_id))
        if row is None or row.project_id != str(project_id):
            raise DomainError(
                "SEMANTIC_DATASET_NOT_FOUND", "语义数据集不存在。", status_code=404
            )
        return row

    def _validate(
        self,
        project_id: UUID,
        root_type_key: str,
        columns: builtins.list[SemanticDatasetColumn],
    ) -> None:
        root_type = self.ontology.require_type_by_key(project_id, root_type_key)
        if root_type.kind not in {TypeKind.OBJECT.value, TypeKind.METRIC.value}:
            raise DomainError(
                "SEMANTIC_DATASET_ROOT_INVALID",
                "语义数据集根类型必须是对象或指标。",
                status_code=422,
            )
        keys = [item.key for item in columns]
        if len(keys) != len(set(keys)):
            raise DomainError(
                "SEMANTIC_DATASET_COLUMN_DUPLICATE",
                "语义数据集列标识不能重复。",
                status_code=422,
            )
        for column in columns:
            reachable_type_keys: set[str] | None = {root_type.key}
            for step in column.path:
                relation = self.ontology.require_type_by_key(
                    project_id, step.relation_type_key
                )
                if relation.kind != TypeKind.RELATION.value:
                    raise DomainError(
                        "SEMANTIC_PATH_RELATION_REQUIRED",
                        "语义路径必须使用关系类型。",
                        status_code=422,
                    )
                role_keys = {item["key"] for item in relation.relation_roles}
                if step.from_role not in role_keys or step.to_role not in role_keys:
                    raise DomainError(
                        "SEMANTIC_PATH_ROLE_INVALID",
                        "语义路径引用了关系中不存在的角色。",
                        status_code=422,
                    )
                role_by_key = {item["key"]: item for item in relation.relation_roles}
                source_type_keys = set(
                    role_by_key[step.from_role].get("allowed_type_keys", [])
                )
                if (
                    source_type_keys
                    and reachable_type_keys is not None
                    and not reachable_type_keys.intersection(source_type_keys)
                ):
                    raise DomainError(
                        "SEMANTIC_PATH_SOURCE_TYPE_INVALID",
                        "语义路径的当前对象类型不能作为该关系角色的来源。",
                        status_code=422,
                        details=[
                            {
                                "relation_type_key": step.relation_type_key,
                                "from_role": step.from_role,
                                "current_type_keys": sorted(reachable_type_keys),
                                "allowed_type_keys": sorted(source_type_keys),
                            }
                        ],
                    )
                target_type_keys = set(role_by_key[step.to_role].get("allowed_type_keys", []))
                reachable_type_keys = target_type_keys or None
            if column.property_key in {"__id__", "__name__", "__stable_key__"}:
                continue
            if reachable_type_keys is None:
                # A relation role without allowed_type_keys intentionally
                # accepts any object type, so the property cannot be checked
                # statically. Query-time values still remain explicit.
                continue
            missing_types: list[str] = []
            for type_key in sorted(reachable_type_keys):
                target_type = self.ontology.require_type_by_key(project_id, type_key)
                property_keys = {item["key"] for item in target_type.properties}
                if column.property_key not in property_keys:
                    missing_types.append(type_key)
            if missing_types:
                raise DomainError(
                    "SEMANTIC_PROPERTY_NOT_FOUND",
                    "语义数据集列引用了目标本体中不存在的属性。",
                    status_code=422,
                    details=[
                        {
                            "column_key": column.key,
                            "property_key": column.property_key,
                            "target_type_keys": sorted(reachable_type_keys),
                            "missing_type_keys": missing_types,
                        }
                    ],
                )

    @staticmethod
    def _value(entity: EntityView, key: str, layer: SemanticValueLayer) -> Any:
        if key == "__id__":
            return str(entity.id)
        if key == "__name__":
            if layer != SemanticValueLayer.DESIGNED and entity.observed_name is not None:
                return entity.observed_name
            return entity.name
        if key == "__stable_key__":
            return entity.stable_key
        if layer == SemanticValueLayer.OBSERVED:
            return entity.observed_properties.get(key)
        if layer == SemanticValueLayer.RESOLVED and key in entity.observed_properties:
            return entity.observed_properties[key]
        return entity.properties.get(key)

    @staticmethod
    def _aggregate(
        column: SemanticDatasetColumn,
        values: builtins.list[Any],
        target_ids: set[str],
        root: EntityView,
        warnings: builtins.list[dict[str, Any]],
    ) -> Any:
        if column.aggregation == SemanticAggregation.COUNT_DISTINCT:
            return len(target_ids)
        if column.aggregation == SemanticAggregation.NONE:
            if len(values) <= 1:
                return values[0] if values else None
            warnings.append(
                {
                    "code": "SEMANTIC_CARDINALITY_AMBIGUOUS",
                    "root_entity_id": str(root.id),
                    "column_key": column.key,
                    "target_count": len(target_ids),
                    "message": "路径得到多个对象；请显式选择聚合口径。",
                }
            )
            return None
        numeric: builtins.list[float] = []
        for value in values:
            if isinstance(value, bool) or not isinstance(value, (int, float)):
                warnings.append(
                    {
                        "code": "SEMANTIC_AGGREGATION_NON_NUMERIC",
                        "root_entity_id": str(root.id),
                        "column_key": column.key,
                        "message": "数值聚合遇到非数值，结果留空。",
                    }
                )
                return None
            numeric.append(float(value))
        if not numeric:
            return None
        if column.aggregation == SemanticAggregation.SUM:
            return sum(numeric)
        if column.aggregation == SemanticAggregation.AVG:
            return sum(numeric) / len(numeric)
        if column.aggregation == SemanticAggregation.MIN:
            return min(numeric)
        return max(numeric)

    def _export_path(self, row: ExportJobRow) -> Path:
        assert self.settings is not None
        return self.settings.data_dir / "exports" / f"{row.project_id}-{row.id}.{row.format}"

    @staticmethod
    def _csv(result: SemanticDatasetResult) -> str:
        output = io.StringIO(newline="")
        fields = ["root_entity_id", *[item.key for item in result.columns], "_lineage"]
        writer = csv.DictWriter(output, fieldnames=fields)
        writer.writeheader()
        for row in result.rows:
            writer.writerow(
                {
                    key: (
                        json.dumps(value, ensure_ascii=False, default=str)
                        if isinstance(value, (dict, list))
                        else value
                    )
                    for key, value in row.items()
                }
            )
        return output.getvalue()
