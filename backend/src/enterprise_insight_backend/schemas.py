from __future__ import annotations

from datetime import datetime
from enum import StrEnum
from typing import Any
from uuid import UUID

from pydantic import BaseModel, ConfigDict, Field, model_validator


class StrictModel(BaseModel):
    model_config = ConfigDict(extra="forbid", from_attributes=True)


class Page[T](StrictModel):
    items: list[T]
    total: int = Field(ge=0)


class HealthView(StrictModel):
    status: str
    version: str
    build_id: str
    schema_revision: str
    database: str
    agent_worker: str
    frontend: str


class CapabilityView(StrictModel):
    key: str
    status: str
    description: str


class CapabilityManifest(StrictModel):
    api_version: str
    capabilities: list[CapabilityView]


class LifecycleStatus(StrEnum):
    DRAFT = "DRAFT"
    REVIEW = "REVIEW"
    ACCEPTED = "ACCEPTED"
    PUBLISHED = "PUBLISHED"
    RETIRED = "RETIRED"
    REJECTED = "REJECTED"


class ProjectStatus(StrEnum):
    DRAFT = "DRAFT"
    ACTIVE = "ACTIVE"
    ARCHIVED = "ARCHIVED"


class Viewpoint(StrEnum):
    DESIGNED = "DESIGNED"
    REPORTED = "REPORTED"
    OBSERVED = "OBSERVED"
    SYSTEM_BOUND = "SYSTEM_BOUND"


class DesignMembership(StrEnum):
    MODELED = "MODELED"
    UNMODELED = "UNMODELED"


class CompanyCreate(StrictModel):
    name: str = Field(min_length=1, max_length=200)
    industry: str | None = Field(default=None, max_length=200)
    description: str | None = Field(default=None, max_length=4000)


class CompanyUpdate(StrictModel):
    name: str | None = Field(default=None, min_length=1, max_length=200)
    industry: str | None = Field(default=None, max_length=200)
    description: str | None = Field(default=None, max_length=4000)


class CompanyView(StrictModel):
    id: UUID
    name: str
    industry: str | None
    description: str | None
    created_at: datetime
    updated_at: datetime


class ProjectCreate(StrictModel):
    name: str = Field(min_length=1, max_length=200)
    description: str | None = Field(default=None, max_length=4000)


class ProjectUpdate(StrictModel):
    name: str | None = Field(default=None, min_length=1, max_length=200)
    description: str | None = Field(default=None, max_length=4000)
    status: ProjectStatus | None = None


class ProjectView(StrictModel):
    id: UUID
    company_id: UUID
    name: str
    description: str | None
    status: ProjectStatus
    revision: int
    created_at: datetime
    updated_at: datetime


class RevisionRequest(StrictModel):
    expected_revision: int = Field(ge=1)


class TypeKind(StrEnum):
    OBJECT = "OBJECT"
    RELATION = "RELATION"
    EVENT = "EVENT"
    ACTION = "ACTION"
    INTERFACE = "INTERFACE"
    METRIC = "METRIC"


class PropertyValueType(StrEnum):
    STRING = "STRING"
    INTEGER = "INTEGER"
    NUMBER = "NUMBER"
    BOOLEAN = "BOOLEAN"
    DATE = "DATE"
    DATETIME = "DATETIME"
    UUID = "UUID"
    JSON = "JSON"
    STRING_LIST = "STRING_LIST"


class PropertyDefinition(StrictModel):
    key: str = Field(pattern=r"^[a-z][a-z0-9_]{0,63}$")
    name: str = Field(min_length=1, max_length=200)
    value_type: PropertyValueType
    required: bool = False
    multiple: bool = False
    description: str | None = Field(default=None, max_length=2000)


class RelationRoleDefinition(StrictModel):
    key: str = Field(pattern=r"^[a-z][a-z0-9_]{0,63}$")
    name: str = Field(min_length=1, max_length=200)
    allowed_type_keys: list[str] = Field(default_factory=list)
    minimum: int = Field(default=0, ge=0)
    maximum: int | None = Field(default=None, ge=1)

    @model_validator(mode="after")
    def validate_range(self) -> RelationRoleDefinition:
        if self.maximum is not None and self.maximum < self.minimum:
            raise ValueError("maximum must be greater than or equal to minimum")
        return self


class ActionParameterDefinition(StrictModel):
    key: str = Field(pattern=r"^[a-z][a-z0-9_]{0,63}$")
    name: str = Field(min_length=1, max_length=200)
    value_type: PropertyValueType
    required: bool = True
    description: str | None = Field(default=None, max_length=2000)


class OntologyTypeCreate(StrictModel):
    key: str = Field(pattern=r"^[a-z][a-z0-9_]{0,63}$")
    name: str = Field(min_length=1, max_length=200)
    kind: TypeKind
    description: str | None = Field(default=None, max_length=4000)
    parent_type_key: str | None = Field(default=None, max_length=64)
    interface_keys: list[str] = Field(default_factory=list)
    properties: list[PropertyDefinition] = Field(default_factory=list)
    relation_roles: list[RelationRoleDefinition] = Field(default_factory=list)
    action_parameters: list[ActionParameterDefinition] = Field(default_factory=list)
    metadata: dict[str, Any] = Field(default_factory=dict)

    @model_validator(mode="after")
    def validate_kind_payload(self) -> OntologyTypeCreate:
        if self.kind == TypeKind.RELATION and len(self.relation_roles) < 2:
            raise ValueError("relation types require at least two participant roles")
        if self.kind != TypeKind.RELATION and self.relation_roles:
            raise ValueError("relation_roles are only valid for RELATION types")
        if self.kind != TypeKind.ACTION and self.action_parameters:
            raise ValueError("action_parameters are only valid for ACTION types")
        return self


class OntologyTypeUpdate(StrictModel):
    name: str | None = Field(default=None, min_length=1, max_length=200)
    description: str | None = Field(default=None, max_length=4000)
    interface_keys: list[str] | None = None
    properties: list[PropertyDefinition] | None = None
    relation_roles: list[RelationRoleDefinition] | None = None
    action_parameters: list[ActionParameterDefinition] | None = None
    metadata: dict[str, Any] | None = None
    expected_revision: int = Field(ge=1)


class OntologyTypeView(OntologyTypeCreate):
    id: UUID
    project_id: UUID
    status: LifecycleStatus
    revision: int
    created_at: datetime
    updated_at: datetime


class OntologyReleaseCreate(StrictModel):
    label: str = Field(min_length=1, max_length=200)
    notes: str | None = Field(default=None, max_length=4000)


class OntologyReleaseView(StrictModel):
    id: UUID
    project_id: UUID
    version: int
    label: str
    notes: str | None
    type_count: int
    created_at: datetime


class EvidenceReference(StrictModel):
    source_document_id: UUID | None = None
    fragment_id: UUID | None = None
    claim_id: UUID | None = None
    note: str | None = Field(default=None, max_length=1000)


class EntityCreate(StrictModel):
    type_key: str = Field(min_length=1, max_length=64)
    stable_key: str | None = Field(default=None, max_length=200)
    name: str = Field(min_length=1, max_length=300)
    properties: dict[str, Any] = Field(default_factory=dict)
    viewpoint: Viewpoint = Viewpoint.DESIGNED
    evidence: list[EvidenceReference] = Field(default_factory=list)


class EntityUpdate(StrictModel):
    name: str | None = Field(default=None, min_length=1, max_length=300)
    properties: dict[str, Any] | None = None
    viewpoint: Viewpoint | None = None
    evidence: list[EvidenceReference] | None = None
    expected_revision: int = Field(ge=1)


class EntityView(EntityCreate):
    id: UUID
    project_id: UUID
    design_membership: DesignMembership = DesignMembership.MODELED
    observed_name: str | None = None
    observed_properties: dict[str, Any] = Field(default_factory=dict)
    comparison: dict[str, Any] = Field(default_factory=dict)
    status: LifecycleStatus
    revision: int
    created_at: datetime
    updated_at: datetime


class RelationParticipantInput(StrictModel):
    role_key: str = Field(min_length=1, max_length=64)
    entity_id: UUID
    ordinal: int = Field(default=0, ge=0)


class RelationParticipantView(RelationParticipantInput):
    entity_name: str
    entity_type_key: str


class RelationCreate(StrictModel):
    type_key: str = Field(min_length=1, max_length=64)
    name: str | None = Field(default=None, max_length=300)
    participants: list[RelationParticipantInput] = Field(min_length=2)
    properties: dict[str, Any] = Field(default_factory=dict)
    viewpoint: Viewpoint = Viewpoint.DESIGNED
    evidence: list[EvidenceReference] = Field(default_factory=list)


class RelationUpdate(StrictModel):
    name: str | None = Field(default=None, max_length=300)
    participants: list[RelationParticipantInput] | None = Field(default=None, min_length=2)
    properties: dict[str, Any] | None = None
    viewpoint: Viewpoint | None = None
    evidence: list[EvidenceReference] | None = None
    expected_revision: int = Field(ge=1)


class RelationView(StrictModel):
    id: UUID
    project_id: UUID
    type_key: str
    name: str | None
    participants: list[RelationParticipantView]
    properties: dict[str, Any]
    viewpoint: Viewpoint
    evidence: list[EvidenceReference]
    status: LifecycleStatus
    revision: int
    created_at: datetime
    updated_at: datetime


class EventParticipantInput(StrictModel):
    role_key: str = Field(min_length=1, max_length=64)
    entity_id: UUID


class EventCreate(StrictModel):
    type_key: str = Field(min_length=1, max_length=64)
    name: str = Field(min_length=1, max_length=300)
    occurred_at: datetime
    participants: list[EventParticipantInput] = Field(min_length=1)
    properties: dict[str, Any] = Field(default_factory=dict)
    evidence: list[EvidenceReference] = Field(default_factory=list)


class EventView(EventCreate):
    id: UUID
    project_id: UUID
    status: LifecycleStatus
    revision: int
    created_at: datetime


class GraphQuery(StrictModel):
    type_keys: list[str] = Field(default_factory=list)
    relation_type_keys: list[str] = Field(default_factory=list)
    viewpoints: list[Viewpoint] = Field(default_factory=list)
    search: str | None = Field(default=None, max_length=200)
    root_entity_id: UUID | None = None
    depth: int = Field(default=2, ge=0, le=8)
    include_retired: bool = False
    include_unmodeled: bool = False
    include_observations: bool = True
    query_snapshot_id: UUID | None = None
    release_id: UUID | None = None
    scenario_id: UUID | None = None


class GraphView(StrictModel):
    project_id: UUID
    revision: int
    query_snapshot_id: UUID | None = None
    release_id: UUID | None
    scenario_id: UUID | None
    entities: list[EntityView]
    relations: list[RelationView]


class ChangeOperationKind(StrEnum):
    CREATE_ENTITY = "CREATE_ENTITY"
    UPDATE_ENTITY = "UPDATE_ENTITY"
    RETIRE_ENTITY = "RETIRE_ENTITY"
    CREATE_RELATION = "CREATE_RELATION"
    UPDATE_RELATION = "UPDATE_RELATION"
    RETIRE_RELATION = "RETIRE_RELATION"
    CREATE_EVENT = "CREATE_EVENT"


class ChangeOperation(StrictModel):
    operation_id: UUID
    kind: ChangeOperationKind
    target_id: UUID | None = None
    payload: dict[str, Any] = Field(default_factory=dict)
    evidence: list[EvidenceReference] = Field(default_factory=list)


class ChangeSetStatus(StrEnum):
    DRAFT = "DRAFT"
    VALID = "VALID"
    INVALID = "INVALID"
    APPROVED = "APPROVED"
    APPLIED = "APPLIED"
    REJECTED = "REJECTED"


class ChangeSetCreate(StrictModel):
    title: str = Field(min_length=1, max_length=300)
    description: str | None = Field(default=None, max_length=4000)
    base_revision: int = Field(ge=0)
    operations: list[ChangeOperation] = Field(default_factory=list)
    created_by: str = Field(default="developer", min_length=1, max_length=100)


class ValidationIssue(StrictModel):
    severity: str
    code: str
    message: str
    operation_id: UUID | None = None
    path: str | None = None


class ChangeSetValidation(StrictModel):
    valid: bool
    issues: list[ValidationIssue]
    checked_at: datetime


class ChangeSetView(ChangeSetCreate):
    id: UUID
    project_id: UUID
    status: ChangeSetStatus
    validation: ChangeSetValidation | None
    created_at: datetime
    updated_at: datetime


class ChangePreview(StrictModel):
    change_set_id: UUID
    base_revision: int
    current_revision: int
    creates: int
    updates: int
    retires: int
    operations: list[ChangeOperation]
    validation: ChangeSetValidation


class AgentKind(StrEnum):
    PROJECTION = "PROJECTION"
    MANAGEMENT = "MANAGEMENT"
    SYSTEM_ONTOLOGY = "SYSTEM_ONTOLOGY"


class AgentThreadCreate(StrictModel):
    agent_kind: AgentKind
    title: str | None = Field(default=None, max_length=200)


class AgentThreadUpdate(StrictModel):
    title: str | None = Field(default=None, min_length=1, max_length=200)
    archived: bool | None = None
    trashed: bool | None = None


class AgentThreadView(StrictModel):
    id: UUID
    project_id: UUID
    agent_kind: AgentKind
    title: str
    archived: bool
    trashed: bool
    created_at: datetime
    updated_at: datetime


class AgentMessageRole(StrEnum):
    USER = "USER"
    ASSISTANT = "ASSISTANT"
    SYSTEM = "SYSTEM"
    TOOL = "TOOL"


class AgentMessageCreate(StrictModel):
    content: str = Field(min_length=1, max_length=100_000)
    attachment_ids: list[UUID] = Field(default_factory=list)
    include_unconfirmed_material: bool = True
    reference_case_ids: list[UUID] = Field(default_factory=list)
    model_profile_id: UUID | None = None
    allow_external_model: bool = False
    share_project_context_with_model: bool = False


class AgentMessageView(StrictModel):
    id: UUID
    thread_id: UUID
    role: AgentMessageRole
    content: str
    citations: list[EvidenceReference] = Field(default_factory=list)
    created_at: datetime


class AgentRunStatus(StrEnum):
    QUEUED = "QUEUED"
    RETRIEVING = "RETRIEVING"
    PLANNING = "PLANNING"
    RUNNING_TOOLS = "RUNNING_TOOLS"
    PRODUCING_PROPOSAL = "PRODUCING_PROPOSAL"
    VALIDATING = "VALIDATING"
    WAITING_REVIEW = "WAITING_REVIEW"
    BUDGET_EXHAUSTED = "BUDGET_EXHAUSTED"
    COMPLETED = "COMPLETED"
    FAILED = "FAILED"
    CANCELLED = "CANCELLED"


class AgentRunView(StrictModel):
    id: UUID
    thread_id: UUID
    project_id: UUID
    agent_kind: AgentKind
    status: AgentRunStatus
    context_manifest: dict[str, Any]
    result_message_id: UUID | None
    change_set_id: UUID | None
    action_invocation_ids: list[UUID] = Field(default_factory=list)
    error: dict[str, Any] | None
    attempt_count: int = Field(default=0, ge=0)
    lease_expires_at: datetime | None = None
    created_at: datetime
    updated_at: datetime


class AgentStepView(StrictModel):
    id: UUID
    project_id: UUID
    run_id: UUID
    position: int = Field(ge=1)
    kind: str
    tool_key: str | None
    status: str
    input_payload: dict[str, Any]
    output_payload: dict[str, Any]
    error: dict[str, Any] | None
    started_at: datetime
    finished_at: datetime | None
    created_at: datetime


class AgentMessageAccepted(StrictModel):
    message: AgentMessageView
    run: AgentRunView


class AgentActionProposal(StrictModel):
    action_key: str = Field(min_length=1, max_length=100)
    input: dict[str, Any] = Field(default_factory=dict)
    target_entity_ids: list[UUID] = Field(default_factory=list)
    reason: str | None = Field(default=None, max_length=4000)


class AgentStructuredOutput(StrictModel):
    content: str = Field(min_length=1, max_length=100_000)
    citations: list[EvidenceReference] = Field(default_factory=list)
    action_proposals: list[AgentActionProposal] = Field(default_factory=list)


class ActionRiskLevel(StrEnum):
    LOW = "LOW"
    MEDIUM = "MEDIUM"
    HIGH = "HIGH"
    CRITICAL = "CRITICAL"


class ActionExecutionMode(StrEnum):
    INTERNAL = "INTERNAL"
    CONNECTOR = "CONNECTOR"


class ActionDefinitionStatus(StrEnum):
    DRAFT = "DRAFT"
    VALIDATED = "VALIDATED"
    PUBLISHED = "PUBLISHED"
    RETIRED = "RETIRED"


class ActionDefinitionCreate(StrictModel):
    key: str = Field(pattern=r"^[a-z][a-z0-9_.-]{0,99}$")
    name: str = Field(min_length=1, max_length=200)
    description: str | None = Field(default=None, max_length=4000)
    target_type_key: str | None = Field(default=None, max_length=64)
    parameters: list[ActionParameterDefinition] = Field(default_factory=list)
    preconditions: list[dict[str, Any]] = Field(default_factory=list)
    effects: list[dict[str, Any]] = Field(default_factory=list)
    execution_mode: ActionExecutionMode = ActionExecutionMode.INTERNAL
    risk_level: ActionRiskLevel = ActionRiskLevel.MEDIUM
    require_approval: bool = True
    enabled: bool = True
    created_by: str = Field(default="developer", min_length=1, max_length=100)


class ActionDefinitionUpdate(StrictModel):
    name: str | None = Field(default=None, min_length=1, max_length=200)
    description: str | None = Field(default=None, max_length=4000)
    target_type_key: str | None = Field(default=None, max_length=64)
    parameters: list[ActionParameterDefinition] | None = None
    preconditions: list[dict[str, Any]] | None = None
    effects: list[dict[str, Any]] | None = None
    execution_mode: ActionExecutionMode | None = None
    risk_level: ActionRiskLevel | None = None
    require_approval: bool | None = None
    enabled: bool | None = None
    expected_revision: int = Field(ge=1)


class ActionDefinitionView(ActionDefinitionCreate):
    id: UUID
    project_id: UUID
    status: ActionDefinitionStatus
    revision: int
    created_at: datetime
    updated_at: datetime


class ActionInvocationStatus(StrEnum):
    DRAFT = "DRAFT"
    DRY_RUN_COMPLETED = "DRY_RUN_COMPLETED"
    WAITING_APPROVAL = "WAITING_APPROVAL"
    APPROVED = "APPROVED"
    RUNNING = "RUNNING"
    SUCCEEDED = "SUCCEEDED"
    FAILED = "FAILED"
    CANCELLED = "CANCELLED"
    OBSERVING = "OBSERVING"
    EFFECTIVE = "EFFECTIVE"
    INEFFECTIVE = "INEFFECTIVE"
    ROLLED_BACK = "ROLLED_BACK"


class ActionInvocationCreate(StrictModel):
    action_definition_id: UUID
    input: dict[str, Any] = Field(default_factory=dict)
    target_entity_ids: list[UUID] = Field(default_factory=list)
    requested_by: str = Field(default="management", min_length=1, max_length=100)
    source_agent_run_id: UUID | None = None
    idempotency_key: str | None = Field(default=None, min_length=1, max_length=200)


class ActionInvocationView(StrictModel):
    id: UUID
    project_id: UUID
    action_definition_id: UUID
    action_key: str
    action_name: str
    source_agent_run_id: UUID | None
    idempotency_key: str
    requested_by: str
    target_entity_ids: list[UUID]
    input: dict[str, Any]
    status: ActionInvocationStatus
    risk_level: ActionRiskLevel
    require_approval: bool
    preflight: dict[str, Any] | None
    result: dict[str, Any] | None
    error: dict[str, Any] | None
    approved_by: str | None
    approved_at: datetime | None
    started_at: datetime | None
    finished_at: datetime | None
    created_at: datetime
    updated_at: datetime


class ActionApprovalRequest(StrictModel):
    approved_by: str = Field(default="management", min_length=1, max_length=100)


class ActionRetryRequest(StrictModel):
    requested_by: str = Field(default="management", min_length=1, max_length=100)
    idempotency_key: str | None = Field(default=None, min_length=1, max_length=200)


class ActionObservationOutcome(StrEnum):
    UNKNOWN = "UNKNOWN"
    EFFECTIVE = "EFFECTIVE"
    INEFFECTIVE = "INEFFECTIVE"


class ActionObservationKind(StrEnum):
    QUALITATIVE = "QUALITATIVE"
    METRIC = "METRIC"


class ActionObservationCreate(StrictModel):
    observation_kind: ActionObservationKind = ActionObservationKind.QUALITATIVE
    metric_key: str = Field(min_length=1, max_length=200)
    period_key: str | None = Field(default=None, max_length=100)
    dimensions: dict[str, Any] = Field(default_factory=dict)
    observed_value: Any
    outcome: ActionObservationOutcome = ActionObservationOutcome.UNKNOWN
    note: str | None = Field(default=None, max_length=4000)
    observed_at: datetime | None = None


class ActionObservationView(ActionObservationCreate):
    id: UUID
    project_id: UUID
    invocation_id: UUID
    metric_definition_id: UUID | None
    metric_observation_id: UUID | None
    observed_at: datetime
    created_at: datetime


class ActionLogView(StrictModel):
    id: UUID
    project_id: UUID
    invocation_id: UUID
    event_type: str
    from_status: ActionInvocationStatus | None
    to_status: ActionInvocationStatus | None
    actor: str
    details: dict[str, Any]
    created_at: datetime


class ExplorationModuleView(StrictModel):
    id: str
    version: str
    name: str
    description: str
    applicable_goals: list[str]
    required_interfaces: list[str]
    enabled: bool
    output_schema: dict[str, Any]


class ManagementGoalCreate(StrictModel):
    goal: str = Field(min_length=1, max_length=10_000)
    constraints: list[str] = Field(default_factory=list)
    success_criteria: list[str] = Field(default_factory=list)
    exploration_module_ids: list[str] = Field(default_factory=list)
    include_unconfirmed_material: bool = True


class HypothesisStatus(StrEnum):
    EXPLORING = "EXPLORING"
    NEEDS_EVIDENCE = "NEEDS_EVIDENCE"
    CONFIRMED = "CONFIRMED"
    PARTIALLY_CONFIRMED = "PARTIALLY_CONFIRMED"
    REJECTED = "REJECTED"
    DEFERRED = "DEFERRED"


class HypothesisCreate(StrictModel):
    type_key: str = Field(min_length=1, max_length=100)
    schema_version: int = Field(default=1, ge=1)
    title: str = Field(min_length=1, max_length=300)
    summary: str = Field(min_length=1, max_length=20_000)
    participant_entity_ids: list[UUID] = Field(default_factory=list)
    supporting_facts: list[str] = Field(default_factory=list)
    counter_evidence: list[str] = Field(default_factory=list)
    alternative_explanations: list[str] = Field(default_factory=list)
    uncertainties: list[str] = Field(default_factory=list)
    validation_questions: list[str] = Field(default_factory=list)
    extension_payload: dict[str, Any] = Field(default_factory=dict)


class HypothesisView(HypothesisCreate):
    id: UUID
    project_id: UUID
    status: HypothesisStatus
    source: str
    module_id: str | None
    module_version: str | None
    created_at: datetime
    updated_at: datetime


class HypothesisFeedbackCreate(StrictModel):
    status: HypothesisStatus
    comment: str | None = Field(default=None, max_length=10_000)
    provided_by: str = Field(default="management", min_length=1, max_length=100)


class HypothesisFeedbackView(HypothesisFeedbackCreate):
    id: UUID
    hypothesis_id: UUID
    created_at: datetime


class CausalHypothesisCreate(StrictModel):
    title: str = Field(min_length=1, max_length=300)
    cause_entity_ids: list[UUID] = Field(min_length=1)
    effect_entity_ids: list[UUID] = Field(min_length=1)
    mechanism: str = Field(min_length=1, max_length=20_000)
    predictions: list[str] = Field(default_factory=list, min_length=1, max_length=100)
    intervention_test: str | None = Field(default=None, max_length=10_000)
    supporting_facts: list[str] = Field(default_factory=list)
    counter_evidence: list[str] = Field(default_factory=list)
    alternative_explanations: list[str] = Field(default_factory=list)
    uncertainties: list[str] = Field(default_factory=list)
    validation_questions: list[str] = Field(default_factory=list)

    @model_validator(mode="after")
    def cause_and_effect_must_differ(self) -> CausalHypothesisCreate:
        if set(self.cause_entity_ids) & set(self.effect_entity_ids):
            raise ValueError("cause and effect entities must differ")
        return self


class CausalHypothesisView(CausalHypothesisCreate):
    id: UUID
    project_id: UUID
    status: HypothesisStatus
    source: str
    created_at: datetime
    updated_at: datetime


class ScenarioStatus(StrEnum):
    DRAFT = "DRAFT"
    UNDER_REVIEW = "UNDER_REVIEW"
    APPROVED = "APPROVED"
    ACTIVE = "ACTIVE"
    COMPLETED = "COMPLETED"
    REJECTED = "REJECTED"


class ScenarioCreate(StrictModel):
    name: str = Field(min_length=1, max_length=300)
    description: str | None = Field(default=None, max_length=20_000)
    goal: str = Field(min_length=1, max_length=10_000)
    assumptions: list[str] = Field(default_factory=list)
    overlay_operations: list[ChangeOperation] = Field(default_factory=list)
    expected_benefits: list[str] = Field(default_factory=list)
    risks: list[str] = Field(default_factory=list)
    validation_metrics: list[str] = Field(default_factory=list)


class ScenarioUpdate(StrictModel):
    name: str | None = Field(default=None, min_length=1, max_length=300)
    description: str | None = Field(default=None, max_length=20_000)
    goal: str | None = Field(default=None, min_length=1, max_length=10_000)
    assumptions: list[str] | None = None
    overlay_operations: list[ChangeOperation] | None = None
    expected_benefits: list[str] | None = None
    risks: list[str] | None = None
    validation_metrics: list[str] | None = None
    status: ScenarioStatus | None = None
    expected_revision: int = Field(ge=1)


class ScenarioRevisionRequest(StrictModel):
    expected_revision: int = Field(ge=1)
    requested_by: str = Field(default="management", min_length=1, max_length=100)


class ScenarioView(ScenarioCreate):
    id: UUID
    project_id: UUID
    base_revision: int
    applied_change_set_id: UUID | None
    status: ScenarioStatus
    revision: int
    created_at: datetime
    updated_at: datetime


class ScenarioDiffView(StrictModel):
    scenario_id: UUID
    base_revision: int
    current_revision: int
    rebase_required: bool
    creates: list[ChangeOperation]
    updates: list[ChangeOperation]
    retires: list[ChangeOperation]
    conflicts: list[dict[str, Any]]


class ScenarioComparisonView(StrictModel):
    left_scenario_id: UUID
    right_scenario_id: UUID
    shared_operations: list[ChangeOperation]
    only_left: list[ChangeOperation]
    only_right: list[ChangeOperation]
    conflicting_targets: list[dict[str, Any]]


class ScenarioCompareRequest(StrictModel):
    left_scenario_id: UUID
    right_scenario_id: UUID


class LearningCaseStatus(StrEnum):
    DRAFT = "DRAFT"
    CONFIRMED = "CONFIRMED"
    RETIRED = "RETIRED"


class LearningCaseCreate(StrictModel):
    title: str = Field(min_length=1, max_length=300)
    industry: str | None = Field(default=None, max_length=200)
    organization_scale: str | None = Field(default=None, max_length=200)
    challenge: str = Field(min_length=1, max_length=10_000)
    context: str | None = Field(default=None, max_length=20_000)
    intervention: str | None = Field(default=None, max_length=20_000)
    outcome: str | None = Field(default=None, max_length=20_000)
    lessons: list[str] = Field(default_factory=list, max_length=100)
    tags: list[str] = Field(default_factory=list, max_length=100)
    evidence: list[EvidenceReference] = Field(default_factory=list)
    reusable: bool = False
    reusable_summary: str | None = Field(default=None, max_length=5000)
    status: LearningCaseStatus = LearningCaseStatus.DRAFT


class LearningCaseUpdate(StrictModel):
    title: str | None = Field(default=None, min_length=1, max_length=300)
    industry: str | None = Field(default=None, max_length=200)
    organization_scale: str | None = Field(default=None, max_length=200)
    challenge: str | None = Field(default=None, min_length=1, max_length=10_000)
    context: str | None = Field(default=None, max_length=20_000)
    intervention: str | None = Field(default=None, max_length=20_000)
    outcome: str | None = Field(default=None, max_length=20_000)
    lessons: list[str] | None = Field(default=None, max_length=100)
    tags: list[str] | None = Field(default=None, max_length=100)
    evidence: list[EvidenceReference] | None = None
    reusable: bool | None = None
    reusable_summary: str | None = Field(default=None, max_length=5000)
    status: LearningCaseStatus | None = None
    expected_revision: int = Field(ge=1)


class LearningCaseDraftFromActionCreate(StrictModel):
    action_invocation_id: UUID
    action_observation_ids: list[UUID] = Field(min_length=1, max_length=100)
    title: str = Field(min_length=1, max_length=300)
    challenge: str = Field(min_length=1, max_length=10_000)
    context: str | None = Field(default=None, max_length=20_000)
    lessons: list[str] = Field(default_factory=list, max_length=100)
    tags: list[str] = Field(default_factory=list, max_length=100)


class LearningCaseDraftFromScenarioCreate(StrictModel):
    scenario_id: UUID
    title: str = Field(min_length=1, max_length=300)
    challenge: str = Field(min_length=1, max_length=10_000)
    context: str | None = Field(default=None, max_length=20_000)
    lessons: list[str] = Field(default_factory=list, max_length=100)
    tags: list[str] = Field(default_factory=list, max_length=100)


class LearningCaseView(LearningCaseCreate):
    id: UUID
    project_id: UUID
    source_action_invocation_id: UUID | None
    source_action_observation_ids: list[UUID]
    source_scenario_id: UUID | None
    origin_kind: str
    revision: int
    created_at: datetime
    updated_at: datetime


class LearningCaseCatalogView(StrictModel):
    id: UUID
    industry: str | None
    organization_scale: str | None
    reusable_summary: str
    tags: list[str]
    updated_at: datetime


class MetricScope(StrEnum):
    LOCAL = "LOCAL"
    ENTERPRISE_OUTCOME = "ENTERPRISE_OUTCOME"


class MetricDirection(StrEnum):
    HIGHER_IS_BETTER = "HIGHER_IS_BETTER"
    LOWER_IS_BETTER = "LOWER_IS_BETTER"
    TARGET_RANGE = "TARGET_RANGE"


class MetricObservationStatus(StrEnum):
    ON_TARGET = "ON_TARGET"
    MISS = "MISS"
    UNKNOWN = "UNKNOWN"


class MetricDefinitionCreate(StrictModel):
    key: str = Field(pattern=r"^[a-z][a-z0-9_.-]{0,99}$")
    name: str = Field(min_length=1, max_length=300)
    description: str | None = Field(default=None, max_length=4000)
    scope: MetricScope = MetricScope.LOCAL
    direction: MetricDirection = MetricDirection.HIGHER_IS_BETTER
    owner_entity_id: UUID | None = None
    strategy_entity_id: UUID | None = None
    outcome_entity_id: UUID | None = None
    unit: str | None = Field(default=None, max_length=100)
    target_value: Any | None = None
    properties: dict[str, Any] = Field(default_factory=dict)


class MetricDefinitionUpdate(StrictModel):
    name: str | None = Field(default=None, min_length=1, max_length=300)
    description: str | None = Field(default=None, max_length=4000)
    scope: MetricScope | None = None
    direction: MetricDirection | None = None
    owner_entity_id: UUID | None = None
    strategy_entity_id: UUID | None = None
    outcome_entity_id: UUID | None = None
    unit: str | None = Field(default=None, max_length=100)
    target_value: Any | None = None
    properties: dict[str, Any] | None = None
    active: bool | None = None
    expected_revision: int = Field(ge=1)


class MetricDefinitionView(MetricDefinitionCreate):
    id: UUID
    entity_id: UUID
    project_id: UUID
    active: bool
    revision: int
    created_at: datetime
    updated_at: datetime


class MetricObservationCreate(StrictModel):
    period_key: str = Field(min_length=1, max_length=100)
    period_start: datetime | None = None
    period_end: datetime | None = None
    dimensions: dict[str, Any] = Field(default_factory=dict)
    observed_at: datetime | None = None
    value: Any
    status: MetricObservationStatus = MetricObservationStatus.UNKNOWN
    source: str = Field(default="MANUAL", min_length=1, max_length=100)
    unit: str | None = Field(default=None, max_length=100)
    evidence: list[EvidenceReference] = Field(default_factory=list)

    @model_validator(mode="after")
    def validate_period(self) -> MetricObservationCreate:
        if self.period_start and self.period_end and self.period_end < self.period_start:
            raise ValueError("period_end 不能早于 period_start")
        return self


class MetricObservationUpdate(StrictModel):
    value: Any
    observed_at: datetime | None = None
    status: MetricObservationStatus = MetricObservationStatus.UNKNOWN
    evidence: list[EvidenceReference] | None = None
    expected_revision: int = Field(ge=1)


class MetricObservationRetire(StrictModel):
    expected_revision: int = Field(ge=1)


class MetricObservationView(MetricObservationCreate):
    id: UUID
    project_id: UUID
    metric_definition_id: UUID
    observed_at: datetime
    numeric_value: float | None
    definition_revision: int
    version: int
    record_status: str
    supersedes_id: UUID | None
    revision: int
    created_at: datetime
    updated_at: datetime


class MeetingActionStatus(StrEnum):
    OPEN = "OPEN"
    DONE = "DONE"
    CANCELLED = "CANCELLED"


class MeetingDecision(StrictModel):
    summary: str = Field(min_length=1, max_length=4000)
    owner_entity_id: UUID | None = None
    rationale: str | None = Field(default=None, max_length=4000)


class MeetingActionItem(StrictModel):
    title: str = Field(min_length=1, max_length=1000)
    owner_entity_id: UUID | None = None
    due_at: datetime | None = None
    status: MeetingActionStatus = MeetingActionStatus.OPEN
    expected_outcome: str | None = Field(default=None, max_length=4000)


class MeetingEscalation(StrictModel):
    topic: str = Field(min_length=1, max_length=1000)
    level: str = Field(default="MANAGEMENT", min_length=1, max_length=100)
    reason: str | None = Field(default=None, max_length=4000)


class MeetingRecordCreate(StrictModel):
    title: str = Field(min_length=1, max_length=300)
    occurred_at: datetime
    participant_entity_ids: list[UUID] = Field(default_factory=list)
    related_entity_ids: list[UUID] = Field(default_factory=list)
    topics: list[str] = Field(default_factory=list, max_length=100)
    decisions: list[MeetingDecision] = Field(default_factory=list, max_length=100)
    action_items: list[MeetingActionItem] = Field(default_factory=list, max_length=200)
    escalations: list[MeetingEscalation] = Field(default_factory=list, max_length=100)
    evidence: list[EvidenceReference] = Field(default_factory=list)


class MeetingRecordUpdate(StrictModel):
    title: str | None = Field(default=None, min_length=1, max_length=300)
    occurred_at: datetime | None = None
    participant_entity_ids: list[UUID] | None = None
    related_entity_ids: list[UUID] | None = None
    topics: list[str] | None = Field(default=None, max_length=100)
    decisions: list[MeetingDecision] | None = Field(default=None, max_length=100)
    action_items: list[MeetingActionItem] | None = Field(default=None, max_length=200)
    escalations: list[MeetingEscalation] | None = Field(default=None, max_length=100)
    evidence: list[EvidenceReference] | None = None
    expected_revision: int = Field(ge=1)


class MeetingRecordView(MeetingRecordCreate):
    id: UUID
    project_id: UUID
    revision: int
    created_at: datetime
    updated_at: datetime


class TradeoffStatus(StrEnum):
    PROPOSED = "PROPOSED"
    ACCEPTED = "ACCEPTED"
    RETIRED = "RETIRED"


class DesignTradeoffCreate(StrictModel):
    title: str = Field(min_length=1, max_length=300)
    issue_family: str = Field(pattern=r"^[A-Z][A-Z0-9_.-]{0,99}$")
    description: str = Field(min_length=1, max_length=20_000)
    benefit: str = Field(min_length=1, max_length=10_000)
    cost: str = Field(min_length=1, max_length=10_000)
    affected_entity_ids: list[UUID] = Field(default_factory=list)
    monitoring_metric_ids: list[UUID] = Field(default_factory=list)
    status: TradeoffStatus = TradeoffStatus.PROPOSED
    accepted_by: str | None = Field(default=None, max_length=100)

    @model_validator(mode="after")
    def accepted_tradeoff_requires_owner(self) -> DesignTradeoffCreate:
        if self.status == TradeoffStatus.ACCEPTED and not self.accepted_by:
            raise ValueError("accepted tradeoffs require accepted_by")
        return self


class DesignTradeoffUpdate(StrictModel):
    title: str | None = Field(default=None, min_length=1, max_length=300)
    description: str | None = Field(default=None, min_length=1, max_length=20_000)
    benefit: str | None = Field(default=None, min_length=1, max_length=10_000)
    cost: str | None = Field(default=None, min_length=1, max_length=10_000)
    affected_entity_ids: list[UUID] | None = None
    monitoring_metric_ids: list[UUID] | None = None
    status: TradeoffStatus | None = None
    accepted_by: str | None = Field(default=None, max_length=100)
    expected_revision: int = Field(ge=1)


class DesignTradeoffView(DesignTradeoffCreate):
    id: UUID
    project_id: UUID
    revision: int
    created_at: datetime
    updated_at: datetime


class ManagementSignalSide(StrEnum):
    DESIGN = "DESIGN"
    OUTCOME = "OUTCOME"


class ManagementSeverity(StrEnum):
    INFO = "INFO"
    WARNING = "WARNING"
    HIGH = "HIGH"
    CRITICAL = "CRITICAL"


class SignalDirection(StrEnum):
    PROBLEM = "PROBLEM"
    HEALTHY = "HEALTHY"


class ManagementAnalysisStatus(StrEnum):
    RUNNING = "RUNNING"
    COMPLETED = "COMPLETED"
    FAILED = "FAILED"


class ManagementAnalysisRequest(StrictModel):
    requested_by: str = Field(default="management", min_length=1, max_length=100)
    include_design: bool = True
    include_outcome: bool = True
    recent_observation_limit: int = Field(default=12, ge=2, le=100)


class ManagementAnalysisRunView(StrictModel):
    id: UUID
    project_id: UUID
    requested_by: str
    status: ManagementAnalysisStatus
    parameters: dict[str, Any]
    design_signal_count: int
    outcome_signal_count: int
    insight_count: int
    error: dict[str, Any] | None
    created_at: datetime
    finished_at: datetime | None


class ManagementSignalView(StrictModel):
    id: UUID
    run_id: UUID
    project_id: UUID
    side: ManagementSignalSide
    signal_key: str
    issue_family: str
    title: str
    summary: str
    severity: ManagementSeverity
    confidence: float = Field(ge=0, le=1)
    expected_direction: SignalDirection
    affected_entity_ids: list[UUID]
    evidence: list[EvidenceReference]
    facts: dict[str, Any]
    fingerprint: str
    created_at: datetime


class ManagementInsightClassification(StrEnum):
    CORROBORATED = "CORROBORATED"
    STRUCTURAL_WARNING = "STRUCTURAL_WARNING"
    UNEXPLAINED_ANOMALY = "UNEXPLAINED_ANOMALY"
    EVIDENCE_CONFLICT = "EVIDENCE_CONFLICT"
    ACCEPTED_TRADEOFF = "ACCEPTED_TRADEOFF"


class ManagementInsightStatus(StrEnum):
    OPEN = "OPEN"
    CONFIRMED = "CONFIRMED"
    DISMISSED = "DISMISSED"
    MONITORING = "MONITORING"
    RESOLVED = "RESOLVED"


class ManagementInsightUpdate(StrictModel):
    status: ManagementInsightStatus | None = None
    management_feedback: str | None = Field(default=None, max_length=20_000)
    provided_by: str = Field(default="management", min_length=1, max_length=100)
    expected_revision: int = Field(ge=1)


class ManagementInsightView(StrictModel):
    id: UUID
    run_id: UUID
    project_id: UUID
    issue_id: UUID | None = None
    occurrence_number: int = Field(default=1, ge=1)
    occurrence_signature: str | None = None
    evidence_changed: bool = False
    classification: ManagementInsightClassification
    issue_family: str
    title: str
    summary: str
    severity: ManagementSeverity
    confidence: float = Field(ge=0, le=1)
    design_signal_ids: list[UUID]
    outcome_signal_ids: list[UUID]
    affected_entity_ids: list[UUID]
    status: ManagementInsightStatus
    rationale: str
    management_feedback: str | None
    revision: int
    created_at: datetime
    updated_at: datetime


class ManagementIssueView(StrictModel):
    id: UUID
    project_id: UUID
    issue_key: str
    issue_family: str
    title: str
    affected_entity_ids: list[UUID]
    status: ManagementInsightStatus
    management_feedback: str | None
    last_occurrence_signature: str
    occurrence_count: int = Field(ge=1)
    revision: int
    first_seen_at: datetime
    last_seen_at: datetime
    created_at: datetime
    updated_at: datetime


class ManagementIssueFeedbackView(StrictModel):
    id: UUID
    project_id: UUID
    issue_id: UUID
    insight_id: UUID | None
    from_status: ManagementInsightStatus
    to_status: ManagementInsightStatus
    feedback: str | None
    provided_by: str
    created_at: datetime


class ManagementIssueReopen(StrictModel):
    reason: str = Field(min_length=1, max_length=20_000)
    provided_by: str = Field(default="management", min_length=1, max_length=100)
    expected_revision: int = Field(ge=1)


class InformationRequestPriority(StrEnum):
    LOW = "LOW"
    MEDIUM = "MEDIUM"
    HIGH = "HIGH"
    CRITICAL = "CRITICAL"


class InformationRequestStatus(StrEnum):
    OPEN = "OPEN"
    ANSWERED = "ANSWERED"
    CANCELLED = "CANCELLED"


class InformationRequestCreate(StrictModel):
    title: str = Field(min_length=1, max_length=300)
    question: str = Field(min_length=1, max_length=20_000)
    reason: str = Field(min_length=1, max_length=20_000)
    priority: InformationRequestPriority = InformationRequestPriority.MEDIUM
    target_entity_ids: list[UUID] = Field(default_factory=list)
    requested_by: str = Field(default="management-agent", min_length=1, max_length=100)


class InformationRequestUpdate(StrictModel):
    status: InformationRequestStatus | None = None
    answer: str | None = Field(default=None, max_length=20_000)
    priority: InformationRequestPriority | None = None
    expected_revision: int = Field(ge=1)

    @model_validator(mode="after")
    def answered_request_requires_answer(self) -> InformationRequestUpdate:
        if self.status == InformationRequestStatus.ANSWERED and not self.answer:
            raise ValueError("answered requests require an answer")
        return self


class InformationRequestView(InformationRequestCreate):
    id: UUID
    project_id: UUID
    status: InformationRequestStatus
    answer: str | None
    revision: int
    created_at: datetime
    updated_at: datetime


class EvaluationSuiteStatus(StrEnum):
    DRAFT = "DRAFT"
    ACTIVE = "ACTIVE"
    RETIRED = "RETIRED"


class EvaluationCaseStatus(StrEnum):
    ACTIVE = "ACTIVE"
    RETIRED = "RETIRED"


class EvaluationSuiteCreate(StrictModel):
    name: str = Field(min_length=1, max_length=300)
    agent_kind: AgentKind
    description: str | None = Field(default=None, max_length=4000)


class EvaluationSuiteUpdate(StrictModel):
    name: str | None = Field(default=None, min_length=1, max_length=300)
    description: str | None = Field(default=None, max_length=4000)
    status: EvaluationSuiteStatus | None = None
    expected_revision: int = Field(ge=1)


class EvaluationSuiteView(EvaluationSuiteCreate):
    id: UUID
    project_id: UUID
    status: EvaluationSuiteStatus
    revision: int
    created_at: datetime
    updated_at: datetime


class EvaluationCaseCreate(StrictModel):
    name: str = Field(min_length=1, max_length=300)
    input: str = Field(min_length=1, max_length=100_000)
    expected_action_keys: list[str] = Field(default_factory=list, max_length=100)
    required_terms: list[str] = Field(default_factory=list, max_length=100)
    forbidden_terms: list[str] = Field(default_factory=list, max_length=100)
    minimum_citations: int = Field(default=0, ge=0, le=1000)


class EvaluationCaseView(EvaluationCaseCreate):
    id: UUID
    suite_id: UUID
    project_id: UUID
    status: EvaluationCaseStatus
    created_at: datetime


class EvaluationPrediction(StrictModel):
    case_id: UUID
    agent_run_id: UUID | None = None
    content: str = Field(default="", max_length=100_000)
    action_keys: list[str] = Field(default_factory=list, max_length=100)
    citation_count: int = Field(default=0, ge=0, le=1000)


class EvaluationRunCreate(StrictModel):
    label: str | None = Field(default=None, max_length=300)
    model_profile_id: UUID | None = None
    predictions: list[EvaluationPrediction] = Field(min_length=1)


class EvaluationExecuteCreate(StrictModel):
    """Run every active case through the persisted Agent runtime before scoring."""

    label: str | None = Field(default=None, max_length=300)
    model_profile_id: UUID | None = None
    allow_external_model: bool = False
    share_project_context_with_model: bool = False


class EvaluationRunView(StrictModel):
    id: UUID
    project_id: UUID
    suite_id: UUID
    model_profile_id: UUID | None
    label: str | None
    status: str
    total_cases: int
    passed_cases: int
    average_score: float = Field(ge=0, le=1)
    created_at: datetime


class EvaluationExecutionStatus(StrEnum):
    QUEUED = "QUEUED"
    RUNNING = "RUNNING"
    FINALIZING = "FINALIZING"
    COMPLETED = "COMPLETED"
    FAILED = "FAILED"


class EvaluationExecutionView(StrictModel):
    id: UUID
    project_id: UUID
    suite_id: UUID
    evaluation_run_id: UUID | None
    model_profile_id: UUID | None
    label: str | None
    status: EvaluationExecutionStatus
    total_cases: int = Field(ge=0)
    completed_cases: int = Field(ge=0)
    error: dict[str, Any] | None
    created_at: datetime
    updated_at: datetime


class EvaluationResultView(StrictModel):
    id: UUID
    run_id: UUID
    case_id: UUID
    project_id: UUID
    candidate_content: str
    candidate_action_keys: list[str]
    candidate_citation_count: int
    score: float = Field(ge=0, le=1)
    passed: bool
    checks: list[dict[str, Any]]
    created_at: datetime


class PublicationCreate(StrictModel):
    label: str = Field(min_length=1, max_length=200)
    notes: str | None = Field(default=None, max_length=4000)
    expected_project_revision: int = Field(ge=0)


class PublicationView(StrictModel):
    id: UUID
    project_id: UUID
    version: int
    label: str
    notes: str | None
    project_revision: int
    ontology_release_id: UUID | None
    entity_count: int
    relation_count: int
    created_at: datetime


class QueryModelScope(StrEnum):
    DRAFT = "DRAFT"
    PUBLISHED = "PUBLISHED"


class QuerySnapshotCreate(StrictModel):
    model_scope: QueryModelScope = QueryModelScope.DRAFT
    publication_id: UUID | None = None
    data_cutoff: datetime | None = None
    include_observations: bool = True


class QuerySnapshotView(StrictModel):
    id: UUID
    project_id: UUID
    model_scope: QueryModelScope
    publication_id: UUID | None
    project_revision: int
    data_cutoff: datetime
    manifest: dict[str, Any]
    created_at: datetime


class ExecutiveContextView(StrictModel):
    company: CompanyView
    project: ProjectView
    publication: PublicationView | None
    graph: GraphView
    open_hypotheses: int
    active_scenarios: int


class ImportKind(StrEnum):
    COMPANYCHECK_CSV = "COMPANYCHECK_CSV"
    CSV = "CSV"
    XLSX = "XLSX"
    DOCX = "DOCX"
    PDF = "PDF"
    TXT = "TXT"
    JSON = "JSON"


class ImportPreviewView(StrictModel):
    id: UUID
    project_id: UUID
    source_system_id: UUID | None
    file_name: str
    kind: ImportKind
    detected_encoding: str | None
    columns: list[str]
    sample_rows: list[dict[str, Any]]
    suggested_mapping: dict[str, str]
    warnings: list[str]
    expires_at: datetime


class ImportConfirmRequest(StrictModel):
    preview_id: UUID
    mapping: dict[str, str] = Field(default_factory=dict)
    options: dict[str, Any] = Field(default_factory=dict)


class ImportResultView(StrictModel):
    id: UUID
    project_id: UUID
    status: str
    documents_created: int
    fragments_created: int
    claims_created: int
    entities_created: int = 0
    entities_updated: int = 0
    identities_bound: int = 0
    observations_created: int = 0
    relations_created: int = 0
    mappings_applied: int = 0
    rows_skipped: int
    warnings: list[str]
    created_at: datetime


class SourceDocumentView(StrictModel):
    id: UUID
    project_id: UUID
    source_system_id: UUID | None
    file_name: str
    kind: ImportKind
    sha256: str
    status: str
    metadata: dict[str, Any]
    created_at: datetime


class EvidenceFragmentView(StrictModel):
    id: UUID
    source_document_id: UUID
    locator: str
    text: str
    metadata: dict[str, Any]
    created_at: datetime


class ClaimView(StrictModel):
    id: UUID
    project_id: UUID
    fragment_id: UUID
    subject: str
    predicate: str
    value: Any
    status: str
    created_at: datetime


class SourceSystemKind(StrEnum):
    FILE = "FILE"
    SQLITE = "SQLITE"
    POSTGRESQL = "POSTGRESQL"
    MYSQL = "MYSQL"
    SQLSERVER = "SQLSERVER"
    REST = "REST"
    ERP = "ERP"
    MES = "MES"
    CRM = "CRM"


class SourceSystemCreate(StrictModel):
    name: str = Field(min_length=1, max_length=200)
    kind: SourceSystemKind
    description: str | None = Field(default=None, max_length=4000)
    connection_profile: dict[str, Any] = Field(default_factory=dict)


class SourceSystemUpdate(StrictModel):
    name: str | None = Field(default=None, min_length=1, max_length=200)
    description: str | None = Field(default=None, max_length=4000)
    connection_profile: dict[str, Any] | None = None
    expected_revision: int = Field(ge=1)


class SourceSystemView(StrictModel):
    id: UUID
    project_id: UUID
    name: str
    kind: SourceSystemKind
    description: str | None
    configured_fields: list[str] = Field(default_factory=list)
    status: str
    last_tested_at: datetime | None
    revision: int
    created_at: datetime
    updated_at: datetime


class SourceAssetCreate(StrictModel):
    asset_key: str = Field(min_length=1, max_length=500)
    name: str = Field(min_length=1, max_length=300)
    description: str | None = Field(default=None, max_length=4000)


class SourceAssetUpdate(StrictModel):
    name: str | None = Field(default=None, min_length=1, max_length=300)
    description: str | None = Field(default=None, max_length=4000)
    status: str | None = Field(default=None, min_length=1, max_length=32)
    expected_revision: int = Field(ge=1)


class SourceAssetView(StrictModel):
    id: UUID
    project_id: UUID
    source_system_id: UUID
    asset_key: str
    name: str
    description: str | None
    schema_fingerprint: str | None
    schema_fields: list[str]
    status: str
    revision: int
    created_at: datetime
    updated_at: datetime


class RawBatchView(StrictModel):
    id: UUID
    project_id: UUID
    source_asset_id: UUID
    source_document_id: UUID
    content_sha256: str
    parser_version: str
    schema_fingerprint: str
    status: str
    record_count: int
    error_count: int
    created_at: datetime


class RawRecordView(StrictModel):
    id: UUID
    project_id: UUID
    raw_batch_id: UUID
    row_number: int
    source_locator: str
    source_record_key: str | None
    payload: dict[str, Any]
    payload_sha256: str
    created_at: datetime


class MaterializationRunView(StrictModel):
    id: UUID
    project_id: UUID
    raw_batch_id: UUID
    mapping_fingerprint: str
    mapping_ids: list[UUID]
    status: str
    records_processed: int
    entities_created: int
    identities_bound: int
    observations_created: int
    relations_created: int
    mappings_applied: int
    output_entity_ids: list[UUID]
    output_identity_ids: list[UUID]
    output_assertion_ids: list[UUID]
    output_relation_ids: list[UUID]
    errors: list[dict[str, Any]]
    created_at: datetime
    finished_at: datetime | None


class SourceIdentityStatus(StrEnum):
    UNRESOLVED = "UNRESOLVED"
    BOUND = "BOUND"


class SourceIdentityView(StrictModel):
    id: UUID
    project_id: UUID
    source_system_id: UUID
    source_asset: str
    source_record_key: str
    target_type_key: str
    entity_id: UUID
    entity_name: str
    status: SourceIdentityStatus
    revision: int
    created_at: datetime
    updated_at: datetime


class SourceIdentityBind(StrictModel):
    entity_id: UUID
    expected_revision: int = Field(ge=1)


class ObservationAssertionView(StrictModel):
    id: UUID
    project_id: UUID
    entity_id: UUID
    source_identity_id: UUID
    source_document_id: UUID
    fragment_id: UUID | None
    raw_record_id: UUID | None
    semantic_mapping_id: UUID | None
    materialization_run_id: UUID | None
    source_asset: str
    source_record_key: str
    field_key: str
    value: Any
    authority_priority: int
    status: str
    version: int
    supersedes_id: UUID | None
    observed_at: datetime
    created_at: datetime
    updated_at: datetime


class ObservationConflictStatus(StrEnum):
    OPEN = "OPEN"
    RESOLVED = "RESOLVED"
    SUPERSEDED = "SUPERSEDED"


class ObservationConflictResolutionKind(StrEnum):
    CHOOSE_ASSERTION = "CHOOSE_ASSERTION"
    OVERRIDE = "OVERRIDE"
    IGNORE = "IGNORE"


class ObservationConflictCandidate(StrictModel):
    assertion_id: UUID
    source_identity_id: UUID
    value: Any
    authority_priority: int
    observed_at: datetime


class ObservationConflictResolve(StrictModel):
    resolution_kind: ObservationConflictResolutionKind
    chosen_assertion_id: UUID | None = None
    override_value: Any | None = None
    rationale: str = Field(min_length=1, max_length=4000)
    resolved_by: str = Field(default="developer", min_length=1, max_length=100)
    expected_revision: int = Field(ge=1)


class ObservationConflictView(StrictModel):
    id: UUID
    project_id: UUID
    entity_id: UUID
    field_key: str
    candidates: list[ObservationConflictCandidate]
    status: ObservationConflictStatus
    resolution_kind: ObservationConflictResolutionKind | None
    chosen_assertion_id: UUID | None
    override_value: Any | None
    rationale: str | None
    resolved_by: str | None
    revision: int
    created_at: datetime
    updated_at: datetime


class SourceIdentityBindAction(SourceIdentityBind):
    identity_id: UUID


class ObservationConflictResolveAction(ObservationConflictResolve):
    conflict_id: UUID


class RawBatchMaterializeAction(StrictModel):
    raw_batch_id: UUID


class MaterialFragmentsReadAction(StrictModel):
    source_document_id: UUID
    offset: int = Field(default=0, ge=0)
    limit: int = Field(default=40, ge=1, le=100)


class GraphNeighborhoodReadAction(StrictModel):
    root_entity_id: UUID
    depth: int = Field(default=2, ge=0, le=8)
    include_observations: bool = True
    max_entities: int = Field(default=500, ge=1, le=2000)
    max_relations: int = Field(default=1000, ge=1, le=10000)


class SemanticMappingAdvanceAction(StrictModel):
    mapping_id: UUID
    command: str = Field(pattern=r"^(VALIDATE|APPROVE|DISABLE)$")
    expected_revision: int = Field(ge=1)


class SemanticMappingSuggestionsReadAction(StrictModel):
    target_type_key: str = Field(min_length=1, max_length=64)
    source_system_id: UUID | None = None
    source_asset: str | None = Field(default=None, max_length=500)
    include_existing: bool = False


class SourceSystemTestView(StrictModel):
    source_system_id: UUID
    ok: bool
    message: str
    checked_at: datetime


class SourceConnectorExtractRequest(StrictModel):
    asset_key: str = Field(min_length=1, max_length=500)
    limit: int = Field(default=1000, ge=1, le=5000)
    watermark_column: str | None = Field(default=None, max_length=200)
    tie_breaker_column: str | None = Field(default=None, max_length=200)
    after_watermark: Any | None = None
    after_tie_breaker: Any | None = None


class SourceConnectorExtractView(StrictModel):
    preview_id: UUID
    content_sha256: str
    source_system_id: UUID
    asset_key: str
    columns: list[str]
    rows: list[dict[str, Any]]
    next_watermark: Any | None
    next_tie_breaker: Any | None


class SourceConnectorSyncRequest(StrictModel):
    preview_id: UUID


class SourceConnectorSyncView(StrictModel):
    extraction: SourceConnectorExtractView
    import_result: ImportResultView


class SemanticMappingCreate(StrictModel):
    source_system_id: UUID
    source_asset: str = Field(min_length=1, max_length=500)
    source_field: str = Field(min_length=1, max_length=500)
    target_type_key: str = Field(min_length=1, max_length=64)
    target_property_key: str = Field(min_length=1, max_length=64)
    transform_expression: str | None = Field(default=None, max_length=4000)
    authority_priority: int = Field(default=100, ge=0)


class SemanticMappingStatus(StrEnum):
    DRAFT = "DRAFT"
    VALIDATED = "VALIDATED"
    APPROVED = "APPROVED"
    SUPERSEDED = "SUPERSEDED"
    DISABLED = "DISABLED"


class SemanticMappingCommand(StrictModel):
    expected_revision: int = Field(ge=1)


class SemanticMappingView(SemanticMappingCreate):
    id: UUID
    project_id: UUID
    status: SemanticMappingStatus
    revision: int
    validation_report: dict[str, Any] = Field(default_factory=dict)
    created_at: datetime
    updated_at: datetime


class SemanticMappingSuggestionRequest(StrictModel):
    """Read-only, evidence-free candidate search for a human-reviewed mapping."""

    target_type_key: str = Field(min_length=1, max_length=64)
    source_system_id: UUID | None = None
    source_asset: str | None = Field(default=None, max_length=500)
    include_existing: bool = False


class SemanticMappingSuggestionView(StrictModel):
    source_system_id: UUID
    source_system_name: str
    source_asset: str
    source_field: str
    target_type_key: str
    target_type_name: str
    target_property_key: str
    target_property_name: str
    transform_expression: str | None
    match_kind: str
    confidence: float = Field(ge=0, le=1)
    rationale: str
    existing_mapping_id: UUID | None = None
    safe_to_auto_apply: bool = False


class SemanticMappingSuggestionPage(StrictModel):
    items: list[SemanticMappingSuggestionView]
    total: int
    warnings: list[str] = Field(default_factory=list)


class SemanticRelationMappingCreate(StrictModel):
    source_system_id: UUID
    source_asset: str = Field(min_length=1, max_length=500)
    source_type_key: str = Field(min_length=1, max_length=64)
    source_field: str = Field(min_length=1, max_length=500)
    relation_type_key: str = Field(min_length=1, max_length=64)
    source_role_key: str = Field(min_length=1, max_length=64)
    target_type_key: str = Field(min_length=1, max_length=64)
    target_role_key: str = Field(min_length=1, max_length=64)
    target_asset: str | None = Field(default=None, max_length=500)
    transform_expression: str | None = Field(default=None, max_length=4000)


class SemanticRelationMappingStatus(StrEnum):
    DRAFT = "DRAFT"
    VALIDATED = "VALIDATED"
    APPROVED = "APPROVED"
    DISABLED = "DISABLED"


class SemanticRelationMappingView(SemanticRelationMappingCreate):
    id: UUID
    project_id: UUID
    status: SemanticRelationMappingStatus
    revision: int
    validation_report: dict[str, Any] = Field(default_factory=dict)
    created_at: datetime
    updated_at: datetime


class SemanticRelationMappingCommand(StrictModel):
    expected_revision: int = Field(ge=1)


class SemanticPathStep(StrictModel):
    relation_type_key: str = Field(min_length=1, max_length=64)
    from_role: str = Field(min_length=1, max_length=64)
    to_role: str = Field(min_length=1, max_length=64)


class SemanticAggregation(StrEnum):
    NONE = "NONE"
    COUNT_DISTINCT = "COUNT_DISTINCT"
    SUM = "SUM"
    AVG = "AVG"
    MIN = "MIN"
    MAX = "MAX"


class SemanticValueLayer(StrEnum):
    DESIGNED = "DESIGNED"
    OBSERVED = "OBSERVED"
    RESOLVED = "RESOLVED"


class SemanticDatasetColumn(StrictModel):
    key: str = Field(min_length=1, max_length=100)
    label: str = Field(min_length=1, max_length=300)
    path: list[SemanticPathStep] = Field(default_factory=list, max_length=8)
    property_key: str = Field(min_length=1, max_length=200)
    aggregation: SemanticAggregation = SemanticAggregation.NONE
    value_layer: SemanticValueLayer = SemanticValueLayer.RESOLVED
    unit: str | None = Field(default=None, max_length=100)


class SemanticDatasetCreate(StrictModel):
    key: str = Field(min_length=1, max_length=100, pattern=r"^[a-z0-9_.-]+$")
    name: str = Field(min_length=1, max_length=300)
    description: str | None = Field(default=None, max_length=4000)
    root_type_key: str = Field(min_length=1, max_length=64)
    columns: list[SemanticDatasetColumn] = Field(min_length=1, max_length=100)


class SemanticDatasetUpdate(StrictModel):
    name: str | None = Field(default=None, min_length=1, max_length=300)
    description: str | None = Field(default=None, max_length=4000)
    columns: list[SemanticDatasetColumn] | None = Field(
        default=None, min_length=1, max_length=100
    )
    status: str | None = Field(default=None, pattern=r"^(ACTIVE|RETIRED)$")
    expected_revision: int = Field(ge=1)


class SemanticDatasetView(SemanticDatasetCreate):
    id: UUID
    project_id: UUID
    status: str
    revision: int
    created_at: datetime
    updated_at: datetime


class SemanticDatasetQuery(StrictModel):
    query_snapshot_id: UUID | None = None
    limit: int = Field(default=200, ge=1, le=5000)
    include_lineage: bool = True


class SemanticDatasetResult(StrictModel):
    run_id: UUID
    dataset_id: UUID
    query_snapshot_id: UUID
    columns: list[SemanticDatasetColumn]
    rows: list[dict[str, Any]]
    warnings: list[dict[str, Any]]
    plan: dict[str, Any]


class SemanticDatasetExport(StrictModel):
    query_snapshot_id: UUID | None = None
    format: str = Field(default="csv", pattern=r"^(csv|json)$")


class SemanticDatasetQueryAction(SemanticDatasetQuery):
    dataset_id: UUID


class ExportRequest(StrictModel):
    format: str = Field(default="json", pattern=r"^(json|csv|xlsx|bundle)$")
    include_evidence: bool = True
    include_lineage: bool = True
    query_snapshot_id: UUID | None = None
    release_id: UUID | None = None
    filters: dict[str, Any] = Field(default_factory=dict)


class ExportJobView(StrictModel):
    id: UUID
    project_id: UUID
    status: str
    format: str
    download_url: str | None
    error: str | None
    created_at: datetime
    updated_at: datetime


class RestorePreviewView(StrictModel):
    id: UUID
    file_name: str
    package_sha256: str
    status: str
    summary: dict[str, Any]
    expires_at: datetime
    created_at: datetime


class RestoreConfirmRequest(StrictModel):
    preview_id: UUID


class RestoreResultView(StrictModel):
    company_id: UUID
    project_id: UUID
    restored_counts: dict[str, int]
    source_connections_reset: int
    active_runs_cancelled: int
    warnings: list[str]


class ModelProvider(StrEnum):
    OPENAI_COMPATIBLE = "OPENAI_COMPATIBLE"
    DEEPSEEK = "DEEPSEEK"
    MOCK = "MOCK"


class ModelProfileCreate(StrictModel):
    name: str = Field(min_length=1, max_length=200)
    provider: ModelProvider
    base_url: str = Field(min_length=1, max_length=1000)
    model: str = Field(min_length=1, max_length=200)
    api_key: str | None = Field(
        default=None,
        min_length=1,
        max_length=1000,
        json_schema_extra={"writeOnly": True},
    )
    temperature: float = Field(default=0.2, ge=0, le=2)
    timeout_seconds: int = Field(default=90, ge=5, le=600)
    is_default: bool = False


class ModelProfileUpdate(StrictModel):
    name: str | None = Field(default=None, min_length=1, max_length=200)
    base_url: str | None = Field(default=None, min_length=1, max_length=1000)
    model: str | None = Field(default=None, min_length=1, max_length=200)
    api_key: str | None = Field(
        default=None,
        min_length=1,
        max_length=1000,
        json_schema_extra={"writeOnly": True},
    )
    temperature: float | None = Field(default=None, ge=0, le=2)
    timeout_seconds: int | None = Field(default=None, ge=5, le=600)
    enabled: bool | None = None
    is_default: bool | None = None


class ModelProfileView(StrictModel):
    id: UUID
    name: str
    provider: ModelProvider
    base_url: str
    model: str
    has_api_key: bool
    temperature: float
    timeout_seconds: int
    enabled: bool
    is_default: bool
    created_at: datetime
    updated_at: datetime


class ModelProfileTestView(StrictModel):
    profile_id: UUID
    ok: bool
    message: str
    model: str
    latency_ms: int | None
    checked_at: datetime
