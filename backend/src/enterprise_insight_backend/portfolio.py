from __future__ import annotations

from uuid import UUID

from sqlalchemy import select
from sqlalchemy.exc import IntegrityError
from sqlalchemy.orm import Session

from enterprise_insight_backend.errors import DomainError
from enterprise_insight_backend.models import CompanyRow, ProjectRow
from enterprise_insight_backend.schemas import (
    CompanyCreate,
    CompanyUpdate,
    CompanyView,
    ProjectCreate,
    ProjectUpdate,
    ProjectView,
)


class PortfolioService:
    def __init__(self, session: Session) -> None:
        self.session = session

    def list_companies(self) -> list[CompanyView]:
        rows = self.session.scalars(select(CompanyRow).order_by(CompanyRow.name)).all()
        return [CompanyView.model_validate(row) for row in rows]

    def create_company(self, payload: CompanyCreate) -> CompanyView:
        row = CompanyRow(**payload.model_dump())
        self.session.add(row)
        self.session.flush()
        return CompanyView.model_validate(row)

    def company(self, company_id: UUID) -> CompanyView:
        return CompanyView.model_validate(self.require_company(company_id))

    def update_company(self, company_id: UUID, payload: CompanyUpdate) -> CompanyView:
        row = self.require_company(company_id)
        for key, value in payload.model_dump(exclude_unset=True).items():
            setattr(row, key, value)
        self.session.flush()
        return CompanyView.model_validate(row)

    def list_projects(self, company_id: UUID | None = None) -> list[ProjectView]:
        statement = select(ProjectRow)
        if company_id is not None:
            statement = statement.where(ProjectRow.company_id == str(company_id))
        rows = self.session.scalars(statement.order_by(ProjectRow.name)).all()
        return [ProjectView.model_validate(row) for row in rows]

    def create_project(self, company_id: UUID, payload: ProjectCreate) -> ProjectView:
        self.require_company(company_id)
        row = ProjectRow(company_id=str(company_id), **payload.model_dump())
        self.session.add(row)
        try:
            self.session.flush()
        except IntegrityError as exc:
            raise DomainError(
                "PROJECT_NAME_CONFLICT",
                "同一公司中已存在同名项目。",
                status_code=409,
            ) from exc
        # Every project starts with the small set of safe, built-in actions.
        # The local import keeps the portfolio and action services decoupled.
        from enterprise_insight_backend.actions import ActionService
        from enterprise_insight_backend.ontology import OntologyService

        ActionService(self.session).ensure_defaults(UUID(row.id))
        # A project created from the UI must be immediately modelable.  Keeping
        # this in the same transaction prevents the former half-initialized
        # state where Agent proposals could not pass type validation.
        OntologyService(self.session).install_default_pack(UUID(row.id))
        return ProjectView.model_validate(row)

    def project(self, project_id: UUID) -> ProjectView:
        return ProjectView.model_validate(self.require_project(project_id))

    def update_project(self, project_id: UUID, payload: ProjectUpdate) -> ProjectView:
        row = self.require_project(project_id)
        for key, value in payload.model_dump(exclude_unset=True).items():
            setattr(row, key, value.value if hasattr(value, "value") else value)
        self.session.flush()
        return ProjectView.model_validate(row)

    def bump_project_revision(self, project_id: UUID) -> int:
        row = self.require_project(project_id)
        row.revision += 1
        self.session.flush()
        return row.revision

    def require_company(self, company_id: UUID | str) -> CompanyRow:
        row = self.session.get(CompanyRow, str(company_id))
        if row is None:
            raise DomainError("COMPANY_NOT_FOUND", "公司不存在。", status_code=404)
        return row

    def require_project(self, project_id: UUID | str) -> ProjectRow:
        row = self.session.get(ProjectRow, str(project_id))
        if row is None:
            raise DomainError("PROJECT_NOT_FOUND", "项目不存在。", status_code=404)
        return row
