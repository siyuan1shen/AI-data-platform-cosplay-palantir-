from __future__ import annotations

from datetime import timedelta
from pathlib import Path
from uuid import UUID

from pytest import MonkeyPatch, mark

from enterprise_insight_backend.agent_runtime import AgentRuntimeService
from enterprise_insight_backend.agent_worker import AgentWorker
from enterprise_insight_backend.config import Settings
from enterprise_insight_backend.database import Database
from enterprise_insight_backend.models import (
    AgentRunRow,
    AgentThreadRow,
    CompanyRow,
    ProjectRow,
)
from enterprise_insight_backend.schemas import AgentRunStatus
from enterprise_insight_backend.service_utils import now_utc


def _database(tmp_path: Path) -> tuple[Database, Settings, UUID, UUID]:
    settings = Settings(
        data_dir=tmp_path,
        database_url=f"sqlite:///{(tmp_path / 'worker.db').as_posix()}",
        agent_worker_enabled=False,
    )
    database = Database(settings)
    database.create_schema()
    with database.session_factory() as session:
        company = CompanyRow(name="队列测试企业")
        session.add(company)
        session.flush()
        project = ProjectRow(company_id=company.id, name="队列测试项目")
        session.add(project)
        session.flush()
        thread = AgentThreadRow(
            project_id=project.id,
            agent_kind="MANAGEMENT",
            title="队列测试对话",
        )
        session.add(thread)
        session.flush()
        run = AgentRunRow(
            project_id=project.id,
            thread_id=thread.id,
            agent_kind="MANAGEMENT",
            status=AgentRunStatus.QUEUED.value,
            context_manifest={},
        )
        session.add(run)
        session.commit()
        return database, settings, UUID(project.id), UUID(run.id)


def test_worker_claim_is_atomic_and_does_not_double_claim(tmp_path: Path) -> None:
    database, settings, project_id, run_id = _database(tmp_path)
    first_worker = AgentWorker(database, settings)
    second_worker = AgentWorker(database, settings)

    assert first_worker._claim_next() == (project_id, run_id)
    assert second_worker._claim_next() is None

    with database.session_factory() as session:
        claimed = session.get(AgentRunRow, str(run_id))
        assert claimed.status == AgentRunStatus.RETRIEVING.value
        assert claimed.attempt_count == 1
        assert claimed.worker_id == first_worker.worker_id
        assert claimed.lease_expires_at is not None


def test_worker_reclaims_only_an_expired_lease(tmp_path: Path) -> None:
    database, settings, project_id, run_id = _database(tmp_path)
    first_worker = AgentWorker(database, settings)
    second_worker = AgentWorker(database, settings)
    assert first_worker._claim_next() == (project_id, run_id)
    assert second_worker._claim_next() is None

    with database.session_factory() as session:
        run = session.get(AgentRunRow, str(run_id))
        run.status = AgentRunStatus.PLANNING.value
        run.lease_expires_at = now_utc() - timedelta(seconds=1)
        session.commit()

    assert second_worker._claim_next() == (project_id, run_id)
    with database.session_factory() as session:
        reclaimed = session.get(AgentRunRow, str(run_id))
        assert reclaimed.worker_id == second_worker.worker_id
        assert reclaimed.attempt_count == 2


def test_worker_heartbeat_requires_the_current_unexpired_lease(tmp_path: Path) -> None:
    database, settings, project_id, run_id = _database(tmp_path)
    worker = AgentWorker(database, settings)
    assert worker._claim_next() == (project_id, run_id)

    assert worker._heartbeat(run_id) is True

    with database.session_factory() as session:
        run = session.get(AgentRunRow, str(run_id))
        run.lease_expires_at = now_utc() - timedelta(seconds=1)
        session.commit()

    assert worker._heartbeat(run_id) is False


@mark.parametrize(
    "terminal_status",
    [
        AgentRunStatus.COMPLETED.value,
        AgentRunStatus.WAITING_REVIEW.value,
        AgentRunStatus.BUDGET_EXHAUSTED.value,
    ],
)
def test_worker_allows_owned_terminal_status_flush(
    tmp_path: Path,
    monkeypatch: MonkeyPatch,
    terminal_status: str,
) -> None:
    database, settings, project_id, run_id = _database(tmp_path)
    worker = AgentWorker(database, settings)
    assert worker._claim_next() == (project_id, run_id)

    def finish(runtime: AgentRuntimeService, _: UUID, __: UUID) -> None:
        run = runtime.session.get(AgentRunRow, str(run_id))
        run.status = terminal_status
        runtime.session.flush()

    monkeypatch.setattr(AgentRuntimeService, "process_run", finish)
    worker._process(project_id, run_id)

    with database.session_factory() as session:
        completed = session.get(AgentRunRow, str(run_id))
        assert completed.status == terminal_status
        assert completed.worker_id is None
        assert completed.lease_expires_at is None


def test_worker_does_not_continue_or_commit_after_lease_fencing(
    tmp_path: Path,
    monkeypatch: MonkeyPatch,
) -> None:
    database, settings, project_id, run_id = _database(tmp_path)
    worker = AgentWorker(database, settings)
    replacement_worker = AgentWorker(database, settings)
    assert worker._claim_next() == (project_id, run_id)
    stale_token = worker.worker_id
    stages_started: list[str] = []

    def lose_lease(runtime: AgentRuntimeService, _: UUID, __: UUID) -> None:
        # Release the old session before the replacement worker takes the lease.
        runtime.session.rollback()
        with database.session_factory() as session:
            run = session.get(AgentRunRow, str(run_id))
            run.lease_expires_at = now_utc() - timedelta(seconds=1)
            session.commit()
        assert replacement_worker._claim_next() == (project_id, run_id)

        run = runtime.session.get(AgentRunRow, str(run_id))
        run.status = AgentRunStatus.COMPLETED.value
        run.context_manifest = {"stale": True}
        stages_started.append("result")
        runtime.session.flush()
        stages_started.append("next-stage")

    monkeypatch.setattr(AgentRuntimeService, "process_run", lose_lease)
    worker._process(project_id, run_id, stale_token)

    assert stages_started == ["result"]
    with database.session_factory() as session:
        fenced = session.get(AgentRunRow, str(run_id))
        assert fenced.status == AgentRunStatus.RETRIEVING.value
        assert fenced.worker_id == replacement_worker.worker_id
        assert fenced.context_manifest == {}


def test_worker_reclaim_rotates_fencing_token_for_same_worker(tmp_path: Path) -> None:
    database, settings, project_id, run_id = _database(tmp_path)
    worker = AgentWorker(database, settings)
    assert worker._claim_next() == (project_id, run_id)
    stale_token = worker.worker_id

    with database.session_factory() as session:
        run = session.get(AgentRunRow, str(run_id))
        run.lease_expires_at = now_utc() - timedelta(seconds=1)
        session.commit()

    assert worker._claim_next() == (project_id, run_id)
    assert worker.worker_id != stale_token

    worker._process(project_id, run_id, stale_token)

    with database.session_factory() as session:
        reclaimed = session.get(AgentRunRow, str(run_id))
        assert reclaimed.status == AgentRunStatus.RETRIEVING.value
        assert reclaimed.worker_id == worker.worker_id


def test_worker_records_infrastructure_failure_without_leaving_run_active(
    tmp_path: Path,
    monkeypatch: MonkeyPatch,
) -> None:
    database, settings, project_id, run_id = _database(tmp_path)
    worker = AgentWorker(database, settings)
    assert worker._claim_next() == (project_id, run_id)

    def fail(*_: object, **__: object) -> None:
        raise RuntimeError("synthetic worker failure")

    monkeypatch.setattr(AgentRuntimeService, "process_run", fail)
    worker._process(project_id, run_id)

    with database.session_factory() as session:
        failed = session.get(AgentRunRow, str(run_id))
        assert failed.status == AgentRunStatus.FAILED.value
        assert failed.error["code"] == "AGENT_WORKER_FAILED"
