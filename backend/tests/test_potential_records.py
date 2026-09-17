from __future__ import annotations

import json
import sqlite3
from concurrent.futures import ThreadPoolExecutor
from datetime import UTC, datetime
from pathlib import Path
from threading import Barrier
from uuid import UUID, uuid4

import pytest
from pydantic import ValidationError
from sqlalchemy import create_engine, inspect, select
from sqlalchemy.exc import IntegrityError

from enterprise_insight_backend.errors import DomainError
from enterprise_insight_backend.potential import (
    EvidenceStatus,
    HumanStatus,
    PotentialAuditRow,
    PotentialCandidateDraft,
    PotentialDatabase,
    PotentialRecordCreate,
    PotentialRecordRow,
    PotentialRecordService,
    PotentialRecordUpdate,
    PotentialScopeRow,
    PotentialType,
    PotentialVersionRow,
)


def _payload(
    company_id: UUID | None = None,
    project_id: UUID | None = None,
    **overrides: object,
) -> dict[str, object]:
    payload: dict[str, object] = {
        "company_id": company_id or uuid4(),
        "project_id": project_id or uuid4(),
        "potential_type": PotentialType.PROBLEM_HYPOTHESIS,
        "claim": "采购岗位可能承担了过多的临时审批工作。",
        "applicability_scope": "适用于制造企业的采购申请流程。",
        "valid_from": datetime(2026, 1, 1, 0, 0, tzinfo=UTC),
        "valid_until": None,
        "task_source": "task:management-review-17",
        "supporting_evidence": [
            {
                "source_ref": "interview:procurement-02#p4",
                "excerpt": "多个采购申请需要临时上交总经理审批。",
                "observed_at": "2026-09-12T10:00:00+08:00",
            }
        ],
        "counterevidence": [],
        "verification_method": "抽样核对近三个月采购申请的实际审批路径。",
        "evidence_status": EvidenceStatus.UNTESTED,
    }
    payload.update(overrides)
    return payload


def _store(database_url: str) -> tuple[PotentialDatabase, PotentialRecordService]:
    database = PotentialDatabase(database_url)
    database.create_schema()
    return database, PotentialRecordService(database)


def test_potential_store_is_physically_separate_and_foreign_keys_are_enabled(
    tmp_path: Path,
) -> None:
    formal_path = tmp_path / "formal.sqlite"
    with sqlite3.connect(formal_path) as formal:
        formal.execute("CREATE TABLE companies (id TEXT PRIMARY KEY, name TEXT NOT NULL)")
        formal.execute("INSERT INTO companies VALUES ('company-1', 'Do not copy this name')")

    potential_path = tmp_path / "potential.sqlite"
    database, service = _store(f"sqlite:///{potential_path.as_posix()}")
    company_id, project_id = uuid4(), uuid4()
    created = service.create(
        PotentialRecordCreate.model_validate(_payload(company_id, project_id)),
        actor_id="reviewer-1",
    )

    assert created.company_id == company_id
    assert created.project_id == project_id
    formal_engine = create_engine(f"sqlite:///{formal_path}")
    formal_tables = set(inspect(formal_engine).get_table_names())
    formal_engine.dispose()
    potential_tables = set(inspect(database.engine).get_table_names())
    assert formal_tables == {"companies"}
    assert "companies" not in potential_tables
    assert "projects" not in potential_tables
    assert {
        "potential_company_projects",
        "potential_records",
        "potential_record_versions",
        "potential_record_audit",
    } <= potential_tables

    with database.engine.connect() as connection:
        assert connection.exec_driver_sql("PRAGMA foreign_keys").scalar_one() == 1
        assert connection.exec_driver_sql("PRAGMA user_version").scalar_one() == 2
        foreign_keys = connection.exec_driver_sql(
            "PRAGMA foreign_key_list('potential_records')"
        ).all()
        assert len(foreign_keys) == 2  # composite (company_id, project_id) scope FK

    with sqlite3.connect(formal_path) as formal:
        assert formal.execute("SELECT name FROM companies").fetchone() == ("Do not copy this name",)
    database.dispose()


def test_potential_v1_store_upgrades_idempotency_without_losing_records(tmp_path: Path) -> None:
    database, service = _store(f"sqlite:///{(tmp_path / 'potential-v1-upgrade.sqlite').as_posix()}")
    payload = PotentialRecordCreate.model_validate(_payload())
    created = service.create(payload, actor_id="reviewer-1")

    with database.engine.begin() as connection:
        connection.exec_driver_sql("DROP INDEX uq_potential_project_idempotency")
        connection.exec_driver_sql("ALTER TABLE potential_records DROP COLUMN idempotency_key")
        connection.exec_driver_sql("PRAGMA user_version=1")

    database.create_schema()

    with database.session_factory() as session:
        stored = session.get(PotentialRecordRow, str(created.id))
        assert stored is not None
        assert stored.claim == payload.claim
        assert stored.payload_hash == created.payload_hash
        assert stored.idempotency_key is None
    with database.engine.connect() as connection:
        assert connection.exec_driver_sql("PRAGMA user_version").scalar_one() == 2
    assert "uq_potential_project_idempotency" in {
        item["name"] for item in inspect(database.engine).get_indexes("potential_records")
    }
    database.dispose()


def test_concurrent_idempotent_potential_creates_return_one_record(tmp_path: Path) -> None:
    database, service = _store(f"sqlite:///{(tmp_path / 'potential-concurrent.sqlite').as_posix()}")
    data = _payload(idempotency_key="concurrent-create-01")
    payload = PotentialRecordCreate.model_validate(data)
    barrier = Barrier(6)

    def create_same_record(_index: int):
        barrier.wait(timeout=10)
        return service.create(payload, actor_id="same-manager")

    with ThreadPoolExecutor(max_workers=6) as executor:
        records = list(executor.map(create_same_record, range(6)))

    assert len({record.id for record in records}) == 1
    history = service.history(
        records[0].id,
        company_id=records[0].company_id,
        project_id=records[0].project_id,
    )
    assert len(history.versions) == len(history.audit) == 1
    database.dispose()


def test_candidate_is_not_persisted_until_actor_confirms_bound_hash() -> None:
    database, service = _store("sqlite:///:memory:")
    data = _payload()
    candidate = PotentialCandidateDraft.model_validate(data)
    assert (
        service.list_records(
            company_id=candidate.company_id,
            project_id=candidate.project_id,
            include_history=True,
        ).total
        == 0
    )

    with pytest.raises(ValidationError):
        PotentialCandidateDraft.model_validate(
            {**data, "created_by": "model-claimed-user", "human_status": "ACCEPTED"}
        )

    with pytest.raises(DomainError) as stale:
        service.accept_candidate(candidate, expected_hash="0" * 64, actor_id="current-human")
    assert stale.value.code == "POTENTIAL_CANDIDATE_STALE"
    assert (
        service.list_records(
            company_id=candidate.company_id,
            project_id=candidate.project_id,
            include_history=True,
        ).total
        == 0
    )

    accepted = service.accept_candidate(
        candidate,
        expected_hash=candidate.payload_hash(),
        actor_id="current-human",
    )
    assert accepted.human_status == HumanStatus.ACCEPTED
    assert accepted.evidence_status == EvidenceStatus.UNTESTED
    assert accepted.created_by == "current-human"
    history = service.history(
        accepted.id, company_id=accepted.company_id, project_id=accepted.project_id
    )
    assert [version.operation.value for version in history.versions] == ["ACCEPTED"]
    assert [entry.operation.value for entry in history.audit] == ["ACCEPTED"]
    assert history.audit[0].actor_id == "current-human"
    database.dispose()


def test_status_axes_lifecycle_versions_and_audit_are_independent() -> None:
    database, service = _store("sqlite:///:memory:")
    company_id, project_id = uuid4(), uuid4()
    created = service.create(
        PotentialRecordCreate.model_validate(_payload(company_id, project_id)),
        actor_id="author",
    )
    assert created.human_status == HumanStatus.ACCEPTED
    assert created.evidence_status == EvidenceStatus.UNTESTED

    edited = service.edit(
        created.id,
        PotentialRecordUpdate.model_validate(
            {
                "expected_version": 1,
                "evidence_status": EvidenceStatus.SUPPORTED,
                "counterevidence": [
                    {
                        "source_ref": "report:approval-sample#12",
                        "excerpt": "抽查样本未发现额外的越级审批。",
                        "observed_at": "2026-09-12T03:00:00Z",
                    }
                ],
            }
        ),
        company_id=company_id,
        project_id=project_id,
        actor_id="analyst",
        reason="补入一次反例核对。",
    )
    assert edited.version == 2
    assert edited.human_status == HumanStatus.ACCEPTED
    assert edited.evidence_status == EvidenceStatus.SUPPORTED
    assert len(edited.counterevidence) == 1

    rejected = service.reject(
        edited.id,
        company_id=company_id,
        project_id=project_id,
        expected_version=2,
        actor_id="manager",
        reason="目前证据不足以将其作为可复用的管理认识。",
    )
    assert rejected.human_status == HumanStatus.REJECTED
    assert rejected.evidence_status == EvidenceStatus.SUPPORTED

    reaccepted = service.accept(
        rejected.id,
        company_id=company_id,
        project_id=project_id,
        expected_version=3,
        actor_id="manager-2",
        reason="补充验证后重新认可。",
    )
    assert reaccepted.human_status == HumanStatus.ACCEPTED
    assert reaccepted.evidence_status == EvidenceStatus.SUPPORTED

    withdrawn = service.withdraw(
        reaccepted.id,
        company_id=company_id,
        project_id=project_id,
        expected_version=4,
        actor_id="manager-2",
        reason="适用基线已变化。",
    )
    assert withdrawn.human_status == HumanStatus.WITHDRAWN
    assert withdrawn.evidence_status == EvidenceStatus.SUPPORTED
    assert service.list_records(company_id=company_id, project_id=project_id).total == 0
    assert (
        service.list_records(
            company_id=company_id,
            project_id=project_id,
            include_history=True,
        ).total
        == 1
    )

    history = service.history(created.id, company_id=company_id, project_id=project_id)
    operations = [item.operation.value for item in history.versions]
    assert operations == ["CREATED", "EDITED", "REJECTED", "ACCEPTED", "WITHDRAWN"]
    assert [item.operation.value for item in history.audit] == operations
    assert [item.version for item in history.versions] == [1, 2, 3, 4, 5]
    assert [item.actor_id for item in history.audit] == [
        "author",
        "analyst",
        "manager",
        "manager-2",
        "manager-2",
    ]
    for index, audit in enumerate(history.audit):
        expected_before = history.versions[index - 1].payload_hash if index else None
        assert audit.before_hash == expected_before
        assert audit.after_hash == history.versions[index].payload_hash
    assert history.versions[0].snapshot["evidence_status"] == "UNTESTED"
    assert history.versions[2].snapshot["human_status"] == "REJECTED"
    assert history.versions[2].snapshot["evidence_status"] == "SUPPORTED"
    assert json.loads(json.dumps(history.versions[0].snapshot, ensure_ascii=False))
    assert history.versions[0].snapshot["supporting_evidence"][0]["observed_at"] == (
        "2026-09-12T02:00:00.000000Z"
    )
    database.dispose()


def test_refuted_evidence_does_not_appear_as_current_positive_knowledge() -> None:
    database, service = _store(":memory:")
    company_id, project_id = uuid4(), uuid4()
    created = service.create(
        PotentialRecordCreate.model_validate(_payload(company_id, project_id)),
        actor_id="author",
    )
    refuted = service.edit(
        created.id,
        PotentialRecordUpdate.model_validate(
            {"expected_version": 1, "evidence_status": EvidenceStatus.REFUTED}
        ),
        company_id=company_id,
        project_id=project_id,
        actor_id="reviewer",
        reason="出现可复核的反证。",
    )
    assert refuted.human_status == HumanStatus.ACCEPTED
    assert refuted.evidence_status == EvidenceStatus.REFUTED
    assert service.list_records(company_id=company_id, project_id=project_id).total == 0
    assert (
        service.list_records(
            company_id=company_id,
            project_id=project_id,
            include_history=True,
        ).total
        == 1
    )
    database.dispose()


def test_optimistic_conflict_and_company_project_boundaries() -> None:
    database, service = _store("sqlite:///:memory:")
    company_a, project_a = uuid4(), uuid4()
    company_b, project_b = uuid4(), uuid4()
    created = service.create(
        PotentialRecordCreate.model_validate(_payload(company_a, project_a)),
        actor_id="author",
    )
    service.edit(
        created.id,
        PotentialRecordUpdate.model_validate(
            {"expected_version": 1, "claim": "经人工核实后的新主张。"}
        ),
        company_id=company_a,
        project_id=project_a,
        actor_id="editor",
    )

    with pytest.raises(DomainError) as conflict:
        service.edit(
            created.id,
            PotentialRecordUpdate.model_validate(
                {"expected_version": 1, "claim": "过期版本不能覆盖新版本。"}
            ),
            company_id=company_a,
            project_id=project_a,
            actor_id="late-editor",
        )
    assert conflict.value.status_code == 409
    assert conflict.value.code == "REVISION_CONFLICT"

    for company_id, project_id in ((company_b, project_a), (company_a, project_b)):
        with pytest.raises(DomainError) as outside:
            service.get(created.id, company_id=company_id, project_id=project_id)
        assert outside.value.status_code == 404

    with pytest.raises(DomainError) as wrong_company_binding:
        service.create(
            PotentialRecordCreate.model_validate(_payload(company_b, project_a)),
            actor_id="other-company",
        )
    assert wrong_company_binding.value.status_code == 404
    history = service.history(created.id, company_id=company_a, project_id=project_a)
    assert len(history.versions) == len(history.audit) == 2
    database.dispose()


def test_foreign_key_and_failed_history_write_roll_back_business_record(
    tmp_path: Path,
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    database, service = _store(f"sqlite:///{(tmp_path / 'rollback.sqlite').as_posix()}")
    company_id, project_id = uuid4(), uuid4()

    with pytest.raises(IntegrityError):
        with database.session_factory.begin() as session:
            session.add(
                PotentialRecordRow(
                    id=str(uuid4()),
                    company_id=str(company_id),
                    project_id=str(project_id),
                    potential_type="PROBLEM_HYPOTHESIS",
                    claim="invalid scope",
                    applicability_scope="invalid scope",
                    valid_from=None,
                    valid_until=None,
                    task_source="task:test",
                    supporting_evidence_json="[]",
                    counterevidence_json="[]",
                    verification_method="尚未定义",
                    human_status="ACCEPTED",
                    evidence_status="UNTESTED",
                    version=1,
                    payload_hash="f" * 64,
                    created_by="test",
                    created_at="2026-09-12T00:00:00.000000Z",
                    updated_at="2026-09-12T00:00:00.000000Z",
                )
            )
            session.flush()

    def fail_history(*args: object, **kwargs: object) -> None:
        raise RuntimeError("simulate a failure after the record insert")

    monkeypatch.setattr(service, "_append_history", fail_history)
    with pytest.raises(RuntimeError, match="simulate a failure"):
        service.create(
            PotentialRecordCreate.model_validate(_payload(company_id, project_id)),
            actor_id="author",
        )

    with database.session_factory() as session:
        assert session.scalars(select(PotentialRecordRow)).all() == []
        assert session.scalars(select(PotentialScopeRow)).all() == []
        assert session.scalars(select(PotentialVersionRow)).all() == []
        assert session.scalars(select(PotentialAuditRow)).all() == []
    database.dispose()


def test_memory_database_persists_across_service_sessions_and_requires_stable_time() -> None:
    database, service = _store(":memory:")
    data = _payload()
    created = service.create(PotentialRecordCreate.model_validate(data), actor_id="author")
    loaded = service.get(created.id, company_id=created.company_id, project_id=created.project_id)
    assert loaded.id == created.id
    assert loaded.created_at.tzinfo == UTC

    with pytest.raises(ValidationError):
        PotentialRecordCreate.model_validate(_payload(valid_from=datetime(2026, 1, 1)))
    database.dispose()
