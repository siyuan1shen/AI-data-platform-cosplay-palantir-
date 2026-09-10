from __future__ import annotations

from typing import Any

from enterprise_insight_backend.errors import DomainError

SUPPORTED_TRANSFORMS = {"strip", "lower", "upper", "int", "float", "bool", "empty_to_null"}


def validate_transform_expression(expression: str | None) -> None:
    if not expression:
        return
    unknown = [step for step in _steps(expression) if step not in SUPPORTED_TRANSFORMS]
    if unknown:
        raise DomainError(
            "MAPPING_TRANSFORM_UNSUPPORTED",
            "转换表达式包含不支持的步骤。",
            status_code=422,
            details=[{"unsupported": unknown, "supported": sorted(SUPPORTED_TRANSFORMS)}],
        )


def apply_transform(value: Any, expression: str | None) -> Any:
    validate_transform_expression(expression)
    result = value
    for step in _steps(expression or ""):
        try:
            if step == "strip":
                result = str(result).strip()
            elif step == "lower":
                result = str(result).lower()
            elif step == "upper":
                result = str(result).upper()
            elif step == "int":
                result = int(str(result).strip())
            elif step == "float":
                result = float(str(result).strip())
            elif step == "bool":
                result = _boolean(result)
            elif step == "empty_to_null" and (result is None or str(result).strip() == ""):
                result = None
        except (TypeError, ValueError) as exc:
            raise DomainError(
                "MAPPING_TRANSFORM_FAILED",
                f"值 {value!r} 无法执行转换步骤 {step}。",
                status_code=422,
            ) from exc
    return result


def _steps(expression: str) -> list[str]:
    return [item.strip().lower() for item in expression.split("|") if item.strip()]


def _boolean(value: Any) -> bool:
    if isinstance(value, bool):
        return value
    normalized = str(value).strip().lower()
    if normalized in {"1", "true", "yes", "y", "是", "真"}:
        return True
    if normalized in {"0", "false", "no", "n", "否", "假"}:
        return False
    raise ValueError("not a boolean")
