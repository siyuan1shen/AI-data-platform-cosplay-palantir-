from __future__ import annotations

import copy
import hashlib
import json
import re
import time
from collections import defaultdict
from dataclasses import dataclass, field
from datetime import datetime, timedelta
from typing import Any
from urllib.parse import quote, urlparse

import httpx

from enterprise_insight_backend.connectors import ConnectorPage
from enterprise_insight_backend.errors import DomainError

_FIELD = re.compile(r"^[A-Za-z_][A-Za-z0-9_]*$")
_DEFAULT_DOCTYPES = {
    "Company",
    "Customer",
    "Supplier",
    "Item",
    "UOM",
    "Warehouse",
    "Sales Order",
    "Sales Order Item",
    "Purchase Order",
    "Purchase Order Item",
    "Delivery Note",
    "Sales Invoice",
    "Payment Entry",
    "Work Order",
    "Project",
    "Task",
}
_DOCSTATUS = {0: "DRAFT", 1: "SUBMITTED", 2: "CANCELLED"}
_TRANSIENT_HTTP_STATUS = {429, 502, 503, 504}


def _canonical_json(value: Any) -> str:
    return json.dumps(
        value,
        ensure_ascii=False,
        sort_keys=True,
        separators=(",", ":"),
        default=str,
    )


def _sha256(value: Any) -> str:
    return hashlib.sha256(_canonical_json(value).encode("utf-8")).hexdigest()


def _replay_sha256(
    rows: list[dict[str, Any]],
    columns: list[str],
    quarantined_records: list[dict[str, Any]],
    request_parameters: dict[str, Any],
    next_watermark: Any | None,
    next_tie_breaker: Any | None,
    has_more: bool,
) -> str:
    return _sha256(
        {
            "rows": rows,
            "columns": columns,
            "quarantined_records": quarantined_records,
            "request_parameters": request_parameters,
            "next_watermark": next_watermark,
            "next_tie_breaker": next_tie_breaker,
            "has_more": has_more,
        }
    )


@dataclass(frozen=True, slots=True)
class ERPNextPageSnapshot:
    """Offline, replayable evidence for one Frappe list response."""

    doctype: str
    source_site: str
    raw_records: tuple[dict[str, Any], ...]
    rows: tuple[dict[str, Any], ...]
    columns: tuple[str, ...]
    quarantined_records: tuple[dict[str, Any], ...]
    request_parameters: dict[str, Any]
    next_watermark: Any | None
    next_tie_breaker: Any | None
    has_more: bool
    raw_sha256: str
    normalized_sha256: str

    def verify(self) -> None:
        if _sha256(list(self.raw_records)) != self.raw_sha256:
            raise DomainError(
                "ERPNEXT_SNAPSHOT_HASH_MISMATCH",
                "ERPNext 原始快照摘要不匹配，已拒绝重放。",
                status_code=409,
            )
        if _replay_sha256(
            list(self.rows),
            list(self.columns),
            list(self.quarantined_records),
            self.request_parameters,
            self.next_watermark,
            self.next_tie_breaker,
            self.has_more,
        ) != self.normalized_sha256:
            raise DomainError(
                "ERPNEXT_SNAPSHOT_HASH_MISMATCH",
                "ERPNext 重放内容或来源清单摘要不匹配，已拒绝重放。",
                status_code=409,
            )

    def replay(self) -> ERPNextConnectorPage:
        self.verify()
        return ERPNextConnectorPage(
            columns=list(self.columns),
            rows=copy.deepcopy(list(self.rows)),
            next_watermark=copy.deepcopy(self.next_watermark),
            next_tie_breaker=copy.deepcopy(self.next_tie_breaker),
            has_more=self.has_more,
            quarantined_records=copy.deepcopy(list(self.quarantined_records)),
            snapshot_sha256=self.raw_sha256,
            request_parameters=copy.deepcopy(self.request_parameters),
            snapshot=self,
        )


@dataclass(slots=True)
class ERPNextConnectorPage(ConnectorPage):
    has_more: bool = False
    quarantined_records: list[dict[str, Any]] = field(default_factory=list)
    warnings: list[str] = field(default_factory=list)
    snapshot_sha256: str = ""
    request_parameters: dict[str, Any] = field(default_factory=dict)
    snapshot: ERPNextPageSnapshot | None = None


class ERPNextConnector:
    """Read-only ERPNext/Frappe v1 Resource API adapter.

    Pagination uses Frappe's stable ``(modified, name)`` ordering and keyset
    filters.  An optional incremental checkpoint is replayed with a bounded
    overlap window; downstream source identity and raw-batch hashes make that
    overlap safe to ingest more than once.
    """

    def __init__(self, profile: dict[str, Any], *, client: Any | None = None) -> None:
        self.profile = profile
        self._client = client

    def test(self) -> tuple[bool, str]:
        doctype = self.profile.get("health_doctype", "Company")
        try:
            self._doctype(doctype)
            payload, _ = self._request_resource(
                str(doctype),
                {
                    "fields": _canonical_json(["name"]),
                    "limit_start": 0,
                    "limit_page_length": 1,
                },
            )
            self._response_rows(payload)
            return True, "ERPNext/Frappe 只读连接有效。"
        except DomainError as exc:
            return False, exc.message

    def extract(
        self,
        asset_key: str,
        *,
        limit: int,
        watermark_column: str | None,
        tie_breaker_column: str | None,
        after_watermark: Any | None,
        after_tie_breaker: Any | None,
    ) -> ERPNextConnectorPage:
        doctype = self._doctype(asset_key)
        if not 1 <= limit <= 5000:
            raise DomainError(
                "ERPNEXT_PAGE_SIZE_INVALID",
                "ERPNext 单页读取数量必须在 1 到 5000 之间。",
                status_code=422,
            )
        if (watermark_column is None) != (tie_breaker_column is None):
            raise DomainError(
                "CONNECTOR_COMPOSITE_WATERMARK_REQUIRED",
                "增量读取必须同时指定 modified 水位和 name 次键。",
                status_code=422,
            )
        if (after_watermark is None) != (after_tie_breaker is None):
            raise DomainError(
                "CONNECTOR_COMPOSITE_CURSOR_REQUIRED",
                "继续读取必须同时提供 modified 水位和 name 次键。",
                status_code=422,
            )
        if watermark_column not in {None, "modified"} or tie_breaker_column not in {
            None,
            "name",
        }:
            raise DomainError(
                "ERPNEXT_CURSOR_FIELDS_UNSUPPORTED",
                "ERPNext 增量游标固定使用 modified 与 name，不能更换字段。",
                status_code=422,
            )

        source_site = self._source_site()
        filters = self._filters_for(doctype)
        query: dict[str, Any] = {
            "fields": _canonical_json(self._fields_for(doctype)),
            "filters": _canonical_json(filters),
            "order_by": "modified asc, name asc",
            "limit_start": 0,
            "limit_page_length": limit,
        }
        if isinstance(after_watermark, dict) and after_watermark.get("mode") == "OFFSET":
            if not isinstance(after_tie_breaker, dict) or after_tie_breaker.get("mode") != "OFFSET":
                raise DomainError(
                    "ERPNEXT_CURSOR_INVALID",
                    "ERPNext offset 恢复游标不完整。",
                    status_code=422,
                )
            offset = after_tie_breaker.get("limit_start")
            if isinstance(offset, bool) or not isinstance(offset, int) or offset < 0:
                raise DomainError(
                    "ERPNEXT_CURSOR_INVALID",
                    "ERPNext offset 恢复游标格式无效。",
                    status_code=422,
                )
            query["limit_start"] = offset
        elif after_watermark is not None:
            if not isinstance(after_watermark, str) or not isinstance(after_tie_breaker, str):
                raise DomainError(
                    "ERPNEXT_CURSOR_INVALID",
                    "ERPNext keyset 游标必须是 modified/name 字符串对。",
                    status_code=422,
                )
            query["filters"] = _canonical_json(
                [*filters, ["modified", ">=", after_watermark]]
            )
            query["or_filters"] = _canonical_json(
                [
                    ["modified", ">", after_watermark],
                    ["name", ">", after_tie_breaker],
                ]
            )

        response, safe_parameters = self._request_resource(doctype, query)
        raw_records = self._response_rows(response)
        rows, quarantined, warnings = self._normalize_records(
            doctype, source_site, raw_records
        )
        rows, relation_warnings = self._validate_links(doctype, rows)
        warnings.extend(relation_warnings)

        last = raw_records[-1] if raw_records else None
        next_watermark: Any | None = None
        next_tie_breaker: Any | None = None
        if last is not None:
            modified = last.get("modified")
            name = last.get("name")
            if isinstance(modified, str) and modified and isinstance(name, str) and name:
                next_watermark, next_tie_breaker = modified, name
            else:
                # A malformed final row must not make later pages loop over the
                # same keyset. Preserve offset pagination as an explicit degraded
                # mode; its limitations are visible in the page warning.
                base_start = query["limit_start"] if isinstance(query["limit_start"], int) else 0
                next_watermark = {"mode": "OFFSET"}
                next_tie_breaker = {
                    "mode": "OFFSET",
                    "limit_start": base_start + len(raw_records),
                }
                warnings.append(
                    "末条源记录缺少 modified/name；续页改用 offset，期间源数据变动可能导致重复，"
                    "需做全量核对。"
                )

        columns = self._columns(rows)
        raw_sha256 = _sha256(raw_records)
        has_more = len(raw_records) >= limit
        snapshot = ERPNextPageSnapshot(
            doctype=doctype,
            source_site=source_site,
            raw_records=tuple(copy.deepcopy(raw_records)),
            rows=tuple(copy.deepcopy(rows)),
            columns=tuple(columns),
            quarantined_records=tuple(copy.deepcopy(quarantined)),
            request_parameters=safe_parameters,
            next_watermark=copy.deepcopy(next_watermark),
            next_tie_breaker=copy.deepcopy(next_tie_breaker),
            has_more=has_more,
            raw_sha256=raw_sha256,
            normalized_sha256=_replay_sha256(
                rows,
                columns,
                quarantined,
                safe_parameters,
                next_watermark,
                next_tie_breaker,
                has_more,
            ),
        )
        return ERPNextConnectorPage(
            columns=columns,
            rows=rows,
            next_watermark=next_watermark,
            next_tie_breaker=next_tie_breaker,
            has_more=has_more,
            quarantined_records=quarantined,
            warnings=warnings,
            snapshot_sha256=raw_sha256,
            request_parameters=safe_parameters,
            snapshot=snapshot,
        )

    def extract_all(
        self,
        asset_key: str,
        *,
        limit: int = 1000,
        after_watermark: str | None = None,
        after_tie_breaker: str | None = None,
        overlap_seconds: int | None = None,
        max_pages: int = 1000,
    ) -> ERPNextConnectorPage:
        """Read a bounded full/delta window and return one replayable raw batch."""
        if (after_watermark is None) != (after_tie_breaker is None):
            raise DomainError(
                "CONNECTOR_COMPOSITE_CURSOR_REQUIRED",
                "同步检查点必须同时含 modified 水位和 name 次键。",
                status_code=422,
            )
        if not 1 <= max_pages <= 10000:
            raise DomainError(
                "ERPNEXT_PAGE_BUDGET_INVALID",
                "ERPNext 同步页数上限必须在 1 到 10000 之间。",
                status_code=422,
            )
        if overlap_seconds is None:
            overlap_seconds = self._overlap_seconds()
        if not 0 <= overlap_seconds <= 86400:
            raise DomainError(
                "ERPNEXT_OVERLAP_INVALID",
                "ERPNext 重叠时间窗必须在 0 到 86400 秒之间。",
                status_code=422,
            )

        start_watermark = after_watermark
        start_name = after_tie_breaker
        if after_watermark is not None and overlap_seconds:
            start_watermark = self._subtract_overlap(after_watermark, overlap_seconds)
            start_name = ""

        all_raw: list[dict[str, Any]] = []
        all_safe_parameters: list[dict[str, Any]] = []
        warnings: list[str] = []
        current_watermark = start_watermark
        current_name = start_name
        for _page_number in range(max_pages):
            page = self.extract(
                asset_key,
                limit=limit,
                watermark_column="modified" if current_watermark is not None else None,
                tie_breaker_column="name" if current_name is not None else None,
                after_watermark=current_watermark,
                after_tie_breaker=current_name,
            )
            if not page.snapshot:
                raise AssertionError("ERPNext extraction must produce a replay snapshot")
            all_safe_parameters.append(page.request_parameters)
            warnings.extend(page.warnings)
            if not page.snapshot.raw_records:
                break
            all_raw.extend(copy.deepcopy(list(page.snapshot.raw_records)))
            if not page.has_more:
                break
            next_watermark = page.next_watermark
            next_name = page.next_tie_breaker
            if not isinstance(next_watermark, str) or not isinstance(next_name, str):
                raise DomainError(
                    "ERPNEXT_SYNC_CURSOR_UNSAFE",
                    "分页结果没有可安全续接的 modified/name；请先修复源记录后重试。",
                    status_code=409,
                )
            if current_watermark is not None and (next_watermark, next_name) <= (
                current_watermark,
                current_name or "",
            ):
                raise DomainError(
                    "ERPNEXT_SYNC_CURSOR_STALLED",
                    "ERPNext 分页游标未前进，已停止以避免重复循环。",
                    status_code=502,
                )
            current_watermark, current_name = next_watermark, next_name
        else:
            raise DomainError(
                "ERPNEXT_PAGE_BUDGET_EXCEEDED",
                "ERPNext 同步达到页数上限；返回部分结果不安全，已中止本次同步。",
                status_code=409,
                details=[{"max_pages": max_pages}],
            )

        if not all_safe_parameters:
            raise AssertionError("ERPNext extract_all must request at least one page")
        doctype = self._doctype(asset_key)
        rows, quarantined, normalization_warnings = self._normalize_records(
            doctype, self._source_site(), all_raw
        )
        rows, relation_warnings = self._validate_links(doctype, rows)
        warnings.extend(normalization_warnings)
        warnings.extend(relation_warnings)
        # The per-page form is kept as the stable query manifest; no auth headers
        # or token material are included in it.
        safe_manifest = {"pages": all_safe_parameters}
        last = all_raw[-1] if all_raw else None
        next_watermark = last.get("modified") if last else after_watermark
        next_tie_breaker = last.get("name") if last else after_tie_breaker
        raw_sha256 = _sha256(all_raw)
        columns = self._columns(rows)
        snapshot = ERPNextPageSnapshot(
            doctype=doctype,
            source_site=self._source_site(),
            raw_records=tuple(copy.deepcopy(all_raw)),
            rows=tuple(copy.deepcopy(rows)),
            columns=tuple(columns),
            quarantined_records=tuple(copy.deepcopy(quarantined)),
            request_parameters=safe_manifest,
            next_watermark=next_watermark,
            next_tie_breaker=next_tie_breaker,
            has_more=False,
            raw_sha256=raw_sha256,
            normalized_sha256=_replay_sha256(
                rows,
                columns,
                quarantined,
                safe_manifest,
                next_watermark,
                next_tie_breaker,
                False,
            ),
        )
        return ERPNextConnectorPage(
            columns=columns,
            rows=rows,
            next_watermark=next_watermark,
            next_tie_breaker=next_tie_breaker,
            has_more=False,
            quarantined_records=quarantined,
            warnings=list(dict.fromkeys(warnings)),
            snapshot_sha256=raw_sha256,
            request_parameters=safe_manifest,
            snapshot=snapshot,
        )

    def _normalize_records(
        self,
        doctype: str,
        source_site: str,
        raw_records: list[dict[str, Any]],
    ) -> tuple[list[dict[str, Any]], list[dict[str, Any]], list[str]]:
        digests = [_sha256(item) for item in raw_records]
        seen_exact: set[tuple[str, str, str]] = set()
        versions: dict[tuple[str, str], set[str]] = defaultdict(set)
        for item, digest in zip(raw_records, digests, strict=True):
            name, modified = item.get("name"), item.get("modified")
            if isinstance(name, str) and name and isinstance(modified, str) and modified:
                versions[(name, modified)].add(digest)

        rows: list[dict[str, Any]] = []
        quarantined: list[dict[str, Any]] = []
        warnings: list[str] = []
        for raw, digest in zip(raw_records, digests, strict=True):
            name = raw.get("name")
            modified = raw.get("modified")
            docstatus = self._docstatus(raw.get("docstatus"))
            reason: str | None = None
            if not isinstance(name, str) or not name.strip():
                reason = "ERPNEXT_SOURCE_IDENTITY_MISSING"
            elif not isinstance(modified, str) or not modified.strip():
                reason = "ERPNEXT_MODIFIED_MISSING"
            elif docstatus is None:
                reason = "ERPNEXT_DOCSTATUS_INVALID"
            elif len(versions.get((name, modified), set())) > 1:
                reason = "ERPNEXT_SAME_VERSION_IDENTITY_CONFLICT"

            row_key = (str(name or ""), str(modified or ""), digest)
            if row_key in seen_exact:
                warnings.append(
                    "响应中出现完全相同的重复源记录，已按 doctype/name/modified/"
                    "摘要去重。"
                )
                continue
            seen_exact.add(row_key)

            lineage: dict[str, Any] = {
                "source_site": source_site,
                "doctype": doctype,
                "source_record_key": name if isinstance(name, str) and name else None,
                "source_modified": modified,
                "source_docstatus": raw.get("docstatus"),
                "source_payload_sha256": digest,
            }
            normalized = copy.deepcopy(raw)
            normalized["__erpnext_lifecycle__"] = _DOCSTATUS.get(docstatus, "UNKNOWN")
            normalized["__erpnext_docstatus__"] = docstatus
            normalized["__erpnext_quarantine__"] = reason
            normalized["__erpnext_relation_conflicts__"] = []
            normalized["__erpnext_relation_validation__"] = {
                "status": "NOT_CONFIGURED",
                "checked_links": 0,
                "conflicts": 0,
            }
            if reason:
                lineage["source_payload"] = copy.deepcopy(raw)
                normalized["name"] = None
                normalized["__erpnext_quarantine__"] = reason
                quarantined.append(
                    {
                        "code": reason,
                        "doctype": doctype,
                        "source_record_key": name if isinstance(name, str) else None,
                        "modified": modified,
                        "payload_sha256": digest,
                        "payload": copy.deepcopy(raw),
                    }
                )
            normalized["__erpnext_lineage__"] = lineage
            rows.append(normalized)
        return rows, quarantined, list(dict.fromkeys(warnings))

    def _validate_links(
        self, doctype: str, rows: list[dict[str, Any]]
    ) -> tuple[list[dict[str, Any]], list[str]]:
        configured = self.profile.get("link_fields_by_doctype", {})
        if not isinstance(configured, dict):
            raise DomainError(
                "ERPNEXT_CONFIG_INVALID",
                "link_fields_by_doctype 必须是对象。",
                status_code=422,
            )
        link_fields = configured.get(doctype, {})
        if not isinstance(link_fields, dict) or not all(
            isinstance(field_name, str)
            and _FIELD.fullmatch(field_name)
            and isinstance(target_doctype, str)
            for field_name, target_doctype in link_fields.items()
        ):
            raise DomainError(
                "ERPNEXT_CONFIG_INVALID",
                "Frappe Link 字段配置必须是字段名到 DocType 的映射。",
                status_code=422,
            )
        if not link_fields:
            return rows, []

        refs: dict[str, set[str]] = defaultdict(set)
        for row in rows:
            for field_name, target_doctype in link_fields.items():
                value = row.get(field_name)
                if value is not None and value != "":
                    if not isinstance(value, str):
                        row["__erpnext_relation_conflicts__"].append(
                            {
                                "code": "ERPNEXT_LINK_VALUE_INVALID",
                                "field": field_name,
                                "target_doctype": target_doctype,
                            }
                        )
                    else:
                        refs[target_doctype].add(value)

        found: dict[str, set[str]] = defaultdict(set)
        unavailable: set[str] = set()
        for target_doctype, names in refs.items():
            self._doctype(target_doctype)
            try:
                for start in range(0, len(names), 100):
                    chunk = sorted(names)[start : start + 100]
                    payload, _ = self._request_resource(
                        target_doctype,
                        {
                            "fields": _canonical_json(["name"]),
                            "filters": _canonical_json([["name", "in", chunk]]),
                            "order_by": "name asc",
                            "limit_start": 0,
                            "limit_page_length": len(chunk),
                        },
                    )
                    found[target_doctype].update(
                        str(item["name"])
                        for item in self._response_rows(payload)
                        if isinstance(item.get("name"), str)
                    )
            except DomainError:
                # A target DocType can have stricter field permissions. The
                # source row remains available, but link validation is marked
                # unresolved instead of treating an inaccessible target as absent.
                unavailable.add(target_doctype)

        warnings: list[str] = []
        for row in rows:
            conflicts: list[dict[str, Any]] = row["__erpnext_relation_conflicts__"]
            checked = 0
            unverified = False
            for field_name, target_doctype in link_fields.items():
                value = row.get(field_name)
                if value is None or value == "" or not isinstance(value, str):
                    continue
                checked += 1
                if target_doctype in unavailable:
                    unverified = True
                elif value not in found[target_doctype]:
                    conflicts.append(
                        {
                            "code": "ERPNEXT_LINK_TARGET_NOT_FOUND",
                            "field": field_name,
                            "target_doctype": target_doctype,
                            "target_record_key": value,
                        }
                    )
            status = (
                "UNVERIFIED"
                if unverified
                else "CONFLICTS"
                if conflicts
                else "VERIFIED"
            )
            row["__erpnext_relation_validation__"] = {
                "status": status,
                "checked_links": checked,
                "conflicts": len(conflicts),
            }
            if conflicts:
                warnings.append("部分 Frappe Link 目标不存在或格式错误，已隔离关系，不丢弃源对象。")
            if unverified:
                warnings.append("部分 Link 目标无权读取或暂不可用；关系状态标为未核验。")
        return rows, list(dict.fromkeys(warnings))

    def _request_resource(
        self, doctype: str, params: dict[str, Any]
    ) -> tuple[dict[str, Any], dict[str, Any]]:
        url = self._url(doctype)
        safe_params = copy.deepcopy(params)
        retries, backoff = self._retry_options()
        for attempt in range(retries + 1):
            try:
                if self._client is None:
                    response = httpx.get(
                        url,
                        params=params,
                        headers=self._headers(),
                        timeout=self._timeout(),
                    )
                else:
                    response = self._client.get(
                        url,
                        params=params,
                        headers=self._headers(),
                        timeout=self._timeout(),
                    )
                if response.status_code in _TRANSIENT_HTTP_STATUS and attempt < retries:
                    delay = self._retry_delay(response, backoff, attempt)
                    if delay:
                        time.sleep(delay)
                    continue
                response.raise_for_status()
                payload = response.json()
                if not isinstance(payload, dict):
                    raise DomainError(
                        "ERPNEXT_RESPONSE_INVALID",
                        "Frappe 响应不是 JSON 对象。",
                        status_code=502,
                    )
                return payload, safe_params
            except DomainError:
                raise
            except httpx.HTTPStatusError as exc:
                raise DomainError(
                    "ERPNEXT_HTTP_ERROR",
                    f"Frappe Resource API 返回 HTTP {exc.response.status_code}。",
                    status_code=502,
                ) from exc
            except (httpx.RequestError, ValueError, TypeError) as exc:
                if attempt < retries and isinstance(exc, httpx.RequestError):
                    if backoff:
                        time.sleep(backoff * (attempt + 1))
                    continue
                raise DomainError(
                    "ERPNEXT_REQUEST_FAILED",
                    "Frappe Resource API 请求失败或响应不是合法 JSON。",
                    status_code=502,
                    details=[{"exception": type(exc).__name__}],
                ) from exc
        raise AssertionError("retry loop must return or raise")

    def _url(self, doctype: str) -> str:
        parsed = self._base_url()
        return f"{parsed.rstrip('/')}/api/resource/{quote(doctype, safe='')}"

    def _base_url(self) -> str:
        value = self.profile.get("base_url")
        if not isinstance(value, str) or not value.strip():
            raise DomainError(
                "ERPNEXT_CONFIG_INCOMPLETE",
                "ERPNext 连接配置必须包含 base_url。",
                status_code=422,
            )
        parsed = urlparse(value)
        if (
            parsed.scheme not in {"http", "https"}
            or not parsed.netloc
            or parsed.username
            or parsed.password
            or parsed.query
            or parsed.fragment
        ):
            raise DomainError(
                "ERPNEXT_CONFIG_INVALID",
                "ERPNext base_url 必须是无凭据、无查询参数的 HTTP(S) 站点地址。",
                status_code=422,
            )
        return value.rstrip("/")

    def _source_site(self) -> str:
        explicit = self.profile.get("site_id")
        if isinstance(explicit, str) and explicit.strip():
            return explicit.strip()[:200]
        parsed = urlparse(self._base_url())
        return f"{parsed.scheme}://{parsed.netloc}{parsed.path.rstrip('/')}"

    def _headers(self) -> dict[str, str]:
        key = self.profile.get("api_key")
        secret = self.profile.get("api_secret")
        if (key is None) != (secret is None):
            raise DomainError(
                "ERPNEXT_AUTH_CONFIG_INVALID",
                "Frappe api_key 与 api_secret 必须同时配置。",
                status_code=422,
            )
        if key is None:
            return {"Accept": "application/json"}
        if not isinstance(key, str) or not key or not isinstance(secret, str) or not secret:
            raise DomainError(
                "ERPNEXT_AUTH_CONFIG_INVALID",
                "Frappe API 凭据必须是非空字符串。",
                status_code=422,
            )
        return {"Accept": "application/json", "Authorization": f"token {key}:{secret}"}

    def _timeout(self) -> float:
        try:
            value = float(self.profile.get("timeout_seconds", 10))
        except (TypeError, ValueError) as exc:
            raise DomainError(
                "ERPNEXT_CONFIG_INVALID",
                "ERPNext timeout_seconds 必须是数字。",
                status_code=422,
            ) from exc
        if not 0 < value <= 120:
            raise DomainError(
                "ERPNEXT_CONFIG_INVALID",
                "ERPNext timeout_seconds 必须在 0 到 120 秒之间。",
                status_code=422,
            )
        return value

    def _retry_options(self) -> tuple[int, float]:
        try:
            retries = int(self.profile.get("max_retries", 2))
            backoff_ms = int(self.profile.get("retry_backoff_ms", 100))
        except (TypeError, ValueError) as exc:
            raise DomainError(
                "ERPNEXT_CONFIG_INVALID",
                "ERPNext 重试参数必须是整数。",
                status_code=422,
            ) from exc
        if not 0 <= retries <= 5 or not 0 <= backoff_ms <= 2000:
            raise DomainError(
                "ERPNEXT_CONFIG_INVALID",
                "ERPNext max_retries 必须在 0–5，retry_backoff_ms 必须在 0–2000。",
                status_code=422,
            )
        return retries, backoff_ms / 1000

    @staticmethod
    def _retry_delay(response: httpx.Response, backoff: float, attempt: int) -> float:
        retry_after = response.headers.get("Retry-After")
        if retry_after:
            try:
                return min(2.0, max(0.0, float(retry_after)))
            except ValueError:
                pass
        return min(2.0, backoff * (attempt + 1))

    def _doctype(self, value: Any) -> str:
        if (
            not isinstance(value, str)
            or not value.strip()
            or len(value) > 140
            or any(char in value for char in "/\\?#%")
            or any(ord(char) < 32 for char in value)
        ):
            raise DomainError(
                "ERPNEXT_DOCTYPE_INVALID",
                "ERPNext 资产必须是有效的 Frappe DocType 名称。",
                status_code=422,
            )
        allowed = self.profile.get("allowed_doctypes")
        permitted = _DEFAULT_DOCTYPES
        if allowed is not None:
            if not isinstance(allowed, list) or not all(
                isinstance(item, str) and item.strip() for item in allowed
            ):
                raise DomainError(
                    "ERPNEXT_CONFIG_INVALID",
                    "allowed_doctypes 必须是字符串数组。",
                    status_code=422,
                )
            permitted = set(allowed)
        if value not in permitted:
            raise DomainError(
                "ERPNEXT_DOCTYPE_NOT_ALLOWED",
                "该 ERPNext DocType 未在连接器允许清单中。",
                status_code=422,
                details=[{"doctype": value}],
            )
        return value

    def _fields_for(self, doctype: str) -> list[str]:
        configured = self.profile.get("fields_by_doctype", {})
        if not isinstance(configured, dict):
            raise DomainError(
                "ERPNEXT_CONFIG_INVALID",
                "fields_by_doctype 必须是对象。",
                status_code=422,
            )
        fields = configured.get(doctype, [])
        if not isinstance(fields, list) or not all(
            isinstance(item, str) and _FIELD.fullmatch(item) for item in fields
        ):
            raise DomainError(
                "ERPNEXT_CONFIG_INVALID",
                "DocType 字段清单必须是安全的字段名数组。",
                status_code=422,
            )
        return list(dict.fromkeys(["name", "modified", "docstatus", *fields]))

    def _filters_for(self, doctype: str) -> list[Any]:
        configured = self.profile.get("filters_by_doctype", {})
        if not isinstance(configured, dict):
            raise DomainError(
                "ERPNEXT_CONFIG_INVALID",
                "filters_by_doctype 必须是对象。",
                status_code=422,
            )
        filters = configured.get(doctype, [])
        if not isinstance(filters, list):
            raise DomainError(
                "ERPNEXT_CONFIG_INVALID",
                "Frappe DocType 过滤条件必须是数组。",
                status_code=422,
            )
        allowed_operators = {
            "=",
            "!=",
            ">",
            ">=",
            "<",
            "<=",
            "in",
            "not in",
            "like",
            "between",
            "is",
        }
        for condition in filters:
            if (
                not isinstance(condition, list)
                or len(condition) != 3
                or not isinstance(condition[0], str)
                or not _FIELD.fullmatch(condition[0])
                or not isinstance(condition[1], str)
                or condition[1].lower() not in allowed_operators
            ):
                raise DomainError(
                    "ERPNEXT_CONFIG_INVALID",
                    "Frappe filters 只能包含字段、受支持运算符和值三项。",
                    status_code=422,
                )
        return copy.deepcopy(filters)

    def _overlap_seconds(self) -> int:
        try:
            value = int(self.profile.get("overlap_seconds", 300))
        except (TypeError, ValueError) as exc:
            raise DomainError(
                "ERPNEXT_OVERLAP_INVALID",
                "ERPNext overlap_seconds 必须是整数。",
                status_code=422,
            ) from exc
        if not 0 <= value <= 86400:
            raise DomainError(
                "ERPNEXT_OVERLAP_INVALID",
                "ERPNext overlap_seconds 必须在 0 到 86400 秒之间。",
                status_code=422,
            )
        return value

    @staticmethod
    def _subtract_overlap(value: str, seconds: int) -> str:
        try:
            parsed = datetime.fromisoformat(value.replace("Z", "+00:00"))
        except ValueError as exc:
            raise DomainError(
                "ERPNEXT_CURSOR_INVALID",
                "ERPNext modified 水位不是可解析的日期时间。",
                status_code=422,
            ) from exc
        if parsed.tzinfo is not None:
            parsed = parsed.replace(tzinfo=None)
        return (parsed - timedelta(seconds=seconds)).strftime("%Y-%m-%d %H:%M:%S.%f")

    @staticmethod
    def _docstatus(value: Any) -> int | None:
        if isinstance(value, bool):
            return None
        if isinstance(value, int) and value in _DOCSTATUS:
            return value
        if isinstance(value, str) and value in {"0", "1", "2"}:
            return int(value)
        return None

    @staticmethod
    def _response_rows(payload: dict[str, Any]) -> list[dict[str, Any]]:
        rows = payload.get("data")
        if not isinstance(rows, list) or not all(isinstance(item, dict) for item in rows):
            raise DomainError(
                "ERPNEXT_RESPONSE_SHAPE_INVALID",
                "Frappe 响应的 data 字段不是对象数组。",
                status_code=502,
            )
        return [copy.deepcopy(item) for item in rows]

    @staticmethod
    def _columns(rows: list[dict[str, Any]]) -> list[str]:
        columns: list[str] = []
        for row in rows:
            for key in row:
                if key not in columns:
                    columns.append(key)
        return columns
