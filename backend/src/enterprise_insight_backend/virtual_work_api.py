from __future__ import annotations

import json
import re
from typing import Annotated
from uuid import UUID

from fastapi import APIRouter, Depends, Path, Query, Request, status
from sqlalchemy import select
from sqlalchemy.orm import Session

from enterprise_insight_backend.dependencies import get_observation_session, get_session
from enterprise_insight_backend.errors import DomainError
from enterprise_insight_backend.models import EntityRow, ProjectRow, PublicationRow
from enterprise_insight_backend.observations import (
    ManagementObservationRow,
    ObservationVersionRow,
)
from enterprise_insight_backend.portfolio import PortfolioService
from enterprise_insight_backend.schemas import GraphView, ManagementObservationKind
from enterprise_insight_backend.virtual_work import (
    EvidenceKind,
    VirtualEdgeCreate,
    VirtualEdgeUpdate,
    VirtualNodeCreate,
    VirtualNodeUpdate,
    VirtualWorkModelCreate,
    VirtualWorkModelSummary,
    VirtualWorkRetireCreate,
    VirtualWorkReviewCreate,
    VirtualWorkReviewView,
    VirtualWorkRevisionView,
    VirtualWorkService,
)
from enterprise_insight_backend.work_observation import WorkObservationAnalysisRow

_OBSERVATION_SOURCE_REF = re.compile(
    r"^observation://(?P<id>[0-9a-fA-F-]{36})/(?P<revision>[1-9][0-9]*)$"
)
_WORK_OBSERVATION_SOURCE_REF = re.compile(
    r"^work-observation://analysis/(?P<id>[0-9a-fA-F-]{36})$"
)
_EVIDENCE_OBSERVATION_KINDS: dict[EvidenceKind, set[str] | None] = {
    EvidenceKind.NORMATIVE_DOCUMENT: {ManagementObservationKind.NORMATIVE_DOCUMENT.value},
    EvidenceKind.DIRECT_OBSERVATION: {ManagementObservationKind.DIRECT_OBSERVATION.value},
    EvidenceKind.WORK_SAMPLE: {ManagementObservationKind.WORK_SAMPLE.value},
    EvidenceKind.SYSTEM_EVENT: {ManagementObservationKind.SYSTEM_EVENT.value},
    EvidenceKind.INTERVIEW: {ManagementObservationKind.INTERVIEW.value},
    EvidenceKind.SURVEY: {ManagementObservationKind.SURVEY.value},
    EvidenceKind.MEETING_NOTE: {
        ManagementObservationKind.MEETING.value,
        ManagementObservationKind.MEETING_NOTE.value,
    },
    EvidenceKind.REPORT: {
        ManagementObservationKind.WORK_REPORT.value,
        ManagementObservationKind.REPORT.value,
        ManagementObservationKind.METRIC_RESULT.value,
        ManagementObservationKind.INCIDENT.value,
    },
    # An inference basis is a traceable input to reasoning, not a claim that
    # the source itself is an inference. It may therefore cite any typed source.
    EvidenceKind.INFERENCE_BASIS: None,
}


def create_virtual_work_router() -> APIRouter:
    router = APIRouter()

    @router.post(
        "/projects/{project_id}/virtual-work/models",
        response_model=VirtualWorkRevisionView,
        status_code=status.HTTP_201_CREATED,
        tags=["virtual-work"],
    )
    def create_virtual_work_model(
        project_id: UUID,
        payload: VirtualWorkModelCreate,
        request: Request,
        session: Annotated[Session, Depends(get_session)],
        observation_session: Annotated[Session, Depends(get_observation_session)],
    ) -> VirtualWorkRevisionView:
        project = PortfolioService(session).require_project(project_id)
        if payload.project_id != project_id or payload.company_id != UUID(project.company_id):
            raise DomainError(
                "VIRTUAL_WORK_SCOPE_MISMATCH",
                "岗位虚模必须属于当前项目及其公司。",
                status_code=422,
            )
        service = _service(session, observation_session)
        return service.create_model(payload, actor_id=request.app.state.settings.local_actor_id)

    @router.get(
        "/projects/{project_id}/virtual-work/models",
        response_model=list[VirtualWorkModelSummary],
        tags=["virtual-work"],
    )
    def list_virtual_work_models(
        project_id: UUID,
        session: Annotated[Session, Depends(get_session)],
        observation_session: Annotated[Session, Depends(get_observation_session)],
    ) -> list[VirtualWorkModelSummary]:
        project = PortfolioService(session).require_project(project_id)
        return _service(session, observation_session).list_models(
            UUID(project.company_id), project_id
        )

    @router.get(
        "/projects/{project_id}/virtual-work/models/{model_id}",
        response_model=VirtualWorkRevisionView,
        tags=["virtual-work"],
    )
    def get_virtual_work_model(
        project_id: UUID,
        model_id: UUID,
        session: Annotated[Session, Depends(get_session)],
        observation_session: Annotated[Session, Depends(get_observation_session)],
        version: Annotated[int | None, Query(ge=1)] = None,
    ) -> VirtualWorkRevisionView:
        project = PortfolioService(session).require_project(project_id)
        return _service(session, observation_session).get_model(
            UUID(project.company_id), project_id, model_id, version=version
        )

    @router.get(
        "/projects/{project_id}/virtual-work/models/{model_id}/revisions/{version}/review",
        response_model=VirtualWorkRevisionView,
        tags=["virtual-work"],
    )
    def get_virtual_work_revision_for_review(
        project_id: UUID,
        model_id: UUID,
        version: Annotated[int, Path(ge=1)],
        session: Annotated[Session, Depends(get_session)],
        observation_session: Annotated[Session, Depends(get_observation_session)],
    ) -> VirtualWorkRevisionView:
        project = PortfolioService(session).require_project(project_id)
        return _service(session, observation_session).get_revision_for_review(
            UUID(project.company_id), project_id, model_id, version=version
        )

    @router.get(
        "/projects/{project_id}/virtual-work/models/{model_id}/reviews",
        response_model=list[VirtualWorkReviewView],
        tags=["virtual-work"],
    )
    def list_virtual_work_reviews(
        project_id: UUID,
        model_id: UUID,
        version: Annotated[int, Query(ge=1)],
        session: Annotated[Session, Depends(get_session)],
        observation_session: Annotated[Session, Depends(get_observation_session)],
    ) -> list[VirtualWorkReviewView]:
        project = PortfolioService(session).require_project(project_id)
        return _service(session, observation_session).list_review_records(
            UUID(project.company_id), project_id, model_id, version=version
        )

    @router.post(
        "/projects/{project_id}/virtual-work/models/{model_id}/reviews",
        response_model=VirtualWorkReviewView,
        status_code=status.HTTP_201_CREATED,
        tags=["virtual-work"],
    )
    def review_virtual_work_revision(
        project_id: UUID,
        model_id: UUID,
        payload: VirtualWorkReviewCreate,
        request: Request,
        session: Annotated[Session, Depends(get_session)],
        observation_session: Annotated[Session, Depends(get_observation_session)],
    ) -> VirtualWorkReviewView:
        project = PortfolioService(session).require_project(project_id)
        return _service(session, observation_session).review_revision(
            UUID(project.company_id),
            project_id,
            model_id,
            payload,
            actor_id=request.app.state.settings.local_actor_id,
        )

    @router.post(
        "/projects/{project_id}/virtual-work/models/{model_id}/nodes",
        response_model=VirtualWorkRevisionView,
        status_code=status.HTTP_201_CREATED,
        tags=["virtual-work"],
    )
    def add_virtual_work_node(
        project_id: UUID,
        model_id: UUID,
        payload: VirtualNodeCreate,
        request: Request,
        session: Annotated[Session, Depends(get_session)],
        observation_session: Annotated[Session, Depends(get_observation_session)],
    ) -> VirtualWorkRevisionView:
        project = PortfolioService(session).require_project(project_id)
        _canonicalize_evidence_roots(payload)
        return _service(session, observation_session).add_node(
            UUID(project.company_id),
            project_id,
            model_id,
            payload,
            actor_id=request.app.state.settings.local_actor_id,
        )

    @router.put(
        "/projects/{project_id}/virtual-work/models/{model_id}/nodes/{node_id}",
        response_model=VirtualWorkRevisionView,
        status_code=status.HTTP_201_CREATED,
        tags=["virtual-work"],
    )
    def update_virtual_work_node(
        project_id: UUID,
        model_id: UUID,
        node_id: UUID,
        payload: VirtualNodeUpdate,
        request: Request,
        session: Annotated[Session, Depends(get_session)],
        observation_session: Annotated[Session, Depends(get_observation_session)],
    ) -> VirtualWorkRevisionView:
        project = PortfolioService(session).require_project(project_id)
        _canonicalize_evidence_roots(payload)
        return _service(session, observation_session).update_node(
            UUID(project.company_id),
            project_id,
            model_id,
            node_id,
            payload,
            actor_id=request.app.state.settings.local_actor_id,
        )

    @router.post(
        "/projects/{project_id}/virtual-work/models/{model_id}/nodes/{node_id}/retire",
        response_model=VirtualWorkRevisionView,
        status_code=status.HTTP_201_CREATED,
        tags=["virtual-work"],
    )
    def retire_virtual_work_node(
        project_id: UUID,
        model_id: UUID,
        node_id: UUID,
        payload: VirtualWorkRetireCreate,
        request: Request,
        session: Annotated[Session, Depends(get_session)],
        observation_session: Annotated[Session, Depends(get_observation_session)],
    ) -> VirtualWorkRevisionView:
        project = PortfolioService(session).require_project(project_id)
        return _service(session, observation_session).retire_node(
            UUID(project.company_id),
            project_id,
            model_id,
            node_id,
            payload,
            actor_id=request.app.state.settings.local_actor_id,
        )

    @router.post(
        "/projects/{project_id}/virtual-work/models/{model_id}/edges",
        response_model=VirtualWorkRevisionView,
        status_code=status.HTTP_201_CREATED,
        tags=["virtual-work"],
    )
    def add_virtual_work_edge(
        project_id: UUID,
        model_id: UUID,
        payload: VirtualEdgeCreate,
        request: Request,
        session: Annotated[Session, Depends(get_session)],
        observation_session: Annotated[Session, Depends(get_observation_session)],
    ) -> VirtualWorkRevisionView:
        project = PortfolioService(session).require_project(project_id)
        _canonicalize_evidence_roots(payload)
        return _service(session, observation_session).add_edge(
            UUID(project.company_id),
            project_id,
            model_id,
            payload,
            actor_id=request.app.state.settings.local_actor_id,
        )

    @router.put(
        "/projects/{project_id}/virtual-work/models/{model_id}/edges/{edge_id}",
        response_model=VirtualWorkRevisionView,
        status_code=status.HTTP_201_CREATED,
        tags=["virtual-work"],
    )
    def update_virtual_work_edge(
        project_id: UUID,
        model_id: UUID,
        edge_id: UUID,
        payload: VirtualEdgeUpdate,
        request: Request,
        session: Annotated[Session, Depends(get_session)],
        observation_session: Annotated[Session, Depends(get_observation_session)],
    ) -> VirtualWorkRevisionView:
        project = PortfolioService(session).require_project(project_id)
        _canonicalize_evidence_roots(payload)
        return _service(session, observation_session).update_edge(
            UUID(project.company_id),
            project_id,
            model_id,
            edge_id,
            payload,
            actor_id=request.app.state.settings.local_actor_id,
        )

    @router.post(
        "/projects/{project_id}/virtual-work/models/{model_id}/edges/{edge_id}/retire",
        response_model=VirtualWorkRevisionView,
        status_code=status.HTTP_201_CREATED,
        tags=["virtual-work"],
    )
    def retire_virtual_work_edge(
        project_id: UUID,
        model_id: UUID,
        edge_id: UUID,
        payload: VirtualWorkRetireCreate,
        request: Request,
        session: Annotated[Session, Depends(get_session)],
        observation_session: Annotated[Session, Depends(get_observation_session)],
    ) -> VirtualWorkRevisionView:
        project = PortfolioService(session).require_project(project_id)
        return _service(session, observation_session).retire_edge(
            UUID(project.company_id),
            project_id,
            model_id,
            edge_id,
            payload,
            actor_id=request.app.state.settings.local_actor_id,
        )

    return router


def _service(formal_session: Session, observation_session: Session) -> VirtualWorkService:
    def project_scope_check(company_id: UUID, project_id: UUID) -> bool:
        project = formal_session.get(ProjectRow, str(project_id))
        return project is not None and project.company_id == str(company_id)

    def formal_anchor_scope_check(
        company_id: UUID,
        project_id: UUID,
        entity_id: UUID,
        release_id: UUID,
    ) -> bool:
        project = formal_session.get(ProjectRow, str(project_id))
        entity = formal_session.get(EntityRow, str(entity_id))
        publication = formal_session.get(PublicationRow, str(release_id))
        if (
            project is None
            or project.company_id != str(company_id)
            or entity is None
            or entity.project_id != str(project_id)
            or publication is None
            or publication.project_id != str(project_id)
        ):
            return False
        try:
            graph = GraphView.model_validate(publication.graph_snapshot)
        except ValueError:
            return False
        return any(item.id == entity_id for item in graph.entities)

    def evidence_source_check(
        company_id: UUID,
        project_id: UUID,
        evidence_kind: EvidenceKind,
        source_ref: str,
        excerpt: str,
    ) -> bool:
        match = _OBSERVATION_SOURCE_REF.fullmatch(source_ref)
        if match is None:
            work_match = _WORK_OBSERVATION_SOURCE_REF.fullmatch(source_ref)
            if work_match is None or evidence_kind is not EvidenceKind.WORK_SAMPLE:
                return False
            analysis = observation_session.get(WorkObservationAnalysisRow, work_match.group("id"))
            if (
                analysis is None
                or analysis.company_id != str(company_id)
                or analysis.project_id != str(project_id)
                or analysis.status != "COMPLETED"
                or not excerpt
            ):
                return False
            return excerpt in json.dumps(analysis.result_json or {}, ensure_ascii=False)
        try:
            observation_id = UUID(match.group("id"))
            revision = int(match.group("revision"))
        except (ValueError, OverflowError):
            return False

        observation = observation_session.get(ManagementObservationRow, str(observation_id))
        if (
            observation is None
            or observation.company_id != str(company_id)
            or observation.project_id != str(project_id)
            or observation.status != "ACTIVE"
        ):
            return False
        version = observation_session.scalar(
            select(ObservationVersionRow).where(
                ObservationVersionRow.observation_id == str(observation_id),
                ObservationVersionRow.revision == revision,
            )
        )
        if version is None or not isinstance(version.snapshot, dict):
            return False
        snapshot = version.snapshot
        if snapshot.get("status") != "ACTIVE":
            return False
        source_kind = snapshot.get("kind")
        allowed_kinds = _EVIDENCE_OBSERVATION_KINDS.get(evidence_kind)
        if allowed_kinds is None:
            # Unknown and generic OTHER records are not valid evidence types.
            if source_kind not in {item.value for item in ManagementObservationKind} - {
                ManagementObservationKind.OTHER.value
            }:
                return False
        elif source_kind not in allowed_kinds:
            return False
        content = snapshot.get("content")
        return isinstance(content, str) and bool(excerpt) and excerpt in content

    return VirtualWorkService(
        observation_session,
        formal_anchor_scope_check=formal_anchor_scope_check,
        project_scope_check=project_scope_check,
        evidence_source_check=evidence_source_check,
    )


def _canonicalize_evidence_roots(payload: VirtualNodeCreate | VirtualEdgeCreate) -> None:
    """Bind each evidence root to the immutable observation identity it cites."""
    for assertion in payload.assertions:
        for evidence in assertion.evidence:
            match = _OBSERVATION_SOURCE_REF.fullmatch(evidence.source_ref)
            if match is None:
                work_match = _WORK_OBSERVATION_SOURCE_REF.fullmatch(evidence.source_ref)
                if work_match is not None:
                    try:
                        evidence.source_root_id = UUID(work_match.group("id"))
                    except ValueError:
                        pass
                continue
            try:
                evidence.source_root_id = UUID(match.group("id"))
            except ValueError:
                # The evidence callback below rejects malformed/unresolvable refs.
                continue
