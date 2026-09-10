from __future__ import annotations

from fastapi.testclient import TestClient


def _entity(
    client: TestClient,
    project_id: str,
    type_key: str,
    name: str,
    properties: dict[str, object] | None = None,
) -> dict[str, object]:
    response = client.post(
        f"/api/v3/projects/{project_id}/entities",
        json={"type_key": type_key, "name": name, "properties": properties or {}},
    )
    assert response.status_code == 201, response.text
    return response.json()


def test_semantic_dataset_deduplicates_paths_and_reuses_snapshot(
    client: TestClient,
) -> None:
    company = client.post("/api/v3/companies", json={"name": "语义查询企业"}).json()
    project = client.post(
        f"/api/v3/companies/{company['id']}/projects", json={"name": "经营语义层"}
    ).json()
    project_id = project["id"]
    client.post(f"/api/v3/projects/{project_id}/ontology/default-pack").raise_for_status()
    department = _entity(client, project_id, "organization_unit", "销售部")
    role_a = _entity(client, project_id, "role", "销售经理", {"headcount": 2})
    role_b = _entity(client, project_id, "role", "客户经理", {"headcount": 3})
    relation = client.post(
        f"/api/v3/projects/{project_id}/relations",
        json={
            "type_key": "contains",
            "participants": [
                {"role_key": "container", "entity_id": department["id"]},
                {"role_key": "member", "entity_id": role_a["id"], "ordinal": 1},
                {"role_key": "member", "entity_id": role_b["id"], "ordinal": 2},
            ],
        },
    )
    assert relation.status_code == 201, relation.text
    path = [
        {
            "relation_type_key": "contains",
            "from_role": "container",
            "to_role": "member",
        }
    ]
    dataset = client.post(
        f"/api/v3/projects/{project_id}/semantic-datasets",
        json={
            "key": "department.capacity",
            "name": "部门编制",
            "root_type_key": "organization_unit",
            "columns": [
                {"key": "department", "label": "部门", "property_key": "__name__"},
                {
                    "key": "role_count",
                    "label": "岗位数",
                    "path": path,
                    "property_key": "__id__",
                    "aggregation": "COUNT_DISTINCT",
                },
                {
                    "key": "headcount",
                    "label": "总编制",
                    "path": path,
                    "property_key": "headcount",
                    "aggregation": "SUM",
                },
                {
                    "key": "ambiguous_role",
                    "label": "岗位",
                    "path": path,
                    "property_key": "__name__",
                },
            ],
        },
    )
    assert dataset.status_code == 201, dataset.text
    queried = client.post(
        f"/api/v3/projects/{project_id}/semantic-datasets/{dataset.json()['id']}/query",
        json={},
    )
    assert queried.status_code == 200, queried.text
    result = queried.json()
    assert len(result["rows"]) == 1
    assert result["rows"][0]["department"] == "销售部"
    assert result["rows"][0]["role_count"] == 2
    assert result["rows"][0]["headcount"] == 5
    assert result["rows"][0]["ambiguous_role"] is None
    assert result["warnings"][0]["code"] == "SEMANTIC_CARDINALITY_AMBIGUOUS"

    updated = client.patch(
        f"/api/v3/projects/{project_id}/entities/{role_a['id']}",
        json={"properties": {"headcount": 20}, "expected_revision": role_a["revision"]},
    )
    assert updated.status_code == 200, updated.text
    historical = client.post(
        f"/api/v3/projects/{project_id}/semantic-datasets/{dataset.json()['id']}/query",
        json={"query_snapshot_id": result["query_snapshot_id"]},
    )
    assert historical.status_code == 200, historical.text
    assert historical.json()["rows"][0]["headcount"] == 5

    exported = client.post(
        f"/api/v3/projects/{project_id}/semantic-datasets/{dataset.json()['id']}/export",
        json={"query_snapshot_id": result["query_snapshot_id"], "format": "csv"},
    )
    assert exported.status_code == 201, exported.text
    download = client.get(exported.json()["download_url"])
    assert download.status_code == 200, download.text
    assert "总编制" not in download.text
    assert "headcount" in download.text
    assert "5" in download.text
