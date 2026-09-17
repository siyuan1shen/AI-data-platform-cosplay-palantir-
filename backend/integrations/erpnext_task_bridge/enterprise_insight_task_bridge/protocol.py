"""Frappe-independent request validation and idempotency primitives."""

from __future__ import annotations

import hashlib
import hmac
import json
import math
import re
from datetime import date
from typing import Any

ACTION_NAMES = frozenset({"CREATE", "UPDATE", "READ", "CANCEL"})
TASK_STATUSES = frozenset(
    {"Open", "Working", "Pending Review", "Overdue", "Template", "Completed", "Cancelled"}
)
PRIORITIES = frozenset({"Low", "Medium", "High", "Urgent"})
CREATE_FIELDS = frozenset(
    {
        "subject",
        "project",
        "description",
        "priority",
        "exp_start_date",
        "exp_end_date",
        "expected_time",
        "is_milestone",
        "department",
        "type",
        "issue",
        "parent_task",
    }
)
UPDATE_FIELDS = CREATE_FIELDS | frozenset({"progress", "status"})
MAX_PAYLOAD_BYTES = 256_000
_OPERATION_ID_RE = re.compile(r"[A-Za-z0-9][A-Za-z0-9._:-]{0,127}\Z")
_SHA256_RE = re.compile(r"[0-9a-f]{64}\Z")

STATUS_TRANSITIONS = {
    "Open": frozenset({"Working", "Pending Review", "Cancelled"}),
    "Working": frozenset({"Open", "Pending Review", "Completed", "Cancelled"}),
    "Pending Review": frozenset({"Working", "Completed", "Cancelled"}),
    "Overdue": frozenset({"Open", "Working", "Pending Review", "Completed", "Cancelled"}),
    "Template": frozenset(),
    "Completed": frozenset(),
    "Cancelled": frozenset(),
}


class ProtocolError(ValueError):
    def __init__(self, code: str, message: str):
        super().__init__(message)
        self.code = code


def validate_operation_id(value: Any) -> str:
    if not isinstance(value, str) or not _OPERATION_ID_RE.fullmatch(value):
        raise ProtocolError(
            "INVALID_OPERATION_ID",
            (
                "operation_id must be 1-128 safe ASCII characters: letters, digits, dot, "
                "underscore, colon, or hyphen."
            ),
        )
    return value


def normalize_action(operation: Any = None, action: Any = None) -> str:
    for value in (operation, action):
        if value is not None and not isinstance(value, str):
            raise ProtocolError("INVALID_ACTION", "operation/action must be a string.")
    values = [
        value.strip().upper()
        for value in (operation, action)
        if isinstance(value, str) and value.strip()
    ]
    if not values or any(value not in ACTION_NAMES for value in values):
        raise ProtocolError(
            "INVALID_ACTION", "operation/action must be CREATE, UPDATE, READ, or CANCEL."
        )
    if len(set(values)) != 1:
        raise ProtocolError(
            "ACTION_ALIAS_MISMATCH", "operation and action must identify the same operation."
        )
    return values[0]


def _reject_duplicate_keys(pairs: list[tuple[str, Any]]) -> dict[str, Any]:
    result: dict[str, Any] = {}
    for key, value in pairs:
        if key in result:
            raise ProtocolError("DUPLICATE_JSON_KEY", f"Duplicate JSON object key: {key}")
        result[key] = value
    return result


def parse_payload(value: Any) -> dict[str, Any]:
    if isinstance(value, str):
        try:
            if len(value.encode("utf-8")) > MAX_PAYLOAD_BYTES:
                raise ProtocolError("PAYLOAD_TOO_LARGE", "payload exceeds the bridge size limit.")
            value = json.loads(value, object_pairs_hook=_reject_duplicate_keys)
        except ProtocolError:
            raise
        except (json.JSONDecodeError, UnicodeEncodeError) as exc:
            raise ProtocolError("INVALID_PAYLOAD_JSON", "payload must be a JSON object.") from exc
    if not isinstance(value, dict) or any(not isinstance(key, str) for key in value):
        raise ProtocolError("INVALID_PAYLOAD", "payload must be a JSON object with string keys.")
    canonical_payload_json(value)
    return value


def canonical_payload_json(payload: dict[str, Any]) -> str:
    try:
        encoded = json.dumps(
            payload,
            ensure_ascii=False,
            sort_keys=True,
            separators=(",", ":"),
            allow_nan=False,
        )
    except (TypeError, ValueError) as exc:
        raise ProtocolError(
            "INVALID_PAYLOAD", "payload must contain only finite JSON values."
        ) from exc
    if len(encoded.encode("utf-8")) > MAX_PAYLOAD_BYTES:
        raise ProtocolError("PAYLOAD_TOO_LARGE", "payload exceeds the bridge size limit.")
    return encoded


def compute_payload_sha256(payload: dict[str, Any]) -> str:
    return hashlib.sha256(canonical_payload_json(payload).encode("utf-8")).hexdigest()


def validate_digest(payload: dict[str, Any], supplied_digest: Any) -> str:
    if not isinstance(supplied_digest, str) or not _SHA256_RE.fullmatch(supplied_digest):
        raise ProtocolError(
            "INVALID_PAYLOAD_SHA256",
            "payload_sha256 must be 64 lowercase hexadecimal characters.",
        )
    calculated = compute_payload_sha256(payload)
    if not hmac.compare_digest(calculated, supplied_digest):
        raise ProtocolError(
            "PAYLOAD_SHA256_MISMATCH", "payload_sha256 does not match canonical payload JSON."
        )
    return calculated


def normalize_expected_modified(value: Any) -> str | None:
    if value is None or value == "":
        return None
    if not isinstance(value, str) or len(value) > 80 or not value.strip():
        raise ProtocolError(
            "INVALID_EXPECTED_MODIFIED",
            "expected_modified must be the exact Frappe modified timestamp string.",
        )
    return value


def validate_payload(action: str, payload: dict[str, Any], expected_modified: str | None) -> None:
    if action == "CREATE":
        _reject_unknown(payload, CREATE_FIELDS)
        if not isinstance(payload.get("subject"), str) or not payload["subject"].strip():
            raise ProtocolError("SUBJECT_REQUIRED", "CREATE payload requires a non-empty subject.")
        if expected_modified is not None:
            raise ProtocolError("UNEXPECTED_MODIFIED", "CREATE does not accept expected_modified.")
        fields = CREATE_FIELDS
    elif action == "UPDATE":
        _validate_target_payload(payload, UPDATE_FIELDS)
        changed_fields = set(payload) - {"name"}
        if not changed_fields:
            raise ProtocolError("EMPTY_UPDATE", "UPDATE requires at least one allowed Task field.")
        _require_expected_modified(expected_modified)
        fields = UPDATE_FIELDS
    elif action == "READ":
        _validate_target_payload(payload, frozenset())
        if set(payload) != {"name"}:
            raise ProtocolError("INVALID_READ_PAYLOAD", "READ payload must contain only name.")
        if expected_modified is not None:
            raise ProtocolError("UNEXPECTED_MODIFIED", "READ does not accept expected_modified.")
        fields = frozenset()
    elif action == "CANCEL":
        _validate_target_payload(payload, frozenset())
        if set(payload) != {"name"}:
            raise ProtocolError("INVALID_CANCEL_PAYLOAD", "CANCEL payload must contain only name.")
        _require_expected_modified(expected_modified)
        fields = frozenset()
    else:
        raise ProtocolError("INVALID_ACTION", "Unsupported operation.")

    for fieldname in fields & set(payload):
        _validate_field(fieldname, payload[fieldname])


def _reject_unknown(payload: dict[str, Any], allowed: frozenset[str]) -> None:
    unknown = set(payload) - allowed
    if unknown:
        raise ProtocolError(
            "FIELD_NOT_ALLOWED", f"Task field(s) not allowed: {', '.join(sorted(unknown))}"
        )


def _validate_target_payload(payload: dict[str, Any], allowed_fields: frozenset[str]) -> None:
    if (
        not isinstance(payload.get("name"), str)
        or not payload["name"].strip()
        or len(payload["name"]) > 140
    ):
        raise ProtocolError("TASK_NAME_REQUIRED", "payload requires a valid Task name.")
    _reject_unknown(payload, allowed_fields | {"name"})


def _require_expected_modified(value: str | None) -> None:
    if value is None:
        raise ProtocolError(
            "EXPECTED_MODIFIED_REQUIRED", "UPDATE and CANCEL require expected_modified."
        )


def _validate_field(fieldname: str, value: Any) -> None:
    if value is None:
        return
    if fieldname == "subject":
        if not isinstance(value, str) or not value.strip() or len(value) > 140:
            raise ProtocolError(
                "INVALID_SUBJECT", "subject must be a non-empty string of at most 140 characters."
            )
    elif fieldname in {"project", "department", "type", "issue", "parent_task"}:
        if not isinstance(value, str) or not value.strip() or len(value) > 140:
            raise ProtocolError(
                "INVALID_LINK", f"{fieldname} must be a non-empty Frappe document name or null."
            )
    elif fieldname == "description":
        if not isinstance(value, str) or len(value) > 20_000:
            raise ProtocolError(
                "INVALID_DESCRIPTION", "description must be text of at most 20000 characters."
            )
    elif fieldname == "priority":
        if not isinstance(value, str) or value not in PRIORITIES:
            raise ProtocolError(
                "INVALID_PRIORITY", "priority must be Low, Medium, High, or Urgent."
            )
    elif fieldname in {"exp_start_date", "exp_end_date"}:
        if not isinstance(value, str):
            raise ProtocolError("INVALID_DATE", f"{fieldname} must use YYYY-MM-DD or be null.")
        try:
            parsed = date.fromisoformat(value)
        except ValueError as exc:
            raise ProtocolError(
                "INVALID_DATE", f"{fieldname} must use YYYY-MM-DD or be null."
            ) from exc
        if parsed.isoformat() != value:
            raise ProtocolError("INVALID_DATE", f"{fieldname} must use YYYY-MM-DD or be null.")
    elif fieldname in {"expected_time", "progress"}:
        if (
            isinstance(value, bool)
            or not isinstance(value, (int, float))
            or not math.isfinite(value)
        ):
            raise ProtocolError("INVALID_NUMBER", f"{fieldname} must be a finite number.")
        minimum, maximum = (0, 100) if fieldname == "progress" else (0, 100_000)
        if not minimum <= value <= maximum:
            raise ProtocolError(
                "NUMBER_OUT_OF_RANGE", f"{fieldname} must be between {minimum} and {maximum}."
            )
    elif fieldname == "is_milestone" and not isinstance(value, bool):
        raise ProtocolError("INVALID_BOOLEAN", "is_milestone must be true, false, or null.")
    elif fieldname == "status" and (not isinstance(value, str) or value not in TASK_STATUSES):
        raise ProtocolError("INVALID_STATUS", "status is not a native ERPNext Task status.")


def status_transition_allowed(current: str, target: str) -> bool:
    if current == target:
        return current in TASK_STATUSES
    return target in STATUS_TRANSITIONS.get(current, frozenset())
