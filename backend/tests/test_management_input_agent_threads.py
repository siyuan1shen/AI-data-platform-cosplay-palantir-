from __future__ import annotations

import json

from fastapi.testclient import TestClient
from sqlalchemy import func, select

from enterprise_insight_backend.models import (
    AgentMessageRow,
    AgentRunRow,
    AgentStepRow,
    EntityRow,
    RelationRow,
)
from enterprise_insight_backend.observations import (
    ManagementObservationRow,
    ObservationAuditRow,
)


def _project(client: TestClient) -> str:
    company = client.post("/api/v3/companies", json={"name": "管理输入边界样本"}).json()
    project = client.post(
        f"/api/v3/companies/{company['id']}/projects", json={"name": "日常信息收集"}
    )
    assert project.status_code == 201, project.text
    return project.json()["id"]


def _input_thread(client: TestClient, project_id: str) -> dict[str, object]:
    response = client.post(
        f"/api/v3/projects/{project_id}/agent-threads",
        json={"agent_kind": "MANAGEMENT_INPUT"},
    )
    assert response.status_code == 201, response.text
    thread = response.json()
    assert thread["agent_kind"] == "MANAGEMENT_INPUT"
    return thread


def _profile(client: TestClient) -> str:
    response = client.post(
        "/api/v3/model-profiles",
        json={
            "name": "管理输入测试模型",
            "provider": "DEEPSEEK",
            "base_url": "https://example.invalid/v1",
            "model": "test-model",
            "api_key": "not-a-real-secret",
        },
    )
    assert response.status_code == 201, response.text
    return response.json()["id"]


def _send(
    client: TestClient,
    project_id: str,
    thread_id: str,
    content: str,
    **extra: object,
) -> dict[str, object]:
    response = client.post(
        f"/api/v3/projects/{project_id}/agent-threads/{thread_id}/messages",
        json={"content": content, **extra},
    )
    assert response.status_code == 202, response.text
    return response.json()


def _execute(client: TestClient, project_id: str, run_id: str) -> dict[str, object]:
    response = client.post(
        f"/api/v3/projects/{project_id}/agent-runs/{run_id}/execute"
    )
    assert response.status_code == 200, response.text
    return response.json()


def test_management_input_thread_keeps_raw_message_out_of_formal_store(
    client: TestClient, monkeypatch
) -> None:
    project_id = _project(client)
    thread = _input_thread(client, project_id)
    content = "周会上，采购主管说：缺料信息通常晚半天才到计划员。"

    def external_call_must_not_happen(*args, **kwargs):
        raise AssertionError("未获外发同意时不得调用模型")

    monkeypatch.setattr(
        "enterprise_insight_backend.input_agent.httpx.post", external_call_must_not_happen
    )
    accepted = _send(client, project_id, str(thread["id"]), content)
    message = accepted["message"]
    assert message["content"] == content
    assert message["source_observation_id"]
    assert message["source_revision"] == 1

    completed = _execute(client, project_id, accepted["run"]["id"])
    assert completed["status"] == "COMPLETED"
    assert completed["context_manifest"]["external_model_used"] is False
    assert completed["action_invocation_ids"] == []

    database = client.app.state.database
    with database.session_factory() as session:
        user_messages = session.scalars(
            select(AgentMessageRow).where(AgentMessageRow.thread_id == thread["id"])
        ).all()
        run = session.get(AgentRunRow, accepted["run"]["id"])
        steps = session.scalars(
            select(AgentStepRow).where(AgentStepRow.run_id == accepted["run"]["id"])
        ).all()
        assert all(content not in item.content for item in user_messages)
        assert content not in json.dumps(run.context_manifest, ensure_ascii=False)
        assert all(
            content not in json.dumps(
                {"input": step.input_payload, "output": step.output_payload},
                ensure_ascii=False,
            )
            for step in steps
        )
        formal_counts = (
            session.scalar(select(func.count()).select_from(EntityRow)),
            session.scalar(select(func.count()).select_from(RelationRow)),
        )
    observation_database = client.app.state.observation_database
    with observation_database.session_factory() as session:
        observation = session.get(
            ManagementObservationRow, message["source_observation_id"]
        )
        assert observation is not None
        assert observation.content == content
        assert observation.revision == 1

    messages = client.get(
        f"/api/v3/projects/{project_id}/agent-threads/{thread['id']}/messages"
    )
    assert messages.status_code == 200, messages.text
    assert messages.json()["items"][0]["content"] == content
    assert messages.json()["items"][0]["source_observation_id"] == message[
        "source_observation_id"
    ]
    assert client.get(f"/api/v3/projects/{project_id}/potential-records").json()["total"] == 0
    with database.session_factory() as session:
        assert formal_counts == (
            session.scalar(select(func.count()).select_from(EntityRow)),
            session.scalar(select(func.count()).select_from(RelationRow)),
        )


def test_management_input_idempotency_reuses_one_observation_message_and_run(
    client: TestClient,
) -> None:
    project_id = _project(client)
    thread = _input_thread(client, project_id)
    thread_id = str(thread["id"])
    payload = {
        "content": "仓库组报告，周五的盘点差异要到下周一才能回填。",
        "idempotency_key": "inventory-note-2026-09-13",
    }
    path = f"/api/v3/projects/{project_id}/agent-threads/{thread_id}/messages"
    first = client.post(path, json=payload)
    second = client.post(path, json=payload)
    assert first.status_code == 202, first.text
    assert second.status_code == 202, second.text
    assert second.json()["message"]["id"] == first.json()["message"]["id"]
    assert second.json()["run"]["id"] == first.json()["run"]["id"]
    assert second.json()["message"]["source_observation_id"] == first.json()["message"][
        "source_observation_id"
    ]

    changed_content = client.post(
        path,
        json={**payload, "content": "同一个键不能覆盖成另一条管理观察。"},
    )
    assert changed_content.status_code == 409
    assert changed_content.json()["error"]["code"] == "OBSERVATION_IDEMPOTENCY_CONFLICT"

    with client.app.state.database.session_factory() as session:
        messages = session.scalars(
            select(AgentMessageRow).where(AgentMessageRow.thread_id == thread_id)
        ).all()
        runs = session.scalars(
            select(AgentRunRow).where(AgentRunRow.thread_id == thread_id)
        ).all()
    with client.app.state.observation_database.session_factory() as session:
        observations = session.scalars(
            select(ManagementObservationRow).where(
                ManagementObservationRow.project_id == project_id
            )
        ).all()
    assert len(messages) == len(runs) == len(observations) == 1


def test_management_input_external_extraction_is_traceable_and_human_review_is_audited(
    client: TestClient, monkeypatch
) -> None:
    project_id = _project(client)
    thread = _input_thread(client, project_id)
    profile_id = _profile(client)
    content = "生产主管报告：本周有两批订单延迟两天。"
    model_output = {
        "items": [
            {
                "kind": "EVENT",
                "statement": "生产主管报告本周有两批订单延迟两天。",
                "supporting_quote": "本周有两批订单延迟两天。",
                "speaker": "生产主管",
                "time_expression": "本周",
            }
        ],
        "unresolved": ["延迟的具体订单编号尚未提供。"],
    }
    outbound: list[dict[str, object]] = []

    class FakeResponse:
        def raise_for_status(self) -> None:
            return None

        def json(self) -> dict[str, object]:
            response_content = json.dumps(model_output, ensure_ascii=False)
            return {"choices": [{"message": {"content": response_content}}]}

    def fake_post(url: str, **kwargs):
        outbound.append(kwargs["json"])
        return FakeResponse()

    monkeypatch.setattr("enterprise_insight_backend.input_agent.httpx.post", fake_post)
    forbidden_context_share = client.post(
        f"/api/v3/projects/{project_id}/agent-threads/{thread['id']}/messages",
        json={
            "content": content,
            "model_profile_id": profile_id,
            "allow_external_model": True,
            "share_project_context_with_model": True,
        },
    )
    assert forbidden_context_share.status_code == 422
    accepted = _send(
        client,
        project_id,
        str(thread["id"]),
        content,
        model_profile_id=profile_id,
        allow_external_model=True,
    )
    run = _execute(client, project_id, accepted["run"]["id"])
    assert run["status"] == "COMPLETED", run
    assert run["context_manifest"]["external_model_used"] is True
    assert run["context_manifest"]["result_store"] == "OBSERVATION_STORE"
    assert run["action_invocation_ids"] == []
    assert len(outbound) == 1
    assert outbound[0]["messages"][1]["content"].endswith(content)
    assert "context_shared" not in outbound[0]

    messages = client.get(
        f"/api/v3/projects/{project_id}/agent-threads/{thread['id']}/messages"
    ).json()["items"]
    assert len(messages) == 2
    assert messages[0]["content"] == content
    assert messages[0]["source_observation_id"] == accepted["message"]["source_observation_id"]
    assert "尚未人工确认" in messages[1]["content"]
    assert "原文：本周有两批订单延迟两天。" in messages[1]["content"]
    assert messages[1]["external_model_used"] is True

    database = client.app.state.database
    with database.session_factory() as session:
        stored_messages = session.scalars(
            select(AgentMessageRow).where(AgentMessageRow.thread_id == thread["id"])
        ).all()
        stored_run = session.get(AgentRunRow, accepted["run"]["id"])
        steps = session.scalars(
            select(AgentStepRow).where(AgentStepRow.run_id == accepted["run"]["id"])
        ).all()
        assert all(content not in item.content for item in stored_messages)
        assert content not in json.dumps(stored_run.context_manifest, ensure_ascii=False)
        assert all(
            content not in json.dumps(
                {"input": step.input_payload, "output": step.output_payload},
                ensure_ascii=False,
            )
            for step in steps
        )

    observation_id = accepted["message"]["source_observation_id"]
    observation_path = f"/api/v3/projects/{project_id}/observations/{observation_id}"
    extractions = client.get(f"{observation_path}/extractions").json()["items"]
    assert len(extractions) == 1
    extraction = extractions[0]
    review_path = (
        f"{observation_path}/extractions/{extraction['id']}/review"
    )
    confirmed = client.post(
        review_path,
        json={
            "expected_source_revision": 1,
            "decision": "CONFIRM",
            "reason": "与会议原文核对一致。",
        },
    )
    assert confirmed.status_code == 200, confirmed.text
    assert confirmed.json()["reviewed_by"] == "local-owner"
    assert confirmed.json()["items"][0]["supporting_quote"] == "本周有两批订单延迟两天。"
    revised = client.post(
        review_path,
        json={
            "expected_source_revision": 1,
            "decision": "REVISE",
            "items": [
                {
                    "kind": "EVENT",
                    "statement": "有一批订单延迟。",
                    "supporting_quote": "本周有两批订单延迟两天。",
                    "speaker": "生产主管",
                    "time_expression": "本周",
                }
            ],
            "reason": "人工校准为单批。",
        },
    )
    assert revised.status_code == 200, revised.text
    assert revised.json()["decision"] == "REVISE"
    assert revised.json()["items"][0]["statement"] == "有一批订单延迟。"
    history = client.get(
        f"{observation_path}/extractions/{extraction['id']}/reviews"
    )
    assert history.status_code == 200
    assert [item["decision"] for item in history.json()["items"]] == ["CONFIRM", "REVISE"]

    revised_source = client.patch(
        observation_path,
        json={
            "expected_revision": 1,
            "kind": "OTHER",
            "title": "新的管理输入对话",
            "content": "更正：本周有一批订单延迟两天。",
        },
    )
    assert revised_source.status_code == 200, revised_source.text
    stale_review = client.post(
        review_path,
        json={"expected_source_revision": 1, "decision": "CONFIRM"},
    )
    assert stale_review.status_code == 409
    with client.app.state.observation_database.session_factory() as session:
        audit = session.scalars(
            select(ObservationAuditRow).where(
                ObservationAuditRow.observation_id == observation_id
            )
        ).all()
    operations = [row.operation for row in audit]
    assert "EXTRACTION_COMPLETED" in operations
    assert "EXTRACTION_REVIEW_CONFIRMED" in operations
    assert "EXTRACTION_REVIEW_REVISED" in operations
    assert "REVISED" in operations
