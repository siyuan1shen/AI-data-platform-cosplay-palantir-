from __future__ import annotations

from pathlib import Path

from fastapi.testclient import TestClient

from enterprise_insight_backend.app import create_app
from enterprise_insight_backend.config import Settings


def test_unexpected_request_failures_use_the_same_error_contract(tmp_path: Path) -> None:
    settings = Settings(
        data_dir=tmp_path,
        database_url=f"sqlite:///{(tmp_path / 'test.db').as_posix()}",
        frontend_dist_dir=tmp_path / "no-frontend",
        agent_worker_enabled=False,
    )
    application = create_app(settings)

    @application.get("/api/v3/test-unexpected", include_in_schema=False)
    def fail_unexpectedly() -> None:
        raise RuntimeError("internal implementation detail")

    with TestClient(application, raise_server_exceptions=False) as client:
        response = client.get(
            "/api/v3/test-unexpected",
            headers={"X-Trace-Id": "test-trace-123"},
        )

    assert response.status_code == 500
    assert response.headers["X-Trace-Id"] == "test-trace-123"
    assert response.json() == {
        "error": {
            "code": "INTERNAL_SERVER_ERROR",
            "message": "服务端发生未预期错误，请根据 trace id 联系开发者。",
            "details": [],
            "trace_id": "test-trace-123",
        }
    }
