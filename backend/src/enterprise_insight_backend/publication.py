from __future__ import annotations

from uuid import UUID

from sqlalchemy import func, select, update
from sqlalchemy.orm import Session

from enterprise_insight_backend.errors import DomainError
from enterprise_insight_backend.models import (
    EntityRow,
    HypothesisRow,
    OntologyReleaseRow,
    OntologyTypeRow,
    PublicationRow,
    RelationRow,
    ScenarioRow,
)
from enterprise_insight_backend.ontology import OntologyService
from enterprise_insight_backend.portfolio import PortfolioService
from enterprise_insight_backend.projection import ProjectionService
from enterprise_insight_backend.schemas import (
    DesignMembership,
    ExecutiveContextView,
    GraphQuery,
    GraphView,
    HypothesisStatus,
    LifecycleStatus,
    OntologyReleaseCreate,
    PublicationCreate,
    PublicationView,
    ScenarioStatus,
)
from enterprise_insight_backend.service_utils import json_ready


class PublicationService:
    def __init__(self, session: Session) -> None:
        self.session = session
        self.portfolio = PortfolioService(session)
        self.projection = ProjectionService(session)

    def list(self, project_id: UUID) -> list[PublicationView]:
        self.portfolio.require_project(project_id)
        rows = self.session.scalars(
            select(PublicationRow)
            .where(PublicationRow.project_id == str(project_id))
            .order_by(PublicationRow.version.desc())
        ).all()
        return [self._view(row) for row in rows]

    def publish(self, project_id: UUID, payload: PublicationCreate) -> PublicationView:
        project = self.portfolio.require_project(project_id)
        if project.revision != payload.expected_project_revision:
            raise DomainError(
                "PUBLICATION_REVISION_CONFLICT",
                "发布前项目已发生变化，请重新检查。",
                status_code=409,
                details=[
                    {
                        "expected_revision": payload.expected_project_revision,
                        "actual_revision": project.revision,
                    }
                ],
            )
        graph = self.projection.graph(
            project_id,
            GraphQuery(include_retired=False, include_observations=False),
        )
        if not graph.entities:
            raise DomainError(
                "PUBLICATION_EMPTY", "企业投影没有任何实体，不能发布。", status_code=409
            )
        latest_release = self.session.scalar(
            select(OntologyReleaseRow)
            .where(OntologyReleaseRow.project_id == str(project_id))
            .order_by(OntologyReleaseRow.version.desc())
            .limit(1)
        )
        has_unreleased_types = bool(
            self.session.scalar(
                select(func.count(OntologyTypeRow.id)).where(
                    OntologyTypeRow.project_id == str(project_id),
                    OntologyTypeRow.status != LifecycleStatus.PUBLISHED.value,
                )
            )
        )
        if latest_release is None or has_unreleased_types:
            released = OntologyService(self.session).create_release(
                project_id,
                OntologyReleaseCreate(
                    label=f"{payload.label} · 本体",
                    notes="随企业投影正式发布自动冻结。",
                ),
            )
            latest_release = self.session.get(OntologyReleaseRow, str(released.id))
        if latest_release is None:  # defensive guard for custom SQLAlchemy backends
            raise DomainError("ONTOLOGY_RELEASE_FAILED", "本体版本冻结失败。", status_code=500)
        latest_version = self.session.scalar(
            select(func.max(PublicationRow.version)).where(
                PublicationRow.project_id == str(project_id)
            )
        )
        row = PublicationRow(
            project_id=str(project_id),
            version=(latest_version or 0) + 1,
            label=payload.label,
            notes=payload.notes,
            project_revision=project.revision,
            ontology_release_id=latest_release.id,
            graph_snapshot=json_ready(graph.model_dump(mode="json")),
            entity_count=len(graph.entities),
            relation_count=len(graph.relations),
        )
        self.session.add(row)
        self.session.execute(
            update(EntityRow)
            .where(
                EntityRow.project_id == str(project_id),
                EntityRow.status != LifecycleStatus.RETIRED.value,
                EntityRow.design_membership == DesignMembership.MODELED.value,
            )
            .values(status=LifecycleStatus.PUBLISHED.value)
        )
        self.session.execute(
            update(RelationRow)
            .where(
                RelationRow.project_id == str(project_id),
                RelationRow.status != LifecycleStatus.RETIRED.value,
            )
            .values(status=LifecycleStatus.PUBLISHED.value)
        )
        self.session.flush()
        return self._view(row)

    def current(self, project_id: UUID) -> PublicationView | None:
        self.portfolio.require_project(project_id)
        row = self._current_row(project_id)
        return self._view(row) if row is not None else None

    def executive_context(self, project_id: UUID) -> ExecutiveContextView:
        project_row = self.portfolio.require_project(project_id)
        company = self.portfolio.company(UUID(project_row.company_id))
        project = self.portfolio.project(project_id)
        publication_row = self._current_row(project_id)
        if publication_row is None:
            graph = GraphView(
                project_id=project_id,
                revision=project.revision,
                release_id=None,
                scenario_id=None,
                entities=[],
                relations=[],
            )
            publication = None
        else:
            graph = GraphView.model_validate(publication_row.graph_snapshot)
            graph = graph.model_copy(update={"release_id": UUID(publication_row.id)})
            publication = self._view(publication_row)
        open_hypotheses = self.session.scalar(
            select(func.count(HypothesisRow.id)).where(
                HypothesisRow.project_id == str(project_id),
                HypothesisRow.status.in_(
                    [HypothesisStatus.EXPLORING.value, HypothesisStatus.NEEDS_EVIDENCE.value]
                ),
            )
        )
        active_scenarios = self.session.scalar(
            select(func.count(ScenarioRow.id)).where(
                ScenarioRow.project_id == str(project_id),
                ScenarioRow.status.in_(
                    [
                        ScenarioStatus.DRAFT.value,
                        ScenarioStatus.UNDER_REVIEW.value,
                        ScenarioStatus.APPROVED.value,
                        ScenarioStatus.ACTIVE.value,
                    ]
                ),
            )
        )
        return ExecutiveContextView(
            company=company,
            project=project,
            publication=publication,
            graph=graph,
            open_hypotheses=open_hypotheses or 0,
            active_scenarios=active_scenarios or 0,
        )

    def _current_row(self, project_id: UUID) -> PublicationRow | None:
        return self.session.scalar(
            select(PublicationRow)
            .where(PublicationRow.project_id == str(project_id))
            .order_by(PublicationRow.version.desc())
            .limit(1)
        )

    @staticmethod
    def _view(row: PublicationRow) -> PublicationView:
        return PublicationView.model_validate(row)
