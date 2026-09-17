from __future__ import annotations

import json
from collections import Counter, defaultdict
from datetime import UTC, datetime
from hashlib import sha256
from itertools import pairwise
from typing import Any
from uuid import UUID, uuid4

from pydantic import BaseModel, ConfigDict, Field, field_validator, model_validator
from sqlalchemy import JSON, DateTime, Index, Integer, String, UniqueConstraint, func, select
from sqlalchemy.orm import DeclarativeBase, Mapped, Session, mapped_column

from enterprise_insight_backend.errors import DomainError
from enterprise_insight_backend.models import ProjectRow
from enterprise_insight_backend.observations import ObservationBase
from enterprise_insight_backend.service_utils import json_ready, now_utc


class WorkObservationModel(BaseModel):
    model_config = ConfigDict(extra="forbid")


class WorkObservationActivity(WorkObservationModel):
    app: str = Field(min_length=1, max_length=200)
    domain: str | None = Field(default=None, max_length=300)
    category: str | None = Field(default=None, max_length=120)
    context: str | None = Field(default=None, max_length=300)

    @field_validator("app", "domain", "category", "context")
    @classmethod
    def strip_text(cls, value: str | None) -> str | None:
        if value is None:
            return None
        value = value.strip()
        return value or None


class WorkObservationEventCreate(WorkObservationModel):
    event_id: str = Field(min_length=1, max_length=200)
    session_id: str = Field(min_length=1, max_length=200)
    sequence: int = Field(ge=0)
    observed_at: datetime
    source_employee_key: str = Field(min_length=1, max_length=200)
    source_role_key: str | None = Field(default=None, max_length=200)
    activity: WorkObservationActivity
    state: str = Field(default="FOREGROUND", min_length=1, max_length=40)
    metadata: dict[str, Any] = Field(default_factory=dict)
    source_id: str | None = Field(default=None, max_length=200)
    batch_id: str | None = Field(default=None, max_length=200)

    @field_validator("event_id", "session_id", "source_employee_key", "source_role_key")
    @classmethod
    def strip_identity(cls, value: str | None) -> str | None:
        if value is None:
            return None
        value = value.strip()
        if not value:
            raise ValueError("标识不能为空。")
        return value

    @field_validator("state")
    @classmethod
    def normalize_state(cls, value: str) -> str:
        value = value.strip().upper()
        if not value:
            raise ValueError("状态不能为空。")
        return value

    @field_validator("observed_at")
    @classmethod
    def require_timezone(cls, value: datetime) -> datetime:
        if value.tzinfo is None or value.utcoffset() is None:
            raise ValueError("observed_at 必须包含时区。")
        return value.astimezone(UTC)


class WorkObservationPackage(WorkObservationModel):
    format_version: str = Field(default="1.0", min_length=1, max_length=40)
    batch_id: str = Field(min_length=1, max_length=200)
    source_id: str = Field(min_length=1, max_length=200)
    events: list[WorkObservationEventCreate] = Field(min_length=1, max_length=100_000)

    @field_validator("format_version", "batch_id", "source_id")
    @classmethod
    def strip_envelope(cls, value: str) -> str:
        value = value.strip()
        if not value:
            raise ValueError("数据包标识不能为空。")
        return value

    @model_validator(mode="after")
    def validate_event_envelope(self) -> WorkObservationPackage:
        event_ids = [event.event_id for event in self.events]
        if len(event_ids) != len(set(event_ids)):
            raise ValueError("同一数据包内 event_id 必须唯一。")
        for event in self.events:
            if event.source_id is not None and event.source_id != self.source_id:
                raise ValueError("事件 source_id 与数据包不一致。")
            if event.batch_id is not None and event.batch_id != self.batch_id:
                raise ValueError("事件 batch_id 与数据包不一致。")
        return self


class WorkObservationIngestionRequest(WorkObservationModel):
    project_id: UUID
    package: WorkObservationPackage


class WorkObservationImportConfirm(WorkObservationModel):
    preview_id: UUID
    payload_hash: str = Field(min_length=64, max_length=64)


class WorkObservationAnalysisCreate(WorkObservationModel):
    employee_keys: list[str] = Field(default_factory=list, max_length=500)
    role_keys: list[str] = Field(default_factory=list, max_length=200)
    source_ids: list[str] = Field(default_factory=list, max_length=200)
    observed_from: datetime | None = None
    observed_to: datetime | None = None
    gap_seconds: int = Field(default=900, ge=30, le=86_400)

    @field_validator("observed_from", "observed_to")
    @classmethod
    def normalize_filter_time(cls, value: datetime | None) -> datetime | None:
        if value is None:
            return None
        if value.tzinfo is None or value.utcoffset() is None:
            raise ValueError("筛选时间必须包含时区。")
        return value.astimezone(UTC)

    @model_validator(mode="after")
    def validate_range(self) -> WorkObservationAnalysisCreate:
        if self.observed_from and self.observed_to and self.observed_from > self.observed_to:
            raise ValueError("observed_from 不能晚于 observed_to。")
        return self


class WorkObservationComparisonCreate(WorkObservationModel):
    analysis_id: UUID
    left_employee_keys: list[str] = Field(min_length=1, max_length=200)
    right_employee_keys: list[str] = Field(min_length=1, max_length=200)


class WorkObservationIdentityBindingCreate(WorkObservationModel):
    source_id: str = Field(min_length=1, max_length=200)
    source_employee_key: str = Field(min_length=1, max_length=200)
    formal_entity_id: UUID
    formal_role_key: str | None = Field(default=None, max_length=200)


class WorkObservationIdentityBindingRetire(WorkObservationModel):
    source_id: str = Field(min_length=1, max_length=200)
    source_employee_key: str = Field(min_length=1, max_length=200)
    reason: str = Field(min_length=1, max_length=2_000)

    @field_validator("source_id", "source_employee_key", "reason")
    @classmethod
    def normalize_retire_text(cls, value: str) -> str:
        value = value.strip()
        if not value:
            raise ValueError("解绑标识和原因不能为空。")
        return value


class WorkObservationVirtualCandidateCreate(WorkObservationModel):
    """Request a human-reviewable virtual-work candidate from an analysis."""

    role_key: str | None = Field(default=None, max_length=200)
    min_count: int = Field(default=2, ge=1, le=10_000)


class WorkObservationVirtualCandidateConfirm(WorkObservationModel):
    decision: str = Field(pattern=r"^(CONFIRM|REJECT)$")
    reason: str = Field(min_length=1, max_length=2_000)
    virtual_work_model_id: UUID | None = None
    position_node_id: UUID | None = None

    @field_validator("reason")
    @classmethod
    def normalize_candidate_reason(cls, value: str) -> str:
        value = value.strip()
        if not value:
            raise ValueError("确认或拒绝原因不能为空。")
        return value


class WorkObservationIdentityBindingView(WorkObservationModel):
    id: UUID
    project_id: UUID
    source_id: str
    source_employee_key: str
    formal_entity_id: UUID
    formal_role_key: str | None
    status: str
    created_at: datetime
    updated_at: datetime


class WorkObservationIdentityBindingHistoryView(WorkObservationModel):
    id: UUID
    project_id: UUID
    source_id: str
    source_employee_key: str
    formal_entity_id: UUID | None
    formal_role_key: str | None
    operation: str
    actor_id: str
    reason: str | None
    created_at: datetime


class WorkObservationVirtualCandidateView(WorkObservationModel):
    id: UUID
    project_id: UUID
    analysis_id: UUID
    candidate_type: str
    label: str
    role_key: str | None
    properties: dict[str, Any]
    evidence_segment_ids: list[str]
    status: str
    virtual_work_model_id: UUID | None
    virtual_node_id: UUID | None
    virtual_edge_id: UUID | None
    decision_reason: str | None
    created_at: datetime
    decided_at: datetime | None


class WorkObservationBatchView(WorkObservationModel):
    id: UUID
    project_id: UUID
    source_batch_id: str
    source_id: str
    format_version: str
    payload_hash: str
    event_count: int
    accepted_count: int
    duplicate_count: int
    status: str
    created_at: datetime


class WorkObservationImportResultView(WorkObservationModel):
    batch_id: UUID
    source_batch_id: str
    status: str
    accepted_count: int
    duplicate_count: int
    event_count: int
    duplicate: bool = False


class WorkObservationPreviewView(WorkObservationModel):
    id: UUID
    project_id: UUID
    source_batch_id: str
    source_id: str
    format_version: str
    payload_hash: str
    event_count: int
    status: str
    created_at: datetime
    sample_events: list[dict[str, Any]] = Field(default_factory=list)
    employee_keys: list[str] = Field(default_factory=list)
    role_keys: list[str] = Field(default_factory=list)
    state_counts: dict[str, int] = Field(default_factory=dict)
    first_observed_at: datetime | None = None
    last_observed_at: datetime | None = None
    duplicate_event_count: int = 0
    conflict_event_count: int = 0
    identity_status: str = "SOURCE_KEYS_UNVERIFIED"
    warnings: list[str] = Field(default_factory=list)


class WorkObservationCoverageView(WorkObservationModel):
    project_id: UUID
    event_count: int
    foreground_event_count: int
    employee_count: int
    session_count: int
    source_count: int
    first_observed_at: datetime | None
    last_observed_at: datetime | None
    batch_count: int


class WorkObservationAnalysisView(WorkObservationModel):
    id: UUID
    project_id: UUID
    status: str
    filters: dict[str, Any]
    event_count: int
    segment_count: int
    employee_count: int
    result: dict[str, Any]
    created_at: datetime


class WorkObservationComparisonView(WorkObservationModel):
    id: UUID
    project_id: UUID
    analysis_id: UUID
    left_employee_keys: list[str]
    right_employee_keys: list[str]
    result: dict[str, Any]
    created_at: datetime


class WorkObservationBase(DeclarativeBase):
    """Work observations use the physically separate observations database."""

    metadata = ObservationBase.metadata


class WorkObservationBatchRow(WorkObservationBase):
    __tablename__ = "work_observation_batches"
    __table_args__ = (
        UniqueConstraint("project_id", "batch_id", name="uq_work_observation_project_batch"),
        Index("ix_work_observation_batch_project_created", "project_id", "created_at"),
    )

    id: Mapped[str] = mapped_column(String(36), primary_key=True)
    company_id: Mapped[str] = mapped_column(String(36), index=True)
    project_id: Mapped[str] = mapped_column(String(36), index=True)
    batch_id: Mapped[str] = mapped_column(String(200))
    source_id: Mapped[str] = mapped_column(String(200), index=True)
    format_version: Mapped[str] = mapped_column(String(40))
    payload_hash: Mapped[str] = mapped_column(String(64))
    event_count: Mapped[int] = mapped_column(Integer)
    accepted_count: Mapped[int] = mapped_column(Integer)
    duplicate_count: Mapped[int] = mapped_column(Integer)
    status: Mapped[str] = mapped_column(String(24), index=True)
    created_at: Mapped[datetime] = mapped_column(DateTime(timezone=True))


class WorkObservationIdentityBindingRow(WorkObservationBase):
    __tablename__ = "work_observation_identity_bindings"
    __table_args__ = (
        UniqueConstraint(
            "project_id",
            "source_id",
            "source_employee_key",
            name="uq_work_observation_identity_binding",
        ),
        Index(
            "ix_work_observation_identity_binding_project_entity",
            "project_id",
            "formal_entity_id",
        ),
    )

    id: Mapped[str] = mapped_column(String(36), primary_key=True)
    company_id: Mapped[str] = mapped_column(String(36), index=True)
    project_id: Mapped[str] = mapped_column(String(36), index=True)
    source_id: Mapped[str] = mapped_column(String(200))
    source_employee_key: Mapped[str] = mapped_column(String(200))
    formal_entity_id: Mapped[str] = mapped_column(String(36))
    formal_role_key: Mapped[str | None] = mapped_column(String(200))
    status: Mapped[str] = mapped_column(String(24), index=True, default="ACTIVE")
    created_at: Mapped[datetime] = mapped_column(DateTime(timezone=True))
    updated_at: Mapped[datetime] = mapped_column(DateTime(timezone=True))


class WorkObservationIdentityBindingHistoryRow(WorkObservationBase):
    __tablename__ = "work_observation_identity_binding_history"
    __table_args__ = (
        Index(
            "ix_work_observation_identity_binding_history_key",
            "project_id",
            "source_id",
            "source_employee_key",
            "created_at",
        ),
    )

    id: Mapped[str] = mapped_column(String(36), primary_key=True)
    company_id: Mapped[str] = mapped_column(String(36), index=True)
    project_id: Mapped[str] = mapped_column(String(36), index=True)
    source_id: Mapped[str] = mapped_column(String(200))
    source_employee_key: Mapped[str] = mapped_column(String(200))
    formal_entity_id: Mapped[str | None] = mapped_column(String(36))
    formal_role_key: Mapped[str | None] = mapped_column(String(200))
    operation: Mapped[str] = mapped_column(String(32))
    actor_id: Mapped[str] = mapped_column(String(128))
    reason: Mapped[str | None] = mapped_column(String(2_000))
    created_at: Mapped[datetime] = mapped_column(DateTime(timezone=True))


class WorkObservationVirtualCandidateRow(WorkObservationBase):
    __tablename__ = "work_observation_virtual_candidates"
    __table_args__ = (
        Index("ix_work_observation_virtual_candidate_project_created", "project_id", "created_at"),
        Index("ix_work_observation_virtual_candidate_analysis", "analysis_id"),
    )

    id: Mapped[str] = mapped_column(String(36), primary_key=True)
    company_id: Mapped[str] = mapped_column(String(36), index=True)
    project_id: Mapped[str] = mapped_column(String(36), index=True)
    analysis_id: Mapped[str] = mapped_column(String(36), index=True)
    candidate_type: Mapped[str] = mapped_column(String(40))
    label: Mapped[str] = mapped_column(String(300))
    role_key: Mapped[str | None] = mapped_column(String(200), index=True)
    properties: Mapped[dict[str, Any]] = mapped_column(JSON, default=dict)
    evidence_segment_ids: Mapped[list[str]] = mapped_column(JSON, default=list)
    status: Mapped[str] = mapped_column(String(24), index=True)
    virtual_work_model_id: Mapped[str | None] = mapped_column(String(36))
    virtual_node_id: Mapped[str | None] = mapped_column(String(36))
    virtual_edge_id: Mapped[str | None] = mapped_column(String(36))
    decision_reason: Mapped[str | None] = mapped_column(String(2_000))
    created_at: Mapped[datetime] = mapped_column(DateTime(timezone=True))
    decided_at: Mapped[datetime | None] = mapped_column(DateTime(timezone=True))


class WorkObservationEventRow(WorkObservationBase):
    __tablename__ = "work_observation_events"
    __table_args__ = (
        UniqueConstraint(
            "project_id", "source_id", "event_id", name="uq_work_observation_project_event"
        ),
        Index(
            "ix_work_observation_event_project_employee_time",
            "project_id",
            "source_employee_key",
            "observed_at",
        ),
        Index(
            "ix_work_observation_event_project_session_time",
            "project_id",
            "session_id",
            "observed_at",
        ),
    )

    id: Mapped[str] = mapped_column(String(36), primary_key=True)
    company_id: Mapped[str] = mapped_column(String(36), index=True)
    project_id: Mapped[str] = mapped_column(String(36), index=True)
    batch_id: Mapped[str] = mapped_column(String(200), index=True)
    source_id: Mapped[str] = mapped_column(String(200), index=True)
    event_id: Mapped[str] = mapped_column(String(200))
    session_id: Mapped[str] = mapped_column(String(200), index=True)
    sequence: Mapped[int] = mapped_column(Integer)
    observed_at: Mapped[datetime] = mapped_column(DateTime(timezone=True), index=True)
    source_employee_key: Mapped[str] = mapped_column(String(200), index=True)
    source_role_key: Mapped[str | None] = mapped_column(String(200), index=True)
    app: Mapped[str] = mapped_column(String(200))
    domain: Mapped[str | None] = mapped_column(String(300))
    category: Mapped[str | None] = mapped_column(String(120), index=True)
    context: Mapped[str | None] = mapped_column(String(300))
    state: Mapped[str] = mapped_column(String(40), index=True)
    metadata_json: Mapped[dict[str, Any]] = mapped_column("metadata", JSON, default=dict)
    payload_hash: Mapped[str] = mapped_column(String(64))
    received_at: Mapped[datetime] = mapped_column(DateTime(timezone=True))


class WorkObservationImportPreviewRow(WorkObservationBase):
    __tablename__ = "work_observation_import_previews"
    __table_args__ = (
        UniqueConstraint(
            "project_id", "batch_id", "payload_hash", name="uq_work_observation_preview_hash"
        ),
        Index("ix_work_observation_preview_project_created", "project_id", "created_at"),
    )

    id: Mapped[str] = mapped_column(String(36), primary_key=True)
    company_id: Mapped[str] = mapped_column(String(36), index=True)
    project_id: Mapped[str] = mapped_column(String(36), index=True)
    batch_id: Mapped[str] = mapped_column(String(200))
    source_id: Mapped[str] = mapped_column(String(200))
    format_version: Mapped[str] = mapped_column(String(40))
    payload_hash: Mapped[str] = mapped_column(String(64))
    event_count: Mapped[int] = mapped_column(Integer)
    payload_json: Mapped[dict[str, Any]] = mapped_column(JSON)
    status: Mapped[str] = mapped_column(String(24), index=True)
    result_json: Mapped[dict[str, Any] | None] = mapped_column(JSON)
    created_at: Mapped[datetime] = mapped_column(DateTime(timezone=True))


class WorkObservationAnalysisRow(WorkObservationBase):
    __tablename__ = "work_observation_analyses"
    __table_args__ = (
        Index("ix_work_observation_analysis_project_created", "project_id", "created_at"),
    )

    id: Mapped[str] = mapped_column(String(36), primary_key=True)
    company_id: Mapped[str] = mapped_column(String(36), index=True)
    project_id: Mapped[str] = mapped_column(String(36), index=True)
    status: Mapped[str] = mapped_column(String(24), index=True)
    filters: Mapped[dict[str, Any]] = mapped_column(JSON, default=dict)
    event_count: Mapped[int] = mapped_column(Integer)
    segment_count: Mapped[int] = mapped_column(Integer)
    employee_count: Mapped[int] = mapped_column(Integer)
    result_json: Mapped[dict[str, Any]] = mapped_column(JSON, default=dict)
    created_at: Mapped[datetime] = mapped_column(DateTime(timezone=True))


class WorkObservationComparisonRow(WorkObservationBase):
    __tablename__ = "work_observation_comparisons"
    __table_args__ = (
        Index("ix_work_observation_comparison_project_created", "project_id", "created_at"),
    )

    id: Mapped[str] = mapped_column(String(36), primary_key=True)
    company_id: Mapped[str] = mapped_column(String(36), index=True)
    project_id: Mapped[str] = mapped_column(String(36), index=True)
    analysis_id: Mapped[str] = mapped_column(String(36), index=True)
    left_employee_keys: Mapped[list[str]] = mapped_column(JSON, default=list)
    right_employee_keys: Mapped[list[str]] = mapped_column(JSON, default=list)
    result_json: Mapped[dict[str, Any]] = mapped_column(JSON, default=dict)
    created_at: Mapped[datetime] = mapped_column(DateTime(timezone=True))


def _canonical_digest(value: Any) -> str:
    encoded = json.dumps(
        json_ready(value), ensure_ascii=False, sort_keys=True, separators=(",", ":")
    )
    return sha256(encoded.encode("utf-8")).hexdigest()


def package_hash(package: WorkObservationPackage) -> str:
    return _canonical_digest(package.model_dump(mode="json"))


def _event_digest(source_id: str, event: WorkObservationEventCreate) -> str:
    payload = event.model_dump(mode="json")
    payload.pop("batch_id", None)
    payload.pop("source_id", None)
    return _canonical_digest({"source_id": source_id, "event": payload})


def _read_datetime(value: datetime | None) -> datetime | None:
    if value is None:
        return None
    if value.tzinfo is None or value.utcoffset() is None:
        return value.replace(tzinfo=UTC)
    return value.astimezone(UTC)


def activity_token(activity: WorkObservationActivity | WorkObservationEventRow) -> str:
    category = activity.category or "UNKNOWN"
    context = activity.context or activity.app
    return f"{category}:{context}"


def _event_from_row(row: WorkObservationEventRow) -> WorkObservationEventCreate:
    return WorkObservationEventCreate(
        event_id=row.event_id,
        session_id=row.session_id,
        sequence=row.sequence,
        observed_at=_read_datetime(row.observed_at),
        source_employee_key=row.source_employee_key,
        source_role_key=row.source_role_key,
        activity=WorkObservationActivity(
            app=row.app, domain=row.domain, category=row.category, context=row.context
        ),
        state=row.state,
        metadata=row.metadata_json,
        source_id=row.source_id,
        batch_id=row.batch_id,
    )


def _new_segment(event: WorkObservationEventCreate) -> dict[str, Any]:
    token = activity_token(event.activity)
    return {
        "segment_id": str(uuid4()),
        "employee_key": event.source_employee_key,
        "role_key": event.source_role_key,
        "session_id": event.session_id,
        "token": token,
        "category": event.activity.category,
        "context": event.activity.context,
        "app": event.activity.app,
        "domain": event.activity.domain,
        "first_observed_at": event.observed_at.isoformat(),
        "last_observed_at": event.observed_at.isoformat(),
        "duration_seconds": 0,
        "event_count": 1,
    }


def build_analysis(rows: list[WorkObservationEventRow], *, gap_seconds: int) -> dict[str, Any]:
    """Build deterministic descriptive paths; never assigns performance or causality."""
    grouped: dict[tuple[str, str, str], list[WorkObservationEventCreate]] = defaultdict(list)
    for row in rows:
        grouped[(row.source_employee_key, row.source_id, row.session_id)].append(
            _event_from_row(row)
        )

    all_segments: list[dict[str, Any]] = []
    paths_by_employee: dict[str, list[list[str]]] = defaultdict(list)
    for (employee_key, _source_id, _session_id), events in grouped.items():
        events.sort(key=lambda item: (item.observed_at, item.sequence, item.event_id))
        current: dict[str, Any] | None = None
        previous: WorkObservationEventCreate | None = None
        path: list[str] = []

        def finish_path(employee: str = employee_key) -> None:
            nonlocal path
            if path:
                paths_by_employee[employee].append(path)
            path = []

        for event in events:
            if event.state != "FOREGROUND":
                if current is not None:
                    all_segments.append(current)
                    current = None
                finish_path()
                previous = None
                continue
            token = activity_token(event.activity)
            delta = (
                (event.observed_at - previous.observed_at).total_seconds()
                if previous is not None
                else None
            )
            if current is None or delta is None or delta > gap_seconds:
                if current is not None:
                    all_segments.append(current)
                if delta is not None and delta > gap_seconds:
                    finish_path()
                current = _new_segment(event)
                path.append(token)
            elif current["token"] == token:
                current["duration_seconds"] += max(0, int(delta))
                current["last_observed_at"] = event.observed_at.isoformat()
                current["event_count"] += 1
            else:
                all_segments.append(current)
                current = _new_segment(event)
                path.append(token)
            previous = event
        if current is not None:
            all_segments.append(current)
        finish_path()

    node_counter: Counter[str] = Counter()
    node_employees: dict[str, set[str]] = defaultdict(set)
    edge_counter: Counter[tuple[str, str]] = Counter()
    edge_employees: dict[tuple[str, str], set[str]] = defaultdict(set)
    pattern_counter: Counter[tuple[str, ...]] = Counter()
    pattern_employees: dict[tuple[str, ...], set[str]] = defaultdict(set)
    for segment in all_segments:
        node_counter[segment["token"]] += 1
        node_employees[segment["token"]].add(segment["employee_key"])
    for employee_key, paths in paths_by_employee.items():
        for path in paths:
            for left, right in pairwise(path):
                edge_counter[(left, right)] += 1
                edge_employees[(left, right)].add(employee_key)
            for length in range(2, 5):
                for index in range(0, max(0, len(path) - length + 1)):
                    pattern = tuple(path[index : index + length])
                    pattern_counter[pattern] += 1
                    pattern_employees[pattern].add(employee_key)

    employee_paths: dict[str, list[dict[str, Any]]] = {}
    for employee_key, paths in paths_by_employee.items():
        counts = Counter(tuple(path) for path in paths if path)
        employee_paths[employee_key] = [
            {"path": list(path), "count": count}
            for path, count in counts.most_common(20)
        ]

    segments = sorted(all_segments, key=lambda item: item["first_observed_at"])
    return {
        "nodes": [
            {"token": token, "count": count, "employee_count": len(node_employees[token])}
            for token, count in node_counter.most_common()
        ],
        "edges": [
            {
                "from": left,
                "to": right,
                "count": count,
                "employee_count": len(edge_employees[(left, right)]),
            }
            for (left, right), count in edge_counter.most_common()
        ],
        "patterns": [
            {
                "path": list(path),
                "length": len(path),
                "count": count,
                "employee_count": len(pattern_employees[path]),
            }
            for path, count in pattern_counter.most_common(200)
        ],
        "employee_paths": employee_paths,
        "segments": segments[:5000],
        "limitations": [
            "活动先后关系不等于业务因果关系。",
            "路径长度和出现次数不等于绩效或最佳流程。",
            "采集缺口、未知应用和未识别上下文可能使路径不完整。",
        ],
    }


def _batch_view(row: WorkObservationBatchRow) -> WorkObservationBatchView:
    return WorkObservationBatchView(
        id=UUID(row.id),
        project_id=UUID(row.project_id),
        source_batch_id=row.batch_id,
        source_id=row.source_id,
        format_version=row.format_version,
        payload_hash=row.payload_hash,
        event_count=row.event_count,
        accepted_count=row.accepted_count,
        duplicate_count=row.duplicate_count,
        status=row.status,
        created_at=_read_datetime(row.created_at),
    )


def _preview_view(row: WorkObservationImportPreviewRow) -> WorkObservationPreviewView:
    details = row.result_json or {}
    return WorkObservationPreviewView(
        id=UUID(row.id),
        project_id=UUID(row.project_id),
        source_batch_id=row.batch_id,
        source_id=row.source_id,
        format_version=row.format_version,
        payload_hash=row.payload_hash,
        event_count=row.event_count,
        status=row.status,
        created_at=_read_datetime(row.created_at),
        sample_events=details.get("sample_events", []),
        employee_keys=details.get("employee_keys", []),
        role_keys=details.get("role_keys", []),
        state_counts=details.get("state_counts", {}),
        first_observed_at=_read_datetime(
            datetime.fromisoformat(details["first_observed_at"])
            if details.get("first_observed_at")
            else None
        ),
        last_observed_at=_read_datetime(
            datetime.fromisoformat(details["last_observed_at"])
            if details.get("last_observed_at")
            else None
        ),
        duplicate_event_count=int(details.get("duplicate_event_count", 0)),
        conflict_event_count=int(details.get("conflict_event_count", 0)),
        identity_status=details.get("identity_status", "SOURCE_KEYS_UNVERIFIED"),
        warnings=details.get("warnings", []),
    )


def _identity_binding_view(
    row: WorkObservationIdentityBindingRow,
) -> WorkObservationIdentityBindingView:
    return WorkObservationIdentityBindingView(
        id=UUID(row.id),
        project_id=UUID(row.project_id),
        source_id=row.source_id,
        source_employee_key=row.source_employee_key,
        formal_entity_id=UUID(row.formal_entity_id),
        formal_role_key=row.formal_role_key,
        status=row.status,
        created_at=_read_datetime(row.created_at),
        updated_at=_read_datetime(row.updated_at),
    )


def _identity_history_view(
    row: WorkObservationIdentityBindingHistoryRow,
) -> WorkObservationIdentityBindingHistoryView:
    return WorkObservationIdentityBindingHistoryView(
        id=UUID(row.id),
        project_id=UUID(row.project_id),
        source_id=row.source_id,
        source_employee_key=row.source_employee_key,
        formal_entity_id=UUID(row.formal_entity_id) if row.formal_entity_id else None,
        formal_role_key=row.formal_role_key,
        operation=row.operation,
        actor_id=row.actor_id,
        reason=row.reason,
        created_at=_read_datetime(row.created_at),
    )


def _virtual_candidate_view(
    row: WorkObservationVirtualCandidateRow,
) -> WorkObservationVirtualCandidateView:
    return WorkObservationVirtualCandidateView(
        id=UUID(row.id),
        project_id=UUID(row.project_id),
        analysis_id=UUID(row.analysis_id),
        candidate_type=row.candidate_type,
        label=row.label,
        role_key=row.role_key,
        properties=row.properties or {},
        evidence_segment_ids=row.evidence_segment_ids or [],
        status=row.status,
        virtual_work_model_id=UUID(row.virtual_work_model_id)
        if row.virtual_work_model_id
        else None,
        virtual_node_id=UUID(row.virtual_node_id) if row.virtual_node_id else None,
        virtual_edge_id=UUID(row.virtual_edge_id) if row.virtual_edge_id else None,
        decision_reason=row.decision_reason,
        created_at=_read_datetime(row.created_at),
        decided_at=_read_datetime(row.decided_at),
    )


class WorkObservationService:
    def __init__(self, session: Session) -> None:
        self.session = session

    def ingest(
        self, project: ProjectRow, package: WorkObservationPackage
    ) -> WorkObservationImportResultView:
        project_id = str(project.id)
        digest = package_hash(package)
        existing_batch = self.session.scalar(
            select(WorkObservationBatchRow).where(
                WorkObservationBatchRow.project_id == project_id,
                WorkObservationBatchRow.batch_id == package.batch_id,
            )
        )
        if existing_batch is not None:
            if (
                existing_batch.source_id == package.source_id
                and existing_batch.payload_hash == digest
            ):
                return self._result(existing_batch, duplicate=True)
            raise DomainError(
                "WORK_OBSERVATION_BATCH_CONFLICT",
                "同一数据包标识已经对应了不同内容。",
                status_code=409,
            )

        event_ids = [event.event_id for event in package.events]
        existing_events = {
            row.event_id: row
            for row in self.session.scalars(
                select(WorkObservationEventRow).where(
                    WorkObservationEventRow.project_id == project_id,
                    WorkObservationEventRow.source_id == package.source_id,
                    WorkObservationEventRow.event_id.in_(event_ids),
                )
            ).all()
        }
        accepted = 0
        duplicate_count = 0
        received = now_utc()
        for event in package.events:
            event_digest = _event_digest(package.source_id, event)
            previous = existing_events.get(event.event_id)
            if previous is not None:
                if previous.payload_hash != event_digest:
                    raise DomainError(
                        "WORK_OBSERVATION_EVENT_CONFLICT",
                        f"事件 {event.event_id} 已存在但内容不同，未导入本批数据。",
                        status_code=409,
                    )
                duplicate_count += 1
                continue
            self.session.add(
                WorkObservationEventRow(
                    id=str(uuid4()),
                    company_id=project.company_id,
                    project_id=project_id,
                    batch_id=package.batch_id,
                    source_id=package.source_id,
                    event_id=event.event_id,
                    session_id=event.session_id,
                    sequence=event.sequence,
                    observed_at=event.observed_at,
                    source_employee_key=event.source_employee_key,
                    source_role_key=event.source_role_key,
                    app=event.activity.app,
                    domain=event.activity.domain,
                    category=event.activity.category,
                    context=event.activity.context,
                    state=event.state,
                    metadata_json=event.metadata,
                    payload_hash=event_digest,
                    received_at=received,
                )
            )
            accepted += 1

        batch = WorkObservationBatchRow(
            id=str(uuid4()),
            company_id=project.company_id,
            project_id=project_id,
            batch_id=package.batch_id,
            source_id=package.source_id,
            format_version=package.format_version,
            payload_hash=digest,
            event_count=len(package.events),
            accepted_count=accepted,
            duplicate_count=duplicate_count,
            status="IMPORTED" if accepted else "DUPLICATE",
            created_at=received,
        )
        self.session.add(batch)
        self.session.flush()
        return self._result(batch, duplicate=False)

    def preview(
        self, project: ProjectRow, package: WorkObservationPackage
    ) -> WorkObservationPreviewView:
        project_id = str(project.id)
        digest = package_hash(package)
        existing = self.session.scalar(
            select(WorkObservationImportPreviewRow).where(
                WorkObservationImportPreviewRow.project_id == project_id,
                WorkObservationImportPreviewRow.batch_id == package.batch_id,
                WorkObservationImportPreviewRow.payload_hash == digest,
            )
        )
        if existing is not None:
            return _preview_view(existing)
        event_ids = [event.event_id for event in package.events]
        existing_events = {
            row.event_id: row
            for row in self.session.scalars(
                select(WorkObservationEventRow).where(
                    WorkObservationEventRow.project_id == project_id,
                    WorkObservationEventRow.source_id == package.source_id,
                    WorkObservationEventRow.event_id.in_(event_ids),
                )
            ).all()
        }
        duplicate_count = 0
        conflict_count = 0
        for event in package.events:
            previous = existing_events.get(event.event_id)
            if previous is None:
                continue
            if previous.payload_hash == _event_digest(package.source_id, event):
                duplicate_count += 1
            else:
                conflict_count += 1
        state_counts = Counter(event.state for event in package.events)
        employee_keys = sorted({event.source_employee_key for event in package.events})
        role_keys = sorted(
            {event.source_role_key for event in package.events if event.source_role_key}
        )
        observed_times = [event.observed_at for event in package.events]
        bindings = {
            row.source_employee_key: row
            for row in self.session.scalars(
                select(WorkObservationIdentityBindingRow).where(
                    WorkObservationIdentityBindingRow.project_id == project_id,
                    WorkObservationIdentityBindingRow.source_id == package.source_id,
                    WorkObservationIdentityBindingRow.status == "ACTIVE",
                )
            ).all()
        }
        unbound_employees = [employee for employee in employee_keys if employee not in bindings]
        identity_status = (
            "RESOLVED"
            if employee_keys and not unbound_employees
            else "PARTIAL"
            if bindings
            else "SOURCE_KEYS_UNVERIFIED"
        )
        warnings: list[str] = []
        if unbound_employees:
            warnings.append(
                f"{len(unbound_employees)} 个采集员工标识尚未与正式企业人员/岗位绑定，"
                "只能作为来源标识。"
            )
        if any(event.activity.category is None for event in package.events):
            warnings.append("部分事件没有活动分类，分析时会归入 UNKNOWN。")
        if duplicate_count:
            warnings.append(f"已有 {duplicate_count} 条事件会按重复数据跳过。")
        if conflict_count:
            warnings.append(f"发现 {conflict_count} 条同标识但内容不同的事件，确认时会拒绝该批次。")
        details = {
            "sample_events": [
                {
                    "event_id": event.event_id,
                    "observed_at": event.observed_at.isoformat(),
                    "source_employee_key": event.source_employee_key,
                    "source_role_key": event.source_role_key,
                    "activity": event.activity.model_dump(mode="json"),
                    "state": event.state,
                }
                for event in package.events[:5]
            ],
            "employee_keys": employee_keys,
            "role_keys": role_keys,
            "state_counts": dict(state_counts),
            "first_observed_at": min(observed_times).isoformat() if observed_times else None,
            "last_observed_at": max(observed_times).isoformat() if observed_times else None,
            "duplicate_event_count": duplicate_count,
            "conflict_event_count": conflict_count,
            "identity_status": identity_status,
            "warnings": warnings,
        }
        row = WorkObservationImportPreviewRow(
            id=str(uuid4()),
            company_id=project.company_id,
            project_id=project_id,
            batch_id=package.batch_id,
            source_id=package.source_id,
            format_version=package.format_version,
            payload_hash=digest,
            event_count=len(package.events),
            payload_json=package.model_dump(mode="json"),
            status="READY_WITH_WARNINGS" if warnings else "READY",
            result_json=details,
            created_at=now_utc(),
        )
        self.session.add(row)
        self.session.flush()
        return _preview_view(row)

    def confirm(
        self, project: ProjectRow, payload: WorkObservationImportConfirm
    ) -> WorkObservationImportResultView:
        row = self.session.scalar(
            select(WorkObservationImportPreviewRow).where(
                WorkObservationImportPreviewRow.id == str(payload.preview_id),
                WorkObservationImportPreviewRow.project_id == str(project.id),
            )
        )
        if row is None:
            raise DomainError(
                "WORK_OBSERVATION_PREVIEW_NOT_FOUND", "导入预览不存在。", status_code=404
            )
        if row.payload_hash != payload.payload_hash:
            raise DomainError(
                "WORK_OBSERVATION_PREVIEW_HASH_MISMATCH",
                "预览内容已变化，请重新预览。",
                status_code=409,
            )
        if row.status == "CONFIRMED" and row.result_json:
            return WorkObservationImportResultView.model_validate(row.result_json)
        package = WorkObservationPackage.model_validate(row.payload_json)
        result = self.ingest(project, package)
        row.status = "CONFIRMED"
        row.result_json = result.model_dump(mode="json")
        self.session.flush()
        return result

    def bind_identity(
        self,
        project: ProjectRow,
        payload: WorkObservationIdentityBindingCreate,
        *,
        actor_id: str = "local-owner",
    ) -> WorkObservationIdentityBindingView:
        project_id = str(project.id)
        existing = self.session.scalar(
            select(WorkObservationIdentityBindingRow).where(
                WorkObservationIdentityBindingRow.project_id == project_id,
                WorkObservationIdentityBindingRow.source_id == payload.source_id,
                WorkObservationIdentityBindingRow.source_employee_key
                == payload.source_employee_key,
            )
        )
        now = now_utc()
        if existing is not None:
            if (
                existing.formal_entity_id == str(payload.formal_entity_id)
                and existing.formal_role_key == payload.formal_role_key
                and existing.status == "ACTIVE"
            ):
                return _identity_binding_view(existing)
            if existing.status == "RETIRED":
                existing.formal_entity_id = str(payload.formal_entity_id)
                existing.formal_role_key = payload.formal_role_key
                existing.status = "ACTIVE"
                existing.updated_at = now
                self._write_identity_history(
                    project,
                    payload.source_id,
                    payload.source_employee_key,
                    formal_entity_id=payload.formal_entity_id,
                    formal_role_key=payload.formal_role_key,
                    operation="REACTIVATED",
                    actor_id=actor_id,
                    reason="重新绑定采集来源员工标识。",
                )
                self.session.flush()
                return _identity_binding_view(existing)
            raise DomainError(
                "WORK_OBSERVATION_IDENTITY_BINDING_CONFLICT",
                "同一来源员工标识已经绑定到不同的正式对象。请先明确解除旧绑定。",
                status_code=409,
            )
        row = WorkObservationIdentityBindingRow(
            id=str(uuid4()),
            company_id=project.company_id,
            project_id=project_id,
            source_id=payload.source_id,
            source_employee_key=payload.source_employee_key,
            formal_entity_id=str(payload.formal_entity_id),
            formal_role_key=payload.formal_role_key,
            status="ACTIVE",
            created_at=now,
            updated_at=now,
        )
        self.session.add(row)
        self._write_identity_history(
            project,
            payload.source_id,
            payload.source_employee_key,
            formal_entity_id=payload.formal_entity_id,
            formal_role_key=payload.formal_role_key,
            operation="CREATED",
            actor_id=actor_id,
            reason="创建采集来源员工标识绑定。",
        )
        self.session.flush()
        return _identity_binding_view(row)

    def retire_identity(
        self,
        project: ProjectRow,
        payload: WorkObservationIdentityBindingRetire,
        *,
        actor_id: str = "local-owner",
    ) -> WorkObservationIdentityBindingView:
        row = self.session.scalar(
            select(WorkObservationIdentityBindingRow).where(
                WorkObservationIdentityBindingRow.project_id == str(project.id),
                WorkObservationIdentityBindingRow.source_id == payload.source_id,
                WorkObservationIdentityBindingRow.source_employee_key
                == payload.source_employee_key,
            )
        )
        if row is None or row.status != "ACTIVE":
            raise DomainError(
                "WORK_OBSERVATION_IDENTITY_BINDING_NOT_FOUND",
                "当前没有可解除的活动绑定。",
                status_code=404,
            )
        now = now_utc()
        row.status = "RETIRED"
        row.updated_at = now
        self._write_identity_history(
            project,
            row.source_id,
            row.source_employee_key,
            formal_entity_id=UUID(row.formal_entity_id),
            formal_role_key=row.formal_role_key,
            operation="RETIRED",
            actor_id=actor_id,
            reason=payload.reason,
        )
        self.session.flush()
        return _identity_binding_view(row)

    def list_identity_history(
        self,
        project_id: UUID,
        *,
        source_id: str | None = None,
        source_employee_key: str | None = None,
        offset: int = 0,
        limit: int = 100,
    ) -> tuple[list[WorkObservationIdentityBindingHistoryView], int]:
        conditions = [WorkObservationIdentityBindingHistoryRow.project_id == str(project_id)]
        if source_id:
            conditions.append(WorkObservationIdentityBindingHistoryRow.source_id == source_id)
        if source_employee_key:
            conditions.append(
                WorkObservationIdentityBindingHistoryRow.source_employee_key
                == source_employee_key
            )
        total = int(
            self.session.scalar(
                select(func.count())
                .select_from(WorkObservationIdentityBindingHistoryRow)
                .where(*conditions)
            )
            or 0
        )
        rows = self.session.scalars(
            select(WorkObservationIdentityBindingHistoryRow)
            .where(*conditions)
            .order_by(WorkObservationIdentityBindingHistoryRow.created_at.desc())
            .offset(offset)
            .limit(limit)
        ).all()
        return [_identity_history_view(row) for row in rows], total

    def _write_identity_history(
        self,
        project: ProjectRow,
        source_id: str,
        source_employee_key: str,
        *,
        formal_entity_id: UUID | None,
        formal_role_key: str | None,
        operation: str,
        actor_id: str,
        reason: str | None,
    ) -> None:
        self.session.add(
            WorkObservationIdentityBindingHistoryRow(
                id=str(uuid4()),
                company_id=str(project.company_id),
                project_id=str(project.id),
                source_id=source_id,
                source_employee_key=source_employee_key,
                formal_entity_id=str(formal_entity_id) if formal_entity_id else None,
                formal_role_key=formal_role_key,
                operation=operation,
                actor_id=(actor_id or "local-owner")[:128],
                reason=reason,
                created_at=now_utc(),
            )
        )

    def propose_virtual_candidates(
        self,
        project: ProjectRow,
        analysis_id: UUID,
        payload: WorkObservationVirtualCandidateCreate,
    ) -> list[WorkObservationVirtualCandidateView]:
        analysis = self.get_analysis(UUID(project.id), analysis_id)
        nodes = analysis.result.get("nodes", [])
        segments = analysis.result.get("segments", [])
        segment_by_token: dict[str, list[str]] = defaultdict(list)
        for segment in segments:
            if payload.role_key and segment.get("role_key") != payload.role_key:
                continue
            token = str(segment.get("token") or "")
            if token:
                segment_by_token[token].append(str(segment.get("segment_id")))
        candidates: list[WorkObservationVirtualCandidateView] = []
        for node in nodes:
            token = str(node.get("token") or "")
            count = int(node.get("count", 0))
            if not token or count < payload.min_count:
                continue
            evidence_ids = segment_by_token.get(token, [])[:50]
            role_key = payload.role_key
            label = token
            existing = self.session.scalar(
                select(WorkObservationVirtualCandidateRow).where(
                    WorkObservationVirtualCandidateRow.project_id == str(project.id),
                    WorkObservationVirtualCandidateRow.analysis_id == str(analysis_id),
                    WorkObservationVirtualCandidateRow.label == label,
                    WorkObservationVirtualCandidateRow.status == "PROPOSED",
                )
            )
            if existing is not None:
                candidates.append(_virtual_candidate_view(existing))
                continue
            row = WorkObservationVirtualCandidateRow(
                id=str(uuid4()),
                company_id=str(project.company_id),
                project_id=str(project.id),
                analysis_id=str(analysis_id),
                candidate_type="ACTIVITY",
                label=label,
                role_key=role_key,
                properties={
                    "activity_token": token,
                    "observed_segment_count": count,
                    "employee_count": int(node.get("employee_count", 0)),
                    "trust": "OBSERVED_WORK_PATTERN_CANDIDATE",
                },
                evidence_segment_ids=evidence_ids,
                status="PROPOSED",
                created_at=now_utc(),
            )
            self.session.add(row)
            self.session.flush()
            candidates.append(_virtual_candidate_view(row))
        return candidates

    def list_virtual_candidates(
        self,
        project_id: UUID,
        *,
        status: str | None = None,
        offset: int = 0,
        limit: int = 100,
    ) -> tuple[list[WorkObservationVirtualCandidateView], int]:
        conditions = [WorkObservationVirtualCandidateRow.project_id == str(project_id)]
        if status:
            conditions.append(WorkObservationVirtualCandidateRow.status == status)
        total = int(
            self.session.scalar(
                select(func.count())
                .select_from(WorkObservationVirtualCandidateRow)
                .where(*conditions)
            )
            or 0
        )
        rows = self.session.scalars(
            select(WorkObservationVirtualCandidateRow)
            .where(*conditions)
            .order_by(WorkObservationVirtualCandidateRow.created_at.desc())
            .offset(offset)
            .limit(limit)
        ).all()
        return [_virtual_candidate_view(row) for row in rows], total

    def get_virtual_candidate(
        self, project_id: UUID, candidate_id: UUID
    ) -> WorkObservationVirtualCandidateRow:
        row = self.session.scalar(
            select(WorkObservationVirtualCandidateRow).where(
                WorkObservationVirtualCandidateRow.id == str(candidate_id),
                WorkObservationVirtualCandidateRow.project_id == str(project_id),
            )
        )
        if row is None:
            raise DomainError(
                "WORK_OBSERVATION_VIRTUAL_CANDIDATE_NOT_FOUND",
                "工作观察虚模候选不存在。",
                status_code=404,
            )
        return row

    def list_identity_bindings(
        self, project_id: UUID, *, offset: int = 0, limit: int = 100
    ) -> tuple[list[WorkObservationIdentityBindingView], int]:
        condition = WorkObservationIdentityBindingRow.project_id == str(project_id)
        total = int(
            self.session.scalar(
                select(func.count())
                .select_from(WorkObservationIdentityBindingRow)
                .where(condition, WorkObservationIdentityBindingRow.status == "ACTIVE")
            )
            or 0
        )
        rows = self.session.scalars(
            select(WorkObservationIdentityBindingRow)
            .where(condition, WorkObservationIdentityBindingRow.status == "ACTIVE")
            .order_by(
                WorkObservationIdentityBindingRow.source_id,
                WorkObservationIdentityBindingRow.source_employee_key,
            )
            .offset(offset)
            .limit(limit)
        ).all()
        return [_identity_binding_view(row) for row in rows], total

    def list_batches(
        self, project_id: UUID, *, offset: int = 0, limit: int = 100
    ) -> tuple[list[WorkObservationBatchView], int]:
        condition = WorkObservationBatchRow.project_id == str(project_id)
        total = int(
            self.session.scalar(
                select(func.count()).select_from(WorkObservationBatchRow).where(condition)
            )
            or 0
        )
        rows = self.session.scalars(
            select(WorkObservationBatchRow)
            .where(condition)
            .order_by(WorkObservationBatchRow.created_at.desc())
            .offset(offset)
            .limit(limit)
        ).all()
        return [_batch_view(row) for row in rows], total

    def coverage(self, project_id: UUID) -> WorkObservationCoverageView:
        project_key = str(project_id)
        condition = WorkObservationEventRow.project_id == project_key
        event_count = int(
            self.session.scalar(
                select(func.count()).select_from(WorkObservationEventRow).where(condition)
            )
            or 0
        )
        foreground = int(
            self.session.scalar(
                select(func.count())
                .select_from(WorkObservationEventRow)
                .where(condition, WorkObservationEventRow.state == "FOREGROUND")
            )
            or 0
        )
        employee_count = int(
            self.session.scalar(
                select(func.count(func.distinct(WorkObservationEventRow.source_employee_key)))
                .where(condition)
            )
            or 0
        )
        session_count = int(
            self.session.scalar(
                select(func.count(func.distinct(WorkObservationEventRow.session_id))).where(
                    condition
                )
            )
            or 0
        )
        source_count = int(
            self.session.scalar(
                select(func.count(func.distinct(WorkObservationEventRow.source_id))).where(condition)
            )
            or 0
        )
        first = self.session.scalar(
            select(func.min(WorkObservationEventRow.observed_at)).where(condition)
        )
        last = self.session.scalar(
            select(func.max(WorkObservationEventRow.observed_at)).where(condition)
        )
        batch_count = int(
            self.session.scalar(
                select(func.count())
                .select_from(WorkObservationBatchRow)
                .where(WorkObservationBatchRow.project_id == project_key)
            )
            or 0
        )
        return WorkObservationCoverageView(
            project_id=project_id,
            event_count=event_count,
            foreground_event_count=foreground,
            employee_count=employee_count,
            session_count=session_count,
            source_count=source_count,
            first_observed_at=_read_datetime(first),
            last_observed_at=_read_datetime(last),
            batch_count=batch_count,
        )

    def analyze(
        self, project: ProjectRow, payload: WorkObservationAnalysisCreate
    ) -> WorkObservationAnalysisView:
        conditions = [WorkObservationEventRow.project_id == str(project.id)]
        if payload.employee_keys:
            conditions.append(
                WorkObservationEventRow.source_employee_key.in_(payload.employee_keys)
            )
        if payload.role_keys:
            conditions.append(WorkObservationEventRow.source_role_key.in_(payload.role_keys))
        if payload.source_ids:
            conditions.append(WorkObservationEventRow.source_id.in_(payload.source_ids))
        if payload.observed_from:
            conditions.append(WorkObservationEventRow.observed_at >= payload.observed_from)
        if payload.observed_to:
            conditions.append(WorkObservationEventRow.observed_at <= payload.observed_to)
        rows = self.session.scalars(
            select(WorkObservationEventRow)
            .where(*conditions)
            .order_by(
                WorkObservationEventRow.source_employee_key,
                WorkObservationEventRow.source_id,
                WorkObservationEventRow.session_id,
                WorkObservationEventRow.observed_at,
                WorkObservationEventRow.sequence,
            )
        ).all()
        result = build_analysis(rows, gap_seconds=payload.gap_seconds)
        observed_source_keys = {(row.source_id, row.source_employee_key) for row in rows}
        bindings = self.session.scalars(
            select(WorkObservationIdentityBindingRow).where(
                WorkObservationIdentityBindingRow.project_id == str(project.id),
                WorkObservationIdentityBindingRow.status == "ACTIVE",
            )
        ).all()
        binding_by_key = {
            (row.source_id, row.source_employee_key): row for row in bindings
        }
        result["identity_bindings"] = [
            {
                "source_id": source_id,
                "source_employee_key": employee_key,
                "formal_entity_id": binding.formal_entity_id,
                "formal_role_key": binding.formal_role_key,
                "status": binding.status,
            }
            for source_id, employee_key in sorted(observed_source_keys)
            if (binding := binding_by_key.get((source_id, employee_key))) is not None
        ]
        result["unbound_source_keys"] = [
            {"source_id": source_id, "source_employee_key": employee_key}
            for source_id, employee_key in sorted(observed_source_keys)
            if (source_id, employee_key) not in binding_by_key
        ]
        result["identity_status"] = (
            "RESOLVED"
            if observed_source_keys and not result["unbound_source_keys"]
            else "PARTIAL"
            if result["identity_bindings"]
            else "SOURCE_KEYS_UNVERIFIED"
        )
        employee_count = len({row.source_employee_key for row in rows})
        analysis = WorkObservationAnalysisRow(
            id=str(uuid4()),
            company_id=project.company_id,
            project_id=str(project.id),
            status="COMPLETED",
            filters=payload.model_dump(mode="json"),
            event_count=len(rows),
            segment_count=len(result["segments"]),
            employee_count=employee_count,
            result_json=result,
            created_at=now_utc(),
        )
        self.session.add(analysis)
        self.session.flush()
        return self._analysis_view(analysis)

    def list_analyses(
        self, project_id: UUID, *, offset: int = 0, limit: int = 50
    ) -> tuple[list[WorkObservationAnalysisView], int]:
        condition = WorkObservationAnalysisRow.project_id == str(project_id)
        total = int(
            self.session.scalar(
                select(func.count()).select_from(WorkObservationAnalysisRow).where(condition)
            )
            or 0
        )
        rows = self.session.scalars(
            select(WorkObservationAnalysisRow)
            .where(condition)
            .order_by(WorkObservationAnalysisRow.created_at.desc())
            .offset(offset)
            .limit(limit)
        ).all()
        return [self._analysis_view(row) for row in rows], total

    def get_analysis(self, project_id: UUID, analysis_id: UUID) -> WorkObservationAnalysisView:
        row = self.session.scalar(
            select(WorkObservationAnalysisRow).where(
                WorkObservationAnalysisRow.id == str(analysis_id),
                WorkObservationAnalysisRow.project_id == str(project_id),
            )
        )
        if row is None:
            raise DomainError(
                "WORK_OBSERVATION_ANALYSIS_NOT_FOUND", "工作观察分析不存在。", status_code=404
            )
        return self._analysis_view(row)

    def latest_analysis(self, project_id: UUID) -> WorkObservationAnalysisView | None:
        row = self.session.scalar(
            select(WorkObservationAnalysisRow)
            .where(WorkObservationAnalysisRow.project_id == str(project_id))
            .order_by(WorkObservationAnalysisRow.created_at.desc())
            .limit(1)
        )
        return self._analysis_view(row) if row else None

    def get_segment(
        self, project_id: UUID, analysis_id: UUID, segment_id: str
    ) -> dict[str, Any]:
        analysis = self.get_analysis(project_id, analysis_id)
        for segment in analysis.result.get("segments", []):
            if segment.get("segment_id") == segment_id:
                return segment
        raise DomainError(
            "WORK_OBSERVATION_SEGMENT_NOT_FOUND", "工作观察片段不存在。", status_code=404
        )

    def compare(
        self, project: ProjectRow, payload: WorkObservationComparisonCreate
    ) -> WorkObservationComparisonView:
        analysis = self.get_analysis(UUID(project.id), payload.analysis_id)
        paths = analysis.result.get("employee_paths", {})
        left = self._aggregate_paths(paths, payload.left_employee_keys)
        right = self._aggregate_paths(paths, payload.right_employee_keys)
        left_keys = set(left)
        right_keys = set(right)
        result = {
            "left_paths": [
                {"path": list(path), "count": count} for path, count in left.items()
            ],
            "right_paths": [
                {"path": list(path), "count": count} for path, count in right.items()
            ],
            "only_left": [list(path) for path in left_keys - right_keys],
            "only_right": [list(path) for path in right_keys - left_keys],
            "shared": [list(path) for path in left_keys & right_keys],
            "limitations": [
                "差异仅表示观察到的路径频次差异，不表示绩效高低或因果关系。",
                "比较结果受采集覆盖率、岗位实际分工和筛选范围影响。",
            ],
        }
        row = WorkObservationComparisonRow(
            id=str(uuid4()),
            company_id=project.company_id,
            project_id=str(project.id),
            analysis_id=str(payload.analysis_id),
            left_employee_keys=payload.left_employee_keys,
            right_employee_keys=payload.right_employee_keys,
            result_json=result,
            created_at=now_utc(),
        )
        self.session.add(row)
        self.session.flush()
        return WorkObservationComparisonView(
            id=UUID(row.id),
            project_id=UUID(row.project_id),
            analysis_id=UUID(row.analysis_id),
            left_employee_keys=row.left_employee_keys,
            right_employee_keys=row.right_employee_keys,
            result=row.result_json,
            created_at=_read_datetime(row.created_at),
        )

    @staticmethod
    def _aggregate_paths(
        paths: dict[str, Any], employees: list[str]
    ) -> dict[tuple[str, ...], int]:
        counter: Counter[tuple[str, ...]] = Counter()
        for employee in employees:
            for item in paths.get(employee, []):
                counter[tuple(item.get("path", []))] += int(item.get("count", 0))
        return dict(counter.most_common(100))

    @staticmethod
    def _result(
        row: WorkObservationBatchRow, *, duplicate: bool
    ) -> WorkObservationImportResultView:
        return WorkObservationImportResultView(
            batch_id=UUID(row.id),
            source_batch_id=row.batch_id,
            status=row.status,
            accepted_count=row.accepted_count,
            duplicate_count=row.duplicate_count,
            event_count=row.event_count,
            duplicate=duplicate,
        )

    @staticmethod
    def _analysis_view(row: WorkObservationAnalysisRow) -> WorkObservationAnalysisView:
        return WorkObservationAnalysisView(
            id=UUID(row.id),
            project_id=UUID(row.project_id),
            status=row.status,
            filters=row.filters,
            event_count=row.event_count,
            segment_count=row.segment_count,
            employee_count=row.employee_count,
            result=row.result_json,
            created_at=_read_datetime(row.created_at),
        )
