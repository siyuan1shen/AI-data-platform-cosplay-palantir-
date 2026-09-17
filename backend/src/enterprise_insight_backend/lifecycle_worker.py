from __future__ import annotations

import asyncio
import logging
from contextlib import suppress

from enterprise_insight_backend.config import Settings
from enterprise_insight_backend.database import Database
from enterprise_insight_backend.lifecycle import LifecycleService
from enterprise_insight_backend.observations import ObservationDatabase

logger = logging.getLogger(__name__)


class LifecycleWorker:
    """Periodically removes only resources whose retention rule has fired."""

    def __init__(
        self,
        database: Database,
        observation_database: ObservationDatabase,
        settings: Settings,
    ) -> None:
        self.database = database
        self.observation_database = observation_database
        self.settings = settings
        self.stop_event = asyncio.Event()
        self.task: asyncio.Task[None] | None = None

    async def start(self) -> None:
        await asyncio.to_thread(self.cleanup_once)
        self.task = asyncio.create_task(self._run(), name="enterprise-insight-lifecycle")

    async def stop(self) -> None:
        self.stop_event.set()
        if self.task is not None:
            self.task.cancel()
            with suppress(asyncio.CancelledError):
                await self.task

    def cleanup_once(self) -> int:
        # In the default single-file deployment both logical stores point to
        # the same SQLite database. Reusing one Session is essential here:
        # opening two write transactions for the same file can hold a lock
        # while the worker is trying to clean a preview. The service keeps the
        # two domain parameters so the isolated-file deployment uses the same
        # code path below.
        same_database = self.settings.unified_storage or (
            str(self.database.engine.url) == str(self.observation_database.engine.url)
        )
        if same_database:
            with self.database.session_factory() as session:
                service = LifecycleService(session, session, self.settings)
                result = service.cleanup_expired(actor_id="system:retention-worker")
                session.commit()
                return result
        with self.database.session_factory() as session:
            with self.observation_database.session_factory() as observation_session:
                service = LifecycleService(session, observation_session, self.settings)
                result = service.cleanup_expired(actor_id="system:retention-worker")
                # The observation and formal stores can be separate physical
                # SQLite files. Flush and commit the observation side first so
                # a shared SQLite file never holds two competing write locks.
                observation_session.flush()
                observation_session.commit()
                session.commit()
                return result

    async def _run(self) -> None:
        while not self.stop_event.is_set():
            try:
                deleted = await asyncio.to_thread(self.cleanup_once)
                if deleted:
                    logger.info("Lifecycle worker deleted %s temporary resources", deleted)
            except Exception:
                logger.exception("Lifecycle cleanup scan failed")
            try:
                await asyncio.wait_for(
                    self.stop_event.wait(),
                    timeout=self.settings.lifecycle_cleanup_interval_seconds,
                )
            except TimeoutError:
                continue
