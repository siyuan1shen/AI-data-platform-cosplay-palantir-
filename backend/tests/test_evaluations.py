from __future__ import annotations

import json
import time
from pathlib import Path

import pytest
from fastapi.testclient import TestClient
from sqlalchemy.exc import IntegrityError

from enterprise_insight_backend.app import create_app
from enterprise_insight_backend.config import Settings
from enterprise_insight_backend.models import ActionInvocationRow, EvaluationExecutionRow
from enterprise_insight_backend.schemas import ActionInvocationStatus


def test_persistent_golden_set_scores_and_retains_agent_regressions(
    client: TestClient,
) -> None:
    profile = client.post(
        "/api/v3/model-profiles",
        json={
            "name": "评测用固定模型",
            "provider": "MOCK",
            "base_url": "mock://evaluation",
            "model": "deterministic",
        },
    ).json()
    company = client.post("/api/v3/companies", json={"name": "Agent评测公司"}).json()
    project = client.post(
        f"/api/v3/companies/{company['id']}/projects", json={"name": "管理Agent回归"}
    ).json()
    project_id = project["id"]
    suite_response = client.post(
        f"/api/v3/projects/{project_id}/evaluations/suites",
        json={
            "name": "管理判断事实边界",
            "agent_kind": "MANAGEMENT",
            "description": "避免把因果假设直接表述为事实。",
        },
    )
    assert suite_response.status_code == 201, suite_response.text
    suite = suite_response.json()
    case_response = client.post(
        f"/api/v3/projects/{project_id}/evaluations/suites/{suite['id']}/cases",
        json={
            "name": "信息不足时请求证据",
            "input": "补充信息：为什么交付越来越慢？",
            "expected_action_keys": ["save_information_request"],
            "required_terms": ["信息不足"],
            "forbidden_terms": ["已经证明"],
            "minimum_citations": 0,
        },
    )
    assert case_response.status_code == 201, case_response.text
    case = case_response.json()

    blocked = client.post(
        f"/api/v3/projects/{project_id}/evaluations/suites/{suite['id']}/runs",
        json={
            "predictions": [
                {
                    "case_id": case["id"],
                    "content": "需要验证审批等待时间。",
                    "action_keys": ["save_information_request"],
                    "citation_count": 1,
                }
            ]
        },
    )
    assert blocked.status_code == 409

    activated = client.patch(
        f"/api/v3/projects/{project_id}/evaluations/suites/{suite['id']}",
        json={"status": "ACTIVE", "expected_revision": suite["revision"]},
    )
    assert activated.status_code == 200, activated.text

    thread = client.post(
        f"/api/v3/projects/{project_id}/agent-threads",
        json={"agent_kind": "MANAGEMENT"},
    ).json()
    accepted = client.post(
        f"/api/v3/projects/{project_id}/agent-threads/{thread['id']}/messages",
        json={"content": case["input"], "model_profile_id": profile["id"]},
    ).json()
    actual_agent_run = client.post(
        f"/api/v3/projects/{project_id}/agent-runs/{accepted['run']['id']}/execute"
    )
    assert actual_agent_run.status_code == 200, actual_agent_run.text
    assert actual_agent_run.json()["status"] == "COMPLETED"

    passing = client.post(
        f"/api/v3/projects/{project_id}/evaluations/suites/{suite['id']}/runs",
        json={
            "label": "基线模型",
            "predictions": [
                {
                    "case_id": case["id"],
                    "agent_run_id": actual_agent_run.json()["id"],
                }
            ],
        },
    )
    assert passing.status_code == 201, passing.text
    passing_result = client.get(
        f"/api/v3/projects/{project_id}/evaluations/runs/{passing.json()['id']}/results"
    ).json()["items"][0]
    passing_checks = passing_result["checks"]
    assert passing.json()["passed_cases"] == 1, {
        "failed": [item for item in passing_checks if not item["passed"]],
        "content": passing_result["candidate_content"],
    }
    assert passing.json()["average_score"] == 1

    regressed = client.post(
        f"/api/v3/projects/{project_id}/evaluations/suites/{suite['id']}/runs",
        json={
            "label": "候选模型",
            "predictions": [
                {
                    "case_id": case["id"],
                    "content": "信息不足，需要继续验证。",
                    "action_keys": ["save_information_request"],
                    "citation_count": 999,
                }
            ],
        },
    )
    assert regressed.status_code == 201, regressed.text
    assert regressed.json()["passed_cases"] == 0
    assert regressed.json()["average_score"] < 1

    runs = client.get(f"/api/v3/projects/{project_id}/evaluations/runs").json()
    assert runs["total"] == 2
    results = client.get(
        f"/api/v3/projects/{project_id}/evaluations/runs/{regressed.json()['id']}/results"
    ).json()
    assert results["total"] == 1
    assert results["items"][0]["passed"] is False
    failed_checks = {
        item["key"] for item in results["items"][0]["checks"] if not item["passed"]
    }
    assert {"verified_agent_run", "expected_actions"}.issubset(failed_checks)

    export = client.post(
        f"/api/v3/projects/{project_id}/exports",
        json={"format": "json", "include_evidence": False, "include_lineage": False},
    )
    assert export.status_code == 201, export.text
    export_payload = export.json()
    assert export_payload["status"] == "COMPLETED", export_payload.get("error")
    assert export_payload["download_url"]
    bundle = json.loads(client.get(export_payload["download_url"]).content.decode("utf-8"))
    assert bundle["evaluations"]["suites"][0]["name"] == "管理判断事实边界"
    assert len(bundle["evaluations"]["runs"]) == 2
    assert len(bundle["evaluations"]["results"]) == 2


def test_unfinished_agent_action_cannot_satisfy_expected_action_key(
    client: TestClient,
) -> None:
    profile = client.post(
        "/api/v3/model-profiles",
        json={
            "name": "动作状态评测模型",
            "provider": "MOCK",
            "base_url": "mock://evaluation-action-status",
            "model": "deterministic",
        },
    ).json()
    company = client.post("/api/v3/companies", json={"name": "动作状态评测企业"}).json()
    project = client.post(
        f"/api/v3/companies/{company['id']}/projects", json={"name": "动作状态评测项目"}
    ).json()
    project_id = project["id"]
    suite = client.post(
        f"/api/v3/projects/{project_id}/evaluations/suites",
        json={"name": "动作最终状态", "agent_kind": "MANAGEMENT"},
    ).json()
    case = client.post(
        f"/api/v3/projects/{project_id}/evaluations/suites/{suite['id']}/cases",
        json={
            "name": "未完成动作不能满分",
            "input": "请分析当前经营问题。",
            "expected_action_keys": ["create_entity"],
            "required_terms": ["信息不足"],
        },
    ).json()
    client.patch(
        f"/api/v3/projects/{project_id}/evaluations/suites/{suite['id']}",
        json={"status": "ACTIVE", "expected_revision": suite["revision"]},
    )
    thread = client.post(
        f"/api/v3/projects/{project_id}/agent-threads",
        json={"agent_kind": "MANAGEMENT"},
    ).json()
    accepted = client.post(
        f"/api/v3/projects/{project_id}/agent-threads/{thread['id']}/messages",
        json={"content": case["input"], "model_profile_id": profile["id"]},
    ).json()
    executed = client.post(
        f"/api/v3/projects/{project_id}/agent-runs/{accepted['run']['id']}/execute"
    )
    assert executed.status_code == 200, executed.text
    run = executed.json()
    definition = next(
        item
        for item in client.get(f"/api/v3/projects/{project_id}/action-definitions").json()["items"]
        if item["key"] == "create_entity"
    )
    invocation = client.post(
        f"/api/v3/projects/{project_id}/action-invocations",
        json={
            "action_definition_id": definition["id"],
            "source_agent_run_id": run["id"],
            "idempotency_key": "evaluation-unfinished-action",
            "requested_by": "evaluation",
            "input": {"type_key": "role", "stable_key": "role.pending", "name": "待审批岗位"},
        },
    )
    assert invocation.status_code == 201, invocation.text
    preview = client.post(
        f"/api/v3/projects/{project_id}/action-invocations/{invocation.json()['id']}/dry-run"
    )
    assert preview.status_code == 200, preview.text
    assert preview.json()["status"] == ActionInvocationStatus.WAITING_APPROVAL.value

    evaluation = client.post(
        f"/api/v3/projects/{project_id}/evaluations/suites/{suite['id']}/runs",
        json={
            "predictions": [
                {
                    "case_id": case["id"],
                    "agent_run_id": run["id"],
                    "action_keys": ["create_entity"],
                    "citation_count": 999,
                }
            ]
        },
    )
    assert evaluation.status_code == 201, evaluation.text
    result = client.get(
        f"/api/v3/projects/{project_id}/evaluations/runs/{evaluation.json()['id']}/results"
    ).json()["items"][0]
    assert result["candidate_action_keys"] == []
    assert result["passed"] is False
    assert any(
        item["key"] == "expected_actions" and item["passed"] is False
        for item in result["checks"]
    )
    assert result["candidate_citation_count"] == 0


def test_failed_agent_action_cannot_satisfy_expected_action_key(
    client: TestClient,
) -> None:
    profile = client.post(
        "/api/v3/model-profiles",
        json={
            "name": "失败动作评测模型",
            "provider": "MOCK",
            "base_url": "mock://evaluation-failed-action",
            "model": "deterministic",
        },
    ).json()
    company = client.post("/api/v3/companies", json={"name": "失败动作评测企业"}).json()
    project = client.post(
        f"/api/v3/companies/{company['id']}/projects", json={"name": "失败动作评测项目"}
    ).json()
    project_id = project["id"]
    suite = client.post(
        f"/api/v3/projects/{project_id}/evaluations/suites",
        json={"name": "动作失败状态", "agent_kind": "MANAGEMENT"},
    ).json()
    case = client.post(
        f"/api/v3/projects/{project_id}/evaluations/suites/{suite['id']}/cases",
        json={
            "name": "失败动作不能满分",
            "input": "请分析当前经营问题。",
            "expected_action_keys": ["create_entity"],
            "required_terms": ["信息不足"],
        },
    ).json()
    activated = client.patch(
        f"/api/v3/projects/{project_id}/evaluations/suites/{suite['id']}",
        json={"status": "ACTIVE", "expected_revision": suite["revision"]},
    )
    assert activated.status_code == 200, activated.text

    thread = client.post(
        f"/api/v3/projects/{project_id}/agent-threads",
        json={"agent_kind": "MANAGEMENT"},
    ).json()
    accepted = client.post(
        f"/api/v3/projects/{project_id}/agent-threads/{thread['id']}/messages",
        json={"content": case["input"], "model_profile_id": profile["id"]},
    ).json()
    executed = client.post(
        f"/api/v3/projects/{project_id}/agent-runs/{accepted['run']['id']}/execute"
    )
    assert executed.status_code == 200, executed.text
    run = executed.json()
    definition = next(
        item
        for item in client.get(f"/api/v3/projects/{project_id}/action-definitions").json()["items"]
        if item["key"] == "create_entity"
    )
    invocation = client.post(
        f"/api/v3/projects/{project_id}/action-invocations",
        json={
            "action_definition_id": definition["id"],
            "source_agent_run_id": run["id"],
            "idempotency_key": "evaluation-failed-action",
            "requested_by": "evaluation",
            "input": {"type_key": "role", "stable_key": "role.failed", "name": "失败岗位"},
        },
    )
    assert invocation.status_code == 201, invocation.text
    with client.app.state.database.session_factory.begin() as session:
        action = session.get(ActionInvocationRow, invocation.json()["id"])
        assert action is not None
        action.status = ActionInvocationStatus.FAILED.value
        action.error = {"code": "TEST_ACTION_FAILED", "message": "测试失败"}

    evaluation = client.post(
        f"/api/v3/projects/{project_id}/evaluations/suites/{suite['id']}/runs",
        json={
            "predictions": [
                {
                    "case_id": case["id"],
                    "agent_run_id": run["id"],
                    "action_keys": ["create_entity"],
                    "citation_count": 999,
                }
            ]
        },
    )
    assert evaluation.status_code == 201, evaluation.text
    result = client.get(
        f"/api/v3/projects/{project_id}/evaluations/runs/{evaluation.json()['id']}/results"
    ).json()["items"][0]
    assert result["candidate_action_keys"] == []
    assert result["passed"] is False
    assert any(
        item["key"] == "expected_actions" and item["passed"] is False
        for item in result["checks"]
    )
    assert result["candidate_citation_count"] == 0


def test_evaluation_execute_runs_active_cases_through_persisted_agent_runtime(
    client: TestClient,
) -> None:
    profile = client.post(
        "/api/v3/model-profiles",
        json={
            "name": "自动评测模拟模型",
            "provider": "MOCK",
            "base_url": "mock://evaluation-execute",
            "model": "deterministic",
            "is_default": True,
        },
    ).json()
    company = client.post("/api/v3/companies", json={"name": "自动评测企业"}).json()
    project = client.post(
        f"/api/v3/companies/{company['id']}/projects", json={"name": "自动评测项目"}
    ).json()
    project_id = project["id"]
    suite = client.post(
        f"/api/v3/projects/{project_id}/evaluations/suites",
        json={"name": "真实运行评测", "agent_kind": "MANAGEMENT"},
    ).json()
    case = client.post(
        f"/api/v3/projects/{project_id}/evaluations/suites/{suite['id']}/cases",
        json={
            "name": "自动生成补充请求",
            "input": "补充信息：为什么客户交付变慢？",
            "expected_action_keys": ["save_information_request"],
            "required_terms": ["信息不足"],
            "forbidden_terms": ["已经证明"],
        },
    ).json()
    activated = client.patch(
        f"/api/v3/projects/{project_id}/evaluations/suites/{suite['id']}",
        json={"status": "ACTIVE", "expected_revision": suite["revision"]},
    )
    assert activated.status_code == 200, activated.text

    executed = client.post(
        f"/api/v3/projects/{project_id}/evaluations/suites/{suite['id']}/execute",
        json={"label": "自动真实轨迹", "model_profile_id": profile["id"]},
    )
    assert executed.status_code == 201, executed.text
    run = executed.json()
    assert run["status"] == "COMPLETED"
    assert run["total_cases"] == 1
    assert run["passed_cases"] == 1
    result = client.get(
        f"/api/v3/projects/{project_id}/evaluations/runs/{run['id']}/results"
    )
    assert result.status_code == 200, result.text
    item = result.json()["items"][0]
    assert item["candidate_action_keys"] == ["save_information_request"]
    assert item["candidate_citation_count"] == 0
    assert item["passed"] is True
    assert case["input"] == "补充信息：为什么客户交付变慢？"


def test_evaluation_execute_rejects_existing_async_execution_with_contract_error(
    client: TestClient,
) -> None:
    profile = client.post(
        "/api/v3/model-profiles",
        json={
            "name": "重复执行保护模型",
            "provider": "MOCK",
            "base_url": "mock://evaluation-duplicate",
            "model": "deterministic",
        },
    ).json()
    company = client.post("/api/v3/companies", json={"name": "重复执行企业"}).json()
    project = client.post(
        f"/api/v3/companies/{company['id']}/projects", json={"name": "重复执行项目"}
    ).json()
    project_id = project["id"]
    suite = client.post(
        f"/api/v3/projects/{project_id}/evaluations/suites",
        json={"name": "重复执行保护评测", "agent_kind": "MANAGEMENT"},
    ).json()
    client.post(
        f"/api/v3/projects/{project_id}/evaluations/suites/{suite['id']}/cases",
        json={"name": "保护案例", "input": "请分析当前经营问题。"},
    )
    activated = client.patch(
        f"/api/v3/projects/{project_id}/evaluations/suites/{suite['id']}",
        json={"status": "ACTIVE", "expected_revision": suite["revision"]},
    )
    assert activated.status_code == 200, activated.text

    with client.app.state.database.session_factory() as session:
        session.add(
            EvaluationExecutionRow(
                project_id=project_id,
                suite_id=suite["id"],
                model_profile_id=profile["id"],
                label="已在执行",
                status="RUNNING",
                total_cases=1,
                completed_cases=0,
                agent_run_ids=[],
            )
        )
        session.commit()

    response = client.post(
        f"/api/v3/projects/{project_id}/evaluations/suites/{suite['id']}/execute",
        json={"label": "重复提交", "model_profile_id": profile["id"]},
    )
    assert response.status_code == 409, response.text
    body = response.json()["error"]
    assert body["code"] == "EVALUATION_ALREADY_RUNNING"
    assert body["details"][0]["status"] == "RUNNING"
    assert "evaluation_run_id" not in response.json()


def test_database_allows_only_one_active_execution_per_suite(
    client: TestClient,
) -> None:
    company = client.post("/api/v3/companies", json={"name": "评测并发约束企业"}).json()
    project = client.post(
        f"/api/v3/companies/{company['id']}/projects", json={"name": "评测并发约束项目"}
    ).json()
    suite = client.post(
        f"/api/v3/projects/{project['id']}/evaluations/suites",
        json={"name": "评测并发约束集", "agent_kind": "MANAGEMENT"},
    ).json()
    database = client.app.state.database

    def execution(status: str) -> EvaluationExecutionRow:
        return EvaluationExecutionRow(
            project_id=project["id"],
            suite_id=suite["id"],
            status=status,
            total_cases=0,
            completed_cases=0,
            agent_run_ids=[],
        )

    with database.session_factory.begin() as session:
        session.add(execution("RUNNING"))

    with pytest.raises(IntegrityError):
        with database.session_factory.begin() as session:
            session.add(execution("QUEUED"))
            session.flush()

    # Terminal executions do not occupy the active slot and remain auditable.
    with database.session_factory.begin() as session:
        session.add(execution("COMPLETED"))


def test_evaluation_async_execution_is_reconciled_by_worker(
    tmp_path: Path,
) -> None:
    settings = Settings(
        data_dir=tmp_path,
        database_url=f"sqlite:///{(tmp_path / 'worker-evaluation.db').as_posix()}",
        agent_worker_enabled=True,
        agent_worker_poll_seconds=0.1,
    )
    with TestClient(create_app(settings)) as worker_client:
        profile = worker_client.post(
            "/api/v3/model-profiles",
            json={
                "name": "异步评测模拟模型",
                "provider": "MOCK",
                "base_url": "mock://evaluation-async",
                "model": "deterministic",
                "is_default": True,
            },
        ).json()
        company = worker_client.post(
            "/api/v3/companies", json={"name": "异步评测企业"}
        ).json()
        project = worker_client.post(
            f"/api/v3/companies/{company['id']}/projects", json={"name": "异步评测项目"}
        ).json()
        project_id = project["id"]
        suite = worker_client.post(
            f"/api/v3/projects/{project_id}/evaluations/suites",
            json={"name": "后台真实运行评测", "agent_kind": "MANAGEMENT"},
        ).json()
        worker_client.post(
            f"/api/v3/projects/{project_id}/evaluations/suites/{suite['id']}/cases",
            json={
                "name": "后台生成补充请求",
                "input": "补充信息：为什么客户交付变慢？",
                "expected_action_keys": ["save_information_request"],
                "required_terms": ["信息不足"],
            },
        )
        activated = worker_client.patch(
            f"/api/v3/projects/{project_id}/evaluations/suites/{suite['id']}",
            json={"status": "ACTIVE", "expected_revision": suite["revision"]},
        )
        assert activated.status_code == 200, activated.text

        scheduled = worker_client.post(
            f"/api/v3/projects/{project_id}/evaluations/suites/{suite['id']}/execute-async",
            json={"label": "后台异步轨迹", "model_profile_id": profile["id"]},
        )
        assert scheduled.status_code == 202, scheduled.text
        execution = scheduled.json()
        assert execution["status"] in {"QUEUED", "RUNNING", "COMPLETED"}
        current_execution = worker_client.get(
            f"/api/v3/projects/{project_id}/evaluations/executions/{execution['id']}"
        ).json()
        duplicate = worker_client.post(
            f"/api/v3/projects/{project_id}/evaluations/suites/{suite['id']}/execute-async",
            json={"label": "重复点击不应创建第二个任务", "model_profile_id": profile["id"]},
        )
        assert duplicate.status_code == 202, duplicate.text
        if duplicate.json()["id"] != execution["id"]:
            # A completed run is a legitimate new evaluation request. If the
            # first request was still active at the exact duplicate boundary,
            # the API returns the same execution; a race that finishes between
            # the two requests may legitimately create the next run.
            assert current_execution["status"] in {
                "COMPLETED",
                "FAILED",
                "QUEUED",
                "RUNNING",
                "FINALIZING",
            }

        deadline = time.monotonic() + 10
        while time.monotonic() < deadline:
            execution = worker_client.get(
                f"/api/v3/projects/{project_id}/evaluations/executions/{execution['id']}"
            ).json()
            if execution["status"] in {"COMPLETED", "FAILED"}:
                break
            time.sleep(0.1)
        assert execution["status"] == "COMPLETED", execution
        assert execution["evaluation_run_id"]
        assert execution["completed_cases"] == 1
        runs = worker_client.get(
            f"/api/v3/projects/{project_id}/evaluations/runs",
            params={"suite_id": suite["id"]},
        )
        assert runs.status_code == 200, runs.text
        assert runs.json()["total"] == 1
        assert runs.json()["items"][0]["id"] == execution["evaluation_run_id"]
        results = worker_client.get(
            f"/api/v3/projects/{project_id}/evaluations/runs/{execution['evaluation_run_id']}/results"
        )
        assert results.status_code == 200, results.text
        assert results.json()["total"] == 1
        assert results.json()["items"][0]["passed"] is True
