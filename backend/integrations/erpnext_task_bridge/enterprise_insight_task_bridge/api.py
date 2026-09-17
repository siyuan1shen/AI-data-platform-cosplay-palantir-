"""HTTP entry points for the narrow ERPNext Task bridge."""

from __future__ import annotations

import hashlib
import json
from typing import Any

import frappe

from .protocol import (
    ProtocolError,
    normalize_action,
    normalize_expected_modified,
    parse_payload,
    status_transition_allowed,
    validate_digest,
    validate_operation_id,
    validate_payload,
)

TERMINAL_STATUSES = {"COMMITTED", "NOT_FOUND", "CONFLICT", "REJECTED", "FAILED"}
_SAVEPOINT_PREFIX = "ei_task_op_"


@frappe.whitelist(methods=["POST"])
def execute_task_operation(
    operation_id: str | None = None,
    operation: str | None = None,
    action: str | None = None,
    payload: Any = None,
    payload_sha256: str | None = None,
    expected_modified: str | None = None,
) -> dict[str, Any]:
    """Execute one idempotent Task operation and return its stable receipt."""
    try:
        operation_id = validate_operation_id(operation_id)
        normalized_action = normalize_action(operation, action)
        parsed_payload = parse_payload(payload)
        digest = validate_digest(parsed_payload, payload_sha256)
        expected = normalize_expected_modified(expected_modified)
    except ProtocolError as exc:
        return _protocol_rejection(operation_id, exc)

    existing = _get_operation(operation_id)
    if existing:
        return _replay_or_conflict(existing, normalized_action, digest, expected)

    operation_doc = frappe.get_doc(
        {
            "doctype": "Task Operation",
            "operation_id": operation_id,
            "action": normalized_action,
            "payload_sha256": digest,
            "expected_modified": expected,
            "requested_by": frappe.session.user,
            "requested_on": frappe.utils.now_datetime(),
            "status": "PROCESSING",
            "response_json": "{}",
        }
    )
    try:
        operation_doc.insert(ignore_permissions=True)
    except frappe.DuplicateEntryError:
        # A unique violation aborts PostgreSQL's transaction. Roll back this request,
        # then read the winner's receipt; the losing insert changed no Task data.
        frappe.db.rollback()
        existing = _get_operation(operation_id)
        if not existing:
            raise
        return _replay_or_conflict(existing, normalized_action, digest, expected)

    savepoint = _SAVEPOINT_PREFIX + hashlib.sha256(operation_id.encode("utf-8")).hexdigest()[:20]
    frappe.db.savepoint(savepoint)
    try:
        validate_payload(normalized_action, parsed_payload, expected)
        receipt = _perform(normalized_action, parsed_payload, expected, operation_id, digest)
    except ProtocolError as exc:
        frappe.db.rollback(save_point=savepoint)
        receipt = _receipt(
            "REJECTED",
            operation_id,
            normalized_action,
            digest,
            error={"code": exc.code, "message": str(exc)},
        )
    except frappe.DoesNotExistError:
        frappe.db.rollback(save_point=savepoint)
        receipt = _receipt(
            "NOT_FOUND",
            operation_id,
            normalized_action,
            digest,
            error={"code": "TASK_NOT_FOUND", "message": "The requested Task does not exist."},
        )
    except frappe.PermissionError:
        frappe.db.rollback(save_point=savepoint)
        receipt = _receipt(
            "REJECTED",
            operation_id,
            normalized_action,
            digest,
            error={
                "code": "TASK_PERMISSION_DENIED",
                "message": "The authenticated user lacks the required Task permission.",
            },
        )
    except frappe.ValidationError as exc:
        frappe.db.rollback(save_point=savepoint)
        receipt = _receipt(
            "REJECTED",
            operation_id,
            normalized_action,
            digest,
            error={"code": "TASK_VALIDATION_FAILED", "message": _safe_error_message(exc)},
        )
    except Exception:
        frappe.db.rollback(save_point=savepoint)
        frappe.logger("erpnext_task_bridge").exception(
            "Unexpected Task bridge error for operation_id=%s", operation_id
        )
        receipt = _receipt(
            "FAILED",
            operation_id,
            normalized_action,
            digest,
            error={
                "code": "INTERNAL_ERROR",
                "message": "The operation failed; look up this operation_id before retrying.",
            },
        )

    _finalize(operation_doc, receipt)
    return receipt


@frappe.whitelist(methods=["GET"])
def get_task_operation(operation_id: str | None = None) -> dict[str, Any]:
    """Return an operation's original receipt, scoped to its initiating user."""
    try:
        operation_id = validate_operation_id(operation_id)
    except ProtocolError as exc:
        return _protocol_rejection(operation_id, exc)

    operation = _get_operation(operation_id)
    if not operation or not _may_inspect(operation):
        return {"status": "NOT_FOUND", "operation_id": operation_id}
    return _stored_receipt(operation)


def _perform(
    action: str,
    payload: dict[str, Any],
    expected_modified: str | None,
    operation_id: str,
    digest: str,
) -> dict[str, Any]:
    if action == "CREATE":
        task = frappe.get_doc({"doctype": "Task", "status": "Open", **payload}).insert()
        return _receipt("COMMITTED", operation_id, action, digest, task=_task_result(task))

    task_name = payload["name"]
    if action == "READ":
        task = frappe.get_doc("Task", task_name)
        task.check_permission("read")
        return _receipt("COMMITTED", operation_id, action, digest, task=_task_result(task))

    # Lock first, then compare the exact Frappe modified value and load the document.
    current_modified = frappe.db.get_value("Task", task_name, "modified", for_update=True)
    if current_modified is None:
        return _receipt(
            "NOT_FOUND",
            operation_id,
            action,
            digest,
            error={"code": "TASK_NOT_FOUND", "message": "The requested Task does not exist."},
        )
    current_modified = str(current_modified)
    if current_modified != expected_modified:
        return _receipt(
            "CONFLICT",
            operation_id,
            action,
            digest,
            task_name=task_name,
            current_modified=current_modified,
            error={
                "code": "STALE_EXPECTED_MODIFIED",
                "message": "Task changed since expected_modified; read it again before retrying.",
            },
        )

    task = frappe.get_doc("Task", task_name)
    task.check_permission("write")
    if str(task.modified) != expected_modified:
        return _receipt(
            "CONFLICT",
            operation_id,
            action,
            digest,
            task_name=task_name,
            current_modified=str(task.modified),
            error={
                "code": "STALE_EXPECTED_MODIFIED",
                "message": "Task changed since expected_modified; read it again before retrying.",
            },
        )

    if action == "CANCEL":
        if not status_transition_allowed(task.status, "Cancelled"):
            raise ProtocolError(
                "INVALID_STATUS_TRANSITION",
                f"Task status cannot transition from {task.status} to Cancelled.",
            )
        if task.status != "Cancelled":
            task.status = "Cancelled"
            task.save()
    else:
        target_status = payload.get("status", task.status)
        if not status_transition_allowed(task.status, target_status):
            raise ProtocolError(
                "INVALID_STATUS_TRANSITION",
                f"Task status cannot transition from {task.status} to {target_status}.",
            )
        changed = False
        for fieldname, value in payload.items():
            if fieldname == "name":
                continue
            if task.get(fieldname) != value:
                task.set(fieldname, value)
                changed = True
        if changed:
            task.save()

    return _receipt("COMMITTED", operation_id, action, digest, task=_task_result(task))


def _task_result(task: Any) -> dict[str, Any]:
    result: dict[str, Any] = {}
    for fieldname in (
        "name",
        "subject",
        "project",
        "description",
        "status",
        "priority",
        "exp_start_date",
        "exp_end_date",
        "expected_time",
        "progress",
        "is_milestone",
        "department",
        "type",
        "issue",
        "parent_task",
        "modified",
    ):
        value = task.get(fieldname)
        if value is not None and fieldname in {"exp_start_date", "exp_end_date", "modified"}:
            value = str(value)
        result[fieldname] = value
    return result


def _receipt(
    status: str,
    operation_id: str,
    action: str,
    digest: str,
    *,
    task: dict[str, Any] | None = None,
    task_name: str | None = None,
    current_modified: str | None = None,
    error: dict[str, str] | None = None,
) -> dict[str, Any]:
    task_name = task_name or (task or {}).get("name")
    result: dict[str, Any] = {
        "status": status,
        "operation_id": operation_id,
        "operation": action,
        "payload_sha256": digest,
        "task_name": task_name,
    }
    if current_modified is not None:
        result["current_modified"] = current_modified
    if task is not None:
        result["task"] = task
    if error is not None:
        result["error"] = error
    return result


def _protocol_rejection(operation_id: Any, exc: ProtocolError) -> dict[str, Any]:
    return {
        "status": "REJECTED",
        "operation_id": operation_id if isinstance(operation_id, str) else None,
        "error": {"code": exc.code, "message": str(exc)},
    }


def _get_operation(operation_id: str) -> Any | None:
    return frappe.db.get_value(
        "Task Operation",
        operation_id,
        [
            "operation_id",
            "action",
            "payload_sha256",
            "expected_modified",
            "requested_by",
            "status",
            "task_name",
            "response_json",
        ],
        as_dict=True,
    )


def _replay_or_conflict(
    existing: Any,
    action: str,
    digest: str,
    expected_modified: str | None,
) -> dict[str, Any]:
    if not _may_inspect(existing):
        return {
            "status": "CONFLICT",
            "operation_id": existing.operation_id,
            "error": {
                "code": "OPERATION_ID_IN_USE",
                "message": "This operation_id is already registered.",
            },
        }
    if (
        existing.action != action
        or existing.payload_sha256 != digest
        or (existing.expected_modified or None) != expected_modified
    ):
        return {
            "status": "CONFLICT",
            "operation_id": existing.operation_id,
            "error": {
                "code": "OPERATION_ID_REUSED",
                "message": "operation_id is bound to a different request.",
            },
        }
    return _stored_receipt(existing)


def _stored_receipt(operation: Any) -> dict[str, Any]:
    try:
        receipt = json.loads(operation.response_json or "{}")
    except (TypeError, json.JSONDecodeError):
        return {
            "status": "FAILED",
            "operation_id": operation.operation_id,
            "error": {
                "code": "CORRUPT_OPERATION_RECEIPT",
                "message": "Stored operation receipt is unreadable.",
            },
        }
    if isinstance(receipt, dict) and receipt.get("status") in TERMINAL_STATUSES | {"IN_PROGRESS"}:
        return receipt
    return {"status": "IN_PROGRESS", "operation_id": operation.operation_id}


def _finalize(operation: Any, receipt: dict[str, Any]) -> None:
    operation.status = receipt["status"]
    operation.task_name = receipt.get("task_name")
    operation.response_json = json.dumps(
        receipt,
        ensure_ascii=False,
        sort_keys=True,
        separators=(",", ":"),
        default=str,
    )
    operation.save(ignore_permissions=True)


def _may_inspect(operation: Any) -> bool:
    return operation.requested_by == frappe.session.user or "System Manager" in frappe.get_roles(
        frappe.session.user
    )


def _safe_error_message(exc: Exception) -> str:
    message = " ".join(str(exc).split())
    return message[:500] or "ERPNext rejected the Task operation."
