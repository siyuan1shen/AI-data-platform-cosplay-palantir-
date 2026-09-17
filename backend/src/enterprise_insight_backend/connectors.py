from __future__ import annotations

import re
import sqlite3
import time
from collections.abc import Callable
from dataclasses import dataclass
from pathlib import Path
from typing import Any, Protocol, TypeVar
from urllib.parse import urlparse

import httpx

from enterprise_insight_backend.errors import DomainError

IDENTIFIER = re.compile(r"^[A-Za-z_][A-Za-z0-9_$]*$")
T = TypeVar("T")


@dataclass(slots=True)
class ConnectorPage:
    columns: list[str]
    rows: list[dict[str, Any]]
    next_watermark: Any | None = None
    next_tie_breaker: Any | None = None


class ReadOnlyConnector(Protocol):
    def test(self) -> tuple[bool, str]: ...

    def extract(
        self,
        asset_key: str,
        *,
        limit: int,
        watermark_column: str | None,
        tie_breaker_column: str | None,
        after_watermark: Any | None,
        after_tie_breaker: Any | None,
    ) -> ConnectorPage: ...


class SQLiteConnector:
    """Local read-only reference connector for SQLite exports and replicas."""

    def __init__(self, profile: dict[str, Any]) -> None:
        self.profile = profile

    def test(self) -> tuple[bool, str]:
        try:
            with self._connect() as connection:
                connection.execute("SELECT 1").fetchone()
            return True, "SQLite 只读连接有效。"
        except DomainError as exc:
            return False, exc.message
        except (OSError, sqlite3.Error) as exc:
            return False, f"SQLite 连接失败：{type(exc).__name__}。"

    def extract(
        self,
        asset_key: str,
        *,
        limit: int,
        watermark_column: str | None,
        tie_breaker_column: str | None,
        after_watermark: Any | None,
        after_tie_breaker: Any | None,
    ) -> ConnectorPage:
        table_name = self._asset(asset_key)
        for name in (watermark_column, tie_breaker_column):
            if name is not None:
                self._identifier(name)
        if (watermark_column is None) != (tie_breaker_column is None):
            raise DomainError(
                "CONNECTOR_COMPOSITE_WATERMARK_REQUIRED",
                "增量读取必须同时指定水位字段和唯一排序字段。",
                status_code=422,
            )
        if (after_watermark is None) != (after_tie_breaker is None):
            raise DomainError(
                "CONNECTOR_COMPOSITE_CURSOR_REQUIRED",
                "继续增量读取必须同时提供上一水位值和上一唯一排序值。",
                status_code=422,
            )
        query = f"SELECT * FROM {self._quote(table_name)}"
        parameters: list[Any] = []
        if watermark_column is not None and tie_breaker_column is not None:
            quoted_watermark = self._quote(watermark_column)
            quoted_tie_breaker = self._quote(tie_breaker_column)
            if after_watermark is not None:
                query += f" WHERE ({quoted_watermark}, {quoted_tie_breaker}) > (?, ?)"
                parameters.extend([after_watermark, after_tie_breaker])
            query += f" ORDER BY {quoted_watermark}, {quoted_tie_breaker}"
        query += " LIMIT ?"
        parameters.append(limit)
        try:
            with self._connect() as connection:
                cursor = connection.execute(query, parameters)
                columns = [item[0] for item in cursor.description or []]
                raw_rows = cursor.fetchall()
        except DomainError:
            raise
        except sqlite3.Error as exc:
            raise DomainError(
                "CONNECTOR_EXTRACT_FAILED",
                "SQLite 只读提取失败。",
                status_code=502,
                details=[{"exception": type(exc).__name__}],
            ) from exc
        rows = [dict(zip(columns, row, strict=True)) for row in raw_rows]
        next_watermark = rows[-1].get(watermark_column) if rows and watermark_column else None
        next_tie_breaker = rows[-1].get(tie_breaker_column) if rows and tie_breaker_column else None
        return ConnectorPage(columns, rows, next_watermark, next_tie_breaker)

    def _connect(self) -> sqlite3.Connection:
        value = self.profile.get("database_path")
        if not isinstance(value, str) or not value.strip():
            raise DomainError(
                "SQLITE_CONFIG_INCOMPLETE",
                "SQLite 连接配置必须包含 database_path。",
                status_code=422,
            )
        path = Path(value).expanduser().resolve()
        if not path.is_file():
            raise DomainError(
                "SQLITE_DATABASE_NOT_FOUND",
                "SQLite 数据库文件不存在。",
                status_code=422,
            )
        connection = sqlite3.connect(f"{path.as_uri()}?mode=ro", uri=True, timeout=10)
        connection.execute("PRAGMA query_only = ON")
        return connection

    @staticmethod
    def _asset(asset_key: str) -> str:
        if "." in asset_key:
            raise DomainError(
                "SQLITE_ASSET_INVALID",
                "SQLite 资产标识只能是表名，不能包含数据库或 schema 前缀。",
                status_code=422,
            )
        return SQLiteConnector._identifier(asset_key)

    @staticmethod
    def _identifier(value: str) -> str:
        if not IDENTIFIER.fullmatch(value):
            raise DomainError(
                "CONNECTOR_IDENTIFIER_INVALID",
                "表名或字段名不符合安全标识符规则。",
                status_code=422,
            )
        return value

    @staticmethod
    def _quote(value: str) -> str:
        return f'"{value.replace(chr(34), chr(34) * 2)}"'


class PostgreSQLConnector:
    """Small read-only reference connector; it never writes to the source database."""

    def __init__(self, profile: dict[str, Any]) -> None:
        self.profile = profile

    def test(self) -> tuple[bool, str]:
        try:
            psycopg, _ = self._driver()

            def probe() -> None:
                with psycopg.connect(**self._connection_kwargs()) as connection:
                    with connection.cursor() as cursor:
                        cursor.execute("SET TRANSACTION READ ONLY")
                        cursor.execute("SELECT 1")
                        cursor.fetchone()

            self._run_with_retry(psycopg, probe)
            return True, "PostgreSQL 只读连接有效。"
        except DomainError as exc:
            return False, exc.message
        except Exception as exc:  # pragma: no cover - depends on external database
            return False, f"PostgreSQL 连接失败：{type(exc).__name__}。"

    def extract(
        self,
        asset_key: str,
        *,
        limit: int,
        watermark_column: str | None,
        tie_breaker_column: str | None,
        after_watermark: Any | None,
        after_tie_breaker: Any | None,
    ) -> ConnectorPage:
        psycopg, sql = self._driver()
        schema_name, table_name = self._asset(asset_key)
        for name in (watermark_column, tie_breaker_column):
            if name is not None:
                self._identifier(name)
        if (watermark_column is None) != (tie_breaker_column is None):
            raise DomainError(
                "CONNECTOR_COMPOSITE_WATERMARK_REQUIRED",
                "增量读取必须同时指定水位字段和唯一排序字段。",
                status_code=422,
            )
        if (after_watermark is None) != (after_tie_breaker is None):
            raise DomainError(
                "CONNECTOR_COMPOSITE_CURSOR_REQUIRED",
                "继续增量读取必须同时提供上一水位值和上一唯一排序值。",
                status_code=422,
            )
        if after_watermark is not None and watermark_column is None:
            raise DomainError(
                "CONNECTOR_WATERMARK_COLUMNS_REQUIRED",
                "提供增量游标时必须同时指定水位字段和唯一排序字段。",
                status_code=422,
            )
        table = sql.Identifier(schema_name, table_name)
        query = sql.SQL("SELECT * FROM {} ").format(table)
        parameters: list[Any] = []
        if watermark_column is not None:
            watermark = sql.Identifier(watermark_column)
            tie_breaker = sql.Identifier(tie_breaker_column)
            if after_watermark is not None:
                query += sql.SQL("WHERE ({}, {}) > (%s, %s) ").format(
                    watermark, tie_breaker
                )
                parameters.extend([after_watermark, after_tie_breaker])
            query += sql.SQL("ORDER BY {}, {} ").format(watermark, tie_breaker)
        query += sql.SQL("LIMIT %s")
        parameters.append(limit)
        def fetch() -> tuple[list[str], list[tuple[Any, ...]]]:
            with psycopg.connect(**self._connection_kwargs()) as connection:
                with connection.cursor() as cursor:
                    cursor.execute("SET TRANSACTION READ ONLY")
                    cursor.execute(query, parameters)
                    columns = [item.name for item in cursor.description or []]
                    raw_rows = cursor.fetchall()
            return columns, raw_rows

        try:
            columns, raw_rows = self._run_with_retry(psycopg, fetch)
        except DomainError:
            raise
        except Exception as exc:  # pragma: no cover - defensive boundary
            raise DomainError(
                "CONNECTOR_EXTRACT_FAILED",
                "PostgreSQL 只读提取失败。",
                status_code=502,
                details=[{"exception": type(exc).__name__}],
            ) from exc
        rows = [dict(zip(columns, values, strict=True)) for values in raw_rows]
        next_watermark = None
        next_tie_breaker = None
        if rows and watermark_column and tie_breaker_column:
            next_watermark = rows[-1].get(watermark_column)
            next_tie_breaker = rows[-1].get(tie_breaker_column)
        return ConnectorPage(columns, rows, next_watermark, next_tie_breaker)

    def _run_with_retry(self, psycopg: Any, operation: Callable[[], T]) -> T:
        max_retries, backoff_seconds = self._retry_options()
        for attempt in range(max_retries + 1):
            try:
                return operation()
            except DomainError:
                raise
            except Exception as exc:
                if attempt >= max_retries or not self._is_transient(psycopg, exc):
                    raise
                if backoff_seconds:
                    time.sleep(backoff_seconds * (attempt + 1))
        raise AssertionError("retry loop must return or raise")

    def _retry_options(self) -> tuple[int, float]:
        try:
            max_retries = int(self.profile.get("max_retries", 2))
            backoff_ms = int(self.profile.get("retry_backoff_ms", 100))
        except (TypeError, ValueError) as exc:
            raise DomainError(
                "CONNECTOR_CONFIG_INVALID",
                "连接器重试参数必须是整数。",
                status_code=422,
            ) from exc
        if not 0 <= max_retries <= 5 or not 0 <= backoff_ms <= 2000:
            raise DomainError(
                "CONNECTOR_CONFIG_INVALID",
                "max_retries 必须在 0–5，retry_backoff_ms 必须在 0–2000。",
                status_code=422,
            )
        return max_retries, backoff_ms / 1000

    @staticmethod
    def _is_transient(psycopg: Any, exc: Exception) -> bool:
        # Server SQLSTATE is more specific than the Python base class: e.g.
        # authentication/database errors may also subclass OperationalError.
        sqlstate = getattr(exc, "sqlstate", None)
        if isinstance(sqlstate, str) and sqlstate:
            return sqlstate.startswith("08") or sqlstate in {"40001", "40P01", "55P03"}
        transient_types = tuple(
            error_type
            for error_type in (
                getattr(psycopg, "OperationalError", None),
                getattr(psycopg, "InterfaceError", None),
            )
            if isinstance(error_type, type)
        )
        if transient_types and isinstance(exc, transient_types):
            return True
        return False

    @staticmethod
    def _driver() -> tuple[Any, Any]:
        try:
            import psycopg  # type: ignore[import-not-found]
            from psycopg import sql
        except ImportError as exc:
            raise DomainError(
                "POSTGRESQL_DRIVER_REQUIRED",
                "尚未安装 PostgreSQL 连接器依赖 psycopg。",
                status_code=409,
            ) from exc
        return psycopg, sql

    def _connection_kwargs(self) -> dict[str, Any]:
        required = ("host", "database", "user")
        missing = [key for key in required if not self.profile.get(key)]
        if missing:
            raise DomainError(
                "CONNECTOR_CONFIG_INCOMPLETE",
                "PostgreSQL 连接配置不完整。",
                status_code=422,
                details=[{"missing": missing}],
            )
        return {
            "host": self.profile["host"],
            "port": int(self.profile.get("port", 5432)),
            "dbname": self.profile["database"],
            "user": self.profile["user"],
            "password": self.profile.get("password"),
            "sslmode": self.profile.get("sslmode", "prefer"),
            "connect_timeout": int(self.profile.get("connect_timeout", 10)),
        }

    @staticmethod
    def _asset(asset_key: str) -> tuple[str, str]:
        parts = asset_key.split(".")
        if len(parts) == 1:
            schema_name, table_name = "public", parts[0]
        elif len(parts) == 2:
            schema_name, table_name = parts
        else:
            raise DomainError(
                "CONNECTOR_ASSET_INVALID",
                "PostgreSQL 资产标识必须是 table 或 schema.table。",
                status_code=422,
            )
        return (
            PostgreSQLConnector._identifier(schema_name),
            PostgreSQLConnector._identifier(table_name),
        )

    @staticmethod
    def _identifier(value: str) -> str:
        if not IDENTIFIER.fullmatch(value):
            raise DomainError(
                "CONNECTOR_IDENTIFIER_INVALID",
                "表名或字段名不符合安全标识符规则。",
                status_code=422,
            )
        return value


class RESTJSONConnector:
    """Configurable read-only JSON connector for ERP/MES/CRM HTTP APIs.

    The connector deliberately handles transport and response shape only. It
    does not infer business semantics; source fields still require an explicit
    ontology mapping before materialization.
    """

    def __init__(self, profile: dict[str, Any]) -> None:
        self.profile = profile

    def test(self) -> tuple[bool, str]:
        try:
            response = httpx.get(
                self._url(str(self.profile.get("health_path", ""))),
                headers=self._headers(),
                timeout=self._timeout(),
            )
            response.raise_for_status()
            return True, "REST JSON 只读连接有效。"
        except DomainError as exc:
            return False, exc.message
        except httpx.HTTPStatusError as exc:
            return False, f"REST 服务返回 HTTP {exc.response.status_code}。"
        except httpx.RequestError as exc:
            return False, f"REST 连接失败：{type(exc).__name__}。"
        except Exception as exc:  # pragma: no cover - defensive boundary
            return False, f"REST 连接失败：{type(exc).__name__}。"

    def extract(
        self,
        asset_key: str,
        *,
        limit: int,
        watermark_column: str | None,
        tie_breaker_column: str | None,
        after_watermark: Any | None,
        after_tie_breaker: Any | None,
    ) -> ConnectorPage:
        if (watermark_column is None) != (tie_breaker_column is None):
            raise DomainError(
                "CONNECTOR_COMPOSITE_WATERMARK_REQUIRED",
                "增量读取必须同时指定水位字段和唯一排序字段。",
                status_code=422,
            )
        if (after_watermark is None) != (after_tie_breaker is None):
            raise DomainError(
                "CONNECTOR_COMPOSITE_CURSOR_REQUIRED",
                "继续增量读取必须同时提供上一水位值和上一唯一排序值。",
                status_code=422,
            )
        params = self._query_params(limit)
        watermark_param = self.profile.get("watermark_param")
        if after_watermark is not None:
            if not isinstance(watermark_param, str) or not watermark_param:
                raise DomainError(
                    "REST_WATERMARK_UNSUPPORTED",
                    "REST 增量读取未配置 watermark_param，不能安全续传业务水位。",
                    status_code=409,
                )
            params[watermark_param] = after_watermark
        if after_tie_breaker is not None:
            cursor_param = self.profile.get("cursor_param")
            if isinstance(cursor_param, str) and cursor_param:
                params[cursor_param] = after_tie_breaker
            else:
                tie_breaker_param = self.profile.get("tie_breaker_param")
                if not isinstance(tie_breaker_param, str) or not tie_breaker_param:
                    raise DomainError(
                        "REST_WATERMARK_UNSUPPORTED",
                        "REST 增量读取未配置 tie_breaker_param，不能安全续传复合水位。",
                        status_code=409,
                    )
                params[tie_breaker_param] = after_tie_breaker
        try:
            response = httpx.get(
                self._url(asset_key),
                headers=self._headers(),
                params=params,
                timeout=self._timeout(),
            )
            response.raise_for_status()
            payload = response.json()
        except DomainError:
            raise
        except httpx.HTTPStatusError as exc:
            raise DomainError(
                "CONNECTOR_EXTRACT_FAILED",
                f"REST 数据提取返回 HTTP {exc.response.status_code}。",
                status_code=502,
            ) from exc
        except (httpx.RequestError, ValueError, TypeError) as exc:
            raise DomainError(
                "CONNECTOR_EXTRACT_FAILED",
                "REST 数据提取失败或响应不是合法 JSON。",
                status_code=502,
                details=[{"exception": type(exc).__name__}],
            ) from exc
        rows = self._rows(payload)[:limit]
        columns = self._columns(rows)
        next_watermark = rows[-1].get(watermark_column) if rows and watermark_column else None
        # A REST cursor is opaque and must come from the response metadata. Do
        # not expose the last business column as a cursor when the endpoint did
        # not explicitly return one; that would make the next request unsafe.
        next_tie_breaker = None
        cursor_path = self.profile.get("next_cursor_path", "next_cursor")
        if isinstance(cursor_path, str) and cursor_path:
            cursor = self._path(payload, cursor_path)
            if cursor is not None:
                next_tie_breaker = cursor
        if (
            next_tie_breaker is None
            and rows
            and isinstance(watermark_param, str)
            and watermark_param
            and isinstance(self.profile.get("tie_breaker_param"), str)
            and self.profile["tie_breaker_param"]
            and not self.profile.get("cursor_param")
        ):
            next_tie_breaker = rows[-1].get(tie_breaker_column)
        return ConnectorPage(columns, rows, next_watermark, next_tie_breaker)

    def _query_params(self, limit: int) -> dict[str, Any]:
        configured = self.profile.get("query_params", {})
        if not isinstance(configured, dict) or not all(
            isinstance(key, str) for key in configured
        ):
            raise DomainError(
                "REST_CONFIG_INVALID",
                "REST query_params 必须是 JSON 对象且键必须是字符串。",
                status_code=422,
            )
        params = dict(configured)
        limit_param = self.profile.get("limit_param", "limit")
        if limit_param is not None:
            if not isinstance(limit_param, str) or not limit_param:
                raise DomainError(
                    "REST_CONFIG_INVALID",
                    "REST limit_param 必须是非空字符串或 null。",
                    status_code=422,
                )
            params[limit_param] = limit
        return params

    def _rows(self, payload: Any) -> list[dict[str, Any]]:
        path = self.profile.get("rows_path", "items")
        if not isinstance(path, str):
            raise DomainError(
                "REST_CONFIG_INVALID",
                "REST rows_path 必须是字符串。",
                status_code=422,
            )
        value = self._path(payload, path)
        if not isinstance(value, list) or not all(isinstance(item, dict) for item in value):
            raise DomainError(
                "REST_RESPONSE_SHAPE_INVALID",
                "REST 响应的 rows_path 没有指向 JSON 对象数组。",
                status_code=502,
            )
        return [dict(item) for item in value]

    def _columns(self, rows: list[dict[str, Any]]) -> list[str]:
        configured = self.profile.get("columns")
        if configured is not None:
            if not isinstance(configured, list) or not all(
                isinstance(item, str) and item for item in configured
            ):
                raise DomainError(
                    "REST_CONFIG_INVALID",
                    "REST columns 必须是非空字符串数组。",
                    status_code=422,
                )
            return list(dict.fromkeys(configured))
        columns: list[str] = []
        for row in rows:
            for key in row:
                if key not in columns:
                    columns.append(key)
        return columns

    def _headers(self) -> dict[str, str]:
        configured = self.profile.get("headers", {})
        if not isinstance(configured, dict) or not all(
            isinstance(key, str) and isinstance(value, str)
            for key, value in configured.items()
        ):
            raise DomainError(
                "REST_CONFIG_INVALID",
                "REST headers 必须是字符串键值对象。",
                status_code=422,
            )
        headers = dict(configured)
        token = self.profile.get("bearer_token")
        if token is not None:
            if not isinstance(token, str) or not token:
                raise DomainError(
                    "REST_CONFIG_INVALID",
                    "REST bearer_token 必须是非空字符串。",
                    status_code=422,
                )
            headers.setdefault("Authorization", f"Bearer {token}")
        return headers

    def _timeout(self) -> float:
        try:
            value = float(self.profile.get("timeout_seconds", 10))
        except (TypeError, ValueError) as exc:
            raise DomainError(
                "REST_CONFIG_INVALID",
                "REST timeout_seconds 必须是数字。",
                status_code=422,
            ) from exc
        if not 0 < value <= 120:
            raise DomainError(
                "REST_CONFIG_INVALID",
                "REST timeout_seconds 必须在 0 到 120 秒之间。",
                status_code=422,
            )
        return value

    def _url(self, path: str) -> str:
        base_url = self.profile.get("base_url")
        if not isinstance(base_url, str) or not base_url:
            raise DomainError(
                "REST_CONFIG_INCOMPLETE",
                "REST 连接配置必须包含 base_url。",
                status_code=422,
            )
        parsed = urlparse(base_url)
        if parsed.scheme not in {"http", "https"} or not parsed.netloc:
            raise DomainError(
                "REST_CONFIG_INVALID",
                "REST base_url 必须是 HTTP 或 HTTPS 地址。",
                status_code=422,
            )
        if not isinstance(path, str) or path.startswith(("http://", "https://", "//")):
            raise DomainError(
                "REST_ASSET_INVALID",
                "REST 资产只能是相对路径，不能覆盖连接地址。",
                status_code=422,
            )
        return f"{base_url.rstrip('/')}/{path.lstrip('/')}"

    @staticmethod
    def _path(value: Any, path: str) -> Any:
        current = value
        if not path:
            return current
        for part in path.split("."):
            if not isinstance(current, dict) or part not in current:
                return None
            current = current[part]
        return current


def connector_for(kind: str, profile: dict[str, Any]) -> ReadOnlyConnector:
    if kind == "SQLITE":
        return SQLiteConnector(profile)
    if kind == "POSTGRESQL":
        return PostgreSQLConnector(profile)
    if kind == "ERP":
        from enterprise_insight_backend.erpnext_connector import ERPNextConnector

        return ERPNextConnector(profile)
    if kind in {"REST", "MES", "CRM"}:
        return RESTJSONConnector(profile)
    raise DomainError(
        "CONNECTOR_UNAVAILABLE",
        "当前数据源类型没有可执行连接器。",
        status_code=409,
        details=[{"kind": kind}],
    )
