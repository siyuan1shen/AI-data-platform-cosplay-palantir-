from __future__ import annotations

import json
from typing import Any
from uuid import UUID

from sqlalchemy import select
from sqlalchemy.orm import Session

from enterprise_insight_backend.errors import DomainError
from enterprise_insight_backend.learning import LearningCaseService
from enterprise_insight_backend.models import (
    AgentMessageRow,
    AgentRunRow,
    AgentStepRow,
    AgentThreadRow,
    EntityRow,
    HypothesisFeedbackRow,
    HypothesisRow,
    PublicationRow,
    RelationRow,
    ScenarioRow,
    SourceDocumentRow,
)
from enterprise_insight_backend.portfolio import PortfolioService
from enterprise_insight_backend.query_snapshots import QuerySnapshotService
from enterprise_insight_backend.schemas import (
    AgentKind,
    AgentMessageAccepted,
    AgentMessageCreate,
    AgentMessageRole,
    AgentMessageView,
    AgentRunStatus,
    AgentRunView,
    AgentStepView,
    AgentThreadCreate,
    AgentThreadUpdate,
    AgentThreadView,
    CausalHypothesisCreate,
    CausalHypothesisView,
    ChangeOperation,
    ChangeOperationKind,
    ChangeSetCreate,
    ExplorationModuleView,
    GraphQuery,
    HypothesisCreate,
    HypothesisFeedbackCreate,
    HypothesisFeedbackView,
    HypothesisView,
    QueryModelScope,
    QuerySnapshotCreate,
    ScenarioComparisonView,
    ScenarioCreate,
    ScenarioDiffView,
    ScenarioRevisionRequest,
    ScenarioStatus,
    ScenarioUpdate,
    ScenarioView,
)
from enterprise_insight_backend.service_utils import json_ready, require_revision

BUILTIN_EXPLORATION_MODULES: tuple[ExplorationModuleView, ...] = (
    ExplorationModuleView(
        id="first_principles.organization_design",
        version="1.0.0",
        name="第一性原理组织设计",
        description="从价值、能力、活动、决策、信息与控制重新推导组织，而非沿用现有部门边界。",
        applicable_goals=["架构优化", "效率提升", "权责重构", "规模扩张", "降本增效"],
        required_interfaces=["organizational_subject", "work_bearer"],
        enabled=True,
        output_schema={
            "type": "object",
            "required": ["current_diagnosis", "options", "unknowns"],
            "properties": {
                "current_diagnosis": {"type": "array"},
                "options": {"type": "array", "minItems": 2},
                "unknowns": {"type": "array"},
            },
        },
    ),
    ExplorationModuleView(
        id="latent_relations.open_discovery",
        version="1.0.0",
        name="潜在关系开放探索",
        description="高召回地探索知识、利益、非正式影响、代理问题和风险耦合等潜在联系。",
        applicable_goals=["发现问题", "风险探索", "潜在关系", "管理诊断"],
        required_interfaces=[],
        enabled=True,
        output_schema={
            "type": "object",
            "required": ["hypotheses"],
            "properties": {"hypotheses": {"type": "array"}},
        },
    ),
)


class CollaborationService:
    def __init__(self, session: Session) -> None:
        self.session = session
        self.portfolio = PortfolioService(session)

    def list_threads(
        self,
        project_id: UUID,
        *,
        include_trashed: bool = False,
    ) -> list[AgentThreadView]:
        self.portfolio.require_project(project_id)
        statement = select(AgentThreadRow).where(AgentThreadRow.project_id == str(project_id))
        if not include_trashed:
            statement = statement.where(AgentThreadRow.trashed.is_(False))
        rows = self.session.scalars(statement.order_by(AgentThreadRow.updated_at.desc())).all()
        return [AgentThreadView.model_validate(row) for row in rows]

    def create_thread(self, project_id: UUID, payload: AgentThreadCreate) -> AgentThreadView:
        self.portfolio.require_project(project_id)
        title = (
            payload.title
            or {
                "PROJECTION": "新的企业投影对话",
                "MANAGEMENT": "新的管理探索对话",
                "MANAGEMENT_INPUT": "新的管理输入对话",
                "SYSTEM_ONTOLOGY": "新的系统对齐对话",
            }[payload.agent_kind.value]
        )
        row = AgentThreadRow(
            project_id=str(project_id),
            agent_kind=payload.agent_kind.value,
            title=title,
        )
        self.session.add(row)
        self.session.flush()
        return AgentThreadView.model_validate(row)

    def update_thread(
        self,
        project_id: UUID,
        thread_id: UUID,
        payload: AgentThreadUpdate,
    ) -> AgentThreadView:
        row = self.require_thread(project_id, thread_id)
        for key, value in payload.model_dump(exclude_unset=True).items():
            setattr(row, key, value)
        self.session.flush()
        return AgentThreadView.model_validate(row)

    def list_messages(self, project_id: UUID, thread_id: UUID) -> list[AgentMessageView]:
        self.require_thread(project_id, thread_id)
        rows = self.session.scalars(
            select(AgentMessageRow)
            .where(AgentMessageRow.thread_id == str(thread_id))
            .order_by(AgentMessageRow.created_at, AgentMessageRow.id)
        ).all()
        return [AgentMessageView.model_validate(row) for row in rows]

    def send_message(
        self,
        project_id: UUID,
        thread_id: UUID,
        payload: AgentMessageCreate,
    ) -> AgentMessageAccepted:
        thread = self.require_thread(project_id, thread_id)
        if (
            payload.task_intent_candidate is not None
            and thread.agent_kind != AgentKind.MANAGEMENT.value
        ):
            raise DomainError(
                "TASK_INTENT_CANDIDATE_WRONG_AGENT",
                "任务意图候选只可附加到管理输出 Agent 对话。",
                status_code=422,
            )
        LearningCaseService(self.session).require_references(project_id, payload.reference_case_ids)
        if payload.attachment_ids:
            document_ids = set(
                self.session.scalars(
                    select(SourceDocumentRow.id).where(
                        SourceDocumentRow.project_id == str(project_id),
                        SourceDocumentRow.id.in_([str(item) for item in payload.attachment_ids]),
                    )
                ).all()
            )
            missing = [
                str(item)
                for item in payload.attachment_ids
                if str(item) not in document_ids
            ]
            if missing:
                raise DomainError(
                    "AGENT_ATTACHMENT_NOT_FOUND",
                    "所选材料不存在或不属于当前项目。",
                    status_code=422,
                    details=[{"document_ids": missing}],
                )
        message = AgentMessageRow(
            thread_id=thread.id,
            role=AgentMessageRole.USER.value,
            content=payload.content,
            citations=[],
        )
        self.session.add(message)
        self.session.flush()
        project = self.portfolio.require_project(project_id)
        publication = None
        if thread.agent_kind == AgentKind.MANAGEMENT.value:
            publication = self.session.scalar(
                select(PublicationRow)
                .where(PublicationRow.project_id == str(project_id))
                .order_by(PublicationRow.version.desc())
                .limit(1)
            )
        snapshot = QuerySnapshotService(self.session).create(
            project_id,
            QuerySnapshotCreate(
                model_scope=(
                    QueryModelScope.PUBLISHED
                    if publication is not None
                    else QueryModelScope.DRAFT
                ),
                publication_id=UUID(publication.id) if publication is not None else None,
            ),
        )
        run = AgentRunRow(
            thread_id=thread.id,
            project_id=str(project_id),
            agent_kind=thread.agent_kind,
            status=AgentRunStatus.QUEUED.value,
            context_manifest={
                "project_revision": project.revision,
                "query_snapshot_id": str(snapshot.id),
                "include_unconfirmed_material": payload.include_unconfirmed_material,
                "attachment_ids": [str(item) for item in payload.attachment_ids],
                "reference_case_ids": [str(item) for item in payload.reference_case_ids],
                "model_profile_id": (
                    str(payload.model_profile_id) if payload.model_profile_id else None
                ),
                "allow_external_model": payload.allow_external_model,
                "share_project_context_with_model": payload.share_project_context_with_model,
                "task_intent_candidate": (
                    payload.task_intent_candidate.model_dump(mode="json")
                    if payload.task_intent_candidate is not None
                    else None
                ),
            },
        )
        self.session.add(run)
        self.session.flush()
        return AgentMessageAccepted(
            message=AgentMessageView.model_validate(message),
            run=AgentRunView.model_validate(run),
        )

    def list_runs(self, project_id: UUID, thread_id: UUID | None = None) -> list[AgentRunView]:
        self.portfolio.require_project(project_id)
        statement = select(AgentRunRow).where(AgentRunRow.project_id == str(project_id))
        if thread_id is not None:
            self.require_thread(project_id, thread_id)
            statement = statement.where(AgentRunRow.thread_id == str(thread_id))
        rows = self.session.scalars(statement.order_by(AgentRunRow.created_at.desc())).all()
        return [AgentRunView.model_validate(row) for row in rows]

    def get_run(self, project_id: UUID, run_id: UUID) -> AgentRunView:
        row = self.session.get(AgentRunRow, str(run_id))
        if row is None or row.project_id != str(project_id):
            raise DomainError("AGENT_RUN_NOT_FOUND", "Agent运行不存在。", status_code=404)
        return AgentRunView.model_validate(row)

    def list_steps(self, project_id: UUID, run_id: UUID) -> list[AgentStepView]:
        self.get_run(project_id, run_id)
        rows = self.session.scalars(
            select(AgentStepRow)
            .where(AgentStepRow.run_id == str(run_id))
            .order_by(AgentStepRow.position)
        ).all()
        return [AgentStepView.model_validate(row) for row in rows]

    def cancel_run(self, project_id: UUID, run_id: UUID) -> AgentRunView:
        row = self.session.get(AgentRunRow, str(run_id))
        if row is None or row.project_id != str(project_id):
            raise DomainError("AGENT_RUN_NOT_FOUND", "Agent运行不存在。", status_code=404)
        if row.status == AgentRunStatus.COMPLETED.value:
            raise DomainError(
                "AGENT_RUN_ALREADY_COMPLETED",
                "已完成的Agent运行不能取消。",
                status_code=409,
            )
        row.status = AgentRunStatus.CANCELLED.value
        row.worker_id = None
        row.lease_expires_at = None
        self.session.flush()
        return AgentRunView.model_validate(row)

    def require_thread(self, project_id: UUID, thread_id: UUID) -> AgentThreadRow:
        row = self.session.get(AgentThreadRow, str(thread_id))
        if row is None or row.project_id != str(project_id):
            raise DomainError("AGENT_THREAD_NOT_FOUND", "Agent对话不存在。", status_code=404)
        return row


class ExplorationService:
    def __init__(self, session: Session) -> None:
        self.session = session
        self.portfolio = PortfolioService(session)

    @staticmethod
    def modules() -> list[ExplorationModuleView]:
        return list(BUILTIN_EXPLORATION_MODULES)

    def list_hypotheses(self, project_id: UUID) -> list[HypothesisView]:
        self.portfolio.require_project(project_id)
        rows = self.session.scalars(
            select(HypothesisRow)
            .where(HypothesisRow.project_id == str(project_id))
            .order_by(HypothesisRow.updated_at.desc())
        ).all()
        return [HypothesisView.model_validate(row) for row in rows]

    def create_hypothesis(
        self,
        project_id: UUID,
        payload: HypothesisCreate,
        *,
        source: str = "MANAGEMENT",
        module_id: str | None = None,
        module_version: str | None = None,
        allow_reserved_type: bool = False,
    ) -> HypothesisView:
        self.portfolio.require_project(project_id)
        if payload.type_key == "causal_hypothesis" and not allow_reserved_type:
            raise DomainError(
                "RESERVED_HYPOTHESIS_TYPE",
                "因果假设必须通过专用因果假设接口创建。",
                status_code=422,
            )
        row = HypothesisRow(
            project_id=str(project_id),
            source=source,
            module_id=module_id,
            module_version=module_version,
            **json_ready(payload.model_dump(mode="json")),
        )
        self.session.add(row)
        self.session.flush()
        return HypothesisView.model_validate(row)

    def list_causal_hypotheses(self, project_id: UUID) -> list[CausalHypothesisView]:
        self.portfolio.require_project(project_id)
        rows = self.session.scalars(
            select(HypothesisRow)
            .where(
                HypothesisRow.project_id == str(project_id),
                HypothesisRow.type_key == "causal_hypothesis",
            )
            .order_by(HypothesisRow.updated_at.desc())
        ).all()
        return [self._causal_view(row) for row in rows]

    def create_causal_hypothesis(
        self,
        project_id: UUID,
        payload: CausalHypothesisCreate,
        *,
        source: str = "MANAGEMENT",
    ) -> CausalHypothesisView:
        self.validate_causal_hypothesis(project_id, payload)
        view = self.create_hypothesis(
            project_id,
            HypothesisCreate(
                type_key="causal_hypothesis",
                title=payload.title,
                summary=payload.mechanism,
                participant_entity_ids=[
                    *payload.cause_entity_ids,
                    *payload.effect_entity_ids,
                ],
                supporting_facts=payload.supporting_facts,
                counter_evidence=payload.counter_evidence,
                alternative_explanations=payload.alternative_explanations,
                uncertainties=payload.uncertainties,
                validation_questions=payload.validation_questions,
                extension_payload={
                    "cause_entity_ids": [str(item) for item in payload.cause_entity_ids],
                    "effect_entity_ids": [str(item) for item in payload.effect_entity_ids],
                    "predictions": payload.predictions,
                    "intervention_test": payload.intervention_test,
                },
            ),
            source=source,
            module_id="causal.hypothesis",
            module_version="1.0.0",
            allow_reserved_type=True,
        )
        row = self.require_hypothesis(project_id, view.id)
        return self._causal_view(row)

    def validate_causal_hypothesis(
        self, project_id: UUID | str, payload: CausalHypothesisCreate
    ) -> None:
        self.portfolio.require_project(project_id)
        entity_ids = {str(item) for item in [*payload.cause_entity_ids, *payload.effect_entity_ids]}
        found = set(
            self.session.scalars(
                select(EntityRow.id).where(
                    EntityRow.project_id == str(project_id), EntityRow.id.in_(entity_ids)
                )
            ).all()
        )
        if found != entity_ids:
            raise DomainError(
                "CAUSAL_ENTITY_REFERENCE_INVALID",
                "因果假设引用的企业对象不存在或不属于当前项目。",
                status_code=422,
            )

    def add_feedback(
        self,
        project_id: UUID,
        hypothesis_id: UUID,
        payload: HypothesisFeedbackCreate,
    ) -> HypothesisFeedbackView:
        hypothesis = self.require_hypothesis(project_id, hypothesis_id)
        feedback = HypothesisFeedbackRow(
            hypothesis_id=hypothesis.id,
            status=payload.status.value,
            comment=payload.comment,
            provided_by=payload.provided_by,
        )
        hypothesis.status = payload.status.value
        self.session.add(feedback)
        self.session.flush()
        return HypothesisFeedbackView.model_validate(feedback)

    def require_hypothesis(self, project_id: UUID, hypothesis_id: UUID) -> HypothesisRow:
        row = self.session.get(HypothesisRow, str(hypothesis_id))
        if row is None or row.project_id != str(project_id):
            raise DomainError("HYPOTHESIS_NOT_FOUND", "潜在问题假设不存在。", status_code=404)
        return row

    def list_scenarios(self, project_id: UUID) -> list[ScenarioView]:
        self.portfolio.require_project(project_id)
        rows = self.session.scalars(
            select(ScenarioRow)
            .where(ScenarioRow.project_id == str(project_id))
            .order_by(ScenarioRow.updated_at.desc())
        ).all()
        return [ScenarioView.model_validate(row) for row in rows]

    def create_scenario(self, project_id: UUID, payload: ScenarioCreate) -> ScenarioView:
        project = self.portfolio.require_project(project_id)
        row = ScenarioRow(
            project_id=str(project_id),
            name=payload.name,
            description=payload.description,
            goal=payload.goal,
            assumptions=payload.assumptions,
            overlay_operations=json_ready(
                [item.model_dump(mode="json") for item in payload.overlay_operations]
            ),
            expected_benefits=payload.expected_benefits,
            risks=payload.risks,
            validation_metrics=payload.validation_metrics,
            base_revision=project.revision,
        )
        self.session.add(row)
        self.session.flush()
        self._validate_scenario_graph(project_id, UUID(row.id))
        return ScenarioView.model_validate(row)

    def update_scenario(
        self, project_id: UUID, scenario_id: UUID, payload: ScenarioUpdate
    ) -> ScenarioView:
        row = self.require_scenario(project_id, scenario_id)
        if row.applied_change_set_id is not None:
            raise DomainError(
                "SCENARIO_ALREADY_APPLIED",
                "已经应用到正式投影的方案不能再修改。",
                status_code=409,
            )
        require_revision(row.revision, payload.expected_revision, resource="管理方案")
        values = payload.model_dump(exclude_unset=True, exclude={"expected_revision"})
        requested_status = values.pop("status", None)
        content_keys = {
            "name",
            "description",
            "goal",
            "assumptions",
            "overlay_operations",
            "expected_benefits",
            "risks",
            "validation_metrics",
        }
        content_changed = bool(content_keys.intersection(values))
        if "overlay_operations" in values:
            values["overlay_operations"] = json_ready(
                [
                    item.model_dump(mode="json")
                    for item in (payload.overlay_operations or [])
                ]
            )
        for key, value in values.items():
            setattr(row, key, value)
        if content_changed:
            row.base_revision = self.portfolio.require_project(project_id).revision
            row.status = ScenarioStatus.DRAFT.value
        if requested_status is not None:
            self._transition_scenario(row, requested_status)
        row.revision += 1
        self.session.flush()
        self._validate_scenario_graph(project_id, scenario_id)
        return ScenarioView.model_validate(row)

    def scenario_diff(self, project_id: UUID, scenario_id: UUID) -> ScenarioDiffView:
        row = self.require_scenario(project_id, scenario_id)
        project = self.portfolio.require_project(project_id)
        operations = [ChangeOperation.model_validate(item) for item in row.overlay_operations]
        conflicts = self._scenario_conflicts(project_id, operations)
        return ScenarioDiffView.model_validate(
            {
                "scenario_id": row.id,
                "base_revision": row.base_revision,
                "current_revision": project.revision,
                "rebase_required": row.base_revision != project.revision,
                "creates": [
                    item for item in operations if item.kind.value.startswith("CREATE_")
                ],
                "updates": [
                    item for item in operations if item.kind.value.startswith("UPDATE_")
                ],
                "retires": [
                    item for item in operations if item.kind.value.startswith("RETIRE_")
                ],
                "conflicts": conflicts,
            }
        )

    def compare_scenarios(
        self, project_id: UUID, left_id: UUID, right_id: UUID
    ) -> ScenarioComparisonView:
        left = self.require_scenario(project_id, left_id)
        right = self.require_scenario(project_id, right_id)
        left_operations = [ChangeOperation.model_validate(item) for item in left.overlay_operations]
        right_operations = [
            ChangeOperation.model_validate(item) for item in right.overlay_operations
        ]
        left_by_fingerprint = {self._operation_fingerprint(item): item for item in left_operations}
        right_by_fingerprint = {
            self._operation_fingerprint(item): item for item in right_operations
        }
        shared_keys = set(left_by_fingerprint).intersection(right_by_fingerprint)
        left_targets = self._operations_by_target(left_operations)
        right_targets = self._operations_by_target(right_operations)
        conflicts: list[dict[str, object]] = []
        for target in sorted(set(left_targets).intersection(right_targets)):
            left_operation = left_targets[target]
            right_operation = right_targets[target]
            if self._operation_fingerprint(left_operation) != self._operation_fingerprint(
                right_operation
            ):
                conflicts.append(
                    {
                        "target": target,
                        "left": left_operation.model_dump(mode="json"),
                        "right": right_operation.model_dump(mode="json"),
                    }
                )
        return ScenarioComparisonView.model_validate(
            {
                "left_scenario_id": left.id,
                "right_scenario_id": right.id,
                "shared_operations": [
                    left_by_fingerprint[key] for key in sorted(shared_keys)
                ],
                "only_left": [
                    left_by_fingerprint[key]
                    for key in sorted(set(left_by_fingerprint) - shared_keys)
                ],
                "only_right": [
                    right_by_fingerprint[key]
                    for key in sorted(set(right_by_fingerprint) - shared_keys)
                ],
                "conflicting_targets": conflicts,
            }
        )

    def rebase_scenario(
        self, project_id: UUID, scenario_id: UUID, payload: ScenarioRevisionRequest
    ) -> ScenarioView:
        row = self.require_scenario(project_id, scenario_id)
        if row.applied_change_set_id is not None:
            raise DomainError(
                "SCENARIO_ALREADY_APPLIED", "已应用方案不需要重基。", status_code=409
            )
        require_revision(row.revision, payload.expected_revision, resource="管理方案")
        operations = [ChangeOperation.model_validate(item) for item in row.overlay_operations]
        conflicts = self._scenario_conflicts(project_id, operations)
        if conflicts:
            raise DomainError(
                "SCENARIO_REBASE_CONFLICT",
                "正式投影中的目标已经变化，请先修改方案操作后再重基。",
                status_code=409,
                details=conflicts,
            )
        row.base_revision = self.portfolio.require_project(project_id).revision
        row.revision += 1
        self.session.flush()
        return ScenarioView.model_validate(row)

    def apply_scenario(
        self, project_id: UUID, scenario_id: UUID, payload: ScenarioRevisionRequest
    ) -> ScenarioView:
        from enterprise_insight_backend.changes import ChangeSetService

        row = self.require_scenario(project_id, scenario_id)
        require_revision(row.revision, payload.expected_revision, resource="管理方案")
        if row.status != ScenarioStatus.APPROVED.value:
            raise DomainError(
                "SCENARIO_NOT_APPROVED", "方案必须先经管理层批准。", status_code=409
            )
        project = self.portfolio.require_project(project_id)
        if row.base_revision != project.revision:
            raise DomainError(
                "SCENARIO_REBASE_REQUIRED",
                "正式投影已变化，应用方案前必须先重基并处理冲突。",
                status_code=409,
                details=[
                    {"base_revision": row.base_revision, "current_revision": project.revision}
                ],
            )
        service = ChangeSetService(self.session)
        change_set = service.create(
            project_id,
            ChangeSetCreate(
                title=f"应用管理方案：{row.name}",
                description=row.description,
                base_revision=project.revision,
                operations=[
                    ChangeOperation.model_validate(item) for item in row.overlay_operations
                ],
                created_by=payload.requested_by,
            ),
        )
        service.validate(project_id, change_set.id)
        service.approve(project_id, change_set.id)
        applied = service.apply(project_id, change_set.id)
        row.applied_change_set_id = str(applied.id)
        row.status = ScenarioStatus.ACTIVE.value
        row.revision += 1
        self.session.flush()
        return ScenarioView.model_validate(row)

    def require_scenario(self, project_id: UUID, scenario_id: UUID) -> ScenarioRow:
        row = self.session.get(ScenarioRow, str(scenario_id))
        if row is None or row.project_id != str(project_id):
            raise DomainError("SCENARIO_NOT_FOUND", "方案不存在。", status_code=404)
        return row

    def _validate_scenario_graph(self, project_id: UUID, scenario_id: UUID) -> None:
        from enterprise_insight_backend.projection import ProjectionService

        ProjectionService(self.session).graph(
            project_id, GraphQuery(scenario_id=scenario_id, include_observations=False)
        )

    def _scenario_conflicts(
        self, project_id: UUID, operations: list[ChangeOperation]
    ) -> list[dict[str, object]]:
        conflicts: list[dict[str, object]] = []
        for operation in operations:
            if operation.target_id is None or operation.kind.value.startswith("CREATE_"):
                continue
            model = (
                EntityRow
                if operation.kind
                in {ChangeOperationKind.UPDATE_ENTITY, ChangeOperationKind.RETIRE_ENTITY}
                else RelationRow
            )
            target: Any = self.session.get(model, str(operation.target_id))
            if target is None or target.project_id != str(project_id):
                conflicts.append(
                    {
                        "code": "TARGET_MISSING",
                        "operation_id": str(operation.operation_id),
                        "target_id": str(operation.target_id),
                    }
                )
                continue
            expected = operation.payload.get("expected_revision")
            if expected is not None and int(expected) != target.revision:
                conflicts.append(
                    {
                        "code": "TARGET_REVISION_CHANGED",
                        "operation_id": str(operation.operation_id),
                        "target_id": str(operation.target_id),
                        "expected_revision": int(expected),
                        "actual_revision": target.revision,
                    }
                )
        return conflicts

    @staticmethod
    def _operation_fingerprint(operation: ChangeOperation) -> str:
        return json.dumps(operation.model_dump(mode="json"), sort_keys=True, ensure_ascii=False)

    @staticmethod
    def _operations_by_target(
        operations: list[ChangeOperation],
    ) -> dict[str, ChangeOperation]:
        return {
            str(item.target_id or item.operation_id): item
            for item in operations
        }

    @staticmethod
    def _transition_scenario(row: ScenarioRow, status: ScenarioStatus) -> None:
        allowed: dict[str, set[str]] = {
            ScenarioStatus.DRAFT.value: {
                ScenarioStatus.UNDER_REVIEW.value,
                ScenarioStatus.REJECTED.value,
            },
            ScenarioStatus.UNDER_REVIEW.value: {
                ScenarioStatus.DRAFT.value,
                ScenarioStatus.APPROVED.value,
                ScenarioStatus.REJECTED.value,
            },
            ScenarioStatus.APPROVED.value: {ScenarioStatus.REJECTED.value},
            ScenarioStatus.REJECTED.value: {ScenarioStatus.DRAFT.value},
        }
        if status.value == row.status:
            return
        if status.value not in allowed.get(row.status, set()):
            raise DomainError(
                "SCENARIO_STATUS_TRANSITION_INVALID",
                f"方案不能从 {row.status} 变更为 {status.value}。",
                status_code=409,
            )
        row.status = status.value

    @staticmethod
    def _causal_view(row: HypothesisRow) -> CausalHypothesisView:
        extension = row.extension_payload or {}
        return CausalHypothesisView.model_validate(
            {
                "id": row.id,
                "project_id": row.project_id,
                "title": row.title,
                "cause_entity_ids": extension.get("cause_entity_ids", []),
                "effect_entity_ids": extension.get("effect_entity_ids", []),
                "mechanism": row.summary,
                "predictions": extension.get("predictions", []),
                "intervention_test": extension.get("intervention_test"),
                "supporting_facts": row.supporting_facts,
                "counter_evidence": row.counter_evidence,
                "alternative_explanations": row.alternative_explanations,
                "uncertainties": row.uncertainties,
                "validation_questions": row.validation_questions,
                "status": row.status,
                "source": row.source,
                "created_at": row.created_at,
                "updated_at": row.updated_at,
            }
        )
