from __future__ import annotations

import hashlib
import json
from datetime import UTC, datetime
from typing import Any
from uuid import UUID, uuid4

from sqlalchemy import func, select
from sqlalchemy.exc import IntegrityError
from sqlalchemy.orm import Session
from sqlalchemy.orm.exc import StaleDataError

from enterprise_insight_backend.errors import DomainError
from enterprise_insight_backend.models import ManagementActionEventRow, ManagementActionRow
from enterprise_insight_backend.portfolio import PortfolioService
from enterprise_insight_backend.schemas import (
    ManagementActionCreate,
    ManagementActionEventCreate,
    ManagementActionEventType,
    ManagementActionEventView,
    ManagementActionRevisionRequest,
    ManagementActionUpdate,
    ManagementActionVerifyDone,
    ManagementActionView,
    Page,
)
from enterprise_insight_backend.service_utils import json_ready, require_revision


class ManagementActionService:
    """Project-scoped human management actions and their immutable event history."""

    def __init__(self, session: Session) -> None:
        self.session = session
        self.portfolio = PortfolioService(session)

    def create(
        self, project_id: UUID, payload: ManagementActionCreate, *, actor_id: str
    ) -> ManagementActionView:
        project = self.portfolio.require_project(project_id)
        actor = _required_actor(actor_id)
        request_hash = _create_request_hash(payload)
        if payload.idempotency_key:
            existing = self.session.scalar(
                select(ManagementActionRow).where(
                    ManagementActionRow.project_id == str(project_id),
                    ManagementActionRow.idempotency_key == payload.idempotency_key,
                )
            )
            if existing is not None:
                if existing.idempotency_hash == request_hash:
                    return self._view(existing)
                raise DomainError(
                    "MANAGEMENT_ACTION_IDEMPOTENCY_CONFLICT",
                    "该提交标识已用于不同的管理行动内容。",
                    status_code=409,
                )
        now = _now()
        row = ManagementActionRow(
            id=str(uuid4()),
            company_id=project.company_id,
            project_id=str(project_id),
            idempotency_key=payload.idempotency_key,
            idempotency_hash=request_hash if payload.idempotency_key else None,
            title=payload.title,
            description=payload.description,
            owner=payload.owner,
            priority=payload.priority.value,
            status="OPEN",
            due_at=_utc(payload.due_at),
            revision=1,
            created_by=actor,
            created_at=now,
            updated_at=now,
        )
        self.session.add(row)
        try:
            self.session.flush()
        except IntegrityError as exc:
            if not payload.idempotency_key:
                raise
            self.session.rollback()
            existing = self.session.scalar(
                select(ManagementActionRow).where(
                    ManagementActionRow.project_id == str(project_id),
                    ManagementActionRow.idempotency_key == payload.idempotency_key,
                )
            )
            if existing is not None and existing.idempotency_hash == request_hash:
                return self._view(existing)
            if existing is not None:
                raise DomainError(
                    "MANAGEMENT_ACTION_IDEMPOTENCY_CONFLICT",
                    "该提交标识已用于不同的管理行动内容。",
                    status_code=409,
                ) from exc
            raise
        self._append_event(
            row,
            event_type=ManagementActionEventType.CREATED,
            actor_id=actor,
            reason=payload.reason,
            details={
                "title": row.title,
                "description": row.description,
                "owner": row.owner,
                "priority": row.priority,
                "status": row.status,
                "due_at": row.due_at,
            },
            at=now,
            bump_revision=False,
        )
        return self._view(row)

    def list(
        self,
        project_id: UUID,
        *,
        include_cancelled: bool = True,
        offset: int = 0,
        limit: int = 50,
    ) -> Page[ManagementActionView]:
        _validate_pagination(offset=offset, limit=limit)
        self.portfolio.require_project(project_id)
        statement = select(ManagementActionRow).where(
            ManagementActionRow.project_id == str(project_id)
        )
        if not include_cancelled:
            statement = statement.where(ManagementActionRow.status != "CANCELLED")
        total = self.session.scalar(select(func.count()).select_from(statement.subquery())) or 0
        rows = self.session.scalars(
            statement.order_by(ManagementActionRow.created_at.desc(), ManagementActionRow.id)
            .offset(offset)
            .limit(limit)
        ).all()
        return Page[ManagementActionView](items=[self._view(row) for row in rows], total=total)

    def get(self, project_id: UUID, action_id: UUID) -> ManagementActionView:
        return self._view(self._require_action(project_id, action_id))

    def update(
        self,
        project_id: UUID,
        action_id: UUID,
        payload: ManagementActionUpdate,
        *,
        actor_id: str,
    ) -> ManagementActionView:
        row = self._require_action(project_id, action_id)
        self._check_revision(row, payload.expected_revision)
        if row.status == "CANCELLED":
            raise DomainError(
                "MANAGEMENT_ACTION_CANCELLED",
                "已取消的管理行动不能修改。",
                status_code=409,
            )

        changes = payload.model_dump(
            exclude_unset=True, exclude={"expected_revision", "reason"}, mode="python"
        )
        previous_status = row.status
        for field_name, value in changes.items():
            setattr(row, field_name, value.value if hasattr(value, "value") else value)
        self._append_event(
            row,
            event_type=ManagementActionEventType.UPDATED,
            actor_id=actor_id,
            reason=payload.reason,
            details={"changes": json_ready(changes)},
            from_status=previous_status if row.status != previous_status else None,
            to_status=row.status if row.status != previous_status else None,
        )
        return self._view(row)

    def cancel(
        self,
        project_id: UUID,
        action_id: UUID,
        payload: ManagementActionRevisionRequest,
        *,
        actor_id: str,
    ) -> ManagementActionView:
        row = self._require_action(project_id, action_id)
        self._check_revision(row, payload.expected_revision)
        if row.status == "CANCELLED":
            return self._view(row)
        if row.reported_done_at is not None:
            raise DomainError(
                "MANAGEMENT_ACTION_ALREADY_REPORTED_DONE",
                "已报告完成的管理行动需先核实，不能直接取消。",
                status_code=409,
            )
        previous_status = row.status
        row.status = "CANCELLED"
        self._append_event(
            row,
            event_type=ManagementActionEventType.CANCELLED,
            actor_id=actor_id,
            reason=payload.reason,
            from_status=previous_status,
            to_status=row.status,
        )
        return self._view(row)

    def append_progress(
        self,
        project_id: UUID,
        action_id: UUID,
        payload: ManagementActionEventCreate,
        *,
        actor_id: str,
    ) -> ManagementActionEventView:
        return self._append_message_event(
            project_id,
            action_id,
            payload,
            actor_id=actor_id,
            event_type=ManagementActionEventType.PROGRESS,
        )

    def append_outcome(
        self,
        project_id: UUID,
        action_id: UUID,
        payload: ManagementActionEventCreate,
        *,
        actor_id: str,
    ) -> ManagementActionEventView:
        return self._append_message_event(
            project_id,
            action_id,
            payload,
            actor_id=actor_id,
            event_type=ManagementActionEventType.OUTCOME,
        )

    def report_done(
        self,
        project_id: UUID,
        action_id: UUID,
        payload: ManagementActionEventCreate,
        *,
        actor_id: str,
    ) -> ManagementActionView:
        row = self._require_action(project_id, action_id)
        self._check_revision(row, payload.expected_revision)
        if row.status == "CANCELLED":
            raise DomainError(
                "MANAGEMENT_ACTION_CANCELLED",
                "已取消的管理行动不能报告完成。",
                status_code=409,
            )
        if row.reported_done_at is not None:
            raise DomainError(
                "MANAGEMENT_ACTION_ALREADY_REPORTED_DONE",
                "该管理行动已报告完成。",
                status_code=409,
            )
        actor = _required_actor(actor_id)
        row.reported_done_at = _now()
        row.reported_done_by = actor
        self._append_event(
            row,
            event_type=ManagementActionEventType.DONE_REPORTED,
            actor_id=actor,
            reason=payload.reason,
            message=payload.message,
            details=payload.details,
        )
        return self._view(row)

    def verify_done(
        self,
        project_id: UUID,
        action_id: UUID,
        payload: ManagementActionVerifyDone,
        *,
        actor_id: str,
    ) -> ManagementActionView:
        row = self._require_action(project_id, action_id)
        self._check_revision(row, payload.expected_revision)
        if row.reported_done_at is None:
            raise DomainError(
                "MANAGEMENT_ACTION_NOT_REPORTED_DONE",
                "只能核实已由执行者报告完成的管理行动。",
                status_code=409,
            )
        if row.verified_done_at is not None:
            raise DomainError(
                "MANAGEMENT_ACTION_ALREADY_VERIFIED",
                "该管理行动已经核实完成。",
                status_code=409,
            )
        actor = _required_actor(actor_id)
        row.verified_done_at = _now()
        row.verified_done_by = actor
        self._append_event(
            row,
            event_type=ManagementActionEventType.DONE_VERIFIED,
            actor_id=actor,
            reason=payload.reason,
            message=payload.verification_note,
        )
        return self._view(row)

    def history(
        self, project_id: UUID, action_id: UUID, *, offset: int = 0, limit: int = 50
    ) -> Page[ManagementActionEventView]:
        _validate_pagination(offset=offset, limit=limit)
        self._require_action(project_id, action_id)
        statement = select(ManagementActionEventRow).where(
            ManagementActionEventRow.project_id == str(project_id),
            ManagementActionEventRow.action_id == str(action_id),
        )
        total = self.session.scalar(select(func.count()).select_from(statement.subquery())) or 0
        rows = self.session.scalars(
            statement.order_by(ManagementActionEventRow.revision).offset(offset).limit(limit)
        ).all()
        return Page[ManagementActionEventView](
            items=[ManagementActionEventView.model_validate(row) for row in rows],
            total=total,
        )

    def _append_message_event(
        self,
        project_id: UUID,
        action_id: UUID,
        payload: ManagementActionEventCreate,
        *,
        actor_id: str,
        event_type: ManagementActionEventType,
    ) -> ManagementActionEventView:
        row = self._require_action(project_id, action_id)
        self._check_revision(row, payload.expected_revision)
        if row.status == "CANCELLED":
            raise DomainError(
                "MANAGEMENT_ACTION_CANCELLED",
                "已取消的管理行动不能追加进展或结果。",
                status_code=409,
            )
        event = self._append_event(
            row,
            event_type=event_type,
            actor_id=actor_id,
            reason=payload.reason,
            message=payload.message,
            details=payload.details,
        )
        return ManagementActionEventView.model_validate(event)

    def _require_action(self, project_id: UUID, action_id: UUID) -> ManagementActionRow:
        self.portfolio.require_project(project_id)
        row = self.session.get(ManagementActionRow, str(action_id))
        if row is None or row.project_id != str(project_id):
            raise DomainError("MANAGEMENT_ACTION_NOT_FOUND", "管理行动不存在。", status_code=404)
        return row

    @staticmethod
    def _check_revision(row: ManagementActionRow, expected_revision: int) -> None:
        require_revision(row.revision, expected_revision, resource="管理行动")

    def _append_event(
        self,
        row: ManagementActionRow,
        *,
        event_type: ManagementActionEventType,
        actor_id: str,
        reason: str | None = None,
        message: str | None = None,
        details: dict[str, Any] | None = None,
        from_status: str | None = None,
        to_status: str | None = None,
        at: datetime | None = None,
        bump_revision: bool = True,
    ) -> ManagementActionEventRow:
        actor = _required_actor(actor_id)
        created_at = at or _now()
        if bump_revision:
            row.revision += 1
            row.updated_at = created_at
        try:
            self.session.flush()
        except StaleDataError as exc:
            raise DomainError(
                "REVISION_CONFLICT",
                "管理行动已被其他操作修改，请刷新后重试。",
                status_code=409,
            ) from exc

        event = ManagementActionEventRow(
            id=str(uuid4()),
            company_id=row.company_id,
            project_id=row.project_id,
            action_id=row.id,
            revision=row.revision,
            event_type=event_type.value,
            message=message,
            details=json_ready(details or {}),
            from_status=from_status,
            to_status=to_status,
            actor_id=actor,
            reason=reason,
            created_at=created_at,
        )
        self.session.add(event)
        self.session.flush()
        return event

    @staticmethod
    def _view(row: ManagementActionRow) -> ManagementActionView:
        return ManagementActionView.model_validate(row)


def _required_actor(actor_id: str) -> str:
    actor = actor_id.strip()
    if not actor:
        raise DomainError("ACTOR_REQUIRED", "必须记录执行操作的人员。", status_code=422)
    if len(actor) > 128:
        raise DomainError("ACTOR_INVALID", "人员标识不能超过 128 个字符。", status_code=422)
    return actor


def _now() -> datetime:
    return datetime.now(UTC)


def _create_request_hash(payload: ManagementActionCreate) -> str:
    content = json.dumps(
        json_ready(payload.model_dump(exclude={"idempotency_key"}, mode="python")),
        ensure_ascii=False,
        sort_keys=True,
        separators=(",", ":"),
    )
    return hashlib.sha256(content.encode("utf-8")).hexdigest()


def _utc(value: datetime | None) -> datetime | None:
    if value is None:
        return None
    if value.tzinfo is None:
        return value.replace(tzinfo=UTC)
    return value.astimezone(UTC)


def _validate_pagination(*, offset: int, limit: int) -> None:
    if offset < 0 or limit < 1 or limit > 100:
        raise DomainError(
            "INVALID_PAGINATION",
            "分页参数无效：offset 不能为负数，limit 必须在 1 到 100 之间。",
            status_code=422,
        )
