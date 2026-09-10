from __future__ import annotations

import builtins
import json
from collections import defaultdict, deque
from datetime import datetime
from typing import Any
from uuid import UUID

from sqlalchemy import select
from sqlalchemy.orm import Session

from enterprise_insight_backend.errors import DomainError
from enterprise_insight_backend.models import (
    MaterializationRunRow,
    MetricObservationRow,
    ObservationAssertionRow,
    ObservationConflictRow,
    PublicationRow,
    QuerySnapshotRow,
    RawBatchRow,
)
from enterprise_insight_backend.portfolio import PortfolioService
from enterprise_insight_backend.projection import ProjectionService
from enterprise_insight_backend.schemas import (
    EntityView,
    GraphQuery,
    GraphView,
    QueryModelScope,
    QuerySnapshotCreate,
    QuerySnapshotView,
)
from enterprise_insight_backend.service_utils import json_ready, now_utc


class QuerySnapshotService:
    """Resolve moving model/data pointers into one immutable query baseline."""

    def __init__(self, session: Session) -> None:
        self.session = session
        self.portfolio = PortfolioService(session)
        self.projection = ProjectionService(session)

    def list(self, project_id: UUID) -> builtins.list[QuerySnapshotView]:
        self.portfolio.require_project(project_id)
        rows = self.session.scalars(
            select(QuerySnapshotRow)
            .where(QuerySnapshotRow.project_id == str(project_id))
            .order_by(QuerySnapshotRow.created_at.desc(), QuerySnapshotRow.id.desc())
        ).all()
        return [QuerySnapshotView.model_validate(row) for row in rows]

    def create(self, project_id: UUID, payload: QuerySnapshotCreate) -> QuerySnapshotView:
        project = self.portfolio.require_project(project_id)
        cutoff = payload.data_cutoff or now_utc()
        publication: PublicationRow | None = None
        if payload.model_scope == QueryModelScope.PUBLISHED:
            publication = self._publication(project_id, payload.publication_id)
            graph = GraphView.model_validate(publication.graph_snapshot)
            graph = graph.model_copy(update={"release_id": UUID(publication.id)})
            project_revision = publication.project_revision
        else:
            if payload.publication_id is not None:
                raise DomainError(
                    "QUERY_SCOPE_INVALID",
                    "草稿查询快照不能指定发布版本。",
                    status_code=422,
                )
            graph = self.projection.graph(
                project_id,
                GraphQuery(include_retired=False, include_observations=False),
            )
            project_revision = project.revision
        assertion_rows = self._assertions(project_id, graph, cutoff)
        conflict_rows = self.session.scalars(
            select(ObservationConflictRow).where(
                ObservationConflictRow.project_id == str(project_id),
                ObservationConflictRow.updated_at <= cutoff,
            )
        ).all()
        if payload.include_observations:
            graph = self._apply_observations(
                graph,
                assertion_rows,
                [item for item in conflict_rows if item.status == "RESOLVED"],
            )
        metric_ids = self._metric_observation_ids(project_id, cutoff)
        materialization_ids = list(
            self.session.scalars(
                select(MaterializationRunRow.id).where(
                    MaterializationRunRow.project_id == str(project_id),
                    MaterializationRunRow.status == "COMPLETED",
                    MaterializationRunRow.finished_at <= cutoff,
                )
            ).all()
        )
        raw_batch_ids = list(
            self.session.scalars(
                select(RawBatchRow.id).where(
                    RawBatchRow.project_id == str(project_id),
                    RawBatchRow.created_at <= cutoff,
                )
            ).all()
        )
        row = QuerySnapshotRow(
            project_id=str(project_id),
            model_scope=payload.model_scope.value,
            publication_id=publication.id if publication is not None else None,
            project_revision=project_revision,
            data_cutoff=cutoff,
            graph_snapshot=json_ready(graph.model_dump(mode="json")),
            manifest={
                "observation_assertion_ids": [item.id for item in assertion_rows],
                "observation_conflict_ids": [item.id for item in conflict_rows],
                "metric_observation_ids": metric_ids,
                "raw_batch_ids": raw_batch_ids,
                "materialization_run_ids": materialization_ids,
                "include_observations": payload.include_observations,
            },
        )
        self.session.add(row)
        self.session.flush()
        return QuerySnapshotView.model_validate(row)

    def _metric_observation_ids(self, project_id: UUID, cutoff: datetime) -> builtins.list[str]:
        rows = self.session.scalars(
            select(MetricObservationRow)
            .where(
                MetricObservationRow.project_id == str(project_id),
                MetricObservationRow.created_at <= cutoff,
            )
            .order_by(
                MetricObservationRow.metric_definition_id,
                MetricObservationRow.period_key,
                MetricObservationRow.dimension_key,
                MetricObservationRow.source,
                MetricObservationRow.version.desc(),
            )
        ).all()
        current: dict[tuple[str, str, str, str], MetricObservationRow] = {}
        for item in rows:
            key = (
                item.metric_definition_id,
                item.period_key,
                item.dimension_key,
                item.source,
            )
            current.setdefault(key, item)
        return [item.id for item in current.values() if item.record_status != "RETRACTED"]

    def get(self, project_id: UUID, snapshot_id: UUID) -> QuerySnapshotView:
        return QuerySnapshotView.model_validate(self.require(project_id, snapshot_id))

    def require(self, project_id: UUID | str, snapshot_id: UUID | str) -> QuerySnapshotRow:
        row = self.session.get(QuerySnapshotRow, str(snapshot_id))
        if row is None or row.project_id != str(project_id):
            raise DomainError("QUERY_SNAPSHOT_NOT_FOUND", "查询快照不存在。", status_code=404)
        return row

    def graph(
        self,
        project_id: UUID,
        snapshot_id: UUID,
        query: GraphQuery | None = None,
    ) -> GraphView:
        row = self.require(project_id, snapshot_id)
        graph = GraphView.model_validate(row.graph_snapshot).model_copy(
            update={"query_snapshot_id": UUID(row.id)}
        )
        return self._filter(graph, query or GraphQuery())

    def _publication(
        self, project_id: UUID, publication_id: UUID | None
    ) -> PublicationRow:
        if publication_id is None:
            row = self.session.scalar(
                select(PublicationRow)
                .where(PublicationRow.project_id == str(project_id))
                .order_by(PublicationRow.version.desc())
                .limit(1)
            )
        else:
            row = self.session.get(PublicationRow, str(publication_id))
        if row is None or row.project_id != str(project_id):
            raise DomainError(
                "PUBLICATION_NOT_FOUND",
                "创建已发布查询快照前必须存在对应发布版本。",
                status_code=404,
            )
        return row

    def _assertions(
        self, project_id: UUID, graph: GraphView, cutoff: datetime
    ) -> builtins.list[ObservationAssertionRow]:
        entity_ids = [str(item.id) for item in graph.entities]
        if not entity_ids:
            return []
        return list(
            self.session.scalars(
                select(ObservationAssertionRow)
                .where(
                    ObservationAssertionRow.project_id == str(project_id),
                    ObservationAssertionRow.entity_id.in_(entity_ids),
                    ObservationAssertionRow.observed_at <= cutoff,
                    ObservationAssertionRow.created_at <= cutoff,
                )
                .order_by(
                    ObservationAssertionRow.observed_at.desc(),
                    ObservationAssertionRow.created_at.desc(),
                    ObservationAssertionRow.id.desc(),
                )
            ).all()
        )

    @staticmethod
    def _apply_observations(
        graph: GraphView,
        assertions: builtins.list[ObservationAssertionRow],
        resolutions: builtins.list[ObservationConflictRow],
    ) -> GraphView:
        by_entity: dict[str, builtins.list[ObservationAssertionRow]] = defaultdict(list)
        resolutions_by_entity: dict[str, dict[str, ObservationConflictRow]] = defaultdict(dict)
        for assertion in assertions:
            by_entity[assertion.entity_id].append(assertion)
        for resolution in resolutions:
            resolutions_by_entity[resolution.entity_id][resolution.field_key] = resolution
        entities = [
            QuerySnapshotService._observed_entity(
                item,
                by_entity[str(item.id)],
                resolutions_by_entity[str(item.id)],
            )
            for item in graph.entities
        ]
        return graph.model_copy(update={"entities": entities})

    @staticmethod
    def _observed_entity(
        entity: EntityView,
        assertions: builtins.list[ObservationAssertionRow],
        resolutions: dict[str, ObservationConflictRow],
    ) -> EntityView:
        latest: dict[tuple[str, str], ObservationAssertionRow] = {}
        for item in assertions:
            latest.setdefault((item.source_identity_id, item.field_key), item)
        by_field: dict[str, builtins.list[ObservationAssertionRow]] = defaultdict(list)
        for item in latest.values():
            by_field[item.field_key].append(item)
        resolved: dict[str, Any] = {}
        conflicts: dict[str, builtins.list[dict[str, Any]]] = {}
        resolved_conflicts: dict[str, dict[str, Any]] = {}
        ignored_conflicts: dict[str, builtins.list[dict[str, Any]]] = {}
        for field_key, candidates in by_field.items():
            priority = max(item.authority_priority for item in candidates)
            authoritative = [item for item in candidates if item.authority_priority == priority]
            values: dict[str, Any] = {}
            for item in authoritative:
                fingerprint = json.dumps(item.value, sort_keys=True, default=str)
                values.setdefault(fingerprint, item.value)
            if len(values) == 1:
                resolved[field_key] = next(iter(values.values()))
            else:
                conflict_candidates = [
                    {
                        "assertion_id": item.id,
                        "value": item.value,
                        "source_identity_id": item.source_identity_id,
                        "authority_priority": item.authority_priority,
                        "observed_at": item.observed_at.isoformat(),
                    }
                    for item in authoritative
                ]
                resolution = resolutions.get(field_key)
                if resolution is None:
                    conflicts[field_key] = conflict_candidates
                elif resolution.resolution_kind == "CHOOSE_ASSERTION":
                    chosen = next(
                        (
                            item
                            for item in authoritative
                            if item.id == resolution.chosen_assertion_id
                        ),
                        None,
                    )
                    if chosen is None:
                        conflicts[field_key] = conflict_candidates
                    else:
                        resolved[field_key] = chosen.value
                        resolved_conflicts[field_key] = {
                            "resolution_kind": resolution.resolution_kind,
                            "chosen_assertion_id": chosen.id,
                            "rationale": resolution.rationale,
                            "candidates": conflict_candidates,
                        }
                elif resolution.resolution_kind == "OVERRIDE":
                    resolved[field_key] = resolution.override_value
                    resolved_conflicts[field_key] = {
                        "resolution_kind": resolution.resolution_kind,
                        "rationale": resolution.rationale,
                        "candidates": conflict_candidates,
                    }
                else:
                    ignored_conflicts[field_key] = conflict_candidates
        observed_name_value = resolved.pop("__name__", None)
        observed_name = str(observed_name_value) if observed_name_value is not None else None
        comparison: dict[str, Any] = {}
        if observed_name is not None:
            comparison["__name__"] = {
                "design": entity.name,
                "observed": observed_name,
                "status": "MATCH" if observed_name == entity.name else "DIVERGED",
            }
        for key, value in resolved.items():
            design_value = entity.properties.get(key)
            comparison[key] = {
                "design": design_value,
                "observed": value,
                "status": (
                    "OBSERVED_ONLY"
                    if key not in entity.properties
                    else "MATCH"
                    if design_value == value
                    else "DIVERGED"
                ),
            }
        for key, conflict_candidates in conflicts.items():
            comparison[key] = {
                "design": entity.name if key == "__name__" else entity.properties.get(key),
                "observed": None,
                "status": "CONFLICT",
                "candidates": conflict_candidates,
            }
        for key, metadata in resolved_conflicts.items():
            comparison[key] = {"status": "RESOLVED_CONFLICT", **metadata}
        for key, ignored_candidates in ignored_conflicts.items():
            comparison[key] = {
                "design": entity.name if key == "__name__" else entity.properties.get(key),
                "observed": None,
                "status": "IGNORED_CONFLICT",
                "candidates": ignored_candidates,
            }
        return entity.model_copy(
            update={
                "observed_name": observed_name,
                "observed_properties": resolved,
                "comparison": comparison,
            }
        )

    @staticmethod
    def _filter(graph: GraphView, query: GraphQuery) -> GraphView:
        if query.release_id is not None:
            raise DomainError(
                "QUERY_SCOPE_INVALID",
                "查询快照与发布版本不能同时指定。",
                status_code=422,
            )
        entities = list(graph.entities)
        relations = list(graph.relations)
        if query.type_keys:
            entities = [item for item in entities if item.type_key in query.type_keys]
        if query.search:
            needle = query.search.casefold()
            entities = [
                item
                for item in entities
                if needle in item.name.casefold()
                or (item.stable_key and needle in item.stable_key.casefold())
            ]
        if query.viewpoints:
            entities = [item for item in entities if item.viewpoint in query.viewpoints]
            relations = [item for item in relations if item.viewpoint in query.viewpoints]
        if query.relation_type_keys:
            relations = [item for item in relations if item.type_key in query.relation_type_keys]
        visible = {item.id for item in entities}
        relations = [
            item
            for item in relations
            if all(participant.entity_id in visible for participant in item.participants)
        ]
        if query.root_entity_id is not None:
            visible = QuerySnapshotService._neighborhood(
                query.root_entity_id, query.depth, visible, relations
            )
            entities = [item for item in entities if item.id in visible]
            relations = [
                item
                for item in relations
                if all(participant.entity_id in visible for participant in item.participants)
            ]
        return graph.model_copy(update={"entities": entities, "relations": relations})

    @staticmethod
    def _neighborhood(
        root_id: UUID, depth: int, entity_ids: set[UUID], relations: builtins.list[Any]
    ) -> set[UUID]:
        if root_id not in entity_ids:
            raise DomainError("ROOT_ENTITY_NOT_FOUND", "图查询起点不存在。", status_code=404)
        adjacency: dict[UUID, set[UUID]] = defaultdict(set)
        for relation in relations:
            ids = [item.entity_id for item in relation.participants]
            for source in ids:
                adjacency[source].update(target for target in ids if target != source)
        seen = {root_id}
        queue: deque[tuple[UUID, int]] = deque([(root_id, 0)])
        while queue:
            current, level = queue.popleft()
            if level >= depth:
                continue
            for target in adjacency[current]:
                if target not in seen:
                    seen.add(target)
                    queue.append((target, level + 1))
        return seen
