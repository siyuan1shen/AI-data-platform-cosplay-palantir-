from __future__ import annotations

import hashlib
import json
import re
import shutil
import sqlite3
import threading
import time
from collections.abc import Iterator, Mapping
from contextlib import closing, contextmanager
from datetime import UTC, datetime
from pathlib import Path
from typing import Any
from uuid import uuid4

from sqlalchemy import Engine, event
from sqlalchemy.engine import Connection

FORMAT_ID = "enterprise-insight.multi-store-backup"
FORMAT_VERSION = 1
STORE_FILES = {
    "formal": "enterprise_insight_v3.db",
    "observation": "observations_v1.db",
    "potential": "potential_v1.db",
    "control": "control_v1.db",
}
MANIFEST_NAME = "backup.manifest.json"
MANIFEST_HASH_NAME = "backup.manifest.sha256"
_FENCE_CONNECTION_KEY_PREFIX = "enterprise_insight.backup_write_fence"
_WRITE_PREFIXES = {"INSERT", "UPDATE", "DELETE", "REPLACE", "CREATE", "ALTER", "DROP"}
_NON_WRITE_PREFIXES = {
    "SELECT",
    "EXPLAIN",
    "BEGIN",
    "COMMIT",
    "ROLLBACK",
    "SAVEPOINT",
    "RELEASE",
}


class BackupRestoreError(RuntimeError):
    def __init__(self, code: str, message: str) -> None:
        super().__init__(message)
        self.code = code


class StorageWriteFence:
    """Process-local barrier that drains and pauses SQLAlchemy writes to all stores."""

    def __init__(self) -> None:
        self._condition = threading.Condition()
        self._frozen = False
        self._active_writers = 0

    def enter_writer(self) -> None:
        with self._condition:
            self._condition.wait_for(lambda: not self._frozen)
            self._active_writers += 1

    def leave_writer(self) -> None:
        with self._condition:
            if self._active_writers <= 0:
                raise RuntimeError("Write-fence writer counter underflow.")
            self._active_writers -= 1
            if self._active_writers == 0:
                self._condition.notify_all()

    @property
    def active_writers(self) -> int:
        with self._condition:
            return self._active_writers

    @property
    def frozen(self) -> bool:
        with self._condition:
            return self._frozen

    @contextmanager
    def freeze(self, timeout_seconds: float = 30.0) -> Iterator[datetime]:
        deadline = time.monotonic() + timeout_seconds
        with self._condition:
            if self._frozen:
                raise BackupRestoreError("BACKUP_ALREADY_RUNNING", "已有备份/恢复栅栏正在运行。")
            self._frozen = True
            while self._active_writers:
                remaining = deadline - time.monotonic()
                if remaining <= 0 or not self._condition.wait(timeout=remaining):
                    self._frozen = False
                    self._condition.notify_all()
                    raise BackupRestoreError(
                        "BACKUP_WRITE_DRAIN_TIMEOUT",
                        "等待在途数据库写入结束超时；未生成备份。",
                    )
            acquired_at = datetime.now(UTC)
        try:
            yield acquired_at
        finally:
            with self._condition:
                self._frozen = False
                self._condition.notify_all()


class MultiStoreBackupService:
    """SQLite-only, four-store backup and restore into a new data directory."""

    def __init__(
        self,
        engines: Mapping[str, Engine],
        *,
        application_version: str,
        build_id: str,
        auxiliary_files: Mapping[str, Path] | None = None,
        auxiliary_directories: Mapping[str, Path] | None = None,
        write_fence: StorageWriteFence | None = None,
    ) -> None:
        if set(engines) != set(STORE_FILES):
            raise BackupRestoreError(
                "BACKUP_STORE_SET_INVALID",
                "备份必须且只能包含 formal、observation、potential、control 四库。",
            )
        self.engines = dict(engines)
        self.unsupported_stores = sorted(
            name for name, engine in engines.items() if engine.dialect.name != "sqlite"
        )
        self.application_version = application_version
        self.build_id = build_id
        self.auxiliary_files = dict(auxiliary_files or {})
        self.auxiliary_directories = dict(auxiliary_directories or {})
        self.write_fence = write_fence or StorageWriteFence()
        self._operation_lock = threading.Lock()
        self._install_write_fence()

    def backup(self, destination: Path, *, timeout_seconds: float = 30.0) -> dict[str, Any]:
        self._require_sqlite_support()
        target = Path(destination).expanduser().absolute()
        if target.exists() or target.is_symlink():
            raise BackupRestoreError(
                "BACKUP_DESTINATION_EXISTS", "备份目标已存在；为避免覆盖，已停止。"
            )
        target.parent.mkdir(parents=True, exist_ok=True)
        staging = target.parent / f".{target.name}.backup-{uuid4().hex}.tmp"
        staging.mkdir(exist_ok=False)
        published = False
        try:
            with self._operation_lock:
                with self.write_fence.freeze(timeout_seconds) as checkpoint_time:
                    manifest = self._write_backup(staging, checkpoint_time)
                self._write_manifest(staging, manifest)
                self._validate_backup_directory(staging, verify_compatibility=False)
            if target.exists() or target.is_symlink():
                raise BackupRestoreError(
                    "BACKUP_DESTINATION_EXISTS", "备份期间目标目录被其他程序创建；没有覆盖。"
                )
            _publish_new_directory(staging, target, "BACKUP_DESTINATION_EXISTS")
            published = True
            return manifest
        finally:
            if not published and staging.exists():
                shutil.rmtree(staging)

    def restore(self, backup_directory: Path, destination: Path) -> dict[str, Any]:
        self._require_sqlite_support()
        requested_source = Path(backup_directory).expanduser().absolute()
        if requested_source.is_symlink():
            raise BackupRestoreError("RESTORE_SOURCE_INVALID", "备份来源不能是符号链接。")
        source = requested_source.resolve(strict=True)
        if not source.is_dir():
            raise BackupRestoreError("RESTORE_SOURCE_INVALID", "备份来源必须是目录。")
        target = Path(destination).expanduser().absolute()
        if target.exists() or target.is_symlink():
            raise BackupRestoreError(
                "RESTORE_DESTINATION_EXISTS", "恢复目标已存在；为避免覆盖任何数据，已停止。"
            )
        # Verify the complete package, every database schema/hash, and version support
        # before creating any output directory.
        source_manifest = self._validate_backup_directory(source, verify_compatibility=True)
        target.parent.mkdir(parents=True, exist_ok=True)
        staging = target.parent / f".{target.name}.restore-{uuid4().hex}.tmp"
        staging.mkdir(exist_ok=False)
        published = False
        try:
            with self._operation_lock:
                with self.write_fence.freeze() as checkpoint_time:
                    for _store_name, file_name in STORE_FILES.items():
                        source_path = source / file_name
                        target_path = staging / file_name
                        shutil.copyfile(source_path, target_path)
                        expected_hash = source_manifest["stores"][_store_name]["sha256"]
                        if _sha256_file(target_path) != expected_hash:
                            raise BackupRestoreError(
                                "RESTORE_DATABASE_COPY_HASH_MISMATCH",
                                f"恢复副本的 {_store_name} 数据库 SHA256 不匹配。",
                            )
                    _copy_auxiliary_files(source_manifest, source, staging)
                    adjustments = _neutralize_running_actions(staging / STORE_FILES["formal"])
                    restored_manifest = self._make_restored_manifest(
                        staging,
                        source_manifest,
                        adjustments,
                        checkpoint_time,
                    )
                    self._write_manifest(staging, restored_manifest)
                    self._validate_backup_directory(staging, verify_compatibility=False)
                if target.exists() or target.is_symlink():
                    raise BackupRestoreError(
                        "RESTORE_DESTINATION_EXISTS",
                        "恢复期间目标目录被其他程序创建；没有覆盖。",
                    )
                _publish_new_directory(staging, target, "RESTORE_DESTINATION_EXISTS")
                published = True
                return restored_manifest
        finally:
            if not published and staging.exists():
                shutil.rmtree(staging)

    def inspect(
        self, backup_directory: Path, *, verify_compatibility: bool = True
    ) -> dict[str, Any]:
        """Validate a backup directory and return its public manifest."""
        self._require_sqlite_support()
        requested_source = Path(backup_directory).expanduser().absolute()
        if requested_source.is_symlink():
            raise BackupRestoreError("RESTORE_SOURCE_INVALID", "备份来源不能是符号链接。")
        source = requested_source.resolve(strict=True)
        if not source.is_dir():
            raise BackupRestoreError("RESTORE_SOURCE_INVALID", "备份来源必须是目录。")
        with self._operation_lock:
            manifest = self._validate_backup_directory(
                source, verify_compatibility=verify_compatibility
            )
        manifest.pop("_directory", None)
        return manifest

    def _install_write_fence(self) -> None:
        for engine in self.engines.values():
            if engine.dialect.name != "sqlite":
                continue
            fence = self.write_fence
            connection_key = f"{_FENCE_CONNECTION_KEY_PREFIX}:{id(fence)}"

            def before_cursor_execute(
                connection: Connection,
                _cursor: Any,
                statement: str,
                _parameters: Any,
                _context: Any,
                _executemany: bool,
                *,
                _fence: StorageWriteFence = fence,
                _connection_key: str = connection_key,
            ) -> None:
                if not _may_write(statement):
                    return
                info = connection.info
                if not info.get(_connection_key):
                    _fence.enter_writer()
                    info[_connection_key] = True

            def release_connection(
                _dbapi_connection: Any,
                connection_record: Any,
                _exception: Any = None,
                *,
                _fence: StorageWriteFence = fence,
                _connection_key: str = connection_key,
            ) -> None:
                if connection_record.info.pop(_connection_key, False):
                    _fence.leave_writer()

            event.listen(engine, "before_cursor_execute", before_cursor_execute)
            event.listen(engine.pool, "checkin", release_connection)
            event.listen(engine.pool, "invalidate", release_connection)

    def _write_backup(self, staging: Path, checkpoint_time: datetime) -> dict[str, Any]:
        checkpoint_id = str(uuid4())
        stores: dict[str, Any] = {}
        for store_name, file_name in STORE_FILES.items():
            engine = self.engines[store_name]
            database_path = staging / file_name
            with engine.connect() as source_connection:
                raw = source_connection.connection.driver_connection
                with closing(sqlite3.connect(database_path)) as destination_connection:
                    raw.backup(destination_connection, pages=256, sleep=0.01)
            metadata = _sqlite_metadata(database_path, store_name)
            stores[store_name] = {
                "file": file_name,
                "schema_version": metadata["schema_version"],
                "sqlite_version": metadata["sqlite_version"],
                "schema_sha256": metadata["schema_sha256"],
                "sha256": _sha256_file(database_path),
                "size_bytes": database_path.stat().st_size,
            }
        files = _copy_auxiliary_files_for_backup(self.auxiliary_files, staging)
        files.update(_copy_auxiliary_directories_for_backup(self.auxiliary_directories, staging))
        pending_work = _read_pending_work(
            staging / STORE_FILES["formal"], staging / STORE_FILES["control"]
        )
        return {
            "format": FORMAT_ID,
            "format_version": FORMAT_VERSION,
            "backup_id": checkpoint_id,
            "created_at": datetime.now(UTC).isoformat(),
            "application": {"version": self.application_version, "build_id": self.build_id},
            "checkpoint": {
                "id": checkpoint_id,
                "kind": "PROCESS_LOCAL_SQLALCHEMY_WRITE_FENCE",
                "frozen_at": checkpoint_time.isoformat(),
                "store_set": list(STORE_FILES),
                "pending_work_captured_while_frozen": True,
            },
            "stores": stores,
            "files": files,
            "pending_work": pending_work,
            "restore_policy": {
                "external_action_invocations_are_never_replayed": True,
                "running_action_invocations_become_cancelled_for_manual_reconciliation": True,
            },
            "limitations": [
                "整体一致性栅栏覆盖本进程中接入的 SQLAlchemy SQLite 写入；"
                "不协调其他进程直接写同一数据库文件。",
                "SHA256 用于完整性校验，不提供签名或来源真实性证明。",
                "本机模型密钥文件会随备份复制，以保留已保存模型凭据的可解密性；备份文件因此包含本机密钥材料。",
                "restore-previews、schema-backups 等临时或迁移回滚目录不随运行数据备份复制。",
            ],
        }

    def _make_restored_manifest(
        self,
        directory: Path,
        source_manifest: dict[str, Any],
        adjustments: list[dict[str, str]],
        checkpoint_time: datetime,
    ) -> dict[str, Any]:
        manifest = dict(source_manifest)
        manifest["backup_id"] = str(uuid4())
        manifest["created_at"] = datetime.now(UTC).isoformat()
        manifest["checkpoint"] = {
            "id": str(uuid4()),
            "kind": "RESTORE_COPY_VERIFIED",
            "frozen_at": checkpoint_time.isoformat(),
            "source_backup_id": source_manifest["backup_id"],
        }
        manifest["stores"] = {}
        for store_name, file_name in STORE_FILES.items():
            path = directory / file_name
            metadata = _sqlite_metadata(path, store_name)
            manifest["stores"][store_name] = {
                "file": file_name,
                "schema_version": metadata["schema_version"],
                "sqlite_version": metadata["sqlite_version"],
                "schema_sha256": metadata["schema_sha256"],
                "sha256": _sha256_file(path),
                "size_bytes": path.stat().st_size,
            }
        manifest["files"] = _manifest_files_for_directory(
            directory,
            [item["file"] for item in manifest["stores"].values()],
        )
        manifest["restore"] = {
            "source_backup_id": source_manifest["backup_id"],
            "source_manifest_sha256": _sha256_file(
                Path(source_manifest["_directory"]) / MANIFEST_NAME
            ),
            "restored_at": datetime.now(UTC).isoformat(),
            "action_invocation_adjustments": adjustments,
            "external_effects_replayed": False,
            "manual_reconciliation_required": bool(adjustments),
        }
        manifest["pending_work_at_backup"] = manifest.get("pending_work", {})
        manifest["pending_work"] = _read_pending_work(
            directory / STORE_FILES["formal"], directory / STORE_FILES["control"]
        )
        return manifest

    def _write_manifest(self, directory: Path, manifest: dict[str, Any]) -> None:
        manifest.pop("_directory", None)
        manifest_path = directory / MANIFEST_NAME
        manifest_path.write_bytes(_canonical_json(manifest))
        (directory / MANIFEST_HASH_NAME).write_text(
            _sha256_file(manifest_path) + "\n", encoding="ascii"
        )

    def _validate_backup_directory(
        self, directory: Path, *, verify_compatibility: bool
    ) -> dict[str, Any]:
        manifest_path = directory / MANIFEST_NAME
        hash_path = directory / MANIFEST_HASH_NAME
        if (
            manifest_path.is_symlink()
            or hash_path.is_symlink()
            or not manifest_path.is_file()
            or not hash_path.is_file()
        ):
            raise BackupRestoreError("BACKUP_MANIFEST_MISSING", "备份缺少有效清单或清单摘要。")
        if hash_path.read_text(encoding="ascii").strip().lower() != _sha256_file(manifest_path):
            raise BackupRestoreError("BACKUP_MANIFEST_HASH_MISMATCH", "备份清单 SHA256 校验失败。")
        try:
            manifest = json.loads(manifest_path.read_text(encoding="utf-8"))
        except (OSError, UnicodeDecodeError, json.JSONDecodeError) as exc:
            raise BackupRestoreError("BACKUP_MANIFEST_INVALID", "备份清单不是有效 JSON。") from exc
        if (
            not isinstance(manifest, dict)
            or manifest.get("format") != FORMAT_ID
            or manifest.get("format_version") != FORMAT_VERSION
        ):
            raise BackupRestoreError("BACKUP_FORMAT_UNSUPPORTED", "备份格式或版本不受支持。")
        stores = manifest.get("stores")
        if not isinstance(stores, dict) or set(stores) != set(STORE_FILES):
            raise BackupRestoreError("BACKUP_STORE_SET_INVALID", "清单中的数据库集合不完整。")
        supported_versions = self._live_schema_versions() if verify_compatibility else {}
        for store_name, expected_file in STORE_FILES.items():
            record = stores.get(store_name)
            if not isinstance(record, dict) or record.get("file") != expected_file:
                raise BackupRestoreError(
                    "BACKUP_FILE_MAP_INVALID", f"{store_name} 数据库文件映射无效。"
                )
            path = directory / expected_file
            if path.is_symlink() or not path.is_file():
                raise BackupRestoreError(
                    "BACKUP_DATABASE_MISSING", f"缺少 {store_name} 数据库文件。"
                )
            if record.get("size_bytes") != path.stat().st_size or record.get(
                "sha256"
            ) != _sha256_file(path):
                raise BackupRestoreError(
                    "BACKUP_DATABASE_HASH_MISMATCH",
                    f"{store_name} 数据库文件大小或 SHA256 不匹配。",
                )
            metadata = _sqlite_metadata(path, store_name)
            if record.get("schema_version") != metadata["schema_version"]:
                raise BackupRestoreError(
                    "BACKUP_DATABASE_VERSION_MISMATCH", f"{store_name} 数据库版本与实际结构不符。"
                )
            if record.get("schema_sha256") != metadata["schema_sha256"]:
                raise BackupRestoreError(
                    "BACKUP_DATABASE_SCHEMA_MISMATCH", f"{store_name} 数据库结构指纹不匹配。"
                )
            expected_version = supported_versions.get(store_name)
            if expected_version is not None and metadata["schema_version"] != expected_version:
                raise BackupRestoreError(
                    "BACKUP_DATABASE_VERSION_UNSUPPORTED",
                    f"{store_name} 数据库版本 {metadata['schema_version']} 与当前程序支持版本 "
                    f"{expected_version} 不一致。",
                )
        expected_files = manifest.get("files")
        actual_files = _manifest_files_for_directory(
            directory, [item["file"] for item in stores.values()]
        )
        if expected_files != actual_files:
            raise BackupRestoreError(
                "BACKUP_FILE_MANIFEST_MISMATCH", "辅助文件清单与实际文件不一致。"
            )
        manifest["_directory"] = str(directory)
        return manifest

    def _live_schema_versions(self) -> dict[str, str]:
        versions: dict[str, str] = {}
        for store_name, engine in self.engines.items():
            try:
                with engine.connect() as connection:
                    raw_connection = connection.connection.driver_connection
                    versions[store_name] = _schema_version_from_connection(
                        raw_connection, store_name
                    )
            except BackupRestoreError:
                raise
            except Exception as exc:
                raise BackupRestoreError(
                    "RESTORE_CURRENT_SCHEMA_UNAVAILABLE",
                    f"无法读取当前运行程序支持的 {store_name} 数据库版本；恢复已停止。",
                ) from exc
        return versions

    def _require_sqlite_support(self) -> None:
        if self.unsupported_stores:
            raise BackupRestoreError(
                "BACKUP_NON_SQLITE_UNSUPPORTED",
                "当前整体备份/恢复只支持 SQLite；非 SQLite 数据库未实现一致性快照适配："
                + ", ".join(self.unsupported_stores),
            )


def _publish_new_directory(staging: Path, target: Path, conflict_code: str) -> None:
    """Claim an absent destination first, so POSIX rename cannot replace an empty dir."""
    try:
        target.mkdir(exist_ok=False)
    except FileExistsError as exc:
        raise BackupRestoreError(
            conflict_code, "目标目录已存在；为避免覆盖任何用户数据，已停止。"
        ) from exc
    try:
        for child in staging.iterdir():
            child.rename(target / child.name)
        staging.rmdir()
    except Exception:
        # target was created exclusively by this call and is not the user's prior data.
        shutil.rmtree(target)
        raise


def _may_write(statement: str) -> bool:
    text = statement.lstrip()
    if not text:
        return False
    # Ignore leading SQL comments before inspecting the first keyword.
    text = re.sub(r"^(?:\s|--[^\n]*(?:\n|$)|/\*.*?\*/)+", "", text, flags=re.DOTALL)
    match = re.match(r"([A-Za-z]+)", text)
    if match is None:
        return True
    keyword = match.group(1).upper()
    if keyword in _WRITE_PREFIXES:
        return True
    if keyword in _NON_WRITE_PREFIXES:
        return False
    if keyword == "PRAGMA":
        return (
            bool(re.match(r"(?is)^PRAGMA\s+[^;=]+\s*=", text)) or "wal_checkpoint" in text.lower()
        )
    # Conservatively fence WITH, ATTACH, VACUUM, and vendor/extension statements.
    return True


def _sqlite_online_backup(source: Path, destination: Path) -> None:
    try:
        with closing(sqlite3.connect(source)) as source_connection:
            with closing(sqlite3.connect(destination)) as destination_connection:
                source_connection.backup(destination_connection, pages=256, sleep=0.01)
    except sqlite3.Error as exc:
        raise BackupRestoreError(
            "RESTORE_SQLITE_BACKUP_FAILED", "SQLite 在线副本创建失败。"
        ) from exc


def _sqlite_metadata(path: Path, store_name: str) -> dict[str, Any]:
    try:
        with closing(sqlite3.connect(path)) as connection:
            integrity = connection.execute("PRAGMA integrity_check").fetchall()
            if integrity != [("ok",)]:
                raise BackupRestoreError(
                    "BACKUP_DATABASE_INTEGRITY_FAILED", f"{store_name} 数据库完整性检查失败。"
                )
            foreign_key_errors = connection.execute("PRAGMA foreign_key_check").fetchall()
            if foreign_key_errors:
                raise BackupRestoreError(
                    "BACKUP_DATABASE_FOREIGN_KEY_FAILED", f"{store_name} 数据库存在外键错误。"
                )
            schema_version = _schema_version_from_connection(connection, store_name)
            schema_rows = connection.execute(
                "SELECT type, name, tbl_name, COALESCE(sql, '') FROM sqlite_master "
                "WHERE name NOT LIKE 'sqlite_%' ORDER BY type, name"
            ).fetchall()
    except BackupRestoreError:
        raise
    except (sqlite3.Error, OSError) as exc:
        raise BackupRestoreError(
            "BACKUP_SQLITE_INVALID", f"{store_name} 不是可读取的 SQLite 数据库。"
        ) from exc
    schema_digest = hashlib.sha256(_canonical_json(schema_rows)).hexdigest()
    return {
        "schema_version": schema_version,
        "sqlite_version": sqlite3.sqlite_version,
        "schema_sha256": schema_digest,
    }


def _schema_version_from_connection(connection: Any, store_name: str) -> str:
    tables = {
        row[0]
        for row in connection.execute(
            "SELECT name FROM sqlite_master WHERE type='table'"
        ).fetchall()
    }
    if store_name == "formal":
        if "alembic_version" not in tables:
            raise BackupRestoreError(
                "BACKUP_DATABASE_VERSION_MISSING", "正式库缺少 Alembic 版本记录。"
            )
        revisions = sorted(
            row[0] for row in connection.execute("SELECT version_num FROM alembic_version")
        )
        if not revisions:
            raise BackupRestoreError(
                "BACKUP_DATABASE_VERSION_MISSING", "正式库没有已安装的迁移版本。"
            )
        return ",".join(revisions)
    if store_name in {"observation", "potential"}:
        # In the unified platform database schema versions are logical-domain
        # rows; PRAGMA user_version is a property of the whole SQLite file and
        # cannot represent more than one domain.  Keep the PRAGMA fallback for
        # legacy split databases produced before unified storage.
        schema_table = f"{store_name}_schema"
        if schema_table in tables:
            row = connection.execute(
                f"SELECT version FROM {schema_table} WHERE id=1"
            ).fetchone()
            if row is not None and int(row[0]) > 0:
                return str(int(row[0]))
        version = int(connection.execute("PRAGMA user_version").fetchone()[0])
        if version <= 0:
            raise BackupRestoreError(
                "BACKUP_DATABASE_VERSION_MISSING", f"{store_name} 数据库缺少有效版本。"
            )
        return str(version)
    if store_name == "control":
        if "control_schema" not in tables:
            raise BackupRestoreError(
                "BACKUP_DATABASE_VERSION_MISSING", "控制库缺少 control_schema 版本记录。"
            )
        row = connection.execute("SELECT version FROM control_schema WHERE id=1").fetchone()
        if row is None:
            raise BackupRestoreError(
                "BACKUP_DATABASE_VERSION_MISSING", "控制库没有已安装的版本记录。"
            )
        return str(int(row[0]))
    raise BackupRestoreError("BACKUP_STORE_SET_INVALID", f"未知数据库：{store_name}。")


def _read_pending_work(formal_path: Path, control_path: Path) -> dict[str, Any]:
    actions: list[dict[str, str]] = []
    with closing(sqlite3.connect(formal_path)) as connection:
        columns = {row[1] for row in connection.execute("PRAGMA table_info(action_invocations)")}
        if {"id", "status"}.issubset(columns):
            terminal = (
                "SUCCEEDED",
                "FAILED",
                "CANCELLED",
                "EFFECTIVE",
                "INEFFECTIVE",
                "ROLLED_BACK",
            )
            placeholders = ",".join("?" for _ in terminal)
            actions = [
                {"id": str(row[0]), "status": str(row[1])}
                for row in connection.execute(
                    f"SELECT id, status FROM action_invocations "
                    f"WHERE status NOT IN ({placeholders}) ORDER BY id",
                    terminal,
                )
            ]
    receipts: list[dict[str, str]] = []
    candidate_acceptances: list[dict[str, str]] = []
    with closing(sqlite3.connect(control_path)) as connection:
        tables = {
            row[0]
            for row in connection.execute("SELECT name FROM sqlite_master WHERE type='table'")
        }
        if "control_operation_receipts" in tables:
            receipts = [
                {"operation_id": str(row[0]), "operation_kind": str(row[1]), "status": str(row[2])}
                for row in connection.execute(
                    "SELECT operation_id, operation_kind, status FROM control_operation_receipts "
                    "WHERE status NOT IN ('COMMITTED', 'FAILED', 'CANCELLED', 'REJECTED') "
                    "ORDER BY operation_id"
                )
            ]
        if "control_potential_candidates" in tables:
            candidate_acceptances = [
                {"candidate_id": str(row[0]), "status": str(row[1])}
                for row in connection.execute(
                    "SELECT id, status FROM control_potential_candidates "
                    "WHERE status='ACCEPTING' ORDER BY id"
                )
            ]
    return {
        "action_invocations": actions,
        "control_operation_receipts": receipts,
        "potential_candidate_acceptances": candidate_acceptances,
    }


def _neutralize_running_actions(formal_path: Path) -> list[dict[str, str]]:
    adjustments: list[dict[str, str]] = []
    now = datetime.now(UTC).isoformat()
    error = json.dumps(
        {
            "code": "RESTORE_ACTION_OUTCOME_UNKNOWN",
            "message": "恢复不会重放动作；原执行是否已产生外部效果未知，请人工核对后再决定下一步。",
        },
        ensure_ascii=False,
        sort_keys=True,
        separators=(",", ":"),
    )
    try:
        with closing(sqlite3.connect(formal_path)) as connection:
            connection.execute("PRAGMA foreign_keys=ON")
            columns = {
                row[1] for row in connection.execute("PRAGMA table_info(action_invocations)")
            }
            if "id" not in columns or "status" not in columns or "error" not in columns:
                raise BackupRestoreError(
                    "RESTORE_ACTION_SCHEMA_UNSUPPORTED", "ActionInvocation 缺少状态/错误字段。"
                )
            rows = connection.execute(
                "SELECT id, status FROM action_invocations WHERE status='RUNNING' ORDER BY id"
            ).fetchall()
            set_parts = ["status='CANCELLED'", "error=?"]
            values: list[Any] = [error]
            if "finished_at" in columns:
                set_parts.append("finished_at=?")
                values.append(now)
            if "updated_at" in columns:
                set_parts.append("updated_at=?")
                values.append(now)
            for action_id, old_status in rows:
                connection.execute(
                    f"UPDATE action_invocations SET {', '.join(set_parts)} WHERE id=?",
                    [*values, action_id],
                )
                adjustments.append(
                    {
                        "action_invocation_id": str(action_id),
                        "from_status": str(old_status),
                        "to_status": "CANCELLED",
                    }
                )
            connection.commit()
    except BackupRestoreError:
        raise
    except sqlite3.Error as exc:
        raise BackupRestoreError(
            "RESTORE_ACTION_RECONCILIATION_FAILED", "无法将运行中的动作置为人工核对状态。"
        ) from exc
    return adjustments


def _copy_auxiliary_files_for_backup(
    configured_files: Mapping[str, Path], directory: Path
) -> dict[str, dict[str, Any]]:
    files: dict[str, dict[str, Any]] = {}
    for relative_name, configured_path in sorted(configured_files.items()):
        name = _validate_auxiliary_name(relative_name)
        source = Path(configured_path)
        if source.is_symlink():
            raise BackupRestoreError(
                "BACKUP_AUXILIARY_FILE_INVALID", f"辅助文件不能是符号链接：{name}。"
            )
        if not source.exists():
            continue
        if not source.is_file():
            raise BackupRestoreError(
                "BACKUP_AUXILIARY_FILE_INVALID", f"辅助路径不是普通文件：{name}。"
            )
        destination = directory.joinpath(*Path(name).parts)
        destination.parent.mkdir(parents=True, exist_ok=True)
        shutil.copyfile(source, destination)
        files[name] = {
            "sha256": _sha256_file(destination),
            "size_bytes": destination.stat().st_size,
        }
    return files


def _copy_auxiliary_directories_for_backup(
    configured_directories: Mapping[str, Path], directory: Path
) -> dict[str, dict[str, Any]]:
    files: dict[str, dict[str, Any]] = {}
    for relative_root, configured_path in sorted(configured_directories.items()):
        safe_root = _validate_auxiliary_name(relative_root)
        source_root = Path(configured_path)
        if source_root.is_symlink():
            raise BackupRestoreError(
                "BACKUP_AUXILIARY_FILE_INVALID", f"辅助目录不能是符号链接：{safe_root}。"
            )
        if not source_root.exists():
            continue
        if not source_root.is_dir():
            raise BackupRestoreError(
                "BACKUP_AUXILIARY_FILE_INVALID", f"辅助路径不是目录：{safe_root}。"
            )
        for source_file in sorted(source_root.rglob("*")):
            if source_file.is_symlink():
                raise BackupRestoreError(
                    "BACKUP_AUXILIARY_FILE_INVALID",
                    f"辅助目录不能包含符号链接：{source_file.name}。",
                )
            if not source_file.is_file():
                continue
            relative_file = Path(safe_root, *source_file.relative_to(source_root).parts).as_posix()
            relative_file = _validate_auxiliary_name(relative_file)
            destination_file = directory.joinpath(*Path(relative_file).parts)
            destination_file.parent.mkdir(parents=True, exist_ok=True)
            shutil.copyfile(source_file, destination_file)
            files[relative_file] = {
                "sha256": _sha256_file(destination_file),
                "size_bytes": destination_file.stat().st_size,
            }
    return files


def _copy_auxiliary_files(manifest: dict[str, Any], source: Path, destination: Path) -> None:
    for name, record in manifest.get("files", {}).items():
        safe_name = _validate_auxiliary_name(name)
        source_path = source.joinpath(*Path(safe_name).parts)
        if source_path.is_symlink() or not source_path.is_file():
            raise BackupRestoreError(
                "BACKUP_AUXILIARY_FILE_MISSING", f"辅助文件不存在或不安全：{safe_name}。"
            )
        destination_path = destination.joinpath(*Path(safe_name).parts)
        destination_path.parent.mkdir(parents=True, exist_ok=True)
        shutil.copyfile(source_path, destination_path)
        if _sha256_file(destination_path) != record["sha256"]:
            raise BackupRestoreError(
                "RESTORE_AUXILIARY_FILE_HASH_MISMATCH", f"辅助文件校验失败：{safe_name}。"
            )


def _manifest_files_for_directory(
    directory: Path, database_files: list[str]
) -> dict[str, dict[str, Any]]:
    excluded = set(database_files) | {MANIFEST_NAME, MANIFEST_HASH_NAME}
    all_paths = list(directory.rglob("*"))
    for item in all_paths:
        if item.is_symlink():
            raise BackupRestoreError(
                "BACKUP_AUXILIARY_FILE_INVALID", f"备份目录不能包含符号链接：{item.name}。"
            )
    actual_names = {
        item.relative_to(directory).as_posix()
        for item in all_paths
        if item.is_file() and item.relative_to(directory).as_posix() not in excluded
    }
    result: dict[str, dict[str, Any]] = {}
    for name in sorted(actual_names):
        safe_name = _validate_auxiliary_name(name)
        path = directory.joinpath(*Path(safe_name).parts)
        result[safe_name] = {"sha256": _sha256_file(path), "size_bytes": path.stat().st_size}
    return result


def _validate_auxiliary_name(name: str) -> str:
    path = Path(name)
    if (
        not name
        or "\\" in name
        or path.is_absolute()
        or re.match(r"^[A-Za-z]:", name)
        or any(part in {"", ".", ".."} for part in name.split("/"))
        or name in {MANIFEST_NAME, MANIFEST_HASH_NAME}
    ):
        raise BackupRestoreError("BACKUP_AUXILIARY_FILE_INVALID", "辅助文件必须是安全的相对路径。")
    return "/".join(name.split("/"))


def _sha256_file(path: Path) -> str:
    digest = hashlib.sha256()
    with path.open("rb") as stream:
        for chunk in iter(lambda: stream.read(1024 * 1024), b""):
            digest.update(chunk)
    return digest.hexdigest()


def _canonical_json(value: Any) -> bytes:
    return json.dumps(value, ensure_ascii=False, sort_keys=True, separators=(",", ":")).encode(
        "utf-8"
    )
