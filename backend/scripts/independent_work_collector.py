"""Independent Windows work-observation collector.

This file is intentionally standalone: it does not import the web application,
does not require a management-system login, and can be copied to an employee
computer as a separate collector package.
"""

from __future__ import annotations

import argparse
import hashlib
import json
import logging
import platform
import sqlite3
import sys
import time
import urllib.error
import urllib.request
from dataclasses import dataclass, field
from datetime import UTC, datetime
from pathlib import Path
from typing import Any
from uuid import uuid4

COLLECTOR_VERSION = "1.1"


@dataclass(slots=True)
class CollectorConfig:
    source_id: str
    employee_key: str
    project_id: str | None = None
    role_key: str | None = None
    upload_url: str | None = None
    cache_dir: Path = field(default_factory=lambda: Path.home() / ".enterprise-insight-collector")
    poll_seconds: int = 5
    idle_after_seconds: int = 300
    headers: dict[str, str] = field(default_factory=dict)
    retry_seconds: int = 30
    log_file: Path | None = None

    @classmethod
    def from_json(cls, path: Path) -> CollectorConfig:
        payload = json.loads(path.read_text(encoding="utf-8"))
        cache_dir = Path(payload.get("cache_dir", "cache"))
        payload["cache_dir"] = cache_dir if cache_dir.is_absolute() else path.parent / cache_dir
        if payload.get("log_file"):
            log_file = Path(payload["log_file"])
            payload["log_file"] = log_file if log_file.is_absolute() else path.parent / log_file
        return cls(**payload)


def _now() -> datetime:
    return datetime.now(UTC)


def _iso(value: datetime) -> str:
    return value.astimezone(UTC).isoformat().replace("+00:00", "Z")


def _stable_batch_id(source_id: str, event_ids: list[str]) -> str:
    digest = hashlib.sha256("\n".join(event_ids).encode("utf-8")).hexdigest()[:24]
    return f"{source_id}-{digest}"


class WorkObservationCollector:
    """Collect coarse foreground activity and deliver it online or offline."""

    def __init__(self, config: CollectorConfig) -> None:
        if not config.source_id.strip() or not config.employee_key.strip():
            raise ValueError("source_id and employee_key are required")
        self.config = config
        self.config.cache_dir.mkdir(parents=True, exist_ok=True)
        self.logger = logging.getLogger("enterprise_insight.collector")
        if not self.logger.handlers:
            handler: logging.Handler
            if self.config.log_file:
                self.config.log_file.parent.mkdir(parents=True, exist_ok=True)
                handler = logging.FileHandler(self.config.log_file, encoding="utf-8")
            else:
                handler = logging.StreamHandler()
            handler.setFormatter(logging.Formatter("%(asctime)s %(levelname)s %(message)s"))
            self.logger.addHandler(handler)
            self.logger.setLevel(logging.INFO)
        self.database_path = self.config.cache_dir / "collector-cache.sqlite3"
        self._connection = sqlite3.connect(self.database_path)
        self._connection.execute(
            "CREATE TABLE IF NOT EXISTS pending_events ("
            "event_id TEXT PRIMARY KEY, payload_json TEXT NOT NULL, "
            "sequence INTEGER NOT NULL, created_at TEXT NOT NULL)"
        )
        self._connection.execute(
            "CREATE TABLE IF NOT EXISTS collector_state (key TEXT PRIMARY KEY, value TEXT NOT NULL)"
        )
        self._connection.commit()
        self._session_id = self._state("session_id") or str(uuid4())
        self._set_state("session_id", self._session_id)
        self._last_activity_key: str | None = None

    def close(self) -> None:
        self._connection.close()

    def __enter__(self) -> WorkObservationCollector:
        return self

    def __exit__(self, *_: object) -> None:
        self.close()

    def _state(self, key: str) -> str | None:
        row = self._connection.execute(
            "SELECT value FROM collector_state WHERE key = ?", (key,)
        ).fetchone()
        return str(row[0]) if row else None

    def _set_state(self, key: str, value: str) -> None:
        self._connection.execute(
            "INSERT INTO collector_state(key, value) VALUES (?, ?) "
            "ON CONFLICT(key) DO UPDATE SET value=excluded.value",
            (key, value),
        )
        self._connection.commit()

    def _next_sequence(self) -> int:
        sequence = int(self._state("sequence") or "0") + 1
        self._set_state("sequence", str(sequence))
        return sequence

    def queue_activity(
        self,
        *,
        app: str,
        category: str | None,
        context: str | None,
        domain: str | None = None,
        state: str = "FOREGROUND",
        observed_at: datetime | None = None,
        session_id: str | None = None,
        metadata: dict[str, Any] | None = None,
    ) -> dict[str, Any]:
        observed_at = observed_at or _now()
        if observed_at.tzinfo is None or observed_at.utcoffset() is None:
            raise ValueError("observed_at must include a timezone")
        event_id = str(uuid4())
        event = {
            "event_id": event_id,
            "session_id": session_id or self._session_id,
            "sequence": self._next_sequence(),
            "observed_at": _iso(observed_at),
            "source_employee_key": self.config.employee_key,
            "source_role_key": self.config.role_key,
            "activity": {
                "app": app,
                "domain": domain,
                "category": category,
                "context": context,
            },
            "state": state.upper(),
            "metadata": metadata or {},
        }
        self._connection.execute(
            "INSERT INTO pending_events(event_id, payload_json, sequence, created_at) "
            "VALUES (?, ?, ?, ?)",
            (event_id, json.dumps(event, ensure_ascii=False), event["sequence"], _iso(_now())),
        )
        self._connection.commit()
        return event

    def capture_once(self, *, force: bool = False) -> dict[str, Any] | None:
        activity = self._capture_windows_activity()
        key = json.dumps(activity, sort_keys=True, ensure_ascii=False)
        if not force and key == self._last_activity_key:
            return None
        self._last_activity_key = key
        return self.queue_activity(**activity)

    def _capture_windows_activity(self) -> dict[str, Any]:
        if platform.system() != "Windows":
            raise RuntimeError("桌面采集器当前只支持 Windows；可使用 queue_activity 导入模拟事件。")
        import ctypes
        from ctypes import wintypes

        user32 = ctypes.windll.user32
        kernel32 = ctypes.windll.kernel32
        hwnd = user32.GetForegroundWindow()

        # Keep the default collector at application granularity.  Window
        # titles may contain customer names, order numbers or document text;
        # the browser extension is the opt-in path for a controlled domain and
        # page category.
        app_name = "Windows"
        try:
            process_id = wintypes.DWORD()
            user32.GetWindowThreadProcessId(hwnd, ctypes.byref(process_id))
            handle = kernel32.OpenProcess(0x1000, False, process_id.value)
            if handle:
                try:
                    executable = ctypes.create_unicode_buffer(260)
                    size = wintypes.DWORD(len(executable))
                    if kernel32.QueryFullProcessImageNameW(
                        handle, 0, executable, ctypes.byref(size)
                    ):
                        app_name = Path(executable.value).stem or app_name
                finally:
                    kernel32.CloseHandle(handle)
        except (AttributeError, OSError):
            # The activity is still useful when Windows denies process lookup.
            pass

        class LastInputInfo(ctypes.Structure):
            _fields_ = [("cbSize", wintypes.UINT), ("dwTime", wintypes.DWORD)]

        last_input = LastInputInfo(ctypes.sizeof(LastInputInfo), 0)
        idle_seconds = 0
        if user32.GetLastInputInfo(ctypes.byref(last_input)):
            idle_seconds = max(0, (kernel32.GetTickCount() - last_input.dwTime) // 1000)
        if idle_seconds >= self.config.idle_after_seconds:
            return {
                "app": app_name,
                "category": "IDLE",
                "context": "idle",
                "domain": None,
                "state": "IDLE",
                "metadata": {"idle_seconds": idle_seconds},
            }
        return {
            "app": app_name,
            "category": _application_category(app_name),
            "context": app_name,
            "domain": None,
            "state": "FOREGROUND",
            "metadata": {},
        }
    def pending_events(self, limit: int | None = 1000) -> list[dict[str, Any]]:
        if limit is None:
            rows = self._connection.execute(
                "SELECT payload_json FROM pending_events ORDER BY sequence"
            ).fetchall()
        else:
            rows = self._connection.execute(
                "SELECT payload_json FROM pending_events ORDER BY sequence LIMIT ?", (limit,)
            ).fetchall()
        return [json.loads(row[0]) for row in rows]

    def build_package(self, limit: int | None = 1000) -> dict[str, Any] | None:
        events = self.pending_events(limit)
        if not events:
            return None
        return {
            "format_version": COLLECTOR_VERSION,
            "batch_id": _stable_batch_id(self.config.source_id, [e["event_id"] for e in events]),
            "source_id": self.config.source_id,
            "events": events,
        }

    def write_offline_package(self, path: Path, limit: int | None = None) -> Path:
        # Offline transfer is an export operation, not a batch upload. Export
        # the complete local queue so the operator does not silently lose the
        # 1001st event when a device was disconnected for a long time.
        package = self.build_package(limit)
        if package is None:
            raise ValueError("没有待导出的活动事件。")
        path.parent.mkdir(parents=True, exist_ok=True)
        path.write_text(json.dumps(package, ensure_ascii=False, indent=2), encoding="utf-8")
        return path

    def flush_online(self, limit: int = 1000, timeout_seconds: int = 30) -> dict[str, Any] | None:
        if not self.config.upload_url or not self.config.project_id:
            raise ValueError("在线上传需要 upload_url 和 project_id。")
        package = self.build_package(limit)
        if package is None:
            return None
        body = {"project_id": self.config.project_id, "package": package}
        request = urllib.request.Request(
            self.config.upload_url,
            data=json.dumps(body, ensure_ascii=False).encode("utf-8"),
            headers={"Content-Type": "application/json", **self.config.headers},
            method="POST",
        )
        try:
            with urllib.request.urlopen(request, timeout=timeout_seconds) as response:
                result = json.loads(response.read().decode("utf-8"))
        except (urllib.error.URLError, TimeoutError, json.JSONDecodeError) as exc:
            raise RuntimeError(f"在线上传失败，事件仍保留在本地缓存：{exc}") from exc
        if not isinstance(result, dict):
            raise RuntimeError("在线上传返回格式无效，事件仍保留在本地缓存。")
        expected_count = len(package["events"])
        try:
            accepted_count = int(result["accepted_count"])
            duplicate_count = int(result["duplicate_count"])
            event_count = int(result["event_count"])
            upload_status = str(result["status"])
        except (KeyError, TypeError, ValueError) as exc:
            raise RuntimeError("在线上传缺少完整接收回执，事件仍保留在本地缓存。") from exc
        if (
            event_count != expected_count
            or accepted_count < 0
            or duplicate_count < 0
            or accepted_count + duplicate_count != expected_count
            or upload_status not in {"IMPORTED", "DUPLICATE"}
        ):
            raise RuntimeError("在线上传回执未确认全部事件，事件仍保留在本地缓存。")
        self._connection.executemany(
            "DELETE FROM pending_events WHERE event_id = ?",
            [(event["event_id"],) for event in package["events"]],
        )
        self._connection.commit()
        return result

    def run_forever(self) -> None:
        self.logger.info(
            "collector started: source_id=%s session_id=%s",
            self.config.source_id,
            self._session_id,
        )
        while True:
            try:
                event = self.capture_once()
                if event:
                    self.logger.info("queued event: %s", event["event_id"])
                if self.config.upload_url:
                    result = self.flush_online()
                    if result:
                        self.logger.info("uploaded package: %s", result)
            except KeyboardInterrupt:
                self.logger.info("collector stopped by user; local cache retained")
                return
            except Exception:
                self.logger.exception(
                    "collector cycle failed; local cache retained, retrying in %ss",
                    max(1, self.config.retry_seconds),
                )
                time.sleep(max(1, self.config.retry_seconds))
                continue
            time.sleep(max(1, self.config.poll_seconds))


def _application_category(app_name: str) -> str:
    """Return a coarse, non-content category for a desktop executable."""
    normalized = app_name.casefold()
    if normalized in {"chrome", "msedge", "firefox", "brave", "opera"}:
        return "BROWSER"
    if normalized in {"excel", "winword", "powerpnt", "outlook"}:
        return "OFFICE"
    if normalized in {"teams", "slack", "wechat", "dingtalk"}:
        return "COLLABORATION"
    return "DESKTOP"


def main() -> None:
    parser = argparse.ArgumentParser(description="独立企业工作观察采集器")
    parser.add_argument("--config", type=Path, required=True)
    parser.add_argument("--once", action="store_true", help="采集一次并退出")
    parser.add_argument("--offline", type=Path, help="采集一次并导出离线数据包")
    parser.add_argument("--flush", action="store_true", help="上传本地缓存事件")
    args = parser.parse_args()
    try:
        config = CollectorConfig.from_json(args.config)
        with WorkObservationCollector(config) as collector:
            if args.once or args.offline:
                collector.capture_once(force=True)
            if args.offline:
                collector.write_offline_package(args.offline)
            elif args.flush:
                collector.flush_online()
            elif not args.once:
                collector.run_forever()
    except KeyboardInterrupt:
        print("采集器已停止；本地缓存仍保留。", file=sys.stderr)
    except Exception as exc:
        print(f"采集器启动或运行失败：{exc}", file=sys.stderr)
        raise SystemExit(1) from exc


if __name__ == "__main__":
    main()
