from __future__ import annotations

from uuid import uuid4

from fastapi.testclient import TestClient


def _project(client: TestClient) -> str:
    company = client.post("/api/v3/companies", json={"name": "批量建模企业"}).json()
    project = client.post(
        f"/api/v3/companies/{company['id']}/projects",
        json={"name": "组织投影"},
    ).json()
    client.post(f"/api/v3/projects/{project['id']}/ontology/default-pack").raise_for_status()
    return project["id"]


def _apply(client: TestClient, project_id: str, change_set_id: str) -> dict[str, object]:
    validated = client.post(
        f"/api/v3/projects/{project_id}/change-sets/{change_set_id}/validate"
    )
    assert validated.status_code == 200, validated.text
    assert validated.json()["status"] == "VALID"
    approved = client.post(
        f"/api/v3/projects/{project_id}/change-sets/{change_set_id}/approve"
    )
    assert approved.status_code == 200, approved.text
    applied = client.post(
        f"/api/v3/projects/{project_id}/change-sets/{change_set_id}/apply"
    )
    assert applied.status_code == 200, applied.text
    return applied.json()


def test_change_set_previews_and_atomically_creates_linked_resources(
    client: TestClient,
) -> None:
    project_id = _project(client)
    revision = client.get(f"/api/v3/projects/{project_id}").json()["revision"]
    department_id = uuid4()
    role_id = uuid4()
    relation_id = uuid4()
    operations = [
        {
            "operation_id": str(relation_id),
            "kind": "CREATE_RELATION",
            "payload": {
                "type_key": "contains",
                "name": "销售部包含销售经理",
                "participants": [
                    {
                        "role_key": "container",
                        "entity_id": str(department_id),
                        "ordinal": 0,
                    },
                    {
                        "role_key": "member",
                        "entity_id": str(role_id),
                        "ordinal": 0,
                    },
                ],
            },
        },
        {
            "operation_id": str(role_id),
            "kind": "CREATE_ENTITY",
            "payload": {
                "type_key": "role",
                "stable_key": "role.sales-manager",
                "name": "销售经理",
                "properties": {"purpose": "负责销售结果", "headcount": 1},
            },
        },
        {
            "operation_id": str(department_id),
            "kind": "CREATE_ENTITY",
            "payload": {
                "type_key": "organization_unit",
                "stable_key": "department.sales",
                "name": "销售部",
                "properties": {"mandate": "获取并服务客户"},
            },
        },
    ]
    created = client.post(
        f"/api/v3/projects/{project_id}/change-sets",
        json={
            "title": "建立销售组织",
            "base_revision": revision,
            "operations": operations,
        },
    )
    assert created.status_code == 201, created.text
    change_set_id = created.json()["id"]

    preview = client.get(
        f"/api/v3/projects/{project_id}/change-sets/{change_set_id}/preview"
    )
    assert preview.status_code == 200, preview.text
    assert preview.json()["validation"]["valid"] is True
    assert preview.json()["creates"] == 3
    assert client.post(
        f"/api/v3/projects/{project_id}/graph/query", json={}
    ).json()["entities"] == []

    applied = _apply(client, project_id, change_set_id)
    assert applied["status"] == "APPLIED"
    graph = client.post(f"/api/v3/projects/{project_id}/graph/query", json={}).json()
    assert {item["id"] for item in graph["entities"]} == {
        str(department_id),
        str(role_id),
    }
    assert graph["relations"][0]["id"] == str(relation_id)
    assert {item["entity_id"] for item in graph["relations"][0]["participants"]} == {
        str(department_id),
        str(role_id),
    }

    repeated = client.post(
        f"/api/v3/projects/{project_id}/change-sets/{change_set_id}/apply"
    )
    assert repeated.status_code == 200
    assert repeated.json()["status"] == "APPLIED"
    repeated_graph = client.post(
        f"/api/v3/projects/{project_id}/graph/query", json={}
    ).json()
    assert len(repeated_graph["entities"]) == 2
    assert len(repeated_graph["relations"]) == 1


def test_change_set_rejects_invalid_temporary_endpoint_before_writing(
    client: TestClient,
) -> None:
    project_id = _project(client)
    revision = client.get(f"/api/v3/projects/{project_id}").json()["revision"]
    role_a = uuid4()
    role_b = uuid4()
    created = client.post(
        f"/api/v3/projects/{project_id}/change-sets",
        json={
            "title": "错误职责关系",
            "base_revision": revision,
            "operations": [
                {
                    "operation_id": str(uuid4()),
                    "kind": "CREATE_RELATION",
                    "payload": {
                        "type_key": "holds_responsibility",
                        "participants": [
                            {
                                "role_key": "accountable",
                                "entity_id": str(role_a),
                            },
                            {
                                "role_key": "responsibility",
                                "entity_id": str(role_b),
                            },
                        ],
                    },
                },
                {
                    "operation_id": str(role_a),
                    "kind": "CREATE_ENTITY",
                    "payload": {"type_key": "role", "name": "销售经理"},
                },
                {
                    "operation_id": str(role_b),
                    "kind": "CREATE_ENTITY",
                    "payload": {"type_key": "role", "name": "错误的职责对象"},
                },
            ],
        },
    ).json()
    validation = client.post(
        f"/api/v3/projects/{project_id}/change-sets/{created['id']}/validate"
    )
    assert validation.status_code == 200
    assert validation.json()["status"] == "INVALID"
    assert any(
        item["code"] == "RELATION_PARTICIPANT_TYPE_MISMATCH"
        for item in validation.json()["validation"]["issues"]
    )
    graph = client.post(f"/api/v3/projects/{project_id}/graph/query", json={}).json()
    assert graph["entities"] == []
    assert graph["relations"] == []


def test_approved_change_set_becomes_stale_without_partial_application(
    client: TestClient,
) -> None:
    project_id = _project(client)
    revision = client.get(f"/api/v3/projects/{project_id}").json()["revision"]
    proposed_id = uuid4()
    created = client.post(
        f"/api/v3/projects/{project_id}/change-sets",
        json={
            "title": "建立运营部",
            "base_revision": revision,
            "operations": [
                {
                    "operation_id": str(proposed_id),
                    "kind": "CREATE_ENTITY",
                    "payload": {"type_key": "organization_unit", "name": "运营部"},
                }
            ],
        },
    ).json()
    client.post(
        f"/api/v3/projects/{project_id}/change-sets/{created['id']}/validate"
    ).raise_for_status()
    client.post(
        f"/api/v3/projects/{project_id}/change-sets/{created['id']}/approve"
    ).raise_for_status()
    client.post(
        f"/api/v3/projects/{project_id}/entities",
        json={"type_key": "organization_unit", "name": "临时新增部门"},
    ).raise_for_status()

    stale = client.post(
        f"/api/v3/projects/{project_id}/change-sets/{created['id']}/apply"
    )
    assert stale.status_code == 409
    assert stale.json()["error"]["code"] == "CHANGESET_BECAME_INVALID"
    graph = client.post(f"/api/v3/projects/{project_id}/graph/query", json={}).json()
    assert {item["name"] for item in graph["entities"]} == {"临时新增部门"}
    assert str(proposed_id) not in {item["id"] for item in graph["entities"]}
