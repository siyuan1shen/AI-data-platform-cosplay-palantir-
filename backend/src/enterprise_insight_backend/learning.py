from __future__ import annotations

import json
from uuid import UUID

from sqlalchemy import select
from sqlalchemy.orm import Session

from enterprise_insight_backend.errors import DomainError
from enterprise_insight_backend.models import (
    ActionDefinitionRow,
    ActionInvocationRow,
    ActionObservationRow,
    LearningCaseRow,
    ScenarioRow,
)
from enterprise_insight_backend.portfolio import PortfolioService
from enterprise_insight_backend.schemas import (
    LearningCaseCatalogView,
    LearningCaseCreate,
    LearningCaseDraftFromActionCreate,
    LearningCaseDraftFromScenarioCreate,
    LearningCaseStatus,
    LearningCaseUpdate,
    LearningCaseView,
)
from enterprise_insight_backend.service_utils import json_ready, require_revision


class LearningCaseService:
    def __init__(self, session: Session) -> None:
        self.session = session
        self.portfolio = PortfolioService(session)

    def list_catalog(
        self, *, industry: str | None = None, search: str | None = None
    ) -> list[LearningCaseCatalogView]:
        statement = select(LearningCaseRow).where(
            LearningCaseRow.status == LearningCaseStatus.CONFIRMED.value,
            LearningCaseRow.reusable.is_(True),
            LearningCaseRow.reusable_summary.is_not(None),
        )
        if industry:
            statement = statement.where(LearningCaseRow.industry == industry)
        if search:
            term = f"%{search.strip()}%"
            statement = statement.where(LearningCaseRow.reusable_summary.ilike(term))
        rows = self.session.scalars(statement.order_by(LearningCaseRow.updated_at.desc())).all()
        return [
            LearningCaseCatalogView(
                id=UUID(row.id),
                industry=row.industry,
                organization_scale=row.organization_scale,
                reusable_summary=row.reusable_summary or "",
                tags=row.tags,
                updated_at=row.updated_at,
            )
            for row in rows
        ]

    def list_project(self, project_id: UUID) -> list[LearningCaseView]:
        self.portfolio.require_project(project_id)
        rows = self.session.scalars(
            select(LearningCaseRow)
            .where(LearningCaseRow.project_id == str(project_id))
            .order_by(LearningCaseRow.updated_at.desc())
        ).all()
        return [LearningCaseView.model_validate(row) for row in rows]

    def create(self, project_id: UUID, payload: LearningCaseCreate) -> LearningCaseView:
        project = self.portfolio.require_project(project_id)
        company = self.portfolio.company(UUID(project.company_id))
        values = payload.model_dump(mode="json")
        if not values.get("industry"):
            values["industry"] = company.industry
        self._validate_reuse(values.get("reusable", False), values.get("reusable_summary"))
        row = LearningCaseRow(
            project_id=str(project_id),
            origin_kind="MANUAL",
            **json_ready(values),
        )
        self.session.add(row)
        self.session.flush()
        return LearningCaseView.model_validate(row)

    def create_from_action(
        self, project_id: UUID, payload: LearningCaseDraftFromActionCreate
    ) -> LearningCaseView:
        project = self.portfolio.require_project(project_id)
        company = self.portfolio.company(UUID(project.company_id))
        invocation, observations = self.resolve_action_source(project_id, payload)
        definition = self.session.get(ActionDefinitionRow, invocation.action_definition_id)
        observation_summary = [
            {
                "kind": item.observation_kind,
                "metric_key": item.metric_key,
                "period_key": item.period_key,
                "observed_value": item.observed_value,
                "outcome": item.outcome,
                "note": item.note,
                "observed_at": item.observed_at.isoformat(),
            }
            for item in observations
        ]
        row = LearningCaseRow(
            project_id=str(project_id),
            source_action_invocation_id=invocation.id,
            source_action_observation_ids=[item.id for item in observations],
            source_scenario_id=None,
            origin_kind="ACTION_RESULT",
            title=payload.title,
            industry=company.industry,
            challenge=payload.challenge,
            context=payload.context,
            intervention=(
                f"{definition.name if definition else '已记录行动'}："
                f"{json.dumps(invocation.input, ensure_ascii=False, sort_keys=True)}"
            ),
            outcome=json.dumps(observation_summary, ensure_ascii=False, sort_keys=True),
            lessons=payload.lessons,
            tags=payload.tags,
            evidence=[],
            reusable=False,
            reusable_summary=None,
            status=LearningCaseStatus.DRAFT.value,
        )
        self.session.add(row)
        self.session.flush()
        return LearningCaseView.model_validate(row)

    def resolve_action_source(
        self, project_id: UUID, payload: LearningCaseDraftFromActionCreate
    ) -> tuple[ActionInvocationRow, list[ActionObservationRow]]:
        invocation = self.session.get(ActionInvocationRow, str(payload.action_invocation_id))
        if invocation is None or invocation.project_id != str(project_id):
            raise DomainError(
                "LEARNING_CASE_ACTION_NOT_FOUND",
                "案例引用的行动不存在。",
                status_code=404,
            )
        observation_ids = [str(item) for item in payload.action_observation_ids]
        observations = list(
            self.session.scalars(
                select(ActionObservationRow).where(
                    ActionObservationRow.id.in_(observation_ids),
                    ActionObservationRow.project_id == str(project_id),
                    ActionObservationRow.invocation_id == invocation.id,
                )
            ).all()
        )
        by_id = {item.id: item for item in observations}
        if len(by_id) != len(set(observation_ids)):
            raise DomainError(
                "LEARNING_CASE_OBSERVATION_INVALID",
                "案例只能引用该行动真实存在的结果观察。",
                status_code=422,
                details=[
                    {"missing": [item for item in observation_ids if item not in by_id]}
                ],
            )
        return invocation, [by_id[item] for item in observation_ids]

    def create_from_scenario(
        self, project_id: UUID, payload: LearningCaseDraftFromScenarioCreate
    ) -> LearningCaseView:
        project = self.portfolio.require_project(project_id)
        company = self.portfolio.company(UUID(project.company_id))
        scenario = self.resolve_scenario_source(project_id, payload.scenario_id)
        scenario_snapshot = {
            "name": scenario.name,
            "description": scenario.description,
            "goal": scenario.goal,
            "assumptions": scenario.assumptions,
            "overlay_operations": scenario.overlay_operations,
            "expected_benefits": scenario.expected_benefits,
            "risks": scenario.risks,
            "validation_metrics": scenario.validation_metrics,
            "base_revision": scenario.base_revision,
            "status": scenario.status,
            "revision": scenario.revision,
        }
        row = LearningCaseRow(
            project_id=str(project_id),
            source_action_invocation_id=None,
            source_action_observation_ids=[],
            source_scenario_id=scenario.id,
            origin_kind="SCENARIO_REFERENCE",
            title=payload.title,
            industry=company.industry,
            challenge=payload.challenge,
            context=payload.context,
            intervention=json.dumps(scenario_snapshot, ensure_ascii=False, sort_keys=True),
            outcome=None,
            lessons=payload.lessons,
            tags=payload.tags,
            evidence=[],
            reusable=False,
            reusable_summary=None,
            status=LearningCaseStatus.DRAFT.value,
        )
        self.session.add(row)
        self.session.flush()
        return LearningCaseView.model_validate(row)

    def resolve_scenario_source(self, project_id: UUID, scenario_id: UUID) -> ScenarioRow:
        scenario = self.session.get(ScenarioRow, str(scenario_id))
        if scenario is None or scenario.project_id != str(project_id):
            raise DomainError(
                "LEARNING_CASE_SCENARIO_NOT_FOUND",
                "案例引用的方案不存在。",
                status_code=404,
            )
        return scenario

    def update(
        self, project_id: UUID, case_id: UUID, payload: LearningCaseUpdate
    ) -> LearningCaseView:
        row = self.require(project_id, case_id)
        require_revision(row.revision, payload.expected_revision, resource="学习案例")
        values = payload.model_dump(exclude_unset=True, exclude={"expected_revision"}, mode="json")
        self._validate_reuse(
            values.get("reusable", row.reusable),
            values.get("reusable_summary", row.reusable_summary),
        )
        for key, value in values.items():
            setattr(row, key, json_ready(value))
        row.revision += 1
        self.session.flush()
        return LearningCaseView.model_validate(row)

    def require(self, project_id: UUID, case_id: UUID) -> LearningCaseRow:
        row = self.session.get(LearningCaseRow, str(case_id))
        if row is None or row.project_id != str(project_id):
            raise DomainError("LEARNING_CASE_NOT_FOUND", "学习案例不存在。", status_code=404)
        return row

    def require_references(self, project_id: UUID, case_ids: list[UUID]) -> list[LearningCaseRow]:
        if not case_ids:
            return []
        rows = self.session.scalars(
            select(LearningCaseRow).where(LearningCaseRow.id.in_([str(item) for item in case_ids]))
        ).all()
        by_id = {row.id: row for row in rows}
        missing = [str(item) for item in case_ids if str(item) not in by_id]
        unavailable = [
            str(item)
            for item in case_ids
            if str(item) in by_id
            and (
                by_id[str(item)].status != LearningCaseStatus.CONFIRMED.value
                or (
                    by_id[str(item)].project_id != str(project_id)
                    and not (by_id[str(item)].reusable and bool(by_id[str(item)].reusable_summary))
                )
            )
        ]
        if missing or unavailable:
            raise DomainError(
                "LEARNING_CASE_REFERENCE_INVALID",
                "引用案例不存在或尚未确认。",
                status_code=422,
                details=[{"missing": missing, "not_confirmed": unavailable}],
            )
        return [by_id[str(item)] for item in case_ids]

    @staticmethod
    def _validate_reuse(reusable: bool, reusable_summary: str | None) -> None:
        if reusable and not (reusable_summary or "").strip():
            raise DomainError(
                "LEARNING_CASE_REUSABLE_SUMMARY_REQUIRED",
                "允许跨公司复用时，必须填写不含企业敏感细节的匿名摘要。",
                status_code=422,
            )
