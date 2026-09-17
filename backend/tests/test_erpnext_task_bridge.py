from __future__ import annotations

import json
from hashlib import sha256
from uuid import uuid4

import httpx
import pytest

from enterprise_insight_backend.erpnext_task_bridge import (
    ERPNextTaskBridgeClient,
    ERPNextTaskOperation,
    ERPNextTaskOperationStatus,
    ERPNextTaskOutcomeUnknown,
)
from enterprise_insight_backend.errors import DomainError

PROFILE = {
    "base_url": "https://erpnext.example.test/frappe",
    "api_key": "local-test-key",
    "api_secret": "local-test-secret",
    "timeout_seconds": 15,
}


def _operation(action: str = "create") -> ERPNextTaskOperation:
    if action == "create":
        payload: dict[str, str] = {"subject": "跟进采购计划", "project": "PRJ-001"}
    elif action == "update":
        payload = {
            "task_name": "TASK-2026-0001",
            "subject": "跟进采购计划",
            "project": "PRJ-001",
        }
    else:
        payload = {"task_name": "TASK-2026-0001"}
    if action in {"update", "cancel"}:
        payload["task_name"] = "TASK-2026-0001"
    return ERPNextTaskOperation(
        operation_id=uuid4(),
        action=action,
        payload=payload,
        expected_modified=(
            "2026-09-13 10:00:00" if action in {"update", "cancel"} else None
        ),
    )


def _client(handler: object) -> tuple[ERPNextTaskBridgeClient, httpx.Client]:
    transport = httpx.MockTransport(handler)  # type: ignore[arg-type]
    http_client = httpx.Client(transport=transport)
    return ERPNextTaskBridgeClient(PROFILE, client=http_client), http_client


def test_execute_sends_stable_operation_hash_and_parses_frappe_receipt() -> None:
    operation = _operation()

    def handler(request: httpx.Request) -> httpx.Response:
        assert request.method == "POST"
        assert request.url.path.endswith("api.execute_task_operation")
        assert request.headers["Authorization"] == "token local-test-key:local-test-secret"
        body = json.loads(request.content)
        assert body["operation_id"] == str(operation.operation_id)
        assert body["payload_sha256"] == operation.payload_sha256
        assert body["action"] == "create"
        return httpx.Response(
            200,
            json={
                "message": {
                    "operation_id": str(operation.operation_id),
                    "payload_sha256": operation.payload_sha256,
                    "status": "COMMITTED",
                    "task_name": "TASK-2026-00101",
                    "task_modified": "2026-09-13 10:00:00",
                    "task": {"subject": "跟进采购计划", "status": "Open"},
                }
            },
        )

    client, http_client = _client(handler)
    try:
        receipt = client.execute(operation)
    finally:
        http_client.close()

    assert receipt.status == ERPNextTaskOperationStatus.COMMITTED
    assert receipt.task_name == "TASK-2026-00101"
    assert receipt.task == {"subject": "跟进采购计划", "status": "Open"}


def test_update_requires_and_transmits_expected_remote_version() -> None:
    with pytest.raises(DomainError) as missing:
        ERPNextTaskOperation(uuid4(), "update", {"task_name": "TASK-1"})
    assert missing.value.code == "ERPNEXT_TASK_EXPECTED_VERSION_REQUIRED"

    operation = _operation("update")

    def handler(request: httpx.Request) -> httpx.Response:
        body = json.loads(request.content)
        assert body["expected_modified"] == operation.expected_modified
        return httpx.Response(
            200,
            json={
                "message": {
                    "operation_id": str(operation.operation_id),
                    "payload_sha256": operation.payload_sha256,
                    "status": "COMMITTED",
                    "task_name": "TASK-2026-00101",
                }
            },
        )

    client, http_client = _client(handler)
    try:
        assert client.execute(operation).status == ERPNextTaskOperationStatus.COMMITTED
    finally:
        http_client.close()


def test_timeout_is_unknown_and_never_blindly_retried() -> None:
    calls = 0
    operation = _operation()

    def handler(_request: httpx.Request) -> httpx.Response:
        nonlocal calls
        calls += 1
        raise httpx.ReadTimeout("remote may have committed")

    client, http_client = _client(handler)
    try:
        with pytest.raises(ERPNextTaskOutcomeUnknown) as unknown:
            client.execute(operation)
    finally:
        http_client.close()

    assert unknown.value.operation_id == operation.operation_id
    assert calls == 1


def test_reconcile_reports_missing_receipt_without_claiming_safe_retry() -> None:
    operation_id = uuid4()

    def handler(request: httpx.Request) -> httpx.Response:
        assert request.method == "GET"
        assert request.url.params["operation_id"] == str(operation_id)
        return httpx.Response(404, json={"exc_type": "DoesNotExistError"})

    client, http_client = _client(handler)
    try:
        receipt = client.reconcile(operation_id)
    finally:
        http_client.close()

    assert receipt.status == ERPNextTaskOperationStatus.NOT_FOUND
    assert "不能据此假定" in (receipt.message or "")


def test_wrong_operation_or_payload_hash_in_receipt_is_rejected() -> None:
    operation = _operation()

    def wrong_operation(_request: httpx.Request) -> httpx.Response:
        return httpx.Response(
            200,
            json={
                "message": {
                    "operation_id": str(uuid4()),
                    "payload_sha256": operation.payload_sha256,
                    "status": "COMMITTED",
                }
            },
        )

    client, http_client = _client(wrong_operation)
    try:
        with pytest.raises(DomainError) as mismatch:
            client.execute(operation)
    finally:
        http_client.close()
    assert mismatch.value.code == "ERPNEXT_TASK_RECEIPT_ID_MISMATCH"

    def wrong_hash(_request: httpx.Request) -> httpx.Response:
        return httpx.Response(
            200,
            json={
                "message": {
                    "operation_id": str(operation.operation_id),
                    "payload_sha256": "0" * 64,
                    "status": "COMMITTED",
                }
            },
        )

    client, http_client = _client(wrong_hash)
    try:
        with pytest.raises(DomainError) as hash_mismatch:
            client.execute(operation)
    finally:
        http_client.close()
    assert hash_mismatch.value.code == "ERPNEXT_TASK_RECEIPT_HASH_MISMATCH"


def test_update_maps_platform_task_name_to_frappe_name_and_matches_digest_contract() -> None:
    operation = _operation("update")
    expected_payload = {"name": "TASK-2026-0001", "subject": "跟进采购计划", "project": "PRJ-001"}
    expected_digest = sha256(
        json.dumps(
            expected_payload,
            ensure_ascii=False,
            sort_keys=True,
            separators=(",", ":"),
            allow_nan=False,
        ).encode("utf-8")
    ).hexdigest()
    assert operation.payload_sha256 == expected_digest
    assert operation.remote_payload == expected_payload

    def handler(request: httpx.Request) -> httpx.Response:
        body = json.loads(request.content)
        assert body["payload"] == expected_payload
        assert body["payload_sha256"] == expected_digest
        assert body["expected_modified"] == operation.expected_modified
        return httpx.Response(
            200,
            json={
                "message": {
                    "operation_id": str(operation.operation_id),
                    "payload_sha256": expected_digest,
                    "status": "COMMITTED",
                    "task_name": "TASK-2026-0001",
                }
            },
        )

    client, http_client = _client(handler)
    try:
        assert client.execute(operation).status == ERPNextTaskOperationStatus.COMMITTED
    finally:
        http_client.close()


def test_reconcile_accepts_in_progress_receipt_without_final_payload_hash() -> None:
    operation_id = uuid4()

    def handler(_request: httpx.Request) -> httpx.Response:
        return httpx.Response(
            200,
            json={"message": {"operation_id": str(operation_id), "status": "IN_PROGRESS"}},
        )

    client, http_client = _client(handler)
    try:
        receipt = client.reconcile(operation_id)
    finally:
        http_client.close()

    assert receipt.status == ERPNextTaskOperationStatus.IN_PROGRESS


def test_non_finite_task_progress_is_rejected() -> None:
    with pytest.raises(DomainError) as invalid:
        ERPNextTaskOperation(
            operation_id=uuid4(),
            action="update",
            payload={"task_name": "TASK-1", "progress": float("nan")},
            expected_modified="2026-09-13 10:00:00",
        )
    assert invalid.value.code == "ERPNEXT_TASK_PROGRESS_INVALID"


def test_task_payload_is_allowlisted_and_status_values_are_checked() -> None:
    with pytest.raises(DomainError) as arbitrary:
        ERPNextTaskOperation(
            operation_id=uuid4(),
            action="create",
            payload={"subject": "任意", "project": "PRJ-001", "sql": "DROP TABLE"},
        )
    assert arbitrary.value.code == "ERPNEXT_TASK_FIELDS_UNSUPPORTED"

    with pytest.raises(DomainError) as invalid_status:
        ERPNextTaskOperation(
            operation_id=uuid4(),
            action="update",
            payload={"task_name": "TASK-1", "status": "Delete"},
            expected_modified="2026-09-13 10:00:00",
        )
    assert invalid_status.value.code == "ERPNEXT_TASK_STATUS_INVALID"

    with pytest.raises(DomainError) as stale_cancel:
        ERPNextTaskOperation(
            operation_id=uuid4(), action="cancel", payload={"task_name": "TASK-1"}
        )
    assert stale_cancel.value.code == "ERPNEXT_TASK_EXPECTED_VERSION_REQUIRED"


@pytest.mark.parametrize(
    "profile",
    [
        {**PROFILE, "base_url": "file:///etc/passwd"},
        {**PROFILE, "base_url": "https://user:secret@erpnext.example.test"},
        {**PROFILE, "api_key": ""},
    ],
)
def test_invalid_url_or_credentials_fail_closed(profile: dict[str, object]) -> None:
    with pytest.raises(DomainError):
        ERPNextTaskBridgeClient(profile)  # type: ignore[arg-type]
