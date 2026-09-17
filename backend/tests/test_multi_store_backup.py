from __future__ import annotations

import hashlib
import json
import sqlite3
import time
from concurrent.futures import ThreadPoolExecutor
from pathlib import Path
from threading import Event
from types import SimpleNamespace
from typing import Any

import pytest
from fastapi.testclient import TestClient
from sqlalchemy import create_engine

from enterprise_insight_backend.app import create_app
from enterprise_insight_backend.config import Settings
from enterprise_insight_backend.multi_store_backup import (
    MANIFEST_HASH_NAME,
    MANIFEST_NAME,
    STORE_FILES,
    BackupRestoreError,
    MultiStoreBackupService,
)


def _client(data_dir: Path) -> TestClient:
    return TestClient(
        create_app(Settings(data_dir=data_dir, frontend_dist_dir=None, agent_worker_enabled=False))
    )


def _seed_store_markers(client: TestClient, marker: str) -> None:
    app = client.app
    engines = {
        "formal": app.state.database.engine,
        "observation": app.state.observation_database.engine,
        "potential": app.state.potential_database.engine,
        "control": app.state.control_database.engine,
    }
    for engine in engines.values():
        with engine.begin() as connection:
            connection.exec_driver_sql(
                "CREATE TABLE IF NOT EXISTS backup_acceptance_marker (value TEXT NOT NULL)"
            )
            connection.exec_driver_sql(
                "INSERT INTO backup_acceptance_marker(value) VALUES (?)", (marker,)
            )


def _read_marker(database_path: Path) -> list[str]:
    with sqlite3.connect(database_path) as connection:
        return [row[0] for row in connection.execute("SELECT value FROM backup_acceptance_marker")]


def _create_running_action(client: TestClient) -> tuple[str, str]:
    company = client.post("/api/v3/companies", json={"name": "备份验收企业"})
    assert company.status_code == 201, company.text
    project = client.post(
        f"/api/v3/companies/{company.json()['id']}/projects", json={"name": "备份验收项目"}
    )
    assert project.status_code == 201, project.text
    project_id = project.json()["id"]
    installed = client.post(f"/api/v3/projects/{project_id}/ontology/default-pack")
    assert installed.status_code == 200, installed.text
    definitions = client.get(f"/api/v3/projects/{project_id}/action-definitions")
    assert definitions.status_code == 200, definitions.text
    definition = next(
        item for item in definitions.json()["items"] if item["key"] == "create_entity"
    )
    invocation = client.post(
        f"/api/v3/projects/{project_id}/action-invocations",
        json={
            "action_definition_id": definition["id"],
            "idempotency_key": "backup-acceptance-running-action",
            "requested_by": "test",
            "input": {
                "type_key": "role",
                "stable_key": "role.backup-test",
                "name": "备份验收岗位",
                "properties": {},
            },
        },
    )
    assert invocation.status_code == 201, invocation.text
    invocation_id = invocation.json()["id"]
    with client.app.state.database.engine.begin() as connection:
        connection.exec_driver_sql(
            "UPDATE action_invocations SET status='RUNNING' WHERE id=?", (invocation_id,)
        )
    return project_id, invocation_id


def test_four_store_backup_and_restore_are_hash_checked_and_openable(tmp_path: Path) -> None:
    source_dir = tmp_path / "live"
    source_dir.mkdir()
    (source_dir / "model-profile.key").write_bytes(b"test-only-fernet-key")
    exports = source_dir / "exports" / "nested"
    exports.mkdir(parents=True)
    (exports / "report.csv").write_text("value\nrestored\n", encoding="utf-8")
    with _client(source_dir) as client:
        company = client.post("/api/v3/companies", json={"name": "四库备份样例"})
        assert company.status_code == 201, company.text
        _seed_store_markers(client, "snapshot-v1")
        service: MultiStoreBackupService = client.app.state.multi_store_backup

        backup_dir = tmp_path / "backup-one"
        manifest = service.backup(backup_dir)

        assert set(manifest["stores"]) == set(STORE_FILES)
        assert manifest["checkpoint"]["kind"] == "PROCESS_LOCAL_SQLALCHEMY_WRITE_FENCE"
        assert manifest["checkpoint"]["pending_work_captured_while_frozen"] is True
        assert (
            manifest["files"]["model-profile.key"]["sha256"]
            == hashlib.sha256(b"test-only-fernet-key").hexdigest()
        )
        assert "exports/nested/report.csv" in manifest["files"]
        assert (backup_dir / MANIFEST_NAME).is_file()
        assert (backup_dir / MANIFEST_HASH_NAME).is_file()
        for store_name, file_name in STORE_FILES.items():
            record = manifest["stores"][store_name]
            assert record["file"] == file_name
            assert (
                record["sha256"]
                == hashlib.sha256((backup_dir / file_name).read_bytes()).hexdigest()
            )
            assert record["schema_version"]
            assert record["schema_sha256"]
            assert _read_marker(backup_dir / file_name) == ["snapshot-v1"]

        restored_dir = tmp_path / "restored"
        restored = service.restore(backup_dir, restored_dir)
        assert restored["restore"]["external_effects_replayed"] is False
        assert restored["application"] == manifest["application"]
        assert (restored_dir / "model-profile.key").read_bytes() == b"test-only-fernet-key"
        assert (restored_dir / "exports/nested/report.csv").read_text(encoding="utf-8") == (
            "value\nrestored\n"
        )
        for store_name, file_name in STORE_FILES.items():
            assert _read_marker(restored_dir / file_name) == ["snapshot-v1"]
            assert (
                restored["stores"][store_name]["sha256"]
                == hashlib.sha256((restored_dir / file_name).read_bytes()).hexdigest()
            )

    # The restored directory uses the normal local database filenames and can be
    # opened as a fresh application data directory without replacing the source.
    with _client(restored_dir) as restored_client:
        companies = restored_client.get("/api/v3/companies")
        assert companies.status_code == 200, companies.text
        assert any(item["name"] == "四库备份样例" for item in companies.json()["items"])


def test_public_inspect_returns_manifest_and_rejects_modified_store(tmp_path: Path) -> None:
    with _client(tmp_path / "live") as client:
        service: MultiStoreBackupService = client.app.state.multi_store_backup
        backup_dir = tmp_path / "backup"
        manifest = service.backup(backup_dir)

        inspected = service.inspect(backup_dir)

        assert inspected["backup_id"] == manifest["backup_id"]
        assert "_directory" not in inspected
        with (backup_dir / STORE_FILES["formal"]).open("ab") as stream:
            stream.write(b"corruption")
        with pytest.raises(BackupRestoreError) as error:
            service.inspect(backup_dir)
        assert error.value.code == "BACKUP_DATABASE_HASH_MISMATCH"


def test_restore_marks_running_invocations_for_manual_reconciliation_not_replay(
    tmp_path: Path,
) -> None:
    source_dir = tmp_path / "live"
    source_dir.mkdir()
    with _client(source_dir) as client:
        _, invocation_id = _create_running_action(client)
        service: MultiStoreBackupService = client.app.state.multi_store_backup
        backup_dir = tmp_path / "backup"
        backup_manifest = service.backup(backup_dir)
        assert backup_manifest["pending_work"]["action_invocations"] == [
            {"id": invocation_id, "status": "RUNNING"}
        ]

        restored_dir = tmp_path / "restore-target"
        restore_manifest = service.restore(backup_dir, restored_dir)
        adjustment = restore_manifest["restore"]["action_invocation_adjustments"]
        assert adjustment == [
            {
                "action_invocation_id": invocation_id,
                "from_status": "RUNNING",
                "to_status": "CANCELLED",
            }
        ]
        assert restore_manifest["restore"]["manual_reconciliation_required"] is True
        assert (
            restore_manifest["restore_policy"]["external_action_invocations_are_never_replayed"]
            is True
        )
        with sqlite3.connect(restored_dir / STORE_FILES["formal"]) as connection:
            status, error = connection.execute(
                "SELECT status, error FROM action_invocations WHERE id=?", (invocation_id,)
            ).fetchone()
        assert status == "CANCELLED"
        assert json.loads(error)["code"] == "RESTORE_ACTION_OUTCOME_UNKNOWN"

        with _client(restored_dir) as restored_client:
            project_id = restored_client.get("/api/v3/companies").json()["items"][0]["id"]
            # Cancellation is durable after a normal app restart; restore did not
            # submit the invocation to an executor.
            with sqlite3.connect(
                restored_client.app.state.database.engine.url.database
            ) as connection:
                assert (
                    connection.execute(
                        "SELECT status FROM action_invocations WHERE id=?", (invocation_id,)
                    ).fetchone()[0]
                    == "CANCELLED"
                )
            assert project_id


def test_backup_waits_for_inflight_writer_then_captures_committed_value(tmp_path: Path) -> None:
    data_dir = tmp_path / "live"
    data_dir.mkdir()
    with _client(data_dir) as client:
        engine = client.app.state.database.engine
        with engine.begin() as connection:
            connection.exec_driver_sql("CREATE TABLE fence_probe (value TEXT NOT NULL)")
        connection = engine.connect()
        transaction = connection.begin()
        connection.exec_driver_sql("INSERT INTO fence_probe(value) VALUES ('committed')")
        service: MultiStoreBackupService = client.app.state.multi_store_backup

        with ThreadPoolExecutor(max_workers=1) as pool:
            future = pool.submit(service.backup, tmp_path / "backup")
            deadline = time.monotonic() + 3
            while not service.write_fence.frozen and time.monotonic() < deadline:
                time.sleep(0.01)
            assert service.write_fence.frozen
            assert not future.done()
            transaction.commit()
            connection.close()
            manifest = future.result(timeout=10)

        assert manifest["stores"]["formal"]["sha256"]
        with sqlite3.connect(tmp_path / "backup" / STORE_FILES["formal"]) as snapshot:
            assert snapshot.execute("SELECT value FROM fence_probe").fetchone()[0] == "committed"


def test_new_sqlalchemy_writes_wait_while_four_store_fence_is_frozen(tmp_path: Path) -> None:
    data_dir = tmp_path / "live"
    data_dir.mkdir()
    with _client(data_dir) as client:
        engine = client.app.state.database.engine
        with engine.begin() as connection:
            connection.exec_driver_sql("CREATE TABLE fence_probe (value TEXT NOT NULL)")
        service: MultiStoreBackupService = client.app.state.multi_store_backup
        started = Event()

        with ThreadPoolExecutor(max_workers=1) as pool:
            with service.write_fence.freeze():
                future = pool.submit(lambda: _insert_after_signal(engine, started))
                assert started.wait(timeout=2)
                time.sleep(0.05)
                assert not future.done()
            future.result(timeout=5)

        with engine.connect() as connection:
            assert (
                connection.exec_driver_sql("SELECT value FROM fence_probe").scalar_one()
                == "after-fence"
            )


def _insert_after_signal(engine: Any, started: Event) -> None:
    started.set()
    with engine.begin() as connection:
        connection.exec_driver_sql("INSERT INTO fence_probe(value) VALUES ('after-fence')")


def test_tampered_database_or_manifest_is_rejected_before_restore_creates_target(
    tmp_path: Path,
) -> None:
    data_dir = tmp_path / "live"
    data_dir.mkdir()
    with _client(data_dir) as client:
        service: MultiStoreBackupService = client.app.state.multi_store_backup
        hash_backup = tmp_path / "bad-manifest-hash"
        service.backup(hash_backup)
        with (hash_backup / MANIFEST_NAME).open("ab") as stream:
            stream.write(b" ")
        hash_target = tmp_path / "hash-target"
        with pytest.raises(BackupRestoreError) as hash_error:
            service.restore(hash_backup, hash_target)
        assert hash_error.value.code == "BACKUP_MANIFEST_HASH_MISMATCH"
        assert not hash_target.exists()

        backup_dir = tmp_path / "backup"
        service.backup(backup_dir)

        with (backup_dir / STORE_FILES["potential"]).open("ab") as stream:
            stream.write(b"tamper")
        target = tmp_path / "must-not-exist"
        with pytest.raises(BackupRestoreError, match="大小或 SHA256") as error:
            service.restore(backup_dir, target)
        assert error.value.code == "BACKUP_DATABASE_HASH_MISMATCH"
        assert not target.exists()

        # A modified schema fingerprint remains rejected even if the sidecar is
        # recomputed, proving structure validation is independent of file hashing.
        clean_backup = tmp_path / "clean-backup"
        service.backup(clean_backup)
        manifest_path = clean_backup / MANIFEST_NAME
        manifest = json.loads(manifest_path.read_text(encoding="utf-8"))
        manifest["stores"]["formal"]["schema_sha256"] = "f" * 64
        _rewrite_manifest(clean_backup, manifest)
        manifest_target = tmp_path / "manifest-target"
        with pytest.raises(BackupRestoreError) as manifest_error:
            service.restore(clean_backup, manifest_target)
        assert manifest_error.value.code == "BACKUP_DATABASE_SCHEMA_MISMATCH"
        assert not manifest_target.exists()

        version_backup = tmp_path / "version-backup"
        service.backup(version_backup)
        observation_path = version_backup / STORE_FILES["observation"]
        with sqlite3.connect(observation_path) as connection:
            connection.execute("PRAGMA user_version=999")
        version_manifest = json.loads((version_backup / MANIFEST_NAME).read_text(encoding="utf-8"))
        version_record = version_manifest["stores"]["observation"]
        version_record["schema_version"] = "999"
        version_record["sha256"] = hashlib.sha256(observation_path.read_bytes()).hexdigest()
        version_record["size_bytes"] = observation_path.stat().st_size
        _rewrite_manifest(version_backup, version_manifest)
        version_target = tmp_path / "version-target"
        with pytest.raises(BackupRestoreError) as version_error:
            service.restore(version_backup, version_target)
        assert version_error.value.code == "BACKUP_DATABASE_VERSION_UNSUPPORTED"
        assert not version_target.exists()


def test_backup_and_restore_never_overwrite_existing_user_data(tmp_path: Path) -> None:
    data_dir = tmp_path / "live"
    data_dir.mkdir()
    with _client(data_dir) as client:
        service: MultiStoreBackupService = client.app.state.multi_store_backup
        existing_backup = tmp_path / "already-here"
        existing_backup.mkdir()
        marker = existing_backup / "keep.txt"
        marker.write_text("user data", encoding="utf-8")
        with pytest.raises(BackupRestoreError) as backup_error:
            service.backup(existing_backup)
        assert backup_error.value.code == "BACKUP_DESTINATION_EXISTS"
        assert marker.read_text(encoding="utf-8") == "user data"

        valid_backup = tmp_path / "valid-backup"
        service.backup(valid_backup)
        existing_restore = tmp_path / "restore-already-here"
        existing_restore.mkdir()
        restore_marker = existing_restore / "keep.txt"
        restore_marker.write_text("user data", encoding="utf-8")
        with pytest.raises(BackupRestoreError) as restore_error:
            service.restore(valid_backup, existing_restore)
        assert restore_error.value.code == "RESTORE_DESTINATION_EXISTS"
        assert restore_marker.read_text(encoding="utf-8") == "user data"


def test_non_sqlite_store_is_explicitly_unsupported() -> None:
    engines: dict[str, Any] = {name: create_engine("sqlite://") for name in STORE_FILES}
    engines["control"] = SimpleNamespace(dialect=SimpleNamespace(name="postgresql"))
    service = MultiStoreBackupService(engines, application_version="test", build_id="test")
    with pytest.raises(BackupRestoreError) as error:
        service.backup(Path("non-sqlite-backup-target"))
    assert error.value.code == "BACKUP_NON_SQLITE_UNSUPPORTED"
    assert "control" in str(error.value)
    with pytest.raises(BackupRestoreError) as inspect_error:
        service.inspect(Path("non-sqlite-backup-source"))
    assert inspect_error.value.code == "BACKUP_NON_SQLITE_UNSUPPORTED"


def test_write_drain_timeout_creates_no_backup_and_reopens_the_fence(tmp_path: Path) -> None:
    data_dir = tmp_path / "live"
    data_dir.mkdir()
    with _client(data_dir) as client:
        service: MultiStoreBackupService = client.app.state.multi_store_backup
        service.write_fence.enter_writer()
        try:
            with pytest.raises(BackupRestoreError) as error:
                service.backup(tmp_path / "timed-out-backup", timeout_seconds=0.01)
            assert error.value.code == "BACKUP_WRITE_DRAIN_TIMEOUT"
            assert not (tmp_path / "timed-out-backup").exists()
            assert not service.write_fence.frozen
        finally:
            service.write_fence.leave_writer()


def _rewrite_manifest(directory: Path, manifest: dict[str, Any]) -> None:
    manifest_path = directory / MANIFEST_NAME
    manifest_path.write_text(
        json.dumps(manifest, ensure_ascii=False, sort_keys=True, separators=(",", ":")),
        encoding="utf-8",
    )
    digest = hashlib.sha256(manifest_path.read_bytes()).hexdigest()
    (directory / MANIFEST_HASH_NAME).write_text(digest + "\n", encoding="ascii")
