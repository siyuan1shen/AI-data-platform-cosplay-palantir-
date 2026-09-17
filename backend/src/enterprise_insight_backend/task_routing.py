"""Deterministic task routing from structured, already-parsed task specifications.

This module deliberately does not interpret natural language, access application
state, call an LLM, execute tools, or grant action approval. Callers must supply
verified identifiers and source evidence; downstream services remain responsible
for authorization, company/project scope, execution, and confirmation policy.
"""

from __future__ import annotations

from collections.abc import Mapping, Sequence
from dataclasses import asdict, dataclass, field, is_dataclass
from enum import Enum, StrEnum

from pydantic import BaseModel, ConfigDict, Field


class TaskRoute(StrEnum):
    """The analysis mode selected by the pure routing rules."""

    SIMPLE = "SIMPLE"
    SIMPLE_ACTION = "SIMPLE_ACTION"
    COMPLEX = "COMPLEX"
    NEEDS_INPUT = "NEEDS_INPUT"


class ModelAccess(StrEnum):
    """Whether a route may read real enterprise data, virtual work detail, or both."""

    REAL_ONLY = "REAL_ONLY"
    REAL_PLUS_VIRTUAL = "REAL_PLUS_VIRTUAL"


class SourceStatus(StrEnum):
    """Minimum source-state vocabulary accepted by the deterministic gate."""

    VALIDATED = "VALIDATED"
    MISSING = "MISSING"
    UNKNOWN = "UNKNOWN"
    STALE = "STALE"
    UNVERIFIED = "UNVERIFIED"


class RuleOutcome(StrEnum):
    """Stable result for one auditable routing rule."""

    MATCHED = "MATCHED"
    BLOCKED = "BLOCKED"
    NOT_APPLICABLE = "NOT_APPLICABLE"
    DEFERRED_TO_COMPLEX = "DEFERRED_TO_COMPLEX"


class CandidateToolInput(BaseModel):
    """A model-parsed tool candidate; never a callable tool or permission grant."""

    model_config = ConfigDict(extra="forbid")

    key: str = Field(min_length=1, max_length=200)
    registered_template: bool = False
    exact_match: bool = False
    parameters_complete: bool = False
    matches_task: bool = True
    effect: str | None = Field(default=None, max_length=200)


class TaskRouteInput(BaseModel):
    """Structured task interpretation passed through deterministic route checks."""

    model_config = ConfigDict(extra="forbid")

    goal: str = Field(min_length=1, max_length=10_000)
    company_id: str | None = Field(default=None, max_length=200)
    project_id: str | None = Field(default=None, max_length=200)
    model_release_id: str | None = Field(default=None, max_length=200)
    intent: str | None = Field(default=None, max_length=200)
    targets: list[str] = Field(default_factory=list, max_length=100)
    target_resolution: dict[str, bool] = Field(default_factory=dict, max_length=100)
    time_range: str | None = Field(default=None, max_length=500)
    time_range_required: bool = False
    metric_ids: list[str] = Field(default_factory=list, max_length=100)
    registered_metric_ids: list[str] = Field(default_factory=list, max_length=100)
    requested_effects: list[str] = Field(default_factory=list, max_length=20)
    candidate_tools: list[CandidateToolInput] = Field(default_factory=list, max_length=100)
    ambiguities: list[str] = Field(default_factory=list, max_length=100)
    explicit_exploration: bool = False
    requires_causal_explanation: bool = False
    requires_tradeoff: bool = False
    requires_unstructured_cross_store: bool = False
    published_model_available: bool = False
    requires_published_model: bool = True
    requires_formal_data: bool = True
    required_source_status: dict[str, SourceStatus] = Field(default_factory=dict, max_length=100)
    required_source_coverage: dict[str, bool] = Field(default_factory=dict, max_length=100)
    conflicts: list[str] = Field(default_factory=list, max_length=100)
    unknown_external_results: list[str] = Field(default_factory=list, max_length=100)
    human_entered_observation: bool = False
    virtual_detail_requested: bool = False
    virtual_reason: str | None = Field(default=None, max_length=2000)
    requested_virtual_scope: list[str] = Field(default_factory=list, max_length=100)
    real_gap: list[str] = Field(default_factory=list, max_length=100)
    virtual_anchor_resolution: dict[str, bool] = Field(default_factory=dict, max_length=100)
    virtual_scope_available: bool = False
    virtual_revision_refs: list[str] = Field(default_factory=list, max_length=100)
    company_scope_read: bool = False
    source_record_query: bool = False


@dataclass(frozen=True, slots=True)
class CandidateTool:
    """A registry candidate after upstream matching, never a callable tool."""

    key: str
    registered_template: bool = False
    exact_match: bool = False
    parameters_complete: bool = False
    matches_task: bool = True
    effect: str | None = None


@dataclass(frozen=True, slots=True)
class TaskSpec:
    """Structured routing input; target keys are canonical IDs, not display names."""

    goal: str
    company_id: str | None = None
    project_id: str | None = None
    model_release_id: str | None = None
    intent: str | None = None
    targets: tuple[str, ...] = ()
    target_resolution: Mapping[str, bool] = field(default_factory=dict)
    time_range: str | None = None
    time_range_required: bool = False
    metric_ids: tuple[str, ...] = ()
    registered_metric_ids: tuple[str, ...] = ()
    requested_effects: tuple[str, ...] = ()
    candidate_tools: tuple[CandidateTool, ...] = ()
    ambiguities: tuple[str, ...] = ()
    explicit_exploration: bool = False
    requires_causal_explanation: bool = False
    requires_tradeoff: bool = False
    requires_unstructured_cross_store: bool = False
    published_model_available: bool = False
    requires_published_model: bool = True
    requires_formal_data: bool = True
    required_source_status: Mapping[str, SourceStatus | str] = field(default_factory=dict)
    required_source_coverage: Mapping[str, bool] = field(default_factory=dict)
    conflicts: tuple[str, ...] = ()
    unknown_external_results: tuple[str, ...] = ()
    human_entered_observation: bool = False
    virtual_detail_requested: bool = False
    virtual_reason: str | None = None
    requested_virtual_scope: tuple[str, ...] = ()
    real_gap: tuple[str, ...] = ()
    virtual_anchor_resolution: Mapping[str, bool] = field(default_factory=dict)
    virtual_scope_available: bool = False
    virtual_revision_refs: tuple[str, ...] = ()
    company_scope_read: bool = False
    source_record_query: bool = False


@dataclass(frozen=True, slots=True)
class RuleHit:
    """One stable rule result and the structured evidence used to reach it."""

    rule_id: str
    description: str
    outcome: RuleOutcome
    evidence: tuple[str, ...] = ()


@dataclass(frozen=True, slots=True)
class RouteDecision:
    """Auditable route result; it is explicitly not an execution authorization."""

    route: TaskRoute
    rule_hits: tuple[RuleHit, ...]
    missing_items: tuple[str, ...]
    boundaries: tuple[str, ...]
    explanation: str
    underlying_complexity: bool
    execution_authorized: bool = False
    confirmation_policy_is_separate: bool = True
    model_access: ModelAccess = ModelAccess.REAL_ONLY
    virtual_reason: str | None = None
    requested_virtual_scope: tuple[str, ...] = ()
    real_gap: tuple[str, ...] = ()
    virtual_revision_refs: tuple[str, ...] = ()


_BASE_BOUNDARIES = (
    "ROUTE_ONLY: 路由只选择处理模式，不授权执行、写入或外部操作。",
    "NO_LLM: 规则只检查结构化字段；不解析自然语言，也不保证上游语义解析正确。",
    "SCOPE: 公司/项目权限、租户边界及数据访问范围必须由下游服务另行校验。",
    "EVIDENCE: 对象解析、来源状态和模板注册状态由调用方提供；本模块不查询数据库或注册表。",
    "MODEL_ACCESS: 默认 REAL_ONLY；虚模读取必须核验原因、实模缺口、锚点及版本。",
)
_ROUTE_BOUNDARIES = {
    TaskRoute.SIMPLE: "SIMPLE: 仍须由下游按注册模板执行并核验结果。",
    TaskRoute.SIMPLE_ACTION: (
        "ACTION: 确认策略独立判定；本路由结果本身不是用户批准或执行许可。"
    ),
    TaskRoute.COMPLEX: "COMPLEX: 不证明因果或结论正确；须保留证据、反例和未覆盖项。",
    TaskRoute.NEEDS_INPUT: "INPUT: 补齐缺项并重新路由；不得静默降级为简单任务。",
}
_RULE_DESCRIPTIONS = {
    "R_GOAL": "目标已提供",
    "R_TARGETS": "目标对象身份明确",
    "R_TIME": "所需时间范围明确",
    "R_AMBIGUITIES": "不存在待澄清歧义",
    "R_MODEL": "所需已发布模型可用",
    "R_SOURCE_STATUS": "所需正式数据来源已验证",
    "R_SOURCE_COVERAGE": "所需数据覆盖完整",
    "R_METRICS": "所需指标已注册",
    "R_CONFLICTS": "没有未解决冲突，或冲突明确留给复杂调查",
    "R_EXTERNAL_RESULT": "不存在未知外部操作结果",
    "R_TEMPLATE": "存在唯一精确注册模板",
    "R_EFFECT_MATCH": "动作模板与请求效果精确对应",
    "R_PARAMETERS": "注册模板参数完整",
    "R_COMPLEX_INTENT": "任务包含显式复杂分析意图",
    "R_COMPLEX_TOOL": "复杂调查有可用的已注册工具候选",
    "R_HUMAN_OBSERVATION": "任务是明确的人类观察录入",
    "R_VIRTUAL_MODEL": "岗位虚模仅在明确需求、实模缺口和有效锚点/版本通过后启用",
}
_RULE_ORDER = tuple(_RULE_DESCRIPTIONS)


def route_task(task: TaskSpec | object) -> RouteDecision:
    """Route a dataclass/Pydantic-like TaskSpec using fixed, fail-closed rules.

    Hard missing/ambiguous inputs take precedence over complexity. A complex
    request therefore remains marked as complex when it needs input, instead of
    silently falling through to SIMPLE. A human observation may be classified as
    SIMPLE_ACTION without a published model or formal source data because saving
    an observation records a statement; it does not validate the statement.
    """

    spec = _coerce_task_spec(task)
    complex_reasons = _complex_reasons(spec)
    if spec.virtual_detail_requested:
        complex_reasons = (*complex_reasons, "virtual_work_detail")
    is_complex = bool(complex_reasons)
    is_action = bool(spec.requested_effects) or spec.human_entered_observation
    is_observation_action = spec.human_entered_observation
    virtual_scope = _stable_unique(spec.requested_virtual_scope)
    real_gap = _stable_unique(spec.real_gap)
    virtual_refs = _stable_unique(spec.virtual_revision_refs)
    anchor_ok = bool(virtual_scope) and all(
        spec.virtual_anchor_resolution.get(anchor) is True for anchor in virtual_scope
    )
    virtual_ready = (
        spec.virtual_detail_requested
        and bool((spec.virtual_reason or "").strip())
        and bool(real_gap)
        and bool(spec.company_id)
        and bool(spec.project_id)
        and bool(spec.model_release_id)
        and spec.published_model_available
        and spec.virtual_scope_available
        and anchor_ok
        and bool(virtual_refs)
    )
    missing: list[str] = []
    outcomes: dict[str, RuleHit] = {}

    _record(outcomes, "R_GOAL", bool(spec.goal.strip()), ("goal",))
    if not spec.goal.strip():
        missing.append("任务目标")

    if is_observation_action:
        _record(
            outcomes,
            "R_TARGETS",
            False,
            ("observation may be stored before object matching",),
            RuleOutcome.NOT_APPLICABLE,
        )
    else:
        target_ids = _stable_unique(spec.targets)
        resolved = tuple(
            target_id
            for target_id in target_ids
            if spec.target_resolution.get(target_id) is True
        )
        company_scope_ok = (
            spec.company_scope_read and bool(spec.company_id) and bool(spec.project_id)
        )
        source_query_ok = spec.source_record_query and bool(spec.goal.strip())
        target_ok = company_scope_ok or source_query_ok or (
            bool(target_ids) and len(resolved) == len(target_ids)
        )
        evidence = ("company scope",) if company_scope_ok else (
            ("source records",) if source_query_ok else (
                resolved if target_ok else tuple(sorted(set(target_ids).difference(resolved)))
            )
        )
        _record(outcomes, "R_TARGETS", target_ok, evidence or ("no target IDs",))
        if not target_ids and not company_scope_ok and not source_query_ok:
            missing.append("明确的目标对象")
        elif not target_ok:
            missing.extend(f"目标对象身份：{item}" for item in evidence)

    if spec.time_range_required:
        time_ok = bool(spec.time_range and spec.time_range.strip())
        _record(outcomes, "R_TIME", time_ok, (spec.time_range or "time_range missing",))
        if not time_ok:
            missing.append("所需时间范围")
    else:
        _record(
            outcomes,
            "R_TIME",
            False,
            ("not required",),
            RuleOutcome.NOT_APPLICABLE,
        )

    ambiguity_evidence = _stable_unique(spec.ambiguities)
    _record(outcomes, "R_AMBIGUITIES", not ambiguity_evidence, ambiguity_evidence)
    missing.extend(f"待澄清：{item}" for item in ambiguity_evidence)

    if is_observation_action or not spec.requires_published_model:
        _record(
            outcomes,
            "R_MODEL",
            False,
            ("published model not required",),
            RuleOutcome.NOT_APPLICABLE,
        )
    else:
        _record(
            outcomes,
            "R_MODEL",
            spec.published_model_available,
            ("published model available" if spec.published_model_available else "unavailable",),
        )
        if not spec.published_model_available:
            missing.append("可用的已发布企业模型")

    if not spec.virtual_detail_requested:
        _record(
            outcomes,
            "R_VIRTUAL_MODEL",
            False,
            ("default REAL_ONLY; no virtual-work request",),
            RuleOutcome.NOT_APPLICABLE,
        )
    elif virtual_ready:
        _record(
            outcomes,
            "R_VIRTUAL_MODEL",
            True,
            (*virtual_scope, *virtual_refs),
        )
    else:
        failed_checks = []
        if not (spec.virtual_reason or "").strip():
            failed_checks.append("missing virtual reason")
        if not real_gap:
            failed_checks.append("no verified real-model gap")
        if not spec.company_id or not spec.project_id:
            failed_checks.append("company/project scope missing")
        if not spec.model_release_id:
            failed_checks.append("real-model release ID missing")
        if not spec.published_model_available:
            failed_checks.append("published real-model release unavailable")
        if not virtual_scope:
            failed_checks.append("virtual scope missing")
        elif not anchor_ok:
            failed_checks.append("virtual scope anchor missing or unresolved")
        if not spec.virtual_scope_available:
            failed_checks.append("virtual work scope unavailable")
        if not virtual_refs:
            failed_checks.append("no validated virtual revision references")
        _record(outcomes, "R_VIRTUAL_MODEL", False, tuple(failed_checks))
        missing.append("可核验的岗位虚模范围、实模缺口、锚点和版本")

    if is_observation_action or not spec.requires_formal_data:
        _record(
            outcomes,
            "R_SOURCE_STATUS",
            False,
            ("formal data not required",),
            RuleOutcome.NOT_APPLICABLE,
        )
        _record(
            outcomes,
            "R_SOURCE_COVERAGE",
            False,
            ("formal data not required",),
            RuleOutcome.NOT_APPLICABLE,
        )
    else:
        source_missing, source_evidence = _check_source_status(spec.required_source_status)
        _record(outcomes, "R_SOURCE_STATUS", not source_missing, source_evidence)
        missing.extend(source_missing)

        coverage_missing, coverage_evidence = _check_coverage(
            spec.required_source_status, spec.required_source_coverage
        )
        _record(outcomes, "R_SOURCE_COVERAGE", not coverage_missing, coverage_evidence)
        missing.extend(coverage_missing)

    unregistered_metrics = tuple(
        sorted(set(spec.metric_ids).difference(spec.registered_metric_ids))
    )
    metric_ok = not unregistered_metrics
    metric_outcome = (
        RuleOutcome.NOT_APPLICABLE if not spec.metric_ids else None
    )
    _record(
        outcomes,
        "R_METRICS",
        metric_ok,
        unregistered_metrics or spec.metric_ids or ("no metrics requested",),
        metric_outcome,
    )
    missing.extend(f"已注册指标：{item}" for item in unregistered_metrics)

    conflict_items = _stable_unique(spec.conflicts)
    if not conflict_items:
        conflict_outcome = RuleOutcome.MATCHED
    elif is_complex:
        conflict_outcome = RuleOutcome.DEFERRED_TO_COMPLEX
    else:
        conflict_outcome = RuleOutcome.BLOCKED
        missing.extend(f"待处理的数据冲突：{item}" for item in conflict_items)
    _record(
        outcomes,
        "R_CONFLICTS",
        conflict_outcome is not RuleOutcome.BLOCKED,
        conflict_items,
        conflict_outcome,
    )

    unknown_result_items = _stable_unique(spec.unknown_external_results)
    _record(outcomes, "R_EXTERNAL_RESULT", not unknown_result_items, unknown_result_items)
    missing.extend(f"未知的外部操作结果：{item}" for item in unknown_result_items)

    matching_tools = tuple(tool for tool in spec.candidate_tools if tool.matches_task)
    exact_templates = tuple(
        tool
        for tool in matching_tools
        if tool.registered_template and tool.exact_match
    )
    if is_action:
        effect_templates = tuple(
            tool
            for tool in exact_templates
            if len(spec.requested_effects) == 1
            and tool.effect == spec.requested_effects[0]
        )
        effect_matches = len(effect_templates) > 0
        _record(
            outcomes,
            "R_EFFECT_MATCH",
            effect_matches,
            spec.requested_effects or ("no requested effect",),
        )
        if not effect_matches:
            missing.append("与请求效果精确对应的已注册动作模板")
        exact_templates = effect_templates
    else:
        _record(
            outcomes,
            "R_EFFECT_MATCH",
            False,
            ("not an action",),
            RuleOutcome.NOT_APPLICABLE,
        )
    template_required = not is_complex or is_action
    if not template_required:
        _record(
            outcomes,
            "R_TEMPLATE",
            False,
            ("complex analysis does not require one exact template",),
            RuleOutcome.NOT_APPLICABLE,
        )
        _record(
            outcomes,
            "R_PARAMETERS",
            False,
            ("complex analysis tool selection is downstream",),
            RuleOutcome.NOT_APPLICABLE,
        )
    else:
        unique_template = len(exact_templates) == 1
        template_evidence = tuple(tool.key for tool in exact_templates)
        _record(outcomes, "R_TEMPLATE", unique_template, template_evidence)
        if not exact_templates:
            missing.append("唯一精确匹配的已注册模板")
        elif len(exact_templates) > 1:
            missing.append("唯一精确匹配的已注册模板（存在多个候选）")

        parameters_complete = unique_template and exact_templates[0].parameters_complete
        _record(
            outcomes,
            "R_PARAMETERS",
            parameters_complete,
            (exact_templates[0].key,) if unique_template else (),
        )
        if unique_template and not parameters_complete:
            missing.append("完整且无歧义的模板参数")

    if is_complex:
        complex_tool_available = any(
            tool.registered_template for tool in matching_tools
        )
        _record(
            outcomes,
            "R_COMPLEX_TOOL",
            complex_tool_available,
            tuple(tool.key for tool in matching_tools if tool.registered_template),
        )
        if not complex_tool_available:
            missing.append("至少一个匹配任务的已注册调查工具")
    else:
        _record(
            outcomes,
            "R_COMPLEX_TOOL",
            False,
            ("not a complex task",),
            RuleOutcome.NOT_APPLICABLE,
        )

    _record(
        outcomes,
        "R_COMPLEX_INTENT",
        is_complex,
        complex_reasons,
        RuleOutcome.MATCHED if is_complex else RuleOutcome.NOT_APPLICABLE,
    )
    _record(
        outcomes,
        "R_HUMAN_OBSERVATION",
        is_observation_action,
        ("human-entered observation",) if is_observation_action else (),
        RuleOutcome.MATCHED if is_observation_action else RuleOutcome.NOT_APPLICABLE,
    )

    missing_items = tuple(dict.fromkeys(missing))
    if missing_items:
        route = TaskRoute.NEEDS_INPUT
    elif is_complex:
        route = TaskRoute.COMPLEX
    elif is_action:
        route = TaskRoute.SIMPLE_ACTION
    else:
        route = TaskRoute.SIMPLE

    ordered_hits = tuple(outcomes[rule_id] for rule_id in _RULE_ORDER)
    boundaries = _BASE_BOUNDARIES + (_ROUTE_BOUNDARIES[route],)
    explanation = _explanation(route, ordered_hits, missing_items, is_complex)
    virtual_access_approved = virtual_ready and route is TaskRoute.COMPLEX
    return RouteDecision(
        route=route,
        rule_hits=ordered_hits,
        missing_items=missing_items,
        boundaries=boundaries,
        explanation=explanation,
        underlying_complexity=is_complex,
        model_access=(
            ModelAccess.REAL_PLUS_VIRTUAL
            if virtual_access_approved
            else ModelAccess.REAL_ONLY
        ),
        virtual_reason=(
            spec.virtual_reason.strip()
            if virtual_access_approved and spec.virtual_reason
            else None
        ),
        requested_virtual_scope=virtual_scope if virtual_access_approved else (),
        real_gap=real_gap if virtual_access_approved else (),
        virtual_revision_refs=virtual_refs if virtual_access_approved else (),
    )


def _coerce_task_spec(value: object) -> TaskSpec:
    """Read a local dataclass, mapping, Pydantic model, or attribute object."""

    if isinstance(value, TaskSpec):
        return _normalize_spec(value)
    if isinstance(value, Mapping):
        data = value
    elif is_dataclass(value) and not isinstance(value, type):
        data = asdict(value)
    else:
        model_dump = getattr(value, "model_dump", None)
        legacy_dump = getattr(value, "dict", None)
        if callable(model_dump):
            data = model_dump(mode="python")
        elif callable(legacy_dump):
            data = legacy_dump()
        else:
            data = {
                name: getattr(value, name)
                for name in TaskSpec.__dataclass_fields__
                if hasattr(value, name)
            }
    if not isinstance(data, Mapping):
        data = {}
    tools = _coerce_tools(data.get("candidate_tools", ()))
    spec = TaskSpec(
        goal=_string(data.get("goal")),
        company_id=_optional_string(data.get("company_id")),
        project_id=_optional_string(data.get("project_id")),
        model_release_id=_optional_string(data.get("model_release_id")),
        intent=_optional_string(data.get("intent")),
        targets=_strings(data.get("targets", ())),
        target_resolution=_bool_mapping(data.get("target_resolution", {})),
        time_range=_optional_string(data.get("time_range")),
        time_range_required=_boolean(data.get("time_range_required"), False),
        metric_ids=_strings(data.get("metric_ids", ())),
        registered_metric_ids=_strings(data.get("registered_metric_ids", ())),
        requested_effects=_strings(data.get("requested_effects", ())),
        candidate_tools=tools,
        ambiguities=_strings(data.get("ambiguities", ())),
        explicit_exploration=_boolean(data.get("explicit_exploration"), False),
        requires_causal_explanation=_boolean(
            data.get("requires_causal_explanation"), False
        ),
        requires_tradeoff=_boolean(data.get("requires_tradeoff"), False),
        requires_unstructured_cross_store=_boolean(
            data.get("requires_unstructured_cross_store"), False
        ),
        published_model_available=_boolean(data.get("published_model_available"), False),
        requires_published_model=_boolean(data.get("requires_published_model"), True),
        requires_formal_data=_boolean(data.get("requires_formal_data"), True),
        required_source_status=_value_mapping(data.get("required_source_status", {})),
        required_source_coverage=_bool_mapping(data.get("required_source_coverage", {})),
        conflicts=_strings(data.get("conflicts", ())),
        unknown_external_results=_strings(data.get("unknown_external_results", ())),
        human_entered_observation=_boolean(
            data.get("human_entered_observation"), False
        ),
        virtual_detail_requested=_boolean(data.get("virtual_detail_requested"), False),
        virtual_reason=_optional_string(data.get("virtual_reason")),
        requested_virtual_scope=_strings(data.get("requested_virtual_scope", ())),
        real_gap=_strings(data.get("real_gap", ())),
        virtual_anchor_resolution=_bool_mapping(
            data.get("virtual_anchor_resolution", {})
        ),
        virtual_scope_available=_boolean(data.get("virtual_scope_available"), False),
        virtual_revision_refs=_strings(data.get("virtual_revision_refs", ())),
        company_scope_read=_boolean(data.get("company_scope_read"), False),
    )
    return _normalize_spec(spec)


def _normalize_spec(spec: TaskSpec) -> TaskSpec:
    """Normalize direct dataclass values identically to foreign model inputs."""

    return TaskSpec(
        goal=_string(spec.goal),
        company_id=_optional_string(spec.company_id),
        project_id=_optional_string(spec.project_id),
        model_release_id=_optional_string(spec.model_release_id),
        intent=_optional_string(spec.intent),
        targets=_strings(spec.targets),
        target_resolution=_bool_mapping(spec.target_resolution),
        time_range=_optional_string(spec.time_range),
        time_range_required=_boolean(spec.time_range_required, False),
        metric_ids=_strings(spec.metric_ids),
        registered_metric_ids=_strings(spec.registered_metric_ids),
        requested_effects=_strings(spec.requested_effects),
        candidate_tools=_coerce_tools(spec.candidate_tools),
        ambiguities=_strings(spec.ambiguities),
        explicit_exploration=_boolean(spec.explicit_exploration, False),
        requires_causal_explanation=_boolean(spec.requires_causal_explanation, False),
        requires_tradeoff=_boolean(spec.requires_tradeoff, False),
        requires_unstructured_cross_store=_boolean(
            spec.requires_unstructured_cross_store, False
        ),
        published_model_available=_boolean(spec.published_model_available, False),
        requires_published_model=_boolean(spec.requires_published_model, True),
        requires_formal_data=_boolean(spec.requires_formal_data, True),
        required_source_status=_value_mapping(spec.required_source_status),
        required_source_coverage=_bool_mapping(spec.required_source_coverage),
        conflicts=_strings(spec.conflicts),
        unknown_external_results=_strings(spec.unknown_external_results),
        human_entered_observation=_boolean(spec.human_entered_observation, False),
        virtual_detail_requested=_boolean(spec.virtual_detail_requested, False),
        virtual_reason=_optional_string(spec.virtual_reason),
        requested_virtual_scope=_strings(spec.requested_virtual_scope),
        real_gap=_strings(spec.real_gap),
        virtual_anchor_resolution=_bool_mapping(spec.virtual_anchor_resolution),
        virtual_scope_available=_boolean(spec.virtual_scope_available, False),
        virtual_revision_refs=_strings(spec.virtual_revision_refs),
        company_scope_read=_boolean(spec.company_scope_read, False),
    )


def _coerce_tools(value: object) -> tuple[CandidateTool, ...]:
    if not isinstance(value, Sequence) or isinstance(value, (str, bytes, bytearray)):
        return ()
    tools: list[CandidateTool] = []
    for index, item in enumerate(value):
        if isinstance(item, CandidateTool):
            raw: Mapping[str, object] = {
                "key": item.key,
                "registered_template": item.registered_template,
                "exact_match": item.exact_match,
                "parameters_complete": item.parameters_complete,
                "matches_task": item.matches_task,
                "effect": item.effect,
            }
        elif isinstance(item, Mapping):
            raw = item
        elif is_dataclass(item) and not isinstance(item, type):
            raw = asdict(item)
        else:
            dump = getattr(item, "model_dump", None)
            if callable(dump):
                dumped = dump(mode="python")
                raw = dumped if isinstance(dumped, Mapping) else {}
            elif isinstance(item, str):
                raw = {"key": item}
            else:
                raw = {
                    name: getattr(item, name)
                    for name in CandidateTool.__dataclass_fields__
                    if hasattr(item, name)
                }
        tools.append(
            CandidateTool(
                key=_string(raw.get("key")) or f"candidate[{index}]",
                registered_template=_boolean(raw.get("registered_template"), False),
                exact_match=_boolean(raw.get("exact_match"), False),
                parameters_complete=_boolean(raw.get("parameters_complete"), False),
                matches_task=_boolean(raw.get("matches_task"), True),
                effect=_optional_string(raw.get("effect")),
            )
        )
    return tuple(tools)


def _check_source_status(
    statuses: Mapping[str, object],
) -> tuple[list[str], tuple[str, ...]]:
    missing: list[str] = []
    evidence: list[str] = []
    if not statuses:
        return ["所需正式数据来源状态"], ("no required source status supplied",)
    for source_id in sorted(statuses):
        status = _enum_value(statuses[source_id])
        evidence.append(f"{source_id}={status or 'UNKNOWN'}")
        if status != SourceStatus.VALIDATED.value:
            missing.append(f"已验证的正式数据来源：{source_id}")
    return missing, tuple(evidence)


def _check_coverage(
    statuses: Mapping[str, object], coverage: Mapping[str, bool]
) -> tuple[list[str], tuple[str, ...]]:
    missing: list[str] = []
    evidence: list[str] = []
    if not statuses:
        return ["所需正式数据覆盖情况"], ("no required sources to verify coverage",)
    for source_id in sorted(statuses):
        complete = coverage.get(source_id) is True
        evidence.append(f"{source_id}={'complete' if complete else 'incomplete or unknown'}")
        if not complete:
            missing.append(f"完整的数据覆盖：{source_id}")
    return missing, tuple(evidence)


def _complex_reasons(spec: TaskSpec) -> tuple[str, ...]:
    reasons = []
    for enabled, reason in (
        (spec.explicit_exploration, "explicit_exploration"),
        (spec.requires_causal_explanation, "causal_explanation"),
        (spec.requires_tradeoff, "tradeoff"),
        (spec.requires_unstructured_cross_store, "unstructured_cross_store"),
    ):
        if enabled:
            reasons.append(reason)
    return tuple(reasons)


def _record(
    outcomes: dict[str, RuleHit],
    rule_id: str,
    matched: bool,
    evidence: tuple[str, ...],
    override: RuleOutcome | None = None,
) -> None:
    outcome = override or (RuleOutcome.MATCHED if matched else RuleOutcome.BLOCKED)
    outcomes[rule_id] = RuleHit(rule_id, _RULE_DESCRIPTIONS[rule_id], outcome, evidence)


def _explanation(
    route: TaskRoute,
    hits: tuple[RuleHit, ...],
    missing: tuple[str, ...],
    is_complex: bool,
) -> str:
    if route is TaskRoute.NEEDS_INPUT:
        prefix = "需要补充输入"
        if is_complex:
            prefix += "（该任务仍按复杂任务处理，不会降级为简单任务）"
        return f"{prefix}：{'；'.join(missing)}。"
    if route is TaskRoute.COMPLEX:
        reasons = next(
            (hit.evidence for hit in hits if hit.rule_id == "R_COMPLEX_INTENT"), ()
        )
        return f"判定为复杂任务：命中 {'、'.join(reasons)}；需按复杂调查流程处理。"
    if route is TaskRoute.SIMPLE_ACTION:
        return "判定为简单动作：输入与注册模板明确；确认策略及执行授权仍由独立流程决定。"
    return "判定为简单任务：对象、发布模型、正式数据和唯一注册模板均通过结构化校验。"


def _enum_value(value: object) -> str | None:
    raw = value.value if isinstance(value, Enum) else value
    return raw.strip().upper() if isinstance(raw, str) else None


def _stable_unique(values: tuple[str, ...]) -> tuple[str, ...]:
    return tuple(dict.fromkeys(item.strip() for item in values if item.strip()))


def _strings(value: object) -> tuple[str, ...]:
    if isinstance(value, str):
        return (value.strip(),) if value.strip() else ()
    if not isinstance(value, Sequence) or isinstance(value, (bytes, bytearray)):
        return ()
    return tuple(item.strip() for item in value if isinstance(item, str) and item.strip())


def _string(value: object) -> str:
    return value.strip() if isinstance(value, str) else ""


def _optional_string(value: object) -> str | None:
    result = _string(value)
    return result or None


def _boolean(value: object, default: bool) -> bool:
    return value if isinstance(value, bool) else default


def _bool_mapping(value: object) -> Mapping[str, bool]:
    if not isinstance(value, Mapping):
        return {}
    return {
        key: item for key, item in value.items() if isinstance(key, str) and isinstance(item, bool)
    }


def _value_mapping(value: object) -> Mapping[str, object]:
    if not isinstance(value, Mapping):
        return {}
    return {key: item for key, item in value.items() if isinstance(key, str)}
