from __future__ import annotations

import statistics
from uuid import UUID

from sqlalchemy import select
from sqlalchemy.exc import IntegrityError
from sqlalchemy.orm import Session

from enterprise_insight_backend.agent_runtime import AgentRuntimeService
from enterprise_insight_backend.collaboration import CollaborationService
from enterprise_insight_backend.config import Settings
from enterprise_insight_backend.errors import DomainError
from enterprise_insight_backend.evidence_validation import validate_evidence_references
from enterprise_insight_backend.models import (
    ActionDefinitionRow,
    ActionInvocationRow,
    AgentMessageRow,
    AgentRunRow,
    EvaluationCaseRow,
    EvaluationExecutionRow,
    EvaluationResultRow,
    EvaluationRunRow,
    EvaluationSuiteRow,
    ModelProfileRow,
)
from enterprise_insight_backend.portfolio import PortfolioService
from enterprise_insight_backend.schemas import (
    ActionInvocationStatus,
    AgentKind,
    AgentMessageCreate,
    AgentMessageRole,
    AgentRunStatus,
    AgentThreadCreate,
    EvaluationCaseCreate,
    EvaluationCaseStatus,
    EvaluationCaseView,
    EvaluationExecuteCreate,
    EvaluationExecutionStatus,
    EvaluationExecutionView,
    EvaluationPrediction,
    EvaluationResultView,
    EvaluationRunCreate,
    EvaluationRunView,
    EvaluationSuiteCreate,
    EvaluationSuiteStatus,
    EvaluationSuiteUpdate,
    EvaluationSuiteView,
    EvidenceReference,
)
from enterprise_insight_backend.service_utils import json_ready, require_revision


class EvaluationService:
    """Persistent golden sets and deterministic scoring for Agent outputs."""

    def __init__(self, session: Session) -> None:
        self.session = session
        self.portfolio = PortfolioService(session)

    def list_suites(self, project_id: UUID) -> list[EvaluationSuiteView]:
        self.portfolio.require_project(project_id)
        rows = self.session.scalars(
            select(EvaluationSuiteRow)
            .where(EvaluationSuiteRow.project_id == str(project_id))
            .order_by(EvaluationSuiteRow.updated_at.desc())
        ).all()
        return [EvaluationSuiteView.model_validate(row) for row in rows]

    def _active_execution(
        self, project_id: UUID, suite_id: str
    ) -> EvaluationExecutionRow | None:
        return self.session.scalars(
            select(EvaluationExecutionRow)
            .where(
                EvaluationExecutionRow.project_id == str(project_id),
                EvaluationExecutionRow.suite_id == suite_id,
                EvaluationExecutionRow.status.in_(
                    [
                        EvaluationExecutionStatus.QUEUED.value,
                        EvaluationExecutionStatus.RUNNING.value,
                        EvaluationExecutionStatus.FINALIZING.value,
                    ]
                ),
            )
            .order_by(EvaluationExecutionRow.created_at.desc())
            .limit(1)
        ).first()

    def create_suite(
        self, project_id: UUID, payload: EvaluationSuiteCreate
    ) -> EvaluationSuiteView:
        self.portfolio.require_project(project_id)
        row = EvaluationSuiteRow(
            project_id=str(project_id),
            name=payload.name,
            agent_kind=payload.agent_kind.value,
            description=payload.description,
        )
        self.session.add(row)
        try:
            self.session.flush()
        except IntegrityError as exc:
            raise DomainError(
                "EVALUATION_SUITE_NAME_CONFLICT",
                "项目内评测集名称不能重复。",
                status_code=409,
            ) from exc
        return EvaluationSuiteView.model_validate(row)

    def update_suite(
        self, project_id: UUID, suite_id: UUID, payload: EvaluationSuiteUpdate
    ) -> EvaluationSuiteView:
        row = self.require_suite(project_id, suite_id)
        require_revision(row.revision, payload.expected_revision, resource="评测集")
        for key, value in json_ready(
            payload.model_dump(exclude_unset=True, exclude={"expected_revision"})
        ).items():
            setattr(row, key, value)
        row.revision += 1
        self.session.flush()
        return EvaluationSuiteView.model_validate(row)

    def list_cases(self, project_id: UUID, suite_id: UUID) -> list[EvaluationCaseView]:
        self.require_suite(project_id, suite_id)
        rows = self.session.scalars(
            select(EvaluationCaseRow)
            .where(EvaluationCaseRow.suite_id == str(suite_id))
            .order_by(EvaluationCaseRow.created_at, EvaluationCaseRow.id)
        ).all()
        return [EvaluationCaseView.model_validate(row) for row in rows]

    def create_case(
        self, project_id: UUID, suite_id: UUID, payload: EvaluationCaseCreate
    ) -> EvaluationCaseView:
        suite = self.require_suite(project_id, suite_id)
        if suite.status == EvaluationSuiteStatus.RETIRED.value:
            raise DomainError(
                "EVALUATION_SUITE_RETIRED",
                "已停用评测集不能新增案例。",
                status_code=409,
            )
        row = EvaluationCaseRow(
            project_id=str(project_id),
            suite_id=suite.id,
            **json_ready(payload.model_dump(mode="json")),
        )
        self.session.add(row)
        try:
            self.session.flush()
        except IntegrityError as exc:
            raise DomainError(
                "EVALUATION_CASE_NAME_CONFLICT",
                "同一评测集内案例名称不能重复。",
                status_code=409,
            ) from exc
        return EvaluationCaseView.model_validate(row)

    def create_run(
        self, project_id: UUID, suite_id: UUID, payload: EvaluationRunCreate
    ) -> EvaluationRunView:
        suite = self.require_suite(project_id, suite_id)
        if suite.status != EvaluationSuiteStatus.ACTIVE.value:
            raise DomainError(
                "EVALUATION_SUITE_NOT_ACTIVE", "评测集必须先启用。", status_code=409
            )
        if payload.model_profile_id is not None:
            profile = self.session.get(ModelProfileRow, str(payload.model_profile_id))
            if profile is None:
                raise DomainError(
                    "MODEL_PROFILE_NOT_FOUND", "模型配置不存在。", status_code=404
                )
        cases = self.session.scalars(
            select(EvaluationCaseRow).where(
                EvaluationCaseRow.suite_id == suite.id,
                EvaluationCaseRow.status == EvaluationCaseStatus.ACTIVE.value,
            )
        ).all()
        if not cases:
            raise DomainError(
                "EVALUATION_SUITE_EMPTY", "评测集没有有效案例。", status_code=409
            )
        predictions = {str(item.case_id): item for item in payload.predictions}
        if len(predictions) != len(payload.predictions):
            raise DomainError(
                "EVALUATION_PREDICTION_DUPLICATE",
                "每个评测案例只能提交一份输出。",
                status_code=422,
            )
        case_ids = {item.id for item in cases}
        if set(predictions) != case_ids:
            raise DomainError(
                "EVALUATION_PREDICTIONS_INCOMPLETE",
                "必须为评测集中的每个有效案例提交且只提交一份输出。",
                status_code=422,
                details=[
                    {
                        "missing_case_ids": sorted(case_ids - set(predictions)),
                        "unknown_case_ids": sorted(set(predictions) - case_ids),
                    }
                ],
            )
        verified_run_ids = [
            item.agent_run_id
            for item in payload.predictions
            if item.agent_run_id is not None
        ]
        if len(verified_run_ids) != len(set(verified_run_ids)):
            raise DomainError(
                "EVALUATION_AGENT_RUN_DUPLICATE",
                "同一个 Agent 运行不能代替多个评测案例。",
                status_code=422,
            )
        resolved_predictions = {
            case.id: self._resolve_prediction(
                project_id, suite.agent_kind, case, predictions[case.id]
            )
            for case in cases
        }
        actual_profile_ids: set[str] = set()
        for agent_run_id in verified_run_ids:
            agent_run = self.session.get(AgentRunRow, str(agent_run_id))
            if agent_run is None:  # Already rejected by _resolve_prediction.
                continue
            profile_id = (agent_run.context_manifest or {}).get("model_profile_id")
            if profile_id is not None:
                actual_profile_ids.add(str(profile_id))
        if payload.model_profile_id is not None and actual_profile_ids != {
            str(payload.model_profile_id)
        }:
            raise DomainError(
                "EVALUATION_MODEL_PROFILE_MISMATCH",
                "声明的模型配置与实际 Agent 运行轨迹不一致。",
                status_code=422,
            )
        inferred_profile_id = (
            next(iter(actual_profile_ids)) if len(actual_profile_ids) == 1 else None
        )
        if inferred_profile_id is not None and self.session.get(
            ModelProfileRow, inferred_profile_id
        ) is None:
            inferred_profile_id = None
        run = EvaluationRunRow(
            project_id=str(project_id),
            suite_id=suite.id,
            model_profile_id=(
                str(payload.model_profile_id)
                if payload.model_profile_id
                else inferred_profile_id
            ),
            label=payload.label,
            total_cases=len(cases),
        )
        self.session.add(run)
        self.session.flush()
        results = [
            self._score(
                run,
                case,
                resolved_predictions[case.id],
            )
            for case in sorted(cases, key=lambda item: (item.created_at, item.id))
        ]
        run.passed_cases = sum(item.passed for item in results)
        run.average_score = statistics.fmean(item.score for item in results)
        self.session.flush()
        return EvaluationRunView.model_validate(run)

    def execute_suite(
        self,
        project_id: UUID,
        suite_id: UUID,
        payload: EvaluationExecuteCreate,
        settings: Settings,
    ) -> EvaluationRunView:
        """Execute an active suite through the real persisted Agent runtime.

        The existing ``create_run`` endpoint intentionally accepts legacy/manual
        predictions for importing historical labels, but this method is the only
        path that claims to measure an Agent.  It creates one isolated thread per
        case, executes it, then delegates scoring to ``create_run`` so citations
        and action keys are read from the stored run rather than caller input.
        """

        if settings.agent_worker_enabled:
            raise DomainError(
                "EVALUATION_REQUIRES_SYNC_RUNTIME",
                "评测执行接口需要同步运行模式；后台 Worker 模式请使用异步评测调度。",
                status_code=409,
            )
        suite = self.require_suite(project_id, suite_id)
        if suite.status != EvaluationSuiteStatus.ACTIVE.value:
            raise DomainError(
                "EVALUATION_SUITE_NOT_ACTIVE", "评测集必须先启用。", status_code=409
            )
        active_execution = self._active_execution(project_id, suite.id)
        if active_execution is not None:
            # The browser disables the button while polling, but the API must
            # also be safe against retries, refresh races, and duplicate clicks.
            raise DomainError(
                "EVALUATION_ALREADY_RUNNING",
                "已有评测正在执行，请等待当前执行完成。",
                status_code=409,
                details=[
                    {
                        "execution_id": active_execution.id,
                        "status": active_execution.status,
                    }
                ],
            )
        cases = self.session.scalars(
            select(EvaluationCaseRow)
            .where(
                EvaluationCaseRow.suite_id == suite.id,
                EvaluationCaseRow.status == EvaluationCaseStatus.ACTIVE.value,
            )
            .order_by(EvaluationCaseRow.created_at, EvaluationCaseRow.id)
        ).all()
        if not cases:
            raise DomainError("EVALUATION_SUITE_EMPTY", "评测集没有有效案例。", status_code=409)

        collaboration = CollaborationService(self.session)
        runtime = AgentRuntimeService(self.session, settings)
        run_ids: list[UUID] = []
        for case in cases:
            thread = collaboration.create_thread(
                project_id,
                AgentThreadCreate(
                    agent_kind=AgentKind(suite.agent_kind),
                    title=f"评测 · {suite.name} · {case.name}",
                ),
            )
            accepted = collaboration.send_message(
                project_id,
                thread.id,
                AgentMessageCreate(
                    content=case.input,
                    model_profile_id=payload.model_profile_id,
                    allow_external_model=payload.allow_external_model,
                    share_project_context_with_model=payload.share_project_context_with_model,
                ),
            )
            processed = runtime.process_run(project_id, accepted.run.id)
            if processed.status != AgentRunStatus.COMPLETED:
                raise DomainError(
                    "EVALUATION_AGENT_RUN_INCOMPLETE",
                    f"评测案例“{case.name}”的 Agent 运行未完成，状态为 {processed.status.value}。",
                    status_code=409,
                    details=[{"case_id": case.id, "run_id": accepted.run.id}],
                )
            run_ids.append(accepted.run.id)

        return self.create_run(
            project_id,
            suite_id,
            EvaluationRunCreate(
                label=payload.label,
                model_profile_id=payload.model_profile_id,
                predictions=[
                    EvaluationPrediction.model_validate(
                        {"case_id": case.id, "agent_run_id": run_id}
                    )
                    for case, run_id in zip(cases, run_ids, strict=True)
                ],
            ),
        )

    def schedule_suite(
        self,
        project_id: UUID,
        suite_id: UUID,
        payload: EvaluationExecuteCreate,
        settings: Settings,
    ) -> EvaluationExecutionView:
        """Queue a durable evaluation execution for either runtime mode.

        In Worker mode the Agent runs are committed as QUEUED and reconciled by
        the polling endpoint after the Worker completes them. In synchronous
        mode this method processes the same persisted runs inline, so the API
        contract remains identical in local tests and production.
        """

        suite = self.require_suite(project_id, suite_id)
        if suite.status != EvaluationSuiteStatus.ACTIVE.value:
            raise DomainError(
                "EVALUATION_SUITE_NOT_ACTIVE", "评测集必须先启用。", status_code=409
            )
        active_execution = self._active_execution(project_id, suite.id)
        if active_execution is not None:
            # The browser disables the button while polling, but the API must
            # also be safe against retries, refresh races, and duplicate clicks.
            return EvaluationExecutionView.model_validate(active_execution)
        cases = self.session.scalars(
            select(EvaluationCaseRow)
            .where(
                EvaluationCaseRow.suite_id == suite.id,
                EvaluationCaseRow.status == EvaluationCaseStatus.ACTIVE.value,
            )
            .order_by(EvaluationCaseRow.created_at, EvaluationCaseRow.id)
        ).all()
        if not cases:
            raise DomainError("EVALUATION_SUITE_EMPTY", "评测集没有有效案例。", status_code=409)
        if payload.model_profile_id is not None and self.session.get(
            ModelProfileRow, str(payload.model_profile_id)
        ) is None:
            raise DomainError("MODEL_PROFILE_NOT_FOUND", "模型配置不存在。", status_code=404)
        execution = EvaluationExecutionRow(
            project_id=str(project_id),
            suite_id=suite.id,
            model_profile_id=(
                str(payload.model_profile_id) if payload.model_profile_id else None
            ),
            label=payload.label,
            status=EvaluationExecutionStatus.QUEUED.value,
            total_cases=len(cases),
            agent_run_ids=[],
        )
        self.session.add(execution)
        try:
            # Keep the check and insert in a savepoint.  The partial unique
            # index is the authoritative race guard across API processes; if
            # another process wins between the lookup and this flush, the
            # losing request can still return the existing execution without
            # poisoning its surrounding transaction.
            with self.session.begin_nested():
                self.session.flush()
        except IntegrityError:
            concurrent_execution = self._active_execution(project_id, suite.id)
            if concurrent_execution is None:
                raise
            return EvaluationExecutionView.model_validate(concurrent_execution)

        collaboration = CollaborationService(self.session)
        mappings: list[dict[str, str]] = []
        for case in cases:
            thread = collaboration.create_thread(
                project_id,
                AgentThreadCreate(
                    agent_kind=AgentKind(suite.agent_kind),
                    title=f"评测 · {suite.name} · {case.name}",
                ),
            )
            accepted = collaboration.send_message(
                project_id,
                thread.id,
                AgentMessageCreate(
                    content=case.input,
                    model_profile_id=payload.model_profile_id,
                    allow_external_model=payload.allow_external_model,
                    share_project_context_with_model=payload.share_project_context_with_model,
                ),
            )
            mappings.append({"case_id": case.id, "agent_run_id": str(accepted.run.id)})
        execution.agent_run_ids = mappings
        execution.status = EvaluationExecutionStatus.RUNNING.value
        self.session.flush()

        if not settings.agent_worker_enabled:
            runtime = AgentRuntimeService(self.session, settings)
            for item in mappings:
                runtime.process_run(project_id, UUID(item["agent_run_id"]))
            self.session.flush()
            return self.reconcile_execution(project_id, UUID(execution.id))
        return EvaluationExecutionView.model_validate(execution)

    def get_execution(
        self, project_id: UUID, execution_id: UUID
    ) -> EvaluationExecutionView:
        return self.reconcile_execution(project_id, execution_id)

    def reconcile_execution(
        self, project_id: UUID, execution_id: UUID
    ) -> EvaluationExecutionView:
        execution = self.session.get(EvaluationExecutionRow, str(execution_id))
        if execution is None or execution.project_id != str(project_id):
            raise DomainError(
                "EVALUATION_EXECUTION_NOT_FOUND", "评测执行不存在。", status_code=404
            )
        if execution.status in {
            EvaluationExecutionStatus.COMPLETED.value,
            EvaluationExecutionStatus.FAILED.value,
        }:
            return EvaluationExecutionView.model_validate(execution)

        mappings = execution.agent_run_ids or []
        run_ids = [str(item["agent_run_id"]) for item in mappings]
        agent_runs = {
            row.id: row
            for row in self.session.scalars(
                select(AgentRunRow).where(AgentRunRow.id.in_(run_ids))
            ).all()
        }
        completed = sum(
            1
            for run_id in run_ids
            if agent_runs.get(run_id) is not None
            and agent_runs[run_id].status == AgentRunStatus.COMPLETED.value
        )
        execution.completed_cases = completed
        failed_run = next(
            (
                agent_runs.get(run_id)
                for run_id in run_ids
                if agent_runs.get(run_id) is not None
                and agent_runs[run_id].status
                in {
                    AgentRunStatus.FAILED.value,
                    AgentRunStatus.CANCELLED.value,
                    AgentRunStatus.BUDGET_EXHAUSTED.value,
                }
            ),
            None,
        )
        if failed_run is not None:
            execution.status = EvaluationExecutionStatus.FAILED.value
            execution.error = {
                "code": "EVALUATION_AGENT_RUN_FAILED",
                "message": "评测中的 Agent 运行失败，未生成评测结果。",
                "run_id": failed_run.id,
                "run_status": failed_run.status,
                "run_error": failed_run.error,
            }
            self.session.flush()
            return EvaluationExecutionView.model_validate(execution)
        if completed < execution.total_cases:
            execution.status = EvaluationExecutionStatus.RUNNING.value
            self.session.flush()
            return EvaluationExecutionView.model_validate(execution)

        execution.status = EvaluationExecutionStatus.FINALIZING.value
        self.session.flush()
        case_rows = {
            row.id: row
            for row in self.session.scalars(
                select(EvaluationCaseRow).where(
                    EvaluationCaseRow.id.in_([str(item["case_id"]) for item in mappings])
                )
            ).all()
        }
        suite = self.require_suite(project_id, UUID(execution.suite_id))
        predictions = [
            EvaluationPrediction(
                case_id=UUID(item["case_id"]),
                agent_run_id=UUID(item["agent_run_id"]),
            )
            for item in mappings
            if item["case_id"] in case_rows
        ]
        if len(predictions) != execution.total_cases:
            execution.status = EvaluationExecutionStatus.FAILED.value
            execution.error = {
                "code": "EVALUATION_CASE_MISSING",
                "message": "评测案例在执行期间不存在，未生成评测结果。",
            }
            self.session.flush()
            return EvaluationExecutionView.model_validate(execution)
        try:
            run = self.create_run(
                project_id,
                UUID(suite.id),
                EvaluationRunCreate(
                    label=execution.label,
                    model_profile_id=(
                        UUID(execution.model_profile_id)
                        if execution.model_profile_id
                        else None
                    ),
                    predictions=predictions,
                ),
            )
        except DomainError as exc:
            execution.status = EvaluationExecutionStatus.FAILED.value
            execution.error = {
                "code": exc.code,
                "message": exc.message,
                "details": exc.details,
            }
            self.session.flush()
            return EvaluationExecutionView.model_validate(execution)
        execution.evaluation_run_id = str(run.id)
        execution.status = EvaluationExecutionStatus.COMPLETED.value
        execution.completed_cases = execution.total_cases
        execution.error = None
        self.session.flush()
        return EvaluationExecutionView.model_validate(execution)

    def list_runs(
        self, project_id: UUID, *, suite_id: UUID | None = None
    ) -> list[EvaluationRunView]:
        self.portfolio.require_project(project_id)
        statement = select(EvaluationRunRow).where(
            EvaluationRunRow.project_id == str(project_id)
        )
        if suite_id is not None:
            self.require_suite(project_id, suite_id)
            statement = statement.where(EvaluationRunRow.suite_id == str(suite_id))
        rows = self.session.scalars(statement.order_by(EvaluationRunRow.created_at.desc())).all()
        return [EvaluationRunView.model_validate(row) for row in rows]

    def list_results(self, project_id: UUID, run_id: UUID) -> list[EvaluationResultView]:
        run = self.session.get(EvaluationRunRow, str(run_id))
        if run is None or run.project_id != str(project_id):
            raise DomainError("EVALUATION_RUN_NOT_FOUND", "评测运行不存在。", status_code=404)
        rows = self.session.scalars(
            select(EvaluationResultRow)
            .where(EvaluationResultRow.run_id == run.id)
            .order_by(EvaluationResultRow.created_at, EvaluationResultRow.id)
        ).all()
        return [EvaluationResultView.model_validate(row) for row in rows]

    def require_suite(self, project_id: UUID, suite_id: UUID) -> EvaluationSuiteRow:
        row = self.session.get(EvaluationSuiteRow, str(suite_id))
        if row is None or row.project_id != str(project_id):
            raise DomainError(
                "EVALUATION_SUITE_NOT_FOUND", "评测集不存在。", status_code=404
            )
        return row

    def _score(
        self,
        run: EvaluationRunRow,
        case: EvaluationCaseRow,
        prediction: EvaluationPrediction,
    ) -> EvaluationResultRow:
        normalized_content = prediction.content.casefold()
        actual_actions = set(prediction.action_keys)
        checks: list[dict[str, object]] = [
            {
                "key": "verified_agent_run",
                "passed": prediction.agent_run_id is not None,
                "expected": "completed persisted Agent run",
                "actual": (
                    str(prediction.agent_run_id)
                    if prediction.agent_run_id is not None
                    else "legacy unverified prediction"
                ),
            },
            {
                "key": "content_non_empty",
                "passed": bool(prediction.content.strip()),
                "expected": "non-empty",
            },
            {
                "key": "expected_actions",
                "passed": set(case.expected_action_keys).issubset(actual_actions),
                "expected": case.expected_action_keys,
                "actual": prediction.action_keys,
            },
            {
                "key": "required_terms",
                "passed": all(
                    item.casefold() in normalized_content for item in case.required_terms
                ),
                "expected": case.required_terms,
            },
            {
                "key": "forbidden_terms",
                "passed": all(
                    item.casefold() not in normalized_content for item in case.forbidden_terms
                ),
                "expected_absent": case.forbidden_terms,
            },
            {
                "key": "minimum_citations",
                "passed": prediction.citation_count >= case.minimum_citations,
                "expected": case.minimum_citations,
                "actual": prediction.citation_count,
            },
        ]
        score = sum(bool(item["passed"]) for item in checks) / len(checks)
        row = EvaluationResultRow(
            run_id=run.id,
            case_id=case.id,
            project_id=run.project_id,
            candidate_content=prediction.content,
            candidate_action_keys=prediction.action_keys,
            candidate_citation_count=prediction.citation_count,
            score=score,
            passed=all(bool(item["passed"]) for item in checks),
            checks=json_ready(checks),
        )
        self.session.add(row)
        return row

    def _resolve_prediction(
        self,
        project_id: UUID,
        agent_kind: str,
        case: EvaluationCaseRow,
        prediction: EvaluationPrediction,
    ) -> EvaluationPrediction:
        """Replace all self-reported score inputs with a persisted Agent trace."""

        if prediction.agent_run_id is None:
            return prediction.model_copy(update={"action_keys": [], "citation_count": 0})
        agent_run = self.session.get(AgentRunRow, str(prediction.agent_run_id))
        if agent_run is None or agent_run.project_id != str(project_id):
            raise DomainError(
                "EVALUATION_AGENT_RUN_NOT_FOUND",
                "评测引用的 Agent 运行不存在或不属于当前项目。",
                status_code=404,
            )
        if agent_run.agent_kind != agent_kind:
            raise DomainError(
                "EVALUATION_AGENT_KIND_MISMATCH",
                "评测案例与 Agent 运行类型不一致。",
                status_code=422,
            )
        if agent_run.status != AgentRunStatus.COMPLETED.value:
            raise DomainError(
                "EVALUATION_AGENT_RUN_INCOMPLETE",
                "只有已完成的 Agent 运行可以进入评测。",
                status_code=409,
            )
        if not agent_run.result_message_id:
            raise DomainError(
                "EVALUATION_AGENT_RESULT_MISSING",
                "Agent 运行没有持久化结果消息。",
                status_code=409,
            )
        result_message = self.session.get(AgentMessageRow, agent_run.result_message_id)
        if (
            result_message is None
            or result_message.thread_id != agent_run.thread_id
            or result_message.role != AgentMessageRole.ASSISTANT.value
        ):
            raise DomainError(
                "EVALUATION_AGENT_RESULT_MISSING",
                "Agent 运行的结果消息不存在或不属于该运行对话。",
                status_code=409,
            )
        input_message = self.session.scalar(
            select(AgentMessageRow)
            .where(
                AgentMessageRow.thread_id == agent_run.thread_id,
                AgentMessageRow.role == AgentMessageRole.USER.value,
                AgentMessageRow.created_at <= agent_run.created_at,
            )
            .order_by(AgentMessageRow.created_at.desc(), AgentMessageRow.id.desc())
            .limit(1)
        )
        if input_message is None or input_message.content.strip() != case.input.strip():
            raise DomainError(
                "EVALUATION_AGENT_INPUT_MISMATCH",
                "Agent 运行的输入与评测案例不一致，不能复用其他回答代替。",
                status_code=422,
            )
        action_rows = self.session.execute(
            select(ActionDefinitionRow.key, ActionInvocationRow.status)
            .join(
                ActionInvocationRow,
                ActionInvocationRow.action_definition_id == ActionDefinitionRow.id,
            )
            .where(
                ActionInvocationRow.source_agent_run_id == agent_run.id,
                ActionInvocationRow.project_id == str(project_id),
                ActionDefinitionRow.project_id == str(project_id),
            )
            .order_by(ActionDefinitionRow.key, ActionInvocationRow.id)
        ).all()
        # Only execution success or an explicitly effective result satisfies an
        # expected action.  INEFFECTIVE, OBSERVING, and all pre-execution,
        # failed, cancelled, and rollback states must not masquerade as success.
        successful_statuses = {
            ActionInvocationStatus.SUCCEEDED.value,
            ActionInvocationStatus.EFFECTIVE.value,
        }
        action_keys = sorted(
            {
                str(action_key)
                for action_key, action_status in action_rows
                if action_status in successful_statuses
            }
        )
        valid_citations = 0
        for raw_reference in result_message.citations:
            try:
                reference = EvidenceReference.model_validate(raw_reference)
                validate_evidence_references(self.session, project_id, [reference])
            except (DomainError, ValueError):
                continue
            valid_citations += 1
        return EvaluationPrediction(
            case_id=UUID(case.id),
            agent_run_id=UUID(agent_run.id),
            content=result_message.content,
            action_keys=action_keys,
            citation_count=valid_citations,
        )
