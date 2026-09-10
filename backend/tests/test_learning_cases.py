from __future__ import annotations

from uuid import UUID

from fastapi.testclient import TestClient

from enterprise_insight_backend.agent_runtime import AgentRuntimeService


def _project(client: TestClient, company_name: str, project_name: str) -> str:
    company = client.post(
        "/api/v3/companies", json={"name": company_name, "industry": "制造业"}
    ).json()
    return client.post(
        f"/api/v3/companies/{company['id']}/projects", json={"name": project_name}
    ).json()["id"]


def test_cross_company_case_catalog_exposes_only_opt_in_anonymous_summary(
    client: TestClient,
) -> None:
    source_project = _project(client, "不可公开公司名", "来源项目")
    target_project = _project(client, "目标企业", "目标项目")
    reusable = client.post(
        f"/api/v3/projects/{source_project}/learning-cases",
        json={
            "title": "不可公开的内部项目名",
            "industry": "制造业",
            "organization_scale": "500-1000人",
            "challenge": "不可公开的客户和组织细节",
            "context": "不可公开的流程细节",
            "intervention": "调整跨部门审批责任",
            "outcome": "周期降低18%",
            "lessons": ["先统一决策权再优化系统"],
            "tags": ["审批", "权责"],
            "status": "CONFIRMED",
            "reusable": True,
            "reusable_summary": "某中型制造企业通过统一审批权责缩短了交付周期。",
        },
    )
    assert reusable.status_code == 201, reusable.text
    reusable_id = reusable.json()["id"]

    catalog = client.get("/api/v3/learning-cases/catalog", params={"industry": "制造业"})
    assert catalog.status_code == 200
    serialized = catalog.text
    assert "某中型制造企业" in serialized
    assert "不可公开" not in serialized
    assert set(catalog.json()["items"][0]) == {
        "id",
        "industry",
        "organization_scale",
        "reusable_summary",
        "tags",
        "updated_at",
    }

    profile = client.post(
        "/api/v3/model-profiles",
        json={
            "name": "案例测试模型",
            "provider": "MOCK",
            "base_url": "mock://local",
            "model": "deterministic",
        },
    ).json()
    thread = client.post(
        f"/api/v3/projects/{target_project}/agent-threads",
        json={"agent_kind": "MANAGEMENT"},
    ).json()
    accepted = client.post(
        f"/api/v3/projects/{target_project}/agent-threads/{thread['id']}/messages",
        json={
            "content": "参考所选案例分析当前企业。",
            "model_profile_id": profile["id"],
            "reference_case_ids": [reusable_id],
        },
    )
    assert accepted.status_code == 202, accepted.text
    run_id = accepted.json()["run"]["id"]
    run = client.post(f"/api/v3/projects/{target_project}/agent-runs/{run_id}/execute")
    assert run.status_code == 200
    assert run.json()["context_manifest"]["context_counts"]["reference_cases"] == 1


def test_unconfirmed_or_non_reusable_cross_project_case_cannot_be_referenced(
    client: TestClient,
) -> None:
    source_project = _project(client, "来源企业", "来源")
    target_project = _project(client, "另一企业", "目标")
    private_case = client.post(
        f"/api/v3/projects/{source_project}/learning-cases",
        json={
            "title": "仅内部使用",
            "challenge": "内部问题",
            "status": "CONFIRMED",
        },
    ).json()
    thread = client.post(
        f"/api/v3/projects/{target_project}/agent-threads",
        json={"agent_kind": "MANAGEMENT"},
    ).json()
    rejected = client.post(
        f"/api/v3/projects/{target_project}/agent-threads/{thread['id']}/messages",
        json={"content": "引用案例", "reference_case_ids": [private_case["id"]]},
    )
    assert rejected.status_code == 422
    assert rejected.json()["error"]["code"] == "LEARNING_CASE_REFERENCE_INVALID"

    missing_summary = client.post(
        f"/api/v3/projects/{source_project}/learning-cases",
        json={"title": "错误案例", "challenge": "问题", "reusable": True},
    )
    assert missing_summary.status_code == 422
    assert missing_summary.json()["error"]["code"] == ("LEARNING_CASE_REUSABLE_SUMMARY_REQUIRED")


def test_action_observation_becomes_a_traceable_case_draft(client: TestClient) -> None:
    project_id = _project(client, "行动学习企业", "行动复盘")
    client.post(f"/api/v3/projects/{project_id}/ontology/default-pack").raise_for_status()
    definitions = client.get(f"/api/v3/projects/{project_id}/action-definitions").json()[
        "items"
    ]
    create_entity = next(item for item in definitions if item["key"] == "create_entity")
    invocation = client.post(
        f"/api/v3/projects/{project_id}/action-invocations",
        json={
            "action_definition_id": create_entity["id"],
            "input": {"type_key": "role", "name": "客户成功负责人"},
        },
    ).json()
    invocation_id = invocation["id"]
    client.post(
        f"/api/v3/projects/{project_id}/action-invocations/{invocation_id}/dry-run"
    ).raise_for_status()
    client.post(
        f"/api/v3/projects/{project_id}/action-invocations/{invocation_id}/approve",
        json={"approved_by": "management"},
    ).raise_for_status()
    client.post(
        f"/api/v3/projects/{project_id}/action-invocations/{invocation_id}/execute"
    ).raise_for_status()
    observation = client.post(
        f"/api/v3/projects/{project_id}/action-invocations/{invocation_id}/observations",
        json={
            "observation_kind": "QUALITATIVE",
            "metric_key": "customer_feedback",
            "observed_value": "职责明确后升级事项减少",
            "outcome": "EFFECTIVE",
            "note": "管理层月度复盘确认",
        },
    )
    assert observation.status_code == 201, observation.text

    thread = client.post(
        f"/api/v3/projects/{project_id}/agent-threads",
        json={"agent_kind": "MANAGEMENT"},
    ).json()
    with client.app.state.database.session_factory() as session:
        context = AgentRuntimeService(session, client.app.state.settings)._build_context(
            UUID(project_id), thread["id"], []
        )
    assert context["counts"]["action_invocations"] == 1
    assert context["counts"]["action_observations"] == 1
    assert context["action_invocations"][0]["id"] == invocation_id
    assert context["action_invocations"][0]["observation_ids"] == [
        observation.json()["id"]
    ]
    assert context["action_observations"][0]["id"] == observation.json()["id"]
    assert any(item["key"] == "draft_learning_case" for item in context["available_actions"])

    drafted = client.post(
        f"/api/v3/projects/{project_id}/learning-cases/from-action",
        json={
            "action_invocation_id": invocation_id,
            "action_observation_ids": [observation.json()["id"]],
            "title": "客户成功职责调整复盘",
            "challenge": "升级事项责任不清",
            "lessons": ["先明确唯一责任人，再观察升级频率"],
        },
    )
    assert drafted.status_code == 201, drafted.text
    payload = drafted.json()
    assert payload["status"] == "DRAFT"
    assert payload["origin_kind"] == "ACTION_RESULT"
    assert payload["source_action_invocation_id"] == invocation_id
    assert payload["source_action_observation_ids"] == [observation.json()["id"]]
    assert "管理层月度复盘确认" in payload["outcome"]

    other_project = _project(client, "另一企业", "错误引用")
    rejected = client.post(
        f"/api/v3/projects/{other_project}/learning-cases/from-action",
        json={
            "action_invocation_id": invocation_id,
            "action_observation_ids": [observation.json()["id"]],
            "title": "越界案例",
            "challenge": "不能跨项目引用行动结果",
        },
    )
    assert rejected.status_code == 404
    assert rejected.json()["error"]["code"] == "LEARNING_CASE_ACTION_NOT_FOUND"


def test_scenario_without_results_becomes_an_explicit_reference_draft(
    client: TestClient,
) -> None:
    project_id = _project(client, "方案学习企业", "架构方案")
    scenario = client.post(
        f"/api/v3/projects/{project_id}/scenarios",
        json={
            "name": "共享服务中心方案",
            "goal": "降低重复职能",
            "assumptions": ["三个事业部流程可标准化"],
            "expected_benefits": ["减少重复岗位"],
            "risks": ["响应业务的速度下降"],
            "validation_metrics": ["需求响应时长"],
            "overlay_operations": [],
        },
    )
    assert scenario.status_code == 201, scenario.text

    drafted = client.post(
        f"/api/v3/projects/{project_id}/learning-cases/from-scenario",
        json={
            "scenario_id": scenario.json()["id"],
            "title": "共享服务中心待验证方案",
            "challenge": "重复职能增加协调成本",
            "lessons": ["先验证标准化假设"],
        },
    )
    assert drafted.status_code == 201, drafted.text
    payload = drafted.json()
    assert payload["status"] == "DRAFT"
    assert payload["origin_kind"] == "SCENARIO_REFERENCE"
    assert payload["source_scenario_id"] == scenario.json()["id"]
    assert payload["source_action_invocation_id"] is None
    assert payload["outcome"] is None
    assert "共享服务中心方案" in payload["intervention"]

    other_project = _project(client, "另一方案企业", "不能越界")
    rejected = client.post(
        f"/api/v3/projects/{other_project}/learning-cases/from-scenario",
        json={
            "scenario_id": scenario.json()["id"],
            "title": "越界方案",
            "challenge": "不能跨项目引用方案",
        },
    )
    assert rejected.status_code == 404
    assert rejected.json()["error"]["code"] == "LEARNING_CASE_SCENARIO_NOT_FOUND"
