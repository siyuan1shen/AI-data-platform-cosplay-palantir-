from __future__ import annotations

from datetime import UTC, datetime, timedelta
from uuid import UUID

import pytest
from fastapi.testclient import TestClient

import enterprise_insight_backend.actions as actions_module
from enterprise_insight_backend.action_recovery_worker import ActionRecoveryWorker
from enterprise_insight_backend.actions import ActionService
from enterprise_insight_backend.erpnext_task_bridge import (
    ERPNextTaskOperation,
    ERPNextTaskOperationReceipt,
    ERPNextTaskOperationStatus,
)
from enterprise_insight_backend.models import ActionInvocationRow, ActionLogRow


def _approved_update(client: TestClient) -> tuple[str, str, str]:
    company = client.post("/api/v3/companies", json={"name": "Task 动作企业"}).json()
    project = client.post(
        f"/api/v3/companies/{company['id']}/projects", json={"name": "ERPNext Task 动作"}
    ).json()
    source_response = client.post(
        f"/api/v3/projects/{project['id']}/source-systems",
        json={
            "name": "ERPNext 测试连接",
            "kind": "ERP",
            "connection_profile": {
                "base_url": "https://erpnext.example.test",
                "api_key": "test-api-key",
                "api_secret": "test-api-secret",
            },
        },
    )
    assert source_response.status_code == 201, source_response.text
    source = source_response.json()

    definition_response = client.post(
        f"/api/v3/projects/{project['id']}/action-definitions",
        json={
            "key": "erpnext.task.update",
            "name": "更新 ERPNext Task",
            "description": "受控的外部 Task 更新",
            "parameters": [],
            "preconditions": [],
            "effects": [{"kind": "UPDATE", "resource": "ERPNext Task"}],
            "execution_mode": "CONNECTOR",
            "risk_level": "HIGH",
            "require_approval": True,
            "enabled": True,
        },
    )
    assert definition_response.status_code == 201, definition_response.text
    definition = definition_response.json()
    validated = client.post(
        f"/api/v3/projects/{project['id']}/action-definitions/{definition['id']}/validate"
    )
    assert validated.status_code == 200, validated.text

    invocation_response = client.post(
        f"/api/v3/projects/{project['id']}/action-invocations",
        json={
            "action_definition_id": definition["id"],
            "idempotency_key": "erpnext-task-update-once",
            "requested_by": "local-owner",
            "input": {
                "source_system_id": source["id"],
                "task_name": "TASK-00001",
                "expected_modified": "2026-09-13 10:00:00",
                "subject": "更新后的事项",
            },
        },
    )
    assert invocation_response.status_code == 201, invocation_response.text
    invocation = invocation_response.json()
    preview = client.post(
        f"/api/v3/projects/{project['id']}/action-invocations/{invocation['id']}/dry-run"
    )
    assert preview.status_code == 200, preview.text
    assert preview.json()["status"] == "WAITING_APPROVAL"
    approval = client.post(
        f"/api/v3/projects/{project['id']}/action-invocations/{invocation['id']}/approve",
        json={"approved_by": "local-owner"},
    )
    assert approval.status_code == 200, approval.text
    return project["id"], invocation["id"], source["id"]


class SimulatedProcessCrash(BaseException):
    pass


def test_persisted_running_external_action_can_be_reconciled_after_process_crash(
    client: TestClient, monkeypatch: pytest.MonkeyPatch
) -> None:
    project_id, invocation_id, _source_id = _approved_update(client)
    captured: list[ERPNextTaskOperation] = []

    class CrashAfterRemoteCommit:
        def __init__(self, _profile: dict[str, object]) -> None:
            pass

        def execute(self, operation: ERPNextTaskOperation) -> None:
            captured.append(operation)
            # Model the process disappearing after the connector call may have
            # committed remotely but before the local receipt was saved.
            raise SimulatedProcessCrash

        def reconcile(self, operation_id: UUID) -> ERPNextTaskOperationReceipt:
            operation = captured[0]
            assert operation_id == operation.operation_id
            return ERPNextTaskOperationReceipt(
                operation_id=operation_id,
                payload_sha256=operation.payload_sha256,
                status=ERPNextTaskOperationStatus.COMMITTED,
                task_name="TASK-00001",
                task_modified="2026-09-13 10:01:00",
                task={"name": "TASK-00001", "subject": "更新后的事项"},
            )

    monkeypatch.setattr(actions_module, "ERPNextTaskBridgeClient", CrashAfterRemoteCommit)
    session = client.app.state.database.session_factory()
    try:
        with pytest.raises(SimulatedProcessCrash):
            ActionService(session, settings=client.app.state.settings).execute(
                UUID(project_id), UUID(invocation_id)
            )
    finally:
        session.rollback()
        session.close()

    persisted = client.get(
        f"/api/v3/projects/{project_id}/action-invocations/{invocation_id}"
    )
    assert persisted.status_code == 200, persisted.text
    assert persisted.json()["status"] == "RUNNING"
    assert len(captured) == 1
    assert captured[0].remote_payload == {
        "name": "TASK-00001",
        "subject": "更新后的事项",
    }

    reconciled = client.post(
        f"/api/v3/projects/{project_id}/action-invocations/{invocation_id}/reconcile"
    )
    assert reconciled.status_code == 200, reconciled.text
    assert reconciled.json()["status"] == "SUCCEEDED"
    assert reconciled.json()["result"]["task_name"] == "TASK-00001"
    assert len(captured) == 1  # Reconciliation never replays the remote write.


def test_remote_unknown_is_not_reexecuted_and_not_found_does_not_clear_unknown(
    client: TestClient, monkeypatch: pytest.MonkeyPatch
) -> None:
    project_id, invocation_id, _source_id = _approved_update(client)
    execute_calls = 0
    operation_ids: list[UUID] = []

    class UnknownRemoteResult:
        def __init__(self, _profile: dict[str, object]) -> None:
            pass

        def execute(self, operation: ERPNextTaskOperation) -> None:
            nonlocal execute_calls
            execute_calls += 1
            operation_ids.append(operation.operation_id)
            from enterprise_insight_backend.erpnext_task_bridge import (
                ERPNextTaskOutcomeUnknown,
            )

            raise ERPNextTaskOutcomeUnknown(operation.operation_id, "HTTP_504")

        def reconcile(self, operation_id: UUID) -> ERPNextTaskOperationReceipt:
            return ERPNextTaskOperationReceipt(
                operation_id=operation_id,
                payload_sha256="",
                status=ERPNextTaskOperationStatus.NOT_FOUND,
                task_name=None,
                task_modified=None,
                task=None,
                message="尚无回执",
            )

    monkeypatch.setattr(actions_module, "ERPNextTaskBridgeClient", UnknownRemoteResult)
    path = f"/api/v3/projects/{project_id}/action-invocations/{invocation_id}"
    first = client.post(f"{path}/execute")
    assert first.status_code == 200, first.text
    assert first.json()["status"] == "OUTCOME_UNKNOWN"

    retried = client.post(f"{path}/execute")
    assert retried.status_code == 200, retried.text
    assert retried.json()["status"] == "OUTCOME_UNKNOWN"
    assert execute_calls == 1

    checked = client.post(f"{path}/reconcile")
    assert checked.status_code == 200, checked.text
    assert checked.json()["status"] == "OUTCOME_UNKNOWN"
    assert execute_calls == 1
    assert operation_ids == [UUID(invocation_id)]


def test_background_recovery_reads_stale_remote_receipt_without_replaying_write(
    client: TestClient, monkeypatch: pytest.MonkeyPatch
) -> None:
    project_id, invocation_id, _source_id = _approved_update(client)
    captured: list[ERPNextTaskOperation] = []
    execute_calls = 0
    reconcile_calls = 0

    class CrashAfterRemoteCommit:
        def __init__(self, _profile: dict[str, object]) -> None:
            pass

        def execute(self, operation: ERPNextTaskOperation) -> None:
            nonlocal execute_calls
            execute_calls += 1
            captured.append(operation)
            raise SimulatedProcessCrash

        def reconcile(self, operation_id: UUID) -> ERPNextTaskOperationReceipt:
            nonlocal reconcile_calls
            reconcile_calls += 1
            operation = captured[0]
            assert operation_id == operation.operation_id
            return ERPNextTaskOperationReceipt(
                operation_id=operation_id,
                payload_sha256=operation.payload_sha256,
                status=ERPNextTaskOperationStatus.COMMITTED,
                task_name="TASK-00001",
                task_modified="2026-09-13 10:01:00",
                task={"name": "TASK-00001", "subject": "更新后的事项"},
            )

    monkeypatch.setattr(actions_module, "ERPNextTaskBridgeClient", CrashAfterRemoteCommit)
    with client.app.state.database.session_factory() as session:
        with pytest.raises(SimulatedProcessCrash):
            ActionService(session, settings=client.app.state.settings).execute(
                UUID(project_id), UUID(invocation_id)
            )
        session.rollback()

    old = datetime.now(UTC) - timedelta(minutes=5)
    with client.app.state.database.session_factory() as session:
        row = session.get(ActionInvocationRow, invocation_id)
        assert row is not None
        row.started_at = old
        row.updated_at = old
        session.commit()

    worker = ActionRecoveryWorker(client.app.state.database, client.app.state.settings)
    assert worker.reconcile_once(now=datetime.now(UTC)) == 1
    assert execute_calls == 1
    assert reconcile_calls == 1

    response = client.get(
        f"/api/v3/projects/{project_id}/action-invocations/{invocation_id}"
    )
    assert response.status_code == 200, response.text
    assert response.json()["status"] == "SUCCEEDED"
    assert worker.reconcile_once(now=datetime.now(UTC)) == 0


def test_background_recovery_audits_and_throttles_failed_receipt_reads(
    client: TestClient, monkeypatch: pytest.MonkeyPatch
) -> None:
    project_id, invocation_id, _source_id = _approved_update(client)
    calls = 0

    class UnavailableReceiptService:
        def __init__(self, _profile: dict[str, object]) -> None:
            pass

        def reconcile(self, _operation_id: UUID) -> ERPNextTaskOperationReceipt:
            nonlocal calls
            calls += 1
            raise TimeoutError("simulated read-only receipt timeout")

    monkeypatch.setattr(actions_module, "ERPNextTaskBridgeClient", UnavailableReceiptService)
    old = datetime.now(UTC) - timedelta(minutes=5)
    with client.app.state.database.session_factory() as session:
        row = session.get(ActionInvocationRow, invocation_id)
        assert row is not None
        row.status = "OUTCOME_UNKNOWN"
        row.started_at = old
        row.updated_at = old
        session.commit()

    worker = ActionRecoveryWorker(client.app.state.database, client.app.state.settings)
    worker.reconcile_once(now=datetime.now(UTC))
    worker.reconcile_once(now=datetime.now(UTC))
    assert calls == 1

    with client.app.state.database.session_factory() as session:
        row = session.get(ActionInvocationRow, invocation_id)
        assert row is not None
        assert row.status == "OUTCOME_UNKNOWN"
        logs = session.query(ActionLogRow).filter_by(invocation_id=invocation_id).all()
        assert any(log.event_type == "REMOTE_RECONCILIATION_FAILED" for log in logs)
