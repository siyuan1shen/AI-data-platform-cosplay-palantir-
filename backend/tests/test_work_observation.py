from __future__ import annotations

from datetime import UTC, datetime, timedelta
from uuid import uuid4

import pytest
from fastapi.testclient import TestClient
from pydantic import ValidationError

from enterprise_insight_backend.agent_runtime import AgentRuntimeService
from enterprise_insight_backend.work_observation import WorkObservationPackage


def _create_project(client: TestClient) -> str:
    company = client.post(
        "/api/v3/companies", json={"name": f"工作观察测试企业-{uuid4().hex[:8]}"}
    )
    assert company.status_code == 201, company.text
    project = client.post(
        f"/api/v3/companies/{company.json()['id']}/projects", json={"name": "岗位流程观察"}
    )
    assert project.status_code == 201, project.text
    return project.json()["id"]


def _package(batch_id: str = "batch-001") -> dict:
    base = datetime(2026, 9, 13, 1, 0, tzinfo=UTC)

    def event(
        event_id: str,
        employee: str,
        sequence: int,
        offset: int,
        category: str,
        context: str,
        *,
        state: str = "FOREGROUND",
    ) -> dict:
        return {
            "event_id": event_id,
            "session_id": f"session-{employee}",
            "sequence": sequence,
            "observed_at": (base + timedelta(seconds=offset)).isoformat(),
            "source_employee_key": employee,
            "source_role_key": "sales",
            "activity": {
                "app": "Browser",
                "domain": "crm.example.com",
                "category": category,
                "context": context,
            },
            "state": state,
        }

    return {
        "format_version": "1.0",
        "batch_id": batch_id,
        "source_id": "device-001",
        "events": [
            event("event-a-1", "employee-a", 1, 0, "CRM", "customer-detail"),
            event("event-a-2", "employee-a", 2, 30, "CRM", "order-create"),
            event("event-a-3", "employee-a", 3, 60, "ERP", "order-submit"),
            event("event-b-1", "employee-b", 1, 0, "CRM", "customer-detail"),
            event("event-b-2", "employee-b", 2, 30, "ERP", "order-submit"),
            event("event-b-3", "employee-b", 3, 31, "IDLE", "locked", state="LOCKED"),
        ],
    }


def test_package_rejects_duplicate_event_ids_before_database_write() -> None:
    package = _package()
    package["events"][1]["event_id"] = package["events"][0]["event_id"]

    with pytest.raises(ValidationError, match="event_id 必须唯一"):
        WorkObservationPackage.model_validate(package)


def test_online_import_is_idempotent_and_conflicts_are_explicit(client: TestClient) -> None:
    project_id = _create_project(client)
    package = _package()
    response = client.post(
        "/api/v3/ingestion/work-observation/batches",
        json={"project_id": project_id, "package": package},
    )
    assert response.status_code == 201, response.text
    assert response.json()["accepted_count"] == 6

    duplicate = client.post(
        "/api/v3/ingestion/work-observation/batches",
        json={"project_id": project_id, "package": package},
    )
    assert duplicate.status_code == 201, duplicate.text
    assert duplicate.json()["duplicate"] is True
    assert duplicate.json()["accepted_count"] == 6

    conflict_package = _package()
    conflict_package["events"][0]["activity"]["context"] = "different-context"
    conflict = client.post(
        "/api/v3/ingestion/work-observation/batches",
        json={"project_id": project_id, "package": conflict_package},
    )
    assert conflict.status_code == 409, conflict.text
    assert conflict.json()["error"]["code"] == "WORK_OBSERVATION_BATCH_CONFLICT"


def test_preview_confirm_and_coverage_are_real_frontend_flow_endpoints(client: TestClient) -> None:
    project_id = _create_project(client)
    package = _package("offline-001")
    preview = client.post(
        f"/api/v3/projects/{project_id}/work-observation/imports/preview", json=package
    )
    assert preview.status_code == 201, preview.text
    preview_json = preview.json()
    assert preview_json["event_count"] == 6
    assert preview_json["payload_hash"]
    assert len(preview_json["sample_events"]) == 5
    assert preview_json["employee_keys"] == ["employee-a", "employee-b"]
    assert preview_json["state_counts"]["FOREGROUND"] == 5
    assert preview_json["identity_status"] == "SOURCE_KEYS_UNVERIFIED"
    assert preview_json["warnings"]

    confirmed = client.post(
        f"/api/v3/projects/{project_id}/work-observation/imports/confirm",
        json={"preview_id": preview_json["id"], "payload_hash": preview_json["payload_hash"]},
    )
    assert confirmed.status_code == 200, confirmed.text
    assert confirmed.json()["accepted_count"] == 6

    coverage = client.get(f"/api/v3/projects/{project_id}/work-observation/coverage")
    assert coverage.status_code == 200, coverage.text
    assert coverage.json() == {
        "project_id": project_id,
        "event_count": 6,
        "foreground_event_count": 5,
        "employee_count": 2,
        "session_count": 2,
        "source_count": 1,
        "first_observed_at": "2026-09-13T01:00:00Z",
        "last_observed_at": "2026-09-13T01:01:00Z",
        "batch_count": 1,
    }


def test_analysis_does_not_join_paths_across_employees(client: TestClient) -> None:
    project_id = _create_project(client)
    imported = client.post(
        f"/api/v3/projects/{project_id}/work-observation/batches", json=_package("analysis-001")
    )
    assert imported.status_code == 201, imported.text
    analysis = client.post(
        f"/api/v3/projects/{project_id}/work-observation/analyses",
        json={"gap_seconds": 900},
    )
    assert analysis.status_code == 201, analysis.text
    body = analysis.json()
    assert body["event_count"] == 6
    assert body["employee_count"] == 2
    assert {item["employee_key"] for item in body["result"]["segments"]} == {
        "employee-a",
        "employee-b",
    }
    assert ["CRM:customer-detail", "CRM:order-create", "ERP:order-submit"] in [
        item["path"] for item in body["result"]["employee_paths"]["employee-a"]
    ]
    assert ["CRM:customer-detail", "ERP:order-submit"] in [
        item["path"] for item in body["result"]["employee_paths"]["employee-b"]
    ]

    comparison = client.post(
        f"/api/v3/projects/{project_id}/work-observation/comparisons",
        json={
            "analysis_id": body["id"],
            "left_employee_keys": ["employee-a"],
            "right_employee_keys": ["employee-b"],
        },
    )
    assert comparison.status_code == 201, comparison.text
    comparison_body = comparison.json()["result"]
    assert comparison_body["only_left"]
    assert comparison_body["only_right"]
    assert comparison_body["limitations"]


def test_work_observation_is_project_scoped_and_requires_timezone(client: TestClient) -> None:
    project_id = _create_project(client)
    invalid = _package("invalid-time")
    invalid["events"][0]["observed_at"] = "2026-09-13T01:00:00"
    response = client.post(
        f"/api/v3/projects/{project_id}/work-observation/batches", json=invalid
    )
    assert response.status_code == 422

    missing_project = client.get(
        f"/api/v3/projects/{uuid4()}/work-observation/coverage"
    )
    assert missing_project.status_code == 404


def test_work_observation_action_results_have_stable_source_references() -> None:
    analysis_id = uuid4()
    references = AgentRuntimeService._tool_source_references(
        {
            "status": "SUCCEEDED",
            "result": {
                "resource": "WORK_OBSERVATION",
                "analysis_id": str(analysis_id),
                "segments": [{"segment_id": "segment-001"}],
            },
        }
    )
    assert f"work-observation://analysis/{analysis_id}" in references
    assert "work-observation://segment/segment-001" in references


def test_identity_binding_is_project_scoped_and_changes_preview_status(client: TestClient) -> None:
    project_id = _create_project(client)
    type_key = f"observed_employee_{uuid4().hex[:8]}"
    type_response = client.post(
        f"/api/v3/projects/{project_id}/ontology/types",
        json={"key": type_key, "name": "观察员工", "kind": "OBJECT"},
    )
    assert type_response.status_code == 201, type_response.text
    entity = client.post(
        f"/api/v3/projects/{project_id}/entities",
        json={"type_key": type_key, "stable_key": "employee-a", "name": "测试员工"},
    )
    assert entity.status_code == 201, entity.text
    binding = client.post(
        f"/api/v3/projects/{project_id}/work-observation/identity-bindings",
        json={
            "source_id": "device-001",
            "source_employee_key": "employee-a",
            "formal_entity_id": entity.json()["id"],
            "formal_role_key": "sales",
        },
    )
    assert binding.status_code == 201, binding.text
    preview = client.post(
        f"/api/v3/projects/{project_id}/work-observation/imports/preview",
        json=_package("bound-preview"),
    )
    assert preview.status_code == 201, preview.text
    assert preview.json()["identity_status"] == "PARTIAL"
    assert preview.json()["warnings"]

    history = client.get(
        f"/api/v3/projects/{project_id}/work-observation/identity-bindings/history"
    )
    assert history.status_code == 200, history.text
    assert history.json()["total"] == 1
    assert history.json()["items"][0]["operation"] == "CREATED"

    retired = client.post(
        f"/api/v3/projects/{project_id}/work-observation/identity-bindings/retire",
        json={
            "source_id": "device-001",
            "source_employee_key": "employee-a",
            "reason": "设备标识已经更换。",
        },
    )
    assert retired.status_code == 200, retired.text
    assert retired.json()["status"] == "RETIRED"
    assert client.get(
        f"/api/v3/projects/{project_id}/work-observation/identity-bindings/history"
    ).json()["total"] == 2


def test_observation_candidate_requires_human_decision_before_virtual_work_draft(
    client: TestClient,
) -> None:
    project_id = _create_project(client)
    project = client.get(f"/api/v3/projects/{project_id}").json()
    model = client.post(
        f"/api/v3/projects/{project_id}/virtual-work/models",
        json={
            "company_id": project["company_id"],
            "project_id": project_id,
            "name": "销售岗位观察虚模",
        },
    )
    assert model.status_code == 201, model.text
    imported = client.post(
        f"/api/v3/projects/{project_id}/work-observation/batches",
        json=_package("candidate-001"),
    )
    assert imported.status_code == 201, imported.text
    analysis = client.post(
        f"/api/v3/projects/{project_id}/work-observation/analyses",
        json={"gap_seconds": 900},
    )
    assert analysis.status_code == 201, analysis.text
    analysis_body = analysis.json()
    proposed = client.post(
        f"/api/v3/projects/{project_id}/work-observation/analyses/{analysis_body['id']}/virtual-candidates",
        json={"min_count": 2},
    )
    assert proposed.status_code == 201, proposed.text
    candidates = proposed.json()
    assert candidates
    assert candidates[0]["status"] == "PROPOSED"

    confirmed = client.post(
        f"/api/v3/projects/{project_id}/work-observation/virtual-candidates/{candidates[0]['id']}/decision",
        json={
            "decision": "CONFIRM",
            "reason": "确认该活动作为岗位工作虚模的观察补充。",
            "virtual_work_model_id": model.json()["virtual_work_model_id"],
        },
    )
    assert confirmed.status_code == 200, confirmed.text
    assert confirmed.json()["status"] == "CONFIRMED"
    assert confirmed.json()["virtual_node_id"]
    draft = client.get(
        f"/api/v3/projects/{project_id}/virtual-work/models/{model.json()['virtual_work_model_id']}/revisions/2/review"
    )
    assert draft.status_code == 200, draft.text
    assert draft.json()["nodes"]
    assert draft.json()["nodes"][0]["assertions"][0]["evidence"][0]["source_ref"].startswith(
        "work-observation://analysis/"
    )
