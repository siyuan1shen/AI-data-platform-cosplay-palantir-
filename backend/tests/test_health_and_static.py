from __future__ import annotations

from pathlib import Path

from fastapi.testclient import TestClient

from enterprise_insight_backend.app import create_app
from enterprise_insight_backend.config import Settings
from enterprise_insight_backend.migration import DATABASE_SCHEMA_REVISION


def test_health_reports_build_schema_worker_and_frontend(tmp_path: Path) -> None:
    frontend = tmp_path / "frontend"
    frontend.mkdir()
    (frontend / "index.html").write_text("<main>V3 shell</main>", encoding="utf-8")
    settings = Settings(
        data_dir=tmp_path / "data",
        build_id="test-build",
        frontend_dist_dir=frontend,
        agent_worker_enabled=False,
    )
    with TestClient(create_app(settings)) as client:
        health = client.get("/api/v3/health")
        assert health.status_code == 200, health.text
        assert health.json() == {
            "status": "ok",
            "version": settings.app_version,
            "build_id": "test-build",
            "schema_revision": DATABASE_SCHEMA_REVISION,
            "database": "ready",
            "agent_worker": "disabled",
            "frontend": "ready",
        }
        assert "V3 shell" in client.get("/developer/").text
        assert "V3 shell" in client.get("/executive/").text
        assert client.get("/api/v3/not-a-real-route").status_code == 404


def test_health_marks_frontend_as_external_when_no_build_exists(tmp_path: Path) -> None:
    settings = Settings(
        data_dir=tmp_path / "data",
        frontend_dist_dir=tmp_path / "missing",
        agent_worker_enabled=False,
    )
    with TestClient(create_app(settings)) as client:
        assert client.get("/api/v3/health").json()["frontend"] == "external"

