from __future__ import annotations

import hashlib
import json
import re
import time
from collections.abc import Callable
from typing import Any
from uuid import NAMESPACE_URL, UUID, uuid5

import httpx
from pydantic import ValidationError
from sqlalchemy import func, or_, select
from sqlalchemy.orm import Session

from enterprise_insight_backend.actions import ActionService
from enterprise_insight_backend.config import Settings
from enterprise_insight_backend.errors import DomainError
from enterprise_insight_backend.input_agent import ManagementInputAgent
from enterprise_insight_backend.model_profiles import LocalSecretVault
from enterprise_insight_backend.models import (
    ActionDefinitionRow,
    ActionInvocationRow,
    ActionObservationRow,
    AgentMessageRow,
    AgentReadSetOutboxRow,
    AgentRunRow,
    AgentStepOutboxRow,
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
from enterprise_insight_backend.observations import (
    ManagementObservationRow,
    ObservationDatabase,
    ObservationService,
    ObservationVersionRow,
)
from enterprise_insight_backend.portfolio import PortfolioService
from enterprise_insight_backend.potential import (
    PotentialCandidateDraft,
    PotentialDatabase,
    PotentialRecordService,
)
from enterprise_insight_backend.projection import ProjectionService
from enterprise_insight_backend.query_snapshots import QuerySnapshotService
from enterprise_insight_backend.schemas import (
    ActionInvocationCreate,
    ActionInvocationStatus,
    AgentActionProposal,
    AgentClaim,
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
from enterprise_insight_backend.task_classifier import (
    TaskClassificationError,
    TaskIntentCandidate,
    build_task_classification_prompt,
    parse_task_classification,
)
from enterprise_insight_backend.task_routing import (
    CandidateTool,
    TaskRoute,
    TaskSpec,
    route_task,
)
from enterprise_insight_backend.tool_registry import get_tool_spec, tool_keys_for_agent
from enterprise_insight_backend.virtual_work_context import read_reviewed_virtual_work_context

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
        "管理观察库中的日常信息是低可信度线索，不得冒充已验证事实；潜在库中的内容经人确认，"
        "但仍只是低于正式企业模型的待验证判断。需要追查跨库线索或初始覆盖被截断时，"
        "使用search_management_observations和search_potential_records按任务关键词分页检索；"
        "需要按ERP来源记录标识读取已物化的现实观测时使用read_source_observations；"
        "该工具只返回来源观测，尚未绑定身份的记录不能当作正式企业事实。"
        "需要查看岗位实际工作模式时使用work_observation.read，需要比较员工或群体路径时使用"
        "work_observation.compare；工作观察只代表独立采集到的描述性证据，不代表绩效、最佳流程或因果。"
        "先用scan_offset=0；若返回next_scan_offset且需要更广覆盖，使用该值继续扫描；"
        "offset只翻当前扫描窗口里的匹配项，ranking_scope表示排序只在本窗口内有效；"
        "不得把搜索未命中说成不存在。复杂任务最终说明实际读取的数据源及未覆盖范围。"
        "如果用户明确要求ERP来源观测、来源观测或已物化来源，必须提出"
        "read_source_observations动作；没有指定具体记录时source_record_keys传空数组，"
        "由程序读取当前项目已物化来源观测，不能因省略记录标识而跳过ERP证据。"
        "岗位职责、岗位目的、流程步骤、考核指标等虚模细节只有在任务路由明确授权时才可读取；"
        "此类内容来自独立的人工审核虚模，不是正式企业事实。必须区分审核设计元数据与有原文依据的"
        "字段断言；只引用上下文内的virtual-work://模型版本、节点、关系、断言URI及其原始"
        "observation://来源，不得把虚模内容提升为正式事实或据此直接执行操作。"
        "简单查询保持简洁，但后台证据引用与工具轨迹仍需完整保存。"
    ),
    AgentKind.SYSTEM_ONTOLOGY.value: (
        "你是系统语义对齐Agent。只依据源字段、本体类型、映射和血缘进行确定性分析。"
        "不得猜测字段含义或身份；不确定时生成待确认项。可使用映射创建/推进、身份绑定、"
        "冲突裁决、批次物化和类型化语义数据集工具。写入必须经过确定性预演，"
        "要求审批的工具不得绕过人工批准。语义数据集查询按固定 query_snapshot_id 分页，"
        "每页最多50行；若 truncated 为真，应使用 next_offset 与同一快照续查，"
        "不能把单页结果描述为全量。"
    ),
}

# The local rules engine is deliberately not exposed as a user-managed model
# profile.  It is the offline, deterministic fallback that keeps the platform
# usable before an external model is configured.  The marker is persisted in
# the run manifest so a run that pauses for review can resume in the same mode.
LOCAL_RULES_PROFILE_ID = "local-rules"

_SIMPLE_READ_ACTIONS = frozenset(
    {
        "read_enterprise_summary",
        "read_graph_neighborhood",
        "read_material_fragments",
        "read_source_observations",
    }
)

_SOURCE_QUERY_NOISE = frozenset(
    {
        "企业投影",
        "已发布企业投影",
        "erp观测",
        "erp 来源观测",
        "erp来源观测",
        "erp接入观测",
        "来源观测",
        "来源资产",
        "身份状态",
        "是否已绑定",
        "企业数据",
        "正式模型",
        "工作观察",
        "岗位工作观察",
        "订单交付流程",
    }
)


def _management_source_observation_requested(user_text: str) -> bool:
    """Detect an explicit request for the materialized ERP/source store."""

    normalized = "".join(user_text.casefold().split())
    return any(
        marker in normalized
        for marker in (
            "erp来源观测",
            "erp现实观测",
            "erp接入观测",
            "来源观测",
            "已物化来源",
        )
    )

_SOURCE_FIELD_ALIASES: dict[str, tuple[str, ...]] = {
    "order_status": ("order_status",),
    "planned_delivery": ("planned_delivery",),
    "quality_status": ("quality_status",),
    "actual_delivery": ("actual_delivery",),
    "amount": ("amount",),
    "状态": ("order_status", "status"),
    "订单状态": ("order_status",),
    "承诺交期": ("planned_delivery",),
    "计划交期": ("planned_delivery",),
    "质量状态": ("quality_status",),
    "实际交付": ("actual_delivery",),
    "订单金额": ("amount",),
    "金额": ("amount",),
}


class AgentRuntimeService:
    def __init__(
        self,
        session: Session,
        settings: Settings,
        observation_database: ObservationDatabase | None = None,
        potential_database: PotentialDatabase | None = None,
    ) -> None:
        self.session = session
        self.settings = settings
        self.portfolio = PortfolioService(session)
        self.projection = ProjectionService(session)
        self.observation_database = observation_database
        self.potential_database = potential_database

    def process_run(self, project_id: UUID, run_id: UUID) -> AgentRunView:
        run = self._require_run(project_id, run_id)
        if run.status == AgentRunStatus.COMPLETED.value:
            return AgentRunView.model_validate(run)
        if run.status == AgentRunStatus.CANCELLED.value:
            raise DomainError("AGENT_RUN_CANCELLED", "已取消的Agent运行不能执行。", status_code=409)
        if run.agent_kind == AgentKind.MANAGEMENT_INPUT.value:
            try:
                return self._process_management_input_run(project_id, run)
            except DomainError as exc:
                self._fail(run, exc.code, exc.message, exc.details)
                return AgentRunView.model_validate(run)
            except Exception as exc:  # pragma: no cover - defensive runtime boundary
                self._fail(
                    run,
                    "MANAGEMENT_INPUT_RUNTIME_FAILED",
                    "管理输入处理失败；原始材料仍保留在管理观察库。",
                    [{"exception": type(exc).__name__}],
                )
                return AgentRunView.model_validate(run)
        try:
            run.error = None
            run.status = AgentRunStatus.RETRIEVING.value
            self.session.flush()
            thread = self._require_thread(project_id, run.thread_id)
            user_message = self._latest_user_message(thread.id, run.created_at)
            reference_ids = [
                UUID(item) for item in (run.context_manifest or {}).get("reference_case_ids", [])
            ]
            ActionService(self.session).ensure_defaults(project_id)
            manifest = dict(run.context_manifest or {})
            if (
                run.agent_kind == AgentKind.MANAGEMENT.value
                and not manifest.get("task_route")
            ):
                if not manifest.get("task_intent_candidate") and manifest.get(
                    "allow_external_model", False
                ):
                    profile = self._resolve_profile(run)
                    if profile.provider != ModelProvider.MOCK.value:
                        candidate = self._classify_management_task(
                            project_id, run, profile, user_message.content
                        )
                        manifest = dict(run.context_manifest or {})
                        manifest["task_intent_candidate"] = candidate.model_dump(mode="json")
                if manifest.get("task_intent_candidate"):
                    routed = self._apply_management_task_route_candidate(
                        project_id,
                        thread,
                        run,
                        user_message.content,
                        manifest,
                    )
                    if routed is not None:
                        return routed
                    manifest = dict(run.context_manifest or {})
            profile = self._resolve_profile(run)
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
                management_context = context.get("management_context")
                if management_context is not None:
                    manifest["management_context_coverage"] = management_context["coverage"]
                previous_query_manifest = manifest.get("query_manifest")
                updated_query_manifest = self._merge_query_manifest(
                    previous_query_manifest,
                    context,
                    model_context_shared=(
                        profile.provider == ModelProvider.MOCK.value
                        or bool(manifest.get("share_project_context_with_model", False))
                    ),
                )
                self._enqueue_query_manifest_read_sets(
                    run, previous_query_manifest, updated_query_manifest
                )
                manifest["query_manifest"] = updated_query_manifest
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
                model_output_payload = output.model_dump(mode="json")
                model_output_payload.pop("potential_candidate_proposals", None)
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
                    output_payload=model_output_payload,
                )
                output = self._ensure_required_management_reads(
                    project_id,
                    run,
                    user_message.content,
                    output,
                    execution,
                    manifest,
                )
                run.status = AgentRunStatus.PRODUCING_PROPOSAL.value
                self.session.flush()
                if self._is_complex_management_output(run):
                    self._persist_potential_candidate_proposals(
                        project_id,
                        run,
                        output.potential_candidate_proposals,
                    )
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
                    return self._finish_completed(
                        run,
                        thread,
                        output,
                        manifest,
                        context=context,
                        references_visible=(
                            profile.provider == ModelProvider.MOCK.value
                            or bool(manifest.get("share_project_context_with_model", False))
                        ),
                    )

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
                    result_message = self._append_assistant_message(
                        run, thread, content, output
                    )
                    self._record_claim_ledger(
                        run,
                        output,
                        context or {},
                        manifest,
                        result_message_id=result_message.id,
                        model_round=execution["model_rounds"],
                        references_visible=(
                            profile.provider == ModelProvider.MOCK.value
                            or bool(manifest.get("share_project_context_with_model", False))
                        ),
                    )
                    run.status = AgentRunStatus.WAITING_REVIEW.value
                    self.session.flush()
                    return AgentRunView.model_validate(run)

            return self._finish_budget_exhausted(run, thread, manifest, execution)
        except DomainError as exc:
            if exc.code == "AGENT_CONTEXT_BUDGET_EXCEEDED":
                if any(
                    isinstance(item, dict) and item.get("status") == "SUCCEEDED"
                    for item in execution.get("tool_results", [])
                ):
                    # All requested deterministic reads have completed, but a
                    # second model round cannot fit in the local budget. Show
                    # the exact successful tool results instead of reporting a
                    # false failure or inventing a model summary.
                    fallback = self._fallback_tool_response(
                        {"agent_execution": execution}
                    )
                    manifest["execution"] = execution
                    manifest["fallback_reason"] = "AGENT_CONTEXT_BUDGET_EXCEEDED"
                    manifest["external_model_used"] = bool(
                        manifest.get("external_model_used", False)
                    )
                    run.context_manifest = json_ready(manifest)
                    run.error = None
                    self._append_assistant_message(
                        run, thread, fallback.content, fallback
                    )
                    run.status = AgentRunStatus.COMPLETED.value
                    self.session.flush()
                    return AgentRunView.model_validate(run)
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

    def _process_management_input_run(
        self, project_id: UUID, run: AgentRunRow
    ) -> AgentRunView:
        run.error = None
        run.status = AgentRunStatus.RETRIEVING.value
        self.session.flush()
        if self.observation_database is None:
            raise DomainError(
                "MANAGEMENT_OBSERVATION_STORE_UNAVAILABLE",
                "管理观察库不可用；未读取或处理这条输入。",
                status_code=503,
            )
        thread = self._require_thread(project_id, run.thread_id)
        manifest = dict(run.context_manifest or {})
        observation_id = UUID(manifest["source_observation_id"])
        source_revision = int(manifest["source_revision"])
        allow_external_model = bool(manifest.get("allow_external_model", False))
        if not allow_external_model:
            response = (
                "原始信息已保存到管理观察库，未发送给外部模型。"
                "如需 AI 整理，请在后续操作中明确同意模型外发；"
                "该整理结果仍是待人工确认的草稿。"
            )
            manifest.update(
                {
                    "external_model_used": False,
                    "result_store": "OBSERVATION_STORE",
                }
            )
            run.context_manifest = json_ready(manifest)
            run.status = AgentRunStatus.PRODUCING_PROPOSAL.value
            self._append_assistant_message(
                run, thread, response, AgentStructuredOutput(content=response)
            )
            run.status = AgentRunStatus.COMPLETED.value
            self._record_step(
                project_id,
                run,
                kind="INPUT",
                status="SUCCEEDED",
                input_payload={
                    "source_observation_id": str(observation_id),
                    "source_revision": source_revision,
                },
                output_payload={
                    "external_model_used": False,
                    "result_store": "OBSERVATION_STORE",
                    "formal_model_write": False,
                    "potential_store_write": False,
                },
            )
            self.session.flush()
            return AgentRunView.model_validate(run)

        profile = self._resolve_profile(run)
        run.status = AgentRunStatus.RETRIEVING.value
        self.session.flush()
        self.session.commit()
        external_model_attempted = profile.provider != ModelProvider.MOCK.value
        with self.observation_database.session_factory() as observation_session:
            observation_service = ObservationService(observation_session, self.settings)
            observation = observation_service.get(project_id, observation_id)
            if observation.revision != source_revision:
                raise DomainError(
                    "MANAGEMENT_INPUT_SOURCE_REVISION_CHANGED",
                    "对话引用的原始材料版本已变化；请基于最新版本重新发送。",
                    status_code=409,
                )
            try:
                draft = ManagementInputAgent(self.settings).extract(
                    observation.content,
                    profile,
                    allow_external_model=True,
                )
            except DomainError as exc:
                extraction = observation_service.record_extraction(
                    project_id,
                    observation_id,
                    model_profile_id=UUID(profile.id),
                    model_name=profile.model,
                    status="FAILED",
                    draft=None,
                    error_code=exc.code,
                )
                observation_session.commit()
                manifest.update(
                    {
                        "external_model_used": external_model_attempted,
                        "extraction_id": str(extraction.id),
                        "result_store": "OBSERVATION_STORE",
                    }
                )
                run.context_manifest = json_ready(manifest)
                run.error = {"code": exc.code, "message": exc.message, "details": exc.details}
                response = "AI 整理未成功；原始信息和失败记录已保留在管理观察库，可稍后重试。"
                self._append_assistant_message(
                    run, thread, response, AgentStructuredOutput(content=response)
                )
                self._record_step(
                    project_id,
                    run,
                    kind="INPUT_EXTRACTION",
                    status="FAILED",
                    input_payload={
                        "source_observation_id": str(observation_id),
                        "source_revision": source_revision,
                        "model_profile_id": profile.id,
                    },
                    output_payload={"extraction_id": str(extraction.id)},
                    error={"code": exc.code},
                )
                run.status = AgentRunStatus.FAILED.value
                self.session.flush()
                return AgentRunView.model_validate(run)

            extraction = observation_service.record_extraction(
                project_id,
                observation_id,
                model_profile_id=UUID(profile.id),
                model_name=profile.model,
                status="COMPLETED",
                draft=draft,
            )
            observation_session.commit()

        marker = (
            f"management-input-extraction:{observation_id}:{extraction.id}:{source_revision}"
        )
        manifest.update(
            {
                "external_model_used": external_model_attempted,
                "extraction_id": str(extraction.id),
                "result_store": "OBSERVATION_STORE",
            }
        )
        run.context_manifest = json_ready(manifest)
        run.status = AgentRunStatus.PRODUCING_PROPOSAL.value
        self._append_assistant_message(
            run, thread, marker, AgentStructuredOutput(content=marker)
        )
        self._record_step(
            project_id,
            run,
            kind="INPUT_EXTRACTION",
            status="SUCCEEDED",
            input_payload={
                "source_observation_id": str(observation_id),
                "source_revision": source_revision,
                "model_profile_id": profile.id,
            },
            output_payload={
                "extraction_id": str(extraction.id),
                "item_count": len(draft.items),
                "unresolved_count": len(draft.unresolved),
                "result_store": "OBSERVATION_STORE",
            },
        )
        run.status = AgentRunStatus.COMPLETED.value
        self.session.flush()
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
        service = ActionService(
            self.session, self.observation_database, self.potential_database
        )
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
        if kind in {"ROUTE", "CLAIM_LEDGER", "POTENTIAL_CANDIDATE_PROPOSED"}:
            event_payload = {
                "step_id": row.id,
                "kind": row.kind,
                "status": row.status,
                "input_payload": row.input_payload,
                "output_payload": row.output_payload,
            }
            event_id = hashlib.sha256(f"agent-step:{row.id}".encode()).hexdigest()
            self.session.add(
                AgentStepOutboxRow(
                    event_id=event_id,
                    step_id=row.id,
                    run_id=run.id,
                    kind=row.kind,
                    payload_sha256=hashlib.sha256(
                        self._canonical_candidate_json(event_payload).encode("utf-8")
                    ).hexdigest(),
                    created_at=timestamp,
                    attempt_count=0,
                )
            )
        return row

    def _finish_completed(
        self,
        run: AgentRunRow,
        thread: AgentThreadRow,
        output: AgentStructuredOutput,
        manifest: dict[str, Any],
        *,
        context: dict[str, Any] | None = None,
        references_visible: bool = False,
    ) -> AgentRunView:
        result_message = self._append_assistant_message(run, thread, output.content, output)
        self._record_claim_ledger(
            run,
            output,
            context or {},
            manifest,
            result_message_id=result_message.id,
            model_round=int((manifest.get("execution") or {}).get("model_rounds") or 0),
            references_visible=references_visible,
        )
        run.context_manifest = json_ready(manifest)
        run.status = AgentRunStatus.COMPLETED.value
        self.session.flush()
        return AgentRunView.model_validate(run)

    def _record_claim_ledger(
        self,
        run: AgentRunRow,
        output: AgentStructuredOutput,
        context: dict[str, Any],
        manifest: dict[str, Any],
        *,
        result_message_id: str,
        model_round: int,
        references_visible: bool,
    ) -> None:
        if not output.claims:
            return
        ledger = self._build_claim_ledger(
            run.id,
            output.claims,
            context,
            references_visible=references_visible,
            model_round=model_round,
        )
        query_manifest_sha256 = hashlib.sha256(
            json.dumps(
                manifest.get("query_manifest", {}),
                ensure_ascii=False,
                sort_keys=True,
                separators=(",", ":"),
            ).encode("utf-8")
        ).hexdigest()
        self._record_step(
            UUID(run.project_id),
            run,
            kind="CLAIM_LEDGER",
            status="COMPLETED",
            input_payload={
                "result_message_id": result_message_id,
                "model_round": model_round,
                "reference_context_shared": references_visible,
                "query_manifest_sha256": query_manifest_sha256,
            },
            output_payload={
                "entries": ledger,
                "validation_policy": "READ_CONTEXT_MEMBERSHIP_ONLY_V1",
                "semantic_support_verified": False,
            },
        )

    @staticmethod
    def _build_claim_ledger(
        run_id: str,
        claims: list[Any],
        context: dict[str, Any],
        *,
        references_visible: bool,
        model_round: int = 0,
    ) -> list[dict[str, Any]]:
        """Resolve model-provided source refs against the exact context it could read.

        This checks identity and scope only. It intentionally does not claim that a
        cited fragment logically proves the statement.
        """
        allowed = AgentRuntimeService._claim_allowed_references(
            context, references_visible=references_visible
        )

        entries: list[dict[str, Any]] = []
        for ordinal, claim in enumerate(claims, start=1):
            payload = claim.model_dump(mode="json")
            submitted_support = list(payload.get("supporting_refs") or [])
            submitted_counter = list(payload.get("counterevidence_refs") or [])
            valid_support = [item for item in submitted_support if item in allowed]
            valid_counter = [item for item in submitted_counter if item in allowed]
            rejected_count = (
                len(submitted_support) - len(valid_support)
                + len(submitted_counter) - len(valid_counter)
            )
            if not references_visible:
                validation_status = "CONTEXT_NOT_SHARED"
            elif rejected_count:
                validation_status = "UNRESOLVED_REFERENCES_REMOVED"
            elif not valid_support and payload["kind"] == "FACT":
                validation_status = "FACT_WITHOUT_SOURCE"
            elif not valid_support:
                validation_status = "NO_SUPPORTING_SOURCE"
            else:
                validation_status = "REFERENCES_RESOLVED_NOT_SEMANTICALLY_VERIFIED"
            claim_id = uuid5(
                NAMESPACE_URL,
                "enterprise-insight:claim-ledger:"
                f"{run_id}:{model_round}:{ordinal}:{payload['statement']}",
            )
            entries.append(
                {
                    "claim_id": str(claim_id),
                    "ordinal": ordinal,
                    "statement": payload["statement"],
                    "kind": payload["kind"],
                    "supporting_refs": valid_support,
                    "counterevidence_refs": valid_counter,
                    "submitted_supporting_refs": submitted_support,
                    "submitted_counterevidence_refs": submitted_counter,
                    "scope": payload.get("scope"),
                    "unknowns": payload.get("unknowns") or [],
                    "validation_status": validation_status,
                    "rejected_reference_count": rejected_count,
                }
            )
        return entries

    @staticmethod
    def _tool_source_references(tool_result: dict[str, Any]) -> set[str]:
        """Return stable source URIs actually present in a successful read result."""
        if tool_result.get("status") != ActionInvocationStatus.SUCCEEDED.value:
            return set()
        result = tool_result.get("result")
        if not isinstance(result, dict):
            return set()

        def normalized_uuid(value: Any) -> str | None:
            if not isinstance(value, str):
                return None
            try:
                return str(UUID(value))
            except (TypeError, ValueError, AttributeError):
                return None

        def positive_version(value: Any) -> int | None:
            return value if type(value) is int and value > 0 else None

        def collect(items: Any, *, prefix: str, version_key: str | None = None) -> set[str]:
            references: set[str] = set()
            if not isinstance(items, list):
                return references
            for item in items:
                if not isinstance(item, dict):
                    continue
                item_id = normalized_uuid(item.get("id"))
                if item_id is None:
                    continue
                if version_key is None:
                    references.add(f"formal://{prefix}/{item_id}")
                    continue
                version = positive_version(item.get(version_key))
                if version is not None:
                    references.add(f"{prefix}://{item_id}/{version}")
            return references

        resource = result.get("resource")
        if resource == "MANAGEMENT_OBSERVATIONS":
            return collect(result.get("items"), prefix="observation", version_key="revision")
        if resource == "POTENTIAL_RECORDS":
            return collect(result.get("items"), prefix="potential", version_key="version")
        if resource == "GRAPH_NEIGHBORHOOD":
            return collect(result.get("entities"), prefix="entities") | collect(
                result.get("relations"), prefix="relations"
            )
        if resource == "SEMANTIC_QUERY_RUN":
            references = collect(
                [{"id": result.get("dataset_id")}], prefix="semantic_datasets"
            ) | collect(
                [{"id": result.get("query_snapshot_id")}], prefix="query_snapshots"
            )
            rows = result.get("rows")
            if isinstance(rows, list):
                for row in rows:
                    if not isinstance(row, dict):
                        continue
                    root_id = normalized_uuid(row.get("root_entity_id"))
                    if root_id is not None:
                        references.add(f"formal://entities/{root_id}")
                    lineage = row.get("_lineage")
                    if not isinstance(lineage, dict):
                        continue
                    for target_ids in lineage.values():
                        if not isinstance(target_ids, list):
                            continue
                        for target_id in target_ids:
                            normalized = normalized_uuid(target_id)
                            if normalized is not None:
                                references.add(f"formal://entities/{normalized}")
            return references
        if resource == "MATERIAL_FRAGMENTS":
            references = collect(result.get("items"), prefix="evidence_fragments")
            document_id = normalized_uuid(result.get("source_document_id"))
            if document_id is not None:
                references.add(f"formal://documents/{document_id}")
            return references
        if resource == "WORK_OBSERVATION":
            references: set[str] = set()
            analysis_id = normalized_uuid(result.get("analysis_id"))
            if analysis_id is not None:
                references.add(f"work-observation://analysis/{analysis_id}")
            segments = result.get("segments")
            if isinstance(segments, list):
                for segment in segments:
                    if not isinstance(segment, dict):
                        continue
                    segment_id = segment.get("id") or segment.get("segment_id")
                    if isinstance(segment_id, str) and segment_id:
                        references.add(f"work-observation://segment/{segment_id}")
            return references
        if resource == "WORK_OBSERVATION_COMPARISON":
            references: set[str] = set()
            comparison_id = normalized_uuid(result.get("id"))
            analysis_id = normalized_uuid(result.get("analysis_id"))
            if comparison_id is not None:
                references.add(f"work-observation://comparison/{comparison_id}")
            if analysis_id is not None:
                references.add(f"work-observation://analysis/{analysis_id}")
            return references
        return set()

    @staticmethod
    def _tool_scan_coverage(tool_results: Any) -> list[dict[str, Any]]:
        """Keep bounded, content-free coverage facts for reads made through tools."""
        if not isinstance(tool_results, list):
            return []
        scan_fields = {
            "available",
            "scan_offset",
            "scanned",
            "next_scan_offset",
            "matching_in_scanned",
            "matching_count_is_lower_bound",
            "ranking_scope",
            "offset",
            "limit",
            "returned",
            "truncated",
        }
        graph_fields = {
            "revision",
            "query_snapshot_id",
            "release_id",
            "available_entities",
            "available_relations",
            "returned_entities",
            "returned_relations",
            "truncated",
        }
        semantic_fields = {
            "id",
            "dataset_id",
            "query_snapshot_id",
            "row_count",
            "total_rows",
            "offset",
            "limit",
            "returned",
            "next_offset",
            "truncated",
        }
        material_fields = {"source_document_id", "offset", "limit", "total", "returned"}
        work_observation_fields = {
            "analysis_id",
            "event_count",
            "segment_count",
            "employee_count",
            "returned",
            "trusted",
        }
        summaries: list[dict[str, Any]] = []
        for tool_result in tool_results[-100:]:
            if not isinstance(tool_result, dict):
                continue
            invocation_id = tool_result.get("invocation_id")
            if tool_result.get("status") != ActionInvocationStatus.SUCCEEDED.value:
                continue
            result = tool_result.get("result")
            if not isinstance(result, dict):
                continue
            resource = result.get("resource")
            fields = (
                scan_fields
                if resource in {"MANAGEMENT_OBSERVATIONS", "POTENTIAL_RECORDS"}
                else graph_fields
                if resource == "GRAPH_NEIGHBORHOOD"
                else semantic_fields
                if resource == "SEMANTIC_QUERY_RUN"
                else material_fields
                if resource == "MATERIAL_FRAGMENTS"
                else work_observation_fields
                if resource in {"WORK_OBSERVATION", "WORK_OBSERVATION_COMPARISON"}
                else None
            )
            if fields is None:
                continue
            summary: dict[str, Any] = {"resource": resource}
            if isinstance(invocation_id, str):
                try:
                    summary["invocation_id"] = str(UUID(invocation_id))
                except (TypeError, ValueError, AttributeError):
                    pass
            for key in fields:
                value = result.get(key)
                if value is None or isinstance(value, (str, bool)) or type(value) is int:
                    summary[key] = value
            summary["source_reference_count"] = len(
                AgentRuntimeService._tool_source_references(tool_result)
            )
            summaries.append(summary)
        return summaries

    @staticmethod
    def _claim_allowed_references(
        context: dict[str, Any], *, references_visible: bool
    ) -> set[str]:
        if not references_visible:
            return set()
        allowed: set[str] = set()
        formal_resources = (
            "entities",
            "relations",
            "documents",
            "evidence_fragments",
            "claims",
            "hypotheses",
            "scenarios",
            "source_systems",
            "semantic_mappings",
            "source_assets",
            "raw_batches",
            "materialization_runs",
            "semantic_datasets",
            "source_identities",
            "meetings",
            "management_signals",
            "management_insights",
            "design_tradeoffs",
            "information_requests",
            "action_invocations",
            "action_observations",
            "metric_definitions",
            "metric_observations",
            "observation_assertions",
            "observation_conflicts",
        )
        for resource in formal_resources:
            items = context.get(resource)
            if not isinstance(items, list):
                continue
            for item in items:
                if isinstance(item, dict) and isinstance(item.get("id"), str):
                    allowed.add(f"formal://{resource}/{item['id']}")

        management = context.get("management_context")
        if isinstance(management, dict):
            read_manifest = management.get("read_manifest") or {}
            for item in read_manifest.get("management_observation_refs", []):
                if isinstance(item, dict) and item.get("id") and item.get("revision"):
                    allowed.add(f"observation://{item['id']}/{item['revision']}")
            for item in read_manifest.get("potential_record_refs", []):
                if isinstance(item, dict) and item.get("id") and item.get("version"):
                    allowed.add(f"potential://{item['id']}/{item['version']}")

        execution = context.get("agent_execution") or {}
        for item in execution.get("tool_results", []):
            if isinstance(item, dict) and isinstance(item.get("invocation_id"), str):
                allowed.add(f"tool://invocation/{item['invocation_id']}")
                allowed.update(AgentRuntimeService._tool_source_references(item))
        return allowed

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
        if profile_id == LOCAL_RULES_PROFILE_ID:
            return self._local_rules_profile()
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
            return self._local_rules_profile()
        return profile

    @staticmethod
    def _local_rules_profile() -> ModelProfileRow:
        """Return an ephemeral profile for deterministic offline execution.

        This is intentionally not inserted into ``model_profiles``: users must
        not have to create a fake API-key profile just to use the built-in
        evidence-backed rules.  The synthetic id is only an internal run-mode
        marker and is handled explicitly by ``_resolve_profile`` on retries.
        """
        return ModelProfileRow(
            id=LOCAL_RULES_PROFILE_ID,
            name="本地规则能力（内置）",
            provider=ModelProvider.MOCK.value,
            base_url="",
            model="local-rules",
            encrypted_api_key=None,
            temperature=0.0,
            timeout_seconds=0,
            enabled=True,
            is_default=False,
        )

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
            + "\n必须输出JSON对象，字段为content、citations、claims、action_proposals、"
            "potential_candidate_proposals。potential_candidate_proposals必须是数组；"
            "claims必须是数组；每项按AgentClaim JSON Schema填写statement、kind、"
            "supporting_refs、counterevidence_refs、scope、unknowns。重要事实/假设/建议"
            "必须逐项记录；只允许引用授权上下文中实际出现的来源URI，不能编造URI。"
            "formal来源格式为formal://<上下文资源名>/<id>，管理观察为"
            "observation://<id>/<revision>，潜在记录为potential://<id>/<version>，"
            "工具结果为tool://invocation/<id>。没有引用时明确留空，事实不得伪装成有来源。"
            "AgentClaim JSON Schema："
            + json.dumps(
                AgentClaim.model_json_schema(), ensure_ascii=False, separators=(",", ":")
            )
            + "。"
            "只有管理决策Agent的COMPLEX管理分析路由可以提出候选，其余情况必须为空数组。"
            "每个候选必须符合以下PotentialCandidateDraft JSON Schema；candidate_id由系统分配，"
            "不要提供或依赖candidate_id："
            + json.dumps(
                PotentialCandidateDraft.model_json_schema(),
                ensure_ascii=False,
                separators=(",", ":"),
            )
            + "。候选只供人工确认，不代表已写入潜在库。每个候选必须包含至少一条"
            "supporting_evidence；每条supporting_evidence和counterevidence都必须引用"
            "本项目当前有效观察版本，source_ref格式为observation://<uuid>/<revision>，"
            "excerpt必须是该版本原文中的逐字连续片段。"
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
                    # DeepSeek/OpenAI-compatible chat endpoints support JSON
                    # mode.  The runtime parser is deliberately strict because
                    # every proposed action must pass local schema validation;
                    # declaring the response format here avoids accepting a
                    # natural-language answer that cannot be audited or run.
                    "response_format": {"type": "json_object"},
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
        if (context.get("agent_execution") or {}).get("tool_results"):
            return self._fallback_tool_response(context)
        raise invalid_output or DomainError(
            "MODEL_OUTPUT_INVALID", "模型没有返回合法的结构化结果。", status_code=502
        )

    @staticmethod
    def _fallback_tool_response(context: dict[str, Any]) -> AgentStructuredOutput:
        """Return an auditable program summary when a post-tool model reply is invalid.

        A successful deterministic read must not be reported as a failed task merely
        because the external model omitted the required JSON envelope. The fallback
        deliberately does not infer, rank, or rewrite the tool result.
        """

        tool_results = (context.get("agent_execution") or {}).get("tool_results") or []
        lines: list[str] = []
        for item in tool_results:
            if item.get("status") != "SUCCEEDED":
                continue
            result = item.get("result") or {}
            resource = str(result.get("resource") or "TOOL_RESULT")
            lines.append(f"资源：{resource}。")
            if resource == "SOURCE_OBSERVATIONS":
                for row in result.get("rows", [])[:100]:
                    lines.append(
                        "- "
                        f"{row.get('source_asset')}/{row.get('source_record_key')} "
                        f"{row.get('field_key')}={row.get('value')!r}；"
                        f"身份={row.get('identity_status')}；"
                        f"映射={row.get('mapping_status')}；"
                        f"匹配={row.get('match_kind')}。"
                    )
                for warning in result.get("warnings", []):
                    lines.append(f"- 说明：{warning}")
            else:
                compact = json.dumps(result, ensure_ascii=False, default=str)
                lines.append(f"- 程序返回：{compact[:4_000]}")
        if not lines:
            lines.append("没有可直接展示的成功工具结果。")
        return AgentStructuredOutput(
            content=(
                "受控工具读取已完成。外部模型未返回符合平台契约的结构化总结；"
                "以下内容由程序直接整理，未采用模型补写，也未进行推断：\n"
                + "\n".join(lines)
            )
        )

    def _classify_management_task(
        self,
        project_id: UUID,
        run: AgentRunRow,
        profile: ModelProfileRow,
        user_text: str,
    ) -> TaskIntentCandidate:
        """Classify only the submitted text; never include enterprise context.

        This is one explicit external request, not a tool/permission decision.
        A failed or interrupted attempt is not automatically resent on resume.
        """
        manifest = dict(run.context_manifest or {})
        if not manifest.get("allow_external_model", False):
            raise DomainError(
                "EXTERNAL_MODEL_CONFIRMATION_REQUIRED",
                "未允许本次管理问题发送给外部模型；未进行任务分类。",
                status_code=409,
            )
        prior_attempt = manifest.get("task_classification")
        if isinstance(prior_attempt, dict) and prior_attempt.get("status") in {
            "REQUESTED",
            "FAILED",
            "COMPLETED",
        }:
            raise DomainError(
                "TASK_CLASSIFICATION_RETRY_REQUIRES_NEW_MESSAGE",
                "分类请求已尝试但没有可靠结果。为避免重复外发，请新建一条管理问题后重试。",
                status_code=409,
            )
        if not profile.encrypted_api_key:
            raise DomainError("MODEL_API_KEY_REQUIRED", "模型配置没有API密钥。", status_code=422)
        try:
            prompt = build_task_classification_prompt(user_text)
        except TaskClassificationError as exc:
            raise DomainError(
                "TASK_CLASSIFICATION_INPUT_INVALID",
                "问题长度或内容不符合自动分类要求；请缩短问题后重新发送。",
                status_code=422,
            ) from exc

        user_text_hash = hashlib.sha256(user_text.encode("utf-8")).hexdigest()
        key = LocalSecretVault(self.settings).decrypt(profile.encrypted_api_key)
        request_url = f"{profile.base_url.rstrip('/')}/chat/completions"
        model_name = profile.model
        timeout_seconds = profile.timeout_seconds
        profile_id = profile.id
        classification_record = {
            "status": "REQUESTED",
            "source": "external_model_task_classifier_v1",
            "model_profile_id": profile_id,
            "provider": profile.provider,
            "model": model_name,
            "external_model_used": True,
            "consent_basis": "allow_external_model=true",
            "request_count": 1,
            "sent_scope": "raw_user_message_only",
            "enterprise_context_sent": False,
            "user_text_sha256": user_text_hash,
            "user_text_chars": len(user_text),
        }
        manifest["task_classification"] = classification_record
        manifest["external_model_used"] = True
        run.context_manifest = json_ready(manifest)
        run.status = AgentRunStatus.RETRIEVING.value
        self.session.flush()
        # Persist the explicit outbound attempt before the request so a worker
        # crash cannot silently cause an automatic duplicate send on resume.
        self.session.commit()

        try:
            response = httpx.post(
                request_url,
                headers={"Authorization": f"Bearer {key}"},
                json={
                    "model": model_name,
                    "messages": [{"role": "user", "content": prompt}],
                    "temperature": 0,
                    # Reasoning-capable providers can spend part of the
                    # budget before returning the structured classifier
                    # answer.  Keep the request bounded, but leave enough
                    # room for both reasoning and the strict JSON object.
                    "max_tokens": 4096,
                    "response_format": {"type": "json_object"},
                },
                timeout=timeout_seconds,
            )
            response.raise_for_status()
            body = response.json()
            content = body["choices"][0]["message"]["content"]
            if not isinstance(content, str):
                raise TypeError("classifier response content must be text")
            candidate = parse_task_classification(content)
        except Exception as exc:  # external boundary: malformed providers must fail closed
            classification_record = {
                **classification_record,
                "status": "FAILED",
                "error_type": type(exc).__name__,
            }
            manifest["task_classification"] = classification_record
            run.context_manifest = json_ready(manifest)
            self._record_step(
                project_id,
                run,
                kind="TASK_CLASSIFICATION",
                status="FAILED",
                input_payload={
                    "model_profile_id": profile_id,
                    "provider": profile.provider,
                    "model": profile.model,
                    "user_text_sha256": user_text_hash,
                    "user_text_chars": len(user_text),
                    "sent_scope": "raw_user_message_only",
                    "enterprise_context_sent": False,
                },
                output_payload={},
                error={"code": "TASK_CLASSIFICATION_FAILED", "exception": type(exc).__name__},
            )
            self.session.commit()
            raise DomainError(
                "TASK_CLASSIFICATION_FAILED",
                "管理任务分类没有得到可验证结果；未读取企业数据或执行动作。请新建问题重试。",
                status_code=502,
                details=[{"exception": type(exc).__name__}],
            ) from exc

        classification_record = {
            **classification_record,
            "status": "COMPLETED",
            "candidate": candidate.model_dump(mode="json"),
        }
        manifest["task_classification"] = classification_record
        manifest["task_intent_candidate"] = candidate.model_dump(mode="json")
        manifest["external_model_used"] = True
        run.context_manifest = json_ready(manifest)
        self._record_step(
            project_id,
            run,
            kind="TASK_CLASSIFICATION",
            status="SUCCEEDED",
            input_payload={
                "model_profile_id": profile_id,
                "provider": profile.provider,
                "model": profile.model,
                "user_text_sha256": user_text_hash,
                "user_text_chars": len(user_text),
                "sent_scope": "raw_user_message_only",
                "enterprise_context_sent": False,
            },
            output_payload={"candidate": candidate.model_dump(mode="json")},
        )
        self.session.commit()
        return candidate

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
        simple_route = self._task_route_name(manifest) == TaskRoute.SIMPLE.value
        route_spec = (
            manifest.get("route_spec")
            if isinstance(manifest.get("route_spec"), dict)
            else {}
        )
        route_target_ids = route_spec.get("target_ids", [])
        graph_query = GraphQuery(
            entity_limit=self.settings.agent_initial_graph_entities,
            relation_limit=self.settings.agent_initial_graph_relations,
        )
        if simple_route and len(route_target_ids) == 1 and not route_spec.get("company_scope_read"):
            try:
                graph_query = GraphQuery(
                    root_entity_id=UUID(str(route_target_ids[0])),
                    depth=2,
                    entity_limit=self.settings.agent_initial_graph_entities,
                    relation_limit=self.settings.agent_initial_graph_relations,
                )
            except ValueError:
                raise DomainError(
                    "TASK_ROUTE_TARGET_INVALID",
                    "简单查询目标不是有效的企业对象标识。",
                    status_code=409,
                ) from None
        publication = None
        snapshot_row = None
        snapshot_id = manifest.get("query_snapshot_id")
        if snapshot_id:
            snapshot_service = QuerySnapshotService(self.session)
            snapshot_row = snapshot_service.require(project_id, snapshot_id)
            graph = snapshot_service.graph(project_id, UUID(snapshot_id), graph_query)
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
                GraphQuery(
                    release_id=UUID(publication.id) if publication is not None else None,
                    entity_limit=self.settings.agent_initial_graph_entities,
                    relation_limit=self.settings.agent_initial_graph_relations,
                ),
            )
        graph_entity_total = len(graph.entities)
        graph_relation_total = len(graph.relations)
        if snapshot_row is None and publication is None:
            graph_entity_total = self.projection.count_entities(project_id)
            graph_relation_total = self.projection.count_relations(project_id)
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
        ontology_rows = self.session.scalars(
            select(OntologyTypeRow)
            .where(OntologyTypeRow.project_id == str(project_id))
            .order_by(OntologyTypeRow.updated_at.desc(), OntologyTypeRow.id)
            .limit(self.settings.agent_context_ontology_types + 1)
        ).all()
        ontology_truncated = len(ontology_rows) > self.settings.agent_context_ontology_types
        ontology = ontology_rows[: self.settings.agent_context_ontology_types]
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
        if simple_route:
            documents = selected_documents
        else:
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
        remaining_fragment_limit = (
            0 if simple_route else max(0, 40 - len(selected_fragments))
        )
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
        if simple_route or not manifest.get("include_unconfirmed_material", True):
            claim_statement = claim_statement.where(ClaimRow.status != "CANDIDATE")
        claims = self.session.scalars(
            claim_statement.order_by(ClaimRow.created_at.desc()).limit(100)
        ).all()
        hypotheses = self.session.scalars(
            select(HypothesisRow)
            .where(HypothesisRow.project_id == str(project_id))
            .order_by(HypothesisRow.updated_at.desc(), HypothesisRow.id)
            .limit(50)
        ).all() if not simple_route else []
        scenarios = self.session.scalars(
            select(ScenarioRow)
            .where(ScenarioRow.project_id == str(project_id))
            .order_by(ScenarioRow.updated_at.desc(), ScenarioRow.id)
            .limit(30)
        ).all() if not simple_route else []
        source_rows = self.session.scalars(
            select(SourceSystemRow)
            .where(SourceSystemRow.project_id == str(project_id))
            .order_by(SourceSystemRow.updated_at.desc(), SourceSystemRow.id)
            .limit(self.settings.agent_context_source_systems + 1)
        ).all()
        source_truncated = len(source_rows) > self.settings.agent_context_source_systems
        sources = source_rows[: self.settings.agent_context_source_systems]
        mapping_rows = self.session.scalars(
            select(SemanticMappingRow)
            .where(SemanticMappingRow.project_id == str(project_id))
            .order_by(SemanticMappingRow.updated_at.desc(), SemanticMappingRow.id)
            .limit(self.settings.agent_context_semantic_mappings + 1)
        ).all()
        mapping_truncated = len(mapping_rows) > self.settings.agent_context_semantic_mappings
        mappings = mapping_rows[: self.settings.agent_context_semantic_mappings]
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
        if simple_route:
            assertion_statement = assertion_statement.where(
                ObservationAssertionRow.entity_id.in_([str(item.id) for item in graph.entities])
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
        if simple_route:
            conflict_statement = conflict_statement.where(
                ObservationConflictRow.entity_id.in_([str(item.id) for item in graph.entities])
            )
        observation_conflicts = self.session.scalars(
            conflict_statement.order_by(ObservationConflictRow.updated_at.desc()).limit(100)
        ).all()
        action_definition_rows = self.session.scalars(
            select(ActionDefinitionRow)
            .where(
                ActionDefinitionRow.project_id == str(project_id),
                ActionDefinitionRow.enabled.is_(True),
            )
            .order_by(ActionDefinitionRow.updated_at.desc(), ActionDefinitionRow.id)
            .limit(self.settings.agent_context_action_definitions + 1)
        ).all()
        action_definitions_truncated = (
            len(action_definition_rows) > self.settings.agent_context_action_definitions
        )
        action_definitions = action_definition_rows[
            : self.settings.agent_context_action_definitions
        ]
        action_history: list[tuple[ActionInvocationRow, ActionDefinitionRow]] = []
        action_observations: list[ActionObservationRow] = []
        if agent_kind == AgentKind.MANAGEMENT.value and not simple_route:
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
        requested_metric_ids = set(route_spec.get("metric_ids", [])) if simple_route else set()
        metric_definition_statement = select(MetricDefinitionRow).where(
            MetricDefinitionRow.project_id == str(project_id)
        )
        if simple_route:
            if requested_metric_ids:
                metric_definition_statement = metric_definition_statement.where(
                    MetricDefinitionRow.id.in_(requested_metric_ids)
                )
            metric_definition_rows = self.session.scalars(
                metric_definition_statement.order_by(MetricDefinitionRow.id)
            ).all()
            metric_definitions_truncated = False
        else:
            metric_definition_rows = self.session.scalars(
                metric_definition_statement
                .order_by(MetricDefinitionRow.updated_at.desc(), MetricDefinitionRow.id)
                .limit(self.settings.agent_context_metric_definitions + 1)
            ).all()
            metric_definitions_truncated = (
                len(metric_definition_rows) > self.settings.agent_context_metric_definitions
            )
        metric_definitions = (
            metric_definition_rows
            if simple_route
            else metric_definition_rows[: self.settings.agent_context_metric_definitions]
        )
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
        if simple_route:
            metric_statement = metric_statement.where(
                MetricObservationRow.metric_definition_id.in_(
                    set(route_spec.get("metric_ids", []))
                )
            )
        metric_observations = self.session.scalars(
            metric_statement.order_by(MetricObservationRow.observed_at.desc()).limit(100)
        ).all()
        meetings = self.session.scalars(
            select(MeetingRecordRow)
            .where(MeetingRecordRow.project_id == str(project_id))
            .order_by(MeetingRecordRow.occurred_at.desc())
            .limit(30)
        ).all() if not simple_route else []
        management_signals = self.session.scalars(
            select(ManagementSignalRow)
            .where(ManagementSignalRow.project_id == str(project_id))
            .order_by(ManagementSignalRow.created_at.desc())
            .limit(50)
        ).all() if not simple_route else []
        management_insights = self.session.scalars(
            select(ManagementInsightRow)
            .where(ManagementInsightRow.project_id == str(project_id))
            .order_by(ManagementInsightRow.created_at.desc())
            .limit(50)
        ).all() if not simple_route else []
        tradeoff_rows = self.session.scalars(
            select(DesignTradeoffRow)
            .where(DesignTradeoffRow.project_id == str(project_id))
            .order_by(DesignTradeoffRow.updated_at.desc(), DesignTradeoffRow.id)
            .limit(self.settings.agent_context_design_tradeoffs + 1)
        ).all() if not simple_route else []
        tradeoffs_truncated = (
            len(tradeoff_rows) > self.settings.agent_context_design_tradeoffs
        ) if not simple_route else False
        tradeoffs = tradeoff_rows[: self.settings.agent_context_design_tradeoffs]
        information_requests = self.session.scalars(
            select(InformationRequestRow)
            .where(InformationRequestRow.project_id == str(project_id))
            .order_by(InformationRequestRow.created_at.desc())
            .limit(50)
        ).all() if not simple_route else []
        learning_cases: list[LearningCaseRow] = []
        if reference_case_ids and not simple_route:
            learning_cases = list(
                self.session.scalars(
                    select(LearningCaseRow).where(
                        LearningCaseRow.id.in_([str(item) for item in reference_case_ids]),
                        LearningCaseRow.status == "CONFIRMED",
                    )
                ).all()
            )
        history = (
            list(
                self.session.scalars(
                    select(AgentMessageRow)
                    .where(AgentMessageRow.thread_id == thread_id)
                    .order_by(AgentMessageRow.created_at.desc(), AgentMessageRow.id.desc())
                    .limit(20)
                ).all()
            )
            if not simple_route
            else []
        )
        history.reverse()
        management_context = self._management_context(project, query) if (
            agent_kind == AgentKind.MANAGEMENT.value and not simple_route
        ) else None
        reviewed_virtual_work: dict[str, Any] | None = None
        if (
            agent_kind == AgentKind.MANAGEMENT.value
            and not simple_route
            and self.observation_database is not None
            and publication is not None
            and company is not None
        ):
            with self.observation_database.session_factory() as observation_session:
                reviewed_virtual_work = read_reviewed_virtual_work_context(
                    observation_session,
                    company_id=company.id,
                    project_id=project.id,
                    release_id=publication.id,
                    allowed_real_entity_ids=[item.id for item in graph.entities],
                    limits={
                        "models": 5,
                        "anchors": 50,
                        "nodes": 100,
                        "edges": 150,
                        "assertions": 100,
                        "sources_per_assertion": 3,
                        "excerpt_chars": 600,
                        "json_chars": 2_000,
                    },
                )
        context = {
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
                "management_observations": (
                    management_context["coverage"]["observations"]["available"]
                    if management_context else 0
                ),
                "potential_records": (
                    management_context["coverage"]["potential_records"]["available"]
                    if management_context else 0
                ),
                "reviewed_virtual_work_models": (
                    reviewed_virtual_work.get("coverage", {}).get("models_included", 0)
                    if reviewed_virtual_work
                    else 0
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
            "retrieval_policy": {
                "strategy": "bounded_sql_then_targeted_tools",
                "initial_context_is_not_a_project_dump": True,
                "lists_may_be_truncated": True,
                "next_step": "use_read_tools_for_narrowed_or_paged_evidence",
                "limits": {
                    "ontology_types": self.settings.agent_context_ontology_types,
                    "source_systems": self.settings.agent_context_source_systems,
                    "semantic_mappings": self.settings.agent_context_semantic_mappings,
                    "action_definitions": self.settings.agent_context_action_definitions,
                    "metric_definitions": self.settings.agent_context_metric_definitions,
                    "design_tradeoffs": self.settings.agent_context_design_tradeoffs,
                },
                "truncated": {
                    "ontology_types": ontology_truncated,
                    "source_systems": source_truncated,
                    "semantic_mappings": mapping_truncated,
                    "action_definitions": action_definitions_truncated,
                    "metric_definitions": metric_definitions_truncated,
                    "design_tradeoffs": tradeoffs_truncated,
                },
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
                and (
                    not simple_route
                    or item.key in (_SIMPLE_READ_ACTIONS - {"read_source_observations"})
                )
                and (
                    item.key != "read_enterprise_summary"
                    or bool(route_spec.get("company_scope_read"))
                )
            ],
            "reference_cases": [
                self._learning_case_context(item, project_id) for item in learning_cases
            ],
            "conversation": [
                {"role": item.role, "content": item.content[-4000:]} for item in history
            ],
            "management_context": management_context,
            "reviewed_virtual_work": reviewed_virtual_work,
            "management_context_access": {
                "decision": (
                    "EXCLUDED_BY_SIMPLE_ROUTE"
                    if agent_kind == AgentKind.MANAGEMENT.value and simple_route
                    else "INCLUDED"
                    if management_context is not None
                    else "NOT_APPLICABLE"
                ),
                "management_observations_read": management_context is not None,
                "potential_records_read": management_context is not None,
                "virtual_work_read": bool(
                    reviewed_virtual_work
                    and reviewed_virtual_work.get("coverage", {}).get("models_included", 0)
                ),
            },
            "task_route": manifest.get("task_route"),
        }
        if simple_route:
            for key in (
                "documents",
                "material_coverage",
                "evidence_fragments",
                "claims",
                "hypotheses",
                "scenarios",
                "source_systems",
                "semantic_mappings",
                "source_assets",
                "raw_batches",
                "materialization_runs",
                "semantic_datasets",
                "source_identities",
                "meetings",
                "management_signals",
                "management_insights",
                "design_tradeoffs",
                "information_requests",
                "action_invocations",
                "action_observations",
                "reference_cases",
                "conversation",
            ):
                context[key] = []
            context["counts"].update(
                {
                    "documents": 0,
                    "claims": 0,
                    "hypotheses": 0,
                    "scenarios": 0,
                    "source_systems": 0,
                    "semantic_mappings": 0,
                    "source_assets": 0,
                    "raw_batches": 0,
                    "materialization_runs": 0,
                    "semantic_datasets": 0,
                    "source_identities": 0,
                    "reference_cases": 0,
                    "meetings": 0,
                    "management_signals": 0,
                    "management_insights": 0,
                    "design_tradeoffs": 0,
                    "information_requests": 0,
                    "action_invocations": 0,
                    "action_observations": 0,
                }
            )
        return context

    @staticmethod
    def _task_route_name(manifest: dict[str, Any]) -> str | None:
        route = manifest.get("task_route")
        if isinstance(route, dict):
            name = route.get("route")
            return str(name) if name is not None else None
        return str(route) if route is not None else None

    def _apply_management_task_route_candidate(
        self,
        project_id: UUID,
        thread: AgentThreadRow,
        run: AgentRunRow,
        user_text: str,
        manifest: dict[str, Any],
    ) -> AgentRunView | None:
        """Validate a caller-supplied interpretation and persist a fail-closed route.

        This method performs no model call. The candidate is untrusted language
        interpretation only; entity, metric, snapshot and tool availability are
        resolved again against this project's database before routing.
        """
        raw_candidate = manifest.get("task_intent_candidate")
        try:
            candidate = TaskIntentCandidate.model_validate(raw_candidate)
        except ValidationError as exc:
            raise DomainError(
                "TASK_INTENT_CANDIDATE_INVALID",
                "任务分类结果无效；未读取管理观察库或潜在库。",
                status_code=422,
            ) from exc

        snapshot_id = manifest.get("query_snapshot_id")
        if not snapshot_id:
            raise DomainError(
                "TASK_ROUTE_SNAPSHOT_REQUIRED",
                "本次任务没有固定查询快照，无法安全路由。",
                status_code=409,
            )
        snapshot_service = QuerySnapshotService(self.session)
        snapshot = snapshot_service.require(project_id, snapshot_id)
        graph = snapshot_service.graph(project_id, UUID(str(snapshot_id)))
        project = self.portfolio.require_project(project_id)
        company = self.session.get(CompanyRow, project.company_id)

        ambiguities: list[str] = []
        if candidate.ambiguity_detected:
            ambiguities.append(candidate.ambiguity_explanation or "意图候选标记为歧义")
        ambiguities.extend(candidate.clarification_questions)
        if candidate.task_kind == "UNCLEAR" and not ambiguities:
            ambiguities.append("无法确定任务类型")
        unresolved_hints: list[str] = []
        if candidate.mentions.time_ranges:
            unresolved_hints.append("当前尚无可核验的时间范围筛选器；未按该时间范围查询")
            if candidate.task_kind != "COMPLEX_ANALYSIS":
                ambiguities.append(unresolved_hints[-1])
        if candidate.mentions.action_effects and candidate.task_kind != "ACTION_REQUEST":
            unresolved_hints.append("任务候选包含动作效果，但本轮不执行外部动作")
            if candidate.task_kind != "COMPLEX_ANALYSIS":
                ambiguities.append(unresolved_hints[-1])
        if snapshot.publication_id is None:
            ambiguities.append("当前项目没有已发布企业模型版本；管理端不会读取开发草稿")

        target_ids: list[str] = []
        entity_ids: list[str] = []
        source_record_refs: list[dict[str, Any]] = []
        source_field_keys: list[str] = []
        def source_identity_matches(mention: str) -> list[SourceIdentityRow]:
            normalized = mention.strip().casefold()
            if not normalized:
                return []
            exact = self.session.scalars(
                select(SourceIdentityRow)
                .where(
                    SourceIdentityRow.project_id == str(project_id),
                    func.lower(SourceIdentityRow.source_record_key) == normalized,
                )
                .order_by(SourceIdentityRow.updated_at.desc(), SourceIdentityRow.id)
                .limit(self.settings.agent_route_identity_candidates)
            ).all()
            if exact:
                return list(exact)
            # The fallback preserves the old "prefix/surrounding label" UX,
            # but the database first narrows candidates by project and key
            # length.  We never build a Python registry of every identity.
            candidates = self.session.scalars(
                select(SourceIdentityRow)
                .where(
                    SourceIdentityRow.project_id == str(project_id),
                    func.length(SourceIdentityRow.source_record_key) <= len(normalized),
                )
                .order_by(SourceIdentityRow.updated_at.desc(), SourceIdentityRow.id)
                .limit(self.settings.agent_route_identity_candidates)
            ).all()
            matches: list[SourceIdentityRow] = []
            for identity in candidates:
                source_key = identity.source_record_key.strip().casefold()
                if not source_key or source_key not in normalized:
                    continue
                if re.search(
                    rf"(?<![a-z0-9]){re.escape(source_key)}(?![a-z0-9])",
                    normalized,
                    flags=re.IGNORECASE,
                ):
                    matches.append(identity)
            return list({item.id: item for item in matches}.values())

        entities_by_name: dict[str, list[str]] = {}
        for entity in graph.entities:
            names = {entity.name.strip().casefold()}
            if entity.stable_key:
                names.add(entity.stable_key.strip().casefold())
            for name in names:
                if name:
                    entities_by_name.setdefault(name, []).append(str(entity.id))

        if candidate.mentions.targets:
            for mention in candidate.mentions.targets:
                normalized = mention.strip().casefold()
                if normalized in _SOURCE_QUERY_NOISE:
                    continue
                if company and normalized == company.name.strip().casefold():
                    target_ids.append(str(project.id))
                    continue
                if normalized == project.name.strip().casefold():
                    target_ids.append(str(project.id))
                    continue
                matches = list(dict.fromkeys(entities_by_name.get(normalized, [])))
                if len(matches) == 1:
                    target_ids.append(matches[0])
                    entity_ids.append(matches[0])
                elif len(matches) > 1:
                    message = f"目标“{mention}”对应多个企业对象"
                    if candidate.task_kind == "COMPLEX_ANALYSIS":
                        unresolved_hints.append(message)
                    else:
                        ambiguities.append(message)
                else:
                    source_matches = source_identity_matches(mention)
                    if len(source_matches) == 1:
                        identity = source_matches[0]
                        source_record_refs.append(
                            {
                                "source_asset": identity.source_asset,
                                "source_record_key": identity.source_record_key,
                                "source_identity_id": identity.id,
                                "identity_status": identity.status,
                                "target_type_key": identity.target_type_key,
                            }
                        )
                    elif len(source_matches) > 1:
                        message = f"来源记录“{mention}”对应多个来源身份，需要限定数据资产"
                        if candidate.task_kind == "COMPLEX_ANALYSIS":
                            unresolved_hints.append(message)
                        else:
                            ambiguities.append(message)
                    else:
                        message = f"目标“{mention}”无法与当前模型对象或来源记录精确匹配"
                        if candidate.task_kind == "COMPLEX_ANALYSIS":
                            unresolved_hints.append(message)
                        else:
                            ambiguities.append(message)
        else:
            target_ids.append(str(project.id))

        target_ids = list(dict.fromkeys(target_ids))
        target_resolution = {target_id: True for target_id in target_ids}

        source_observation_requested = _management_source_observation_requested(user_text)
        metric_ids: list[str] = []
        for mention in candidate.mentions.metrics:
            normalized = mention.strip().casefold()
            source_aliases = _SOURCE_FIELD_ALIASES.get(normalized)
            if source_aliases:
                source_field_keys.extend(source_aliases)
                continue
            if not normalized:
                continue
            metric_definitions = self.session.scalars(
                select(MetricDefinitionRow)
                .where(
                    MetricDefinitionRow.project_id == str(project_id),
                    MetricDefinitionRow.active.is_(True),
                    or_(
                        func.lower(MetricDefinitionRow.key) == normalized,
                        func.lower(MetricDefinitionRow.name) == normalized,
                    ),
                )
                .order_by(MetricDefinitionRow.updated_at.desc(), MetricDefinitionRow.id)
                .limit(20)
            ).all()
            matches = [
                row
                for row in metric_definitions
                if normalized in {row.key.strip().casefold(), row.name.strip().casefold()}
            ]
            if len(matches) == 1:
                metric_ids.append(matches[0].id)
            elif len(matches) > 1:
                message = f"指标“{mention}”对应多个定义"
                if candidate.task_kind == "COMPLEX_ANALYSIS":
                    unresolved_hints.append(message)
                else:
                    ambiguities.append(message)
            else:
                message = f"指标“{mention}”尚未注册或无法精确匹配"
                if candidate.task_kind == "COMPLEX_ANALYSIS":
                    unresolved_hints.append(message)
                else:
                    ambiguities.append(message)

        if metric_ids:
            snapshot_metric_ids = snapshot.manifest.get("metric_observation_ids", [])
            available_metric_ids = set(
                self.session.scalars(
                    select(MetricObservationRow.metric_definition_id).where(
                        MetricObservationRow.project_id == str(project_id),
                        MetricObservationRow.id.in_(snapshot_metric_ids),
                        MetricObservationRow.record_status == "ACTIVE",
                        MetricObservationRow.metric_definition_id.in_(metric_ids),
                    )
                ).all()
            )
            for metric_id in metric_ids:
                if metric_id not in available_metric_ids:
                    ambiguities.append(f"已注册指标“{metric_id}”在本次查询快照中没有有效数据")

        available_definitions = self.session.scalars(
            select(ActionDefinitionRow)
            .where(
                ActionDefinitionRow.project_id == str(project_id),
                ActionDefinitionRow.enabled.is_(True),
                ActionDefinitionRow.key.in_(tool_keys_for_agent(AgentKind.MANAGEMENT.value)),
            )
            .order_by(ActionDefinitionRow.key, ActionDefinitionRow.id)
            .limit(len(tool_keys_for_agent(AgentKind.MANAGEMENT.value)))
        ).all()
        registered_keys = {
            row.key
            for row in available_definitions
            if row.key in tool_keys_for_agent(AgentKind.MANAGEMENT.value)
        }

        candidate_tools: list[CandidateTool] = []
        actual_entity_targets = len(entity_ids) == 1 and len(target_ids) == 1
        company_scope_requested = (
            not actual_entity_targets
            and (
                (
                    candidate.task_kind == "SIMPLE_READ"
                    and (
                        not candidate.mentions.targets
                        or any(
                            marker in mention
                            for mention in candidate.mentions.targets
                            for marker in ("公司", "企业", "整体", "全局", "总体")
                        )
                    )
                )
                or (candidate.task_kind == "COMPLEX_ANALYSIS" and not entity_ids)
            )
        )
        if (
            candidate.task_kind == "SIMPLE_READ"
            and not actual_entity_targets
            and not company_scope_requested
            and not (source_record_refs or source_observation_requested)
        ):
            ambiguities.append(
                "简单查询需要一个已建模且精确唯一的企业对象；若要查公司概览，请明确说明公司整体"
            )
        if candidate.task_kind == "SIMPLE_READ" and (
            source_record_refs or source_observation_requested
        ):
            key = "read_source_observations"
            candidate_tools.append(
                CandidateTool(
                    key=key,
                    registered_template=key in registered_keys,
                    exact_match=True,
                    parameters_complete=True,
                    matches_task=True,
                    effect="READ:SOURCE_OBSERVATIONS",
                )
            )
        elif candidate.task_kind == "SIMPLE_READ" and company_scope_requested:
            key = "read_enterprise_summary"
            candidate_tools.append(
                CandidateTool(
                    key=key,
                    registered_template=key in registered_keys,
                    exact_match=True,
                    parameters_complete=True,
                    matches_task=True,
                    effect="READ:ENTERPRISE_SUMMARY",
                )
            )
        elif candidate.task_kind == "SIMPLE_READ" and actual_entity_targets:
            key = "read_graph_neighborhood"
            candidate_tools.append(
                CandidateTool(
                    key=key,
                    registered_template=key in registered_keys,
                    exact_match=True,
                    parameters_complete=True,
                    matches_task=True,
                    effect="READ:GRAPH_NEIGHBORHOOD",
                )
            )
        elif candidate.task_kind == "COMPLEX_ANALYSIS":
            allowed_complex_readers = {
                "read_graph_neighborhood",
                "read_source_observations",
                "search_management_observations",
                "search_potential_records",
                "work_observation.read",
                "work_observation.compare",
            }
            candidate_tools.extend(
                CandidateTool(
                    key=key,
                    registered_template=True,
                    exact_match=False,
                    parameters_complete=True,
                    matches_task=True,
                )
                for key in sorted(registered_keys & allowed_complex_readers)
            )

        if candidate.task_kind in {"ACTION_REQUEST", "OBSERVATION_INPUT"}:
            action_boundary = (
                "此消息属于管理输入，应通过管理输入 Agent 写入独立观察库；"
                "本次输出端对话未保存为观察。"
                if candidate.task_kind == "OBSERVATION_INPUT"
                else "动作尚未精确解析为已注册模板和完整参数；本次未执行任何动作。"
            )
            ambiguities.append(action_boundary)

        if candidate.task_kind in {"ACTION_REQUEST", "OBSERVATION_INPUT", "UNCLEAR"}:
            candidate_tools = []

        is_complex = candidate.task_kind == "COMPLEX_ANALYSIS"
        spec = TaskSpec(
            goal=user_text,
            company_id=str(project.company_id),
            project_id=str(project.id),
            model_release_id=snapshot.publication_id,
            intent=candidate.task_kind,
            targets=tuple(target_ids),
            target_resolution=target_resolution,
            metric_ids=tuple(metric_ids),
            registered_metric_ids=tuple(metric_ids),
            candidate_tools=tuple(candidate_tools),
            ambiguities=tuple(dict.fromkeys(ambiguities)),
            explicit_exploration=(
                is_complex
                or candidate.signals.explicit_exploration
                or candidate.signals.requires_role_field_detail
            ),
            requires_causal_explanation=candidate.signals.requires_causal_explanation,
            requires_tradeoff=candidate.signals.requires_tradeoff,
            requires_unstructured_cross_store=(
                candidate.signals.requires_unstructured_cross_store
                or bool(manifest.get("attachment_ids"))
            ),
            published_model_available=snapshot.publication_id is not None,
            requires_published_model=True,
            requires_formal_data=False,
            company_scope_read=company_scope_requested,
            source_record_query=bool(source_record_refs or source_observation_requested),
        )
        decision = route_task(spec)
        stores = ["formal_query_snapshot"]
        if source_record_refs or source_observation_requested:
            stores.append("source_observations")
        if decision.route == TaskRoute.COMPLEX:
            if self.observation_database is not None:
                stores.append("management_observations")
            if self.potential_database is not None:
                stores.append("potential_records")
            if manifest.get("attachment_ids"):
                stores.append("source_materials")
        route_payload = {
            "route": decision.route.value,
            "task_kind": candidate.task_kind,
            "rule_version": "task-routing-v1",
            "explanation": decision.explanation,
            "rule_hits": [
                {
                    "rule_id": item.rule_id,
                    "outcome": item.outcome.value,
                    "evidence": list(item.evidence),
                }
                for item in decision.rule_hits
            ],
            "missing_items": list(decision.missing_items),
            "boundaries": list(decision.boundaries),
            "target_ids": target_ids,
            "source_record_refs": source_record_refs,
            "source_field_keys": list(dict.fromkeys(source_field_keys)),
            "source_observation_requested": source_observation_requested,
            "unresolved_mentions": list(dict.fromkeys(unresolved_hints)),
            "metric_ids": metric_ids,
            "query_snapshot_id": snapshot.id,
            "selected_stores": stores,
            "model_access": decision.model_access.value,
            "execution_authorized": False,
            "classification_source": (
                (manifest.get("task_classification") or {}).get("source")
                or "caller_supplied_untrusted_candidate"
            ),
        }
        manifest["task_route"] = route_payload
        manifest["route_spec"] = {
            "goal": user_text,
            "target_ids": target_ids,
            "source_record_refs": source_record_refs,
            "source_field_keys": list(dict.fromkeys(source_field_keys)),
            "source_record_query": bool(source_record_refs or source_observation_requested),
            "source_observation_requested": source_observation_requested,
            "unresolved_mentions": list(dict.fromkeys(unresolved_hints)),
            "metric_ids": metric_ids,
            "query_snapshot_id": snapshot.id,
            "company_scope_read": company_scope_requested,
            "candidate": candidate.model_dump(mode="json"),
        }
        self._record_step(
            project_id,
            run,
            kind="ROUTE",
            status="SUCCEEDED",
            input_payload={
                "candidate": candidate.model_dump(mode="json"),
                "project_id": str(project.id),
                "query_snapshot_id": snapshot.id,
            },
            output_payload=route_payload,
        )

        if decision.route == TaskRoute.NEEDS_INPUT:
            content = decision.explanation
            if candidate.clarification_questions:
                content += "\n\n请补充：\n" + "\n".join(
                    f"- {question}" for question in candidate.clarification_questions
                )
            if candidate.task_kind == "ACTION_REQUEST":
                content += "\n\n任务分类不等于执行授权；未调用任何动作。"
            if candidate.task_kind == "OBSERVATION_INPUT":
                content += "\n\n请切换到管理输入 Agent 提交这条信息。"
            output = AgentStructuredOutput(content=content)
            manifest["route_result"] = "NEEDS_INPUT"
            manifest["external_model_used"] = bool(
                (manifest.get("task_classification") or {}).get("external_model_used", False)
            )
            return self._finish_completed(run, thread, output, manifest)

        run.context_manifest = json_ready(manifest)
        self.session.flush()
        return None

    @staticmethod
    def _merge_query_manifest(
        previous: dict[str, Any] | None,
        context: dict[str, Any],
        *,
        model_context_shared: bool = False,
    ) -> dict[str, Any]:
        """Persist each actual cross-store read set and its visible coverage."""
        old = previous if isinstance(previous, dict) else {}
        old_read_sets = old.get("read_sets")
        read_sets = list(old_read_sets) if isinstance(old_read_sets, list) else []
        management = context.get("management_context") or {}
        baseline = context.get("project", {}).get("model_baseline") or {}
        coverage = management.get("coverage", {})
        if not isinstance(coverage, dict):
            coverage = {}
        formal_coverage = {
            key: context[key]
            for key in ("counts", "graph_coverage", "material_coverage")
            if isinstance(context.get(key), (dict, list))
        }
        if formal_coverage:
            coverage = {**coverage, "formal_context": formal_coverage}
        tool_scans = AgentRuntimeService._tool_scan_coverage(
            (context.get("agent_execution") or {}).get("tool_results")
        )
        if tool_scans:
            coverage = {**coverage, "tool_scans": tool_scans}
        current = {
            "recorded_at": now_utc().isoformat(),
            "query_snapshot_id": baseline.get("query_snapshot_id"),
            "route": AgentRuntimeService._task_route_name(
                {"task_route": context.get("task_route")}
            ),
            "selected_stores": (context.get("task_route") or {}).get("selected_stores", []),
            "management_context_access": context.get("management_context_access", {}),
            "management_observation_refs": management.get("read_manifest", {}).get(
                "management_observation_refs", []
            ),
            "potential_record_refs": management.get("read_manifest", {}).get(
                "potential_record_refs", []
            ),
            "coverage": coverage,
            "model_context_shared": model_context_shared,
            "read_reference_uris": sorted(
                AgentRuntimeService._claim_allowed_references(
                    context, references_visible=True
                )
            ),
        }
        current["model_visible_reference_uris"] = (
            current["read_reference_uris"] if model_context_shared else []
        )
        if current not in read_sets:
            read_sets.append(current)
        return {"read_sets": read_sets}

    def _enqueue_query_manifest_read_sets(
        self,
        run: AgentRunRow,
        previous: dict[str, Any] | None,
        updated: dict[str, Any],
    ) -> None:
        """Write newly appended read sets to the formal-store outbox transactionally."""
        old_sets = previous.get("read_sets") if isinstance(previous, dict) else None
        old_sets = old_sets if isinstance(old_sets, list) else []
        read_sets = updated.get("read_sets")
        if not isinstance(read_sets, list) or len(read_sets) < len(old_sets):
            raise DomainError(
                "QUERY_READSET_OUTBOX_INVALID_TRANSITION",
                "查询读集只能追加，不能缩短；本轮运行已停止。",
                status_code=409,
            )
        for position, prior in enumerate(old_sets):
            if position >= len(read_sets) or read_sets[position] != prior:
                raise DomainError(
                    "QUERY_READSET_OUTBOX_INVALID_TRANSITION",
                    "已有查询读集不可被改写；本轮运行已停止。",
                    status_code=409,
                )
        for ordinal, raw_read_set in enumerate(read_sets[len(old_sets) :], start=len(old_sets) + 1):
            if not isinstance(raw_read_set, dict):
                raise DomainError(
                    "QUERY_READSET_OUTBOX_INVALID_TRANSITION",
                    "新增查询读集格式无效；本轮运行已停止。",
                    status_code=409,
                )
            payload = {"read_set": json_ready(raw_read_set)}
            payload_sha256 = hashlib.sha256(
                self._canonical_candidate_json(payload).encode("utf-8")
            ).hexdigest()
            event_id = hashlib.sha256(f"{run.id}:{ordinal}".encode()).hexdigest()
            existing = self.session.get(AgentReadSetOutboxRow, event_id)
            if existing is not None:
                if existing.payload_sha256 != payload_sha256:
                    raise DomainError(
                        "QUERY_READSET_OUTBOX_IMMUTABLE_CONFLICT",
                        "同一查询读集 outbox 事件内容不一致，需要人工核对。",
                        status_code=409,
                    )
                continue
            self.session.add(
                AgentReadSetOutboxRow(
                    event_id=event_id,
                    run_id=run.id,
                    ordinal=ordinal,
                    payload=payload,
                    payload_sha256=payload_sha256,
                    created_at=now_utc(),
                    attempt_count=0,
                )
            )

    def _management_context(self, project: Any, query: str | None) -> dict[str, Any]:
        """Read the two deliberately separate management stores within project scope.

        The formal projection remains in the primary database. Daily observations and
        human-confirmed potential records are presented under explicit trust labels and
        never copied into the formal graph.
        """
        observation_total = 0
        observations: list[dict[str, Any]] = []
        if self.observation_database is not None:
            with self.observation_database.session_factory() as session:
                base = select(ManagementObservationRow).where(
                    ManagementObservationRow.company_id == project.company_id,
                    ManagementObservationRow.project_id == project.id,
                    ManagementObservationRow.status == "ACTIVE",
                )
                observation_total = session.scalar(
                    select(func.count()).select_from(base.subquery())
                ) or 0
                rows = list(session.scalars(
                    base.order_by(
                        ManagementObservationRow.created_at.desc(),
                        ManagementObservationRow.id,
                    ).limit(200)
                ).all())
            ranked_rows = self._rank_context_items(
                rows,
                query,
                lambda item: f"{item.kind} {item.title} {item.content}",
            )
            observations = [
                {
                    "id": item.id,
                    "kind": item.kind,
                    "title": item.title,
                    "content_excerpt": item.content[:900],
                    "content_truncated": len(item.content) > 900,
                    "occurred_at": item.occurred_at,
                    "submitted_by": item.submitted_by,
                    "revision": item.revision,
                    "created_at": item.created_at,
                    "trust": "UNVERIFIED_MANAGEMENT_OBSERVATION",
                    "source_store": "management_observations",
                }
                for item in ranked_rows[:12]
            ]

        potential_total = 0
        potential_records: list[dict[str, Any]] = []
        if self.potential_database is not None:
            page = PotentialRecordService(self.potential_database).list_records(
                company_id=UUID(project.company_id),
                project_id=UUID(project.id),
                include_history=False,
                limit=200,
            )
            potential_total = page.total
            ranked_records = self._rank_context_items(
                page.items,
                query,
                lambda item: self._search_text(item.model_dump(mode="json")),
            )
            potential_records = [
                {
                    "id": str(item.id),
                    "potential_type": item.potential_type.value,
                    "claim": item.claim[:1_000],
                    "claim_truncated": len(item.claim) > 1_000,
                    "applicability_scope": item.applicability_scope,
                    "task_source": item.task_source,
                    "supporting_evidence": [
                        evidence.model_dump(mode="json")
                        for evidence in item.supporting_evidence[:5]
                    ],
                    "counterevidence": [
                        evidence.model_dump(mode="json") for evidence in item.counterevidence[:5]
                    ],
                    "evidence_status": item.evidence_status.value,
                    "version": item.version,
                    "updated_at": item.updated_at,
                    "trust": "HUMAN_CONFIRMED_UNVERIFIED_POTENTIAL",
                    "source_store": "potential_records",
                }
                for item in ranked_records[:12]
            ]

        return {
            "trust_boundary": {
                "formal_model": (
                    "正式模型与经校验的信息系统数据；"
                    "本段没有把其它库的内容提升为正式事实。"
                ),
                "management_observations": "变化快、可信度较低的日常输入，仅作待核查线索。",
                "potential_records": (
                    "经人确认但仍未验证的潜在判断；"
                    "可信度高于观察库、低于正式企业模型。"
                ),
            },
            "coverage": {
                "observations": {
                    "available": observation_total,
                    "included": len(observations),
                    "truncated": observation_total > len(observations),
                    "store_available": self.observation_database is not None,
                },
                "potential_records": {
                    "available": potential_total,
                    "included": len(potential_records),
                    "truncated": potential_total > len(potential_records),
                    "store_available": self.potential_database is not None,
                },
            },
            "management_observations": observations,
            "human_confirmed_potential_records": potential_records,
            "read_manifest": {
                "management_observation_refs": [
                    {"id": item["id"], "revision": item["revision"]}
                    for item in observations
                ],
                "potential_record_refs": [
                    {"id": item["id"], "version": item["version"]}
                    for item in potential_records
                ],
            },
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
        allowed = tool_keys_for_agent(run.agent_kind)
        definitions = {
            item.key: item
            for item in self.session.scalars(
                select(ActionDefinitionRow)
                .where(
                    ActionDefinitionRow.project_id == str(project_id),
                    ActionDefinitionRow.key.in_(allowed),
                )
                .order_by(ActionDefinitionRow.key, ActionDefinitionRow.id)
                .limit(len(allowed))
            ).all()
        }
        route_name = self._task_route_name(run.context_manifest or {})
        route_spec = (
            (run.context_manifest or {}).get("route_spec")
            if isinstance((run.context_manifest or {}).get("route_spec"), dict)
            else {}
        )
        company_scope_read = bool(route_spec.get("company_scope_read"))
        invocation_ids: list[str] = []
        notes: list[str] = []
        for index, proposal in enumerate(proposals[:20]):
            if proposal.action_key not in allowed:
                notes.append(f"已拒绝越权动作 {proposal.action_key}")
                continue
            if route_name == "SIMPLE" and proposal.action_key not in _SIMPLE_READ_ACTIONS:
                notes.append(f"简单查询路由不允许创建写入或管理动作 {proposal.action_key}")
                continue
            if (
                route_name == "SIMPLE"
                and proposal.action_key == "read_enterprise_summary"
                and not company_scope_read
            ):
                notes.append("实体级简单查询不允许创建公司级概览动作")
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

    def _ensure_required_management_reads(
        self,
        project_id: UUID,
        run: AgentRunRow,
        user_content: str,
        output: AgentStructuredOutput,
        execution: dict[str, Any],
        manifest: dict[str, Any],
    ) -> AgentStructuredOutput:
        """Add a deterministic ERP read when a complex request explicitly asks for it.

        The language model may choose the order of relevant read tools, but it
        must not be able to silently omit an explicitly requested source store.
        This adds only a read-only, schema-validated proposal and never changes
        the model, ontology, identity bindings, or external systems.
        """

        if (
            not self._is_complex_management_output(run)
            or not _management_source_observation_requested(user_content)
        ):
            return output
        proposals = list(output.action_proposals)
        if any(item.action_key == "read_source_observations" for item in proposals):
            return output
        for item in execution.get("tool_results", []):
            invocation_id = item.get("invocation_id") if isinstance(item, dict) else None
            if not invocation_id:
                continue
            invocation = self.session.get(ActionInvocationRow, str(invocation_id))
            if (
                invocation is not None
                and self._action_key(invocation) == "read_source_observations"
            ):
                return output

        route_spec = manifest.get("route_spec")
        route_spec = route_spec if isinstance(route_spec, dict) else {}
        source_record_keys = [
            str(item.get("source_record_key"))
            for item in route_spec.get("source_record_refs", [])
            if isinstance(item, dict) and item.get("source_record_key")
        ]
        field_keys = [
            str(item)
            for item in route_spec.get("source_field_keys", [])
            if str(item).strip()
        ]
        proposal = AgentActionProposal(
            action_key="read_source_observations",
            input={
                "source_record_keys": list(dict.fromkeys(source_record_keys)),
                "field_keys": list(dict.fromkeys(field_keys)),
                "limit": 100,
            },
            reason="用户明确要求读取 ERP 来源观测；程序强制补齐只读证据步骤。",
        )
        self._record_step(
            project_id,
            run,
            kind="REQUIRED_READ",
            status="SUCCEEDED",
            input_payload={"resource": "SOURCE_OBSERVATIONS"},
            output_payload={
                "action_key": proposal.action_key,
                "reason": proposal.reason,
                "source_record_keys": proposal.input.get("source_record_keys", []),
                "field_keys": proposal.input.get("field_keys", []),
            },
        )
        return output.model_copy(update={"action_proposals": [*proposals, proposal]})

    @staticmethod
    def _is_complex_management_output(run: AgentRunRow) -> bool:
        manifest = run.context_manifest or {}
        route = manifest.get("task_route")
        return (
            run.agent_kind == AgentKind.MANAGEMENT.value
            and isinstance(route, dict)
            and route.get("route") == TaskRoute.COMPLEX.value
            and route.get("task_kind") == "COMPLEX_ANALYSIS"
        )

    @staticmethod
    def _canonical_candidate_json(payload: dict[str, Any]) -> str:
        return json.dumps(
            payload,
            ensure_ascii=False,
            sort_keys=True,
            separators=(",", ":"),
        )

    @staticmethod
    def _parse_observation_source_ref(source_ref: str) -> tuple[UUID, int] | None:
        match = re.fullmatch(r"observation://([^/]+)/([1-9][0-9]*)", source_ref)
        if match is None:
            return None
        try:
            observation_id = UUID(match.group(1))
            revision = int(match.group(2))
        except (ValueError, OverflowError):
            return None
        if str(observation_id) != match.group(1).lower():
            return None
        return observation_id, revision

    def _validate_potential_candidate_evidence(
        self,
        candidate: PotentialCandidateDraft,
        *,
        company_id: str,
        project_id: str,
    ) -> str | None:
        if self.observation_database is None:
            return "OBSERVATION_STORE_UNAVAILABLE"
        try:
            with self.observation_database.session_factory() as observation_session:
                for evidence in [*candidate.supporting_evidence, *candidate.counterevidence]:
                    parsed_ref = self._parse_observation_source_ref(evidence.source_ref)
                    if parsed_ref is None:
                        return "INVALID_OBSERVATION_SOURCE_REF"
                    observation_id, revision = parsed_ref
                    version = observation_session.scalar(
                        select(ObservationVersionRow)
                        .join(
                            ManagementObservationRow,
                            ManagementObservationRow.id
                            == ObservationVersionRow.observation_id,
                        )
                        .where(
                            ObservationVersionRow.observation_id == str(observation_id),
                            ObservationVersionRow.revision == revision,
                            ManagementObservationRow.company_id == company_id,
                            ManagementObservationRow.project_id == project_id,
                            ManagementObservationRow.status == "ACTIVE",
                            ManagementObservationRow.revision == revision,
                        )
                    )
                    snapshot = version.snapshot if version is not None else None
                    source_text = snapshot.get("content") if isinstance(snapshot, dict) else None
                    if (
                        version is None
                        or not isinstance(snapshot, dict)
                        or snapshot.get("status") != "ACTIVE"
                        or not isinstance(source_text, str)
                        or evidence.excerpt not in source_text
                    ):
                        return "SOURCE_VERSION_OR_EXCERPT_INVALID"
        except Exception:
            return "SOURCE_VALIDATION_UNAVAILABLE"
        return None

    def _persist_potential_candidate_proposals(
        self,
        project_id: UUID,
        run: AgentRunRow,
        proposals: list[dict[str, Any]],
    ) -> None:
        """Append verified proposal envelopes without writing PotentialRecord rows."""
        if not proposals or not self._is_complex_management_output(run):
            return

        project = self.portfolio.require_project(project_id)
        expected_company_id = str(project.company_id)
        expected_project_id = str(project_id)
        existing_candidate_ids: set[str] = set()
        existing_steps = self.session.scalars(
            select(AgentStepRow).where(
                AgentStepRow.run_id == run.id,
                AgentStepRow.kind == "POTENTIAL_CANDIDATE_PROPOSED",
            )
        ).all()
        for step in existing_steps:
            existing = (step.output_payload or {}).get("candidate")
            if isinstance(existing, dict) and existing.get("candidate_id"):
                existing_candidate_ids.add(str(existing["candidate_id"]))

        accepted_count = 0
        rejection_counts: dict[str, int] = {}

        def reject(reason: str) -> None:
            rejection_counts[reason] = rejection_counts.get(reason, 0) + 1

        for raw_candidate in proposals:
            try:
                candidate = PotentialCandidateDraft.model_validate(raw_candidate)
            except (ValidationError, ValueError, TypeError):
                reject("INVALID_CANDIDATE_SCHEMA")
                continue

            if (
                str(candidate.company_id) != expected_company_id
                or str(candidate.project_id) != expected_project_id
            ):
                reject("SCOPE_MISMATCH")
                continue
            if not candidate.supporting_evidence:
                reject("SUPPORTING_EVIDENCE_REQUIRED")
                continue

            evidence_error = self._validate_potential_candidate_evidence(
                candidate,
                company_id=expected_company_id,
                project_id=expected_project_id,
            )
            if evidence_error is not None:
                reject(evidence_error)
                continue

            identity_payload = candidate.model_dump(mode="json")
            identity_payload.pop("candidate_id", None)
            identity_hash = hashlib.sha256(
                self._canonical_candidate_json(identity_payload).encode("utf-8")
            ).hexdigest()
            stable_candidate_id = uuid5(
                NAMESPACE_URL,
                f"enterprise-insight:potential-candidate:{run.id}:{identity_hash}",
            )
            candidate = candidate.model_copy(update={"candidate_id": stable_candidate_id})
            if str(candidate.candidate_id) in existing_candidate_ids:
                reject("DUPLICATE_CANDIDATE")
                continue

            candidate_payload = candidate.model_dump(mode="json")
            candidate_hash = hashlib.sha256(
                self._canonical_candidate_json(candidate_payload).encode("utf-8")
            ).hexdigest()
            self._record_step(
                project_id,
                run,
                kind="POTENTIAL_CANDIDATE_PROPOSED",
                status="SUCCEEDED",
                input_payload={"candidate_id": str(candidate.candidate_id)},
                output_payload={
                    "candidate": candidate_payload,
                    "candidate_hash": candidate_hash,
                },
            )
            existing_candidate_ids.add(str(candidate.candidate_id))
            accepted_count += 1

        rejected_count = sum(rejection_counts.values())
        if rejected_count:
            self._record_step(
                project_id,
                run,
                kind="POTENTIAL_CANDIDATE_VALIDATION",
                status="COMPLETED",
                input_payload={"proposal_count": len(proposals)},
                output_payload={
                    "accepted_count": accepted_count,
                    "rejected_count": rejected_count,
                    "rejection_counts": rejection_counts,
                },
            )

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
            # “员工” describes a subject of many surveys, not evidence that
            # an HR department exists.  Keeping it as an alias caused the
            # deterministic fallback to invent 人力资源 for unrelated text.
            "people": ("人力", "人事", "招聘", "培训"),
            "customer_service": ("客服", "客户服务", "投诉"),
            "operations": ("运营", "经营管理"),
            "legal": ("法务", "合规", "合同"),
            # “数据” is deliberately not a department signal; it appears in
            # almost every enterprise document and must not create an IT unit.
            "technology": ("信息化", "信息系统", "IT"),
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
