from __future__ import annotations

import json
import re
from datetime import UTC, datetime
from enum import StrEnum
from hashlib import sha256
from typing import Any, Literal
from uuid import UUID, uuid4

from pydantic import BaseModel, ConfigDict, Field, field_validator
from sqlalchemy import (
    JSON,
    Boolean,
    DateTime,
    Integer,
    String,
    Text,
    UniqueConstraint,
    create_engine,
    event,
    func,
    inspect,
    select,
)
from sqlalchemy.engine import Engine
from sqlalchemy.orm import DeclarativeBase, Mapped, Session, mapped_column, sessionmaker

from enterprise_insight_backend.errors import DomainError
from enterprise_insight_backend.models import (
    AgentReadSetOutboxRow,
    AgentRunRow,
    AgentStepOutboxRow,
    AgentStepRow,
    ProjectRow,
)
from enterprise_insight_backend.potential import (
    EvidenceStatus,
    PotentialCandidateDraft,
    PotentialRecordView,
)
from enterprise_insight_backend.schemas import AgentClaimKind


class ControlBase(DeclarativeBase):
    pass


class ControlSchemaRow(ControlBase):
    __tablename__ = "control_schema"

    id: Mapped[int] = mapped_column(Integer, primary_key=True, default=1)
    version: Mapped[int] = mapped_column(Integer, nullable=False)
    query_manifest_backfill_completed: Mapped[bool] = mapped_column(
        Boolean, nullable=False, default=False
    )
    step_index_backfill_completed: Mapped[bool] = mapped_column(
        Boolean, nullable=False, default=False
    )
    updated_at: Mapped[datetime] = mapped_column(DateTime(timezone=True), nullable=False)


class ControlTaskRouteRow(ControlBase):
    """Rebuildable control-plane index; the source AgentStep stays authoritative."""

    __tablename__ = "control_task_routes"

    run_id: Mapped[str] = mapped_column(String(36), primary_key=True)
    source_step_id: Mapped[str] = mapped_column(String(36), unique=True, nullable=False)
    company_id: Mapped[str] = mapped_column(String(36), index=True, nullable=False)
    project_id: Mapped[str] = mapped_column(String(36), index=True, nullable=False)
    thread_id: Mapped[str] = mapped_column(String(36), index=True, nullable=False)
    agent_kind: Mapped[str] = mapped_column(String(32), nullable=False)
    task_kind: Mapped[str] = mapped_column(String(48), nullable=False)
    route: Mapped[str] = mapped_column(String(32), index=True, nullable=False)
    rule_version: Mapped[str] = mapped_column(String(100), nullable=False)
    route_payload: Mapped[dict[str, Any]] = mapped_column(JSON, nullable=False)
    payload_sha256: Mapped[str] = mapped_column(String(64), nullable=False)
    recorded_at: Mapped[datetime] = mapped_column(DateTime(timezone=True), nullable=False)


class ControlQueryManifestRow(ControlBase):
    """Immutable, content-free record of one actual Agent context read set."""

    __tablename__ = "control_query_manifests"
    __table_args__ = (UniqueConstraint("run_id", "ordinal", name="uq_control_query_run_ordinal"),)

    id: Mapped[str] = mapped_column(String(64), primary_key=True)
    run_id: Mapped[str] = mapped_column(String(36), index=True, nullable=False)
    ordinal: Mapped[int] = mapped_column(Integer, nullable=False)
    company_id: Mapped[str] = mapped_column(String(36), index=True, nullable=False)
    project_id: Mapped[str] = mapped_column(String(36), index=True, nullable=False)
    query_snapshot_id: Mapped[str | None] = mapped_column(String(36))
    route: Mapped[str | None] = mapped_column(String(32))
    selected_stores: Mapped[list[str]] = mapped_column(JSON, nullable=False)
    management_context_access: Mapped[dict[str, Any]] = mapped_column(JSON, nullable=False)
    coverage: Mapped[dict[str, Any]] = mapped_column(JSON, nullable=False)
    read_reference_uris: Mapped[list[str]] = mapped_column(JSON, nullable=False)
    model_visible_reference_uris: Mapped[list[str]] = mapped_column(JSON, nullable=False)
    model_context_shared: Mapped[bool] = mapped_column(Boolean, nullable=False)
    payload_sha256: Mapped[str] = mapped_column(String(64), nullable=False)
    recorded_at: Mapped[datetime] = mapped_column(DateTime(timezone=True), nullable=False)


class ControlEvidenceReadRow(ControlBase):
    """One immutable URI-level read record; raw source contents are never copied."""

    __tablename__ = "control_evidence_reads"

    id: Mapped[str] = mapped_column(String(64), primary_key=True)
    query_manifest_id: Mapped[str] = mapped_column(String(64), index=True, nullable=False)
    run_id: Mapped[str] = mapped_column(String(36), index=True, nullable=False)
    company_id: Mapped[str] = mapped_column(String(36), index=True, nullable=False)
    project_id: Mapped[str] = mapped_column(String(36), index=True, nullable=False)
    ordinal: Mapped[int] = mapped_column(Integer, nullable=False)
    source_store: Mapped[str] = mapped_column(String(40), nullable=False)
    reference_uri: Mapped[str] = mapped_column(Text, nullable=False)
    model_visible: Mapped[bool] = mapped_column(Boolean, nullable=False)
    query_manifest_sha256: Mapped[str] = mapped_column(String(64), nullable=False)
    recorded_at: Mapped[datetime] = mapped_column(DateTime(timezone=True), nullable=False)


class ControlClaimLedgerRow(ControlBase):
    """Immutable index of claim/source bindings emitted by completed Agent runs."""

    __tablename__ = "control_claim_ledger"

    claim_id: Mapped[str] = mapped_column(String(36), primary_key=True)
    company_id: Mapped[str] = mapped_column(String(36), index=True, nullable=False)
    project_id: Mapped[str] = mapped_column(String(36), index=True, nullable=False)
    run_id: Mapped[str] = mapped_column(String(36), index=True, nullable=False)
    source_step_id: Mapped[str] = mapped_column(String(36), nullable=False)
    ordinal: Mapped[int] = mapped_column(Integer, nullable=False)
    statement: Mapped[str] = mapped_column(Text, nullable=False)
    kind: Mapped[str] = mapped_column(String(24), nullable=False)
    supporting_refs: Mapped[list[str]] = mapped_column(JSON, nullable=False)
    counterevidence_refs: Mapped[list[str]] = mapped_column(JSON, nullable=False)
    submitted_supporting_refs: Mapped[list[str]] = mapped_column(JSON, nullable=False)
    submitted_counterevidence_refs: Mapped[list[str]] = mapped_column(JSON, nullable=False)
    scope: Mapped[str | None] = mapped_column(Text)
    unknowns: Mapped[list[str]] = mapped_column(JSON, nullable=False)
    validation_status: Mapped[str] = mapped_column(String(64), index=True, nullable=False)
    rejected_reference_count: Mapped[int] = mapped_column(Integer, nullable=False)
    query_manifest_sha256: Mapped[str] = mapped_column(String(64), nullable=False)
    payload_sha256: Mapped[str] = mapped_column(String(64), nullable=False)
    created_at: Mapped[datetime] = mapped_column(DateTime(timezone=True), nullable=False)


class ControlAuditIndexRow(ControlBase):
    __tablename__ = "control_audit_index"

    event_id: Mapped[str] = mapped_column(String(100), primary_key=True)
    event_kind: Mapped[str] = mapped_column(String(64), index=True, nullable=False)
    company_id: Mapped[str] = mapped_column(String(36), index=True, nullable=False)
    project_id: Mapped[str] = mapped_column(String(36), index=True, nullable=False)
    run_id: Mapped[str] = mapped_column(String(36), index=True, nullable=False)
    source_store: Mapped[str] = mapped_column(String(32), nullable=False)
    source_record_id: Mapped[str] = mapped_column(String(100), nullable=False)
    payload_sha256: Mapped[str] = mapped_column(String(64), nullable=False)
    payload: Mapped[dict[str, Any]] = mapped_column(JSON, nullable=False)
    occurred_at: Mapped[datetime] = mapped_column(DateTime(timezone=True), nullable=False)
    indexed_at: Mapped[datetime] = mapped_column(DateTime(timezone=True), nullable=False)


class ControlOperationReceiptRow(ControlBase):
    """Control-side receipt state for future cross-store operations."""

    __tablename__ = "control_operation_receipts"

    operation_id: Mapped[str] = mapped_column(String(100), primary_key=True)
    target_store: Mapped[str] = mapped_column(String(32), nullable=False)
    operation_kind: Mapped[str] = mapped_column(String(80), nullable=False)
    target_record_id: Mapped[str | None] = mapped_column(String(100))
    request_sha256: Mapped[str] = mapped_column(String(64), nullable=False)
    status: Mapped[str] = mapped_column(String(32), index=True, nullable=False)
    result: Mapped[dict[str, Any] | None] = mapped_column(JSON)
    created_at: Mapped[datetime] = mapped_column(DateTime(timezone=True), nullable=False)
    updated_at: Mapped[datetime] = mapped_column(DateTime(timezone=True), nullable=False)


class PotentialCandidateStatus(StrEnum):
    PROPOSED = "PROPOSED"
    ACCEPTING = "ACCEPTING"
    ACCEPTED = "ACCEPTED"
    REJECTED = "REJECTED"
    STALE = "STALE"


ControlPotentialCandidateStatus = PotentialCandidateStatus


class ClaimLedgerValidationStatus(StrEnum):
    CONTEXT_NOT_SHARED = "CONTEXT_NOT_SHARED"
    UNRESOLVED_REFERENCES_REMOVED = "UNRESOLVED_REFERENCES_REMOVED"
    FACT_WITHOUT_SOURCE = "FACT_WITHOUT_SOURCE"
    NO_SUPPORTING_SOURCE = "NO_SUPPORTING_SOURCE"
    REFERENCES_RESOLVED_NOT_SEMANTICALLY_VERIFIED = (
        "REFERENCES_RESOLVED_NOT_SEMANTICALLY_VERIFIED"
    )


class ControlPotentialCandidateRow(ControlBase):
    __tablename__ = "control_potential_candidates"

    id: Mapped[str] = mapped_column(String(36), primary_key=True)
    company_id: Mapped[str] = mapped_column(String(36), index=True, nullable=False)
    project_id: Mapped[str] = mapped_column(String(36), index=True, nullable=False)
    source_run_id: Mapped[str] = mapped_column(String(36), index=True, nullable=False)
    source_step_id: Mapped[str] = mapped_column(String(36), unique=True, nullable=False)
    version: Mapped[int] = mapped_column(Integer, nullable=False)
    payload: Mapped[dict[str, Any]] = mapped_column(JSON, nullable=False)
    payload_sha256: Mapped[str] = mapped_column(String(64), nullable=False)
    status: Mapped[str] = mapped_column(String(24), index=True, nullable=False)
    potential_record_id: Mapped[str | None] = mapped_column(String(36))
    reviewed_by: Mapped[str | None] = mapped_column(String(128))
    reviewed_at: Mapped[datetime | None] = mapped_column(DateTime(timezone=True))
    created_at: Mapped[datetime] = mapped_column(DateTime(timezone=True), nullable=False)
    updated_at: Mapped[datetime] = mapped_column(DateTime(timezone=True), nullable=False)


class ControlPotentialCandidateEventRow(ControlBase):
    __tablename__ = "control_potential_candidate_events"

    id: Mapped[str] = mapped_column(String(36), primary_key=True)
    candidate_id: Mapped[str] = mapped_column(
        String(36), index=True, nullable=False
    )
    version: Mapped[int] = mapped_column(Integer, nullable=False)
    operation: Mapped[str] = mapped_column(String(24), nullable=False)
    actor_id: Mapped[str] = mapped_column(String(128), nullable=False)
    payload_sha256: Mapped[str] = mapped_column(String(64), nullable=False)
    snapshot: Mapped[dict[str, Any]] = mapped_column(JSON, nullable=False)
    reason: Mapped[str | None] = mapped_column(Text)
    created_at: Mapped[datetime] = mapped_column(DateTime(timezone=True), nullable=False)


class ControlTaskRouteView(BaseModel):
    model_config = ConfigDict(from_attributes=True)

    run_id: UUID
    source_step_id: UUID
    company_id: UUID
    project_id: UUID
    thread_id: UUID
    agent_kind: str
    task_kind: str
    route: str
    rule_version: str
    route_payload: dict[str, Any]
    payload_sha256: str = Field(pattern=r"^[0-9a-f]{64}$")
    recorded_at: datetime

    @field_validator("recorded_at", mode="before")
    @classmethod
    def recorded_at_is_utc(cls, value: datetime) -> datetime:
        return _aware(value)


class ControlTaskRoutePage(BaseModel):
    items: list[ControlTaskRouteView]
    total: int


class ControlQueryManifestView(BaseModel):
    model_config = ConfigDict(from_attributes=True)

    id: str = Field(pattern=r"^[0-9a-f]{64}$")
    run_id: UUID
    ordinal: int = Field(ge=1)
    company_id: UUID
    project_id: UUID
    query_snapshot_id: UUID | None
    route: str | None
    selected_stores: list[str]
    management_context_access: dict[str, Any]
    coverage: dict[str, Any]
    read_reference_uris: list[str]
    model_visible_reference_uris: list[str]
    model_context_shared: bool
    payload_sha256: str = Field(pattern=r"^[0-9a-f]{64}$")
    recorded_at: datetime

    @field_validator("recorded_at", mode="before")
    @classmethod
    def recorded_at_is_utc(cls, value: datetime) -> datetime:
        return _aware(value)


class ControlQueryManifestPage(BaseModel):
    items: list[ControlQueryManifestView]
    total: int


class ControlEvidenceReadView(BaseModel):
    model_config = ConfigDict(from_attributes=True)

    id: str = Field(pattern=r"^[0-9a-f]{64}$")
    query_manifest_id: str = Field(pattern=r"^[0-9a-f]{64}$")
    run_id: UUID
    company_id: UUID
    project_id: UUID
    ordinal: int = Field(ge=1)
    source_store: str
    reference_uri: str
    model_visible: bool
    query_manifest_sha256: str = Field(pattern=r"^[0-9a-f]{64}$")
    recorded_at: datetime

    @field_validator("recorded_at", mode="before")
    @classmethod
    def recorded_at_is_utc(cls, value: datetime) -> datetime:
        return _aware(value)


class ControlEvidenceReadPage(BaseModel):
    items: list[ControlEvidenceReadView]
    total: int


class ControlClaimLedgerView(BaseModel):
    model_config = ConfigDict(from_attributes=True)

    claim_id: UUID
    company_id: UUID
    project_id: UUID
    run_id: UUID
    source_step_id: UUID
    ordinal: int = Field(ge=1)
    statement: str
    kind: AgentClaimKind
    supporting_refs: list[str]
    counterevidence_refs: list[str]
    submitted_supporting_refs: list[str]
    submitted_counterevidence_refs: list[str]
    scope: str | None
    unknowns: list[str]
    validation_status: ClaimLedgerValidationStatus
    rejected_reference_count: int = Field(ge=0)
    query_manifest_sha256: str = Field(pattern=r"^[0-9a-f]{64}$")
    payload_sha256: str = Field(pattern=r"^[0-9a-f]{64}$")
    created_at: datetime

    @field_validator("created_at", mode="before")
    @classmethod
    def created_at_is_utc(cls, value: datetime) -> datetime:
        return _aware(value)


class ControlClaimLedgerPage(BaseModel):
    items: list[ControlClaimLedgerView]
    total: int


class ControlExecutionRuleHitView(BaseModel):
    rule_id: str
    outcome: str
    evidence_item_count: int


class ControlExecutionRouteView(BaseModel):
    decision_source: Literal["PERSISTED_DETERMINISTIC_RULE_OUTPUT"]
    route: str
    task_kind: str
    rule_version: str
    execution_authorized: bool | None
    selected_stores: list[str]
    model_access: str | None
    rule_hits: list[ControlExecutionRuleHitView]
    rule_trace_available: bool
    missing_item_count: int
    boundary_count: int
    recorded_at: datetime


class ControlExecutionSourceReferenceView(BaseModel):
    reference_uri: str
    source_store: str
    model_visible: bool
    evidence_read_indexed: bool


class ControlExecutionReadSetView(BaseModel):
    query_manifest_id: str = Field(pattern=r"^[0-9a-f]{64}$")
    ordinal: int = Field(ge=1)
    recorded_at: datetime
    route: str | None
    query_snapshot_id: UUID | None
    selected_stores: list[str]
    management_context_access: dict[str, Any]
    coverage: dict[str, Any]
    model_context_shared: bool
    source_references: list[ControlExecutionSourceReferenceView]
    omitted_source_reference_count: int = Field(ge=0)
    evidence_index_consistent: bool


class ControlExecutionClaimReferenceView(BaseModel):
    reference_uri: str
    read_in_this_run: bool


class ControlExecutionClaimView(BaseModel):
    claim_id: UUID
    ordinal: int = Field(ge=1)
    kind: str
    validation_status: str
    supporting_references: list[ControlExecutionClaimReferenceView]
    counterevidence_references: list[ControlExecutionClaimReferenceView]
    omitted_reference_count: int = Field(ge=0)
    query_manifest_ids: list[str]
    manifest_binding_found: bool
    rejected_reference_count: int = Field(ge=0)


class ControlExecutionExplanation(BaseModel):
    scope: Literal["CONTROL_INDEX_ONLY"] = "CONTROL_INDEX_ONLY"
    company_id: UUID
    project_id: UUID
    run_id: UUID
    route: ControlExecutionRouteView
    read_sets: list[ControlExecutionReadSetView]
    claims: list[ControlExecutionClaimView]
    source_reference_count: int = Field(ge=0)
    business_content_included: Literal[False] = False
    business_databases_accessed: Literal[False] = False
    semantic_support_proven: Literal[False] = False
    limitations: list[str]


class ControlPotentialCandidateView(BaseModel):
    id: UUID
    company_id: UUID
    project_id: UUID
    source_run_id: UUID
    source_step_id: UUID
    version: int = Field(ge=1)
    candidate: PotentialCandidateDraft
    payload_sha256: str = Field(pattern=r"^[0-9a-f]{64}$")
    status: PotentialCandidateStatus
    potential_record_id: UUID | None
    reviewed_by: str | None
    reviewed_at: datetime | None
    created_at: datetime
    updated_at: datetime

    @field_validator("reviewed_at", "created_at", "updated_at", mode="before")
    @classmethod
    def timestamps_are_utc(cls, value: datetime | None) -> datetime | None:
        return _aware(value) if value is not None else None


class ControlPotentialCandidatePage(BaseModel):
    items: list[ControlPotentialCandidateView]
    total: int


class ControlPotentialCandidateAcceptance(BaseModel):
    candidate: ControlPotentialCandidateView
    record: PotentialRecordView


class ControlPotentialCandidateEdit(BaseModel):
    model_config = ConfigDict(extra="forbid")

    expected_hash: str = Field(pattern=r"^[0-9a-f]{64}$")
    candidate: PotentialCandidateDraft
    reason: str = Field(min_length=1, max_length=4_000)


class ControlPotentialCandidateDecision(BaseModel):
    model_config = ConfigDict(extra="forbid")

    expected_hash: str = Field(pattern=r"^[0-9a-f]{64}$")
    reason: str | None = Field(default=None, max_length=4_000)


class ControlPotentialCandidateEventView(BaseModel):
    model_config = ConfigDict(from_attributes=True)

    id: UUID
    candidate_id: UUID
    version: int = Field(ge=1)
    operation: str
    actor_id: str
    payload_sha256: str = Field(pattern=r"^[0-9a-f]{64}$")
    snapshot: dict[str, Any]
    reason: str | None
    created_at: datetime

    @field_validator("created_at", mode="before")
    @classmethod
    def created_at_is_utc(cls, value: datetime) -> datetime:
        return _aware(value)


class ControlPotentialCandidateEventPage(BaseModel):
    items: list[ControlPotentialCandidateEventView]
    total: int


class ControlDatabase:
    CURRENT_SCHEMA_VERSION = 6

    def __init__(self, database_url: str) -> None:
        connect_args = (
            {"check_same_thread": False, "timeout": 30}
            if database_url.startswith("sqlite")
            else {}
        )
        self.engine: Engine = create_engine(
            database_url, connect_args=connect_args, pool_pre_ping=True
        )
        if database_url.startswith("sqlite"):
            event.listen(self.engine, "connect", _enable_sqlite_foreign_keys)
        self.session_factory = sessionmaker(
            bind=self.engine, autoflush=False, expire_on_commit=False
        )

    def create_schema(self) -> None:
        if inspect(self.engine).has_table("control_schema"):
            with self.engine.connect() as connection:
                existing_version = connection.exec_driver_sql(
                    "SELECT version FROM control_schema WHERE id = 1"
                ).scalar_one_or_none()
                version = int(existing_version) if existing_version is not None else None
            if version is not None and version > self.CURRENT_SCHEMA_VERSION:
                raise RuntimeError(
                    "控制库版本高于当前程序；为避免降级破坏数据，已停止启动。"
                )
            existing_columns = {
                item["name"] for item in inspect(self.engine).get_columns("control_schema")
            }
            missing_checkpoints = {
                "query_manifest_backfill_completed",
                "step_index_backfill_completed",
            } - existing_columns
            if missing_checkpoints:
                # Control DB upgrades are additive. Keep existing ledgers and add
                # durable one-time backfill checkpoints needed by the outboxes.
                with self.engine.begin() as connection:
                    for name in sorted(missing_checkpoints):
                        connection.exec_driver_sql(
                            f"ALTER TABLE control_schema ADD COLUMN {name} "
                            "BOOLEAN NOT NULL DEFAULT FALSE"
                        )
        ControlBase.metadata.create_all(self.engine)
        with self.session_factory.begin() as session:
            schema = session.get(ControlSchemaRow, 1)
            if schema is None:
                session.add(
                    ControlSchemaRow(
                        id=1,
                        version=self.CURRENT_SCHEMA_VERSION,
                        updated_at=_now(),
                    )
                )
            elif schema.version in {1, 2, 3, 4, 5} and self.CURRENT_SCHEMA_VERSION == 6:
                # All control schema upgrades are additive and preserve prior ledgers.
                schema.version = self.CURRENT_SCHEMA_VERSION
                schema.updated_at = _now()
            elif schema.version != self.CURRENT_SCHEMA_VERSION:
                raise RuntimeError(
                    f"控制库版本 {schema.version} 没有可用迁移到 "
                    f"{self.CURRENT_SCHEMA_VERSION}。"
                )
        _install_sqlite_immutability_triggers(self.engine)

    def dispose(self) -> None:
        self.engine.dispose()


class ControlIndexService:
    """Idempotently mirrors route, claim, and review evidence from source stores."""

    def __init__(self, session: Session) -> None:
        self.session = session

    def synchronize_routes(
        self,
        formal_session: Session,
        *,
        source_step_ids: set[str] | None = None,
    ) -> int:
        if source_step_ids is not None and not source_step_ids:
            return 0
        source_statement = (
            select(AgentStepRow, AgentRunRow, ProjectRow)
            .join(AgentRunRow, AgentRunRow.id == AgentStepRow.run_id)
            .join(ProjectRow, ProjectRow.id == AgentStepRow.project_id)
            .where(AgentStepRow.kind == "ROUTE")
            .order_by(AgentStepRow.created_at, AgentStepRow.id)
        )
        if source_step_ids is not None:
            source_statement = source_statement.where(AgentStepRow.id.in_(source_step_ids))
        source_rows = formal_session.execute(source_statement).all()
        inserted = self._synchronize_claim_ledger(
            formal_session, source_step_ids=source_step_ids
        )
        for step, run, project in source_rows:
            payload = _canonical_payload(step.output_payload or {})
            route = payload.get("route")
            task_kind = payload.get("task_kind")
            if not isinstance(route, str) or not isinstance(task_kind, str):
                # Incomplete or legacy route events remain in the source store but
                # are not projected as authoritative control records.
                continue
            digest = _digest(payload)
            recorded_at = _aware(step.created_at)
            existing = self.session.get(ControlTaskRouteRow, run.id)
            if existing is not None:
                if existing.source_step_id != step.id or existing.payload_sha256 != digest:
                    raise DomainError(
                        "CONTROL_ROUTE_IMMUTABLE_CONFLICT",
                        "同一任务的控制路由记录与正式来源不一致，需要人工核对。",
                        status_code=409,
                    )
            else:
                self.session.add(
                    ControlTaskRouteRow(
                        run_id=run.id,
                        source_step_id=step.id,
                        company_id=project.company_id,
                        project_id=project.id,
                        thread_id=run.thread_id,
                        agent_kind=run.agent_kind,
                        task_kind=task_kind,
                        route=route,
                        rule_version=str(payload.get("rule_version") or "unknown"),
                        route_payload=payload,
                        payload_sha256=digest,
                        recorded_at=recorded_at,
                    )
                )
                inserted += 1
            event_id = f"route:{step.id}"
            event_payload = {
                "route": route,
                "task_kind": task_kind,
                "rule_version": payload.get("rule_version"),
                "execution_authorized": payload.get("execution_authorized", False),
                "source_step_id": step.id,
            }
            event_digest = _digest(event_payload)
            existing_event = self.session.get(ControlAuditIndexRow, event_id)
            if existing_event is None:
                self.session.add(
                    ControlAuditIndexRow(
                        event_id=event_id,
                        event_kind="TASK_ROUTE_RECORDED",
                        company_id=project.company_id,
                        project_id=project.id,
                        run_id=run.id,
                        source_store="formal",
                        source_record_id=step.id,
                        payload_sha256=event_digest,
                        payload=event_payload,
                        occurred_at=recorded_at,
                        indexed_at=_now(),
                    )
                )
            else:
                _assert_same_event(existing_event, event_digest)
        inserted += ControlPotentialCandidateService(
            self.session
        ).synchronize_potential_candidates(
            formal_session, source_step_ids=source_step_ids
        )
        self.session.flush()
        return inserted

    def synchronize_step_outbox(
        self, formal_session: Session, events: list[AgentStepOutboxRow]
    ) -> int:
        """Index a bounded set of route, claim, or candidate steps by stable source ID."""
        if not events:
            return 0
        step_ids = {item.step_id for item in events}
        source_rows = formal_session.execute(
            select(AgentStepRow, AgentRunRow, ProjectRow)
            .join(AgentRunRow, AgentRunRow.id == AgentStepRow.run_id)
            .join(ProjectRow, ProjectRow.id == AgentStepRow.project_id)
            .where(AgentStepRow.id.in_(step_ids))
        ).all()
        by_step_id = {step.id: (step, run, project) for step, run, project in source_rows}
        allowed_kinds = {"ROUTE", "CLAIM_LEDGER", "POTENTIAL_CANDIDATE_PROPOSED"}
        for outbox_event in events:
            source = by_step_id.get(outbox_event.step_id)
            if source is None:
                raise DomainError(
                    "CONTROL_STEP_OUTBOX_SOURCE_MISSING",
                    "控制索引 outbox 找不到正式来源，事件保留待人工核对。",
                    status_code=409,
                )
            step, run, _project = source
            event_payload = {
                "step_id": step.id,
                "kind": step.kind,
                "status": step.status,
                "input_payload": step.input_payload or {},
                "output_payload": step.output_payload or {},
            }
            if (
                step.kind not in allowed_kinds
                or outbox_event.kind != step.kind
                or outbox_event.run_id != run.id
                or outbox_event.event_id != sha256(f"agent-step:{step.id}".encode()).hexdigest()
                or outbox_event.payload_sha256 != _digest(event_payload)
            ):
                raise DomainError(
                    "CONTROL_STEP_OUTBOX_SOURCE_CONFLICT",
                    "控制索引 outbox 与正式 Agent 步骤不一致，事件保留待人工核对。",
                    status_code=409,
                )
        inserted = self.synchronize_routes(formal_session, source_step_ids=step_ids)
        self.session.flush()
        return inserted

    def backfill_query_manifests(self, formal_session: Session) -> int:
        """One-time legacy backfill; steady-state delivery uses the source outbox."""
        source_rows = formal_session.execute(
            select(AgentRunRow, ProjectRow)
            .join(ProjectRow, ProjectRow.id == AgentRunRow.project_id)
            .order_by(AgentRunRow.created_at, AgentRunRow.id)
        ).all()
        inserted = 0
        for run, project in source_rows:
            context_manifest = run.context_manifest
            if not isinstance(context_manifest, dict):
                continue
            query_manifest = context_manifest.get("query_manifest")
            read_sets = (
                query_manifest.get("read_sets")
                if isinstance(query_manifest, dict)
                else None
            )
            if not isinstance(read_sets, list):
                continue
            for ordinal, raw_read_set in enumerate(read_sets, start=1):
                if not isinstance(raw_read_set, dict):
                    continue
                inserted += self._index_query_manifest_read_set(
                    run, project, ordinal, raw_read_set
                )
        return inserted

    def synchronize_query_manifest_outbox(
        self, formal_session: Session, events: list[AgentReadSetOutboxRow]
    ) -> int:
        """Index a bounded batch after verifying each event against its formal source."""
        if not events:
            return 0
        run_ids = {item.run_id for item in events}
        source_rows = formal_session.execute(
            select(AgentRunRow, ProjectRow)
            .join(ProjectRow, ProjectRow.id == AgentRunRow.project_id)
            .where(AgentRunRow.id.in_(run_ids))
        ).all()
        by_run_id = {run.id: (run, project) for run, project in source_rows}
        inserted = 0
        for outbox_event in events:
            source = by_run_id.get(outbox_event.run_id)
            if source is None:
                raise DomainError(
                    "QUERY_READSET_OUTBOX_SOURCE_MISSING",
                    "查询读集 outbox 找不到正式来源，事件保留待人工核对。",
                    status_code=409,
                )
            run, project = source
            event_read_set = outbox_event.payload.get("read_set")
            query_manifest = (run.context_manifest or {}).get("query_manifest")
            read_sets = (
                query_manifest.get("read_sets")
                if isinstance(query_manifest, dict)
                else None
            )
            source_read_set = (
                read_sets[outbox_event.ordinal - 1]
                if isinstance(read_sets, list)
                and 0 < outbox_event.ordinal <= len(read_sets)
                else None
            )
            if (
                not isinstance(event_read_set, dict)
                or not isinstance(source_read_set, dict)
                or _digest(outbox_event.payload) != outbox_event.payload_sha256
                or _digest({"read_set": source_read_set}) != outbox_event.payload_sha256
            ):
                raise DomainError(
                    "QUERY_READSET_OUTBOX_SOURCE_CONFLICT",
                    "查询读集 outbox 与正式运行记录不一致，事件保留待人工核对。",
                    status_code=409,
                )
            inserted += self._index_query_manifest_read_set(
                run, project, outbox_event.ordinal, event_read_set
            )
        self.session.flush()
        return inserted

    def _index_query_manifest_read_set(
        self,
        run: AgentRunRow,
        project: ProjectRow,
        ordinal: int,
        raw_read_set: dict[str, Any],
    ) -> int:
        """Persist exact read-set metadata without copying source contents."""
        selected_stores = _safe_string_list(
            raw_read_set.get("selected_stores"), maximum_items=20, maximum_length=80
        )
        read_refs = _safe_reference_list(raw_read_set.get("read_reference_uris"))
        shared = raw_read_set.get("model_context_shared") is True
        visible_refs = (
            _safe_reference_list(raw_read_set.get("model_visible_reference_uris"))
            if shared
            else []
        )
        visible_refs = [item for item in visible_refs if item in set(read_refs)]
        access = _safe_context_access(raw_read_set.get("management_context_access"))
        coverage = _safe_coverage(raw_read_set.get("coverage"))
        snapshot_id = _optional_uuid_text(raw_read_set.get("query_snapshot_id"))
        route = raw_read_set.get("route")
        route = route[:32] if isinstance(route, str) else None
        recorded_at = _readset_time(raw_read_set.get("recorded_at"), run)
        payload = {
            "run_id": run.id,
            "ordinal": ordinal,
            "company_id": project.company_id,
            "project_id": project.id,
            "query_snapshot_id": snapshot_id,
            "route": route,
            "selected_stores": selected_stores,
            "management_context_access": access,
            "coverage": coverage,
            "read_reference_uris": read_refs,
            "model_visible_reference_uris": visible_refs,
            "model_context_shared": shared,
            "recorded_at": recorded_at.isoformat(),
        }
        digest = _digest(payload)
        manifest_id = sha256(f"{run.id}:{ordinal}".encode()).hexdigest()
        inserted = 0
        existing = self.session.get(ControlQueryManifestRow, manifest_id)
        if existing is not None:
            if existing.payload_sha256 != digest:
                raise DomainError(
                    "CONTROL_QUERY_MANIFEST_IMMUTABLE_CONFLICT",
                    "同一任务的查询读集与正式运行记录不一致，需要人工核对。",
                    status_code=409,
                )
        else:
            self.session.add(
                ControlQueryManifestRow(
                    id=manifest_id,
                    run_id=run.id,
                    ordinal=ordinal,
                    company_id=project.company_id,
                    project_id=project.id,
                    query_snapshot_id=snapshot_id,
                    route=route,
                    selected_stores=selected_stores,
                    management_context_access=access,
                    coverage=coverage,
                    read_reference_uris=read_refs,
                    model_visible_reference_uris=visible_refs,
                    model_context_shared=shared,
                    payload_sha256=digest,
                    recorded_at=recorded_at,
                )
            )
            inserted += 1

        for evidence_ordinal, reference_uri in enumerate(read_refs, start=1):
            evidence_id = sha256(f"{manifest_id}:{reference_uri}".encode()).hexdigest()
            evidence_payload = {
                "id": evidence_id,
                "query_manifest_id": manifest_id,
                "run_id": run.id,
                "company_id": project.company_id,
                "project_id": project.id,
                "ordinal": evidence_ordinal,
                "source_store": _reference_store(reference_uri),
                "reference_uri": reference_uri,
                "model_visible": reference_uri in visible_refs,
                "query_manifest_sha256": digest,
                "recorded_at": recorded_at.isoformat(),
            }
            evidence_hash = _digest(evidence_payload)
            existing_read = self.session.get(ControlEvidenceReadRow, evidence_id)
            if existing_read is not None:
                if _digest(_evidence_read_payload(existing_read)) != evidence_hash:
                    raise DomainError(
                        "CONTROL_EVIDENCE_READ_IMMUTABLE_CONFLICT",
                        "同一查询读集的来源引用与正式运行记录不一致，需要人工核对。",
                        status_code=409,
                    )
                continue
            persisted_read = dict(evidence_payload)
            persisted_read["recorded_at"] = recorded_at
            self.session.add(ControlEvidenceReadRow(**persisted_read))
            inserted += 1

        event_id = f"query-read:{manifest_id}"
        event_payload = {
            "query_manifest_id": manifest_id,
            "ordinal": ordinal,
            "read_count": len(read_refs),
            "payload_sha256": digest,
        }
        event_digest = _digest(event_payload)
        existing_event = self.session.get(ControlAuditIndexRow, event_id)
        if existing_event is None:
            self.session.add(
                ControlAuditIndexRow(
                    event_id=event_id,
                    event_kind="QUERY_READ_SET_RECORDED",
                    company_id=project.company_id,
                    project_id=project.id,
                    run_id=run.id,
                    source_store="formal",
                    source_record_id=run.id,
                    payload_sha256=event_digest,
                    payload=event_payload,
                    occurred_at=recorded_at,
                    indexed_at=_now(),
                )
            )
        else:
            _assert_same_event(existing_event, event_digest)
        return inserted

    def _synchronize_claim_ledger(
        self,
        formal_session: Session,
        *,
        source_step_ids: set[str] | None = None,
    ) -> int:
        source_statement = (
            select(AgentStepRow, AgentRunRow, ProjectRow)
            .join(AgentRunRow, AgentRunRow.id == AgentStepRow.run_id)
            .join(ProjectRow, ProjectRow.id == AgentStepRow.project_id)
            .where(AgentStepRow.kind == "CLAIM_LEDGER")
            .order_by(AgentStepRow.created_at, AgentStepRow.id)
        )
        if source_step_ids is not None:
            source_statement = source_statement.where(AgentStepRow.id.in_(source_step_ids))
        source_rows = formal_session.execute(source_statement).all()
        inserted = 0
        for step, run, project in source_rows:
            output = step.output_payload or {}
            entries = output.get("entries") if isinstance(output, dict) else None
            if not isinstance(entries, list):
                continue
            query_manifest_sha256 = str(
                (step.input_payload or {}).get("query_manifest_sha256") or _digest({})
            )
            for ordinal, entry in enumerate(entries, start=1):
                if not isinstance(entry, dict):
                    continue
                claim_id = entry.get("claim_id")
                if not isinstance(claim_id, str):
                    continue
                payload = _canonical_payload(entry)
                digest = _digest(payload)
                existing = self.session.get(ControlClaimLedgerRow, claim_id)
                if existing is not None:
                    if (
                        existing.source_step_id != step.id
                        or existing.payload_sha256 != digest
                    ):
                        raise DomainError(
                            "CONTROL_CLAIM_IMMUTABLE_CONFLICT",
                            "同一结论的控制台账与正式来源不一致，需要人工核对。",
                            status_code=409,
                        )
                else:
                    self.session.add(
                        ControlClaimLedgerRow(
                            claim_id=claim_id,
                            company_id=project.company_id,
                            project_id=project.id,
                            run_id=run.id,
                            source_step_id=step.id,
                            ordinal=int(payload.get("ordinal") or ordinal),
                            statement=str(payload.get("statement") or ""),
                            kind=str(payload.get("kind") or "UNKNOWN"),
                            supporting_refs=list(payload.get("supporting_refs") or []),
                            counterevidence_refs=list(payload.get("counterevidence_refs") or []),
                            submitted_supporting_refs=list(
                                payload.get("submitted_supporting_refs") or []
                            ),
                            submitted_counterevidence_refs=list(
                                payload.get("submitted_counterevidence_refs") or []
                            ),
                            scope=payload.get("scope"),
                            unknowns=list(payload.get("unknowns") or []),
                            validation_status=str(
                                payload.get("validation_status") or "UNKNOWN"
                            ),
                            rejected_reference_count=int(
                                payload.get("rejected_reference_count") or 0
                            ),
                            query_manifest_sha256=query_manifest_sha256,
                            payload_sha256=digest,
                            created_at=_aware(step.created_at),
                        )
                    )
                    inserted += 1
                event_id = f"claim:{claim_id}"
                event_payload = {
                    "claim_id": claim_id,
                    "source_step_id": step.id,
                    "validation_status": payload.get("validation_status"),
                    "payload_sha256": digest,
                }
                event_digest = _digest(event_payload)
                existing_event = self.session.get(ControlAuditIndexRow, event_id)
                if existing_event is None:
                    self.session.add(
                        ControlAuditIndexRow(
                            event_id=event_id,
                            event_kind="CLAIM_LEDGER_RECORDED",
                            company_id=project.company_id,
                            project_id=project.id,
                            run_id=run.id,
                            source_store="formal",
                            source_record_id=step.id,
                            payload_sha256=event_digest,
                            payload=event_payload,
                            occurred_at=_aware(step.created_at),
                            indexed_at=_now(),
                        )
                    )
                else:
                    _assert_same_event(existing_event, event_digest)
        return inserted

    def list_claims(
        self,
        project_id: UUID,
        *,
        run_id: UUID | None = None,
        offset: int = 0,
        limit: int = 100,
    ) -> ControlClaimLedgerPage:
        statement = select(ControlClaimLedgerRow).where(
            ControlClaimLedgerRow.project_id == str(project_id)
        )
        count_statement = select(func.count()).select_from(ControlClaimLedgerRow).where(
            ControlClaimLedgerRow.project_id == str(project_id)
        )
        if run_id is not None:
            statement = statement.where(ControlClaimLedgerRow.run_id == str(run_id))
            count_statement = count_statement.where(
                ControlClaimLedgerRow.run_id == str(run_id)
            )
        rows = self.session.scalars(
            statement.order_by(
                ControlClaimLedgerRow.created_at.desc(),
                ControlClaimLedgerRow.ordinal,
                ControlClaimLedgerRow.claim_id,
            ).offset(offset).limit(limit)
        ).all()
        total = int(self.session.scalar(count_statement) or 0)
        return ControlClaimLedgerPage(
            items=[ControlClaimLedgerView.model_validate(row) for row in rows],
            total=total,
        )

    def get_claim(self, project_id: UUID, claim_id: UUID) -> ControlClaimLedgerView:
        row = self.session.get(ControlClaimLedgerRow, str(claim_id))
        if row is None or row.project_id != str(project_id):
            raise DomainError(
                "CONTROL_CLAIM_NOT_FOUND",
                "找不到该项目中的结论台账记录。",
                status_code=404,
            )
        return ControlClaimLedgerView.model_validate(row)

    def list_routes(
        self, project_id: UUID, *, offset: int = 0, limit: int = 100
    ) -> ControlTaskRoutePage:
        query = select(ControlTaskRouteRow).where(
            ControlTaskRouteRow.project_id == str(project_id)
        )
        total = int(
            self.session.scalar(
                select(func.count()).select_from(ControlTaskRouteRow).where(
                    ControlTaskRouteRow.project_id == str(project_id)
                )
            )
            or 0
        )
        rows = self.session.scalars(
            query.order_by(ControlTaskRouteRow.recorded_at.desc(), ControlTaskRouteRow.run_id)
            .offset(offset)
            .limit(limit)
        ).all()
        return ControlTaskRoutePage(
            items=[ControlTaskRouteView.model_validate(row) for row in rows], total=total
        )

    def get_route(self, project_id: UUID, run_id: UUID) -> ControlTaskRouteView:
        row = self.session.get(ControlTaskRouteRow, str(run_id))
        if row is None or row.project_id != str(project_id):
            raise DomainError(
                "CONTROL_TASK_ROUTE_NOT_FOUND",
                "找不到该任务的路由记录。",
                status_code=404,
            )
        return ControlTaskRouteView.model_validate(row)

    def get_execution_explanation(
        self, project_id: UUID, company_id: UUID, run_id: UUID
    ) -> ControlExecutionExplanation:
        """Build a content-free execution report from the durable control index only."""
        scope = (
            ControlTaskRouteRow.project_id == str(project_id),
            ControlTaskRouteRow.company_id == str(company_id),
            ControlTaskRouteRow.run_id == str(run_id),
            ControlTaskRouteRow.agent_kind == "MANAGEMENT",
        )
        route = self.session.scalar(
            select(ControlTaskRouteRow).where(*scope)
        )
        if route is None:
            raise DomainError(
                "CONTROL_EXECUTION_EXPLANATION_NOT_FOUND",
                "找不到该公司和项目内已索引的管理 Agent 执行记录。",
                status_code=404,
            )

        manifests = self.session.scalars(
            select(ControlQueryManifestRow)
            .where(
                ControlQueryManifestRow.company_id == str(company_id),
                ControlQueryManifestRow.project_id == str(project_id),
                ControlQueryManifestRow.run_id == str(run_id),
            )
            .order_by(ControlQueryManifestRow.ordinal, ControlQueryManifestRow.id)
        ).all()
        evidence_rows = self.session.scalars(
            select(ControlEvidenceReadRow)
            .where(
                ControlEvidenceReadRow.company_id == str(company_id),
                ControlEvidenceReadRow.project_id == str(project_id),
                ControlEvidenceReadRow.run_id == str(run_id),
            )
            .order_by(
                ControlEvidenceReadRow.query_manifest_id,
                ControlEvidenceReadRow.ordinal,
                ControlEvidenceReadRow.id,
            )
        ).all()
        evidence_by_manifest: dict[str, list[ControlEvidenceReadRow]] = {}
        for evidence in evidence_rows:
            evidence_by_manifest.setdefault(evidence.query_manifest_id, []).append(evidence)

        read_sets: list[ControlExecutionReadSetView] = []
        read_reference_uris: set[str] = set()
        for manifest in manifests:
            evidence = evidence_by_manifest.get(manifest.id, [])
            evidence_keys = {
                (item.reference_uri, item.source_store, item.model_visible)
                for item in evidence
            }
            indexed_refs = _safe_reference_list(manifest.read_reference_uris)
            visible_refs = (
                set(_safe_reference_list(manifest.model_visible_reference_uris))
                if manifest.model_context_shared
                else set()
            )
            expected_evidence_keys = {
                (uri, _reference_store(uri), uri in visible_refs)
                for uri in indexed_refs
            }
            safe_refs, omitted_count = _execution_reference_list(indexed_refs)
            sources = [
                ControlExecutionSourceReferenceView(
                    reference_uri=uri,
                    source_store=_reference_store(uri),
                    model_visible=uri in visible_refs,
                    evidence_read_indexed=(
                        uri,
                        _reference_store(uri),
                        uri in visible_refs,
                    )
                    in evidence_keys,
                )
                for uri in safe_refs
            ]
            read_reference_uris.update(item.reference_uri for item in sources)
            read_sets.append(
                ControlExecutionReadSetView(
                    query_manifest_id=manifest.id,
                    ordinal=manifest.ordinal,
                    recorded_at=_aware(manifest.recorded_at),
                    route=_safe_route_name(manifest.route),
                    query_snapshot_id=_optional_uuid(manifest.query_snapshot_id),
                    selected_stores=_execution_store_names(manifest.selected_stores),
                    management_context_access=_execution_context_access(
                        manifest.management_context_access
                    ),
                    coverage=_execution_coverage(manifest.coverage),
                    model_context_shared=manifest.model_context_shared,
                    source_references=sources,
                    omitted_source_reference_count=omitted_count,
                    evidence_index_consistent=(
                        len(evidence) == len(expected_evidence_keys)
                        and evidence_keys == expected_evidence_keys
                    ),
                )
            )

        claims = self.session.scalars(
            select(ControlClaimLedgerRow)
            .where(
                ControlClaimLedgerRow.company_id == str(company_id),
                ControlClaimLedgerRow.project_id == str(project_id),
                ControlClaimLedgerRow.run_id == str(run_id),
            )
            .order_by(ControlClaimLedgerRow.ordinal, ControlClaimLedgerRow.claim_id)
        ).all()
        manifests_by_hash: dict[str, list[str]] = {}
        for manifest in manifests:
            manifests_by_hash.setdefault(manifest.payload_sha256, []).append(manifest.id)

        claim_views: list[ControlExecutionClaimView] = []
        for claim in claims:
            supporting, supporting_omitted = _execution_reference_list(
                claim.supporting_refs
            )
            counterevidence, counterevidence_omitted = _execution_reference_list(
                claim.counterevidence_refs
            )
            claim_views.append(
                ControlExecutionClaimView(
                    claim_id=UUID(claim.claim_id),
                    ordinal=claim.ordinal,
                    kind=_safe_claim_kind(claim.kind),
                    validation_status=_safe_claim_status(claim.validation_status),
                    supporting_references=[
                        ControlExecutionClaimReferenceView(
                            reference_uri=uri,
                            read_in_this_run=uri in read_reference_uris,
                        )
                        for uri in supporting
                    ],
                    counterevidence_references=[
                        ControlExecutionClaimReferenceView(
                            reference_uri=uri,
                            read_in_this_run=uri in read_reference_uris,
                        )
                        for uri in counterevidence
                    ],
                    omitted_reference_count=(
                        supporting_omitted + counterevidence_omitted
                    ),
                    query_manifest_ids=manifests_by_hash.get(
                        claim.query_manifest_sha256, []
                    ),
                    manifest_binding_found=(
                        claim.query_manifest_sha256 in manifests_by_hash
                    ),
                    rejected_reference_count=claim.rejected_reference_count,
                )
            )

        source_reference_count = sum(len(item.source_references) for item in read_sets)
        return ControlExecutionExplanation(
            company_id=company_id,
            project_id=project_id,
            run_id=run_id,
            route=_execution_route_view(route),
            read_sets=read_sets,
            claims=claim_views,
            source_reference_count=source_reference_count,
            limitations=[
                "本报告只读取控制库中的已索引记录，不连接或回查任何业务数据源。",
                "路由展示的是持久化规则输出；本报告不重跑规则，也不验证上游解析输入的语义正确性。",
                "来源引用存在或可解析不等于语义上支持结论；ClaimLedger 不构成语义或因果证明。",
                "覆盖和截断仅反映被索引的扫描元数据；未记录或未覆盖不能推断为不存在相关数据。",
                "为避免复制业务正文，报告不返回用户任务原文、路由证据文本、结论正文、范围/未知项或材料正文。",
            ],
        )

    def list_query_manifests(
        self,
        project_id: UUID,
        *,
        run_id: UUID | None = None,
        offset: int = 0,
        limit: int = 100,
    ) -> ControlQueryManifestPage:
        statement = select(ControlQueryManifestRow).where(
            ControlQueryManifestRow.project_id == str(project_id)
        )
        count_statement = select(func.count()).select_from(ControlQueryManifestRow).where(
            ControlQueryManifestRow.project_id == str(project_id)
        )
        if run_id is not None:
            statement = statement.where(ControlQueryManifestRow.run_id == str(run_id))
            count_statement = count_statement.where(
                ControlQueryManifestRow.run_id == str(run_id)
            )
        rows = self.session.scalars(
            statement.order_by(
                ControlQueryManifestRow.recorded_at.desc(),
                ControlQueryManifestRow.ordinal.desc(),
                ControlQueryManifestRow.id,
            )
            .offset(offset)
            .limit(limit)
        ).all()
        total = int(self.session.scalar(count_statement) or 0)
        return ControlQueryManifestPage(
            items=[ControlQueryManifestView.model_validate(row) for row in rows],
            total=total,
        )

    def list_evidence_reads(
        self,
        project_id: UUID,
        *,
        run_id: UUID | None = None,
        query_manifest_id: str | None = None,
        offset: int = 0,
        limit: int = 100,
    ) -> ControlEvidenceReadPage:
        statement = select(ControlEvidenceReadRow).where(
            ControlEvidenceReadRow.project_id == str(project_id)
        )
        count_statement = select(func.count()).select_from(ControlEvidenceReadRow).where(
            ControlEvidenceReadRow.project_id == str(project_id)
        )
        if run_id is not None:
            statement = statement.where(ControlEvidenceReadRow.run_id == str(run_id))
            count_statement = count_statement.where(ControlEvidenceReadRow.run_id == str(run_id))
        if query_manifest_id is not None:
            statement = statement.where(
                ControlEvidenceReadRow.query_manifest_id == query_manifest_id
            )
            count_statement = count_statement.where(
                ControlEvidenceReadRow.query_manifest_id == query_manifest_id
            )
        rows = self.session.scalars(
            statement.order_by(
                ControlEvidenceReadRow.recorded_at.desc(),
                ControlEvidenceReadRow.ordinal,
                ControlEvidenceReadRow.id,
            )
            .offset(offset)
            .limit(limit)
        ).all()
        total = int(self.session.scalar(count_statement) or 0)
        return ControlEvidenceReadPage(
            items=[ControlEvidenceReadView.model_validate(row) for row in rows],
            total=total,
        )


class ControlClaimLedgerService:
    """Read-only project-scoped access to the reconciled immutable claim index."""

    def __init__(self, session: Session) -> None:
        self.session = session

    def list_claims(
        self,
        project_id: UUID,
        *,
        run_id: UUID | None = None,
        offset: int = 0,
        limit: int = 100,
    ) -> ControlClaimLedgerPage:
        return ControlIndexService(self.session).list_claims(
            project_id, run_id=run_id, offset=offset, limit=limit
        )


class ControlPotentialCandidateService:
    """Versioned human review lifecycle for AI-proposed potential knowledge."""

    def __init__(self, session: Session) -> None:
        self.session = session

    def list_candidates(
        self,
        project_id: UUID,
        *,
        status: PotentialCandidateStatus | None = None,
        offset: int = 0,
        limit: int = 100,
    ) -> ControlPotentialCandidatePage:
        query = select(ControlPotentialCandidateRow).where(
            ControlPotentialCandidateRow.project_id == str(project_id)
        )
        count_query = select(func.count()).select_from(ControlPotentialCandidateRow).where(
            ControlPotentialCandidateRow.project_id == str(project_id)
        )
        if status is not None:
            query = query.where(ControlPotentialCandidateRow.status == status.value)
            count_query = count_query.where(ControlPotentialCandidateRow.status == status.value)
        total = int(self.session.scalar(count_query) or 0)
        rows = self.session.scalars(
            query.order_by(
                ControlPotentialCandidateRow.created_at.desc(),
                ControlPotentialCandidateRow.id,
            )
            .offset(offset)
            .limit(limit)
        ).all()
        return ControlPotentialCandidatePage(
            items=[_candidate_view(row) for row in rows], total=total
        )

    def get_candidate(
        self, project_id: UUID, candidate_id: UUID
    ) -> ControlPotentialCandidateView:
        row = self._get(project_id, candidate_id)
        return _candidate_view(row)

    def history(
        self,
        project_id: UUID,
        candidate_id: UUID,
        *,
        offset: int = 0,
        limit: int = 100,
    ) -> ControlPotentialCandidateEventPage:
        self._get(project_id, candidate_id)
        count = int(
            self.session.scalar(
                select(func.count()).select_from(ControlPotentialCandidateEventRow).where(
                    ControlPotentialCandidateEventRow.candidate_id == str(candidate_id)
                )
            )
            or 0
        )
        rows = self.session.scalars(
            select(ControlPotentialCandidateEventRow)
            .where(ControlPotentialCandidateEventRow.candidate_id == str(candidate_id))
            .order_by(
                ControlPotentialCandidateEventRow.created_at,
                ControlPotentialCandidateEventRow.id,
            )
            .offset(offset)
            .limit(limit)
        ).all()
        return ControlPotentialCandidateEventPage(
            items=[ControlPotentialCandidateEventView.model_validate(row) for row in rows],
            total=count,
        )

    def edit(
        self,
        project_id: UUID,
        candidate_id: UUID,
        request: ControlPotentialCandidateEdit,
        *,
        actor_id: str,
    ) -> ControlPotentialCandidateView:
        row = self._get(project_id, candidate_id)
        self._require_current_hash(row, request.expected_hash)
        if row.status != PotentialCandidateStatus.PROPOSED.value:
            raise DomainError(
                "CONTROL_CANDIDATE_NOT_EDITABLE",
                "只有待确认候选可以修改。",
                status_code=409,
            )
        current = PotentialCandidateDraft.model_validate(row.payload)
        candidate = request.candidate
        if (
            candidate.candidate_id != candidate_id
            or candidate.company_id != UUID(row.company_id)
            or candidate.project_id != project_id
            or candidate.supporting_evidence != current.supporting_evidence
            or candidate.counterevidence != current.counterevidence
            or candidate.evidence_status != current.evidence_status
        ):
            raise DomainError(
                "CONTROL_CANDIDATE_EVIDENCE_IMMUTABLE",
                "修改候选时不可替换来源证据或提高证据状态；请保留原证据并单独补充材料。",
                status_code=422,
            )
        new_payload = candidate.model_dump(mode="json")
        new_hash = candidate.payload_hash()
        if new_hash == row.payload_sha256:
            return _candidate_view(row)
        now = _now()
        row.version += 1
        row.payload = new_payload
        row.payload_sha256 = new_hash
        row.updated_at = now
        self._append_event(
            row,
            operation="EDITED",
            actor_id=actor_id,
            reason=request.reason,
            at=now,
        )
        self.session.flush()
        return _candidate_view(row)

    def prepare_accept(
        self,
        project_id: UUID,
        candidate_id: UUID,
        request: ControlPotentialCandidateDecision,
        *,
        actor_id: str,
    ) -> ControlPotentialCandidateView:
        row = self._get(project_id, candidate_id)
        self._require_current_hash(row, request.expected_hash)
        if row.status == PotentialCandidateStatus.ACCEPTED.value:
            return _candidate_view(row)
        if row.status == PotentialCandidateStatus.REJECTED.value:
            raise DomainError(
                "CONTROL_CANDIDATE_ALREADY_REJECTED",
                "已拒绝的候选不能直接接受。",
                status_code=409,
            )
        if row.status == PotentialCandidateStatus.STALE.value:
            raise DomainError(
                "CONTROL_CANDIDATE_SOURCE_STALE",
                "候选所依赖的观察已修订或撤回，不能接受；请重新分析或拒绝候选。",
                status_code=409,
            )
        if row.status == PotentialCandidateStatus.PROPOSED.value:
            now = _now()
            row.status = PotentialCandidateStatus.ACCEPTING.value
            row.reviewed_by = _actor_id(actor_id)
            row.reviewed_at = now
            row.updated_at = now
            self._append_event(
                row,
                operation="ACCEPTING",
                actor_id=actor_id,
                reason=request.reason or "人工确认候选；正在写入潜在库。",
                at=now,
            )
            self._ensure_operation_receipt(row, now=now)
            self.session.flush()
        elif row.status == PotentialCandidateStatus.ACCEPTING.value:
            self._ensure_operation_receipt(row, now=_now())
            self.session.flush()
        return _candidate_view(row)

    def mark_stale(
        self,
        project_id: UUID,
        candidate_id: UUID,
        *,
        expected_hash: str,
    ) -> ControlPotentialCandidateView:
        row = self._get(project_id, candidate_id)
        self._require_current_hash(row, expected_hash)
        if row.status == PotentialCandidateStatus.STALE.value:
            return _candidate_view(row)
        if row.status != PotentialCandidateStatus.PROPOSED.value:
            raise DomainError(
                "CONTROL_CANDIDATE_NOT_STALEABLE",
                "只有待确认候选可以因来源变化而标记过期。",
                status_code=409,
            )
        now = _now()
        row.status = PotentialCandidateStatus.STALE.value
        row.reviewed_by = "system:source-validation"
        row.reviewed_at = now
        row.updated_at = now
        self._append_event(
            row,
            operation="STALE",
            actor_id="system:source-validation",
            reason="来源记录已修订、撤回或无法核验；需重新分析。",
            at=now,
        )
        self.session.flush()
        return _candidate_view(row)

    def finish_accept(
        self,
        project_id: UUID,
        candidate_id: UUID,
        *,
        expected_hash: str,
        potential_record_id: UUID,
        actor_id: str,
    ) -> ControlPotentialCandidateView:
        row = self._get(project_id, candidate_id)
        self._require_current_hash(row, expected_hash)
        if row.status == PotentialCandidateStatus.ACCEPTED.value:
            if row.potential_record_id != str(potential_record_id):
                raise DomainError(
                    "CONTROL_CANDIDATE_ACCEPTANCE_CONFLICT",
                    "候选已关联到不同的潜在记录，需要人工核对。",
                    status_code=409,
                )
            return _candidate_view(row)
        if row.status != PotentialCandidateStatus.ACCEPTING.value:
            raise DomainError(
                "CONTROL_CANDIDATE_NOT_ACCEPTING",
                "候选尚未进入接受流程。",
                status_code=409,
            )
        now = _now()
        row.status = PotentialCandidateStatus.ACCEPTED.value
        row.potential_record_id = str(potential_record_id)
        row.reviewed_by = _actor_id(actor_id)
        row.reviewed_at = row.reviewed_at or now
        row.updated_at = now
        self._append_event(
            row,
            operation="ACCEPTED",
            actor_id=actor_id,
            reason="潜在库已确认写入。",
            at=now,
        )
        receipt = self._ensure_operation_receipt(row, now=now)
        receipt.status = "COMMITTED"
        receipt.target_record_id = str(potential_record_id)
        receipt.result = {"potential_record_id": str(potential_record_id)}
        receipt.updated_at = now
        self.session.flush()
        return _candidate_view(row)

    def reject(
        self,
        project_id: UUID,
        candidate_id: UUID,
        request: ControlPotentialCandidateDecision,
        *,
        actor_id: str,
    ) -> ControlPotentialCandidateView:
        row = self._get(project_id, candidate_id)
        self._require_current_hash(row, request.expected_hash)
        if row.status == PotentialCandidateStatus.REJECTED.value:
            return _candidate_view(row)
        if row.status not in {
            PotentialCandidateStatus.PROPOSED.value,
            PotentialCandidateStatus.STALE.value,
        }:
            raise DomainError(
                "CONTROL_CANDIDATE_NOT_REJECTABLE",
                "只有待确认候选可以拒绝。",
                status_code=409,
            )
        now = _now()
        row.status = PotentialCandidateStatus.REJECTED.value
        row.reviewed_by = _actor_id(actor_id)
        row.reviewed_at = now
        row.updated_at = now
        self._append_event(
            row,
            operation="REJECTED",
            actor_id=actor_id,
            reason=request.reason or "管理者拒绝该潜在候选。",
            at=now,
        )
        self.session.flush()
        return _candidate_view(row)

    def _get(self, project_id: UUID, candidate_id: UUID) -> ControlPotentialCandidateRow:
        row = self.session.get(ControlPotentialCandidateRow, str(candidate_id))
        if row is None or row.project_id != str(project_id):
            raise DomainError(
                "CONTROL_CANDIDATE_NOT_FOUND",
                "找不到该项目中的潜在候选。",
                status_code=404,
            )
        return row

    @staticmethod
    def _require_current_hash(row: ControlPotentialCandidateRow, expected_hash: str) -> None:
        if row.payload_sha256 != expected_hash:
            raise DomainError(
                "CONTROL_CANDIDATE_STALE",
                "候选版本已变化，请重新查看后再确认。",
                status_code=409,
            )

    def _append_event(
        self,
        row: ControlPotentialCandidateRow,
        *,
        operation: str,
        actor_id: str,
        reason: str | None,
        at: datetime,
    ) -> None:
        self.session.add(
            ControlPotentialCandidateEventRow(
                id=str(uuid4()),
                candidate_id=row.id,
                version=row.version,
                operation=operation,
                actor_id=_actor_id(actor_id),
                payload_sha256=row.payload_sha256,
                snapshot={
                    "candidate": row.payload,
                    "status": row.status,
                    "potential_record_id": row.potential_record_id,
                    "operation_id": _candidate_operation_id(row),
                },
                reason=reason,
                created_at=at,
            )
        )

    def _ensure_operation_receipt(
        self, row: ControlPotentialCandidateRow, *, now: datetime
    ) -> ControlOperationReceiptRow:
        operation_id = _candidate_operation_id(row)
        receipt = self.session.get(ControlOperationReceiptRow, operation_id)
        if receipt is None:
            receipt = ControlOperationReceiptRow(
                operation_id=operation_id,
                target_store="potential",
                operation_kind="ACCEPT_POTENTIAL_CANDIDATE",
                target_record_id=None,
                request_sha256=row.payload_sha256,
                status="RECEIVED",
                result=None,
                created_at=now,
                updated_at=now,
            )
            self.session.add(receipt)
            return receipt
        if receipt.request_sha256 != row.payload_sha256:
            raise DomainError(
                "CONTROL_OPERATION_IDEMPOTENCY_CONFLICT",
                "跨库操作回执与候选版本不一致，需要人工核对。",
                status_code=409,
            )
        return receipt

    def synchronize_potential_candidates(
        self,
        formal_session: Session,
        *,
        source_step_ids: set[str] | None = None,
    ) -> int:
        if source_step_ids is not None and not source_step_ids:
            return 0
        source_statement = (
            select(AgentStepRow, AgentRunRow, ProjectRow)
            .join(AgentRunRow, AgentRunRow.id == AgentStepRow.run_id)
            .join(ProjectRow, ProjectRow.id == AgentStepRow.project_id)
            .where(AgentStepRow.kind == "POTENTIAL_CANDIDATE_PROPOSED")
            .order_by(AgentStepRow.created_at, AgentStepRow.id)
        )
        if source_step_ids is not None:
            source_statement = source_statement.where(AgentStepRow.id.in_(source_step_ids))
        source_rows = formal_session.execute(source_statement).all()
        inserted = 0
        for step, run, project in source_rows:
            envelope = step.output_payload or {}
            raw_candidate = envelope.get("candidate")
            if not isinstance(raw_candidate, dict):
                continue
            try:
                candidate = PotentialCandidateDraft.model_validate(raw_candidate)
            except Exception:
                continue
            if (
                str(candidate.company_id) != project.company_id
                or str(candidate.project_id) != project.id
                or candidate.evidence_status != EvidenceStatus.UNTESTED
            ):
                continue
            payload = candidate.model_dump(mode="json")
            digest = candidate.payload_hash()
            if envelope.get("candidate_hash") != digest:
                continue
            existing = self.session.get(ControlPotentialCandidateRow, str(candidate.candidate_id))
            if existing is not None:
                if existing.source_step_id != step.id or existing.payload_sha256 != digest:
                    raise DomainError(
                        "CONTROL_CANDIDATE_IMMUTABLE_CONFLICT",
                        "同一潜在候选与正式来源不一致，需要人工核对。",
                        status_code=409,
                    )
                continue
            now = _now()
            self.session.add(
                ControlPotentialCandidateRow(
                    id=str(candidate.candidate_id),
                    company_id=project.company_id,
                    project_id=project.id,
                    source_run_id=run.id,
                    source_step_id=step.id,
                    version=1,
                    payload=payload,
                    payload_sha256=digest,
                    status=PotentialCandidateStatus.PROPOSED.value,
                    potential_record_id=None,
                    reviewed_by=None,
                    reviewed_at=None,
                    created_at=now,
                    updated_at=now,
                )
            )
            event_payload = {"candidate": payload, "status": "PROPOSED"}
            self.session.add(
                ControlPotentialCandidateEventRow(
                    id=str(uuid4()),
                    candidate_id=str(candidate.candidate_id),
                    version=1,
                    operation="PROPOSED",
                    actor_id="agent",
                    payload_sha256=digest,
                    snapshot=event_payload,
                    reason="Agent 提出待人工核实的潜在认识。",
                    created_at=now,
                )
            )
            inserted += 1
        return inserted

def _assert_same_event(row: ControlAuditIndexRow, digest: str) -> None:
    if row.payload_sha256 != digest:
        raise DomainError(
            "CONTROL_AUDIT_IMMUTABLE_CONFLICT",
            "控制审计索引与正式来源摘要不一致，需要人工核对。",
            status_code=409,
        )


def _canonical_payload(payload: dict[str, Any]) -> dict[str, Any]:
    return json.loads(json.dumps(payload, ensure_ascii=False, sort_keys=True, default=str))


def _digest(value: Any) -> str:
    raw = json.dumps(value, ensure_ascii=False, sort_keys=True, separators=(",", ":"))
    return sha256(raw.encode("utf-8")).hexdigest()


def _safe_string_list(value: Any, *, maximum_items: int, maximum_length: int) -> list[str]:
    if not isinstance(value, list):
        return []
    output: list[str] = []
    seen: set[str] = set()
    for item in value:
        if not isinstance(item, str) or not item or len(item) > maximum_length:
            continue
        if item not in seen:
            output.append(item)
            seen.add(item)
        if len(output) >= maximum_items:
            break
    return output


def _safe_reference_list(value: Any) -> list[str]:
    prefixes = (
        "formal://",
        "observation://",
        "potential://",
        "virtual-work://",
        "tool://",
    )
    if not isinstance(value, list):
        return []
    return _safe_string_list(
        [
            item
            for item in value
            if isinstance(item, str)
            and item.startswith(prefixes)
            and "\n" not in item
            and "\r" not in item
        ],
        maximum_items=5_000,
        maximum_length=2_000,
    )


def _safe_context_access(value: Any) -> dict[str, Any]:
    if not isinstance(value, dict):
        return {}
    output: dict[str, Any] = {}
    decision = value.get("decision")
    if isinstance(decision, str):
        output["decision"] = decision[:80]
    for key in (
        "management_observations_read",
        "potential_records_read",
        "virtual_work_read",
    ):
        if isinstance(value.get(key), bool):
            output[key] = value[key]
    return output


def _safe_coverage(value: Any) -> dict[str, Any]:
    if not isinstance(value, dict):
        return {}

    def sanitize(item: Any, depth: int = 0) -> Any:
        if depth > 4:
            return "<depth-limit>"
        if item is None or isinstance(item, bool | int | float):
            return item
        if isinstance(item, str):
            return item[:200]
        if isinstance(item, list):
            return [sanitize(child, depth + 1) for child in item[:100]]
        if isinstance(item, dict):
            return {
                str(key)[:100]: sanitize(child, depth + 1)
                for key, child in list(item.items())[:100]
                if isinstance(key, str)
            }
        return "<unsupported>"

    sanitized = sanitize(value)
    try:
        encoded = json.dumps(sanitized, ensure_ascii=False, separators=(",", ":"))
    except (TypeError, ValueError):
        return {"truncated": True}
    return sanitized if len(encoded) <= 20_000 else {"truncated": True}


def _optional_uuid_text(value: Any) -> str | None:
    if not isinstance(value, str):
        return None
    try:
        parsed = UUID(value)
    except ValueError:
        return None
    return str(parsed)


def _readset_time(value: Any, run: AgentRunRow) -> datetime:
    if isinstance(value, str):
        try:
            return _aware(datetime.fromisoformat(value.replace("Z", "+00:00")))
        except ValueError:
            pass
    fallback = run.updated_at or run.created_at
    return _aware(fallback) if fallback is not None else _now()


def _reference_store(reference_uri: str) -> str:
    scheme = reference_uri.split("://", maxsplit=1)[0]
    return {
        "formal": "formal_query_snapshot",
        "observation": "management_observations",
        "potential": "potential_records",
        "virtual-work": "reviewed_virtual_work",
        "tool": "tool_results",
    }.get(scheme, "unknown")


def _execution_route_view(row: ControlTaskRouteRow) -> ControlExecutionRouteView:
    payload = row.route_payload if isinstance(row.route_payload, dict) else {}
    raw_hits = payload.get("rule_hits")
    hits: list[ControlExecutionRuleHitView] = []
    if isinstance(raw_hits, list):
        for raw_hit in raw_hits[:100]:
            if not isinstance(raw_hit, dict):
                continue
            rule_id = raw_hit.get("rule_id")
            outcome = raw_hit.get("outcome")
            if not isinstance(rule_id, str) or not re.fullmatch(r"R_[A-Z0-9_]{1,80}", rule_id):
                continue
            if not isinstance(outcome, str) or outcome not in {
                "MATCHED",
                "BLOCKED",
                "NOT_APPLICABLE",
                "DEFERRED_TO_COMPLEX",
            }:
                continue
            evidence = raw_hit.get("evidence")
            hits.append(
                ControlExecutionRuleHitView(
                    rule_id=rule_id,
                    outcome=outcome,
                    evidence_item_count=len(evidence) if isinstance(evidence, list) else 0,
                )
            )
    missing_items = payload.get("missing_items")
    boundaries = payload.get("boundaries")
    authorized = payload.get("execution_authorized")
    model_access = payload.get("model_access")
    return ControlExecutionRouteView(
        decision_source="PERSISTED_DETERMINISTIC_RULE_OUTPUT",
        route=_safe_route_name(row.route) or "UNKNOWN",
        task_kind=_safe_execution_token(row.task_kind, fallback="UNKNOWN"),
        rule_version=_safe_execution_token(row.rule_version, fallback="UNKNOWN"),
        execution_authorized=authorized if isinstance(authorized, bool) else None,
        selected_stores=_execution_store_names(payload.get("selected_stores")),
        model_access=(
            model_access
            if isinstance(model_access, str)
            and model_access in {"REAL_ONLY", "REAL_PLUS_VIRTUAL"}
            else None
        ),
        rule_hits=hits,
        rule_trace_available=bool(hits),
        missing_item_count=len(missing_items) if isinstance(missing_items, list) else 0,
        boundary_count=len(boundaries) if isinstance(boundaries, list) else 0,
        recorded_at=_aware(row.recorded_at),
    )


def _safe_route_name(value: Any) -> str | None:
    allowed = {"SIMPLE", "SIMPLE_ACTION", "COMPLEX", "NEEDS_INPUT"}
    return value if isinstance(value, str) and value in allowed else None


def _safe_execution_token(value: Any, *, fallback: str) -> str:
    if isinstance(value, str) and re.fullmatch(r"[A-Za-z0-9][A-Za-z0-9_.-]{0,99}", value):
        return value
    return fallback


def _execution_store_names(value: Any) -> list[str]:
    allowed = {
        "formal_query_snapshot",
        "management_observations",
        "potential_records",
        "source_materials",
        "reviewed_virtual_work",
    }
    if not isinstance(value, list):
        return []
    return list(dict.fromkeys(item for item in value if isinstance(item, str) and item in allowed))


def _execution_context_access(value: Any) -> dict[str, Any]:
    if not isinstance(value, dict):
        return {}
    output: dict[str, Any] = {}
    decision = value.get("decision")
    if isinstance(decision, str) and re.fullmatch(r"[A-Z_]{1,40}", decision):
        output["decision"] = decision
    for key in (
        "management_observations_read",
        "potential_records_read",
        "virtual_work_read",
    ):
        if isinstance(value.get(key), bool):
            output[key] = value[key]
    return output


def _execution_coverage(value: Any) -> dict[str, Any]:
    """Retain allow-listed coverage metadata; drop arbitrary text and body fields."""
    if not isinstance(value, dict):
        return {}

    def metrics(raw: Any, allowed: set[str]) -> dict[str, Any]:
        if not isinstance(raw, dict):
            return {}
        return {
            key: item
            for key, item in raw.items()
            if key in allowed
            and (type(item) is int and item >= 0 or isinstance(item, bool))
        }

    output: dict[str, Any] = {}
    coverage_fields = {"available", "included", "truncated", "store_available"}
    for section in ("observations", "potential_records"):
        sanitized = metrics(value.get(section), coverage_fields)
        if sanitized:
            output[section] = sanitized

    formal = value.get("formal_context")
    if isinstance(formal, dict):
        formal_output: dict[str, Any] = {}
        count_fields = {
            "ontology_types",
            "entities",
            "relations",
            "documents",
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
            "unresolved_source_identities",
            "observation_assertions",
            "observation_conflicts",
            "reference_cases",
            "metric_definitions",
            "metric_observations",
            "meetings",
            "management_signals",
            "management_insights",
            "design_tradeoffs",
            "information_requests",
            "action_invocations",
            "action_observations",
            "available_actions",
            "management_observations",
            "potential_records",
        }
        counts = metrics(formal.get("counts"), count_fields)
        if counts:
            formal_output["counts"] = counts
        graph = metrics(
            formal.get("graph_coverage"),
            {
                "available_entities",
                "included_entities",
                "available_relations",
                "included_relations",
                "truncated",
            },
        )
        raw_graph = formal.get("graph_coverage")
        if (
            isinstance(raw_graph, dict)
            and raw_graph.get("read_tool") == "read_graph_neighborhood"
        ):
            graph["read_tool"] = "read_graph_neighborhood"
        if graph:
            formal_output["graph_coverage"] = graph
        raw_material = formal.get("material_coverage")
        if isinstance(raw_material, list):
            material_items = []
            for item in raw_material[:100]:
                if not isinstance(item, dict):
                    continue
                material: dict[str, Any] = {}
                document_id = item.get("source_document_id")
                if _canonical_uuid_text(document_id):
                    material["source_document_id"] = document_id
                for key in ("selected", "available_fragments", "included_fragments"):
                    value_item = item.get(key)
                    if isinstance(value_item, bool) or type(value_item) is int and value_item >= 0:
                        material[key] = value_item
                if material:
                    material_items.append(material)
            if material_items:
                formal_output["material_coverage"] = material_items
        if formal_output:
            output["formal_context"] = formal_output

    raw_scans = value.get("tool_scans")
    if isinstance(raw_scans, list):
        scan_fields = {
            "available",
            "scan_offset",
            "scanned",
            "next_scan_offset",
            "matching_in_scanned",
            "matching_count_is_lower_bound",
            "offset",
            "limit",
            "returned",
            "truncated",
            "revision",
            "query_snapshot_id",
            "release_id",
            "available_entities",
            "available_relations",
            "returned_entities",
            "returned_relations",
            "source_document_id",
            "total",
            "row_count",
            "dataset_id",
            "source_reference_count",
        }
        safe_resources = {
            "MANAGEMENT_OBSERVATIONS",
            "POTENTIAL_RECORDS",
            "GRAPH_NEIGHBORHOOD",
            "MATERIAL_FRAGMENTS",
            "SEMANTIC_QUERY_RUN",
        }
        scans: list[dict[str, Any]] = []
        for item in raw_scans[:100]:
            if (
                not isinstance(item, dict)
                or not isinstance(item.get("resource"), str)
                or item.get("resource") not in safe_resources
            ):
                continue
            sanitized_scan: dict[str, Any] = {"resource": item["resource"]}
            invocation_id = item.get("invocation_id")
            if _canonical_uuid_text(invocation_id):
                sanitized_scan["invocation_id"] = invocation_id
            for key in scan_fields:
                scan_value = item.get(key)
                if key in {"query_snapshot_id", "release_id", "source_document_id", "dataset_id"}:
                    if _canonical_uuid_text(scan_value):
                        sanitized_scan[key] = scan_value
                elif key == "ranking_scope":
                    if scan_value == "CURRENT_SCAN_WINDOW":
                        sanitized_scan[key] = scan_value
                elif scan_value is None and key == "next_scan_offset":
                    sanitized_scan[key] = None
                elif isinstance(scan_value, bool) or type(scan_value) is int and scan_value >= 0:
                    sanitized_scan[key] = scan_value
            scans.append(sanitized_scan)
        if scans:
            output["tool_scans"] = scans
    return output


def _execution_reference_list(value: Any) -> tuple[list[str], int]:
    raw = value if isinstance(value, list) else []
    valid: list[str] = []
    for item in raw:
        if not isinstance(item, str) or not _is_execution_reference_uri(item):
            continue
        if item not in valid:
            valid.append(item)
    return valid, max(0, len(raw) - len(valid))


def _is_execution_reference_uri(value: str) -> bool:
    formal = re.fullmatch(r"formal://[a-z][a-z0-9_]{0,63}/([^/]+)", value)
    if formal is not None:
        return _canonical_uuid_text(formal.group(1))
    for scheme in ("observation", "potential"):
        match = re.fullmatch(rf"{scheme}://([^/]+)/([1-9][0-9]*)", value)
        if match is not None:
            return _canonical_uuid_text(match.group(1))
    invocation = re.fullmatch(r"tool://invocation/([^/]+)", value)
    if invocation is not None:
        return _canonical_uuid_text(invocation.group(1))
    virtual_model = re.fullmatch(
        r"virtual-work://models/([^/]+)/revisions/([1-9][0-9]*)", value
    )
    return bool(virtual_model and _canonical_uuid_text(virtual_model.group(1)))


def _canonical_uuid_text(value: Any) -> bool:
    if not isinstance(value, str):
        return False
    try:
        return str(UUID(value)) == value.lower()
    except (TypeError, ValueError, AttributeError):
        return False


def _optional_uuid(value: Any) -> UUID | None:
    if not isinstance(value, str):
        return None
    try:
        return UUID(value)
    except (TypeError, ValueError, AttributeError):
        return None


def _safe_claim_kind(value: Any) -> str:
    allowed = {item.value for item in AgentClaimKind}
    return value if isinstance(value, str) and value in allowed else "UNKNOWN"


def _safe_claim_status(value: Any) -> str:
    allowed = {item.value for item in ClaimLedgerValidationStatus}
    return value if isinstance(value, str) and value in allowed else "UNKNOWN"


def _evidence_read_payload(row: ControlEvidenceReadRow) -> dict[str, Any]:
    return {
        "id": row.id,
        "query_manifest_id": row.query_manifest_id,
        "run_id": row.run_id,
        "company_id": row.company_id,
        "project_id": row.project_id,
        "ordinal": row.ordinal,
        "source_store": row.source_store,
        "reference_uri": row.reference_uri,
        "model_visible": row.model_visible,
        "query_manifest_sha256": row.query_manifest_sha256,
        "recorded_at": _aware(row.recorded_at).isoformat(),
    }


def _aware(value: datetime) -> datetime:
    return value.replace(tzinfo=UTC) if value.tzinfo is None else value.astimezone(UTC)


def _now() -> datetime:
    return datetime.now(UTC)


def _enable_sqlite_foreign_keys(connection: object, _: object) -> None:
    cursor = connection.cursor()  # type: ignore[attr-defined]
    cursor.execute("PRAGMA foreign_keys=ON")
    cursor.close()


def _install_sqlite_immutability_triggers(engine: Engine) -> None:
    if engine.dialect.name != "sqlite":
        return
    statements = (
        "CREATE TRIGGER IF NOT EXISTS trg_control_routes_no_update "
        "BEFORE UPDATE ON control_task_routes "
        "BEGIN SELECT RAISE(ABORT, 'immutable control route'); END",
        "CREATE TRIGGER IF NOT EXISTS trg_control_routes_no_delete "
        "BEFORE DELETE ON control_task_routes "
        "BEGIN SELECT RAISE(ABORT, 'immutable control route'); END",
        "CREATE TRIGGER IF NOT EXISTS trg_control_audit_no_update "
        "BEFORE UPDATE ON control_audit_index "
        "BEGIN SELECT RAISE(ABORT, 'immutable control audit'); END",
        "CREATE TRIGGER IF NOT EXISTS trg_control_audit_no_delete "
        "BEFORE DELETE ON control_audit_index "
        "BEGIN SELECT RAISE(ABORT, 'immutable control audit'); END",
        "CREATE TRIGGER IF NOT EXISTS trg_control_claims_no_update "
        "BEFORE UPDATE ON control_claim_ledger "
        "BEGIN SELECT RAISE(ABORT, 'immutable control claim'); END",
        "CREATE TRIGGER IF NOT EXISTS trg_control_claims_no_delete "
        "BEFORE DELETE ON control_claim_ledger "
        "BEGIN SELECT RAISE(ABORT, 'immutable control claim'); END",
        "CREATE TRIGGER IF NOT EXISTS trg_control_query_manifests_no_update "
        "BEFORE UPDATE ON control_query_manifests "
        "BEGIN SELECT RAISE(ABORT, 'immutable control query manifest'); END",
        "CREATE TRIGGER IF NOT EXISTS trg_control_query_manifests_no_delete "
        "BEFORE DELETE ON control_query_manifests "
        "BEGIN SELECT RAISE(ABORT, 'immutable control query manifest'); END",
        "CREATE TRIGGER IF NOT EXISTS trg_control_evidence_reads_no_update "
        "BEFORE UPDATE ON control_evidence_reads "
        "BEGIN SELECT RAISE(ABORT, 'immutable control evidence read'); END",
        "CREATE TRIGGER IF NOT EXISTS trg_control_evidence_reads_no_delete "
        "BEFORE DELETE ON control_evidence_reads "
        "BEGIN SELECT RAISE(ABORT, 'immutable control evidence read'); END",
        "CREATE TRIGGER IF NOT EXISTS trg_control_candidate_events_no_update "
        "BEFORE UPDATE ON control_potential_candidate_events "
        "BEGIN SELECT RAISE(ABORT, 'immutable potential candidate event'); END",
        "CREATE TRIGGER IF NOT EXISTS trg_control_candidate_events_no_delete "
        "BEFORE DELETE ON control_potential_candidate_events "
        "BEGIN SELECT RAISE(ABORT, 'immutable potential candidate event'); END",
    )
    with engine.begin() as connection:
        for statement in statements:
            connection.exec_driver_sql(statement)


def _candidate_view(row: ControlPotentialCandidateRow) -> ControlPotentialCandidateView:
    return ControlPotentialCandidateView(
        id=UUID(row.id),
        company_id=UUID(row.company_id),
        project_id=UUID(row.project_id),
        source_run_id=UUID(row.source_run_id),
        source_step_id=UUID(row.source_step_id),
        version=row.version,
        candidate=PotentialCandidateDraft.model_validate(row.payload),
        payload_sha256=row.payload_sha256,
        status=PotentialCandidateStatus(row.status),
        potential_record_id=(UUID(row.potential_record_id) if row.potential_record_id else None),
        reviewed_by=row.reviewed_by,
        reviewed_at=row.reviewed_at,
        created_at=row.created_at,
        updated_at=row.updated_at,
    )


def _candidate_operation_id(row: ControlPotentialCandidateRow) -> str:
    return f"potential-accept:{row.id}:v{row.version}:{row.payload_sha256[:32]}"


def validate_candidate_observation_sources(
    candidate: PotentialCandidateDraft, observation_session: Session
) -> str | None:
    """Return a safe failure code if any cited observation is not current and exact."""
    from enterprise_insight_backend.observations import (
        ManagementObservationRow,
        ObservationVersionRow,
    )

    evidence_items = [*candidate.supporting_evidence, *candidate.counterevidence]
    if not candidate.supporting_evidence:
        return "SUPPORTING_EVIDENCE_REQUIRED"
    for evidence in evidence_items:
        match = re.fullmatch(r"observation://([^/]+)/([1-9][0-9]*)", evidence.source_ref)
        if match is None:
            return "INVALID_OBSERVATION_SOURCE_REF"
        try:
            observation_id = UUID(match.group(1))
            revision = int(match.group(2))
        except (ValueError, OverflowError):
            return "INVALID_OBSERVATION_SOURCE_REF"
        if str(observation_id) != match.group(1).lower():
            return "INVALID_OBSERVATION_SOURCE_REF"
        version = observation_session.scalar(
            select(ObservationVersionRow)
            .join(
                ManagementObservationRow,
                ManagementObservationRow.id == ObservationVersionRow.observation_id,
            )
            .where(
                ObservationVersionRow.observation_id == str(observation_id),
                ObservationVersionRow.revision == revision,
                ManagementObservationRow.company_id == str(candidate.company_id),
                ManagementObservationRow.project_id == str(candidate.project_id),
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
    return None


def _actor_id(value: str) -> str:
    actor = value.strip()
    if not actor or len(actor) > 128:
        raise DomainError("CONTROL_ACTOR_INVALID", "操作者标识无效。", status_code=422)
    return actor
