from __future__ import annotations

import json
import re
import time
from collections.abc import Callable
from typing import Any
from uuid import NAMESPACE_URL, UUID, uuid5

import httpx
from pydantic import ValidationError
from sqlalchemy import func, select
from sqlalchemy.orm import Session

from enterprise_insight_backend.actions import ActionService
from enterprise_insight_backend.config import Settings
from enterprise_insight_backend.errors import DomainError
from enterprise_insight_backend.model_profiles import LocalSecretVault
from enterprise_insight_backend.models import (
    ActionDefinitionRow,
    ActionInvocationRow,
    ActionObservationRow,
    AgentMessageRow,
    AgentRunRow,
    AgentStepRow,
    AgentThreadRow,
    ClaimRow,
    CompanyRow,
    DesignTradeoffRow,
    EvidenceFragmentRow,
    HypothesisRow,
    InformationRequestRow,
    LearningCaseRow,
    ManagementInsightRow,
    ManagementSignalRow,
    MaterializationRunRow,
    MeetingRecordRow,
    MetricDefinitionRow,
    MetricObservationRow,
    ModelProfileRow,
    ObservationAssertionRow,
    ObservationConflictRow,
    OntologyTypeRow,
    PublicationRow,
    RawBatchRow,
    ScenarioRow,
    SemanticDatasetRow,
    SemanticMappingRow,
    SourceAssetRow,
    SourceDocumentRow,
    SourceIdentityRow,
    SourceSystemRow,
)
from enterprise_insight_backend.portfolio import PortfolioService
from enterprise_insight_backend.projection import ProjectionService
from enterprise_insight_backend.query_snapshots import QuerySnapshotService
from enterprise_insight_backend.schemas import (
    ActionInvocationCreate,
    ActionInvocationStatus,
    AgentActionProposal,
    AgentKind,
    AgentMessageRole,
    AgentRunStatus,
    AgentRunView,
    AgentStructuredOutput,
    EvidenceReference,
    GraphQuery,
    ModelProvider,
)
from enterprise_insight_backend.service_utils import json_ready, now_utc
from enterprise_insight_backend.tool_registry import get_tool_spec, tool_keys_for_agent

SYSTEM_PROMPTS: dict[str, str] = {
    AgentKind.PROJECTION.value: (
        "你是企业投影Agent。只依据给定材料和本体提出确定性建模建议；无法确认时列出未知项。"
        "不得把推测写成事实。可提议create_entity、create_relation、apply_projection_changes，"
        "正式投影写入必须经过系统预演和人工审批。"
    ),
    AgentKind.MANAGEMENT.value: (
        "你是服务核心管理层的管理决策Agent。可从第一性原理探索组织、职责、权限、流程、"
        "代理问题、知识依赖、利益关系和风险耦合，但必须区分事实、假设、反例和未知项。"
        "先检查设计侧（战略、职责、权限、工作、绩效）与结果侧（会议、绩效结果、企业结果）"
        "是否相互印证。可提议save_hypothesis、save_causal_hypothesis、create_scenario、"
        "save_information_request、"
        "record_design_tradeoff、record_meeting_observation和run_management_analysis。"
        "已有行动结果时可用draft_learning_case沉淀可追溯案例草稿；尚无现实结果的方案"
        "只能用draft_scenario_learning_case保存为待验证参考，不得冒充已验证经验。"
        "任何因果判断只能作为待验证假设，不得直接修改可信企业投影。"
    ),
    AgentKind.SYSTEM_ONTOLOGY.value: (
        "你是系统语义对齐Agent。只依据源字段、本体类型、映射和血缘进行确定性分析。"
        "不得猜测字段含义或身份；不确定时生成待确认项。可使用映射创建/推进、身份绑定、"
        "冲突裁决、批次物化和类型化语义数据集工具。写入必须经过确定性预演，"
        "要求审批的工具不得绕过人工批准。"
    ),
}


class AgentRuntimeService:
    def __init__(self, session: Session, settings: Settings) -> None:
        self.session = session
        self.settings = settings
        self.portfolio = PortfolioService(session)
        self.projection = ProjectionService(session)

    def process_run(self, project_id: UUID, run_id: UUID) -> AgentRunView:
        run = self._require_run(project_id, run_id)
        if run.status == AgentRunStatus.COMPLETED.value:
            return AgentRunView.model_validate(run)
        if run.status == AgentRunStatus.CANCELLED.value:
            raise DomainError("AGENT_RUN_CANCELLED", "已取消的Agent运行不能执行。", status_code=409)
        try:
            run.error = None
            run.status = AgentRunStatus.RETRIEVING.value
            self.session.flush()
            thread = self._require_thread(project_id, run.thread_id)
            user_message = self._latest_user_message(thread.id, run.created_at)
            profile = self._resolve_profile(run)
            reference_ids = [
                UUID(item) for item in (run.context_manifest or {}).get("reference_case_ids", [])
            ]
            ActionService(self.session).ensure_defaults(project_id)
            manifest = dict(run.context_manifest or {})
            execution = dict(manifest.get("execution") or {})
            execution.setdefault("model_rounds", 0)
            execution.setdefault("tool_calls", 0)
            execution.setdefault("tool_results", [])
            execution.setdefault("pending_action_ids", [])
            accumulated_ids = list(manifest.get("action_invocation_ids") or [])

            if execution["pending_action_ids"]:
                waiting = self._resume_pending_actions(project_id, run, execution)
                manifest["execution"] = execution
                manifest["action_invocation_ids"] = accumulated_ids
                run.context_manifest = json_ready(manifest)
                if waiting:
                    run.status = AgentRunStatus.WAITING_REVIEW.value
                    self.session.flush()
                    return AgentRunView.model_validate(run)

            while execution["model_rounds"] < self.settings.agent_max_model_rounds:
                if execution["tool_calls"] >= self.settings.agent_max_tool_calls:
                    return self._finish_budget_exhausted(run, thread, manifest, execution)
                context = self._build_context(
                    project_id,
                    thread.id,
                    reference_ids,
                    query=user_message.content,
                    context_manifest=manifest,
                )
                context["agent_execution"] = {
                    "model_rounds": execution["model_rounds"],
                    "tool_calls": execution["tool_calls"],
                    "tool_results": execution["tool_results"],
                }
                # Persist the exact coverage ledger before leaving the local
                # transaction.  This keeps the UI honest if an external model
                # call fails or the process is restarted between rounds.
                manifest["context_counts"] = context["counts"]
                manifest["material_coverage"] = context.get("material_coverage", [])
                run.context_manifest = json_ready(manifest)
                run.status = AgentRunStatus.PLANNING.value
                self.session.flush()
                # Release the write transaction while the external model is running.
                self.session.commit()
                output = self._invoke_model(profile, run, user_message.content, context)
                self.session.refresh(run)
                if run.status == AgentRunStatus.CANCELLED.value:
                    return AgentRunView.model_validate(run)
                execution["model_rounds"] += 1
                self._record_step(
                    project_id,
                    run,
                    kind="MODEL",
                    status="SUCCEEDED",
                    input_payload={
                        "round": execution["model_rounds"],
                        "user_content": user_message.content,
                        "model_baseline": context["project"]["model_baseline"],
                        "context_counts": context["counts"],
                    },
                    output_payload=output.model_dump(mode="json"),
                )
                run.status = AgentRunStatus.PRODUCING_PROPOSAL.value
                self.session.flush()
                invocation_ids, action_notes = self._save_action_proposals(
                    project_id,
                    run,
                    output.action_proposals,
                    round_number=execution["model_rounds"],
                )
                accumulated_ids.extend(
                    item for item in invocation_ids if item not in accumulated_ids
                )
                if not invocation_ids:
                    manifest.update(
                        {
                            "model_profile_id": profile.id,
                            "model": profile.model,
                            "context_counts": context["counts"],
                            "material_coverage": context.get("material_coverage", []),
                            "action_invocation_ids": accumulated_ids,
                            "execution": execution,
                        }
                    )
                    return self._finish_completed(run, thread, output, manifest)

                run.status = AgentRunStatus.RUNNING_TOOLS.value
                waiting_ids: list[str] = []
                for invocation_id in invocation_ids:
                    if execution["tool_calls"] >= self.settings.agent_max_tool_calls:
                        break
                    execution["tool_calls"] += 1
                    result = self._advance_action(project_id, run, invocation_id)
                    execution["tool_results"].append(result)
                    if result["status"] == ActionInvocationStatus.WAITING_APPROVAL.value:
                        waiting_ids.append(invocation_id)
                execution["pending_action_ids"] = waiting_ids
                manifest.update(
                    {
                        "model_profile_id": profile.id,
                        "model": profile.model,
                        "context_counts": context["counts"],
                        "material_coverage": context.get("material_coverage", []),
                        "action_invocation_ids": accumulated_ids,
                        "execution": execution,
                    }
                )
                run.context_manifest = json_ready(manifest)
                if waiting_ids:
                    content = output.content
                    if action_notes:
                        content += "\n\n动作状态：\n" + "\n".join(
                            f"- {item}" for item in action_notes
                        )
                    content += "\n\n正式写入已完成预演，等待人工审批；审批后可继续本次任务。"
                    self._append_assistant_message(run, thread, content, output)
                    run.status = AgentRunStatus.WAITING_REVIEW.value
                    self.session.flush()
                    return AgentRunView.model_validate(run)

            return self._finish_budget_exhausted(run, thread, manifest, execution)
        except DomainError as exc:
            if exc.code == "AGENT_CONTEXT_BUDGET_EXCEEDED":
                run.error = {"code": exc.code, "message": exc.message, "details": exc.details}
                manifest["execution"] = execution
                run.context_manifest = json_ready(manifest)
                self._append_assistant_message(
                    run, thread, exc.message, AgentStructuredOutput(content=exc.message)
                )
                run.status = AgentRunStatus.BUDGET_EXHAUSTED.value
                self.session.flush()
                return AgentRunView.model_validate(run)
            self._fail(run, exc.code, exc.message, exc.details)
        except Exception as exc:  # pragma: no cover
            self._fail(
                run,
                "AGENT_RUNTIME_FAILED",
                "Agent运行失败，请查看错误详情。",
                [{"exception": type(exc).__name__}],
            )
        return AgentRunView.model_validate(run)

    def prepare_resume(self, project_id: UUID, run_id: UUID) -> AgentRunView:
        run = self._require_run(project_id, run_id)
        if run.status not in {
            AgentRunStatus.WAITING_REVIEW.value,
            AgentRunStatus.BUDGET_EXHAUSTED.value,
            AgentRunStatus.FAILED.value,
        }:
            raise DomainError(
                "AGENT_RUN_NOT_RESUMABLE",
                "只有待审批、预算暂停或失败的Agent任务可以继续。",
                status_code=409,
            )
        if run.status == AgentRunStatus.BUDGET_EXHAUSTED.value:
            manifest = dict(run.context_manifest or {})
            execution = dict(manifest.get("execution") or {})
            execution["model_rounds"] = 0
            execution["tool_calls"] = 0
            manifest["execution"] = execution
            run.context_manifest = json_ready(manifest)
        run.status = AgentRunStatus.QUEUED.value
        run.error = None
        run.worker_id = None
        run.lease_expires_at = None
        self.session.flush()
        return AgentRunView.model_validate(run)

    def _resume_pending_actions(
        self,
        project_id: UUID,
        run: AgentRunRow,
        execution: dict[str, Any],
    ) -> bool:
        waiting_ids: list[str] = []
        recorded_ids = {
            item.get("invocation_id")
            for item in execution["tool_results"]
            if isinstance(item, dict)
        }
        for invocation_id in execution["pending_action_ids"]:
            row = self.session.get(ActionInvocationRow, invocation_id)
            if row is None or row.project_id != str(project_id):
                raise DomainError(
                    "ACTION_INVOCATION_NOT_FOUND",
                    "待继续的动作不存在。",
                    status_code=404,
                )
            if row.status == ActionInvocationStatus.WAITING_APPROVAL.value:
                waiting_ids.append(invocation_id)
                continue
            if invocation_id in recorded_ids and row.status in {
                ActionInvocationStatus.SUCCEEDED.value,
                ActionInvocationStatus.FAILED.value,
                ActionInvocationStatus.CANCELLED.value,
            }:
                continue
            result = self._advance_action(project_id, run, invocation_id)
            execution["tool_calls"] += 1
            execution["tool_results"].append(result)
        execution["pending_action_ids"] = waiting_ids
        return bool(waiting_ids)

    def _advance_action(
        self, project_id: UUID, run: AgentRunRow, invocation_id: str
    ) -> dict[str, Any]:
        service = ActionService(self.session)
        row = service.require_invocation(project_id, invocation_id)
        try:
            if row.status in {
                ActionInvocationStatus.DRAFT.value,
                ActionInvocationStatus.FAILED.value,
            }:
                service.dry_run(project_id, UUID(invocation_id))
                row = service.require_invocation(project_id, invocation_id)
            if row.status in {
                ActionInvocationStatus.DRY_RUN_COMPLETED.value,
                ActionInvocationStatus.APPROVED.value,
            }:
                service.execute(project_id, UUID(invocation_id))
                row = service.require_invocation(project_id, invocation_id)
            output = {
                "invocation_id": invocation_id,
                "status": row.status,
                "result": row.result,
                "error": row.error,
            }
            self._record_step(
                project_id,
                run,
                kind="TOOL",
                tool_key=self._action_key(row),
                status=row.status,
                input_payload={
                    "invocation_id": invocation_id,
                    "input": row.input,
                    "target_entity_ids": row.target_entity_ids,
                },
                output_payload=output,
                error=row.error,
            )
            return output
        except (DomainError, ValidationError, ValueError) as exc:
            error = {
                "code": exc.code if isinstance(exc, DomainError) else type(exc).__name__,
                "message": exc.message if isinstance(exc, DomainError) else str(exc),
            }
            output = {
                "invocation_id": invocation_id,
                "status": "FAILED",
                "result": None,
                "error": error,
            }
            self._record_step(
                project_id,
                run,
                kind="TOOL",
                tool_key=self._action_key(row),
                status="FAILED",
                input_payload={"invocation_id": invocation_id, "input": row.input},
                output_payload=output,
                error=error,
            )
            return output

    def _record_step(
        self,
        project_id: UUID,
        run: AgentRunRow,
        *,
        kind: str,
        status: str,
        input_payload: dict[str, Any],
        output_payload: dict[str, Any],
        tool_key: str | None = None,
        error: dict[str, Any] | None = None,
    ) -> AgentStepRow:
        position = (
            int(
                self.session.scalar(
                    select(func.coalesce(func.max(AgentStepRow.position), 0)).where(
                        AgentStepRow.run_id == run.id
                    )
                )
                or 0
            )
            + 1
        )
        timestamp = now_utc()
        row = AgentStepRow(
            project_id=str(project_id),
            run_id=run.id,
            position=position,
            kind=kind,
            tool_key=tool_key,
            status=status,
            input_payload=json_ready(input_payload),
            output_payload=json_ready(output_payload),
            error=json_ready(error) if error else None,
            started_at=timestamp,
            finished_at=timestamp,
        )
        self.session.add(row)
        self.session.flush()
        return row

    def _finish_completed(
        self,
        run: AgentRunRow,
        thread: AgentThreadRow,
        output: AgentStructuredOutput,
        manifest: dict[str, Any],
    ) -> AgentRunView:
        self._append_assistant_message(run, thread, output.content, output)
        run.context_manifest = json_ready(manifest)
        run.status = AgentRunStatus.COMPLETED.value
        self.session.flush()
        return AgentRunView.model_validate(run)

    def _finish_budget_exhausted(
        self,
        run: AgentRunRow,
        thread: AgentThreadRow,
        manifest: dict[str, Any],
        execution: dict[str, Any],
    ) -> AgentRunView:
        manifest["execution"] = execution
        run.context_manifest = json_ready(manifest)
        self._append_assistant_message(
            run,
            thread,
            "本次任务已达到可配置执行预算，检查点已经保存；可点击继续恢复。",
            AgentStructuredOutput(content="本次任务已达到执行预算。"),
        )
        run.status = AgentRunStatus.BUDGET_EXHAUSTED.value
        self.session.flush()
        return AgentRunView.model_validate(run)

    def _append_assistant_message(
        self,
        run: AgentRunRow,
        thread: AgentThreadRow,
        content: str,
        output: AgentStructuredOutput,
    ) -> AgentMessageRow:
        row = AgentMessageRow(
            thread_id=thread.id,
            role=AgentMessageRole.ASSISTANT.value,
            content=content,
            citations=json_ready([item.model_dump(mode="json") for item in output.citations]),
        )
        self.session.add(row)
        self.session.flush()
        run.result_message_id = row.id
        return row

    def _action_key(self, row: ActionInvocationRow) -> str:
        definition = self.session.get(ActionDefinitionRow, row.action_definition_id)
        return definition.key if definition is not None else "unknown"

    @staticmethod
    def _action_input_schema(action_key: str) -> dict[str, Any]:
        spec = get_tool_spec(action_key)
        return spec.json_schema() if spec is not None else {}

    def _resolve_profile(self, run: AgentRunRow) -> ModelProfileRow:
        profile_id = (run.context_manifest or {}).get("model_profile_id")
        if profile_id:
            profile = self.session.get(ModelProfileRow, str(profile_id))
            if profile is None or not profile.enabled:
                raise DomainError(
                    "MODEL_PROFILE_NOT_AVAILABLE",
                    "指定的模型配置不存在或已禁用。",
                    status_code=409,
                )
            return profile
        profile = self.session.scalar(
            select(ModelProfileRow)
            .where(ModelProfileRow.enabled.is_(True))
            .order_by(ModelProfileRow.is_default.desc(), ModelProfileRow.created_at)
            .limit(1)
        )
        if profile is None:
            raise DomainError(
                "MODEL_PROFILE_REQUIRED",
                "请先在模型配置中启用一个模型。",
                status_code=409,
            )
        return profile

    def _invoke_model(
        self,
        profile: ModelProfileRow,
        run: AgentRunRow,
        user_content: str,
        context: dict[str, Any],
    ) -> AgentStructuredOutput:
        if profile.provider == ModelProvider.MOCK.value:
            return self._mock_response(run.agent_kind, user_content, context)
        manifest = run.context_manifest or {}
        if not manifest.get("allow_external_model", False):
            raise DomainError(
                "EXTERNAL_MODEL_CONFIRMATION_REQUIRED",
                "本次对话尚未允许发送到外部模型。",
                status_code=409,
            )
        if not profile.encrypted_api_key:
            raise DomainError("MODEL_API_KEY_REQUIRED", "模型配置没有API密钥。", status_code=422)
        key = LocalSecretVault(self.settings).decrypt(profile.encrypted_api_key)
        model_context: dict[str, Any] = {
            "context_shared": False,
            "available_actions": context.get("available_actions", []),
            "agent_execution": context.get("agent_execution", {}),
        }
        if manifest.get("share_project_context_with_model", False):
            model_context = context
        context_json = json.dumps(
            model_context, ensure_ascii=False, default=str, separators=(",", ":")
        )
        if len(context_json) > self.settings.agent_context_max_chars:
            raise DomainError(
                "AGENT_CONTEXT_BUDGET_EXCEEDED",
                "完整上下文超过本地字符预算，尚未发送到模型。执行进度已保存；"
                "请先提高 agent_context_max_chars 配置再继续，或新建范围更小的任务。",
                status_code=409,
                details=[{
                    "required_chars": len(context_json),
                    "budget_chars": self.settings.agent_context_max_chars,
                }],
            )
        system_prompt = (
            SYSTEM_PROMPTS.get(run.agent_kind, SYSTEM_PROMPTS[AgentKind.MANAGEMENT.value])
            + "\n必须输出JSON对象，字段为content、citations、action_proposals。"
            + "动作项字段只能是action_key、input、target_entity_ids、reason。"
            + "每轮只提出依赖当前真实ID即可执行的下一批动作；收到工具结果后先核对，"
            + "仍需工作则继续提出动作，全部完成时action_proposals必须为空。"
            + "material_coverage 表示材料读取覆盖；included_fragments 小于 "
            + "available_fragments 或 text_truncated 为真时，只能说明已读部分，"
            + "不得声称已完整阅读材料或把未见内容解释为现实中不存在。"
            + "graph_coverage.truncated 为真时，图中只包含初始样本；必须按需调用"
            + "read_graph_neighborhood 获取相关对象及关系后再作局部判断。"
        )
        messages = [
            {"role": "system", "content": system_prompt},
            {"role": "system", "content": f"授权范围内上下文：{context_json}"},
            {"role": "user", "content": user_content},
        ]
        invalid_output: DomainError | None = None
        for output_attempt in range(2):
            raw: str | None = None
            for attempt in range(self.settings.agent_model_max_retries + 1):
                request_payload = {
                    "model": profile.model,
                    "messages": messages,
                    "temperature": profile.temperature,
                }
                try:
                    response = httpx.post(
                        f"{profile.base_url.rstrip('/')}/chat/completions",
                        headers={"Authorization": f"Bearer {key}"},
                        json=request_payload,
                        timeout=profile.timeout_seconds,
                    )
                    response.raise_for_status()
                    payload = response.json()
                    raw = str(payload["choices"][0]["message"]["content"])
                    break
                except (httpx.HTTPError, KeyError, IndexError, TypeError, ValueError) as exc:
                    if (
                        attempt >= self.settings.agent_model_max_retries
                        or not self._is_retryable_model_error(exc)
                    ):
                        raise DomainError(
                            "MODEL_REQUEST_FAILED",
                            "模型请求失败，请检查模型地址、名称和密钥。",
                            status_code=502,
                            details=[{"exception": type(exc).__name__}],
                        ) from exc
                    delay = self.settings.agent_model_retry_base_seconds * (2**attempt)
                    if delay > 0:
                        time.sleep(delay)
            if raw is None:  # pragma: no cover - the loop either returns or raises
                raise DomainError("MODEL_REQUEST_FAILED", "模型请求没有返回内容。", status_code=502)
            try:
                return self._parse_model_output(raw)
            except DomainError as exc:
                invalid_output = exc
                if output_attempt == 0:
                    messages.extend(
                        [
                            {"role": "assistant", "content": raw[-8000:]},
                            {
                                "role": "user",
                                "content": (
                                    "上一个输出不符合JSON契约。请只返回合法JSON对象，"
                                    "不要使用Markdown代码块，也不要增加额外字段。"
                                ),
                            },
                        ]
                    )
        raise invalid_output or DomainError(
            "MODEL_OUTPUT_INVALID", "模型没有返回合法的结构化结果。", status_code=502
        )

    @staticmethod
    def _is_retryable_model_error(error: Exception) -> bool:
        """Return whether retrying the model request can plausibly help.

        Authentication, permission, and endpoint errors must fail immediately. A
        provider 5xx/429 or a transport/timeout error may recover. Malformed
        response shapes are also retried once because providers can transiently
        return an incomplete gateway response, but the final error remains
        explicit and never becomes a successful Agent result.
        """
        if isinstance(error, httpx.HTTPStatusError):
            status = error.response.status_code
            return status in {408, 425, 429, 500, 502, 503, 504}
        if isinstance(error, httpx.HTTPError):
            return True
        return isinstance(error, (KeyError, IndexError, TypeError, ValueError))

    def _build_context(
        self,
        project_id: UUID,
        thread_id: str,
        reference_case_ids: list[UUID],
        *,
        query: str | None = None,
        context_manifest: dict[str, Any] | None = None,
    ) -> dict[str, Any]:
        project = self.portfolio.require_project(project_id)
        agent_kind = self._require_thread(project_id, thread_id).agent_kind
        company = self.session.get(CompanyRow, project.company_id)
        manifest = context_manifest or {}
        publication = None
        snapshot_row = None
        snapshot_id = manifest.get("query_snapshot_id")
        if snapshot_id:
            snapshot_service = QuerySnapshotService(self.session)
            snapshot_row = snapshot_service.require(project_id, snapshot_id)
            graph = snapshot_service.graph(project_id, UUID(snapshot_id))
            if snapshot_row.publication_id:
                publication = self.session.get(PublicationRow, snapshot_row.publication_id)
        elif agent_kind == AgentKind.MANAGEMENT.value:
            publication = self.session.scalar(
                select(PublicationRow)
                .where(PublicationRow.project_id == str(project_id))
                .order_by(PublicationRow.version.desc())
                .limit(1)
            )
        if snapshot_row is None:
            graph = self.projection.graph(
                project_id,
                GraphQuery(release_id=UUID(publication.id) if publication is not None else None),
            )
        graph_entity_total = len(graph.entities)
        graph_relation_total = len(graph.relations)
        ranked_entities = self._rank_context_items(
            graph.entities,
            query,
            lambda item: self._search_text(item.model_dump(mode="json")),
        )
        ranked_relations = self._rank_context_items(
            graph.relations,
            query,
            lambda item: self._search_text(item.model_dump(mode="json")),
        )
        context_entities, context_relations = self._select_graph_context(
            ranked_entities,
            ranked_relations,
            entity_limit=self.settings.agent_initial_graph_entities,
            relation_limit=self.settings.agent_initial_graph_relations,
        )
        ontology = self.session.scalars(
            select(OntologyTypeRow).where(OntologyTypeRow.project_id == str(project_id))
        ).all()
        attachment_ids = [str(item) for item in manifest.get("attachment_ids", [])]
        selected_documents: list[SourceDocumentRow] = []
        if attachment_ids:
            selected_documents = list(
                self.session.scalars(
                    select(SourceDocumentRow).where(
                        SourceDocumentRow.project_id == str(project_id),
                        SourceDocumentRow.id.in_(attachment_ids),
                    )
                ).all()
            )
            selected_by_id = {item.id: item for item in selected_documents}
            selected_documents = [
                selected_by_id[item] for item in attachment_ids if item in selected_by_id
            ]
        document_statement = select(SourceDocumentRow).where(
            SourceDocumentRow.project_id == str(project_id)
        )
        if attachment_ids:
            document_statement = document_statement.where(
                SourceDocumentRow.id.not_in(attachment_ids)
            )
        documents = list(
            self.session.scalars(
                document_statement.order_by(SourceDocumentRow.created_at.desc()).limit(30)
            ).all()
        )
        documents = [*selected_documents, *documents[: max(0, 30 - len(selected_documents))]]
        selected_fragments: list[EvidenceFragmentRow] = []
        if attachment_ids:
            ranked = (
                select(
                    EvidenceFragmentRow.id.label("fragment_id"),
                    func.row_number().over(
                        partition_by=EvidenceFragmentRow.source_document_id,
                        order_by=(EvidenceFragmentRow.created_at, EvidenceFragmentRow.id),
                    ).label("document_rank"),
                )
                .where(EvidenceFragmentRow.source_document_id.in_(
                    [item.id for item in selected_documents]
                ))
                .subquery()
            )
            selected_fragments = list(
                self.session.scalars(
                    select(EvidenceFragmentRow)
                    .join(ranked, ranked.c.fragment_id == EvidenceFragmentRow.id)
                    .order_by(
                        ranked.c.document_rank,
                        EvidenceFragmentRow.source_document_id,
                        EvidenceFragmentRow.id,
                    )
                    .limit(40)
                ).all()
            )
        remaining_fragment_limit = max(0, 40 - len(selected_fragments))
        fragment_statement = (
            select(EvidenceFragmentRow)
            .join(SourceDocumentRow, SourceDocumentRow.id == EvidenceFragmentRow.source_document_id)
            .where(SourceDocumentRow.project_id == str(project_id))
        )
        if attachment_ids:
            fragment_statement = fragment_statement.where(
                EvidenceFragmentRow.source_document_id.not_in(attachment_ids)
            )
        fragments = list(
            self.session.scalars(
                fragment_statement.order_by(
                    EvidenceFragmentRow.created_at.desc(), EvidenceFragmentRow.id.desc()
                ).limit(remaining_fragment_limit)
            ).all()
        )
        fragments = [*selected_fragments, *fragments]
        if query:
            fragments = self._rank_context_items(
                fragments,
                query,
                lambda item: f"{item.locator} {item.text}",
                preserve_prefix_count=len(selected_fragments),
            )
        fragment_totals: dict[str, int] = {
            source_document_id: count
            for source_document_id, count in self.session.execute(
                select(
                    EvidenceFragmentRow.source_document_id,
                    func.count(EvidenceFragmentRow.id),
                )
                .where(
                    EvidenceFragmentRow.source_document_id.in_([item.id for item in documents])
                )
                .group_by(EvidenceFragmentRow.source_document_id)
            ).all()
        }
        material_coverage = [{
            "source_document_id": document.id,
            "selected": document.id in attachment_ids,
            "available_fragments": fragment_totals.get(document.id, 0),
            "included_fragments": sum(
                item.source_document_id == document.id for item in fragments
            ),
        } for document in documents]
        claim_statement = select(ClaimRow).where(ClaimRow.project_id == str(project_id))
        if not manifest.get("include_unconfirmed_material", True):
            claim_statement = claim_statement.where(ClaimRow.status != "CANDIDATE")
        claims = self.session.scalars(
            claim_statement.order_by(ClaimRow.created_at.desc()).limit(100)
        ).all()
        hypotheses = self.session.scalars(
            select(HypothesisRow).where(HypothesisRow.project_id == str(project_id)).limit(50)
        ).all()
        scenarios = self.session.scalars(
            select(ScenarioRow).where(ScenarioRow.project_id == str(project_id)).limit(30)
        ).all()
        sources = self.session.scalars(
            select(SourceSystemRow).where(SourceSystemRow.project_id == str(project_id))
        ).all()
        mappings = self.session.scalars(
            select(SemanticMappingRow).where(SemanticMappingRow.project_id == str(project_id))
        ).all()
        source_assets = self.session.scalars(
            select(SourceAssetRow)
            .where(SourceAssetRow.project_id == str(project_id))
            .order_by(SourceAssetRow.updated_at.desc())
            .limit(100)
        ).all()
        raw_batches = self.session.scalars(
            select(RawBatchRow)
            .where(RawBatchRow.project_id == str(project_id))
            .order_by(RawBatchRow.created_at.desc())
            .limit(100)
        ).all()
        materialization_runs = self.session.scalars(
            select(MaterializationRunRow)
            .where(MaterializationRunRow.project_id == str(project_id))
            .order_by(MaterializationRunRow.created_at.desc())
            .limit(100)
        ).all()
        semantic_datasets = self.session.scalars(
            select(SemanticDatasetRow)
            .where(SemanticDatasetRow.project_id == str(project_id))
            .order_by(SemanticDatasetRow.updated_at.desc())
            .limit(100)
        ).all()
        source_identities = self.session.scalars(
            select(SourceIdentityRow)
            .where(SourceIdentityRow.project_id == str(project_id))
            .order_by(SourceIdentityRow.updated_at.desc())
            .limit(100)
        ).all()
        assertion_statement = select(ObservationAssertionRow).where(
            ObservationAssertionRow.project_id == str(project_id)
        )
        if snapshot_row is not None:
            assertion_statement = assertion_statement.where(
                ObservationAssertionRow.id.in_(
                    snapshot_row.manifest.get("observation_assertion_ids", [])
                )
            )
        else:
            assertion_statement = assertion_statement.where(
                ObservationAssertionRow.status == "ACTIVE"
            )
        observation_assertions = self.session.scalars(
            assertion_statement.order_by(
                ObservationAssertionRow.observed_at.desc(),
                ObservationAssertionRow.authority_priority.desc(),
            ).limit(200)
        ).all()
        conflict_statement = select(ObservationConflictRow).where(
            ObservationConflictRow.project_id == str(project_id)
        )
        if snapshot_row is not None:
            conflict_statement = conflict_statement.where(
                ObservationConflictRow.id.in_(
                    snapshot_row.manifest.get("observation_conflict_ids", [])
                )
            )
        observation_conflicts = self.session.scalars(
            conflict_statement.order_by(ObservationConflictRow.updated_at.desc()).limit(100)
        ).all()
        action_definitions = self.session.scalars(
            select(ActionDefinitionRow).where(
                ActionDefinitionRow.project_id == str(project_id),
                ActionDefinitionRow.enabled.is_(True),
            )
        ).all()
        action_history: list[tuple[ActionInvocationRow, ActionDefinitionRow]] = []
        action_observations: list[ActionObservationRow] = []
        if agent_kind == AgentKind.MANAGEMENT.value:
            action_history = [
                (item[0], item[1])
                for item in self.session.execute(
                    select(ActionInvocationRow, ActionDefinitionRow)
                    .join(
                        ActionDefinitionRow,
                        ActionDefinitionRow.id == ActionInvocationRow.action_definition_id,
                    )
                    .where(ActionInvocationRow.project_id == str(project_id))
                    .order_by(ActionInvocationRow.created_at.desc())
                    .limit(50)
                ).all()
            ]
            action_ids = [invocation.id for invocation, _definition in action_history]
            if action_ids:
                action_observations = list(
                    self.session.scalars(
                        select(ActionObservationRow)
                        .where(
                            ActionObservationRow.project_id == str(project_id),
                            ActionObservationRow.invocation_id.in_(action_ids),
                        )
                        .order_by(ActionObservationRow.observed_at.desc())
                        .limit(100)
                    ).all()
                )
        metric_definitions = self.session.scalars(
            select(MetricDefinitionRow).where(MetricDefinitionRow.project_id == str(project_id))
        ).all()
        metric_statement = select(MetricObservationRow).where(
            MetricObservationRow.project_id == str(project_id)
        )
        if snapshot_row is not None:
            metric_statement = metric_statement.where(
                MetricObservationRow.id.in_(snapshot_row.manifest.get("metric_observation_ids", []))
            )
        else:
            metric_statement = metric_statement.where(
                MetricObservationRow.record_status == "ACTIVE"
            )
        metric_observations = self.session.scalars(
            metric_statement.order_by(MetricObservationRow.observed_at.desc()).limit(100)
        ).all()
        meetings = self.session.scalars(
            select(MeetingRecordRow)
            .where(MeetingRecordRow.project_id == str(project_id))
            .order_by(MeetingRecordRow.occurred_at.desc())
            .limit(30)
        ).all()
        management_signals = self.session.scalars(
            select(ManagementSignalRow)
            .where(ManagementSignalRow.project_id == str(project_id))
            .order_by(ManagementSignalRow.created_at.desc())
            .limit(50)
        ).all()
        management_insights = self.session.scalars(
            select(ManagementInsightRow)
            .where(ManagementInsightRow.project_id == str(project_id))
            .order_by(ManagementInsightRow.created_at.desc())
            .limit(50)
        ).all()
        tradeoffs = self.session.scalars(
            select(DesignTradeoffRow).where(DesignTradeoffRow.project_id == str(project_id))
        ).all()
        information_requests = self.session.scalars(
            select(InformationRequestRow)
            .where(InformationRequestRow.project_id == str(project_id))
            .order_by(InformationRequestRow.created_at.desc())
            .limit(50)
        ).all()
        learning_cases: list[LearningCaseRow] = []
        if reference_case_ids:
            learning_cases = list(
                self.session.scalars(
                    select(LearningCaseRow).where(
                        LearningCaseRow.id.in_([str(item) for item in reference_case_ids]),
                        LearningCaseRow.status == "CONFIRMED",
                    )
                ).all()
            )
        history = list(
            self.session.scalars(
                select(AgentMessageRow)
                .where(AgentMessageRow.thread_id == thread_id)
                .order_by(AgentMessageRow.created_at.desc(), AgentMessageRow.id.desc())
                .limit(20)
            ).all()
        )
        history.reverse()
        return {
            "company": {
                "id": company.id if company else None,
                "name": company.name if company else None,
                "industry": company.industry if company else None,
            },
            "project": {
                "id": project.id,
                "name": project.name,
                "revision": project.revision,
                "model_baseline": (
                    {
                        "kind": "QUERY_SNAPSHOT",
                        "query_snapshot_id": snapshot_row.id,
                        "model_scope": snapshot_row.model_scope,
                        "publication_id": snapshot_row.publication_id,
                        "project_revision": snapshot_row.project_revision,
                        "data_cutoff": snapshot_row.data_cutoff,
                    }
                    if snapshot_row is not None
                    else {
                        "kind": "RELEASE",
                        "release_id": publication.id,
                        "version": publication.version,
                    }
                    if publication is not None
                    else {"kind": "DRAFT", "revision": project.revision}
                ),
            },
            "counts": {
                "ontology_types": len(ontology),
                "entities": graph_entity_total,
                "relations": graph_relation_total,
                "documents": len(documents),
                "claims": len(claims),
                "hypotheses": len(hypotheses),
                "scenarios": len(scenarios),
                "source_systems": len(sources),
                "semantic_mappings": len(mappings),
                "source_assets": len(source_assets),
                "raw_batches": len(raw_batches),
                "materialization_runs": len(materialization_runs),
                "semantic_datasets": len(semantic_datasets),
                "source_identities": len(source_identities),
                "unresolved_source_identities": len(
                    [item for item in source_identities if item.status == "UNRESOLVED"]
                ),
                "observation_assertions": len(observation_assertions),
                "observation_conflicts": len(observation_conflicts),
                "reference_cases": len(learning_cases),
                "metric_definitions": len(metric_definitions),
                "metric_observations": len(metric_observations),
                "meetings": len(meetings),
                "management_signals": len(management_signals),
                "management_insights": len(management_insights),
                "design_tradeoffs": len(tradeoffs),
                "information_requests": len(information_requests),
                "action_invocations": len(action_history),
                "action_observations": len(action_observations),
                "available_actions": len(
                    [
                        item
                        for item in action_definitions
                        if item.key in tool_keys_for_agent(agent_kind)
                    ]
                ),
            },
            "ontology": [
                {
                    "key": item.key,
                    "name": item.name,
                    "kind": item.kind,
                    "properties": item.properties,
                    "relation_roles": item.relation_roles,
                }
                for item in ontology
            ],
            "graph_coverage": {
                "available_entities": graph_entity_total,
                "included_entities": len(context_entities),
                "available_relations": graph_relation_total,
                "included_relations": len(context_relations),
                "truncated": (
                    graph_entity_total > len(context_entities)
                    or graph_relation_total > len(context_relations)
                ),
                "read_tool": "read_graph_neighborhood",
            },
            "entities": [item.model_dump(mode="json") for item in context_entities],
            "relations": [item.model_dump(mode="json") for item in context_relations],
            "documents": [
                {"id": item.id, "file_name": item.file_name, "kind": item.kind}
                for item in documents
            ],
            "material_coverage": material_coverage,
            "evidence_fragments": [
                {
                    "id": item.id,
                    "source_document_id": item.source_document_id,
                    "locator": item.locator,
                    "text": item.text[:1500],
                    "text_truncated": len(item.text) > 1500,
                    "original_char_count": len(item.text),
                }
                for item in fragments
            ],
            "claims": [
                {
                    "id": item.id,
                    "subject": item.subject,
                    "predicate": item.predicate,
                    "value": item.value,
                    "status": item.status,
                }
                for item in claims
            ],
            "hypotheses": [
                {"id": item.id, "title": item.title, "status": item.status} for item in hypotheses
            ],
            "scenarios": [
                {"id": item.id, "name": item.name, "goal": item.goal, "status": item.status}
                for item in scenarios
            ],
            "source_systems": [
                {"id": item.id, "name": item.name, "kind": item.kind, "status": item.status}
                for item in sources
            ],
            "semantic_mappings": [
                {
                    "id": item.id,
                    "source_asset": item.source_asset,
                    "source_field": item.source_field,
                    "target_type_key": item.target_type_key,
                    "target_property_key": item.target_property_key,
                    "status": item.status,
                    "revision": item.revision,
                }
                for item in mappings
            ],
            "source_assets": [
                {
                    "id": item.id,
                    "source_system_id": item.source_system_id,
                    "asset_key": item.asset_key,
                    "name": item.name,
                    "schema_fields": item.schema_fields,
                    "status": item.status,
                    "revision": item.revision,
                }
                for item in source_assets
            ],
            "raw_batches": [
                {
                    "id": item.id,
                    "source_asset_id": item.source_asset_id,
                    "status": item.status,
                    "record_count": item.record_count,
                    "schema_fingerprint": item.schema_fingerprint,
                }
                for item in raw_batches
            ],
            "materialization_runs": [
                {
                    "id": item.id,
                    "raw_batch_id": item.raw_batch_id,
                    "mapping_ids": item.mapping_ids,
                    "status": item.status,
                    "records_processed": item.records_processed,
                    "mappings_applied": item.mappings_applied,
                    "errors": item.errors,
                }
                for item in materialization_runs
            ],
            "semantic_datasets": [
                {
                    "id": item.id,
                    "key": item.key,
                    "name": item.name,
                    "root_type_key": item.root_type_key,
                    "columns": item.columns,
                    "status": item.status,
                    "revision": item.revision,
                }
                for item in semantic_datasets
            ],
            "source_identities": [
                {
                    "id": item.id,
                    "source_system_id": item.source_system_id,
                    "source_asset": item.source_asset,
                    "source_record_key": item.source_record_key,
                    "target_type_key": item.target_type_key,
                    "entity_id": item.entity_id,
                    "status": item.status,
                }
                for item in source_identities
            ],
            "observation_assertions": [
                {
                    "id": item.id,
                    "entity_id": item.entity_id,
                    "source_identity_id": item.source_identity_id,
                    "source_document_id": item.source_document_id,
                    "raw_record_id": item.raw_record_id,
                    "semantic_mapping_id": item.semantic_mapping_id,
                    "materialization_run_id": item.materialization_run_id,
                    "source_asset": item.source_asset,
                    "source_record_key": item.source_record_key,
                    "field_key": item.field_key,
                    "value": item.value,
                    "authority_priority": item.authority_priority,
                    "observed_at": item.observed_at,
                    "version": item.version,
                    "supersedes_id": item.supersedes_id,
                }
                for item in observation_assertions
            ],
            "observation_conflicts": [
                {
                    "id": item.id,
                    "entity_id": item.entity_id,
                    "field_key": item.field_key,
                    "candidate_assertion_ids": item.candidate_assertion_ids,
                    "status": item.status,
                    "resolution_kind": item.resolution_kind,
                    "chosen_assertion_id": item.chosen_assertion_id,
                    "revision": item.revision,
                }
                for item in observation_conflicts
            ],
            "metric_definitions": [
                {
                    "id": item.id,
                    "entity_id": item.entity_id,
                    "key": item.key,
                    "name": item.name,
                    "scope": item.scope,
                    "direction": item.direction,
                    "owner_entity_id": item.owner_entity_id,
                    "strategy_entity_id": item.strategy_entity_id,
                    "outcome_entity_id": item.outcome_entity_id,
                    "target_value": item.target_value,
                    "properties": item.properties,
                }
                for item in metric_definitions
            ],
            "metric_observations": [
                {
                    "metric_definition_id": item.metric_definition_id,
                    "period_key": item.period_key,
                    "observed_at": item.observed_at,
                    "value": item.value,
                    "status": item.status,
                }
                for item in metric_observations
            ],
            "meetings": [
                {
                    "id": item.id,
                    "title": item.title,
                    "occurred_at": item.occurred_at,
                    "related_entity_ids": item.related_entity_ids,
                    "topics": item.topics,
                    "decisions": item.decisions,
                    "action_items": item.action_items,
                    "escalations": item.escalations,
                }
                for item in meetings
            ],
            "management_signals": [
                {
                    "id": item.id,
                    "side": item.side,
                    "signal_key": item.signal_key,
                    "issue_family": item.issue_family,
                    "title": item.title,
                    "summary": item.summary,
                    "severity": item.severity,
                    "confidence": item.confidence,
                    "affected_entity_ids": item.affected_entity_ids,
                }
                for item in management_signals
            ],
            "management_insights": [
                {
                    "id": item.id,
                    "classification": item.classification,
                    "issue_family": item.issue_family,
                    "title": item.title,
                    "summary": item.summary,
                    "severity": item.severity,
                    "status": item.status,
                    "rationale": item.rationale,
                    "management_feedback": item.management_feedback,
                }
                for item in management_insights
            ],
            "design_tradeoffs": [
                {
                    "id": item.id,
                    "title": item.title,
                    "issue_family": item.issue_family,
                    "benefit": item.benefit,
                    "cost": item.cost,
                    "affected_entity_ids": item.affected_entity_ids,
                    "status": item.status,
                }
                for item in tradeoffs
            ],
            "information_requests": [
                {
                    "id": item.id,
                    "title": item.title,
                    "question": item.question,
                    "reason": item.reason,
                    "priority": item.priority,
                    "status": item.status,
                    "answer": item.answer,
                }
                for item in information_requests
            ],
            "action_invocations": [
                {
                    "id": invocation.id,
                    "action_key": definition.key,
                    "action_name": definition.name,
                    "status": invocation.status,
                    "target_entity_ids": invocation.target_entity_ids,
                    "input": invocation.input,
                    "result": invocation.result,
                    "error": invocation.error,
                    "created_at": invocation.created_at,
                    "finished_at": invocation.finished_at,
                    "observation_ids": [
                        item.id
                        for item in action_observations
                        if item.invocation_id == invocation.id
                    ],
                }
                for invocation, definition in action_history
            ],
            "action_observations": [
                {
                    "id": item.id,
                    "invocation_id": item.invocation_id,
                    "observation_kind": item.observation_kind,
                    "metric_key": item.metric_key,
                    "metric_definition_id": item.metric_definition_id,
                    "metric_observation_id": item.metric_observation_id,
                    "period_key": item.period_key,
                    "dimensions": item.dimensions,
                    "observed_value": item.observed_value,
                    "outcome": item.outcome,
                    "note": item.note,
                    "observed_at": item.observed_at,
                }
                for item in action_observations
            ],
            "available_actions": [
                {
                    "key": item.key,
                    "name": item.name,
                    "description": item.description,
                    "parameters": item.parameters,
                    "input_schema": self._action_input_schema(item.key),
                    "require_approval": item.require_approval,
                    "risk_level": item.risk_level,
                }
                for item in action_definitions
                if item.key in tool_keys_for_agent(agent_kind)
            ],
            "reference_cases": [
                self._learning_case_context(item, project_id) for item in learning_cases
            ],
            "conversation": [
                {"role": item.role, "content": item.content[-4000:]} for item in history
            ],
        }

    @staticmethod
    def _search_text(value: Any) -> str:
        if isinstance(value, str):
            return value
        try:
            return json.dumps(value, ensure_ascii=False, default=str, separators=(",", ":"))
        except (TypeError, ValueError):
            return str(value)

    @staticmethod
    def _search_terms(query: str | None) -> list[str]:
        if not query:
            return []
        lowered = query.casefold()
        terms = re.findall(r"[a-z0-9_]+", lowered)
        for run in re.findall(r"[\u3400-\u9fff]+", lowered):
            if len(run) <= 3:
                terms.append(run)
            else:
                terms.extend(run[index : index + 2] for index in range(len(run) - 1))
                terms.extend(run[index : index + 3] for index in range(len(run) - 2))
        return list(dict.fromkeys(term for term in terms if term))

    @classmethod
    def _relevance_score(cls, text: str, query: str | None) -> int:
        terms = cls._search_terms(query)
        if not terms:
            return 0
        normalized_text = text.casefold()
        normalized_query = "".join((query or "").casefold().split())
        score = 3 if normalized_query and normalized_query in normalized_text else 0
        score += sum(2 if len(term) > 1 else 1 for term in terms if term in normalized_text)
        return score

    @classmethod
    def _rank_context_items(
        cls,
        items: list[Any],
        query: str | None,
        text_getter: Callable[[Any], str],
        *,
        preserve_prefix_count: int = 0,
    ) -> list[Any]:
        if not query or not items:
            return items
        prefix = items[:preserve_prefix_count]
        suffix = items[preserve_prefix_count:]
        ranked = sorted(
            enumerate(suffix),
            key=lambda pair: (-cls._relevance_score(text_getter(pair[1]), query), pair[0]),
        )
        return [*prefix, *(item for _, item in ranked)]

    @staticmethod
    def _select_graph_context(
        entities: list[Any],
        relations: list[Any],
        *,
        entity_limit: int,
        relation_limit: int,
    ) -> tuple[list[Any], list[Any]]:
        selected_entities = entities[:entity_limit]
        selected_ids = {str(item.id) for item in selected_entities}
        # Preserve endpoint coherence for a relevant relation when the bounded
        # entity sample has room for its missing endpoint(s).
        for relation in relations:
            participant_ids = {
                str(participant.entity_id) for participant in relation.participants
            }
            missing = participant_ids - selected_ids
            if not missing or not (participant_ids & selected_ids):
                continue
            available = entity_limit - len(selected_entities)
            if len(missing) <= available:
                selected_entities.extend(
                    item for item in entities if str(item.id) in missing
                )
                selected_ids.update(missing)
        selected_relations = [
            relation
            for relation in relations
            if all(
                str(participant.entity_id) in selected_ids
                for participant in relation.participants
            )
        ][:relation_limit]
        return selected_entities, selected_relations

    @staticmethod
    def _learning_case_context(item: LearningCaseRow, project_id: UUID) -> dict[str, Any]:
        if item.project_id != str(project_id):
            return {
                "id": item.id,
                "industry": item.industry,
                "organization_scale": item.organization_scale,
                "reusable_summary": item.reusable_summary,
                "tags": item.tags,
                "scope": "ANONYMIZED_CROSS_PROJECT",
            }
        return {
            "id": item.id,
            "title": item.title,
            "industry": item.industry,
            "organization_scale": item.organization_scale,
            "challenge": item.challenge,
            "context": item.context,
            "intervention": item.intervention,
            "outcome": item.outcome,
            "lessons": item.lessons,
            "tags": item.tags,
            "scope": "CURRENT_PROJECT",
        }

    def _save_action_proposals(
        self,
        project_id: UUID,
        run: AgentRunRow,
        proposals: list[AgentActionProposal],
        *,
        round_number: int,
    ) -> tuple[list[str], list[str]]:
        action_service = ActionService(self.session)
        action_service.ensure_defaults(project_id)
        definitions = {
            item.key: item
            for item in self.session.scalars(
                select(ActionDefinitionRow).where(ActionDefinitionRow.project_id == str(project_id))
            ).all()
        }
        allowed = tool_keys_for_agent(run.agent_kind)
        invocation_ids: list[str] = []
        notes: list[str] = []
        for index, proposal in enumerate(proposals[:20]):
            if proposal.action_key not in allowed:
                notes.append(f"已拒绝越权动作 {proposal.action_key}")
                continue
            definition = definitions.get(proposal.action_key)
            if definition is None:
                notes.append(f"动作 {proposal.action_key} 尚未注册")
                continue
            spec = get_tool_spec(proposal.action_key)
            if spec is None or not spec.handler_name:
                notes.append(f"动作 {proposal.action_key} 没有可执行工具实现")
                continue
            try:
                with self.session.begin_nested():
                    invocation = action_service.create_invocation(
                        project_id,
                        ActionInvocationCreate(
                            action_definition_id=UUID(definition.id),
                            input=proposal.input,
                            target_entity_ids=proposal.target_entity_ids,
                            requested_by=f"agent:{run.agent_kind.lower()}",
                            source_agent_run_id=UUID(run.id),
                            idempotency_key=(
                                f"agent:{run.id}:{round_number}:{index}:{proposal.action_key}"
                            ),
                        ),
                    )
                invocation_ids.append(str(invocation.id))
                notes.append(f"{definition.name} 已保存，等待预演和确认")
            except (DomainError, ValidationError, ValueError) as exc:
                code = exc.code if isinstance(exc, DomainError) else type(exc).__name__
                notes.append(f"动作 {proposal.action_key} 未保存：{code}")
        return invocation_ids, notes

    @staticmethod
    def _parse_model_output(raw: str) -> AgentStructuredOutput:
        text = raw.strip()
        if text.startswith("```"):
            text = re.sub(r"^```(?:json)?\s*", "", text, flags=re.IGNORECASE)
            text = re.sub(r"\s*```$", "", text)
        start = text.find("{")
        end = text.rfind("}")
        if start >= 0 and end > start:
            try:
                return AgentStructuredOutput.model_validate(json.loads(text[start : end + 1]))
            except (json.JSONDecodeError, ValidationError):
                pass
        raise DomainError(
            "MODEL_OUTPUT_INVALID",
            "模型没有返回合法的结构化结果。",
            status_code=502,
        )

    @staticmethod
    def _mock_response(
        agent_kind: str, user_content: str, context: dict[str, Any]
    ) -> AgentStructuredOutput:
        normalized = user_content.strip()
        tool_results = (context.get("agent_execution") or {}).get("tool_results") or []
        if tool_results:
            completed = [item for item in tool_results if item.get("status") == "SUCCEEDED"]
            failed = [item for item in tool_results if item.get("status") == "FAILED"]
            return AgentStructuredOutput(
                content=(
                    f"原问题：{normalized[:500]}。"
                    f"已核对工具回读：{len(completed)}项成功、{len(failed)}项失败。"
                    "成功只表示平台记录已经写入；信息不足的判断、现实执行或效果观察"
                    "仍待后续确认。"
                )
            )
        if agent_kind == AgentKind.MANAGEMENT.value and any(
            word in normalized
            for word in (
                "运行分析",
                "管理分析",
                "分析管理信号",
                "交叉分析",
                "全面检查",
                "重新诊断",
            )
        ):
            return AgentStructuredOutput(
                content="我已形成一次设计侧与结果侧交叉分析动作提案。",
                action_proposals=[
                    AgentActionProposal(
                        action_key="run_management_analysis",
                        input={"requested_by": "management-agent"},
                    )
                ],
            )
        if agent_kind == AgentKind.MANAGEMENT.value and any(
            word in normalized
            for word in ("补充信息", "信息请求", "需要了解", "继续调研", "为什么", "原因")
        ):
            subject = AgentRuntimeService._subject(normalized, "补充管理信息")
            return AgentStructuredOutput(
                content="当前信息不足以形成可靠判断，我已生成补充信息请求。",
                action_proposals=[
                    AgentActionProposal(
                        action_key="save_information_request",
                        input={
                            "title": subject[:300],
                            "question": normalized,
                            "reason": "用于验证管理判断并减少因果推断的不确定性。",
                            "priority": "MEDIUM",
                        },
                    )
                ],
            )
        if agent_kind == AgentKind.MANAGEMENT.value:
            for marker, action_key, reply in (
                ("因果假设", "save_causal_hypothesis", "已形成待验证、可证伪的因果假设。"),
                ("记录会议", "record_meeting_observation", "已形成会议现实结果记录提案。"),
                ("记录取舍", "record_design_tradeoff", "已形成设计取舍记录提案。"),
            ):
                if marker not in normalized:
                    continue
                start = normalized.find("{")
                end = normalized.rfind("}")
                if start < 0 or end <= start:
                    continue
                try:
                    action_input = json.loads(normalized[start : end + 1])
                except json.JSONDecodeError:
                    continue
                if isinstance(action_input, dict):
                    return AgentStructuredOutput(
                        content=reply,
                        action_proposals=[
                            AgentActionProposal(action_key=action_key, input=action_input)
                        ],
                    )
        if agent_kind == AgentKind.MANAGEMENT.value and any(
            word in normalized for word in ("保存假设", "可能", "瓶颈", "潜在", "风险")
        ):
            title = AgentRuntimeService._subject(normalized, "管理层待确认假设")
            return AgentStructuredOutput(
                content="我已形成一条待验证管理假设，它不会进入可信企业投影。",
                action_proposals=[
                    AgentActionProposal(
                        action_key="save_hypothesis",
                        input={
                            "type_key": "management_observation",
                            "title": title[:300],
                            "summary": normalized,
                            "uncertainties": ["尚未经过管理层确认"],
                            "validation_questions": ["哪些事实能够证实或否定这一判断？"],
                        },
                    )
                ],
            )
        if agent_kind == AgentKind.MANAGEMENT.value and any(
            word in normalized for word in ("方案", "重构", "优化")
        ):
            name = AgentRuntimeService._subject(normalized, "管理改进方案")
            return AgentStructuredOutput(
                content="我已形成一项待确认的管理方案。",
                action_proposals=[
                    AgentActionProposal(
                        action_key="create_scenario",
                        input={"name": name[:300], "goal": normalized},
                    )
                ],
            )
        if agent_kind == AgentKind.PROJECTION.value and "创建岗位" in normalized:
            name = AgentRuntimeService._subject(normalized, "待确认岗位")
            return AgentStructuredOutput(
                content="已形成创建岗位的受控动作提案。",
                action_proposals=[
                    AgentActionProposal(
                        action_key="create_entity",
                        input={
                            "type_key": "role",
                            "name": name[:300],
                            "properties": {"purpose": "待结合调研材料确认"},
                            "viewpoint": "REPORTED",
                        },
                    )
                ],
            )
        if agent_kind == AgentKind.PROJECTION.value and any(
            marker in normalized
            for marker in (
                "建立投影",
                "建立数字投影",
                "订单履约数字投影",
                "结构化企业对象",
                "企业对象和关系",
                "根据材料建模",
                "根据调研建模",
                "形成模型草案",
            )
        ):
            projection_response = AgentRuntimeService._mock_projection_response(normalized, context)
            if projection_response is not None:
                return projection_response
        if agent_kind == AgentKind.SYSTEM_ONTOLOGY.value and "创建映射" in normalized:
            start = normalized.find("{")
            end = normalized.rfind("}")
            if start >= 0 and end > start:
                try:
                    mapping_input = json.loads(normalized[start : end + 1])
                except json.JSONDecodeError:
                    mapping_input = None
                if isinstance(mapping_input, dict):
                    return AgentStructuredOutput(
                        content="已形成语义映射动作提案，等待预演和人工批准。",
                        action_proposals=[
                            AgentActionProposal(
                                action_key="create_semantic_mapping",
                                input=mapping_input,
                            )
                        ],
                    )
        if agent_kind == AgentKind.SYSTEM_ONTOLOGY.value and any(
            marker in normalized for marker in ("查找映射候选", "建议映射", "检查字段", "对齐字段")
        ):
            start = normalized.find("{")
            end = normalized.rfind("}")
            request: dict[str, Any] | None = None
            if start >= 0 and end > start:
                try:
                    decoded = json.loads(normalized[start : end + 1])
                except json.JSONDecodeError:
                    decoded = None
                if isinstance(decoded, dict):
                    request = decoded
            if request and request.get("target_type_key"):
                return AgentStructuredOutput(
                    content="我将只读取来源资产字段与本体定义，生成需要人工核对的映射候选；不会自动写入。",
                    action_proposals=[
                        AgentActionProposal(
                            action_key="suggest_semantic_mappings",
                            input=request,
                        )
                    ],
                )
        counts = context.get("counts", {})
        return AgentStructuredOutput(
            content=(
                "已读取当前项目上下文："
                f"{counts.get('entities', 0)}个对象、{counts.get('relations', 0)}条关系、"
                f"{counts.get('claims', 0)}条证据声明。请继续说明目标。"
            )
        )

    @staticmethod
    def _mock_projection_response(
        normalized: str, context: dict[str, Any]
    ) -> AgentStructuredOutput | None:
        """Build a deterministic, review-only manufacturing projection for offline UAT.

        The mock never creates unsupported concepts: every node and edge is gated by a
        matching evidence fragment and carries that exact fragment reference.  It is a
        local acceptance fixture, not a substitute for a configured language model.
        """
        counts = context.get("counts") or {}
        if counts.get("entities", 0) or not counts.get("claims", 0):
            return None
        ontology_keys = {item.get("key") for item in context.get("ontology", [])}
        required_types = {
            "company",
            "role",
            "responsibility",
            "process",
            "process_step",
            "contains",
            "holds_responsibility",
            "performs",
            "information_flow",
            "command_flow",
        }
        if not required_types <= ontology_keys:
            return None
        fragments = list(context.get("evidence_fragments") or [])
        corpus = "\n".join(str(item.get("text") or "") for item in fragments)
        if not all(marker in corpus for marker in ("订单", "销售", "生产", "质量")):
            return AgentRuntimeService._mock_generic_projection_response(
                normalized, context, fragments, corpus, ontology_keys
            )

        project_id = str((context.get("project") or {}).get("id") or "mock-project")
        base_revision = int((context.get("project") or {}).get("revision") or 0)
        company = context.get("company") or {}
        operations: list[dict[str, Any]] = []
        citations: list[EvidenceReference] = []
        entity_ids: dict[str, str] = {}

        def evidence_for(*keywords: str) -> list[dict[str, Any]]:
            fragment = next(
                (
                    item
                    for item in fragments
                    if any(keyword in str(item.get("text") or "") for keyword in keywords)
                ),
                fragments[0] if fragments else None,
            )
            if fragment is None:
                return []
            reference = {
                "source_document_id": fragment.get("source_document_id"),
                "fragment_id": fragment.get("id"),
                "note": f"离线验收规则依据含“{keywords[0]}”的材料片段",
            }
            parsed = EvidenceReference.model_validate(reference)
            if all(
                item.fragment_id != parsed.fragment_id or item.note != parsed.note
                for item in citations
            ):
                citations.append(parsed)
            return [reference]

        def operation_id(stable_key: str) -> str:
            return str(uuid5(NAMESPACE_URL, f"enterprise-insight:{project_id}:{stable_key}"))

        def add_entity(
            stable_key: str,
            type_key: str,
            name: str,
            properties: dict[str, Any],
            *keywords: str,
        ) -> None:
            references = evidence_for(*keywords)
            identifier = operation_id(stable_key)
            entity_ids[stable_key] = identifier
            operations.append(
                {
                    "operation_id": identifier,
                    "kind": "CREATE_ENTITY",
                    "payload": {
                        "type_key": type_key,
                        "stable_key": stable_key,
                        "name": name,
                        "properties": properties,
                        "viewpoint": "REPORTED",
                        "evidence": references,
                    },
                    "evidence": references,
                }
            )

        def add_relation(
            stable_key: str,
            type_key: str,
            name: str,
            participants: list[tuple[str, str]],
            *keywords: str,
            properties: dict[str, Any] | None = None,
        ) -> None:
            references = evidence_for(*keywords)
            operations.append(
                {
                    "operation_id": operation_id(stable_key),
                    "kind": "CREATE_RELATION",
                    "payload": {
                        "type_key": type_key,
                        "name": name,
                        "participants": [
                            {
                                "role_key": role_key,
                                "entity_id": entity_ids[entity_key],
                                "ordinal": index,
                            }
                            for index, (role_key, entity_key) in enumerate(participants)
                        ],
                        "properties": properties or {},
                        "viewpoint": "REPORTED",
                        "evidence": references,
                    },
                    "evidence": references,
                }
            )

        company_name = str(company.get("name") or "当前企业")
        company_properties: dict[str, Any] = {"purpose": "完成客户订单并实现回款"}
        if company.get("industry"):
            company_properties["industry"] = company["industry"]
        add_entity(
            "company.current",
            "company",
            company_name,
            company_properties,
            "客户需求",
            "订单",
        )

        role_specs = (
            ("role.sales", "销售岗位", "接收客户需求并协调报价、交付与回款", "销售"),
            ("role.planning", "计划岗位", "评估产能、承诺交期并维护排产", "计划"),
            ("role.production", "生产岗位", "按计划组织生产并反馈执行变化", "生产"),
            ("role.quality", "质量岗位", "执行质量检验并控制放行", "质量"),
            ("role.finance", "财务岗位", "开票、记录应收并跟进回款", "财务"),
        )
        active_roles = [item for item in role_specs if item[3] in corpus]
        for stable_key, name, purpose, keyword in active_roles:
            add_entity(stable_key, "role", name, {"purpose": purpose}, keyword)

        add_entity("process.order_to_cash", "process", "客户需求到回款", {}, "客户需求", "回款")
        step_specs = (
            ("step.requirement", "接收客户需求", 1, "客户需求"),
            ("step.quotation", "报价评审", 2, "报价"),
            ("step.order_confirmation", "订单确认", 3, "订单确认"),
            ("step.scheduling", "排产", 4, "排产"),
            ("step.production", "生产执行", 5, "生产"),
            ("step.inspection", "检验与放行", 6, "检验"),
            ("step.delivery", "发货", 7, "发货"),
            ("step.invoicing", "开票", 8, "开票"),
            ("step.collection", "回款", 9, "回款"),
        )
        active_steps = [item for item in step_specs if item[3] in corpus]
        for stable_key, name, sequence, keyword in active_steps:
            add_entity(
                stable_key,
                "process_step",
                name,
                {"sequence": sequence, "control_purpose": "保持订单状态与责任可追踪"},
                keyword,
            )

        responsibility_specs = (
            ("responsibility.sales_commitment", "客户与商业承诺责任", "role.sales", "销售"),
            ("responsibility.delivery_commitment", "产能与交期承诺责任", "role.planning", "交期"),
            ("responsibility.production_execution", "生产执行责任", "role.production", "生产"),
            ("responsibility.quality_release", "质量放行责任", "role.quality", "质量"),
            ("responsibility.receivables", "应收与回款责任", "role.finance", "回款"),
        )
        active_responsibilities = [
            item for item in responsibility_specs if item[2] in entity_ids and item[3] in corpus
        ]
        for stable_key, name, _role_key, keyword in active_responsibilities:
            add_entity(
                stable_key,
                "responsibility",
                name,
                {"rationale": "让跨部门订单结果具有明确责任归属", "accountability": name},
                keyword,
            )

        members = [("container", "company.current")]
        members.extend(("member", item[0]) for item in active_roles)
        members.append(("member", "process.order_to_cash"))
        add_relation(
            "relation.company_contains",
            "contains",
            f"{company_name}的岗位与核心流程",
            members,
            "订单",
        )
        if active_steps:
            add_relation(
                "relation.process_contains_steps",
                "contains",
                "客户需求到回款包含流程步骤",
                [("container", "process.order_to_cash")]
                + [("member", item[0]) for item in active_steps],
                "客户需求",
                "回款",
            )

        role_by_step = {
            "step.requirement": "role.sales",
            "step.quotation": "role.sales",
            "step.order_confirmation": "role.sales",
            "step.scheduling": "role.planning",
            "step.production": "role.production",
            "step.inspection": "role.quality",
            "step.delivery": "role.sales",
            "step.invoicing": "role.finance",
            "step.collection": "role.finance",
        }
        for step_key, role_key in role_by_step.items():
            if step_key not in entity_ids or role_key not in entity_ids:
                continue
            add_relation(
                f"relation.performs.{step_key}",
                "performs",
                f"{next(item[1] for item in active_roles if item[0] == role_key)}执行"
                f"{next(item[1] for item in active_steps if item[0] == step_key)}",
                [("performer", role_key), ("activity", step_key)],
                next(item[3] for item in active_steps if item[0] == step_key),
            )

        for responsibility_key, name, role_key, keyword in active_responsibilities:
            add_relation(
                f"relation.holds.{responsibility_key}",
                "holds_responsibility",
                f"{next(item[1] for item in active_roles if item[0] == role_key)}承担{name}",
                [("accountable", role_key), ("responsibility", responsibility_key)],
                keyword,
            )

        direct_relations = (
            (
                "relation.info.sales_planning",
                "information_flow",
                "销售向计划传递客户需求与订单",
                "role.sales",
                "role.planning",
                "客户需求",
            ),
            (
                "relation.info.planning_production",
                "information_flow",
                "计划向生产传递排产变化",
                "role.planning",
                "role.production",
                "排产变化",
            ),
            (
                "relation.info.quality_sales",
                "information_flow",
                "质量向销售反馈放行状态",
                "role.quality",
                "role.sales",
                "质量放行",
            ),
            (
                "relation.info.sales_finance",
                "information_flow",
                "销售向财务传递交付与回款信息",
                "role.sales",
                "role.finance",
                "回款",
            ),
        )
        for stable_key, type_key, name, sender, receiver, keyword in direct_relations:
            if sender in entity_ids and receiver in entity_ids and keyword in corpus:
                add_relation(
                    stable_key,
                    type_key,
                    name,
                    [("sender", sender), ("receiver", receiver)],
                    keyword,
                    properties={"content": name},
                )
        command_relations = (
            (
                "relation.command.planning_production",
                "计划向生产下达排产指令",
                "role.planning",
                "role.production",
                "排产",
            ),
            (
                "relation.command.quality_production",
                "质量向生产发出停产或放行指令",
                "role.quality",
                "role.production",
                "暂停生产",
            ),
        )
        for stable_key, name, issuer, receiver, keyword in command_relations:
            if issuer in entity_ids and receiver in entity_ids and keyword in corpus:
                add_relation(
                    stable_key,
                    "command_flow",
                    name,
                    [("issuer", issuer), ("receiver", receiver)],
                    keyword,
                )

        return AgentStructuredOutput(
            content=(
                f"已依据已选材料形成一项待人工审批的最小订单履约投影："
                f"{sum(item['kind'] == 'CREATE_ENTITY' for item in operations)}个对象、"
                f"{sum(item['kind'] == 'CREATE_RELATION' for item in operations)}条关系。"
                "该结果仅用于离线验收；未被材料支持的内容没有写入。"
            ),
            citations=citations,
            action_proposals=[
                AgentActionProposal(
                    action_key="apply_projection_changes",
                    input={
                        "title": "依据 CompanyCheck 调研建立订单履约投影",
                        "description": normalized[:1000],
                        "base_revision": base_revision,
                        "operations": operations,
                        "created_by": "agent:projection:mock-uat",
                    },
                    reason="批量变更先经过确定性预演，再由开发者审批后原子写入。",
                )
            ],
        )

    @staticmethod
    def _mock_generic_projection_response(
        normalized: str,
        context: dict[str, Any],
        fragments: list[dict[str, Any]],
        corpus: str,
        ontology_keys: set[str | None],
    ) -> AgentStructuredOutput | None:
        """Create a conservative, evidence-backed starter projection for other industries.

        Offline mode must remain useful outside the manufacturing fixture without pretending
        to understand arbitrary language.  This fallback only emits entities for explicit
        organization/process vocabulary and leaves responsibilities, permissions and flows
        to a configured model or a human confirmation pass.
        """
        required_types = {"company", "organization_unit", "process", "contains"}
        if not required_types <= ontology_keys:
            return None

        organization_specs = (
            ("sales", "销售与市场", "客户获取、商业沟通和订单协同"),
            ("research", "产品与研发", "产品设计、研发交付和技术能力建设"),
            ("supply", "采购与供应链", "供应商协同、采购和供应保障"),
            ("manufacturing", "生产制造", "产品或服务的交付执行"),
            ("quality", "质量管理", "质量标准、检验和问题改进"),
            ("logistics", "仓储与物流", "库存、仓储和交付调度"),
            ("finance", "财务与结算", "核算、结算、开票和资金管理"),
            ("people", "人力资源", "招聘、培养和人员支持"),
            ("customer_service", "客户服务", "客户支持、投诉处理和服务改进"),
            ("operations", "运营管理", "日常运营、资源协调和经营支持"),
            ("legal", "法务与合规", "合同、合规和风险控制"),
            ("technology", "信息与技术", "信息系统、数据和技术支撑"),
        )
        organization_aliases: dict[str, tuple[str, ...]] = {
            "sales": ("销售", "市场", "获客", "商务"),
            "research": ("研发", "技术", "产品", "开发"),
            "supply": ("采购", "供应链", "供应商"),
            "manufacturing": ("生产", "制造", "工厂"),
            "quality": ("质量", "品控", "检验"),
            "logistics": ("仓储", "物流", "配送", "库存"),
            "finance": ("财务", "会计", "资金", "结算", "回款", "开票"),
            "people": ("人力", "招聘", "员工", "培训"),
            "customer_service": ("客服", "客户服务", "投诉"),
            "operations": ("运营", "经营管理"),
            "legal": ("法务", "合规", "合同"),
            "technology": ("信息化", "信息系统", "数据", "IT"),
        }
        process_specs = (
            ("customer", "客户与销售流程", ("客户", "销售", "获客", "商务")),
            ("research", "产品研发流程", ("研发", "技术", "产品", "开发")),
            ("procurement", "采购流程", ("采购", "供应商")),
            ("manufacturing", "生产流程", ("生产", "制造")),
            ("quality", "质量控制流程", ("质量", "品控", "检验")),
            ("fulfillment", "履约与物流流程", ("仓储", "物流", "配送", "交付")),
            ("finance", "财务结算流程", ("财务", "回款", "结算", "开票", "对账")),
            ("people", "人力资源流程", ("招聘", "入职", "培训")),
            ("service", "客户服务流程", ("客服", "投诉", "客户服务")),
            ("compliance", "合同与合规流程", ("合同", "合规", "法务")),
        )
        step_specs = (
            ("requirement", "需求分析", ("需求", "客户需求")),
            ("approval", "审批", ("审批", "审核")),
            ("contract", "合同确认", ("合同",)),
            ("development", "开发或实施", ("开发", "研发", "实施")),
            ("production", "生产或交付", ("生产", "制造", "交付")),
            ("inspection", "检验与验收", ("检验", "验收", "质量")),
            ("shipping", "发货或上线", ("发货", "上线", "发布")),
            ("settlement", "结算与回款", ("结算", "回款", "开票", "对账")),
            ("service", "服务与投诉处理", ("客服", "投诉", "售后")),
            ("review", "复盘改进", ("复盘", "改进")),
        )

        matched_organizations = [
            item
            for item in organization_specs
            if any(alias in corpus for alias in organization_aliases[item[0]])
        ]
        matched_processes = [
            item for item in process_specs if any(alias in corpus for alias in item[2])
        ]
        if not matched_organizations and not matched_processes:
            return None

        project_id = str((context.get("project") or {}).get("id") or "mock-project")
        base_revision = int((context.get("project") or {}).get("revision") or 0)
        company = context.get("company") or {}
        company_name = str(company.get("name") or "当前企业")
        operations: list[dict[str, Any]] = []
        citations: list[EvidenceReference] = []
        entity_ids: dict[str, str] = {}

        def evidence_for(*keywords: str) -> list[dict[str, Any]]:
            fragment = next(
                (
                    item
                    for item in fragments
                    if any(keyword in str(item.get("text") or "") for keyword in keywords)
                ),
                fragments[0] if fragments else None,
            )
            if fragment is None:
                return []
            reference = {
                "source_document_id": fragment.get("source_document_id"),
                "fragment_id": fragment.get("id"),
                "note": f"离线通用建模规则依据含“{keywords[0]}”的材料片段",
            }
            parsed = EvidenceReference.model_validate(reference)
            if all(
                item.fragment_id != parsed.fragment_id or item.note != parsed.note
                for item in citations
            ):
                citations.append(parsed)
            return [reference]

        def operation_id(stable_key: str) -> str:
            return str(uuid5(NAMESPACE_URL, f"enterprise-insight:{project_id}:{stable_key}"))

        def add_entity(
            stable_key: str,
            type_key: str,
            name: str,
            properties: dict[str, Any],
            *keywords: str,
        ) -> None:
            references = evidence_for(*keywords)
            identifier = operation_id(stable_key)
            entity_ids[stable_key] = identifier
            operations.append(
                {
                    "operation_id": identifier,
                    "kind": "CREATE_ENTITY",
                    "payload": {
                        "type_key": type_key,
                        "stable_key": stable_key,
                        "name": name,
                        "properties": properties,
                        "viewpoint": "REPORTED",
                        "evidence": references,
                    },
                    "evidence": references,
                }
            )

        def add_relation(
            stable_key: str,
            name: str,
            participants: list[tuple[str, str]],
            *keywords: str,
        ) -> None:
            references = evidence_for(*keywords)
            operations.append(
                {
                    "operation_id": operation_id(stable_key),
                    "kind": "CREATE_RELATION",
                    "payload": {
                        "type_key": "contains",
                        "name": name,
                        "participants": [
                            {
                                "role_key": role_key,
                                "entity_id": entity_ids[entity_key],
                                "ordinal": index,
                            }
                            for index, (role_key, entity_key) in enumerate(participants)
                        ],
                        "properties": {},
                        "viewpoint": "REPORTED",
                        "evidence": references,
                    },
                    "evidence": references,
                }
            )

        add_entity(
            "company.current",
            "company",
            company_name,
            {"industry": company["industry"]} if company.get("industry") else {},
            "企业",
        )
        for key, name, mandate in matched_organizations:
            aliases = organization_aliases[key]
            add_entity(
                f"organization.{key}",
                "organization_unit",
                name,
                {"mandate": f"{mandate}（待管理层确认）"},
                *aliases,
            )
        for key, name, aliases in matched_processes:
            add_entity(f"process.{key}", "process", name, {}, *aliases)

        members = [("container", "company.current")]
        members.extend(
            ("member", f"organization.{key}")
            for key, _name, _mandate in matched_organizations
        )
        members.extend(
            ("member", f"process.{key}") for key, _name, _aliases in matched_processes
        )
        if len(members) > 1:
            add_relation(
                "relation.company_contains_generic",
                f"{company_name}包含初步组织与流程",
                members,
                "企业",
            )

        matched_steps = [
            item for item in step_specs if any(alias in corpus for alias in item[2])
        ]
        for index, (key, name, aliases) in enumerate(matched_steps, start=1):
            add_entity(
                f"step.generic.{key}",
                "process_step",
                name,
                {"sequence": index, "control_purpose": "待结合企业实际流程确认"},
                *aliases,
            )
            target_process = next(
                (
                    process_key
                    for process_key, _process_name, process_aliases in matched_processes
                    if any(alias in process_aliases for alias in aliases)
                ),
                matched_processes[0][0] if matched_processes else None,
            )
            if target_process is not None:
                target_process_name = next(
                    process_name
                    for process_key, process_name, _process_aliases in matched_processes
                    if process_key == target_process
                )
                add_relation(
                    f"relation.process_contains_generic.{key}",
                    f"{target_process_name}包含{name}",
                    [("container", f"process.{target_process}"), ("member", f"step.generic.{key}")],
                    *aliases,
                )

        entity_count = sum(item["kind"] == "CREATE_ENTITY" for item in operations)
        relation_count = sum(item["kind"] == "CREATE_RELATION" for item in operations)
        return AgentStructuredOutput(
            content=(
                f"已依据已选材料形成一项待人工审批的通用企业投影草案："
                f"{entity_count}个对象、{relation_count}条包含关系。"
                "对象仅来自材料中明确出现的组织/业务词；职责、权限、信息流和潜在关系留待后续确认。"
            ),
            citations=citations,
            action_proposals=[
                AgentActionProposal(
                    action_key="apply_projection_changes",
                    input={
                        "title": "依据调研材料建立通用企业投影草案",
                        "description": normalized[:1000],
                        "base_revision": base_revision,
                        "operations": operations,
                        "created_by": "agent:projection:mock-generic-uat",
                    },
                    reason="通用离线建模只提交材料中明确出现的组织和流程候选，仍需开发者审批。",
                )
            ],
        )

    @staticmethod
    def _subject(content: str, fallback: str) -> str:
        for separator in ("：", ":"):
            if separator in content:
                value = content.split(separator, 1)[1].strip()
                if value:
                    return value
        return content[:300] or fallback

    def _latest_user_message(self, thread_id: str, created_at: Any) -> AgentMessageRow:
        row = self.session.scalar(
            select(AgentMessageRow)
            .where(
                AgentMessageRow.thread_id == thread_id,
                AgentMessageRow.role == AgentMessageRole.USER.value,
                AgentMessageRow.created_at <= created_at,
            )
            .order_by(AgentMessageRow.created_at.desc(), AgentMessageRow.id.desc())
            .limit(1)
        )
        if row is None:
            raise DomainError("AGENT_MESSAGE_NOT_FOUND", "Agent运行没有对应消息。", status_code=404)
        return row

    def _require_run(self, project_id: UUID, run_id: UUID) -> AgentRunRow:
        row = self.session.get(AgentRunRow, str(run_id))
        if row is None or row.project_id != str(project_id):
            raise DomainError("AGENT_RUN_NOT_FOUND", "Agent运行不存在。", status_code=404)
        return row

    def _require_thread(self, project_id: UUID, thread_id: str) -> AgentThreadRow:
        row = self.session.get(AgentThreadRow, thread_id)
        if row is None or row.project_id != str(project_id):
            raise DomainError("AGENT_THREAD_NOT_FOUND", "Agent对话不存在。", status_code=404)
        return row

    def _fail(
        self,
        run: AgentRunRow,
        code: str,
        message: str,
        details: list[dict[str, Any]],
    ) -> None:
        run.status = AgentRunStatus.FAILED.value
        run.error = {"code": code, "message": message, "details": json_ready(details)}
        self.session.flush()
