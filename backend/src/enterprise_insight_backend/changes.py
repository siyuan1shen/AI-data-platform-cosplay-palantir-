from __future__ import annotations

from collections import Counter
from collections.abc import Collection, Sequence
from typing import Any
from uuid import UUID

from pydantic import ValidationError
from sqlalchemy import select
from sqlalchemy.orm import Session

from enterprise_insight_backend.errors import DomainError
from enterprise_insight_backend.evidence_validation import validate_evidence_references
from enterprise_insight_backend.models import (
    ChangeSetRow,
    EntityRow,
    EventRow,
    RelationParticipantRow,
    RelationRow,
)
from enterprise_insight_backend.ontology import OntologyService
from enterprise_insight_backend.portfolio import PortfolioService
from enterprise_insight_backend.projection import ProjectionService
from enterprise_insight_backend.schemas import (
    ChangeOperation,
    ChangeOperationKind,
    ChangePreview,
    ChangeSetCreate,
    ChangeSetStatus,
    ChangeSetValidation,
    ChangeSetView,
    EntityCreate,
    EntityUpdate,
    EventCreate,
    LifecycleStatus,
    RelationCreate,
    RelationParticipantInput,
    RelationUpdate,
    TypeKind,
    ValidationIssue,
)
from enterprise_insight_backend.service_utils import json_ready, now_utc


class ChangeSetService:
    def __init__(self, session: Session) -> None:
        self.session = session
        self.portfolio = PortfolioService(session)
        self.projection = ProjectionService(session)
        self.ontology = OntologyService(session)

    def list(self, project_id: UUID) -> list[ChangeSetView]:
        self.portfolio.require_project(project_id)
        rows = self.session.scalars(
            select(ChangeSetRow)
            .where(ChangeSetRow.project_id == str(project_id))
            .order_by(ChangeSetRow.created_at.desc())
        ).all()
        return [self._view(row) for row in rows]

    def create(self, project_id: UUID, payload: ChangeSetCreate) -> ChangeSetView:
        project = self.portfolio.require_project(project_id)
        if project.revision != payload.base_revision:
            raise DomainError(
                "CHANGESET_BASE_REVISION_CONFLICT",
                "变更集基准版本不是项目当前版本。",
                status_code=409,
                details=[
                    {
                        "base_revision": payload.base_revision,
                        "current_revision": project.revision,
                    }
                ],
            )
        row = ChangeSetRow(
            project_id=str(project_id),
            title=payload.title,
            description=payload.description,
            base_revision=payload.base_revision,
            operations=json_ready([item.model_dump(mode="json") for item in payload.operations]),
            created_by=payload.created_by,
        )
        self.session.add(row)
        self.session.flush()
        return self._view(row)

    def get(self, project_id: UUID, change_set_id: UUID) -> ChangeSetView:
        return self._view(self.require(project_id, change_set_id))

    def validate_payload(
        self, project_id: UUID, payload: ChangeSetCreate
    ) -> ChangeSetValidation:
        """Validate an Agent-proposed batch without persisting a draft row."""
        self.portfolio.require_project(project_id)
        candidate = ChangeSetRow(
            project_id=str(project_id),
            title=payload.title,
            description=payload.description,
            base_revision=payload.base_revision,
            operations=json_ready(
                [item.model_dump(mode="json") for item in payload.operations]
            ),
            created_by=payload.created_by,
        )
        return self._validate_row(candidate)

    def validate(self, project_id: UUID, change_set_id: UUID) -> ChangeSetView:
        row = self.require(project_id, change_set_id)
        self._ensure_mutable(row)
        validation = self._validate_row(row)
        row.validation = json_ready(validation.model_dump(mode="json"))
        row.status = (
            ChangeSetStatus.VALID.value if validation.valid else ChangeSetStatus.INVALID.value
        )
        self.session.flush()
        return self._view(row)

    def preview(self, project_id: UUID, change_set_id: UUID) -> ChangePreview:
        row = self.require(project_id, change_set_id)
        validation = self._validate_row(row)
        operations = [ChangeOperation.model_validate(item) for item in row.operations]
        counts = Counter(item.kind.value.split("_", 1)[0] for item in operations)
        current_revision = self.portfolio.require_project(project_id).revision
        return ChangePreview(
            change_set_id=UUID(row.id),
            base_revision=row.base_revision,
            current_revision=current_revision,
            creates=counts["CREATE"],
            updates=counts["UPDATE"],
            retires=counts["RETIRE"],
            operations=operations,
            validation=validation,
        )

    def approve(self, project_id: UUID, change_set_id: UUID) -> ChangeSetView:
        row = self.require(project_id, change_set_id)
        validation = self._validate_row(row)
        row.validation = json_ready(validation.model_dump(mode="json"))
        if not validation.valid:
            row.status = ChangeSetStatus.INVALID.value
            self.session.flush()
            raise DomainError(
                "CHANGESET_INVALID",
                "变更集存在错误，不能批准。",
                status_code=409,
                details=[item.model_dump(mode="json") for item in validation.issues],
            )
        row.status = ChangeSetStatus.APPROVED.value
        self.session.flush()
        return self._view(row)

    def apply(self, project_id: UUID, change_set_id: UUID) -> ChangeSetView:
        row = self.require(project_id, change_set_id)
        if row.status == ChangeSetStatus.APPLIED.value:
            return self._view(row)
        if row.status != ChangeSetStatus.APPROVED.value:
            raise DomainError(
                "CHANGESET_NOT_APPROVED",
                "变更集必须先通过校验并批准。",
                status_code=409,
            )
        validation = self._validate_row(row)
        if not validation.valid:
            raise DomainError(
                "CHANGESET_BECAME_INVALID",
                "项目状态已变化，变更集需要重新校验。",
                status_code=409,
                details=[item.model_dump(mode="json") for item in validation.issues],
            )
        operations = [ChangeOperation.model_validate(item) for item in row.operations]
        execution_order = {
            ChangeOperationKind.CREATE_ENTITY: 10,
            ChangeOperationKind.UPDATE_ENTITY: 20,
            ChangeOperationKind.CREATE_RELATION: 30,
            ChangeOperationKind.UPDATE_RELATION: 40,
            ChangeOperationKind.RETIRE_RELATION: 50,
            ChangeOperationKind.CREATE_EVENT: 60,
            ChangeOperationKind.RETIRE_ENTITY: 70,
        }
        ordered_operations = sorted(
            enumerate(operations),
            key=lambda item: (execution_order[item[1].kind], item[0]),
        )
        for _, operation in ordered_operations:
            if operation.kind == ChangeOperationKind.CREATE_ENTITY:
                self.projection.create_entity(
                    project_id,
                    EntityCreate.model_validate(operation.payload),
                    entity_id=operation.operation_id,
                )
            elif operation.kind == ChangeOperationKind.UPDATE_ENTITY:
                self.projection.update_entity(
                    project_id,
                    self._required_target(operation),
                    EntityUpdate.model_validate(operation.payload),
                )
            elif operation.kind == ChangeOperationKind.RETIRE_ENTITY:
                self.projection.retire_entity(
                    project_id,
                    self._required_target(operation),
                    int(operation.payload["expected_revision"]),
                )
            elif operation.kind == ChangeOperationKind.CREATE_RELATION:
                self.projection.create_relation(
                    project_id,
                    RelationCreate.model_validate(operation.payload),
                    relation_id=operation.operation_id,
                )
            elif operation.kind == ChangeOperationKind.UPDATE_RELATION:
                self.projection.update_relation(
                    project_id,
                    self._required_target(operation),
                    RelationUpdate.model_validate(operation.payload),
                )
            elif operation.kind == ChangeOperationKind.RETIRE_RELATION:
                self.projection.retire_relation(
                    project_id,
                    self._required_target(operation),
                    int(operation.payload["expected_revision"]),
                )
            elif operation.kind == ChangeOperationKind.CREATE_EVENT:
                self.projection.create_event(
                    project_id,
                    EventCreate.model_validate(operation.payload),
                    event_id=operation.operation_id,
                )
        row.status = ChangeSetStatus.APPLIED.value
        row.validation = json_ready(validation.model_dump(mode="json"))
        self.session.flush()
        return self._view(row)

    def require(self, project_id: UUID, change_set_id: UUID) -> ChangeSetRow:
        row = self.session.get(ChangeSetRow, str(change_set_id))
        if row is None or row.project_id != str(project_id):
            raise DomainError("CHANGESET_NOT_FOUND", "变更集不存在。", status_code=404)
        return row

    def _validate_row(self, row: ChangeSetRow) -> ChangeSetValidation:
        payload: Any
        target: Any
        candidate: Any
        issues: list[ValidationIssue] = []
        project = self.portfolio.require_project(row.project_id)
        if project.revision != row.base_revision:
            issues.append(
                ValidationIssue(
                    severity="ERROR",
                    code="BASE_REVISION_CONFLICT",
                    message="项目当前版本与变更集基准版本不一致。",
                )
            )
        try:
            operations = [ChangeOperation.model_validate(item) for item in row.operations]
        except ValidationError as exc:
            return ChangeSetValidation(
                valid=False,
                issues=[
                    ValidationIssue(
                        severity="ERROR",
                        code="INVALID_OPERATION",
                        message=str(exc),
                    )
                ],
                checked_at=now_utc(),
            )
        operation_ids = [item.operation_id for item in operations]
        if len(operation_ids) != len(set(operation_ids)):
            issues.append(
                ValidationIssue(
                    severity="ERROR",
                    code="DUPLICATE_OPERATION_ID",
                    message="变更操作标识不能重复。",
                )
            )

        mutations: set[tuple[str, UUID]] = set()
        for operation in operations:
            resource = (
                "ENTITY"
                if operation.kind
                in {ChangeOperationKind.UPDATE_ENTITY, ChangeOperationKind.RETIRE_ENTITY}
                else (
                    "RELATION"
                    if operation.kind
                    in {
                        ChangeOperationKind.UPDATE_RELATION,
                        ChangeOperationKind.RETIRE_RELATION,
                    }
                    else None
                )
            )
            if resource is None or operation.target_id is None:
                continue
            mutation = (resource, operation.target_id)
            if mutation in mutations:
                issues.append(
                    ValidationIssue(
                        severity="ERROR",
                        code="DUPLICATE_RESOURCE_MUTATION",
                        message="同一变更集不能多次修改或退役同一个资源。",
                        operation_id=operation.operation_id,
                    )
                )
            mutations.add(mutation)

        temporary_entities: dict[UUID, EntityCreate] = {}
        temporary_relation_ids = {
            item.operation_id
            for item in operations
            if item.kind == ChangeOperationKind.CREATE_RELATION
        }
        temporary_stable_keys: set[str] = set()
        for operation in operations:
            if operation.kind != ChangeOperationKind.CREATE_ENTITY:
                continue
            try:
                payload = EntityCreate.model_validate(operation.payload)
                self.projection.validate_entity_payload(UUID(row.project_id), payload)
                if self.session.get(EntityRow, str(operation.operation_id)) is not None:
                    raise DomainError(
                        "CHANGESET_ENTITY_ID_CONFLICT",
                        "新建实体的预分配标识已经存在。",
                        status_code=409,
                    )
                if payload.stable_key is not None:
                    existing = self.session.scalar(
                        select(EntityRow.id).where(
                            EntityRow.project_id == row.project_id,
                            EntityRow.stable_key == payload.stable_key,
                        )
                    )
                    if existing is not None or payload.stable_key in temporary_stable_keys:
                        raise DomainError(
                            "ENTITY_STABLE_KEY_CONFLICT",
                            "项目内实体稳定标识不能重复。",
                            status_code=409,
                        )
                    temporary_stable_keys.add(payload.stable_key)
                temporary_entities[operation.operation_id] = payload
            except (DomainError, ValidationError) as exc:
                issues.append(self._validation_issue(operation, exc))

        for operation in operations:
            try:
                if operation.kind == ChangeOperationKind.CREATE_ENTITY:
                    continue
                elif operation.kind == ChangeOperationKind.UPDATE_ENTITY:
                    self._reject_temporary_target(operation, temporary_entities)
                    payload = EntityUpdate.model_validate(operation.payload)
                    target = self.projection.require_entity(
                        row.project_id, self._required_target(operation)
                    )
                    if target.revision != payload.expected_revision:
                        raise DomainError("REVISION_CONFLICT", "实体版本冲突。", status_code=409)
                    candidate = EntityCreate(
                        type_key=target.type_key,
                        stable_key=target.stable_key,
                        name=payload.name if payload.name is not None else target.name,
                        properties=(
                            payload.properties
                            if payload.properties is not None
                            else target.properties
                        ),
                        viewpoint=(
                            payload.viewpoint
                            if payload.viewpoint is not None
                            else target.viewpoint
                        ),
                        evidence=(
                            payload.evidence if payload.evidence is not None else target.evidence
                        ),
                    )
                    self.projection.validate_entity_payload(UUID(row.project_id), candidate)
                elif operation.kind == ChangeOperationKind.RETIRE_ENTITY:
                    self._reject_temporary_target(operation, temporary_entities)
                    target = self.projection.require_entity(
                        row.project_id, self._required_target(operation)
                    )
                    expected_revision = int(operation.payload["expected_revision"])
                    if target.revision != expected_revision:
                        raise DomainError("REVISION_CONFLICT", "实体版本冲突。", status_code=409)
                    self._validate_retire_entity_dependencies(
                        UUID(row.project_id), UUID(target.id), operations
                    )
                elif operation.kind == ChangeOperationKind.CREATE_RELATION:
                    if self.session.get(RelationRow, str(operation.operation_id)) is not None:
                        raise DomainError(
                            "CHANGESET_RELATION_ID_CONFLICT",
                            "新建关系的预分配标识已经存在。",
                            status_code=409,
                        )
                    payload = RelationCreate.model_validate(operation.payload)
                    self._validate_relation_with_temporary(
                        UUID(row.project_id), payload, temporary_entities
                    )
                elif operation.kind == ChangeOperationKind.UPDATE_RELATION:
                    self._reject_temporary_target(operation, temporary_relation_ids)
                    payload = RelationUpdate.model_validate(operation.payload)
                    target = self.projection.require_relation(
                        row.project_id, self._required_target(operation)
                    )
                    if target.revision != payload.expected_revision:
                        raise DomainError("REVISION_CONFLICT", "关系版本冲突。", status_code=409)
                    candidate = RelationCreate(
                        type_key=target.type_key,
                        name=payload.name if payload.name is not None else target.name,
                        participants=(
                            payload.participants
                            if payload.participants is not None
                            else [
                                RelationParticipantInput(
                                    role_key=item.role_key,
                                    entity_id=UUID(item.entity_id),
                                    ordinal=item.ordinal,
                                )
                                for item in target.participants
                            ]
                        ),
                        properties=(
                            payload.properties
                            if payload.properties is not None
                            else target.properties
                        ),
                        viewpoint=(
                            payload.viewpoint
                            if payload.viewpoint is not None
                            else target.viewpoint
                        ),
                        evidence=(
                            payload.evidence if payload.evidence is not None else target.evidence
                        ),
                    )
                    self._validate_relation_with_temporary(
                        UUID(row.project_id), candidate, temporary_entities
                    )
                elif operation.kind == ChangeOperationKind.RETIRE_RELATION:
                    self._reject_temporary_target(operation, temporary_relation_ids)
                    target = self.projection.require_relation(
                        row.project_id, self._required_target(operation)
                    )
                    expected_revision = int(operation.payload["expected_revision"])
                    if target.revision != expected_revision:
                        raise DomainError("REVISION_CONFLICT", "关系版本冲突。", status_code=409)
                elif operation.kind == ChangeOperationKind.CREATE_EVENT:
                    if self.session.get(EventRow, str(operation.operation_id)) is not None:
                        raise DomainError(
                            "CHANGESET_EVENT_ID_CONFLICT",
                            "新建事件的预分配标识已经存在。",
                            status_code=409,
                        )
                    payload = EventCreate.model_validate(operation.payload)
                    self._validate_event_with_temporary(
                        UUID(row.project_id), payload, temporary_entities
                    )
            except (DomainError, ValidationError, KeyError, TypeError, ValueError) as exc:
                issues.append(self._validation_issue(operation, exc))
        return ChangeSetValidation(valid=not issues, issues=issues, checked_at=now_utc())

    def _validate_relation_with_temporary(
        self,
        project_id: UUID,
        payload: RelationCreate,
        temporary_entities: dict[UUID, EntityCreate],
    ) -> None:
        relation_type = self.ontology.require_type_by_key(
            project_id, payload.type_key, kind=TypeKind.RELATION
        )
        self.ontology.validate_properties(relation_type, payload.properties)
        validate_evidence_references(self.session, project_id, payload.evidence)
        definitions = {item["key"]: item for item in relation_type.relation_roles}
        counts = Counter(item.role_key for item in payload.participants)
        unknown_roles = sorted(set(counts) - set(definitions))
        if unknown_roles:
            raise DomainError(
                "UNKNOWN_RELATION_ROLES",
                "关系参与角色未定义。",
                status_code=422,
                details=[{"roles": unknown_roles}],
            )
        participant_keys = [
            (item.role_key, item.entity_id, item.ordinal) for item in payload.participants
        ]
        if len(participant_keys) != len(set(participant_keys)):
            raise DomainError(
                "DUPLICATE_RELATION_PARTICIPANT",
                "关系不能包含完全重复的参与者。",
                status_code=422,
            )
        for role, definition in definitions.items():
            count = counts[role]
            if count < definition.get("minimum", 0):
                raise DomainError(
                    "RELATION_CARDINALITY_VIOLATION",
                    f"关系角色 {role} 缺少参与者。",
                    status_code=422,
                )
            maximum = definition.get("maximum")
            if maximum is not None and count > maximum:
                raise DomainError(
                    "RELATION_CARDINALITY_VIOLATION",
                    f"关系角色 {role} 参与者过多。",
                    status_code=422,
                )
        for participant in payload.participants:
            temporary = temporary_entities.get(participant.entity_id)
            if temporary is not None:
                entity_type_key = temporary.type_key
                entity_name = temporary.name
            else:
                entity = self.projection.require_entity(project_id, participant.entity_id)
                if entity.status == LifecycleStatus.RETIRED.value:
                    raise DomainError(
                        "RELATION_PARTICIPANT_RETIRED",
                        f"已退役实体 {entity.name} 不能参与新关系。",
                        status_code=422,
                    )
                if entity.design_membership != "MODELED":
                    raise DomainError(
                        "RELATION_PARTICIPANT_UNMODELED",
                        f"未完成身份对齐的系统对象 {entity.name} 不能参与正式关系。",
                        status_code=422,
                    )
                entity_type_key = entity.type_key
                entity_name = entity.name
            allowed = set(definitions[participant.role_key].get("allowed_type_keys") or [])
            entity_type = self.ontology.require_type_by_key(project_id, entity_type_key)
            if allowed and not self.ontology.entity_type_satisfies(entity_type, allowed):
                raise DomainError(
                    "RELATION_PARTICIPANT_TYPE_MISMATCH",
                    f"实体 {entity_name} 不能担任关系角色 {participant.role_key}。",
                    status_code=422,
                )

    def _validate_event_with_temporary(
        self,
        project_id: UUID,
        payload: EventCreate,
        temporary_entities: dict[UUID, EntityCreate],
    ) -> None:
        event_type = self.ontology.require_type_by_key(
            project_id, payload.type_key, kind=TypeKind.EVENT
        )
        self.ontology.validate_properties(event_type, payload.properties)
        validate_evidence_references(self.session, project_id, payload.evidence)
        for participant in payload.participants:
            if participant.entity_id in temporary_entities:
                continue
            entity = self.projection.require_entity(project_id, participant.entity_id)
            if entity.status == LifecycleStatus.RETIRED.value:
                raise DomainError(
                    "EVENT_PARTICIPANT_RETIRED",
                    f"已退役实体 {entity.name} 不能参与新事件。",
                    status_code=422,
                )
            if entity.design_membership != "MODELED":
                raise DomainError(
                    "EVENT_PARTICIPANT_UNMODELED",
                    f"未完成身份对齐的系统对象 {entity.name} 不能参与正式事件。",
                    status_code=422,
                )

    def _validate_retire_entity_dependencies(
        self,
        project_id: UUID,
        entity_id: UUID,
        operations: Sequence[ChangeOperation],
    ) -> None:
        retired_relation_ids = {
            item.target_id
            for item in operations
            if item.kind == ChangeOperationKind.RETIRE_RELATION
            and item.target_id is not None
        }
        relation_updates: dict[UUID, RelationUpdate] = {}
        for operation in operations:
            if (
                operation.kind == ChangeOperationKind.UPDATE_RELATION
                and operation.target_id is not None
            ):
                relation_updates[operation.target_id] = RelationUpdate.model_validate(
                    operation.payload
                )

        active_relation_ids = self.session.scalars(
            select(RelationRow.id)
            .join(RelationParticipantRow)
            .where(
                RelationRow.project_id == str(project_id),
                RelationRow.status != LifecycleStatus.RETIRED.value,
                RelationParticipantRow.entity_id == str(entity_id),
            )
        ).all()
        for raw_relation_id in active_relation_ids:
            relation_id = UUID(raw_relation_id)
            if relation_id in retired_relation_ids:
                continue
            update = relation_updates.get(relation_id)
            if update is not None and update.participants is not None and all(
                item.entity_id != entity_id for item in update.participants
            ):
                continue
            raise DomainError(
                "ENTITY_HAS_ACTIVE_RELATIONS",
                "实体仍参与有效关系；变更集中必须先退役关系或移除该参与方。",
                status_code=409,
                details=[{"relation_id": str(relation_id)}],
            )

        for operation in operations:
            if operation.kind == ChangeOperationKind.CREATE_RELATION:
                participants = RelationCreate.model_validate(operation.payload).participants
            elif (
                operation.kind == ChangeOperationKind.UPDATE_RELATION
                and operation.target_id not in retired_relation_ids
            ):
                participants = RelationUpdate.model_validate(operation.payload).participants or []
            else:
                continue
            if any(item.entity_id == entity_id for item in participants):
                raise DomainError(
                    "RETIRED_ENTITY_REFERENCED_IN_CHANGESET",
                    "变更集中的关系仍引用了将被退役的实体。",
                    status_code=422,
                    details=[{"operation_id": str(operation.operation_id)}],
                )

    @staticmethod
    def _reject_temporary_target(
        operation: ChangeOperation,
        temporary_ids: Collection[UUID],
    ) -> None:
        if operation.target_id is not None and operation.target_id in temporary_ids:
            raise DomainError(
                "CHANGESET_TEMP_TARGET_UNSUPPORTED",
                "新建资源请在 CREATE 操作中给出最终内容，不能在同批次再次修改或退役。",
                status_code=422,
            )

    @staticmethod
    def _validation_issue(
        operation: ChangeOperation,
        exc: DomainError | ValidationError | KeyError | TypeError | ValueError,
    ) -> ValidationIssue:
        message = exc.message if isinstance(exc, DomainError) else str(exc)
        code = exc.code if isinstance(exc, DomainError) else "INVALID_OPERATION_PAYLOAD"
        return ValidationIssue(
            severity="ERROR",
            code=code,
            message=message,
            operation_id=operation.operation_id,
        )

    @staticmethod
    def _required_target(operation: ChangeOperation) -> UUID:
        if operation.target_id is None:
            raise DomainError(
                "CHANGE_OPERATION_TARGET_REQUIRED",
                f"{operation.kind.value} 操作缺少目标。",
                status_code=422,
            )
        return operation.target_id

    @staticmethod
    def _ensure_mutable(row: ChangeSetRow) -> None:
        if row.status == ChangeSetStatus.APPLIED.value:
            raise DomainError(
                "CHANGESET_ALREADY_APPLIED", "已应用的变更集不能修改。", status_code=409
            )

    @staticmethod
    def _view(row: ChangeSetRow) -> ChangeSetView:
        return ChangeSetView.model_validate(row)
