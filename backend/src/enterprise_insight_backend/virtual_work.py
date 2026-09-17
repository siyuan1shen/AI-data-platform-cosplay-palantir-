from __future__ import annotations

import json
from collections import defaultdict
from collections.abc import Callable
from datetime import UTC, datetime
from enum import StrEnum
from hashlib import sha256
from typing import Any
from uuid import UUID, uuid4

from pydantic import BaseModel, ConfigDict, Field, JsonValue, field_validator, model_validator
from sqlalchemy import (
    DDL,
    JSON,
    DateTime,
    ForeignKey,
    ForeignKeyConstraint,
    Index,
    Integer,
    String,
    Text,
    UniqueConstraint,
    event,
    select,
)
from sqlalchemy.orm import DeclarativeBase, Mapped, Session, mapped_column

from enterprise_insight_backend.errors import DomainError
from enterprise_insight_backend.observations import ObservationBase


class Base(DeclarativeBase):
    """Virtual-work tables share observations.db metadata and physical storage."""

    metadata = ObservationBase.metadata


class VirtualNodeType(StrEnum):
    DEPARTMENT = "DEPARTMENT"
    POSITION = "POSITION"
    PERSON = "PERSON"
    ROLE_ASSIGNMENT = "ROLE_ASSIGNMENT"
    RESPONSIBILITY = "RESPONSIBILITY"
    ACTIVITY = "ACTIVITY"
    WORK_INSTANCE = "WORK_INSTANCE"
    PROCESS = "PROCESS"
    SYSTEM = "SYSTEM"
    TOOL = "TOOL"


class VirtualEdgeType(StrEnum):
    REPORTS_TO = "REPORTS_TO"
    ASSIGNMENT_PERSON = "ASSIGNMENT_PERSON"
    ASSIGNMENT_POSITION = "ASSIGNMENT_POSITION"
    POSITION_OWNS_RESPONSIBILITY = "POSITION_OWNS_RESPONSIBILITY"
    POSITION_PERFORMS_ACTIVITY = "POSITION_PERFORMS_ACTIVITY"
    POSITION_PERFORMS_WORK = "POSITION_PERFORMS_WORK"
    ASSIGNMENT_PERFORMS_ACTIVITY = "ASSIGNMENT_PERFORMS_ACTIVITY"
    ASSIGNMENT_PERFORMS_WORK = "ASSIGNMENT_PERFORMS_WORK"
    COLLABORATES_WITH = "COLLABORATES_WITH"
    HANDOFF = "HANDOFF"
    PRECEDES = "PRECEDES"
    DEPENDS_ON = "DEPENDS_ON"
    PRODUCES = "PRODUCES"
    CONSUMES = "CONSUMES"
    USES = "USES"
    SUPPORTS = "SUPPORTS"
    REVIEWS = "REVIEWS"
    RELATED_TO = "RELATED_TO"


class AssertionKind(StrEnum):
    NORMATIVE = "NORMATIVE"
    OBSERVED_PRACTICE = "OBSERVED_PRACTICE"
    SYSTEM_EVENT = "SYSTEM_EVENT"
    REPORTED = "REPORTED"
    INFERRED = "INFERRED"


class EvidenceKind(StrEnum):
    NORMATIVE_DOCUMENT = "NORMATIVE_DOCUMENT"
    DIRECT_OBSERVATION = "DIRECT_OBSERVATION"
    WORK_SAMPLE = "WORK_SAMPLE"
    SYSTEM_EVENT = "SYSTEM_EVENT"
    INTERVIEW = "INTERVIEW"
    SURVEY = "SURVEY"
    MEETING_NOTE = "MEETING_NOTE"
    REPORT = "REPORT"
    INFERENCE_BASIS = "INFERENCE_BASIS"


class AnchorRelationKind(StrEnum):
    REFINES = "REFINES"
    CORRESPONDS_TO = "CORRESPONDS_TO"
    EXPLAINS = "EXPLAINS"


class AnchorStatus(StrEnum):
    ACTIVE = "ACTIVE"
    STALE = "STALE"
    WITHDRAWN = "WITHDRAWN"


class VirtualRevisionStatus(StrEnum):
    DRAFT = "DRAFT"
    REVIEWED = "REVIEWED"
    REJECTED = "REJECTED"
    WITHDRAWN = "WITHDRAWN"


class ReviewDecision(StrEnum):
    REVIEWED = "REVIEWED"
    REJECTED = "REJECTED"
    WITHDRAWN = "WITHDRAWN"


class ConfirmationKind(StrEnum):
    HUMAN_REVIEW = "HUMAN_REVIEW"
    FDE_VALIDATION = "FDE_VALIDATION"
    MANAGER_CONFIRMATION = "MANAGER_CONFIRMATION"


_ALLOWED_EVIDENCE: dict[AssertionKind, set[EvidenceKind]] = {
    AssertionKind.NORMATIVE: {EvidenceKind.NORMATIVE_DOCUMENT},
    AssertionKind.OBSERVED_PRACTICE: {
        EvidenceKind.DIRECT_OBSERVATION,
        EvidenceKind.WORK_SAMPLE,
    },
    AssertionKind.SYSTEM_EVENT: {EvidenceKind.SYSTEM_EVENT},
    AssertionKind.REPORTED: {
        EvidenceKind.INTERVIEW,
        EvidenceKind.SURVEY,
        EvidenceKind.MEETING_NOTE,
        EvidenceKind.REPORT,
    },
}


class VirtualWorkInput(BaseModel):
    model_config = ConfigDict(extra="forbid")


class VirtualWorkOutput(BaseModel):
    model_config = ConfigDict(extra="forbid", from_attributes=True)


def _require_aware(value: datetime | None, field_name: str) -> datetime | None:
    if value is not None and (value.tzinfo is None or value.utcoffset() is None):
        raise ValueError(f"{field_name} 必须包含时区。")
    return value


def _normalize_valid_time(value: datetime | None) -> datetime | None:
    if value is None:
        return None
    if value.tzinfo is None or value.utcoffset() is None:
        raise ValueError("有效时间必须包含时区。")
    return value.astimezone(UTC)


def _read_valid_time(value: datetime | None) -> datetime | None:
    if value is None:
        return None
    # SQLite drops tzinfo for DateTime(timezone=True); stored values are normalized to UTC.
    if value.tzinfo is None or value.utcoffset() is None:
        return value.replace(tzinfo=UTC)
    return value.astimezone(UTC)


class EvidenceCreate(VirtualWorkInput):
    evidence_kind: EvidenceKind
    source_ref: str = Field(min_length=1, max_length=500)
    excerpt: str = Field(min_length=1, max_length=12_000)
    source_root_id: UUID = Field(default_factory=uuid4)
    captured_at: datetime | None = None

    @field_validator("source_ref", "excerpt")
    @classmethod
    def strip_required_text(cls, value: str) -> str:
        value = value.strip()
        if not value:
            raise ValueError("证据来源和摘录不能为空。")
        return value

    @field_validator("captured_at")
    @classmethod
    def validate_captured_at(cls, value: datetime | None) -> datetime | None:
        return _require_aware(value, "captured_at")

    @model_validator(mode="after")
    def system_event_requires_time(self) -> EvidenceCreate:
        if self.evidence_kind is EvidenceKind.SYSTEM_EVENT and self.captured_at is None:
            raise ValueError("系统事件证据必须提供带时区的发生时间。")
        return self


class FieldAssertionCreate(VirtualWorkInput):
    field_name: str = Field(min_length=1, max_length=160, pattern=r"^[a-zA-Z0-9_.:-]+$")
    value: JsonValue
    assertion_kind: AssertionKind
    scope: dict[str, JsonValue] = Field(default_factory=dict)
    valid_from: datetime | None = None
    valid_to: datetime | None = None
    evidence: list[EvidenceCreate] = Field(min_length=1, max_length=50)
    method: str | None = Field(default=None, max_length=2_000)

    @field_validator("field_name", "method")
    @classmethod
    def normalize_text(cls, value: str | None) -> str | None:
        if value is None:
            return None
        value = value.strip()
        return value or None

    @field_validator("valid_from", "valid_to")
    @classmethod
    def valid_times_must_include_timezone(cls, value: datetime | None) -> datetime | None:
        return _require_aware(value, "valid_from/valid_to")

    @model_validator(mode="after")
    def valid_time_range_must_be_ordered(self) -> FieldAssertionCreate:
        if (
            self.valid_from is not None
            and self.valid_to is not None
            and self.valid_to.astimezone(UTC) <= self.valid_from.astimezone(UTC)
        ):
            raise ValueError("valid_to 必须晚于 valid_from。")
        return self

    @model_validator(mode="after")
    def evidence_matches_assertion_kind(self) -> FieldAssertionCreate:
        kinds = {item.evidence_kind for item in self.evidence}
        if self.assertion_kind is AssertionKind.INFERRED:
            if EvidenceKind.INFERENCE_BASIS not in kinds:
                raise ValueError("INFERRED 断言必须包含 INFERENCE_BASIS 证据。")
            if not (kinds - {EvidenceKind.INFERENCE_BASIS}):
                raise ValueError("INFERRED 断言还必须引用至少一项原始依据证据。")
            if not self.method:
                raise ValueError("INFERRED 断言必须说明推导方法。")
        else:
            allowed = _ALLOWED_EVIDENCE[self.assertion_kind]
            if not kinds or not kinds.issubset(allowed):
                expected = ", ".join(sorted(item.value for item in allowed))
                raise ValueError(
                    f"{self.assertion_kind.value} 断言需要匹配的证据类型：{expected}。"
                )
        return self


class RealAnchorCreate(VirtualWorkInput):
    real_entity_id: UUID
    real_release_id: UUID
    relation_kind: AnchorRelationKind
    support_ref: str = Field(min_length=1, max_length=500)

    @field_validator("support_ref")
    @classmethod
    def normalize_support_ref(cls, value: str) -> str:
        value = value.strip()
        if not value:
            raise ValueError("锚点依据不能为空。")
        return value


class ConfirmedScope(VirtualWorkInput):
    company_id: UUID
    project_id: UUID
    virtual_work_model_id: UUID


class VirtualWorkReviewCreate(VirtualWorkInput):
    revision_id: UUID
    version: int = Field(ge=1)
    revision_hash: str = Field(pattern=r"^[0-9a-f]{64}$")
    confirmed_scope: ConfirmedScope
    confirmation_kind: ConfirmationKind
    decision: ReviewDecision
    reason: str | None = Field(default=None, max_length=4_000)

    @field_validator("reason")
    @classmethod
    def normalize_reason(cls, value: str | None) -> str | None:
        if value is None:
            return None
        value = value.strip()
        return value or None


class VirtualWorkModelCreate(VirtualWorkInput):
    company_id: UUID
    project_id: UUID
    name: str = Field(min_length=1, max_length=200)
    description: str | None = Field(default=None, max_length=4_000)

    @field_validator("name", "description")
    @classmethod
    def normalize_name_and_description(cls, value: str | None) -> str | None:
        if value is None:
            return None
        value = value.strip()
        if not value and value is not None:
            raise ValueError("名称不能为空，描述应为非空文本或 null。")
        return value


class VirtualNodeCreate(VirtualWorkInput):
    node_type: VirtualNodeType
    label: str = Field(min_length=1, max_length=300)
    properties: dict[str, JsonValue] = Field(default_factory=dict)
    assertions: list[FieldAssertionCreate] = Field(min_length=1, max_length=200)
    anchors: list[RealAnchorCreate] = Field(default_factory=list, max_length=50)

    @field_validator("label")
    @classmethod
    def normalize_label(cls, value: str) -> str:
        value = value.strip()
        if not value:
            raise ValueError("节点名称不能为空。")
        return value

    @model_validator(mode="after")
    def anchors_are_unique(self) -> VirtualNodeCreate:
        keys = [
            (item.real_entity_id, item.real_release_id, item.relation_kind) for item in self.anchors
        ]
        if len(keys) != len(set(keys)):
            raise ValueError("同一节点不能重复添加相同的正式实体锚点。")
        return self


class VirtualEdgeCreate(VirtualWorkInput):
    source_node_id: UUID
    target_node_id: UUID
    edge_type: VirtualEdgeType
    label: str | None = Field(default=None, max_length=300)
    properties: dict[str, JsonValue] = Field(default_factory=dict)
    assertions: list[FieldAssertionCreate] = Field(min_length=1, max_length=200)

    @field_validator("label")
    @classmethod
    def normalize_label(cls, value: str | None) -> str | None:
        if value is None:
            return None
        value = value.strip()
        return value or None


class VirtualNodeUpdate(VirtualNodeCreate):
    expected_version: int = Field(ge=1)
    expected_revision_hash: str = Field(pattern=r"^[0-9a-f]{64}$")
    reason: str = Field(min_length=1, max_length=2_000)

    @field_validator("reason")
    @classmethod
    def normalize_reason(cls, value: str) -> str:
        value = value.strip()
        if not value:
            raise ValueError("更新原因不能为空。")
        return value


class VirtualEdgeUpdate(VirtualEdgeCreate):
    expected_version: int = Field(ge=1)
    expected_revision_hash: str = Field(pattern=r"^[0-9a-f]{64}$")
    reason: str = Field(min_length=1, max_length=2_000)

    @field_validator("reason")
    @classmethod
    def normalize_reason(cls, value: str) -> str:
        value = value.strip()
        if not value:
            raise ValueError("更新原因不能为空。")
        return value


class VirtualWorkRetireCreate(VirtualWorkInput):
    expected_version: int = Field(ge=1)
    expected_revision_hash: str = Field(pattern=r"^[0-9a-f]{64}$")
    reason: str = Field(min_length=1, max_length=2_000)

    @field_validator("reason")
    @classmethod
    def normalize_reason(cls, value: str) -> str:
        value = value.strip()
        if not value:
            raise ValueError("退役原因不能为空。")
        return value


class VirtualWorkModelRow(Base):
    __tablename__ = "virtual_work_models"
    __table_args__ = (Index("ix_virtual_work_model_scope", "company_id", "project_id"),)

    id: Mapped[str] = mapped_column(String(36), primary_key=True)
    company_id: Mapped[str] = mapped_column(String(36), nullable=False)
    project_id: Mapped[str] = mapped_column(String(36), nullable=False)
    created_at: Mapped[datetime] = mapped_column(DateTime(timezone=True), nullable=False)


class VirtualWorkRevisionRow(Base):
    __tablename__ = "virtual_work_revisions"
    __table_args__ = (
        UniqueConstraint("model_id", "version", name="uq_virtual_work_revision_version"),
        UniqueConstraint("model_id", "id", name="uq_virtual_work_revision_model_id"),
        Index("ix_virtual_work_revision_model_version", "model_id", "version"),
    )

    id: Mapped[str] = mapped_column(String(36), primary_key=True)
    model_id: Mapped[str] = mapped_column(
        String(36), ForeignKey("virtual_work_models.id", ondelete="RESTRICT"), nullable=False
    )
    version: Mapped[int] = mapped_column(Integer, nullable=False)
    name: Mapped[str] = mapped_column(String(200), nullable=False)
    description: Mapped[str | None] = mapped_column(Text)
    operation: Mapped[str] = mapped_column(String(40), nullable=False)
    change_summary: Mapped[str] = mapped_column(String(2_000), nullable=False)
    change_payload: Mapped[dict[str, Any]] = mapped_column(JSON, nullable=False, default=dict)
    revision_hash: Mapped[str] = mapped_column(String(64), nullable=False)
    created_by: Mapped[str] = mapped_column(String(128), nullable=False)
    created_at: Mapped[datetime] = mapped_column(DateTime(timezone=True), nullable=False)


class VirtualWorkNodeRow(Base):
    __tablename__ = "virtual_work_nodes"
    __table_args__ = (
        ForeignKeyConstraint(
            ["model_id", "revision_id"],
            ["virtual_work_revisions.model_id", "virtual_work_revisions.id"],
            ondelete="RESTRICT",
            name="fk_virtual_node_revision_scope",
        ),
        Index("ix_virtual_work_node_revision_type", "model_id", "revision_id", "node_type"),
    )

    model_id: Mapped[str] = mapped_column(String(36), primary_key=True)
    revision_id: Mapped[str] = mapped_column(String(36), primary_key=True)
    id: Mapped[str] = mapped_column(String(36), primary_key=True)
    node_type: Mapped[str] = mapped_column(String(40), nullable=False)
    label: Mapped[str] = mapped_column(String(300), nullable=False)
    properties: Mapped[dict[str, Any]] = mapped_column(JSON, nullable=False, default=dict)
    created_at: Mapped[datetime] = mapped_column(DateTime(timezone=True), nullable=False)


class VirtualWorkEdgeRow(Base):
    __tablename__ = "virtual_work_edges"
    __table_args__ = (
        ForeignKeyConstraint(
            ["model_id", "revision_id"],
            ["virtual_work_revisions.model_id", "virtual_work_revisions.id"],
            ondelete="RESTRICT",
            name="fk_virtual_edge_revision_scope",
        ),
        ForeignKeyConstraint(
            ["model_id", "revision_id", "source_node_id"],
            [
                "virtual_work_nodes.model_id",
                "virtual_work_nodes.revision_id",
                "virtual_work_nodes.id",
            ],
            ondelete="RESTRICT",
            name="fk_virtual_edge_source_same_revision",
        ),
        ForeignKeyConstraint(
            ["model_id", "revision_id", "target_node_id"],
            [
                "virtual_work_nodes.model_id",
                "virtual_work_nodes.revision_id",
                "virtual_work_nodes.id",
            ],
            ondelete="RESTRICT",
            name="fk_virtual_edge_target_same_revision",
        ),
        Index("ix_virtual_work_edge_source", "model_id", "revision_id", "source_node_id"),
        Index("ix_virtual_work_edge_target", "model_id", "revision_id", "target_node_id"),
    )

    model_id: Mapped[str] = mapped_column(String(36), primary_key=True)
    revision_id: Mapped[str] = mapped_column(String(36), primary_key=True)
    id: Mapped[str] = mapped_column(String(36), primary_key=True)
    source_node_id: Mapped[str] = mapped_column(String(36), nullable=False)
    target_node_id: Mapped[str] = mapped_column(String(36), nullable=False)
    edge_type: Mapped[str] = mapped_column(String(48), nullable=False)
    label: Mapped[str | None] = mapped_column(String(300))
    properties: Mapped[dict[str, Any]] = mapped_column(JSON, nullable=False, default=dict)
    created_at: Mapped[datetime] = mapped_column(DateTime(timezone=True), nullable=False)


class VirtualWorkEvidenceRow(Base):
    __tablename__ = "virtual_work_evidence"
    __table_args__ = (
        ForeignKeyConstraint(
            ["model_id", "revision_id"],
            ["virtual_work_revisions.model_id", "virtual_work_revisions.id"],
            ondelete="RESTRICT",
            name="fk_virtual_evidence_revision_scope",
        ),
        Index("ix_virtual_work_evidence_revision_kind", "model_id", "revision_id", "evidence_kind"),
        Index("ix_virtual_work_evidence_source_root", "source_root_id"),
    )

    model_id: Mapped[str] = mapped_column(String(36), primary_key=True)
    revision_id: Mapped[str] = mapped_column(String(36), primary_key=True)
    id: Mapped[str] = mapped_column(String(36), primary_key=True)
    evidence_kind: Mapped[str] = mapped_column(String(40), nullable=False)
    source_ref: Mapped[str] = mapped_column(String(500), nullable=False)
    excerpt: Mapped[str] = mapped_column(Text, nullable=False)
    source_root_id: Mapped[str] = mapped_column(String(36), nullable=False)
    captured_at: Mapped[datetime | None] = mapped_column(DateTime(timezone=True))
    created_at: Mapped[datetime] = mapped_column(DateTime(timezone=True), nullable=False)


class VirtualWorkAssertionRow(Base):
    __tablename__ = "virtual_work_assertions"
    __table_args__ = (
        ForeignKeyConstraint(
            ["model_id", "revision_id"],
            ["virtual_work_revisions.model_id", "virtual_work_revisions.id"],
            ondelete="RESTRICT",
            name="fk_virtual_assertion_revision_scope",
        ),
        Index(
            "ix_virtual_work_assertion_subject",
            "model_id",
            "revision_id",
            "subject_kind",
            "subject_id",
        ),
    )

    model_id: Mapped[str] = mapped_column(String(36), primary_key=True)
    revision_id: Mapped[str] = mapped_column(String(36), primary_key=True)
    id: Mapped[str] = mapped_column(String(36), primary_key=True)
    subject_kind: Mapped[str] = mapped_column(String(16), nullable=False)
    subject_id: Mapped[str] = mapped_column(String(36), nullable=False)
    field_name: Mapped[str] = mapped_column(String(160), nullable=False)
    value_json: Mapped[Any] = mapped_column(JSON, nullable=False)
    assertion_kind: Mapped[str] = mapped_column(String(32), nullable=False)
    scope: Mapped[dict[str, Any]] = mapped_column(JSON, nullable=False, default=dict)
    valid_from: Mapped[datetime | None] = mapped_column(DateTime(timezone=True))
    valid_to: Mapped[datetime | None] = mapped_column(DateTime(timezone=True))
    method: Mapped[str | None] = mapped_column(String(2_000))
    created_at: Mapped[datetime] = mapped_column(DateTime(timezone=True), nullable=False)


class VirtualWorkAssertionEvidenceRow(Base):
    __tablename__ = "virtual_work_assertion_evidence"
    __table_args__ = (
        ForeignKeyConstraint(
            ["model_id", "revision_id", "assertion_id"],
            [
                "virtual_work_assertions.model_id",
                "virtual_work_assertions.revision_id",
                "virtual_work_assertions.id",
            ],
            ondelete="RESTRICT",
            name="fk_virtual_assertion_evidence_assertion_scope",
        ),
        ForeignKeyConstraint(
            ["model_id", "revision_id", "evidence_id"],
            [
                "virtual_work_evidence.model_id",
                "virtual_work_evidence.revision_id",
                "virtual_work_evidence.id",
            ],
            ondelete="RESTRICT",
            name="fk_virtual_assertion_evidence_evidence_scope",
        ),
    )

    model_id: Mapped[str] = mapped_column(String(36), primary_key=True)
    revision_id: Mapped[str] = mapped_column(String(36), primary_key=True)
    assertion_id: Mapped[str] = mapped_column(String(36), primary_key=True)
    evidence_id: Mapped[str] = mapped_column(String(36), primary_key=True)


class RealAnchorRow(Base):
    __tablename__ = "virtual_work_real_anchors"
    __table_args__ = (
        ForeignKeyConstraint(
            ["model_id", "revision_id", "virtual_node_id"],
            [
                "virtual_work_nodes.model_id",
                "virtual_work_nodes.revision_id",
                "virtual_work_nodes.id",
            ],
            ondelete="RESTRICT",
            name="fk_virtual_anchor_node_scope",
        ),
        Index("ix_virtual_anchor_formal_ref", "real_entity_id", "real_release_id"),
    )

    model_id: Mapped[str] = mapped_column(String(36), primary_key=True)
    revision_id: Mapped[str] = mapped_column(String(36), primary_key=True)
    id: Mapped[str] = mapped_column(String(36), primary_key=True)
    virtual_node_id: Mapped[str] = mapped_column(String(36), nullable=False)
    real_entity_id: Mapped[str] = mapped_column(String(36), nullable=False)
    real_release_id: Mapped[str] = mapped_column(String(36), nullable=False)
    relation_kind: Mapped[str] = mapped_column(String(32), nullable=False)
    support_ref: Mapped[str] = mapped_column(String(500), nullable=False)
    status: Mapped[str] = mapped_column(
        String(24), nullable=False, default=AnchorStatus.ACTIVE.value
    )
    created_at: Mapped[datetime] = mapped_column(DateTime(timezone=True), nullable=False)


class VirtualWorkReviewRow(Base):
    __tablename__ = "virtual_work_reviews"
    __table_args__ = (
        ForeignKeyConstraint(
            ["model_id", "revision_id"],
            ["virtual_work_revisions.model_id", "virtual_work_revisions.id"],
            ondelete="RESTRICT",
            name="fk_virtual_review_revision_scope",
        ),
        UniqueConstraint("revision_id", "sequence", name="uq_virtual_work_review_sequence"),
        Index("ix_virtual_work_review_revision_sequence", "model_id", "revision_id", "sequence"),
    )

    model_id: Mapped[str] = mapped_column(String(36), primary_key=True)
    revision_id: Mapped[str] = mapped_column(String(36), primary_key=True)
    id: Mapped[str] = mapped_column(String(36), primary_key=True)
    sequence: Mapped[int] = mapped_column(Integer, nullable=False)
    version: Mapped[int] = mapped_column(Integer, nullable=False)
    revision_hash: Mapped[str] = mapped_column(String(64), nullable=False)
    confirmed_scope: Mapped[dict[str, str]] = mapped_column(JSON, nullable=False)
    confirmation_kind: Mapped[str] = mapped_column(String(40), nullable=False)
    decision: Mapped[str] = mapped_column(String(24), nullable=False)
    actor_id: Mapped[str] = mapped_column(String(128), nullable=False)
    reason: Mapped[str | None] = mapped_column(String(4_000))
    created_at: Mapped[datetime] = mapped_column(DateTime(timezone=True), nullable=False)


class EvidenceView(VirtualWorkOutput):
    id: UUID
    evidence_kind: EvidenceKind
    source_ref: str
    excerpt: str
    source_root_id: UUID
    captured_at: datetime | None


class FieldAssertionView(VirtualWorkOutput):
    id: UUID
    field_name: str
    value: JsonValue
    assertion_kind: AssertionKind
    scope: dict[str, JsonValue]
    valid_from: datetime | None
    valid_to: datetime | None
    method: str | None
    evidence: list[EvidenceView]


class RealAnchorView(VirtualWorkOutput):
    id: UUID
    virtual_node_id: UUID
    real_entity_id: UUID
    real_release_id: UUID
    relation_kind: AnchorRelationKind
    support_ref: str
    status: AnchorStatus


class VirtualNodeView(VirtualWorkOutput):
    id: UUID
    node_type: VirtualNodeType
    label: str
    properties: dict[str, JsonValue]
    assertions: list[FieldAssertionView]
    anchors: list[RealAnchorView]


class VirtualEdgeView(VirtualWorkOutput):
    id: UUID
    source_node_id: UUID
    target_node_id: UUID
    edge_type: VirtualEdgeType
    label: str | None
    properties: dict[str, JsonValue]
    assertions: list[FieldAssertionView]


class VirtualWorkReviewView(VirtualWorkOutput):
    id: UUID
    revision_id: UUID
    version: int
    revision_hash: str
    confirmed_scope: ConfirmedScope
    confirmation_kind: ConfirmationKind
    decision: ReviewDecision
    actor_id: str
    reason: str | None
    created_at: datetime


class VirtualWorkRevisionView(VirtualWorkOutput):
    virtual_work_model_id: UUID
    company_id: UUID
    project_id: UUID
    revision_id: UUID
    version: int = Field(ge=1)
    status: VirtualRevisionStatus
    revision_hash: str = Field(pattern=r"^[0-9a-f]{64}$")
    name: str
    description: str | None
    operation: str
    change_summary: str
    change_payload: dict[str, JsonValue]
    created_by: str
    created_at: datetime
    review_records: list[VirtualWorkReviewView]
    nodes: list[VirtualNodeView]
    edges: list[VirtualEdgeView]


class VirtualWorkModelSummary(VirtualWorkOutput):
    id: UUID
    company_id: UUID
    project_id: UUID
    name: str
    description: str | None
    version: int = Field(ge=1)
    created_at: datetime


FormalAnchorScopeCheck = Callable[[UUID, UUID, UUID, UUID], bool]
ProjectScopeCheck = Callable[[UUID, UUID], bool]
EvidenceSourceCheck = Callable[[UUID, UUID, EvidenceKind, str, str], bool]


_EDGE_ENDPOINTS: dict[VirtualEdgeType, tuple[set[VirtualNodeType], set[VirtualNodeType]]] = {
    VirtualEdgeType.REPORTS_TO: ({VirtualNodeType.POSITION}, {VirtualNodeType.POSITION}),
    VirtualEdgeType.ASSIGNMENT_PERSON: (
        {VirtualNodeType.ROLE_ASSIGNMENT},
        {VirtualNodeType.PERSON},
    ),
    VirtualEdgeType.ASSIGNMENT_POSITION: (
        {VirtualNodeType.ROLE_ASSIGNMENT},
        {VirtualNodeType.POSITION},
    ),
    VirtualEdgeType.POSITION_OWNS_RESPONSIBILITY: (
        {VirtualNodeType.POSITION},
        {VirtualNodeType.RESPONSIBILITY},
    ),
    VirtualEdgeType.POSITION_PERFORMS_ACTIVITY: (
        {VirtualNodeType.POSITION},
        {VirtualNodeType.ACTIVITY},
    ),
    VirtualEdgeType.POSITION_PERFORMS_WORK: (
        {VirtualNodeType.POSITION},
        {VirtualNodeType.WORK_INSTANCE},
    ),
    VirtualEdgeType.ASSIGNMENT_PERFORMS_ACTIVITY: (
        {VirtualNodeType.ROLE_ASSIGNMENT},
        {VirtualNodeType.ACTIVITY},
    ),
    VirtualEdgeType.ASSIGNMENT_PERFORMS_WORK: (
        {VirtualNodeType.ROLE_ASSIGNMENT},
        {VirtualNodeType.WORK_INSTANCE},
    ),
    VirtualEdgeType.COLLABORATES_WITH: (
        {VirtualNodeType.POSITION, VirtualNodeType.ROLE_ASSIGNMENT},
        {VirtualNodeType.POSITION, VirtualNodeType.ROLE_ASSIGNMENT},
    ),
    VirtualEdgeType.HANDOFF: (
        {
            VirtualNodeType.POSITION,
            VirtualNodeType.ROLE_ASSIGNMENT,
            VirtualNodeType.ACTIVITY,
            VirtualNodeType.WORK_INSTANCE,
        },
        {
            VirtualNodeType.POSITION,
            VirtualNodeType.ROLE_ASSIGNMENT,
            VirtualNodeType.ACTIVITY,
            VirtualNodeType.WORK_INSTANCE,
        },
    ),
    VirtualEdgeType.PRECEDES: (
        {VirtualNodeType.ACTIVITY, VirtualNodeType.WORK_INSTANCE, VirtualNodeType.PROCESS},
        {VirtualNodeType.ACTIVITY, VirtualNodeType.WORK_INSTANCE, VirtualNodeType.PROCESS},
    ),
    VirtualEdgeType.DEPENDS_ON: (
        set(VirtualNodeType),
        set(VirtualNodeType),
    ),
    VirtualEdgeType.PRODUCES: (
        {VirtualNodeType.ACTIVITY, VirtualNodeType.WORK_INSTANCE, VirtualNodeType.PROCESS},
        {VirtualNodeType.ACTIVITY, VirtualNodeType.WORK_INSTANCE, VirtualNodeType.RESPONSIBILITY},
    ),
    VirtualEdgeType.CONSUMES: (
        {VirtualNodeType.ACTIVITY, VirtualNodeType.WORK_INSTANCE, VirtualNodeType.PROCESS},
        {VirtualNodeType.ACTIVITY, VirtualNodeType.WORK_INSTANCE, VirtualNodeType.RESPONSIBILITY},
    ),
    VirtualEdgeType.USES: (
        {
            VirtualNodeType.POSITION,
            VirtualNodeType.ROLE_ASSIGNMENT,
            VirtualNodeType.ACTIVITY,
            VirtualNodeType.WORK_INSTANCE,
        },
        {VirtualNodeType.SYSTEM, VirtualNodeType.TOOL},
    ),
    VirtualEdgeType.SUPPORTS: (
        {VirtualNodeType.SYSTEM, VirtualNodeType.TOOL, VirtualNodeType.POSITION},
        {VirtualNodeType.ACTIVITY, VirtualNodeType.WORK_INSTANCE, VirtualNodeType.RESPONSIBILITY},
    ),
    VirtualEdgeType.REVIEWS: (
        {VirtualNodeType.POSITION, VirtualNodeType.ROLE_ASSIGNMENT},
        {VirtualNodeType.ACTIVITY, VirtualNodeType.WORK_INSTANCE},
    ),
    VirtualEdgeType.RELATED_TO: (set(VirtualNodeType), set(VirtualNodeType)),
}


class VirtualWorkService:
    """Append-only virtual-work graph repository scoped to one company/project."""

    def __init__(
        self,
        session: Session,
        *,
        formal_anchor_scope_check: FormalAnchorScopeCheck | None = None,
        project_scope_check: ProjectScopeCheck | None = None,
        evidence_source_check: EvidenceSourceCheck | None = None,
    ) -> None:
        self.session = session
        self.formal_anchor_scope_check = formal_anchor_scope_check
        self.project_scope_check = project_scope_check
        self.evidence_source_check = evidence_source_check

    def create_model(
        self, payload: VirtualWorkModelCreate, *, actor_id: str
    ) -> VirtualWorkRevisionView:
        actor = self._actor(actor_id)
        if self.project_scope_check and not self.project_scope_check(
            payload.company_id, payload.project_id
        ):
            raise DomainError(
                "VIRTUAL_WORK_PROJECT_OUT_OF_SCOPE",
                "项目不属于指定公司范围。",
                status_code=422,
            )
        now = _now()
        model_id = str(uuid4())
        change_payload = payload.model_dump(mode="json")
        revision = VirtualWorkRevisionRow(
            id=str(uuid4()),
            model_id=model_id,
            version=1,
            name=payload.name,
            description=payload.description,
            operation="CREATED",
            change_summary="建立岗位工作虚模。",
            change_payload=change_payload,
            revision_hash=_revision_hash(model_id, 1, None, "CREATED", change_payload),
            created_by=actor,
            created_at=now,
        )
        with self.session.begin_nested():
            self.session.add(
                VirtualWorkModelRow(
                    id=model_id,
                    company_id=str(payload.company_id),
                    project_id=str(payload.project_id),
                    created_at=now,
                )
            )
            self.session.add(revision)
            self.session.flush()
        return self.get_revision_for_review(
            payload.company_id, payload.project_id, UUID(model_id), version=1
        )

    def add_node(
        self,
        company_id: UUID,
        project_id: UUID,
        model_id: UUID,
        payload: VirtualNodeCreate,
        *,
        actor_id: str,
    ) -> VirtualWorkRevisionView:
        actor = self._actor(actor_id)
        model = self._model(company_id, project_id, model_id)
        previous = self._latest_revision(model.id)
        for anchor in payload.anchors:
            self._verify_anchor(model, anchor)
        self._verify_evidence_sources(company_id, project_id, payload.assertions)

        now = _now()
        node_id = str(uuid4())
        with self.session.begin_nested():
            revision = self._append_revision(
                model,
                previous,
                operation="NODE_ADDED",
                summary=f"新增 {payload.node_type.value} 节点。",
                change_payload={
                    "node_id": node_id,
                    **payload.model_dump(mode="json"),
                },
                actor=actor,
                now=now,
            )
            self._copy_snapshot(model.id, previous.id, revision.id)
            self.session.add(
                VirtualWorkNodeRow(
                    model_id=model.id,
                    revision_id=revision.id,
                    id=node_id,
                    node_type=payload.node_type.value,
                    label=payload.label,
                    properties=payload.properties,
                    created_at=now,
                )
            )
            self._add_assertions(
                model.id,
                revision.id,
                subject_kind="NODE",
                subject_id=node_id,
                assertions=payload.assertions,
                now=now,
            )
            for anchor in payload.anchors:
                self.session.add(
                    RealAnchorRow(
                        model_id=model.id,
                        revision_id=revision.id,
                        id=str(uuid4()),
                        virtual_node_id=node_id,
                        real_entity_id=str(anchor.real_entity_id),
                        real_release_id=str(anchor.real_release_id),
                        relation_kind=anchor.relation_kind.value,
                        support_ref=anchor.support_ref,
                        status=AnchorStatus.ACTIVE.value,
                        created_at=now,
                    )
                )
            self.session.flush()
        return self.get_revision_for_review(
            company_id, project_id, model_id, version=revision.version
        )

    def update_node(
        self,
        company_id: UUID,
        project_id: UUID,
        model_id: UUID,
        node_id: UUID,
        payload: VirtualNodeUpdate,
        *,
        actor_id: str,
    ) -> VirtualWorkRevisionView:
        actor = self._actor(actor_id)
        model = self._model(company_id, project_id, model_id)
        previous = self._latest_revision(model.id)
        self._assert_expected_revision(
            previous, payload.expected_version, payload.expected_revision_hash
        )
        node = self._node_in_revision(model.id, previous.id, node_id)
        self._validate_node_replacement(model.id, previous.id, node.id, payload.node_type)
        for anchor in payload.anchors:
            self._verify_anchor(model, anchor)
        self._verify_evidence_sources(company_id, project_id, payload.assertions)

        now = _now()
        change_payload = payload.model_dump(
            mode="json", exclude={"expected_version", "expected_revision_hash"}
        )
        change_payload["node_id"] = str(node_id)
        with self.session.begin_nested():
            revision = self._append_revision(
                model,
                previous,
                operation="NODE_UPDATED",
                summary=payload.reason,
                change_payload=change_payload,
                actor=actor,
                now=now,
            )
            self._copy_snapshot(
                model.id,
                previous.id,
                revision.id,
                replaced_nodes={node.id: payload},
                replacement_created_at=now,
            )
            self._add_assertions(
                model.id,
                revision.id,
                subject_kind="NODE",
                subject_id=node.id,
                assertions=payload.assertions,
                now=now,
            )
            self.session.add_all(
                [
                    RealAnchorRow(
                        model_id=model.id,
                        revision_id=revision.id,
                        id=str(uuid4()),
                        virtual_node_id=node.id,
                        real_entity_id=str(anchor.real_entity_id),
                        real_release_id=str(anchor.real_release_id),
                        relation_kind=anchor.relation_kind.value,
                        support_ref=anchor.support_ref,
                        status=AnchorStatus.ACTIVE.value,
                        created_at=now,
                    )
                    for anchor in payload.anchors
                ]
            )
            self.session.flush()
        return self.get_revision_for_review(
            company_id, project_id, model_id, version=revision.version
        )

    def retire_node(
        self,
        company_id: UUID,
        project_id: UUID,
        model_id: UUID,
        node_id: UUID,
        payload: VirtualWorkRetireCreate,
        *,
        actor_id: str,
    ) -> VirtualWorkRevisionView:
        actor = self._actor(actor_id)
        model = self._model(company_id, project_id, model_id)
        previous = self._latest_revision(model.id)
        self._assert_expected_revision(
            previous, payload.expected_version, payload.expected_revision_hash
        )
        node = self._node_in_revision(model.id, previous.id, node_id)
        linked_edges = self.session.scalars(
            select(VirtualWorkEdgeRow).where(
                VirtualWorkEdgeRow.model_id == model.id,
                VirtualWorkEdgeRow.revision_id == previous.id,
                (VirtualWorkEdgeRow.source_node_id == node.id)
                | (VirtualWorkEdgeRow.target_node_id == node.id),
            )
        ).all()
        retired_edge_ids = sorted(row.id for row in linked_edges)
        change_payload = {
            "node_id": node.id,
            "retired_linked_edge_ids": retired_edge_ids,
            "reason": payload.reason,
        }
        now = _now()
        with self.session.begin_nested():
            revision = self._append_revision(
                model,
                previous,
                operation="NODE_RETIRED",
                summary=payload.reason,
                change_payload=change_payload,
                actor=actor,
                now=now,
            )
            self._copy_snapshot(
                model.id,
                previous.id,
                revision.id,
                retired_node_ids={node.id},
                excluded_edge_ids=set(retired_edge_ids),
            )
            self.session.flush()
        return self.get_revision_for_review(
            company_id, project_id, model_id, version=revision.version
        )

    def add_edge(
        self,
        company_id: UUID,
        project_id: UUID,
        model_id: UUID,
        payload: VirtualEdgeCreate,
        *,
        actor_id: str,
    ) -> VirtualWorkRevisionView:
        actor = self._actor(actor_id)
        model = self._model(company_id, project_id, model_id)
        previous = self._latest_revision(model.id)
        node_rows = self.session.scalars(
            select(VirtualWorkNodeRow).where(
                VirtualWorkNodeRow.model_id == model.id,
                VirtualWorkNodeRow.revision_id == previous.id,
                VirtualWorkNodeRow.id.in_(
                    [str(payload.source_node_id), str(payload.target_node_id)]
                ),
            )
        ).all()
        endpoint_types = {item.id: VirtualNodeType(item.node_type) for item in node_rows}
        expected_ids = {str(payload.source_node_id), str(payload.target_node_id)}
        if set(endpoint_types) != expected_ids:
            self._raise_missing_or_out_of_scope_nodes(model.id, expected_ids - set(endpoint_types))

        allowed_source, allowed_target = _EDGE_ENDPOINTS[payload.edge_type]
        source_type = endpoint_types[str(payload.source_node_id)]
        target_type = endpoint_types[str(payload.target_node_id)]
        if source_type not in allowed_source or target_type not in allowed_target:
            raise DomainError(
                "VIRTUAL_WORK_EDGE_TYPE_MISMATCH",
                f"{payload.edge_type.value} 不允许连接 {source_type.value} → {target_type.value}。",
                status_code=422,
            )
        self._verify_evidence_sources(company_id, project_id, payload.assertions)

        now = _now()
        edge_id = str(uuid4())
        with self.session.begin_nested():
            revision = self._append_revision(
                model,
                previous,
                operation="EDGE_ADDED",
                summary=f"新增 {payload.edge_type.value} 关系。",
                change_payload={
                    "edge_id": edge_id,
                    **payload.model_dump(mode="json"),
                },
                actor=actor,
                now=now,
            )
            self._copy_snapshot(model.id, previous.id, revision.id)
            self.session.add(
                VirtualWorkEdgeRow(
                    model_id=model.id,
                    revision_id=revision.id,
                    id=edge_id,
                    source_node_id=str(payload.source_node_id),
                    target_node_id=str(payload.target_node_id),
                    edge_type=payload.edge_type.value,
                    label=payload.label,
                    properties=payload.properties,
                    created_at=now,
                )
            )
            self._add_assertions(
                model.id,
                revision.id,
                subject_kind="EDGE",
                subject_id=edge_id,
                assertions=payload.assertions,
                now=now,
            )
            self.session.flush()
        return self.get_revision_for_review(
            company_id, project_id, model_id, version=revision.version
        )

    def update_edge(
        self,
        company_id: UUID,
        project_id: UUID,
        model_id: UUID,
        edge_id: UUID,
        payload: VirtualEdgeUpdate,
        *,
        actor_id: str,
    ) -> VirtualWorkRevisionView:
        actor = self._actor(actor_id)
        model = self._model(company_id, project_id, model_id)
        previous = self._latest_revision(model.id)
        self._assert_expected_revision(
            previous, payload.expected_version, payload.expected_revision_hash
        )
        edge = self._edge_in_revision(model.id, previous.id, edge_id)
        endpoint_types = self._edge_endpoint_types(
            model.id, previous.id, payload.source_node_id, payload.target_node_id
        )
        self._validate_edge_types(
            payload.edge_type,
            endpoint_types,
            payload.source_node_id,
            payload.target_node_id,
        )
        self._verify_evidence_sources(company_id, project_id, payload.assertions)

        now = _now()
        change_payload = payload.model_dump(
            mode="json", exclude={"expected_version", "expected_revision_hash"}
        )
        change_payload["edge_id"] = str(edge_id)
        with self.session.begin_nested():
            revision = self._append_revision(
                model,
                previous,
                operation="EDGE_UPDATED",
                summary=payload.reason,
                change_payload=change_payload,
                actor=actor,
                now=now,
            )
            self._copy_snapshot(
                model.id,
                previous.id,
                revision.id,
                excluded_edge_ids={edge.id},
            )
            self.session.add(
                VirtualWorkEdgeRow(
                    model_id=model.id,
                    revision_id=revision.id,
                    id=edge.id,
                    source_node_id=str(payload.source_node_id),
                    target_node_id=str(payload.target_node_id),
                    edge_type=payload.edge_type.value,
                    label=payload.label,
                    properties=payload.properties,
                    created_at=now,
                )
            )
            self._add_assertions(
                model.id,
                revision.id,
                subject_kind="EDGE",
                subject_id=edge.id,
                assertions=payload.assertions,
                now=now,
            )
            self.session.flush()
        return self.get_revision_for_review(
            company_id, project_id, model_id, version=revision.version
        )

    def retire_edge(
        self,
        company_id: UUID,
        project_id: UUID,
        model_id: UUID,
        edge_id: UUID,
        payload: VirtualWorkRetireCreate,
        *,
        actor_id: str,
    ) -> VirtualWorkRevisionView:
        actor = self._actor(actor_id)
        model = self._model(company_id, project_id, model_id)
        previous = self._latest_revision(model.id)
        self._assert_expected_revision(
            previous, payload.expected_version, payload.expected_revision_hash
        )
        edge = self._edge_in_revision(model.id, previous.id, edge_id)
        now = _now()
        with self.session.begin_nested():
            revision = self._append_revision(
                model,
                previous,
                operation="EDGE_RETIRED",
                summary=payload.reason,
                change_payload={"edge_id": edge.id, "reason": payload.reason},
                actor=actor,
                now=now,
            )
            self._copy_snapshot(
                model.id,
                previous.id,
                revision.id,
                excluded_edge_ids={edge.id},
            )
            self.session.flush()
        return self.get_revision_for_review(
            company_id, project_id, model_id, version=revision.version
        )

    def list_models(self, company_id: UUID, project_id: UUID) -> list[VirtualWorkModelSummary]:
        rows = self.session.scalars(
            select(VirtualWorkModelRow)
            .where(
                VirtualWorkModelRow.company_id == str(company_id),
                VirtualWorkModelRow.project_id == str(project_id),
            )
            .order_by(VirtualWorkModelRow.created_at, VirtualWorkModelRow.id)
        ).all()
        summaries: list[VirtualWorkModelSummary] = []
        for model in rows:
            revision = self._latest_reviewed_revision(model.id)
            if revision is None:
                continue
            summaries.append(
                VirtualWorkModelSummary(
                    id=UUID(model.id),
                    company_id=UUID(model.company_id),
                    project_id=UUID(model.project_id),
                    name=revision.name,
                    description=revision.description,
                    version=revision.version,
                    created_at=model.created_at,
                )
            )
        return summaries

    def get_model(
        self,
        company_id: UUID,
        project_id: UUID,
        model_id: UUID,
        *,
        version: int | None = None,
    ) -> VirtualWorkRevisionView:
        model = self._model(company_id, project_id, model_id)
        if version is None:
            revision = self._latest_reviewed_revision(model.id)
            if revision is None:
                raise DomainError(
                    "VIRTUAL_WORK_MODEL_NOT_REVIEWED",
                    "该虚模尚无已审阅版本，草稿不能用于通用查询。",
                    status_code=404,
                )
        else:
            if version < 1:
                raise DomainError(
                    "VIRTUAL_WORK_VERSION_INVALID", "版本号必须大于零。", status_code=422
                )
            revision = self.session.scalar(
                select(VirtualWorkRevisionRow).where(
                    VirtualWorkRevisionRow.model_id == model.id,
                    VirtualWorkRevisionRow.version == version,
                )
            )
            if revision is None:
                raise DomainError(
                    "VIRTUAL_WORK_VERSION_NOT_FOUND",
                    "指定版本不存在。",
                    status_code=404,
                )
            if self._revision_status(revision) is not VirtualRevisionStatus.REVIEWED:
                raise DomainError(
                    "VIRTUAL_WORK_REVISION_NOT_REVIEWED",
                    "指定版本尚未通过人工审阅，不能用于通用查询。",
                    status_code=404,
                )
        return self._revision_view(model, revision)

    def get_revision_for_review(
        self,
        company_id: UUID,
        project_id: UUID,
        model_id: UUID,
        *,
        version: int,
    ) -> VirtualWorkRevisionView:
        """Explicit review-workflow read; unlike get_model it may return a draft."""
        model = self._model(company_id, project_id, model_id)
        revision = self.session.scalar(
            select(VirtualWorkRevisionRow).where(
                VirtualWorkRevisionRow.model_id == model.id,
                VirtualWorkRevisionRow.version == version,
            )
        )
        if revision is None:
            raise DomainError("VIRTUAL_WORK_VERSION_NOT_FOUND", "指定版本不存在。", status_code=404)
        return self._revision_view(model, revision)

    def review_revision(
        self,
        company_id: UUID,
        project_id: UUID,
        model_id: UUID,
        payload: VirtualWorkReviewCreate,
        *,
        actor_id: str,
    ) -> VirtualWorkReviewView:
        actor = self._actor(actor_id)
        model = self._model(company_id, project_id, model_id)
        revision = self.session.scalar(
            select(VirtualWorkRevisionRow).where(
                VirtualWorkRevisionRow.model_id == model.id,
                VirtualWorkRevisionRow.id == str(payload.revision_id),
                VirtualWorkRevisionRow.version == payload.version,
            )
        )
        if revision is None:
            raise DomainError(
                "VIRTUAL_WORK_REVIEW_REVISION_MISMATCH",
                "审阅指定的版本号或修订标识不匹配。",
                status_code=409,
            )
        if revision.revision_hash != payload.revision_hash:
            raise DomainError(
                "VIRTUAL_WORK_REVIEW_HASH_MISMATCH",
                "修订哈希已变化或与审阅提交不一致，未记录审阅。",
                status_code=409,
            )
        confirmed_scope = ConfirmedScope(
            company_id=company_id,
            project_id=project_id,
            virtual_work_model_id=model_id,
        )
        if payload.confirmed_scope != confirmed_scope:
            raise DomainError(
                "VIRTUAL_WORK_REVIEW_SCOPE_MISMATCH",
                "确认范围必须与该修订所属公司、项目和虚模完全一致。",
                status_code=409,
            )
        previous_review = self.session.scalar(
            select(VirtualWorkReviewRow)
            .where(
                VirtualWorkReviewRow.model_id == model.id,
                VirtualWorkReviewRow.revision_id == revision.id,
            )
            .order_by(VirtualWorkReviewRow.sequence.desc())
            .limit(1)
        )
        row = VirtualWorkReviewRow(
            model_id=model.id,
            revision_id=revision.id,
            id=str(uuid4()),
            sequence=(previous_review.sequence + 1) if previous_review else 1,
            version=revision.version,
            revision_hash=revision.revision_hash,
            confirmed_scope=payload.confirmed_scope.model_dump(mode="json"),
            confirmation_kind=payload.confirmation_kind.value,
            decision=payload.decision.value,
            actor_id=actor,
            reason=payload.reason,
            created_at=_now(),
        )
        with self.session.begin_nested():
            self.session.add(row)
            self.session.flush()
        return self._review_view(row)

    def list_review_records(
        self,
        company_id: UUID,
        project_id: UUID,
        model_id: UUID,
        *,
        version: int,
    ) -> list[VirtualWorkReviewView]:
        model = self._model(company_id, project_id, model_id)
        revision = self.session.scalar(
            select(VirtualWorkRevisionRow).where(
                VirtualWorkRevisionRow.model_id == model.id,
                VirtualWorkRevisionRow.version == version,
            )
        )
        if revision is None:
            raise DomainError("VIRTUAL_WORK_VERSION_NOT_FOUND", "指定版本不存在。", status_code=404)
        rows = self.session.scalars(
            select(VirtualWorkReviewRow)
            .where(
                VirtualWorkReviewRow.model_id == model.id,
                VirtualWorkReviewRow.revision_id == revision.id,
            )
            .order_by(VirtualWorkReviewRow.sequence)
        ).all()
        return [self._review_view(row) for row in rows]

    def _model(self, company_id: UUID, project_id: UUID, model_id: UUID) -> VirtualWorkModelRow:
        model = self.session.scalar(
            select(VirtualWorkModelRow).where(
                VirtualWorkModelRow.id == str(model_id),
                VirtualWorkModelRow.company_id == str(company_id),
                VirtualWorkModelRow.project_id == str(project_id),
            )
        )
        if model is None:
            raise DomainError(
                "VIRTUAL_WORK_MODEL_NOT_FOUND", "虚模不存在或不属于当前公司/项目。", status_code=404
            )
        return model

    @staticmethod
    def _assert_expected_revision(
        latest: VirtualWorkRevisionRow, expected_version: int, expected_hash: str
    ) -> None:
        if latest.version != expected_version:
            raise DomainError(
                "VIRTUAL_WORK_REVISION_STALE",
                "虚模已产生更新版本，请先读取最新版本后再提交修改。",
                status_code=409,
            )
        if latest.revision_hash != expected_hash:
            raise DomainError(
                "VIRTUAL_WORK_REVISION_HASH_MISMATCH",
                "修订哈希与当前版本不一致，未写入修改。",
                status_code=409,
            )

    def _node_in_revision(
        self, model_id: str, revision_id: str, node_id: UUID
    ) -> VirtualWorkNodeRow:
        node = self.session.scalar(
            select(VirtualWorkNodeRow).where(
                VirtualWorkNodeRow.model_id == model_id,
                VirtualWorkNodeRow.revision_id == revision_id,
                VirtualWorkNodeRow.id == str(node_id),
            )
        )
        if node is None:
            self._raise_missing_or_out_of_scope_nodes(model_id, {str(node_id)})
        return node

    def _edge_in_revision(
        self, model_id: str, revision_id: str, edge_id: UUID
    ) -> VirtualWorkEdgeRow:
        edge = self.session.scalar(
            select(VirtualWorkEdgeRow).where(
                VirtualWorkEdgeRow.model_id == model_id,
                VirtualWorkEdgeRow.revision_id == revision_id,
                VirtualWorkEdgeRow.id == str(edge_id),
            )
        )
        if edge is not None:
            return edge
        existing_model_id = self.session.scalar(
            select(VirtualWorkEdgeRow.model_id)
            .where(VirtualWorkEdgeRow.id == str(edge_id))
            .limit(1)
        )
        if existing_model_id is not None and existing_model_id != model_id:
            raise DomainError(
                "VIRTUAL_WORK_REFERENCE_OUT_OF_SCOPE",
                "关系属于其他虚模，不能跨公司/项目或跨虚模修改。",
                status_code=422,
            )
        if existing_model_id == model_id:
            raise DomainError(
                "VIRTUAL_WORK_EDGE_NOT_IN_VERSION",
                "关系不属于当前版本，可能已在后续版本中退役。",
                status_code=422,
            )
        raise DomainError("VIRTUAL_WORK_EDGE_NOT_FOUND", "关系不存在。", status_code=404)

    def _edge_endpoint_types(
        self,
        model_id: str,
        revision_id: str,
        source_node_id: UUID,
        target_node_id: UUID,
    ) -> dict[str, VirtualNodeType]:
        expected_ids = {str(source_node_id), str(target_node_id)}
        nodes = self.session.scalars(
            select(VirtualWorkNodeRow).where(
                VirtualWorkNodeRow.model_id == model_id,
                VirtualWorkNodeRow.revision_id == revision_id,
                VirtualWorkNodeRow.id.in_(expected_ids),
            )
        ).all()
        endpoint_types = {node.id: VirtualNodeType(node.node_type) for node in nodes}
        if set(endpoint_types) != expected_ids:
            self._raise_missing_or_out_of_scope_nodes(model_id, expected_ids - set(endpoint_types))
        return endpoint_types

    @staticmethod
    def _validate_edge_types(
        edge_type: VirtualEdgeType,
        endpoint_types: dict[str, VirtualNodeType],
        source_node_id: UUID,
        target_node_id: UUID,
    ) -> None:
        allowed_source, allowed_target = _EDGE_ENDPOINTS[edge_type]
        source_type = endpoint_types[str(source_node_id)]
        target_type = endpoint_types[str(target_node_id)]
        if source_type not in allowed_source or target_type not in allowed_target:
            raise DomainError(
                "VIRTUAL_WORK_EDGE_TYPE_MISMATCH",
                f"{edge_type.value} 不允许连接 {source_type.value} → {target_type.value}。",
                status_code=422,
            )

    def _validate_node_replacement(
        self,
        model_id: str,
        revision_id: str,
        node_id: str,
        replacement_type: VirtualNodeType,
    ) -> None:
        node_types = {
            row.id: VirtualNodeType(row.node_type)
            for row in self.session.scalars(
                select(VirtualWorkNodeRow).where(
                    VirtualWorkNodeRow.model_id == model_id,
                    VirtualWorkNodeRow.revision_id == revision_id,
                )
            ).all()
        }
        node_types[node_id] = replacement_type
        edges = self.session.scalars(
            select(VirtualWorkEdgeRow).where(
                VirtualWorkEdgeRow.model_id == model_id,
                VirtualWorkEdgeRow.revision_id == revision_id,
            )
        ).all()
        for edge in edges:
            source_type = node_types[edge.source_node_id]
            target_type = node_types[edge.target_node_id]
            allowed_source, allowed_target = _EDGE_ENDPOINTS[VirtualEdgeType(edge.edge_type)]
            if source_type not in allowed_source or target_type not in allowed_target:
                raise DomainError(
                    "VIRTUAL_WORK_EDGE_TYPE_MISMATCH",
                    "节点类型调整会使现有关系失效；请先更新或退役相关关系。",
                    status_code=422,
                )

    def _latest_revision(self, model_id: str) -> VirtualWorkRevisionRow:
        revision = self.session.scalar(
            select(VirtualWorkRevisionRow)
            .where(VirtualWorkRevisionRow.model_id == model_id)
            .order_by(VirtualWorkRevisionRow.version.desc())
            .limit(1)
        )
        if revision is None:
            raise DomainError(
                "VIRTUAL_WORK_REVISION_MISSING", "虚模缺少有效版本。", status_code=409
            )
        return revision

    def _latest_reviewed_revision(self, model_id: str) -> VirtualWorkRevisionRow | None:
        revisions = self.session.scalars(
            select(VirtualWorkRevisionRow)
            .where(VirtualWorkRevisionRow.model_id == model_id)
            .order_by(VirtualWorkRevisionRow.version.desc())
        ).all()
        return next(
            (
                revision
                for revision in revisions
                if self._revision_status(revision) is VirtualRevisionStatus.REVIEWED
            ),
            None,
        )

    def _revision_status(self, revision: VirtualWorkRevisionRow) -> VirtualRevisionStatus:
        latest_review = self.session.scalar(
            select(VirtualWorkReviewRow)
            .where(
                VirtualWorkReviewRow.model_id == revision.model_id,
                VirtualWorkReviewRow.revision_id == revision.id,
            )
            .order_by(VirtualWorkReviewRow.sequence.desc())
            .limit(1)
        )
        if latest_review is None:
            return VirtualRevisionStatus.DRAFT
        return VirtualRevisionStatus(latest_review.decision)

    @staticmethod
    def _review_view(row: VirtualWorkReviewRow) -> VirtualWorkReviewView:
        scope = row.confirmed_scope
        return VirtualWorkReviewView(
            id=UUID(row.id),
            revision_id=UUID(row.revision_id),
            version=row.version,
            revision_hash=row.revision_hash,
            confirmed_scope=ConfirmedScope(
                company_id=UUID(scope["company_id"]),
                project_id=UUID(scope["project_id"]),
                virtual_work_model_id=UUID(scope["virtual_work_model_id"]),
            ),
            confirmation_kind=ConfirmationKind(row.confirmation_kind),
            decision=ReviewDecision(row.decision),
            actor_id=row.actor_id,
            reason=row.reason,
            created_at=row.created_at,
        )

    def _verify_anchor(self, model: VirtualWorkModelRow, anchor: RealAnchorCreate) -> None:
        if self.formal_anchor_scope_check is None:
            raise DomainError(
                "VIRTUAL_WORK_ANCHOR_VERIFIER_REQUIRED",
                "未配置正式实体范围校验器，不能创建正式实体锚点。",
                status_code=409,
            )
        if not self.formal_anchor_scope_check(
            UUID(model.company_id),
            UUID(model.project_id),
            anchor.real_entity_id,
            anchor.real_release_id,
        ):
            raise DomainError(
                "VIRTUAL_WORK_ANCHOR_OUT_OF_SCOPE",
                "正式实体或 release 不属于当前公司/项目范围。",
                status_code=422,
            )

    def _verify_evidence_sources(
        self,
        company_id: UUID,
        project_id: UUID,
        assertions: list[FieldAssertionCreate],
    ) -> None:
        if self.evidence_source_check is None:
            return
        for assertion in assertions:
            for evidence in assertion.evidence:
                if not self.evidence_source_check(
                    company_id,
                    project_id,
                    evidence.evidence_kind,
                    evidence.source_ref,
                    evidence.excerpt,
                ):
                    raise DomainError(
                        "VIRTUAL_WORK_EVIDENCE_SOURCE_REJECTED",
                        "证据来源未通过当前公司/项目范围校验，虚模修订未写入。",
                        status_code=422,
                    )

    def _raise_missing_or_out_of_scope_nodes(self, model_id: str, missing_ids: set[str]) -> None:
        other_model = self.session.scalar(
            select(VirtualWorkNodeRow.model_id)
            .where(VirtualWorkNodeRow.id.in_(missing_ids))
            .limit(1)
        )
        if other_model is not None and other_model != model_id:
            raise DomainError(
                "VIRTUAL_WORK_REFERENCE_OUT_OF_SCOPE",
                "关系端点属于其他虚模，不能跨公司/项目或跨虚模连接。",
                status_code=422,
            )
        same_model = self.session.scalar(
            select(VirtualWorkNodeRow.id)
            .where(
                VirtualWorkNodeRow.model_id == model_id,
                VirtualWorkNodeRow.id.in_(missing_ids),
            )
            .limit(1)
        )
        if same_model is not None:
            raise DomainError(
                "VIRTUAL_WORK_REFERENCE_NOT_IN_VERSION",
                "关系端点不属于当前虚模版本。",
                status_code=422,
            )
        raise DomainError("VIRTUAL_WORK_NODE_NOT_FOUND", "关系端点不存在。", status_code=404)

    def _append_revision(
        self,
        model: VirtualWorkModelRow,
        previous: VirtualWorkRevisionRow,
        *,
        operation: str,
        summary: str,
        change_payload: dict[str, Any],
        actor: str,
        now: datetime,
    ) -> VirtualWorkRevisionRow:
        revision = VirtualWorkRevisionRow(
            id=str(uuid4()),
            model_id=model.id,
            version=previous.version + 1,
            name=previous.name,
            description=previous.description,
            operation=operation,
            change_summary=summary,
            change_payload=change_payload,
            revision_hash=_revision_hash(
                model.id,
                previous.version + 1,
                previous.revision_hash,
                operation,
                change_payload,
            ),
            created_by=actor,
            created_at=now,
        )
        self.session.add(revision)
        self.session.flush()
        return revision

    def _copy_snapshot(
        self,
        model_id: str,
        from_revision: str,
        to_revision: str,
        *,
        retired_node_ids: set[str] | None = None,
        excluded_edge_ids: set[str] | None = None,
        replaced_nodes: dict[str, VirtualNodeCreate] | None = None,
        replacement_created_at: datetime | None = None,
    ) -> None:
        retired_node_ids = retired_node_ids or set()
        excluded_edge_ids = excluded_edge_ids or set()
        replaced_nodes = replaced_nodes or {}
        replaced_node_ids = set(replaced_nodes)
        changed_node_ids = retired_node_ids | replaced_node_ids
        node_rows = self.session.scalars(
            select(VirtualWorkNodeRow).where(
                VirtualWorkNodeRow.model_id == model_id,
                VirtualWorkNodeRow.revision_id == from_revision,
            )
        ).all()
        node_rows = [row for row in node_rows if row.id not in retired_node_ids]
        edge_rows = self.session.scalars(
            select(VirtualWorkEdgeRow).where(
                VirtualWorkEdgeRow.model_id == model_id,
                VirtualWorkEdgeRow.revision_id == from_revision,
            )
        ).all()
        edge_rows = [
            row
            for row in edge_rows
            if row.id not in excluded_edge_ids
            and row.source_node_id not in retired_node_ids
            and row.target_node_id not in retired_node_ids
        ]
        excluded_subjects = {("NODE", item) for item in changed_node_ids}
        excluded_subjects |= {("EDGE", item) for item in excluded_edge_ids}
        assertion_rows = self.session.scalars(
            select(VirtualWorkAssertionRow).where(
                VirtualWorkAssertionRow.model_id == model_id,
                VirtualWorkAssertionRow.revision_id == from_revision,
            )
        ).all()
        assertion_rows = [
            row for row in assertion_rows
            if (row.subject_kind, row.subject_id) not in excluded_subjects
        ]
        assertion_ids = {row.id for row in assertion_rows}
        association_rows = self.session.scalars(
            select(VirtualWorkAssertionEvidenceRow).where(
                VirtualWorkAssertionEvidenceRow.model_id == model_id,
                VirtualWorkAssertionEvidenceRow.revision_id == from_revision,
            )
        ).all()
        association_rows = [row for row in association_rows if row.assertion_id in assertion_ids]
        evidence_ids = {row.evidence_id for row in association_rows}
        evidence_rows = [
            row
            for row in self.session.scalars(
                select(VirtualWorkEvidenceRow).where(
                    VirtualWorkEvidenceRow.model_id == model_id,
                    VirtualWorkEvidenceRow.revision_id == from_revision,
                )
            ).all()
            if row.id in evidence_ids
        ]
        anchor_rows = self.session.scalars(
            select(RealAnchorRow).where(
                RealAnchorRow.model_id == model_id,
                RealAnchorRow.revision_id == from_revision,
            )
        ).all()
        anchor_rows = [row for row in anchor_rows if row.virtual_node_id not in changed_node_ids]

        self.session.add_all(
            [
                VirtualWorkNodeRow(
                    model_id=row.model_id,
                    revision_id=to_revision,
                    id=row.id,
                    node_type=(replaced_nodes[row.id].node_type.value
                               if row.id in replaced_nodes else row.node_type),
                    label=(replaced_nodes[row.id].label
                           if row.id in replaced_nodes else row.label),
                    properties=(replaced_nodes[row.id].properties
                                if row.id in replaced_nodes else row.properties),
                    created_at=(replacement_created_at
                                if row.id in replaced_nodes and replacement_created_at
                                else row.created_at),
                )
                for row in node_rows
            ]
        )
        self.session.flush()
        self.session.add_all(
            [
                VirtualWorkEdgeRow(
                    model_id=row.model_id,
                    revision_id=to_revision,
                    id=row.id,
                    source_node_id=row.source_node_id,
                    target_node_id=row.target_node_id,
                    edge_type=row.edge_type,
                    label=row.label,
                    properties=row.properties,
                    created_at=row.created_at,
                )
                for row in edge_rows
            ]
        )
        self.session.add_all(
            [
                VirtualWorkEvidenceRow(
                    model_id=row.model_id,
                    revision_id=to_revision,
                    id=row.id,
                    evidence_kind=row.evidence_kind,
                    source_ref=row.source_ref,
                    excerpt=row.excerpt,
                    source_root_id=row.source_root_id,
                    captured_at=row.captured_at,
                    created_at=row.created_at,
                )
                for row in evidence_rows
            ]
        )
        self.session.add_all(
            [
                VirtualWorkAssertionRow(
                    model_id=row.model_id,
                    revision_id=to_revision,
                    id=row.id,
                    subject_kind=row.subject_kind,
                    subject_id=row.subject_id,
                    field_name=row.field_name,
                    value_json=row.value_json,
                    assertion_kind=row.assertion_kind,
                    scope=row.scope,
                    valid_from=row.valid_from,
                    valid_to=row.valid_to,
                    method=row.method,
                    created_at=row.created_at,
                )
                for row in assertion_rows
            ]
        )
        self.session.flush()
        self.session.add_all(
            [
                VirtualWorkAssertionEvidenceRow(
                    model_id=row.model_id,
                    revision_id=to_revision,
                    assertion_id=row.assertion_id,
                    evidence_id=row.evidence_id,
                )
                for row in association_rows
            ]
        )
        self.session.add_all(
            [
                RealAnchorRow(
                    model_id=row.model_id,
                    revision_id=to_revision,
                    id=row.id,
                    virtual_node_id=row.virtual_node_id,
                    real_entity_id=row.real_entity_id,
                    real_release_id=row.real_release_id,
                    relation_kind=row.relation_kind,
                    support_ref=row.support_ref,
                    status=row.status,
                    created_at=row.created_at,
                )
                for row in anchor_rows
            ]
        )
        self.session.flush()

    def _add_assertions(
        self,
        model_id: str,
        revision_id: str,
        *,
        subject_kind: str,
        subject_id: str,
        assertions: list[FieldAssertionCreate],
        now: datetime,
    ) -> None:
        associations: list[VirtualWorkAssertionEvidenceRow] = []
        for assertion in assertions:
            assertion_id = str(uuid4())
            self.session.add(
                VirtualWorkAssertionRow(
                    model_id=model_id,
                    revision_id=revision_id,
                    id=assertion_id,
                    subject_kind=subject_kind,
                    subject_id=subject_id,
                    field_name=assertion.field_name,
                    value_json=assertion.value,
                    assertion_kind=assertion.assertion_kind.value,
                    scope=assertion.scope,
                    valid_from=_normalize_valid_time(assertion.valid_from),
                    valid_to=_normalize_valid_time(assertion.valid_to),
                    method=assertion.method,
                    created_at=now,
                )
            )
            for evidence in assertion.evidence:
                evidence_id = str(uuid4())
                self.session.add(
                    VirtualWorkEvidenceRow(
                        model_id=model_id,
                        revision_id=revision_id,
                        id=evidence_id,
                        evidence_kind=evidence.evidence_kind.value,
                        source_ref=evidence.source_ref,
                        excerpt=evidence.excerpt,
                        source_root_id=str(evidence.source_root_id),
                        captured_at=evidence.captured_at,
                        created_at=now,
                    )
                )
                associations.append(
                    VirtualWorkAssertionEvidenceRow(
                        model_id=model_id,
                        revision_id=revision_id,
                        assertion_id=assertion_id,
                        evidence_id=evidence_id,
                    )
                )
        self.session.flush()
        self.session.add_all(associations)

    def _revision_view(
        self,
        model: VirtualWorkModelRow,
        revision: VirtualWorkRevisionRow,
    ) -> VirtualWorkRevisionView:
        evidence_rows = self.session.scalars(
            select(VirtualWorkEvidenceRow).where(
                VirtualWorkEvidenceRow.model_id == model.id,
                VirtualWorkEvidenceRow.revision_id == revision.id,
            )
        ).all()
        evidence_views = {
            row.id: EvidenceView(
                id=UUID(row.id),
                evidence_kind=EvidenceKind(row.evidence_kind),
                source_ref=row.source_ref,
                excerpt=row.excerpt,
                source_root_id=UUID(row.source_root_id),
                captured_at=row.captured_at,
            )
            for row in evidence_rows
        }
        links = self.session.scalars(
            select(VirtualWorkAssertionEvidenceRow).where(
                VirtualWorkAssertionEvidenceRow.model_id == model.id,
                VirtualWorkAssertionEvidenceRow.revision_id == revision.id,
            )
        ).all()
        evidence_by_assertion: dict[str, list[EvidenceView]] = defaultdict(list)
        for link in links:
            evidence_by_assertion[link.assertion_id].append(evidence_views[link.evidence_id])
        assertion_rows = self.session.scalars(
            select(VirtualWorkAssertionRow).where(
                VirtualWorkAssertionRow.model_id == model.id,
                VirtualWorkAssertionRow.revision_id == revision.id,
            )
        ).all()
        assertions_by_subject: dict[tuple[str, str], list[FieldAssertionView]] = defaultdict(list)
        for row in assertion_rows:
            assertions_by_subject[(row.subject_kind, row.subject_id)].append(
                FieldAssertionView(
                    id=UUID(row.id),
                    field_name=row.field_name,
                    value=row.value_json,
                    assertion_kind=AssertionKind(row.assertion_kind),
                    scope=row.scope,
                    valid_from=_read_valid_time(row.valid_from),
                    valid_to=_read_valid_time(row.valid_to),
                    method=row.method,
                    evidence=evidence_by_assertion[row.id],
                )
            )
        anchor_rows = self.session.scalars(
            select(RealAnchorRow).where(
                RealAnchorRow.model_id == model.id,
                RealAnchorRow.revision_id == revision.id,
            )
        ).all()
        anchors_by_node: dict[str, list[RealAnchorView]] = defaultdict(list)
        for row in anchor_rows:
            anchors_by_node[row.virtual_node_id].append(
                RealAnchorView(
                    id=UUID(row.id),
                    virtual_node_id=UUID(row.virtual_node_id),
                    real_entity_id=UUID(row.real_entity_id),
                    real_release_id=UUID(row.real_release_id),
                    relation_kind=AnchorRelationKind(row.relation_kind),
                    support_ref=row.support_ref,
                    status=AnchorStatus(row.status),
                )
            )
        node_rows = self.session.scalars(
            select(VirtualWorkNodeRow)
            .where(
                VirtualWorkNodeRow.model_id == model.id,
                VirtualWorkNodeRow.revision_id == revision.id,
            )
            .order_by(VirtualWorkNodeRow.created_at, VirtualWorkNodeRow.id)
        ).all()
        nodes = [
            VirtualNodeView(
                id=UUID(row.id),
                node_type=VirtualNodeType(row.node_type),
                label=row.label,
                properties=row.properties,
                assertions=assertions_by_subject[("NODE", row.id)],
                anchors=anchors_by_node[row.id],
            )
            for row in node_rows
        ]
        edge_rows = self.session.scalars(
            select(VirtualWorkEdgeRow)
            .where(
                VirtualWorkEdgeRow.model_id == model.id,
                VirtualWorkEdgeRow.revision_id == revision.id,
            )
            .order_by(VirtualWorkEdgeRow.created_at, VirtualWorkEdgeRow.id)
        ).all()
        edges = [
            VirtualEdgeView(
                id=UUID(row.id),
                source_node_id=UUID(row.source_node_id),
                target_node_id=UUID(row.target_node_id),
                edge_type=VirtualEdgeType(row.edge_type),
                label=row.label,
                properties=row.properties,
                assertions=assertions_by_subject[("EDGE", row.id)],
            )
            for row in edge_rows
        ]
        review_rows = self.session.scalars(
            select(VirtualWorkReviewRow)
            .where(
                VirtualWorkReviewRow.model_id == model.id,
                VirtualWorkReviewRow.revision_id == revision.id,
            )
            .order_by(VirtualWorkReviewRow.sequence)
        ).all()
        return VirtualWorkRevisionView(
            virtual_work_model_id=UUID(model.id),
            company_id=UUID(model.company_id),
            project_id=UUID(model.project_id),
            revision_id=UUID(revision.id),
            version=revision.version,
            status=self._revision_status(revision),
            revision_hash=revision.revision_hash,
            name=revision.name,
            description=revision.description,
            operation=revision.operation,
            change_summary=revision.change_summary,
            change_payload=revision.change_payload,
            created_by=revision.created_by,
            created_at=revision.created_at,
            review_records=[self._review_view(row) for row in review_rows],
            nodes=nodes,
            edges=edges,
        )

    @staticmethod
    def _actor(actor_id: str) -> str:
        actor = actor_id.strip()
        if not actor or len(actor) > 128:
            raise DomainError(
                "VIRTUAL_WORK_ACTOR_INVALID",
                "操作者标识不能为空且不能超过 128 个字符。",
                status_code=422,
            )
        return actor


def _now() -> datetime:
    return datetime.now(UTC)


def _revision_hash(
    model_id: str,
    version: int,
    parent_hash: str | None,
    operation: str,
    change_payload: dict[str, Any],
) -> str:
    serialized = json.dumps(
        {
            "model_id": model_id,
            "version": version,
            "parent_hash": parent_hash,
            "operation": operation,
            "change_payload": change_payload,
        },
        ensure_ascii=False,
        sort_keys=True,
        separators=(",", ":"),
    )
    return sha256(serialized.encode("utf-8")).hexdigest()


_IMMUTABLE_TABLES = (
    "virtual_work_models",
    "virtual_work_revisions",
    "virtual_work_nodes",
    "virtual_work_edges",
    "virtual_work_evidence",
    "virtual_work_assertions",
    "virtual_work_assertion_evidence",
    "virtual_work_real_anchors",
    "virtual_work_reviews",
)

for _table_name in _IMMUTABLE_TABLES:
    _table = Base.metadata.tables[_table_name]
    for _operation in ("UPDATE", "DELETE"):
        _trigger_name = f"trg_{_table_name}_{_operation.lower()}_immutable"
        event.listen(
            _table,
            "after_create",
            DDL(
                f"CREATE TRIGGER IF NOT EXISTS {_trigger_name} "
                f"BEFORE {_operation} ON {_table_name} "
                "BEGIN SELECT RAISE(ABORT, 'virtual work history is append-only'); END"
            ).execute_if(dialect="sqlite"),
        )
