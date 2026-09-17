from __future__ import annotations

from dataclasses import dataclass
from typing import Any

from pydantic import BaseModel

from enterprise_insight_backend.schemas import (
    AgentKind,
    CausalHypothesisCreate,
    ChangeSetCreate,
    DesignTradeoffCreate,
    EnterpriseSummaryReadAction,
    EntityCreate,
    GraphNeighborhoodReadAction,
    HypothesisCreate,
    InformationRequestCreate,
    LearningCaseDraftFromActionCreate,
    LearningCaseDraftFromScenarioCreate,
    ManagementAnalysisRequest,
    ManagementObservationSearchAction,
    MaterialFragmentsReadAction,
    MeetingRecordCreate,
    ObservationConflictResolveAction,
    PotentialRecordsSearchAction,
    RawBatchMaterializeAction,
    RelationCreate,
    ScenarioCreate,
    ScenarioSimulationAction,
    SemanticDatasetCreate,
    SemanticDatasetQueryAction,
    SemanticMappingAdvanceAction,
    SemanticMappingCreate,
    SemanticMappingSuggestionsReadAction,
    SourceIdentityBindAction,
    SourceObservationsReadAction,
    WorkObservationCompareAction,
    WorkObservationReadAction,
)


@dataclass(frozen=True, slots=True)
class ToolSpec:
    key: str
    agents: frozenset[str]
    input_model: type[BaseModel] | None
    extra_input_keys: frozenset[str] = frozenset()
    handler_name: str = ""
    supports_compensation: bool = False

    @property
    def input_keys(self) -> set[str]:
        model_keys = set(self.input_model.model_fields) if self.input_model is not None else set()
        return model_keys | set(self.extra_input_keys)

    def json_schema(self) -> dict[str, Any]:
        if self.input_model is None:
            return {
                "type": "object",
                "properties": {key: {} for key in sorted(self.extra_input_keys)},
                "additionalProperties": False,
            }
        return self.input_model.model_json_schema()


PROJECTION = frozenset({AgentKind.PROJECTION.value})
MANAGEMENT = frozenset({AgentKind.MANAGEMENT.value})
SYSTEM = frozenset({AgentKind.SYSTEM_ONTOLOGY.value})
CONTEXT_READERS = frozenset({
    AgentKind.PROJECTION.value,
    AgentKind.MANAGEMENT.value,
})

TOOL_SPECS: dict[str, ToolSpec] = {
    spec.key: spec
    for spec in (
        ToolSpec(
            "create_entity", PROJECTION, EntityCreate, handler_name="create_entity",
            supports_compensation=True,
        ),
        ToolSpec(
            "create_relation", PROJECTION, RelationCreate, handler_name="create_relation",
            supports_compensation=True,
        ),
        ToolSpec(
            "apply_change_set",
            PROJECTION,
            None,
            frozenset({"change_set_id"}),
            "apply_change_set",
        ),
        ToolSpec(
            "apply_projection_changes",
            PROJECTION,
            ChangeSetCreate,
            handler_name="apply_projection_changes",
        ),
        ToolSpec("save_hypothesis", MANAGEMENT, HypothesisCreate, handler_name="save_hypothesis"),
        ToolSpec("create_scenario", MANAGEMENT, ScenarioCreate, handler_name="create_scenario"),
        ToolSpec(
            "run_scenario_simulation",
            MANAGEMENT,
            ScenarioSimulationAction,
            handler_name="run_scenario_simulation",
        ),
        ToolSpec(
            "save_causal_hypothesis",
            MANAGEMENT,
            CausalHypothesisCreate,
            handler_name="save_causal_hypothesis",
        ),
        ToolSpec(
            "save_information_request",
            MANAGEMENT,
            InformationRequestCreate,
            handler_name="save_information_request",
        ),
        ToolSpec(
            "record_design_tradeoff",
            MANAGEMENT,
            DesignTradeoffCreate,
            handler_name="record_design_tradeoff",
        ),
        ToolSpec(
            "record_meeting_observation",
            MANAGEMENT,
            MeetingRecordCreate,
            handler_name="record_meeting_observation",
        ),
        ToolSpec(
            "run_management_analysis",
            MANAGEMENT,
            ManagementAnalysisRequest,
            handler_name="run_management_analysis",
        ),
        ToolSpec(
            "draft_learning_case",
            MANAGEMENT,
            LearningCaseDraftFromActionCreate,
            handler_name="draft_learning_case",
        ),
        ToolSpec(
            "draft_scenario_learning_case",
            MANAGEMENT,
            LearningCaseDraftFromScenarioCreate,
            handler_name="draft_scenario_learning_case",
        ),
        ToolSpec(
            "create_semantic_mapping",
            SYSTEM,
            SemanticMappingCreate,
            handler_name="create_semantic_mapping",
        ),
        ToolSpec(
            "advance_semantic_mapping",
            SYSTEM,
            SemanticMappingAdvanceAction,
            handler_name="advance_semantic_mapping",
        ),
        ToolSpec(
            "bind_source_identity",
            SYSTEM,
            SourceIdentityBindAction,
            handler_name="bind_source_identity",
        ),
        ToolSpec(
            "resolve_observation_conflict",
            SYSTEM,
            ObservationConflictResolveAction,
            handler_name="resolve_observation_conflict",
        ),
        ToolSpec(
            "materialize_raw_batch",
            SYSTEM,
            RawBatchMaterializeAction,
            handler_name="materialize_raw_batch",
        ),
        ToolSpec(
            "create_semantic_dataset",
            SYSTEM,
            SemanticDatasetCreate,
            handler_name="create_semantic_dataset",
        ),
        ToolSpec(
            "query_semantic_dataset",
            SYSTEM,
            SemanticDatasetQueryAction,
            handler_name="query_semantic_dataset",
        ),
        ToolSpec(
            "suggest_semantic_mappings",
            SYSTEM,
            SemanticMappingSuggestionsReadAction,
            handler_name="suggest_semantic_mappings",
        ),
        ToolSpec(
            "read_material_fragments",
            CONTEXT_READERS,
            MaterialFragmentsReadAction,
            handler_name="read_material_fragments",
        ),
        ToolSpec(
            "read_source_observations",
            CONTEXT_READERS,
            SourceObservationsReadAction,
            handler_name="read_source_observations",
        ),
        ToolSpec(
            "read_enterprise_summary",
            CONTEXT_READERS,
            EnterpriseSummaryReadAction,
            handler_name="read_enterprise_summary",
        ),
        ToolSpec(
            "read_graph_neighborhood",
            CONTEXT_READERS,
            GraphNeighborhoodReadAction,
            handler_name="read_graph_neighborhood",
        ),
        ToolSpec(
            "search_management_observations",
            CONTEXT_READERS,
            ManagementObservationSearchAction,
            handler_name="search_management_observations",
        ),
        ToolSpec(
            "search_potential_records",
            CONTEXT_READERS,
            PotentialRecordsSearchAction,
            handler_name="search_potential_records",
        ),
        ToolSpec(
            "work_observation.read",
            CONTEXT_READERS,
            WorkObservationReadAction,
            handler_name="work_observation_read",
        ),
        ToolSpec(
            "work_observation.compare",
            CONTEXT_READERS,
            WorkObservationCompareAction,
            handler_name="work_observation_compare",
        ),
    )
}


def get_tool_spec(key: str) -> ToolSpec | None:
    return TOOL_SPECS.get(key)


def tool_keys_for_agent(agent_kind: str) -> set[str]:
    return {spec.key for spec in TOOL_SPECS.values() if agent_kind in spec.agents}


def validate_tool_registry(default_action_keys: set[str]) -> list[str]:
    """Return deterministic configuration errors without touching the database."""
    errors: list[str] = []
    registry_keys = set(TOOL_SPECS)
    for key in sorted(default_action_keys - registry_keys):
        errors.append(f"default action '{key}' has no tool specification")
    for key in sorted(registry_keys - default_action_keys):
        errors.append(f"tool specification '{key}' has no default action definition")
    for key, spec in sorted(TOOL_SPECS.items()):
        if not spec.handler_name:
            errors.append(f"tool specification '{key}' has no handler")
        if not spec.agents:
            errors.append(f"tool specification '{key}' is not assigned to an agent")
    return errors
