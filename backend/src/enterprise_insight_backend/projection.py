from __future__ import annotations

import json
from collections import defaultdict, deque
from uuid import UUID

from pydantic import ValidationError
from sqlalchemy import func, or_, select
from sqlalchemy.exc import IntegrityError
from sqlalchemy.orm import Session, selectinload

from enterprise_insight_backend.errors import DomainError
from enterprise_insight_backend.evidence_validation import validate_evidence_references
from enterprise_insight_backend.models import (
    EntityRow,
    EventRow,
    ObservationAssertionRow,
    ObservationConflictRow,
    RelationParticipantRow,
    RelationRow,
    ScenarioRow,
)
from enterprise_insight_backend.ontology import OntologyService
from enterprise_insight_backend.portfolio import PortfolioService
from enterprise_insight_backend.schemas import (
    ChangeOperation,
    ChangeOperationKind,
    DesignMembership,
    EntityCreate,
    EntityUpdate,
    EntityView,
    EventCreate,
    EventView,
    GraphQuery,
    GraphView,
    LifecycleStatus,
    RelationCreate,
    RelationParticipantInput,
    RelationParticipantView,
    RelationUpdate,
    RelationView,
    TypeKind,
)
from enterprise_insight_backend.service_utils import json_ready, now_utc, require_revision


class ProjectionService:
    def __init__(self, session: Session) -> None:
        self.session = session
        self.ontology = OntologyService(session)
        self.portfolio = PortfolioService(session)

    def list_entities(
        self,
        project_id: UUID,
        *,
        type_key: str | None = None,
        type_keys: list[str] | None = None,
        entity_ids: set[str] | None = None,
        viewpoints: list[str] | None = None,
        search: str | None = None,
        include_retired: bool = False,
        include_unmodeled: bool = False,
        include_observations: bool = True,
        limit: int | None = None,
        offset: int = 0,
    ) -> list[EntityView]:
        self.portfolio.require_project(project_id)
        statement = select(EntityRow).where(EntityRow.project_id == str(project_id))
        if type_key:
            statement = statement.where(EntityRow.type_key == type_key)
        if type_keys:
            statement = statement.where(EntityRow.type_key.in_(type_keys))
        if entity_ids is not None:
            if not entity_ids:
                return []
            statement = statement.where(EntityRow.id.in_(entity_ids))
        if viewpoints:
            statement = statement.where(EntityRow.viewpoint.in_(viewpoints))
        if search:
            statement = statement.where(
                or_(
                    EntityRow.name.ilike(f"%{search}%"),
                    EntityRow.stable_key.ilike(f"%{search}%"),
                )
            )
        if not include_retired:
            statement = statement.where(EntityRow.status != LifecycleStatus.RETIRED.value)
        if not include_unmodeled:
            statement = statement.where(
                EntityRow.design_membership == DesignMembership.MODELED.value
            )
        statement = statement.order_by(EntityRow.type_key, EntityRow.name)
        if offset:
            statement = statement.offset(offset)
        if limit is not None:
            statement = statement.limit(limit)
        rows = self.session.scalars(statement).all()
        return self._entity_views(list(rows), include_observations=include_observations)

    def count_entities(
        self,
        project_id: UUID,
        *,
        type_key: str | None = None,
        search: str | None = None,
        include_retired: bool = False,
        include_unmodeled: bool = False,
    ) -> int:
        self.portfolio.require_project(project_id)
        statement = select(func.count(EntityRow.id)).where(
            EntityRow.project_id == str(project_id)
        )
        if type_key:
            statement = statement.where(EntityRow.type_key == type_key)
        if search:
            statement = statement.where(
                or_(
                    EntityRow.name.ilike(f"%{search}%"),
                    EntityRow.stable_key.ilike(f"%{search}%"),
                )
            )
        if not include_retired:
            statement = statement.where(EntityRow.status != LifecycleStatus.RETIRED.value)
        if not include_unmodeled:
            statement = statement.where(
                EntityRow.design_membership == DesignMembership.MODELED.value
            )
        return int(self.session.scalar(statement) or 0)

    def create_entity(
        self,
        project_id: UUID,
        payload: EntityCreate,
        *,
        entity_id: UUID | None = None,
        design_membership: DesignMembership = DesignMembership.MODELED,
    ) -> EntityView:
        self.validate_entity_payload(project_id, payload)
        return self._entity_view(
            self._create_entity_row(
                project_id,
                payload,
                entity_id=entity_id,
                design_membership=design_membership,
            )
        )

    def validate_entity_payload(self, project_id: UUID, payload: EntityCreate) -> None:
        self.portfolio.require_project(project_id)
        type_row = self.ontology.require_type_by_key(project_id, payload.type_key)
        if type_row.kind not in {TypeKind.OBJECT.value, TypeKind.METRIC.value}:
            raise DomainError(
                "ENTITY_TYPE_REQUIRED",
                "实体只能使用 OBJECT 或 METRIC 类型。",
                status_code=422,
            )
        self.ontology.validate_properties(type_row, payload.properties)
        validate_evidence_references(self.session, project_id, payload.evidence)

    def _create_entity_row(
        self,
        project_id: UUID,
        payload: EntityCreate,
        *,
        entity_id: UUID | None = None,
        design_membership: DesignMembership = DesignMembership.MODELED,
    ) -> EntityRow:
        row = EntityRow(
            id=str(entity_id) if entity_id is not None else None,
            project_id=str(project_id),
            type_key=payload.type_key,
            stable_key=payload.stable_key,
            name=payload.name,
            properties=json_ready(payload.properties),
            design_membership=design_membership.value,
            viewpoint=payload.viewpoint.value,
            evidence=json_ready([item.model_dump(mode="json") for item in payload.evidence]),
        )
        self.session.add(row)
        try:
            self.session.flush()
        except IntegrityError as exc:
            raise DomainError(
                "ENTITY_STABLE_KEY_CONFLICT",
                "项目内实体稳定标识不能重复。",
                status_code=409,
            ) from exc
        self.portfolio.bump_project_revision(project_id)
        return row

    def entity(self, project_id: UUID, entity_id: UUID) -> EntityView:
        return self._entity_view(self.require_entity(project_id, entity_id))

    def update_entity(
        self,
        project_id: UUID,
        entity_id: UUID,
        payload: EntityUpdate,
    ) -> EntityView:
        row = self.require_entity(project_id, entity_id)
        if row.design_membership != DesignMembership.MODELED.value:
            raise DomainError(
                "UNMODELED_ENTITY_NOT_EDITABLE",
                "系统发现的未对齐对象不能作为正式设计直接编辑，请先完成来源身份对齐。",
                status_code=409,
            )
        require_revision(row.revision, payload.expected_revision, resource="实体")
        if payload.properties is not None:
            type_row = self.ontology.require_type_by_key(project_id, row.type_key)
            self.ontology.validate_properties(type_row, payload.properties)
            row.properties = json_ready(payload.properties)
        if payload.name is not None:
            row.name = payload.name
        if payload.viewpoint is not None:
            row.viewpoint = payload.viewpoint.value
        if payload.evidence is not None:
            validate_evidence_references(self.session, project_id, payload.evidence)
            row.evidence = json_ready([item.model_dump(mode="json") for item in payload.evidence])
        row.revision += 1
        row.status = LifecycleStatus.DRAFT.value
        self.session.flush()
        self.portfolio.bump_project_revision(project_id)
        return self._entity_view(row)

    def retire_entity(
        self, project_id: UUID, entity_id: UUID, expected_revision: int
    ) -> EntityView:
        row = self.require_entity(project_id, entity_id)
        require_revision(row.revision, expected_revision, resource="实体")
        active_relations = self.session.scalar(
            select(RelationParticipantRow.id)
            .join(RelationRow, RelationRow.id == RelationParticipantRow.relation_id)
            .where(
                RelationParticipantRow.entity_id == row.id,
                RelationRow.status != LifecycleStatus.RETIRED.value,
            )
            .limit(1)
        )
        if active_relations is not None:
            raise DomainError(
                "ENTITY_HAS_ACTIVE_RELATIONS",
                "实体仍参与有效关系，请先处理这些关系。",
                status_code=409,
            )
        row.status = LifecycleStatus.RETIRED.value
        row.revision += 1
        self.session.flush()
        self.portfolio.bump_project_revision(project_id)
        return self._entity_view(row)

    def list_relations(
        self,
        project_id: UUID,
        *,
        type_key: str | None = None,
        type_keys: list[str] | None = None,
        relation_ids: set[str] | None = None,
        viewpoints: list[str] | None = None,
        entity_id: UUID | None = None,
        include_retired: bool = False,
        limit: int | None = None,
        offset: int = 0,
    ) -> list[RelationView]:
        self.portfolio.require_project(project_id)
        statement = select(RelationRow).where(RelationRow.project_id == str(project_id))
        if type_key:
            statement = statement.where(RelationRow.type_key == type_key)
        if type_keys:
            statement = statement.where(RelationRow.type_key.in_(type_keys))
        if relation_ids is not None:
            if not relation_ids:
                return []
            statement = statement.where(RelationRow.id.in_(relation_ids))
        if viewpoints:
            statement = statement.where(RelationRow.viewpoint.in_(viewpoints))
        if entity_id:
            statement = statement.join(RelationParticipantRow).where(
                RelationParticipantRow.entity_id == str(entity_id)
            )
        if not include_retired:
            statement = statement.where(RelationRow.status != LifecycleStatus.RETIRED.value)
        statement = (
            statement.options(selectinload(RelationRow.participants))
            .distinct()
            .order_by(RelationRow.type_key)
        )
        if offset:
            statement = statement.offset(offset)
        if limit is not None:
            statement = statement.limit(limit)
        rows = self.session.scalars(statement).all()
        return self._relation_views(list(rows))

    def count_relations(
        self,
        project_id: UUID,
        *,
        type_key: str | None = None,
        entity_id: UUID | None = None,
        include_retired: bool = False,
    ) -> int:
        self.portfolio.require_project(project_id)
        statement = select(RelationRow.id).where(RelationRow.project_id == str(project_id))
        if type_key:
            statement = statement.where(RelationRow.type_key == type_key)
        if entity_id:
            statement = statement.join(RelationParticipantRow).where(
                RelationParticipantRow.entity_id == str(entity_id)
            )
        if not include_retired:
            statement = statement.where(RelationRow.status != LifecycleStatus.RETIRED.value)
        return int(
            self.session.scalar(select(func.count()).select_from(statement.distinct().subquery()))
            or 0
        )

    def create_relation(
        self,
        project_id: UUID,
        payload: RelationCreate,
        *,
        relation_id: UUID | None = None,
    ) -> RelationView:
        self.portfolio.require_project(project_id)
        self.validate_relation_payload(project_id, payload)
        row = RelationRow(
            id=str(relation_id) if relation_id is not None else None,
            project_id=str(project_id),
            type_key=payload.type_key,
            name=payload.name,
            properties=json_ready(payload.properties),
            viewpoint=payload.viewpoint.value,
            evidence=json_ready([item.model_dump(mode="json") for item in payload.evidence]),
        )
        row.participants = [
            RelationParticipantRow(
                role_key=item.role_key,
                entity_id=str(item.entity_id),
                ordinal=item.ordinal,
            )
            for item in payload.participants
        ]
        self.session.add(row)
        self.session.flush()
        self.portfolio.bump_project_revision(project_id)
        return self._relation_view(row)

    def relation(self, project_id: UUID, relation_id: UUID) -> RelationView:
        return self._relation_view(self.require_relation(project_id, relation_id))

    def update_relation(
        self,
        project_id: UUID,
        relation_id: UUID,
        payload: RelationUpdate,
    ) -> RelationView:
        row = self.require_relation(project_id, relation_id)
        require_revision(row.revision, payload.expected_revision, resource="关系")
        participants = payload.participants or [
            RelationParticipantInput(
                role_key=item.role_key,
                entity_id=UUID(item.entity_id),
                ordinal=item.ordinal,
            )
            for item in row.participants
        ]
        candidate = RelationCreate.model_validate(
            {
                "type_key": row.type_key,
                "name": payload.name if payload.name is not None else row.name,
                "participants": participants,
                "properties": payload.properties
                if payload.properties is not None
                else row.properties,
                "viewpoint": payload.viewpoint.value
                if payload.viewpoint is not None
                else row.viewpoint,
                "evidence": payload.evidence if payload.evidence is not None else row.evidence,
            }
        )
        self.validate_relation_payload(project_id, candidate)
        if payload.name is not None:
            row.name = payload.name
        if payload.properties is not None:
            row.properties = json_ready(payload.properties)
        if payload.viewpoint is not None:
            row.viewpoint = payload.viewpoint.value
        if payload.evidence is not None:
            row.evidence = json_ready([item.model_dump(mode="json") for item in payload.evidence])
        if payload.participants is not None:
            row.participants.clear()
            self.session.flush()
            row.participants.extend(
                RelationParticipantRow(
                    role_key=item.role_key,
                    entity_id=str(item.entity_id),
                    ordinal=item.ordinal,
                )
                for item in payload.participants
            )
        row.revision += 1
        row.status = LifecycleStatus.DRAFT.value
        self.session.flush()
        self.portfolio.bump_project_revision(project_id)
        return self._relation_view(row)

    def retire_relation(
        self,
        project_id: UUID,
        relation_id: UUID,
        expected_revision: int,
    ) -> RelationView:
        row = self.require_relation(project_id, relation_id)
        require_revision(row.revision, expected_revision, resource="关系")
        row.status = LifecycleStatus.RETIRED.value
        row.revision += 1
        self.session.flush()
        self.portfolio.bump_project_revision(project_id)
        return self._relation_view(row)

    def list_events(self, project_id: UUID) -> list[EventView]:
        self.portfolio.require_project(project_id)
        rows = self.session.scalars(
            select(EventRow)
            .where(EventRow.project_id == str(project_id))
            .order_by(EventRow.occurred_at.desc())
        ).all()
        return [EventView.model_validate(row) for row in rows]

    def create_event(
        self,
        project_id: UUID,
        payload: EventCreate,
        *,
        event_id: UUID | None = None,
    ) -> EventView:
        self.portfolio.require_project(project_id)
        type_row = self.ontology.require_type_by_key(
            project_id, payload.type_key, kind=TypeKind.EVENT
        )
        self.ontology.validate_properties(type_row, payload.properties)
        validate_evidence_references(self.session, project_id, payload.evidence)
        for participant in payload.participants:
            entity = self.require_entity(project_id, participant.entity_id)
            if entity.status == LifecycleStatus.RETIRED.value:
                raise DomainError(
                    "EVENT_PARTICIPANT_RETIRED",
                    f"已退役实体 {entity.name} 不能参与新事件。",
                    status_code=422,
                    details=[{"entity_id": entity.id}],
                )
            if entity.design_membership != DesignMembership.MODELED.value:
                raise DomainError(
                    "EVENT_PARTICIPANT_UNMODELED",
                    f"待对齐实体 {entity.name} 不能参与正式事件。",
                    status_code=422,
                )
        row = EventRow(
            id=str(event_id) if event_id is not None else None,
            project_id=str(project_id),
            type_key=payload.type_key,
            name=payload.name,
            occurred_at=payload.occurred_at,
            participants=json_ready(
                [item.model_dump(mode="json") for item in payload.participants]
            ),
            properties=json_ready(payload.properties),
            evidence=json_ready([item.model_dump(mode="json") for item in payload.evidence]),
        )
        self.session.add(row)
        self.session.flush()
        self.portfolio.bump_project_revision(project_id)
        return EventView.model_validate(row)

    def graph(self, project_id: UUID, query: GraphQuery) -> GraphView:
        project = self.portfolio.require_project(project_id)
        if query.release_id is not None:
            if query.scenario_id is not None:
                raise DomainError(
                    "SCENARIO_RELEASE_COMBINATION_UNSUPPORTED",
                    "当前方案基于设计草稿，不能同时指定历史发布版本。",
                    status_code=422,
                )
            return self._filter_graph(self._released_graph(project_id, query.release_id), query)
        neighborhood_entity_ids: set[str] | None = None
        neighborhood_relation_ids: set[str] | None = None
        if query.root_entity_id is not None and query.scenario_id is None:
            neighborhood_entity_ids, neighborhood_relation_ids = self._neighborhood_selection(
                project_id,
                query.root_entity_id,
                query.depth,
                include_retired=query.include_retired,
                relation_type_keys=query.relation_type_keys,
                viewpoints=[item.value for item in query.viewpoints],
            )
        entities = self.list_entities(
            project_id,
            type_keys=query.type_keys if query.scenario_id is None else None,
            entity_ids=neighborhood_entity_ids,
            viewpoints=(
                [item.value for item in query.viewpoints]
                if query.scenario_id is None
                else None
            ),
            search=query.search if query.scenario_id is None else None,
            include_retired=query.include_retired,
            include_unmodeled=query.include_unmodeled,
            include_observations=query.include_observations,
            limit=(query.entity_limit if neighborhood_entity_ids is None else None),
        )
        relations = self.list_relations(
            project_id,
            type_keys=query.relation_type_keys if query.scenario_id is None else None,
            relation_ids=neighborhood_relation_ids,
            viewpoints=(
                [item.value for item in query.viewpoints]
                if query.scenario_id is None
                else None
            ),
            include_retired=query.include_retired,
            limit=(query.relation_limit if neighborhood_relation_ids is None else None),
        )
        if query.scenario_id is not None:
            entities, relations = self._apply_scenario(
                project_id, query.scenario_id, entities, relations
            )
            if query.type_keys:
                entities = [item for item in entities if item.type_key in query.type_keys]
            if query.search:
                needle = query.search.casefold()
                entities = [
                    item
                    for item in entities
                    if needle in item.name.casefold()
                    or (item.stable_key is not None and needle in item.stable_key.casefold())
                ]
            if query.viewpoints:
                entities = [item for item in entities if item.viewpoint in query.viewpoints]
                relations = [item for item in relations if item.viewpoint in query.viewpoints]
            if query.relation_type_keys:
                relations = [
                    item for item in relations if item.type_key in query.relation_type_keys
                ]
        if query.root_entity_id is not None:
            if neighborhood_entity_ids is not None:
                allowed_ids = {item.id for item in entities}
                if query.root_entity_id not in allowed_ids:
                    raise DomainError(
                        "ROOT_ENTITY_NOT_FOUND",
                        "图查询起点不存在或不在当前筛选范围。",
                        status_code=404,
                    )
            else:
                allowed_ids = self._neighborhood_ids(
                    query.root_entity_id, query.depth, entities, relations
                )
            entities = [item for item in entities if item.id in allowed_ids]
            relations = [
                item
                for item in relations
                if all(participant.entity_id in allowed_ids for participant in item.participants)
            ]
        else:
            visible_ids = {item.id for item in entities}
            relations = [
                item
                for item in relations
                if all(participant.entity_id in visible_ids for participant in item.participants)
            ]
        if query.entity_limit is not None and len(entities) > query.entity_limit:
            entities = sorted(
                entities,
                key=lambda item: (
                    0
                    if query.root_entity_id is not None and item.id == query.root_entity_id
                    else 1,
                    item.type_key,
                    item.name,
                    str(item.id),
                ),
            )[: query.entity_limit]
            visible_ids = {item.id for item in entities}
            relations = [
                item
                for item in relations
                if all(participant.entity_id in visible_ids for participant in item.participants)
            ]
        if query.relation_limit is not None:
            relations = relations[: query.relation_limit]
        return GraphView(
            project_id=project_id,
            revision=project.revision,
            release_id=None,
            scenario_id=query.scenario_id,
            entities=entities,
            relations=relations,
        )

    def _apply_scenario(
        self,
        project_id: UUID,
        scenario_id: UUID,
        entities: list[EntityView],
        relations: list[RelationView],
    ) -> tuple[list[EntityView], list[RelationView]]:
        scenario = self.session.get(ScenarioRow, str(scenario_id))
        if scenario is None or scenario.project_id != str(project_id):
            raise DomainError("SCENARIO_NOT_FOUND", "方案不存在。", status_code=404)
        if scenario.applied_change_set_id is not None:
            return entities, relations
        entity_by_id = {item.id: item for item in entities}
        relation_by_id = {item.id: item for item in relations}
        try:
            self._apply_scenario_operations(
                project_id, scenario.overlay_operations, entity_by_id, relation_by_id
            )
        except ValidationError as exc:
            raise DomainError(
                "SCENARIO_OPERATION_INVALID",
                "方案操作不符合对象、关系或本体约束。",
                status_code=422,
                details=[
                    {
                        "path": ".".join(str(item) for item in error["loc"]),
                        "message": error["msg"],
                        "type": error["type"],
                    }
                    for error in exc.errors()
                ],
            ) from exc
        return list(entity_by_id.values()), list(relation_by_id.values())

    def _apply_scenario_operations(
        self,
        project_id: UUID,
        raw_operations: list[dict[str, object]],
        entity_by_id: dict[UUID, EntityView],
        relation_by_id: dict[UUID, RelationView],
    ) -> None:
        for raw_operation in raw_operations:
            operation = ChangeOperation.model_validate(raw_operation)
            payload = operation.payload
            if operation.kind == ChangeOperationKind.CREATE_ENTITY:
                entity_payload = EntityCreate.model_validate(payload)
                self.validate_entity_payload(project_id, entity_payload)
                if any(
                    entity_payload.stable_key
                    and item.stable_key == entity_payload.stable_key
                    for item in entity_by_id.values()
                ):
                    raise DomainError(
                        "SCENARIO_ENTITY_STABLE_KEY_CONFLICT",
                        "方案创建的实体稳定标识与现有实体冲突。",
                        status_code=409,
                    )
                timestamp = now_utc()
                entity_by_id[operation.operation_id] = EntityView(
                    id=operation.operation_id,
                    project_id=project_id,
                    status=LifecycleStatus.DRAFT,
                    revision=1,
                    created_at=timestamp,
                    updated_at=timestamp,
                    design_membership=DesignMembership.MODELED,
                    **entity_payload.model_dump(),
                )
            elif operation.kind == ChangeOperationKind.UPDATE_ENTITY:
                entity = self._scenario_entity(entity_by_id, operation.target_id)
                self._check_scenario_revision(entity.revision, payload)
                candidate = EntityCreate(
                    type_key=entity.type_key,
                    stable_key=entity.stable_key,
                    name=payload["name"] if "name" in payload else entity.name,
                    properties=(
                        payload["properties"] if "properties" in payload else entity.properties
                    ),
                    viewpoint=(
                        payload["viewpoint"] if "viewpoint" in payload else entity.viewpoint
                    ),
                    evidence=payload["evidence"] if "evidence" in payload else entity.evidence,
                )
                self.validate_entity_payload(project_id, candidate)
                entity_by_id[entity.id] = EntityView(
                    **entity.model_dump(exclude={
                        "name", "properties", "viewpoint", "evidence", "revision", "updated_at"
                    }),
                    name=candidate.name,
                    properties=candidate.properties,
                    viewpoint=candidate.viewpoint,
                    evidence=candidate.evidence,
                    revision=entity.revision + 1,
                    updated_at=now_utc(),
                )
            elif operation.kind == ChangeOperationKind.RETIRE_ENTITY:
                entity = self._scenario_entity(entity_by_id, operation.target_id)
                self._check_scenario_revision(entity.revision, payload)
                del entity_by_id[entity.id]
                affected_relation_ids = [
                    relation_id
                    for relation_id, relation in relation_by_id.items()
                    if any(item.entity_id == entity.id for item in relation.participants)
                ]
                for relation_id in affected_relation_ids:
                    del relation_by_id[relation_id]
            elif operation.kind in {
                ChangeOperationKind.CREATE_RELATION,
                ChangeOperationKind.UPDATE_RELATION,
            }:
                current = (
                    None
                    if operation.kind == ChangeOperationKind.CREATE_RELATION
                    else self._scenario_relation(relation_by_id, operation.target_id)
                )
                if current is not None:
                    self._check_scenario_revision(current.revision, payload)
                relation_candidate = RelationCreate.model_validate(
                    {
                        "type_key": payload.get(
                            "type_key", current.type_key if current else None
                        ),
                        "name": payload.get("name", current.name if current else None),
                        "participants": payload.get(
                            "participants", current.participants if current else None
                        ),
                        "properties": payload.get(
                            "properties", current.properties if current else {}
                        ),
                        "viewpoint": payload.get(
                            "viewpoint", current.viewpoint if current else "DESIGNED"
                        ),
                        "evidence": payload.get(
                            "evidence", current.evidence if current else []
                        ),
                    }
                )
                participants = self._validate_scenario_relation(
                    project_id, relation_candidate, entity_by_id
                )
                timestamp = now_utc()
                relation_id = current.id if current is not None else operation.operation_id
                relation_by_id[relation_id] = RelationView(
                    id=relation_id,
                    project_id=project_id,
                    type_key=relation_candidate.type_key,
                    name=relation_candidate.name,
                    participants=participants,
                    properties=relation_candidate.properties,
                    viewpoint=relation_candidate.viewpoint,
                    evidence=relation_candidate.evidence,
                    status=LifecycleStatus.DRAFT,
                    revision=(current.revision + 1 if current is not None else 1),
                    created_at=current.created_at if current is not None else timestamp,
                    updated_at=timestamp,
                )
            elif operation.kind == ChangeOperationKind.RETIRE_RELATION:
                relation = self._scenario_relation(relation_by_id, operation.target_id)
                self._check_scenario_revision(relation.revision, payload)
                del relation_by_id[relation.id]
            else:
                raise DomainError(
                    "SCENARIO_OPERATION_UNSUPPORTED",
                    f"方案图暂不支持操作 {operation.kind.value}。",
                    status_code=422,
                )

    def _validate_scenario_relation(
        self,
        project_id: UUID,
        payload: RelationCreate,
        entities: dict[UUID, EntityView],
    ) -> list[RelationParticipantView]:
        type_row = self.ontology.require_type_by_key(
            project_id, payload.type_key, kind=TypeKind.RELATION
        )
        self.ontology.validate_properties(type_row, payload.properties)
        validate_evidence_references(self.session, project_id, payload.evidence)
        definitions = {item["key"]: item for item in type_row.relation_roles}
        grouped: dict[str, list[EntityView]] = defaultdict(list)
        participant_views: list[RelationParticipantView] = []
        for participant in payload.participants:
            entity = entities.get(participant.entity_id)
            if entity is None:
                raise DomainError(
                    "SCENARIO_RELATION_ENTITY_NOT_FOUND",
                    "方案关系引用了不存在的实体。",
                    status_code=422,
                )
            grouped[participant.role_key].append(entity)
            participant_views.append(
                RelationParticipantView(
                    **participant.model_dump(),
                    entity_name=entity.name,
                    entity_type_key=entity.type_key,
                )
            )
        unknown_roles = sorted(set(grouped) - set(definitions))
        if unknown_roles:
            raise DomainError(
                "UNKNOWN_RELATION_ROLES",
                "方案关系包含未定义的参与角色。",
                status_code=422,
                details=[{"roles": unknown_roles}],
            )
        for role_key, definition in definitions.items():
            assigned = grouped.get(role_key, [])
            minimum = definition.get("minimum", 0)
            maximum = definition.get("maximum")
            if len(assigned) < minimum or (
                maximum is not None and len(assigned) > maximum
            ):
                raise DomainError(
                    "RELATION_CARDINALITY_VIOLATION",
                    f"方案关系角色 {role_key} 的参与数量不符合本体约束。",
                    status_code=422,
                )
            allowed = set(definition.get("allowed_type_keys") or [])
            for entity in assigned:
                entity_type = self.ontology.require_type_by_key(project_id, entity.type_key)
                if allowed and not self.ontology.entity_type_satisfies(entity_type, allowed):
                    raise DomainError(
                        "RELATION_PARTICIPANT_TYPE_MISMATCH",
                        f"实体 {entity.name} 不能担任方案关系角色 {role_key}。",
                        status_code=422,
                    )
        return participant_views

    @staticmethod
    def _scenario_entity(
        entities: dict[UUID, EntityView], entity_id: UUID | None
    ) -> EntityView:
        if entity_id is None or entity_id not in entities:
            raise DomainError("SCENARIO_ENTITY_NOT_FOUND", "方案目标实体不存在。", status_code=404)
        return entities[entity_id]

    @staticmethod
    def _scenario_relation(
        relations: dict[UUID, RelationView], relation_id: UUID | None
    ) -> RelationView:
        if relation_id is None or relation_id not in relations:
            raise DomainError(
                "SCENARIO_RELATION_NOT_FOUND", "方案目标关系不存在。", status_code=404
            )
        return relations[relation_id]

    @staticmethod
    def _check_scenario_revision(actual_revision: int, payload: dict[str, object]) -> None:
        expected = payload.get("expected_revision")
        if expected is not None and expected != actual_revision:
            raise DomainError(
                "SCENARIO_BASE_STALE",
                "方案引用的对象或关系已经发生变化，请重新建立或重基方案。",
                status_code=409,
                details=[{"expected_revision": expected, "actual_revision": actual_revision}],
            )

    def validate_relation_payload(self, project_id: UUID, payload: RelationCreate) -> None:
        self._validate_relation_payload(project_id, payload, allow_unmodeled=False)

    def validate_system_relation_payload(self, project_id: UUID, payload: RelationCreate) -> None:
        """Validate a source-bound relation before it enters the projection.

        System materialization is allowed to connect UNMODELED source identities.  It
        still goes through the same ontology, role, cardinality, evidence, and type
        checks as a designed relation; promotion to the designed layer remains a
        separate explicit operation.
        """
        self._validate_relation_payload(project_id, payload, allow_unmodeled=True)

    def _validate_relation_payload(
        self, project_id: UUID, payload: RelationCreate, *, allow_unmodeled: bool
    ) -> None:
        type_row = self.ontology.require_type_by_key(
            project_id, payload.type_key, kind=TypeKind.RELATION
        )
        self.ontology.validate_properties(type_row, payload.properties)
        validate_evidence_references(self.session, project_id, payload.evidence)
        participants_by_role: dict[str, list[EntityRow]] = defaultdict(list)
        for participant in payload.participants:
            entity = self.require_entity(project_id, participant.entity_id)
            if entity.status == LifecycleStatus.RETIRED.value:
                raise DomainError(
                    "RELATION_PARTICIPANT_RETIRED",
                    f"已退役实体 {entity.name} 不能参与新关系。",
                    status_code=422,
                    details=[{"entity_id": entity.id}],
                )
            if not allow_unmodeled and entity.design_membership != DesignMembership.MODELED.value:
                raise DomainError(
                    "RELATION_PARTICIPANT_UNMODELED",
                    f"待对齐实体 {entity.name} 不能参与正式关系。",
                    status_code=422,
                )
            participants_by_role[participant.role_key].append(entity)
        definitions = {item["key"]: item for item in type_row.relation_roles}
        unknown_roles = sorted(set(participants_by_role) - set(definitions))
        if unknown_roles:
            raise DomainError(
                "UNKNOWN_RELATION_ROLES",
                "关系包含未定义的参与角色。",
                status_code=422,
                details=[
                    {
                        "roles": unknown_roles,
                        "type_key": payload.type_key,
                        "allowed_roles": [
                            {"key": key, "name": definition.get("name", key)}
                            for key, definition in definitions.items()
                        ],
                    }
                ],
            )
        for role_key, definition in definitions.items():
            count = len(participants_by_role.get(role_key, []))
            minimum = definition.get("minimum", 0)
            maximum = definition.get("maximum")
            if count < minimum or (maximum is not None and count > maximum):
                raise DomainError(
                    "RELATION_CARDINALITY_VIOLATION",
                    f"关系角色 {role_key} 的参与数量不符合本体约束。",
                    status_code=422,
                    details=[
                        {"role": role_key, "count": count, "minimum": minimum, "maximum": maximum}
                    ],
                )
            allowed = set(definition.get("allowed_type_keys") or [])
            for entity in participants_by_role.get(role_key, []):
                if allowed and not self._entity_satisfies(entity, allowed):
                    raise DomainError(
                        "RELATION_PARTICIPANT_TYPE_MISMATCH",
                        f"实体 {entity.name} 不能担任关系角色 {role_key}。",
                        status_code=422,
                        details=[
                            {"entity_type": entity.type_key, "allowed_types": sorted(allowed)}
                        ],
                    )

    def require_entity(self, project_id: UUID | str, entity_id: UUID | str) -> EntityRow:
        row = self.session.get(EntityRow, str(entity_id))
        if row is None or row.project_id != str(project_id):
            raise DomainError("ENTITY_NOT_FOUND", "实体不存在。", status_code=404)
        return row

    def require_relation(self, project_id: UUID | str, relation_id: UUID | str) -> RelationRow:
        row = self.session.get(RelationRow, str(relation_id))
        if row is None or row.project_id != str(project_id):
            raise DomainError("RELATION_NOT_FOUND", "关系不存在。", status_code=404)
        return row

    def _entity_satisfies(self, entity: EntityRow, allowed: set[str]) -> bool:
        type_row = self.ontology.require_type_by_key(entity.project_id, entity.type_key)
        return self.ontology.entity_type_satisfies(type_row, allowed)

    def _entity_view(
        self,
        row: EntityRow,
        *,
        include_observations: bool = True,
        assertions: list[ObservationAssertionRow] | None = None,
        resolutions: dict[str, ObservationConflictRow] | None = None,
    ) -> EntityView:
        if not include_observations:
            return EntityView.model_validate(row)
        if assertions is None:
            assertions = list(
                self.session.scalars(
                    select(ObservationAssertionRow)
                    .where(
                        ObservationAssertionRow.project_id == row.project_id,
                        ObservationAssertionRow.entity_id == row.id,
                        ObservationAssertionRow.status == "ACTIVE",
                    )
                    .order_by(
                        ObservationAssertionRow.observed_at.desc(),
                        ObservationAssertionRow.created_at.desc(),
                        ObservationAssertionRow.id.desc(),
                    )
                ).all()
            )
        if resolutions is None:
            resolutions = {
                item.field_key: item
                for item in self.session.scalars(
                    select(ObservationConflictRow).where(
                        ObservationConflictRow.project_id == row.project_id,
                        ObservationConflictRow.entity_id == row.id,
                        ObservationConflictRow.status == "RESOLVED",
                    )
                ).all()
            }
        latest_by_source: dict[tuple[str, str], ObservationAssertionRow] = {}
        for assertion in assertions:
            latest_by_source.setdefault(
                (assertion.source_identity_id, assertion.field_key), assertion
            )
        candidates_by_field: dict[str, list[ObservationAssertionRow]] = defaultdict(list)
        for assertion in latest_by_source.values():
            candidates_by_field[assertion.field_key].append(assertion)

        resolved: dict[str, object] = {}
        conflicts: dict[str, list[dict[str, object]]] = {}
        resolved_conflicts: dict[str, dict[str, object]] = {}
        ignored_conflicts: dict[str, list[dict[str, object]]] = {}
        for field_key, candidates in candidates_by_field.items():
            highest_priority = max(item.authority_priority for item in candidates)
            authoritative = [
                item for item in candidates if item.authority_priority == highest_priority
            ]
            values: dict[str, object] = {}
            for item in authoritative:
                fingerprint = json.dumps(
                    item.value, ensure_ascii=False, sort_keys=True, default=str
                )
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
        observed_name = (
            str(observed_name_value) if observed_name_value is not None else None
        )
        observed_properties = dict(resolved)
        comparison: dict[str, object] = {}
        if observed_name is not None:
            comparison["__name__"] = {
                "design": row.name,
                "observed": observed_name,
                "status": "MATCH" if row.name == observed_name else "DIVERGED",
            }
        for key, observed_value in observed_properties.items():
            design_value = row.properties.get(key)
            comparison[key] = {
                "design": design_value,
                "observed": observed_value,
                "status": (
                    "OBSERVED_ONLY"
                    if key not in row.properties
                    else "MATCH"
                    if design_value == observed_value
                    else "DIVERGED"
                ),
            }
        for key, conflict_candidates in conflicts.items():
            comparison[key] = {
                "design": row.name if key == "__name__" else row.properties.get(key),
                "observed": None,
                "status": "CONFLICT",
                "candidates": conflict_candidates,
            }
        for key, metadata in resolved_conflicts.items():
            comparison[key] = {"status": "RESOLVED_CONFLICT", **metadata}
        for key, ignored_candidates in ignored_conflicts.items():
            comparison[key] = {
                "design": row.name if key == "__name__" else row.properties.get(key),
                "observed": None,
                "status": "IGNORED_CONFLICT",
                "candidates": ignored_candidates,
            }
        return EntityView.model_validate(row).model_copy(
            update={
                "observed_name": observed_name,
                "observed_properties": observed_properties,
                "comparison": comparison,
            }
        )

    def _entity_views(
        self,
        rows: list[EntityRow],
        *,
        include_observations: bool,
    ) -> list[EntityView]:
        if not rows or not include_observations:
            return [
                self._entity_view(row, include_observations=include_observations)
                for row in rows
            ]
        assertions_by_entity: dict[str, list[ObservationAssertionRow]] = defaultdict(list)
        resolutions_by_entity: dict[str, dict[str, ObservationConflictRow]] = defaultdict(dict)
        assertions = self.session.scalars(
            select(ObservationAssertionRow)
            .where(
                ObservationAssertionRow.project_id == rows[0].project_id,
                ObservationAssertionRow.entity_id.in_([row.id for row in rows]),
                ObservationAssertionRow.status == "ACTIVE",
            )
            .order_by(
                ObservationAssertionRow.observed_at.desc(),
                ObservationAssertionRow.created_at.desc(),
                ObservationAssertionRow.id.desc(),
            )
        ).all()
        for assertion in assertions:
            assertions_by_entity[assertion.entity_id].append(assertion)
        resolutions = self.session.scalars(
            select(ObservationConflictRow).where(
                ObservationConflictRow.project_id == rows[0].project_id,
                ObservationConflictRow.entity_id.in_([row.id for row in rows]),
                ObservationConflictRow.status == "RESOLVED",
            )
        ).all()
        for resolution in resolutions:
            resolutions_by_entity[resolution.entity_id][resolution.field_key] = resolution
        return [
            self._entity_view(
                row,
                assertions=assertions_by_entity[row.id],
                resolutions=resolutions_by_entity[row.id],
            )
            for row in rows
        ]

    def _relation_view(self, row: RelationRow) -> RelationView:
        return self._relation_views([row])[0]

    def _relation_views(self, rows: list[RelationRow]) -> list[RelationView]:
        if not rows:
            return []
        entity_ids = {
            participant.entity_id for row in rows for participant in row.participants
        }
        entities = {
            item.id: item
            for item in self.session.scalars(
                select(EntityRow).where(EntityRow.id.in_(entity_ids))
            ).all()
        }
        return [
            RelationView.model_validate(
                {
                    "id": row.id,
                    "project_id": row.project_id,
                    "type_key": row.type_key,
                    "name": row.name,
                    "participants": [
                        {
                            "role_key": item.role_key,
                            "entity_id": item.entity_id,
                            "ordinal": item.ordinal,
                            "entity_name": entities[item.entity_id].name,
                            "entity_type_key": entities[item.entity_id].type_key,
                        }
                        for item in row.participants
                    ],
                    "properties": row.properties,
                    "viewpoint": row.viewpoint,
                    "evidence": row.evidence,
                    "status": row.status,
                    "revision": row.revision,
                    "created_at": row.created_at,
                    "updated_at": row.updated_at,
                }
            )
            for row in rows
        ]

    def _neighborhood_selection(
        self,
        project_id: UUID,
        root_id: UUID,
        depth: int,
        *,
        include_retired: bool,
        relation_type_keys: list[str],
        viewpoints: list[str],
    ) -> tuple[set[str], set[str]]:
        root = self.session.get(EntityRow, str(root_id))
        if root is None or root.project_id != str(project_id):
            raise DomainError("ROOT_ENTITY_NOT_FOUND", "图查询起点不存在。", status_code=404)
        seen = {root.id}
        frontier = {root.id}
        relation_ids: set[str] = set()
        for _ in range(depth):
            if not frontier:
                break
            statement = (
                select(RelationParticipantRow.relation_id)
                .join(RelationRow, RelationRow.id == RelationParticipantRow.relation_id)
                .where(
                    RelationRow.project_id == str(project_id),
                    RelationParticipantRow.entity_id.in_(frontier),
                )
            )
            if not include_retired:
                statement = statement.where(
                    RelationRow.status != LifecycleStatus.RETIRED.value
                )
            if relation_type_keys:
                statement = statement.where(RelationRow.type_key.in_(relation_type_keys))
            if viewpoints:
                statement = statement.where(RelationRow.viewpoint.in_(viewpoints))
            step_relation_ids = set(self.session.scalars(statement.distinct()).all())
            if not step_relation_ids:
                break
            relation_ids.update(step_relation_ids)
            neighbor_ids = set(
                self.session.scalars(
                    select(RelationParticipantRow.entity_id)
                    .where(RelationParticipantRow.relation_id.in_(step_relation_ids))
                    .distinct()
                ).all()
            )
            frontier = neighbor_ids - seen
            seen.update(neighbor_ids)
        return seen, relation_ids

    @staticmethod
    def _neighborhood_ids(
        root_id: UUID,
        depth: int,
        entities: list[EntityView],
        relations: list[RelationView],
    ) -> set[UUID]:
        entity_ids = {item.id for item in entities}
        if root_id not in entity_ids:
            raise DomainError("ROOT_ENTITY_NOT_FOUND", "图查询起点不存在。", status_code=404)
        adjacency: dict[UUID, set[UUID]] = defaultdict(set)
        for relation in relations:
            ids = [participant.entity_id for participant in relation.participants]
            for source in ids:
                adjacency[source].update(item for item in ids if item != source)
        seen = {root_id}
        queue: deque[tuple[UUID, int]] = deque([(root_id, 0)])
        while queue:
            current, current_depth = queue.popleft()
            if current_depth >= depth:
                continue
            for neighbor in adjacency[current]:
                if neighbor not in seen:
                    seen.add(neighbor)
                    queue.append((neighbor, current_depth + 1))
        return seen

    def _released_graph(self, project_id: UUID, release_id: UUID) -> GraphView:
        from enterprise_insight_backend.models import PublicationRow

        publication = self.session.get(PublicationRow, str(release_id))
        if publication is None or publication.project_id != str(project_id):
            raise DomainError("PUBLICATION_NOT_FOUND", "发布版本不存在。", status_code=404)
        return GraphView.model_validate(publication.graph_snapshot)

    def _filter_graph(self, graph: GraphView, query: GraphQuery) -> GraphView:
        entities = list(graph.entities)
        relations = list(graph.relations)
        if not query.include_retired:
            entities = [item for item in entities if item.status != LifecycleStatus.RETIRED]
            relations = [item for item in relations if item.status != LifecycleStatus.RETIRED]
        if not query.include_unmodeled:
            entities = [
                item for item in entities if item.design_membership == DesignMembership.MODELED
            ]
        if query.type_keys:
            entities = [item for item in entities if item.type_key in query.type_keys]
        if query.search:
            needle = query.search.casefold()
            entities = [
                item
                for item in entities
                if needle in item.name.casefold()
                or (item.stable_key is not None and needle in item.stable_key.casefold())
            ]
        if query.viewpoints:
            entities = [item for item in entities if item.viewpoint in query.viewpoints]
            relations = [item for item in relations if item.viewpoint in query.viewpoints]
        if query.relation_type_keys:
            relations = [item for item in relations if item.type_key in query.relation_type_keys]
        if query.root_entity_id is not None:
            allowed_ids = self._neighborhood_ids(
                query.root_entity_id, query.depth, entities, relations
            )
            entities = [item for item in entities if item.id in allowed_ids]
        visible_ids = {item.id for item in entities}
        relations = [
            item
            for item in relations
            if all(participant.entity_id in visible_ids for participant in item.participants)
        ]
        if query.entity_limit is not None:
            entities = entities[: query.entity_limit]
            visible_ids = {item.id for item in entities}
            relations = [
                item
                for item in relations
                if all(participant.entity_id in visible_ids for participant in item.participants)
            ]
        if query.relation_limit is not None:
            relations = relations[: query.relation_limit]
        return graph.model_copy(update={"entities": entities, "relations": relations})
