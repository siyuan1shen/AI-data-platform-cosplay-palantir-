from __future__ import annotations

import hashlib
import json
import math
import re
import statistics
from collections import defaultdict
from collections.abc import Sequence
from dataclasses import dataclass, field
from datetime import datetime
from typing import Any
from uuid import UUID, uuid4

from sqlalchemy import select
from sqlalchemy.exc import IntegrityError
from sqlalchemy.orm import Session

from enterprise_insight_backend.errors import DomainError
from enterprise_insight_backend.evidence_validation import validate_evidence_references
from enterprise_insight_backend.models import (
    DesignTradeoffRow,
    EntityRow,
    InformationRequestRow,
    ManagementAnalysisRunRow,
    ManagementInsightRow,
    ManagementIssueFeedbackRow,
    ManagementIssueRow,
    ManagementSignalRow,
    MeetingRecordRow,
    MetricDefinitionRow,
    MetricObservationRow,
    RelationRow,
)
from enterprise_insight_backend.portfolio import PortfolioService
from enterprise_insight_backend.projection import ProjectionService
from enterprise_insight_backend.schemas import (
    DesignTradeoffCreate,
    DesignTradeoffUpdate,
    DesignTradeoffView,
    EntityCreate,
    EntityUpdate,
    InformationRequestCreate,
    InformationRequestStatus,
    InformationRequestUpdate,
    InformationRequestView,
    LifecycleStatus,
    ManagementAnalysisRequest,
    ManagementAnalysisRunView,
    ManagementInsightClassification,
    ManagementInsightUpdate,
    ManagementInsightView,
    ManagementIssueFeedbackView,
    ManagementIssueReopen,
    ManagementIssueView,
    ManagementSeverity,
    ManagementSignalSide,
    ManagementSignalView,
    MeetingRecordCreate,
    MeetingRecordUpdate,
    MeetingRecordView,
    MetricDefinitionCreate,
    MetricDefinitionUpdate,
    MetricDefinitionView,
    MetricDirection,
    MetricObservationCreate,
    MetricObservationRetire,
    MetricObservationUpdate,
    MetricObservationView,
    SignalDirection,
    TradeoffStatus,
    TypeKind,
    Viewpoint,
)
from enterprise_insight_backend.service_utils import (
    json_ready,
    now_utc,
    require_revision,
)

SEVERITY_ORDER = {
    ManagementSeverity.INFO.value: 0,
    ManagementSeverity.WARNING.value: 1,
    ManagementSeverity.HIGH.value: 2,
    ManagementSeverity.CRITICAL.value: 3,
}


@dataclass(slots=True)
class SignalCandidate:
    side: str
    signal_key: str
    issue_family: str
    title: str
    summary: str
    severity: str
    confidence: float
    affected_entity_ids: list[str] = field(default_factory=list)
    evidence: list[dict[str, Any]] = field(default_factory=list)
    facts: dict[str, Any] = field(default_factory=dict)
    expected_direction: str = SignalDirection.PROBLEM.value


class ManagementIntelligenceService:
    """Persistent management diagnosis with a strict fact/hypothesis boundary."""

    def __init__(self, session: Session) -> None:
        self.session = session
        self.portfolio = PortfolioService(session)

    # Metric and outcome records -------------------------------------------------
    def list_metric_definitions(self, project_id: UUID) -> list[MetricDefinitionView]:
        self.portfolio.require_project(project_id)
        rows = self.session.scalars(
            select(MetricDefinitionRow)
            .where(MetricDefinitionRow.project_id == str(project_id))
            .order_by(MetricDefinitionRow.scope, MetricDefinitionRow.name)
        ).all()
        return [MetricDefinitionView.model_validate(row) for row in rows]

    def create_metric_definition(
        self, project_id: UUID, payload: MetricDefinitionCreate
    ) -> MetricDefinitionView:
        self.portfolio.require_project(project_id)
        self._validate_metric_entity_references(project_id, payload)
        projection = ProjectionService(self.session)
        stable_key = f"metric.{payload.key}"
        entity = self.session.scalar(
            select(EntityRow).where(
                EntityRow.project_id == str(project_id),
                EntityRow.stable_key == stable_key,
            )
        )
        entity_properties = self._metric_entity_properties(project_id, payload, active=True)
        if entity is None:
            metric_id = uuid4()
            entity_view = projection.create_entity(
                project_id,
                EntityCreate(
                    type_key="metric",
                    stable_key=stable_key,
                    name=payload.name,
                    properties=entity_properties,
                    viewpoint=Viewpoint.DESIGNED,
                ),
                entity_id=metric_id,
            )
            entity_id = entity_view.id
        else:
            self._validate_metric_entity(project_id, entity)
            if self.session.get(MetricDefinitionRow, entity.id) is not None:
                raise DomainError(
                    "METRIC_KEY_CONFLICT", "项目内指标标识不能重复。", status_code=409
                )
            metric_id = UUID(entity.id)
            entity_id = UUID(entity.id)
            projection.update_entity(
                project_id,
                entity_id,
                EntityUpdate(
                    name=payload.name,
                    properties=entity_properties,
                    expected_revision=entity.revision,
                ),
            )
        row = MetricDefinitionRow(
            id=str(metric_id),
            project_id=str(project_id),
            entity_id=str(entity_id),
            **json_ready(payload.model_dump(mode="json")),
        )
        self.session.add(row)
        try:
            self.session.flush()
        except IntegrityError as exc:
            raise DomainError(
                "METRIC_KEY_CONFLICT", "项目内指标标识不能重复。", status_code=409
            ) from exc
        return MetricDefinitionView.model_validate(row)

    def update_metric_definition(
        self, project_id: UUID, metric_id: UUID, payload: MetricDefinitionUpdate
    ) -> MetricDefinitionView:
        row = self.require_metric(project_id, metric_id)
        require_revision(row.revision, payload.expected_revision, resource="指标定义")
        values = payload.model_dump(exclude_unset=True, exclude={"expected_revision"})
        candidate = MetricDefinitionCreate(
            key=row.key,
            name=values.get("name", row.name),
            description=values.get("description", row.description),
            scope=values.get("scope", row.scope),
            direction=values.get("direction", row.direction),
            owner_entity_id=values.get("owner_entity_id", row.owner_entity_id),
            strategy_entity_id=values.get("strategy_entity_id", row.strategy_entity_id),
            outcome_entity_id=values.get("outcome_entity_id", row.outcome_entity_id),
            unit=values.get("unit", row.unit),
            target_value=values.get("target_value", row.target_value),
            properties=values.get("properties", row.properties),
        )
        self._validate_metric_entity_references(project_id, candidate)
        active = values.get("active", row.active)
        for key, value in json_ready(values).items():
            setattr(row, key, value.value if hasattr(value, "value") else value)
        entity = self.session.get(EntityRow, row.entity_id)
        if entity is None:
            raise DomainError(
                "METRIC_ENTITY_MISSING",
                "指标对应的本体实体不存在，无法安全更新。",
                status_code=409,
            )
        self._validate_metric_entity(project_id, entity)
        ProjectionService(self.session).update_entity(
            project_id,
            UUID(entity.id),
            EntityUpdate(
                name=candidate.name,
                properties=self._metric_entity_properties(
                    project_id, candidate, active=bool(active)
                ),
                expected_revision=entity.revision,
            ),
        )
        row.revision += 1
        self.session.flush()
        return MetricDefinitionView.model_validate(row)

    def add_metric_observation(
        self, project_id: UUID, metric_id: UUID, payload: MetricObservationCreate
    ) -> MetricObservationView:
        metric = self.require_metric(project_id, metric_id)
        validate_evidence_references(self.session, project_id, payload.evidence)
        dimensions = json_ready(payload.dimensions)
        dimension_key = hashlib.sha256(
            json.dumps(
                dimensions, ensure_ascii=False, sort_keys=True, separators=(",", ":")
            ).encode("utf-8")
        ).hexdigest()
        numeric_value = self._numeric(payload.value)
        observation_status = payload.status.value
        if observation_status == "UNKNOWN":
            observation_status = self._computed_metric_status(metric, numeric_value)
        previous = self.session.scalar(
            select(MetricObservationRow)
            .where(
                MetricObservationRow.metric_definition_id == metric.id,
                MetricObservationRow.period_key == payload.period_key,
                MetricObservationRow.dimension_key == dimension_key,
                MetricObservationRow.source == payload.source,
                MetricObservationRow.record_status == "ACTIVE",
            )
            .order_by(MetricObservationRow.version.desc())
        )
        version = 1
        if previous is not None:
            previous.record_status = "SUPERSEDED"
            previous.revision += 1
            version = previous.version + 1
        row = MetricObservationRow(
            project_id=str(project_id),
            metric_definition_id=metric.id,
            period_key=payload.period_key,
            period_start=payload.period_start,
            period_end=payload.period_end,
            dimensions=dimensions,
            dimension_key=dimension_key,
            observed_at=payload.observed_at or now_utc(),
            value=json_ready(payload.value),
            numeric_value=numeric_value,
            status=observation_status,
            source=payload.source,
            unit=payload.unit or metric.unit,
            definition_revision=metric.revision,
            version=version,
            supersedes_id=previous.id if previous is not None else None,
            evidence=json_ready([item.model_dump(mode="json") for item in payload.evidence]),
        )
        self.session.add(row)
        self.session.flush()
        return MetricObservationView.model_validate(row)

    def update_metric_observation(
        self,
        project_id: UUID,
        metric_id: UUID,
        observation_id: UUID,
        payload: MetricObservationUpdate,
    ) -> MetricObservationView:
        metric = self.require_metric(project_id, metric_id)
        previous = self.require_metric_observation(project_id, metric_id, observation_id)
        require_revision(previous.revision, payload.expected_revision, resource="指标观测")
        if previous.record_status != "ACTIVE":
            raise DomainError(
                "METRIC_OBSERVATION_NOT_ACTIVE",
                "只有当前有效的指标观测可以修订。",
                status_code=409,
            )
        evidence = (
            payload.evidence
            if payload.evidence is not None
            else previous.evidence
        )
        evidence_payload: Any = evidence
        if payload.evidence is not None:
            evidence_payload = [item.model_dump(mode="json") for item in payload.evidence]
        validate_evidence_references(self.session, project_id, evidence)
        numeric_value = self._numeric(payload.value)
        observation_status = payload.status.value
        if observation_status == "UNKNOWN":
            observation_status = self._computed_metric_status(metric, numeric_value)
        previous.record_status = "SUPERSEDED"
        previous.revision += 1
        row = MetricObservationRow(
            project_id=str(project_id),
            metric_definition_id=metric.id,
            period_key=previous.period_key,
            period_start=previous.period_start,
            period_end=previous.period_end,
            dimensions=previous.dimensions,
            dimension_key=previous.dimension_key,
            observed_at=payload.observed_at or now_utc(),
            value=json_ready(payload.value),
            numeric_value=numeric_value,
            status=observation_status,
            source=previous.source,
            unit=previous.unit or metric.unit,
            definition_revision=metric.revision,
            version=previous.version + 1,
            supersedes_id=previous.id,
            evidence=json_ready(evidence_payload),
        )
        self.session.add(row)
        self.session.flush()
        return MetricObservationView.model_validate(row)

    def retire_metric_observation(
        self,
        project_id: UUID,
        metric_id: UUID,
        observation_id: UUID,
        payload: MetricObservationRetire,
    ) -> MetricObservationView:
        row = self.require_metric_observation(project_id, metric_id, observation_id)
        require_revision(row.revision, payload.expected_revision, resource="指标观测")
        if row.record_status == "ACTIVE":
            row.record_status = "RETRACTED"
            row.revision += 1
            self.session.flush()
        return MetricObservationView.model_validate(row)

    def list_metric_observations(
        self, project_id: UUID, metric_id: UUID, *, include_history: bool = False
    ) -> list[MetricObservationView]:
        self.require_metric(project_id, metric_id)
        statement = select(MetricObservationRow).where(
            MetricObservationRow.metric_definition_id == str(metric_id)
        )
        if not include_history:
            statement = statement.where(MetricObservationRow.record_status == "ACTIVE")
        rows = self.session.scalars(
            statement
            .order_by(MetricObservationRow.observed_at.desc(), MetricObservationRow.id.desc())
        ).all()
        return [MetricObservationView.model_validate(row) for row in rows]

    def require_metric_observation(
        self,
        project_id: UUID | str,
        metric_id: UUID | str,
        observation_id: UUID | str,
    ) -> MetricObservationRow:
        row = self.session.get(MetricObservationRow, str(observation_id))
        if (
            row is None
            or row.project_id != str(project_id)
            or row.metric_definition_id != str(metric_id)
        ):
            raise DomainError(
                "METRIC_OBSERVATION_NOT_FOUND", "指标观测不存在。", status_code=404
            )
        return row

    def require_metric(self, project_id: UUID | str, metric_id: UUID | str) -> MetricDefinitionRow:
        row = self.session.get(MetricDefinitionRow, str(metric_id))
        if row is None or row.project_id != str(project_id):
            raise DomainError("METRIC_NOT_FOUND", "指标定义不存在。", status_code=404)
        return row

    # Meeting records -----------------------------------------------------------
    def list_meetings(self, project_id: UUID) -> list[MeetingRecordView]:
        self.portfolio.require_project(project_id)
        rows = self.session.scalars(
            select(MeetingRecordRow)
            .where(MeetingRecordRow.project_id == str(project_id))
            .order_by(MeetingRecordRow.occurred_at.desc())
        ).all()
        return [MeetingRecordView.model_validate(row) for row in rows]

    def create_meeting(
        self, project_id: UUID, payload: MeetingRecordCreate
    ) -> MeetingRecordView:
        self.validate_meeting(project_id, payload)
        values = json_ready(payload.model_dump(mode="json"))
        values["occurred_at"] = payload.occurred_at
        row = MeetingRecordRow(
            project_id=str(project_id),
            **values,
        )
        self.session.add(row)
        self.session.flush()
        return MeetingRecordView.model_validate(row)

    def validate_meeting(self, project_id: UUID | str, payload: MeetingRecordCreate) -> None:
        self.portfolio.require_project(project_id)
        self._validate_meeting_entities(UUID(str(project_id)), payload)
        validate_evidence_references(self.session, project_id, payload.evidence)

    def update_meeting(
        self, project_id: UUID, meeting_id: UUID, payload: MeetingRecordUpdate
    ) -> MeetingRecordView:
        row = self.require_meeting(project_id, meeting_id)
        require_revision(row.revision, payload.expected_revision, resource="会议记录")
        values = payload.model_dump(exclude_unset=True, exclude={"expected_revision"})
        required_nulls = [
            key for key in ("title", "occurred_at") if key in values and values[key] is None
        ]
        if required_nulls:
            raise DomainError(
                "MEETING_REQUIRED_FIELD_NULL",
                "会议标题和发生时间不能清空。",
                status_code=422,
                details=[{"fields": required_nulls}],
            )
        merged = MeetingRecordCreate(
            title=values.get("title", row.title),
            occurred_at=values.get("occurred_at", row.occurred_at),
            participant_entity_ids=values.get(
                "participant_entity_ids", row.participant_entity_ids
            ),
            related_entity_ids=values.get("related_entity_ids", row.related_entity_ids),
            topics=values.get("topics", row.topics),
            decisions=values.get("decisions", row.decisions),
            action_items=values.get("action_items", row.action_items),
            escalations=values.get("escalations", row.escalations),
            evidence=values.get("evidence", row.evidence),
        )
        self._validate_meeting_entities(project_id, merged)
        validate_evidence_references(self.session, project_id, merged.evidence)
        serialized = json_ready(values)
        if "occurred_at" in values:
            serialized["occurred_at"] = values["occurred_at"]
        for key, value in serialized.items():
            setattr(row, key, value)
        row.revision += 1
        self.session.flush()
        return MeetingRecordView.model_validate(row)

    def require_meeting(self, project_id: UUID, meeting_id: UUID) -> MeetingRecordRow:
        row = self.session.get(MeetingRecordRow, str(meeting_id))
        if row is None or row.project_id != str(project_id):
            raise DomainError("MEETING_NOT_FOUND", "会议记录不存在。", status_code=404)
        return row

    # Explicit design trade-offs ------------------------------------------------
    def list_tradeoffs(self, project_id: UUID) -> list[DesignTradeoffView]:
        self.portfolio.require_project(project_id)
        rows = self.session.scalars(
            select(DesignTradeoffRow)
            .where(DesignTradeoffRow.project_id == str(project_id))
            .order_by(DesignTradeoffRow.updated_at.desc())
        ).all()
        return [DesignTradeoffView.model_validate(row) for row in rows]

    def create_tradeoff(
        self, project_id: UUID, payload: DesignTradeoffCreate
    ) -> DesignTradeoffView:
        self.validate_tradeoff(project_id, payload)
        row = DesignTradeoffRow(
            project_id=str(project_id),
            **json_ready(payload.model_dump(mode="json")),
        )
        self.session.add(row)
        self.session.flush()
        return DesignTradeoffView.model_validate(row)

    def validate_tradeoff(self, project_id: UUID | str, payload: DesignTradeoffCreate) -> None:
        self.portfolio.require_project(project_id)
        normalized_project_id = UUID(str(project_id))
        self._validate_entity_ids(normalized_project_id, payload.affected_entity_ids)
        self._validate_metric_ids(normalized_project_id, payload.monitoring_metric_ids)

    def update_tradeoff(
        self, project_id: UUID, tradeoff_id: UUID, payload: DesignTradeoffUpdate
    ) -> DesignTradeoffView:
        row = self.require_tradeoff(project_id, tradeoff_id)
        require_revision(row.revision, payload.expected_revision, resource="设计取舍")
        values = payload.model_dump(exclude_unset=True, exclude={"expected_revision"})
        entity_ids = values.get("affected_entity_ids", row.affected_entity_ids)
        metric_ids = values.get("monitoring_metric_ids", row.monitoring_metric_ids)
        self._validate_entity_ids(project_id, entity_ids)
        self._validate_metric_ids(project_id, metric_ids)
        next_status = values.get("status", row.status)
        accepted_by = values.get("accepted_by", row.accepted_by)
        status_value = next_status.value if hasattr(next_status, "value") else next_status
        if status_value == TradeoffStatus.ACCEPTED.value and not accepted_by:
            raise DomainError(
                "TRADEOFF_ACCEPTOR_REQUIRED", "接受设计取舍时必须记录确认人。", status_code=422
            )
        for key, value in json_ready(values).items():
            setattr(row, key, value)
        row.revision += 1
        self.session.flush()
        return DesignTradeoffView.model_validate(row)

    def require_tradeoff(self, project_id: UUID, tradeoff_id: UUID) -> DesignTradeoffRow:
        row = self.session.get(DesignTradeoffRow, str(tradeoff_id))
        if row is None or row.project_id != str(project_id):
            raise DomainError("TRADEOFF_NOT_FOUND", "设计取舍不存在。", status_code=404)
        return row

    # Information requests ------------------------------------------------------
    def list_information_requests(self, project_id: UUID) -> list[InformationRequestView]:
        self.portfolio.require_project(project_id)
        rows = self.session.scalars(
            select(InformationRequestRow)
            .where(InformationRequestRow.project_id == str(project_id))
            .order_by(InformationRequestRow.updated_at.desc())
        ).all()
        return [InformationRequestView.model_validate(row) for row in rows]

    def create_information_request(
        self, project_id: UUID, payload: InformationRequestCreate
    ) -> InformationRequestView:
        self.validate_information_request(project_id, payload)
        row = InformationRequestRow(
            project_id=str(project_id),
            **json_ready(payload.model_dump(mode="json")),
        )
        self.session.add(row)
        self.session.flush()
        return InformationRequestView.model_validate(row)

    def validate_information_request(
        self, project_id: UUID | str, payload: InformationRequestCreate
    ) -> None:
        self.portfolio.require_project(project_id)
        self._validate_entity_ids(UUID(str(project_id)), payload.target_entity_ids)

    def update_information_request(
        self, project_id: UUID, request_id: UUID, payload: InformationRequestUpdate
    ) -> InformationRequestView:
        row = self.require_information_request(project_id, request_id)
        require_revision(row.revision, payload.expected_revision, resource="信息请求")
        values = payload.model_dump(exclude_unset=True, exclude={"expected_revision"})
        resulting_status = values.get("status", row.status)
        resulting_answer = values.get("answer", row.answer)
        if (
            resulting_status == InformationRequestStatus.ANSWERED.value
            and (not isinstance(resulting_answer, str) or not resulting_answer.strip())
        ):
            raise DomainError(
                "INFORMATION_REQUEST_ANSWER_REQUIRED",
                "标记为已回答的信息请求必须保留有效回答。",
                status_code=422,
            )
        for key, value in json_ready(values).items():
            setattr(row, key, value)
        row.revision += 1
        self.session.flush()
        return InformationRequestView.model_validate(row)

    def require_information_request(
        self, project_id: UUID, request_id: UUID
    ) -> InformationRequestRow:
        row = self.session.get(InformationRequestRow, str(request_id))
        if row is None or row.project_id != str(project_id):
            raise DomainError(
                "INFORMATION_REQUEST_NOT_FOUND", "信息请求不存在。", status_code=404
            )
        return row

    # Analysis ------------------------------------------------------------------
    def run_analysis(
        self, project_id: UUID, payload: ManagementAnalysisRequest
    ) -> ManagementAnalysisRunView:
        self.portfolio.require_project(project_id)
        run = ManagementAnalysisRunRow(
            project_id=str(project_id),
            requested_by=payload.requested_by,
            parameters=json_ready(payload.model_dump(mode="json")),
        )
        self.session.add(run)
        self.session.flush()
        try:
            # Keep a failed analysis run for diagnosis, but roll back any
            # partially generated signals/insights so a failed run cannot look
            # like a usable mixed result.
            with self.session.begin_nested():
                candidates: list[SignalCandidate] = []
                if payload.include_design:
                    candidates.extend(self._design_signals(project_id))
                if payload.include_outcome:
                    candidates.extend(
                        self._outcome_signals(project_id, payload.recent_observation_limit)
                    )
                signals: list[ManagementSignalRow] = []
                fingerprints: set[str] = set()
                for candidate in candidates:
                    fingerprint = self._signal_fingerprint(candidate)
                    if fingerprint in fingerprints:
                        continue
                    fingerprints.add(fingerprint)
                    signals.append(self._persist_signal(run, candidate, fingerprint))
                self.session.flush()
                insights = self._reconcile(project_id, run, signals)
                run.design_signal_count = sum(
                    item.side == ManagementSignalSide.DESIGN.value for item in signals
                )
                run.outcome_signal_count = sum(
                    item.side == ManagementSignalSide.OUTCOME.value for item in signals
                )
                run.insight_count = len(insights)
                run.status = "COMPLETED"
                run.finished_at = now_utc()
                self.session.flush()
        except Exception as exc:
            run.status = "FAILED"
            run.error = {
                "code": "MANAGEMENT_ANALYSIS_FAILED",
                "message": "管理分析未能完成。",
                "details": [{"exception": type(exc).__name__}],
            }
            run.finished_at = now_utc()
            self.session.flush()
        return ManagementAnalysisRunView.model_validate(run)

    def list_analysis_runs(self, project_id: UUID) -> list[ManagementAnalysisRunView]:
        self.portfolio.require_project(project_id)
        rows = self.session.scalars(
            select(ManagementAnalysisRunRow)
            .where(ManagementAnalysisRunRow.project_id == str(project_id))
            .order_by(ManagementAnalysisRunRow.created_at.desc())
        ).all()
        return [ManagementAnalysisRunView.model_validate(row) for row in rows]

    def list_signals(
        self,
        project_id: UUID,
        *,
        run_id: UUID | None = None,
        side: str | None = None,
    ) -> list[ManagementSignalView]:
        self.portfolio.require_project(project_id)
        statement = select(ManagementSignalRow).where(
            ManagementSignalRow.project_id == str(project_id)
        )
        if run_id is not None:
            self._require_run(project_id, run_id)
            statement = statement.where(ManagementSignalRow.run_id == str(run_id))
        if side is not None:
            statement = statement.where(ManagementSignalRow.side == side)
        rows = self.session.scalars(statement.order_by(ManagementSignalRow.created_at.desc())).all()
        return [ManagementSignalView.model_validate(row) for row in rows]

    def list_insights(
        self, project_id: UUID, *, run_id: UUID | None = None
    ) -> list[ManagementInsightView]:
        self.portfolio.require_project(project_id)
        statement = select(ManagementInsightRow).where(
            ManagementInsightRow.project_id == str(project_id)
        )
        if run_id is not None:
            self._require_run(project_id, run_id)
            statement = statement.where(ManagementInsightRow.run_id == str(run_id))
        rows = list(self.session.scalars(
            statement.order_by(ManagementInsightRow.created_at.desc())
        ).all())
        if run_id is None:
            seen: set[str] = set()
            latest_rows: list[ManagementInsightRow] = []
            for row in rows:
                key = row.issue_id or row.id
                if key in seen:
                    continue
                seen.add(key)
                latest_rows.append(row)
            rows = latest_rows
        return [ManagementInsightView.model_validate(row) for row in rows]

    def list_issues(self, project_id: UUID) -> list[ManagementIssueView]:
        self.portfolio.require_project(project_id)
        rows = self.session.scalars(
            select(ManagementIssueRow)
            .where(ManagementIssueRow.project_id == str(project_id))
            .order_by(ManagementIssueRow.last_seen_at.desc())
        ).all()
        return [ManagementIssueView.model_validate(row) for row in rows]

    def list_issue_occurrences(
        self, project_id: UUID, issue_id: UUID
    ) -> list[ManagementInsightView]:
        self._require_issue(project_id, issue_id)
        rows = self.session.scalars(
            select(ManagementInsightRow)
            .where(ManagementInsightRow.issue_id == str(issue_id))
            .order_by(ManagementInsightRow.occurrence_number.desc())
        ).all()
        return [ManagementInsightView.model_validate(row) for row in rows]

    def list_issue_feedback(
        self, project_id: UUID, issue_id: UUID
    ) -> list[ManagementIssueFeedbackView]:
        self._require_issue(project_id, issue_id)
        rows = self.session.scalars(
            select(ManagementIssueFeedbackRow)
            .where(ManagementIssueFeedbackRow.issue_id == str(issue_id))
            .order_by(ManagementIssueFeedbackRow.created_at.desc())
        ).all()
        return [ManagementIssueFeedbackView.model_validate(row) for row in rows]

    def update_insight(
        self, project_id: UUID, insight_id: UUID, payload: ManagementInsightUpdate
    ) -> ManagementInsightView:
        row = self.session.get(ManagementInsightRow, str(insight_id))
        if row is None or row.project_id != str(project_id):
            raise DomainError("MANAGEMENT_INSIGHT_NOT_FOUND", "管理判断不存在。", status_code=404)
        require_revision(row.revision, payload.expected_revision, resource="管理判断")
        old_status = row.status
        for key, value in json_ready(
            payload.model_dump(
                exclude_unset=True, exclude={"expected_revision", "provided_by"}
            )
        ).items():
            setattr(row, key, value)
        row.revision += 1
        if row.issue_id:
            issue = self._require_issue(project_id, UUID(row.issue_id))
            issue.status = row.status
            issue.management_feedback = row.management_feedback
            issue.revision += 1
            self.session.add(
                ManagementIssueFeedbackRow(
                    project_id=str(project_id),
                    issue_id=issue.id,
                    insight_id=row.id,
                    from_status=old_status,
                    to_status=row.status,
                    feedback=row.management_feedback,
                    provided_by=payload.provided_by,
                )
            )
        self.session.flush()
        return ManagementInsightView.model_validate(row)

    def reopen_issue(
        self, project_id: UUID, issue_id: UUID, payload: ManagementIssueReopen
    ) -> ManagementIssueView:
        issue = self._require_issue(project_id, issue_id)
        require_revision(issue.revision, payload.expected_revision, resource="管理问题")
        old_status = issue.status
        issue.status = "OPEN"
        issue.management_feedback = payload.reason
        issue.revision += 1
        latest = self.session.scalar(
            select(ManagementInsightRow)
            .where(ManagementInsightRow.issue_id == issue.id)
            .order_by(ManagementInsightRow.occurrence_number.desc())
            .limit(1)
        )
        if latest is not None:
            latest.status = "OPEN"
            latest.management_feedback = payload.reason
            latest.revision += 1
        self.session.add(
            ManagementIssueFeedbackRow(
                project_id=str(project_id),
                issue_id=issue.id,
                insight_id=latest.id if latest else None,
                from_status=old_status,
                to_status="OPEN",
                feedback=payload.reason,
                provided_by=payload.provided_by,
            )
        )
        self.session.flush()
        return ManagementIssueView.model_validate(issue)

    def _require_issue(self, project_id: UUID, issue_id: UUID) -> ManagementIssueRow:
        row = self.session.get(ManagementIssueRow, str(issue_id))
        if row is None or row.project_id != str(project_id):
            raise DomainError("MANAGEMENT_ISSUE_NOT_FOUND", "管理问题不存在。", status_code=404)
        return row

    def _design_signals(self, project_id: UUID) -> list[SignalCandidate]:
        entities = self.session.scalars(
            select(EntityRow).where(
                EntityRow.project_id == str(project_id),
                EntityRow.status != LifecycleStatus.RETIRED.value,
            )
        ).all()
        relations = self.session.scalars(
            select(RelationRow).where(
                RelationRow.project_id == str(project_id),
                RelationRow.status != LifecycleStatus.RETIRED.value,
            )
        ).all()
        metrics = self.session.scalars(
            select(MetricDefinitionRow).where(
                MetricDefinitionRow.project_id == str(project_id),
                MetricDefinitionRow.active.is_(True),
            )
        ).all()
        by_id = {item.id: item for item in entities}
        by_type: dict[str, list[EntityRow]] = defaultdict(list)
        for item in entities:
            by_type[item.type_key].append(item)
        rels: dict[str, list[RelationRow]] = defaultdict(list)
        for relation_row in relations:
            rels[relation_row.type_key].append(relation_row)
        candidates: list[SignalCandidate] = []

        responsibility_holders: dict[str, set[str]] = defaultdict(set)
        role_responsibilities: dict[str, set[str]] = defaultdict(set)
        for relation in rels["holds_responsibility"]:
            holders = self._role_ids(relation, "accountable")
            responsibilities = self._role_ids(relation, "responsibility")
            for responsibility_id in responsibilities:
                responsibility_holders[responsibility_id].update(holders)
            for holder_id in holders:
                role_responsibilities[holder_id].update(responsibilities)
        authority_holders: set[str] = set()
        for relation in rels["holds_authority"]:
            authority_holders.update(self._role_ids(relation, "holder"))

        for responsibility in by_type["responsibility"]:
            holders = responsibility_holders.get(responsibility.id, set())
            if not holders:
                candidates.append(
                    self._candidate(
                        "responsibility_gap",
                        "ACCOUNTABILITY",
                        f"职责“{responsibility.name}”没有明确责任主体",
                        "职责已经存在，但没有岗位或组织单元承担结果责任。",
                        "HIGH",
                        0.98,
                        [responsibility.id],
                    )
                )
            elif len(holders) > 1:
                names = [by_id[item].name for item in holders if item in by_id]
                candidates.append(
                    self._candidate(
                        "shared_accountability",
                        "ACCOUNTABILITY",
                        f"职责“{responsibility.name}”存在多头结果责任",
                        f"当前同时由 {len(holders)} 个主体承担：{'、'.join(names)}。",
                        "WARNING",
                        0.95,
                        [responsibility.id, *holders],
                        facts={"holder_count": len(holders)},
                    )
                )
        for holder_id, responsibility_ids in role_responsibilities.items():
            if holder_id not in authority_holders and holder_id in by_id:
                candidates.append(
                    self._candidate(
                        "accountability_without_authority",
                        "ACCOUNTABILITY",
                        f"“{by_id[holder_id].name}”有责但未配置决策权",
                        "该主体承担职责，但投影中没有任何明确决策权。",
                        "HIGH",
                        0.9,
                        [holder_id, *responsibility_ids],
                    )
                )
        for holder_id in authority_holders:
            if holder_id not in role_responsibilities and holder_id in by_id:
                candidates.append(
                    self._candidate(
                        "authority_without_accountability",
                        "ACCOUNTABILITY",
                        f"“{by_id[holder_id].name}”有权但未配置结果责任",
                        "该主体拥有明确决策权，但没有承担已建模职责。",
                        "WARNING",
                        0.9,
                        [holder_id],
                    )
                )

        metrics_by_strategy: dict[str, list[MetricDefinitionRow]] = defaultdict(list)
        for metric in metrics:
            if metric.strategy_entity_id:
                metrics_by_strategy[metric.strategy_entity_id].append(metric)
            if metric.owner_entity_id and metric.properties.get("controllable_by_owner") is False:
                candidates.append(
                    self._candidate(
                        "uncontrollable_kpi",
                        "KPI_PERFORMANCE",
                        f"指标“{metric.name}”超出责任人的可控范围",
                        "指标被明确标记为责任人无法充分控制，存在代理问题与错误激励风险。",
                        "HIGH",
                        0.98,
                        [
                            item
                            for item in [metric.owner_entity_id, metric.strategy_entity_id]
                            if item
                        ],
                        facts={"metric_id": metric.id},
                    )
                )
            proxy_risk = str(metric.properties.get("proxy_risk", "")).upper()
            if proxy_risk in {"HIGH", "CRITICAL"}:
                candidates.append(
                    self._candidate(
                        "proxy_metric_risk",
                        "KPI_PERFORMANCE",
                        f"指标“{metric.name}”存在替代指标博弈风险",
                        "该指标可能被优化，但并不必然改善企业最终结果。",
                        proxy_risk,
                        0.95,
                        [
                            item
                            for item in [metric.owner_entity_id, metric.strategy_entity_id]
                            if item
                        ],
                        facts={"metric_id": metric.id, "proxy_risk": proxy_risk},
                    )
                )
        for strategy in by_type["strategy_objective"]:
            if not metrics_by_strategy.get(strategy.id):
                candidates.append(
                    self._candidate(
                        "strategy_without_metric",
                        "STRATEGY_OUTCOME",
                        f"战略目标“{strategy.name}”没有结果指标",
                        "战略目标无法通过当前指标体系验证是否实现。",
                        "HIGH",
                        0.98,
                        [strategy.id],
                    )
                )

        measured_entities: set[str] = set()
        for relation in rels["measured_by"]:
            measured_entities.update(self._role_ids(relation, "subject"))
        for activity in by_type["work_activity"]:
            if activity.properties.get("critical") is True and activity.id not in measured_entities:
                candidates.append(
                    self._candidate(
                        "critical_work_unmeasured",
                        "KPI_PERFORMANCE",
                        f"关键工作“{activity.name}”没有被度量",
                        "关键活动缺少指标，管理层难以区分过程失效与结果波动。",
                        "WARNING",
                        0.97,
                        [activity.id],
                    )
                )

        for relation in rels["conflicts_with"]:
            participant_ids = [item.entity_id for item in relation.participants]
            names = [by_id[item].name for item in participant_ids if item in by_id]
            candidates.append(
                self._candidate(
                    "conflicting_metrics",
                    "KPI_PERFORMANCE",
                    "指标之间存在显式目标冲突",
                    f"冲突指标：{'、'.join(names) or relation.name or '未命名指标'}。",
                    "HIGH",
                    0.99,
                    participant_ids,
                )
            )

        knowledge_holders: dict[str, set[str]] = defaultdict(set)
        for relation in rels["holds_knowledge"]:
            holders = self._role_ids(relation, "holder")
            domains = self._role_ids(relation, "knowledge")
            for domain in domains:
                knowledge_holders[domain].update(holders)
        for knowledge in by_type["knowledge_domain"]:
            holders = knowledge_holders.get(knowledge.id, set())
            if knowledge.properties.get("critical") is True and len(holders) <= 1:
                candidates.append(
                    self._candidate(
                        "knowledge_single_point",
                        "KNOWLEDGE_DEPENDENCY",
                        f"关键知识“{knowledge.name}”存在单点依赖",
                        "关键知识只有一名持有人或尚未记录持有人。",
                        "HIGH",
                        0.96,
                        [knowledge.id, *holders],
                        facts={"holder_count": len(holders)},
                    )
                )

        step_ids_by_process: dict[str, set[str]] = defaultdict(set)
        for relation in rels["contains"]:
            containers = self._role_ids(relation, "container")
            members = self._role_ids(relation, "member")
            for container in containers:
                if by_id.get(container) and by_id[container].type_key == "process":
                    step_ids_by_process[container].update(members)
        for process_id, step_ids in step_ids_by_process.items():
            approval_steps = [
                by_id[item]
                for item in step_ids
                if item in by_id
                and by_id[item].type_key == "process_step"
                and self._is_approval_step(by_id[item])
            ]
            if len(approval_steps) >= 3:
                candidates.append(
                    self._candidate(
                        "excessive_serial_approvals",
                        "PROCESS_CONTROL",
                        f"流程“{by_id[process_id].name}”存在过多审批环节",
                        f"识别到 {len(approval_steps)} 个审批或控制步骤，"
                        "需要验证是否均创造必要控制价值。",
                        "WARNING",
                        0.9,
                        [process_id, *[item.id for item in approval_steps]],
                        facts={"approval_step_count": len(approval_steps)},
                    )
                )
        return candidates

    def _outcome_signals(self, project_id: UUID, recent_limit: int) -> list[SignalCandidate]:
        metrics = self.session.scalars(
            select(MetricDefinitionRow).where(
                MetricDefinitionRow.project_id == str(project_id),
                MetricDefinitionRow.active.is_(True),
            )
        ).all()
        observations = self.session.scalars(
            select(MetricObservationRow)
            .where(
                MetricObservationRow.project_id == str(project_id),
                MetricObservationRow.record_status == "ACTIVE",
            )
            .order_by(MetricObservationRow.observed_at.desc())
        ).all()
        observations_by_metric: dict[str, list[MetricObservationRow]] = defaultdict(list)
        observed_periods: dict[str, set[str]] = defaultdict(set)
        for item in observations:
            metric_id = item.metric_definition_id
            if item.period_key in observed_periods[metric_id]:
                continue
            if len(observations_by_metric[metric_id]) < recent_limit:
                observations_by_metric[metric_id].append(item)
                observed_periods[metric_id].add(item.period_key)
        candidates: list[SignalCandidate] = []
        metrics_by_strategy: dict[str, list[MetricDefinitionRow]] = defaultdict(list)
        for metric in metrics:
            if metric.strategy_entity_id:
                metrics_by_strategy[metric.strategy_entity_id].append(metric)
            recent = observations_by_metric.get(metric.id, [])
            misses = [item for item in recent[:3] if item.status == "MISS"]
            if len(misses) >= 2:
                candidates.append(
                    SignalCandidate(
                        side="OUTCOME",
                        signal_key="repeated_metric_miss",
                        issue_family="KPI_PERFORMANCE",
                        title=f"指标“{metric.name}”连续偏离目标",
                        summary=f"最近三次记录中有 {len(misses)} 次未达标。",
                        severity="HIGH" if metric.scope == "ENTERPRISE_OUTCOME" else "WARNING",
                        confidence=0.99,
                        affected_entity_ids=[
                            item
                            for item in [
                                metric.owner_entity_id,
                                metric.strategy_entity_id,
                                metric.outcome_entity_id,
                            ]
                            if item
                        ],
                        evidence=self._observation_evidence(misses),
                        facts={"metric_id": metric.id, "miss_count": len(misses)},
                    )
                )
            numeric = [item.numeric_value for item in recent if item.numeric_value is not None]
            if len(numeric) >= 3:
                mean = statistics.fmean(numeric)
                volatility = statistics.pstdev(numeric) / max(abs(mean), 1e-9)
                if volatility >= 0.25:
                    candidates.append(
                        SignalCandidate(
                            side="OUTCOME",
                            signal_key="metric_volatility",
                            issue_family="KPI_PERFORMANCE",
                            title=f"指标“{metric.name}”出现高波动",
                            summary=f"最近记录的变异系数为 {volatility:.2f}。",
                            severity="WARNING",
                            confidence=0.95,
                            affected_entity_ids=[
                                item
                                for item in [metric.owner_entity_id, metric.strategy_entity_id]
                                if item
                            ],
                            evidence=self._observation_evidence(recent),
                            facts={"metric_id": metric.id, "coefficient_of_variation": volatility},
                        )
                    )

        for strategy_id, linked_metrics in metrics_by_strategy.items():
            local_improved: dict[str, MetricDefinitionRow] = {}
            outcomes_worsened: dict[str, MetricDefinitionRow] = {}
            local_metrics = [item for item in linked_metrics if item.scope == "LOCAL"]
            outcome_metrics = [
                item for item in linked_metrics if item.scope == "ENTERPRISE_OUTCOME"
            ]
            for local_metric in local_metrics:
                for outcome_metric in outcome_metrics:
                    local_rows, outcome_rows = self._shared_period_rows(
                        observations_by_metric.get(local_metric.id, []),
                        observations_by_metric.get(outcome_metric.id, []),
                    )
                    if (
                        self._trend(local_metric, local_rows) > 0
                        and self._trend(outcome_metric, outcome_rows) < 0
                    ):
                        local_improved[local_metric.id] = local_metric
                        outcomes_worsened[outcome_metric.id] = outcome_metric
            if local_improved and outcomes_worsened:
                candidates.append(
                    SignalCandidate(
                        side="OUTCOME",
                        signal_key="local_global_divergence",
                        issue_family="STRATEGY_OUTCOME",
                        title="局部绩效改善但企业结果恶化",
                        summary=(
                            "同一战略目标下，局部指标改善（"
                            + "、".join(item.name for item in local_improved.values())
                            + "），但企业结果指标恶化（"
                            + "、".join(item.name for item in outcomes_worsened.values())
                            + "）。"
                        ),
                        severity="CRITICAL",
                        confidence=0.98,
                        affected_entity_ids=[
                            strategy_id,
                            *[
                                item.owner_entity_id
                                for item in local_improved.values()
                                if item.owner_entity_id
                            ],
                        ],
                        facts={
                            "improving_local_metric_ids": list(local_improved),
                            "worsening_outcome_metric_ids": [
                                item.id for item in outcomes_worsened.values()
                            ],
                        },
                    )
                )

        meetings = self.session.scalars(
            select(MeetingRecordRow)
            .where(MeetingRecordRow.project_id == str(project_id))
            .order_by(MeetingRecordRow.occurred_at.desc())
            .limit(50)
        ).all()
        topic_records: dict[str, list[MeetingRecordRow]] = defaultdict(list)
        escalation_records: dict[str, list[MeetingRecordRow]] = defaultdict(list)
        current = now_utc()
        for meeting in meetings:
            for topic in meeting.topics:
                topic_records[self._normalize_topic(topic)].append(meeting)
            for index, action_item in enumerate(meeting.action_items):
                entity_ids = sorted(
                    {
                        *meeting.related_entity_ids,
                        *[value for value in [action_item.get("owner_entity_id")] if value],
                    }
                )
                missing = []
                if not action_item.get("owner_entity_id"):
                    missing.append("负责人")
                if not action_item.get("due_at"):
                    missing.append("截止时间")
                if missing and action_item.get("status", "OPEN") == "OPEN":
                    candidates.append(
                        SignalCandidate(
                            side="OUTCOME",
                            signal_key="meeting_action_incomplete_control",
                            issue_family="ACCOUNTABILITY",
                            title=f"会议行动项缺少{'和'.join(missing)}",
                            summary=str(action_item.get("title") or meeting.title),
                            severity="WARNING",
                            confidence=0.99,
                            affected_entity_ids=entity_ids,
                            evidence=list(meeting.evidence or []),
                            facts={"meeting_id": meeting.id, "action_index": index},
                        )
                    )
                due_at = self._parse_datetime(action_item.get("due_at"))
                if due_at and due_at < current and action_item.get("status", "OPEN") == "OPEN":
                    candidates.append(
                        SignalCandidate(
                            side="OUTCOME",
                            signal_key="overdue_meeting_action",
                            issue_family="EXECUTION_DISCIPLINE",
                            title="会议行动项已经逾期",
                            summary=str(action_item.get("title") or meeting.title),
                            severity="HIGH",
                            confidence=0.99,
                            affected_entity_ids=entity_ids,
                            evidence=list(meeting.evidence or []),
                            facts={"meeting_id": meeting.id, "due_at": due_at.isoformat()},
                        )
                    )
            for escalation in meeting.escalations:
                escalation_records[self._normalize_topic(str(escalation.get("topic", "")))].append(
                    meeting
                )
        for topic, records in topic_records.items():
            if topic and len({item.id for item in records}) >= 2:
                candidates.append(
                    SignalCandidate(
                        side="OUTCOME",
                        signal_key="recurring_meeting_issue",
                        issue_family="PROCESS_CONTROL",
                        title=f"议题“{topic}”在会议中反复出现",
                        summary=f"该议题在 {len({item.id for item in records})} 次会议中出现。",
                        severity="WARNING",
                        confidence=0.9,
                        affected_entity_ids=sorted(
                            {
                                entity_id
                                for record in records
                                for entity_id in record.related_entity_ids
                            }
                        ),
                        evidence=self._meeting_evidence(records),
                        facts={"normalized_topic": topic},
                    )
                )
        for topic, records in escalation_records.items():
            if topic and len({item.id for item in records}) >= 2:
                candidates.append(
                    SignalCandidate(
                        side="OUTCOME",
                        signal_key="recurring_escalation",
                        issue_family="EXECUTION_DISCIPLINE",
                        title=f"议题“{topic}”被反复升级到管理层",
                        summary=f"该议题至少升级 {len({item.id for item in records})} 次。",
                        severity="HIGH",
                        confidence=0.95,
                        affected_entity_ids=sorted(
                            {
                                entity_id
                                for record in records
                                for entity_id in record.related_entity_ids
                            }
                        ),
                        evidence=self._meeting_evidence(records),
                        facts={"normalized_topic": topic},
                    )
                )
        return candidates

    def _persist_signal(
        self,
        run: ManagementAnalysisRunRow,
        candidate: SignalCandidate,
        fingerprint: str,
    ) -> ManagementSignalRow:
        entity_ids = sorted(set(candidate.affected_entity_ids))
        row = ManagementSignalRow(
            run_id=run.id,
            project_id=run.project_id,
            side=candidate.side,
            signal_key=candidate.signal_key,
            issue_family=candidate.issue_family,
            title=candidate.title,
            summary=candidate.summary,
            severity=candidate.severity,
            confidence=max(0.0, min(candidate.confidence, 1.0)),
            expected_direction=candidate.expected_direction,
            affected_entity_ids=entity_ids,
            evidence=json_ready(candidate.evidence),
            facts=json_ready(candidate.facts),
            fingerprint=fingerprint,
        )
        self.session.add(row)
        return row

    @staticmethod
    def _signal_fingerprint(candidate: SignalCandidate) -> str:
        raw = "|".join(
            [
                candidate.side,
                candidate.signal_key,
                candidate.issue_family,
                *sorted(set(candidate.affected_entity_ids)),
                str(candidate.facts),
            ]
        )
        return hashlib.sha256(raw.encode("utf-8")).hexdigest()

    def _reconcile(
        self,
        project_id: UUID,
        run: ManagementAnalysisRunRow,
        signals: list[ManagementSignalRow],
    ) -> list[ManagementInsightRow]:
        design = [item for item in signals if item.side == "DESIGN"]
        outcome = [item for item in signals if item.side == "OUTCOME"]
        tradeoffs = self.session.scalars(
            select(DesignTradeoffRow).where(
                DesignTradeoffRow.project_id == str(project_id),
                DesignTradeoffRow.status == TradeoffStatus.ACCEPTED.value,
            )
        ).all()
        matched_outcomes: set[str] = set()
        results: list[ManagementInsightRow] = []
        for design_signal in design:
            matching = [
                item
                for item in outcome
                if self._signals_match(design_signal, item)
            ]
            matching_ids = {item.id for item in matching}
            matched_outcomes.update(matching_ids)
            related_tradeoff = self._matching_tradeoff(design_signal, tradeoffs)
            directions = {design_signal.expected_direction} | {
                item.expected_direction for item in matching
            }
            if related_tradeoff is not None:
                classification = ManagementInsightClassification.ACCEPTED_TRADEOFF.value
                rationale = (
                    f"该风险与已接受取舍“{related_tradeoff.title}”一致；继续按监控指标观察。"
                )
            elif len(directions) > 1:
                classification = ManagementInsightClassification.EVIDENCE_CONFLICT.value
                rationale = "设计侧与结果侧信号方向冲突，需要补充证据后再判断。"
            elif matching:
                classification = ManagementInsightClassification.CORROBORATED.value
                rationale = "设计侧结构风险与现实结果同时出现，应优先由管理层确认。"
            else:
                classification = ManagementInsightClassification.STRUCTURAL_WARNING.value
                rationale = "当前仅有设计侧证据，属于前置预警，尚未观察到结果侧验证。"
            results.append(
                self._insight(
                    run,
                    classification,
                    design_signal.issue_family,
                    design_signal.title,
                    self._combined_summary(design_signal, matching),
                    [design_signal],
                    matching,
                    rationale,
                )
            )
        for outcome_signal in outcome:
            if outcome_signal.id in matched_outcomes:
                continue
            results.append(
                self._insight(
                    run,
                    ManagementInsightClassification.UNEXPLAINED_ANOMALY.value,
                    outcome_signal.issue_family,
                    outcome_signal.title,
                    outcome_signal.summary,
                    [],
                    [outcome_signal],
                    "当前只有结果侧异常，现有企业投影尚不能解释，应补充调研或结构关系。",
                )
            )
        return results

    def _insight(
        self,
        run: ManagementAnalysisRunRow,
        classification: str,
        issue_family: str,
        title: str,
        summary: str,
        design: list[ManagementSignalRow],
        outcome: list[ManagementSignalRow],
        rationale: str,
    ) -> ManagementInsightRow:
        all_signals = [*design, *outcome]
        affected_entity_ids = sorted(
            {entity_id for item in all_signals for entity_id in item.affected_entity_ids}
        )
        severity = max(
            (item.severity for item in all_signals),
            key=lambda value: SEVERITY_ORDER.get(value, 0),
        )
        confidence = max(item.confidence for item in all_signals)
        if design and outcome:
            confidence = min(0.99, statistics.fmean(item.confidence for item in all_signals) + 0.1)
        anchor_signal_key = sorted(
            {item.signal_key for item in (design or outcome)}
        )[0]
        issue_key = hashlib.sha256(
            json.dumps(
                [issue_family, anchor_signal_key, affected_entity_ids],
                ensure_ascii=False,
                separators=(",", ":"),
            ).encode("utf-8")
        ).hexdigest()
        occurrence_signature = hashlib.sha256(
            json.dumps(
                {
                    "classification": classification,
                    "severity": severity,
                    "signals": sorted(item.fingerprint for item in all_signals),
                },
                ensure_ascii=False,
                sort_keys=True,
                separators=(",", ":"),
            ).encode("utf-8")
        ).hexdigest()
        issue = self.session.scalar(
            select(ManagementIssueRow)
            .where(
                ManagementIssueRow.project_id == run.project_id,
                ManagementIssueRow.issue_key == issue_key,
            )
            .limit(1)
        )
        evidence_changed = False
        if issue is None:
            issue = ManagementIssueRow(
                project_id=run.project_id,
                issue_key=issue_key,
                issue_family=issue_family,
                title=title,
                affected_entity_ids=affected_entity_ids,
                last_occurrence_signature=occurrence_signature,
                occurrence_count=1,
            )
            self.session.add(issue)
            self.session.flush()
        else:
            evidence_changed = issue.last_occurrence_signature != occurrence_signature
            issue.last_occurrence_signature = occurrence_signature
            issue.last_seen_at = now_utc()
            issue.occurrence_count += 1
            issue.revision += 1
            issue.affected_entity_ids = sorted(
                set(issue.affected_entity_ids) | set(affected_entity_ids)
            )
        row = ManagementInsightRow(
            run_id=run.id,
            project_id=run.project_id,
            issue_id=issue.id,
            occurrence_number=issue.occurrence_count,
            occurrence_signature=occurrence_signature,
            evidence_changed=evidence_changed,
            classification=classification,
            issue_family=issue_family,
            title=title,
            summary=summary,
            severity=severity,
            confidence=confidence,
            design_signal_ids=[item.id for item in design],
            outcome_signal_ids=[item.id for item in outcome],
            affected_entity_ids=affected_entity_ids,
            rationale=rationale,
            status=issue.status,
            management_feedback=issue.management_feedback,
        )
        self.session.add(row)
        return row

    @staticmethod
    def _signals_match(left: ManagementSignalRow, right: ManagementSignalRow) -> bool:
        if left.issue_family != right.issue_family:
            return False
        left_entities = set(left.affected_entity_ids or [])
        right_entities = set(right.affected_entity_ids or [])
        return bool(left_entities and right_entities and left_entities & right_entities)

    @staticmethod
    def _matching_tradeoff(
        signal: ManagementSignalRow, tradeoffs: Sequence[DesignTradeoffRow]
    ) -> DesignTradeoffRow | None:
        signal_entities = set(signal.affected_entity_ids or [])
        for tradeoff in tradeoffs:
            if tradeoff.issue_family != signal.issue_family:
                continue
            tradeoff_entities = set(tradeoff.affected_entity_ids or [])
            if not tradeoff_entities or signal_entities & tradeoff_entities:
                return tradeoff
        return None

    @staticmethod
    def _combined_summary(
        design: ManagementSignalRow, outcomes: list[ManagementSignalRow]
    ) -> str:
        if not outcomes:
            return design.summary
        return design.summary + " 结果侧同时发现：" + "；".join(item.summary for item in outcomes)

    @staticmethod
    def _candidate(
        signal_key: str,
        issue_family: str,
        title: str,
        summary: str,
        severity: str,
        confidence: float,
        affected_entity_ids: list[str],
        *,
        facts: dict[str, Any] | None = None,
    ) -> SignalCandidate:
        return SignalCandidate(
            side=ManagementSignalSide.DESIGN.value,
            signal_key=signal_key,
            issue_family=issue_family,
            title=title,
            summary=summary,
            severity=severity,
            confidence=confidence,
            affected_entity_ids=affected_entity_ids,
            facts=facts or {},
        )

    @staticmethod
    def _role_ids(relation: RelationRow, role_key: str) -> set[str]:
        return {item.entity_id for item in relation.participants if item.role_key == role_key}

    @staticmethod
    def _is_approval_step(entity: EntityRow) -> bool:
        text = " ".join(
            [
                entity.name,
                str(entity.properties.get("control_purpose", "")),
                str(entity.properties.get("step_kind", "")),
            ]
        ).lower()
        return any(word in text for word in ("审批", "批准", "复核", "approval", "review"))

    @staticmethod
    def _trend(metric: MetricDefinitionRow, rows: list[MetricObservationRow]) -> int:
        numeric = [item.numeric_value for item in rows[:2] if item.numeric_value is not None]
        if len(numeric) < 2 or math.isclose(numeric[0], numeric[1]):
            return 0
        raw = 1 if numeric[0] > numeric[1] else -1
        if metric.direction == MetricDirection.LOWER_IS_BETTER.value:
            return -raw
        if metric.direction == MetricDirection.TARGET_RANGE.value:
            if rows[0].status == "ON_TARGET" and rows[1].status == "MISS":
                return 1
            if rows[0].status == "MISS" and rows[1].status == "ON_TARGET":
                return -1
            return 0
        return raw

    @staticmethod
    def _shared_period_rows(
        left: list[MetricObservationRow], right: list[MetricObservationRow]
    ) -> tuple[list[MetricObservationRow], list[MetricObservationRow]]:
        right_periods = {item.period_key for item in right}
        common_periods = {item.period_key for item in left if item.period_key in right_periods}
        if len(common_periods) < 2:
            return [], []
        return (
            [item for item in left if item.period_key in common_periods],
            [item for item in right if item.period_key in common_periods],
        )

    @staticmethod
    def _computed_metric_status(
        metric: MetricDefinitionRow, numeric_value: float | None
    ) -> str:
        if numeric_value is None or metric.target_value is None:
            return "UNKNOWN"
        target = metric.target_value
        if metric.direction == MetricDirection.TARGET_RANGE.value:
            if isinstance(target, dict):
                lower = ManagementIntelligenceService._numeric(target.get("min"))
                upper = ManagementIntelligenceService._numeric(target.get("max"))
            elif isinstance(target, list | tuple) and len(target) == 2:
                lower = ManagementIntelligenceService._numeric(target[0])
                upper = ManagementIntelligenceService._numeric(target[1])
            else:
                return "UNKNOWN"
            if lower is None or upper is None or lower > upper:
                return "UNKNOWN"
            return "ON_TARGET" if lower <= numeric_value <= upper else "MISS"
        target_number = ManagementIntelligenceService._numeric(target)
        if target_number is None:
            return "UNKNOWN"
        if metric.direction == MetricDirection.LOWER_IS_BETTER.value:
            return "ON_TARGET" if numeric_value <= target_number else "MISS"
        return "ON_TARGET" if numeric_value >= target_number else "MISS"

    @staticmethod
    def _numeric(value: Any) -> float | None:
        if isinstance(value, bool):
            return None
        if isinstance(value, int | float) and math.isfinite(float(value)):
            return float(value)
        return None

    @staticmethod
    def _normalize_topic(value: str) -> str:
        return re.sub(r"[^\w\u4e00-\u9fff]+", "", value.lower())[:200]

    @staticmethod
    def _parse_datetime(value: Any) -> datetime | None:
        if not value:
            return None
        if isinstance(value, datetime):
            return value
        try:
            parsed = datetime.fromisoformat(str(value).replace("Z", "+00:00"))
        except ValueError:
            return None
        if parsed.tzinfo is None:
            parsed = parsed.replace(tzinfo=now_utc().tzinfo)
        return parsed

    @staticmethod
    def _observation_evidence(rows: list[MetricObservationRow]) -> list[dict[str, Any]]:
        result: list[dict[str, Any]] = []
        for row in rows:
            if row.evidence:
                result.extend(row.evidence)
        return result

    @staticmethod
    def _meeting_evidence(rows: list[MeetingRecordRow]) -> list[dict[str, Any]]:
        result: list[dict[str, Any]] = []
        for row in rows:
            result.extend(row.evidence or [])
        return result

    def _validate_metric_entity_references(
        self, project_id: UUID, payload: MetricDefinitionCreate
    ) -> None:
        entity_ids = [
            item
            for item in [
                payload.owner_entity_id,
                payload.strategy_entity_id,
                payload.outcome_entity_id,
            ]
            if item is not None
        ]
        rows = self._validate_entity_ids(project_id, entity_ids)
        by_id = {row.id: row for row in rows}
        if payload.strategy_entity_id is not None:
            row = by_id[str(payload.strategy_entity_id)]
            if row.type_key != "strategy_objective":
                raise DomainError(
                    "METRIC_STRATEGY_TYPE_MISMATCH",
                    "strategy_entity_id 必须指向战略目标。",
                    status_code=422,
                )
        if payload.outcome_entity_id is not None:
            row = by_id[str(payload.outcome_entity_id)]
            if row.type_key != "business_outcome":
                raise DomainError(
                    "METRIC_OUTCOME_TYPE_MISMATCH",
                    "outcome_entity_id 必须指向企业结果。",
                    status_code=422,
                )

    def _validate_metric_entity(self, project_id: UUID, entity: EntityRow) -> None:
        if (
            entity.project_id != str(project_id)
            or entity.status == LifecycleStatus.RETIRED.value
            or entity.design_membership != "MODELED"
        ):
            raise DomainError(
                "METRIC_ENTITY_INVALID",
                "指标必须对应当前项目内有效的正式本体实体。",
                status_code=422,
            )
        type_row = ProjectionService(self.session).ontology.require_type_by_key(
            project_id, entity.type_key
        )
        if type_row.kind != TypeKind.METRIC.value:
            raise DomainError(
                "METRIC_ENTITY_TYPE_MISMATCH",
                "指标定义只能对应 METRIC 类型实体。",
                status_code=422,
            )

    def _metric_entity_properties(
        self,
        project_id: UUID,
        payload: MetricDefinitionCreate,
        *,
        active: bool,
    ) -> dict[str, Any]:
        type_row = ProjectionService(self.session).ontology.require_type_by_key(
            project_id, "metric", kind=TypeKind.METRIC
        )
        allowed = {
            item["key"]
            for item in ProjectionService(self.session).ontology.property_definitions(
                type_row
            ).values()
        }
        values = {
            **payload.properties,
            "scope": payload.scope.value,
            "direction": payload.direction.value,
            "target_value": payload.target_value,
            "active": active,
        }
        if payload.unit is not None:
            values["unit"] = payload.unit
        return {key: value for key, value in values.items() if key in allowed}

    def _validate_meeting_entities(
        self, project_id: UUID, payload: MeetingRecordCreate
    ) -> None:
        ids = [*payload.participant_entity_ids, *payload.related_entity_ids]
        ids.extend(
            item.owner_entity_id for item in payload.decisions if item.owner_entity_id is not None
        )
        ids.extend(
            item.owner_entity_id
            for item in payload.action_items
            if item.owner_entity_id is not None
        )
        self._validate_entity_ids(project_id, ids)

    def _validate_entity_ids(
        self, project_id: UUID, entity_ids: Sequence[UUID | str]
    ) -> list[EntityRow]:
        normalized = {str(item) for item in entity_ids}
        if not normalized:
            return []
        rows = self.session.scalars(
            select(EntityRow).where(
                EntityRow.project_id == str(project_id),
                EntityRow.id.in_(normalized),
                EntityRow.status != LifecycleStatus.RETIRED.value,
            )
        ).all()
        if {item.id for item in rows} != normalized:
            raise DomainError(
                "ENTITY_REFERENCE_INVALID",
                "引用的企业对象不存在、不属于当前项目或已经停用。",
                status_code=422,
            )
        return list(rows)

    def _validate_metric_ids(
        self, project_id: UUID, metric_ids: Sequence[UUID | str]
    ) -> None:
        normalized = {str(item) for item in metric_ids}
        if not normalized:
            return
        found = set(
            self.session.scalars(
                select(MetricDefinitionRow.id).where(
                    MetricDefinitionRow.project_id == str(project_id),
                    MetricDefinitionRow.id.in_(normalized),
                )
            ).all()
        )
        if found != normalized:
            raise DomainError(
                "METRIC_REFERENCE_INVALID", "引用的监控指标不存在。", status_code=422
            )

    def _require_run(self, project_id: UUID, run_id: UUID) -> ManagementAnalysisRunRow:
        row = self.session.get(ManagementAnalysisRunRow, str(run_id))
        if row is None or row.project_id != str(project_id):
            raise DomainError(
                "MANAGEMENT_ANALYSIS_NOT_FOUND", "管理分析运行不存在。", status_code=404
            )
        return row
