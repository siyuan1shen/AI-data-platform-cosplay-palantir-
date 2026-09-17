from __future__ import annotations

import json
import mimetypes
from datetime import datetime
from hashlib import sha256
from typing import Annotated, Any, cast
from urllib.parse import quote
from uuid import NAMESPACE_URL, UUID, uuid4, uuid5

from fastapi import APIRouter, Depends, File, Form, Query, Request, UploadFile, status
from fastapi.responses import FileResponse, Response
from sqlalchemy import select
from sqlalchemy.orm import Session

from enterprise_insight_backend.actions import ActionService
from enterprise_insight_backend.agent_runtime import AgentRuntimeService
from enterprise_insight_backend.changes import ChangeSetService
from enterprise_insight_backend.collaboration import CollaborationService, ExplorationService
from enterprise_insight_backend.config import Settings
from enterprise_insight_backend.control import (
    ControlClaimLedgerPage,
    ControlClaimLedgerService,
    ControlEvidenceReadPage,
    ControlExecutionExplanation,
    ControlIndexService,
    ControlPotentialCandidateAcceptance,
    ControlPotentialCandidateDecision,
    ControlPotentialCandidateEdit,
    ControlPotentialCandidateEventPage,
    ControlPotentialCandidatePage,
    ControlPotentialCandidateService,
    ControlPotentialCandidateStatus,
    ControlPotentialCandidateView,
    ControlQueryManifestPage,
    ControlTaskRoutePage,
    ControlTaskRouteView,
    validate_candidate_observation_sources,
)
from enterprise_insight_backend.dependencies import (
    get_control_session,
    get_observation_session,
    get_session,
)
from enterprise_insight_backend.errors import DomainError, ErrorResponse
from enterprise_insight_backend.evaluation import EvaluationService
from enterprise_insight_backend.evidence import EvidenceService
from enterprise_insight_backend.exporting import ExportService
from enterprise_insight_backend.input_agent import ManagementInputAgent
from enterprise_insight_backend.integration import IntegrationService
from enterprise_insight_backend.learning import LearningCaseService
from enterprise_insight_backend.lifecycle import LifecycleService
from enterprise_insight_backend.management_actions import ManagementActionService
from enterprise_insight_backend.management_intelligence import ManagementIntelligenceService
from enterprise_insight_backend.migration import DATABASE_SCHEMA_REVISION
from enterprise_insight_backend.model_profiles import ModelProfileService
from enterprise_insight_backend.models import AgentMessageRow, AgentRunRow
from enterprise_insight_backend.observation_conflicts import ObservationConflictService
from enterprise_insight_backend.observations import (
    ObservationAuditRow,
    ObservationExtractionRow,
    ObservationService,
    ObservationVersionRow,
)
from enterprise_insight_backend.ontology import OntologyService
from enterprise_insight_backend.parsers import MAX_IMPORT_BYTES, ParsedDocument, parse_import
from enterprise_insight_backend.portfolio import PortfolioService
from enterprise_insight_backend.potential import (
    PotentialHistoryView,
    PotentialPage,
    PotentialRecordCreate,
    PotentialRecordService,
    PotentialRecordStatusRequest,
    PotentialRecordUpdate,
    PotentialRecordView,
)
from enterprise_insight_backend.projection import ProjectionService
from enterprise_insight_backend.publication import PublicationService
from enterprise_insight_backend.query_snapshots import QuerySnapshotService
from enterprise_insight_backend.restoring import MAX_RESTORE_PACKAGE_BYTES, RestoreService
from enterprise_insight_backend.scenario_engine import ScenarioRunService
from enterprise_insight_backend.schemas import (
    ActionApprovalRequest,
    ActionDefinitionCreate,
    ActionDefinitionUpdate,
    ActionDefinitionView,
    ActionInvocationCreate,
    ActionInvocationStatus,
    ActionInvocationView,
    ActionLogView,
    ActionObservationCreate,
    ActionObservationView,
    ActionRetryRequest,
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
    CapabilityManifest,
    CapabilityView,
    CausalHypothesisCreate,
    CausalHypothesisView,
    ChangePreview,
    ChangeSetCreate,
    ChangeSetView,
    ClaimView,
    CompanyCreate,
    CompanyUpdate,
    CompanyView,
    DesignTradeoffCreate,
    DesignTradeoffUpdate,
    DesignTradeoffView,
    EntityCreate,
    EntityUpdate,
    EntityView,
    EvaluationCaseCreate,
    EvaluationCaseView,
    EvaluationExecuteCreate,
    EvaluationExecutionView,
    EvaluationResultView,
    EvaluationRunCreate,
    EvaluationRunView,
    EvaluationSuiteCreate,
    EvaluationSuiteUpdate,
    EvaluationSuiteView,
    EventCreate,
    EventView,
    EvidenceFragmentView,
    ExecutiveContextView,
    ExplorationModuleView,
    ExportJobView,
    ExportRequest,
    GraphQuery,
    GraphView,
    HealthView,
    HypothesisCreate,
    HypothesisFeedbackCreate,
    HypothesisFeedbackView,
    HypothesisView,
    ImportConfirmRequest,
    ImportKind,
    ImportPreviewView,
    ImportResultView,
    InformationRequestCreate,
    InformationRequestUpdate,
    InformationRequestView,
    LearningCaseCatalogView,
    LearningCaseCreate,
    LearningCaseDraftFromActionCreate,
    LearningCaseDraftFromScenarioCreate,
    LearningCaseUpdate,
    LearningCaseView,
    LifecycleCleanupRequest,
    LifecycleCleanupResult,
    LifecycleDeleteRequest,
    LifecyclePolicyView,
    LifecyclePreviewView,
    LifecycleResourceKind,
    ManagementActionCreate,
    ManagementActionEventCreate,
    ManagementActionEventView,
    ManagementActionRevisionRequest,
    ManagementActionUpdate,
    ManagementActionVerifyDone,
    ManagementActionView,
    ManagementAnalysisRequest,
    ManagementAnalysisRunView,
    ManagementInsightUpdate,
    ManagementInsightView,
    ManagementIssueFeedbackView,
    ManagementIssueReopen,
    ManagementIssueView,
    ManagementSignalSide,
    ManagementSignalView,
    MaterializationRunView,
    MeetingRecordCreate,
    MeetingRecordUpdate,
    MeetingRecordView,
    MetricDefinitionCreate,
    MetricDefinitionUpdate,
    MetricDefinitionView,
    MetricObservationCreate,
    MetricObservationRetire,
    MetricObservationUpdate,
    MetricObservationView,
    ModelProfileCreate,
    ModelProfileTestView,
    ModelProfileUpdate,
    ModelProfileView,
    ObservationAssertionView,
    ObservationAttachmentView,
    ObservationConflictResolve,
    ObservationConflictStatus,
    ObservationConflictView,
    ObservationCreate,
    ObservationExtractionHistoryView,
    ObservationExtractionItem,
    ObservationExtractionReviewCreate,
    ObservationExtractionReviewDecision,
    ObservationExtractionReviewHistoryView,
    ObservationExtractionReviewView,
    ObservationExtractionView,
    ObservationExtractRequest,
    ObservationHistoryView,
    ObservationIngestionView,
    ObservationUpdate,
    ObservationView,
    OntologyReleaseCreate,
    OntologyReleaseView,
    OntologyTypeCreate,
    OntologyTypeUpdate,
    OntologyTypeView,
    Page,
    ProjectCreate,
    ProjectUpdate,
    ProjectView,
    PublicationCreate,
    PublicationView,
    QuerySnapshotCreate,
    QuerySnapshotView,
    RawBatchView,
    RawRecordView,
    RelationCreate,
    RelationUpdate,
    RelationView,
    RestoreConfirmRequest,
    RestorePreviewView,
    RestoreResultView,
    RevisionRequest,
    ScenarioCompareRequest,
    ScenarioComparisonView,
    ScenarioCreate,
    ScenarioDiffView,
    ScenarioRevisionRequest,
    ScenarioRunCompareRequest,
    ScenarioRunComparisonView,
    ScenarioRunView,
    ScenarioSimulationRequest,
    ScenarioUpdate,
    ScenarioView,
    SemanticDatasetCreate,
    SemanticDatasetExport,
    SemanticDatasetQuery,
    SemanticDatasetResult,
    SemanticDatasetUpdate,
    SemanticDatasetView,
    SemanticMappingCommand,
    SemanticMappingCreate,
    SemanticMappingSuggestionPage,
    SemanticMappingSuggestionRequest,
    SemanticMappingView,
    SemanticRelationMappingCommand,
    SemanticRelationMappingCreate,
    SemanticRelationMappingView,
    SourceAssetCreate,
    SourceAssetUpdate,
    SourceAssetView,
    SourceConnectorExtractRequest,
    SourceConnectorExtractView,
    SourceConnectorSyncRequest,
    SourceConnectorSyncView,
    SourceDocumentView,
    SourceIdentityBind,
    SourceIdentityView,
    SourceSystemCreate,
    SourceSystemTestView,
    SourceSystemUpdate,
    SourceSystemView,
    TypeKind,
)
from enterprise_insight_backend.semantic_datasets import SemanticDatasetService
from enterprise_insight_backend.service_utils import now_utc


def _render_management_input_extraction(row: ObservationExtractionRow) -> str:
    if row.status != "COMPLETED":
        return "管理输入 Agent 未能整理这条材料；原始内容仍保存在管理观察库。"
    lines = ["管理输入 Agent 整理草稿（尚未人工确认）："]
    for item in row.items:
        speaker = f"；说话者：{item.get('speaker')}" if item.get("speaker") else ""
        time_expression = (
            f"；时间：{item.get('time_expression')}" if item.get("time_expression") else ""
        )
        lines.append(
            f"- [{item.get('kind', 'STATEMENT')}] {item.get('statement', '')}"
            f"（原文：{item.get('supporting_quote', '')}{speaker}{time_expression}）"
        )
    if row.unresolved:
        lines.append("待澄清：" + "；".join(row.unresolved))
    lines.append("请在观察记录中确认、修订或拒绝；草稿不会自动进入正式模型或潜在库。")
    return "\n".join(lines)


async def _read_upload_with_limit(
    file: UploadFile,
    *,
    max_bytes: int | None = None,
    error_code: str = "IMPORT_FILE_TOO_LARGE",
    error_message: str = "单个材料暂时不能超过 25MB。",
) -> bytes:
    """Read an upload in bounded chunks before handing it to a parser."""

    if max_bytes is None:
        max_bytes = MAX_IMPORT_BYTES
    chunks: list[bytes] = []
    total = 0
    chunk_size = 1024 * 1024
    while total <= max_bytes:
        remaining = max_bytes - total + 1
        chunk = await file.read(min(chunk_size, remaining))
        if not chunk:
            break
        chunks.append(chunk)
        total += len(chunk)
        if total > max_bytes:
            raise DomainError(
                error_code,
                error_message,
                status_code=413,
            )
    return b"".join(chunks)


def _safe_upload_name(value: str) -> str:
    name = value.replace("\\", "/").rsplit("/", 1)[-1].strip()
    if not name or name in {".", ".."}:
        raise DomainError("OBSERVATION_FILE_NAME_INVALID", "文件名无效。", status_code=422)
    return name[:500]


def _import_kind_for_name(file_name: str) -> ImportKind:
    suffix = file_name.rsplit(".", 1)[-1].lower() if "." in file_name else ""
    supported = {
        "csv": ImportKind.CSV,
        "xlsx": ImportKind.XLSX,
        "docx": ImportKind.DOCX,
        "pdf": ImportKind.PDF,
        "txt": ImportKind.TXT,
        "json": ImportKind.JSON,
    }
    kind = supported.get(suffix)
    if kind is None:
        raise DomainError(
            "OBSERVATION_FILE_TYPE_UNSUPPORTED",
            "日常信息支持 CSV、XLSX、DOCX、可提取文字的 PDF、TXT 和 JSON。",
            status_code=422,
        )
    return kind


def _render_observation_import(
    parsed: ParsedDocument, *, limit: int
) -> tuple[str, bool]:
    """Create a bounded readable view while retaining the exact original upload."""

    segments: list[str] = []
    size = 0
    truncated = False
    if parsed.document_like:
        for index, row in enumerate(parsed.rows, start=1):
            text = str(row.get("text", "")).strip()
            if not text:
                continue
            segment = f"[片段 {row.get('locator', index)}]\n{text}\n"
            if size + len(segment) > limit:
                remaining = limit - size
                if remaining > 0:
                    segments.append(segment[:remaining])
                truncated = True
                break
            segments.append(segment)
            size += len(segment)
    else:
        header = f"字段：{', '.join(parsed.columns)}\n"
        segments.append(header[:limit])
        size = min(len(header), limit)
        truncated = len(header) > limit
        for index, row in enumerate(parsed.rows, start=1):
            segment = (
                f"[源行 {index}] "
                f"{json.dumps(row, ensure_ascii=False, default=str, separators=(',', ':'))}\n"
            )
            if size + len(segment) > limit:
                remaining = limit - size
                if remaining > 0:
                    segments.append(segment[:remaining])
                truncated = True
                break
            segments.append(segment)
            size += len(segment)
    return "".join(segments).strip(), truncated


def _export_media_type(export_format: str) -> str:
    return {
        "json": "application/json",
        "csv": "text/csv; charset=utf-8",
        "xlsx": "application/vnd.openxmlformats-officedocument.spreadsheetml.sheet",
        "bundle": "application/zip",
    }.get(export_format, "application/octet-stream")


def _uses_same_database(settings: Settings, session: Session, other: Session) -> bool:
    """Tell cross-domain endpoints when one SQLAlchemy session is required.

    The default deployment uses one physical SQLite file with logical domain
    tables. Two independent uncommitted writers to that file create avoidable
    ``database is locked`` failures. Explicitly separated stores keep their
    own transaction boundaries.
    """

    if settings.unified_storage:
        return True
    try:
        return str(session.get_bind().url) == str(other.get_bind().url)
    except (AttributeError, RuntimeError):
        return False


ERROR_RESPONSES = {
    400: {"model": ErrorResponse},
    404: {"model": ErrorResponse},
    409: {"model": ErrorResponse},
    422: {"model": ErrorResponse},
}


def create_api_router(settings: Settings) -> APIRouter:
    router = APIRouter(responses=cast(Any, ERROR_RESPONSES))

    @router.get("/health", response_model=HealthView, tags=["system"])
    def health() -> HealthView:
        frontend_ready = bool(
            settings.frontend_dist_dir
            and (settings.frontend_dist_dir / "index.html").is_file()
        )
        return HealthView(
            status="ok",
            version=settings.app_version,
            build_id=settings.build_id,
            schema_revision=DATABASE_SCHEMA_REVISION,
            database="ready",
            agent_worker="enabled" if settings.agent_worker_enabled else "disabled",
            frontend="ready" if frontend_ready else "external",
        )

    @router.get(
        "/lifecycle/policies",
        response_model=list[LifecyclePolicyView],
        tags=["lifecycle"],
    )
    def lifecycle_policies(
        session: Annotated[Session, Depends(get_session)],
        observation_session: Annotated[Session, Depends(get_observation_session)],
    ) -> list[LifecyclePolicyView]:
        return LifecycleService(session, observation_session, settings).policies()

    @router.get(
        "/projects/{project_id}/lifecycle/cleanup/preview",
        response_model=LifecyclePreviewView,
        tags=["lifecycle"],
    )
    def lifecycle_cleanup_preview(
        project_id: UUID,
        session: Annotated[Session, Depends(get_session)],
        observation_session: Annotated[Session, Depends(get_observation_session)],
        resource_kinds: Annotated[list[LifecycleResourceKind] | None, Query()] = None,
    ) -> LifecyclePreviewView:
        return LifecycleService(session, observation_session, settings).preview(
            project_id, resource_kinds=resource_kinds
        )

    @router.post(
        "/projects/{project_id}/lifecycle/cleanup",
        response_model=LifecycleCleanupResult,
        tags=["lifecycle"],
    )
    def lifecycle_cleanup(
        project_id: UUID,
        payload: LifecycleCleanupRequest,
        request: Request,
        session: Annotated[Session, Depends(get_session)],
        observation_session: Annotated[Session, Depends(get_observation_session)],
    ) -> LifecycleCleanupResult:
        lifecycle_observation_session = (
            session
            if _uses_same_database(settings, session, observation_session)
            else observation_session
        )
        return LifecycleService(session, lifecycle_observation_session, settings).cleanup(
            project_id,
            payload,
            actor_id=request.app.state.settings.local_actor_id,
        )

    @router.delete(
        "/projects/{project_id}/lifecycle/resources/{resource_kind}/{resource_id}",
        response_model=LifecycleCleanupResult,
        tags=["lifecycle"],
    )
    def lifecycle_delete_resource(
        project_id: UUID,
        resource_kind: LifecycleResourceKind,
        resource_id: UUID,
        payload: LifecycleDeleteRequest,
        request: Request,
        session: Annotated[Session, Depends(get_session)],
        observation_session: Annotated[Session, Depends(get_observation_session)],
    ) -> LifecycleCleanupResult:
        lifecycle_observation_session = (
            session
            if _uses_same_database(settings, session, observation_session)
            else observation_session
        )
        return LifecycleService(session, lifecycle_observation_session, settings).delete_one(
            project_id,
            resource_kind,
            resource_id,
            payload,
            actor_id=request.app.state.settings.local_actor_id,
        )

    @router.get(
        "/projects/{project_id}/control/task-routes",
        response_model=ControlTaskRoutePage,
        tags=["control-plane"],
    )
    def list_control_task_routes(
        project_id: UUID,
        session: Annotated[Session, Depends(get_session)],
        control_session: Annotated[Session, Depends(get_control_session)],
        offset: Annotated[int, Query(ge=0)] = 0,
        limit: Annotated[int, Query(ge=1, le=500)] = 100,
    ) -> ControlTaskRoutePage:
        PortfolioService(session).require_project(project_id)
        return ControlIndexService(control_session).list_routes(
            project_id, offset=offset, limit=limit
        )

    @router.get(
        "/projects/{project_id}/control/task-routes/{run_id}",
        response_model=ControlTaskRouteView,
        tags=["control-plane"],
    )
    def get_control_task_route(
        project_id: UUID,
        run_id: UUID,
        session: Annotated[Session, Depends(get_session)],
        control_session: Annotated[Session, Depends(get_control_session)],
    ) -> ControlTaskRouteView:
        PortfolioService(session).require_project(project_id)
        return ControlIndexService(control_session).get_route(project_id, run_id)

    @router.get(
        "/projects/{project_id}/control/runs/{run_id}/execution-explanation",
        response_model=ControlExecutionExplanation,
        tags=["control-plane"],
    )
    def get_control_execution_explanation(
        project_id: UUID,
        run_id: UUID,
        session: Annotated[Session, Depends(get_session)],
        control_session: Annotated[Session, Depends(get_control_session)],
    ) -> ControlExecutionExplanation:
        project = PortfolioService(session).require_project(project_id)
        return ControlIndexService(control_session).get_execution_explanation(
            project_id, UUID(project.company_id), run_id
        )

    @router.get(
        "/projects/{project_id}/control/claim-ledger",
        response_model=ControlClaimLedgerPage,
        tags=["control-plane"],
    )
    def list_control_claim_ledger(
        project_id: UUID,
        session: Annotated[Session, Depends(get_session)],
        control_session: Annotated[Session, Depends(get_control_session)],
        run_id: UUID | None = None,
        offset: Annotated[int, Query(ge=0)] = 0,
        limit: Annotated[int, Query(ge=1, le=500)] = 100,
    ) -> ControlClaimLedgerPage:
        PortfolioService(session).require_project(project_id)
        return ControlClaimLedgerService(control_session).list_claims(
            project_id, run_id=run_id, offset=offset, limit=limit
        )

    @router.get(
        "/projects/{project_id}/control/query-manifests",
        response_model=ControlQueryManifestPage,
        tags=["control-plane"],
    )
    def list_control_query_manifests(
        project_id: UUID,
        session: Annotated[Session, Depends(get_session)],
        control_session: Annotated[Session, Depends(get_control_session)],
        run_id: UUID | None = None,
        offset: Annotated[int, Query(ge=0)] = 0,
        limit: Annotated[int, Query(ge=1, le=500)] = 100,
    ) -> ControlQueryManifestPage:
        PortfolioService(session).require_project(project_id)
        return ControlIndexService(control_session).list_query_manifests(
            project_id, run_id=run_id, offset=offset, limit=limit
        )

    @router.get(
        "/projects/{project_id}/control/evidence-reads",
        response_model=ControlEvidenceReadPage,
        tags=["control-plane"],
    )
    def list_control_evidence_reads(
        project_id: UUID,
        session: Annotated[Session, Depends(get_session)],
        control_session: Annotated[Session, Depends(get_control_session)],
        run_id: UUID | None = None,
        query_manifest_id: str | None = Query(
            default=None, min_length=64, max_length=64, pattern=r"^[0-9a-f]{64}$"
        ),
        offset: Annotated[int, Query(ge=0)] = 0,
        limit: Annotated[int, Query(ge=1, le=500)] = 100,
    ) -> ControlEvidenceReadPage:
        PortfolioService(session).require_project(project_id)
        return ControlIndexService(control_session).list_evidence_reads(
            project_id,
            run_id=run_id,
            query_manifest_id=query_manifest_id,
            offset=offset,
            limit=limit,
        )

    @router.get("/meta/capabilities", response_model=CapabilityManifest, tags=["system"])
    def capabilities() -> CapabilityManifest:
        return CapabilityManifest(
            api_version="v3",
            capabilities=[
                CapabilityView(
                    key="portfolio",
                    status="WORKING",
                    description="公司中心化管理；项目仅作为兼容性的内部投影键",
                ),
                CapabilityView(key="ontology", status="WORKING", description="强类型本体"),
                CapabilityView(key="projection", status="WORKING", description="统一企业图"),
                CapabilityView(key="change_sets", status="WORKING", description="受控变更"),
                CapabilityView(
                    key="material_import",
                    status="WORKING",
                    description="CSV、TXT、Markdown、DOCX、文本PDF、XLSX和限定JSON预览确认",
                ),
                CapabilityView(
                    key="agent_runtime",
                    status="WORKING",
                    description="持久化对话、模型调用与后台运行队列",
                ),
                CapabilityView(
                    key="action_engine",
                    status="PARTIAL",
                    description=(
                        "平台内注册动作支持预演、审批、执行和回滚；"
                        "ERPNext 等外部系统写入连接器尚未接通"
                    ),
                ),
                CapabilityView(key="exploration", status="WORKING", description="可扩展管理探索"),
                CapabilityView(
                    key="management_intelligence",
                    status="WORKING",
                    description="设计侧与结果侧信号、交叉验证和管理反馈闭环",
                ),
                CapabilityView(
                    key="agent_evaluations",
                    status="WORKING",
                    description="持久化黄金集、逐项检查与模型版本回归比较",
                ),
                CapabilityView(
                    key="source_lineage",
                    status="WORKING",
                    description="稳定资产、原始批次、字段漂移、身份绑定、冲突和逐条血缘",
                ),
                CapabilityView(
                    key="bounded_retrieval",
                    status="WORKING",
                    description=(
                        "原始来源值使用数据库索引和有界分页检索；"
                        "图查询按关系类型缩小内存工作集，Agent 不直接遍历数据库"
                    ),
                ),
                CapabilityView(
                    key="temporary_data_lifecycle",
                    status="WORKING",
                    description=(
                        "过期预览、恢复临时包和过期导出自动清理；"
                        "支持带原因的临时资源主动删除并记录审计"
                    ),
                ),
                CapabilityView(
                    key="source_relation_mappings",
                    status="WORKING",
                    description="外键到本体关系角色的校验、批准、物化和未知目标告警",
                ),
                CapabilityView(
                    key="postgresql_connector",
                    status="WORKING",
                    description="PostgreSQL表/视图只读分页、组合水位和有界重试参考连接器",
                ),
                CapabilityView(
                    key="sqlite_connector",
                    status="WORKING",
                    description="本地 SQLite 文件只读分页和组合水位参考连接器",
                ),
                CapabilityView(
                    key="rest_json_connector",
                    status="WORKING",
                    description="通用 REST JSON 只读路径、分页参数、游标和显式响应结构适配",
                ),
                CapabilityView(
                    key="vendor_erp_connectors",
                    status="PARTIAL",
                    description=(
                        "已接入 ERPNext/Frappe 只读参考适配并通过离线模拟；"
                        "真实实例验收、ERP 写入及 MES/CRM 适配尚未完成"
                    ),
                ),
                CapabilityView(
                    key="multi_store_backup_restore",
                    status="PARTIAL",
                    description=(
                        "统一 SQLite 单库逻辑域的一致快照与校验后恢复到新目录；"
                        "旧四库备份仍可兼容读取；"
                        "安全的用户界面导出/导入与活动数据切换尚未完成"
                    ),
                ),
                CapabilityView(
                    key="semantic_datasets",
                    status="WORKING",
                    description="类型化字段、关系连接、基数风险检查、查询快照和导出",
                ),
                CapabilityView(
                    key="scenario_lifecycle",
                    status="WORKING",
                    description="隔离方案、差异、比较、重基、审批和原子应用",
                ),
                CapabilityView(
                    key="scenario_simulation",
                    status="WORKING",
                    description="版本化类型计算、不可变运行结果、同基准比较及 Action 调用",
                ),
                CapabilityView(
                    key="learning_cases",
                    status="WORKING",
                    description="行动结果案例、待验证方案参考和匿名跨企业参考",
                ),
                CapabilityView(
                    key="project_restore",
                    status="WORKING",
                    description="完整项目包预览、校验、确认恢复且不包含模型密钥",
                ),
                CapabilityView(key="publication", status="WORKING", description="发布快照"),
            ],
        )

    @router.get("/companies", response_model=Page[CompanyView], tags=["portfolio"])
    def list_companies(session: Annotated[Session, Depends(get_session)]) -> Page[CompanyView]:
        items = PortfolioService(session).list_companies()
        return Page(items=items, total=len(items))

    @router.post(
        "/companies",
        response_model=CompanyView,
        status_code=status.HTTP_201_CREATED,
        tags=["portfolio"],
    )
    def create_company(
        payload: CompanyCreate, session: Annotated[Session, Depends(get_session)]
    ) -> CompanyView:
        return PortfolioService(session).create_company(payload)

    @router.get("/companies/{company_id}", response_model=CompanyView, tags=["portfolio"])
    def get_company(
        company_id: UUID, session: Annotated[Session, Depends(get_session)]
    ) -> CompanyView:
        return PortfolioService(session).company(company_id)

    @router.patch("/companies/{company_id}", response_model=CompanyView, tags=["portfolio"])
    def update_company(
        company_id: UUID,
        payload: CompanyUpdate,
        session: Annotated[Session, Depends(get_session)],
    ) -> CompanyView:
        return PortfolioService(session).update_company(company_id, payload)

    @router.get("/projects", response_model=Page[ProjectView], tags=["portfolio"])
    def list_projects(
        session: Annotated[Session, Depends(get_session)],
        company_id: UUID | None = None,
        ensure_workspace: bool = False,
    ) -> Page[ProjectView]:
        if company_id is not None and ensure_workspace:
            primary = PortfolioService(session).ensure_workspace(company_id)
            return Page(items=[primary], total=1)
        items = PortfolioService(session).list_projects(company_id)
        return Page(items=items, total=len(items))

    @router.post(
        "/companies/{company_id}/projects",
        response_model=ProjectView,
        status_code=status.HTTP_201_CREATED,
        tags=["portfolio"],
    )
    def create_project(
        company_id: UUID,
        payload: ProjectCreate,
        session: Annotated[Session, Depends(get_session)],
    ) -> ProjectView:
        return PortfolioService(session).create_project(company_id, payload)

    @router.get("/projects/{project_id}", response_model=ProjectView, tags=["portfolio"])
    def get_project(
        project_id: UUID, session: Annotated[Session, Depends(get_session)]
    ) -> ProjectView:
        return PortfolioService(session).project(project_id)

    @router.patch("/projects/{project_id}", response_model=ProjectView, tags=["portfolio"])
    def update_project(
        project_id: UUID,
        payload: ProjectUpdate,
        session: Annotated[Session, Depends(get_session)],
    ) -> ProjectView:
        return PortfolioService(session).update_project(project_id, payload)

    @router.get(
        "/projects/{project_id}/ontology/types",
        response_model=Page[OntologyTypeView],
        tags=["ontology"],
    )
    def list_ontology_types(
        project_id: UUID,
        session: Annotated[Session, Depends(get_session)],
        kind: TypeKind | None = None,
    ) -> Page[OntologyTypeView]:
        items = OntologyService(session).list_types(project_id, kind=kind)
        return Page(items=items, total=len(items))

    @router.post(
        "/projects/{project_id}/ontology/types",
        response_model=OntologyTypeView,
        status_code=status.HTTP_201_CREATED,
        tags=["ontology"],
    )
    def create_ontology_type(
        project_id: UUID,
        payload: OntologyTypeCreate,
        session: Annotated[Session, Depends(get_session)],
    ) -> OntologyTypeView:
        return OntologyService(session).create_type(project_id, payload)

    @router.get(
        "/projects/{project_id}/ontology/types/{type_id}",
        response_model=OntologyTypeView,
        tags=["ontology"],
    )
    def get_ontology_type(
        project_id: UUID,
        type_id: UUID,
        session: Annotated[Session, Depends(get_session)],
    ) -> OntologyTypeView:
        return OntologyService(session).type(project_id, type_id)

    @router.patch(
        "/projects/{project_id}/ontology/types/{type_id}",
        response_model=OntologyTypeView,
        tags=["ontology"],
    )
    def update_ontology_type(
        project_id: UUID,
        type_id: UUID,
        payload: OntologyTypeUpdate,
        session: Annotated[Session, Depends(get_session)],
    ) -> OntologyTypeView:
        return OntologyService(session).update_type(project_id, type_id, payload)

    @router.post(
        "/projects/{project_id}/ontology/default-pack",
        response_model=list[OntologyTypeView],
        tags=["ontology"],
    )
    def install_default_ontology(
        project_id: UUID, session: Annotated[Session, Depends(get_session)]
    ) -> list[OntologyTypeView]:
        return OntologyService(session).install_default_pack(project_id)

    @router.get(
        "/projects/{project_id}/ontology/releases",
        response_model=Page[OntologyReleaseView],
        tags=["ontology"],
    )
    def list_ontology_releases(
        project_id: UUID, session: Annotated[Session, Depends(get_session)]
    ) -> Page[OntologyReleaseView]:
        items = OntologyService(session).list_releases(project_id)
        return Page(items=items, total=len(items))

    @router.post(
        "/projects/{project_id}/ontology/releases",
        response_model=OntologyReleaseView,
        status_code=status.HTTP_201_CREATED,
        tags=["ontology"],
    )
    def release_ontology(
        project_id: UUID,
        payload: OntologyReleaseCreate,
        session: Annotated[Session, Depends(get_session)],
    ) -> OntologyReleaseView:
        return OntologyService(session).create_release(project_id, payload)

    @router.get(
        "/projects/{project_id}/entities", response_model=Page[EntityView], tags=["projection"]
    )
    def list_entities(
        project_id: UUID,
        session: Annotated[Session, Depends(get_session)],
        type_key: str | None = None,
        search: str | None = None,
        include_retired: bool = False,
        include_unmodeled: bool = False,
        limit: int = Query(default=200, ge=1, le=5000),
        offset: int = Query(default=0, ge=0),
    ) -> Page[EntityView]:
        service = ProjectionService(session)
        items = service.list_entities(
            project_id,
            type_key=type_key,
            search=search,
            include_retired=include_retired,
            include_unmodeled=include_unmodeled,
            limit=limit,
            offset=offset,
        )
        total = service.count_entities(
            project_id,
            type_key=type_key,
            search=search,
            include_retired=include_retired,
            include_unmodeled=include_unmodeled,
        )
        return Page(items=items, total=total)

    @router.post(
        "/projects/{project_id}/entities",
        response_model=EntityView,
        status_code=status.HTTP_201_CREATED,
        tags=["projection"],
    )
    def create_entity(
        project_id: UUID,
        payload: EntityCreate,
        session: Annotated[Session, Depends(get_session)],
    ) -> EntityView:
        return ProjectionService(session).create_entity(project_id, payload)

    @router.get(
        "/projects/{project_id}/entities/{entity_id}",
        response_model=EntityView,
        tags=["projection"],
    )
    def get_entity(
        project_id: UUID,
        entity_id: UUID,
        session: Annotated[Session, Depends(get_session)],
    ) -> EntityView:
        return ProjectionService(session).entity(project_id, entity_id)

    @router.patch(
        "/projects/{project_id}/entities/{entity_id}",
        response_model=EntityView,
        tags=["projection"],
    )
    def update_entity(
        project_id: UUID,
        entity_id: UUID,
        payload: EntityUpdate,
        session: Annotated[Session, Depends(get_session)],
    ) -> EntityView:
        return ProjectionService(session).update_entity(project_id, entity_id, payload)

    @router.post(
        "/projects/{project_id}/entities/{entity_id}/retire",
        response_model=EntityView,
        tags=["projection"],
    )
    def retire_entity(
        project_id: UUID,
        entity_id: UUID,
        payload: RevisionRequest,
        session: Annotated[Session, Depends(get_session)],
    ) -> EntityView:
        return ProjectionService(session).retire_entity(
            project_id, entity_id, payload.expected_revision
        )

    @router.get(
        "/projects/{project_id}/relations",
        response_model=Page[RelationView],
        tags=["projection"],
    )
    def list_relations(
        project_id: UUID,
        session: Annotated[Session, Depends(get_session)],
        type_key: str | None = None,
        entity_id: UUID | None = None,
        include_retired: bool = False,
        limit: int = Query(default=200, ge=1, le=5000),
        offset: int = Query(default=0, ge=0),
    ) -> Page[RelationView]:
        service = ProjectionService(session)
        items = service.list_relations(
            project_id,
            type_key=type_key,
            entity_id=entity_id,
            include_retired=include_retired,
            limit=limit,
            offset=offset,
        )
        total = service.count_relations(
            project_id,
            type_key=type_key,
            entity_id=entity_id,
            include_retired=include_retired,
        )
        return Page(items=items, total=total)

    @router.post(
        "/projects/{project_id}/relations",
        response_model=RelationView,
        status_code=status.HTTP_201_CREATED,
        tags=["projection"],
    )
    def create_relation(
        project_id: UUID,
        payload: RelationCreate,
        session: Annotated[Session, Depends(get_session)],
    ) -> RelationView:
        return ProjectionService(session).create_relation(project_id, payload)

    @router.get(
        "/projects/{project_id}/relations/{relation_id}",
        response_model=RelationView,
        tags=["projection"],
    )
    def get_relation(
        project_id: UUID,
        relation_id: UUID,
        session: Annotated[Session, Depends(get_session)],
    ) -> RelationView:
        return ProjectionService(session).relation(project_id, relation_id)

    @router.patch(
        "/projects/{project_id}/relations/{relation_id}",
        response_model=RelationView,
        tags=["projection"],
    )
    def update_relation(
        project_id: UUID,
        relation_id: UUID,
        payload: RelationUpdate,
        session: Annotated[Session, Depends(get_session)],
    ) -> RelationView:
        return ProjectionService(session).update_relation(project_id, relation_id, payload)

    @router.post(
        "/projects/{project_id}/relations/{relation_id}/retire",
        response_model=RelationView,
        tags=["projection"],
    )
    def retire_relation(
        project_id: UUID,
        relation_id: UUID,
        payload: RevisionRequest,
        session: Annotated[Session, Depends(get_session)],
    ) -> RelationView:
        return ProjectionService(session).retire_relation(
            project_id, relation_id, payload.expected_revision
        )

    @router.get(
        "/projects/{project_id}/events", response_model=Page[EventView], tags=["projection"]
    )
    def list_events(
        project_id: UUID, session: Annotated[Session, Depends(get_session)]
    ) -> Page[EventView]:
        items = ProjectionService(session).list_events(project_id)
        return Page(items=items, total=len(items))

    @router.post(
        "/projects/{project_id}/events",
        response_model=EventView,
        status_code=status.HTTP_201_CREATED,
        tags=["projection"],
    )
    def create_event(
        project_id: UUID,
        payload: EventCreate,
        session: Annotated[Session, Depends(get_session)],
    ) -> EventView:
        return ProjectionService(session).create_event(project_id, payload)

    @router.post(
        "/projects/{project_id}/graph/query", response_model=GraphView, tags=["projection"]
    )
    def query_graph(
        project_id: UUID,
        payload: GraphQuery,
        session: Annotated[Session, Depends(get_session)],
    ) -> GraphView:
        if payload.query_snapshot_id is not None:
            return QuerySnapshotService(session).graph(
                project_id, payload.query_snapshot_id, payload
            )
        return ProjectionService(session).graph(project_id, payload)

    @router.get(
        "/projects/{project_id}/change-sets",
        response_model=Page[ChangeSetView],
        tags=["change-sets"],
    )
    def list_change_sets(
        project_id: UUID, session: Annotated[Session, Depends(get_session)]
    ) -> Page[ChangeSetView]:
        items = ChangeSetService(session).list(project_id)
        return Page(items=items, total=len(items))

    @router.post(
        "/projects/{project_id}/change-sets",
        response_model=ChangeSetView,
        status_code=status.HTTP_201_CREATED,
        tags=["change-sets"],
    )
    def create_change_set(
        project_id: UUID,
        payload: ChangeSetCreate,
        session: Annotated[Session, Depends(get_session)],
    ) -> ChangeSetView:
        return ChangeSetService(session).create(project_id, payload)

    @router.get(
        "/projects/{project_id}/change-sets/{change_set_id}",
        response_model=ChangeSetView,
        tags=["change-sets"],
    )
    def get_change_set(
        project_id: UUID,
        change_set_id: UUID,
        session: Annotated[Session, Depends(get_session)],
    ) -> ChangeSetView:
        return ChangeSetService(session).get(project_id, change_set_id)

    @router.post(
        "/projects/{project_id}/change-sets/{change_set_id}/validate",
        response_model=ChangeSetView,
        tags=["change-sets"],
    )
    def validate_change_set(
        project_id: UUID,
        change_set_id: UUID,
        session: Annotated[Session, Depends(get_session)],
    ) -> ChangeSetView:
        return ChangeSetService(session).validate(project_id, change_set_id)

    @router.get(
        "/projects/{project_id}/change-sets/{change_set_id}/preview",
        response_model=ChangePreview,
        tags=["change-sets"],
    )
    def preview_change_set(
        project_id: UUID,
        change_set_id: UUID,
        session: Annotated[Session, Depends(get_session)],
    ) -> ChangePreview:
        return ChangeSetService(session).preview(project_id, change_set_id)

    @router.post(
        "/projects/{project_id}/change-sets/{change_set_id}/approve",
        response_model=ChangeSetView,
        tags=["change-sets"],
    )
    def approve_change_set(
        project_id: UUID,
        change_set_id: UUID,
        session: Annotated[Session, Depends(get_session)],
    ) -> ChangeSetView:
        return ChangeSetService(session).approve(project_id, change_set_id)

    @router.post(
        "/projects/{project_id}/change-sets/{change_set_id}/apply",
        response_model=ChangeSetView,
        tags=["change-sets"],
    )
    def apply_change_set(
        project_id: UUID,
        change_set_id: UUID,
        session: Annotated[Session, Depends(get_session)],
    ) -> ChangeSetView:
        return ChangeSetService(session).apply(project_id, change_set_id)

    @router.post(
        "/projects/{project_id}/imports/preview",
        response_model=ImportPreviewView,
        tags=["evidence"],
    )
    async def preview_import(
        project_id: UUID,
        session: Annotated[Session, Depends(get_session)],
        file: Annotated[UploadFile, File(description="待预览的材料")],
        kind: Annotated[ImportKind, Form()],
    ) -> ImportPreviewView:
        return EvidenceService(session).preview_csv(
            project_id,
            file_name=file.filename or "upload.csv",
            content=await _read_upload_with_limit(file),
            kind=kind,
        )

    @router.post(
        "/projects/{project_id}/imports/confirm",
        response_model=ImportResultView,
        tags=["evidence"],
    )
    def confirm_import(
        project_id: UUID,
        payload: ImportConfirmRequest,
        session: Annotated[Session, Depends(get_session)],
    ) -> ImportResultView:
        return EvidenceService(session).confirm(project_id, payload)

    @router.get(
        "/projects/{project_id}/documents",
        response_model=Page[SourceDocumentView],
        tags=["evidence"],
    )
    def list_documents(
        project_id: UUID, session: Annotated[Session, Depends(get_session)]
    ) -> Page[SourceDocumentView]:
        items = EvidenceService(session).list_documents(project_id)
        return Page(items=items, total=len(items))

    @router.get(
        "/projects/{project_id}/documents/{document_id}/fragments",
        response_model=Page[EvidenceFragmentView],
        tags=["evidence"],
    )
    def list_fragments(
        project_id: UUID,
        document_id: UUID,
        session: Annotated[Session, Depends(get_session)],
        offset: Annotated[int, Query(ge=0)] = 0,
        limit: Annotated[int | None, Query(ge=1, le=100)] = None,
    ) -> Page[EvidenceFragmentView]:
        service = EvidenceService(session)
        items = service.list_fragments(project_id, document_id, offset=offset, limit=limit)
        return Page(items=items, total=service.count_fragments(project_id, document_id))

    @router.get("/projects/{project_id}/claims", response_model=Page[ClaimView], tags=["evidence"])
    def list_claims(
        project_id: UUID, session: Annotated[Session, Depends(get_session)]
    ) -> Page[ClaimView]:
        items = EvidenceService(session).list_claims(project_id)
        return Page(items=items, total=len(items))

    @router.get(
        "/projects/{project_id}/observations",
        response_model=Page[ObservationView],
        tags=["management-input"],
    )
    def list_management_observations(
        project_id: UUID,
        session: Annotated[Session, Depends(get_session)],
        observation_session: Annotated[Session, Depends(get_observation_session)],
        include_withdrawn: bool = False,
        offset: Annotated[int, Query(ge=0)] = 0,
        limit: Annotated[int, Query(ge=1, le=100)] = 50,
    ) -> Page[ObservationView]:
        PortfolioService(session).require_project(project_id)
        items, total = ObservationService(observation_session, settings).list(
            project_id,
            include_withdrawn=include_withdrawn,
            offset=offset,
            limit=limit,
        )
        return Page(items=items, total=total)

    @router.post(
        "/projects/{project_id}/observations",
        response_model=ObservationView,
        status_code=status.HTTP_201_CREATED,
        tags=["management-input"],
    )
    def create_management_observation(
        project_id: UUID,
        payload: ObservationCreate,
        session: Annotated[Session, Depends(get_session)],
        observation_session: Annotated[Session, Depends(get_observation_session)],
    ) -> ObservationView:
        project = PortfolioService(session).require_project(project_id)
        return ObservationService(observation_session, settings).create(
            project, project_id, payload
        )

    @router.post(
        "/projects/{project_id}/observation-ingestions",
        response_model=ObservationIngestionView,
        status_code=status.HTTP_201_CREATED,
        tags=["management-input"],
    )
    async def ingest_management_observation(
        project_id: UUID,
        file: Annotated[UploadFile, File(description="访谈、问卷、会议纪要或业务记录")],
        session: Annotated[Session, Depends(get_session)],
        observation_session: Annotated[Session, Depends(get_observation_session)],
        observation_kind: Annotated[str, Form()] = "OTHER",
        title: Annotated[str | None, Form()] = None,
        occurred_at: Annotated[str | None, Form()] = None,
    ) -> ObservationIngestionView:
        project = PortfolioService(session).require_project(project_id)
        file_name = _safe_upload_name(file.filename or "management-material.txt")
        file_kind = _import_kind_for_name(file_name)
        raw_content = await _read_upload_with_limit(file)
        parsed = parse_import(file_name, raw_content, file_kind)
        extracted, preview_truncated = _render_observation_import(parsed, limit=50_000)
        warnings = list(parsed.warnings)
        if preview_truncated:
            warnings.append(
                "可供 Agent 整理的文本预览已达到 50,000 字符上限；"
                "完整原文件已保存在管理观察库附件中。"
            )
        normalized_title = (title or "").strip() or file_name
        try:
            parsed_occurred_at = (
                datetime.fromisoformat(occurred_at.replace("Z", "+00:00"))
                if occurred_at
                else None
            )
        except ValueError as exc:
            raise DomainError(
                "OBSERVATION_INGESTION_TIME_INVALID",
                "发生时间格式无效，请重新选择或留空。",
                status_code=422,
            ) from exc
        idempotency_key = sha256(
            f"{project_id}:{observation_kind}:{normalized_title}:".encode()
            + sha256(raw_content).digest()
        ).hexdigest()
        payload = ObservationCreate(
            kind=observation_kind,
            title=normalized_title,
            content=extracted,
            occurred_at=parsed_occurred_at,
            idempotency_key=idempotency_key,
        )
        service = ObservationService(observation_session, settings)
        observation = service.create(project, project_id, payload)
        attachment = service.attach_source_file(
            project_id,
            observation.id,
            file_name=file_name,
            media_type=mimetypes.guess_type(file_name)[0] or "application/octet-stream",
            content=raw_content,
        )
        return ObservationIngestionView(
            observation=observation,
            attachment=attachment,
            parser_version=parsed.parser_version,
            extracted_character_count=len(extracted),
            preview_truncated=preview_truncated,
            warnings=warnings,
        )

    @router.get(
        "/projects/{project_id}/observations/{observation_id}/attachments",
        response_model=Page[ObservationAttachmentView],
        tags=["management-input"],
    )
    def list_management_observation_attachments(
        project_id: UUID,
        observation_id: UUID,
        session: Annotated[Session, Depends(get_session)],
        observation_session: Annotated[Session, Depends(get_observation_session)],
    ) -> Page[ObservationAttachmentView]:
        PortfolioService(session).require_project(project_id)
        items = ObservationService(observation_session, settings).list_attachments(
            project_id, observation_id
        )
        return Page(items=items, total=len(items))

    @router.get(
        "/projects/{project_id}/observations/{observation_id}/attachments/{attachment_id}",
        tags=["management-input"],
    )
    def download_management_observation_attachment(
        project_id: UUID,
        observation_id: UUID,
        attachment_id: UUID,
        session: Annotated[Session, Depends(get_session)],
        observation_session: Annotated[Session, Depends(get_observation_session)],
    ) -> Response:
        PortfolioService(session).require_project(project_id)
        row = ObservationService(observation_session, settings).get_attachment(
            project_id, observation_id, attachment_id
        )
        return Response(
            content=row.content,
            media_type=row.media_type,
            headers={
                "Content-Disposition": f"attachment; filename*=UTF-8''{quote(row.file_name)}",
                "X-Content-SHA256": row.content_sha256,
            },
        )

    @router.patch(
        "/projects/{project_id}/observations/{observation_id}",
        response_model=ObservationView,
        tags=["management-input"],
    )
    def revise_management_observation(
        project_id: UUID,
        observation_id: UUID,
        payload: ObservationUpdate,
        session: Annotated[Session, Depends(get_session)],
        observation_session: Annotated[Session, Depends(get_observation_session)],
    ) -> ObservationView:
        PortfolioService(session).require_project(project_id)
        return ObservationService(observation_session, settings).revise(
            project_id,
            observation_id,
            payload.expected_revision,
            ObservationCreate(
                kind=payload.kind,
                title=payload.title,
                content=payload.content,
                occurred_at=payload.occurred_at,
            ),
        )

    @router.delete(
        "/projects/{project_id}/observations/{observation_id}",
        response_model=ObservationView,
        tags=["management-input"],
    )
    def withdraw_management_observation(
        project_id: UUID,
        observation_id: UUID,
        expected_revision: Annotated[int, Query(ge=1)],
        session: Annotated[Session, Depends(get_session)],
        observation_session: Annotated[Session, Depends(get_observation_session)],
    ) -> ObservationView:
        PortfolioService(session).require_project(project_id)
        return ObservationService(observation_session, settings).withdraw(
            project_id, observation_id, expected_revision
        )

    @router.get(
        "/projects/{project_id}/observations/{observation_id}/history",
        response_model=ObservationHistoryView,
        tags=["management-input"],
    )
    def get_management_observation_history(
        project_id: UUID,
        observation_id: UUID,
        session: Annotated[Session, Depends(get_session)],
        observation_session: Annotated[Session, Depends(get_observation_session)],
    ) -> ObservationHistoryView:
        PortfolioService(session).require_project(project_id)
        return ObservationService(observation_session, settings).history(
            project_id, observation_id
        )

    @router.post(
        "/projects/{project_id}/observations/{observation_id}/extract",
        response_model=ObservationExtractionView,
        tags=["management-input"],
    )
    def extract_management_observation(
        project_id: UUID,
        observation_id: UUID,
        payload: ObservationExtractRequest,
        session: Annotated[Session, Depends(get_session)],
        observation_session: Annotated[Session, Depends(get_observation_session)],
    ) -> ObservationExtractionView:
        PortfolioService(session).require_project(project_id)
        observation_service = ObservationService(observation_session, settings)
        observation = observation_service.get(project_id, observation_id)
        profile = ModelProfileService(session, settings).require(payload.model_profile_id)
        try:
            draft = ManagementInputAgent(settings).extract(
                observation.content,
                profile,
                allow_external_model=payload.allow_external_model,
            )
        except DomainError as exc:
            return observation_service.record_extraction(
                project_id,
                observation_id,
                model_profile_id=UUID(profile.id),
                model_name=profile.model,
                status="FAILED",
                draft=None,
                error_code=exc.code,
            )
        return observation_service.record_extraction(
            project_id,
            observation_id,
            model_profile_id=UUID(profile.id),
            model_name=profile.model,
            status="COMPLETED",
            draft=draft,
        )

    @router.get(
        "/projects/{project_id}/observations/{observation_id}/extractions",
        response_model=ObservationExtractionHistoryView,
        tags=["management-input"],
    )
    def list_management_observation_extractions(
        project_id: UUID,
        observation_id: UUID,
        session: Annotated[Session, Depends(get_session)],
        observation_session: Annotated[Session, Depends(get_observation_session)],
    ) -> ObservationExtractionHistoryView:
        PortfolioService(session).require_project(project_id)
        return ObservationService(observation_session, settings).extraction_history(
            project_id, observation_id
        )

    @router.post(
        "/projects/{project_id}/observations/{observation_id}/extractions/{extraction_id}/review",
        response_model=ObservationExtractionReviewView,
        tags=["management-input"],
    )
    def review_management_observation_extraction(
        project_id: UUID,
        observation_id: UUID,
        extraction_id: UUID,
        payload: ObservationExtractionReviewCreate,
        session: Annotated[Session, Depends(get_session)],
        observation_session: Annotated[Session, Depends(get_observation_session)],
    ) -> ObservationExtractionReviewView:
        PortfolioService(session).require_project(project_id)
        observation_service = ObservationService(observation_session, settings)
        observation = observation_service.get(project_id, observation_id)
        extraction = observation_session.get(ObservationExtractionRow, str(extraction_id))
        if extraction is None or extraction.observation_id != str(observation_id):
            raise DomainError(
                "OBSERVATION_EXTRACTION_NOT_FOUND", "信息整理结果不存在。", status_code=404
            )
        if extraction.status != "COMPLETED":
            raise DomainError(
                "OBSERVATION_EXTRACTION_NOT_REVIEWABLE",
                "只有成功生成的整理草稿可以确认、修改或拒绝。",
                status_code=409,
            )
        if (
            observation.revision != payload.expected_source_revision
            or extraction.source_revision != observation.revision
        ):
            raise DomainError(
                "OBSERVATION_EXTRACTION_SOURCE_CHANGED",
                "原始材料已发生变化，请基于最新版本重新整理后再确认。",
                status_code=409,
            )

        if payload.decision == ObservationExtractionReviewDecision.CONFIRM:
            reviewed_items = [
                ObservationExtractionItem.model_validate(item)
                for item in extraction.items
            ]
        elif payload.decision == ObservationExtractionReviewDecision.REVISE:
            reviewed_items = payload.items or []
        else:
            reviewed_items = []
        for item in reviewed_items:
            if item.supporting_quote not in observation.content:
                raise DomainError(
                    "OBSERVATION_REVIEW_QUOTE_NOT_IN_SOURCE",
                    "确认或修订后的原文引句必须能在当前原始材料中逐字找到。",
                    status_code=422,
                )

        created_at = now_utc()
        audit_id = uuid4()
        snapshot = {
            "extraction_id": str(extraction_id),
            "source_revision": observation.revision,
            "decision": payload.decision.value,
            "items": [item.model_dump(mode="json") for item in reviewed_items],
            "reason": payload.reason,
        }
        operation = {
            ObservationExtractionReviewDecision.CONFIRM: "EXTRACTION_REVIEW_CONFIRMED",
            ObservationExtractionReviewDecision.REVISE: "EXTRACTION_REVIEW_REVISED",
            ObservationExtractionReviewDecision.REJECT: "EXTRACTION_REVIEW_REJECTED",
        }[payload.decision]
        observation_session.add(
            ObservationAuditRow(
                id=str(audit_id),
                observation_id=observation.id,
                company_id=observation.company_id,
                project_id=observation.project_id,
                actor_id=settings.local_actor_id,
                operation=operation,
                revision=observation.revision,
                snapshot=snapshot,
                created_at=created_at,
            )
        )
        observation_session.flush()
        return ObservationExtractionReviewView(
            id=audit_id,
            observation_id=observation_id,
            extraction_id=extraction_id,
            source_revision=observation.revision,
            decision=payload.decision,
            items=reviewed_items,
            reason=payload.reason,
            reviewed_by=settings.local_actor_id,
            created_at=created_at,
        )

    @router.get(
        "/projects/{project_id}/observations/{observation_id}/extractions/{extraction_id}/reviews",
        response_model=ObservationExtractionReviewHistoryView,
        tags=["management-input"],
    )
    def list_management_observation_extraction_reviews(
        project_id: UUID,
        observation_id: UUID,
        extraction_id: UUID,
        session: Annotated[Session, Depends(get_session)],
        observation_session: Annotated[Session, Depends(get_observation_session)],
    ) -> ObservationExtractionReviewHistoryView:
        PortfolioService(session).require_project(project_id)
        observation = ObservationService(observation_session, settings).get(
            project_id, observation_id
        )
        extraction = observation_session.get(ObservationExtractionRow, str(extraction_id))
        if extraction is None or extraction.observation_id != observation.id:
            raise DomainError(
                "OBSERVATION_EXTRACTION_NOT_FOUND", "信息整理结果不存在。", status_code=404
            )
        audit_rows = observation_session.scalars(
            select(ObservationAuditRow)
            .where(
                ObservationAuditRow.observation_id == observation.id,
                ObservationAuditRow.operation.in_(
                    [
                        "EXTRACTION_REVIEW_CONFIRMED",
                        "EXTRACTION_REVIEW_REVISED",
                        "EXTRACTION_REVIEW_REJECTED",
                    ]
                ),
            )
            .order_by(ObservationAuditRow.created_at, ObservationAuditRow.id)
        ).all()
        items = []
        for row in audit_rows:
            snapshot = row.snapshot or {}
            if snapshot.get("extraction_id") != str(extraction_id):
                continue
            items.append(
                ObservationExtractionReviewView(
                    id=row.id,
                    observation_id=observation_id,
                    extraction_id=extraction_id,
                    source_revision=snapshot["source_revision"],
                    decision=snapshot["decision"],
                    items=snapshot.get("items", []),
                    reason=snapshot.get("reason"),
                    reviewed_by=row.actor_id,
                    created_at=row.created_at,
                )
            )
        return ObservationExtractionReviewHistoryView(items=items, total=len(items))

    @router.get(
        "/projects/{project_id}/potential-records",
        response_model=PotentialPage,
        tags=["potential-knowledge"],
    )
    def list_potential_records(
        project_id: UUID,
        request: Request,
        session: Annotated[Session, Depends(get_session)],
        include_history: bool = False,
        offset: Annotated[int, Query(ge=0)] = 0,
        limit: Annotated[int, Query(ge=1, le=500)] = 100,
    ) -> PotentialPage:
        project = PortfolioService(session).require_project(project_id)
        service = PotentialRecordService(request.app.state.potential_database)
        return service.list_records(
            company_id=UUID(project.company_id),
            project_id=project_id,
            include_history=include_history,
            offset=offset,
            limit=limit,
        )

    @router.post(
        "/projects/{project_id}/potential-records",
        response_model=PotentialRecordView,
        status_code=status.HTTP_201_CREATED,
        tags=["potential-knowledge"],
    )
    def create_potential_record(
        project_id: UUID,
        payload: PotentialRecordCreate,
        request: Request,
        session: Annotated[Session, Depends(get_session)],
    ) -> PotentialRecordView:
        project = PortfolioService(session).require_project(project_id)
        if payload.project_id != project_id or payload.company_id != UUID(project.company_id):
            raise DomainError(
                "POTENTIAL_SCOPE_MISMATCH",
                "潜在认识的公司和项目必须与当前项目一致。",
                status_code=422,
            )
        return PotentialRecordService(request.app.state.potential_database).create(
            payload, actor_id=settings.local_actor_id
        )

    @router.get(
        "/projects/{project_id}/potential-candidates",
        response_model=ControlPotentialCandidatePage,
        tags=["potential-knowledge"],
    )
    def list_potential_candidates(
        project_id: UUID,
        session: Annotated[Session, Depends(get_session)],
        control_session: Annotated[Session, Depends(get_control_session)],
        status_filter: Annotated[
            ControlPotentialCandidateStatus | None, Query(alias="status")
        ] = None,
        offset: Annotated[int, Query(ge=0)] = 0,
        limit: Annotated[int, Query(ge=1, le=500)] = 100,
    ) -> ControlPotentialCandidatePage:
        PortfolioService(session).require_project(project_id)
        return ControlPotentialCandidateService(control_session).list_candidates(
            project_id,
            status=status_filter,
            offset=offset,
            limit=limit,
        )

    @router.get(
        "/projects/{project_id}/potential-candidates/{candidate_id}",
        response_model=ControlPotentialCandidateView,
        tags=["potential-knowledge"],
    )
    def get_potential_candidate(
        project_id: UUID,
        candidate_id: UUID,
        session: Annotated[Session, Depends(get_session)],
        control_session: Annotated[Session, Depends(get_control_session)],
    ) -> ControlPotentialCandidateView:
        PortfolioService(session).require_project(project_id)
        return ControlPotentialCandidateService(control_session).get_candidate(
            project_id, candidate_id
        )

    @router.get(
        "/projects/{project_id}/potential-candidates/{candidate_id}/history",
        response_model=ControlPotentialCandidateEventPage,
        tags=["potential-knowledge"],
    )
    def get_potential_candidate_history(
        project_id: UUID,
        candidate_id: UUID,
        session: Annotated[Session, Depends(get_session)],
        control_session: Annotated[Session, Depends(get_control_session)],
        offset: Annotated[int, Query(ge=0)] = 0,
        limit: Annotated[int, Query(ge=1, le=200)] = 100,
    ) -> ControlPotentialCandidateEventPage:
        PortfolioService(session).require_project(project_id)
        return ControlPotentialCandidateService(control_session).history(
            project_id, candidate_id, offset=offset, limit=limit
        )

    @router.patch(
        "/projects/{project_id}/potential-candidates/{candidate_id}",
        response_model=ControlPotentialCandidateView,
        tags=["potential-knowledge"],
    )
    def edit_potential_candidate(
        project_id: UUID,
        candidate_id: UUID,
        payload: ControlPotentialCandidateEdit,
        session: Annotated[Session, Depends(get_session)],
        control_session: Annotated[Session, Depends(get_control_session)],
    ) -> ControlPotentialCandidateView:
        PortfolioService(session).require_project(project_id)
        return ControlPotentialCandidateService(control_session).edit(
            project_id,
            candidate_id,
            payload,
            actor_id=settings.local_actor_id,
        )

    @router.post(
        "/projects/{project_id}/potential-candidates/{candidate_id}/accept",
        response_model=ControlPotentialCandidateAcceptance,
        tags=["potential-knowledge"],
    )
    def accept_potential_candidate(
        project_id: UUID,
        candidate_id: UUID,
        payload: ControlPotentialCandidateDecision,
        request: Request,
        session: Annotated[Session, Depends(get_session)],
        control_session: Annotated[Session, Depends(get_control_session)],
        observation_session: Annotated[Session, Depends(get_observation_session)],
    ) -> ControlPotentialCandidateAcceptance:
        project = PortfolioService(session).require_project(project_id)
        candidates = ControlPotentialCandidateService(control_session)
        current = candidates.get_candidate(project_id, candidate_id)
        if current.status == ControlPotentialCandidateStatus.PROPOSED:
            source_error = validate_candidate_observation_sources(
                current.candidate, observation_session
            )
            if source_error is not None:
                candidates.mark_stale(
                    project_id,
                    candidate_id,
                    expected_hash=payload.expected_hash,
                )
                control_session.commit()
                raise DomainError(
                    "CONTROL_CANDIDATE_SOURCE_STALE",
                    "候选依赖的观察来源已修订、撤回或无法核验；候选已标记过期，请重新分析。",
                    status_code=409,
                    details=[{"reason": source_error}],
                )
        candidate_view = candidates.prepare_accept(
            project_id,
            candidate_id,
            payload,
            actor_id=settings.local_actor_id,
        )
        # Persist ACCEPTING first. If the following store commit succeeds but this
        # request crashes, retrying uses the same candidate/version idempotency key.
        control_session.commit()
        potential = PotentialRecordService(request.app.state.potential_database)
        if candidate_view.status == ControlPotentialCandidateStatus.ACCEPTED:
            if candidate_view.potential_record_id is None:
                raise DomainError(
                    "CONTROL_CANDIDATE_ACCEPTANCE_INCOMPLETE",
                    "候选状态与潜在记录回执不一致，需要管理员核对。",
                    status_code=409,
                )
            record = potential.get(
                candidate_view.potential_record_id,
                company_id=UUID(project.company_id),
                project_id=project_id,
            )
            return ControlPotentialCandidateAcceptance(candidate=candidate_view, record=record)
        candidate = candidate_view.candidate
        record = potential.accept_candidate(
            candidate,
            expected_hash=candidate.payload_hash(),
            actor_id=settings.local_actor_id,
            idempotency_key=(
                f"candidate:{candidate_id}:v{candidate_view.version}:"
                f"{candidate_view.payload_sha256}"
            ),
        )
        candidate_view = candidates.finish_accept(
            project_id,
            candidate_id,
            expected_hash=candidate_view.payload_sha256,
            potential_record_id=record.id,
            actor_id=settings.local_actor_id,
        )
        return ControlPotentialCandidateAcceptance(candidate=candidate_view, record=record)

    @router.post(
        "/projects/{project_id}/potential-candidates/{candidate_id}/reject",
        response_model=ControlPotentialCandidateView,
        tags=["potential-knowledge"],
    )
    def reject_potential_candidate(
        project_id: UUID,
        candidate_id: UUID,
        payload: ControlPotentialCandidateDecision,
        session: Annotated[Session, Depends(get_session)],
        control_session: Annotated[Session, Depends(get_control_session)],
    ) -> ControlPotentialCandidateView:
        PortfolioService(session).require_project(project_id)
        return ControlPotentialCandidateService(control_session).reject(
            project_id,
            candidate_id,
            payload,
            actor_id=settings.local_actor_id,
        )

    @router.patch(
        "/projects/{project_id}/potential-records/{record_id}",
        response_model=PotentialRecordView,
        tags=["potential-knowledge"],
    )
    def edit_potential_record(
        project_id: UUID,
        record_id: UUID,
        payload: PotentialRecordUpdate,
        request: Request,
        session: Annotated[Session, Depends(get_session)],
    ) -> PotentialRecordView:
        project = PortfolioService(session).require_project(project_id)
        return PotentialRecordService(request.app.state.potential_database).edit(
            record_id,
            payload,
            company_id=UUID(project.company_id),
            project_id=project_id,
            actor_id=settings.local_actor_id,
        )

    @router.get(
        "/projects/{project_id}/potential-records/{record_id}/history",
        response_model=PotentialHistoryView,
        tags=["potential-knowledge"],
    )
    def get_potential_record_history(
        project_id: UUID,
        record_id: UUID,
        request: Request,
        session: Annotated[Session, Depends(get_session)],
    ) -> PotentialHistoryView:
        project = PortfolioService(session).require_project(project_id)
        return PotentialRecordService(request.app.state.potential_database).history(
            record_id, company_id=UUID(project.company_id), project_id=project_id
        )

    @router.post(
        "/projects/{project_id}/potential-records/{record_id}/accept",
        response_model=PotentialRecordView,
        tags=["potential-knowledge"],
    )
    def accept_potential_record(
        project_id: UUID,
        record_id: UUID,
        payload: PotentialRecordStatusRequest,
        request: Request,
        session: Annotated[Session, Depends(get_session)],
    ) -> PotentialRecordView:
        project = PortfolioService(session).require_project(project_id)
        return PotentialRecordService(request.app.state.potential_database).accept(
            record_id,
            company_id=UUID(project.company_id),
            project_id=project_id,
            expected_version=payload.expected_version,
            actor_id=settings.local_actor_id,
            reason=payload.reason,
        )

    @router.post(
        "/projects/{project_id}/potential-records/{record_id}/reject",
        response_model=PotentialRecordView,
        tags=["potential-knowledge"],
    )
    def reject_potential_record(
        project_id: UUID,
        record_id: UUID,
        payload: PotentialRecordStatusRequest,
        request: Request,
        session: Annotated[Session, Depends(get_session)],
    ) -> PotentialRecordView:
        project = PortfolioService(session).require_project(project_id)
        return PotentialRecordService(request.app.state.potential_database).reject(
            record_id,
            company_id=UUID(project.company_id),
            project_id=project_id,
            expected_version=payload.expected_version,
            actor_id=settings.local_actor_id,
            reason=payload.reason or "人工拒绝，未提供补充理由。",
        )

    @router.post(
        "/projects/{project_id}/potential-records/{record_id}/withdraw",
        response_model=PotentialRecordView,
        tags=["potential-knowledge"],
    )
    def withdraw_potential_record(
        project_id: UUID,
        record_id: UUID,
        payload: PotentialRecordStatusRequest,
        request: Request,
        session: Annotated[Session, Depends(get_session)],
    ) -> PotentialRecordView:
        project = PortfolioService(session).require_project(project_id)
        return PotentialRecordService(request.app.state.potential_database).withdraw(
            record_id,
            company_id=UUID(project.company_id),
            project_id=project_id,
            expected_version=payload.expected_version,
            actor_id=settings.local_actor_id,
            reason=payload.reason,
        )

    @router.get(
        "/projects/{project_id}/agent-threads",
        response_model=Page[AgentThreadView],
        tags=["agents"],
    )
    def list_agent_threads(
        project_id: UUID,
        session: Annotated[Session, Depends(get_session)],
        include_trashed: bool = False,
    ) -> Page[AgentThreadView]:
        items = CollaborationService(session).list_threads(
            project_id, include_trashed=include_trashed
        )
        return Page(items=items, total=len(items))

    @router.post(
        "/projects/{project_id}/agent-threads",
        response_model=AgentThreadView,
        status_code=status.HTTP_201_CREATED,
        tags=["agents"],
    )
    def create_agent_thread(
        project_id: UUID,
        payload: AgentThreadCreate,
        session: Annotated[Session, Depends(get_session)],
    ) -> AgentThreadView:
        if payload.agent_kind == AgentKind.MANAGEMENT_INPUT and not payload.title:
            payload = payload.model_copy(update={"title": "新的管理输入对话"})
        return CollaborationService(session).create_thread(project_id, payload)

    @router.patch(
        "/projects/{project_id}/agent-threads/{thread_id}",
        response_model=AgentThreadView,
        tags=["agents"],
    )
    def update_agent_thread(
        project_id: UUID,
        thread_id: UUID,
        payload: AgentThreadUpdate,
        session: Annotated[Session, Depends(get_session)],
    ) -> AgentThreadView:
        return CollaborationService(session).update_thread(project_id, thread_id, payload)

    @router.delete(
        "/projects/{project_id}/agent-threads/{thread_id}",
        response_model=AgentThreadView,
        tags=["agents"],
    )
    def trash_agent_thread(
        project_id: UUID,
        thread_id: UUID,
        session: Annotated[Session, Depends(get_session)],
    ) -> AgentThreadView:
        return CollaborationService(session).update_thread(
            project_id, thread_id, AgentThreadUpdate(trashed=True)
        )

    @router.get(
        "/projects/{project_id}/agent-threads/{thread_id}/messages",
        response_model=Page[AgentMessageView],
        tags=["agents"],
    )
    def list_agent_messages(
        project_id: UUID,
        thread_id: UUID,
        session: Annotated[Session, Depends(get_session)],
        observation_session: Annotated[Session, Depends(get_observation_session)],
    ) -> Page[AgentMessageView]:
        collaboration = CollaborationService(session)
        thread = collaboration.require_thread(project_id, thread_id)
        items = collaboration.list_messages(project_id, thread_id)
        if thread.agent_kind == AgentKind.MANAGEMENT_INPUT.value:
            observation_service = ObservationService(observation_session, settings)
            hydrated: list[AgentMessageView] = []
            for item in items:
                row = session.get(AgentMessageRow, str(item.id))
                if row is None:
                    hydrated.append(item)
                    continue
                if row.content.startswith("management-input-observation:"):
                    parts = row.content.split(":")
                    if len(parts) == 3:
                        observation_id = UUID(parts[1])
                        source_revision = int(parts[2])
                        observation_service.get(project_id, observation_id)
                        source_version = observation_session.scalar(
                            select(ObservationVersionRow).where(
                                ObservationVersionRow.observation_id == str(observation_id),
                                ObservationVersionRow.revision == source_revision,
                            )
                        )
                        if source_version is None:
                            raise DomainError(
                                "OBSERVATION_SOURCE_VERSION_NOT_FOUND",
                                "对话引用的原始材料版本不存在。",
                                status_code=404,
                            )
                        hydrated.append(
                            item.model_copy(
                                update={
                                    "content": source_version.snapshot["content"],
                                    "source_observation_id": observation_id,
                                    "source_revision": source_revision,
                                }
                            )
                        )
                        continue
                if row.content.startswith("management-input-extraction:"):
                    parts = row.content.split(":")
                    if len(parts) == 4:
                        observation_id = UUID(parts[1])
                        extraction_id = UUID(parts[2])
                        source_revision = int(parts[3])
                        observation_service.get(project_id, observation_id)
                        extraction = observation_session.get(
                            ObservationExtractionRow, str(extraction_id)
                        )
                        if (
                            extraction is None
                            or extraction.observation_id != str(observation_id)
                        ):
                            raise DomainError(
                                "OBSERVATION_EXTRACTION_NOT_FOUND",
                                "对话引用的信息整理结果不存在。",
                                status_code=404,
                            )
                        hydrated.append(
                            item.model_copy(
                                update={
                                    "content": _render_management_input_extraction(extraction),
                                    "source_observation_id": observation_id,
                                    "source_revision": source_revision,
                                    "external_model_used": True,
                                }
                            )
                        )
                        continue
                hydrated.append(item)
            items = hydrated
        return Page(items=items, total=len(items))

    @router.post(
        "/projects/{project_id}/agent-threads/{thread_id}/messages",
        response_model=AgentMessageAccepted,
        status_code=status.HTTP_202_ACCEPTED,
        tags=["agents"],
    )
    def send_agent_message(
        project_id: UUID,
        thread_id: UUID,
        payload: AgentMessageCreate,
        session: Annotated[Session, Depends(get_session)],
        observation_session: Annotated[Session, Depends(get_observation_session)],
    ) -> AgentMessageAccepted:
        collaboration = CollaborationService(session)
        thread = collaboration.require_thread(project_id, thread_id)
        if thread.agent_kind == AgentKind.MANAGEMENT_INPUT.value:
            if payload.share_project_context_with_model:
                raise DomainError(
                    "MANAGEMENT_INPUT_PROJECT_CONTEXT_SHARING_DISABLED",
                    "管理输入 Agent 只处理当前原始材料，不允许向模型共享正式项目上下文。",
                    status_code=422,
                )
            if payload.attachment_ids:
                raise DomainError(
                    "MANAGEMENT_INPUT_ATTACHMENT_INGESTION_REQUIRED",
                    "管理输入对话不能引用正式模型附件；请先通过管理观察文件导入，将原件存入观察库。",
                    status_code=422,
                )
            project = PortfolioService(session).require_project(project_id)
            retry_token = payload.idempotency_key or str(uuid4())
            request_identity = sha256(
                f"management-input:{project_id}:{thread_id}:{retry_token}".encode()
            ).hexdigest()
            message_id = uuid5(NAMESPACE_URL, request_identity)
            observation_writer = (
                session
                if _uses_same_database(settings, session, observation_session)
                else observation_session
            )
            observation = ObservationService(observation_writer, settings).create(
                project,
                project_id,
                ObservationCreate(
                    kind="OTHER",
                    title=thread.title[:200] or "管理输入对话",
                    content=payload.content,
                    idempotency_key=request_identity,
                ),
            )
            message = session.get(AgentMessageRow, str(message_id))
            run = None
            if message is not None:
                prior_runs = session.scalars(
                    select(AgentRunRow)
                    .where(
                        AgentRunRow.thread_id == thread.id,
                        AgentRunRow.agent_kind == AgentKind.MANAGEMENT_INPUT.value,
                    )
                    .order_by(AgentRunRow.created_at.desc())
                ).all()
                run = next(
                    (
                        item
                        for item in prior_runs
                        if (item.context_manifest or {}).get("management_input_request_id")
                        == request_identity
                    ),
                    None,
                )
                if run is not None:
                    stored_manifest = run.context_manifest or {}
                    if (
                        stored_manifest.get("allow_external_model")
                        != payload.allow_external_model
                        or stored_manifest.get("model_profile_id")
                        != (
                            str(payload.model_profile_id)
                            if payload.model_profile_id
                            else None
                        )
                    ):
                        raise DomainError(
                            "MANAGEMENT_INPUT_IDEMPOTENCY_CONFLICT",
                            "相同幂等键不能用于不同模型或外发许可设置。",
                            status_code=409,
                        )
                    return AgentMessageAccepted(
                        message=AgentMessageView(
                            id=message_id,
                            thread_id=thread_id,
                            role=AgentMessageRole.USER,
                            content=payload.content,
                            source_observation_id=observation.id,
                            source_revision=observation.revision,
                            created_at=message.created_at,
                        ),
                        run=AgentRunView.model_validate(run),
                    )
            if message is None:
                message = AgentMessageRow(
                    id=str(message_id),
                    thread_id=thread.id,
                    role=AgentMessageRole.USER.value,
                    content=(
                        f"management-input-observation:{observation.id}:{observation.revision}"
                    ),
                    citations=[],
                )
                session.add(message)
            if run is None:
                run = AgentRunRow(
                    thread_id=thread.id,
                    project_id=str(project_id),
                    agent_kind=AgentKind.MANAGEMENT_INPUT.value,
                    status=AgentRunStatus.QUEUED.value,
                    context_manifest={
                        "source_observation_id": str(observation.id),
                        "source_revision": observation.revision,
                        "management_input_request_id": request_identity,
                        "allow_external_model": payload.allow_external_model,
                        "model_profile_id": (
                            str(payload.model_profile_id) if payload.model_profile_id else None
                        ),
                        "share_project_context_with_model": False,
                        "management_input_boundary": "OBSERVATION_STORE_ONLY",
                    },
                )
                session.add(run)
            session.flush()
            return AgentMessageAccepted(
                message=AgentMessageView(
                    id=message_id,
                    thread_id=thread_id,
                    role=AgentMessageRole.USER,
                    content=payload.content,
                    source_observation_id=observation.id,
                    source_revision=observation.revision,
                    created_at=message.created_at,
                ),
                run=AgentRunView.model_validate(run),
            )
        return CollaborationService(session).send_message(project_id, thread_id, payload)

    @router.get(
        "/projects/{project_id}/agent-runs",
        response_model=Page[AgentRunView],
        tags=["agents"],
    )
    def list_agent_runs(
        project_id: UUID,
        session: Annotated[Session, Depends(get_session)],
        thread_id: UUID | None = None,
    ) -> Page[AgentRunView]:
        items = CollaborationService(session).list_runs(project_id, thread_id)
        return Page(items=items, total=len(items))

    @router.get(
        "/projects/{project_id}/agent-runs/{run_id}",
        response_model=AgentRunView,
        tags=["agents"],
    )
    def get_agent_run(
        project_id: UUID,
        run_id: UUID,
        session: Annotated[Session, Depends(get_session)],
    ) -> AgentRunView:
        return CollaborationService(session).get_run(project_id, run_id)

    @router.get(
        "/projects/{project_id}/agent-runs/{run_id}/steps",
        response_model=Page[AgentStepView],
        tags=["agents"],
    )
    def list_agent_steps(
        project_id: UUID,
        run_id: UUID,
        session: Annotated[Session, Depends(get_session)],
    ) -> Page[AgentStepView]:
        items = CollaborationService(session).list_steps(project_id, run_id)
        return Page(items=items, total=len(items))

    @router.post(
        "/projects/{project_id}/agent-runs/{run_id}/cancel",
        response_model=AgentRunView,
        tags=["agents"],
    )
    def cancel_agent_run(
        project_id: UUID,
        run_id: UUID,
        session: Annotated[Session, Depends(get_session)],
    ) -> AgentRunView:
        return CollaborationService(session).cancel_run(project_id, run_id)

    @router.post(
        "/projects/{project_id}/agent-runs/{run_id}/execute",
        response_model=AgentRunView,
        tags=["agents"],
    )
    def execute_agent_run(
        project_id: UUID,
        run_id: UUID,
        request: Request,
        session: Annotated[Session, Depends(get_session)],
    ) -> AgentRunView:
        if settings.agent_worker_enabled:
            raise DomainError(
                "AGENT_RUN_MANAGED_BY_WORKER",
                "Agent 任务已由后台队列执行，请轮询运行状态。",
                status_code=409,
            )
        return AgentRuntimeService(
            session,
            settings,
            request.app.state.observation_database,
            request.app.state.potential_database,
        ).process_run(project_id, run_id)

    @router.post(
        "/projects/{project_id}/agent-runs/{run_id}/continue",
        response_model=AgentRunView,
        status_code=status.HTTP_202_ACCEPTED,
        tags=["agents"],
    )
    def continue_agent_run(
        project_id: UUID,
        run_id: UUID,
        request: Request,
        session: Annotated[Session, Depends(get_session)],
    ) -> AgentRunView:
        service = AgentRuntimeService(
            session,
            settings,
            request.app.state.observation_database,
            request.app.state.potential_database,
        )
        run = service.prepare_resume(project_id, run_id)
        if settings.agent_worker_enabled:
            return run
        return service.process_run(project_id, run_id)

    @router.get(
        "/projects/{project_id}/action-definitions",
        response_model=Page[ActionDefinitionView],
        tags=["actions"],
    )
    def list_action_definitions(
        project_id: UUID, session: Annotated[Session, Depends(get_session)]
    ) -> Page[ActionDefinitionView]:
        items = ActionService(session, settings=settings).list_definitions(project_id)
        return Page(items=items, total=len(items))

    @router.post(
        "/projects/{project_id}/action-definitions",
        response_model=ActionDefinitionView,
        status_code=status.HTTP_201_CREATED,
        tags=["actions"],
    )
    def create_action_definition(
        project_id: UUID,
        payload: ActionDefinitionCreate,
        session: Annotated[Session, Depends(get_session)],
    ) -> ActionDefinitionView:
        return ActionService(session, settings=settings).create_definition(project_id, payload)

    @router.get(
        "/projects/{project_id}/action-definitions/{action_id}",
        response_model=ActionDefinitionView,
        tags=["actions"],
    )
    def get_action_definition(
        project_id: UUID,
        action_id: UUID,
        session: Annotated[Session, Depends(get_session)],
    ) -> ActionDefinitionView:
        return ActionService(session, settings=settings).get_definition(project_id, action_id)

    @router.patch(
        "/projects/{project_id}/action-definitions/{action_id}",
        response_model=ActionDefinitionView,
        tags=["actions"],
    )
    def update_action_definition(
        project_id: UUID,
        action_id: UUID,
        payload: ActionDefinitionUpdate,
        session: Annotated[Session, Depends(get_session)],
    ) -> ActionDefinitionView:
        return ActionService(session, settings=settings).update_definition(
            project_id, action_id, payload
        )

    @router.post(
        "/projects/{project_id}/action-definitions/{action_id}/validate",
        response_model=ActionDefinitionView,
        tags=["actions"],
    )
    def validate_action_definition(
        project_id: UUID,
        action_id: UUID,
        session: Annotated[Session, Depends(get_session)],
    ) -> ActionDefinitionView:
        return ActionService(session, settings=settings).validate_definition(project_id, action_id)

    @router.get(
        "/projects/{project_id}/action-invocations",
        response_model=Page[ActionInvocationView],
        tags=["actions"],
    )
    def list_action_invocations(
        project_id: UUID,
        session: Annotated[Session, Depends(get_session)],
        action_status: Annotated[ActionInvocationStatus | None, Query(alias="status")] = None,
    ) -> Page[ActionInvocationView]:
        items = ActionService(session, settings=settings).list_invocations(
            project_id, status=action_status
        )
        return Page(items=items, total=len(items))

    @router.post(
        "/projects/{project_id}/action-invocations",
        response_model=ActionInvocationView,
        status_code=status.HTTP_201_CREATED,
        tags=["actions"],
    )
    def create_action_invocation(
        project_id: UUID,
        payload: ActionInvocationCreate,
        session: Annotated[Session, Depends(get_session)],
    ) -> ActionInvocationView:
        return ActionService(session, settings=settings).create_invocation(project_id, payload)

    @router.get(
        "/projects/{project_id}/action-invocations/{invocation_id}",
        response_model=ActionInvocationView,
        tags=["actions"],
    )
    def get_action_invocation(
        project_id: UUID,
        invocation_id: UUID,
        session: Annotated[Session, Depends(get_session)],
    ) -> ActionInvocationView:
        return ActionService(session, settings=settings).get_invocation(project_id, invocation_id)

    @router.post(
        "/projects/{project_id}/action-invocations/{invocation_id}/dry-run",
        response_model=ActionInvocationView,
        tags=["actions"],
    )
    def dry_run_action(
        project_id: UUID,
        invocation_id: UUID,
        request: Request,
        session: Annotated[Session, Depends(get_session)],
    ) -> ActionInvocationView:
        return ActionService(
            session,
            request.app.state.observation_database,
            request.app.state.potential_database,
            settings=settings,
        ).dry_run(project_id, invocation_id)

    @router.post(
        "/projects/{project_id}/action-invocations/{invocation_id}/approve",
        response_model=ActionInvocationView,
        tags=["actions"],
    )
    def approve_action(
        project_id: UUID,
        invocation_id: UUID,
        payload: ActionApprovalRequest,
        session: Annotated[Session, Depends(get_session)],
    ) -> ActionInvocationView:
        return ActionService(session, settings=settings).approve(project_id, invocation_id, payload)

    @router.post(
        "/projects/{project_id}/action-invocations/{invocation_id}/execute",
        response_model=ActionInvocationView,
        tags=["actions"],
    )
    def execute_action(
        project_id: UUID,
        invocation_id: UUID,
        request: Request,
        session: Annotated[Session, Depends(get_session)],
    ) -> ActionInvocationView:
        return ActionService(
            session,
            request.app.state.observation_database,
            request.app.state.potential_database,
            settings=settings,
        ).execute(project_id, invocation_id)

    @router.post(
        "/projects/{project_id}/action-invocations/{invocation_id}/reconcile",
        response_model=ActionInvocationView,
        tags=["actions"],
    )
    def reconcile_action_invocation(
        project_id: UUID,
        invocation_id: UUID,
        session: Annotated[Session, Depends(get_session)],
    ) -> ActionInvocationView:
        return ActionService(session, settings=settings).reconcile(project_id, invocation_id)

    @router.post(
        "/projects/{project_id}/action-invocations/{invocation_id}/cancel",
        response_model=ActionInvocationView,
        tags=["actions"],
    )
    def cancel_action(
        project_id: UUID,
        invocation_id: UUID,
        session: Annotated[Session, Depends(get_session)],
    ) -> ActionInvocationView:
        return ActionService(session, settings=settings).cancel(project_id, invocation_id)

    @router.post(
        "/projects/{project_id}/action-invocations/{invocation_id}/retry",
        response_model=ActionInvocationView,
        status_code=status.HTTP_201_CREATED,
        tags=["actions"],
    )
    def retry_action(
        project_id: UUID,
        invocation_id: UUID,
        payload: ActionRetryRequest,
        session: Annotated[Session, Depends(get_session)],
    ) -> ActionInvocationView:
        return ActionService(session, settings=settings).retry(project_id, invocation_id, payload)

    @router.post(
        "/projects/{project_id}/action-invocations/{invocation_id}/rollback",
        response_model=ActionInvocationView,
        tags=["actions"],
    )
    def rollback_action(
        project_id: UUID,
        invocation_id: UUID,
        session: Annotated[Session, Depends(get_session)],
    ) -> ActionInvocationView:
        return ActionService(session, settings=settings).rollback(project_id, invocation_id)

    @router.get(
        "/projects/{project_id}/action-logs",
        response_model=Page[ActionLogView],
        tags=["actions"],
    )
    def list_action_logs(
        project_id: UUID,
        session: Annotated[Session, Depends(get_session)],
        invocation_id: UUID | None = None,
    ) -> Page[ActionLogView]:
        items = ActionService(session, settings=settings).list_logs(project_id, invocation_id)
        return Page(items=items, total=len(items))

    @router.get(
        "/projects/{project_id}/action-invocations/{invocation_id}/observations",
        response_model=Page[ActionObservationView],
        tags=["actions"],
    )
    def list_action_observations(
        project_id: UUID,
        invocation_id: UUID,
        session: Annotated[Session, Depends(get_session)],
    ) -> Page[ActionObservationView]:
        items = ActionService(session, settings=settings).list_observations(
            project_id, invocation_id
        )
        return Page(items=items, total=len(items))

    @router.post(
        "/projects/{project_id}/action-invocations/{invocation_id}/observations",
        response_model=ActionObservationView,
        status_code=status.HTTP_201_CREATED,
        tags=["actions"],
    )
    def add_action_observation(
        project_id: UUID,
        invocation_id: UUID,
        payload: ActionObservationCreate,
        session: Annotated[Session, Depends(get_session)],
    ) -> ActionObservationView:
        return ActionService(session, settings=settings).add_observation(
            project_id, invocation_id, payload
        )

    @router.get(
        "/exploration/modules",
        response_model=Page[ExplorationModuleView],
        tags=["exploration"],
    )
    def list_exploration_modules() -> Page[ExplorationModuleView]:
        items = ExplorationService.modules()
        return Page(items=items, total=len(items))

    @router.get(
        "/projects/{project_id}/hypotheses",
        response_model=Page[HypothesisView],
        tags=["exploration"],
    )
    def list_hypotheses(
        project_id: UUID, session: Annotated[Session, Depends(get_session)]
    ) -> Page[HypothesisView]:
        items = ExplorationService(session).list_hypotheses(project_id)
        return Page(items=items, total=len(items))

    @router.post(
        "/projects/{project_id}/hypotheses",
        response_model=HypothesisView,
        status_code=status.HTTP_201_CREATED,
        tags=["exploration"],
    )
    def create_hypothesis(
        project_id: UUID,
        payload: HypothesisCreate,
        session: Annotated[Session, Depends(get_session)],
    ) -> HypothesisView:
        return ExplorationService(session).create_hypothesis(project_id, payload)

    @router.post(
        "/projects/{project_id}/hypotheses/{hypothesis_id}/feedback",
        response_model=HypothesisFeedbackView,
        status_code=status.HTTP_201_CREATED,
        tags=["exploration"],
    )
    def add_hypothesis_feedback(
        project_id: UUID,
        hypothesis_id: UUID,
        payload: HypothesisFeedbackCreate,
        session: Annotated[Session, Depends(get_session)],
    ) -> HypothesisFeedbackView:
        return ExplorationService(session).add_feedback(project_id, hypothesis_id, payload)

    @router.get(
        "/projects/{project_id}/causal-hypotheses",
        response_model=Page[CausalHypothesisView],
        tags=["exploration"],
    )
    def list_causal_hypotheses(
        project_id: UUID,
        session: Annotated[Session, Depends(get_session)],
    ) -> Page[CausalHypothesisView]:
        items = ExplorationService(session).list_causal_hypotheses(project_id)
        return Page(items=items, total=len(items))

    @router.post(
        "/projects/{project_id}/causal-hypotheses",
        response_model=CausalHypothesisView,
        status_code=status.HTTP_201_CREATED,
        tags=["exploration"],
    )
    def create_causal_hypothesis(
        project_id: UUID,
        payload: CausalHypothesisCreate,
        session: Annotated[Session, Depends(get_session)],
    ) -> CausalHypothesisView:
        return ExplorationService(session).create_causal_hypothesis(project_id, payload)

    @router.get(
        "/projects/{project_id}/scenarios",
        response_model=Page[ScenarioView],
        tags=["exploration"],
    )
    def list_scenarios(
        project_id: UUID, session: Annotated[Session, Depends(get_session)]
    ) -> Page[ScenarioView]:
        items = ExplorationService(session).list_scenarios(project_id)
        return Page(items=items, total=len(items))

    @router.post(
        "/projects/{project_id}/scenarios",
        response_model=ScenarioView,
        status_code=status.HTTP_201_CREATED,
        tags=["exploration"],
    )
    def create_scenario(
        project_id: UUID,
        payload: ScenarioCreate,
        session: Annotated[Session, Depends(get_session)],
    ) -> ScenarioView:
        return ExplorationService(session).create_scenario(project_id, payload)

    @router.patch(
        "/projects/{project_id}/scenarios/{scenario_id}",
        response_model=ScenarioView,
        tags=["exploration"],
    )
    def update_scenario(
        project_id: UUID,
        scenario_id: UUID,
        payload: ScenarioUpdate,
        session: Annotated[Session, Depends(get_session)],
    ) -> ScenarioView:
        return ExplorationService(session).update_scenario(project_id, scenario_id, payload)

    @router.get(
        "/projects/{project_id}/scenarios/{scenario_id}/diff",
        response_model=ScenarioDiffView,
        tags=["exploration"],
    )
    def get_scenario_diff(
        project_id: UUID,
        scenario_id: UUID,
        session: Annotated[Session, Depends(get_session)],
    ) -> ScenarioDiffView:
        return ExplorationService(session).scenario_diff(project_id, scenario_id)

    @router.post(
        "/projects/{project_id}/scenario-comparisons",
        response_model=ScenarioComparisonView,
        tags=["exploration"],
    )
    def compare_scenarios(
        project_id: UUID,
        payload: ScenarioCompareRequest,
        session: Annotated[Session, Depends(get_session)],
    ) -> ScenarioComparisonView:
        return ExplorationService(session).compare_scenarios(
            project_id, payload.left_scenario_id, payload.right_scenario_id
        )

    @router.post(
        "/projects/{project_id}/scenarios/{scenario_id}/rebase",
        response_model=ScenarioView,
        tags=["exploration"],
    )
    def rebase_scenario(
        project_id: UUID,
        scenario_id: UUID,
        payload: ScenarioRevisionRequest,
        session: Annotated[Session, Depends(get_session)],
    ) -> ScenarioView:
        return ExplorationService(session).rebase_scenario(project_id, scenario_id, payload)

    @router.post(
        "/projects/{project_id}/scenarios/{scenario_id}/apply",
        response_model=ScenarioView,
        tags=["exploration"],
    )
    def apply_scenario(
        project_id: UUID,
        scenario_id: UUID,
        payload: ScenarioRevisionRequest,
        session: Annotated[Session, Depends(get_session)],
    ) -> ScenarioView:
        return ExplorationService(session).apply_scenario(project_id, scenario_id, payload)

    @router.get(
        "/projects/{project_id}/scenarios/{scenario_id}/runs",
        response_model=Page[ScenarioRunView],
        tags=["exploration"],
    )
    def list_scenario_runs(
        project_id: UUID,
        scenario_id: UUID,
        session: Annotated[Session, Depends(get_session)],
    ) -> Page[ScenarioRunView]:
        items = ScenarioRunService(session).list(project_id, scenario_id)
        return Page(items=items, total=len(items))

    @router.post(
        "/projects/{project_id}/scenarios/{scenario_id}/runs",
        response_model=ScenarioRunView,
        status_code=status.HTTP_201_CREATED,
        tags=["exploration"],
    )
    def run_scenario(
        project_id: UUID,
        scenario_id: UUID,
        payload: ScenarioSimulationRequest,
        session: Annotated[Session, Depends(get_session)],
    ) -> ScenarioRunView:
        return ScenarioRunService(session).run(project_id, scenario_id, payload)

    @router.post(
        "/projects/{project_id}/scenario-run-comparisons",
        response_model=ScenarioRunComparisonView,
        tags=["exploration"],
    )
    def compare_scenario_runs(
        project_id: UUID,
        payload: ScenarioRunCompareRequest,
        session: Annotated[Session, Depends(get_session)],
    ) -> ScenarioRunComparisonView:
        return ScenarioRunService(session).compare(project_id, payload)

    @router.get(
        "/projects/{project_id}/management/metrics",
        response_model=Page[MetricDefinitionView],
        tags=["management-intelligence"],
    )
    def list_management_metrics(
        project_id: UUID,
        session: Annotated[Session, Depends(get_session)],
    ) -> Page[MetricDefinitionView]:
        items = ManagementIntelligenceService(session).list_metric_definitions(project_id)
        return Page(items=items, total=len(items))

    @router.get(
        "/projects/{project_id}/management/actions",
        response_model=Page[ManagementActionView],
        tags=["management-actions"],
    )
    def list_management_actions(
        project_id: UUID,
        session: Annotated[Session, Depends(get_session)],
        include_cancelled: Annotated[bool, Query()] = True,
        offset: Annotated[int, Query(ge=0)] = 0,
        limit: Annotated[int, Query(ge=1, le=100)] = 50,
    ) -> Page[ManagementActionView]:
        return ManagementActionService(session).list(
            project_id,
            include_cancelled=include_cancelled,
            offset=offset,
            limit=limit,
        )

    @router.post(
        "/projects/{project_id}/management/actions",
        response_model=ManagementActionView,
        status_code=status.HTTP_201_CREATED,
        tags=["management-actions"],
    )
    def create_management_action(
        project_id: UUID,
        payload: ManagementActionCreate,
        session: Annotated[Session, Depends(get_session)],
    ) -> ManagementActionView:
        return ManagementActionService(session).create(
            project_id, payload, actor_id=settings.local_actor_id
        )

    @router.get(
        "/projects/{project_id}/management/actions/{action_id}",
        response_model=ManagementActionView,
        tags=["management-actions"],
    )
    def get_management_action(
        project_id: UUID,
        action_id: UUID,
        session: Annotated[Session, Depends(get_session)],
    ) -> ManagementActionView:
        return ManagementActionService(session).get(project_id, action_id)

    @router.patch(
        "/projects/{project_id}/management/actions/{action_id}",
        response_model=ManagementActionView,
        tags=["management-actions"],
    )
    def update_management_action(
        project_id: UUID,
        action_id: UUID,
        payload: ManagementActionUpdate,
        session: Annotated[Session, Depends(get_session)],
    ) -> ManagementActionView:
        return ManagementActionService(session).update(
            project_id, action_id, payload, actor_id=settings.local_actor_id
        )

    @router.post(
        "/projects/{project_id}/management/actions/{action_id}/cancel",
        response_model=ManagementActionView,
        tags=["management-actions"],
    )
    def cancel_management_action(
        project_id: UUID,
        action_id: UUID,
        payload: ManagementActionRevisionRequest,
        session: Annotated[Session, Depends(get_session)],
    ) -> ManagementActionView:
        return ManagementActionService(session).cancel(
            project_id, action_id, payload, actor_id=settings.local_actor_id
        )

    @router.post(
        "/projects/{project_id}/management/actions/{action_id}/progress",
        response_model=ManagementActionEventView,
        status_code=status.HTTP_201_CREATED,
        tags=["management-actions"],
    )
    def append_management_action_progress(
        project_id: UUID,
        action_id: UUID,
        payload: ManagementActionEventCreate,
        session: Annotated[Session, Depends(get_session)],
    ) -> ManagementActionEventView:
        return ManagementActionService(session).append_progress(
            project_id, action_id, payload, actor_id=settings.local_actor_id
        )

    @router.post(
        "/projects/{project_id}/management/actions/{action_id}/outcomes",
        response_model=ManagementActionEventView,
        status_code=status.HTTP_201_CREATED,
        tags=["management-actions"],
    )
    def append_management_action_outcome(
        project_id: UUID,
        action_id: UUID,
        payload: ManagementActionEventCreate,
        session: Annotated[Session, Depends(get_session)],
    ) -> ManagementActionEventView:
        return ManagementActionService(session).append_outcome(
            project_id, action_id, payload, actor_id=settings.local_actor_id
        )

    @router.post(
        "/projects/{project_id}/management/actions/{action_id}/report-done",
        response_model=ManagementActionView,
        tags=["management-actions"],
    )
    def report_management_action_done(
        project_id: UUID,
        action_id: UUID,
        payload: ManagementActionEventCreate,
        session: Annotated[Session, Depends(get_session)],
    ) -> ManagementActionView:
        return ManagementActionService(session).report_done(
            project_id, action_id, payload, actor_id=settings.local_actor_id
        )

    @router.post(
        "/projects/{project_id}/management/actions/{action_id}/verify-done",
        response_model=ManagementActionView,
        tags=["management-actions"],
    )
    def verify_management_action_done(
        project_id: UUID,
        action_id: UUID,
        payload: ManagementActionVerifyDone,
        session: Annotated[Session, Depends(get_session)],
    ) -> ManagementActionView:
        return ManagementActionService(session).verify_done(
            project_id, action_id, payload, actor_id=settings.local_actor_id
        )

    @router.get(
        "/projects/{project_id}/management/actions/{action_id}/history",
        response_model=Page[ManagementActionEventView],
        tags=["management-actions"],
    )
    def get_management_action_history(
        project_id: UUID,
        action_id: UUID,
        session: Annotated[Session, Depends(get_session)],
        offset: Annotated[int, Query(ge=0)] = 0,
        limit: Annotated[int, Query(ge=1, le=100)] = 50,
    ) -> Page[ManagementActionEventView]:
        return ManagementActionService(session).history(
            project_id, action_id, offset=offset, limit=limit
        )

    @router.post(
        "/projects/{project_id}/management/metrics",
        response_model=MetricDefinitionView,
        status_code=status.HTTP_201_CREATED,
        tags=["management-intelligence"],
    )
    def create_management_metric(
        project_id: UUID,
        payload: MetricDefinitionCreate,
        session: Annotated[Session, Depends(get_session)],
    ) -> MetricDefinitionView:
        return ManagementIntelligenceService(session).create_metric_definition(
            project_id, payload
        )

    @router.patch(
        "/projects/{project_id}/management/metrics/{metric_id}",
        response_model=MetricDefinitionView,
        tags=["management-intelligence"],
    )
    def update_management_metric(
        project_id: UUID,
        metric_id: UUID,
        payload: MetricDefinitionUpdate,
        session: Annotated[Session, Depends(get_session)],
    ) -> MetricDefinitionView:
        return ManagementIntelligenceService(session).update_metric_definition(
            project_id, metric_id, payload
        )

    @router.get(
        "/projects/{project_id}/management/metrics/{metric_id}/observations",
        response_model=Page[MetricObservationView],
        tags=["management-intelligence"],
    )
    def list_management_metric_observations(
        project_id: UUID,
        metric_id: UUID,
        session: Annotated[Session, Depends(get_session)],
        include_history: Annotated[bool, Query()] = False,
    ) -> Page[MetricObservationView]:
        items = ManagementIntelligenceService(session).list_metric_observations(
            project_id, metric_id, include_history=include_history
        )
        return Page(items=items, total=len(items))

    @router.post(
        "/projects/{project_id}/management/metrics/{metric_id}/observations",
        response_model=MetricObservationView,
        status_code=status.HTTP_201_CREATED,
        tags=["management-intelligence"],
    )
    def add_management_metric_observation(
        project_id: UUID,
        metric_id: UUID,
        payload: MetricObservationCreate,
        session: Annotated[Session, Depends(get_session)],
    ) -> MetricObservationView:
        return ManagementIntelligenceService(session).add_metric_observation(
            project_id, metric_id, payload
        )

    @router.patch(
        "/projects/{project_id}/management/metrics/{metric_id}/observations/{observation_id}",
        response_model=MetricObservationView,
        tags=["management-intelligence"],
    )
    def update_management_metric_observation(
        project_id: UUID,
        metric_id: UUID,
        observation_id: UUID,
        payload: MetricObservationUpdate,
        session: Annotated[Session, Depends(get_session)],
    ) -> MetricObservationView:
        return ManagementIntelligenceService(session).update_metric_observation(
            project_id, metric_id, observation_id, payload
        )

    @router.post(
        "/projects/{project_id}/management/metrics/{metric_id}/observations/{observation_id}/retire",
        response_model=MetricObservationView,
        tags=["management-intelligence"],
    )
    def retire_management_metric_observation(
        project_id: UUID,
        metric_id: UUID,
        observation_id: UUID,
        payload: MetricObservationRetire,
        session: Annotated[Session, Depends(get_session)],
    ) -> MetricObservationView:
        return ManagementIntelligenceService(session).retire_metric_observation(
            project_id, metric_id, observation_id, payload
        )

    @router.get(
        "/projects/{project_id}/management/meetings",
        response_model=Page[MeetingRecordView],
        tags=["management-intelligence"],
    )
    def list_management_meetings(
        project_id: UUID,
        session: Annotated[Session, Depends(get_session)],
    ) -> Page[MeetingRecordView]:
        items = ManagementIntelligenceService(session).list_meetings(project_id)
        return Page(items=items, total=len(items))

    @router.post(
        "/projects/{project_id}/management/meetings",
        response_model=MeetingRecordView,
        status_code=status.HTTP_201_CREATED,
        tags=["management-intelligence"],
    )
    def create_management_meeting(
        project_id: UUID,
        payload: MeetingRecordCreate,
        session: Annotated[Session, Depends(get_session)],
    ) -> MeetingRecordView:
        return ManagementIntelligenceService(session).create_meeting(project_id, payload)

    @router.patch(
        "/projects/{project_id}/management/meetings/{meeting_id}",
        response_model=MeetingRecordView,
        tags=["management-intelligence"],
    )
    def update_management_meeting(
        project_id: UUID,
        meeting_id: UUID,
        payload: MeetingRecordUpdate,
        session: Annotated[Session, Depends(get_session)],
    ) -> MeetingRecordView:
        return ManagementIntelligenceService(session).update_meeting(
            project_id, meeting_id, payload
        )

    @router.get(
        "/projects/{project_id}/management/tradeoffs",
        response_model=Page[DesignTradeoffView],
        tags=["management-intelligence"],
    )
    def list_design_tradeoffs(
        project_id: UUID,
        session: Annotated[Session, Depends(get_session)],
    ) -> Page[DesignTradeoffView]:
        items = ManagementIntelligenceService(session).list_tradeoffs(project_id)
        return Page(items=items, total=len(items))

    @router.post(
        "/projects/{project_id}/management/tradeoffs",
        response_model=DesignTradeoffView,
        status_code=status.HTTP_201_CREATED,
        tags=["management-intelligence"],
    )
    def create_design_tradeoff(
        project_id: UUID,
        payload: DesignTradeoffCreate,
        session: Annotated[Session, Depends(get_session)],
    ) -> DesignTradeoffView:
        return ManagementIntelligenceService(session).create_tradeoff(project_id, payload)

    @router.patch(
        "/projects/{project_id}/management/tradeoffs/{tradeoff_id}",
        response_model=DesignTradeoffView,
        tags=["management-intelligence"],
    )
    def update_design_tradeoff(
        project_id: UUID,
        tradeoff_id: UUID,
        payload: DesignTradeoffUpdate,
        session: Annotated[Session, Depends(get_session)],
    ) -> DesignTradeoffView:
        return ManagementIntelligenceService(session).update_tradeoff(
            project_id, tradeoff_id, payload
        )

    @router.get(
        "/projects/{project_id}/management/information-requests",
        response_model=Page[InformationRequestView],
        tags=["management-intelligence"],
    )
    def list_information_requests(
        project_id: UUID,
        session: Annotated[Session, Depends(get_session)],
    ) -> Page[InformationRequestView]:
        items = ManagementIntelligenceService(session).list_information_requests(project_id)
        return Page(items=items, total=len(items))

    @router.post(
        "/projects/{project_id}/management/information-requests",
        response_model=InformationRequestView,
        status_code=status.HTTP_201_CREATED,
        tags=["management-intelligence"],
    )
    def create_information_request(
        project_id: UUID,
        payload: InformationRequestCreate,
        session: Annotated[Session, Depends(get_session)],
    ) -> InformationRequestView:
        return ManagementIntelligenceService(session).create_information_request(
            project_id, payload
        )

    @router.patch(
        "/projects/{project_id}/management/information-requests/{request_id}",
        response_model=InformationRequestView,
        tags=["management-intelligence"],
    )
    def update_information_request(
        project_id: UUID,
        request_id: UUID,
        payload: InformationRequestUpdate,
        session: Annotated[Session, Depends(get_session)],
    ) -> InformationRequestView:
        return ManagementIntelligenceService(session).update_information_request(
            project_id, request_id, payload
        )

    @router.post(
        "/projects/{project_id}/management/analysis-runs",
        response_model=ManagementAnalysisRunView,
        status_code=status.HTTP_201_CREATED,
        tags=["management-intelligence"],
    )
    def run_management_analysis(
        project_id: UUID,
        payload: ManagementAnalysisRequest,
        session: Annotated[Session, Depends(get_session)],
    ) -> ManagementAnalysisRunView:
        return ManagementIntelligenceService(session).run_analysis(project_id, payload)

    @router.get(
        "/projects/{project_id}/management/analysis-runs",
        response_model=Page[ManagementAnalysisRunView],
        tags=["management-intelligence"],
    )
    def list_management_analysis_runs(
        project_id: UUID,
        session: Annotated[Session, Depends(get_session)],
    ) -> Page[ManagementAnalysisRunView]:
        items = ManagementIntelligenceService(session).list_analysis_runs(project_id)
        return Page(items=items, total=len(items))

    @router.get(
        "/projects/{project_id}/management/signals",
        response_model=Page[ManagementSignalView],
        tags=["management-intelligence"],
    )
    def list_management_signals(
        project_id: UUID,
        session: Annotated[Session, Depends(get_session)],
        run_id: Annotated[UUID | None, Query()] = None,
        side: Annotated[ManagementSignalSide | None, Query()] = None,
    ) -> Page[ManagementSignalView]:
        items = ManagementIntelligenceService(session).list_signals(
            project_id, run_id=run_id, side=side.value if side else None
        )
        return Page(items=items, total=len(items))

    @router.get(
        "/projects/{project_id}/management/insights",
        response_model=Page[ManagementInsightView],
        tags=["management-intelligence"],
    )
    def list_management_insights(
        project_id: UUID,
        session: Annotated[Session, Depends(get_session)],
        run_id: Annotated[UUID | None, Query()] = None,
    ) -> Page[ManagementInsightView]:
        items = ManagementIntelligenceService(session).list_insights(
            project_id, run_id=run_id
        )
        return Page(items=items, total=len(items))

    @router.patch(
        "/projects/{project_id}/management/insights/{insight_id}",
        response_model=ManagementInsightView,
        tags=["management-intelligence"],
    )
    def update_management_insight(
        project_id: UUID,
        insight_id: UUID,
        payload: ManagementInsightUpdate,
        session: Annotated[Session, Depends(get_session)],
    ) -> ManagementInsightView:
        return ManagementIntelligenceService(session).update_insight(
            project_id, insight_id, payload
        )

    @router.get(
        "/projects/{project_id}/management/issues",
        response_model=Page[ManagementIssueView],
        tags=["management-intelligence"],
    )
    def list_management_issues(
        project_id: UUID,
        session: Annotated[Session, Depends(get_session)],
    ) -> Page[ManagementIssueView]:
        items = ManagementIntelligenceService(session).list_issues(project_id)
        return Page(items=items, total=len(items))

    @router.get(
        "/projects/{project_id}/management/issues/{issue_id}/occurrences",
        response_model=Page[ManagementInsightView],
        tags=["management-intelligence"],
    )
    def list_management_issue_occurrences(
        project_id: UUID,
        issue_id: UUID,
        session: Annotated[Session, Depends(get_session)],
    ) -> Page[ManagementInsightView]:
        items = ManagementIntelligenceService(session).list_issue_occurrences(
            project_id, issue_id
        )
        return Page(items=items, total=len(items))

    @router.get(
        "/projects/{project_id}/management/issues/{issue_id}/feedback",
        response_model=Page[ManagementIssueFeedbackView],
        tags=["management-intelligence"],
    )
    def list_management_issue_feedback(
        project_id: UUID,
        issue_id: UUID,
        session: Annotated[Session, Depends(get_session)],
    ) -> Page[ManagementIssueFeedbackView]:
        items = ManagementIntelligenceService(session).list_issue_feedback(
            project_id, issue_id
        )
        return Page(items=items, total=len(items))

    @router.post(
        "/projects/{project_id}/management/issues/{issue_id}/reopen",
        response_model=ManagementIssueView,
        tags=["management-intelligence"],
    )
    def reopen_management_issue(
        project_id: UUID,
        issue_id: UUID,
        payload: ManagementIssueReopen,
        session: Annotated[Session, Depends(get_session)],
    ) -> ManagementIssueView:
        return ManagementIntelligenceService(session).reopen_issue(
            project_id, issue_id, payload
        )

    @router.get(
        "/projects/{project_id}/evaluations/suites",
        response_model=Page[EvaluationSuiteView],
        tags=["evaluations"],
    )
    def list_evaluation_suites(
        project_id: UUID,
        session: Annotated[Session, Depends(get_session)],
    ) -> Page[EvaluationSuiteView]:
        items = EvaluationService(session).list_suites(project_id)
        return Page(items=items, total=len(items))

    @router.post(
        "/projects/{project_id}/evaluations/suites",
        response_model=EvaluationSuiteView,
        status_code=status.HTTP_201_CREATED,
        tags=["evaluations"],
    )
    def create_evaluation_suite(
        project_id: UUID,
        payload: EvaluationSuiteCreate,
        session: Annotated[Session, Depends(get_session)],
    ) -> EvaluationSuiteView:
        return EvaluationService(session).create_suite(project_id, payload)

    @router.patch(
        "/projects/{project_id}/evaluations/suites/{suite_id}",
        response_model=EvaluationSuiteView,
        tags=["evaluations"],
    )
    def update_evaluation_suite(
        project_id: UUID,
        suite_id: UUID,
        payload: EvaluationSuiteUpdate,
        session: Annotated[Session, Depends(get_session)],
    ) -> EvaluationSuiteView:
        return EvaluationService(session).update_suite(project_id, suite_id, payload)

    @router.get(
        "/projects/{project_id}/evaluations/suites/{suite_id}/cases",
        response_model=Page[EvaluationCaseView],
        tags=["evaluations"],
    )
    def list_evaluation_cases(
        project_id: UUID,
        suite_id: UUID,
        session: Annotated[Session, Depends(get_session)],
    ) -> Page[EvaluationCaseView]:
        items = EvaluationService(session).list_cases(project_id, suite_id)
        return Page(items=items, total=len(items))

    @router.post(
        "/projects/{project_id}/evaluations/suites/{suite_id}/cases",
        response_model=EvaluationCaseView,
        status_code=status.HTTP_201_CREATED,
        tags=["evaluations"],
    )
    def create_evaluation_case(
        project_id: UUID,
        suite_id: UUID,
        payload: EvaluationCaseCreate,
        session: Annotated[Session, Depends(get_session)],
    ) -> EvaluationCaseView:
        return EvaluationService(session).create_case(project_id, suite_id, payload)

    @router.post(
        "/projects/{project_id}/evaluations/suites/{suite_id}/runs",
        response_model=EvaluationRunView,
        status_code=status.HTTP_201_CREATED,
        tags=["evaluations"],
    )
    def create_evaluation_run(
        project_id: UUID,
        suite_id: UUID,
        payload: EvaluationRunCreate,
        session: Annotated[Session, Depends(get_session)],
    ) -> EvaluationRunView:
        return EvaluationService(session).create_run(project_id, suite_id, payload)

    @router.post(
        "/projects/{project_id}/evaluations/suites/{suite_id}/execute",
        response_model=EvaluationRunView,
        status_code=status.HTTP_201_CREATED,
        tags=["evaluations"],
    )
    def execute_evaluation_suite(
        project_id: UUID,
        suite_id: UUID,
        payload: EvaluationExecuteCreate,
        session: Annotated[Session, Depends(get_session)],
    ) -> EvaluationRunView:
        return EvaluationService(session).execute_suite(
            project_id, suite_id, payload, settings
        )

    @router.post(
        "/projects/{project_id}/evaluations/suites/{suite_id}/execute-async",
        response_model=EvaluationExecutionView,
        status_code=status.HTTP_202_ACCEPTED,
        tags=["evaluations"],
    )
    def schedule_evaluation_suite(
        project_id: UUID,
        suite_id: UUID,
        payload: EvaluationExecuteCreate,
        session: Annotated[Session, Depends(get_session)],
    ) -> EvaluationExecutionView:
        return EvaluationService(session).schedule_suite(
            project_id, suite_id, payload, settings
        )

    @router.get(
        "/projects/{project_id}/evaluations/executions/{execution_id}",
        response_model=EvaluationExecutionView,
        tags=["evaluations"],
    )
    def get_evaluation_execution(
        project_id: UUID,
        execution_id: UUID,
        session: Annotated[Session, Depends(get_session)],
    ) -> EvaluationExecutionView:
        return EvaluationService(session).get_execution(project_id, execution_id)

    @router.get(
        "/projects/{project_id}/evaluations/runs",
        response_model=Page[EvaluationRunView],
        tags=["evaluations"],
    )
    def list_evaluation_runs(
        project_id: UUID,
        session: Annotated[Session, Depends(get_session)],
        suite_id: Annotated[UUID | None, Query()] = None,
    ) -> Page[EvaluationRunView]:
        items = EvaluationService(session).list_runs(project_id, suite_id=suite_id)
        return Page(items=items, total=len(items))

    @router.get(
        "/projects/{project_id}/evaluations/runs/{run_id}/results",
        response_model=Page[EvaluationResultView],
        tags=["evaluations"],
    )
    def list_evaluation_results(
        project_id: UUID,
        run_id: UUID,
        session: Annotated[Session, Depends(get_session)],
    ) -> Page[EvaluationResultView]:
        items = EvaluationService(session).list_results(project_id, run_id)
        return Page(items=items, total=len(items))

    @router.get(
        "/learning-cases/catalog",
        response_model=Page[LearningCaseCatalogView],
        tags=["learning"],
    )
    def list_learning_case_catalog(
        session: Annotated[Session, Depends(get_session)],
        industry: str | None = Query(default=None),
        search: str | None = Query(default=None),
    ) -> Page[LearningCaseCatalogView]:
        items = LearningCaseService(session).list_catalog(industry=industry, search=search)
        return Page(items=items, total=len(items))

    @router.get(
        "/projects/{project_id}/learning-cases",
        response_model=Page[LearningCaseView],
        tags=["learning"],
    )
    def list_project_learning_cases(
        project_id: UUID,
        session: Annotated[Session, Depends(get_session)],
    ) -> Page[LearningCaseView]:
        items = LearningCaseService(session).list_project(project_id)
        return Page(items=items, total=len(items))

    @router.post(
        "/projects/{project_id}/learning-cases",
        response_model=LearningCaseView,
        status_code=status.HTTP_201_CREATED,
        tags=["learning"],
    )
    def create_learning_case(
        project_id: UUID,
        payload: LearningCaseCreate,
        session: Annotated[Session, Depends(get_session)],
    ) -> LearningCaseView:
        return LearningCaseService(session).create(project_id, payload)

    @router.post(
        "/projects/{project_id}/learning-cases/from-action",
        response_model=LearningCaseView,
        status_code=status.HTTP_201_CREATED,
        tags=["learning"],
    )
    def create_learning_case_from_action(
        project_id: UUID,
        payload: LearningCaseDraftFromActionCreate,
        session: Annotated[Session, Depends(get_session)],
    ) -> LearningCaseView:
        return LearningCaseService(session).create_from_action(project_id, payload)

    @router.post(
        "/projects/{project_id}/learning-cases/from-scenario",
        response_model=LearningCaseView,
        status_code=status.HTTP_201_CREATED,
        tags=["learning"],
    )
    def create_learning_case_from_scenario(
        project_id: UUID,
        payload: LearningCaseDraftFromScenarioCreate,
        session: Annotated[Session, Depends(get_session)],
    ) -> LearningCaseView:
        return LearningCaseService(session).create_from_scenario(project_id, payload)

    @router.patch(
        "/projects/{project_id}/learning-cases/{case_id}",
        response_model=LearningCaseView,
        tags=["learning"],
    )
    def update_learning_case(
        project_id: UUID,
        case_id: UUID,
        payload: LearningCaseUpdate,
        session: Annotated[Session, Depends(get_session)],
    ) -> LearningCaseView:
        return LearningCaseService(session).update(project_id, case_id, payload)

    @router.get(
        "/projects/{project_id}/publications",
        response_model=Page[PublicationView],
        tags=["publication"],
    )
    def list_publications(
        project_id: UUID, session: Annotated[Session, Depends(get_session)]
    ) -> Page[PublicationView]:
        items = PublicationService(session).list(project_id)
        return Page(items=items, total=len(items))

    @router.post(
        "/projects/{project_id}/publications",
        response_model=PublicationView,
        status_code=status.HTTP_201_CREATED,
        tags=["publication"],
    )
    def publish_project(
        project_id: UUID,
        payload: PublicationCreate,
        session: Annotated[Session, Depends(get_session)],
    ) -> PublicationView:
        return PublicationService(session).publish(project_id, payload)

    @router.get(
        "/projects/{project_id}/publications/current",
        response_model=PublicationView | None,
        tags=["publication"],
    )
    def current_publication(
        project_id: UUID, session: Annotated[Session, Depends(get_session)]
    ) -> PublicationView | None:
        return PublicationService(session).current(project_id)

    @router.get(
        "/projects/{project_id}/query-snapshots",
        response_model=Page[QuerySnapshotView],
        tags=["publication"],
    )
    def list_query_snapshots(
        project_id: UUID, session: Annotated[Session, Depends(get_session)]
    ) -> Page[QuerySnapshotView]:
        items = QuerySnapshotService(session).list(project_id)
        return Page(items=items, total=len(items))

    @router.post(
        "/projects/{project_id}/query-snapshots",
        response_model=QuerySnapshotView,
        status_code=status.HTTP_201_CREATED,
        tags=["publication"],
    )
    def create_query_snapshot(
        project_id: UUID,
        payload: QuerySnapshotCreate,
        session: Annotated[Session, Depends(get_session)],
    ) -> QuerySnapshotView:
        return QuerySnapshotService(session).create(project_id, payload)

    @router.get(
        "/projects/{project_id}/query-snapshots/{snapshot_id}",
        response_model=QuerySnapshotView,
        tags=["publication"],
    )
    def get_query_snapshot(
        project_id: UUID,
        snapshot_id: UUID,
        session: Annotated[Session, Depends(get_session)],
    ) -> QuerySnapshotView:
        return QuerySnapshotService(session).get(project_id, snapshot_id)

    @router.get(
        "/projects/{project_id}/executive/context",
        response_model=ExecutiveContextView,
        tags=["publication"],
    )
    def executive_context(
        project_id: UUID,
        session: Annotated[Session, Depends(get_session)],
        preview: bool = Query(default=False, description="开发者预览当前草稿"),
    ) -> ExecutiveContextView:
        service = PublicationService(session)
        context = service.executive_context(project_id)
        if not preview:
            return context
        draft_graph = ProjectionService(session).graph(project_id, GraphQuery())
        return context.model_copy(update={"graph": draft_graph})

    @router.get(
        "/projects/{project_id}/source-systems",
        response_model=Page[SourceSystemView],
        tags=["integration"],
    )
    def list_source_systems(
        project_id: UUID, session: Annotated[Session, Depends(get_session)]
    ) -> Page[SourceSystemView]:
        items = IntegrationService(session, settings).list_sources(project_id)
        return Page(items=items, total=len(items))

    @router.post(
        "/projects/{project_id}/source-systems",
        response_model=SourceSystemView,
        status_code=status.HTTP_201_CREATED,
        tags=["integration"],
    )
    def create_source_system(
        project_id: UUID,
        payload: SourceSystemCreate,
        session: Annotated[Session, Depends(get_session)],
    ) -> SourceSystemView:
        return IntegrationService(session, settings).create_source(project_id, payload)

    @router.patch(
        "/projects/{project_id}/source-systems/{source_id}",
        response_model=SourceSystemView,
        tags=["integration"],
    )
    def update_source_system(
        project_id: UUID,
        source_id: UUID,
        payload: SourceSystemUpdate,
        session: Annotated[Session, Depends(get_session)],
    ) -> SourceSystemView:
        return IntegrationService(session, settings).update_source(project_id, source_id, payload)

    @router.post(
        "/projects/{project_id}/source-systems/{source_id}/test",
        response_model=SourceSystemTestView,
        tags=["integration"],
    )
    def test_source_system(
        project_id: UUID,
        source_id: UUID,
        session: Annotated[Session, Depends(get_session)],
    ) -> SourceSystemTestView:
        return IntegrationService(session, settings).test_source(project_id, source_id)

    @router.post(
        "/projects/{project_id}/source-systems/{source_id}/extract/preview",
        response_model=SourceConnectorExtractView,
        tags=["integration"],
    )
    def preview_source_system_extract(
        project_id: UUID,
        source_id: UUID,
        payload: SourceConnectorExtractRequest,
        session: Annotated[Session, Depends(get_session)],
    ) -> SourceConnectorExtractView:
        return IntegrationService(session, settings).extract_preview(project_id, source_id, payload)

    @router.post(
        "/projects/{project_id}/source-systems/{source_id}/sync",
        response_model=SourceConnectorSyncView,
        tags=["integration"],
    )
    def sync_source_system(
        project_id: UUID,
        source_id: UUID,
        payload: SourceConnectorSyncRequest,
        session: Annotated[Session, Depends(get_session)],
    ) -> SourceConnectorSyncView:
        return IntegrationService(session, settings).sync(project_id, source_id, payload)

    @router.get(
        "/projects/{project_id}/source-systems/{source_id}/assets",
        response_model=Page[SourceAssetView],
        tags=["integration"],
    )
    def list_source_assets(
        project_id: UUID,
        source_id: UUID,
        session: Annotated[Session, Depends(get_session)],
    ) -> Page[SourceAssetView]:
        items = IntegrationService(session, settings).list_assets(project_id, source_id)
        return Page(items=items, total=len(items))

    @router.post(
        "/projects/{project_id}/source-systems/{source_id}/assets",
        response_model=SourceAssetView,
        status_code=status.HTTP_201_CREATED,
        tags=["integration"],
    )
    def create_source_asset(
        project_id: UUID,
        source_id: UUID,
        payload: SourceAssetCreate,
        session: Annotated[Session, Depends(get_session)],
    ) -> SourceAssetView:
        return IntegrationService(session, settings).create_asset(project_id, source_id, payload)

    @router.patch(
        "/projects/{project_id}/source-systems/{source_id}/assets/{asset_id}",
        response_model=SourceAssetView,
        tags=["integration"],
    )
    def update_source_asset(
        project_id: UUID,
        source_id: UUID,
        asset_id: UUID,
        payload: SourceAssetUpdate,
        session: Annotated[Session, Depends(get_session)],
    ) -> SourceAssetView:
        return IntegrationService(session, settings).update_asset(
            project_id, source_id, asset_id, payload
        )

    @router.post(
        "/projects/{project_id}/source-systems/{source_id}/imports/preview",
        response_model=ImportPreviewView,
        tags=["integration"],
    )
    async def preview_source_system_import(
        project_id: UUID,
        source_id: UUID,
        session: Annotated[Session, Depends(get_session)],
        file: Annotated[UploadFile, File(description="数据源导出的 CSV 文件")],
        kind: Annotated[ImportKind, Form()] = ImportKind.CSV,
    ) -> ImportPreviewView:
        IntegrationService(session, settings).require_file_source(project_id, source_id)
        return EvidenceService(session).preview_csv(
            project_id,
            file_name=file.filename or "source-export.csv",
            content=await _read_upload_with_limit(file),
            kind=kind,
            source_system_id=source_id,
        )

    @router.get(
        "/projects/{project_id}/raw-batches",
        response_model=Page[RawBatchView],
        tags=["integration"],
    )
    def list_raw_batches(
        project_id: UUID,
        session: Annotated[Session, Depends(get_session)],
        source_asset_id: Annotated[UUID | None, Query()] = None,
    ) -> Page[RawBatchView]:
        items = EvidenceService(session).list_raw_batches(
            project_id, source_asset_id=source_asset_id
        )
        return Page(items=items, total=len(items))

    @router.get(
        "/projects/{project_id}/raw-batches/{raw_batch_id}/records",
        response_model=Page[RawRecordView],
        tags=["integration"],
    )
    def list_raw_records(
        project_id: UUID,
        raw_batch_id: UUID,
        session: Annotated[Session, Depends(get_session)],
        offset: Annotated[int, Query(ge=0, le=10_000_000)] = 0,
        limit: Annotated[int, Query(ge=1, le=500)] = 100,
    ) -> Page[RawRecordView]:
        items, total = EvidenceService(session).list_raw_records(
            project_id, raw_batch_id, offset=offset, limit=limit
        )
        return Page(items=items, total=total)

    @router.get(
        "/projects/{project_id}/materialization-runs",
        response_model=Page[MaterializationRunView],
        tags=["integration"],
    )
    def list_materialization_runs(
        project_id: UUID,
        session: Annotated[Session, Depends(get_session)],
        raw_batch_id: Annotated[UUID | None, Query()] = None,
    ) -> Page[MaterializationRunView]:
        items = EvidenceService(session).list_materialization_runs(
            project_id, raw_batch_id=raw_batch_id
        )
        return Page(items=items, total=len(items))

    @router.post(
        "/projects/{project_id}/raw-batches/{raw_batch_id}/materialize",
        response_model=MaterializationRunView,
        tags=["integration"],
    )
    def materialize_raw_batch(
        project_id: UUID,
        raw_batch_id: UUID,
        session: Annotated[Session, Depends(get_session)],
    ) -> MaterializationRunView:
        return EvidenceService(session).materialize_raw_batch(project_id, raw_batch_id)

    @router.get(
        "/projects/{project_id}/semantic-mappings",
        response_model=Page[SemanticMappingView],
        tags=["integration"],
    )
    def list_semantic_mappings(
        project_id: UUID, session: Annotated[Session, Depends(get_session)]
    ) -> Page[SemanticMappingView]:
        items = IntegrationService(session, settings).list_mappings(project_id)
        return Page(items=items, total=len(items))

    @router.get(
        "/projects/{project_id}/semantic-mapping-suggestions",
        response_model=SemanticMappingSuggestionPage,
        tags=["integration"],
    )
    def suggest_semantic_mappings(
        project_id: UUID,
        session: Annotated[Session, Depends(get_session)],
        target_type_key: Annotated[str, Query(min_length=1, max_length=64)],
        source_system_id: Annotated[UUID | None, Query()] = None,
        source_asset: Annotated[str | None, Query(max_length=500)] = None,
        include_existing: bool = False,
    ) -> SemanticMappingSuggestionPage:
        payload = SemanticMappingSuggestionRequest(
            target_type_key=target_type_key,
            source_system_id=source_system_id,
            source_asset=source_asset,
            include_existing=include_existing,
        )
        return IntegrationService(session, settings).suggest_mappings(project_id, payload)

    @router.post(
        "/projects/{project_id}/semantic-mappings",
        response_model=SemanticMappingView,
        status_code=status.HTTP_201_CREATED,
        tags=["integration"],
    )
    def create_semantic_mapping(
        project_id: UUID,
        payload: SemanticMappingCreate,
        session: Annotated[Session, Depends(get_session)],
    ) -> SemanticMappingView:
        return IntegrationService(session, settings).create_mapping(project_id, payload)

    @router.post(
        "/projects/{project_id}/semantic-mappings/{mapping_id}/validate",
        response_model=SemanticMappingView,
        tags=["integration"],
    )
    def validate_semantic_mapping(
        project_id: UUID,
        mapping_id: UUID,
        payload: SemanticMappingCommand,
        session: Annotated[Session, Depends(get_session)],
    ) -> SemanticMappingView:
        return IntegrationService(session, settings).validate_mapping(
            project_id, mapping_id, payload
        )

    @router.post(
        "/projects/{project_id}/semantic-mappings/{mapping_id}/approve",
        response_model=SemanticMappingView,
        tags=["integration"],
    )
    def approve_semantic_mapping(
        project_id: UUID,
        mapping_id: UUID,
        payload: SemanticMappingCommand,
        session: Annotated[Session, Depends(get_session)],
    ) -> SemanticMappingView:
        return IntegrationService(session, settings).approve_mapping(
            project_id, mapping_id, payload
        )

    @router.post(
        "/projects/{project_id}/semantic-mappings/{mapping_id}/disable",
        response_model=SemanticMappingView,
        tags=["integration"],
    )
    def disable_semantic_mapping(
        project_id: UUID,
        mapping_id: UUID,
        payload: SemanticMappingCommand,
        session: Annotated[Session, Depends(get_session)],
    ) -> SemanticMappingView:
        return IntegrationService(session, settings).disable_mapping(
            project_id, mapping_id, payload
        )

    @router.get(
        "/projects/{project_id}/semantic-relation-mappings",
        response_model=Page[SemanticRelationMappingView],
        tags=["integration"],
    )
    def list_semantic_relation_mappings(
        project_id: UUID, session: Annotated[Session, Depends(get_session)]
    ) -> Page[SemanticRelationMappingView]:
        items = IntegrationService(session, settings).list_relation_mappings(project_id)
        return Page(items=items, total=len(items))

    @router.post(
        "/projects/{project_id}/semantic-relation-mappings",
        response_model=SemanticRelationMappingView,
        status_code=status.HTTP_201_CREATED,
        tags=["integration"],
    )
    def create_semantic_relation_mapping(
        project_id: UUID,
        payload: SemanticRelationMappingCreate,
        session: Annotated[Session, Depends(get_session)],
    ) -> SemanticRelationMappingView:
        return IntegrationService(session, settings).create_relation_mapping(project_id, payload)

    @router.post(
        "/projects/{project_id}/semantic-relation-mappings/{mapping_id}/validate",
        response_model=SemanticRelationMappingView,
        tags=["integration"],
    )
    def validate_semantic_relation_mapping(
        project_id: UUID,
        mapping_id: UUID,
        payload: SemanticRelationMappingCommand,
        session: Annotated[Session, Depends(get_session)],
    ) -> SemanticRelationMappingView:
        return IntegrationService(session, settings).validate_relation_mapping(
            project_id, mapping_id, payload
        )

    @router.post(
        "/projects/{project_id}/semantic-relation-mappings/{mapping_id}/approve",
        response_model=SemanticRelationMappingView,
        tags=["integration"],
    )
    def approve_semantic_relation_mapping(
        project_id: UUID,
        mapping_id: UUID,
        payload: SemanticRelationMappingCommand,
        session: Annotated[Session, Depends(get_session)],
    ) -> SemanticRelationMappingView:
        return IntegrationService(session, settings).approve_relation_mapping(
            project_id, mapping_id, payload
        )

    @router.post(
        "/projects/{project_id}/semantic-relation-mappings/{mapping_id}/disable",
        response_model=SemanticRelationMappingView,
        tags=["integration"],
    )
    def disable_semantic_relation_mapping(
        project_id: UUID,
        mapping_id: UUID,
        payload: SemanticRelationMappingCommand,
        session: Annotated[Session, Depends(get_session)],
    ) -> SemanticRelationMappingView:
        return IntegrationService(session, settings).disable_relation_mapping(
            project_id, mapping_id, payload
        )

    @router.get(
        "/projects/{project_id}/semantic-datasets",
        response_model=Page[SemanticDatasetView],
        tags=["semantic-query"],
    )
    def list_semantic_datasets(
        project_id: UUID,
        session: Annotated[Session, Depends(get_session)],
    ) -> Page[SemanticDatasetView]:
        items = SemanticDatasetService(session).list(project_id)
        return Page(items=items, total=len(items))

    @router.post(
        "/projects/{project_id}/semantic-datasets",
        response_model=SemanticDatasetView,
        status_code=status.HTTP_201_CREATED,
        tags=["semantic-query"],
    )
    def create_semantic_dataset(
        project_id: UUID,
        payload: SemanticDatasetCreate,
        session: Annotated[Session, Depends(get_session)],
    ) -> SemanticDatasetView:
        return SemanticDatasetService(session).create(project_id, payload)

    @router.patch(
        "/projects/{project_id}/semantic-datasets/{dataset_id}",
        response_model=SemanticDatasetView,
        tags=["semantic-query"],
    )
    def update_semantic_dataset(
        project_id: UUID,
        dataset_id: UUID,
        payload: SemanticDatasetUpdate,
        session: Annotated[Session, Depends(get_session)],
    ) -> SemanticDatasetView:
        return SemanticDatasetService(session).update(project_id, dataset_id, payload)

    @router.post(
        "/projects/{project_id}/semantic-datasets/{dataset_id}/query",
        response_model=SemanticDatasetResult,
        tags=["semantic-query"],
    )
    def query_semantic_dataset(
        project_id: UUID,
        dataset_id: UUID,
        payload: SemanticDatasetQuery,
        session: Annotated[Session, Depends(get_session)],
    ) -> SemanticDatasetResult:
        return SemanticDatasetService(session).query(project_id, dataset_id, payload)

    @router.post(
        "/projects/{project_id}/semantic-datasets/{dataset_id}/export",
        response_model=ExportJobView,
        status_code=status.HTTP_201_CREATED,
        tags=["semantic-query"],
    )
    def export_semantic_dataset(
        project_id: UUID,
        dataset_id: UUID,
        payload: SemanticDatasetExport,
        session: Annotated[Session, Depends(get_session)],
    ) -> ExportJobView:
        return SemanticDatasetService(session, settings).export(
            project_id, dataset_id, payload
        )

    @router.get(
        "/projects/{project_id}/source-identities",
        response_model=Page[SourceIdentityView],
        tags=["integration"],
    )
    def list_source_identities(
        project_id: UUID,
        session: Annotated[Session, Depends(get_session)],
        source_system_id: UUID | None = None,
    ) -> Page[SourceIdentityView]:
        items = IntegrationService(session, settings).list_identities(
            project_id, source_system_id=source_system_id
        )
        return Page(items=items, total=len(items))

    @router.patch(
        "/projects/{project_id}/source-identities/{identity_id}/binding",
        response_model=SourceIdentityView,
        tags=["integration"],
    )
    def bind_source_identity(
        project_id: UUID,
        identity_id: UUID,
        payload: SourceIdentityBind,
        session: Annotated[Session, Depends(get_session)],
    ) -> SourceIdentityView:
        return IntegrationService(session, settings).bind_identity(project_id, identity_id, payload)

    @router.get(
        "/projects/{project_id}/observation-assertions",
        response_model=Page[ObservationAssertionView],
        tags=["integration"],
    )
    def list_observation_assertions(
        project_id: UUID,
        session: Annotated[Session, Depends(get_session)],
        entity_id: UUID | None = None,
    ) -> Page[ObservationAssertionView]:
        items = IntegrationService(session, settings).list_observations(
            project_id, entity_id=entity_id
        )
        return Page(items=items, total=len(items))

    @router.get(
        "/projects/{project_id}/observation-conflicts",
        response_model=Page[ObservationConflictView],
        tags=["integration"],
    )
    def list_observation_conflicts(
        project_id: UUID,
        session: Annotated[Session, Depends(get_session)],
        conflict_status: Annotated[
            ObservationConflictStatus | None, Query(alias="status")
        ] = None,
    ) -> Page[ObservationConflictView]:
        items = ObservationConflictService(session).list(
            project_id, status=conflict_status
        )
        return Page(items=items, total=len(items))

    @router.post(
        "/projects/{project_id}/observation-conflicts/{conflict_id}/resolve",
        response_model=ObservationConflictView,
        tags=["integration"],
    )
    def resolve_observation_conflict(
        project_id: UUID,
        conflict_id: UUID,
        payload: ObservationConflictResolve,
        session: Annotated[Session, Depends(get_session)],
    ) -> ObservationConflictView:
        return ObservationConflictService(session).resolve(project_id, conflict_id, payload)

    @router.post(
        "/restores/preview",
        response_model=RestorePreviewView,
        tags=["integration"],
    )
    async def preview_project_restore(
        session: Annotated[Session, Depends(get_session)],
        file: Annotated[UploadFile, File(description="企业解读台项目恢复 ZIP")],
    ) -> RestorePreviewView:
        return RestoreService(session, settings).preview(
            file.filename or "project-archive.zip",
            await _read_upload_with_limit(
                file,
                max_bytes=MAX_RESTORE_PACKAGE_BYTES,
                error_code="RESTORE_PACKAGE_TOO_LARGE",
                error_message="恢复包不能超过 100MB。",
            ),
        )

    @router.post(
        "/restores/confirm",
        response_model=RestoreResultView,
        tags=["integration"],
    )
    def confirm_project_restore(
        payload: RestoreConfirmRequest,
        session: Annotated[Session, Depends(get_session)],
    ) -> RestoreResultView:
        return RestoreService(session, settings).confirm(payload.preview_id)

    @router.get(
        "/projects/{project_id}/exports",
        response_model=Page[ExportJobView],
        tags=["integration"],
    )
    def list_exports(
        project_id: UUID,
        session: Annotated[Session, Depends(get_session)],
    ) -> Page[ExportJobView]:
        items = ExportService(session, settings).list(project_id)
        return Page(items=items, total=len(items))

    @router.post(
        "/projects/{project_id}/exports",
        response_model=ExportJobView,
        status_code=status.HTTP_201_CREATED,
        tags=["integration"],
    )
    def create_export(
        project_id: UUID,
        payload: ExportRequest,
        session: Annotated[Session, Depends(get_session)],
    ) -> ExportJobView:
        return ExportService(session, settings).create(project_id, payload)

    @router.get(
        "/projects/{project_id}/exports/{export_id}",
        response_model=ExportJobView,
        tags=["integration"],
    )
    def get_export(
        project_id: UUID,
        export_id: UUID,
        session: Annotated[Session, Depends(get_session)],
    ) -> ExportJobView:
        return ExportService(session, settings).get(project_id, export_id)

    @router.get(
        "/projects/{project_id}/exports/{export_id}/download",
        response_class=FileResponse,
        tags=["integration"],
    )
    def download_export(
        project_id: UUID,
        export_id: UUID,
        session: Annotated[Session, Depends(get_session)],
    ) -> FileResponse:
        service = ExportService(session, settings)
        job = service.require(project_id, export_id)
        path = service.file(project_id, export_id)
        return FileResponse(path, filename=path.name, media_type=_export_media_type(job.format))

    @router.get("/model-profiles", response_model=Page[ModelProfileView], tags=["model-profiles"])
    def list_model_profiles(
        session: Annotated[Session, Depends(get_session)],
    ) -> Page[ModelProfileView]:
        items = ModelProfileService(session, settings).list()
        return Page(items=items, total=len(items))

    @router.post(
        "/model-profiles",
        response_model=ModelProfileView,
        status_code=status.HTTP_201_CREATED,
        tags=["model-profiles"],
    )
    def create_model_profile(
        payload: ModelProfileCreate,
        session: Annotated[Session, Depends(get_session)],
    ) -> ModelProfileView:
        return ModelProfileService(session, settings).create(payload)

    @router.patch(
        "/model-profiles/{profile_id}",
        response_model=ModelProfileView,
        tags=["model-profiles"],
    )
    def update_model_profile(
        profile_id: UUID,
        payload: ModelProfileUpdate,
        session: Annotated[Session, Depends(get_session)],
    ) -> ModelProfileView:
        return ModelProfileService(session, settings).update(profile_id, payload)

    @router.post(
        "/model-profiles/{profile_id}/test",
        response_model=ModelProfileTestView,
        tags=["model-profiles"],
    )
    def test_model_profile(
        profile_id: UUID,
        session: Annotated[Session, Depends(get_session)],
    ) -> ModelProfileTestView:
        return ModelProfileService(session, settings).test(profile_id)

    @router.delete(
        "/model-profiles/{profile_id}",
        status_code=status.HTTP_204_NO_CONTENT,
        tags=["model-profiles"],
    )
    def delete_model_profile(
        profile_id: UUID,
        session: Annotated[Session, Depends(get_session)],
    ) -> None:
        ModelProfileService(session, settings).delete(profile_id)

    return router
