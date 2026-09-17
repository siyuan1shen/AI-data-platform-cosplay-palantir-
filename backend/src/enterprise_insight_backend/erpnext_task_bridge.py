from __future__ import annotations

import hashlib
import json
import math
from dataclasses import dataclass
from enum import StrEnum
from typing import Any
from urllib.parse import urlsplit, urlunsplit
from uuid import UUID

import httpx

from enterprise_insight_backend.errors import DomainError

_ALLOWED_ACTIONS = {"create", "update", "cancel"}
_OUTCOME_UNKNOWN_HTTP_CODES = {408, 425, 429, 500, 502, 503, 504}
_CREATE_FIELDS = {
    "subject",
    "project",
    "status",
    "priority",
    "description",
    "exp_start_date",
    "exp_end_date",
    "expected_time",
}
_UPDATE_FIELDS = _CREATE_FIELDS | {"progress"}
_TASK_STATUSES = {"Open", "Working", "Pending Review", "Completed", "Cancelled"}
_TASK_PRIORITIES = {"Low", "Medium", "High", "Urgent"}


class ERPNextTaskOperationStatus(StrEnum):
    COMMITTED = "COMMITTED"
    IN_PROGRESS = "IN_PROGRESS"
    NOT_FOUND = "NOT_FOUND"
    CONFLICT = "CONFLICT"
    REJECTED = "REJECTED"
    FAILED = "FAILED"
    UNKNOWN = "UNKNOWN"


@dataclass(frozen=True, slots=True)
class ERPNextTaskOperation:
    operation_id: UUID
    action: str
    payload: dict[str, Any]
    expected_modified: str | None = None

    def __post_init__(self) -> None:
        if self.action not in _ALLOWED_ACTIONS:
            raise DomainError(
                "ERPNEXT_TASK_ACTION_UNSUPPORTED",
                "ERPNext Task 动作只支持 create、update、cancel。",
                status_code=422,
            )
        if self.action in {"update", "cancel"} and not self.expected_modified:
            raise DomainError(
                "ERPNEXT_TASK_EXPECTED_VERSION_REQUIRED",
                "更新或取消 ERPNext Task 必须提供读取时的 modified 版本。",
                status_code=422,
            )
        if self.action in {"update", "cancel"} and not self.payload.get("task_name"):
            raise DomainError(
                "ERPNEXT_TASK_NAME_REQUIRED",
                "更新或取消 ERPNext Task 必须提供外部任务编号。",
                status_code=422,
            )
        if self.action == "create":
            allowed_fields = _CREATE_FIELDS
            if not all(
                isinstance(self.payload.get(key), str) and self.payload[key].strip()
                for key in ("subject", "project")
            ):
                raise DomainError(
                    "ERPNEXT_TASK_REQUIRED_FIELDS_MISSING",
                    "创建 ERPNext Task 必须提供 subject 和 project。",
                    status_code=422,
                )
        elif self.action == "update":
            allowed_fields = _UPDATE_FIELDS | {"task_name"}
            if not any(key in self.payload for key in _UPDATE_FIELDS):
                raise DomainError(
                    "ERPNEXT_TASK_UPDATE_FIELDS_MISSING",
                    "更新 ERPNext Task 至少要修改一个允许的字段。",
                    status_code=422,
                )
        else:
            allowed_fields = {"task_name"}
        unknown = set(self.payload) - allowed_fields
        if unknown:
            raise DomainError(
                "ERPNEXT_TASK_FIELDS_UNSUPPORTED",
                "ERPNext Task 操作包含未允许的字段。",
                status_code=422,
                details=[{"fields": sorted(unknown)}],
            )
        if self.action != "cancel":
            status = self.payload.get("status")
            if status is not None and status not in _TASK_STATUSES:
                raise DomainError(
                    "ERPNEXT_TASK_STATUS_INVALID",
                    "ERPNext Task 状态值不在允许范围内。",
                    status_code=422,
                )
            priority = self.payload.get("priority")
            if priority is not None and priority not in _TASK_PRIORITIES:
                raise DomainError(
                    "ERPNEXT_TASK_PRIORITY_INVALID",
                    "ERPNext Task 优先级值不在允许范围内。",
                    status_code=422,
                )
            progress = self.payload.get("progress")
            if progress is not None and (
                not isinstance(progress, (int, float))
                or isinstance(progress, bool)
                or not math.isfinite(progress)
                or not 0 <= progress <= 100
            ):
                raise DomainError(
                    "ERPNEXT_TASK_PROGRESS_INVALID",
                    "ERPNext Task progress 必须是 0–100 的数字。",
                    status_code=422,
                )

    @property
    def payload_sha256(self) -> str:
        # The Frappe bridge hashes the canonical payload only. Action and
        # expected_modified are stored and compared as separate immutable
        # operation fields, so both sides must use this exact contract. The
        # platform-facing task_name field maps to Frappe's native name field.
        canonical = json.dumps(
            self.remote_payload,
            ensure_ascii=False,
            sort_keys=True,
            separators=(",", ":"),
            allow_nan=False,
        ).encode("utf-8")
        return hashlib.sha256(canonical).hexdigest()

    @property
    def remote_payload(self) -> dict[str, Any]:
        return {
            ("name" if key == "task_name" else key): value
            for key, value in self.payload.items()
        }


@dataclass(frozen=True, slots=True)
class ERPNextTaskOperationReceipt:
    operation_id: UUID
    payload_sha256: str
    status: ERPNextTaskOperationStatus
    task_name: str | None
    task_modified: str | None
    task: dict[str, Any] | None
    message: str | None = None


class ERPNextTaskOutcomeUnknown(RuntimeError):
    """The remote server may have committed; callers must reconcile, not retry blindly."""

    def __init__(self, operation_id: UUID, reason_code: str) -> None:
        super().__init__("ERPNext 未确认 Task 动作是否已提交；请先按 operation_id 回查。")
        self.operation_id = operation_id
        self.reason_code = reason_code


class ERPNextTaskBridgeClient:
    """Client for the companion Frappe app's idempotent Task operation API.

    Calls are intentionally not retried. A timeout or retryable server response may
    occur after a remote transaction committed, so callers must use ``reconcile``.
    """

    def __init__(
        self,
        profile: dict[str, Any],
        *,
        client: httpx.Client | None = None,
    ) -> None:
        self.profile = profile
        self._client = client
        self._base_url = self._validate_base_url(profile.get("base_url"))
        api_key, api_secret = profile.get("api_key"), profile.get("api_secret")
        if not isinstance(api_key, str) or not api_key.strip() or not isinstance(
            api_secret, str
        ) or not api_secret.strip():
            raise DomainError(
                "ERPNEXT_CREDENTIALS_REQUIRED",
                "ERPNext Task 执行需要 API key 和 API secret。",
                status_code=422,
            )
        self._headers = {
            "Accept": "application/json",
            "Content-Type": "application/json",
            "Authorization": f"token {api_key.strip()}:{api_secret.strip()}",
        }
        self._timeout = self._validate_timeout(profile.get("timeout_seconds", 15))

    def execute(self, operation: ERPNextTaskOperation) -> ERPNextTaskOperationReceipt:
        body = {
            "operation_id": str(operation.operation_id),
            "action": operation.action,
            "payload": operation.remote_payload,
            "payload_sha256": operation.payload_sha256,
            "expected_modified": operation.expected_modified,
        }
        try:
            response = self._request(
                "POST",
                "execute_task_operation",
                json_body=body,
            )
        except (httpx.TimeoutException, httpx.NetworkError) as exc:
            raise ERPNextTaskOutcomeUnknown(
                operation.operation_id, type(exc).__name__
            ) from exc
        if response.status_code in _OUTCOME_UNKNOWN_HTTP_CODES:
            raise ERPNextTaskOutcomeUnknown(
                operation.operation_id, f"HTTP_{response.status_code}"
            )
        if response.status_code in {409, 412}:
            return self._parse_receipt(response, operation, expected_status="CONFLICT")
        if response.status_code >= 400:
            raise DomainError(
                "ERPNEXT_TASK_OPERATION_REJECTED",
                "ERPNext 拒绝了 Task 操作；没有自动重试。",
                status_code=422,
                details=[{"remote_status": response.status_code}],
            )
        return self._parse_receipt(response, operation, expected_status="COMMITTED")

    def reconcile(self, operation_id: UUID) -> ERPNextTaskOperationReceipt:
        try:
            response = self._request(
                "GET",
                "get_task_operation",
                params={"operation_id": str(operation_id)},
            )
        except (httpx.TimeoutException, httpx.NetworkError) as exc:
            raise ERPNextTaskOutcomeUnknown(operation_id, type(exc).__name__) from exc
        if response.status_code in _OUTCOME_UNKNOWN_HTTP_CODES:
            raise ERPNextTaskOutcomeUnknown(operation_id, f"HTTP_{response.status_code}")
        if response.status_code == 404:
            return ERPNextTaskOperationReceipt(
                operation_id=operation_id,
                payload_sha256="",
                status=ERPNextTaskOperationStatus.NOT_FOUND,
                task_name=None,
                task_modified=None,
                task=None,
                message="远端当前没有可见回执；不能据此假定此前请求从未提交。",
            )
        if response.status_code >= 400:
            raise DomainError(
                "ERPNEXT_TASK_RECONCILIATION_FAILED",
                "无法从 ERPNext 回查 Task 操作结果。",
                status_code=502,
                details=[{"remote_status": response.status_code}],
            )
        return self._parse_receipt(response, operation_id=operation_id)

    def _request(
        self,
        method: str,
        method_name: str,
        *,
        json_body: dict[str, Any] | None = None,
        params: dict[str, str] | None = None,
    ) -> httpx.Response:
        url = f"{self._base_url}/api/method/enterprise_insight_task_bridge.api.{method_name}"
        if self._client is not None:
            try:
                return self._client.request(
                    method,
                    url,
                    headers=self._headers,
                    json=json_body,
                    params=params,
                    timeout=self._timeout,
                )
            except httpx.TimeoutException:
                raise
            except httpx.NetworkError:
                raise
            except httpx.HTTPError as exc:
                raise DomainError(
                    "ERPNEXT_TASK_TRANSPORT_FAILED",
                    "ERPNext Task 通信失败，未自动重试。",
                    status_code=502,
                    details=[{"exception": type(exc).__name__}],
                ) from exc
        try:
            with httpx.Client(timeout=self._timeout) as client:
                return client.request(
                    method,
                    url,
                    headers=self._headers,
                    json=json_body,
                    params=params,
                )
        except (httpx.TimeoutException, httpx.NetworkError):
            raise
        except httpx.HTTPError as exc:
            raise DomainError(
                "ERPNEXT_TASK_TRANSPORT_FAILED",
                "ERPNext Task 通信失败，未自动重试。",
                status_code=502,
                details=[{"exception": type(exc).__name__}],
            ) from exc

    @staticmethod
    def _parse_receipt(
        response: httpx.Response,
        operation: ERPNextTaskOperation | None = None,
        *,
        operation_id: UUID | None = None,
        expected_status: str | None = None,
    ) -> ERPNextTaskOperationReceipt:
        try:
            envelope = response.json()
            payload = envelope.get("message") if isinstance(envelope, dict) else None
            if not isinstance(payload, dict):
                raise ValueError("Frappe response has no message object")
            returned_id = UUID(str(payload.get("operation_id")))
            returned_hash = str(payload.get("payload_sha256") or "")
            status_value = ERPNextTaskOperationStatus(str(payload.get("status")))
        except (ValueError, TypeError, AttributeError) as exc:
            raise DomainError(
                "ERPNEXT_TASK_RECEIPT_INVALID",
                "ERPNext 返回的操作回执格式无效。",
                status_code=502,
            ) from exc
        expected_id = operation.operation_id if operation else operation_id
        if expected_id is None or returned_id != expected_id:
            raise DomainError(
                "ERPNEXT_TASK_RECEIPT_ID_MISMATCH",
                "ERPNext 返回了不匹配的 operation_id。",
                status_code=502,
            )
        if operation is not None and returned_hash != operation.payload_sha256:
            raise DomainError(
                "ERPNEXT_TASK_RECEIPT_HASH_MISMATCH",
                "ERPNext 回执对应的操作内容与本次请求不一致。",
                status_code=409,
            )
        if expected_status and status_value.value != expected_status:
            if status_value == ERPNextTaskOperationStatus.CONFLICT:
                raise DomainError(
                    "ERPNEXT_TASK_OPERATION_CONFLICT",
                    "ERPNext 检测到幂等键冲突或目标版本已变化。",
                    status_code=409,
                )
            if status_value == ERPNextTaskOperationStatus.UNKNOWN:
                raise ERPNextTaskOutcomeUnknown(returned_id, "REMOTE_STATUS_UNKNOWN")
            raise DomainError(
                "ERPNEXT_TASK_OPERATION_NOT_COMMITTED",
                "ERPNext 没有确认 Task 操作已提交。",
                status_code=409,
            )
        return ERPNextTaskOperationReceipt(
            operation_id=returned_id,
            payload_sha256=returned_hash,
            status=status_value,
            task_name=_optional_string(payload.get("task_name")),
            task_modified=_optional_string(payload.get("task_modified")),
            task=payload.get("task") if isinstance(payload.get("task"), dict) else None,
            message=_optional_string(payload.get("message")),
        )

    @staticmethod
    def _validate_base_url(value: Any) -> str:
        if not isinstance(value, str) or not value.strip():
            raise DomainError(
                "ERPNEXT_BASE_URL_REQUIRED", "ERPNext 连接配置必须包含 base_url。", status_code=422
            )
        parsed = urlsplit(value.strip().rstrip("/"))
        if (
            parsed.scheme not in {"http", "https"}
            or not parsed.netloc
            or parsed.username
            or parsed.password
            or parsed.query
            or parsed.fragment
        ):
            raise DomainError(
                "ERPNEXT_BASE_URL_INVALID",
                "ERPNext base_url 必须是无凭据、无查询参数的 HTTP(S) 地址。",
                status_code=422,
            )
        return urlunsplit((parsed.scheme, parsed.netloc, parsed.path.rstrip("/"), "", ""))

    @staticmethod
    def _validate_timeout(value: Any) -> float:
        try:
            timeout = float(value)
        except (TypeError, ValueError) as exc:
            raise DomainError(
                "ERPNEXT_TIMEOUT_INVALID", "ERPNext timeout_seconds 必须是数字。", status_code=422
            ) from exc
        if not 0.1 <= timeout <= 120:
            raise DomainError(
                "ERPNEXT_TIMEOUT_INVALID",
                "ERPNext timeout_seconds 必须在 0.1 到 120 秒之间。",
                status_code=422,
            )
        return timeout


def _optional_string(value: Any) -> str | None:
    return value if isinstance(value, str) and value else None
