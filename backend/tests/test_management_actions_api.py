from __future__ import annotations

from collections.abc import Generator
from pathlib import Path

import pytest
from fastapi.testclient import TestClient

from enterprise_insight_backend.app import create_app
from enterprise_insight_backend.config import Settings


@pytest.fixture
def action_client(tmp_path: Path) -> Generator[TestClient, None, None]:
    settings = Settings(
        data_dir=tmp_path,
        database_url=f"sqlite:///{(tmp_path / 'test.db').as_posix()}",
        local_actor_id="api-test-manager",
        agent_worker_enabled=False,
    )
    with TestClient(create_app(settings)) as test_client:
        yield test_client


def _project(client: TestClient, suffix: str) -> str:
    company = client.post("/api/v3/companies", json={"name": f"管理行动 API 公司-{suffix}"})
    assert company.status_code == 201, company.text
    project = client.post(
        f"/api/v3/companies/{company.json()['id']}/projects",
        json={"name": f"管理行动 API 项目-{suffix}"},
    )
    assert project.status_code == 201, project.text
    return project.json()["id"]


def test_management_action_api_full_lifecycle_and_audit_history(
    action_client: TestClient,
) -> None:
    project_id = _project(action_client, "lifecycle")
    base = f"/api/v3/projects/{project_id}/management/actions"

    created_response = action_client.post(
        base,
        json={
            "title": "复盘订单延期",
            "description": "明确跨部门交接阻塞点",
            "owner": "交付负责人",
            "priority": "HIGH",
            "due_at": "2026-09-30T13:00:00+08:00",
            "reason": "管理层周会决定",
        },
    )
    assert created_response.status_code == 201, created_response.text
    action = created_response.json()
    action_id = action["id"]
    assert action["company_id"]
    assert action["project_id"] == project_id
    assert action["revision"] == 1
    assert action["reported_done"] is False
    assert action["verified_done"] is False

    listed = action_client.get(base, params={"include_cancelled": False})
    assert listed.status_code == 200, listed.text
    assert listed.json()["total"] == 1
    assert listed.json()["items"][0]["id"] == action_id

    item_url = f"{base}/{action_id}"
    fetched = action_client.get(item_url)
    assert fetched.status_code == 200, fetched.text
    assert fetched.json()["title"] == "复盘订单延期"

    updated_response = action_client.patch(
        item_url,
        json={
            "title": "复盘订单延期并制定措施",
            "status": "IN_PROGRESS",
            "due_at": "2026-10-02T09:30:00+08:00",
            "expected_revision": 1,
            "reason": "补充了交付团队时限",
        },
    )
    assert updated_response.status_code == 200, updated_response.text
    updated = updated_response.json()
    assert updated["revision"] == 2
    assert updated["status"] == "IN_PROGRESS"
    assert updated["title"] == "复盘订单延期并制定措施"
    assert updated["due_at"] is not None

    conflict = action_client.patch(
        item_url,
        json={"title": "过期写入", "expected_revision": 1},
    )
    assert conflict.status_code == 409, conflict.text
    assert conflict.json()["error"]["code"] == "REVISION_CONFLICT"

    progress = action_client.post(
        f"{item_url}/progress",
        json={
            "message": "完成销售和交付访谈",
            "details": {"访谈人数": 5},
            "expected_revision": 2,
            "reason": "每周进度更新",
        },
    )
    assert progress.status_code == 201, progress.text
    assert progress.json()["event_type"] == "PROGRESS"
    assert progress.json()["revision"] == 3

    outcome = action_client.post(
        f"{item_url}/outcomes",
        json={
            "message": "发现主要等待发生在需求变更确认",
            "details": {"evidence": "访谈记录"},
            "expected_revision": 3,
        },
    )
    assert outcome.status_code == 201, outcome.text
    assert outcome.json()["event_type"] == "OUTCOME"
    assert outcome.json()["revision"] == 4

    reported_response = action_client.post(
        f"{item_url}/report-done",
        json={
            "message": "复盘报告和改进清单已提交",
            "expected_revision": 4,
            "reason": "执行方报告",
        },
    )
    assert reported_response.status_code == 200, reported_response.text
    reported = reported_response.json()
    assert reported["reported_done"] is True
    assert reported["verified_done"] is False
    assert reported["revision"] == 5

    verified_response = action_client.post(
        f"{item_url}/verify-done",
        json={
            "expected_revision": 5,
            "verification_note": "抽查访谈记录、报告和责任人清单一致",
            "reason": "管理者复核",
        },
    )
    assert verified_response.status_code == 200, verified_response.text
    verified = verified_response.json()
    assert verified["reported_done"] is True
    assert verified["verified_done"] is True
    assert verified["revision"] == 6

    history_response = action_client.get(f"{item_url}/history")
    assert history_response.status_code == 200, history_response.text
    history = history_response.json()
    assert history["total"] == 6
    assert [event["event_type"] for event in history["items"]] == [
        "CREATED",
        "UPDATED",
        "PROGRESS",
        "OUTCOME",
        "DONE_REPORTED",
        "DONE_VERIFIED",
    ]
    assert [event["revision"] for event in history["items"]] == [1, 2, 3, 4, 5, 6]
    assert {event["actor_id"] for event in history["items"]} == {"api-test-manager"}
    assert history["items"][0]["reason"] == "管理层周会决定"
    assert history["items"][1]["reason"] == "补充了交付团队时限"
    assert history["items"][-1]["reason"] == "管理者复核"
    assert history["items"][-1]["message"] == "抽查访谈记录、报告和责任人清单一致"


def test_management_action_create_is_idempotent_and_rejects_key_reuse(
    action_client: TestClient,
) -> None:
    project_id = _project(action_client, "idempotency")
    base = f"/api/v3/projects/{project_id}/management/actions"
    payload = {
        "title": "每周订单风险复核",
        "owner": "交付负责人",
        "idempotency_key": "manager-ui-create-action-001",
    }
    first = action_client.post(base, json=payload)
    assert first.status_code == 201, first.text
    repeated = action_client.post(base, json=payload)
    assert repeated.status_code == 201, repeated.text
    assert repeated.json()["id"] == first.json()["id"]

    conflicting = action_client.post(
        base, json={**payload, "title": "使用同一标识提交不同内容"}
    )
    assert conflicting.status_code == 409, conflicting.text
    assert (
        conflicting.json()["error"]["code"]
        == "MANAGEMENT_ACTION_IDEMPOTENCY_CONFLICT"
    )
    history = action_client.get(f"{base}/{first.json()['id']}/history")
    assert history.status_code == 200
    assert history.json()["total"] == 1
    assert history.json()["items"][0]["event_type"] == "CREATED"


def test_management_action_cannot_be_verified_before_report(
    action_client: TestClient,
) -> None:
    project_id = _project(action_client, "verify-order")
    base = f"/api/v3/projects/{project_id}/management/actions"
    created = action_client.post(base, json={"title": "先报告再核实"})
    assert created.status_code == 201, created.text
    action_id = created.json()["id"]

    response = action_client.post(
        f"{base}/{action_id}/verify-done",
        json={"expected_revision": 1, "verification_note": "通过"},
    )
    assert response.status_code == 409
    assert response.json()["error"]["code"] == "MANAGEMENT_ACTION_NOT_REPORTED_DONE"

    history = action_client.get(f"{base}/{action_id}/history")
    assert history.status_code == 200
    assert [item["event_type"] for item in history.json()["items"]] == ["CREATED"]


def test_management_action_cancel_route_records_audited_status_change(
    action_client: TestClient,
) -> None:
    project_id = _project(action_client, "cancel")
    base = f"/api/v3/projects/{project_id}/management/actions"
    created = action_client.post(base, json={"title": "取消此项管理行动"})
    assert created.status_code == 201, created.text
    action_id = created.json()["id"]

    cancelled = action_client.post(
        f"{base}/{action_id}/cancel",
        json={"expected_revision": 1, "reason": "业务优先级发生变化"},
    )
    assert cancelled.status_code == 200, cancelled.text
    assert cancelled.json()["status"] == "CANCELLED"
    assert cancelled.json()["revision"] == 2

    stale_cancel = action_client.post(
        f"{base}/{action_id}/cancel",
        json={"expected_revision": 1, "reason": "过期请求"},
    )
    assert stale_cancel.status_code == 409
    assert stale_cancel.json()["error"]["code"] == "REVISION_CONFLICT"

    active = action_client.get(base, params={"include_cancelled": False})
    all_actions = action_client.get(base, params={"include_cancelled": True})
    assert active.status_code == 200 and active.json()["total"] == 0
    assert all_actions.status_code == 200 and all_actions.json()["total"] == 1

    history = action_client.get(f"{base}/{action_id}/history")
    assert history.status_code == 200
    assert [item["event_type"] for item in history.json()["items"]] == [
        "CREATED",
        "CANCELLED",
    ]
    assert history.json()["items"][-1]["actor_id"] == "api-test-manager"
    assert history.json()["items"][-1]["reason"] == "业务优先级发生变化"


def test_management_action_routes_hide_records_from_other_projects(
    action_client: TestClient,
) -> None:
    owner_project_id = _project(action_client, "owner")
    other_project_id = _project(action_client, "other")
    owner_base = f"/api/v3/projects/{owner_project_id}/management/actions"
    other_base = f"/api/v3/projects/{other_project_id}/management/actions"
    created = action_client.post(owner_base, json={"title": "不得跨项目访问"})
    assert created.status_code == 201, created.text
    action_id = created.json()["id"]

    other_list = action_client.get(other_base)
    assert other_list.status_code == 200
    assert other_list.json()["total"] == 0

    wrong_project_requests = [
        ("get", f"{other_base}/{action_id}", None),
        (
            "patch",
            f"{other_base}/{action_id}",
            {"title": "越权修改", "expected_revision": 1},
        ),
        (
            "post",
            f"{other_base}/{action_id}/cancel",
            {"expected_revision": 1},
        ),
        (
            "post",
            f"{other_base}/{action_id}/progress",
            {"message": "越权进展", "expected_revision": 1},
        ),
        (
            "post",
            f"{other_base}/{action_id}/outcomes",
            {"message": "越权结果", "expected_revision": 1},
        ),
        (
            "post",
            f"{other_base}/{action_id}/report-done",
            {"message": "越权报告", "expected_revision": 1},
        ),
        (
            "post",
            f"{other_base}/{action_id}/verify-done",
            {"expected_revision": 1, "verification_note": "越权核实"},
        ),
        ("get", f"{other_base}/{action_id}/history", None),
    ]
    for method, url, payload in wrong_project_requests:
        response = action_client.request(method, url, json=payload)
        assert response.status_code == 404, f"{method.upper()} {url}: {response.text}"
        assert response.json()["error"]["code"] == "MANAGEMENT_ACTION_NOT_FOUND"

    owner_history = action_client.get(f"{owner_base}/{action_id}/history")
    assert owner_history.status_code == 200
    assert owner_history.json()["total"] == 1
