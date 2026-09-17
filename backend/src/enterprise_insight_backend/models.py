from __future__ import annotations

from datetime import UTC, datetime
from typing import Any
from uuid import uuid4

from sqlalchemy import (
    JSON,
    Boolean,
    CheckConstraint,
    DateTime,
    Float,
    ForeignKey,
    Index,
    Integer,
    String,
    Text,
    UniqueConstraint,
    text,
)
from sqlalchemy.orm import DeclarativeBase, Mapped, mapped_column, relationship


def utc_now() -> datetime:
    return datetime.now(UTC)


def new_id() -> str:
    return str(uuid4())


class Base(DeclarativeBase):
    pass


class CompanyRow(Base):
    __tablename__ = "companies"

    id: Mapped[str] = mapped_column(String(36), primary_key=True, default=new_id)
    name: Mapped[str] = mapped_column(String(200), index=True)
    industry: Mapped[str | None] = mapped_column(String(200))
    description: Mapped[str | None] = mapped_column(Text)
    # The first project is retained as the compatibility storage key for the
    # company's single enterprise projection.  New UI flows resolve through
    # this pointer and do not ask users to create a second model container.
    canonical_project_id: Mapped[str | None] = mapped_column(String(36), index=True)
    created_at: Mapped[datetime] = mapped_column(DateTime(timezone=True), default=utc_now)
    updated_at: Mapped[datetime] = mapped_column(
        DateTime(timezone=True), default=utc_now, onupdate=utc_now
    )


class ProjectRow(Base):
    __tablename__ = "projects"
    __table_args__ = (UniqueConstraint("company_id", "name", name="uq_project_company_name"),)

    id: Mapped[str] = mapped_column(String(36), primary_key=True, default=new_id)
    company_id: Mapped[str] = mapped_column(
        String(36), ForeignKey("companies.id", ondelete="CASCADE"), index=True
    )
    name: Mapped[str] = mapped_column(String(200))
    description: Mapped[str | None] = mapped_column(Text)
    status: Mapped[str] = mapped_column(String(32), default="ACTIVE", index=True)
    is_primary: Mapped[bool] = mapped_column(Boolean, default=False, index=True)
    canonical_project_id: Mapped[str | None] = mapped_column(String(36), index=True)
    revision: Mapped[int] = mapped_column(Integer, default=0)
    created_at: Mapped[datetime] = mapped_column(DateTime(timezone=True), default=utc_now)
    updated_at: Mapped[datetime] = mapped_column(
        DateTime(timezone=True), default=utc_now, onupdate=utc_now
    )


class OntologyTypeRow(Base):
    __tablename__ = "ontology_types"
    __table_args__ = (UniqueConstraint("project_id", "key", name="uq_ontology_type_key"),)

    id: Mapped[str] = mapped_column(String(36), primary_key=True, default=new_id)
    project_id: Mapped[str] = mapped_column(
        String(36), ForeignKey("projects.id", ondelete="CASCADE"), index=True
    )
    key: Mapped[str] = mapped_column(String(64))
    name: Mapped[str] = mapped_column(String(200))
    kind: Mapped[str] = mapped_column(String(32), index=True)
    description: Mapped[str | None] = mapped_column(Text)
    parent_type_key: Mapped[str | None] = mapped_column(String(64))
    interface_keys: Mapped[list[str]] = mapped_column(JSON, default=list)
    properties: Mapped[list[dict[str, Any]]] = mapped_column(JSON, default=list)
    relation_roles: Mapped[list[dict[str, Any]]] = mapped_column(JSON, default=list)
    action_parameters: Mapped[list[dict[str, Any]]] = mapped_column(JSON, default=list)
    extra_metadata: Mapped[dict[str, Any]] = mapped_column("metadata", JSON, default=dict)
    status: Mapped[str] = mapped_column(String(32), default="DRAFT")
    revision: Mapped[int] = mapped_column(Integer, default=1)
    created_at: Mapped[datetime] = mapped_column(DateTime(timezone=True), default=utc_now)
    updated_at: Mapped[datetime] = mapped_column(
        DateTime(timezone=True), default=utc_now, onupdate=utc_now
    )


class OntologyReleaseRow(Base):
    __tablename__ = "ontology_releases"
    __table_args__ = (UniqueConstraint("project_id", "version", name="uq_ontology_release"),)

    id: Mapped[str] = mapped_column(String(36), primary_key=True, default=new_id)
    project_id: Mapped[str] = mapped_column(
        String(36), ForeignKey("projects.id", ondelete="CASCADE"), index=True
    )
    version: Mapped[int] = mapped_column(Integer)
    label: Mapped[str] = mapped_column(String(200))
    notes: Mapped[str | None] = mapped_column(Text)
    snapshot: Mapped[list[dict[str, Any]]] = mapped_column(JSON, default=list)
    created_at: Mapped[datetime] = mapped_column(DateTime(timezone=True), default=utc_now)


class EntityRow(Base):
    __tablename__ = "entities"
    __table_args__ = (UniqueConstraint("project_id", "stable_key", name="uq_entity_stable_key"),)

    id: Mapped[str] = mapped_column(String(36), primary_key=True, default=new_id)
    project_id: Mapped[str] = mapped_column(
        String(36), ForeignKey("projects.id", ondelete="CASCADE"), index=True
    )
    type_key: Mapped[str] = mapped_column(String(64), index=True)
    stable_key: Mapped[str | None] = mapped_column(String(200))
    name: Mapped[str] = mapped_column(String(300), index=True)
    properties: Mapped[dict[str, Any]] = mapped_column(JSON, default=dict)
    design_membership: Mapped[str] = mapped_column(
        String(32), default="MODELED", index=True
    )
    viewpoint: Mapped[str] = mapped_column(String(32), default="DESIGNED", index=True)
    evidence: Mapped[list[dict[str, Any]]] = mapped_column(JSON, default=list)
    status: Mapped[str] = mapped_column(String(32), default="DRAFT", index=True)
    revision: Mapped[int] = mapped_column(Integer, default=1)
    created_at: Mapped[datetime] = mapped_column(DateTime(timezone=True), default=utc_now)
    updated_at: Mapped[datetime] = mapped_column(
        DateTime(timezone=True), default=utc_now, onupdate=utc_now
    )


class RelationRow(Base):
    __tablename__ = "relations"

    id: Mapped[str] = mapped_column(String(36), primary_key=True, default=new_id)
    project_id: Mapped[str] = mapped_column(
        String(36), ForeignKey("projects.id", ondelete="CASCADE"), index=True
    )
    type_key: Mapped[str] = mapped_column(String(64), index=True)
    name: Mapped[str | None] = mapped_column(String(300))
    properties: Mapped[dict[str, Any]] = mapped_column(JSON, default=dict)
    viewpoint: Mapped[str] = mapped_column(String(32), default="DESIGNED", index=True)
    evidence: Mapped[list[dict[str, Any]]] = mapped_column(JSON, default=list)
    status: Mapped[str] = mapped_column(String(32), default="DRAFT", index=True)
    revision: Mapped[int] = mapped_column(Integer, default=1)
    created_at: Mapped[datetime] = mapped_column(DateTime(timezone=True), default=utc_now)
    updated_at: Mapped[datetime] = mapped_column(
        DateTime(timezone=True), default=utc_now, onupdate=utc_now
    )

    participants: Mapped[list[RelationParticipantRow]] = relationship(
        back_populates="relation",
        cascade="all, delete-orphan",
        order_by="RelationParticipantRow.ordinal",
    )


class RelationParticipantRow(Base):
    __tablename__ = "relation_participants"
    __table_args__ = (
        UniqueConstraint(
            "relation_id", "role_key", "entity_id", "ordinal", name="uq_relation_participant"
        ),
    )

    id: Mapped[str] = mapped_column(String(36), primary_key=True, default=new_id)
    relation_id: Mapped[str] = mapped_column(
        String(36), ForeignKey("relations.id", ondelete="CASCADE"), index=True
    )
    role_key: Mapped[str] = mapped_column(String(64), index=True)
    entity_id: Mapped[str] = mapped_column(
        String(36), ForeignKey("entities.id", ondelete="RESTRICT"), index=True
    )
    ordinal: Mapped[int] = mapped_column(Integer, default=0)

    relation: Mapped[RelationRow] = relationship(back_populates="participants")


class EventRow(Base):
    __tablename__ = "event_occurrences"

    id: Mapped[str] = mapped_column(String(36), primary_key=True, default=new_id)
    project_id: Mapped[str] = mapped_column(
        String(36), ForeignKey("projects.id", ondelete="CASCADE"), index=True
    )
    type_key: Mapped[str] = mapped_column(String(64), index=True)
    name: Mapped[str] = mapped_column(String(300))
    occurred_at: Mapped[datetime] = mapped_column(DateTime(timezone=True), index=True)
    participants: Mapped[list[dict[str, Any]]] = mapped_column(JSON, default=list)
    properties: Mapped[dict[str, Any]] = mapped_column(JSON, default=dict)
    evidence: Mapped[list[dict[str, Any]]] = mapped_column(JSON, default=list)
    status: Mapped[str] = mapped_column(String(32), default="DRAFT")
    revision: Mapped[int] = mapped_column(Integer, default=1)
    created_at: Mapped[datetime] = mapped_column(DateTime(timezone=True), default=utc_now)


class ChangeSetRow(Base):
    __tablename__ = "change_sets"

    id: Mapped[str] = mapped_column(String(36), primary_key=True, default=new_id)
    project_id: Mapped[str] = mapped_column(
        String(36), ForeignKey("projects.id", ondelete="CASCADE"), index=True
    )
    title: Mapped[str] = mapped_column(String(300))
    description: Mapped[str | None] = mapped_column(Text)
    base_revision: Mapped[int] = mapped_column(Integer)
    operations: Mapped[list[dict[str, Any]]] = mapped_column(JSON, default=list)
    created_by: Mapped[str] = mapped_column(String(100))
    status: Mapped[str] = mapped_column(String(32), default="DRAFT", index=True)
    validation: Mapped[dict[str, Any] | None] = mapped_column(JSON)
    created_at: Mapped[datetime] = mapped_column(DateTime(timezone=True), default=utc_now)
    updated_at: Mapped[datetime] = mapped_column(
        DateTime(timezone=True), default=utc_now, onupdate=utc_now
    )


class AgentThreadRow(Base):
    __tablename__ = "agent_threads"

    id: Mapped[str] = mapped_column(String(36), primary_key=True, default=new_id)
    project_id: Mapped[str] = mapped_column(
        String(36), ForeignKey("projects.id", ondelete="CASCADE"), index=True
    )
    agent_kind: Mapped[str] = mapped_column(String(32), index=True)
    title: Mapped[str] = mapped_column(String(200))
    archived: Mapped[bool] = mapped_column(Boolean, default=False)
    trashed: Mapped[bool] = mapped_column(Boolean, default=False)
    created_at: Mapped[datetime] = mapped_column(DateTime(timezone=True), default=utc_now)
    updated_at: Mapped[datetime] = mapped_column(
        DateTime(timezone=True), default=utc_now, onupdate=utc_now
    )


class AgentMessageRow(Base):
    __tablename__ = "agent_messages"

    id: Mapped[str] = mapped_column(String(36), primary_key=True, default=new_id)
    thread_id: Mapped[str] = mapped_column(
        String(36), ForeignKey("agent_threads.id", ondelete="CASCADE"), index=True
    )
    role: Mapped[str] = mapped_column(String(32))
    content: Mapped[str] = mapped_column(Text)
    citations: Mapped[list[dict[str, Any]]] = mapped_column(JSON, default=list)
    created_at: Mapped[datetime] = mapped_column(DateTime(timezone=True), default=utc_now)


class AgentRunRow(Base):
    __tablename__ = "agent_runs"

    id: Mapped[str] = mapped_column(String(36), primary_key=True, default=new_id)
    thread_id: Mapped[str] = mapped_column(
        String(36), ForeignKey("agent_threads.id", ondelete="CASCADE"), index=True
    )
    project_id: Mapped[str] = mapped_column(
        String(36), ForeignKey("projects.id", ondelete="CASCADE"), index=True
    )
    agent_kind: Mapped[str] = mapped_column(String(32))
    status: Mapped[str] = mapped_column(String(32), default="QUEUED", index=True)
    context_manifest: Mapped[dict[str, Any]] = mapped_column(JSON, default=dict)
    result_message_id: Mapped[str | None] = mapped_column(String(36))
    change_set_id: Mapped[str | None] = mapped_column(String(36))
    error: Mapped[dict[str, Any] | None] = mapped_column(JSON)
    worker_id: Mapped[str | None] = mapped_column(String(36), index=True)
    lease_expires_at: Mapped[datetime | None] = mapped_column(
        DateTime(timezone=True), index=True
    )
    heartbeat_at: Mapped[datetime | None] = mapped_column(DateTime(timezone=True))
    attempt_count: Mapped[int] = mapped_column(Integer, default=0)
    created_at: Mapped[datetime] = mapped_column(DateTime(timezone=True), default=utc_now)
    updated_at: Mapped[datetime] = mapped_column(
        DateTime(timezone=True), default=utc_now, onupdate=utc_now
    )

    @property
    def action_invocation_ids(self) -> list[str]:
        return list((self.context_manifest or {}).get("action_invocation_ids", []))


class AgentReadSetOutboxRow(Base):
    """Transactional delivery record for immutable Agent context read sets."""

    __tablename__ = "agent_readset_outbox"
    __table_args__ = (
        UniqueConstraint("run_id", "ordinal", name="uq_agent_readset_outbox_run_ordinal"),
        Index("ix_agent_readset_outbox_pending", "delivered_at", "created_at"),
    )

    event_id: Mapped[str] = mapped_column(String(64), primary_key=True)
    run_id: Mapped[str] = mapped_column(
        String(36), ForeignKey("agent_runs.id", ondelete="CASCADE"), index=True
    )
    ordinal: Mapped[int] = mapped_column(Integer)
    payload: Mapped[dict[str, Any]] = mapped_column(JSON)
    payload_sha256: Mapped[str] = mapped_column(String(64))
    created_at: Mapped[datetime] = mapped_column(DateTime(timezone=True), default=utc_now)
    delivered_at: Mapped[datetime | None] = mapped_column(DateTime(timezone=True))
    attempt_count: Mapped[int] = mapped_column(Integer, default=0)
    last_error_code: Mapped[str | None] = mapped_column(String(80))


class AgentStepOutboxRow(Base):
    """Incremental control-index delivery for immutable Agent route/claim steps."""

    __tablename__ = "agent_step_outbox"
    __table_args__ = (
        UniqueConstraint("step_id", name="uq_agent_step_outbox_step_id"),
        Index("ix_agent_step_outbox_pending", "delivered_at", "created_at"),
    )

    event_id: Mapped[str] = mapped_column(String(64), primary_key=True)
    step_id: Mapped[str] = mapped_column(
        String(36), ForeignKey("agent_steps.id", ondelete="CASCADE"), index=True
    )
    run_id: Mapped[str] = mapped_column(
        String(36), ForeignKey("agent_runs.id", ondelete="CASCADE"), index=True
    )
    kind: Mapped[str] = mapped_column(String(32))
    payload_sha256: Mapped[str] = mapped_column(String(64))
    created_at: Mapped[datetime] = mapped_column(DateTime(timezone=True), default=utc_now)
    delivered_at: Mapped[datetime | None] = mapped_column(DateTime(timezone=True))
    attempt_count: Mapped[int] = mapped_column(Integer, default=0)
    last_error_code: Mapped[str | None] = mapped_column(String(80))


class AgentStepRow(Base):
    __tablename__ = "agent_steps"
    __table_args__ = (UniqueConstraint("run_id", "position", name="uq_agent_step_position"),)

    id: Mapped[str] = mapped_column(String(36), primary_key=True, default=new_id)
    project_id: Mapped[str] = mapped_column(
        String(36), ForeignKey("projects.id", ondelete="CASCADE"), index=True
    )
    run_id: Mapped[str] = mapped_column(
        String(36), ForeignKey("agent_runs.id", ondelete="CASCADE"), index=True
    )
    position: Mapped[int] = mapped_column(Integer)
    kind: Mapped[str] = mapped_column(String(32), index=True)
    tool_key: Mapped[str | None] = mapped_column(String(100), index=True)
    status: Mapped[str] = mapped_column(String(32), index=True)
    input_payload: Mapped[dict[str, Any]] = mapped_column(JSON, default=dict)
    output_payload: Mapped[dict[str, Any]] = mapped_column(JSON, default=dict)
    error: Mapped[dict[str, Any] | None] = mapped_column(JSON)
    started_at: Mapped[datetime] = mapped_column(DateTime(timezone=True), default=utc_now)
    finished_at: Mapped[datetime | None] = mapped_column(DateTime(timezone=True))
    created_at: Mapped[datetime] = mapped_column(DateTime(timezone=True), default=utc_now)


class ActionDefinitionRow(Base):
    __tablename__ = "action_definitions"
    __table_args__ = (UniqueConstraint("project_id", "key", name="uq_action_definition_key"),)

    id: Mapped[str] = mapped_column(String(36), primary_key=True, default=new_id)
    project_id: Mapped[str] = mapped_column(
        String(36), ForeignKey("projects.id", ondelete="CASCADE"), index=True
    )
    key: Mapped[str] = mapped_column(String(100))
    name: Mapped[str] = mapped_column(String(200))
    description: Mapped[str | None] = mapped_column(Text)
    target_type_key: Mapped[str | None] = mapped_column(String(64))
    parameters: Mapped[list[dict[str, Any]]] = mapped_column(JSON, default=list)
    preconditions: Mapped[list[dict[str, Any]]] = mapped_column(JSON, default=list)
    effects: Mapped[list[dict[str, Any]]] = mapped_column(JSON, default=list)
    execution_mode: Mapped[str] = mapped_column(String(32), default="INTERNAL")
    risk_level: Mapped[str] = mapped_column(String(32), default="MEDIUM")
    require_approval: Mapped[bool] = mapped_column(Boolean, default=True)
    enabled: Mapped[bool] = mapped_column(Boolean, default=True)
    status: Mapped[str] = mapped_column(String(32), default="DRAFT", index=True)
    revision: Mapped[int] = mapped_column(Integer, default=1)
    created_by: Mapped[str] = mapped_column(String(100), default="developer")
    created_at: Mapped[datetime] = mapped_column(DateTime(timezone=True), default=utc_now)
    updated_at: Mapped[datetime] = mapped_column(
        DateTime(timezone=True), default=utc_now, onupdate=utc_now
    )


class ActionInvocationRow(Base):
    __tablename__ = "action_invocations"
    __table_args__ = (
        UniqueConstraint("project_id", "idempotency_key", name="uq_action_invocation_idempotency"),
    )

    id: Mapped[str] = mapped_column(String(36), primary_key=True, default=new_id)
    project_id: Mapped[str] = mapped_column(
        String(36), ForeignKey("projects.id", ondelete="CASCADE"), index=True
    )
    action_definition_id: Mapped[str] = mapped_column(
        String(36), ForeignKey("action_definitions.id", ondelete="RESTRICT"), index=True
    )
    source_agent_run_id: Mapped[str | None] = mapped_column(
        String(36), ForeignKey("agent_runs.id", ondelete="SET NULL"), index=True
    )
    idempotency_key: Mapped[str] = mapped_column(String(200))
    requested_by: Mapped[str] = mapped_column(String(100))
    target_entity_ids: Mapped[list[str]] = mapped_column(JSON, default=list)
    input: Mapped[dict[str, Any]] = mapped_column(JSON, default=dict)
    status: Mapped[str] = mapped_column(String(32), default="DRAFT", index=True)
    preflight: Mapped[dict[str, Any] | None] = mapped_column(JSON)
    result: Mapped[dict[str, Any] | None] = mapped_column(JSON)
    error: Mapped[dict[str, Any] | None] = mapped_column(JSON)
    approved_by: Mapped[str | None] = mapped_column(String(100))
    approved_at: Mapped[datetime | None] = mapped_column(DateTime(timezone=True))
    started_at: Mapped[datetime | None] = mapped_column(DateTime(timezone=True))
    finished_at: Mapped[datetime | None] = mapped_column(DateTime(timezone=True))
    created_at: Mapped[datetime] = mapped_column(DateTime(timezone=True), default=utc_now)
    updated_at: Mapped[datetime] = mapped_column(
        DateTime(timezone=True), default=utc_now, onupdate=utc_now
    )


class ManagementActionRow(Base):
    """A human-owned management task, separate from executable action invocations."""

    __tablename__ = "management_actions"
    __table_args__ = (
        Index(
            "uq_management_action_project_idempotency",
            "project_id",
            "idempotency_key",
            unique=True,
        ),
        Index("ix_management_action_project_status_due", "project_id", "status", "due_at"),
        CheckConstraint(
            "status IN ('OPEN', 'IN_PROGRESS', 'CANCELLED')",
            name="ck_management_action_status",
        ),
        CheckConstraint(
            "priority IN ('LOW', 'NORMAL', 'HIGH', 'URGENT')",
            name="ck_management_action_priority",
        ),
        CheckConstraint(
            "verified_done_at IS NULL OR reported_done_at IS NOT NULL",
            name="ck_management_action_verified_requires_reported",
        ),
    )

    id: Mapped[str] = mapped_column(String(36), primary_key=True, default=new_id)
    company_id: Mapped[str] = mapped_column(
        String(36), ForeignKey("companies.id", ondelete="RESTRICT"), index=True
    )
    project_id: Mapped[str] = mapped_column(
        String(36), ForeignKey("projects.id", ondelete="RESTRICT"), index=True
    )
    idempotency_key: Mapped[str | None] = mapped_column(String(200))
    idempotency_hash: Mapped[str | None] = mapped_column(String(64))
    title: Mapped[str] = mapped_column(String(200))
    description: Mapped[str | None] = mapped_column(Text)
    owner: Mapped[str | None] = mapped_column(String(200))
    priority: Mapped[str] = mapped_column(String(24), default="NORMAL", index=True)
    status: Mapped[str] = mapped_column(String(24), default="OPEN", index=True)
    due_at: Mapped[datetime | None] = mapped_column(DateTime(timezone=True))
    reported_done_at: Mapped[datetime | None] = mapped_column(DateTime(timezone=True))
    reported_done_by: Mapped[str | None] = mapped_column(String(128))
    verified_done_at: Mapped[datetime | None] = mapped_column(DateTime(timezone=True))
    verified_done_by: Mapped[str | None] = mapped_column(String(128))
    revision: Mapped[int] = mapped_column(Integer, default=1)
    created_by: Mapped[str] = mapped_column(String(128))
    created_at: Mapped[datetime] = mapped_column(DateTime(timezone=True), default=utc_now)
    updated_at: Mapped[datetime] = mapped_column(
        DateTime(timezone=True), default=utc_now, onupdate=utc_now
    )

    __mapper_args__ = {"version_id_col": revision, "version_id_generator": False}

    @property
    def reported_done(self) -> bool:
        return self.reported_done_at is not None

    @property
    def verified_done(self) -> bool:
        return self.verified_done_at is not None


class ManagementActionEventRow(Base):
    """Append-only history for management-action changes and reported results."""

    __tablename__ = "management_action_events"
    __table_args__ = (
        UniqueConstraint("action_id", "revision", name="uq_management_action_event_revision"),
        Index("ix_management_action_event_project_created", "project_id", "created_at"),
    )

    id: Mapped[str] = mapped_column(String(36), primary_key=True, default=new_id)
    company_id: Mapped[str] = mapped_column(
        String(36), ForeignKey("companies.id", ondelete="RESTRICT"), index=True
    )
    project_id: Mapped[str] = mapped_column(
        String(36), ForeignKey("projects.id", ondelete="RESTRICT"), index=True
    )
    action_id: Mapped[str] = mapped_column(
        String(36), ForeignKey("management_actions.id", ondelete="RESTRICT"), index=True
    )
    revision: Mapped[int] = mapped_column(Integer)
    event_type: Mapped[str] = mapped_column(String(32), index=True)
    message: Mapped[str | None] = mapped_column(Text)
    details: Mapped[dict[str, Any]] = mapped_column(JSON, default=dict)
    from_status: Mapped[str | None] = mapped_column(String(24))
    to_status: Mapped[str | None] = mapped_column(String(24))
    actor_id: Mapped[str] = mapped_column(String(128))
    reason: Mapped[str | None] = mapped_column(Text)
    created_at: Mapped[datetime] = mapped_column(DateTime(timezone=True), default=utc_now)


class ActionLogRow(Base):
    __tablename__ = "action_logs"

    id: Mapped[str] = mapped_column(String(36), primary_key=True, default=new_id)
    project_id: Mapped[str] = mapped_column(
        String(36), ForeignKey("projects.id", ondelete="CASCADE"), index=True
    )
    invocation_id: Mapped[str] = mapped_column(
        String(36), ForeignKey("action_invocations.id", ondelete="CASCADE"), index=True
    )
    event_type: Mapped[str] = mapped_column(String(64))
    from_status: Mapped[str | None] = mapped_column(String(32))
    to_status: Mapped[str | None] = mapped_column(String(32))
    actor: Mapped[str] = mapped_column(String(100))
    details: Mapped[dict[str, Any]] = mapped_column(JSON, default=dict)
    created_at: Mapped[datetime] = mapped_column(DateTime(timezone=True), default=utc_now)


class ActionObservationRow(Base):
    __tablename__ = "action_observations"

    id: Mapped[str] = mapped_column(String(36), primary_key=True, default=new_id)
    project_id: Mapped[str] = mapped_column(
        String(36), ForeignKey("projects.id", ondelete="CASCADE"), index=True
    )
    invocation_id: Mapped[str] = mapped_column(
        String(36), ForeignKey("action_invocations.id", ondelete="CASCADE"), index=True
    )
    observation_kind: Mapped[str] = mapped_column(String(32), default="QUALITATIVE")
    metric_key: Mapped[str] = mapped_column(String(200))
    metric_definition_id: Mapped[str | None] = mapped_column(
        String(36), ForeignKey("metric_definitions.id", ondelete="SET NULL"), index=True
    )
    metric_observation_id: Mapped[str | None] = mapped_column(
        String(36), ForeignKey("metric_observations.id", ondelete="SET NULL"), index=True
    )
    period_key: Mapped[str | None] = mapped_column(String(100))
    dimensions: Mapped[dict[str, Any]] = mapped_column(JSON, default=dict)
    observed_value: Mapped[Any] = mapped_column(JSON)
    outcome: Mapped[str] = mapped_column(String(32), default="UNKNOWN")
    note: Mapped[str | None] = mapped_column(Text)
    observed_at: Mapped[datetime] = mapped_column(DateTime(timezone=True), default=utc_now)
    created_at: Mapped[datetime] = mapped_column(DateTime(timezone=True), default=utc_now)


class HypothesisRow(Base):
    __tablename__ = "hypotheses"

    id: Mapped[str] = mapped_column(String(36), primary_key=True, default=new_id)
    project_id: Mapped[str] = mapped_column(
        String(36), ForeignKey("projects.id", ondelete="CASCADE"), index=True
    )
    type_key: Mapped[str] = mapped_column(String(100), index=True)
    schema_version: Mapped[int] = mapped_column(Integer, default=1)
    title: Mapped[str] = mapped_column(String(300))
    summary: Mapped[str] = mapped_column(Text)
    participant_entity_ids: Mapped[list[str]] = mapped_column(JSON, default=list)
    supporting_facts: Mapped[list[str]] = mapped_column(JSON, default=list)
    counter_evidence: Mapped[list[str]] = mapped_column(JSON, default=list)
    alternative_explanations: Mapped[list[str]] = mapped_column(JSON, default=list)
    uncertainties: Mapped[list[str]] = mapped_column(JSON, default=list)
    validation_questions: Mapped[list[str]] = mapped_column(JSON, default=list)
    extension_payload: Mapped[dict[str, Any]] = mapped_column(JSON, default=dict)
    status: Mapped[str] = mapped_column(String(32), default="EXPLORING", index=True)
    source: Mapped[str] = mapped_column(String(64), default="MANAGEMENT")
    module_id: Mapped[str | None] = mapped_column(String(200))
    module_version: Mapped[str | None] = mapped_column(String(64))
    created_at: Mapped[datetime] = mapped_column(DateTime(timezone=True), default=utc_now)
    updated_at: Mapped[datetime] = mapped_column(
        DateTime(timezone=True), default=utc_now, onupdate=utc_now
    )


class HypothesisFeedbackRow(Base):
    __tablename__ = "hypothesis_feedback"

    id: Mapped[str] = mapped_column(String(36), primary_key=True, default=new_id)
    hypothesis_id: Mapped[str] = mapped_column(
        String(36), ForeignKey("hypotheses.id", ondelete="CASCADE"), index=True
    )
    status: Mapped[str] = mapped_column(String(32))
    comment: Mapped[str | None] = mapped_column(Text)
    provided_by: Mapped[str] = mapped_column(String(100))
    created_at: Mapped[datetime] = mapped_column(DateTime(timezone=True), default=utc_now)


class ScenarioRow(Base):
    __tablename__ = "scenarios"

    id: Mapped[str] = mapped_column(String(36), primary_key=True, default=new_id)
    project_id: Mapped[str] = mapped_column(
        String(36), ForeignKey("projects.id", ondelete="CASCADE"), index=True
    )
    name: Mapped[str] = mapped_column(String(300))
    description: Mapped[str | None] = mapped_column(Text)
    goal: Mapped[str] = mapped_column(Text)
    assumptions: Mapped[list[str]] = mapped_column(JSON, default=list)
    overlay_operations: Mapped[list[dict[str, Any]]] = mapped_column(JSON, default=list)
    expected_benefits: Mapped[list[str]] = mapped_column(JSON, default=list)
    risks: Mapped[list[str]] = mapped_column(JSON, default=list)
    validation_metrics: Mapped[list[str]] = mapped_column(JSON, default=list)
    base_revision: Mapped[int] = mapped_column(Integer, default=0)
    applied_change_set_id: Mapped[str | None] = mapped_column(
        String(36), ForeignKey("change_sets.id", ondelete="SET NULL"), index=True
    )
    status: Mapped[str] = mapped_column(String(32), default="DRAFT", index=True)
    revision: Mapped[int] = mapped_column(Integer, default=1)
    created_at: Mapped[datetime] = mapped_column(DateTime(timezone=True), default=utc_now)
    updated_at: Mapped[datetime] = mapped_column(
        DateTime(timezone=True), default=utc_now, onupdate=utc_now
    )


class ScenarioRunRow(Base):
    """Immutable result of a deterministic or explicitly exploratory run."""

    __tablename__ = "scenario_runs"
    __table_args__ = (
        Index("ix_scenario_runs_project_created", "project_id", "created_at"),
        Index("ix_scenario_runs_scenario_revision", "scenario_id", "scenario_revision"),
    )

    id: Mapped[str] = mapped_column(String(36), primary_key=True, default=new_id)
    project_id: Mapped[str] = mapped_column(
        String(36), ForeignKey("projects.id", ondelete="CASCADE"), index=True
    )
    scenario_id: Mapped[str] = mapped_column(
        String(36), ForeignKey("scenarios.id", ondelete="CASCADE"), index=True
    )
    scenario_revision: Mapped[int] = mapped_column(Integer)
    baseline_revision: Mapped[int] = mapped_column(Integer)
    status: Mapped[str] = mapped_column(String(32), default="SUCCEEDED", index=True)
    input_snapshot: Mapped[dict[str, Any]] = mapped_column(JSON, default=dict)
    rule_snapshot: Mapped[dict[str, Any]] = mapped_column(JSON, default=dict)
    result: Mapped[dict[str, Any]] = mapped_column(JSON, default=dict)
    errors: Mapped[list[dict[str, Any]]] = mapped_column(JSON, default=list)
    created_by: Mapped[str] = mapped_column(String(128), default="management")
    created_at: Mapped[datetime] = mapped_column(DateTime(timezone=True), default=utc_now)


class LearningCaseRow(Base):
    __tablename__ = "learning_cases"

    id: Mapped[str] = mapped_column(String(36), primary_key=True, default=new_id)
    project_id: Mapped[str] = mapped_column(
        String(36), ForeignKey("projects.id", ondelete="CASCADE"), index=True
    )
    source_action_invocation_id: Mapped[str | None] = mapped_column(
        String(36), ForeignKey("action_invocations.id", ondelete="SET NULL"), index=True
    )
    source_action_observation_ids: Mapped[list[str]] = mapped_column(JSON, default=list)
    source_scenario_id: Mapped[str | None] = mapped_column(
        String(36), ForeignKey("scenarios.id", ondelete="SET NULL"), index=True
    )
    origin_kind: Mapped[str] = mapped_column(String(32), default="MANUAL", index=True)
    title: Mapped[str] = mapped_column(String(300), index=True)
    industry: Mapped[str | None] = mapped_column(String(200), index=True)
    organization_scale: Mapped[str | None] = mapped_column(String(200))
    challenge: Mapped[str] = mapped_column(Text)
    context: Mapped[str | None] = mapped_column(Text)
    intervention: Mapped[str | None] = mapped_column(Text)
    outcome: Mapped[str | None] = mapped_column(Text)
    lessons: Mapped[list[str]] = mapped_column(JSON, default=list)
    tags: Mapped[list[str]] = mapped_column(JSON, default=list)
    evidence: Mapped[list[dict[str, Any]]] = mapped_column(JSON, default=list)
    reusable: Mapped[bool] = mapped_column(Boolean, default=False, index=True)
    reusable_summary: Mapped[str | None] = mapped_column(Text)
    status: Mapped[str] = mapped_column(String(32), default="DRAFT", index=True)
    revision: Mapped[int] = mapped_column(Integer, default=1)
    created_at: Mapped[datetime] = mapped_column(DateTime(timezone=True), default=utc_now)
    updated_at: Mapped[datetime] = mapped_column(
        DateTime(timezone=True), default=utc_now, onupdate=utc_now
    )


class MetricDefinitionRow(Base):
    __tablename__ = "metric_definitions"
    __table_args__ = (UniqueConstraint("project_id", "key", name="uq_metric_definition_key"),)

    id: Mapped[str] = mapped_column(String(36), primary_key=True, default=new_id)
    project_id: Mapped[str] = mapped_column(
        String(36), ForeignKey("projects.id", ondelete="CASCADE"), index=True
    )
    entity_id: Mapped[str] = mapped_column(
        String(36), ForeignKey("entities.id", ondelete="RESTRICT"), unique=True, index=True
    )
    key: Mapped[str] = mapped_column(String(100))
    name: Mapped[str] = mapped_column(String(300))
    description: Mapped[str | None] = mapped_column(Text)
    scope: Mapped[str] = mapped_column(String(32), default="LOCAL", index=True)
    direction: Mapped[str] = mapped_column(String(32), default="HIGHER_IS_BETTER")
    owner_entity_id: Mapped[str | None] = mapped_column(
        String(36), ForeignKey("entities.id", ondelete="SET NULL"), index=True
    )
    strategy_entity_id: Mapped[str | None] = mapped_column(
        String(36), ForeignKey("entities.id", ondelete="SET NULL"), index=True
    )
    outcome_entity_id: Mapped[str | None] = mapped_column(
        String(36), ForeignKey("entities.id", ondelete="SET NULL"), index=True
    )
    unit: Mapped[str | None] = mapped_column(String(100))
    target_value: Mapped[Any | None] = mapped_column(JSON)
    properties: Mapped[dict[str, Any]] = mapped_column(JSON, default=dict)
    active: Mapped[bool] = mapped_column(Boolean, default=True, index=True)
    revision: Mapped[int] = mapped_column(Integer, default=1)
    created_at: Mapped[datetime] = mapped_column(DateTime(timezone=True), default=utc_now)
    updated_at: Mapped[datetime] = mapped_column(
        DateTime(timezone=True), default=utc_now, onupdate=utc_now
    )


class MetricObservationRow(Base):
    __tablename__ = "metric_observations"
    __table_args__ = (
        UniqueConstraint(
            "metric_definition_id",
            "period_key",
            "dimension_key",
            "source",
            "version",
            name="uq_metric_observation_version",
        ),
    )

    id: Mapped[str] = mapped_column(String(36), primary_key=True, default=new_id)
    project_id: Mapped[str] = mapped_column(
        String(36), ForeignKey("projects.id", ondelete="CASCADE"), index=True
    )
    metric_definition_id: Mapped[str] = mapped_column(
        String(36), ForeignKey("metric_definitions.id", ondelete="CASCADE"), index=True
    )
    period_key: Mapped[str] = mapped_column(String(100), index=True)
    period_start: Mapped[datetime | None] = mapped_column(DateTime(timezone=True), index=True)
    period_end: Mapped[datetime | None] = mapped_column(DateTime(timezone=True), index=True)
    dimensions: Mapped[dict[str, Any]] = mapped_column(JSON, default=dict)
    dimension_key: Mapped[str] = mapped_column(String(64), default="{}", index=True)
    observed_at: Mapped[datetime] = mapped_column(DateTime(timezone=True), index=True)
    value: Mapped[Any] = mapped_column(JSON)
    numeric_value: Mapped[float | None] = mapped_column(Float)
    status: Mapped[str] = mapped_column(String(32), default="UNKNOWN", index=True)
    source: Mapped[str] = mapped_column(String(100), default="MANUAL")
    unit: Mapped[str | None] = mapped_column(String(100))
    definition_revision: Mapped[int] = mapped_column(Integer, default=1)
    version: Mapped[int] = mapped_column(Integer, default=1)
    record_status: Mapped[str] = mapped_column(String(32), default="ACTIVE", index=True)
    supersedes_id: Mapped[str | None] = mapped_column(
        String(36), ForeignKey("metric_observations.id", ondelete="SET NULL"), index=True
    )
    evidence: Mapped[list[dict[str, Any]]] = mapped_column(JSON, default=list)
    revision: Mapped[int] = mapped_column(Integer, default=1)
    created_at: Mapped[datetime] = mapped_column(DateTime(timezone=True), default=utc_now)
    updated_at: Mapped[datetime] = mapped_column(
        DateTime(timezone=True), default=utc_now, onupdate=utc_now
    )


class MeetingRecordRow(Base):
    __tablename__ = "meeting_records"

    id: Mapped[str] = mapped_column(String(36), primary_key=True, default=new_id)
    project_id: Mapped[str] = mapped_column(
        String(36), ForeignKey("projects.id", ondelete="CASCADE"), index=True
    )
    title: Mapped[str] = mapped_column(String(300), index=True)
    occurred_at: Mapped[datetime] = mapped_column(DateTime(timezone=True), index=True)
    participant_entity_ids: Mapped[list[str]] = mapped_column(JSON, default=list)
    related_entity_ids: Mapped[list[str]] = mapped_column(JSON, default=list)
    topics: Mapped[list[str]] = mapped_column(JSON, default=list)
    decisions: Mapped[list[dict[str, Any]]] = mapped_column(JSON, default=list)
    action_items: Mapped[list[dict[str, Any]]] = mapped_column(JSON, default=list)
    escalations: Mapped[list[dict[str, Any]]] = mapped_column(JSON, default=list)
    evidence: Mapped[list[dict[str, Any]]] = mapped_column(JSON, default=list)
    revision: Mapped[int] = mapped_column(Integer, default=1)
    created_at: Mapped[datetime] = mapped_column(DateTime(timezone=True), default=utc_now)
    updated_at: Mapped[datetime] = mapped_column(
        DateTime(timezone=True), default=utc_now, onupdate=utc_now
    )


class DesignTradeoffRow(Base):
    __tablename__ = "design_tradeoffs"

    id: Mapped[str] = mapped_column(String(36), primary_key=True, default=new_id)
    project_id: Mapped[str] = mapped_column(
        String(36), ForeignKey("projects.id", ondelete="CASCADE"), index=True
    )
    title: Mapped[str] = mapped_column(String(300))
    issue_family: Mapped[str] = mapped_column(String(100), index=True)
    description: Mapped[str] = mapped_column(Text)
    benefit: Mapped[str] = mapped_column(Text)
    cost: Mapped[str] = mapped_column(Text)
    affected_entity_ids: Mapped[list[str]] = mapped_column(JSON, default=list)
    monitoring_metric_ids: Mapped[list[str]] = mapped_column(JSON, default=list)
    status: Mapped[str] = mapped_column(String(32), default="PROPOSED", index=True)
    accepted_by: Mapped[str | None] = mapped_column(String(100))
    revision: Mapped[int] = mapped_column(Integer, default=1)
    created_at: Mapped[datetime] = mapped_column(DateTime(timezone=True), default=utc_now)
    updated_at: Mapped[datetime] = mapped_column(
        DateTime(timezone=True), default=utc_now, onupdate=utc_now
    )


class ManagementAnalysisRunRow(Base):
    __tablename__ = "management_analysis_runs"

    id: Mapped[str] = mapped_column(String(36), primary_key=True, default=new_id)
    project_id: Mapped[str] = mapped_column(
        String(36), ForeignKey("projects.id", ondelete="CASCADE"), index=True
    )
    requested_by: Mapped[str] = mapped_column(String(100))
    status: Mapped[str] = mapped_column(String(32), default="RUNNING", index=True)
    parameters: Mapped[dict[str, Any]] = mapped_column(JSON, default=dict)
    design_signal_count: Mapped[int] = mapped_column(Integer, default=0)
    outcome_signal_count: Mapped[int] = mapped_column(Integer, default=0)
    insight_count: Mapped[int] = mapped_column(Integer, default=0)
    error: Mapped[dict[str, Any] | None] = mapped_column(JSON)
    created_at: Mapped[datetime] = mapped_column(DateTime(timezone=True), default=utc_now)
    finished_at: Mapped[datetime | None] = mapped_column(DateTime(timezone=True))


class ManagementSignalRow(Base):
    __tablename__ = "management_signals"
    __table_args__ = (UniqueConstraint("run_id", "fingerprint", name="uq_signal_run_fp"),)

    id: Mapped[str] = mapped_column(String(36), primary_key=True, default=new_id)
    run_id: Mapped[str] = mapped_column(
        String(36), ForeignKey("management_analysis_runs.id", ondelete="CASCADE"), index=True
    )
    project_id: Mapped[str] = mapped_column(
        String(36), ForeignKey("projects.id", ondelete="CASCADE"), index=True
    )
    side: Mapped[str] = mapped_column(String(32), index=True)
    signal_key: Mapped[str] = mapped_column(String(100), index=True)
    issue_family: Mapped[str] = mapped_column(String(100), index=True)
    title: Mapped[str] = mapped_column(String(300))
    summary: Mapped[str] = mapped_column(Text)
    severity: Mapped[str] = mapped_column(String(32), index=True)
    confidence: Mapped[float] = mapped_column(Float, default=0.5)
    expected_direction: Mapped[str] = mapped_column(String(32), default="PROBLEM")
    affected_entity_ids: Mapped[list[str]] = mapped_column(JSON, default=list)
    evidence: Mapped[list[dict[str, Any]]] = mapped_column(JSON, default=list)
    facts: Mapped[dict[str, Any]] = mapped_column(JSON, default=dict)
    fingerprint: Mapped[str] = mapped_column(String(64))
    created_at: Mapped[datetime] = mapped_column(DateTime(timezone=True), default=utc_now)


class ManagementIssueRow(Base):
    __tablename__ = "management_issues"
    __table_args__ = (
        UniqueConstraint("project_id", "issue_key", name="uq_management_issue_key"),
    )

    id: Mapped[str] = mapped_column(String(36), primary_key=True, default=new_id)
    project_id: Mapped[str] = mapped_column(
        String(36), ForeignKey("projects.id", ondelete="CASCADE"), index=True
    )
    issue_key: Mapped[str] = mapped_column(String(64), index=True)
    issue_family: Mapped[str] = mapped_column(String(100), index=True)
    title: Mapped[str] = mapped_column(String(300))
    affected_entity_ids: Mapped[list[str]] = mapped_column(JSON, default=list)
    status: Mapped[str] = mapped_column(String(32), default="OPEN", index=True)
    management_feedback: Mapped[str | None] = mapped_column(Text)
    last_occurrence_signature: Mapped[str] = mapped_column(String(64))
    occurrence_count: Mapped[int] = mapped_column(Integer, default=0)
    revision: Mapped[int] = mapped_column(Integer, default=1)
    first_seen_at: Mapped[datetime] = mapped_column(DateTime(timezone=True), default=utc_now)
    last_seen_at: Mapped[datetime] = mapped_column(DateTime(timezone=True), default=utc_now)
    created_at: Mapped[datetime] = mapped_column(DateTime(timezone=True), default=utc_now)
    updated_at: Mapped[datetime] = mapped_column(
        DateTime(timezone=True), default=utc_now, onupdate=utc_now
    )


class ManagementInsightRow(Base):
    __tablename__ = "management_insights"

    id: Mapped[str] = mapped_column(String(36), primary_key=True, default=new_id)
    run_id: Mapped[str] = mapped_column(
        String(36), ForeignKey("management_analysis_runs.id", ondelete="CASCADE"), index=True
    )
    project_id: Mapped[str] = mapped_column(
        String(36), ForeignKey("projects.id", ondelete="CASCADE"), index=True
    )
    issue_id: Mapped[str | None] = mapped_column(
        String(36), ForeignKey("management_issues.id", ondelete="CASCADE"), index=True
    )
    occurrence_number: Mapped[int] = mapped_column(Integer, default=1)
    occurrence_signature: Mapped[str | None] = mapped_column(String(64), index=True)
    evidence_changed: Mapped[bool] = mapped_column(Boolean, default=False)
    classification: Mapped[str] = mapped_column(String(32), index=True)
    issue_family: Mapped[str] = mapped_column(String(100), index=True)
    title: Mapped[str] = mapped_column(String(300))
    summary: Mapped[str] = mapped_column(Text)
    severity: Mapped[str] = mapped_column(String(32), index=True)
    confidence: Mapped[float] = mapped_column(Float, default=0.5)
    design_signal_ids: Mapped[list[str]] = mapped_column(JSON, default=list)
    outcome_signal_ids: Mapped[list[str]] = mapped_column(JSON, default=list)
    affected_entity_ids: Mapped[list[str]] = mapped_column(JSON, default=list)
    status: Mapped[str] = mapped_column(String(32), default="OPEN", index=True)
    rationale: Mapped[str] = mapped_column(Text)
    management_feedback: Mapped[str | None] = mapped_column(Text)
    revision: Mapped[int] = mapped_column(Integer, default=1)
    created_at: Mapped[datetime] = mapped_column(DateTime(timezone=True), default=utc_now)
    updated_at: Mapped[datetime] = mapped_column(
        DateTime(timezone=True), default=utc_now, onupdate=utc_now
    )


class ManagementIssueFeedbackRow(Base):
    __tablename__ = "management_issue_feedback"

    id: Mapped[str] = mapped_column(String(36), primary_key=True, default=new_id)
    project_id: Mapped[str] = mapped_column(
        String(36), ForeignKey("projects.id", ondelete="CASCADE"), index=True
    )
    issue_id: Mapped[str] = mapped_column(
        String(36), ForeignKey("management_issues.id", ondelete="CASCADE"), index=True
    )
    insight_id: Mapped[str | None] = mapped_column(
        String(36), ForeignKey("management_insights.id", ondelete="SET NULL"), index=True
    )
    from_status: Mapped[str] = mapped_column(String(32))
    to_status: Mapped[str] = mapped_column(String(32))
    feedback: Mapped[str | None] = mapped_column(Text)
    provided_by: Mapped[str] = mapped_column(String(100), default="management")
    created_at: Mapped[datetime] = mapped_column(DateTime(timezone=True), default=utc_now)


class InformationRequestRow(Base):
    __tablename__ = "information_requests"

    id: Mapped[str] = mapped_column(String(36), primary_key=True, default=new_id)
    project_id: Mapped[str] = mapped_column(
        String(36), ForeignKey("projects.id", ondelete="CASCADE"), index=True
    )
    title: Mapped[str] = mapped_column(String(300))
    question: Mapped[str] = mapped_column(Text)
    reason: Mapped[str] = mapped_column(Text)
    priority: Mapped[str] = mapped_column(String(32), default="MEDIUM", index=True)
    target_entity_ids: Mapped[list[str]] = mapped_column(JSON, default=list)
    requested_by: Mapped[str] = mapped_column(String(100), default="management-agent")
    status: Mapped[str] = mapped_column(String(32), default="OPEN", index=True)
    answer: Mapped[str | None] = mapped_column(Text)
    revision: Mapped[int] = mapped_column(Integer, default=1)
    created_at: Mapped[datetime] = mapped_column(DateTime(timezone=True), default=utc_now)
    updated_at: Mapped[datetime] = mapped_column(
        DateTime(timezone=True), default=utc_now, onupdate=utc_now
    )


class EvaluationSuiteRow(Base):
    __tablename__ = "evaluation_suites"
    __table_args__ = (UniqueConstraint("project_id", "name", name="uq_evaluation_suite_name"),)

    id: Mapped[str] = mapped_column(String(36), primary_key=True, default=new_id)
    project_id: Mapped[str] = mapped_column(
        String(36), ForeignKey("projects.id", ondelete="CASCADE"), index=True
    )
    name: Mapped[str] = mapped_column(String(300))
    agent_kind: Mapped[str] = mapped_column(String(32), index=True)
    description: Mapped[str | None] = mapped_column(Text)
    status: Mapped[str] = mapped_column(String(32), default="DRAFT", index=True)
    revision: Mapped[int] = mapped_column(Integer, default=1)
    created_at: Mapped[datetime] = mapped_column(DateTime(timezone=True), default=utc_now)
    updated_at: Mapped[datetime] = mapped_column(
        DateTime(timezone=True), default=utc_now, onupdate=utc_now
    )


class EvaluationCaseRow(Base):
    __tablename__ = "evaluation_cases"
    __table_args__ = (UniqueConstraint("suite_id", "name", name="uq_evaluation_case_name"),)

    id: Mapped[str] = mapped_column(String(36), primary_key=True, default=new_id)
    suite_id: Mapped[str] = mapped_column(
        String(36), ForeignKey("evaluation_suites.id", ondelete="CASCADE"), index=True
    )
    project_id: Mapped[str] = mapped_column(
        String(36), ForeignKey("projects.id", ondelete="CASCADE"), index=True
    )
    name: Mapped[str] = mapped_column(String(300))
    input: Mapped[str] = mapped_column(Text)
    expected_action_keys: Mapped[list[str]] = mapped_column(JSON, default=list)
    required_terms: Mapped[list[str]] = mapped_column(JSON, default=list)
    forbidden_terms: Mapped[list[str]] = mapped_column(JSON, default=list)
    minimum_citations: Mapped[int] = mapped_column(Integer, default=0)
    status: Mapped[str] = mapped_column(String(32), default="ACTIVE", index=True)
    created_at: Mapped[datetime] = mapped_column(DateTime(timezone=True), default=utc_now)


class EvaluationRunRow(Base):
    __tablename__ = "evaluation_runs"

    id: Mapped[str] = mapped_column(String(36), primary_key=True, default=new_id)
    project_id: Mapped[str] = mapped_column(
        String(36), ForeignKey("projects.id", ondelete="CASCADE"), index=True
    )
    suite_id: Mapped[str] = mapped_column(
        String(36), ForeignKey("evaluation_suites.id", ondelete="CASCADE"), index=True
    )
    model_profile_id: Mapped[str | None] = mapped_column(
        String(36), ForeignKey("model_profiles.id", ondelete="SET NULL"), index=True
    )
    label: Mapped[str | None] = mapped_column(String(300))
    status: Mapped[str] = mapped_column(String(32), default="COMPLETED", index=True)
    total_cases: Mapped[int] = mapped_column(Integer, default=0)
    passed_cases: Mapped[int] = mapped_column(Integer, default=0)
    average_score: Mapped[float] = mapped_column(Float, default=0.0)
    created_at: Mapped[datetime] = mapped_column(DateTime(timezone=True), default=utc_now)


class EvaluationExecutionRow(Base):
    """Durable coordinator for evaluation runs submitted to the Agent Worker."""

    __tablename__ = "evaluation_executions"
    __table_args__ = (
        # The application-level lookup prevents normal duplicate clicks.  This
        # partial unique index is the final guard when two API processes race
        # before either one can observe the other's uncommitted row.
        Index(
            "uq_evaluation_execution_active",
            "project_id",
            "suite_id",
            unique=True,
            sqlite_where=text("status IN ('QUEUED', 'RUNNING', 'FINALIZING')"),
            postgresql_where=text("status IN ('QUEUED', 'RUNNING', 'FINALIZING')"),
        ),
    )

    id: Mapped[str] = mapped_column(String(36), primary_key=True, default=new_id)
    project_id: Mapped[str] = mapped_column(
        String(36), ForeignKey("projects.id", ondelete="CASCADE"), index=True
    )
    suite_id: Mapped[str] = mapped_column(
        String(36), ForeignKey("evaluation_suites.id", ondelete="CASCADE"), index=True
    )
    evaluation_run_id: Mapped[str | None] = mapped_column(
        String(36), ForeignKey("evaluation_runs.id", ondelete="SET NULL"), index=True
    )
    model_profile_id: Mapped[str | None] = mapped_column(
        String(36), ForeignKey("model_profiles.id", ondelete="SET NULL"), index=True
    )
    label: Mapped[str | None] = mapped_column(String(300))
    status: Mapped[str] = mapped_column(String(32), default="QUEUED", index=True)
    total_cases: Mapped[int] = mapped_column(Integer, default=0)
    completed_cases: Mapped[int] = mapped_column(Integer, default=0)
    agent_run_ids: Mapped[list[dict[str, str]]] = mapped_column(JSON, default=list)
    error: Mapped[dict[str, Any] | None] = mapped_column(JSON)
    created_at: Mapped[datetime] = mapped_column(DateTime(timezone=True), default=utc_now)
    updated_at: Mapped[datetime] = mapped_column(
        DateTime(timezone=True), default=utc_now, onupdate=utc_now
    )


class EvaluationResultRow(Base):
    __tablename__ = "evaluation_results"
    __table_args__ = (UniqueConstraint("run_id", "case_id", name="uq_evaluation_result_case"),)

    id: Mapped[str] = mapped_column(String(36), primary_key=True, default=new_id)
    run_id: Mapped[str] = mapped_column(
        String(36), ForeignKey("evaluation_runs.id", ondelete="CASCADE"), index=True
    )
    case_id: Mapped[str] = mapped_column(
        String(36), ForeignKey("evaluation_cases.id", ondelete="CASCADE"), index=True
    )
    project_id: Mapped[str] = mapped_column(
        String(36), ForeignKey("projects.id", ondelete="CASCADE"), index=True
    )
    candidate_content: Mapped[str] = mapped_column(Text)
    candidate_action_keys: Mapped[list[str]] = mapped_column(JSON, default=list)
    candidate_citation_count: Mapped[int] = mapped_column(Integer, default=0)
    score: Mapped[float] = mapped_column(Float)
    passed: Mapped[bool] = mapped_column(Boolean, index=True)
    checks: Mapped[list[dict[str, Any]]] = mapped_column(JSON, default=list)
    created_at: Mapped[datetime] = mapped_column(DateTime(timezone=True), default=utc_now)


class PublicationRow(Base):
    __tablename__ = "publications"
    __table_args__ = (UniqueConstraint("project_id", "version", name="uq_publication_version"),)

    id: Mapped[str] = mapped_column(String(36), primary_key=True, default=new_id)
    project_id: Mapped[str] = mapped_column(
        String(36), ForeignKey("projects.id", ondelete="CASCADE"), index=True
    )
    version: Mapped[int] = mapped_column(Integer)
    label: Mapped[str] = mapped_column(String(200))
    notes: Mapped[str | None] = mapped_column(Text)
    project_revision: Mapped[int] = mapped_column(Integer)
    ontology_release_id: Mapped[str | None] = mapped_column(String(36))
    graph_snapshot: Mapped[dict[str, Any]] = mapped_column(JSON)
    entity_count: Mapped[int] = mapped_column(Integer)
    relation_count: Mapped[int] = mapped_column(Integer)
    created_at: Mapped[datetime] = mapped_column(DateTime(timezone=True), default=utc_now)


class QuerySnapshotRow(Base):
    __tablename__ = "query_snapshots"

    id: Mapped[str] = mapped_column(String(36), primary_key=True, default=new_id)
    project_id: Mapped[str] = mapped_column(
        String(36), ForeignKey("projects.id", ondelete="CASCADE"), index=True
    )
    model_scope: Mapped[str] = mapped_column(String(32), index=True)
    publication_id: Mapped[str | None] = mapped_column(
        String(36), ForeignKey("publications.id", ondelete="RESTRICT"), index=True
    )
    project_revision: Mapped[int] = mapped_column(Integer)
    data_cutoff: Mapped[datetime] = mapped_column(DateTime(timezone=True), index=True)
    graph_snapshot: Mapped[dict[str, Any]] = mapped_column(JSON)
    manifest: Mapped[dict[str, Any]] = mapped_column(JSON, default=dict)
    created_at: Mapped[datetime] = mapped_column(DateTime(timezone=True), default=utc_now)


class ImportPreviewRow(Base):
    __tablename__ = "import_previews"

    id: Mapped[str] = mapped_column(String(36), primary_key=True, default=new_id)
    project_id: Mapped[str] = mapped_column(
        String(36), ForeignKey("projects.id", ondelete="CASCADE"), index=True
    )
    source_system_id: Mapped[str | None] = mapped_column(
        String(36), ForeignKey("source_systems.id", ondelete="SET NULL"), index=True
    )
    file_name: Mapped[str] = mapped_column(String(500))
    kind: Mapped[str] = mapped_column(String(32))
    content_sha256: Mapped[str] = mapped_column(String(64))
    detected_encoding: Mapped[str | None] = mapped_column(String(32))
    columns: Mapped[list[str]] = mapped_column(JSON, default=list)
    sample_rows: Mapped[list[dict[str, Any]]] = mapped_column(JSON, default=list)
    all_rows: Mapped[list[dict[str, Any]]] = mapped_column(JSON, default=list)
    suggested_mapping: Mapped[dict[str, str]] = mapped_column(JSON, default=dict)
    warnings: Mapped[list[str]] = mapped_column(JSON, default=list)
    preview_metadata: Mapped[dict[str, Any]] = mapped_column(JSON, default=dict)
    expires_at: Mapped[datetime] = mapped_column(DateTime(timezone=True), index=True)
    consumed_at: Mapped[datetime | None] = mapped_column(DateTime(timezone=True))


class LifecycleDeletionAuditRow(Base):
    """Immutable-by-convention record of temporary-data deletion.

    This table deliberately lives beside the formal store, but it never stores
    the deleted payload.  It records what was removed, why, and whether the
    removal was automatic or explicitly requested by a human.
    """

    __tablename__ = "lifecycle_deletion_audit"
    __table_args__ = (
        Index("ix_lifecycle_deletion_audit_project_created", "project_id", "created_at"),
        Index("ix_lifecycle_deletion_audit_resource", "resource_kind", "resource_id"),
    )

    id: Mapped[str] = mapped_column(String(36), primary_key=True, default=new_id)
    project_id: Mapped[str | None] = mapped_column(
        String(36), ForeignKey("projects.id", ondelete="SET NULL"), index=True
    )
    resource_kind: Mapped[str] = mapped_column(String(64), index=True)
    resource_id: Mapped[str] = mapped_column(String(100), index=True)
    deletion_mode: Mapped[str] = mapped_column(String(16), index=True)
    actor_id: Mapped[str] = mapped_column(String(128))
    reason: Mapped[str] = mapped_column(String(2_000))
    details: Mapped[dict[str, Any]] = mapped_column(JSON, default=dict)
    created_at: Mapped[datetime] = mapped_column(DateTime(timezone=True), default=utc_now)


class SourceDocumentRow(Base):
    __tablename__ = "source_documents"

    id: Mapped[str] = mapped_column(String(36), primary_key=True, default=new_id)
    project_id: Mapped[str] = mapped_column(
        String(36), ForeignKey("projects.id", ondelete="CASCADE"), index=True
    )
    source_system_id: Mapped[str | None] = mapped_column(
        String(36), ForeignKey("source_systems.id", ondelete="SET NULL"), index=True
    )
    file_name: Mapped[str] = mapped_column(String(500))
    kind: Mapped[str] = mapped_column(String(32))
    sha256: Mapped[str] = mapped_column(String(64), index=True)
    status: Mapped[str] = mapped_column(String(32), default="IMPORTED")
    extra_metadata: Mapped[dict[str, Any]] = mapped_column("metadata", JSON, default=dict)
    created_at: Mapped[datetime] = mapped_column(DateTime(timezone=True), default=utc_now)


class EvidenceFragmentRow(Base):
    __tablename__ = "evidence_fragments"

    id: Mapped[str] = mapped_column(String(36), primary_key=True, default=new_id)
    source_document_id: Mapped[str] = mapped_column(
        String(36), ForeignKey("source_documents.id", ondelete="CASCADE"), index=True
    )
    locator: Mapped[str] = mapped_column(String(500))
    text: Mapped[str] = mapped_column(Text)
    extra_metadata: Mapped[dict[str, Any]] = mapped_column("metadata", JSON, default=dict)
    created_at: Mapped[datetime] = mapped_column(DateTime(timezone=True), default=utc_now)


class ClaimRow(Base):
    __tablename__ = "claims"

    id: Mapped[str] = mapped_column(String(36), primary_key=True, default=new_id)
    project_id: Mapped[str] = mapped_column(
        String(36), ForeignKey("projects.id", ondelete="CASCADE"), index=True
    )
    fragment_id: Mapped[str] = mapped_column(
        String(36), ForeignKey("evidence_fragments.id", ondelete="CASCADE"), index=True
    )
    subject: Mapped[str] = mapped_column(String(500))
    predicate: Mapped[str] = mapped_column(String(200))
    value: Mapped[Any] = mapped_column(JSON)
    status: Mapped[str] = mapped_column(String(32), default="CANDIDATE", index=True)
    created_at: Mapped[datetime] = mapped_column(DateTime(timezone=True), default=utc_now)


class SourceSystemRow(Base):
    __tablename__ = "source_systems"

    id: Mapped[str] = mapped_column(String(36), primary_key=True, default=new_id)
    project_id: Mapped[str] = mapped_column(
        String(36), ForeignKey("projects.id", ondelete="CASCADE"), index=True
    )
    name: Mapped[str] = mapped_column(String(200))
    kind: Mapped[str] = mapped_column(String(32))
    description: Mapped[str | None] = mapped_column(Text)
    connection_profile: Mapped[dict[str, Any]] = mapped_column(JSON, default=dict)
    encrypted_connection_secrets: Mapped[str | None] = mapped_column(Text)
    status: Mapped[str] = mapped_column(String(32), default="CONFIGURED")
    last_tested_at: Mapped[datetime | None] = mapped_column(DateTime(timezone=True))
    revision: Mapped[int] = mapped_column(Integer, default=1)
    created_at: Mapped[datetime] = mapped_column(DateTime(timezone=True), default=utc_now)
    updated_at: Mapped[datetime] = mapped_column(
        DateTime(timezone=True), default=utc_now, onupdate=utc_now
    )


class SourceAssetRow(Base):
    __tablename__ = "source_assets"
    __table_args__ = (
        UniqueConstraint(
            "source_system_id", "asset_key", name="uq_source_asset_system_key"
        ),
    )

    id: Mapped[str] = mapped_column(String(36), primary_key=True, default=new_id)
    project_id: Mapped[str] = mapped_column(
        String(36), ForeignKey("projects.id", ondelete="CASCADE"), index=True
    )
    source_system_id: Mapped[str] = mapped_column(
        String(36), ForeignKey("source_systems.id", ondelete="CASCADE"), index=True
    )
    asset_key: Mapped[str] = mapped_column(String(500), index=True)
    name: Mapped[str] = mapped_column(String(300))
    description: Mapped[str | None] = mapped_column(Text)
    schema_fingerprint: Mapped[str | None] = mapped_column(String(64), index=True)
    schema_fields: Mapped[list[str]] = mapped_column(JSON, default=list)
    status: Mapped[str] = mapped_column(String(32), default="ACTIVE", index=True)
    revision: Mapped[int] = mapped_column(Integer, default=1)
    created_at: Mapped[datetime] = mapped_column(DateTime(timezone=True), default=utc_now)
    updated_at: Mapped[datetime] = mapped_column(
        DateTime(timezone=True), default=utc_now, onupdate=utc_now
    )


class RawBatchRow(Base):
    __tablename__ = "raw_batches"
    __table_args__ = (
        UniqueConstraint(
            "source_asset_id", "content_sha256", name="uq_raw_batch_asset_content"
        ),
    )

    id: Mapped[str] = mapped_column(String(36), primary_key=True, default=new_id)
    project_id: Mapped[str] = mapped_column(
        String(36), ForeignKey("projects.id", ondelete="CASCADE"), index=True
    )
    source_asset_id: Mapped[str] = mapped_column(
        String(36), ForeignKey("source_assets.id", ondelete="RESTRICT"), index=True
    )
    source_document_id: Mapped[str] = mapped_column(
        String(36), ForeignKey("source_documents.id", ondelete="RESTRICT"), index=True
    )
    content_sha256: Mapped[str] = mapped_column(String(64), index=True)
    parser_version: Mapped[str] = mapped_column(String(64))
    schema_fingerprint: Mapped[str] = mapped_column(String(64), index=True)
    status: Mapped[str] = mapped_column(String(32), default="INGESTED", index=True)
    record_count: Mapped[int] = mapped_column(Integer, default=0)
    error_count: Mapped[int] = mapped_column(Integer, default=0)
    created_at: Mapped[datetime] = mapped_column(DateTime(timezone=True), default=utc_now)


class RawRecordRow(Base):
    __tablename__ = "raw_records"
    __table_args__ = (
        UniqueConstraint("raw_batch_id", "row_number", name="uq_raw_record_batch_row"),
    )

    id: Mapped[str] = mapped_column(String(36), primary_key=True, default=new_id)
    project_id: Mapped[str] = mapped_column(
        String(36), ForeignKey("projects.id", ondelete="CASCADE"), index=True
    )
    raw_batch_id: Mapped[str] = mapped_column(
        String(36), ForeignKey("raw_batches.id", ondelete="CASCADE"), index=True
    )
    row_number: Mapped[int] = mapped_column(Integer)
    source_locator: Mapped[str] = mapped_column(String(500))
    source_record_key: Mapped[str | None] = mapped_column(String(500), index=True)
    payload: Mapped[dict[str, Any]] = mapped_column(JSON)
    payload_sha256: Mapped[str] = mapped_column(String(64), index=True)
    created_at: Mapped[datetime] = mapped_column(DateTime(timezone=True), default=utc_now)


class RawRecordValueRow(Base):
    """Scalar lookup index for values inside immutable raw records.

    Raw payloads remain the source of truth.  This narrow index exists so a
    controlled cross-asset lookup (for example production.order_id -> order)
    can use SQL predicates instead of loading every JSON payload into Python.
    """

    __tablename__ = "raw_record_values"
    __table_args__ = (
        UniqueConstraint(
            "raw_record_id",
            "field_path",
            "value_text",
            name="uq_raw_record_value_path_text",
        ),
        Index(
            "ix_raw_record_value_project_text",
            "project_id",
            "value_text",
            "raw_record_id",
        ),
        Index("ix_raw_record_value_record", "raw_record_id"),
    )

    id: Mapped[str] = mapped_column(String(36), primary_key=True, default=new_id)
    project_id: Mapped[str] = mapped_column(
        String(36), ForeignKey("projects.id", ondelete="CASCADE"), index=True
    )
    raw_record_id: Mapped[str] = mapped_column(
        String(36), ForeignKey("raw_records.id", ondelete="CASCADE"), index=True
    )
    field_path: Mapped[str] = mapped_column(String(500))
    value_text: Mapped[str] = mapped_column(String(1000), index=True)
    value_kind: Mapped[str] = mapped_column(String(32), default="SCALAR")
    created_at: Mapped[datetime] = mapped_column(DateTime(timezone=True), default=utc_now)


class MaterializationRunRow(Base):
    __tablename__ = "materialization_runs"
    __table_args__ = (
        UniqueConstraint(
            "raw_batch_id",
            "mapping_fingerprint",
            name="uq_materialization_batch_mapping",
        ),
    )

    id: Mapped[str] = mapped_column(String(36), primary_key=True, default=new_id)
    project_id: Mapped[str] = mapped_column(
        String(36), ForeignKey("projects.id", ondelete="CASCADE"), index=True
    )
    raw_batch_id: Mapped[str] = mapped_column(
        String(36), ForeignKey("raw_batches.id", ondelete="CASCADE"), index=True
    )
    mapping_fingerprint: Mapped[str] = mapped_column(String(64), index=True)
    mapping_ids: Mapped[list[str]] = mapped_column(JSON, default=list)
    status: Mapped[str] = mapped_column(String(32), default="RUNNING", index=True)
    records_processed: Mapped[int] = mapped_column(Integer, default=0)
    entities_created: Mapped[int] = mapped_column(Integer, default=0)
    identities_bound: Mapped[int] = mapped_column(Integer, default=0)
    observations_created: Mapped[int] = mapped_column(Integer, default=0)
    relations_created: Mapped[int] = mapped_column(Integer, default=0)
    mappings_applied: Mapped[int] = mapped_column(Integer, default=0)
    output_entity_ids: Mapped[list[str]] = mapped_column(JSON, default=list)
    output_identity_ids: Mapped[list[str]] = mapped_column(JSON, default=list)
    output_assertion_ids: Mapped[list[str]] = mapped_column(JSON, default=list)
    output_relation_ids: Mapped[list[str]] = mapped_column(JSON, default=list)
    errors: Mapped[list[dict[str, Any]]] = mapped_column(JSON, default=list)
    created_at: Mapped[datetime] = mapped_column(DateTime(timezone=True), default=utc_now)
    finished_at: Mapped[datetime | None] = mapped_column(DateTime(timezone=True))


class SourceIdentityRow(Base):
    __tablename__ = "source_identities"
    __table_args__ = (
        UniqueConstraint(
            "project_id",
            "source_system_id",
            "source_asset",
            "source_record_key",
            "target_type_key",
            name="uq_source_identity_record",
        ),
        Index(
            "ix_source_identity_lookup",
            "project_id",
            "source_system_id",
            "source_asset",
            "source_record_key",
            "status",
        ),
    )

    id: Mapped[str] = mapped_column(String(36), primary_key=True, default=new_id)
    project_id: Mapped[str] = mapped_column(
        String(36), ForeignKey("projects.id", ondelete="CASCADE"), index=True
    )
    source_system_id: Mapped[str] = mapped_column(
        String(36), ForeignKey("source_systems.id", ondelete="CASCADE"), index=True
    )
    source_asset: Mapped[str] = mapped_column(String(500), index=True)
    source_record_key: Mapped[str] = mapped_column(String(500), index=True)
    target_type_key: Mapped[str] = mapped_column(String(64), index=True)
    entity_id: Mapped[str] = mapped_column(
        String(36), ForeignKey("entities.id", ondelete="RESTRICT"), index=True
    )
    status: Mapped[str] = mapped_column(String(32), default="UNRESOLVED", index=True)
    revision: Mapped[int] = mapped_column(Integer, default=1)
    created_at: Mapped[datetime] = mapped_column(DateTime(timezone=True), default=utc_now)
    updated_at: Mapped[datetime] = mapped_column(
        DateTime(timezone=True), default=utc_now, onupdate=utc_now
    )


class ObservationAssertionRow(Base):
    __tablename__ = "observation_assertions"
    __table_args__ = (
        UniqueConstraint(
            "materialization_run_id",
            "raw_record_id",
            "entity_id",
            "field_key",
            name="uq_observation_assertion_materialized_field",
        ),
        Index(
            "ix_observation_assertion_source_lookup",
            "project_id",
            "status",
            "source_asset",
            "source_record_key",
            "field_key",
        ),
        Index(
            "ix_observation_assertion_source_key_lookup",
            "project_id",
            "status",
            "source_record_key",
        ),
        Index(
            "ix_observation_assertion_asset_field_lookup",
            "project_id",
            "status",
            "source_asset",
            "field_key",
            "observed_at",
        ),
        Index(
            "ix_observation_assertion_entity_time",
            "project_id",
            "entity_id",
            "status",
            "observed_at",
        ),
    )

    id: Mapped[str] = mapped_column(String(36), primary_key=True, default=new_id)
    project_id: Mapped[str] = mapped_column(
        String(36), ForeignKey("projects.id", ondelete="CASCADE"), index=True
    )
    entity_id: Mapped[str] = mapped_column(
        String(36), ForeignKey("entities.id", ondelete="CASCADE"), index=True
    )
    source_identity_id: Mapped[str] = mapped_column(
        String(36), ForeignKey("source_identities.id", ondelete="CASCADE"), index=True
    )
    source_document_id: Mapped[str] = mapped_column(
        String(36), ForeignKey("source_documents.id", ondelete="CASCADE"), index=True
    )
    fragment_id: Mapped[str | None] = mapped_column(
        String(36), ForeignKey("evidence_fragments.id", ondelete="SET NULL"), index=True
    )
    raw_record_id: Mapped[str | None] = mapped_column(
        String(36), ForeignKey("raw_records.id", ondelete="SET NULL"), index=True
    )
    semantic_mapping_id: Mapped[str | None] = mapped_column(
        String(36), ForeignKey("semantic_mappings.id", ondelete="SET NULL"), index=True
    )
    materialization_run_id: Mapped[str | None] = mapped_column(
        String(36), ForeignKey("materialization_runs.id", ondelete="SET NULL"), index=True
    )
    source_asset: Mapped[str] = mapped_column(String(500), index=True)
    source_record_key: Mapped[str] = mapped_column(String(500), index=True)
    field_key: Mapped[str] = mapped_column(String(200), index=True)
    value: Mapped[Any] = mapped_column(JSON)
    authority_priority: Mapped[int] = mapped_column(Integer, default=100)
    status: Mapped[str] = mapped_column(String(32), default="ACTIVE", index=True)
    version: Mapped[int] = mapped_column(Integer, default=1)
    supersedes_id: Mapped[str | None] = mapped_column(
        String(36), ForeignKey("observation_assertions.id", ondelete="SET NULL"), index=True
    )
    observed_at: Mapped[datetime] = mapped_column(DateTime(timezone=True), index=True)
    created_at: Mapped[datetime] = mapped_column(DateTime(timezone=True), default=utc_now)
    updated_at: Mapped[datetime] = mapped_column(
        DateTime(timezone=True), default=utc_now, onupdate=utc_now
    )


class ObservationConflictRow(Base):
    __tablename__ = "observation_conflicts"
    __table_args__ = (
        UniqueConstraint(
            "project_id", "entity_id", "field_key", name="uq_observation_conflict_field"
        ),
    )

    id: Mapped[str] = mapped_column(String(36), primary_key=True, default=new_id)
    project_id: Mapped[str] = mapped_column(
        String(36), ForeignKey("projects.id", ondelete="CASCADE"), index=True
    )
    entity_id: Mapped[str] = mapped_column(
        String(36), ForeignKey("entities.id", ondelete="CASCADE"), index=True
    )
    field_key: Mapped[str] = mapped_column(String(200), index=True)
    candidate_assertion_ids: Mapped[list[str]] = mapped_column(JSON, default=list)
    status: Mapped[str] = mapped_column(String(32), default="OPEN", index=True)
    resolution_kind: Mapped[str | None] = mapped_column(String(32))
    chosen_assertion_id: Mapped[str | None] = mapped_column(
        String(36), ForeignKey("observation_assertions.id", ondelete="SET NULL")
    )
    override_value: Mapped[Any | None] = mapped_column(JSON)
    rationale: Mapped[str | None] = mapped_column(Text)
    resolved_by: Mapped[str | None] = mapped_column(String(100))
    revision: Mapped[int] = mapped_column(Integer, default=1)
    created_at: Mapped[datetime] = mapped_column(DateTime(timezone=True), default=utc_now)
    updated_at: Mapped[datetime] = mapped_column(
        DateTime(timezone=True), default=utc_now, onupdate=utc_now
    )


class SemanticMappingRow(Base):
    __tablename__ = "semantic_mappings"

    id: Mapped[str] = mapped_column(String(36), primary_key=True, default=new_id)
    project_id: Mapped[str] = mapped_column(
        String(36), ForeignKey("projects.id", ondelete="CASCADE"), index=True
    )
    source_system_id: Mapped[str] = mapped_column(
        String(36), ForeignKey("source_systems.id", ondelete="CASCADE"), index=True
    )
    source_asset: Mapped[str] = mapped_column(String(500))
    source_field: Mapped[str] = mapped_column(String(500))
    target_type_key: Mapped[str] = mapped_column(String(64))
    target_property_key: Mapped[str] = mapped_column(String(64))
    transform_expression: Mapped[str | None] = mapped_column(Text)
    authority_priority: Mapped[int] = mapped_column(Integer, default=100)
    status: Mapped[str] = mapped_column(String(32), default="DRAFT")
    revision: Mapped[int] = mapped_column(Integer, default=1)
    validation_report: Mapped[dict[str, Any]] = mapped_column(JSON, default=dict)
    created_at: Mapped[datetime] = mapped_column(DateTime(timezone=True), default=utc_now)
    updated_at: Mapped[datetime] = mapped_column(
        DateTime(timezone=True), default=utc_now, onupdate=utc_now
    )


class SemanticRelationMappingRow(Base):
    __tablename__ = "semantic_relation_mappings"

    id: Mapped[str] = mapped_column(String(36), primary_key=True, default=new_id)
    project_id: Mapped[str] = mapped_column(
        String(36), ForeignKey("projects.id", ondelete="CASCADE"), index=True
    )
    source_system_id: Mapped[str] = mapped_column(
        String(36), ForeignKey("source_systems.id", ondelete="CASCADE"), index=True
    )
    source_asset: Mapped[str] = mapped_column(String(500), index=True)
    source_type_key: Mapped[str] = mapped_column(String(64), index=True)
    source_field: Mapped[str] = mapped_column(String(500))
    relation_type_key: Mapped[str] = mapped_column(String(64), index=True)
    source_role_key: Mapped[str] = mapped_column(String(64))
    target_type_key: Mapped[str] = mapped_column(String(64), index=True)
    target_role_key: Mapped[str] = mapped_column(String(64))
    target_asset: Mapped[str | None] = mapped_column(String(500), index=True)
    transform_expression: Mapped[str | None] = mapped_column(Text)
    status: Mapped[str] = mapped_column(String(32), default="DRAFT", index=True)
    revision: Mapped[int] = mapped_column(Integer, default=1)
    validation_report: Mapped[dict[str, Any]] = mapped_column(JSON, default=dict)
    created_at: Mapped[datetime] = mapped_column(DateTime(timezone=True), default=utc_now)
    updated_at: Mapped[datetime] = mapped_column(
        DateTime(timezone=True), default=utc_now, onupdate=utc_now
    )


class SemanticDatasetRow(Base):
    __tablename__ = "semantic_datasets"
    __table_args__ = (
        UniqueConstraint("project_id", "key", name="uq_semantic_dataset_key"),
    )

    id: Mapped[str] = mapped_column(String(36), primary_key=True, default=new_id)
    project_id: Mapped[str] = mapped_column(
        String(36), ForeignKey("projects.id", ondelete="CASCADE"), index=True
    )
    key: Mapped[str] = mapped_column(String(100))
    name: Mapped[str] = mapped_column(String(300))
    description: Mapped[str | None] = mapped_column(Text)
    root_type_key: Mapped[str] = mapped_column(String(64), index=True)
    columns: Mapped[list[dict[str, Any]]] = mapped_column(JSON, default=list)
    status: Mapped[str] = mapped_column(String(32), default="ACTIVE", index=True)
    revision: Mapped[int] = mapped_column(Integer, default=1)
    created_at: Mapped[datetime] = mapped_column(DateTime(timezone=True), default=utc_now)
    updated_at: Mapped[datetime] = mapped_column(
        DateTime(timezone=True), default=utc_now, onupdate=utc_now
    )


class SemanticQueryRunRow(Base):
    __tablename__ = "semantic_query_runs"

    id: Mapped[str] = mapped_column(String(36), primary_key=True, default=new_id)
    project_id: Mapped[str] = mapped_column(
        String(36), ForeignKey("projects.id", ondelete="CASCADE"), index=True
    )
    dataset_id: Mapped[str] = mapped_column(
        String(36), ForeignKey("semantic_datasets.id", ondelete="CASCADE"), index=True
    )
    query_snapshot_id: Mapped[str] = mapped_column(
        String(36), ForeignKey("query_snapshots.id", ondelete="RESTRICT"), index=True
    )
    status: Mapped[str] = mapped_column(String(32), default="COMPLETED", index=True)
    row_count: Mapped[int] = mapped_column(Integer, default=0)
    plan: Mapped[dict[str, Any]] = mapped_column(JSON, default=dict)
    warnings: Mapped[list[dict[str, Any]]] = mapped_column(JSON, default=list)
    created_at: Mapped[datetime] = mapped_column(DateTime(timezone=True), default=utc_now)


class ExportJobRow(Base):
    __tablename__ = "export_jobs"

    id: Mapped[str] = mapped_column(String(36), primary_key=True, default=new_id)
    project_id: Mapped[str] = mapped_column(
        String(36), ForeignKey("projects.id", ondelete="CASCADE"), index=True
    )
    status: Mapped[str] = mapped_column(String(32), default="QUEUED")
    format: Mapped[str] = mapped_column(String(32))
    request_payload: Mapped[dict[str, Any]] = mapped_column(JSON, default=dict)
    download_url: Mapped[str | None] = mapped_column(String(1000))
    error: Mapped[str | None] = mapped_column(Text)
    created_at: Mapped[datetime] = mapped_column(DateTime(timezone=True), default=utc_now)
    updated_at: Mapped[datetime] = mapped_column(
        DateTime(timezone=True), default=utc_now, onupdate=utc_now
    )


class RestorePreviewRow(Base):
    __tablename__ = "restore_previews"

    id: Mapped[str] = mapped_column(String(36), primary_key=True, default=new_id)
    file_name: Mapped[str] = mapped_column(String(500))
    package_sha256: Mapped[str] = mapped_column(String(64), index=True)
    package_path: Mapped[str] = mapped_column(String(1000))
    status: Mapped[str] = mapped_column(String(32), default="READY", index=True)
    summary: Mapped[dict[str, Any]] = mapped_column(JSON, default=dict)
    expires_at: Mapped[datetime] = mapped_column(DateTime(timezone=True), index=True)
    consumed_at: Mapped[datetime | None] = mapped_column(DateTime(timezone=True))
    created_at: Mapped[datetime] = mapped_column(DateTime(timezone=True), default=utc_now)


class ModelProfileRow(Base):
    __tablename__ = "model_profiles"

    id: Mapped[str] = mapped_column(String(36), primary_key=True, default=new_id)
    name: Mapped[str] = mapped_column(String(200), unique=True)
    provider: Mapped[str] = mapped_column(String(32))
    base_url: Mapped[str] = mapped_column(String(1000))
    model: Mapped[str] = mapped_column(String(200))
    encrypted_api_key: Mapped[str | None] = mapped_column(Text)
    temperature: Mapped[float] = mapped_column(default=0.2)
    timeout_seconds: Mapped[int] = mapped_column(Integer, default=90)
    enabled: Mapped[bool] = mapped_column(Boolean, default=True)
    is_default: Mapped[bool] = mapped_column(Boolean, default=False, index=True)
    created_at: Mapped[datetime] = mapped_column(DateTime(timezone=True), default=utc_now)
    updated_at: Mapped[datetime] = mapped_column(
        DateTime(timezone=True), default=utc_now, onupdate=utc_now
    )
