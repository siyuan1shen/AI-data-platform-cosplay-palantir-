from __future__ import annotations

from math import nan
from uuid import UUID, uuid4

import pytest
from pydantic import ValidationError
from sqlalchemy import create_engine
from sqlalchemy.orm import Session

from enterprise_insight_backend.errors import DomainError
from enterprise_insight_backend.models import Base, CompanyRow, ProjectRow, ScenarioRow
from enterprise_insight_backend.scenario_engine import ScenarioRunService, calculate_flow
from enterprise_insight_backend.schemas import (
    ScenarioCaseInput,
    ScenarioRunCompareRequest,
    ScenarioSimulationRequest,
)


def _case(key: str, capacity: list[float]) -> ScenarioCaseInput:
    return ScenarioCaseInput(
        key=key,
        label=key,
        periods=["d1", "d2"],
        demand=[90, 90],
        capacity=capacity,
        unit="units",
    )


def test_flow_balance_acceptance_case_is_deterministic() -> None:
    result = calculate_flow(
        ScenarioCaseInput(
            key="impact",
            label="capacity reduced",
            periods=["d1", "d2", "d3", "d4", "d5"],
            demand=[90, 90, 90, 90, 90],
            capacity=[80, 80, 80, 80, 80],
            unit="units",
        )
    )
    assert result["totals"] == {
        "demand": 450.0,
        "capacity": 400.0,
        "completed": 400.0,
        "ending_inventory": 0.0,
        "ending_backlog": 50.0,
    }


def test_non_storable_service_capacity_does_not_become_inventory() -> None:
    result = calculate_flow(
        ScenarioCaseInput(
            key="service",
            label="不可储存服务能力",
            periods=["d1", "d2"],
            demand=[0, 100],
            capacity=[100, 0],
            flow_mode="NON_STORABLE_SERVICE",
            unit="requests",
        )
    )

    assert result["totals"]["completed"] == 0.0
    assert result["totals"]["ending_backlog"] == 100.0
    assert result["totals"]["ending_inventory"] == 0.0


def test_scenario_input_rejects_non_finite_and_ambiguous_periods() -> None:
    with pytest.raises(ValidationError, match="有限数字"):
        ScenarioCaseInput(
            key="invalid",
            label="invalid",
            periods=["d1"],
            demand=[nan],
            capacity=[1],
            unit="units",
        )

    with pytest.raises(ValidationError, match="周期标识必须唯一"):
        ScenarioCaseInput(
            key="invalid",
            label="invalid",
            periods=["d1", "d1"],
            demand=[1, 1],
            capacity=[1, 1],
            unit="units",
        )


def test_scenario_runs_are_immutable_and_compare_same_baseline() -> None:
    engine = create_engine("sqlite:///:memory:")
    Base.metadata.create_all(engine)
    with Session(engine) as session:
        company = CompanyRow(id=str(uuid4()), name="scenario-test")
        project = ProjectRow(id=str(uuid4()), company_id=company.id, name="projection")
        scenario = ScenarioRow(
            id=str(uuid4()),
            project_id=project.id,
            name="capacity",
            goal="compare",
            assumptions=[],
            overlay_operations=[],
            expected_benefits=[],
            risks=[],
            validation_metrics=[],
            base_revision=0,
            status="DRAFT",
            revision=1,
        )
        session.add_all([company, project, scenario])
        session.commit()

        service = ScenarioRunService(session)
        request = ScenarioSimulationRequest(cases=[_case("baseline", [100, 100])])
        left = service.run(UUID(project.id), UUID(scenario.id), request)
        right = service.run(
            UUID(project.id),
            UUID(scenario.id),
            ScenarioSimulationRequest(cases=[_case("baseline", [80, 80])]),
        )
        comparison = service.compare(
            project.id,
            ScenarioRunCompareRequest(left_run_id=left.id, right_run_id=right.id),
        )

        assert left.id != right.id
        assert comparison.cases[0]["completed_delta"] == -20
        assert session.query(ScenarioRow).one().revision == 1

        service_mode = service.run(
            UUID(project.id),
            UUID(scenario.id),
            ScenarioSimulationRequest(
                cases=[
                    ScenarioCaseInput(
                        key="baseline",
                        label="服务基线",
                        periods=["d1", "d2"],
                        demand=[90, 90],
                        capacity=[100, 100],
                        flow_mode="NON_STORABLE_SERVICE",
                        unit="units",
                    )
                ]
            ),
        )
        with pytest.raises(DomainError) as mode_mismatch:
            service.compare(
                project.id,
                ScenarioRunCompareRequest(left_run_id=left.id, right_run_id=service_mode.id),
            )
        assert mode_mismatch.value.code == "SCENARIO_RUN_FLOW_MODE_MISMATCH"
