from __future__ import annotations

import json

from fastapi.testclient import TestClient


def _setup(client: TestClient) -> tuple[str, dict, dict]:
    company = client.post("/api/v3/companies", json={"name": "快照测试企业"}).json()
    project = client.post(
        f"/api/v3/companies/{company['id']}/projects", json={"name": "统一查询基线"}
    ).json()
    project_id = project["id"]
    client.post(f"/api/v3/projects/{project_id}/ontology/default-pack").raise_for_status()
    entity = client.post(
        f"/api/v3/projects/{project_id}/entities",
        json={
            "type_key": "role",
            "stable_key": "role.sales",
            "name": "销售负责人",
            "properties": {"headcount": 1},
        },
    ).json()
    source = client.post(
        f"/api/v3/projects/{project_id}/source-systems",
        json={"name": "HR CSV", "kind": "FILE", "connection_profile": {}},
    ).json()
    for field, target, expression in (
        ("id", "__stable_key__", None),
        ("name", "__name__", None),
        ("headcount", "headcount", "int"),
    ):
        mapping = client.post(
            f"/api/v3/projects/{project_id}/semantic-mappings",
            json={
                "source_system_id": source["id"],
                "source_asset": "*",
                "source_field": field,
                "target_type_key": "role",
                "target_property_key": target,
                "transform_expression": expression,
            },
        ).json()
        mapping = client.post(
            f"/api/v3/projects/{project_id}/semantic-mappings/{mapping['id']}/validate",
            json={"expected_revision": mapping["revision"]},
        ).json()
        client.post(
            f"/api/v3/projects/{project_id}/semantic-mappings/{mapping['id']}/approve",
            json={"expected_revision": mapping["revision"]},
        ).raise_for_status()
    return project_id, entity, source


def _import_observation(
    client: TestClient, project_id: str, source_id: str, file_name: str, headcount: int
) -> None:
    preview = client.post(
        f"/api/v3/projects/{project_id}/source-systems/{source_id}/imports/preview",
        files={
            "file": (
                file_name,
                f"id,name,headcount\nrole.sales,系统销售经理,{headcount}\n".encode(),
                "text/csv",
            )
        },
        data={"kind": "CSV"},
    )
    assert preview.status_code == 200, preview.text
    confirmed = client.post(
        f"/api/v3/projects/{project_id}/imports/confirm",
        json={"preview_id": preview.json()["id"]},
    )
    assert confirmed.status_code == 200, confirmed.text


def test_query_snapshot_freezes_model_and_operational_observations(
    client: TestClient,
) -> None:
    project_id, entity, source = _setup(client)
    _import_observation(client, project_id, source["id"], "roles.csv", 2)
    snapshot = client.post(
        f"/api/v3/projects/{project_id}/query-snapshots",
        json={"model_scope": "DRAFT", "include_observations": True},
    )
    assert snapshot.status_code == 201, snapshot.text
    snapshot_id = snapshot.json()["id"]
    assert snapshot.json()["manifest"]["observation_assertion_ids"]

    renamed = client.patch(
        f"/api/v3/projects/{project_id}/entities/{entity['id']}",
        json={"name": "新的设计名称", "expected_revision": entity["revision"]},
    )
    assert renamed.status_code == 200, renamed.text
    _import_observation(client, project_id, source["id"], "roles.csv", 5)

    current = client.get(f"/api/v3/projects/{project_id}/entities/{entity['id']}").json()
    assert current["name"] == "新的设计名称"
    assert current["observed_properties"]["headcount"] == 5
    frozen = client.post(
        f"/api/v3/projects/{project_id}/graph/query",
        json={"query_snapshot_id": snapshot_id},
    )
    assert frozen.status_code == 200, frozen.text
    frozen_entity = frozen.json()["entities"][0]
    assert frozen.json()["query_snapshot_id"] == snapshot_id
    assert frozen_entity["name"] == "销售负责人"
    assert frozen_entity["observed_properties"]["headcount"] == 2

    job = client.post(
        f"/api/v3/projects/{project_id}/exports",
        json={
            "format": "json",
            "include_evidence": False,
            "include_lineage": True,
            "query_snapshot_id": snapshot_id,
        },
    )
    assert job.status_code == 201, job.text
    exported = json.loads(client.get(job.json()["download_url"]).content.decode("utf-8"))
    assert exported["manifest"]["query_snapshot_id"] == snapshot_id
    assert exported["graph"]["entities"][0]["name"] == "销售负责人"
    assert exported["graph"]["entities"][0]["observed_properties"]["headcount"] == 2
    assert len(exported["observation_assertions"]) == 2


def test_agent_run_captures_query_snapshot_when_message_is_accepted(
    client: TestClient,
) -> None:
    project_id, entity, _ = _setup(client)
    profile = client.post(
        "/api/v3/model-profiles",
        json={
            "name": "快照 Mock",
            "provider": "MOCK",
            "base_url": "mock://snapshot",
            "model": "deterministic",
        },
    ).json()
    thread = client.post(
        f"/api/v3/projects/{project_id}/agent-threads",
        json={"agent_kind": "PROJECTION"},
    ).json()
    accepted = client.post(
        f"/api/v3/projects/{project_id}/agent-threads/{thread['id']}/messages",
        json={"content": "解释当前模型", "model_profile_id": profile["id"]},
    ).json()
    snapshot_id = accepted["run"]["context_manifest"]["query_snapshot_id"]
    client.patch(
        f"/api/v3/projects/{project_id}/entities/{entity['id']}",
        json={"name": "消息之后的名称", "expected_revision": entity["revision"]},
    ).raise_for_status()
    frozen = client.post(
        f"/api/v3/projects/{project_id}/graph/query",
        json={"query_snapshot_id": snapshot_id},
    ).json()
    assert frozen["entities"][0]["name"] == "销售负责人"
    completed = client.post(
        f"/api/v3/projects/{project_id}/agent-runs/{accepted['run']['id']}/execute"
    )
    assert completed.status_code == 200, completed.text
    assert completed.json()["context_manifest"]["query_snapshot_id"] == snapshot_id
