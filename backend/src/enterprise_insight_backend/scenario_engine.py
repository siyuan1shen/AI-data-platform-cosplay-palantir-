from __future__ import annotations

from typing import Any
from uuid import UUID, uuid4

from sqlalchemy import select
from sqlalchemy.orm import Session

from enterprise_insight_backend.errors import DomainError
from enterprise_insight_backend.models import ProjectRow, ScenarioRow, ScenarioRunRow
from enterprise_insight_backend.schemas import (
    ScenarioCaseInput,
    ScenarioRunCompareRequest,
    ScenarioRunComparisonView,
    ScenarioRunView,
    ScenarioSimulationRequest,
)
from enterprise_insight_backend.service_utils import json_ready


def calculate_flow(case: ScenarioCaseInput) -> dict[str, Any]:
    """Run a small typed flow-balance model without eval or model-generated code.

    ``STORABLE_GOODS`` carries produced inventory between periods.  For
    ``NON_STORABLE_SERVICE`` unused capacity expires at the end of each period;
    otherwise the engine would incorrectly turn idle service capacity into
    inventory for a later day.
    """
    inventory = case.initial_inventory
    backlog = case.initial_backlog
    periods: list[dict[str, Any]] = []
    for period, demand, capacity in zip(case.periods, case.demand, case.capacity, strict=True):
        required = demand + backlog
        available = capacity + inventory if case.flow_mode == "STORABLE_GOODS" else capacity
        completed = min(required, available)
        backlog = required - completed
        inventory = available - completed if case.flow_mode == "STORABLE_GOODS" else 0.0
        periods.append(
            {
                "period": period,
                "demand": demand,
                "capacity": capacity,
                "required": required,
                "completed": completed,
                "ending_inventory": inventory,
                "ending_backlog": backlog,
                "unit": case.unit,
                "flow_mode": case.flow_mode,
            }
        )
    return {
        "key": case.key,
        "label": case.label,
        "unit": case.unit,
        "period_keys": list(case.periods),
        "periods": periods,
        "totals": {
            "demand": sum(item["demand"] for item in periods),
            "capacity": sum(item["capacity"] for item in periods),
            "completed": sum(item["completed"] for item in periods),
            "ending_inventory": inventory,
            "ending_backlog": backlog,
        },
        "flow_mode": case.flow_mode,
    }


class ScenarioRunService:
    def __init__(self, session: Session) -> None:
        self.session = session

    def run(
        self,
        project_id: UUID,
        scenario_id: UUID,
        payload: ScenarioSimulationRequest,
    ) -> ScenarioRunView:
        scenario = self.session.get(ScenarioRow, str(scenario_id))
        if scenario is None or scenario.project_id != str(project_id):
            raise DomainError("SCENARIO_NOT_FOUND", "情景不存在。", status_code=404)
        project = self.session.get(ProjectRow, str(project_id))
        if project is None:
            raise DomainError("PROJECT_NOT_FOUND", "企业投影不存在。", status_code=404)
        results: list[dict[str, Any]] = []
        errors: list[dict[str, Any]] = []
        units = {item.unit for item in payload.cases}
        if len(units) > 1:
            errors.append({"code": "UNIT_MISMATCH", "message": "比较方案的单位必须一致。"})
        if not errors:
            for case in payload.cases:
                results.append(calculate_flow(case))
        status = "SUCCEEDED" if not errors else "FAILED"
        row = ScenarioRunRow(
            id=str(uuid4()),
            project_id=str(project_id),
            scenario_id=str(scenario_id),
            scenario_revision=scenario.revision,
            baseline_revision=project.revision,
            status=status,
            input_snapshot=json_ready(payload.model_dump(mode="json")),
            rule_snapshot={
                "engine": "typed.flow_balance.v1",
                "formula": (
                    "required=demand+prior_backlog; "
                    "available=capacity+prior_inventory (storable) or capacity "
                    "(non-storable); completed=min(required,available)"
                ),
                "flow_modes": ["STORABLE_GOODS", "NON_STORABLE_SERVICE"],
                "limitations": [
                    "未定义利润、流失、压力或其他非输入指标。",
                    "服务能力不会跨周期结转为库存。",
                ],
            },
            result={"cases": results, "limitations": ["结果仅对输入的周期和条件成立。"]},
            errors=errors,
            created_by=payload.created_by,
        )
        self.session.add(row)
        self.session.flush()
        return ScenarioRunView.model_validate(row)

    def list(self, project_id: UUID, scenario_id: UUID) -> list[ScenarioRunView]:
        rows = self.session.scalars(
            select(ScenarioRunRow)
            .where(
                ScenarioRunRow.project_id == str(project_id),
                ScenarioRunRow.scenario_id == str(scenario_id),
            )
            .order_by(ScenarioRunRow.created_at.desc(), ScenarioRunRow.id)
        ).all()
        return [ScenarioRunView.model_validate(row) for row in rows]

    def compare(
        self, project_id: UUID, payload: ScenarioRunCompareRequest
    ) -> ScenarioRunComparisonView:
        left = self.session.get(ScenarioRunRow, str(payload.left_run_id))
        right = self.session.get(ScenarioRunRow, str(payload.right_run_id))
        if (
            left is None
            or right is None
            or left.project_id != str(project_id)
            or right.project_id != str(project_id)
        ):
            raise DomainError("SCENARIO_RUN_NOT_FOUND", "推演运行不存在。", status_code=404)
        if left.scenario_id != right.scenario_id:
            raise DomainError(
                "SCENARIO_RUN_DIFFERENT_SCENARIO",
                "只有同一情景的不同运行结果可以直接比较。",
                status_code=409,
            )
        if left.baseline_revision != right.baseline_revision:
            raise DomainError(
                "SCENARIO_RUN_BASELINE_MISMATCH",
                "两次运行基于不同企业修订，不能直接比较。",
                status_code=409,
            )
        if left.status != "SUCCEEDED" or right.status != "SUCCEEDED":
            raise DomainError(
                "SCENARIO_RUN_NOT_SUCCEEDED",
                "失败或部分运行结果不能作为可比基线。",
                status_code=409,
            )
        left_cases = {item["key"]: item for item in (left.result.get("cases") or [])}
        right_cases = {item["key"]: item for item in (right.result.get("cases") or [])}
        comparisons: list[dict[str, Any]] = []
        for key in sorted(set(left_cases) | set(right_cases)):
            left_case = left_cases.get(key)
            right_case = right_cases.get(key)
            if left_case is None or right_case is None:
                comparisons.append({"key": key, "status": "ONLY_ONE_RUN"})
                continue
            if left_case.get("unit") != right_case.get("unit"):
                raise DomainError(
                    "SCENARIO_RUN_UNIT_MISMATCH",
                    f"方案 {key} 的单位不同，不能直接比较。",
                    status_code=409,
                )
            if left_case.get("flow_mode") != right_case.get("flow_mode"):
                raise DomainError(
                    "SCENARIO_RUN_FLOW_MODE_MISMATCH",
                    f"方案 {key} 的流动类型不同，不能直接比较。",
                    status_code=409,
                )
            if left_case.get("period_keys") != right_case.get("period_keys"):
                raise DomainError(
                    "SCENARIO_RUN_PERIOD_MISMATCH",
                    f"方案 {key} 的周期不同，不能直接比较。",
                    status_code=409,
                )
            comparisons.append(
                {
                    "key": key,
                    "left_label": left_case.get("label"),
                    "right_label": right_case.get("label"),
                    "completed_delta": (
                        right_case["totals"]["completed"] - left_case["totals"]["completed"]
                    ),
                    "ending_backlog_delta": (
                        right_case["totals"]["ending_backlog"]
                        - left_case["totals"]["ending_backlog"]
                    ),
                    "ending_inventory_delta": (
                        right_case["totals"]["ending_inventory"]
                        - left_case["totals"]["ending_inventory"]
                    ),
                    "unit": left_case.get("unit"),
                }
            )
        return ScenarioRunComparisonView.model_validate(
            {
                "left_run_id": left.id,
                "right_run_id": right.id,
                "cases": comparisons,
                "limitations": [
                    "比较只反映两次运行的显式输入差异，不构成因果证明。",
                    "未定义的经营指标不会由系统推测。",
                ],
            }
        )
