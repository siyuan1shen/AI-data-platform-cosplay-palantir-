from __future__ import annotations

import json

from fastapi.testclient import TestClient
from sqlalchemy import inspect, select

from enterprise_insight_backend.observations import (
    ManagementObservationRow,
    ObservationAuditRow,
)


def _project(client: TestClient, name: str) -> str:
    company = client.post("/api/v3/companies", json={"name": f"{name}企业"}).json()
    project = client.post(
        f"/api/v3/companies/{company['id']}/projects", json={"name": f"{name}项目"}
    )
    assert project.status_code == 201, project.text
    return project.json()["id"]


def test_observation_isolated_from_formal_db_and_every_revision_is_audited(
    client: TestClient,
) -> None:
    project_id = _project(client, "观察库")
    response = client.post(
        f"/api/v3/projects/{project_id}/observations",
        json={
            "kind": "MEETING",
            "title": "采购审批情况",
            "content": "上周有三笔采购需要临时上交总经理审批。",
            "idempotency_key": "meeting-entry-0001",
        },
    )
    assert response.status_code == 201, response.text
    created = response.json()
    assert created["revision"] == 1
    assert created["submitted_by"] == "local-owner"

    observation_database = client.app.state.observation_database
    formal_database = client.app.state.database
    with observation_database.session_factory() as session:
        stored = session.get(ManagementObservationRow, created["id"])
        assert stored is not None
        audit = session.scalars(
            select(ObservationAuditRow).where(
                ObservationAuditRow.observation_id == created["id"]
            )
        ).all()
        assert [(item.operation, item.revision) for item in audit] == [("CREATED", 1)]

    formal_tables = set(inspect(formal_database.engine).get_table_names())
    assert "management_observations" not in formal_tables
    assert "management_observation_audit" not in formal_tables

    revised = client.patch(
        f"/api/v3/projects/{project_id}/observations/{created['id']}",
        json={
            "expected_revision": 1,
            "kind": "MEETING",
            "title": "采购审批情况（更正）",
            "content": "上周有两笔采购需要临时上交总经理审批。",
            "occurred_at": None,
        },
    )
    assert revised.status_code == 200, revised.text
    assert revised.json()["revision"] == 2

    history = client.get(
        f"/api/v3/projects/{project_id}/observations/{created['id']}/history"
    )
    assert history.status_code == 200
    assert [item["operation"] for item in history.json()["items"]] == ["CREATED", "REVISED"]
    original_content = history.json()["items"][0]["snapshot"]["content"]
    assert original_content.endswith("三笔采购需要临时上交总经理审批。")


def test_observation_idempotency_revision_conflict_withdrawal_and_project_scope(
    client: TestClient,
) -> None:
    project_id = _project(client, "幂等")
    other_project_id = _project(client, "边界")
    payload = {
        "kind": "INCIDENT",
        "title": "生产线停机",
        "content": "二号生产线停机四十分钟，原因还未确认。",
        "idempotency_key": "incident-entry-0001",
    }
    path = f"/api/v3/projects/{project_id}/observations"
    created = client.post(path, json=payload)
    assert created.status_code == 201, created.text
    repeated = client.post(path, json=payload)
    assert repeated.status_code == 201
    assert repeated.json()["id"] == created.json()["id"]

    conflicting = {**payload, "content": "另一条不同事件。"}
    assert client.post(path, json=conflicting).status_code == 409

    stale = client.patch(
        f"{path}/{created.json()['id']}",
        json={
            "expected_revision": 3,
            "kind": "INCIDENT",
            "title": "过期修改",
            "content": "不能覆盖较新版本。",
        },
    )
    assert stale.status_code == 409

    outside_scope = client.get(
        f"/api/v3/projects/{other_project_id}/observations/{created.json()['id']}/history"
    )
    assert outside_scope.status_code == 404

    withdrawn = client.delete(
        f"{path}/{created.json()['id']}?expected_revision=1"
    )
    assert withdrawn.status_code == 200
    assert withdrawn.json()["status"] == "WITHDRAWN"
    visible = client.get(path)
    assert visible.json()["total"] == 0
    with_history = client.get(f"{path}?include_withdrawn=true")
    assert with_history.json()["total"] == 1


def test_idempotent_datetime_roundtrip_is_stable_on_sqlite(client: TestClient) -> None:
    project_id = _project(client, "时间幂等")
    path = f"/api/v3/projects/{project_id}/observations"
    payload = {
        "kind": "MEETING",
        "title": "带时区会议",
        "content": "同一个带发生时间的提交应保持幂等。",
        "occurred_at": "2026-09-12T09:30:00+08:00",
        "idempotency_key": "meeting-time-entry-1",
    }
    created = client.post(path, json=payload)
    repeated = client.post(path, json=payload)
    assert created.status_code == 201, created.text
    assert repeated.status_code == 201, repeated.text
    assert repeated.json()["id"] == created.json()["id"]


def test_observation_rejects_blank_content_and_unaccepted_fields(client: TestClient) -> None:
    project_id = _project(client, "输入校验")
    path = f"/api/v3/projects/{project_id}/observations"
    blank = client.post(
        path,
        json={"kind": "OTHER", "title": " ", "content": "有空格但无内容"},
    )
    assert blank.status_code == 422
    untrusted_actor = client.post(
        path,
        json={
            "kind": "OTHER",
            "title": "提交人不可自填",
            "content": "服务端应使用当前本机操作者标识。",
            "submitted_by": "spoofed-user",
        },
    )
    assert untrusted_actor.status_code == 422


def test_input_agent_requires_consent_and_only_saves_verifiable_quotes(
    client: TestClient, monkeypatch
) -> None:
    project_id = _project(client, "输入Agent")
    project_path = f"/api/v3/projects/{project_id}/observations"
    created = client.post(
        project_path,
        json={
            "kind": "MEETING",
            "title": "周会",
            "content": "周例会上，生产主管说：本周有两批订单延迟两天。",
        },
    )
    assert created.status_code == 201, created.text
    observation_id = created.json()["id"]
    profile = client.post(
        "/api/v3/model-profiles",
        json={
            "name": "观察输入测试模型",
            "provider": "DEEPSEEK",
            "base_url": "https://example.invalid/v1",
            "model": "test-model",
            "api_key": "test-secret",
        },
    )
    assert profile.status_code == 201, profile.text
    extract_path = f"{project_path}/{observation_id}/extract"
    request = {"model_profile_id": profile.json()["id"]}

    consent_missing = client.post(extract_path, json=request)
    assert consent_missing.status_code == 200
    assert consent_missing.json()["status"] == "FAILED"
    assert consent_missing.json()["error_code"] == "INPUT_AGENT_EXTERNAL_MODEL_CONSENT_REQUIRED"

    quoted_output = {
        "items": [{
            "kind": "EVENT",
            "statement": "生产主管报告本周有两批订单延迟两天。",
            "supporting_quote": "本周有两批订单延迟两天。",
            "speaker": "生产主管",
            "time_expression": "本周",
        }],
        "unresolved": [],
    }

    class FakeResponse:
        def __init__(self, output: dict[str, object]) -> None:
            self.output = output

        def raise_for_status(self) -> None:
            return None

        def json(self) -> dict[str, object]:
            content = json.dumps(self.output, ensure_ascii=False)
            return {"choices": [{"message": {"content": content}}]}

    monkeypatch.setattr(
        "enterprise_insight_backend.input_agent.httpx.post",
        lambda *args, **kwargs: FakeResponse(quoted_output),
    )
    completed = client.post(extract_path, json={**request, "allow_external_model": True})
    assert completed.status_code == 200, completed.text
    assert completed.json()["status"] == "COMPLETED"
    assert completed.json()["items"][0]["supporting_quote"] == "本周有两批订单延迟两天。"

    quoted_output["items"][0]["supporting_quote"] = "不存在于来源里的内容"
    rejected = client.post(extract_path, json={**request, "allow_external_model": True})
    assert rejected.status_code == 200
    assert rejected.json()["status"] == "FAILED"
    assert rejected.json()["error_code"] == "INPUT_AGENT_QUOTE_NOT_IN_SOURCE"

    history = client.get(f"{project_path}/{observation_id}/extractions")
    assert history.status_code == 200
    assert [item["status"] for item in history.json()["items"]] == [
        "FAILED", "COMPLETED", "FAILED"
    ]
    with client.app.state.observation_database.session_factory() as session:
        events = session.scalars(
            select(ObservationAuditRow).where(
                ObservationAuditRow.observation_id == observation_id
            )
        ).all()
    assert [item.operation for item in events].count("EXTRACTION_COMPLETED") == 1


def test_observation_upload_retains_source_and_exposes_parsed_text_in_isolated_store(
    client: TestClient,
) -> None:
    project_id = _project(client, "文件收件箱")
    original = "部门,事项,说明\n采购,审批,两级审批耗时三天\n"
    response = client.post(
        f"/api/v3/projects/{project_id}/observation-ingestions",
        data={"observation_kind": "MEETING", "title": "周会纪要"},
        files={"file": ("meeting.csv", original.encode("utf-8"), "text/csv")},
    )
    assert response.status_code == 201, response.text
    result = response.json()
    assert result["observation"]["title"] == "周会纪要"
    assert "两级审批耗时三天" in result["observation"]["content"]
    assert result["preview_truncated"] is False
    assert result["attachment"]["file_name"] == "meeting.csv"

    project_path = f"/api/v3/projects/{project_id}/observations/{result['observation']['id']}"
    listing = client.get(f"{project_path}/attachments")
    assert listing.status_code == 200
    assert listing.json()["total"] == 1
    download = client.get(f"{project_path}/attachments/{result['attachment']['id']}")
    assert download.status_code == 200
    assert download.content == original.encode("utf-8")
    assert download.headers["x-content-sha256"] == result["attachment"]["content_sha256"]

    observation_tables = set(
        inspect(client.app.state.observation_database.engine).get_table_names()
    )
    formal_tables = set(inspect(client.app.state.database.engine).get_table_names())
    assert "management_observation_attachments" in observation_tables
    assert "management_observation_attachments" not in formal_tables

    repeated = client.post(
        f"/api/v3/projects/{project_id}/observation-ingestions",
        data={"observation_kind": "MEETING", "title": "周会纪要"},
        files={"file": ("meeting.csv", original.encode("utf-8"), "text/csv")},
    )
    assert repeated.status_code == 201
    assert repeated.json()["observation"]["id"] == result["observation"]["id"]
    assert repeated.json()["attachment"]["id"] == result["attachment"]["id"]


def test_observation_upload_rejects_unsupported_file_type_and_foreign_scope(
    client: TestClient,
) -> None:
    project_id = _project(client, "文件边界")
    other_project_id = _project(client, "另一项目")
    unsupported = client.post(
        f"/api/v3/projects/{project_id}/observation-ingestions",
        files={"file": ("record.doc", b"not supported", "application/msword")},
    )
    assert unsupported.status_code == 422

    created = client.post(
        f"/api/v3/projects/{project_id}/observation-ingestions",
        files={"file": ("record.txt", "这是一条记录".encode(), "text/plain")},
    )
    assert created.status_code == 201, created.text
    observation_id = created.json()["observation"]["id"]
    attachment_id = created.json()["attachment"]["id"]
    outside_scope = client.get(
        f"/api/v3/projects/{other_project_id}/observations/{observation_id}/attachments/{attachment_id}"
    )
    assert outside_scope.status_code == 404
