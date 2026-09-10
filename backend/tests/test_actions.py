from __future__ import annotations

from concurrent.futures import ThreadPoolExecutor
from threading import Event, Lock

from fastapi.testclient import TestClient

from enterprise_insight_backend.actions import DEFAULT_ACTIONS, ActionService
from enterprise_insight_backend.tool_registry import (
    TOOL_SPECS,
    get_tool_spec,
    tool_keys_for_agent,
    validate_tool_registry,
)


def _project(client: TestClient) -> str:
    company = client.post("/api/v3/companies", json={"name": "动作样本"}).json()
    project = client.post(
        f"/api/v3/companies/{company['id']}/projects", json={"name": "动作闭环"}
    ).json()
    installed = client.post(f"/api/v3/projects/{project['id']}/ontology/default-pack")
    assert installed.status_code == 200, installed.text
    return project["id"]


def test_default_actions_and_tool_registry_are_complete() -> None:
    default_keys = {item["key"] for item in DEFAULT_ACTIONS}
    assert validate_tool_registry(default_keys) == []
    assert set(TOOL_SPECS) == default_keys
    for key in default_keys:
        spec = get_tool_spec(key)
        assert spec is not None
        assert spec.handler_name
        assert spec.agents
        assert spec.json_schema()["type"] == "object"
    assert "create_entity" in tool_keys_for_agent("PROJECTION")
    assert "save_hypothesis" not in tool_keys_for_agent("PROJECTION")


def test_mapping_suggestion_action_advertises_all_optional_inputs() -> None:
    definition = next(
        item for item in DEFAULT_ACTIONS if item["key"] == "suggest_semantic_mappings"
    )
    advertised = {item["key"] for item in definition["parameters"]}
    assert advertised == {
        "target_type_key",
        "source_system_id",
        "source_asset",
        "include_existing",
    }
    assert advertised <= get_tool_spec("suggest_semantic_mappings").input_keys


def test_unregistered_internal_action_is_rejected(client: TestClient) -> None:
    project_id = _project(client)
    response = client.post(
        f"/api/v3/projects/{project_id}/action-definitions",
        json={
            "key": "unknown_internal_action",
            "name": "不存在的内部动作",
            "description": "不能注册没有后端实现的动作。",
            "parameters": [],
            "preconditions": [],
            "effects": [],
            "execution_mode": "INTERNAL",
            "risk_level": "LOW",
            "require_approval": False,
        },
    )
    assert response.status_code == 422, response.text
    assert response.json()["error"]["code"] == "ACTION_HANDLER_UNAVAILABLE"


def test_unavailable_connector_action_is_rejected_at_definition_boundary(
    client: TestClient,
) -> None:
    project_id = _project(client)
    response = client.post(
        f"/api/v3/projects/{project_id}/action-definitions",
        json={
            "key": "external_side_effect",
            "name": "未安装的外部动作",
            "description": "不能把不可执行的连接器伪装成可用动作。",
            "parameters": [],
            "preconditions": [],
            "effects": [{"kind": "UPDATE", "resource": "EXTERNAL"}],
            "execution_mode": "CONNECTOR",
            "risk_level": "HIGH",
            "require_approval": True,
        },
    )
    assert response.status_code == 422, response.text
    assert response.json()["error"]["code"] == "ACTION_CONNECTOR_UNAVAILABLE"


def test_agent_action_dry_run_approval_execute_and_rollback(client: TestClient) -> None:
    project_id = _project(client)
    definitions = client.get(f"/api/v3/projects/{project_id}/action-definitions")
    assert definitions.status_code == 200, definitions.text
    create_entity = next(
        item for item in definitions.json()["items"] if item["key"] == "create_entity"
    )

    thread = client.post(
        f"/api/v3/projects/{project_id}/agent-threads",
        json={"agent_kind": "PROJECTION", "title": "动作来源"},
    ).json()
    run = client.post(
        f"/api/v3/projects/{project_id}/agent-threads/{thread['id']}/messages",
        json={"content": "请建立一个销售岗位"},
    ).json()["run"]
    invocation = client.post(
        f"/api/v3/projects/{project_id}/action-invocations",
        json={
            "action_definition_id": create_entity["id"],
            "source_agent_run_id": run["id"],
            "idempotency_key": "create-sales-role-1",
            "requested_by": "projection-agent",
            "input": {
                "type_key": "role",
                "stable_key": "role.sales",
                "name": "销售岗位",
                "properties": {"purpose": "负责销售结果"},
            },
        },
    )
    assert invocation.status_code == 201, invocation.text
    invocation_id = invocation.json()["id"]

    duplicate = client.post(
        f"/api/v3/projects/{project_id}/action-invocations",
        json={
            "action_definition_id": create_entity["id"],
            "source_agent_run_id": run["id"],
            "idempotency_key": "create-sales-role-1",
            "requested_by": "projection-agent",
            "input": {
                "type_key": "role",
                "stable_key": "role.sales",
                "name": "销售岗位",
                "properties": {"purpose": "负责销售结果"},
            },
        },
    )
    assert duplicate.status_code == 201
    assert duplicate.json()["id"] == invocation_id

    preview = client.post(
        f"/api/v3/projects/{project_id}/action-invocations/{invocation_id}/dry-run"
    )
    assert preview.status_code == 200, preview.text
    assert preview.json()["status"] == "WAITING_APPROVAL"

    before_approval = client.post(
        f"/api/v3/projects/{project_id}/action-invocations/{invocation_id}/execute"
    )
    assert before_approval.status_code == 409

    approved = client.post(
        f"/api/v3/projects/{project_id}/action-invocations/{invocation_id}/approve",
        json={"approved_by": "executive"},
    )
    assert approved.status_code == 200, approved.text
    executed = client.post(
        f"/api/v3/projects/{project_id}/action-invocations/{invocation_id}/execute"
    )
    assert executed.status_code == 200, executed.text
    assert executed.json()["status"] == "SUCCEEDED"
    entity_id = executed.json()["result"]["id"]

    graph = client.post(f"/api/v3/projects/{project_id}/graph/query", json={}).json()
    assert [item["id"] for item in graph["entities"]] == [entity_id]

    logs = client.get(
        f"/api/v3/projects/{project_id}/action-logs", params={"invocation_id": invocation_id}
    )
    assert logs.status_code == 200
    assert {item["to_status"] for item in logs.json()["items"]} >= {
        "DRAFT",
        "WAITING_APPROVAL",
        "APPROVED",
        "RUNNING",
        "SUCCEEDED",
    }

    metric = client.post(
        f"/api/v3/projects/{project_id}/management/metrics",
        json={
            "key": "cycle_time",
            "name": "流程周期",
            "unit": "小时",
            "direction": "LOWER_IS_BETTER",
            "target_value": 24,
        },
    )
    assert metric.status_code == 201, metric.text
    observed = client.post(
        f"/api/v3/projects/{project_id}/action-invocations/{invocation_id}/observations",
        json={
            "observation_kind": "METRIC",
            "metric_key": "cycle_time",
            "period_key": "2026-09",
            "observed_value": 30,
            "outcome": "INEFFECTIVE",
            "note": "上线后首月结果",
        },
    )
    assert observed.status_code == 201, observed.text
    assert observed.json()["metric_definition_id"] == metric.json()["id"]
    assert observed.json()["metric_observation_id"]
    metric_rows = client.get(
        f"/api/v3/projects/{project_id}/management/metrics/{metric.json()['id']}/observations"
    ).json()["items"]
    assert metric_rows[0]["id"] == observed.json()["metric_observation_id"]
    assert metric_rows[0]["source"] == f"ACTION:{invocation_id}"
    assert metric_rows[0]["status"] == "MISS"

    rolled_back = client.post(
        f"/api/v3/projects/{project_id}/action-invocations/{invocation_id}/rollback"
    )
    assert rolled_back.status_code == 200, rolled_back.text
    assert rolled_back.json()["status"] == "ROLLED_BACK"
    retired = client.get(f"/api/v3/projects/{project_id}/entities/{entity_id}")
    assert retired.status_code == 200
    assert retired.json()["status"] == "RETIRED"


def test_dry_run_does_not_mutate_and_low_risk_agent_action_runs(client: TestClient) -> None:
    project_id = _project(client)
    definitions = client.get(f"/api/v3/projects/{project_id}/action-definitions").json()["items"]
    save_hypothesis = next(item for item in definitions if item["key"] == "save_hypothesis")
    invocation = client.post(
        f"/api/v3/projects/{project_id}/action-invocations",
        json={
            "action_definition_id": save_hypothesis["id"],
            "idempotency_key": "hypothesis-1",
            "input": {
                "type_key": "latent_risk",
                "title": "可能存在流程瓶颈",
                "summary": "需要管理层进一步确认。",
            },
        },
    )
    assert invocation.status_code == 201, invocation.text
    invocation_id = invocation.json()["id"]
    preview = client.post(
        f"/api/v3/projects/{project_id}/action-invocations/{invocation_id}/dry-run"
    )
    assert preview.status_code == 200, preview.text
    assert preview.json()["status"] == "DRY_RUN_COMPLETED"
    assert (
        client.post(f"/api/v3/projects/{project_id}/graph/query", json={}).json()["entities"] == []
    )

    executed = client.post(
        f"/api/v3/projects/{project_id}/action-invocations/{invocation_id}/execute"
    )
    assert executed.status_code == 200, executed.text
    assert executed.json()["status"] == "SUCCEEDED"
    hypotheses = client.get(f"/api/v3/projects/{project_id}/hypotheses")
    assert hypotheses.status_code == 200
    assert hypotheses.json()["total"] == 1


def test_projection_agent_can_apply_an_atomic_multi_resource_change(
    client: TestClient,
) -> None:
    project_id = _project(client)
    project = client.get(f"/api/v3/projects/{project_id}").json()
    definitions = client.get(f"/api/v3/projects/{project_id}/action-definitions").json()[
        "items"
    ]
    definition = next(
        item for item in definitions if item["key"] == "apply_projection_changes"
    )
    manager_id = "11111111-1111-4111-8111-111111111111"
    analyst_id = "22222222-2222-4222-8222-222222222222"
    relation_id = "33333333-3333-4333-8333-333333333333"
    payload = {
        "title": "建立管理与分析岗位及汇报关系",
        "base_revision": project["revision"],
        "created_by": "agent:projection",
        "operations": [
            {
                "operation_id": manager_id,
                "kind": "CREATE_ENTITY",
                "payload": {
                    "type_key": "role",
                    "stable_key": "role.manager",
                    "name": "管理岗位",
                    "properties": {"purpose": "明确经营责任"},
                },
            },
            {
                "operation_id": analyst_id,
                "kind": "CREATE_ENTITY",
                "payload": {
                    "type_key": "role",
                    "stable_key": "role.analyst",
                    "name": "分析岗位",
                    "properties": {"purpose": "提供经营分析"},
                },
            },
            {
                "operation_id": relation_id,
                "kind": "CREATE_RELATION",
                "payload": {
                    "type_key": "reports_to",
                    "participants": [
                        {"role_key": "reporter", "entity_id": analyst_id},
                        {"role_key": "manager", "entity_id": manager_id},
                    ],
                },
            },
        ],
    }
    invocation = client.post(
        f"/api/v3/projects/{project_id}/action-invocations",
        json={"action_definition_id": definition["id"], "input": payload},
    )
    assert invocation.status_code == 201, invocation.text
    invocation_id = invocation.json()["id"]

    dry_run = client.post(
        f"/api/v3/projects/{project_id}/action-invocations/{invocation_id}/dry-run"
    )
    assert dry_run.status_code == 200, dry_run.text
    assert dry_run.json()["status"] == "WAITING_APPROVAL"
    assert client.post(f"/api/v3/projects/{project_id}/graph/query", json={}).json()[
        "entities"
    ] == []

    assert client.post(
        f"/api/v3/projects/{project_id}/action-invocations/{invocation_id}/approve",
        json={"approved_by": "developer"},
    ).status_code == 200
    executed = client.post(
        f"/api/v3/projects/{project_id}/action-invocations/{invocation_id}/execute"
    )
    assert executed.status_code == 200, executed.text
    assert executed.json()["status"] == "SUCCEEDED"
    graph = client.post(f"/api/v3/projects/{project_id}/graph/query", json={}).json()
    assert {item["id"] for item in graph["entities"]} == {manager_id, analyst_id}
    assert [item["id"] for item in graph["relations"]] == [relation_id]


def test_concurrent_approved_execute_claims_once_and_returns_running_state(
    client: TestClient, monkeypatch
) -> None:
    project_id = _project(client)
    definitions = client.get(f"/api/v3/projects/{project_id}/action-definitions").json()["items"]
    definition = next(item for item in definitions if item["key"] == "create_entity")
    invocation = client.post(
        f"/api/v3/projects/{project_id}/action-invocations",
        json={
            "action_definition_id": definition["id"],
            "idempotency_key": "concurrent-approved-create",
            "input": {
                "type_key": "role",
                "stable_key": "role.concurrent",
                "name": "并发岗位",
            },
        },
    )
    assert invocation.status_code == 201, invocation.text
    invocation_id = invocation.json()["id"]
    assert client.post(
        f"/api/v3/projects/{project_id}/action-invocations/{invocation_id}/dry-run"
    ).json()["status"] == "WAITING_APPROVAL"
    assert client.post(
        f"/api/v3/projects/{project_id}/action-invocations/{invocation_id}/approve",
        json={"approved_by": "executive"},
    ).status_code == 200

    handler_entered = Event()
    release_handler = Event()
    calls = 0
    calls_lock = Lock()
    original_handler = ActionService._execute_handler

    def blocking_handler(self, *args, **kwargs):
        nonlocal calls
        with calls_lock:
            calls += 1
        handler_entered.set()
        assert release_handler.wait(5)
        return original_handler(self, *args, **kwargs)

    monkeypatch.setattr(ActionService, "_execute_handler", blocking_handler)
    execute_path = (
        f"/api/v3/projects/{project_id}/action-invocations/{invocation_id}/execute"
    )
    with TestClient(client.app) as first_client, TestClient(client.app) as second_client:
        with ThreadPoolExecutor(max_workers=1) as pool:
            first = pool.submit(first_client.post, execute_path)
            assert handler_entered.wait(5)
            second = second_client.post(execute_path)
            assert second.status_code == 200, second.text
            assert second.json()["status"] == "RUNNING"
            release_handler.set()
            executed = first.result(timeout=5)

    assert executed.status_code == 200, executed.text
    assert executed.json()["status"] == "SUCCEEDED"
    assert calls == 1
    graph = client.post(f"/api/v3/projects/{project_id}/graph/query", json={}).json()
    assert len(graph["entities"]) == 1


def test_concurrent_no_approval_execute_claims_once_and_is_idempotent(
    client: TestClient, monkeypatch
) -> None:
    project_id = _project(client)
    definitions = client.get(f"/api/v3/projects/{project_id}/action-definitions").json()["items"]
    definition = next(item for item in definitions if item["key"] == "save_hypothesis")
    invocation = client.post(
        f"/api/v3/projects/{project_id}/action-invocations",
        json={
            "action_definition_id": definition["id"],
            "idempotency_key": "concurrent-no-approval",
            "input": {
                "type_key": "latent_risk",
                "title": "并发假设",
                "summary": "只能保存一次。",
            },
        },
    )
    assert invocation.status_code == 201, invocation.text
    invocation_id = invocation.json()["id"]
    assert client.post(
        f"/api/v3/projects/{project_id}/action-invocations/{invocation_id}/dry-run"
    ).json()["status"] == "DRY_RUN_COMPLETED"

    handler_entered = Event()
    release_handler = Event()
    calls = 0
    calls_lock = Lock()
    original_handler = ActionService._execute_handler

    def blocking_handler(self, *args, **kwargs):
        nonlocal calls
        with calls_lock:
            calls += 1
        handler_entered.set()
        assert release_handler.wait(5)
        return original_handler(self, *args, **kwargs)

    monkeypatch.setattr(ActionService, "_execute_handler", blocking_handler)
    execute_path = (
        f"/api/v3/projects/{project_id}/action-invocations/{invocation_id}/execute"
    )
    with TestClient(client.app) as first_client, TestClient(client.app) as second_client:
        with ThreadPoolExecutor(max_workers=1) as pool:
            first = pool.submit(first_client.post, execute_path)
            assert handler_entered.wait(5)
            second = second_client.post(execute_path)
            assert second.status_code == 200, second.text
            assert second.json()["status"] == "RUNNING"
            release_handler.set()
            executed = first.result(timeout=5)

    assert executed.status_code == 200, executed.text
    assert executed.json()["status"] == "SUCCEEDED"
    assert calls == 1
    hypotheses = client.get(f"/api/v3/projects/{project_id}/hypotheses")
    assert hypotheses.status_code == 200
    assert hypotheses.json()["total"] == 1
