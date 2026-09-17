from __future__ import annotations

import json
from datetime import UTC, datetime, timedelta, timezone
from uuid import UUID, uuid4

import pytest
from pydantic import ValidationError
from sqlalchemy import create_engine, event, inspect, select, text
from sqlalchemy.exc import DBAPIError
from sqlalchemy.orm import Session, sessionmaker
from sqlalchemy.pool import StaticPool

from enterprise_insight_backend.errors import DomainError
from enterprise_insight_backend.models import Base as FormalBase
from enterprise_insight_backend.virtual_work import (
    AnchorRelationKind,
    AssertionKind,
    Base,
    ConfirmationKind,
    ConfirmedScope,
    EvidenceCreate,
    EvidenceKind,
    FieldAssertionCreate,
    RealAnchorCreate,
    RealAnchorRow,
    ReviewDecision,
    VirtualEdgeCreate,
    VirtualEdgeType,
    VirtualEdgeUpdate,
    VirtualNodeCreate,
    VirtualNodeType,
    VirtualNodeUpdate,
    VirtualRevisionStatus,
    VirtualWorkEdgeRow,
    VirtualWorkModelCreate,
    VirtualWorkNodeRow,
    VirtualWorkRetireCreate,
    VirtualWorkReviewCreate,
    VirtualWorkReviewRow,
    VirtualWorkRevisionRow,
    VirtualWorkService,
    _revision_hash,
)


@pytest.fixture
def store() -> tuple[Session, object]:
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
        yield session, engine
    finally:
        session.close()
        engine.dispose()


def _evidence(kind: EvidenceKind, *, suffix: str = "a") -> EvidenceCreate:
    return EvidenceCreate(
        evidence_kind=kind,
        source_ref=f"case://{suffix}",
        excerpt=f"可追溯的 {kind.value} 材料片段。",
        captured_at=datetime(2026, 9, 1, tzinfo=UTC) if kind is EvidenceKind.SYSTEM_EVENT else None,
    )


def _assertion(
    kind: AssertionKind = AssertionKind.NORMATIVE,
    *,
    field_name: str = "purpose",
    value: str = "处理业务任务",
) -> FieldAssertionCreate:
    evidence_by_kind = {
        AssertionKind.NORMATIVE: [EvidenceKind.NORMATIVE_DOCUMENT],
        AssertionKind.OBSERVED_PRACTICE: [EvidenceKind.DIRECT_OBSERVATION],
        AssertionKind.SYSTEM_EVENT: [EvidenceKind.SYSTEM_EVENT],
        AssertionKind.REPORTED: [EvidenceKind.INTERVIEW],
        AssertionKind.INFERRED: [EvidenceKind.INFERENCE_BASIS, EvidenceKind.REPORT],
    }
    return FieldAssertionCreate(
        field_name=field_name,
        value=value,
        assertion_kind=kind,
        evidence=[
            _evidence(item, suffix=f"{field_name}-{index}")
            for index, item in enumerate(evidence_by_kind[kind])
        ],
        method="依据两项来源进行对照推导。" if kind is AssertionKind.INFERRED else None,
    )


def _create_model(
    service: VirtualWorkService,
    company_id: UUID | None = None,
    project_id: UUID | None = None,
    *,
    name: str = "岗位工作虚模",
):
    company_id = company_id or uuid4()
    project_id = project_id or uuid4()
    result = service.create_model(
        VirtualWorkModelCreate(
            company_id=company_id,
            project_id=project_id,
            name=name,
            description="FDE 现场细节的独立版本化模型。",
        ),
        actor_id="fde-user",
    )
    return company_id, project_id, result


def _review(
    service: VirtualWorkService,
    company_id: UUID,
    project_id: UUID,
    revision,
    *,
    actor_id: str = "reviewer",
    decision: ReviewDecision = ReviewDecision.REVIEWED,
    confirmed_scope: ConfirmedScope | None = None,
    revision_hash: str | None = None,
):
    scope = confirmed_scope or ConfirmedScope(
        company_id=company_id,
        project_id=project_id,
        virtual_work_model_id=revision.virtual_work_model_id,
    )
    return service.review_revision(
        company_id,
        project_id,
        revision.virtual_work_model_id,
        VirtualWorkReviewCreate(
            revision_id=revision.revision_id,
            version=revision.version,
            revision_hash=revision_hash or revision.revision_hash,
            confirmed_scope=scope,
            confirmation_kind=ConfirmationKind.HUMAN_REVIEW,
            decision=decision,
            reason="逐项核对来源与范围。",
        ),
        actor_id=actor_id,
    )


def _add_node(
    service: VirtualWorkService,
    company_id: UUID,
    project_id: UUID,
    model_id: UUID,
    node_type: VirtualNodeType,
    label: str,
    *,
    anchors: list[RealAnchorCreate] | None = None,
):
    return service.add_node(
        company_id,
        project_id,
        model_id,
        VirtualNodeCreate(
            node_type=node_type,
            label=label,
            properties={"source": "fde"},
            assertions=[_assertion()],
            anchors=anchors or [],
        ),
        actor_id="fde-user",
    )


def _add_edge(
    service: VirtualWorkService,
    company_id: UUID,
    project_id: UUID,
    model_id: UUID,
    source: UUID,
    target: UUID,
    edge_type: VirtualEdgeType,
):
    return service.add_edge(
        company_id,
        project_id,
        model_id,
        VirtualEdgeCreate(
            source_node_id=source,
            target_node_id=target,
            edge_type=edge_type,
            assertions=[_assertion(AssertionKind.OBSERVED_PRACTICE)],
        ),
        actor_id="fde-user",
    )


def test_node_update_creates_new_snapshot_and_replaces_evidence_and_anchors(store) -> None:
    session, _engine = store
    service = VirtualWorkService(session, formal_anchor_scope_check=lambda *_args: True)
    company_id, project_id, model = _create_model(service)
    model_id = model.virtual_work_model_id
    old_anchor = RealAnchorCreate(
        real_entity_id=uuid4(),
        real_release_id=uuid4(),
        relation_kind=AnchorRelationKind.CORRESPONDS_TO,
        support_ref="正式岗位目录/采购员",
    )
    original = service.add_node(
        company_id,
        project_id,
        model_id,
        VirtualNodeCreate(
            node_type=VirtualNodeType.POSITION,
            label="采购员",
            properties={"level": 1},
            assertions=[_assertion(AssertionKind.REPORTED, field_name="purpose")],
            anchors=[old_anchor],
        ),
        actor_id="fde-user",
    )
    node_id = original.nodes[0].id
    responsibility = _add_node(
        service,
        company_id,
        project_id,
        model_id,
        VirtualNodeType.RESPONSIBILITY,
        "负责供应商询价",
    )
    connected = _add_edge(
        service,
        company_id,
        project_id,
        model_id,
        node_id,
        responsibility.nodes[1].id,
        VirtualEdgeType.POSITION_OWNS_RESPONSIBILITY,
    )
    original = connected
    new_anchor = RealAnchorCreate(
        real_entity_id=uuid4(),
        real_release_id=uuid4(),
        relation_kind=AnchorRelationKind.REFINES,
        support_ref="正式岗位目录/采购岗位职责",
    )

    updated = service.update_node(
        company_id,
        project_id,
        model_id,
        node_id,
        VirtualNodeUpdate(
            expected_version=original.version,
            expected_revision_hash=original.revision_hash,
            reason="访谈确认岗位职责已调整。",
            node_type=VirtualNodeType.POSITION,
            label="采购专员",
            properties={"level": 2},
            assertions=[
                _assertion(
                    AssertionKind.REPORTED,
                    field_name="purpose",
                    value="负责采购需求核验",
                )
            ],
            anchors=[new_anchor],
        ),
        actor_id="fde-user",
    )

    assert updated.version == original.version + 1
    assert updated.operation == "NODE_UPDATED"
    assert updated.change_summary == "访谈确认岗位职责已调整。"
    assert len(updated.nodes) == 2
    updated_position = next(item for item in updated.nodes if item.id == node_id)
    assert updated_position.label == "采购专员"
    assert updated_position.properties == {"level": 2}
    assert updated_position.assertions[0].value == "负责采购需求核验"
    assert updated_position.assertions[0].evidence[0].source_ref.startswith("case://purpose-")
    assert len(updated_position.anchors) == 1
    assert updated_position.anchors[0].real_entity_id == new_anchor.real_entity_id
    assert [edge.id for edge in updated.edges] == [connected.edges[0].id]

    historical = service.get_revision_for_review(
        company_id, project_id, model_id, version=original.version
    )
    assert historical.nodes[0].label == "采购员"
    assert historical.nodes[0].assertions[0].value == "处理业务任务"
    assert historical.nodes[0].assertions[0].evidence[0].source_ref.startswith("case://purpose-")
    assert historical.nodes[0].anchors[0].real_entity_id == old_anchor.real_entity_id
    assert session.scalar(
        select(VirtualWorkNodeRow.label).where(
            VirtualWorkNodeRow.model_id == str(model_id),
            VirtualWorkNodeRow.revision_id == str(original.revision_id),
            VirtualWorkNodeRow.id == str(node_id),
        )
    ) == "采购员"

    unanchored = service.update_node(
        company_id,
        project_id,
        model_id,
        node_id,
        VirtualNodeUpdate(
            expected_version=updated.version,
            expected_revision_hash=updated.revision_hash,
            reason="解除已失效的正式岗位映射。",
            node_type=VirtualNodeType.POSITION,
            label="采购专员",
            properties={"level": 2},
            assertions=[_assertion(AssertionKind.REPORTED, value="负责采购需求核验")],
            anchors=[],
        ),
        actor_id="fde-user",
    )
    unanchored_position = next(item for item in unanchored.nodes if item.id == node_id)
    assert unanchored_position.anchors == []
    assert [edge.id for edge in unanchored.edges] == [connected.edges[0].id]
    updated_history = service.get_revision_for_review(
        company_id, project_id, model_id, version=updated.version
    )
    assert next(item for item in updated_history.nodes if item.id == node_id).anchors[
        0
    ].real_entity_id == new_anchor.real_entity_id


def test_retiring_node_omits_linked_edges_but_keeps_all_prior_rows(store) -> None:
    session, _engine = store
    service = VirtualWorkService(session)
    company_id, project_id, model = _create_model(service)
    model_id = model.virtual_work_model_id
    first = _add_node(
        service, company_id, project_id, model_id, VirtualNodeType.ACTIVITY, "初审"
    )
    second = _add_node(
        service, company_id, project_id, model_id, VirtualNodeType.ACTIVITY, "复核"
    )
    connected = _add_edge(
        service,
        company_id,
        project_id,
        model_id,
        first.nodes[0].id,
        second.nodes[1].id,
        VirtualEdgeType.PRECEDES,
    )
    retired_node_id = first.nodes[0].id
    retired_edge_id = connected.edges[0].id

    retired = service.retire_node(
        company_id,
        project_id,
        model_id,
        retired_node_id,
        VirtualWorkRetireCreate(
            expected_version=connected.version,
            expected_revision_hash=connected.revision_hash,
            reason="该岗位活动已取消。",
        ),
        actor_id="fde-user",
    )

    assert retired.operation == "NODE_RETIRED"
    assert retired.change_payload["retired_linked_edge_ids"] == [str(retired_edge_id)]
    assert [node.id for node in retired.nodes] == [second.nodes[1].id]
    assert retired.edges == []
    historical = service.get_revision_for_review(
        company_id, project_id, model_id, version=connected.version
    )
    assert {node.id for node in historical.nodes} == {retired_node_id, second.nodes[1].id}
    assert [edge.id for edge in historical.edges] == [retired_edge_id]

    # Database-level append-only triggers and version-scoped rows retain the source snapshot.
    assert session.scalar(
        select(VirtualWorkNodeRow.id).where(
            VirtualWorkNodeRow.model_id == str(model_id),
            VirtualWorkNodeRow.revision_id == str(connected.revision_id),
            VirtualWorkNodeRow.id == str(retired_node_id),
        )
    ) == str(retired_node_id)
    assert session.scalar(
        select(VirtualWorkEdgeRow.id).where(
            VirtualWorkEdgeRow.model_id == str(model_id),
            VirtualWorkEdgeRow.revision_id == str(connected.revision_id),
            VirtualWorkEdgeRow.id == str(retired_edge_id),
        )
    ) == str(retired_edge_id)


def test_edge_update_and_retirement_are_version_bound_and_keep_history(store) -> None:
    session, _engine = store
    service = VirtualWorkService(session)
    company_id, project_id, model = _create_model(service)
    model_id = model.virtual_work_model_id
    source = _add_node(
        service, company_id, project_id, model_id, VirtualNodeType.ACTIVITY, "录入"
    )
    target = _add_node(
        service, company_id, project_id, model_id, VirtualNodeType.ACTIVITY, "校验"
    )
    original = _add_edge(
        service,
        company_id,
        project_id,
        model_id,
        source.nodes[0].id,
        target.nodes[1].id,
        VirtualEdgeType.PRECEDES,
    )
    original_edge = original.edges[0]
    update_payload = VirtualEdgeUpdate(
        expected_version=original.version,
        expected_revision_hash=original.revision_hash,
        reason="流程顺序经现场核实后调整。",
        source_node_id=target.nodes[1].id,
        target_node_id=source.nodes[0].id,
        edge_type=VirtualEdgeType.PRECEDES,
        label="逆序返工",
        properties={"reason_code": "REWORK"},
        assertions=[_assertion(AssertionKind.OBSERVED_PRACTICE, value="发现问题后返回录入")],
    )
    updated = service.update_edge(
        company_id, project_id, model_id, original_edge.id, update_payload, actor_id="fde-user"
    )
    assert updated.operation == "EDGE_UPDATED"
    assert len(updated.edges) == 1
    assert updated.edges[0].id == original_edge.id
    assert updated.edges[0].source_node_id == target.nodes[1].id
    assert updated.edges[0].label == "逆序返工"
    assert updated.edges[0].properties == {"reason_code": "REWORK"}
    assert updated.edges[0].assertions[0].value == "发现问题后返回录入"
    assert service.get_revision_for_review(
        company_id, project_id, model_id, version=original.version
    ).edges[0] == original_edge

    with pytest.raises(DomainError) as stale:
        service.update_edge(
            company_id, project_id, model_id, original_edge.id, update_payload, actor_id="fde-user"
        )
    assert stale.value.code == "VIRTUAL_WORK_REVISION_STALE"
    with pytest.raises(DomainError) as missing:
        service.retire_edge(
            company_id,
            project_id,
            model_id,
            uuid4(),
            VirtualWorkRetireCreate(
                expected_version=updated.version,
                expected_revision_hash=updated.revision_hash,
                reason="验证缺失关系。",
            ),
            actor_id="fde-user",
        )
    assert missing.value.code == "VIRTUAL_WORK_EDGE_NOT_FOUND"

    retired = service.retire_edge(
        company_id,
        project_id,
        model_id,
        original_edge.id,
        VirtualWorkRetireCreate(
            expected_version=updated.version,
            expected_revision_hash=updated.revision_hash,
            reason="该关系不再成立。",
        ),
        actor_id="fde-user",
    )
    assert retired.edges == []
    assert retired.operation == "EDGE_RETIRED"
    assert service.get_revision_for_review(
        company_id, project_id, model_id, version=updated.version
    ).edges[0].id == original_edge.id
    with pytest.raises(DomainError) as retired_id:
        service.retire_edge(
            company_id,
            project_id,
            model_id,
            original_edge.id,
            VirtualWorkRetireCreate(
                expected_version=retired.version,
                expected_revision_hash=retired.revision_hash,
                reason="重复退役检查。",
            ),
            actor_id="fde-user",
        )
    assert retired_id.value.code == "VIRTUAL_WORK_EDGE_NOT_IN_VERSION"


def test_node_mutation_rejects_stale_hash_and_missing_id(store) -> None:
    session, _engine = store
    service = VirtualWorkService(session)
    company_id, project_id, model = _create_model(service)
    model_id = model.virtual_work_model_id
    original = _add_node(
        service, company_id, project_id, model_id, VirtualNodeType.POSITION, "原岗位"
    )
    payload = VirtualNodeUpdate(
        expected_version=original.version,
        expected_revision_hash="0" * 64,
        reason="验证乐观锁。",
        node_type=VirtualNodeType.POSITION,
        label="新岗位",
        assertions=[_assertion()],
    )
    with pytest.raises(DomainError) as mismatch:
        service.update_node(
            company_id, project_id, model_id, original.nodes[0].id, payload, actor_id="fde-user"
        )
    assert mismatch.value.code == "VIRTUAL_WORK_REVISION_HASH_MISMATCH"

    missing_payload = VirtualNodeUpdate(
        expected_version=original.version,
        expected_revision_hash=original.revision_hash,
        reason="验证缺失节点。",
        node_type=VirtualNodeType.POSITION,
        label="新岗位",
        assertions=[_assertion()],
    )
    with pytest.raises(DomainError) as missing:
        service.update_node(
            company_id, project_id, model_id, uuid4(), missing_payload, actor_id="fde-user"
        )
    assert missing.value.code == "VIRTUAL_WORK_NODE_NOT_FOUND"


def test_virtual_work_base_creates_tables_in_observation_metadata_only(store) -> None:
    _session, observation_engine = store
    observation_tables = set(inspect(observation_engine).get_table_names())
    assert "management_observations" in observation_tables
    assert "virtual_work_models" in observation_tables
    assert "virtual_work_nodes" in observation_tables
    assert "virtual_work_real_anchors" in observation_tables

    formal_engine = create_engine("sqlite://")
    FormalBase.metadata.create_all(formal_engine)
    formal_tables = set(inspect(formal_engine).get_table_names())
    assert not any(name.startswith("virtual_work_") for name in formal_tables)
    formal_engine.dispose()


def test_http_json_uuid_strings_are_accepted_while_unknown_fields_are_rejected(store) -> None:
    session, _engine = store
    company_id, project_id = uuid4(), uuid4()
    request_body = {
        "company_id": str(company_id),
        "project_id": str(project_id),
        "name": "HTTP 输入虚模",
        "description": None,
    }
    # FastAPI validates a parsed JSON object through validate_python; model_validate_json
    # separately guards the wire-format path.
    parsed_for_api = VirtualWorkModelCreate.model_validate(request_body)
    parsed_from_json = VirtualWorkModelCreate.model_validate_json(json.dumps(request_body))
    assert parsed_for_api.company_id == company_id
    assert parsed_for_api.project_id == project_id
    assert parsed_from_json == parsed_for_api

    created = VirtualWorkService(session).create_model(parsed_for_api, actor_id="http-user")
    assert created.company_id == company_id
    assert created.project_id == project_id
    assert created.status is VirtualRevisionStatus.DRAFT

    with pytest.raises(ValidationError):
        VirtualWorkModelCreate.model_validate({**request_body, "unexpected": "forbidden"})


def test_position_role_assignment_activity_and_work_instance_are_distinct_and_many_to_many(
    store,
) -> None:
    session, _engine = store
    service = VirtualWorkService(session)
    company_id, project_id, model = _create_model(service)
    model_id = model.virtual_work_model_id

    _add_node(service, company_id, project_id, model_id, VirtualNodeType.POSITION, "采购员甲")
    _add_node(service, company_id, project_id, model_id, VirtualNodeType.POSITION, "采购员乙")
    _add_node(service, company_id, project_id, model_id, VirtualNodeType.PERSON, "人员甲")
    _add_node(
        service, company_id, project_id, model_id, VirtualNodeType.ROLE_ASSIGNMENT, "人员甲任职记录"
    )
    _add_node(service, company_id, project_id, model_id, VirtualNodeType.ACTIVITY, "核对询价")
    work = _add_node(
        service,
        company_id,
        project_id,
        model_id,
        VirtualNodeType.WORK_INSTANCE,
        "询价案例 001",
    )

    by_label = {node.label: node.id for node in work.nodes}
    assert by_label["采购员甲"] != by_label["人员甲任职记录"]
    assert by_label["人员甲"] != by_label["采购员甲"]
    assignment_node = next(node for node in work.nodes if node.label == "人员甲任职记录")
    assert assignment_node.node_type is VirtualNodeType.ROLE_ASSIGNMENT

    _add_edge(
        service,
        company_id,
        project_id,
        model_id,
        by_label["人员甲任职记录"],
        by_label["人员甲"],
        VirtualEdgeType.ASSIGNMENT_PERSON,
    )
    _add_edge(
        service,
        company_id,
        project_id,
        model_id,
        by_label["人员甲任职记录"],
        by_label["采购员甲"],
        VirtualEdgeType.ASSIGNMENT_POSITION,
    )
    for position_label in ("采购员甲", "采购员乙"):
        position_id = by_label[position_label]
        _add_edge(
            service,
            company_id,
            project_id,
            model_id,
            position_id,
            by_label["核对询价"],
            VirtualEdgeType.POSITION_PERFORMS_ACTIVITY,
        )
        final = _add_edge(
            service,
            company_id,
            project_id,
            model_id,
            position_id,
            by_label["询价案例 001"],
            VirtualEdgeType.POSITION_PERFORMS_WORK,
        )

    assert final.status is VirtualRevisionStatus.DRAFT
    assert len(final.edges) == 6
    activity_edges = [
        edge for edge in final.edges if edge.edge_type is VirtualEdgeType.POSITION_PERFORMS_ACTIVITY
    ]
    work_edges = [
        edge for edge in final.edges if edge.edge_type is VirtualEdgeType.POSITION_PERFORMS_WORK
    ]
    assert len({edge.source_node_id for edge in activity_edges}) == 2
    assert len({edge.source_node_id for edge in work_edges}) == 2
    assert all(edge.target_node_id == by_label["核对询价"] for edge in activity_edges)
    assert all(edge.target_node_id == by_label["询价案例 001"] for edge in work_edges)


def test_real_anchor_is_typed_reference_only_and_must_resolve_in_model_scope(store) -> None:
    session, _engine = store
    company_id, project_id, model = _create_model(VirtualWorkService(session))
    entity_id, release_id = uuid4(), uuid4()
    service = VirtualWorkService(
        session,
        formal_anchor_scope_check=lambda company, project, entity, release: (
            company == company_id
            and project == project_id
            and entity == entity_id
            and release == release_id
        ),
    )
    anchor = RealAnchorCreate(
        real_entity_id=entity_id,
        real_release_id=release_id,
        relation_kind=AnchorRelationKind.REFINES,
        support_ref="formal://release/entity",
    )
    revision = _add_node(
        service,
        company_id,
        project_id,
        model.virtual_work_model_id,
        VirtualNodeType.POSITION,
        "采购员",
        anchors=[anchor],
    )
    stored = session.scalars(select(RealAnchorRow)).one()
    assert stored.real_entity_id == str(entity_id)
    assert stored.real_release_id == str(release_id)
    assert not {"entity_name", "formal_payload", "company_id", "project_id"}.intersection(
        RealAnchorRow.__table__.columns.keys()
    )
    node = next(node for node in revision.nodes if node.label == "采购员")
    assert node.id != entity_id
    assert node.anchors[0].real_entity_id == entity_id
    assert node.anchors[0].real_release_id == release_id

    wrong_scope_service = VirtualWorkService(
        session, formal_anchor_scope_check=lambda *_args: False
    )
    with pytest.raises(DomainError, match="正式实体或 release") as error:
        _add_node(
            wrong_scope_service,
            company_id,
            project_id,
            model.virtual_work_model_id,
            VirtualNodeType.POSITION,
            "不应写入的岗位",
            anchors=[anchor],
        )
    assert error.value.code == "VIRTUAL_WORK_ANCHOR_OUT_OF_SCOPE"
    assert (
        wrong_scope_service.get_revision_for_review(
            company_id, project_id, model.virtual_work_model_id, version=2
        ).version
        == 2
    )


def test_edge_endpoint_scope_type_and_version_are_validated(store) -> None:
    session, _engine = store
    service = VirtualWorkService(session)
    company_a, project_a, model_a = _create_model(service, name="A 的虚模")
    company_b, project_b, model_b = _create_model(service, name="B 的虚模")
    position = _add_node(
        service,
        company_a,
        project_a,
        model_a.virtual_work_model_id,
        VirtualNodeType.POSITION,
        "岗位 A",
    )
    foreign_activity = _add_node(
        service,
        company_b,
        project_b,
        model_b.virtual_work_model_id,
        VirtualNodeType.ACTIVITY,
        "活动 B",
    )
    source_id = next(node.id for node in position.nodes if node.label == "岗位 A")
    target_id = next(node.id for node in foreign_activity.nodes if node.label == "活动 B")
    with pytest.raises(DomainError) as out_of_scope:
        _add_edge(
            service,
            company_a,
            project_a,
            model_a.virtual_work_model_id,
            source_id,
            target_id,
            VirtualEdgeType.POSITION_PERFORMS_ACTIVITY,
        )
    assert out_of_scope.value.code == "VIRTUAL_WORK_REFERENCE_OUT_OF_SCOPE"

    wrong_type = _add_node(
        service,
        company_a,
        project_a,
        model_a.virtual_work_model_id,
        VirtualNodeType.PERSON,
        "人员 A",
    )
    wrong_type_id = next(node.id for node in wrong_type.nodes if node.label == "人员 A")
    with pytest.raises(DomainError) as mismatch:
        _add_edge(
            service,
            company_a,
            project_a,
            model_a.virtual_work_model_id,
            source_id,
            wrong_type_id,
            VirtualEdgeType.POSITION_PERFORMS_ACTIVITY,
        )
    assert mismatch.value.code == "VIRTUAL_WORK_EDGE_TYPE_MISMATCH"


def test_versions_are_append_only_and_each_read_returns_the_requested_snapshot(store) -> None:
    session, engine = store
    service = VirtualWorkService(session)
    company_id, project_id, model = _create_model(service)
    model_id = model.virtual_work_model_id
    assert model.version == 1 and not model.nodes

    version_two = _add_node(
        service, company_id, project_id, model_id, VirtualNodeType.POSITION, "岗位"
    )
    position_id = version_two.nodes[0].id
    version_three = _add_node(
        service, company_id, project_id, model_id, VirtualNodeType.ACTIVITY, "活动"
    )
    activity_id = next(node.id for node in version_three.nodes if node.label == "活动")
    version_four = _add_edge(
        service,
        company_id,
        project_id,
        model_id,
        position_id,
        activity_id,
        VirtualEdgeType.POSITION_PERFORMS_ACTIVITY,
    )
    assert [version_two.version, version_three.version, version_four.version] == [2, 3, 4]
    for revision in (model, version_two, version_three, version_four):
        _review(service, company_id, project_id, revision)
    assert service.get_model(company_id, project_id, model_id, version=1).nodes == []
    version_two_read = service.get_model(company_id, project_id, model_id, version=2)
    assert [node.label for node in version_two_read.nodes] == ["岗位"]
    assert len(service.get_model(company_id, project_id, model_id, version=3).edges) == 0
    assert len(service.get_model(company_id, project_id, model_id, version=4).edges) == 1

    with pytest.raises(DBAPIError, match="append-only"):
        with engine.begin() as connection:
            connection.execute(
                text(
                    "UPDATE virtual_work_revisions SET name='覆写历史' "
                    "WHERE model_id=:model_id AND version=1"
                ),
                {"model_id": str(model_id)},
            )
    session.rollback()
    assert service.get_model(company_id, project_id, model_id, version=1).name == "岗位工作虚模"
    revisions = session.execute(
        select(VirtualWorkRevisionRow.version, VirtualWorkRevisionRow.operation)
        .where(VirtualWorkRevisionRow.model_id == str(model_id))
        .order_by(VirtualWorkRevisionRow.version)
    ).all()
    assert revisions == [(1, "CREATED"), (2, "NODE_ADDED"), (3, "NODE_ADDED"), (4, "EDGE_ADDED")]


def test_model_node_and_edge_writes_return_drafts_hidden_from_generic_reads(store) -> None:
    session, _engine = store
    service = VirtualWorkService(session)
    company_id, project_id, created = _create_model(service)
    model_id = created.virtual_work_model_id
    assert created.status is VirtualRevisionStatus.DRAFT
    with pytest.raises(DomainError) as hidden_initial:
        service.get_model(company_id, project_id, model_id)
    assert hidden_initial.value.code == "VIRTUAL_WORK_MODEL_NOT_REVIEWED"

    position_revision = _add_node(
        service, company_id, project_id, model_id, VirtualNodeType.POSITION, "岗位"
    )
    assert position_revision.status is VirtualRevisionStatus.DRAFT
    activity_revision = _add_node(
        service, company_id, project_id, model_id, VirtualNodeType.ACTIVITY, "活动"
    )
    assert activity_revision.status is VirtualRevisionStatus.DRAFT
    position_id = next(node.id for node in activity_revision.nodes if node.label == "岗位")
    activity_id = next(node.id for node in activity_revision.nodes if node.label == "活动")
    edge_revision = _add_edge(
        service,
        company_id,
        project_id,
        model_id,
        position_id,
        activity_id,
        VirtualEdgeType.POSITION_PERFORMS_ACTIVITY,
    )
    assert edge_revision.status is VirtualRevisionStatus.DRAFT
    assert edge_revision.version == 4
    assert service.list_models(company_id, project_id) == []
    for version in range(1, 5):
        with pytest.raises(DomainError) as hidden_draft:
            service.get_model(company_id, project_id, model_id, version=version)
        assert hidden_draft.value.code == "VIRTUAL_WORK_REVISION_NOT_REVIEWED"


def test_review_rejects_scope_or_hash_mismatch_and_reviewing_old_version_does_not_replace_newer(
    store,
) -> None:
    session, _engine = store
    service = VirtualWorkService(session)
    company_id, project_id, revision_one = _create_model(service)
    model_id = revision_one.virtual_work_model_id
    wrong_scope = ConfirmedScope(
        company_id=uuid4(),
        project_id=project_id,
        virtual_work_model_id=model_id,
    )

    with pytest.raises(DomainError) as scope_error:
        _review(
            service,
            company_id,
            project_id,
            revision_one,
            confirmed_scope=wrong_scope,
        )
    assert scope_error.value.code == "VIRTUAL_WORK_REVIEW_SCOPE_MISMATCH"

    with pytest.raises(DomainError) as hash_error:
        _review(service, company_id, project_id, revision_one, revision_hash="0" * 64)
    assert hash_error.value.code == "VIRTUAL_WORK_REVIEW_HASH_MISMATCH"
    assert service.list_review_records(company_id, project_id, model_id, version=1) == []

    first_review = _review(
        service,
        company_id,
        project_id,
        revision_one,
        actor_id="reviewer-one",
    )
    assert first_review.revision_id == revision_one.revision_id
    assert first_review.version == revision_one.version
    assert first_review.revision_hash == revision_one.revision_hash
    assert first_review.confirmed_scope == ConfirmedScope(
        company_id=company_id,
        project_id=project_id,
        virtual_work_model_id=model_id,
    )
    assert first_review.confirmation_kind is ConfirmationKind.HUMAN_REVIEW
    assert first_review.actor_id == "reviewer-one"

    revision_two = _add_node(
        service, company_id, project_id, model_id, VirtualNodeType.POSITION, "岗位新版本"
    )
    _review(service, company_id, project_id, revision_two, actor_id="reviewer-two")
    _review(
        service,
        company_id,
        project_id,
        revision_one,
        actor_id="reviewer-three",
        decision=ReviewDecision.REVIEWED,
    )

    current = service.get_model(company_id, project_id, model_id)
    assert current.version == revision_two.version
    assert current.revision_id == revision_two.revision_id
    history = service.list_review_records(company_id, project_id, model_id, version=1)
    assert [record.actor_id for record in history] == ["reviewer-one", "reviewer-three"]
    assert (
        session.query(VirtualWorkReviewRow)
        .filter_by(revision_id=str(revision_one.revision_id))
        .count()
        == 2
    )


def test_rejected_and_withdrawn_are_append_only_revision_states(store) -> None:
    session, _engine = store
    service = VirtualWorkService(session)
    company_id, project_id, draft = _create_model(service)
    model_id = draft.virtual_work_model_id

    rejected = _review(
        service,
        company_id,
        project_id,
        draft,
        decision=ReviewDecision.REJECTED,
    )
    assert (
        service.get_revision_for_review(company_id, project_id, model_id, version=1).status
        is VirtualRevisionStatus.REJECTED
    )
    with pytest.raises(DomainError) as hidden:
        service.get_model(company_id, project_id, model_id)
    assert hidden.value.code == "VIRTUAL_WORK_MODEL_NOT_REVIEWED"

    withdrawn = _review(
        service,
        company_id,
        project_id,
        draft,
        decision=ReviewDecision.WITHDRAWN,
    )
    assert rejected.id != withdrawn.id
    assert (
        service.get_revision_for_review(company_id, project_id, model_id, version=1).status
        is VirtualRevisionStatus.WITHDRAWN
    )
    assert [
        item.decision
        for item in service.list_review_records(company_id, project_id, model_id, version=1)
    ] == [ReviewDecision.REJECTED, ReviewDecision.WITHDRAWN]
    assert (
        session.query(VirtualWorkReviewRow).filter_by(revision_id=str(draft.revision_id)).count()
        == 2
    )


def test_company_project_reads_are_isolated_and_project_scope_can_be_verified(store) -> None:
    session, _engine = store
    company_a, project_a = uuid4(), uuid4()
    company_b, project_b = uuid4(), uuid4()
    service = VirtualWorkService(session)
    _company, _project, model_a = _create_model(service, company_a, project_a, name="公司 A")
    _company, _project, model_b = _create_model(service, company_b, project_b, name="公司 B")
    _review(service, company_a, project_a, model_a)
    _review(service, company_b, project_b, model_b)
    assert [item.name for item in service.list_models(company_a, project_a)] == ["公司 A"]
    assert service.list_models(company_a, project_b) == []
    with pytest.raises(DomainError) as error:
        service.get_model(company_b, project_b, model_a.virtual_work_model_id)
    assert error.value.code == "VIRTUAL_WORK_MODEL_NOT_FOUND"

    rejecting_service = VirtualWorkService(
        session, project_scope_check=lambda _company, _project: False
    )
    with pytest.raises(DomainError) as rejected:
        _create_model(rejecting_service, company_a, project_a, name="跨范围项目")
    assert rejected.value.code == "VIRTUAL_WORK_PROJECT_OUT_OF_SCOPE"


def test_field_assertions_reject_misclassified_or_untraceable_claims() -> None:
    valid = [
        _assertion(AssertionKind.NORMATIVE),
        _assertion(AssertionKind.OBSERVED_PRACTICE),
        _assertion(AssertionKind.SYSTEM_EVENT),
        _assertion(AssertionKind.REPORTED),
        _assertion(AssertionKind.INFERRED),
    ]
    assert {item.assertion_kind for item in valid} == set(AssertionKind)
    inferred = next(item for item in valid if item.assertion_kind is AssertionKind.INFERRED)
    assert {item.evidence_kind for item in inferred.evidence} == {
        EvidenceKind.INFERENCE_BASIS,
        EvidenceKind.REPORT,
    }

    with pytest.raises(ValidationError, match="证据类型"):
        FieldAssertionCreate(
            field_name="duty",
            value="审批采购",
            assertion_kind=AssertionKind.NORMATIVE,
            evidence=[_evidence(EvidenceKind.INTERVIEW)],
        )
    with pytest.raises(ValidationError, match="原始依据证据"):
        FieldAssertionCreate(
            field_name="risk",
            value="可能存在瓶颈",
            assertion_kind=AssertionKind.INFERRED,
            evidence=[_evidence(EvidenceKind.INFERENCE_BASIS)],
            method="单一推测。",
        )
    with pytest.raises(ValidationError, match="推导方法"):
        FieldAssertionCreate(
            field_name="risk",
            value="可能存在瓶颈",
            assertion_kind=AssertionKind.INFERRED,
            evidence=[_evidence(EvidenceKind.INFERENCE_BASIS), _evidence(EvidenceKind.REPORT)],
        )


def test_assertion_scope_validity_and_evidence_root_survive_revision_copy(store) -> None:
    session, _engine = store
    service = VirtualWorkService(session)
    company_id, project_id, model = _create_model(service)
    model_id = model.virtual_work_model_id
    source_root_id = uuid4()
    source_timezone = timezone(timedelta(hours=8))
    valid_from = datetime(2026, 9, 1, 8, tzinfo=source_timezone)
    valid_to = datetime(2026, 9, 30, 18, tzinfo=source_timezone)
    scope = {
        "department_id": "manufacturing",
        "site": {"country": "CN", "plant": "east-1"},
    }

    def reported_assertion(field_name: str, excerpt: str) -> FieldAssertionCreate:
        return FieldAssertionCreate(
            field_name=field_name,
            value=excerpt,
            assertion_kind=AssertionKind.REPORTED,
            scope=scope,
            valid_from=valid_from,
            valid_to=valid_to,
            evidence=[
                EvidenceCreate(
                    evidence_kind=EvidenceKind.INTERVIEW,
                    source_ref="interview://session-17",
                    excerpt=excerpt,
                    source_root_id=source_root_id,
                )
            ],
        )

    first_revision = service.add_node(
        company_id,
        project_id,
        model_id,
        VirtualNodeCreate(
            node_type=VirtualNodeType.POSITION,
            label="生产计划员",
            assertions=[
                reported_assertion("responsibility", "协调周生产计划"),
                reported_assertion("rationale", "减少跨班组信息延迟"),
            ],
        ),
        actor_id="fde-user",
    )
    first_node = next(node for node in first_revision.nodes if node.label == "生产计划员")
    assertions_by_field = {item.field_name: item for item in first_node.assertions}
    assert assertions_by_field["responsibility"].scope == scope
    assert assertions_by_field["responsibility"].valid_from == valid_from.astimezone(UTC)
    assert assertions_by_field["responsibility"].valid_to == valid_to.astimezone(UTC)
    assert all(item.evidence[0].source_root_id == source_root_id for item in first_node.assertions)

    stored_revision = session.get(VirtualWorkRevisionRow, str(first_revision.revision_id))
    parent_revision = session.scalar(
        select(VirtualWorkRevisionRow).where(
            VirtualWorkRevisionRow.model_id == str(model_id),
            VirtualWorkRevisionRow.version == first_revision.version - 1,
        )
    )
    assert stored_revision is not None and parent_revision is not None
    change_payload = stored_revision.change_payload
    assertion_payload = next(
        item for item in change_payload["assertions"] if item["field_name"] == "responsibility"
    )
    assert assertion_payload["scope"] == scope
    assert assertion_payload["valid_from"].endswith("+08:00")
    assert assertion_payload["valid_to"].endswith("+08:00")
    assert assertion_payload["evidence"][0]["source_root_id"] == str(source_root_id)
    assert stored_revision.revision_hash == _revision_hash(
        str(model_id),
        first_revision.version,
        parent_revision.revision_hash,
        stored_revision.operation,
        change_payload,
    )

    for field_path, replacement in (
        (("scope",), {"department_id": "finance"}),
        (("valid_from",), "2026-09-02T00:00:00+00:00"),
        (("valid_to",), "2026-10-01T00:00:00+00:00"),
        (("evidence", 0, "source_root_id"), str(uuid4())),
    ):
        changed_payload = json.loads(json.dumps(change_payload))
        changed_assertion = next(
            item for item in changed_payload["assertions"] if item["field_name"] == "responsibility"
        )
        target = changed_assertion
        for key in field_path[:-1]:
            target = target[key]
        target[field_path[-1]] = replacement
        assert (
            _revision_hash(
                str(model_id),
                first_revision.version,
                parent_revision.revision_hash,
                stored_revision.operation,
                changed_payload,
            )
            != stored_revision.revision_hash
        )

    copied_revision = _add_node(
        service,
        company_id,
        project_id,
        model_id,
        VirtualNodeType.ACTIVITY,
        "编制生产计划",
    )
    copied_node = next(node for node in copied_revision.nodes if node.id == first_node.id)
    copied_assertions = {item.field_name: item for item in copied_node.assertions}
    for field_name, original in assertions_by_field.items():
        copied = copied_assertions[field_name]
        assert copied.id == original.id
        assert copied.scope == original.scope
        assert copied.valid_from == original.valid_from
        assert copied.valid_to == original.valid_to
        assert copied.evidence[0].id == original.evidence[0].id
        assert copied.evidence[0].source_root_id == original.evidence[0].source_root_id
    assert len({item.evidence[0].source_root_id for item in copied_assertions.values()}) == 1


def test_assertion_validity_requires_aware_ordered_interval() -> None:
    base = {
        "field_name": "duty",
        "value": "按规定完成工作",
        "assertion_kind": AssertionKind.NORMATIVE,
        "evidence": [_evidence(EvidenceKind.NORMATIVE_DOCUMENT)],
    }
    with pytest.raises(ValidationError, match="时区"):
        FieldAssertionCreate(**base, valid_from=datetime(2026, 9, 1))

    valid_from = datetime(2026, 9, 1, 8, tzinfo=timezone(timedelta(hours=8)))
    same_instant_utc = datetime(2026, 9, 1, 0, tzinfo=UTC)
    with pytest.raises(ValidationError, match="valid_to 必须晚于 valid_from"):
        FieldAssertionCreate(
            **base,
            valid_from=valid_from,
            valid_to=same_instant_utc,
        )


def test_evidence_source_check_receives_company_project_and_each_evidence_item(store) -> None:
    session, _engine = store
    checked: list[tuple[UUID, UUID, EvidenceKind, str, str]] = []

    def check_source(
        company_id: UUID,
        project_id: UUID,
        evidence_kind: EvidenceKind,
        source_ref: str,
        excerpt: str,
    ) -> bool:
        checked.append((company_id, project_id, evidence_kind, source_ref, excerpt))
        return True

    service = VirtualWorkService(session, evidence_source_check=check_source)
    company_id, project_id, model = _create_model(service)
    position_revision = service.add_node(
        company_id,
        project_id,
        model.virtual_work_model_id,
        VirtualNodeCreate(
            node_type=VirtualNodeType.POSITION,
            label="采购岗位",
            assertions=[_assertion(), _assertion(field_name="approval_limit")],
        ),
        actor_id="fde-user",
    )
    activity_revision = _add_node(
        service,
        company_id,
        project_id,
        model.virtual_work_model_id,
        VirtualNodeType.ACTIVITY,
        "审核采购申请",
    )
    position = next(node for node in activity_revision.nodes if node.label == "采购岗位")
    activity = next(node for node in activity_revision.nodes if node.label == "审核采购申请")
    edge_revision = _add_edge(
        service,
        company_id,
        project_id,
        model.virtual_work_model_id,
        position.id,
        activity.id,
        VirtualEdgeType.POSITION_PERFORMS_ACTIVITY,
    )

    assert position_revision.version == 2
    assert edge_revision.version == 4
    assert len(checked) == 4
    assert all(item[0] == company_id and item[1] == project_id for item in checked)
    assert [item[2] for item in checked] == [
        EvidenceKind.NORMATIVE_DOCUMENT,
        EvidenceKind.NORMATIVE_DOCUMENT,
        EvidenceKind.NORMATIVE_DOCUMENT,
        EvidenceKind.DIRECT_OBSERVATION,
    ]
    assert all(item[3].startswith("case://") and item[4] for item in checked)


def test_rejected_evidence_source_check_writes_no_node_or_edge_revision(store) -> None:
    session, _engine = store
    accepting_service = VirtualWorkService(session)
    company_id, project_id, model = _create_model(accepting_service)
    _add_node(
        accepting_service,
        company_id,
        project_id,
        model.virtual_work_model_id,
        VirtualNodeType.POSITION,
        "采购岗位",
    )
    activity_revision = _add_node(
        accepting_service,
        company_id,
        project_id,
        model.virtual_work_model_id,
        VirtualNodeType.ACTIVITY,
        "审核采购申请",
    )
    position = next(node for node in activity_revision.nodes if node.label == "采购岗位")
    activity = next(node for node in activity_revision.nodes if node.label == "审核采购申请")
    checked: list[str] = []

    def reject_source(
        _company_id: UUID,
        _project_id: UUID,
        _evidence_kind: EvidenceKind,
        source_ref: str,
        _excerpt: str,
    ) -> bool:
        checked.append(source_ref)
        return False

    rejecting_service = VirtualWorkService(session, evidence_source_check=reject_source)
    revision_count_before = session.query(VirtualWorkRevisionRow).count()
    with pytest.raises(DomainError) as node_error:
        rejecting_service.add_node(
            company_id,
            project_id,
            model.virtual_work_model_id,
            VirtualNodeCreate(
                node_type=VirtualNodeType.POSITION,
                label="未核验岗位",
                assertions=[_assertion()],
            ),
            actor_id="fde-user",
        )
    assert node_error.value.code == "VIRTUAL_WORK_EVIDENCE_SOURCE_REJECTED"
    assert session.query(VirtualWorkRevisionRow).count() == revision_count_before

    with pytest.raises(DomainError) as edge_error:
        rejecting_service.add_edge(
            company_id,
            project_id,
            model.virtual_work_model_id,
            VirtualEdgeCreate(
                source_node_id=position.id,
                target_node_id=activity.id,
                edge_type=VirtualEdgeType.POSITION_PERFORMS_ACTIVITY,
                assertions=[_assertion(AssertionKind.OBSERVED_PRACTICE)],
            ),
            actor_id="fde-user",
        )
    assert edge_error.value.code == "VIRTUAL_WORK_EVIDENCE_SOURCE_REJECTED"
    assert session.query(VirtualWorkRevisionRow).count() == revision_count_before
    assert len(checked) == 2

    unchanged = accepting_service.get_revision_for_review(
        company_id,
        project_id,
        model.virtual_work_model_id,
        version=activity_revision.version,
    )
    assert unchanged.version == activity_revision.version
    assert len(unchanged.nodes) == 2
    assert unchanged.edges == []


def test_anchor_requires_verifier_and_bad_system_event_requires_timestamp(store) -> None:
    session, _engine = store
    company_id, project_id, model = _create_model(VirtualWorkService(session))
    anchor = RealAnchorCreate(
        real_entity_id=uuid4(),
        real_release_id=uuid4(),
        relation_kind=AnchorRelationKind.CORRESPONDS_TO,
        support_ref="formal://position",
    )
    with pytest.raises(DomainError) as error:
        _add_node(
            VirtualWorkService(session),
            company_id,
            project_id,
            model.virtual_work_model_id,
            VirtualNodeType.POSITION,
            "无校验器岗位",
            anchors=[anchor],
        )
    assert error.value.code == "VIRTUAL_WORK_ANCHOR_VERIFIER_REQUIRED"

    with pytest.raises(ValidationError, match="发生时间"):
        EvidenceCreate(
            evidence_kind=EvidenceKind.SYSTEM_EVENT,
            source_ref="erp://event/1",
            excerpt="发生了一次操作。",
        )
