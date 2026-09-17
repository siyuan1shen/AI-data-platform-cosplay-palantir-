from __future__ import annotations

from uuid import UUID, uuid4

from fastapi.testclient import TestClient


def _project(client: TestClient, name: str) -> str:
    company = client.post("/api/v3/companies", json={"name": f"{name}企业"}).json()
    response = client.post(
        f"/api/v3/companies/{company['id']}/projects", json={"name": f"{name}项目"}
    )
    assert response.status_code == 201, response.text
    return response.json()["id"]


def _observation(client: TestClient, project_id: str, content: str) -> dict[str, str]:
    response = client.post(
        f"/api/v3/projects/{project_id}/observations",
        json={"kind": "INTERVIEW", "title": "岗位访谈", "content": content},
    )
    assert response.status_code == 201, response.text
    return response.json()


def _reported_node_payload(source_ref: str, excerpt: str) -> dict[str, object]:
    return {
        "node_type": "ACTIVITY",
        "label": "整理询价资料",
        "assertions": [
            {
                "field_name": "practice",
                "value": "按现场方式整理询价资料",
                "assertion_kind": "REPORTED",
                "scope": {"department": "采购"},
                "valid_from": "2026-09-01T00:00:00+08:00",
                "evidence": [
                    {
                        "evidence_kind": "INTERVIEW",
                        "source_ref": source_ref,
                        "excerpt": excerpt,
                    }
                ],
            }
        ],
    }


def test_virtual_work_api_hides_drafts_and_records_version_bound_review(
    client: TestClient,
) -> None:
    project_id = _project(client, "岗位现场虚模")
    project = client.get(f"/api/v3/projects/{project_id}").json()
    company_id = project["company_id"]
    base = f"/api/v3/projects/{project_id}/virtual-work/models"
    created_response = client.post(
        base,
        json={
            "company_id": company_id,
            "project_id": project_id,
            "name": "采购岗位现场工作",
            "description": "FDE 现场核对后的岗位实际做法。",
        },
    )
    assert created_response.status_code == 201, created_response.text
    created = created_response.json()
    assert created["status"] == "DRAFT"

    assert client.get(base).json() == []
    hidden = client.get(f"{base}/{created['virtual_work_model_id']}")
    assert hidden.status_code == 404
    assert hidden.json()["error"]["code"] == "VIRTUAL_WORK_MODEL_NOT_REVIEWED"

    draft = client.get(
        f"{base}/{created['virtual_work_model_id']}/revisions/{created['version']}/review"
    )
    assert draft.status_code == 200, draft.text
    assert draft.json()["revision_hash"] == created["revision_hash"]

    scope = {
        "company_id": company_id,
        "project_id": project_id,
        "virtual_work_model_id": created["virtual_work_model_id"],
    }
    review = client.post(
        f"{base}/{created['virtual_work_model_id']}/reviews",
        json={
            "revision_id": created["revision_id"],
            "version": created["version"],
            "revision_hash": created["revision_hash"],
            "confirmed_scope": scope,
            "confirmation_kind": "FDE_VALIDATION",
            "decision": "REVIEWED",
            "reason": "逐项核对岗位身份、现场范围与材料来源。",
        },
    )
    assert review.status_code == 201, review.text
    assert review.json()["actor_id"] == "local-owner"
    assert review.json()["confirmed_scope"] == scope

    visible = client.get(f"{base}/{created['virtual_work_model_id']}")
    assert visible.status_code == 200, visible.text
    assert visible.json()["status"] == "REVIEWED"
    audit = client.get(
        f"{base}/{created['virtual_work_model_id']}/reviews?version=1"
    )
    assert audit.status_code == 200
    assert len(audit.json()) == 1


def test_virtual_work_api_rejects_cross_company_scope_and_review_hash_mismatch(
    client: TestClient,
) -> None:
    project_id = _project(client, "虚模范围校验")
    company = client.post("/api/v3/companies", json={"name": "另一家公司"}).json()
    base = f"/api/v3/projects/{project_id}/virtual-work/models"
    mismatch = client.post(
        base,
        json={
            "company_id": company["id"],
            "project_id": project_id,
            "name": "越界虚模",
        },
    )
    assert mismatch.status_code == 422
    assert mismatch.json()["error"]["code"] == "VIRTUAL_WORK_SCOPE_MISMATCH"

    project = client.get(f"/api/v3/projects/{project_id}").json()
    created = client.post(
        base,
        json={
            "company_id": project["company_id"],
            "project_id": project_id,
            "name": "哈希校验",
        },
    ).json()
    response = client.post(
        f"{base}/{created['virtual_work_model_id']}/reviews",
        json={
            "revision_id": created["revision_id"],
            "version": created["version"],
            "revision_hash": "0" * 64,
            "confirmed_scope": {
                "company_id": project["company_id"],
                "project_id": project_id,
                "virtual_work_model_id": created["virtual_work_model_id"],
            },
            "confirmation_kind": "HUMAN_REVIEW",
            "decision": "REVIEWED",
        },
    )
    assert response.status_code == 409
    assert response.json()["error"]["code"] == "VIRTUAL_WORK_REVIEW_HASH_MISMATCH"
    assert UUID(created["revision_id"])
    assert client.get(f"{base}/{created['virtual_work_model_id']}").status_code == 404


def test_virtual_work_api_rejects_fake_formal_entity_anchor(client: TestClient) -> None:
    project_id = _project(client, "正式锚点验证")
    project = client.get(f"/api/v3/projects/{project_id}").json()
    observation = _observation(
        client, project_id, "员工陈述：采购员先整理询价资料，再交由主管复核。"
    )
    base = f"/api/v3/projects/{project_id}/virtual-work/models"
    model = client.post(
        base,
        json={
            "company_id": project["company_id"],
            "project_id": project_id,
            "name": "不得接受伪锚点",
        },
    ).json()
    node = client.post(
        f"{base}/{model['virtual_work_model_id']}/nodes",
        json={
            **_reported_node_payload(
                f"observation://{observation['id']}/1", "先整理询价资料"
            ),
            "anchors": [
                {
                    "real_entity_id": str(uuid4()),
                    "real_release_id": str(uuid4()),
                    "relation_kind": "REFINES",
                    "support_ref": "formal://fabricated",
                }
            ],
        },
    )
    assert node.status_code == 422, node.text
    assert node.json()["error"]["code"] == "VIRTUAL_WORK_ANCHOR_OUT_OF_SCOPE"
    draft = client.get(
        f"{base}/{model['virtual_work_model_id']}/revisions/1/review"
    )
    assert draft.status_code == 200
    assert draft.json()["version"] == 1
    assert draft.json()["nodes"] == []


def test_virtual_work_api_binds_evidence_to_exact_observation_revision_and_excerpt(
    client: TestClient,
) -> None:
    project_id = _project(client, "虚模证据绑定")
    project = client.get(f"/api/v3/projects/{project_id}").json()
    observation = _observation(
        client,
        project_id,
        "员工陈述：采购员先整理询价资料，再交由主管复核。",
    )
    base = f"/api/v3/projects/{project_id}/virtual-work/models"
    model = client.post(
        base,
        json={
            "company_id": project["company_id"],
            "project_id": project_id,
            "name": "带来源的现场虚模",
        },
    ).json()

    response = client.post(
        f"{base}/{model['virtual_work_model_id']}/nodes",
        json=_reported_node_payload(
            f"observation://{observation['id']}/1", "先整理询价资料，再交由主管复核"
        ),
    )

    assert response.status_code == 201, response.text
    assertion = response.json()["nodes"][0]["assertions"][0]
    assert assertion["scope"] == {"department": "采购"}
    assert assertion["valid_from"] == "2026-08-31T16:00:00Z"
    assert assertion["evidence"][0]["source_root_id"] == observation["id"]
    assert assertion["evidence"][0]["source_ref"] == f"observation://{observation['id']}/1"


def test_virtual_work_api_rejects_unverifiable_evidence_sources(client: TestClient) -> None:
    project_id = _project(client, "虚模证据拒绝")
    project = client.get(f"/api/v3/projects/{project_id}").json()
    observation = _observation(client, project_id, "员工说明：系统中没有记录该项复核。")
    base = f"/api/v3/projects/{project_id}/virtual-work/models"
    model = client.post(
        base,
        json={
            "company_id": project["company_id"],
            "project_id": project_id,
            "name": "来源验证",
        },
    ).json()

    for source_ref, excerpt in (
        ("observation://not-a-uuid/1", "系统中没有记录"),
        (f"observation://{observation['id']}/2", "系统中没有记录"),
        (f"observation://{observation['id']}/1", "原文中不存在的引用"),
        ("file://unverified/path", "系统中没有记录"),
    ):
        response = client.post(
            f"{base}/{model['virtual_work_model_id']}/nodes",
            json=_reported_node_payload(source_ref, excerpt),
        )
        assert response.status_code == 422, response.text
        assert response.json()["error"]["code"] == "VIRTUAL_WORK_EVIDENCE_SOURCE_REJECTED"

    draft = client.get(f"{base}/{model['virtual_work_model_id']}/revisions/1/review")
    assert draft.status_code == 200
    assert draft.json()["version"] == 1
    assert draft.json()["nodes"] == []


def test_virtual_work_api_updates_and_retires_nodes_and_edges_with_revision_preconditions(
    client: TestClient,
) -> None:
    project_id = _project(client, "虚模图编辑API")
    project = client.get(f"/api/v3/projects/{project_id}").json()
    observation = _observation(client, project_id, "先录入，再校验，错误时退回录入。")
    base = f"/api/v3/projects/{project_id}/virtual-work/models"
    model = client.post(
        base,
        json={
            "company_id": project["company_id"],
            "project_id": project_id,
            "name": "可编辑岗位流程",
        },
    ).json()
    model_base = f"{base}/{model['virtual_work_model_id']}"
    first = client.post(
        f"{model_base}/nodes",
        json=_reported_node_payload(
            f"observation://{observation['id']}/1", "先录入，再校验"
        ),
    )
    assert first.status_code == 201, first.text
    first = first.json()
    first_node = first["nodes"][0]

    updated_node = client.put(
        f"{model_base}/nodes/{first_node['id']}",
        json={
            **_reported_node_payload(
                f"observation://{observation['id']}/1", "先录入，再校验"
            ),
            "label": "录入并初步检查",
            "expected_version": first["version"],
            "expected_revision_hash": first["revision_hash"],
            "reason": "访谈核实后修正活动名称。",
        },
    )
    assert updated_node.status_code == 201, updated_node.text
    updated_node = updated_node.json()
    assert updated_node["operation"] == "NODE_UPDATED"
    assert updated_node["nodes"][0]["id"] == first_node["id"]
    assert updated_node["nodes"][0]["label"] == "录入并初步检查"
    stale = client.put(
        f"{model_base}/nodes/{first_node['id']}",
        json={
            **_reported_node_payload(
                f"observation://{observation['id']}/1", "先录入，再校验"
            ),
            "expected_version": first["version"],
            "expected_revision_hash": first["revision_hash"],
            "reason": "过期版本不应覆盖新版本。",
        },
    )
    assert stale.status_code == 409
    assert stale.json()["error"]["code"] == "VIRTUAL_WORK_REVISION_STALE"

    second = client.post(
        f"{model_base}/nodes",
        json=_reported_node_payload(
            f"observation://{observation['id']}/1", "再校验"
        ),
    )
    assert second.status_code == 201, second.text
    second = second.json()
    edge_body = {
        "source_node_id": updated_node["nodes"][0]["id"],
        "target_node_id": second["nodes"][-1]["id"],
        "edge_type": "PRECEDES",
        "label": "正常流程",
        "properties": {},
        "assertions": _reported_node_payload(
            f"observation://{observation['id']}/1", "先录入，再校验"
        )["assertions"],
    }
    created_edge = client.post(f"{model_base}/edges", json=edge_body)
    assert created_edge.status_code == 201, created_edge.text
    created_edge = created_edge.json()
    edge = created_edge["edges"][0]
    updated_edge_response = client.put(
        f"{model_base}/edges/{edge['id']}",
        json={
            **edge_body,
            "source_node_id": edge_body["target_node_id"],
            "target_node_id": edge_body["source_node_id"],
            "label": "返工路径",
            "expected_version": created_edge["version"],
            "expected_revision_hash": created_edge["revision_hash"],
            "reason": "现场发现校验失败会退回录入。",
        },
    )
    assert updated_edge_response.status_code == 201, updated_edge_response.text
    updated_edge = updated_edge_response.json()
    assert updated_edge["operation"] == "EDGE_UPDATED"
    assert updated_edge["edges"][0]["id"] == edge["id"]
    assert updated_edge["edges"][0]["label"] == "返工路径"

    retired_edge_response = client.post(
        f"{model_base}/edges/{edge['id']}/retire",
        json={
            "expected_version": updated_edge["version"],
            "expected_revision_hash": updated_edge["revision_hash"],
            "reason": "该返工关系已停止使用。",
        },
    )
    assert retired_edge_response.status_code == 201, retired_edge_response.text
    retired_edge = retired_edge_response.json()
    assert retired_edge["edges"] == []
    historical_edge = client.get(
        f"{model_base}/revisions/{updated_edge['version']}/review"
    )
    assert historical_edge.status_code == 200
    assert historical_edge.json()["edges"][0]["id"] == edge["id"]

    retired_node_response = client.post(
        f"{model_base}/nodes/{first_node['id']}/retire",
        json={
            "expected_version": retired_edge["version"],
            "expected_revision_hash": retired_edge["revision_hash"],
            "reason": "该活动已从流程中移除。",
        },
    )
    assert retired_node_response.status_code == 201, retired_node_response.text
    retired_node = retired_node_response.json()
    assert all(node["id"] != first_node["id"] for node in retired_node["nodes"])
    assert retired_node["operation"] == "NODE_RETIRED"
