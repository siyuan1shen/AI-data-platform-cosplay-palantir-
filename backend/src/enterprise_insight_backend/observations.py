from __future__ import annotations

from collections.abc import Generator
from datetime import UTC, datetime
from hashlib import sha256
from typing import Any
from uuid import UUID, uuid4

from sqlalchemy import (
    JSON,
    DateTime,
    ForeignKey,
    Index,
    Integer,
    LargeBinary,
    String,
    Text,
    UniqueConstraint,
    create_engine,
    event,
    func,
    select,
)
from sqlalchemy.engine import Engine
from sqlalchemy.orm import DeclarativeBase, Mapped, Session, mapped_column, sessionmaker

from enterprise_insight_backend.config import Settings
from enterprise_insight_backend.errors import DomainError
from enterprise_insight_backend.models import ProjectRow
from enterprise_insight_backend.schemas import (
    ObservationAttachmentView,
    ObservationCreate,
    ObservationExtractionDraft,
    ObservationExtractionHistoryView,
    ObservationExtractionView,
    ObservationHistoryView,
    ObservationVersionView,
    ObservationView,
)
from enterprise_insight_backend.service_utils import json_ready, now_utc, require_revision


class ObservationBase(DeclarativeBase):
    pass


class ManagementObservationRow(ObservationBase):
    __tablename__ = "management_observations"
    __table_args__ = (
        UniqueConstraint(
            "project_id", "idempotency_key", name="uq_observation_project_idempotency"
        ),
        Index("ix_observation_project_status_created", "project_id", "status", "created_at"),
    )

    id: Mapped[str] = mapped_column(String(36), primary_key=True)
    company_id: Mapped[str] = mapped_column(String(36), index=True)
    project_id: Mapped[str] = mapped_column(String(36), index=True)
    kind: Mapped[str] = mapped_column(String(32), index=True)
    title: Mapped[str] = mapped_column(String(200))
    content: Mapped[str] = mapped_column(Text)
    content_sha256: Mapped[str] = mapped_column(String(64))
    occurred_at: Mapped[datetime | None] = mapped_column(DateTime(timezone=True))
    submitted_by: Mapped[str] = mapped_column(String(128))
    status: Mapped[str] = mapped_column(String(24), default="ACTIVE", index=True)
    revision: Mapped[int] = mapped_column(Integer, default=1)
    idempotency_key: Mapped[str | None] = mapped_column(String(128))
    created_at: Mapped[datetime] = mapped_column(DateTime(timezone=True))
    updated_at: Mapped[datetime] = mapped_column(DateTime(timezone=True))


class ObservationVersionRow(ObservationBase):
    __tablename__ = "management_observation_versions"
    __table_args__ = (
        UniqueConstraint("observation_id", "revision", name="uq_observation_version"),
        Index("ix_observation_version_observation_created", "observation_id", "created_at"),
    )

    id: Mapped[str] = mapped_column(String(36), primary_key=True)
    observation_id: Mapped[str] = mapped_column(
        String(36), ForeignKey("management_observations.id", ondelete="RESTRICT"), index=True
    )
    revision: Mapped[int] = mapped_column(Integer)
    snapshot: Mapped[dict[str, Any]] = mapped_column(JSON, default=dict)
    operation: Mapped[str] = mapped_column(String(24))
    actor_id: Mapped[str] = mapped_column(String(128))
    created_at: Mapped[datetime] = mapped_column(DateTime(timezone=True))


class ObservationAuditRow(ObservationBase):
    __tablename__ = "management_observation_audit"
    __table_args__ = (
        Index("ix_observation_audit_target_created", "observation_id", "created_at"),
    )

    id: Mapped[str] = mapped_column(String(36), primary_key=True)
    observation_id: Mapped[str] = mapped_column(String(36), index=True)
    company_id: Mapped[str] = mapped_column(String(36), index=True)
    project_id: Mapped[str] = mapped_column(String(36), index=True)
    actor_id: Mapped[str] = mapped_column(String(128))
    operation: Mapped[str] = mapped_column(String(24))
    revision: Mapped[int] = mapped_column(Integer)
    snapshot: Mapped[dict[str, Any]] = mapped_column(JSON, default=dict)
    created_at: Mapped[datetime] = mapped_column(DateTime(timezone=True))


class ObservationExtractionRow(ObservationBase):
    __tablename__ = "management_observation_extractions"
    __table_args__ = (
        Index("ix_observation_extraction_observation_created", "observation_id", "created_at"),
    )

    id: Mapped[str] = mapped_column(String(36), primary_key=True)
    observation_id: Mapped[str] = mapped_column(
        String(36), ForeignKey("management_observations.id", ondelete="RESTRICT"), index=True
    )
    source_revision: Mapped[int] = mapped_column(Integer)
    model_profile_id: Mapped[str] = mapped_column(String(36))
    model_name: Mapped[str] = mapped_column(String(200))
    status: Mapped[str] = mapped_column(String(24), index=True)
    items: Mapped[list[dict[str, Any]]] = mapped_column(JSON, default=list)
    unresolved: Mapped[list[str]] = mapped_column(JSON, default=list)
    error_code: Mapped[str | None] = mapped_column(String(80))
    created_at: Mapped[datetime] = mapped_column(DateTime(timezone=True))


class ObservationAttachmentRow(ObservationBase):
    __tablename__ = "management_observation_attachments"
    __table_args__ = (
        UniqueConstraint("observation_id", "content_sha256", name="uq_observation_attachment_hash"),
        Index("ix_observation_attachment_observation", "observation_id"),
    )

    id: Mapped[str] = mapped_column(String(36), primary_key=True)
    observation_id: Mapped[str] = mapped_column(
        String(36), ForeignKey("management_observations.id", ondelete="RESTRICT"), index=True
    )
    file_name: Mapped[str] = mapped_column(String(500))
    media_type: Mapped[str] = mapped_column(String(200))
    content_sha256: Mapped[str] = mapped_column(String(64))
    size_bytes: Mapped[int] = mapped_column(Integer)
    content: Mapped[bytes] = mapped_column(LargeBinary)
    created_at: Mapped[datetime] = mapped_column(DateTime(timezone=True))


class ObservationDatabase:
    """Logical observation domain backed by the platform database by default."""

    def __init__(self, settings: Settings) -> None:
        url = settings.resolved_observation_database_url
        if not url.startswith("sqlite"):
            raise ValueError("The isolated observation store currently requires SQLite.")
        connect_args = (
            {"check_same_thread": False, "timeout": 30} if url.startswith("sqlite") else {}
        )
        self.engine: Engine = create_engine(url, connect_args=connect_args, pool_pre_ping=True)
        self.shared_storage = settings.unified_storage and url == settings.resolved_database_url
        if url.startswith("sqlite"):
            event.listen(self.engine, "connect", _enable_sqlite_foreign_keys)
        self.session_factory = sessionmaker(
            bind=self.engine, autoflush=False, expire_on_commit=False
        )

    def create_schema(self) -> None:
        # Importing the virtual-work domain registers its separate logical
        # resources in the shared observations.db metadata. It never imports
        # or writes the formal enterprise database.
        from enterprise_insight_backend import work_observation  # noqa: F401
        from enterprise_insight_backend.virtual_work import Base as VirtualWorkBase

        with self.engine.begin() as connection:
            if self.shared_storage:
                # PRAGMA user_version is database-global when all domains share
                # one SQLite file, so use a domain-local marker in that mode.
                connection.exec_driver_sql(
                    "CREATE TABLE IF NOT EXISTS observation_schema "
                    "(id INTEGER PRIMARY KEY, version INTEGER NOT NULL)"
                )
                version = connection.exec_driver_sql(
                    "SELECT version FROM observation_schema WHERE id = 1"
                ).scalar_one_or_none()
                if version is not None and int(version) > 4:
                    raise RuntimeError(f"Unsupported observation database revision: {version}")
            else:
                version = connection.exec_driver_sql("PRAGMA user_version").scalar_one()
                if int(version) > 4:
                    raise RuntimeError(f"Unsupported observation database revision: {version}")
            VirtualWorkBase.metadata.create_all(connection)
            if self.shared_storage:
                connection.exec_driver_sql(
                    "INSERT INTO observation_schema(id, version) VALUES (1, 4) "
                    "ON CONFLICT(id) DO UPDATE SET version = excluded.version"
                )
            else:
                connection.exec_driver_sql("PRAGMA user_version=4")

    def session_dependency(self) -> Generator[Session, None, None]:
        with self.session_factory() as session:
            try:
                yield session
                session.commit()
            except Exception:
                session.rollback()
                raise


class ObservationService:
    def __init__(self, session: Session, settings: Settings) -> None:
        self.session = session
        self.settings = settings

    def create(
        self, project: ProjectRow, project_id: UUID, payload: ObservationCreate
    ) -> ObservationView:
        digest = sha256(payload.content.encode("utf-8")).hexdigest()
        if payload.idempotency_key:
            existing = self.session.scalar(
                select(ManagementObservationRow).where(
                    ManagementObservationRow.project_id == str(project_id),
                    ManagementObservationRow.idempotency_key == payload.idempotency_key,
                )
            )
            if existing is not None:
                if existing.content_sha256 == digest and self._matches_payload(existing, payload):
                    return self._view(existing)
                raise DomainError(
                    "OBSERVATION_IDEMPOTENCY_CONFLICT",
                    "该提交标识已用于不同内容，请刷新后重新提交。",
                    status_code=409,
                )

        now = now_utc()
        row = ManagementObservationRow(
            id=str(uuid4()),
            company_id=project.company_id,
            project_id=str(project_id),
            kind=payload.kind.value,
            title=payload.title,
            content=payload.content,
            content_sha256=digest,
            occurred_at=_normalize_datetime(payload.occurred_at),
            submitted_by=self.settings.local_actor_id,
            status="ACTIVE",
            revision=1,
            idempotency_key=payload.idempotency_key,
            created_at=now,
            updated_at=now,
        )
        self.session.add(row)
        self.session.flush()
        self._record_revision(row, operation="CREATED", at=now)
        self.session.flush()
        return self._view(row)

    def list(
        self,
        project_id: UUID,
        *,
        include_withdrawn: bool = False,
        offset: int = 0,
        limit: int = 50,
    ) -> tuple[list[ObservationView], int]:
        statement = select(ManagementObservationRow).where(
            ManagementObservationRow.project_id == str(project_id)
        )
        if not include_withdrawn:
            statement = statement.where(ManagementObservationRow.status == "ACTIVE")
        total = self.session.scalar(select(func.count()).select_from(statement.subquery())) or 0
        rows = self.session.scalars(
            statement.order_by(
                ManagementObservationRow.created_at.desc(), ManagementObservationRow.id
            ).offset(offset).limit(limit)
        ).all()
        return [self._view(row) for row in rows], total

    def get(self, project_id: UUID, observation_id: UUID) -> ManagementObservationRow:
        row = self.session.get(ManagementObservationRow, str(observation_id))
        if row is None or row.project_id != str(project_id):
            raise DomainError("OBSERVATION_NOT_FOUND", "管理观察不存在。", status_code=404)
        return row

    def revise(
        self,
        project_id: UUID,
        observation_id: UUID,
        expected_revision: int,
        payload: ObservationCreate,
    ) -> ObservationView:
        row = self.get(project_id, observation_id)
        require_revision(row.revision, expected_revision, resource="管理观察")
        if row.status != "ACTIVE":
            raise DomainError(
                "OBSERVATION_WITHDRAWN", "已撤回的观察不能修改，请重新提交新记录。", status_code=409
            )
        row.kind = payload.kind.value
        row.title = payload.title
        row.content = payload.content
        row.content_sha256 = sha256(payload.content.encode("utf-8")).hexdigest()
        row.occurred_at = _normalize_datetime(payload.occurred_at)
        row.revision += 1
        row.updated_at = now_utc()
        self._record_revision(row, operation="REVISED", at=row.updated_at)
        self.session.flush()
        return self._view(row)

    def withdraw(
        self, project_id: UUID, observation_id: UUID, expected_revision: int
    ) -> ObservationView:
        row = self.get(project_id, observation_id)
        require_revision(row.revision, expected_revision, resource="管理观察")
        if row.status == "WITHDRAWN":
            return self._view(row)
        row.status = "WITHDRAWN"
        row.revision += 1
        row.updated_at = now_utc()
        self._record_revision(row, operation="WITHDRAWN", at=row.updated_at)
        self.session.flush()
        return self._view(row)

    def history(
        self, project_id: UUID, observation_id: UUID
    ) -> ObservationHistoryView:
        self.get(project_id, observation_id)
        versions = self.session.scalars(
            select(ObservationVersionRow)
            .where(ObservationVersionRow.observation_id == str(observation_id))
            .order_by(ObservationVersionRow.revision)
        ).all()
        return ObservationHistoryView(
            items=[ObservationVersionView.model_validate(item) for item in versions],
            total=len(versions),
        )

    def record_extraction(
        self,
        project_id: UUID,
        observation_id: UUID,
        *,
        model_profile_id: UUID,
        model_name: str,
        status: str,
        draft: ObservationExtractionDraft | None,
        error_code: str | None = None,
    ) -> ObservationExtractionView:
        observation = self.get(project_id, observation_id)
        if status not in {"COMPLETED", "FAILED"}:
            raise ValueError("Unsupported extraction status.")
        now = now_utc()
        item_values = [
            item.model_dump(mode="json") for item in draft.items
        ] if draft else []
        unresolved = draft.unresolved if draft else []
        row = ObservationExtractionRow(
            id=str(uuid4()),
            observation_id=observation.id,
            source_revision=observation.revision,
            model_profile_id=str(model_profile_id),
            model_name=model_name,
            status=status,
            items=item_values,
            unresolved=unresolved,
            error_code=error_code,
            created_at=now,
        )
        snapshot = {
            "extraction_id": row.id,
            "source_revision": row.source_revision,
            "model_profile_id": row.model_profile_id,
            "model_name": row.model_name,
            "status": status,
            "items": item_values,
            "unresolved": unresolved,
            "error_code": error_code,
        }
        audit = ObservationAuditRow(
            id=str(uuid4()),
            observation_id=observation.id,
            company_id=observation.company_id,
            project_id=observation.project_id,
            actor_id=self.settings.local_actor_id,
            operation=f"EXTRACTION_{status}",
            revision=observation.revision,
            snapshot=snapshot,
            created_at=now,
        )
        self.session.add_all([row, audit])
        self.session.flush()
        return ObservationExtractionView.model_validate(row)

    def attach_source_file(
        self,
        project_id: UUID,
        observation_id: UUID,
        *,
        file_name: str,
        media_type: str,
        content: bytes,
    ) -> ObservationAttachmentView:
        observation = self.get(project_id, observation_id)
        digest = sha256(content).hexdigest()
        existing = self.session.scalar(
            select(ObservationAttachmentRow).where(
                ObservationAttachmentRow.observation_id == observation.id,
                ObservationAttachmentRow.content_sha256 == digest,
            )
        )
        if existing is not None:
            return _attachment_view(existing)
        row = ObservationAttachmentRow(
            id=str(uuid4()),
            observation_id=observation.id,
            file_name=file_name[:500],
            media_type=media_type[:200],
            content_sha256=digest,
            size_bytes=len(content),
            content=content,
            created_at=now_utc(),
        )
        snapshot = {
            "attachment_id": row.id,
            "file_name": row.file_name,
            "media_type": row.media_type,
            "content_sha256": row.content_sha256,
            "size_bytes": row.size_bytes,
        }
        self.session.add(row)
        self.session.add(
            ObservationAuditRow(
                id=str(uuid4()),
                observation_id=observation.id,
                company_id=observation.company_id,
                project_id=observation.project_id,
                actor_id=self.settings.local_actor_id,
                operation="ATTACHMENT_ADDED",
                revision=observation.revision,
                snapshot=snapshot,
                created_at=row.created_at,
            )
        )
        self.session.flush()
        return _attachment_view(row)

    def list_attachments(
        self, project_id: UUID, observation_id: UUID
    ) -> list[ObservationAttachmentView]:
        observation = self.get(project_id, observation_id)
        rows = self.session.scalars(
            select(ObservationAttachmentRow)
            .where(ObservationAttachmentRow.observation_id == observation.id)
            .order_by(ObservationAttachmentRow.created_at, ObservationAttachmentRow.id)
        ).all()
        return [_attachment_view(row) for row in rows]

    def get_attachment(
        self, project_id: UUID, observation_id: UUID, attachment_id: UUID
    ) -> ObservationAttachmentRow:
        self.get(project_id, observation_id)
        row = self.session.get(ObservationAttachmentRow, str(attachment_id))
        if row is None or row.observation_id != str(observation_id):
            raise DomainError(
                "OBSERVATION_ATTACHMENT_NOT_FOUND", "管理信息附件不存在。", status_code=404
            )
        return row

    def extraction_history(
        self, project_id: UUID, observation_id: UUID
    ) -> ObservationExtractionHistoryView:
        self.get(project_id, observation_id)
        rows = self.session.scalars(
            select(ObservationExtractionRow)
            .where(ObservationExtractionRow.observation_id == str(observation_id))
            .order_by(ObservationExtractionRow.created_at.desc(), ObservationExtractionRow.id)
        ).all()
        return ObservationExtractionHistoryView(
            items=[ObservationExtractionView.model_validate(row) for row in rows],
            total=len(rows),
        )

    def _record_revision(
        self, row: ManagementObservationRow, *, operation: str, at: datetime
    ) -> None:
        snapshot = {
            "kind": row.kind,
            "title": row.title,
            "content": row.content,
            "content_sha256": row.content_sha256,
            "occurred_at": json_ready(_normalize_datetime(row.occurred_at)),
            "status": row.status,
        }
        version = ObservationVersionRow(
            id=str(uuid4()),
            observation_id=row.id,
            revision=row.revision,
            snapshot=snapshot,
            operation=operation,
            actor_id=self.settings.local_actor_id,
            created_at=at,
        )
        audit = ObservationAuditRow(
            id=str(uuid4()),
            observation_id=row.id,
            company_id=row.company_id,
            project_id=row.project_id,
            actor_id=self.settings.local_actor_id,
            operation=operation,
            revision=row.revision,
            snapshot=snapshot,
            created_at=at,
        )
        self.session.add_all([version, audit])

    @staticmethod
    def _view(row: ManagementObservationRow) -> ObservationView:
        view = ObservationView.model_validate(row)
        return view.model_copy(update={"occurred_at": _normalize_datetime(view.occurred_at)})

    @staticmethod
    def _matches_payload(row: ManagementObservationRow, payload: ObservationCreate) -> bool:
        return (
            row.kind == payload.kind.value
            and row.title == payload.title
            and _normalize_datetime(row.occurred_at) == _normalize_datetime(payload.occurred_at)
        )


def _enable_sqlite_foreign_keys(dbapi_connection: object, _: object) -> None:
    cursor = dbapi_connection.cursor()  # type: ignore[attr-defined]
    cursor.execute("PRAGMA foreign_keys=ON")
    cursor.close()


def _attachment_view(row: ObservationAttachmentRow) -> ObservationAttachmentView:
    return ObservationAttachmentView(
        id=UUID(row.id),
        observation_id=UUID(row.observation_id),
        file_name=row.file_name,
        media_type=row.media_type,
        content_sha256=row.content_sha256,
        size_bytes=row.size_bytes,
        created_at=row.created_at,
    )


def _normalize_datetime(value: datetime | None) -> datetime | None:
    if value is None:
        return None
    if value.tzinfo is None:
        value = value.replace(tzinfo=UTC)
    return value.astimezone(UTC)
