from __future__ import annotations

from typing import Annotated, Any, cast
from uuid import UUID

from fastapi import APIRouter, Depends, File, Form, Query, UploadFile, status
from fastapi.responses import FileResponse
from sqlalchemy.orm import Session

from enterprise_insight_backend.actions import ActionService
from enterprise_insight_backend.agent_runtime import AgentRuntimeService
from enterprise_insight_backend.changes import ChangeSetService
from enterprise_insight_backend.collaboration import CollaborationService, ExplorationService
from enterprise_insight_backend.config import Settings
from enterprise_insight_backend.dependencies import get_session
from enterprise_insight_backend.errors import DomainError, ErrorResponse
from enterprise_insight_backend.evaluation import EvaluationService
from enterprise_insight_backend.evidence import EvidenceService
from enterprise_insight_backend.exporting import ExportService
from enterprise_insight_backend.integration import IntegrationService
from enterprise_insight_backend.learning import LearningCaseService
from enterprise_insight_backend.management_intelligence import ManagementIntelligenceService
from enterprise_insight_backend.migration import DATABASE_SCHEMA_REVISION
from enterprise_insight_backend.model_profiles import ModelProfileService
from enterprise_insight_backend.observation_conflicts import ObservationConflictService
from enterprise_insight_backend.ontology import OntologyService
from enterprise_insight_backend.parsers import MAX_IMPORT_BYTES
from enterprise_insight_backend.portfolio import PortfolioService
from enterprise_insight_backend.projection import ProjectionService
from enterprise_insight_backend.publication import PublicationService
from enterprise_insight_backend.query_snapshots import QuerySnapshotService
from enterprise_insight_backend.restoring import MAX_RESTORE_PACKAGE_BYTES, RestoreService
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
    AgentMessageAccepted,
    AgentMessageCreate,
    AgentMessageView,
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
    ObservationConflictResolve,
    ObservationConflictStatus,
    ObservationConflictView,
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


def _export_media_type(export_format: str) -> str:
    return {
        "json": "application/json",
        "csv": "text/csv; charset=utf-8",
        "xlsx": "application/vnd.openxmlformats-officedocument.spreadsheetml.sheet",
        "bundle": "application/zip",
    }.get(export_format, "application/octet-stream")


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

    @router.get("/meta/capabilities", response_model=CapabilityManifest, tags=["system"])
    def capabilities() -> CapabilityManifest:
        return CapabilityManifest(
            api_version="v3",
            capabilities=[
                CapabilityView(key="portfolio", status="WORKING", description="公司与项目"),
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
                    status="WORKING",
                    description="Agent动作预演、审批、执行、回滚与观察",
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
                    description="尚未针对具体ERP/MES/CRM厂商完成适配与现场验收",
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
    ) -> Page[ProjectView]:
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
    ) -> Page[AgentMessageView]:
        items = CollaborationService(session).list_messages(project_id, thread_id)
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
    ) -> AgentMessageAccepted:
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
        session: Annotated[Session, Depends(get_session)],
    ) -> AgentRunView:
        if settings.agent_worker_enabled:
            raise DomainError(
                "AGENT_RUN_MANAGED_BY_WORKER",
                "Agent 任务已由后台队列执行，请轮询运行状态。",
                status_code=409,
            )
        return AgentRuntimeService(session, settings).process_run(project_id, run_id)

    @router.post(
        "/projects/{project_id}/agent-runs/{run_id}/continue",
        response_model=AgentRunView,
        status_code=status.HTTP_202_ACCEPTED,
        tags=["agents"],
    )
    def continue_agent_run(
        project_id: UUID,
        run_id: UUID,
        session: Annotated[Session, Depends(get_session)],
    ) -> AgentRunView:
        service = AgentRuntimeService(session, settings)
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
        items = ActionService(session).list_definitions(project_id)
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
        return ActionService(session).create_definition(project_id, payload)

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
        return ActionService(session).get_definition(project_id, action_id)

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
        return ActionService(session).update_definition(project_id, action_id, payload)

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
        return ActionService(session).validate_definition(project_id, action_id)

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
        items = ActionService(session).list_invocations(project_id, status=action_status)
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
        return ActionService(session).create_invocation(project_id, payload)

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
        return ActionService(session).get_invocation(project_id, invocation_id)

    @router.post(
        "/projects/{project_id}/action-invocations/{invocation_id}/dry-run",
        response_model=ActionInvocationView,
        tags=["actions"],
    )
    def dry_run_action(
        project_id: UUID,
        invocation_id: UUID,
        session: Annotated[Session, Depends(get_session)],
    ) -> ActionInvocationView:
        return ActionService(session).dry_run(project_id, invocation_id)

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
        return ActionService(session).approve(project_id, invocation_id, payload)

    @router.post(
        "/projects/{project_id}/action-invocations/{invocation_id}/execute",
        response_model=ActionInvocationView,
        tags=["actions"],
    )
    def execute_action(
        project_id: UUID,
        invocation_id: UUID,
        session: Annotated[Session, Depends(get_session)],
    ) -> ActionInvocationView:
        return ActionService(session).execute(project_id, invocation_id)

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
        return ActionService(session).cancel(project_id, invocation_id)

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
        return ActionService(session).retry(project_id, invocation_id, payload)

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
        return ActionService(session).rollback(project_id, invocation_id)

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
        items = ActionService(session).list_logs(project_id, invocation_id)
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
        items = ActionService(session).list_observations(project_id, invocation_id)
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
        return ActionService(session).add_observation(project_id, invocation_id, payload)

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
        items = IntegrationService(session).list_sources(project_id)
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
        return IntegrationService(session).create_source(project_id, payload)

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
        return IntegrationService(session).update_source(project_id, source_id, payload)

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
        return IntegrationService(session).test_source(project_id, source_id)

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
        return IntegrationService(session).extract_preview(project_id, source_id, payload)

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
        return IntegrationService(session).sync(project_id, source_id, payload)

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
        items = IntegrationService(session).list_assets(project_id, source_id)
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
        return IntegrationService(session).create_asset(project_id, source_id, payload)

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
        return IntegrationService(session).update_asset(
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
        IntegrationService(session).require_file_source(project_id, source_id)
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
        items = IntegrationService(session).list_mappings(project_id)
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
        return IntegrationService(session).suggest_mappings(project_id, payload)

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
        return IntegrationService(session).create_mapping(project_id, payload)

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
        return IntegrationService(session).validate_mapping(project_id, mapping_id, payload)

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
        return IntegrationService(session).approve_mapping(project_id, mapping_id, payload)

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
        return IntegrationService(session).disable_mapping(project_id, mapping_id, payload)

    @router.get(
        "/projects/{project_id}/semantic-relation-mappings",
        response_model=Page[SemanticRelationMappingView],
        tags=["integration"],
    )
    def list_semantic_relation_mappings(
        project_id: UUID, session: Annotated[Session, Depends(get_session)]
    ) -> Page[SemanticRelationMappingView]:
        items = IntegrationService(session).list_relation_mappings(project_id)
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
        return IntegrationService(session).create_relation_mapping(project_id, payload)

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
        return IntegrationService(session).validate_relation_mapping(
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
        return IntegrationService(session).approve_relation_mapping(
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
        return IntegrationService(session).disable_relation_mapping(
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
        items = IntegrationService(session).list_identities(
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
        return IntegrationService(session).bind_identity(project_id, identity_id, payload)

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
        items = IntegrationService(session).list_observations(
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
