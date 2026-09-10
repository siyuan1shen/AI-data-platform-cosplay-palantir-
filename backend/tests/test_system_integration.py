from __future__ import annotations

import json
import sqlite3
import threading
from http.server import BaseHTTPRequestHandler, ThreadingHTTPServer
from uuid import UUID

from fastapi.testclient import TestClient

from enterprise_insight_backend import connectors as connectors_module
from enterprise_insight_backend import integration as integration_module
from enterprise_insight_backend.agent_runtime import AgentRuntimeService
from enterprise_insight_backend.connectors import ConnectorPage


def _project(client: TestClient) -> str:
    company = client.post("/api/v3/companies", json={"name": "系统接入企业"}).json()
    project = client.post(
        f"/api/v3/companies/{company['id']}/projects", json={"name": "语义对齐"}
    ).json()
    project_id = project["id"]
    assert client.post(f"/api/v3/projects/{project_id}/ontology/default-pack").status_code == 200
    return project_id


def _approved_mapping(
    client: TestClient, project_id: str, payload: dict[str, object]
) -> dict[str, object]:
    created = client.post(
        f"/api/v3/projects/{project_id}/semantic-mappings", json=payload
    )
    assert created.status_code == 201, created.text
    mapping = created.json()
    assert mapping["status"] == "DRAFT"
    validated = client.post(
        f"/api/v3/projects/{project_id}/semantic-mappings/{mapping['id']}/validate",
        json={"expected_revision": mapping["revision"]},
    )
    assert validated.status_code == 200, validated.text
    mapping = validated.json()
    assert mapping["status"] == "VALIDATED"
    approved = client.post(
        f"/api/v3/projects/{project_id}/semantic-mappings/{mapping['id']}/approve",
        json={"expected_revision": mapping["revision"]},
    )
    assert approved.status_code == 200, approved.text
    assert approved.json()["status"] == "APPROVED"
    return approved.json()


def _approved_relation_mapping(
    client: TestClient, project_id: str, payload: dict[str, object]
) -> dict[str, object]:
    created = client.post(
        f"/api/v3/projects/{project_id}/semantic-relation-mappings", json=payload
    )
    assert created.status_code == 201, created.text
    mapping = created.json()
    assert mapping["status"] == "DRAFT"
    validated = client.post(
        f"/api/v3/projects/{project_id}/semantic-relation-mappings/{mapping['id']}/validate",
        json={"expected_revision": mapping["revision"]},
    )
    assert validated.status_code == 200, validated.text
    mapping = validated.json()
    assert mapping["status"] == "VALIDATED"
    approved = client.post(
        f"/api/v3/projects/{project_id}/semantic-relation-mappings/{mapping['id']}/approve",
        json={"expected_revision": mapping["revision"]},
    )
    assert approved.status_code == 200, approved.text
    assert approved.json()["status"] == "APPROVED"
    return approved.json()


def test_file_source_creates_observations_without_polluting_design_graph(
    client: TestClient,
) -> None:
    project_id = _project(client)
    source_response = client.post(
        f"/api/v3/projects/{project_id}/source-systems",
        json={
            "name": "HR 岗位导出",
            "kind": "FILE",
            "description": "人力系统离线导出",
            "connection_profile": {"folder": "D:/private", "password": "never-return-this"},
        },
    )
    assert source_response.status_code == 201, source_response.text
    source = source_response.json()
    assert "connection_profile" not in source
    assert source["configured_fields"] == ["folder", "password"]
    source_id = source["id"]

    mappings = [
        ("岗位编码", "__stable_key__", "strip"),
        ("岗位名称", "__name__", "strip"),
        ("编制人数", "headcount", "strip|int"),
    ]
    for source_field, target_property, expression in mappings:
        _approved_mapping(
            client,
            project_id,
            {
                "source_system_id": source_id,
                "source_asset": "*",
                "source_field": source_field,
                "target_type_key": "role",
                "target_property_key": target_property,
                "transform_expression": expression,
            },
        )

    csv_bytes = "岗位编码,岗位名称,编制人数\nrole.sales,销售经理,2\nrole.ops,运营经理,3\n".encode(
        "utf-8-sig"
    )
    preview_response = client.post(
        f"/api/v3/projects/{project_id}/source-systems/{source_id}/imports/preview",
        files={"file": ("roles.csv", csv_bytes, "text/csv")},
        data={"kind": "CSV"},
    )
    assert preview_response.status_code == 200, preview_response.text
    preview = preview_response.json()
    assert preview["source_system_id"] == source_id

    confirmed = client.post(
        f"/api/v3/projects/{project_id}/imports/confirm",
        json={"preview_id": preview["id"], "options": {}},
    )
    assert confirmed.status_code == 200, confirmed.text
    result = confirmed.json()
    assert result["entities_created"] == 2
    assert result["entities_updated"] == 0
    assert result["identities_bound"] == 0
    assert result["observations_created"] == 4
    assert result["mappings_applied"] == 6

    graph = client.post(f"/api/v3/projects/{project_id}/graph/query", json={}).json()
    assert graph["entities"] == []

    unmodeled = client.get(
        f"/api/v3/projects/{project_id}/entities?include_unmodeled=true"
    ).json()
    assert unmodeled["total"] == 2
    assert {item["design_membership"] for item in unmodeled["items"]} == {"UNMODELED"}
    assert {item["observed_name"] for item in unmodeled["items"]} == {
        "销售经理",
        "运营经理",
    }
    identities = client.get(
        f"/api/v3/projects/{project_id}/source-identities"
    ).json()
    assert identities["total"] == 2
    assert {item["status"] for item in identities["items"]} == {"UNRESOLVED"}
    observations = client.get(
        f"/api/v3/projects/{project_id}/observation-assertions"
    ).json()
    assert observations["total"] == 4

    documents = client.get(f"/api/v3/projects/{project_id}/documents").json()["items"]
    assert documents[0]["source_system_id"] == source_id
    assets = client.get(
        f"/api/v3/projects/{project_id}/source-systems/{source_id}/assets"
    ).json()
    assert assets["total"] == 1
    assert assets["items"][0]["asset_key"] == "roles.csv"
    batches = client.get(f"/api/v3/projects/{project_id}/raw-batches").json()
    assert batches["total"] == 1
    assert batches["items"][0]["record_count"] == 2
    records = client.get(
        f"/api/v3/projects/{project_id}/raw-batches/{batches['items'][0]['id']}/records"
    ).json()
    assert records["total"] == 2
    assert records["items"][0]["payload"]["岗位编码"] == "role.sales"
    paged_records = client.get(
        f"/api/v3/projects/{project_id}/raw-batches/{batches['items'][0]['id']}/records",
        params={"offset": 1, "limit": 1},
    ).json()
    assert paged_records["total"] == 2
    assert len(paged_records["items"]) == 1
    assert paged_records["items"][0]["payload"]["岗位编码"] == "role.ops"
    runs = client.get(f"/api/v3/projects/{project_id}/materialization-runs").json()
    assert runs["total"] == 1
    assert runs["items"][0]["status"] == "COMPLETED"
    assert len(runs["items"][0]["output_entity_ids"]) == 2
    assert len(runs["items"][0]["output_identity_ids"]) == 2
    assert len(runs["items"][0]["output_assertion_ids"]) == 4
    repeated_run = client.post(
        f"/api/v3/projects/{project_id}/raw-batches/{batches['items'][0]['id']}/materialize"
    )
    assert repeated_run.status_code == 200, repeated_run.text
    assert repeated_run.json()["id"] == runs["items"][0]["id"]
    assert client.get(
        f"/api/v3/projects/{project_id}/materialization-runs"
    ).json()["total"] == 1


def test_approved_foreign_key_mapping_materializes_system_relation_without_guessing(
    client: TestClient,
) -> None:
    project_id = _project(client)
    source_response = client.post(
        f"/api/v3/projects/{project_id}/source-systems",
        json={"name": "组织与岗位系统", "kind": "FILE", "connection_profile": {}},
    )
    assert source_response.status_code == 201, source_response.text
    source_id = source_response.json()["id"]

    for asset, target_type in (("departments.csv", "organization_unit"), ("roles.csv", "role")):
        for source_field, target_property in (("id", "__stable_key__"), ("name", "__name__")):
            _approved_mapping(
                client,
                project_id,
                {
                    "source_system_id": source_id,
                    "source_asset": asset,
                    "source_field": source_field,
                    "target_type_key": target_type,
                    "target_property_key": target_property,
                    "transform_expression": "strip",
                },
            )

    relation_mapping = _approved_relation_mapping(
        client,
        project_id,
        {
            "source_system_id": source_id,
            "source_asset": "roles.csv",
            "source_type_key": "role",
            "source_field": "department_id",
            "relation_type_key": "contains",
            "source_role_key": "member",
            "target_type_key": "organization_unit",
            "target_role_key": "container",
            "target_asset": "departments.csv",
            "transform_expression": "strip",
        },
    )

    departments_preview = client.post(
        f"/api/v3/projects/{project_id}/source-systems/{source_id}/imports/preview",
        files={"file": ("departments.csv", "id,name\nd1,销售部\nd2,运营部\n".encode(), "text/csv")},
        data={"kind": "CSV"},
    ).json()
    departments_result = client.post(
        f"/api/v3/projects/{project_id}/imports/confirm",
        json={"preview_id": departments_preview["id"], "options": {}},
    )
    assert departments_result.status_code == 200, departments_result.text

    roles_preview = client.post(
        f"/api/v3/projects/{project_id}/source-systems/{source_id}/imports/preview",
        files={
            "file": (
                "roles.csv",
                "id,name,department_id\nr1,销售经理,d1\nr2,运营经理,missing\n".encode(),
                "text/csv",
            )
        },
        data={"kind": "CSV"},
    ).json()
    roles_result = client.post(
        f"/api/v3/projects/{project_id}/imports/confirm",
        json={"preview_id": roles_preview["id"], "options": {}},
    )
    assert roles_result.status_code == 200, roles_result.text
    assert roles_result.json()["relations_created"] == 1, roles_result.text

    mappings = client.get(
        f"/api/v3/projects/{project_id}/semantic-relation-mappings"
    ).json()
    assert mappings["total"] == 1
    assert mappings["items"][0]["id"] == relation_mapping["id"]
    relations = client.get(f"/api/v3/projects/{project_id}/relations").json()
    assert relations["total"] == 1
    relation = relations["items"][0]
    assert relation["type_key"] == "contains"
    assert relation["viewpoint"] == "SYSTEM_BOUND"
    assert {item["role_key"] for item in relation["participants"]} == {"member", "container"}

    runs = client.get(f"/api/v3/projects/{project_id}/materialization-runs").json()
    role_run = next(item for item in runs["items"] if item["records_processed"] == 2)
    assert role_run["relations_created"] == 1
    assert len(role_run["errors"]) == 1
    assert role_run["errors"][0]["code"] == "SOURCE_RELATION_TARGET_NOT_FOUND"

    changed_roles_preview = client.post(
        f"/api/v3/projects/{project_id}/source-systems/{source_id}/imports/preview",
        files={
            "file": (
                "roles.csv",
                "id,name,department_id\nr1,销售经理,d2\nr2,运营经理,d2\n".encode(),
                "text/csv",
            )
        },
        data={"kind": "CSV"},
    ).json()
    changed_roles_result = client.post(
        f"/api/v3/projects/{project_id}/imports/confirm",
        json={"preview_id": changed_roles_preview["id"], "options": {}},
    )
    assert changed_roles_result.status_code == 200, changed_roles_result.text
    assert changed_roles_result.json()["relations_created"] == 2
    assert client.get(f"/api/v3/projects/{project_id}/relations").json()["total"] == 2


def test_connector_boundaries_and_transform_validation_are_explicit(client: TestClient) -> None:
    project_id = _project(client)
    rest_source = client.post(
        f"/api/v3/projects/{project_id}/source-systems",
        json={"name": "ERP API", "kind": "REST", "connection_profile": {}},
    ).json()
    tested = client.post(f"/api/v3/projects/{project_id}/source-systems/{rest_source['id']}/test")
    assert tested.status_code == 200
    assert tested.json()["ok"] is False
    reconfigured = client.patch(
        f"/api/v3/projects/{project_id}/source-systems/{rest_source['id']}",
        json={
            "connection_profile": {"base_url": "https://erp.invalid/api"},
            "expected_revision": rest_source["revision"],
        },
    )
    assert reconfigured.status_code == 200, reconfigured.text
    assert reconfigured.json()["configured_fields"] == ["base_url"]
    assert reconfigured.json()["status"] == "CONFIGURED"
    stale = client.patch(
        f"/api/v3/projects/{project_id}/source-systems/{rest_source['id']}",
        json={"name": "过期修改", "expected_revision": rest_source["revision"]},
    )
    assert stale.status_code == 409

    preview = client.post(
        f"/api/v3/projects/{project_id}/source-systems/{rest_source['id']}/imports/preview",
        files={"file": ("data.csv", b"id,name\n1,test\n", "text/csv")},
        data={"kind": "CSV"},
    )
    assert preview.status_code == 409
    assert preview.json()["error"]["code"] == "FILE_CONNECTOR_REQUIRED"

    invalid_mapping = client.post(
        f"/api/v3/projects/{project_id}/semantic-mappings",
        json={
            "source_system_id": rest_source["id"],
            "source_asset": "roles",
            "source_field": "id",
            "target_type_key": "role",
            "target_property_key": "__stable_key__",
            "transform_expression": "execute_arbitrary_code",
        },
    )
    assert invalid_mapping.status_code == 422
    assert invalid_mapping.json()["error"]["code"] == "MAPPING_TRANSFORM_UNSUPPORTED"


def test_rest_json_connector_previews_a_read_only_api(client: TestClient) -> None:
    class Handler(BaseHTTPRequestHandler):
        def do_GET(self) -> None:  # noqa: N802 - stdlib handler contract
            from urllib.parse import parse_qs, urlparse

            path = urlparse(self.path).path
            query = parse_qs(urlparse(self.path).query)
            if self.headers.get("Authorization") != "Bearer local-test-token":
                self.send_response(401)
                self.end_headers()
                return
            if path.endswith("/health"):
                response = {"ok": True}
            elif path.endswith("/roles"):
                assert query.get("limit") == ["1"]
                response = {
                    "data": {
                        "items": [
                            {
                                "id": "role.sales",
                                "name": "销售负责人",
                                "updated_at": "2026-09-09T00:00:00Z",
                            },
                            {
                                "id": "role.plan",
                                "name": "计划负责人",
                                "updated_at": "2026-09-09T00:00:01Z",
                            },
                        ]
                    },
                    "meta": {"next": "cursor-2"},
                }
            else:
                self.send_response(404)
                self.end_headers()
                return
            content = json.dumps(response, ensure_ascii=False).encode("utf-8")
            self.send_response(200)
            self.send_header("Content-Type", "application/json")
            self.send_header("Content-Length", str(len(content)))
            self.end_headers()
            self.wfile.write(content)

        def log_message(self, _format: str, *_args: object) -> None:
            return

    server = ThreadingHTTPServer(("127.0.0.1", 0), Handler)
    thread = threading.Thread(target=server.serve_forever, daemon=True)
    thread.start()
    try:
        project_id = _project(client)
        source = client.post(
            f"/api/v3/projects/{project_id}/source-systems",
            json={
                "name": "通用 REST 业务系统",
                "kind": "REST",
                "connection_profile": {
                    "base_url": f"http://127.0.0.1:{server.server_port}/api",
                    "health_path": "health",
                    "rows_path": "data.items",
                    "next_cursor_path": "meta.next",
                    "cursor_param": "cursor",
                    "bearer_token": "local-test-token",
                    "columns": ["id", "name", "updated_at"],
                },
            },
        ).json()
        tested = client.post(
            f"/api/v3/projects/{project_id}/source-systems/{source['id']}/test"
        )
        assert tested.status_code == 200, tested.text
        assert tested.json()["ok"] is True
        preview = client.post(
            f"/api/v3/projects/{project_id}/source-systems/{source['id']}/extract/preview",
            json={
                "asset_key": "roles",
                "limit": 1,
                "watermark_column": "updated_at",
                "tie_breaker_column": "id",
            },
        )
        assert preview.status_code == 200, preview.text
        body = preview.json()
        assert body["columns"] == ["id", "name", "updated_at"]
        assert len(body["rows"]) == 1
        assert body["rows"][0]["id"] == "role.sales"
        assert body["next_tie_breaker"] == "cursor-2"
    finally:
        server.shutdown()
        server.server_close()
        thread.join(timeout=2)


def test_sqlite_connector_previews_and_syncs_a_read_only_local_database(
    client: TestClient, tmp_path
) -> None:
    database_path = tmp_path / "erp.sqlite"
    with sqlite3.connect(database_path) as connection:
        connection.execute(
            "CREATE TABLE roles (id TEXT PRIMARY KEY, name TEXT NOT NULL, updated_at TEXT NOT NULL)"
        )
        connection.executemany(
            "INSERT INTO roles (id, name, updated_at) VALUES (?, ?, ?)",
            [
                ("role.sales", "销售负责人", "2026-09-09T00:00:00Z"),
                ("role.plan", "计划负责人", "2026-09-09T00:00:01Z"),
            ],
        )
        connection.commit()

    project_id = _project(client)
    source = client.post(
        f"/api/v3/projects/{project_id}/source-systems",
        json={
            "name": "本地 ERP SQLite 副本",
            "kind": "SQLITE",
            "connection_profile": {"database_path": str(database_path)},
        },
    ).json()
    tested = client.post(
        f"/api/v3/projects/{project_id}/source-systems/{source['id']}/test"
    )
    assert tested.status_code == 200, tested.text
    assert tested.json()["ok"] is True

    preview = client.post(
        f"/api/v3/projects/{project_id}/source-systems/{source['id']}/extract/preview",
        json={
            "asset_key": "roles",
            "limit": 1,
            "watermark_column": "updated_at",
            "tie_breaker_column": "id",
        },
    )
    assert preview.status_code == 200, preview.text
    body = preview.json()
    assert body["columns"] == ["id", "name", "updated_at"]
    assert body["rows"] == [
        {"id": "role.sales", "name": "销售负责人", "updated_at": "2026-09-09T00:00:00Z"}
    ]
    assert body["next_tie_breaker"] == "role.sales"

    with sqlite3.connect(database_path) as connection:
        connection.execute("UPDATE roles SET name = ? WHERE id = ?", ("已修改源数据", "role.sales"))
        connection.commit()

    synced = client.post(
        f"/api/v3/projects/{project_id}/source-systems/{source['id']}/sync",
        json={"preview_id": body["preview_id"]},
    )
    assert synced.status_code == 200, synced.text
    assert synced.json()["extraction"]["rows"][0]["name"] == "销售负责人"
    assert synced.json()["import_result"]["observations_created"] == 0
    base = f"/api/v3/projects/{project_id}"
    batches = client.get(f"/api/v3/projects/{project_id}/raw-batches").json()["items"]
    records = client.get(
        f"{base}/raw-batches/{batches[0]['id']}/records"
    ).json()["items"]
    assert records[0]["payload"]["name"] == "销售负责人"


def test_postgresql_connector_preview_and_sync_feed_the_raw_layer(
    client: TestClient, monkeypatch
) -> None:
    project_id = _project(client)
    source = client.post(
        f"/api/v3/projects/{project_id}/source-systems",
        json={
            "name": "只读 PostgreSQL",
            "kind": "POSTGRESQL",
            "connection_profile": {
                "host": "database.internal",
                "database": "erp",
                "user": "reader",
                "password": "must-not-be-returned",
            },
        },
    ).json()
    assert "connection_profile" not in source

    extract_calls = 0

    class FakeConnector:
        def test(self) -> tuple[bool, str]:
            return True, "测试连接有效。"

        def extract(self, asset_key: str, **kwargs) -> ConnectorPage:
            nonlocal extract_calls
            extract_calls += 1
            assert asset_key == "public.roles"
            assert kwargs["limit"] == 100
            return ConnectorPage(
                columns=["id", "name", "headcount", "updated_at"],
                rows=[
                    {
                        "id": "role.sales",
                        "name": "销售经理",
                        "headcount": 2,
                        "updated_at": "2026-09-07T08:00:00Z",
                    }
                ],
                next_watermark="2026-09-07T08:00:00Z",
                next_tie_breaker="role.sales",
            )

    monkeypatch.setattr(
        integration_module, "connector_for", lambda _kind, _profile: FakeConnector()
    )
    tested = client.post(
        f"/api/v3/projects/{project_id}/source-systems/{source['id']}/test"
    )
    assert tested.status_code == 200
    assert tested.json()["ok"] is True

    for source_field, target_property in (
        ("id", "__stable_key__"),
        ("name", "__name__"),
        ("headcount", "headcount"),
    ):
        _approved_mapping(
            client,
            project_id,
            {
                "source_system_id": source["id"],
                "source_asset": "public.roles",
                "source_field": source_field,
                "target_type_key": "role",
                "target_property_key": target_property,
            },
        )

    request = {
        "asset_key": "public.roles",
        "limit": 100,
        "watermark_column": "updated_at",
        "tie_breaker_column": "id",
    }
    preview = client.post(
        f"/api/v3/projects/{project_id}/source-systems/{source['id']}/extract/preview",
        json=request,
    )
    assert preview.status_code == 200, preview.text
    assert preview.json()["rows"][0]["name"] == "销售经理"
    assert preview.json()["next_tie_breaker"] == "role.sales"

    assert len(preview.json()["content_sha256"]) == 64

    synced = client.post(
        f"/api/v3/projects/{project_id}/source-systems/{source['id']}/sync",
        json={"preview_id": preview.json()["preview_id"]},
    )
    assert synced.status_code == 200, synced.text
    assert extract_calls == 1
    assert synced.json()["import_result"]["entities_created"] == 1
    assert synced.json()["import_result"]["observations_created"] == 2
    assert client.get(f"/api/v3/projects/{project_id}/raw-batches").json()["total"] == 1
    assert (
        client.get(f"/api/v3/projects/{project_id}/materialization-runs").json()["items"][0][
            "status"
        ]
        == "COMPLETED"
    )
    repeated = client.post(
        f"/api/v3/projects/{project_id}/source-systems/{source['id']}/sync",
        json={"preview_id": preview.json()["preview_id"]},
    )
    assert repeated.status_code == 409
    assert repeated.json()["error"]["code"] == "IMPORT_PREVIEW_CONSUMED"


def test_rest_connector_does_not_invent_cursor_from_business_columns(client: TestClient) -> None:
    requests: list[str] = []

    class Handler(BaseHTTPRequestHandler):
        def do_GET(self) -> None:
            requests.append(self.path)
            content = json.dumps(
                {"data": {"items": [{"id": "role.sales", "updated_at": "2026-09-09"}]}}
            ).encode("utf-8")
            self.send_response(200)
            self.send_header("Content-Type", "application/json")
            self.send_header("Content-Length", str(len(content)))
            self.end_headers()
            self.wfile.write(content)

        def log_message(self, _format: str, *_args: object) -> None:
            return

    server = ThreadingHTTPServer(("127.0.0.1", 0), Handler)
    thread = threading.Thread(target=server.serve_forever, daemon=True)
    thread.start()
    try:
        project_id = _project(client)
        source = client.post(
            f"/api/v3/projects/{project_id}/source-systems",
            json={
                "name": "无游标 REST",
                "kind": "REST",
                "connection_profile": {
                    "base_url": f"http://127.0.0.1:{server.server_port}",
                    "rows_path": "data.items",
                    "watermark_param": "updated_after",
                    "tie_breaker_param": "id_after",
                    "columns": ["id", "updated_at"],
                },
            },
        ).json()
        preview = client.post(
            f"/api/v3/projects/{project_id}/source-systems/{source['id']}/extract/preview",
            json={
                "asset_key": "roles",
                "limit": 1,
                "watermark_column": "updated_at",
                "tie_breaker_column": "id",
            },
        )
        assert preview.status_code == 200, preview.text
        assert preview.json()["next_watermark"] == "2026-09-09"
        assert preview.json()["next_tie_breaker"] == "role.sales"
        continued = client.post(
            f"/api/v3/projects/{project_id}/source-systems/{source['id']}/extract/preview",
            json={
                "asset_key": "roles",
                "limit": 1,
                "watermark_column": "updated_at",
                "tie_breaker_column": "id",
                "after_watermark": "2026-09-09",
                "after_tie_breaker": "role.sales",
            },
        )
        assert continued.status_code == 200, continued.text
        assert "updated_after=2026-09-09" in requests[-1]
        assert "id_after=role.sales" in requests[-1]
    finally:
        server.shutdown()
        server.server_close()
        thread.join(timeout=2)


def test_unsupported_database_connector_is_rejected_before_registration(
    client: TestClient,
) -> None:
    project_id = _project(client)
    response = client.post(
        f"/api/v3/projects/{project_id}/source-systems",
        json={
            "name": "未安装数据库",
            "kind": "MYSQL",
            "connection_profile": {"host": "127.0.0.1"},
        },
    )
    assert response.status_code == 422, response.text
    assert response.json()["error"]["code"] == "CONNECTOR_UNAVAILABLE"


def test_postgresql_connector_retries_only_transient_failures(monkeypatch) -> None:
    class TransientFailure(Exception):
        pass

    class PermanentFailure(Exception):
        pass

    class Cursor:
        def __enter__(self):
            return self

        def __exit__(self, *_args) -> None:
            return None

        def execute(self, *_args) -> None:
            return None

        def fetchone(self) -> tuple[int]:
            return (1,)

    class Connection:
        def __enter__(self):
            return self

        def __exit__(self, *_args) -> None:
            return None

        def cursor(self) -> Cursor:
            return Cursor()

    class Driver:
        OperationalError = TransientFailure
        InterfaceError = type("InterfaceFailure", (Exception,), {})

        def __init__(self, failures: list[Exception]) -> None:
            self.failures = failures
            self.calls = 0

        def connect(self, **_kwargs) -> Connection:
            self.calls += 1
            if self.failures:
                raise self.failures.pop(0)
            return Connection()

    connector = connectors_module.PostgreSQLConnector(
        {
            "host": "localhost",
            "database": "erp",
            "user": "reader",
            "max_retries": 2,
            "retry_backoff_ms": 0,
        }
    )
    transient_driver = Driver([TransientFailure("temporary")])
    monkeypatch.setattr(connector, "_driver", lambda: (transient_driver, object()))
    assert connector.test()[0] is True
    assert transient_driver.calls == 2

    permanent_driver = Driver([PermanentFailure("bad query")])
    monkeypatch.setattr(connector, "_driver", lambda: (permanent_driver, object()))
    ok, message = connector.test()
    assert ok is False
    assert "PermanentFailure" in message
    assert permanent_driver.calls == 1

    class AuthenticationFailure(TransientFailure):
        sqlstate = "28P01"

    authentication_driver = Driver([AuthenticationFailure("invalid password")])
    monkeypatch.setattr(connector, "_driver", lambda: (authentication_driver, object()))
    assert connector.test()[0] is False
    assert authentication_driver.calls == 1


def test_only_validated_and_approved_mapping_can_materialize(client: TestClient) -> None:
    project_id = _project(client)
    source = client.post(
        f"/api/v3/projects/{project_id}/source-systems",
        json={"name": "审批测试源", "kind": "FILE", "connection_profile": {}},
    ).json()
    created = client.post(
        f"/api/v3/projects/{project_id}/semantic-mappings",
        json={
            "source_system_id": source["id"],
            "source_asset": "*",
            "source_field": "id",
            "target_type_key": "role",
            "target_property_key": "__stable_key__",
        },
    ).json()
    assert created["status"] == "DRAFT"

    csv_bytes = b"id\nrole.approval\n"
    preview = client.post(
        f"/api/v3/projects/{project_id}/source-systems/{source['id']}/imports/preview",
        files={"file": ("approval.csv", csv_bytes, "text/csv")},
        data={"kind": "CSV"},
    ).json()
    draft_result = client.post(
        f"/api/v3/projects/{project_id}/imports/confirm",
        json={"preview_id": preview["id"]},
    ).json()
    assert draft_result["mappings_applied"] == 0
    assert draft_result["entities_created"] == 0

    premature = client.post(
        f"/api/v3/projects/{project_id}/semantic-mappings/{created['id']}/approve",
        json={"expected_revision": created["revision"]},
    )
    assert premature.status_code == 409
    assert premature.json()["error"]["code"] == "MAPPING_VALIDATION_REQUIRED"
    validated = client.post(
        f"/api/v3/projects/{project_id}/semantic-mappings/{created['id']}/validate",
        json={"expected_revision": created["revision"]},
    ).json()
    stale = client.post(
        f"/api/v3/projects/{project_id}/semantic-mappings/{created['id']}/approve",
        json={"expected_revision": created["revision"]},
    )
    assert stale.status_code == 409
    approved = client.post(
        f"/api/v3/projects/{project_id}/semantic-mappings/{created['id']}/approve",
        json={"expected_revision": validated["revision"]},
    ).json()
    assert approved["status"] == "APPROVED"

    preview = client.post(
        f"/api/v3/projects/{project_id}/source-systems/{source['id']}/imports/preview",
        files={"file": ("approval.csv", csv_bytes, "text/csv")},
        data={"kind": "CSV"},
    ).json()
    approved_result = client.post(
        f"/api/v3/projects/{project_id}/imports/confirm",
        json={"preview_id": preview["id"]},
    ).json()
    assert approved_result["entities_created"] == 1
    assert approved_result["mappings_applied"] == 1


def test_same_raw_file_can_be_reprocessed_after_mapping_is_added(client: TestClient) -> None:
    project_id = _project(client)
    source = client.post(
        f"/api/v3/projects/{project_id}/source-systems",
        json={"name": "待映射文件", "kind": "FILE", "connection_profile": {}},
    ).json()
    csv_bytes = "id,name\nrole.sales,销售经理\n".encode("utf-8-sig")

    first_preview = client.post(
        f"/api/v3/projects/{project_id}/source-systems/{source['id']}/imports/preview",
        files={"file": ("roles.csv", csv_bytes, "text/csv")},
        data={"kind": "CSV"},
    ).json()
    first_result = client.post(
        f"/api/v3/projects/{project_id}/imports/confirm",
        json={"preview_id": first_preview["id"], "options": {}},
    ).json()
    assert first_result["entities_created"] == 0

    approved_mappings = []
    for source_field, target_property in (("id", "__stable_key__"), ("name", "__name__")):
        approved_mappings.append(_approved_mapping(
            client,
            project_id,
            {
                "source_system_id": source["id"],
                "source_asset": "*",
                "source_field": source_field,
                "target_type_key": "role",
                "target_property_key": target_property,
            },
        ))

    second_preview = client.post(
        f"/api/v3/projects/{project_id}/source-systems/{source['id']}/imports/preview",
        files={"file": ("roles.csv", csv_bytes, "text/csv")},
        data={"kind": "CSV"},
    ).json()
    second_result = client.post(
        f"/api/v3/projects/{project_id}/imports/confirm",
        json={"preview_id": second_preview["id"], "options": {}},
    ).json()
    assert second_result["status"] == "REPROCESSED"
    assert second_result["entities_created"] == 1

    third_preview = client.post(
        f"/api/v3/projects/{project_id}/source-systems/{source['id']}/imports/preview",
        files={"file": ("roles.csv", csv_bytes, "text/csv")},
        data={"kind": "CSV"},
    ).json()
    third_result = client.post(
        f"/api/v3/projects/{project_id}/imports/confirm",
        json={"preview_id": third_preview["id"], "options": {}},
    ).json()
    assert third_result["entities_created"] == 0
    assert third_result["entities_updated"] == 0
    assert third_result["observations_created"] == 0
    assert client.get(f"/api/v3/projects/{project_id}/entities").json()["total"] == 0
    assert (
        client.get(
            f"/api/v3/projects/{project_id}/entities?include_unmodeled=true"
        ).json()["total"]
        == 1
    )

    old_name_mapping = approved_mappings[1]
    disabled = client.post(
        f"/api/v3/projects/{project_id}/semantic-mappings/{old_name_mapping['id']}/disable",
        json={"expected_revision": old_name_mapping["revision"]},
    )
    assert disabled.status_code == 200, disabled.text
    _approved_mapping(
        client,
        project_id,
        {
            "source_system_id": source["id"],
            "source_asset": "*",
            "source_field": "name",
            "target_type_key": "role",
            "target_property_key": "__name__",
            "transform_expression": "upper",
        },
    )
    raw_batch = client.get(f"/api/v3/projects/{project_id}/raw-batches").json()[
        "items"
    ][0]
    rerun = client.post(
        f"/api/v3/projects/{project_id}/raw-batches/{raw_batch['id']}/materialize"
    )
    assert rerun.status_code == 200, rerun.text
    assert client.get(
        f"/api/v3/projects/{project_id}/materialization-runs"
    ).json()["total"] == 3
    observations = client.get(
        f"/api/v3/projects/{project_id}/observation-assertions"
    ).json()["items"]
    name_observations = [item for item in observations if item["field_key"] == "__name__"]
    assert len(name_observations) == 2
    assert {item["status"] for item in name_observations} == {"ACTIVE", "SUPERSEDED"}
    assert max(item["version"] for item in name_observations) == 2
    assert all(item["raw_record_id"] for item in name_observations)
    assert all(item["materialization_run_id"] for item in name_observations)


def test_exact_identity_binding_preserves_design_and_exposes_divergence(
    client: TestClient,
) -> None:
    project_id = _project(client)
    designed = client.post(
        f"/api/v3/projects/{project_id}/entities",
        json={
            "type_key": "role",
            "stable_key": "role.sales",
            "name": "销售负责人",
            "properties": {"purpose": "负责商业结果", "headcount": 1},
        },
    ).json()
    source = client.post(
        f"/api/v3/projects/{project_id}/source-systems",
        json={"name": "HR 导出", "kind": "FILE", "connection_profile": {}},
    ).json()
    for source_field, target_property, expression in (
        ("id", "__stable_key__", None),
        ("name", "__name__", None),
        ("headcount", "headcount", "int"),
    ):
        _approved_mapping(
            client,
            project_id,
            {
                "source_system_id": source["id"],
                "source_asset": "*",
                "source_field": source_field,
                "target_type_key": "role",
                "target_property_key": target_property,
                "transform_expression": expression,
            },
        )
    preview = client.post(
        f"/api/v3/projects/{project_id}/source-systems/{source['id']}/imports/preview",
        files={
            "file": (
                "roles.csv",
                "id,name,headcount\nrole.sales,系统里的销售经理,3\n".encode("utf-8-sig"),
                "text/csv",
            )
        },
        data={"kind": "CSV"},
    ).json()
    result = client.post(
        f"/api/v3/projects/{project_id}/imports/confirm",
        json={"preview_id": preview["id"]},
    ).json()
    assert result["entities_created"] == 0
    assert result["identities_bound"] == 1
    assert result["observations_created"] == 2

    entity = client.get(
        f"/api/v3/projects/{project_id}/entities/{designed['id']}"
    ).json()
    assert entity["name"] == "销售负责人"
    assert entity["properties"] == {"purpose": "负责商业结果", "headcount": 1}
    assert entity["viewpoint"] == "DESIGNED"
    assert entity["observed_name"] == "系统里的销售经理"
    assert entity["observed_properties"]["headcount"] == 3
    assert entity["comparison"]["headcount"]["status"] == "DIVERGED"

    client.post(
        f"/api/v3/projects/{project_id}/ontology/releases",
        json={"label": "设计本体"},
    ).raise_for_status()
    project = client.get(f"/api/v3/projects/{project_id}").json()
    client.post(
        f"/api/v3/projects/{project_id}/publications",
        json={
            "label": "设计基线",
            "expected_project_revision": project["revision"],
        },
    ).raise_for_status()
    executive_entity = client.get(
        f"/api/v3/projects/{project_id}/executive/context"
    ).json()["graph"]["entities"][0]
    assert executive_entity["name"] == "销售负责人"
    assert executive_entity["observed_name"] is None
    assert executive_entity["comparison"] == {}
    preview_entity = client.get(
        f"/api/v3/projects/{project_id}/executive/context?preview=true"
    ).json()["graph"]["entities"][0]
    assert preview_entity["observed_name"] == "系统里的销售经理"

    export = client.post(
        f"/api/v3/projects/{project_id}/exports",
        json={"format": "json", "include_evidence": False, "include_lineage": True},
    )
    assert export.status_code == 201, export.text
    bundle = json.loads(client.get(export.json()["download_url"]).content.decode("utf-8"))
    assert len(bundle["source_identities"]) == 1
    assert len(bundle["observation_assertions"]) == 2

    thread = client.post(
        f"/api/v3/projects/{project_id}/agent-threads",
        json={"agent_kind": "SYSTEM_ONTOLOGY"},
    ).json()
    with client.app.state.database.session_factory() as session:
        context = AgentRuntimeService(session, client.app.state.settings)._build_context(
            UUID(project_id), thread["id"], []
        )
    assert context["counts"]["source_identities"] == 1
    assert context["counts"]["observation_assertions"] == 2
    assert context["source_identities"][0]["status"] == "BOUND"


def test_unresolved_source_identity_can_be_manually_bound(client: TestClient) -> None:
    project_id = _project(client)
    target = client.post(
        f"/api/v3/projects/{project_id}/entities",
        json={"type_key": "role", "name": "销售主管"},
    ).json()
    source = client.post(
        f"/api/v3/projects/{project_id}/source-systems",
        json={"name": "外部岗位表", "kind": "FILE", "connection_profile": {}},
    ).json()
    for source_field, target_property in (("id", "__stable_key__"), ("name", "__name__")):
        _approved_mapping(
            client,
            project_id,
            {
                "source_system_id": source["id"],
                "source_asset": "*",
                "source_field": source_field,
                "target_type_key": "role",
                "target_property_key": target_property,
            },
        )
    preview = client.post(
        f"/api/v3/projects/{project_id}/source-systems/{source['id']}/imports/preview",
        files={"file": ("roles.csv", b"id,name\nx-9,Sales Lead\n", "text/csv")},
        data={"kind": "CSV"},
    ).json()
    client.post(
        f"/api/v3/projects/{project_id}/imports/confirm",
        json={"preview_id": preview["id"]},
    ).raise_for_status()
    identity = client.get(
        f"/api/v3/projects/{project_id}/source-identities"
    ).json()["items"][0]
    assert identity["status"] == "UNRESOLVED"

    bound = client.patch(
        f"/api/v3/projects/{project_id}/source-identities/{identity['id']}/binding",
        json={"entity_id": target["id"], "expected_revision": identity["revision"]},
    )
    assert bound.status_code == 200, bound.text
    assert bound.json()["status"] == "BOUND"
    assert bound.json()["entity_id"] == target["id"]
    target_after = client.get(
        f"/api/v3/projects/{project_id}/entities/{target['id']}"
    ).json()
    assert target_after["name"] == "销售主管"
    assert target_after["observed_name"] == "Sales Lead"


def test_equal_authority_conflicts_do_not_use_import_order(client: TestClient) -> None:
    project_id = _project(client)
    designed = client.post(
        f"/api/v3/projects/{project_id}/entities",
        json={
            "type_key": "role",
            "stable_key": "role.owner",
            "name": "业务负责人",
            "properties": {"headcount": 1},
        },
    ).json()

    def import_source(name: str, headcount: int, priority: int) -> None:
        source = client.post(
            f"/api/v3/projects/{project_id}/source-systems",
            json={"name": name, "kind": "FILE", "connection_profile": {}},
        ).json()
        _approved_mapping(
            client,
            project_id,
            {
                "source_system_id": source["id"],
                "source_asset": "*",
                "source_field": "id",
                "target_type_key": "role",
                "target_property_key": "__stable_key__",
                "authority_priority": priority,
            },
        )
        _approved_mapping(
            client,
            project_id,
            {
                "source_system_id": source["id"],
                "source_asset": "*",
                "source_field": "headcount",
                "target_type_key": "role",
                "target_property_key": "headcount",
                "transform_expression": "int",
                "authority_priority": priority,
            },
        )
        content = f"id,headcount\nrole.owner,{headcount}\n".encode()
        preview = client.post(
            f"/api/v3/projects/{project_id}/source-systems/{source['id']}/imports/preview",
            files={"file": (f"{name}.csv", content, "text/csv")},
            data={"kind": "CSV"},
        ).json()
        client.post(
            f"/api/v3/projects/{project_id}/imports/confirm",
            json={"preview_id": preview["id"]},
        ).raise_for_status()

    import_source("同权威甲", 2, 100)
    import_source("同权威乙", 3, 100)
    conflicted = client.get(
        f"/api/v3/projects/{project_id}/entities/{designed['id']}"
    ).json()
    assert "headcount" not in conflicted["observed_properties"]
    assert conflicted["comparison"]["headcount"]["status"] == "CONFLICT"
    assert {
        item["value"]
        for item in conflicted["comparison"]["headcount"]["candidates"]
    } == {2, 3}

    conflicts = client.get(
        f"/api/v3/projects/{project_id}/observation-conflicts?status=OPEN"
    )
    assert conflicts.status_code == 200, conflicts.text
    assert conflicts.json()["total"] == 1
    conflict = conflicts.json()["items"][0]
    chosen = next(item for item in conflict["candidates"] if item["value"] == 3)
    resolved_conflict = client.post(
        f"/api/v3/projects/{project_id}/observation-conflicts/{conflict['id']}/resolve",
        json={
            "resolution_kind": "CHOOSE_ASSERTION",
            "chosen_assertion_id": chosen["assertion_id"],
            "rationale": "HR 口径由管理者确认。",
            "resolved_by": "system-owner",
            "expected_revision": conflict["revision"],
        },
    )
    assert resolved_conflict.status_code == 200, resolved_conflict.text
    resolved_entity = client.get(
        f"/api/v3/projects/{project_id}/entities/{designed['id']}"
    ).json()
    assert resolved_entity["observed_properties"]["headcount"] == 3
    assert resolved_entity["comparison"]["headcount"]["status"] == "RESOLVED_CONFLICT"

    import_source("高权威", 4, 200)
    resolved = client.get(
        f"/api/v3/projects/{project_id}/entities/{designed['id']}"
    ).json()
    assert resolved["observed_properties"]["headcount"] == 4
    assert resolved["comparison"]["headcount"]["status"] == "DIVERGED"
    historical = client.get(
        f"/api/v3/projects/{project_id}/observation-conflicts"
    ).json()["items"]
    assert historical[0]["status"] == "SUPERSEDED"
