from __future__ import annotations

import json
from datetime import UTC, datetime
from enum import StrEnum
from hashlib import sha256
from typing import Any
from uuid import UUID, uuid4

from pydantic import BaseModel, ConfigDict, Field, model_validator
from sqlalchemy import (
    ForeignKey,
    ForeignKeyConstraint,
    Index,
    Integer,
    String,
    Text,
    UniqueConstraint,
    create_engine,
    event,
    func,
    select,
    update,
)
from sqlalchemy.dialects.sqlite import insert as sqlite_insert
from sqlalchemy.engine import Engine, make_url
from sqlalchemy.orm import DeclarativeBase, Mapped, Session, mapped_column, sessionmaker
from sqlalchemy.pool import StaticPool

from enterprise_insight_backend.errors import DomainError
from enterprise_insight_backend.service_utils import now_utc, require_revision


class PotentialType(StrEnum):
    RELATION_HYPOTHESIS = "RELATION_HYPOTHESIS"
    PROBLEM_HYPOTHESIS = "PROBLEM_HYPOTHESIS"
    DESIGN_TRADEOFF = "DESIGN_TRADEOFF"
    CAUSAL_HYPOTHESIS = "CAUSAL_HYPOTHESIS"
    MANAGEMENT_LEARNING = "MANAGEMENT_LEARNING"


class HumanStatus(StrEnum):
    ACCEPTED = "ACCEPTED"
    REJECTED = "REJECTED"
    WITHDRAWN = "WITHDRAWN"


class EvidenceStatus(StrEnum):
    UNTESTED = "UNTESTED"
    SUPPORTED = "SUPPORTED"
    CONTESTED = "CONTESTED"
    REFUTED = "REFUTED"
    STALE = "STALE"


class PotentialOperation(StrEnum):
    CREATED = "CREATED"
    ACCEPTED = "ACCEPTED"
    EDITED = "EDITED"
    REJECTED = "REJECTED"
    WITHDRAWN = "WITHDRAWN"


class PotentialModel(BaseModel):
    model_config = ConfigDict(extra="forbid", from_attributes=True)


class PotentialEvidence(PotentialModel):
    source_ref: str = Field(min_length=1, max_length=500)
    excerpt: str = Field(min_length=1, max_length=12_000)
    observed_at: datetime | None = None
    note: str | None = Field(default=None, max_length=4_000)

    @model_validator(mode="after")
    def validate_text_and_time(self) -> PotentialEvidence:
        self.source_ref = self.source_ref.strip()
        self.excerpt = self.excerpt.strip()
        if not self.source_ref or not self.excerpt:
            raise ValueError("证据来源和摘录不能为空。")
        if self.note is not None:
            self.note = self.note.strip() or None
        _require_aware(self.observed_at, field="observed_at")
        return self


class PotentialFields(PotentialModel):
    company_id: UUID
    project_id: UUID
    potential_type: PotentialType
    claim: str = Field(min_length=1, max_length=12_000)
    applicability_scope: str = Field(min_length=1, max_length=4_000)
    valid_from: datetime | None = None
    valid_until: datetime | None = None
    task_source: str = Field(min_length=1, max_length=2_000)
    supporting_evidence: list[PotentialEvidence] = Field(default_factory=list, max_length=100)
    counterevidence: list[PotentialEvidence] = Field(default_factory=list, max_length=100)
    verification_method: str = Field(default="尚未定义", min_length=1, max_length=4_000)
    evidence_status: EvidenceStatus = EvidenceStatus.UNTESTED

    @model_validator(mode="after")
    def validate_content_and_dates(self) -> PotentialFields:
        self.claim = self.claim.strip()
        self.applicability_scope = self.applicability_scope.strip()
        self.task_source = self.task_source.strip()
        self.verification_method = self.verification_method.strip()
        if not self.claim or not self.applicability_scope or not self.task_source:
            raise ValueError("主张、适用范围和任务来源不能为空。")
        _require_aware(self.valid_from, field="valid_from")
        _require_aware(self.valid_until, field="valid_until")
        if self.valid_from and self.valid_until and self.valid_until < self.valid_from:
            raise ValueError("适用时间结束值不能早于开始值。")
        return self


class PotentialRecordCreate(PotentialFields):
    """Human-authored record input. Actor and human state are service-owned fields."""

    idempotency_key: str | None = Field(default=None, min_length=1, max_length=200)

    @model_validator(mode="after")
    def normalize_idempotency_key(self) -> PotentialRecordCreate:
        if self.idempotency_key is not None:
            self.idempotency_key = self.idempotency_key.strip()
            if not self.idempotency_key:
                raise ValueError("幂等标识不能是空白。")
        return self


class PotentialCandidateDraft(PotentialFields):
    """Unpersisted candidate draft; it deliberately has no actor or human status field."""

    candidate_id: UUID = Field(default_factory=uuid4)

    def payload_hash(self) -> str:
        payload = self.model_dump(mode="python")
        return _digest(payload)


class PotentialRecordUpdate(PotentialModel):
    expected_version: int = Field(ge=1)
    reason: str | None = Field(default=None, max_length=4_000)
    potential_type: PotentialType | None = None
    claim: str | None = Field(default=None, min_length=1, max_length=12_000)
    applicability_scope: str | None = Field(default=None, min_length=1, max_length=4_000)
    valid_from: datetime | None = None
    valid_until: datetime | None = None
    task_source: str | None = Field(default=None, min_length=1, max_length=2_000)
    supporting_evidence: list[PotentialEvidence] | None = Field(default=None, max_length=100)
    counterevidence: list[PotentialEvidence] | None = Field(default=None, max_length=100)
    verification_method: str | None = Field(default=None, min_length=1, max_length=4_000)
    evidence_status: EvidenceStatus | None = None

    @model_validator(mode="after")
    def validate_patch(self) -> PotentialRecordUpdate:
        editable = self.model_fields_set - {"expected_version", "reason"}
        if not editable:
            raise ValueError("至少提供一个需要修改的字段。")
        if self.reason is not None:
            self.reason = self.reason.strip() or None
        for field in ("claim", "applicability_scope", "task_source", "verification_method"):
            value = getattr(self, field)
            if field in editable and value is not None:
                value = value.strip()
                setattr(self, field, value)
                if not value:
                    raise ValueError(f"{field} 不能为空。")
        _require_aware(self.valid_from, field="valid_from")
        _require_aware(self.valid_until, field="valid_until")
        return self


class PotentialRecordStatusRequest(PotentialModel):
    expected_version: int = Field(ge=1)
    reason: str | None = Field(default=None, max_length=4_000)

    @model_validator(mode="after")
    def trim_reason(self) -> PotentialRecordStatusRequest:
        if self.reason is not None:
            self.reason = self.reason.strip() or None
        return self


class PotentialRecordView(PotentialFields):
    id: UUID
    human_status: HumanStatus
    version: int = Field(ge=1)
    payload_hash: str = Field(pattern=r"^[0-9a-f]{64}$")
    created_by: str = Field(min_length=1, max_length=128)
    created_at: datetime
    updated_at: datetime


class PotentialVersionView(PotentialModel):
    id: UUID
    record_id: UUID
    version: int = Field(ge=1)
    payload_hash: str = Field(pattern=r"^[0-9a-f]{64}$")
    snapshot: dict[str, Any]
    operation: PotentialOperation
    actor_id: str
    reason: str | None
    created_at: datetime


class PotentialAuditView(PotentialModel):
    id: UUID
    record_id: UUID
    company_id: UUID
    project_id: UUID
    version: int = Field(ge=1)
    operation: PotentialOperation
    actor_id: str
    before_hash: str | None
    after_hash: str
    reason: str | None
    created_at: datetime


class PotentialHistoryView(PotentialModel):
    versions: list[PotentialVersionView]
    audit: list[PotentialAuditView]


class PotentialPage(PotentialModel):
    items: list[PotentialRecordView]
    total: int = Field(ge=0)


class PotentialBase(DeclarativeBase):
    pass


class PotentialScopeRow(PotentialBase):
    __tablename__ = "potential_company_projects"
    __table_args__ = (UniqueConstraint("project_id", name="uq_potential_scope_project"),)

    company_id: Mapped[str] = mapped_column(String(36), primary_key=True)
    project_id: Mapped[str] = mapped_column(String(36), primary_key=True)


class PotentialRecordRow(PotentialBase):
    __tablename__ = "potential_records"
    __table_args__ = (
        ForeignKeyConstraint(
            ["company_id", "project_id"],
            ["potential_company_projects.company_id", "potential_company_projects.project_id"],
            ondelete="RESTRICT",
        ),
        UniqueConstraint("company_id", "project_id", "id", name="uq_potential_scoped_id"),
        Index(
            "uq_potential_project_idempotency",
            "company_id",
            "project_id",
            "idempotency_key",
            unique=True,
        ),
        Index(
            "ix_potential_scope_status_time",
            "company_id",
            "project_id",
            "human_status",
            "created_at",
        ),
    )

    id: Mapped[str] = mapped_column(String(36), primary_key=True)
    company_id: Mapped[str] = mapped_column(String(36))
    project_id: Mapped[str] = mapped_column(String(36))
    idempotency_key: Mapped[str | None] = mapped_column(String(200))
    potential_type: Mapped[str] = mapped_column(String(40))
    claim: Mapped[str] = mapped_column(Text)
    applicability_scope: Mapped[str] = mapped_column(Text)
    valid_from: Mapped[str | None] = mapped_column(String(40))
    valid_until: Mapped[str | None] = mapped_column(String(40))
    task_source: Mapped[str] = mapped_column(Text)
    supporting_evidence_json: Mapped[str] = mapped_column(Text)
    counterevidence_json: Mapped[str] = mapped_column(Text)
    verification_method: Mapped[str] = mapped_column(Text)
    human_status: Mapped[str] = mapped_column(String(16), index=True)
    evidence_status: Mapped[str] = mapped_column(String(16), index=True)
    version: Mapped[int] = mapped_column(Integer)
    payload_hash: Mapped[str] = mapped_column(String(64))
    created_by: Mapped[str] = mapped_column(String(128))
    created_at: Mapped[str] = mapped_column(String(40))
    updated_at: Mapped[str] = mapped_column(String(40))


class PotentialVersionRow(PotentialBase):
    __tablename__ = "potential_record_versions"
    __table_args__ = (
        UniqueConstraint("record_id", "version", name="uq_potential_record_version"),
        Index("ix_potential_version_record", "record_id", "version"),
    )

    id: Mapped[str] = mapped_column(String(36), primary_key=True)
    record_id: Mapped[str] = mapped_column(
        String(36), ForeignKey("potential_records.id", ondelete="RESTRICT"), index=True
    )
    version: Mapped[int] = mapped_column(Integer)
    payload_hash: Mapped[str] = mapped_column(String(64))
    snapshot_json: Mapped[str] = mapped_column(Text)
    operation: Mapped[str] = mapped_column(String(16))
    actor_id: Mapped[str] = mapped_column(String(128))
    reason: Mapped[str | None] = mapped_column(Text)
    created_at: Mapped[str] = mapped_column(String(40))


class PotentialAuditRow(PotentialBase):
    __tablename__ = "potential_record_audit"
    __table_args__ = (Index("ix_potential_audit_record", "record_id", "version"),)

    id: Mapped[str] = mapped_column(String(36), primary_key=True)
    record_id: Mapped[str] = mapped_column(
        String(36), ForeignKey("potential_records.id", ondelete="RESTRICT"), index=True
    )
    company_id: Mapped[str] = mapped_column(String(36))
    project_id: Mapped[str] = mapped_column(String(36))
    version: Mapped[int] = mapped_column(Integer)
    operation: Mapped[str] = mapped_column(String(16))
    actor_id: Mapped[str] = mapped_column(String(128))
    before_hash: Mapped[str | None] = mapped_column(String(64))
    after_hash: Mapped[str] = mapped_column(String(64))
    reason: Mapped[str | None] = mapped_column(Text)
    created_at: Mapped[str] = mapped_column(String(40))


class PotentialDatabase:
    """A physically separate SQLite store. Scope rows contain IDs only, not formal data."""

    def __init__(self, database_url: str) -> None:
        if database_url == ":memory:":
            database_url = "sqlite:///:memory:"
        try:
            parsed_url = make_url(database_url)
        except Exception as exc:
            raise ValueError("database_url 必须是有效的 SQLite URL 或 :memory:。") from exc
        if parsed_url.get_backend_name() != "sqlite":
            raise ValueError("潜在库切片目前只支持独立 SQLite 数据库。")

        self.database_url = database_url
        is_memory = parsed_url.database in (None, "", ":memory:")
        engine_options: dict[str, Any] = {
            "connect_args": {"check_same_thread": False, "timeout": 30},
            "pool_pre_ping": True,
        }
        if is_memory:
            engine_options["poolclass"] = StaticPool
        self.engine: Engine = create_engine(database_url, **engine_options)
        event.listen(self.engine, "connect", _enable_sqlite_foreign_keys)
        self.session_factory = sessionmaker(
            bind=self.engine, autoflush=False, expire_on_commit=False
        )

    def create_schema(self) -> None:
        with self.engine.begin() as connection:
            # PRAGMA user_version belongs to the whole SQLite file.  Use a
            # domain-local marker so the potential library can live beside
            # formal, observation and control tables in one database.
            connection.exec_driver_sql(
                "CREATE TABLE IF NOT EXISTS potential_schema "
                "(id INTEGER PRIMARY KEY, version INTEGER NOT NULL)"
            )
            version = connection.exec_driver_sql(
                "SELECT version FROM potential_schema WHERE id = 1"
            ).scalar_one_or_none()
            if version is not None and int(version) > 2:
                raise RuntimeError(f"Unsupported potential database revision: {version}")
            PotentialBase.metadata.create_all(connection)
            columns = {
                row[1] for row in connection.exec_driver_sql("PRAGMA table_info(potential_records)")
            }
            if "idempotency_key" not in columns:
                connection.exec_driver_sql(
                    "ALTER TABLE potential_records ADD COLUMN idempotency_key VARCHAR(200)"
                )
            connection.exec_driver_sql(
                "CREATE UNIQUE INDEX IF NOT EXISTS uq_potential_project_idempotency "
                "ON potential_records(company_id, project_id, idempotency_key)"
            )
            connection.exec_driver_sql(
                "INSERT INTO potential_schema(id, version) VALUES (1, 2) "
                "ON CONFLICT(id) DO UPDATE SET version = excluded.version"
            )

    def dispose(self) -> None:
        self.engine.dispose()


class PotentialRecordService:
    """Transactional operations over accepted/rejected/withdrawn potential records."""

    def __init__(self, database: PotentialDatabase) -> None:
        self.database = database

    def create(self, payload: PotentialRecordCreate, *, actor_id: str) -> PotentialRecordView:
        actor = _actor_id(actor_id)
        with self.database.session_factory.begin() as session:
            self._ensure_scope(session, payload.company_id, payload.project_id)
            expected_hash = _new_record_payload_hash(payload)
            now = now_utc()
            row = _new_record_row(
                payload,
                record_id=uuid4(),
                actor_id=actor,
                human_status=HumanStatus.ACCEPTED,
                version=1,
                now=now,
            )
            row.idempotency_key = payload.idempotency_key
            values = {
                column.key: getattr(row, column.key)
                for column in PotentialRecordRow.__table__.columns
            }
            insert_result = session.execute(
                sqlite_insert(PotentialRecordRow)
                .values(**values)
                .on_conflict_do_nothing(
                    index_elements=["company_id", "project_id", "idempotency_key"]
                )
            )
            if insert_result.rowcount == 0:
                existing = session.scalar(
                    select(PotentialRecordRow).where(
                        PotentialRecordRow.company_id == str(payload.company_id),
                        PotentialRecordRow.project_id == str(payload.project_id),
                        PotentialRecordRow.idempotency_key == payload.idempotency_key,
                    )
                )
                if existing is not None and existing.payload_hash == expected_hash:
                    return _record_view(existing)
                if existing is not None:
                    raise DomainError(
                        "POTENTIAL_IDEMPOTENCY_CONFLICT",
                        "该提交标识已用于不同的潜在记录内容。",
                        status_code=409,
                    )
                raise RuntimeError("潜在记录创建时发生未识别的唯一键冲突。")
            persisted = session.get(PotentialRecordRow, row.id)
            if persisted is None:
                raise RuntimeError("潜在记录写入后无法重新读取。")
            self._append_history(
                session,
                persisted,
                operation=PotentialOperation.CREATED,
                actor_id=actor,
                reason=None,
                before_hash=None,
                at=now,
            )
            session.flush()
            return _record_view(persisted)

    def accept_candidate(
        self,
        candidate: PotentialCandidateDraft,
        *,
        expected_hash: str,
        actor_id: str,
        idempotency_key: str | None = None,
    ) -> PotentialRecordView:
        actor = _actor_id(actor_id)
        actual_hash = candidate.payload_hash()
        if expected_hash != actual_hash:
            raise DomainError(
                "POTENTIAL_CANDIDATE_STALE",
                "候选内容已变化或确认版本不匹配，请重新检查后确认。",
                status_code=409,
                details=[{"expected_hash": expected_hash, "actual_hash": actual_hash}],
            )
        with self.database.session_factory.begin() as session:
            self._ensure_scope(session, candidate.company_id, candidate.project_id)
            now = now_utc()
            row = _new_record_row(
                candidate,
                record_id=uuid4(),
                actor_id=actor,
                human_status=HumanStatus.ACCEPTED,
                version=1,
                now=now,
            )
            row.idempotency_key = idempotency_key
            values = {
                column.key: getattr(row, column.key)
                for column in PotentialRecordRow.__table__.columns
            }
            inserted = session.execute(
                sqlite_insert(PotentialRecordRow)
                .values(**values)
                .on_conflict_do_nothing(
                    index_elements=["company_id", "project_id", "idempotency_key"]
                )
            )
            if inserted.rowcount == 0:
                existing = session.scalar(
                    select(PotentialRecordRow).where(
                        PotentialRecordRow.company_id == str(candidate.company_id),
                        PotentialRecordRow.project_id == str(candidate.project_id),
                        PotentialRecordRow.idempotency_key == idempotency_key,
                    )
                )
                if existing is not None and existing.payload_hash == _new_record_payload_hash(
                    candidate
                ):
                    return _record_view(existing)
                if existing is not None:
                    raise DomainError(
                        "POTENTIAL_IDEMPOTENCY_CONFLICT",
                        "该提交标识已用于不同的潜在记录内容。",
                        status_code=409,
                    )
                raise RuntimeError("潜在候选确认时发生未识别的唯一键冲突。")
            persisted = session.get(PotentialRecordRow, row.id)
            if persisted is None:
                raise RuntimeError("潜在候选写入后无法重新读取。")
            self._append_history(
                session,
                persisted,
                operation=PotentialOperation.ACCEPTED,
                actor_id=actor,
                reason="人工确认候选并创建潜在记录。",
                before_hash=None,
                at=now,
            )
            session.flush()
            return _record_view(persisted)

    def get(self, record_id: UUID, *, company_id: UUID, project_id: UUID) -> PotentialRecordView:
        with self.database.session_factory() as session:
            row = self._get_row(session, record_id, company_id=company_id, project_id=project_id)
            return _record_view(row)

    def list_records(
        self,
        *,
        company_id: UUID,
        project_id: UUID,
        include_history: bool = False,
        limit: int = 100,
        offset: int = 0,
    ) -> PotentialPage:
        if not 1 <= limit <= 500 or offset < 0:
            raise DomainError("POTENTIAL_PAGE_INVALID", "分页参数超出允许范围。", status_code=422)
        with self.database.session_factory() as session:
            self._assert_scope(session, company_id, project_id)
            statement = select(PotentialRecordRow).where(
                PotentialRecordRow.company_id == str(company_id),
                PotentialRecordRow.project_id == str(project_id),
            )
            if not include_history:
                statement = statement.where(
                    PotentialRecordRow.human_status == HumanStatus.ACCEPTED.value,
                    PotentialRecordRow.evidence_status != EvidenceStatus.REFUTED.value,
                )
            rows = session.scalars(
                statement.order_by(PotentialRecordRow.created_at.desc(), PotentialRecordRow.id)
                .offset(offset)
                .limit(limit)
            ).all()
            count_statement = (
                select(func.count())
                .select_from(PotentialRecordRow)
                .where(
                    PotentialRecordRow.company_id == str(company_id),
                    PotentialRecordRow.project_id == str(project_id),
                )
            )
            if not include_history:
                count_statement = count_statement.where(
                    PotentialRecordRow.human_status == HumanStatus.ACCEPTED.value,
                    PotentialRecordRow.evidence_status != EvidenceStatus.REFUTED.value,
                )
            total = session.scalar(count_statement) or 0
            return PotentialPage(items=[_record_view(row) for row in rows], total=total)

    def history(
        self, record_id: UUID, *, company_id: UUID, project_id: UUID
    ) -> PotentialHistoryView:
        with self.database.session_factory() as session:
            self._get_row(session, record_id, company_id=company_id, project_id=project_id)
            versions = session.scalars(
                select(PotentialVersionRow)
                .where(PotentialVersionRow.record_id == str(record_id))
                .order_by(PotentialVersionRow.version)
            ).all()
            audits = session.scalars(
                select(PotentialAuditRow)
                .where(PotentialAuditRow.record_id == str(record_id))
                .order_by(PotentialAuditRow.version)
            ).all()
            return PotentialHistoryView(
                versions=[_version_view(item) for item in versions],
                audit=[_audit_view(item) for item in audits],
            )

    def edit(
        self,
        record_id: UUID,
        payload: PotentialRecordUpdate,
        *,
        company_id: UUID,
        project_id: UUID,
        actor_id: str,
        reason: str | None = None,
    ) -> PotentialRecordView:
        actor = _actor_id(actor_id)
        updates = payload.model_dump(
            exclude_unset=True, exclude={"expected_version", "reason"}
        )
        for required in (
            "potential_type",
            "claim",
            "applicability_scope",
            "task_source",
            "verification_method",
        ):
            if required in updates and updates[required] is None:
                raise DomainError(
                    "POTENTIAL_EDIT_INVALID", f"{required} 不能设为 null。", status_code=422
                )
        return self._change(
            record_id,
            company_id=company_id,
            project_id=project_id,
            expected_version=payload.expected_version,
            actor_id=actor,
            operation=PotentialOperation.EDITED,
            reason=reason if reason is not None else payload.reason,
            changes=updates,
        )

    def accept(
        self,
        record_id: UUID,
        *,
        company_id: UUID,
        project_id: UUID,
        expected_version: int,
        actor_id: str,
        reason: str | None = None,
    ) -> PotentialRecordView:
        return self._change(
            record_id,
            company_id=company_id,
            project_id=project_id,
            expected_version=expected_version,
            actor_id=_actor_id(actor_id),
            operation=PotentialOperation.ACCEPTED,
            reason=reason,
            changes={"human_status": HumanStatus.ACCEPTED.value},
        )

    def reject(
        self,
        record_id: UUID,
        *,
        company_id: UUID,
        project_id: UUID,
        expected_version: int,
        actor_id: str,
        reason: str,
    ) -> PotentialRecordView:
        normalized_reason = _reason(reason, required=True)
        return self._change(
            record_id,
            company_id=company_id,
            project_id=project_id,
            expected_version=expected_version,
            actor_id=_actor_id(actor_id),
            operation=PotentialOperation.REJECTED,
            reason=normalized_reason,
            changes={"human_status": HumanStatus.REJECTED.value},
        )

    def withdraw(
        self,
        record_id: UUID,
        *,
        company_id: UUID,
        project_id: UUID,
        expected_version: int,
        actor_id: str,
        reason: str | None = None,
    ) -> PotentialRecordView:
        return self._change(
            record_id,
            company_id=company_id,
            project_id=project_id,
            expected_version=expected_version,
            actor_id=_actor_id(actor_id),
            operation=PotentialOperation.WITHDRAWN,
            reason=reason,
            changes={"human_status": HumanStatus.WITHDRAWN.value},
        )

    def _change(
        self,
        record_id: UUID,
        *,
        company_id: UUID,
        project_id: UUID,
        expected_version: int,
        actor_id: str,
        operation: PotentialOperation,
        reason: str | None,
        changes: dict[str, Any],
    ) -> PotentialRecordView:
        normalized_reason = _reason(reason, required=False)
        with self.database.session_factory.begin() as session:
            row = self._get_row(session, record_id, company_id=company_id, project_id=project_id)
            require_revision(row.version, expected_version, resource="潜在记录")
            if row.human_status == HumanStatus.WITHDRAWN.value:
                raise DomainError(
                    "POTENTIAL_RECORD_WITHDRAWN",
                    "已撤回记录不能再次修改或改变人工状态。",
                    status_code=409,
                )
            current = _record_content(row)
            if (
                operation == PotentialOperation.ACCEPTED
                and row.human_status != HumanStatus.REJECTED.value
            ):
                raise DomainError(
                    "POTENTIAL_ACCEPT_INVALID", "只有已拒绝的记录可以重新接受。", status_code=409
                )
            if (
                operation == PotentialOperation.REJECTED
                and row.human_status != HumanStatus.ACCEPTED.value
            ):
                raise DomainError(
                    "POTENTIAL_REJECT_INVALID", "只有已接受的记录可以拒绝。", status_code=409
                )
            if operation == PotentialOperation.WITHDRAWN and row.human_status not in {
                HumanStatus.ACCEPTED.value,
                HumanStatus.REJECTED.value,
            }:
                raise DomainError(
                    "POTENTIAL_WITHDRAW_INVALID", "当前状态不能撤回。", status_code=409
                )

            next_content = {**current, **_normalize_input_values(changes)}
            valid_from = _parse_utc(next_content["valid_from"])
            valid_until = _parse_utc(next_content["valid_until"])
            if valid_from and valid_until and valid_until < valid_from:
                raise DomainError(
                    "POTENTIAL_TIME_RANGE_INVALID",
                    "适用时间结束值不能早于开始值。",
                    status_code=422,
                )
            next_version = row.version + 1
            next_hash = _digest(_scoped_content(row.company_id, row.project_id, next_content))
            now = now_utc()
            db_values = _content_to_columns(next_content)
            db_values.update(
                {
                    "version": next_version,
                    "payload_hash": next_hash,
                    "updated_at": _utc_string(now),
                }
            )
            result = session.execute(
                update(PotentialRecordRow)
                .where(
                    PotentialRecordRow.id == str(record_id),
                    PotentialRecordRow.company_id == str(company_id),
                    PotentialRecordRow.project_id == str(project_id),
                    PotentialRecordRow.version == expected_version,
                )
                .values(**db_values)
                .execution_options(synchronize_session=False)
            )
            if result.rowcount != 1:
                latest = session.scalar(
                    select(PotentialRecordRow).where(
                        PotentialRecordRow.id == str(record_id),
                        PotentialRecordRow.company_id == str(company_id),
                        PotentialRecordRow.project_id == str(project_id),
                    )
                )
                if latest is None:
                    raise _not_found()
                require_revision(latest.version, expected_version, resource="潜在记录")
                raise DomainError(
                    "POTENTIAL_UPDATE_CONFLICT", "潜在记录更新冲突。", status_code=409
                )

            session.flush()
            session.refresh(row)
            self._append_history(
                session,
                row,
                operation=operation,
                actor_id=actor_id,
                reason=normalized_reason,
                before_hash=_digest(_scoped_content(row.company_id, row.project_id, current)),
                at=now,
            )
            session.flush()
            return _record_view(row)

    def _append_history(
        self,
        session: Session,
        row: PotentialRecordRow,
        *,
        operation: PotentialOperation,
        actor_id: str,
        reason: str | None,
        before_hash: str | None,
        at: datetime,
    ) -> None:
        snapshot = _record_snapshot(row)
        session.add(
            PotentialVersionRow(
                id=str(uuid4()),
                record_id=row.id,
                version=row.version,
                payload_hash=row.payload_hash,
                snapshot_json=_json_dump(snapshot),
                operation=operation.value,
                actor_id=actor_id,
                reason=reason,
                created_at=_utc_string(at),
            )
        )
        session.add(
            PotentialAuditRow(
                id=str(uuid4()),
                record_id=row.id,
                company_id=row.company_id,
                project_id=row.project_id,
                version=row.version,
                operation=operation.value,
                actor_id=actor_id,
                before_hash=before_hash,
                after_hash=row.payload_hash,
                reason=reason,
                created_at=_utc_string(at),
            )
        )

    @staticmethod
    def _ensure_scope(session: Session, company_id: UUID, project_id: UUID) -> None:
        session.execute(
            sqlite_insert(PotentialScopeRow)
            .values(company_id=str(company_id), project_id=str(project_id))
            .on_conflict_do_nothing(index_elements=["project_id"])
        )
        existing = session.scalar(
            select(PotentialScopeRow).where(PotentialScopeRow.project_id == str(project_id))
        )
        if existing is not None:
            if existing.company_id != str(company_id):
                raise _not_found()
            return
        raise RuntimeError("潜在库企业/项目边界无法确认。")

    @staticmethod
    def _assert_scope(session: Session, company_id: UUID, project_id: UUID) -> None:
        existing = session.get(PotentialScopeRow, (str(company_id), str(project_id)))
        if existing is None:
            other_company = session.scalar(
                select(PotentialScopeRow).where(PotentialScopeRow.project_id == str(project_id))
            )
            if other_company is not None:
                raise _not_found()

    @staticmethod
    def _get_row(
        session: Session, record_id: UUID, *, company_id: UUID, project_id: UUID
    ) -> PotentialRecordRow:
        row = session.scalar(
            select(PotentialRecordRow).where(
                PotentialRecordRow.id == str(record_id),
                PotentialRecordRow.company_id == str(company_id),
                PotentialRecordRow.project_id == str(project_id),
            )
        )
        if row is None:
            raise _not_found()
        return row


def _new_record_row(
    payload: PotentialFields,
    *,
    record_id: UUID,
    actor_id: str,
    human_status: HumanStatus,
    version: int,
    now: datetime,
) -> PotentialRecordRow:
    content = _fields_content(payload)
    content.update({"human_status": human_status.value})
    return PotentialRecordRow(
        id=str(record_id),
        company_id=str(payload.company_id),
        project_id=str(payload.project_id),
        **_content_to_columns(content),
        version=version,
        payload_hash=_digest(_scoped_content(payload.company_id, payload.project_id, content)),
        created_by=actor_id,
        created_at=_utc_string(now),
        updated_at=_utc_string(now),
    )


def _new_record_payload_hash(payload: PotentialFields) -> str:
    content = _fields_content(payload)
    content["human_status"] = HumanStatus.ACCEPTED.value
    return _digest(_scoped_content(payload.company_id, payload.project_id, content))


def _fields_content(payload: PotentialFields) -> dict[str, Any]:
    return _normalize_input_values(
        payload.model_dump(
            mode="python", exclude={"company_id", "project_id", "idempotency_key"}
        )
    )


def _record_content(row: PotentialRecordRow) -> dict[str, Any]:
    return {
        "potential_type": row.potential_type,
        "claim": row.claim,
        "applicability_scope": row.applicability_scope,
        "valid_from": row.valid_from,
        "valid_until": row.valid_until,
        "task_source": row.task_source,
        "supporting_evidence": json.loads(row.supporting_evidence_json),
        "counterevidence": json.loads(row.counterevidence_json),
        "verification_method": row.verification_method,
        "evidence_status": row.evidence_status,
        "human_status": row.human_status,
    }


def _scoped_content(
    company_id: str | UUID, project_id: str | UUID, content: dict[str, Any]
) -> dict[str, Any]:
    return {"company_id": str(company_id), "project_id": str(project_id), **content}


def _content_to_columns(content: dict[str, Any]) -> dict[str, Any]:
    return {
        "potential_type": content["potential_type"],
        "claim": content["claim"],
        "applicability_scope": content["applicability_scope"],
        "valid_from": content["valid_from"],
        "valid_until": content["valid_until"],
        "task_source": content["task_source"],
        "supporting_evidence_json": _json_dump(content["supporting_evidence"]),
        "counterevidence_json": _json_dump(content["counterevidence"]),
        "verification_method": content["verification_method"],
        "human_status": content["human_status"],
        "evidence_status": content["evidence_status"],
    }


def _record_snapshot(row: PotentialRecordRow) -> dict[str, Any]:
    return {
        "id": row.id,
        "company_id": row.company_id,
        "project_id": row.project_id,
        **_record_content(row),
        "version": row.version,
        "payload_hash": row.payload_hash,
        "created_by": row.created_by,
        "created_at": row.created_at,
        "updated_at": row.updated_at,
    }


def _record_view(row: PotentialRecordRow) -> PotentialRecordView:
    return PotentialRecordView.model_validate(_record_snapshot(row))


def _version_view(row: PotentialVersionRow) -> PotentialVersionView:
    return PotentialVersionView(
        id=UUID(row.id),
        record_id=UUID(row.record_id),
        version=row.version,
        payload_hash=row.payload_hash,
        snapshot=json.loads(row.snapshot_json),
        operation=PotentialOperation(row.operation),
        actor_id=row.actor_id,
        reason=row.reason,
        created_at=_parse_utc(row.created_at),
    )


def _audit_view(row: PotentialAuditRow) -> PotentialAuditView:
    return PotentialAuditView(
        id=UUID(row.id),
        record_id=UUID(row.record_id),
        company_id=UUID(row.company_id),
        project_id=UUID(row.project_id),
        version=row.version,
        operation=PotentialOperation(row.operation),
        actor_id=row.actor_id,
        before_hash=row.before_hash,
        after_hash=row.after_hash,
        reason=row.reason,
        created_at=_parse_utc(row.created_at),
    )


def _normalize_input_values(value: Any) -> Any:
    if isinstance(value, dict):
        return {key: _normalize_input_values(item) for key, item in value.items()}
    if isinstance(value, list):
        return [_normalize_input_values(item) for item in value]
    if isinstance(value, tuple):
        return [_normalize_input_values(item) for item in value]
    if isinstance(value, datetime):
        return _utc_string(value)
    if isinstance(value, UUID):
        return str(value)
    if isinstance(value, StrEnum):
        return value.value
    return value


def _digest(value: Any) -> str:
    return sha256(_json_dump(_normalize_input_values(value)).encode("utf-8")).hexdigest()


def _json_dump(value: Any) -> str:
    return json.dumps(
        _normalize_input_values(value),
        ensure_ascii=False,
        sort_keys=True,
        separators=(",", ":"),
    )


def _utc_string(value: datetime) -> str:
    _require_aware(value, field="timestamp")
    return value.astimezone(UTC).isoformat(timespec="microseconds").replace("+00:00", "Z")


def _parse_utc(value: str | None) -> datetime | None:
    if value is None:
        return None
    parsed = datetime.fromisoformat(value.replace("Z", "+00:00"))
    return parsed.astimezone(UTC)


def _require_aware(value: datetime | None, *, field: str) -> None:
    if value is not None and (value.tzinfo is None or value.utcoffset() is None):
        raise ValueError(f"{field} 必须包含时区，建议使用 UTC 或带偏移的时间。")


def _actor_id(actor_id: str) -> str:
    actor = actor_id.strip()
    if not actor or len(actor) > 128:
        raise DomainError("ACTOR_REQUIRED", "必须提供当前操作者标识。", status_code=422)
    return actor


def _reason(reason: str | None, *, required: bool) -> str | None:
    normalized = reason.strip() if reason is not None else None
    if required and not normalized:
        raise DomainError("POTENTIAL_REASON_REQUIRED", "拒绝记录必须填写原因。", status_code=422)
    if normalized is not None and len(normalized) > 4_000:
        raise DomainError(
            "POTENTIAL_REASON_TOO_LONG", "操作说明不能超过 4000 字符。", status_code=422
        )
    return normalized or None


def _not_found() -> DomainError:
    return DomainError("POTENTIAL_RECORD_NOT_FOUND", "潜在记录不存在。", status_code=404)


def _enable_sqlite_foreign_keys(dbapi_connection: Any, _: Any) -> None:
    cursor = dbapi_connection.cursor()
    cursor.execute("PRAGMA foreign_keys=ON")
    cursor.close()
