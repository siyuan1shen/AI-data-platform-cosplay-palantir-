from __future__ import annotations

import hashlib
import io
import json
from datetime import timedelta
from pathlib import Path
from typing import Any
from uuid import UUID
from zipfile import BadZipFile, ZipFile

from sqlalchemy.orm import Session

from enterprise_insight_backend.config import Settings
from enterprise_insight_backend.errors import DomainError
from enterprise_insight_backend.exporting import atomic_write
from enterprise_insight_backend.models import RestorePreviewRow
from enterprise_insight_backend.project_archives import (
    ARCHIVE_SCHEMA,
    archive_conflicts,
    restore_archive,
    validate_archive_payload,
)
from enterprise_insight_backend.schemas import RestorePreviewView, RestoreResultView
from enterprise_insight_backend.service_utils import now_utc

MAX_RESTORE_PACKAGE_BYTES = 100 * 1024 * 1024
MAX_RESTORE_PAYLOAD_BYTES = 250 * 1024 * 1024


class RestoreService:
    def __init__(self, session: Session, settings: Settings) -> None:
        self.session = session
        self.settings = settings

    def preview(self, file_name: str, content: bytes) -> RestorePreviewView:
        if not file_name.lower().endswith(".zip"):
            raise DomainError(
                "RESTORE_ZIP_REQUIRED", "项目恢复包必须是 ZIP 文件。", status_code=422
            )
        if not content:
            raise DomainError("RESTORE_EMPTY", "恢复包为空。", status_code=422)
        if len(content) > MAX_RESTORE_PACKAGE_BYTES:
            raise DomainError(
                "RESTORE_PACKAGE_TOO_LARGE",
                "恢复包不能超过 100MB。",
                status_code=413,
            )
        payload = self._payload(content)
        conflicts = archive_conflicts(self.session, payload)
        tables = payload["tables"]
        summary = {
            "schema": ARCHIVE_SCHEMA,
            "company": {
                "id": tables["companies"][0]["id"],
                "name": tables["companies"][0]["name"],
            },
            "project": {
                "id": tables["projects"][0]["id"],
                "name": tables["projects"][0]["name"],
            },
            "table_counts": {key: len(value) for key, value in tables.items()},
            "total_records": sum(len(value) for value in tables.values()),
            "conflicts": conflicts,
            "can_confirm": not conflicts,
            "excluded": payload.get("excluded", []),
            "warnings": [
                "外部系统连接参数和模型密钥不会恢复，需要在目标实例重新配置。"
            ],
        }
        package_sha256 = hashlib.sha256(content).hexdigest()
        row = RestorePreviewRow(
            file_name=file_name,
            package_sha256=package_sha256,
            package_path="pending",
            status="READY" if not conflicts else "CONFLICT",
            summary=summary,
            expires_at=now_utc() + timedelta(hours=24),
        )
        self.session.add(row)
        self.session.flush()
        path = self._base_dir() / f"{row.id}.zip"
        try:
            atomic_write(path, lambda temporary_path: temporary_path.write_bytes(content))
        except Exception:
            self.session.delete(row)
            self.session.flush()
            raise
        row.package_path = str(path)
        self.session.flush()
        return RestorePreviewView.model_validate(row)

    def confirm(self, preview_id: UUID) -> RestoreResultView:
        row = self.session.get(RestorePreviewRow, str(preview_id))
        if row is None:
            raise DomainError(
                "RESTORE_PREVIEW_NOT_FOUND", "恢复预览不存在。", status_code=404
            )
        if row.consumed_at is not None:
            raise DomainError(
                "RESTORE_PREVIEW_CONSUMED", "该恢复预览已经使用。", status_code=409
            )
        expires_at = row.expires_at
        if expires_at.tzinfo is None:
            expires_at = expires_at.replace(tzinfo=now_utc().tzinfo)
        if expires_at < now_utc():
            raise DomainError(
                "RESTORE_PREVIEW_EXPIRED", "恢复预览已过期，请重新上传。", status_code=410
            )
        if row.status != "READY":
            raise DomainError(
                "RESTORE_CONFLICTS_PRESENT",
                "恢复预览存在标识冲突，不能确认。",
                status_code=409,
                details=row.summary.get("conflicts", []),
            )
        path = Path(row.package_path).resolve()
        base = self._base_dir().resolve()
        if not path.is_relative_to(base) or not path.is_file():
            raise DomainError(
                "RESTORE_PACKAGE_MISSING", "恢复包临时文件不存在。", status_code=404
            )
        content = path.read_bytes()
        if hashlib.sha256(content).hexdigest() != row.package_sha256:
            raise DomainError(
                "RESTORE_PACKAGE_CHANGED",
                "恢复包在预览后发生变化，未执行恢复。",
                status_code=409,
            )
        payload = self._payload(content)
        result = restore_archive(self.session, payload)
        row.status = "RESTORED"
        row.consumed_at = now_utc()
        self.session.flush()
        return RestoreResultView.model_validate(result)

    def _base_dir(self) -> Path:
        path = self.settings.data_dir / "restore-previews"
        path.mkdir(parents=True, exist_ok=True)
        return path

    @staticmethod
    def _payload(content: bytes) -> dict[str, Any]:
        try:
            with ZipFile(io.BytesIO(content)) as archive:
                names = set(archive.namelist())
                required = {"restore.json", "restore.manifest.json"}
                if not required.issubset(names):
                    raise DomainError(
                        "RESTORE_CONTENT_MISSING",
                        "ZIP 中缺少 restore.json 或完整性清单。",
                        status_code=422,
                    )
                for name in required:
                    info = archive.getinfo(name)
                    if info.file_size > MAX_RESTORE_PAYLOAD_BYTES:
                        raise DomainError(
                            "RESTORE_PAYLOAD_TOO_LARGE",
                            "恢复包解压后的核心数据过大。",
                            status_code=413,
                        )
                payload = json.loads(archive.read("restore.json"))
                manifest = json.loads(archive.read("restore.manifest.json"))
        except DomainError:
            raise
        except (BadZipFile, KeyError, UnicodeDecodeError, json.JSONDecodeError) as exc:
            raise DomainError(
                "RESTORE_PACKAGE_INVALID",
                "恢复包损坏或内容不可读取。",
                status_code=422,
            ) from exc
        return validate_archive_payload(payload, manifest)
