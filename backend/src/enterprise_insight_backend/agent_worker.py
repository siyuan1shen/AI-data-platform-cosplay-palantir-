from __future__ import annotations

import asyncio
from contextlib import suppress
from datetime import timedelta
from threading import Event
from uuid import UUID, uuid4

from sqlalchemy import or_, select, update
from sqlalchemy.orm import Session

from enterprise_insight_backend.agent_runtime import AgentRuntimeService
from enterprise_insight_backend.config import Settings
from enterprise_insight_backend.database import Database
from enterprise_insight_backend.models import AgentRunRow
from enterprise_insight_backend.schemas import AgentRunStatus
from enterprise_insight_backend.service_utils import now_utc

ACTIVE_STATUSES = (
    AgentRunStatus.RETRIEVING.value,
    AgentRunStatus.PLANNING.value,
    AgentRunStatus.RUNNING_TOOLS.value,
    AgentRunStatus.PRODUCING_PROPOSAL.value,
    AgentRunStatus.VALIDATING.value,
)


class _LeaseLost(RuntimeError):
    """The worker can no longer safely write this run."""


class AgentWorker:
    def __init__(self, database: Database, settings: Settings) -> None:
        self.database = database
        self.settings = settings
        self.stop_event = asyncio.Event()
        self.task: asyncio.Task[None] | None = None
        self.worker_id = str(uuid4())

    async def start(self) -> None:
        await asyncio.to_thread(self._recover_interrupted)
        self.task = asyncio.create_task(self._run(), name="enterprise-insight-agent-worker")

    async def stop(self) -> None:
        self.stop_event.set()
        if self.task is not None:
            self.task.cancel()
            with suppress(asyncio.CancelledError):
                await self.task

    async def _run(self) -> None:
        while not self.stop_event.is_set():
            try:
                claimed = await asyncio.to_thread(self._claim_next)
                if claimed is not None:
                    project_id, run_id = claimed
                    lease_token = self.worker_id
                    lease_lost = Event()
                    process_task = asyncio.create_task(
                        asyncio.to_thread(
                            self._process,
                            project_id,
                            run_id,
                            lease_token,
                            lease_lost,
                        )
                    )
                    heartbeat_interval = max(
                        0.1, self.settings.agent_worker_lease_seconds / 3
                    )
                    while not process_task.done() and not self.stop_event.is_set():
                        done, _ = await asyncio.wait(
                            {process_task}, timeout=heartbeat_interval
                        )
                        if not done:
                            try:
                                heartbeat_ok = await asyncio.to_thread(
                                    self._heartbeat, run_id, lease_token
                                )
                            except Exception:  # pragma: no cover - infra failure
                                heartbeat_ok = False
                            if not heartbeat_ok:
                                lease_lost.set()
                                process_task.cancel()
                                break
                    if not process_task.done():
                        process_task.cancel()
                    with suppress(asyncio.CancelledError):
                        await process_task
                    continue
            except Exception:  # pragma: no cover - keeps the queue alive after infra faults
                pass
            try:
                await asyncio.wait_for(
                    self.stop_event.wait(), timeout=self.settings.agent_worker_poll_seconds
                )
            except TimeoutError:
                pass

    def _recover_interrupted(self) -> None:
        with self.database.session_factory() as session:
            self._requeue_expired(session)
            session.commit()

    def _claim_next(self) -> tuple[UUID, UUID] | None:
        with self.database.session_factory() as session:
            self._requeue_expired(session)
            timestamp = now_utc()
            lease_token = str(uuid4())
            candidate_id = (
                select(AgentRunRow)
                .where(AgentRunRow.status == AgentRunStatus.QUEUED.value)
                .order_by(AgentRunRow.created_at, AgentRunRow.id)
                .limit(1)
                .with_only_columns(AgentRunRow.id)
                .scalar_subquery()
            )
            claimed = session.execute(
                update(AgentRunRow)
                .where(
                    AgentRunRow.id == candidate_id,
                    AgentRunRow.status == AgentRunStatus.QUEUED.value,
                )
                .values(
                    status=AgentRunStatus.RETRIEVING.value,
                    worker_id=lease_token,
                    heartbeat_at=timestamp,
                    lease_expires_at=timestamp
                    + timedelta(seconds=self.settings.agent_worker_lease_seconds),
                    attempt_count=AgentRunRow.attempt_count + 1,
                )
                .returning(AgentRunRow.project_id, AgentRunRow.id)
            ).first()
            if claimed is None:
                return None
            session.commit()
            # worker_id doubles as the fencing token. It must change for every
            # claim so a stale thread from this same worker cannot regain authority
            # after the run is requeued and claimed again.
            self.worker_id = lease_token
            return UUID(claimed.project_id), UUID(claimed.id)

    def _process(
        self,
        project_id: UUID,
        run_id: UUID,
        lease_token: str | None = None,
        lease_lost: Event | None = None,
    ) -> None:
        lease_token = lease_token or self.worker_id
        lease_lost = lease_lost or Event()
        try:
            with self.database.session_factory() as session:
                run = session.get(AgentRunRow, str(run_id))
                if run is None or run.worker_id != lease_token:
                    return
                self._assert_lease(run_id, lease_token, lease_lost)
                self._install_lease_guards(session, run_id, lease_token, lease_lost)
                AgentRuntimeService(session, self.settings).process_run(project_id, run_id)
                session.refresh(run)
                if run.worker_id != lease_token:
                    session.rollback()
                    return
                session.commit()
                self._release_lease(run_id, lease_token)
        except _LeaseLost:
            # A stale attempt must never turn its in-memory result into a durable
            # result, nor overwrite the newer owner's state with a failure.
            return
        except Exception as exc:  # pragma: no cover - defensive infrastructure boundary
            self._record_worker_failure(run_id, lease_token, exc)

    def _release_lease(self, run_id: UUID, lease_token: str) -> None:
        """Release a completed run's lease without reopening its result transaction."""
        with self.database.session_factory() as session:
            session.execute(
                update(AgentRunRow)
                .where(
                    AgentRunRow.id == str(run_id),
                    AgentRunRow.worker_id == lease_token,
                )
                .values(worker_id=None, lease_expires_at=None)
            )
            session.commit()

    def _heartbeat(self, run_id: UUID, lease_token: str | None = None) -> bool:
        lease_token = lease_token or self.worker_id
        timestamp = now_utc()
        with self.database.session_factory() as session:
            result = session.execute(
                update(AgentRunRow)
                .where(
                    AgentRunRow.id == str(run_id),
                    AgentRunRow.worker_id == lease_token,
                    AgentRunRow.status.in_(ACTIVE_STATUSES),
                    AgentRunRow.lease_expires_at > timestamp,
                )
                .values(
                    heartbeat_at=timestamp,
                    lease_expires_at=timestamp
                    + timedelta(seconds=self.settings.agent_worker_lease_seconds),
                )
            )
            if getattr(result, "rowcount", 0) != 1:
                session.rollback()
                return False
            session.commit()
            return True

    def _assert_lease(self, run_id: UUID, lease_token: str, lease_lost: Event) -> None:
        if lease_lost.is_set():
            raise _LeaseLost
        timestamp = now_utc()
        with self.database.session_factory() as session:
            owned = session.scalar(
                select(AgentRunRow.id).where(
                    AgentRunRow.id == str(run_id),
                    AgentRunRow.worker_id == lease_token,
                    AgentRunRow.lease_expires_at > timestamp,
                )
            )
        if owned is None:
            raise _LeaseLost

    def _renew_lease_for_commit(self, session: Session, run_id: UUID, lease_token: str) -> None:
        """Fence and renew in the same transaction immediately before commit."""
        timestamp = now_utc()
        try:
            # Avoid autoflush here: first fence the database row while it still
            # carries this token, then let the guarded commit flush the terminal
            # status. The lease is released in a separate conditional transaction.
            with session.no_autoflush:
                owned = session.scalar(
                    select(AgentRunRow.id).where(
                        AgentRunRow.id == str(run_id),
                        AgentRunRow.worker_id == lease_token,
                        AgentRunRow.lease_expires_at > timestamp,
                    )
                )
                if owned is None:
                    session.rollback()
                    raise _LeaseLost
                session.execute(
                    update(AgentRunRow)
                    .execution_options(synchronize_session=False)
                    .where(
                        AgentRunRow.id == str(run_id),
                        AgentRunRow.worker_id == lease_token,
                        AgentRunRow.lease_expires_at > timestamp,
                    )
                    .values(
                        heartbeat_at=timestamp,
                        lease_expires_at=timestamp
                        + timedelta(seconds=self.settings.agent_worker_lease_seconds),
                    )
                )
        except _LeaseLost:
            raise
        except Exception as exc:
            session.rollback()
            raise _LeaseLost from exc

    def _install_lease_guards(
        self,
        session: Session,
        run_id: UUID,
        lease_token: str,
        lease_lost: Event,
    ) -> None:
        """Guard runtime transaction boundaries without changing the runtime service."""
        original_commit = session.commit
        original_flush = session.flush
        original_refresh = session.refresh

        def guarded_commit() -> None:
            if lease_lost.is_set():
                session.rollback()
                raise _LeaseLost
            self._renew_lease_for_commit(session, run_id, lease_token)
            original_commit()

        def guarded_flush(*args: object, **kwargs: object) -> None:
            self._assert_lease(run_id, lease_token, lease_lost)
            original_flush(*args, **kwargs)  # type: ignore[arg-type]

        def guarded_refresh(*args: object, **kwargs: object) -> None:
            self._assert_lease(run_id, lease_token, lease_lost)
            original_refresh(*args, **kwargs)  # type: ignore[arg-type]

        session.commit = guarded_commit  # type: ignore[method-assign]
        session.flush = guarded_flush  # type: ignore[method-assign]
        session.refresh = guarded_refresh  # type: ignore[method-assign]

    def _record_worker_failure(self, run_id: UUID, lease_token: str, exc: Exception) -> None:
        timestamp = now_utc()
        with self.database.session_factory() as session:
            result = session.execute(
                update(AgentRunRow)
                .where(
                    AgentRunRow.id == str(run_id),
                    AgentRunRow.worker_id == lease_token,
                    AgentRunRow.status.in_(ACTIVE_STATUSES),
                    AgentRunRow.lease_expires_at > timestamp,
                )
                .values(
                    status=AgentRunStatus.FAILED.value,
                    worker_id=None,
                    lease_expires_at=None,
                    error={
                        "code": "AGENT_WORKER_FAILED",
                        "message": "后台任务执行失败，队列将继续处理其他任务。",
                        "details": [{"exception": type(exc).__name__}],
                    },
                )
            )
            if getattr(result, "rowcount", 0) == 1:
                session.commit()

    @staticmethod
    def _requeue_expired(session: Session) -> None:
        timestamp = now_utc()
        session.execute(
            update(AgentRunRow)
            .where(
                AgentRunRow.status.in_(ACTIVE_STATUSES),
                or_(
                    AgentRunRow.lease_expires_at.is_(None),
                    AgentRunRow.lease_expires_at < timestamp,
                ),
            )
            .values(
                status=AgentRunStatus.QUEUED.value,
                worker_id=None,
                lease_expires_at=None,
            )
        )
