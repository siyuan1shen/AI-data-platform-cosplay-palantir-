from __future__ import annotations

import hashlib
import json
from typing import Any
from uuid import uuid4

import pytest
from fastapi.testclient import TestClient

from enterprise_insight_backend.agent_runtime import AgentRuntimeService
from enterprise_insight_backend.control_worker import ControlIndexWorker
from enterprise_insight_backend.schemas import AgentStructuredOutput


def _complex_candidate() -> dict[str, Any]:
    return {
        "task_kind": "COMPLEX_ANALYSIS",
        "signals": {
            "explicit_exploration": True,
            "requires_causal_explanation": False,
            "requires_tradeoff": False,
            "requires_unstructured_cross_store": True,
            "requires_role_field_detail": False,
        },
        "mentions": {
            "targets": [],
            "metrics": [],
            "action_effects": [],
            "time_ranges": [],
        },
        "ambiguity_detected": False,
        "ambiguity_explanation": "",
        "clarification_needed": False,
        "clarification_questions": [],
    }


def _simple_candidate() -> dict[str, Any]:
    candidate = _complex_candidate()
    candidate["task_kind"] = "SIMPLE_READ"
    candidate["signals"]["explicit_exploration"] = False
    candidate["signals"]["requires_unstructured_cross_store"] = False
    candidate["mentions"]["targets"] = ["运营部"]
    return candidate


def _setup(client: TestClient) -> tuple[str, str, str]:
    profile = client.post(
        "/api/v3/model-profiles",
        json={
            "name": "候选提案测试模型",
            "provider": "MOCK",
            "base_url": "mock://local",
            "model": "deterministic",
        },
    )
    assert profile.status_code == 201, profile.text
    company = client.post("/api/v3/companies", json={"name": "提案校验公司"}).json()
    project = client.post(
        f"/api/v3/companies/{company['id']}/projects",
        json={"name": "提案校验项目"},
    ).json()
    installed = client.post(f"/api/v3/projects/{project['id']}/ontology/default-pack")
    assert installed.status_code == 200, installed.text
    entity = client.post(
        f"/api/v3/projects/{project['id']}/entities",
        json={"type_key": "organization_unit", "stable_key": "ops", "name": "运营部"},
    )
    assert entity.status_code == 201, entity.text
    current_project = client.get(f"/api/v3/projects/{project['id']}").json()
    publication = client.post(
        f"/api/v3/projects/{project['id']}/publications",
        json={
            "label": "候选提案测试基线",
            "expected_project_revision": current_project["revision"],
        },
    )
    assert publication.status_code == 201, publication.text
    return company["id"], project["id"], profile.json()["id"]


def _create_observation(client: TestClient, project_id: str) -> dict[str, Any]:
    response = client.post(
        f"/api/v3/projects/{project_id}/observations",
        json={
            "kind": "INTERVIEW",
            "title": "跨部门协作访谈",
            "content": "紧急插单需要计划岗与销售岗反复确认交付日期。",
        },
    )
    assert response.status_code == 201, response.text
    return response.json()


def _proposal(
    company_id: str,
    project_id: str,
    source_ref: str,
    *,
    excerpt: str = "计划岗与销售岗反复确认交付日期",
    counterevidence: list[dict[str, str]] | None = None,
) -> dict[str, Any]:
    return {
        "company_id": company_id,
        "project_id": project_id,
        "potential_type": "PROBLEM_HYPOTHESIS",
        "claim": "紧急插单可能造成跨岗位重复确认。",
        "applicability_scope": "订单交付流程",
        "task_source": "管理决策Agent复杂分析",
        "supporting_evidence": [{"source_ref": source_ref, "excerpt": excerpt}],
        "counterevidence": counterevidence or [],
        "verification_method": "比较紧急与常规订单的确认次数",
        "evidence_status": "UNTESTED",
    }


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
            "content": "请分析组织协作中可能存在的潜在问题",
            "model_profile_id": profile_id,
            "task_intent_candidate": candidate,
        },
    )
    assert accepted.status_code == 202, accepted.text
    return thread["id"], accepted.json()["run"]["id"]


def _execute_with_proposals(
    client: TestClient,
    monkeypatch: pytest.MonkeyPatch,
    *,
    project_id: str,
    profile_id: str,
    task_candidate: dict[str, Any],
    proposals: list[dict[str, Any]],
) -> tuple[dict[str, Any], list[dict[str, Any]], str]:
    thread_id, run_id = _start_run(client, project_id, profile_id, task_candidate)

    def fake_invoke(_self, _profile, _run, _user_text, _context):
        return AgentStructuredOutput(
            content="已完成分析；以下候选仍需管理者确认。",
            potential_candidate_proposals=proposals,
        )

    monkeypatch.setattr(AgentRuntimeService, "_invoke_model", fake_invoke)
    response = client.post(f"/api/v3/projects/{project_id}/agent-runs/{run_id}/execute")
    assert response.status_code == 200, response.text
    messages = client.get(
        f"/api/v3/projects/{project_id}/agent-threads/{thread_id}/messages"
    )
    assert messages.status_code == 200, messages.text
    assert any(
        item["content"] == "已完成分析；以下候选仍需管理者确认。"
        for item in messages.json()["items"]
    )
    steps_response = client.get(
        f"/api/v3/projects/{project_id}/agent-runs/{run_id}/steps"
    )
    assert steps_response.status_code == 200, steps_response.text
    return response.json(), steps_response.json()["items"], run_id


def _assert_safe_rejection(steps: list[dict[str, Any]], expected_reason: str) -> None:
    assert not [step for step in steps if step["kind"] == "POTENTIAL_CANDIDATE_PROPOSED"]
    validation_steps = [
        step for step in steps if step["kind"] == "POTENTIAL_CANDIDATE_VALIDATION"
    ]
    assert len(validation_steps) == 1
    validation = validation_steps[0]
    assert validation["output_payload"]["accepted_count"] == 0
    assert validation["output_payload"]["rejected_count"] == 1
    assert validation["output_payload"]["rejection_counts"] == {expected_reason: 1}
    serialized = json.dumps(validation, ensure_ascii=False)
    assert "计划岗与销售岗反复确认交付日期" not in serialized
    assert "紧急插单可能造成跨岗位重复确认" not in serialized


@pytest.mark.parametrize("mismatch", ["company", "project"])
def test_scope_mismatch_candidate_is_rejected_without_leaking_text(
    client: TestClient, monkeypatch: pytest.MonkeyPatch, mismatch: str
) -> None:
    company_id, project_id, profile_id = _setup(client)
    observation = _create_observation(client, project_id)
    candidate = _proposal(
        company_id,
        project_id,
        f"observation://{observation['id']}/{observation['revision']}",
    )
    candidate[f"{mismatch}_id"] = str(uuid4())

    result, steps, _run_id = _execute_with_proposals(
        client,
        monkeypatch,
        project_id=project_id,
        profile_id=profile_id,
        task_candidate=_complex_candidate(),
        proposals=[candidate],
    )

    assert result["status"] == "COMPLETED"
    _assert_safe_rejection(steps, "SCOPE_MISMATCH")


def test_fake_observation_source_is_rejected(client: TestClient, monkeypatch) -> None:
    company_id, project_id, profile_id = _setup(client)
    candidate = _proposal(company_id, project_id, f"observation://{uuid4()}/1")

    result, steps, _run_id = _execute_with_proposals(
        client,
        monkeypatch,
        project_id=project_id,
        profile_id=profile_id,
        task_candidate=_complex_candidate(),
        proposals=[candidate],
    )

    assert result["status"] == "COMPLETED"
    _assert_safe_rejection(steps, "SOURCE_VERSION_OR_EXCERPT_INVALID")


def test_stale_observation_revision_is_rejected(client: TestClient, monkeypatch) -> None:
    company_id, project_id, profile_id = _setup(client)
    observation = _create_observation(client, project_id)
    revised = client.patch(
        f"/api/v3/projects/{project_id}/observations/{observation['id']}",
        json={
            "expected_revision": observation["revision"],
            "kind": observation["kind"],
            "title": observation["title"],
            "content": "当前修订已替换原始访谈内容。",
            "occurred_at": observation["occurred_at"],
        },
    )
    assert revised.status_code == 200, revised.text
    candidate = _proposal(
        company_id,
        project_id,
        f"observation://{observation['id']}/{observation['revision']}",
    )

    result, steps, _run_id = _execute_with_proposals(
        client,
        monkeypatch,
        project_id=project_id,
        profile_id=profile_id,
        task_candidate=_complex_candidate(),
        proposals=[candidate],
    )

    assert result["status"] == "COMPLETED"
    _assert_safe_rejection(steps, "SOURCE_VERSION_OR_EXCERPT_INVALID")


def test_non_exact_excerpt_is_rejected(client: TestClient, monkeypatch) -> None:
    company_id, project_id, profile_id = _setup(client)
    observation = _create_observation(client, project_id)
    candidate = _proposal(
        company_id,
        project_id,
        f"observation://{observation['id']}/{observation['revision']}",
        excerpt="访谈中没有出现的内容",
    )

    result, steps, _run_id = _execute_with_proposals(
        client,
        monkeypatch,
        project_id=project_id,
        profile_id=profile_id,
        task_candidate=_complex_candidate(),
        proposals=[candidate],
    )

    assert result["status"] == "COMPLETED"
    _assert_safe_rejection(steps, "SOURCE_VERSION_OR_EXCERPT_INVALID")


def test_all_evidence_including_counterevidence_must_be_valid(
    client: TestClient, monkeypatch
) -> None:
    company_id, project_id, profile_id = _setup(client)
    observation = _create_observation(client, project_id)
    candidate = _proposal(
        company_id,
        project_id,
        f"observation://{observation['id']}/{observation['revision']}",
        counterevidence=[
            {
                "source_ref": f"observation://{uuid4()}/1",
                "excerpt": "不存在的反证原文",
            }
        ],
    )

    result, steps, _run_id = _execute_with_proposals(
        client,
        monkeypatch,
        project_id=project_id,
        profile_id=profile_id,
        task_candidate=_complex_candidate(),
        proposals=[candidate],
    )

    assert result["status"] == "COMPLETED"
    _assert_safe_rejection(steps, "SOURCE_VERSION_OR_EXCERPT_INVALID")


def test_valid_candidate_writes_one_stable_hashed_outbox_step_only(
    client: TestClient, monkeypatch: pytest.MonkeyPatch
) -> None:
    company_id, project_id, profile_id = _setup(client)
    observation = _create_observation(client, project_id)
    proposal = _proposal(
        company_id,
        project_id,
        f"observation://{observation['id']}/{observation['revision']}",
    )

    result, steps, run_id = _execute_with_proposals(
        client,
        monkeypatch,
        project_id=project_id,
        profile_id=profile_id,
        task_candidate=_complex_candidate(),
        proposals=[proposal, proposal],
    )

    assert result["status"] == "COMPLETED"
    proposed = [step for step in steps if step["kind"] == "POTENTIAL_CANDIDATE_PROPOSED"]
    assert len(proposed) == 1
    validation = next(
        step for step in steps if step["kind"] == "POTENTIAL_CANDIDATE_VALIDATION"
    )
    assert validation["output_payload"]["accepted_count"] == 1
    assert validation["output_payload"]["rejected_count"] == 1
    assert validation["output_payload"]["rejection_counts"] == {"DUPLICATE_CANDIDATE": 1}
    envelope = proposed[0]["output_payload"]
    assert set(envelope) == {"candidate", "candidate_hash"}
    canonical = json.dumps(
        envelope["candidate"],
        ensure_ascii=False,
        sort_keys=True,
        separators=(",", ":"),
    )
    assert envelope["candidate_hash"] == hashlib.sha256(canonical.encode("utf-8")).hexdigest()
    candidate_id = envelope["candidate"]["candidate_id"]
    assert candidate_id
    reread = client.get(f"/api/v3/projects/{project_id}/agent-runs/{run_id}/steps").json()
    reread_proposed = [
        step for step in reread["items"] if step["kind"] == "POTENTIAL_CANDIDATE_PROPOSED"
    ]
    assert len(reread_proposed) == 1
    assert reread_proposed[0]["output_payload"]["candidate"]["candidate_id"] == candidate_id
    model_step = next(step for step in steps if step["kind"] == "MODEL")
    assert "potential_candidate_proposals" not in model_step["output_payload"]
    assert client.get(f"/api/v3/projects/{project_id}/potential-records").json()["total"] == 0

    index_worker = ControlIndexWorker(
        client.app.state.database,
        client.app.state.control_database,
        client.app.state.settings,
    )
    # Route and candidate plus the immutable query manifest and its two source reads.
    assert index_worker.synchronize_once() == 5
    assert index_worker.synchronize_once() == 0
    query_manifest = client.get(
        f"/api/v3/projects/{project_id}/control/query-manifests?run_id={run_id}"
    )
    assert query_manifest.status_code == 200, query_manifest.text
    assert query_manifest.json()["total"] == 1
    candidates = client.get(f"/api/v3/projects/{project_id}/potential-candidates")
    assert candidates.status_code == 200, candidates.text
    assert candidates.json()["total"] == 1
    assert candidates.json()["items"][0]["id"] == candidate_id
    assert candidates.json()["items"][0]["status"] == "PROPOSED"


def test_simple_route_does_not_capture_candidate_proposal(
    client: TestClient, monkeypatch: pytest.MonkeyPatch
) -> None:
    company_id, project_id, profile_id = _setup(client)
    observation = _create_observation(client, project_id)
    proposal = _proposal(
        company_id,
        project_id,
        f"observation://{observation['id']}/{observation['revision']}",
    )

    result, steps, _run_id = _execute_with_proposals(
        client,
        monkeypatch,
        project_id=project_id,
        profile_id=profile_id,
        task_candidate=_simple_candidate(),
        proposals=[proposal],
    )

    assert result["status"] == "COMPLETED"
    assert not [step for step in steps if step["kind"].startswith("POTENTIAL_CANDIDATE")]
    model_step = next(step for step in steps if step["kind"] == "MODEL")
    assert "potential_candidate_proposals" not in model_step["output_payload"]


def test_candidate_is_marked_stale_if_source_changes_before_human_acceptance(
    client: TestClient, monkeypatch: pytest.MonkeyPatch
) -> None:
    company_id, project_id, profile_id = _setup(client)
    observation = _create_observation(client, project_id)
    proposal = _proposal(
        company_id,
        project_id,
        f"observation://{observation['id']}/{observation['revision']}",
        excerpt="计划岗与销售岗反复确认交付日期",
    )
    _result, _steps, run_id = _execute_with_proposals(
        client,
        monkeypatch,
        project_id=project_id,
        profile_id=profile_id,
        task_candidate=_complex_candidate(),
        proposals=[proposal],
    )
    worker = ControlIndexWorker(
        client.app.state.database,
        client.app.state.control_database,
        client.app.state.settings,
    )
    assert worker.synchronize_once() == 5
    assert worker.synchronize_once() == 0
    assert client.get(
        f"/api/v3/projects/{project_id}/control/query-manifests?run_id={run_id}"
    ).json()["total"] == 1
    candidates = client.get(f"/api/v3/projects/{project_id}/potential-candidates")
    candidate = candidates.json()["items"][0]

    revised = client.patch(
        f"/api/v3/projects/{project_id}/observations/{observation['id']}",
        json={
            "expected_revision": observation["revision"],
            "kind": observation["kind"],
            "title": observation["title"],
            "content": "访谈修订后已不再支持旧版本中的判断。",
            "occurred_at": observation["occurred_at"],
        },
    )
    assert revised.status_code == 200, revised.text
    accepted = client.post(
        f"/api/v3/projects/{project_id}/potential-candidates/{candidate['id']}/accept",
        json={"expected_hash": candidate["payload_sha256"]},
    )
    assert accepted.status_code == 409
    assert accepted.json()["error"]["code"] == "CONTROL_CANDIDATE_SOURCE_STALE"
    refreshed = client.get(
        f"/api/v3/projects/{project_id}/potential-candidates/{candidate['id']}"
    )
    assert refreshed.json()["status"] == "STALE"
    assert client.get(f"/api/v3/projects/{project_id}/potential-records").json()["total"] == 0
