from __future__ import annotations

from uuid import uuid4

from fastapi.testclient import TestClient


def test_scenario_graph_applies_overlay_without_changing_formal_projection(
    client: TestClient,
) -> None:
    company = client.post("/api/v3/companies", json={"name": "方案测试企业"}).json()
    project = client.post(
        f"/api/v3/companies/{company['id']}/projects", json={"name": "组织调整"}
    ).json()
    project_id = project["id"]
    client.post(f"/api/v3/projects/{project_id}/ontology/default-pack").raise_for_status()
    employee = client.post(
        f"/api/v3/projects/{project_id}/entities",
        json={"type_key": "role", "name": "销售专员"},
    ).json()
    manager = client.post(
        f"/api/v3/projects/{project_id}/entities",
        json={"type_key": "role", "name": "销售经理"},
    ).json()
    new_role_id = uuid4()
    relation_id = uuid4()
    scenario = client.post(
        f"/api/v3/projects/{project_id}/scenarios",
        json={
            "name": "增设大客户岗",
            "goal": "比较组织调整后的汇报关系",
            "overlay_operations": [
                {
                    "operation_id": str(uuid4()),
                    "kind": "UPDATE_ENTITY",
                    "target_id": employee["id"],
                    "payload": {
                        "name": "客户成功专员",
                        "expected_revision": employee["revision"],
                    },
                },
                {
                    "operation_id": str(new_role_id),
                    "kind": "CREATE_ENTITY",
                    "payload": {"type_key": "role", "name": "大客户经理"},
                },
                {
                    "operation_id": str(relation_id),
                    "kind": "CREATE_RELATION",
                    "payload": {
                        "type_key": "reports_to",
                        "participants": [
                            {
                                "role_key": "reporter",
                                "entity_id": str(new_role_id),
                                "ordinal": 0,
                            },
                            {
                                "role_key": "manager",
                                "entity_id": manager["id"],
                                "ordinal": 1,
                            },
                        ],
                    },
                },
            ],
        },
    )
    assert scenario.status_code == 201, scenario.text

    scenario_graph = client.post(
        f"/api/v3/projects/{project_id}/graph/query",
        json={"scenario_id": scenario.json()["id"]},
    )
    assert scenario_graph.status_code == 200, scenario_graph.text
    scenario_names = {item["name"] for item in scenario_graph.json()["entities"]}
    assert scenario_names == {"客户成功专员", "销售经理", "大客户经理"}
    assert scenario_graph.json()["relations"][0]["id"] == str(relation_id)

    formal_graph = client.post(f"/api/v3/projects/{project_id}/graph/query", json={}).json()
    assert {item["name"] for item in formal_graph["entities"]} == {"销售专员", "销售经理"}
    assert formal_graph["relations"] == []

    client.patch(
        f"/api/v3/projects/{project_id}/entities/{employee['id']}",
        json={"name": "销售顾问", "expected_revision": employee["revision"]},
    ).raise_for_status()
    stale = client.post(
        f"/api/v3/projects/{project_id}/graph/query",
        json={"scenario_id": scenario.json()["id"]},
    )
    assert stale.status_code == 409
    assert stale.json()["error"]["code"] == "SCENARIO_BASE_STALE"


def test_scenario_compare_rebase_review_and_atomic_apply(client: TestClient) -> None:
    company = client.post("/api/v3/companies", json={"name": "方案闭环企业"}).json()
    project = client.post(
        f"/api/v3/companies/{company['id']}/projects", json={"name": "方案决策"}
    ).json()
    project_id = project["id"]
    client.post(f"/api/v3/projects/{project_id}/ontology/default-pack").raise_for_status()
    role = client.post(
        f"/api/v3/projects/{project_id}/entities",
        json={"type_key": "role", "name": "销售专员"},
    ).json()

    def create_option(name: str) -> dict[str, object]:
        response = client.post(
            f"/api/v3/projects/{project_id}/scenarios",
            json={
                "name": name,
                "goal": "调整销售岗位定位",
                "overlay_operations": [
                    {
                        "operation_id": str(uuid4()),
                        "kind": "UPDATE_ENTITY",
                        "target_id": role["id"],
                        "payload": {
                            "name": name,
                            "expected_revision": role["revision"],
                        },
                    }
                ],
            },
        )
        assert response.status_code == 201, response.text
        return response.json()

    first = create_option("客户成功专员")
    second = create_option("大客户专员")
    diff = client.get(
        f"/api/v3/projects/{project_id}/scenarios/{first['id']}/diff"
    )
    assert diff.status_code == 200, diff.text
    assert len(diff.json()["updates"]) == 1
    assert diff.json()["conflicts"] == []

    compared = client.post(
        f"/api/v3/projects/{project_id}/scenario-comparisons",
        json={"left_scenario_id": first["id"], "right_scenario_id": second["id"]},
    )
    assert compared.status_code == 200, compared.text
    assert len(compared.json()["conflicting_targets"]) == 1

    reviewing = client.patch(
        f"/api/v3/projects/{project_id}/scenarios/{first['id']}",
        json={"status": "UNDER_REVIEW", "expected_revision": first["revision"]},
    ).json()
    approved = client.patch(
        f"/api/v3/projects/{project_id}/scenarios/{first['id']}",
        json={"status": "APPROVED", "expected_revision": reviewing["revision"]},
    ).json()
    applied = client.post(
        f"/api/v3/projects/{project_id}/scenarios/{first['id']}/apply",
        json={"expected_revision": approved["revision"], "requested_by": "management"},
    )
    assert applied.status_code == 200, applied.text
    assert applied.json()["status"] == "ACTIVE"
    assert applied.json()["applied_change_set_id"]
    assert client.get(f"/api/v3/projects/{project_id}/entities/{role['id']}").json()[
        "name"
    ] == "客户成功专员"

    immutable = client.patch(
        f"/api/v3/projects/{project_id}/scenarios/{first['id']}",
        json={"name": "不得修改", "expected_revision": applied.json()["revision"]},
    )
    assert immutable.status_code == 409
    assert immutable.json()["error"]["code"] == "SCENARIO_ALREADY_APPLIED"

    empty = client.post(
        f"/api/v3/projects/{project_id}/scenarios",
        json={"name": "待重基方案", "goal": "验证无冲突重基"},
    ).json()
    client.post(
        f"/api/v3/projects/{project_id}/entities",
        json={"type_key": "role", "name": "无关新增岗位"},
    ).raise_for_status()
    before_rebase = client.get(
        f"/api/v3/projects/{project_id}/scenarios/{empty['id']}/diff"
    ).json()
    assert before_rebase["rebase_required"] is True
    rebased = client.post(
        f"/api/v3/projects/{project_id}/scenarios/{empty['id']}/rebase",
        json={"expected_revision": empty["revision"]},
    )
    assert rebased.status_code == 200, rebased.text
    assert rebased.json()["base_revision"] == before_rebase["current_revision"]
