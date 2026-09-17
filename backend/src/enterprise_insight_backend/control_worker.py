from __future__ import annotations

import asyncio
import logging
from contextlib import suppress
from datetime import UTC, datetime

from sqlalchemy import select

from enterprise_insight_backend.config import Settings
from enterprise_insight_backend.control import (
    ControlDatabase,
    ControlIndexService,
    ControlSchemaRow,
)
from enterprise_insight_backend.database import Database
from enterprise_insight_backend.models import AgentReadSetOutboxRow, AgentStepOutboxRow

logger = logging.getLogger(__name__)


class ControlIndexWorker:
    """Reconciles control indexes from immutable source-store evidence."""

    OUTBOX_BATCH_SIZE = 100

    def __init__(
        self,
        formal_database: Database,
        control_database: ControlDatabase,
        settings: Settings,
    ):
        self.formal_database = formal_database
        self.control_database = control_database
        self.settings = settings
        self.stop_event = asyncio.Event()
        self.task: asyncio.Task[None] | None = None

    async def start(self) -> None:
        await asyncio.to_thread(self.synchronize_once)
        self.task = asyncio.create_task(self._run(), name="enterprise-insight-control-index")

    async def stop(self) -> None:
        self.stop_event.set()
        if self.task is not None:
            self.task.cancel()
            with suppress(asyncio.CancelledError):
                await self.task

    def synchronize_once(self) -> int:
        inserted = self._backfill_step_indexes_once()
        inserted += self._backfill_query_manifests_once()
        return inserted + self._deliver_step_outbox() + self._deliver_query_manifest_outbox()

    def _backfill_step_indexes_once(self) -> int:
        with self.formal_database.session_factory() as source_session:
            with self.control_database.session_factory() as control_session:
                schema = control_session.get(ControlSchemaRow, 1)
                if schema is None or schema.step_index_backfill_completed:
                    return 0
                inserted = ControlIndexService(control_session).synchronize_routes(
                    source_session
                )
                schema.step_index_backfill_completed = True
                schema.updated_at = datetime.now(UTC)
                control_session.commit()
                return inserted

    def _backfill_query_manifests_once(self) -> int:
        with self.formal_database.session_factory() as source_session:
            with self.control_database.session_factory() as control_session:
                schema = control_session.get(ControlSchemaRow, 1)
                if schema is None or schema.query_manifest_backfill_completed:
                    return 0
                inserted = ControlIndexService(
                    control_session
                ).backfill_query_manifests(source_session)
                schema.query_manifest_backfill_completed = True
                schema.updated_at = datetime.now(UTC)
                control_session.commit()
                return inserted

    def _deliver_step_outbox(self) -> int:
        """At-least-once source-step delivery, acknowledged after control commit."""
        with self.formal_database.session_factory() as source_session:
            events = source_session.scalars(
                select(AgentStepOutboxRow)
                .where(AgentStepOutboxRow.delivered_at.is_(None))
                .order_by(AgentStepOutboxRow.created_at, AgentStepOutboxRow.event_id)
                .limit(self.OUTBOX_BATCH_SIZE)
            ).all()
            if not events:
                return 0
            event_ids = [item.event_id for item in events]
            try:
                with self.control_database.session_factory() as control_session:
                    inserted = ControlIndexService(
                        control_session
                    ).synchronize_step_outbox(source_session, events)
                    control_session.commit()
                delivered_at = datetime.now(UTC)
                for item in events:
                    item.delivered_at = delivered_at
                    item.attempt_count += 1
                    item.last_error_code = None
                source_session.commit()
                return inserted
            except Exception as exc:
                source_session.rollback()
                with self.formal_database.session_factory.begin() as retry_session:
                    pending = retry_session.scalars(
                        select(AgentStepOutboxRow).where(
                            AgentStepOutboxRow.event_id.in_(event_ids),
                            AgentStepOutboxRow.delivered_at.is_(None),
                        )
                    ).all()
                    for item in pending:
                        item.attempt_count += 1
                        item.last_error_code = type(exc).__name__[:80]
                raise

    def _deliver_query_manifest_outbox(self) -> int:
        """At-least-once cross-store delivery; control commit precedes source ack."""
        with self.formal_database.session_factory() as source_session:
            events = source_session.scalars(
                select(AgentReadSetOutboxRow)
                .where(AgentReadSetOutboxRow.delivered_at.is_(None))
                .order_by(AgentReadSetOutboxRow.created_at, AgentReadSetOutboxRow.event_id)
                .limit(self.OUTBOX_BATCH_SIZE)
            ).all()
            if not events:
                return 0
            event_ids = [item.event_id for item in events]
            try:
                with self.control_database.session_factory() as control_session:
                    inserted = ControlIndexService(
                        control_session
                    ).synchronize_query_manifest_outbox(source_session, events)
                    control_session.commit()
                delivered_at = datetime.now(UTC)
                for item in events:
                    item.delivered_at = delivered_at
                    item.attempt_count += 1
                    item.last_error_code = None
                source_session.commit()
                return inserted
            except Exception as exc:
                source_session.rollback()
                # The target may already have committed. Retrying is safe because
                # control indexes are keyed by stable run/ordinal-derived ids.
                with self.formal_database.session_factory.begin() as retry_session:
                    pending = retry_session.scalars(
                        select(AgentReadSetOutboxRow).where(
                            AgentReadSetOutboxRow.event_id.in_(event_ids),
                            AgentReadSetOutboxRow.delivered_at.is_(None),
                        )
                    ).all()
                    for item in pending:
                        item.attempt_count += 1
                        item.last_error_code = type(exc).__name__[:80]
                raise

    async def _run(self) -> None:
        while not self.stop_event.is_set():
            try:
                await asyncio.to_thread(self.synchronize_once)
            except Exception:
                # The source store remains authoritative; a later pass replays the
                # exact same route ids and hashes without duplicating index entries.
                logger.exception("Control index reconciliation failed")
            try:
                await asyncio.wait_for(
                    self.stop_event.wait(), timeout=self.settings.control_index_poll_seconds
                )
            except TimeoutError:
                continue
