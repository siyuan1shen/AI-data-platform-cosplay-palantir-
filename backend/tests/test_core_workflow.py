from __future__ import annotations

from fastapi.testclient import TestClient


def test_formal_publication_atomically_freezes_ontology_and_projection(
    client: TestClient,
) -> None:
    company = client.post("/api/v3/companies", json={"name": "原子发布企业"}).json()
    project = client.post(
        f"/api/v3/companies/{company['id']}/projects", json={"name": "原子发布项目"}
    ).json()
    project_id = project["id"]
    entity = client.post(
        f"/api/v3/projects/{project_id}/entities",
        json={
            "type_key": "role",
            "name": "总经理",
            "properties": {"purpose": "承担经营结果"},
            "evidence": [],
        },
    )
    assert entity.status_code == 201, entity.text
    current = client.get(f"/api/v3/projects/{project_id}").json()

    published = client.post(
        f"/api/v3/projects/{project_id}/publications",
        json={
            "label": "管理层基线",
            "expected_project_revision": current["revision"],
        },
    )
    assert published.status_code == 201, published.text
    assert published.json()["entity_count"] == 1
    releases = client.get(f"/api/v3/projects/{project_id}/ontology/releases").json()
    assert releases["total"] == 1
    assert published.json()["ontology_release_id"] == releases["items"][0]["id"]


def test_company_to_published_projection_workflow(client: TestClient) -> None:
    company = client.post(
        "/api/v3/companies", json={"name": "示例制造", "industry": "制造业"}
    ).json()
    project = client.post(
        f"/api/v3/companies/{company['id']}/projects",
        json={"name": "企业整体建模"},
    ).json()
    project_id = project["id"]

    initial_types = client.get(f"/api/v3/projects/{project_id}/ontology/types")
    assert initial_types.status_code == 200
    assert initial_types.json()["total"] >= 10

    installed = client.post(f"/api/v3/projects/{project_id}/ontology/default-pack")
    assert installed.status_code == 200
    assert len(installed.json()) >= 10

    released = client.post(
        f"/api/v3/projects/{project_id}/ontology/releases",
        json={"label": "基础本体"},
    )
    assert released.status_code == 201

    department = client.post(
        f"/api/v3/projects/{project_id}/entities",
        json={
            "type_key": "organization_unit",
            "stable_key": "department.sales",
            "name": "销售部",
            "properties": {"mandate": "获取并服务客户"},
            "viewpoint": "DESIGNED",
            "evidence": [],
        },
    )
    assert department.status_code == 201, department.text
    role = client.post(
        f"/api/v3/projects/{project_id}/entities",
        json={
            "type_key": "role",
            "stable_key": "role.sales_manager",
            "name": "销售经理",
            "properties": {"purpose": "负责销售结果", "headcount": 1},
            "viewpoint": "DESIGNED",
            "evidence": [],
        },
    )
    assert role.status_code == 201, role.text
    relation = client.post(
        f"/api/v3/projects/{project_id}/relations",
        json={
            "type_key": "contains",
            "name": "销售部包含销售经理",
            "participants": [
                {"role_key": "container", "entity_id": department.json()["id"], "ordinal": 0},
                {"role_key": "member", "entity_id": role.json()["id"], "ordinal": 0},
            ],
            "properties": {},
            "viewpoint": "DESIGNED",
            "evidence": [],
        },
    )
    assert relation.status_code == 201, relation.text

    graph = client.post(f"/api/v3/projects/{project_id}/graph/query", json={"depth": 2})
    assert graph.status_code == 200
    assert len(graph.json()["entities"]) == 2
    assert len(graph.json()["relations"]) == 1

    current_project = client.get(f"/api/v3/projects/{project_id}").json()
    publication = client.post(
        f"/api/v3/projects/{project_id}/publications",
        json={
            "label": "第一版企业投影",
            "expected_project_revision": current_project["revision"],
        },
    )
    assert publication.status_code == 201, publication.text

    executive = client.get(f"/api/v3/projects/{project_id}/executive/context")
    assert executive.status_code == 200
    assert executive.json()["publication"]["version"] == 1
    assert len(executive.json()["graph"]["entities"]) == 2


def test_relation_validation_exposes_allowed_roles_for_form_clients(
    client: TestClient,
) -> None:
    company = client.post("/api/v3/companies", json={"name": "关系表单体验样本"}).json()
    project = client.post(
        f"/api/v3/companies/{company['id']}/projects", json={"name": "角色提示"}
    ).json()
    project_id = project["id"]
    client.post(f"/api/v3/projects/{project_id}/ontology/default-pack").raise_for_status()
    parent = client.post(
        f"/api/v3/projects/{project_id}/entities",
        json={"type_key": "organization_unit", "name": "上级组织"},
    )
    child = client.post(
        f"/api/v3/projects/{project_id}/entities",
        json={"type_key": "organization_unit", "name": "下级组织"},
    )
    assert parent.status_code == 201, parent.text
    assert child.status_code == 201, child.text

    invalid = client.post(
        f"/api/v3/projects/{project_id}/relations",
        json={
            "type_key": "contains",
            "participants": [
                {"role_key": "source", "entity_id": parent.json()["id"]},
                {"role_key": "target", "entity_id": child.json()["id"]},
            ],
        },
    )
    assert invalid.status_code == 422, invalid.text
    detail = invalid.json()["error"]["details"][0]
    assert detail["roles"] == ["source", "target"]
    assert detail["allowed_roles"] == [
        {"key": "container", "name": "上级"},
        {"key": "member", "name": "成员"},
    ]


def test_hypothesis_never_enters_trusted_graph(client: TestClient) -> None:
    company = client.post("/api/v3/companies", json={"name": "探索样本"}).json()
    project = client.post(
        f"/api/v3/companies/{company['id']}/projects", json={"name": "管理探索"}
    ).json()
    project_id = project["id"]
    hypothesis = client.post(
        f"/api/v3/projects/{project_id}/hypotheses",
        json={
            "type_key": "informal_influence",
            "title": "可能存在非正式决策中心",
            "summary": "需要管理层进一步确认。",
        },
    )
    assert hypothesis.status_code == 201, hypothesis.text
    graph = client.post(f"/api/v3/projects/{project_id}/graph/query", json={})
    assert graph.status_code == 200
    assert graph.json()["entities"] == []
    assert graph.json()["relations"] == []


def test_graph_filters_and_neighborhood_match_for_draft_and_release(
    client: TestClient,
) -> None:
    company = client.post("/api/v3/companies", json={"name": "图查询样本"}).json()
    project = client.post(
        f"/api/v3/companies/{company['id']}/projects", json={"name": "图查询"}
    ).json()
    project_id = project["id"]
    client.post(f"/api/v3/projects/{project_id}/ontology/default-pack").raise_for_status()
    client.post(
        f"/api/v3/projects/{project_id}/ontology/releases",
        json={"label": "图查询本体"},
    ).raise_for_status()
    entities = []
    for name in ("总部", "销售部", "销售一组"):
        response = client.post(
            f"/api/v3/projects/{project_id}/entities",
            json={
                "type_key": "organization_unit",
                "name": name,
                "properties": {"mandate": f"{name}职责"},
            },
        )
        assert response.status_code == 201, response.text
        entities.append(response.json())
    for parent, child in zip(entities[:-1], entities[1:], strict=True):
        response = client.post(
            f"/api/v3/projects/{project_id}/relations",
            json={
                "type_key": "contains",
                "participants": [
                    {"role_key": "container", "entity_id": parent["id"]},
                    {"role_key": "member", "entity_id": child["id"]},
                ],
            },
        )
        assert response.status_code == 201, response.text

    entity_page = client.get(
        f"/api/v3/projects/{project_id}/entities", params={"limit": 1, "offset": 1}
    ).json()
    assert entity_page["total"] == 3
    assert len(entity_page["items"]) == 1
    relation_page = client.get(
        f"/api/v3/projects/{project_id}/relations", params={"limit": 1, "offset": 1}
    ).json()
    assert relation_page["total"] == 2
    assert len(relation_page["items"]) == 1

    draft = client.post(
        f"/api/v3/projects/{project_id}/graph/query",
        json={"root_entity_id": entities[0]["id"], "depth": 1},
    )
    assert draft.status_code == 200, draft.text
    assert {item["name"] for item in draft.json()["entities"]} == {"总部", "销售部"}
    assert len(draft.json()["relations"]) == 1

    current = client.get(f"/api/v3/projects/{project_id}").json()
    publication = client.post(
        f"/api/v3/projects/{project_id}/publications",
        json={"label": "查询基线", "expected_project_revision": current["revision"]},
    )
    assert publication.status_code == 201, publication.text
    released = client.post(
        f"/api/v3/projects/{project_id}/graph/query",
        json={"release_id": publication.json()["id"], "search": "销售部"},
    )
    assert released.status_code == 200, released.text
    assert [item["name"] for item in released.json()["entities"]] == ["销售部"]
    assert released.json()["relations"] == []
