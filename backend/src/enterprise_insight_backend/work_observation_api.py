from __future__ import annotations

from datetime import UTC, datetime
from typing import Annotated, Any
from uuid import UUID

from fastapi import APIRouter, Depends, Query, Request
from sqlalchemy import select
from sqlalchemy.orm import Session

from enterprise_insight_backend.dependencies import get_observation_session, get_session
from enterprise_insight_backend.errors import DomainError
from enterprise_insight_backend.models import EntityRow, ProjectRow
from enterprise_insight_backend.schemas import Page
from enterprise_insight_backend.virtual_work import (
    AssertionKind,
    EvidenceCreate,
    EvidenceKind,
    FieldAssertionCreate,
    VirtualEdgeCreate,
    VirtualEdgeType,
    VirtualNodeCreate,
    VirtualNodeType,
)
from enterprise_insight_backend.virtual_work_api import _service as virtual_work_service
from enterprise_insight_backend.work_observation import (
    WorkObservationAnalysisCreate,
    WorkObservationAnalysisView,
    WorkObservationBatchView,
    WorkObservationComparisonCreate,
    WorkObservationComparisonView,
    WorkObservationCoverageView,
    WorkObservationIdentityBindingCreate,
    WorkObservationIdentityBindingHistoryView,
    WorkObservationIdentityBindingRetire,
    WorkObservationIdentityBindingView,
    WorkObservationImportConfirm,
    WorkObservationImportResultView,
    WorkObservationIngestionRequest,
    WorkObservationPackage,
    WorkObservationPreviewView,
    WorkObservationService,
    WorkObservationVirtualCandidateConfirm,
    WorkObservationVirtualCandidateCreate,
    WorkObservationVirtualCandidateView,
)


def _require_project(session: Session, project_id: UUID) -> ProjectRow:
    project = session.scalar(select(ProjectRow).where(ProjectRow.id == str(project_id)))
    if project is None:
        raise DomainError("PROJECT_NOT_FOUND", "项目不存在。", status_code=404)
    return project


def create_work_observation_router() -> APIRouter:
    router = APIRouter(tags=["work-observation"])

    @router.post(
        "/ingestion/work-observation/batches",
        response_model=WorkObservationImportResultView,
        status_code=201,
    )
    def ingest_batch(
        payload: WorkObservationIngestionRequest,
        session: Annotated[Session, Depends(get_session)],
        observation_session: Annotated[Session, Depends(get_observation_session)],
    ) -> WorkObservationImportResultView:
        project = _require_project(session, payload.project_id)
        return WorkObservationService(observation_session).ingest(project, payload.package)

    @router.post(
        "/projects/{project_id}/work-observation/batches",
        response_model=WorkObservationImportResultView,
        status_code=201,
    )
    def ingest_project_batch(
        project_id: UUID,
        payload: WorkObservationPackage,
        session: Annotated[Session, Depends(get_session)],
        observation_session: Annotated[Session, Depends(get_observation_session)],
    ) -> WorkObservationImportResultView:
        project = _require_project(session, project_id)
        return WorkObservationService(observation_session).ingest(project, payload)

    @router.get(
        "/projects/{project_id}/work-observation/batches",
        response_model=Page[WorkObservationBatchView],
    )
    def list_batches(
        project_id: UUID,
        session: Annotated[Session, Depends(get_session)],
        observation_session: Annotated[Session, Depends(get_observation_session)],
        offset: Annotated[int, Query(ge=0)] = 0,
        limit: Annotated[int, Query(ge=1, le=500)] = 100,
    ) -> Page[WorkObservationBatchView]:
        _require_project(session, project_id)
        items, total = WorkObservationService(observation_session).list_batches(
            project_id, offset=offset, limit=limit
        )
        return Page(items=items, total=total)

    @router.post(
        "/projects/{project_id}/work-observation/imports/preview",
        response_model=WorkObservationPreviewView,
        status_code=201,
    )
    def preview_import(
        project_id: UUID,
        payload: WorkObservationPackage,
        session: Annotated[Session, Depends(get_session)],
        observation_session: Annotated[Session, Depends(get_observation_session)],
    ) -> WorkObservationPreviewView:
        project = _require_project(session, project_id)
        return WorkObservationService(observation_session).preview(project, payload)

    @router.post(
        "/projects/{project_id}/work-observation/imports/confirm",
        response_model=WorkObservationImportResultView,
    )
    def confirm_import(
        project_id: UUID,
        payload: WorkObservationImportConfirm,
        session: Annotated[Session, Depends(get_session)],
        observation_session: Annotated[Session, Depends(get_observation_session)],
    ) -> WorkObservationImportResultView:
        project = _require_project(session, project_id)
        return WorkObservationService(observation_session).confirm(project, payload)

    @router.post(
        "/projects/{project_id}/work-observation/identity-bindings",
        response_model=WorkObservationIdentityBindingView,
        status_code=201,
    )
    def bind_identity(
        project_id: UUID,
        payload: WorkObservationIdentityBindingCreate,
        request: Request,
        session: Annotated[Session, Depends(get_session)],
        observation_session: Annotated[Session, Depends(get_observation_session)],
    ) -> WorkObservationIdentityBindingView:
        project = _require_project(session, project_id)
        entity = session.scalar(
            select(EntityRow).where(
                EntityRow.id == str(payload.formal_entity_id),
                EntityRow.project_id == str(project_id),
                EntityRow.status != "RETIRED",
            )
        )
        if entity is None:
            raise DomainError(
                "WORK_OBSERVATION_FORMAL_ENTITY_NOT_FOUND",
                "要绑定的正式企业对象不存在或不属于当前项目。",
                status_code=404,
            )
        return WorkObservationService(observation_session).bind_identity(
            project,
            payload,
            actor_id=request.app.state.settings.local_actor_id,
        )

    @router.post(
        "/projects/{project_id}/work-observation/identity-bindings/retire",
        response_model=WorkObservationIdentityBindingView,
    )
    def retire_identity(
        project_id: UUID,
        payload: WorkObservationIdentityBindingRetire,
        request: Request,
        session: Annotated[Session, Depends(get_session)],
        observation_session: Annotated[Session, Depends(get_observation_session)],
    ) -> WorkObservationIdentityBindingView:
        project = _require_project(session, project_id)
        return WorkObservationService(observation_session).retire_identity(
            project,
            payload,
            actor_id=request.app.state.settings.local_actor_id,
        )

    @router.get(
        "/projects/{project_id}/work-observation/identity-bindings",
        response_model=Page[WorkObservationIdentityBindingView],
    )
    def list_identity_bindings(
        project_id: UUID,
        session: Annotated[Session, Depends(get_session)],
        observation_session: Annotated[Session, Depends(get_observation_session)],
        offset: Annotated[int, Query(ge=0)] = 0,
        limit: Annotated[int, Query(ge=1, le=500)] = 100,
    ) -> Page[WorkObservationIdentityBindingView]:
        _require_project(session, project_id)
        items, total = WorkObservationService(observation_session).list_identity_bindings(
            project_id, offset=offset, limit=limit
        )
        return Page(items=items, total=total)

    @router.get(
        "/projects/{project_id}/work-observation/identity-bindings/history",
        response_model=Page[WorkObservationIdentityBindingHistoryView],
    )
    def list_identity_history(
        project_id: UUID,
        session: Annotated[Session, Depends(get_session)],
        observation_session: Annotated[Session, Depends(get_observation_session)],
        source_id: str | None = None,
        source_employee_key: str | None = None,
        offset: Annotated[int, Query(ge=0)] = 0,
        limit: Annotated[int, Query(ge=1, le=500)] = 100,
    ) -> Page[WorkObservationIdentityBindingHistoryView]:
        _require_project(session, project_id)
        items, total = WorkObservationService(observation_session).list_identity_history(
            project_id,
            source_id=source_id,
            source_employee_key=source_employee_key,
            offset=offset,
            limit=limit,
        )
        return Page(items=items, total=total)

    @router.get(
        "/projects/{project_id}/work-observation/coverage",
        response_model=WorkObservationCoverageView,
    )
    def coverage(
        project_id: UUID,
        session: Annotated[Session, Depends(get_session)],
        observation_session: Annotated[Session, Depends(get_observation_session)],
    ) -> WorkObservationCoverageView:
        _require_project(session, project_id)
        return WorkObservationService(observation_session).coverage(project_id)

    @router.post(
        "/projects/{project_id}/work-observation/analyses",
        response_model=WorkObservationAnalysisView,
        status_code=201,
    )
    def create_analysis(
        project_id: UUID,
        payload: WorkObservationAnalysisCreate,
        session: Annotated[Session, Depends(get_session)],
        observation_session: Annotated[Session, Depends(get_observation_session)],
    ) -> WorkObservationAnalysisView:
        project = _require_project(session, project_id)
        return WorkObservationService(observation_session).analyze(project, payload)

    @router.get(
        "/projects/{project_id}/work-observation/analyses",
        response_model=Page[WorkObservationAnalysisView],
    )
    def list_analyses(
        project_id: UUID,
        session: Annotated[Session, Depends(get_session)],
        observation_session: Annotated[Session, Depends(get_observation_session)],
        offset: Annotated[int, Query(ge=0)] = 0,
        limit: Annotated[int, Query(ge=1, le=100)] = 50,
    ) -> Page[WorkObservationAnalysisView]:
        _require_project(session, project_id)
        items, total = WorkObservationService(observation_session).list_analyses(
            project_id, offset=offset, limit=limit
        )
        return Page(items=items, total=total)

    @router.get(
        "/projects/{project_id}/work-observation/analyses/{analysis_id}",
        response_model=WorkObservationAnalysisView,
    )
    def get_analysis(
        project_id: UUID,
        analysis_id: UUID,
        session: Annotated[Session, Depends(get_session)],
        observation_session: Annotated[Session, Depends(get_observation_session)],
    ) -> WorkObservationAnalysisView:
        _require_project(session, project_id)
        return WorkObservationService(observation_session).get_analysis(project_id, analysis_id)

    @router.post(
        "/projects/{project_id}/work-observation/analyses/{analysis_id}/virtual-candidates",
        response_model=list[WorkObservationVirtualCandidateView],
        status_code=201,
    )
    def propose_virtual_candidates(
        project_id: UUID,
        analysis_id: UUID,
        payload: WorkObservationVirtualCandidateCreate,
        session: Annotated[Session, Depends(get_session)],
        observation_session: Annotated[Session, Depends(get_observation_session)],
    ) -> list[WorkObservationVirtualCandidateView]:
        project = _require_project(session, project_id)
        return WorkObservationService(observation_session).propose_virtual_candidates(
            project, analysis_id, payload
        )

    @router.get(
        "/projects/{project_id}/work-observation/virtual-candidates",
        response_model=Page[WorkObservationVirtualCandidateView],
    )
    def list_virtual_candidates(
        project_id: UUID,
        session: Annotated[Session, Depends(get_session)],
        observation_session: Annotated[Session, Depends(get_observation_session)],
        status: str | None = None,
        offset: Annotated[int, Query(ge=0)] = 0,
        limit: Annotated[int, Query(ge=1, le=500)] = 100,
    ) -> Page[WorkObservationVirtualCandidateView]:
        _require_project(session, project_id)
        items, total = WorkObservationService(observation_session).list_virtual_candidates(
            project_id, status=status, offset=offset, limit=limit
        )
        return Page(items=items, total=total)

    @router.post(
        "/projects/{project_id}/work-observation/virtual-candidates/{candidate_id}/decision",
        response_model=WorkObservationVirtualCandidateView,
    )
    def decide_virtual_candidate(
        project_id: UUID,
        candidate_id: UUID,
        payload: WorkObservationVirtualCandidateConfirm,
        request: Request,
        session: Annotated[Session, Depends(get_session)],
        observation_session: Annotated[Session, Depends(get_observation_session)],
    ) -> WorkObservationVirtualCandidateView:
        project = _require_project(session, project_id)
        observation_service = WorkObservationService(observation_session)
        candidate = observation_service.get_virtual_candidate(project_id, candidate_id)
        if candidate.status != "PROPOSED":
            raise DomainError(
                "WORK_OBSERVATION_VIRTUAL_CANDIDATE_ALREADY_DECIDED",
                "该虚模候选已经完成确认，不能重复处理。",
                status_code=409,
            )
        now = datetime.now(UTC)
        if payload.decision == "REJECT":
            candidate.status = "REJECTED"
            candidate.decision_reason = payload.reason
            candidate.decided_at = now
            observation_session.flush()
            from enterprise_insight_backend.work_observation import _virtual_candidate_view

            return _virtual_candidate_view(candidate)

        if payload.virtual_work_model_id is None:
            raise DomainError(
                "WORK_OBSERVATION_VIRTUAL_MODEL_REQUIRED",
                "确认工作观察候选时必须选择岗位虚模。",
                status_code=422,
            )
        analysis = observation_service.get_analysis(project_id, UUID(candidate.analysis_id))
        excerpt = candidate.label
        assertion = FieldAssertionCreate(
            field_name="observed_activity",
            value=candidate.properties,
            assertion_kind=AssertionKind.OBSERVED_PRACTICE,
            scope={
                "analysis_id": str(analysis.id),
                "role_key": candidate.role_key,
                "candidate_id": str(candidate.id),
            },
            evidence=[
                EvidenceCreate(
                    evidence_kind=EvidenceKind.WORK_SAMPLE,
                    source_ref=f"work-observation://analysis/{analysis.id}",
                    source_root_id=analysis.id,
                    excerpt=excerpt,
                    captured_at=analysis.created_at,
                )
            ],
            method="工作观察分析中的重复活动节点，经管理者确认后写入岗位工作虚模草稿。",
        )
        service = virtual_work_service(session, observation_session)
        revision = service.add_node(
            UUID(project.company_id),
            project_id,
            payload.virtual_work_model_id,
            VirtualNodeCreate(
                node_type=VirtualNodeType.ACTIVITY,
                label=excerpt,
                properties={
                    **candidate.properties,
                    "role_key": candidate.role_key,
                    "work_observation_candidate_id": str(candidate.id),
                },
                assertions=[assertion],
            ),
            actor_id=request.app.state.settings.local_actor_id,
        )
        node_id = UUID(str(revision.change_payload["node_id"]))
        edge_id: UUID | None = None
        if payload.position_node_id is not None:
            edge_revision = service.add_edge(
                UUID(project.company_id),
                project_id,
                payload.virtual_work_model_id,
                VirtualEdgeCreate(
                    source_node_id=payload.position_node_id,
                    target_node_id=node_id,
                    edge_type=VirtualEdgeType.POSITION_PERFORMS_ACTIVITY,
                    label="观察到岗位执行活动",
                    properties={"work_observation_candidate_id": str(candidate.id)},
                    assertions=[assertion],
                ),
                actor_id=request.app.state.settings.local_actor_id,
            )
            edge_id = UUID(str(edge_revision.change_payload["edge_id"]))
        candidate.status = "CONFIRMED"
        candidate.virtual_work_model_id = str(payload.virtual_work_model_id)
        candidate.virtual_node_id = str(node_id)
        candidate.virtual_edge_id = str(edge_id) if edge_id else None
        candidate.decision_reason = payload.reason
        candidate.decided_at = now
        observation_session.flush()
        from enterprise_insight_backend.work_observation import _virtual_candidate_view

        return _virtual_candidate_view(candidate)

    @router.get(
        "/projects/{project_id}/work-observation/graphs",
        response_model=dict[str, Any],
    )
    def get_graphs(
        project_id: UUID,
        session: Annotated[Session, Depends(get_session)],
        observation_session: Annotated[Session, Depends(get_observation_session)],
        analysis_id: UUID | None = None,
    ) -> dict[str, Any]:
        _require_project(session, project_id)
        service = WorkObservationService(observation_session)
        analysis = (
            service.get_analysis(project_id, analysis_id)
            if analysis_id is not None
            else service.latest_analysis(project_id)
        )
        return {
            "analysis_id": str(analysis.id) if analysis else None,
            "graph": analysis.result if analysis else None,
        }

    @router.get(
        "/projects/{project_id}/work-observation/segments/{segment_id}",
        response_model=dict[str, Any],
    )
    def get_segment(
        project_id: UUID,
        segment_id: str,
        analysis_id: UUID,
        session: Annotated[Session, Depends(get_session)],
        observation_session: Annotated[Session, Depends(get_observation_session)],
    ) -> dict[str, Any]:
        _require_project(session, project_id)
        return WorkObservationService(observation_session).get_segment(
            project_id, analysis_id, segment_id
        )

    @router.post(
        "/projects/{project_id}/work-observation/comparisons",
        response_model=WorkObservationComparisonView,
        status_code=201,
    )
    def compare(
        project_id: UUID,
        payload: WorkObservationComparisonCreate,
        session: Annotated[Session, Depends(get_session)],
        observation_session: Annotated[Session, Depends(get_observation_session)],
    ) -> WorkObservationComparisonView:
        project = _require_project(session, project_id)
        return WorkObservationService(observation_session).compare(project, payload)

    return router
