"""Bounded data-lifecycle operations for temporary platform resources.

The formal enterprise model and verified semantic data are intentionally not
part of this service.  Only previews, generated export files, and temporary
restore packages are eligible for automatic removal.  Work-observation
previews are also temporary; confirmed events and analyses are retained until
an explicit domain-level retirement workflow is added.
"""

from __future__ import annotations

from datetime import UTC, datetime, timedelta
from pathlib import Path
from uuid import UUID, uuid4

from sqlalchemy import or_, select
from sqlalchemy.orm import Session

from enterprise_insight_backend.config import Settings
from enterprise_insight_backend.errors import DomainError
from enterprise_insight_backend.models import (
    ExportJobRow,
    ImportPreviewRow,
    LifecycleDeletionAuditRow,
    ProjectRow,
    RestorePreviewRow,
)
from enterprise_insight_backend.schemas import (
    LifecycleCandidateView,
    LifecycleCleanupRequest,
    LifecycleCleanupResult,
    LifecycleDeleteRequest,
    LifecyclePolicyView,
    LifecyclePreviewView,
    LifecycleResourceKind,
)
from enterprise_insight_backend.service_utils import now_utc
from enterprise_insight_backend.work_observation import WorkObservationImportPreviewRow

_PROJECT_KINDS = (
    LifecycleResourceKind.IMPORT_PREVIEW,
    LifecycleResourceKind.WORK_OBSERVATION_PREVIEW,
    LifecycleResourceKind.EXPORT_FILE,
)
_ALL_KINDS = (*_PROJECT_KINDS, LifecycleResourceKind.RESTORE_PREVIEW)
_TERMINAL_EXPORT_STATUSES = ("COMPLETED", "FAILED")


class LifecycleService:
    def __init__(
        self,
        session: Session,
        observation_session: Session,
        settings: Settings,
    ) -> None:
        self.session = session
        self.observation_session = observation_session
        self.settings = settings

    def policies(self) -> list[LifecyclePolicyView]:
        return [
            LifecyclePolicyView(
                resource_kind=LifecycleResourceKind.IMPORT_PREVIEW,
                automatic=True,
                retention_description="导入预览使用自身 expires_at，到期后自动清理。",
                deletion_scope="只删除未确认的临时预览内容，不删除已导入材料。",
            ),
            LifecyclePolicyView(
                resource_kind=LifecycleResourceKind.WORK_OBSERVATION_PREVIEW,
                automatic=True,
                retention_description=(
                    "未确认工作观察预览保留 "
                    f"{self.settings.lifecycle_work_observation_preview_days} 天。"
                ),
                deletion_scope="只删除临时预览，不删除已确认的工作观察事件。",
            ),
            LifecyclePolicyView(
                resource_kind=LifecycleResourceKind.RESTORE_PREVIEW,
                automatic=True,
                retention_description="恢复预览到期后自动清理临时 ZIP 包。",
                deletion_scope="只删除恢复临时包，不触碰已经恢复的企业数据。",
            ),
            LifecyclePolicyView(
                resource_kind=LifecycleResourceKind.EXPORT_FILE,
                automatic=True,
                retention_description=(
                    "已完成或失败的导出保留 "
                    f"{self.settings.lifecycle_export_days} 天。"
                ),
                deletion_scope="同时删除导出记录和对应生成文件。",
            ),
        ]

    def preview(
        self,
        project_id: UUID | None = None,
        *,
        resource_kinds: list[LifecycleResourceKind] | None = None,
        now: datetime | None = None,
    ) -> LifecyclePreviewView:
        selected = tuple(resource_kinds or (_PROJECT_KINDS if project_id else _ALL_KINDS))
        unknown = [kind for kind in selected if kind not in _ALL_KINDS]
        if unknown:
            raise DomainError(
                "LIFECYCLE_KIND_UNSUPPORTED", "不支持的生命周期资源类型。", status_code=422
            )
        current = _aware(now or now_utc())
        candidates: list[LifecycleCandidateView] = []
        project_key = str(project_id) if project_id else None
        if project_id is not None:
            self._require_project(project_id)
        if LifecycleResourceKind.IMPORT_PREVIEW in selected:
            statement = select(ImportPreviewRow).where(ImportPreviewRow.expires_at <= current)
            if project_key:
                statement = statement.where(ImportPreviewRow.project_id == project_key)
            for row in self.session.scalars(statement.order_by(ImportPreviewRow.expires_at)).all():
                candidates.append(
                    _candidate(
                        LifecycleResourceKind.IMPORT_PREVIEW,
                        row.id,
                        row.project_id,
                        "CONSUMED" if row.consumed_at is not None else "READY",
                        row.expires_at,
                        row.expires_at,
                        "导入预览已到期。",
                    )
                )
        if LifecycleResourceKind.WORK_OBSERVATION_PREVIEW in selected:
            cutoff = current - timedelta(
                days=self.settings.lifecycle_work_observation_preview_days
            )
            statement = select(WorkObservationImportPreviewRow).where(
                WorkObservationImportPreviewRow.created_at <= cutoff,
                WorkObservationImportPreviewRow.status != "CONFIRMED",
            )
            if project_key:
                statement = statement.where(
                    WorkObservationImportPreviewRow.project_id == project_key
                )
            for row in self.observation_session.scalars(
                statement.order_by(WorkObservationImportPreviewRow.created_at)
            ).all():
                candidates.append(
                    _candidate(
                        LifecycleResourceKind.WORK_OBSERVATION_PREVIEW,
                        row.id,
                        row.project_id,
                        row.status,
                        row.created_at,
                        None,
                        "工作观察预览未确认且已超过临时保留期。",
                    )
                )
        if LifecycleResourceKind.EXPORT_FILE in selected:
            cutoff = current - timedelta(days=self.settings.lifecycle_export_days)
            statement = select(ExportJobRow).where(
                ExportJobRow.status.in_(_TERMINAL_EXPORT_STATUSES),
                ExportJobRow.updated_at <= cutoff,
            )
            if project_key:
                statement = statement.where(ExportJobRow.project_id == project_key)
            for row in self.session.scalars(statement.order_by(ExportJobRow.updated_at)).all():
                candidates.append(
                    _candidate(
                        LifecycleResourceKind.EXPORT_FILE,
                        row.id,
                        row.project_id,
                        row.status,
                        row.created_at,
                        row.updated_at,
                        "导出任务已经结束且超过文件保留期。",
                    )
                )
        if LifecycleResourceKind.RESTORE_PREVIEW in selected:
            statement = select(RestorePreviewRow).where(
                or_(
                    RestorePreviewRow.expires_at <= current,
                    RestorePreviewRow.consumed_at.is_not(None),
                )
            )
            for row in self.session.scalars(statement.order_by(RestorePreviewRow.created_at)).all():
                candidates.append(
                    _candidate(
                        LifecycleResourceKind.RESTORE_PREVIEW,
                        row.id,
                        None,
                        row.status,
                        row.created_at,
                        row.expires_at,
                        "恢复预览已到期或已经消费。",
                    )
                )
        candidates.sort(
            key=lambda item: (
                _aware(item.created_at),
                item.resource_kind.value,
                str(item.resource_id),
            )
        )
        return LifecyclePreviewView(
            generated_at=current,
            policies=self.policies(),
            candidates=candidates,
            total=len(candidates),
        )

    def cleanup(
        self,
        project_id: UUID | None,
        payload: LifecycleCleanupRequest,
        *,
        actor_id: str,
        now: datetime | None = None,
    ) -> LifecycleCleanupResult:
        selected_kinds = tuple(
            payload.resource_kinds or (_PROJECT_KINDS if project_id else _ALL_KINDS)
        )
        preview = self.preview(project_id, resource_kinds=list(selected_kinds), now=now)
        wanted = {str(item) for item in payload.candidate_ids}
        candidates = [
            item
            for item in preview.candidates
            if not wanted or str(item.resource_id) in wanted
        ]
        if wanted:
            found = {str(item.resource_id) for item in candidates}
            missing = sorted(wanted - found)
            if missing:
                raise DomainError(
                    "LIFECYCLE_CANDIDATE_NOT_ELIGIBLE",
                    "部分候选已不存在、未到期或不属于当前项目。",
                    status_code=409,
                    details=[{"resource_ids": missing}],
                )
        return self._execute_candidates(candidates, payload.reason, actor_id, "MANUAL")

    def delete_one(
        self,
        project_id: UUID,
        resource_kind: LifecycleResourceKind,
        resource_id: UUID,
        payload: LifecycleDeleteRequest,
        *,
        actor_id: str,
    ) -> LifecycleCleanupResult:
        if resource_kind not in _PROJECT_KINDS:
            raise DomainError(
                "LIFECYCLE_MANUAL_DELETE_UNSUPPORTED",
                "该资源不支持项目级主动删除。",
                status_code=422,
            )
        self._require_project(project_id)
        project_key = str(project_id)
        if resource_kind is LifecycleResourceKind.IMPORT_PREVIEW:
            row = self.session.get(ImportPreviewRow, str(resource_id))
            if row is None or row.project_id != project_key:
                raise DomainError(
                    "LIFECYCLE_RESOURCE_NOT_FOUND", "生命周期资源不存在。", status_code=404
                )
            candidate = _candidate(
                resource_kind,
                row.id,
                row.project_id,
                row.status,
                row.expires_at,
                row.expires_at,
                "按管理者要求主动删除导入预览。",
            )
        elif resource_kind is LifecycleResourceKind.WORK_OBSERVATION_PREVIEW:
            row = self.observation_session.get(WorkObservationImportPreviewRow, str(resource_id))
            if row is None or row.project_id != project_key:
                raise DomainError(
                    "LIFECYCLE_RESOURCE_NOT_FOUND", "生命周期资源不存在。", status_code=404
                )
            candidate = _candidate(
                resource_kind,
                row.id,
                row.project_id,
                row.status,
                row.created_at,
                None,
                "按管理者要求主动删除工作观察预览。",
            )
        else:
            row = self.session.get(ExportJobRow, str(resource_id))
            if row is None or row.project_id != project_key:
                raise DomainError(
                    "LIFECYCLE_RESOURCE_NOT_FOUND", "生命周期资源不存在。", status_code=404
                )
            candidate = _candidate(
                resource_kind,
                row.id,
                row.project_id,
                row.status,
                row.created_at,
                row.updated_at,
                "按管理者要求主动删除导出文件。",
            )
        return self._execute_candidates([candidate], payload.reason, actor_id, "MANUAL")

    def cleanup_expired(self, *, actor_id: str, now: datetime | None = None) -> int:
        preview = self.preview(None, now=now)
        result = self._execute_candidates(
            preview.candidates,
            "临时资源已达到生命周期清理条件。",
            actor_id,
            "AUTO",
        )
        return result.deleted

    def _execute_candidates(
        self,
        candidates: list[LifecycleCandidateView],
        reason: str,
        actor_id: str,
        deletion_mode: str,
    ) -> LifecycleCleanupResult:
        run_id = uuid4()
        deleted_by_kind: dict[str, int] = {}
        errors: list[str] = []
        deleted = 0
        for candidate in candidates:
            try:
                self._delete(candidate)
            except Exception as exc:
                errors.append(
                    f"{candidate.resource_kind.value}/{candidate.resource_id}: "
                    f"{type(exc).__name__}"
                )
                continue
            self.session.add(
                LifecycleDeletionAuditRow(
                    project_id=(str(candidate.project_id) if candidate.project_id else None),
                    resource_kind=candidate.resource_kind.value,
                    resource_id=str(candidate.resource_id),
                    deletion_mode=deletion_mode,
                    actor_id=(actor_id or "unknown")[:128],
                    reason=reason,
                    details={
                        "run_id": str(run_id),
                        "eligibility_reason": candidate.reason,
                        "status_before_delete": candidate.status,
                    },
                    created_at=now_utc(),
                )
            )
            deleted += 1
            key = candidate.resource_kind.value
            deleted_by_kind[key] = deleted_by_kind.get(key, 0) + 1
        return LifecycleCleanupResult(
            run_id=run_id,
            deletion_mode=deletion_mode,
            selected=len(candidates),
            deleted=deleted,
            failed=len(errors),
            deleted_by_kind=deleted_by_kind,
            errors=errors,
            completed_at=now_utc(),
        )

    def _delete(self, candidate: LifecycleCandidateView) -> None:
        resource_id = str(candidate.resource_id)
        if candidate.resource_kind is LifecycleResourceKind.IMPORT_PREVIEW:
            row = self.session.get(ImportPreviewRow, resource_id)
            if row is None or (
                candidate.project_id and row.project_id != str(candidate.project_id)
            ):
                raise DomainError(
                    "LIFECYCLE_RESOURCE_NOT_FOUND", "生命周期资源不存在。", status_code=404
                )
            self.session.delete(row)
            return
        if candidate.resource_kind is LifecycleResourceKind.WORK_OBSERVATION_PREVIEW:
            row = self.observation_session.get(WorkObservationImportPreviewRow, resource_id)
            if row is None or (
                candidate.project_id and row.project_id != str(candidate.project_id)
            ):
                raise DomainError(
                    "LIFECYCLE_RESOURCE_NOT_FOUND", "生命周期资源不存在。", status_code=404
                )
            self.observation_session.delete(row)
            return
        if candidate.resource_kind is LifecycleResourceKind.EXPORT_FILE:
            row = self.session.get(ExportJobRow, resource_id)
            if row is None or (
                candidate.project_id and row.project_id != str(candidate.project_id)
            ):
                raise DomainError(
                    "LIFECYCLE_RESOURCE_NOT_FOUND", "生命周期资源不存在。", status_code=404
                )
            self._export_path(row).unlink(missing_ok=True)
            self.session.delete(row)
            return
        row = self.session.get(RestorePreviewRow, resource_id)
        if row is None:
            raise DomainError(
                "LIFECYCLE_RESOURCE_NOT_FOUND", "生命周期资源不存在。", status_code=404
            )
        package_path = Path(row.package_path).resolve()
        base = (self.settings.data_dir / "restore-previews").resolve()
        if package_path.is_relative_to(base):
            package_path.unlink(missing_ok=True)
        self.session.delete(row)

    def _export_path(self, row: ExportJobRow) -> Path:
        extension = "zip" if row.format == "bundle" else row.format
        return self.settings.data_dir / "exports" / f"{row.project_id}-{row.id}.{extension}"

    def _require_project(self, project_id: UUID) -> ProjectRow:
        row = self.session.get(ProjectRow, str(project_id))
        if row is None:
            raise DomainError("PROJECT_NOT_FOUND", "项目不存在。", status_code=404)
        return row


def _candidate(
    kind: LifecycleResourceKind,
    resource_id: str,
    project_id: str | None,
    status: str,
    created_at: datetime,
    expires_at: datetime | None,
    reason: str,
) -> LifecycleCandidateView:
    return LifecycleCandidateView(
        resource_kind=kind,
        resource_id=UUID(resource_id),
        project_id=UUID(project_id) if project_id else None,
        status=status,
        created_at=_aware(created_at),
        expires_at=_aware(expires_at) if expires_at else None,
        reason=reason,
    )


def _aware(value: datetime) -> datetime:
    if value.tzinfo is None:
        return value.replace(tzinfo=UTC)
    return value.astimezone(UTC)
