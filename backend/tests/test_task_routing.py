"""Typed boundary tests for the pure deterministic task-routing rules."""

from __future__ import annotations

from dataclasses import replace

import pytest
from pydantic import BaseModel, ConfigDict

from enterprise_insight_backend.task_routing import (
    CandidateTool,
    ModelAccess,
    RouteDecision,
    RuleOutcome,
    SourceStatus,
    TaskRoute,
    TaskSpec,
    route_task,
)


def _simple_spec(**overrides: object) -> TaskSpec:
    """Return a fully evidenced query that is eligible for SIMPLE routing."""

    spec = TaskSpec(
        goal="查询指定订单的已验证金额",
        targets=("order:001",),
        target_resolution={"order:001": True},
        metric_ids=("metric:order_amount",),
        registered_metric_ids=("metric:order_amount",),
        candidate_tools=(
            CandidateTool(
                key="formal.query.order_amount.v1",
                registered_template=True,
                exact_match=True,
                parameters_complete=True,
            ),
        ),
        published_model_available=True,
        required_source_status={"erpnext:SalesOrder": SourceStatus.VALIDATED},
        required_source_coverage={"erpnext:SalesOrder": True},
    )
    return replace(spec, **overrides)


def _hit(decision: RouteDecision, rule_id: str) -> RuleOutcome:
    return next(hit.outcome for hit in decision.rule_hits if hit.rule_id == rule_id)


def test_explicit_published_validated_single_template_routes_simple() -> None:
    decision = route_task(_simple_spec())

    assert decision.route is TaskRoute.SIMPLE
    assert decision.missing_items == ()
    assert decision.underlying_complexity is False
    assert decision.execution_authorized is False
    assert decision.confirmation_policy_is_separate is True
    assert all(hit.description for hit in decision.rule_hits)
    assert _hit(decision, "R_TARGETS") is RuleOutcome.MATCHED
    assert _hit(decision, "R_SOURCE_STATUS") is RuleOutcome.MATCHED
    assert _hit(decision, "R_TEMPLATE") is RuleOutcome.MATCHED


def test_company_scope_summary_routes_simple_without_fake_entity_target() -> None:
    decision = route_task(
        TaskSpec(
            goal="查看公司整体有多少部门和关系",
            company_id="company-001",
            project_id="project-001",
            company_scope_read=True,
            candidate_tools=(
                CandidateTool(
                    key="read_enterprise_summary",
                    registered_template=True,
                    exact_match=True,
                    parameters_complete=True,
                ),
            ),
            published_model_available=True,
            requires_formal_data=False,
        )
    )

    assert decision.route is TaskRoute.SIMPLE
    assert decision.missing_items == ()
    assert _hit(decision, "R_TARGETS") is RuleOutcome.MATCHED


@pytest.mark.parametrize(
    ("changes", "expected_missing"),
    [
        ({"targets": (), "target_resolution": {}}, "明确的目标对象"),
        ({"target_resolution": {"order:001": False}}, "目标对象身份：order:001"),
        ({"published_model_available": False}, "已发布企业模型"),
        ({"required_source_status": {"erpnext:SalesOrder": "UNKNOWN"}}, "已验证的正式数据来源"),
        ({"required_source_coverage": {"erpnext:SalesOrder": False}}, "完整的数据覆盖"),
        ({"conflicts": ("订单金额不一致",)}, "待处理的数据冲突"),
        ({"metric_ids": ("metric:unknown",)}, "已注册指标：metric:unknown"),
        ({"candidate_tools": ()}, "唯一精确匹配的已注册模板"),
        (
            {
                "candidate_tools": (
                    CandidateTool(
                        key="formal.query.order_amount.v1",
                        registered_template=True,
                        exact_match=True,
                    ),
                )
            },
            "完整且无歧义的模板参数",
        ),
    ],
)
def test_each_simple_admission_failure_routes_needs_input(
    changes: dict[str, object], expected_missing: str
) -> None:
    decision = route_task(_simple_spec(**changes))

    assert decision.route is TaskRoute.NEEDS_INPUT
    assert any(expected_missing in item for item in decision.missing_items)
    assert "INPUT:" in decision.boundaries[-1]


@pytest.mark.parametrize(
    "changes",
    [
        {"time_range_required": True},
        {"ambiguities": ("金额含税口径不明",)},
        {"unknown_external_results": ("ERPNext 写入响应未知",)},
    ],
)
def test_missing_time_ambiguity_and_unknown_external_result_block_simple(
    changes: dict[str, object],
) -> None:
    decision = route_task(_simple_spec(**changes))

    assert decision.route is TaskRoute.NEEDS_INPUT
    assert decision.missing_items


@pytest.mark.parametrize(
    ("field", "reason"),
    [
        ("explicit_exploration", "explicit_exploration"),
        ("requires_causal_explanation", "causal_explanation"),
        ("requires_tradeoff", "tradeoff"),
        ("requires_unstructured_cross_store", "unstructured_cross_store"),
    ],
)
def test_declared_exploration_cause_tradeoff_or_cross_store_routes_complex(
    field: str, reason: str
) -> None:
    changes: dict[str, object] = {field: True}
    generic_tool = CandidateTool(
        key="context.search",
        registered_template=True,
        exact_match=False,
        parameters_complete=False,
    )
    decision = route_task(_simple_spec(candidate_tools=(generic_tool,), **changes))

    assert decision.route is TaskRoute.COMPLEX
    assert decision.underlying_complexity is True
    assert reason in decision.explanation
    assert _hit(decision, "R_TEMPLATE") is RuleOutcome.NOT_APPLICABLE
    assert "COMPLEX:" in decision.boundaries[-1]


def test_complex_request_keeps_priority_over_missing_input_and_is_not_downgraded() -> None:
    spec = _simple_spec(
        explicit_exploration=True,
        ambiguities=("两个部门对流程归属描述相反",),
    )
    first = route_task(spec)
    second = route_task(spec)

    assert first.route is TaskRoute.NEEDS_INPUT
    assert first.underlying_complexity is True
    assert "不会降级为简单任务" in first.explanation
    assert "待澄清：两个部门对流程归属描述相反" in first.missing_items
    assert first.explanation == second.explanation
    assert first.rule_hits == second.rule_hits


def test_complex_conflicts_are_retained_for_investigation_not_marked_resolved() -> None:
    tool = CandidateTool("context.search", registered_template=True)
    decision = route_task(
        _simple_spec(
            explicit_exploration=True,
            conflicts=("台账与订单金额不一致",),
            candidate_tools=(tool,),
        )
    )

    assert decision.route is TaskRoute.COMPLEX
    assert _hit(decision, "R_CONFLICTS") is RuleOutcome.DEFERRED_TO_COMPLEX
    assert "不证明因果或结论正确" in decision.boundaries[-1]


def test_complex_task_without_registered_investigation_tool_needs_input() -> None:
    decision = route_task(
        _simple_spec(explicit_exploration=True, candidate_tools=())
    )

    assert decision.route is TaskRoute.NEEDS_INPUT
    assert decision.underlying_complexity is True
    assert "至少一个匹配任务的已注册调查工具" in decision.missing_items


def test_clear_human_observation_is_simple_action_not_fact_validation_or_approval() -> None:
    decision = route_task(
        TaskSpec(
            goal="将管理者提供的会议观察原文存档",
            requested_effects=("observation.ingest",),
            candidate_tools=(
                CandidateTool(
                    key="observation.ingest.v1",
                    registered_template=True,
                    exact_match=True,
                    parameters_complete=True,
                    effect="observation.ingest",
                ),
            ),
            human_entered_observation=True,
            published_model_available=False,
        )
    )

    assert decision.route is TaskRoute.SIMPLE_ACTION
    assert _hit(decision, "R_MODEL") is RuleOutcome.NOT_APPLICABLE
    assert _hit(decision, "R_SOURCE_STATUS") is RuleOutcome.NOT_APPLICABLE
    assert decision.execution_authorized is False
    assert decision.confirmation_policy_is_separate is True
    assert "不是用户批准" in decision.boundaries[-1]


def test_unknown_external_action_result_requires_input() -> None:
    decision = route_task(
        _simple_spec(
            requested_effects=("erpnext.task.create",),
            unknown_external_results=("invocation-17 status unknown",),
            candidate_tools=(
                CandidateTool(
                    key="erpnext.task.create.v1",
                    registered_template=True,
                    exact_match=True,
                    parameters_complete=True,
                    effect="erpnext.task.create",
                ),
            ),
        )
    )

    assert decision.route is TaskRoute.NEEDS_INPUT
    assert "未知的外部操作结果：invocation-17 status unknown" in decision.missing_items


def test_action_template_for_a_different_effect_is_not_accepted() -> None:
    decision = route_task(
        _simple_spec(
            requested_effects=("erpnext.task.create",),
            candidate_tools=(
                CandidateTool(
                    key="erpnext.task.cancel.v1",
                    registered_template=True,
                    exact_match=True,
                    parameters_complete=True,
                    effect="erpnext.task.cancel",
                ),
            ),
        )
    )

    assert decision.route is TaskRoute.NEEDS_INPUT
    assert "与请求效果精确对应的已注册动作模板" in decision.missing_items
    assert _hit(decision, "R_EFFECT_MATCH") is RuleOutcome.BLOCKED


def test_multiple_exact_templates_are_ambiguous_and_not_selected_arbitrarily() -> None:
    tools = (
        CandidateTool("formal.query.v1", True, True, True),
        CandidateTool("formal.query.v2", True, True, True),
    )
    decision = route_task(_simple_spec(candidate_tools=tools))

    assert decision.route is TaskRoute.NEEDS_INPUT
    assert "唯一精确匹配的已注册模板（存在多个候选）" in decision.missing_items
    assert _hit(decision, "R_TEMPLATE") is RuleOutcome.BLOCKED


def test_unstructured_pydantic_task_spec_is_accepted_without_llm_or_mutation() -> None:
    class ToolModel(BaseModel):
        model_config = ConfigDict(frozen=True)

        key: str
        registered_template: bool
        exact_match: bool
        parameters_complete: bool

    class TaskModel(BaseModel):
        model_config = ConfigDict(frozen=True)

        goal: str
        targets: tuple[str, ...]
        target_resolution: dict[str, bool]
        candidate_tools: tuple[ToolModel, ...]
        published_model_available: bool
        required_source_status: dict[str, str]
        required_source_coverage: dict[str, bool]

    pydantic_task = TaskModel(
        goal="读取指定订单金额",
        targets=("order:001",),
        target_resolution={"order:001": True},
        candidate_tools=(
            ToolModel(
                key="formal.query.order_amount.v1",
                registered_template=True,
                exact_match=True,
                parameters_complete=True,
            ),
        ),
        published_model_available=True,
        required_source_status={"erpnext:SalesOrder": "VALIDATED"},
        required_source_coverage={"erpnext:SalesOrder": True},
    )
    original = pydantic_task.model_dump(mode="python")

    decision = route_task(pydantic_task)

    assert decision.route is TaskRoute.SIMPLE
    assert pydantic_task.model_dump(mode="python") == original


def test_route_explanation_is_stable_across_mapping_insertion_order() -> None:
    first = _simple_spec(
        required_source_status={"erp:b": "VALIDATED", "erp:a": "VALIDATED"},
        required_source_coverage={"erp:b": True, "erp:a": True},
    )
    second = _simple_spec(
        required_source_status={"erp:a": "VALIDATED", "erp:b": "VALIDATED"},
        required_source_coverage={"erp:a": True, "erp:b": True},
    )

    first_decision = route_task(first)
    second_decision = route_task(second)

    assert first_decision.explanation == second_decision.explanation
    assert first_decision.rule_hits == second_decision.rule_hits
    assert first_decision.missing_items == second_decision.missing_items
    assert tuple(hit.rule_id for hit in first_decision.rule_hits) == tuple(
        hit.rule_id for hit in second_decision.rule_hits
    )


def test_free_text_goal_does_not_itself_trigger_simple_routing() -> None:
    decision = route_task(TaskSpec(goal="查一下今年营收"))

    assert decision.route is TaskRoute.NEEDS_INPUT
    assert "明确的目标对象" in decision.missing_items
    assert "所需正式数据来源状态" in decision.missing_items
    assert "唯一精确匹配的已注册模板" in decision.missing_items


def test_real_only_is_the_default_even_for_complex_tasks() -> None:
    decision = route_task(
        _simple_spec(
            explicit_exploration=True,
            candidate_tools=(CandidateTool("context.search", registered_template=True),),
        )
    )

    assert decision.route is TaskRoute.COMPLEX
    assert decision.model_access is ModelAccess.REAL_ONLY
    assert decision.requested_virtual_scope == ()
    assert decision.virtual_revision_refs == ()


def test_virtual_work_requires_explicit_reason_gap_scope_real_anchor_and_revision() -> None:
    decision = route_task(
        _simple_spec(
            company_id="company-1",
            project_id="project-1",
            model_release_id="release-4",
            virtual_detail_requested=True,
            virtual_reason="核对车间交接为何反复等待",
            requested_virtual_scope=("position:planner", "activity:handoff"),
            real_gap=("formal model contains no observed handoff sequence",),
            virtual_anchor_resolution={
                "position:planner": True,
                "activity:handoff": True,
            },
            virtual_scope_available=True,
            virtual_revision_refs=("virtual-revision:12",),
            candidate_tools=(CandidateTool("context.search", registered_template=True),),
        )
    )

    assert decision.route is TaskRoute.COMPLEX
    assert decision.model_access is ModelAccess.REAL_PLUS_VIRTUAL
    assert decision.virtual_reason == "核对车间交接为何反复等待"
    assert decision.requested_virtual_scope == ("position:planner", "activity:handoff")
    assert decision.real_gap == ("formal model contains no observed handoff sequence",)
    assert decision.virtual_revision_refs == ("virtual-revision:12",)
    assert _hit(decision, "R_VIRTUAL_MODEL") is RuleOutcome.MATCHED


@pytest.mark.parametrize(
    "changes",
    [
        {"virtual_reason": ""},
        {"real_gap": ()},
        {"company_id": None},
        {"project_id": None},
        {"model_release_id": None},
        {"virtual_anchor_resolution": {"position:planner": False}},
        {"virtual_scope_available": False},
        {"virtual_revision_refs": ()},
    ],
)
def test_incomplete_virtual_work_request_fails_closed_without_refs(
    changes: dict[str, object],
) -> None:
    ready: dict[str, object] = {
        "company_id": "company-1",
        "project_id": "project-1",
        "model_release_id": "release-4",
        "virtual_detail_requested": True,
        "virtual_reason": "核对岗位交接情况",
        "requested_virtual_scope": ("position:planner",),
        "real_gap": ("formal model lacks observed work",),
        "virtual_anchor_resolution": {"position:planner": True},
        "virtual_scope_available": True,
        "virtual_revision_refs": ("virtual-revision:12",),
        "candidate_tools": (CandidateTool("context.search", registered_template=True),),
    }
    ready.update(changes)
    decision = route_task(_simple_spec(**ready))

    assert decision.route is TaskRoute.NEEDS_INPUT
    assert decision.model_access is ModelAccess.REAL_ONLY
    assert decision.requested_virtual_scope == ()
    assert decision.virtual_revision_refs == ()
    assert "可核验的岗位虚模范围、实模缺口、锚点和版本" in decision.missing_items
    assert _hit(decision, "R_VIRTUAL_MODEL") is RuleOutcome.BLOCKED


def test_virtual_request_does_not_enable_read_without_company_and_project_scope() -> None:
    decision = route_task(
        _simple_spec(
            model_release_id="release-4",
            virtual_detail_requested=True,
            virtual_reason="查看特定岗位的现场工作",
            requested_virtual_scope=("position:planner",),
            real_gap=("no formal fieldwork details",),
            virtual_anchor_resolution={"position:planner": True},
            virtual_scope_available=True,
            virtual_revision_refs=("virtual-revision:12",),
            candidate_tools=(CandidateTool("context.search", registered_template=True),),
        )
    )

    assert decision.route is TaskRoute.NEEDS_INPUT
    assert decision.model_access is ModelAccess.REAL_ONLY
    virtual_hit = next(
        hit for hit in decision.rule_hits if hit.rule_id == "R_VIRTUAL_MODEL"
    )
    assert "company/project scope missing" in virtual_hit.evidence


def test_virtual_request_with_other_missing_inputs_does_not_authorize_data_access() -> None:
    decision = route_task(
        _simple_spec(
            company_id="company-1",
            project_id="project-1",
            model_release_id="release-4",
            virtual_detail_requested=True,
            virtual_reason="比较现场操作与正式流程",
            requested_virtual_scope=("position:planner",),
            real_gap=("formal model omits current handoff detail",),
            virtual_anchor_resolution={"position:planner": True},
            virtual_scope_available=True,
            virtual_revision_refs=("virtual-revision:12",),
            ambiguities=("比较期间尚未说明",),
            candidate_tools=(CandidateTool("context.search", registered_template=True),),
        )
    )

    assert decision.route is TaskRoute.NEEDS_INPUT
    assert decision.model_access is ModelAccess.REAL_ONLY
    assert decision.requested_virtual_scope == ()
    assert decision.virtual_revision_refs == ()
