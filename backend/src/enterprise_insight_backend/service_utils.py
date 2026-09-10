from __future__ import annotations

from datetime import UTC, datetime
from typing import Any
from uuid import UUID

from enterprise_insight_backend.errors import DomainError


def as_uuid(value: str) -> UUID:
    return UUID(value)


def now_utc() -> datetime:
    return datetime.now(UTC)


def require_revision(actual: int, expected: int, *, resource: str) -> None:
    if actual != expected:
        raise DomainError(
            "REVISION_CONFLICT",
            f"{resource} 已被其他操作修改，请刷新后重试。",
            status_code=409,
            details=[{"expected_revision": expected, "actual_revision": actual}],
        )


def json_ready(value: Any) -> Any:
    if isinstance(value, BaseException):
        return str(value)
    if isinstance(value, UUID):
        return str(value)
    if isinstance(value, datetime):
        return value.isoformat()
    if isinstance(value, dict):
        return {str(key): json_ready(item) for key, item in value.items()}
    if isinstance(value, (list, tuple, set)):
        return [json_ready(item) for item in value]
    return value
