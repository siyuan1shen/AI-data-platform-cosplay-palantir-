from __future__ import annotations

from uuid import uuid4

from fastapi.testclient import TestClient


def _project(client: TestClient, *, install_defaults: bool = False) -> str:
    company = client.post("/api/v3/companies", json={"name": "审查回归公司"}).json()
    project = client.post(
        f"/api/v3/companies/{company['id']}/projects", json={"name": "审查回归项目"}
    ).json()
    if install_defaults:
        response = client.post(f"/api/v3/projects/{project['id']}/ontology/default-pack")
        assert response.status_code == 200, response.text
    return project["id"]


def _entity(client: TestClient, project_id: str, type_key: str, name: str) -> dict[str, object]:
    response = client.post(
        f"/api/v3/projects/{project_id}/entities",
        json={"type_key": type_key, "name": name, "properties": {}, "evidence": []},
    )
    assert response.status_code == 201, response.text
    return response.json()


def test_typed_properties_and_inherited_requirements_are_enforced(client: TestClient) -> None:
    project_id = _project(client)
    typed = client.post(
        f"/api/v3/projects/{project_id}/ontology/types",
        json={
            "key": "typed_record",
            "name": "强类型记录",
            "kind": "OBJECT",
            "properties": [
                {"key": "date", "name": "日期", "value_type": "DATE", "required": True},
                {"key": "reference", "name": "引用", "value_type": "UUID", "required": True},
            ],
        },
    )
    assert typed.status_code == 201, typed.text
    malformed = client.post(
        f"/api/v3/projects/{project_id}/entities",
        json={
            "type_key": "typed_record",
            "name": "非法记录",
            "properties": {"date": "not-a-date", "reference": "not-a-uuid"},
        },
    )
    assert malformed.status_code == 422
    assert malformed.json()["error"]["code"] == "PROPERTY_TYPE_MISMATCH"

    parent = client.post(
        f"/api/v3/projects/{project_id}/ontology/types",
        json={
            "key": "parent_record",
            "name": "父记录",
            "kind": "OBJECT",
            "properties": [
                {
                    "key": "required_code",
                    "name": "继承必填编码",
                    "value_type": "STRING",
                    "required": True,
                }
            ],
        },
    )
    assert parent.status_code == 201, parent.text
    child = client.post(
        f"/api/v3/projects/{project_id}/ontology/types",
        json={
            "key": "child_record",
            "name": "子记录",
            "kind": "OBJECT",
            "parent_type_key": "parent_record",
        },
    )
    assert child.status_code == 201, child.text
    missing = client.post(
        f"/api/v3/projects/{project_id}/entities",
        json={"type_key": "child_record", "name": "缺少父类字段", "properties": {}},
    )
    assert missing.status_code == 422
    assert missing.json()["error"]["code"] == "REQUIRED_PROPERTIES_MISSING"


def test_retired_entities_and_fabricated_evidence_cannot_enter_new_facts(
    client: TestClient,
) -> None:
    project_id = _project(client, install_defaults=True)
    retired = _entity(client, project_id, "role", "已退役岗位")
    active = _entity(client, project_id, "role", "现任主管")
    response = client.post(
        f"/api/v3/projects/{project_id}/entities/{retired['id']}/retire",
        json={"expected_revision": retired["revision"]},
    )
    assert response.status_code == 200, response.text
    relation = client.post(
        f"/api/v3/projects/{project_id}/relations",
        json={
            "type_key": "reports_to",
            "participants": [
                {"role_key": "reporter", "entity_id": retired["id"]},
                {"role_key": "manager", "entity_id": active["id"]},
            ],
            "properties": {},
            "evidence": [],
        },
    )
    assert relation.status_code == 422
    assert relation.json()["error"]["code"] == "RELATION_PARTICIPANT_RETIRED"

    metric = client.post(
        f"/api/v3/projects/{project_id}/management/metrics",
        json={"key": "quality.rate", "name": "质量率"},
    )
    assert metric.status_code == 201, metric.text
    observation = client.post(
        f"/api/v3/projects/{project_id}/management/metrics/{metric.json()['id']}/observations",
        json={
            "period_key": "2026-09",
            "value": 98,
            "evidence": [
                {"source_document_id": str(uuid4()), "fragment_id": str(uuid4())}
            ],
        },
    )
    assert observation.status_code == 422
    assert observation.json()["error"]["code"] == "EVIDENCE_REFERENCE_INVALID"


def test_reserved_hypothesis_and_partial_updates_keep_resource_invariants(
    client: TestClient,
) -> None:
    project_id = _project(client)
    generic = client.post(
        f"/api/v3/projects/{project_id}/hypotheses",
        json={
            "type_key": "causal_hypothesis",
            "title": "缺少因果字段",
            "summary": "不能从通用入口创建。",
        },
    )
    assert generic.status_code == 422
    assert generic.json()["error"]["code"] == "RESERVED_HYPOTHESIS_TYPE"
    listed = client.get(f"/api/v3/projects/{project_id}/causal-hypotheses")
    assert listed.status_code == 200, listed.text
    assert listed.json()["total"] == 0

    meeting = client.post(
        f"/api/v3/projects/{project_id}/management/meetings",
        json={"title": "经营会", "occurred_at": "2026-09-06T09:00:00Z"},
    )
    assert meeting.status_code == 201, meeting.text
    clear_title = client.patch(
        f"/api/v3/projects/{project_id}/management/meetings/{meeting.json()['id']}",
        json={"title": None, "expected_revision": meeting.json()["revision"]},
    )
    assert clear_title.status_code == 422
    assert clear_title.json()["error"]["code"] == "MEETING_REQUIRED_FIELD_NULL"

    request = client.post(
        f"/api/v3/projects/{project_id}/management/information-requests",
        json={"title": "确认职责", "question": "谁负责？", "reason": "完成模型"},
    )
    assert request.status_code == 201, request.text
    answered = client.patch(
        f"/api/v3/projects/{project_id}/management/information-requests/{request.json()['id']}",
        json={
            "status": "ANSWERED",
            "answer": "运营负责人",
            "expected_revision": request.json()["revision"],
        },
    )
    assert answered.status_code == 200, answered.text
    cleared = client.patch(
        f"/api/v3/projects/{project_id}/management/information-requests/{request.json()['id']}",
        json={"answer": None, "expected_revision": answered.json()["revision"]},
    )
    assert cleared.status_code == 422
    assert cleared.json()["error"]["code"] == "INFORMATION_REQUEST_ANSWER_REQUIRED"


def test_action_preflight_runs_domain_validation_before_approval(client: TestClient) -> None:
    project_id = _project(client, install_defaults=True)
    definitions = client.get(
        f"/api/v3/projects/{project_id}/action-definitions"
    ).json()["items"]
    create_entity = next(item for item in definitions if item["key"] == "create_entity")
    invocation = client.post(
        f"/api/v3/projects/{project_id}/action-invocations",
        json={
            "action_definition_id": create_entity["id"],
            "idempotency_key": "invalid-entity-preflight",
            "input": {
                "type_key": "nonexistent_type",
                "name": "不应通过预演",
                "properties": {},
            },
        },
    )
    assert invocation.status_code == 201, invocation.text
    dry_run = client.post(
        f"/api/v3/projects/{project_id}/action-invocations/{invocation.json()['id']}/dry-run"
    )
    assert dry_run.status_code == 422
    assert dry_run.json()["error"]["code"] == "ONTOLOGY_TYPE_NOT_FOUND"
    current = client.get(
        f"/api/v3/projects/{project_id}/action-invocations/{invocation.json()['id']}"
    )
    assert current.status_code == 200
    assert current.json()["status"] == "DRAFT"
