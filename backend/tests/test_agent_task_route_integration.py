from __future__ import annotations

import json
from typing import Any

from fastapi.testclient import TestClient

from enterprise_insight_backend.agent_runtime import AgentRuntimeService
from enterprise_insight_backend.schemas import AgentStructuredOutput


def _candidate(
    task_kind: str,
    *,
    targets: list[str] | None = None,
    metrics: list[str] | None = None,
    time_ranges: list[str] | None = None,
    action_effects: list[str] | None = None,
    signals: dict[str, bool] | None = None,
) -> dict[str, Any]:
    return {
        "task_kind": task_kind,
        "signals": signals
        or {
            "explicit_exploration": False,
            "requires_causal_explanation": False,
            "requires_tradeoff": False,
            "requires_unstructured_cross_store": False,
            "requires_role_field_detail": False,
        },
        "mentions": {
            "targets": targets or [],
            "metrics": metrics or [],
            "action_effects": action_effects or [],
            "time_ranges": time_ranges or [],
        },
        "ambiguity_detected": False,
        "ambiguity_explanation": "",
        "clarification_needed": False,
        "clarification_questions": [],
    }


def _setup(client: TestClient, *, publish: bool = True) -> tuple[str, str, str]:
    profile = client.post(
        "/api/v3/model-profiles",
        json={
            "name": "路由测试模型",
            "provider": "MOCK",
            "base_url": "mock://local",
            "model": "deterministic",
        },
    )
    assert profile.status_code == 201, profile.text
    company = client.post("/api/v3/companies", json={"name": "路由样本公司"}).json()
    project = client.post(
        f"/api/v3/companies/{company['id']}/projects",
        json={"name": "路由样本项目"},
    ).json()
    installed = client.post(f"/api/v3/projects/{project['id']}/ontology/default-pack")
    assert installed.status_code == 200, installed.text
    entity = client.post(
        f"/api/v3/projects/{project['id']}/entities",
        json={"type_key": "organization_unit", "stable_key": "ops", "name": "运营部"},
    )
    assert entity.status_code == 201, entity.text
    if publish:
        current_project = client.get(f"/api/v3/projects/{project['id']}").json()
        publication = client.post(
            f"/api/v3/projects/{project['id']}/publications",
            json={
                "label": "路由测试基线",
                "expected_project_revision": current_project["revision"],
            },
        )
        assert publication.status_code == 201, publication.text
    return project["id"], profile.json()["id"], entity.json()["id"]


def _start_run(
    client: TestClient,
    project_id: str,
    profile_id: str,
    candidate: dict[str, Any],
) -> tuple[str, str]:
    thread = client.post(
        f"/api/v3/projects/{project_id}/agent-threads",
        json={"agent_kind": "MANAGEMENT"},
    ).json()
    accepted = client.post(
        f"/api/v3/projects/{project_id}/agent-threads/{thread['id']}/messages",
        json={
            "content": "请根据当前企业信息处理此请求",
            "model_profile_id": profile_id,
            "task_intent_candidate": candidate,
        },
    )
    assert accepted.status_code == 202, accepted.text
    return thread["id"], accepted.json()["run"]["id"]


def test_simple_route_uses_exact_project_entity_and_excludes_virtual_stores(
    client: TestClient, monkeypatch
) -> None:
    project_id, profile_id, _entity_id = _setup(client)
    observation = client.post(
        f"/api/v3/projects/{project_id}/observations",
        json={"kind": "INTERVIEW", "title": "每日访谈", "content": "未验证的审批现场信息"},
    )
    assert observation.status_code == 201, observation.text
    thread_id, run_id = _start_run(
        client,
        project_id,
        profile_id,
        _candidate("SIMPLE_READ", targets=["运营部"]),
    )
    captured: dict[str, Any] = {}

    def fake_invoke(_self, _profile, _run, _user_text, context):
        captured.update(context)
        return AgentStructuredOutput(content="已根据正式企业模型查询。")

    monkeypatch.setattr(AgentRuntimeService, "_invoke_model", fake_invoke)
    response = client.post(f"/api/v3/projects/{project_id}/agent-runs/{run_id}/execute")
    assert response.status_code == 200, response.text
    assert response.json()["status"] == "COMPLETED"
    route = captured["task_route"]
    assert route["route"] == "SIMPLE"
    assert route["selected_stores"] == ["formal_query_snapshot"]
    assert captured["management_context"] is None
    assert captured["management_context_access"]["decision"] == "EXCLUDED_BY_SIMPLE_ROUTE"
    assert [item["name"] for item in captured["entities"]] == ["运营部"]
    assert captured["documents"] == []
    assert captured["hypotheses"] == []
    assert captured["scenarios"] == []
    assert captured["meetings"] == []
    assert captured["management_signals"] == []
    assert captured["conversation"] == []
    assert {item["key"] for item in captured["available_actions"]} <= {
        "read_enterprise_summary",
        "read_graph_neighborhood",
        "read_material_fragments",
    }

    steps = client.get(
        f"/api/v3/projects/{project_id}/agent-runs/{run_id}/steps"
    ).json()["items"]
    assert [item["kind"] for item in steps] == ["ROUTE", "MODEL"]
    result = client.get(f"/api/v3/projects/{project_id}/agent-runs/{run_id}").json()
    assert result["context_manifest"]["task_route"]["execution_authorized"] is False
    assert result["context_manifest"]["query_manifest"]["read_sets"][0]["selected_stores"] == [
        "formal_query_snapshot"
    ]


def test_company_level_simple_route_uses_formal_summary_tool(
    client: TestClient, monkeypatch
) -> None:
    project_id, profile_id, _entity_id = _setup(client)
    _thread_id, run_id = _start_run(
        client,
        project_id,
        profile_id,
        _candidate("SIMPLE_READ"),
    )
    captured: dict[str, Any] = {}

    def fake_invoke(_self, _profile, _run, _user_text, context):
        captured.update(context)
        return AgentStructuredOutput(content="已根据正式企业投影读取公司概览。")

    monkeypatch.setattr(AgentRuntimeService, "_invoke_model", fake_invoke)
    response = client.post(f"/api/v3/projects/{project_id}/agent-runs/{run_id}/execute")
    assert response.status_code == 200, response.text
    assert captured["task_route"]["route"] == "SIMPLE"
    action_keys = {item["key"] for item in captured["available_actions"]}
    assert "read_enterprise_summary" in action_keys
    assert "search_management_observations" not in action_keys


def test_complex_route_reads_separate_management_context_and_records_route(
    client: TestClient, monkeypatch
) -> None:
    project_id, profile_id, _entity_id = _setup(client)
    thread_id, run_id = _start_run(
        client,
        project_id,
        profile_id,
        _candidate(
            "COMPLEX_ANALYSIS",
            signals={
                "explicit_exploration": True,
                "requires_causal_explanation": True,
                "requires_tradeoff": True,
                "requires_unstructured_cross_store": True,
                "requires_role_field_detail": True,
            },
        ),
    )
    captured: dict[str, Any] = {}

    def fake_invoke(_self, _profile, _run, _user_text, context):
        captured.update(context)
        return AgentStructuredOutput(content="已完成跨层资料调查。")

    monkeypatch.setattr(AgentRuntimeService, "_invoke_model", fake_invoke)
    response = client.post(f"/api/v3/projects/{project_id}/agent-runs/{run_id}/execute")
    assert response.status_code == 200, response.text
    assert response.json()["status"] == "COMPLETED"
    assert captured["task_route"]["route"] == "COMPLEX"
    assert captured["task_route"]["selected_stores"] == [
        "formal_query_snapshot",
        "management_observations",
        "potential_records",
    ]
    assert captured["management_context"] is not None
    access = captured["management_context_access"]
    assert access["management_observations_read"] is True
    assert access["potential_records_read"] is True


def test_unresolved_time_filter_fails_closed_without_model_or_action(
    client: TestClient, monkeypatch
) -> None:
    project_id, profile_id, _entity_id = _setup(client)
    _thread_id, run_id = _start_run(
        client,
        project_id,
        profile_id,
        _candidate("SIMPLE_READ", targets=["运营部"], time_ranges=["过去三个月"]),
    )

    def unexpected_invoke(*_args, **_kwargs):
        raise AssertionError("unverified time filtering must not reach the model")

    monkeypatch.setattr(AgentRuntimeService, "_invoke_model", unexpected_invoke)
    response = client.post(f"/api/v3/projects/{project_id}/agent-runs/{run_id}/execute")
    assert response.status_code == 200, response.text
    assert response.json()["status"] == "COMPLETED"
    assert response.json()["action_invocation_ids"] == []
    run = client.get(f"/api/v3/projects/{project_id}/agent-runs/{run_id}").json()
    assert run["context_manifest"]["task_route"]["route"] == "NEEDS_INPUT"
    assert run["context_manifest"]["external_model_used"] is False
    messages = client.get(
        f"/api/v3/projects/{project_id}/agent-threads/{_thread_id}/messages"
    ).json()["items"]
    assert "未按该时间范围查询" in messages[-1]["content"]


def test_unresolved_action_is_not_executed_by_candidate_classification(
    client: TestClient, monkeypatch
) -> None:
    project_id, profile_id, _entity_id = _setup(client)
    _thread_id, run_id = _start_run(
        client,
        project_id,
        profile_id,
        _candidate("ACTION_REQUEST", action_effects=["调整审批流程"]),
    )

    def unexpected_invoke(*_args, **_kwargs):
        raise AssertionError("unresolved action must not reach the model")

    monkeypatch.setattr(AgentRuntimeService, "_invoke_model", unexpected_invoke)
    response = client.post(f"/api/v3/projects/{project_id}/agent-runs/{run_id}/execute")
    assert response.status_code == 200, response.text
    assert response.json()["status"] == "COMPLETED"
    assert response.json()["action_invocation_ids"] == []
    run = client.get(f"/api/v3/projects/{project_id}/agent-runs/{run_id}").json()
    assert run["context_manifest"]["task_route"]["route"] == "NEEDS_INPUT"
    assert run["context_manifest"]["task_route"]["execution_authorized"] is False


def test_route_refuses_unpublished_draft_without_resolving_model_profile(
    client: TestClient, monkeypatch
) -> None:
    project_id, profile_id, _entity_id = _setup(client, publish=False)
    _thread_id, run_id = _start_run(
        client,
        project_id,
        profile_id,
        _candidate("SIMPLE_READ", targets=["运营部"]),
    )

    def unexpected_profile_lookup(*_args, **_kwargs):
        raise AssertionError("a local NEEDS_INPUT route must not need a model profile")

    monkeypatch.setattr(AgentRuntimeService, "_resolve_profile", unexpected_profile_lookup)
    response = client.post(f"/api/v3/projects/{project_id}/agent-runs/{run_id}/execute")
    assert response.status_code == 200, response.text
    assert response.json()["status"] == "COMPLETED"
    run = client.get(f"/api/v3/projects/{project_id}/agent-runs/{run_id}").json()
    route = run["context_manifest"]["task_route"]
    assert route["route"] == "NEEDS_INPUT"
    assert any("没有已发布企业模型" in item for item in route["missing_items"])
    assert run["context_manifest"]["external_model_used"] is False


def test_automatic_classifier_sends_only_user_text_and_routes_strict_candidate(
    client: TestClient, monkeypatch
) -> None:
    project_id, _mock_profile_id, _entity_id = _setup(client)
    profile = client.post(
        "/api/v3/model-profiles",
        json={
            "name": "自动分类外部模型",
            "provider": "DEEPSEEK",
            "base_url": "https://example.invalid/v1",
            "model": "classifier-test",
            "api_key": "synthetic-classifier-key",
        },
    )
    assert profile.status_code == 201, profile.text
    thread = client.post(
        f"/api/v3/projects/{project_id}/agent-threads",
        json={"agent_kind": "MANAGEMENT"},
    ).json()
    user_text = "请查询运营部当前职责"
    accepted = client.post(
        f"/api/v3/projects/{project_id}/agent-threads/{thread['id']}/messages",
        json={
            "content": user_text,
            "model_profile_id": profile.json()["id"],
            "allow_external_model": True,
            "share_project_context_with_model": False,
        },
    )
    assert accepted.status_code == 202, accepted.text

    candidate = _candidate("SIMPLE_READ", targets=["运营部"])
    outbound: list[dict[str, Any]] = []

    class FakeResponse:
        def raise_for_status(self) -> None:
            return None

        def json(self) -> dict[str, Any]:
            return {"choices": [{"message": {"content": json.dumps(candidate)}}]}

    def fake_post(_url: str, **kwargs: Any) -> FakeResponse:
        outbound.append(kwargs["json"])
        return FakeResponse()

    monkeypatch.setattr("enterprise_insight_backend.agent_runtime.httpx.post", fake_post)
    captured: dict[str, Any] = {}

    def fake_invoke(_self, _profile, _run, _user_text, context):
        captured.update(context)
        return AgentStructuredOutput(content="已根据正式模型查询运营部。")

    monkeypatch.setattr(AgentRuntimeService, "_invoke_model", fake_invoke)
    run_id = accepted.json()["run"]["id"]
    completed = client.post(f"/api/v3/projects/{project_id}/agent-runs/{run_id}/execute")
    assert completed.status_code == 200, completed.text
    assert completed.json()["status"] == "COMPLETED"
    assert len(outbound) == 1
    serialized_prompt = json.dumps(outbound[0], ensure_ascii=False)
    assert user_text in serialized_prompt
    assert "路由样本公司" not in serialized_prompt
    assert _entity_id not in serialized_prompt
    assert outbound[0]["model"] == "classifier-test"
    assert captured["task_route"]["route"] == "SIMPLE"

    run = client.get(f"/api/v3/projects/{project_id}/agent-runs/{run_id}").json()
    classification = run["context_manifest"]["task_classification"]
    assert classification["status"] == "COMPLETED"
    assert classification["request_count"] == 1
    assert classification["sent_scope"] == "raw_user_message_only"
    assert classification["enterprise_context_sent"] is False
    assert run["context_manifest"]["external_model_used"] is True
    steps = client.get(
        f"/api/v3/projects/{project_id}/agent-runs/{run_id}/steps"
    ).json()["items"]
    assert [item["kind"] for item in steps] == ["TASK_CLASSIFICATION", "ROUTE", "MODEL"]
    assert steps[0]["input_payload"]["user_text_sha256"]
    assert "user_text" not in steps[0]["input_payload"]


def test_automatic_classifier_requires_external_model_consent(
    client: TestClient, monkeypatch
) -> None:
    project_id, _mock_profile_id, _entity_id = _setup(client)
    profile = client.post(
        "/api/v3/model-profiles",
        json={
            "name": "未同意外发模型",
            "provider": "DEEPSEEK",
            "base_url": "https://example.invalid/v1",
            "model": "classifier-test",
            "api_key": "synthetic-classifier-key",
        },
    )
    assert profile.status_code == 201, profile.text
    thread = client.post(
        f"/api/v3/projects/{project_id}/agent-threads",
        json={"agent_kind": "MANAGEMENT"},
    ).json()
    accepted = client.post(
        f"/api/v3/projects/{project_id}/agent-threads/{thread['id']}/messages",
        json={"content": "请查询运营部", "model_profile_id": profile.json()["id"]},
    )
    assert accepted.status_code == 202, accepted.text
    outbound: list[dict[str, Any]] = []
    monkeypatch.setattr(
        "enterprise_insight_backend.agent_runtime.httpx.post",
        lambda _url, **kwargs: outbound.append(kwargs["json"]),
    )

    run_id = accepted.json()["run"]["id"]
    completed = client.post(f"/api/v3/projects/{project_id}/agent-runs/{run_id}/execute")
    assert completed.status_code == 200, completed.text
    assert completed.json()["status"] == "FAILED"
    assert outbound == []
    run = client.get(f"/api/v3/projects/{project_id}/agent-runs/{run_id}").json()
    assert "task_classification" not in run["context_manifest"]
    assert run["error"]["code"] == "EXTERNAL_MODEL_CONFIRMATION_REQUIRED"


def test_automatic_classifier_invalid_response_fails_closed_without_retry(
    client: TestClient, monkeypatch
) -> None:
    project_id, _mock_profile_id, _entity_id = _setup(client)
    profile = client.post(
        "/api/v3/model-profiles",
        json={
            "name": "分类格式错误模型",
            "provider": "DEEPSEEK",
            "base_url": "https://example.invalid/v1",
            "model": "classifier-test",
            "api_key": "synthetic-classifier-key",
        },
    )
    assert profile.status_code == 201, profile.text
    thread = client.post(
        f"/api/v3/projects/{project_id}/agent-threads",
        json={"agent_kind": "MANAGEMENT"},
    ).json()
    accepted = client.post(
        f"/api/v3/projects/{project_id}/agent-threads/{thread['id']}/messages",
        json={
            "content": "请查询运营部",
            "model_profile_id": profile.json()["id"],
            "allow_external_model": True,
        },
    )
    assert accepted.status_code == 202, accepted.text
    calls: list[int] = []

    class InvalidResponse:
        def raise_for_status(self) -> None:
            return None

        def json(self) -> dict[str, Any]:
            return {"choices": [{"message": {"content": '{"task_kind":"SIMPLE_READ"}'}}]}

    def invalid_post(_url: str, **_kwargs: Any) -> InvalidResponse:
        calls.append(1)
        return InvalidResponse()

    monkeypatch.setattr("enterprise_insight_backend.agent_runtime.httpx.post", invalid_post)

    def unexpected_model_call(*_args: Any, **_kwargs: Any) -> AgentStructuredOutput:
        raise AssertionError("an invalid candidate must never reach the answer model")

    monkeypatch.setattr(AgentRuntimeService, "_invoke_model", unexpected_model_call)
    run_id = accepted.json()["run"]["id"]
    completed = client.post(f"/api/v3/projects/{project_id}/agent-runs/{run_id}/execute")
    assert completed.status_code == 200, completed.text
    assert completed.json()["status"] == "FAILED"
    assert len(calls) == 1
    run = client.get(f"/api/v3/projects/{project_id}/agent-runs/{run_id}").json()
    assert run["context_manifest"]["task_classification"]["status"] == "FAILED"
    assert "task_route" not in run["context_manifest"]
    steps = client.get(
        f"/api/v3/projects/{project_id}/agent-runs/{run_id}/steps"
    ).json()["items"]
    assert [item["kind"] for item in steps] == ["TASK_CLASSIFICATION"]
    assert steps[0]["status"] == "FAILED"
