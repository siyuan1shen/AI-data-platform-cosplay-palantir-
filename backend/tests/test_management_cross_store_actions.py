from __future__ import annotations

from datetime import UTC, datetime, timedelta
from uuid import UUID, uuid4

from fastapi.testclient import TestClient
from sqlalchemy import select

from enterprise_insight_backend.actions import ActionService
from enterprise_insight_backend.agent_runtime import AgentRuntimeService
from enterprise_insight_backend.models import ActionDefinitionRow
from enterprise_insight_backend.observations import ManagementObservationRow
from enterprise_insight_backend.potential import PotentialRecordRow
from enterprise_insight_backend.schemas import (
    ActionInvocationCreate,
    ActionInvocationStatus,
    AgentActionProposal,
    AgentStructuredOutput,
)


def _project(client: TestClient, company_name: str, project_name: str) -> tuple[str, str]:
    company = client.post("/api/v3/companies", json={"name": company_name})
    assert company.status_code == 201, company.text
    project = client.post(
        f"/api/v3/companies/{company.json()['id']}/projects",
        json={"name": project_name},
    )
    assert project.status_code == 201, project.text
    return company.json()["id"], project.json()["id"]


def _execute_read_action(
    client: TestClient, project_id: str, action_key: str, input_data: dict[str, object]
) -> dict[str, object]:
    with client.app.state.database.session_factory() as session:
        service = ActionService(
            session,
            client.app.state.observation_database,
            client.app.state.potential_database,
        )
        service.ensure_defaults(UUID(project_id))
        definition = session.scalar(
            select(ActionDefinitionRow).where(
                ActionDefinitionRow.project_id == project_id,
                ActionDefinitionRow.key == action_key,
            )
        )
        assert definition is not None
        invocation = service.create_invocation(
            UUID(project_id),
            ActionInvocationCreate(
                action_definition_id=UUID(definition.id),
                input=input_data,
                requested_by="agent:management",
                idempotency_key=f"test:{project_id}:{action_key}:{uuid4()}",
            ),
        )
        preview = service.dry_run(UUID(project_id), UUID(str(invocation.id)))
        assert preview.status == ActionInvocationStatus.DRY_RUN_COMPLETED
        executed = service.execute(UUID(project_id), UUID(str(invocation.id)))
        assert executed.status == ActionInvocationStatus.SUCCEEDED
        assert executed.result is not None
        return executed.result


def test_management_read_actions_search_separate_stores_with_project_scope(
    client: TestClient,
) -> None:
    company_id, project_id = _project(client, "观察检索公司", "主项目")
    _other_company_id, other_project_id = _project(client, "另一观察检索公司", "隔离项目")

    observation = client.post(
        f"/api/v3/projects/{project_id}/observations",
        json={
            "kind": "MEETING",
            "title": "临时插单复盘",
            "content": "临时插单后，生产计划有三次调整。",
        },
    )
    assert observation.status_code == 201, observation.text
    other_observation = client.post(
        f"/api/v3/projects/{other_project_id}/observations",
        json={
            "kind": "MEETING",
            "title": "另一个项目的插单",
            "content": "临时插单隔离测试内容。",
        },
    )
    assert other_observation.status_code == 201, other_observation.text

    potential = client.post(
        f"/api/v3/projects/{project_id}/potential-records",
        json={
            "company_id": company_id,
            "project_id": project_id,
            "potential_type": "PROBLEM_HYPOTHESIS",
            "claim": "临时插单可能增加排产变更",
            "applicability_scope": "主项目订单交付流程",
            "task_source": "管理层要求分析交付流程",
            "supporting_evidence": [
                {
                    "source_ref": f"observation:{observation.json()['id']}",
                    "excerpt": "临时插单后，生产计划有三次调整。",
                }
            ],
            "counterevidence": [],
            "verification_method": "比较插单和非插单订单的排产变更次数",
            "evidence_status": "UNTESTED",
        },
    )
    assert potential.status_code == 201, potential.text

    observations_result = _execute_read_action(
        client,
        project_id,
        "search_management_observations",
        {"query": "临时插单", "limit": 10},
    )
    assert observations_result["resource"] == "MANAGEMENT_OBSERVATIONS"
    assert observations_result["matching_in_scanned"] == 1
    assert observations_result["items"][0]["id"] == observation.json()["id"]
    assert observations_result["items"][0]["trust"] == "UNVERIFIED_MANAGEMENT_OBSERVATION"
    assert observations_result["truncated"] is False

    potential_result = _execute_read_action(
        client,
        project_id,
        "search_potential_records",
        {"query": "临时插单", "limit": 10},
    )
    assert potential_result["resource"] == "POTENTIAL_RECORDS"
    assert potential_result["matching_in_scanned"] == 1
    assert potential_result["items"][0]["id"] == potential.json()["id"]
    assert potential_result["items"][0]["trust"] == "HUMAN_CONFIRMED_UNVERIFIED_POTENTIAL"
    assert potential_result["items"][0]["evidence_status"] == "UNTESTED"


def test_management_agent_can_call_cross_store_search_action(
    client: TestClient, monkeypatch
) -> None:
    _company_id, project_id = _project(client, "管理 Agent 工具调用", "交付改善")
    observation = client.post(
        f"/api/v3/projects/{project_id}/observations",
        json={
            "kind": "MEETING",
            "title": "计划变更线索",
            "content": "临时插单令排产发生多次变更。",
        },
    )
    assert observation.status_code == 201, observation.text
    profile = client.post(
        "/api/v3/model-profiles",
        json={
            "name": "跨库检索流程测试",
            "provider": "MOCK",
            "base_url": "mock://local",
            "model": "deterministic",
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
            "content": "调查临时插单对排产的影响",
            "model_profile_id": profile.json()["id"],
        },
    )
    assert accepted.status_code == 202, accepted.text
    run_id = accepted.json()["run"]["id"]
    calls = 0

    def fake_invoke(self, _profile, _run, _user_content, context):
        nonlocal calls
        calls += 1
        if calls == 1:
            assert "search_management_observations" in {
                item["key"] for item in context["available_actions"]
            }
            return AgentStructuredOutput(
                content="先查找管理观察。",
                action_proposals=[
                    AgentActionProposal(
                        action_key="search_management_observations",
                        input={"query": "临时插单 排产"},
                    )
                ],
            )
        results = context["agent_execution"]["tool_results"]
        assert results[0]["result"]["resource"] == "MANAGEMENT_OBSERVATIONS"
        assert results[0]["result"]["items"][0]["id"] == observation.json()["id"]
        return AgentStructuredOutput(content="检索到一条同项目观察，并保留其未验证状态。")

    monkeypatch.setattr(AgentRuntimeService, "_invoke_model", fake_invoke)
    completed = client.post(f"/api/v3/projects/{project_id}/agent-runs/{run_id}/execute")
    assert completed.status_code == 200, completed.text
    assert completed.json()["status"] == "COMPLETED"
    action_ids = completed.json()["action_invocation_ids"]
    assert len(action_ids) == 1
    action = client.get(f"/api/v3/projects/{project_id}/action-invocations/{action_ids[0]}")
    assert action.status_code == 200, action.text
    assert action.json()["action_key"] == "search_management_observations"
    assert action.json()["status"] == "SUCCEEDED"


def test_observation_search_can_continue_past_first_scan_window(client: TestClient) -> None:
    company_id, project_id = _project(client, "观察分页公司", "历史线索")
    database = client.app.state.observation_database
    start = datetime(2026, 9, 12, tzinfo=UTC)
    rows = [
        ManagementObservationRow(
            id=str(uuid4()),
            company_id=company_id,
            project_id=project_id,
            kind="MEETING",
            title=("历史窗口目标" if index == 500 else f"普通观察 {index}"),
            content=("需要跨窗口检索的遗留线索" if index == 500 else "常规会议记录"),
            content_sha256="0" * 64,
            occurred_at=None,
            submitted_by="manager",
            status="ACTIVE",
            revision=1,
            idempotency_key=None,
            created_at=start - timedelta(minutes=index),
            updated_at=start - timedelta(minutes=index),
        )
        for index in range(501)
    ]
    with database.session_factory.begin() as session:
        session.add_all(rows)

    first = _execute_read_action(
        client,
        project_id,
        "search_management_observations",
        {"query": "遗留线索", "limit": 20, "scan_offset": 0},
    )
    assert first["scanned"] == 500
    assert first["next_scan_offset"] == 500
    assert first["matching_in_scanned"] == 0
    assert first["truncated"] is True

    second = _execute_read_action(
        client,
        project_id,
        "search_management_observations",
        {"query": "遗留线索", "limit": 20, "scan_offset": first["next_scan_offset"]},
    )
    assert second["scanned"] == 1
    assert second["next_scan_offset"] is None
    assert second["items"][0]["title"] == "历史窗口目标"
    assert second["ranking_scope"] == "CURRENT_SCAN_WINDOW"


def test_potential_search_can_continue_past_first_scan_window(client: TestClient) -> None:
    company_id, project_id = _project(client, "潜在记录分页公司", "历史认识")
    created = client.post(
        f"/api/v3/projects/{project_id}/potential-records",
        json={
            "company_id": company_id,
            "project_id": project_id,
            "potential_type": "PROBLEM_HYPOTHESIS",
            "claim": "历史旧系统接口可能造成重复录入",
            "applicability_scope": "本公司历史接口流程",
            "task_source": "调查历史系统数据",
            "supporting_evidence": [],
            "counterevidence": [],
            "verification_method": "核对历史接口记录",
            "evidence_status": "UNTESTED",
        },
    )
    assert created.status_code == 201, created.text
    target_id = created.json()["id"]
    database = client.app.state.potential_database
    with database.session_factory.begin() as session:
        target = session.get(PotentialRecordRow, target_id)
        assert target is not None
        target.created_at = "2020-01-01T00:00:00.000000Z"
        fillers = [
            PotentialRecordRow(
                id=str(uuid4()),
                company_id=company_id,
                project_id=project_id,
                idempotency_key=None,
                potential_type="PROBLEM_HYPOTHESIS",
                claim=f"普通潜在记录 {index}",
                applicability_scope="测试范围",
                valid_from=None,
                valid_until=None,
                task_source="test",
                supporting_evidence_json="[]",
                counterevidence_json="[]",
                verification_method="测试",
                human_status="ACCEPTED",
                evidence_status="UNTESTED",
                version=1,
                payload_hash="a" * 64,
                created_by="test",
                created_at=f"2026-09-12T00:{index // 60:02d}:{index % 60:02d}.000000Z",
                updated_at="2026-09-12T00:00:00.000000Z",
            )
            for index in range(500)
        ]
        session.add_all(fillers)

    first = _execute_read_action(
        client,
        project_id,
        "search_potential_records",
        {"query": "旧系统接口", "limit": 20, "scan_offset": 0},
    )
    assert first["scanned"] == 500
    assert first["next_scan_offset"] == 500
    assert first["matching_in_scanned"] == 0

    second = _execute_read_action(
        client,
        project_id,
        "search_potential_records",
        {"query": "旧系统接口", "limit": 20, "scan_offset": first["next_scan_offset"]},
    )
    assert second["scanned"] == 1
    assert second["next_scan_offset"] is None
    assert second["items"][0]["id"] == target_id
