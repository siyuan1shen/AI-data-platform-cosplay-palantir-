from __future__ import annotations

import json
from copy import deepcopy
from typing import Any

import httpx
import pytest
from fastapi.testclient import TestClient

from enterprise_insight_backend.connectors import connector_for
from enterprise_insight_backend.erpnext_connector import (
    ERPNextConnector,
    ERPNextPageSnapshot,
)
from enterprise_insight_backend.errors import DomainError

BASE_PROFILE = {
    "base_url": "http://erpnext.test",
    "site_id": "test-site",
    "api_key": "local-test-key",
    "api_secret": "local-test-secret",
    "fields_by_doctype": {
        "Task": ["subject", "status", "progress", "project"],
        "Project": ["project_name"],
    },
    "max_retries": 0,
}


def _task(
    name: str,
    modified: str,
    *,
    docstatus: int = 0,
    status: str = "Open",
    project: str | None = None,
) -> dict[str, Any]:
    return {
        "name": name,
        "modified": modified,
        "docstatus": docstatus,
        "subject": f"任务 {name}",
        "status": status,
        "progress": 20,
        "project": project,
    }


def _mock_client(handler: Any) -> httpx.Client:
    return httpx.Client(transport=httpx.MockTransport(handler))


def test_frappe_keyset_paging_handles_equal_modified_values_and_preserves_auth() -> None:
    rows = [
        _task("TASK-001", "2026-09-10 10:00:00.000000"),
        _task("TASK-002", "2026-09-10 10:00:00.000000"),
        _task("TASK-003", "2026-09-10 10:00:00.000000"),
        _task("TASK-004", "2026-09-10 10:01:00.000000"),
    ]
    requests: list[httpx.Request] = []

    def handler(request: httpx.Request) -> httpx.Response:
        requests.append(request)
        assert request.url.path == "/api/resource/Task"
        assert request.headers["Authorization"] == "token local-test-key:local-test-secret"
        params = request.url.params
        start = int(params.get("limit_start", "0"))
        limit = int(params["limit_page_length"])
        selected = rows
        if "filters" in params:
            filters = json.loads(params["filters"])
            if any(item[:2] == ["modified", ">="] for item in filters):
                watermark = next(item[2] for item in filters if item[0] == "modified")
                or_filters = json.loads(params["or_filters"])
                tie_breaker = or_filters[1][2]
                selected = [
                    row
                    for row in rows
                    if row["modified"] > watermark
                    or (row["modified"] == watermark and row["name"] > tie_breaker)
                ]
        return httpx.Response(200, json={"data": selected[start : start + limit]}, request=request)

    connector = ERPNextConnector(BASE_PROFILE, client=_mock_client(handler))
    first = connector.extract(
        "Task",
        limit=2,
        watermark_column=None,
        tie_breaker_column=None,
        after_watermark=None,
        after_tie_breaker=None,
    )
    second = connector.extract(
        "Task",
        limit=2,
        watermark_column="modified",
        tie_breaker_column="name",
        after_watermark=first.next_watermark,
        after_tie_breaker=first.next_tie_breaker,
    )

    assert [row["name"] for row in first.rows] == ["TASK-001", "TASK-002"]
    assert [row["name"] for row in second.rows] == ["TASK-003", "TASK-004"]
    assert first.has_more is True
    assert second.has_more is True
    assert second.next_tie_breaker == "TASK-004"
    assert json.loads(requests[1].url.params["filters"])[-1] == [
        "modified",
        ">=",
        "2026-09-10 10:00:00.000000",
    ]
    assert json.loads(requests[1].url.params["or_filters"]) == [
        ["modified", ">", "2026-09-10 10:00:00.000000"],
        ["name", ">", "TASK-002"],
    ]
    assert "local-test-secret" not in json.dumps(first.request_parameters)


def test_snapshot_replay_is_deterministic_and_conflicting_identity_is_quarantined() -> None:
    records = [
        _task("TASK-001", "2026-09-10 10:00:00.000000", status="Open"),
        _task("TASK-001", "2026-09-10 10:00:00.000000", status="Working"),
        _task("TASK-002", "2026-09-10 10:01:00.000000", docstatus=2, status="Cancelled"),
        {"modified": "2026-09-10 10:02:00.000000", "docstatus": 0, "subject": "缺少源ID"},
    ]
    client = _mock_client(lambda req: httpx.Response(200, json={"data": records}, request=req))
    connector = ERPNextConnector(BASE_PROFILE, client=client)
    page = connector.extract(
        "Task",
        limit=10,
        watermark_column=None,
        tie_breaker_column=None,
        after_watermark=None,
        after_tie_breaker=None,
    )

    conflicted = [row for row in page.rows if row["__erpnext_quarantine__"]]
    cancelled = next(row for row in page.rows if row.get("name") == "TASK-002")
    assert len(conflicted) == 3
    assert {row["__erpnext_quarantine__"] for row in conflicted} == {
        "ERPNEXT_SAME_VERSION_IDENTITY_CONFLICT",
        "ERPNEXT_SOURCE_IDENTITY_MISSING",
    }
    assert all(row["name"] is None for row in conflicted)
    assert cancelled["__erpnext_lifecycle__"] == "CANCELLED"
    assert cancelled["__erpnext_lineage__"]["source_record_key"] == "TASK-002"
    assert cancelled["__erpnext_lineage__"]["source_payload_sha256"]
    assert page.snapshot is not None
    replayed = page.snapshot.replay()
    assert replayed.rows == page.rows
    assert replayed.snapshot_sha256 == page.snapshot_sha256

    corrupted = ERPNextPageSnapshot(
        doctype=page.snapshot.doctype,
        source_site=page.snapshot.source_site,
        raw_records=({"name": "tampered"},),
        rows=page.snapshot.rows,
        columns=page.snapshot.columns,
        quarantined_records=page.snapshot.quarantined_records,
        request_parameters=page.snapshot.request_parameters,
        next_watermark=page.snapshot.next_watermark,
        next_tie_breaker=page.snapshot.next_tie_breaker,
        has_more=page.snapshot.has_more,
        raw_sha256=page.snapshot.raw_sha256,
        normalized_sha256=page.snapshot.normalized_sha256,
    )
    with pytest.raises(DomainError, match="摘要不匹配"):
        corrupted.replay()

    mutated_rows = ERPNextPageSnapshot(
        doctype=page.snapshot.doctype,
        source_site=page.snapshot.source_site,
        raw_records=page.snapshot.raw_records,
        rows=({"name": "tampered"},),
        columns=page.snapshot.columns,
        quarantined_records=page.snapshot.quarantined_records,
        request_parameters=page.snapshot.request_parameters,
        next_watermark=page.snapshot.next_watermark,
        next_tie_breaker=page.snapshot.next_tie_breaker,
        has_more=page.snapshot.has_more,
        raw_sha256=page.snapshot.raw_sha256,
        normalized_sha256=page.snapshot.normalized_sha256,
    )
    with pytest.raises(DomainError, match="重放内容或来源清单摘要不匹配"):
        mutated_rows.replay()


def test_link_target_conflicts_are_isolated_without_dropping_source_record() -> None:
    profile = {
        **BASE_PROFILE,
        "link_fields_by_doctype": {"Task": {"project": "Project"}},
    }

    def handler(request: httpx.Request) -> httpx.Response:
        if request.url.path.endswith("/Project"):
            return httpx.Response(
                200,
                json={"data": [{"name": "KNOWN-PROJECT"}]},
                request=request,
            )
        task = _task(
            "TASK-001", "2026-09-10 10:00:00.000000", project="MISSING-PROJECT"
        )
        return httpx.Response(200, json={"data": [task]}, request=request)

    connector = ERPNextConnector(profile, client=_mock_client(handler))
    page = connector.extract(
        "Task",
        limit=20,
        watermark_column=None,
        tie_breaker_column=None,
        after_watermark=None,
        after_tie_breaker=None,
    )

    assert len(page.rows) == 1
    assert page.rows[0]["name"] == "TASK-001"
    assert page.rows[0]["__erpnext_relation_validation__"] == {
        "status": "CONFLICTS",
        "checked_links": 1,
        "conflicts": 1,
    }
    assert page.rows[0]["__erpnext_relation_conflicts__"] == [
        {
            "code": "ERPNEXT_LINK_TARGET_NOT_FOUND",
            "field": "project",
            "target_doctype": "Project",
            "target_record_key": "MISSING-PROJECT",
        }
    ]
    assert page.warnings


def test_transient_frappe_errors_retry_safe_read_requests() -> None:
    calls = 0

    def handler(request: httpx.Request) -> httpx.Response:
        nonlocal calls
        calls += 1
        if calls == 1:
            return httpx.Response(503, json={"message": "temporary"}, request=request)
        return httpx.Response(
            200,
            json={"data": [_task("TASK-001", "2026-09-10 10:00:00.000000")]},
            request=request,
        )

    connector = ERPNextConnector(
        {**BASE_PROFILE, "max_retries": 1, "retry_backoff_ms": 0},
        client=_mock_client(handler),
    )
    page = connector.extract(
        "Task",
        limit=10,
        watermark_column=None,
        tie_breaker_column=None,
        after_watermark=None,
        after_tie_breaker=None,
    )

    assert calls == 2
    assert [row["name"] for row in page.rows] == ["TASK-001"]


def test_incremental_overlap_replays_older_window_and_deduplicates_source_revisions() -> None:
    rows = [
        _task("TASK-001", "2026-09-10 10:00:00.000000"),
        _task("TASK-002", "2026-09-10 10:02:00.000000"),
        _task("TASK-003", "2026-09-10 10:04:00.000000"),
    ]
    requests: list[httpx.Request] = []

    def handler(request: httpx.Request) -> httpx.Response:
        requests.append(request)
        params = request.url.params
        selected = rows
        if "filters" in params:
            filters = json.loads(params["filters"])
            watermark = next(item[2] for item in filters if item[0] == "modified")
            or_filters = json.loads(params["or_filters"])
            tie = or_filters[1][2]
            selected = [
                row
                for row in rows
                if row["modified"] > watermark
                or (row["modified"] == watermark and row["name"] > tie)
            ]
        limit = int(params["limit_page_length"])
        return httpx.Response(200, json={"data": selected[:limit]}, request=request)

    connector = ERPNextConnector(
        {**BASE_PROFILE, "overlap_seconds": 300},
        client=_mock_client(handler),
    )
    page = connector.extract_all(
        "Task",
        limit=2,
        after_watermark="2026-09-10 10:04:00.000000",
        after_tie_breaker="TASK-003",
    )

    # The five-minute overlap includes the current high-water record and the
    # previous page; the connector retains one instance of each exact revision.
    assert [row["name"] for row in page.rows] == ["TASK-001", "TASK-002", "TASK-003"]
    assert len({row["__erpnext_lineage__"]["source_payload_sha256"] for row in page.rows}) == 3
    first_filters = json.loads(requests[0].url.params["filters"])
    assert ["modified", ">=", "2026-09-10 09:59:00.000000"] in first_filters
    assert page.next_watermark == "2026-09-10 10:04:00.000000"
    assert page.next_tie_breaker == "TASK-003"


def _project_and_erp_source(client: TestClient) -> tuple[str, str]:
    company = client.post("/api/v3/companies", json={"name": "Frappe 本地连接器测试"})
    assert company.status_code == 201, company.text
    project = client.post(
        f"/api/v3/companies/{company.json()['id']}/projects",
        json={"name": "ERPNext 同步回归"},
    )
    assert project.status_code == 201, project.text
    project_id = project.json()["id"]
    source = client.post(
        f"/api/v3/projects/{project_id}/source-systems",
        json={
            "name": "本地模拟 Frappe v1",
            "kind": "ERP",
            "connection_profile": BASE_PROFILE,
        },
    )
    assert source.status_code == 201, source.text
    return project_id, source.json()["id"]


def test_erpnext_api_connector_receives_decrypted_credentials(
    client: TestClient, monkeypatch: pytest.MonkeyPatch
) -> None:
    project_id, source_id = _project_and_erp_source(client)
    authorization_headers: list[str] = []

    def authorized(url: str, **kwargs: Any) -> httpx.Response:
        authorization_headers.append(kwargs["headers"]["Authorization"])
        request = httpx.Request("GET", url, params=kwargs.get("params"))
        return httpx.Response(200, json={"data": [{"name": "COMPANY-1"}]}, request=request)

    monkeypatch.setattr("enterprise_insight_backend.erpnext_connector.httpx.get", authorized)
    tested = client.post(
        f"/api/v3/projects/{project_id}/source-systems/{source_id}/test"
    )
    assert tested.status_code == 200, tested.text
    assert tested.json()["ok"] is True
    assert authorization_headers == ["token local-test-key:local-test-secret"]
    assert "local-test-key" not in tested.text
    assert "local-test-secret" not in tested.text


def _approved_mapping(
    client: TestClient,
    project_id: str,
    source_id: str,
    field: str,
    property_key: str,
) -> None:
    created = client.post(
        f"/api/v3/projects/{project_id}/semantic-mappings",
        json={
            "source_system_id": source_id,
            "source_asset": "Task",
            "source_field": field,
            "target_type_key": "erp_task",
            "target_property_key": property_key,
            "transform_expression": "strip",
        },
    )
    assert created.status_code == 201, created.text
    mapping = created.json()
    validated = client.post(
        f"/api/v3/projects/{project_id}/semantic-mappings/{mapping['id']}/validate",
        json={"expected_revision": mapping["revision"]},
    )
    assert validated.status_code == 200, validated.text
    mapping = validated.json()
    approved = client.post(
        f"/api/v3/projects/{project_id}/semantic-mappings/{mapping['id']}/approve",
        json={"expected_revision": mapping["revision"]},
    )
    assert approved.status_code == 200, approved.text


def _sync_erp_page(
    client: TestClient,
    project_id: str,
    source_id: str,
    monkeypatch: pytest.MonkeyPatch,
    record: dict[str, Any],
) -> dict[str, Any]:
    def fake_get(
        url: str,
        *,
        params: dict[str, Any],
        headers: dict[str, str],
        timeout: float,
    ) -> httpx.Response:
        request = httpx.Request("GET", url, params=params, headers=headers)
        assert request.headers["Authorization"] == "token local-test-key:local-test-secret"
        return httpx.Response(200, json={"data": [deepcopy(record)]}, request=request)

    monkeypatch.setattr(
        "enterprise_insight_backend.erpnext_connector.httpx.get",
        fake_get,
    )
    preview = client.post(
        f"/api/v3/projects/{project_id}/source-systems/{source_id}/extract/preview",
        json={"asset_key": "Task", "limit": 10},
    )
    assert preview.status_code == 200, preview.text
    assert preview.json()["rows"][0]["__erpnext_lineage__"]["source_record_key"] == record["name"]
    synced = client.post(
        f"/api/v3/projects/{project_id}/source-systems/{source_id}/sync",
        json={"preview_id": preview.json()["preview_id"]},
    )
    assert synced.status_code == 200, synced.text
    return synced.json()["import_result"]


def test_erp_factory_sync_is_idempotent_and_versions_cancelled_status(
    client: TestClient, monkeypatch: pytest.MonkeyPatch
) -> None:
    assert isinstance(connector_for("ERP", BASE_PROFILE), ERPNextConnector)
    project_id, source_id = _project_and_erp_source(client)
    type_response = client.post(
        f"/api/v3/projects/{project_id}/ontology/types",
        json={
            "key": "erp_task",
            "name": "ERPNext 任务",
            "kind": "OBJECT",
            "properties": [
                {"key": "lifecycle", "name": "文档生命周期", "value_type": "STRING"},
                {"key": "task_status", "name": "任务状态", "value_type": "STRING"},
            ],
        },
    )
    assert type_response.status_code == 201, type_response.text
    _approved_mapping(client, project_id, source_id, "name", "__stable_key__")
    _approved_mapping(client, project_id, source_id, "subject", "__name__")
    _approved_mapping(client, project_id, source_id, "__erpnext_lifecycle__", "lifecycle")
    _approved_mapping(client, project_id, source_id, "status", "task_status")

    first_record = _task("TASK-001", "2026-09-10 10:00:00.000000", status="Open")
    first_result = _sync_erp_page(client, project_id, source_id, monkeypatch, first_record)
    assert first_result["entities_created"] == 1
    assert first_result["observations_created"] == 3

    repeated_result = _sync_erp_page(client, project_id, source_id, monkeypatch, first_record)
    assert repeated_result["status"] == "REPROCESSED"
    assert repeated_result["entities_created"] == 0
    assert repeated_result["observations_created"] == 0

    cancelled_record = _task(
        "TASK-001",
        "2026-09-10 10:03:00.000000",
        docstatus=2,
        status="Cancelled",
    )
    cancelled_result = _sync_erp_page(
        client, project_id, source_id, monkeypatch, cancelled_record
    )
    assert cancelled_result["entities_created"] == 0
    assert cancelled_result["observations_created"] == 3

    assertions = client.get(
        f"/api/v3/projects/{project_id}/observation-assertions"
    )
    assert assertions.status_code == 200, assertions.text
    lifecycle_versions = [
        row for row in assertions.json()["items"] if row["field_key"] == "lifecycle"
    ]
    assert len(lifecycle_versions) == 2
    assert {row["value"] for row in lifecycle_versions} == {"DRAFT", "CANCELLED"}
    assert {row["status"] for row in lifecycle_versions} == {"ACTIVE", "SUPERSEDED"}

    raw_batches = client.get(f"/api/v3/projects/{project_id}/raw-batches")
    assert raw_batches.status_code == 200, raw_batches.text
    latest_batch = raw_batches.json()["items"][0]
    raw_records = client.get(
        f"/api/v3/projects/{project_id}/raw-batches/{latest_batch['id']}/records"
    )
    assert raw_records.status_code == 200, raw_records.text
    lineage = raw_records.json()["items"][0]["payload"]["__erpnext_lineage__"]
    assert lineage["doctype"] == "Task"
    assert lineage["source_record_key"] == "TASK-001"
    assert lineage["source_docstatus"] == 2
    assert lineage["source_payload_sha256"]


def test_erp_source_does_not_fall_back_to_generic_rest_on_failed_auth(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    def unauthorized(url: str, **kwargs: Any) -> httpx.Response:
        request = httpx.Request("GET", url, params=kwargs.get("params"))
        return httpx.Response(401, json={"message": "do not expose credentials"}, request=request)

    monkeypatch.setattr("enterprise_insight_backend.erpnext_connector.httpx.get", unauthorized)
    connector = connector_for("ERP", BASE_PROFILE)
    ok, message = connector.test()
    assert ok is False
    assert "401" in message
    assert "local-test-secret" not in message
