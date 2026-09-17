from __future__ import annotations

from fastapi.testclient import TestClient
from sqlalchemy import inspect, select

from enterprise_insight_backend.potential import PotentialAuditRow


def _project(client: TestClient, company_name: str, project_name: str) -> tuple[str, str]:
    company = client.post("/api/v3/companies", json={"name": company_name})
    assert company.status_code == 201, company.text
    project = client.post(
        f"/api/v3/companies/{company.json()['id']}/projects",
        json={"name": project_name},
    )
    assert project.status_code == 201, project.text
    return company.json()["id"], project.json()["id"]


def _payload(company_id: str, project_id: str) -> dict[str, object]:
    return {
        "company_id": company_id,
        "project_id": project_id,
        "potential_type": "PROBLEM_HYPOTHESIS",
        "claim": "临时插单可能造成计划频繁变更",
        "applicability_scope": "订单交付流程；2026 年第三季度",
        "task_source": "管理层要求调查交付延误",
        "supporting_evidence": [
            {
                "source_ref": "observation:meeting-1",
                "excerpt": "过去一个月多次临时调整排产。",
            }
        ],
        "counterevidence": [],
        "verification_method": "对比插单与非插单订单的准时交付率",
        "evidence_status": "UNTESTED",
    }


def test_potential_record_full_lifecycle_is_separate_and_audited(client: TestClient) -> None:
    company_id, project_id = _project(client, "潜在记录公司", "交付流程调查")
    other_company_id, other_project_id = _project(client, "另一家公司", "另一项目")
    path = f"/api/v3/projects/{project_id}/potential-records"

    mismatched_scope = client.post(
        path, json=_payload(other_company_id, project_id)
    )
    assert mismatched_scope.status_code == 422

    created_response = client.post(path, json=_payload(company_id, project_id))
    assert created_response.status_code == 201, created_response.text
    created = created_response.json()
    assert created["human_status"] == "ACCEPTED"
    assert created["evidence_status"] == "UNTESTED"
    assert created["version"] == 1
    record_id = created["id"]

    revised_response = client.patch(
        f"{path}/{record_id}",
        json={
            "expected_version": 1,
            "reason": "补充适用范围",
            "applicability_scope": "订单交付流程；华东工厂；2026 年第三季度",
        },
    )
    assert revised_response.status_code == 200, revised_response.text
    assert revised_response.json()["version"] == 2

    stale_response = client.patch(
        f"{path}/{record_id}",
        json={"expected_version": 1, "claim": "过期内容不能覆盖新版本"},
    )
    assert stale_response.status_code == 409

    rejected_response = client.post(
        f"{path}/{record_id}/reject",
        json={"expected_version": 2, "reason": "检查后暂不认可该假设"},
    )
    assert rejected_response.status_code == 200, rejected_response.text
    rejected = rejected_response.json()
    assert rejected["human_status"] == "REJECTED"
    assert rejected["evidence_status"] == "UNTESTED"
    assert rejected["version"] == 3

    accepted_response = client.post(
        f"{path}/{record_id}/accept",
        json={"expected_version": 3, "reason": "新增访谈材料后重新接受"},
    )
    assert accepted_response.status_code == 200, accepted_response.text
    assert accepted_response.json()["human_status"] == "ACCEPTED"
    assert accepted_response.json()["version"] == 4

    history = client.get(f"{path}/{record_id}/history")
    assert history.status_code == 200, history.text
    assert [item["version"] for item in history.json()["versions"]] == [1, 2, 3, 4]
    assert [item["operation"] for item in history.json()["audit"]] == [
        "CREATED", "EDITED", "REJECTED", "ACCEPTED"
    ]
    assert history.json()["audit"][1]["reason"] == "补充适用范围"
    assert history.json()["audit"][2]["actor_id"] == "local-owner"

    out_of_scope = client.get(
        f"/api/v3/projects/{other_project_id}/potential-records/{record_id}/history"
    )
    assert out_of_scope.status_code == 404

    listed = client.get(path)
    assert listed.status_code == 200
    assert [item["id"] for item in listed.json()["items"]] == [record_id]
    potential_tables = set(inspect(client.app.state.potential_database.engine).get_table_names())
    formal_tables = set(inspect(client.app.state.database.engine).get_table_names())
    assert "potential_records" in potential_tables
    assert "potential_records" not in formal_tables
    assert "companies" in formal_tables
    assert "companies" not in potential_tables
    with client.app.state.potential_database.session_factory() as session:
        audit = session.scalars(
            select(PotentialAuditRow).where(PotentialAuditRow.record_id == record_id)
        ).all()
    assert [item.operation for item in audit] == [
        "CREATED", "EDITED", "REJECTED", "ACCEPTED"
    ]


def test_potential_record_create_is_idempotent_and_rejects_key_reuse(
    client: TestClient,
) -> None:
    company_id, project_id = _project(client, "潜在幂等公司", "潜在幂等项目")
    path = f"/api/v3/projects/{project_id}/potential-records"
    payload = {
        **_payload(company_id, project_id),
        "idempotency_key": "potential-panel-submit-001",
    }
    first = client.post(path, json=payload)
    assert first.status_code == 201, first.text
    repeated = client.post(path, json=payload)
    assert repeated.status_code == 201, repeated.text
    assert repeated.json()["id"] == first.json()["id"]

    conflict = client.post(path, json={**payload, "claim": "不同主张不能复用此标识"})
    assert conflict.status_code == 409, conflict.text
    assert conflict.json()["error"]["code"] == "POTENTIAL_IDEMPOTENCY_CONFLICT"
    history = client.get(f"{path}/{first.json()['id']}/history")
    assert history.status_code == 200
    assert len(history.json()["versions"]) == 1
    assert len(history.json()["audit"]) == 1


def test_withdrawn_potential_is_hidden_by_default_but_remains_in_history(
    client: TestClient,
) -> None:
    company_id, project_id = _project(client, "撤回样本", "撤回测试")
    path = f"/api/v3/projects/{project_id}/potential-records"
    created = client.post(path, json=_payload(company_id, project_id))
    assert created.status_code == 201, created.text
    record_id = created.json()["id"]

    withdrawn = client.post(
        f"{path}/{record_id}/withdraw",
        json={"expected_version": 1, "reason": "管理者撤回，当前不再适用"},
    )
    assert withdrawn.status_code == 200, withdrawn.text
    assert withdrawn.json()["human_status"] == "WITHDRAWN"
    assert client.get(path).json()["total"] == 0
    assert client.get(f"{path}?include_history=true").json()["total"] == 1

    history = client.get(f"{path}/{record_id}/history").json()
    assert history["audit"][-1]["operation"] == "WITHDRAWN"
    assert history["audit"][-1]["reason"] == "管理者撤回，当前不再适用"
