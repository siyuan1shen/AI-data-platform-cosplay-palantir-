from __future__ import annotations

from collections.abc import Generator
from pathlib import Path

import pytest
from fastapi.testclient import TestClient

from enterprise_insight_backend import integration as integration_module
from enterprise_insight_backend.app import create_app
from enterprise_insight_backend.config import Settings


@pytest.fixture
def client(tmp_path: Path, monkeypatch: pytest.MonkeyPatch) -> Generator[TestClient, None, None]:
    settings = Settings(
        data_dir=tmp_path,
        database_url=f"sqlite:///{(tmp_path / 'test.db').as_posix()}",
        agent_worker_enabled=False,
    )
    monkeypatch.setattr(integration_module, "get_settings", lambda: settings)
    with TestClient(create_app(settings)) as test_client:
        yield test_client
