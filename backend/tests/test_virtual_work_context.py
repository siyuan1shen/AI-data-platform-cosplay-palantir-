from __future__ import annotations

from datetime import UTC, datetime
from hashlib import sha256
from uuid import UUID, uuid4

import pytest
from sqlalchemy import create_engine, event
from sqlalchemy.orm import Session, sessionmaker
from sqlalchemy.pool import StaticPool

from enterprise_insight_backend.observations import (
    ManagementObservationRow,
    ObservationVersionRow,
)
from enterprise_insight_backend.virtual_work import (
    Base,
    RealAnchorRow,
    VirtualWorkAssertionEvidenceRow,
    VirtualWorkAssertionRow,
    VirtualWorkEdgeRow,
    VirtualWorkEvidenceRow,
    VirtualWorkModelRow,
    VirtualWorkNodeRow,
    VirtualWorkReviewRow,
    VirtualWorkRevisionRow,
)
from enterprise_insight_backend.virtual_work_context import (
    read_reviewed_virtual_work_context,
    virtual_work_context_fingerprint,
)

_NOW = datetime(2026, 9, 13, 8, 0, tzinfo=UTC)


@pytest.fixture
def db_session() -> Session:
    engine = create_engine(
        "sqlite://",
        connect_args={"check_same_thread": False},
        poolclass=StaticPool,
    )

    @event.listens_for(engine, "connect")
    def enable_foreign_keys(connection, _record) -> None:
        cursor = connection.cursor()
        cursor.execute("PRAGMA foreign_keys=ON")
        cursor.close()

    Base.metadata.create_all(engine)
    session = sessionmaker(bind=engine, expire_on_commit=False)()
    try:
        yield session
    finally:
        session.close()
        engine.dispose()


def _observation(
    session: Session,
    company_id: UUID,
    project_id: UUID,
    *,
    content: str = "采购岗位负责审批供应商准入。",
    status: str = "ACTIVE",
    revision: int = 1,
    snapshot_content: str | None = None,
    snapshot_status: str | None = None,
) -> tuple[UUID, str]:
    observation_id = uuid4()
    session.add(
        ManagementObservationRow(
            id=str(observation_id),
            company_id=str(company_id),
            project_id=str(project_id),
            kind="INTERVIEW",
            title="岗位访谈",
            content=content,
            content_sha256=sha256(content.encode("utf-8")).hexdigest(),
            occurred_at=None,
            submitted_by="manager",
            status=status,
            revision=revision,
            idempotency_key=None,
            created_at=_NOW,
            updated_at=_NOW,
        )
    )
    session.flush()
    source_content = snapshot_content if snapshot_content is not None else content
    session.add(
        ObservationVersionRow(
            id=str(uuid4()),
            observation_id=str(observation_id),
            revision=revision,
            snapshot={
                "kind": "INTERVIEW",
                "title": "岗位访谈",
                "content": source_content,
                "status": snapshot_status if snapshot_status is not None else status,
            },
            operation="CREATED",
            actor_id="manager",
            created_at=_NOW,
        )
    )
    session.flush()
    return observation_id, content


def _add_model(
    session: Session,
    company_id: UUID,
    project_id: UUID,
    release_id: UUID,
    entity_id: UUID,
    *,
    name: str = "岗位工作虚模",
    latest_decision: str | None = "REVIEWED",
    version: int = 1,
    source_ref: str | None = None,
    excerpt: str | None = None,
    add_edge: bool = True,
    extra_anchors: list[tuple[UUID, UUID, str]] | None = None,
) -> tuple[str, str, str]:
    model_id, revision_id = str(uuid4()), str(uuid4())
    node_id, target_node_id = str(uuid4()), str(uuid4())
    revision_hash = sha256(f"{model_id}/{version}".encode()).hexdigest()
    session.add(
        VirtualWorkModelRow(
            id=model_id,
            company_id=str(company_id),
            project_id=str(project_id),
            created_at=_NOW,
        )
    )
    session.add(
        VirtualWorkRevisionRow(
            id=revision_id,
            model_id=model_id,
            version=version,
            name=name,
            description="经审阅的岗位职责与流程模型。",
            operation="CREATED",
            change_summary="创建模型",
            change_payload={},
            revision_hash=revision_hash,
            created_by="fde",
            created_at=_NOW,
        )
    )
    session.flush()
    session.add_all(
        [
            VirtualWorkNodeRow(
                model_id=model_id,
                revision_id=revision_id,
                id=node_id,
                node_type="POSITION",
                label="采购专员",
                properties={"purpose": "供应商准入"},
                created_at=_NOW,
            ),
            VirtualWorkNodeRow(
                model_id=model_id,
                revision_id=revision_id,
                id=target_node_id,
                node_type="ACTIVITY",
                label="审批供应商",
                properties={},
                created_at=_NOW,
            ),
        ]
    )
    session.flush()
    session.add(
        RealAnchorRow(
            model_id=model_id,
            revision_id=revision_id,
            id=str(uuid4()),
            virtual_node_id=node_id,
            real_entity_id=str(entity_id),
            real_release_id=str(release_id),
            relation_kind="CORRESPONDS_TO",
            support_ref="岗位目录",
            status="ACTIVE",
            created_at=_NOW,
        )
    )
    for anchor_entity, anchor_release, anchor_status in extra_anchors or []:
        session.add(
            RealAnchorRow(
                model_id=model_id,
                revision_id=revision_id,
                id=str(uuid4()),
                virtual_node_id=node_id,
                real_entity_id=str(anchor_entity),
                real_release_id=str(anchor_release),
                relation_kind="REFINES",
                support_ref="额外锚点",
                status=anchor_status,
                created_at=_NOW,
            )
        )
    if add_edge:
        session.add(
            VirtualWorkEdgeRow(
                model_id=model_id,
                revision_id=revision_id,
                id=str(uuid4()),
                source_node_id=node_id,
                target_node_id=target_node_id,
                edge_type="POSITION_PERFORMS_ACTIVITY",
                label="执行",
                properties={},
                created_at=_NOW,
            )
        )
    if source_ref is not None and excerpt is not None:
        assertion_id, evidence_id = str(uuid4()), str(uuid4())
        session.add(
            VirtualWorkAssertionRow(
                model_id=model_id,
                revision_id=revision_id,
                id=assertion_id,
                subject_kind="NODE",
                subject_id=node_id,
                field_name="responsibility",
                value_json="审批供应商准入",
                assertion_kind="REPORTED",
                scope={},
                valid_from=None,
                valid_to=None,
                method="访谈确认",
                created_at=_NOW,
            )
        )
        session.add(
            VirtualWorkEvidenceRow(
                model_id=model_id,
                revision_id=revision_id,
                id=evidence_id,
                evidence_kind="INTERVIEW",
                source_ref=source_ref,
                excerpt=excerpt,
                source_root_id=source_ref.split("/")[2]
                if source_ref.startswith("observation://")
                else str(uuid4()),
                captured_at=None,
                created_at=_NOW,
            )
        )
        session.flush()
        session.add(
            VirtualWorkAssertionEvidenceRow(
                model_id=model_id,
                revision_id=revision_id,
                assertion_id=assertion_id,
                evidence_id=evidence_id,
            )
        )
    if latest_decision is not None:
        session.add(
            VirtualWorkReviewRow(
                model_id=model_id,
                revision_id=revision_id,
                id=str(uuid4()),
                sequence=1,
                version=version,
                revision_hash=revision_hash,
                confirmed_scope={
                    "company_id": str(company_id),
                    "project_id": str(project_id),
                    "virtual_work_model_id": model_id,
                },
                confirmation_kind="MANAGER_CONFIRMATION",
                decision=latest_decision,
                actor_id="manager",
                reason="核实完成",
                created_at=_NOW,
            )
        )
    session.flush()
    return model_id, revision_id, node_id


def _append_revision(
    session: Session,
    model_id: str,
    company_id: UUID,
    project_id: UUID,
    *,
    version: int,
    decision: str | None,
) -> str:
    revision_id = str(uuid4())
    revision_hash = sha256(f"{model_id}/{version}".encode()).hexdigest()
    session.add(
        VirtualWorkRevisionRow(
            id=revision_id,
            model_id=model_id,
            version=version,
            name="新修订",
            description=None,
            operation="UPDATED",
            change_summary="新增修订",
            change_payload={},
            revision_hash=revision_hash,
            created_by="fde",
            created_at=_NOW,
        )
    )
    session.flush()
    if decision is not None:
        session.add(
            VirtualWorkReviewRow(
                model_id=model_id,
                revision_id=revision_id,
                id=str(uuid4()),
                sequence=1,
                version=version,
                revision_hash=revision_hash,
                confirmed_scope={
                    "company_id": str(company_id),
                    "project_id": str(project_id),
                    "virtual_work_model_id": model_id,
                },
                confirmation_kind="MANAGER_CONFIRMATION",
                decision=decision,
                actor_id="manager",
                reason="最新版本状态",
                created_at=_NOW,
            )
        )
        session.flush()
    return revision_id


def _read(
    session: Session,
    company_id: UUID,
    project_id: UUID,
    release_id: UUID,
    entity_ids: list[UUID],
    **kwargs,
) -> dict:
    return read_reviewed_virtual_work_context(
        session,
        company_id=company_id,
        project_id=project_id,
        release_id=release_id,
        allowed_real_entity_ids=entity_ids,
        **kwargs,
    )


def _reason_counts(result: dict) -> dict[str, int]:
    return {item["reason"]: item["count"] for item in result["coverage"]["skip_reasons"]}


def test_exact_release_tenant_and_entity_anchor_scope(db_session: Session) -> None:
    company_id, project_id, release_id, entity_id = uuid4(), uuid4(), uuid4(), uuid4()
    observation_id, content = _observation(db_session, company_id, project_id)
    current = _add_model(
        db_session,
        company_id,
        project_id,
        release_id,
        entity_id,
        source_ref=f"observation://{observation_id}/1",
        excerpt="采购岗位负责审批供应商准入。",
        extra_anchors=[
            (uuid4(), release_id, "ACTIVE"),
            (entity_id, uuid4(), "ACTIVE"),
            (entity_id, release_id, "WITHDRAWN"),
        ],
    )
    _add_model(db_session, uuid4(), project_id, release_id, entity_id, name="异公司")
    _add_model(db_session, company_id, uuid4(), release_id, entity_id, name="异项目")
    _add_model(db_session, company_id, project_id, uuid4(), entity_id, name="旧 release")
    _add_model(db_session, company_id, project_id, release_id, uuid4(), name="未允许实体")

    result = _read(db_session, company_id, project_id, release_id, [entity_id])

    assert [item["model_id"] for item in result["models"]] == [current[0]]
    assert result["models"][0]["revision_id"] == current[1]
    assert result["models"][0]["anchors"] == [
            {
                "id": result["models"][0]["anchors"][0]["id"],
                "reference_uri": result["models"][0]["anchors"][0]["reference_uri"],
                "virtual_node_id": current[2],
            "real_entity_id": str(entity_id),
            "real_release_id": str(release_id),
            "relation_kind": "CORRESPONDS_TO",
            "support_ref": "岗位目录",
        }
    ]
    assert result["coverage"]["models_considered"] == 3
    assert result["coverage"]["status"] == "PARTIAL"
    assert result["models"][0]["nodes"][0]["field_assertions"][0]["value"] == "审批供应商准入"
    assert result["models"][0]["nodes"][0]["field_assertions"][0]["source_refs"][0][
        "excerpt"
    ] in content
    assert result["is_formal_fact"] is False
    assert result["actions_executed"] is False


def test_virtual_read_contains_only_one_hop_around_exact_anchor(db_session: Session) -> None:
    company_id, project_id, release_id, entity_id = uuid4(), uuid4(), uuid4(), uuid4()
    observation_id, _ = _observation(db_session, company_id, project_id)
    model_id, revision_id, anchor_node_id = _add_model(
        db_session,
        company_id,
        project_id,
        release_id,
        entity_id,
        source_ref=f"observation://{observation_id}/1",
        excerpt="采购岗位负责审批供应商准入。",
    )
    direct_neighbour_id = db_session.query(VirtualWorkEdgeRow).filter_by(
        model_id=model_id, revision_id=revision_id
    ).one().target_node_id
    second_hop_id = str(uuid4())
    unrelated_node_id = str(uuid4())
    db_session.add_all(
        [
            VirtualWorkNodeRow(
                model_id=model_id,
                revision_id=revision_id,
                id=second_hop_id,
                node_type="ACTIVITY",
                label="复核供应商",
                properties={},
                created_at=_NOW,
            ),
            VirtualWorkNodeRow(
                model_id=model_id,
                revision_id=revision_id,
                id=unrelated_node_id,
                node_type="POSITION",
                label="财务经理",
                properties={},
                created_at=_NOW,
            ),
        ]
    )
    db_session.flush()
    db_session.add_all(
        [
            VirtualWorkEdgeRow(
                model_id=model_id,
                revision_id=revision_id,
                id=str(uuid4()),
                source_node_id=direct_neighbour_id,
                target_node_id=second_hop_id,
                edge_type="ACTIVITY_FOLLOWS_ACTIVITY",
                label="后续步骤",
                properties={},
                created_at=_NOW,
            ),
            VirtualWorkEdgeRow(
                model_id=model_id,
                revision_id=revision_id,
                id=str(uuid4()),
                source_node_id=second_hop_id,
                target_node_id=unrelated_node_id,
                edge_type="ACTIVITY_OWNED_BY_POSITION",
                label="由岗位负责",
                properties={},
                created_at=_NOW,
            ),
        ]
    )
    db_session.flush()

    result = _read(db_session, company_id, project_id, release_id, [entity_id])
    included_node_ids = {node["id"] for node in result["models"][0]["nodes"]}
    included_edge_ids = {edge["id"] for edge in result["models"][0]["edges"]}

    assert included_node_ids == {anchor_node_id, direct_neighbour_id}
    assert second_hop_id not in included_node_ids
    assert unrelated_node_id not in included_node_ids
    assert len(included_edge_ids) == 1
    assert result["models"][0]["nodes"][0]["design_metadata_trust"] == (
        "HUMAN_REVIEWED_MODEL_NOT_SOURCE_ASSERTION"
    )
    assert result["read_manifest"]["virtual_revision_refs"] == [
        result["models"][0]["revision_ref"]
    ]
    assert result["read_manifest"]["virtual_assertion_refs"]


@pytest.mark.parametrize("latest_decision", [None, "REJECTED", "WITHDRAWN"])
def test_latest_prior_reviewed_revision_is_returned_when_newer_revision_is_unreviewed(
    db_session: Session, latest_decision: str | None
) -> None:
    company_id, project_id, release_id, entity_id = uuid4(), uuid4(), uuid4(), uuid4()
    older = _add_model(
        db_session,
        company_id,
        project_id,
        release_id,
        entity_id,
        name="未审模型",
    )
    _append_revision(
        db_session,
        older[0],
        company_id,
        project_id,
        version=2,
        decision=latest_decision,
    )
    latest_reviewed = _add_model(
        db_session, company_id, project_id, release_id, entity_id, name="已审模型"
    )

    result = _read(db_session, company_id, project_id, release_id, [entity_id])

    assert {item["model_id"] for item in result["models"]} == {
        older[0],
        latest_reviewed[0],
    }
    assert next(item for item in result["models"] if item["model_id"] == older[0])[
        "revision_version"
    ] == 1
    assert result["coverage"]["models_included"] == 2
    assert "latest_revision_not_reviewed" not in _reason_counts(result)


@pytest.mark.parametrize(
    ("source_mode", "expected_reason"),
    [
        ("missing", "source_observation_missing"),
        ("revision_changed", "source_revision_stale"),
        ("withdrawn", "source_observation_inactive"),
        ("excerpt_changed", "source_excerpt_mismatch"),
        ("tenant_mismatch", "source_observation_scope_mismatch"),
        ("version_missing", "source_revision_missing"),
    ],
)
def test_stale_or_unverifiable_observation_source_excludes_assertion(
    db_session: Session, source_mode: str, expected_reason: str
) -> None:
    company_id, project_id, release_id, entity_id = uuid4(), uuid4(), uuid4(), uuid4()
    observation_company = uuid4() if source_mode == "tenant_mismatch" else company_id
    observation_project = uuid4() if source_mode == "tenant_mismatch" else project_id
    if source_mode == "missing":
        observation_id, _content = uuid4(), ""
        source_ref = f"observation://{observation_id}/1"
        excerpt = "采购岗位负责审批供应商准入。"
    else:
        status = "WITHDRAWN" if source_mode == "withdrawn" else "ACTIVE"
        revision = 2 if source_mode == "revision_changed" else 1
        snapshot_content = "其他内容" if source_mode == "excerpt_changed" else None
        observation_id, _content = _observation(
            db_session,
            observation_company,
            observation_project,
            status=status,
            revision=revision,
            snapshot_content=snapshot_content,
        )
        source_ref = f"observation://{observation_id}/1"
        if source_mode == "withdrawn":
            source_ref = f"observation://{observation_id}/1"
        if source_mode == "version_missing":
            db_session.query(ObservationVersionRow).filter_by(
                observation_id=str(observation_id)
            ).delete()
            db_session.flush()
        excerpt = "采购岗位负责审批供应商准入。"
    _add_model(
        db_session,
        company_id,
        project_id,
        release_id,
        entity_id,
        source_ref=source_ref,
        excerpt=excerpt,
    )

    result = _read(db_session, company_id, project_id, release_id, [entity_id])

    node = result["models"][0]["nodes"][0]
    assert node["field_assertions"] == []
    assert expected_reason in _reason_counts(result)
    assert result["coverage"]["source_refs_checked"] == 1
    assert result["coverage"]["source_refs_stale_or_invalid"] == 1
    assert any(risk["code"] == "SKIPPED_OR_STALE_VIRTUAL_CONTENT" for risk in result["risks"])


def test_multiple_models_and_output_limits_are_reported(db_session: Session) -> None:
    company_id, project_id, release_id, entity_id = uuid4(), uuid4(), uuid4(), uuid4()
    observation_id, _ = _observation(db_session, company_id, project_id)
    for suffix in ("一", "二"):
        _add_model(
            db_session,
            company_id,
            project_id,
            release_id,
            entity_id,
            name=f"模型{suffix}",
            source_ref=f"observation://{observation_id}/1",
            excerpt="采购岗位负责审批供应商准入。",
        )

    result = _read(
        db_session,
        company_id,
        project_id,
        release_id,
        [entity_id],
        limits={"models": 1, "anchors": 1, "nodes": 1, "edges": 0, "assertions": 0},
    )

    assert len(result["models"]) == 1
    assert len(result["models"][0]["nodes"]) == 1
    assert result["models"][0]["edges"] == []
    assert result["models"][0]["nodes"][0]["field_assertions"] == []
    assert result["coverage"]["truncated"] is True
    reasons = {item["reason"] for item in result["coverage"]["truncation_reasons"]}
    assert {
        "model_limit",
        "node_limit",
        "edge_limit_or_unselected_endpoint",
        "assertion_limit",
    } <= reasons
    assert any(risk["code"] == "CONTEXT_TRUNCATED" for risk in result["risks"])


@pytest.mark.parametrize(
    "kwargs",
    [
        {"company_id": None},
        {"project_id": "not-a-uuid"},
        {"release_id": None},
        {"allowed_real_entity_ids": []},
        {"allowed_real_entity_ids": "model supplied id"},
        {"allowed_real_entity_ids": ["not-a-uuid"]},
    ],
)
def test_empty_or_invalid_scope_fails_closed(db_session: Session, kwargs: dict) -> None:
    company_id, project_id, release_id, entity_id = uuid4(), uuid4(), uuid4(), uuid4()
    _add_model(db_session, company_id, project_id, release_id, entity_id)
    args = {
        "company_id": company_id,
        "project_id": project_id,
        "release_id": release_id,
        "allowed_real_entity_ids": [entity_id],
    }
    args.update(kwargs)

    result = read_reviewed_virtual_work_context(db_session, **args)

    assert result["models"] == []
    assert result["coverage"]["status"] == "EMPTY"
    assert result["coverage"]["skip_reasons"] == [
        {"reason": "invalid_or_empty_scope", "count": 1}
    ]
    assert result["risks"][0]["code"] == "FAIL_CLOSED_INVALID_SCOPE"


def test_source_ref_format_must_be_exact(db_session: Session) -> None:
    company_id, project_id, release_id, entity_id = uuid4(), uuid4(), uuid4(), uuid4()
    observation_id, _ = _observation(db_session, company_id, project_id)
    _add_model(
        db_session,
        company_id,
        project_id,
        release_id,
        entity_id,
        source_ref=f"observation://{observation_id}/01",
        excerpt="采购岗位负责审批供应商准入。",
    )

    result = _read(db_session, company_id, project_id, release_id, [entity_id])

    assert result["models"][0]["nodes"][0]["field_assertions"] == []
    assert _reason_counts(result)["source_ref_invalid"] == 1


def test_one_stale_source_invalidates_entire_field_assertion(db_session: Session) -> None:
    company_id, project_id, release_id, entity_id = uuid4(), uuid4(), uuid4(), uuid4()
    good_observation_id, _ = _observation(db_session, company_id, project_id)
    stale_observation_id, _ = _observation(
        db_session, company_id, project_id, revision=2
    )
    model_id, revision_id, _node_id = _add_model(
        db_session,
        company_id,
        project_id,
        release_id,
        entity_id,
        source_ref=f"observation://{good_observation_id}/1",
        excerpt="采购岗位负责审批供应商准入。",
    )
    assertion = (
        db_session.query(VirtualWorkAssertionRow)
        .filter_by(model_id=model_id, revision_id=revision_id)
        .one()
    )
    evidence_id = str(uuid4())
    db_session.add(
        VirtualWorkEvidenceRow(
            model_id=model_id,
            revision_id=revision_id,
            id=evidence_id,
            evidence_kind="INTERVIEW",
            source_ref=f"observation://{stale_observation_id}/1",
            excerpt="采购岗位负责审批供应商准入。",
            source_root_id=str(stale_observation_id),
            captured_at=None,
            created_at=_NOW,
        )
    )
    db_session.flush()
    db_session.add(
        VirtualWorkAssertionEvidenceRow(
            model_id=model_id,
            revision_id=revision_id,
            assertion_id=assertion.id,
            evidence_id=evidence_id,
        )
    )
    db_session.flush()

    result = _read(db_session, company_id, project_id, release_id, [entity_id])

    assert result["models"][0]["nodes"][0]["field_assertions"] == []
    assert result["coverage"]["source_refs_checked"] == 2
    assert result["coverage"]["source_refs_stale_or_invalid"] == 1
    assert result["coverage"]["assertions_skipped"] == 1


def test_limits_hard_cap_untrusted_request_values(db_session: Session) -> None:
    result = read_reviewed_virtual_work_context(
        db_session,
        company_id=uuid4(),
        project_id=uuid4(),
        release_id=uuid4(),
        allowed_real_entity_ids=[uuid4()],
        limits={"models": 99_999},
    )

    assert result["models"] == []
    assert result["coverage"]["truncated"] is True
    assert {item["reason"] for item in result["coverage"]["truncation_reasons"]} == {
        "models_hard_cap"
    }


def test_reader_does_not_modify_observation_or_virtual_rows(db_session: Session) -> None:
    company_id, project_id, release_id, entity_id = uuid4(), uuid4(), uuid4(), uuid4()
    observation_id, _ = _observation(db_session, company_id, project_id)
    _add_model(
        db_session,
        company_id,
        project_id,
        release_id,
        entity_id,
        source_ref=f"observation://{observation_id}/1",
        excerpt="采购岗位负责审批供应商准入。",
    )
    before = (
        db_session.query(ManagementObservationRow).count(),
        db_session.query(VirtualWorkModelRow).count(),
        db_session.query(VirtualWorkRevisionRow).count(),
    )

    result = _read(db_session, company_id, project_id, release_id, [entity_id])

    after = (
        db_session.query(ManagementObservationRow).count(),
        db_session.query(VirtualWorkModelRow).count(),
        db_session.query(VirtualWorkRevisionRow).count(),
    )
    assert result["models"]
    assert after == before


def test_virtual_readset_fingerprint_changes_when_source_is_withdrawn(
    db_session: Session,
) -> None:
    company_id, project_id, release_id, entity_id = uuid4(), uuid4(), uuid4(), uuid4()
    observation_id, _ = _observation(db_session, company_id, project_id)
    _add_model(
        db_session,
        company_id,
        project_id,
        release_id,
        entity_id,
        source_ref=f"observation://{observation_id}/1",
        excerpt="采购岗位负责审批供应商准入。",
    )
    first = _read(db_session, company_id, project_id, release_id, [entity_id])
    repeated = _read(db_session, company_id, project_id, release_id, [entity_id])
    assert virtual_work_context_fingerprint(first) == virtual_work_context_fingerprint(repeated)

    observation = db_session.get(ManagementObservationRow, str(observation_id))
    assert observation is not None
    observation.status = "WITHDRAWN"
    db_session.flush()
    changed = _read(db_session, company_id, project_id, release_id, [entity_id])

    assert virtual_work_context_fingerprint(changed) != virtual_work_context_fingerprint(first)
    assert changed["coverage"]["assertions_included"] == 0
    assert changed["coverage"]["source_refs_stale_or_invalid"] == 1
