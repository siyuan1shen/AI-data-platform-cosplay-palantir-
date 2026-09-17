from __future__ import annotations

import asyncio
import logging
from datetime import UTC, datetime, timedelta
from uuid import UUID

from sqlalchemy import and_, or_, select

from enterprise_insight_backend.actions import ActionService
from enterprise_insight_backend.config import Settings
from enterprise_insight_backend.database import Database
from enterprise_insight_backend.models import ActionDefinitionRow, ActionInvocationRow
from enterprise_insight_backend.schemas import ActionExecutionMode, ActionInvocationStatus

logger = logging.getLogger(__name__)
_RECOVERABLE_ACTION_KEYS = (
    "erpnext.task.create",
    "erpnext.task.update",
    "erpnext.task.cancel",
)


class ActionRecoveryWorker:
    """Reconciles stale external invocations by reading receipts only.

    This worker never calls ActionService.execute and therefore cannot replay an
    uncertain write.  It uses the invocation's durable operation id to ask the
    connector for its receipt, with a grace period and bounded batch size.
    """

    def __init__(self, database: Database, settings: Settings) -> None:
        self.database = database
        self.settings = settings
        self.stop_event = asyncio.Event()
        self.task: asyncio.Task[None] | None = None

    async def start(self) -> None:
        self.task = asyncio.create_task(self._run(), name="enterprise-insight-action-recovery")

    async def stop(self) -> None:
        self.stop_event.set()
        if self.task is not None:
            # Let an in-flight read-only receipt lookup finish before the app
            # disposes database engines used by its worker thread.
            await self.task

    def reconcile_once(self, *, now: datetime | None = None) -> int:
        cutoff = _aware(now or datetime.now(UTC)) - timedelta(
            seconds=self.settings.action_recovery_grace_seconds
        )
        with self.database.session_factory() as session:
            statement = (
                select(ActionInvocationRow.project_id, ActionInvocationRow.id)
                .join(
                    ActionDefinitionRow,
                    ActionDefinitionRow.id == ActionInvocationRow.action_definition_id,
                )
                .where(
                    ActionDefinitionRow.execution_mode == ActionExecutionMode.CONNECTOR.value,
                    ActionDefinitionRow.key.in_(_RECOVERABLE_ACTION_KEYS),
                    ActionInvocationRow.updated_at <= cutoff,
                    or_(
                        ActionInvocationRow.status == ActionInvocationStatus.OUTCOME_UNKNOWN.value,
                        and_(
                            ActionInvocationRow.status == ActionInvocationStatus.RUNNING.value,
                            ActionInvocationRow.started_at.is_not(None),
                            ActionInvocationRow.started_at <= cutoff,
                        ),
                    ),
                )
                .order_by(ActionInvocationRow.updated_at, ActionInvocationRow.id)
                .limit(self.settings.action_recovery_batch_size)
            )
            invocation_keys = list(session.execute(statement).all())

        attempted = 0
        for project_id, invocation_id in invocation_keys:
            try:
                with self.database.session_factory() as session:
                    ActionService(session, settings=self.settings).reconcile(
                        project_id=UUID(project_id),
                        invocation_id=UUID(invocation_id),
                    )
                    session.commit()
                attempted += 1
            except Exception as exc:
                logger.exception(
                    "Action receipt reconciliation failed project_id=%s invocation_id=%s",
                    project_id,
                    invocation_id,
                )
                error_code = getattr(exc, "code", type(exc).__name__)
                safe_error_code = (
                    error_code
                    if isinstance(error_code, str)
                    and error_code.replace("_", "").isalnum()
                    and error_code.upper() == error_code
                    else "UNEXPECTED"
                )
                try:
                    with self.database.session_factory() as session:
                        service = ActionService(session, settings=self.settings)
                        service.record_reconciliation_failure(
                            UUID(project_id), UUID(invocation_id), error_code=safe_error_code
                        )
                        session.commit()
                except Exception:
                    logger.exception(
                        "Could not persist reconciliation failure project_id=%s invocation_id=%s",
                        project_id,
                        invocation_id,
                    )
        return attempted

    async def _run(self) -> None:
        while not self.stop_event.is_set():
            try:
                await asyncio.to_thread(self.reconcile_once)
            except Exception:
                logger.exception("Action recovery scan failed")
            try:
                await asyncio.wait_for(
                    self.stop_event.wait(),
                    timeout=self.settings.action_recovery_poll_seconds,
                )
            except TimeoutError:
                continue


def _aware(value: datetime) -> datetime:
    if value.tzinfo is None:
        return value.replace(tzinfo=UTC)
    return value.astimezone(UTC)
