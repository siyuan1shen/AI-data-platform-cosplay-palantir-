from __future__ import annotations

from uuid import uuid4

import pytest
from fastapi.testclient import TestClient
from sqlalchemy.exc import IntegrityError

from enterprise_insight_backend.agent_runtime import AgentRuntimeService
from enterprise_insight_backend.control import ControlIndexService
from enterprise_insight_backend.control_worker import ControlIndexWorker
from enterprise_insight_backend.models import AgentReadSetOutboxRow, AgentStepOutboxRow
from enterprise_insight_backend.schemas import AgentClaim, AgentClaimKind, AgentStructuredOutput


def test_claim_ledger_accepts_only_references_in_the_read_context() -> None:
    fragment_id = str(uuid4())
    observation_id = str(uuid4())
    tool_id = str(uuid4())
    context = {
        "evidence_fragments": [{"id": fragment_id}],
        "management_context": {
            "read_manifest": {
                "management_observation_refs": [
                    {"id": observation_id, "revision": 2}
                ],
                "potential_record_refs": [],
            }
        },
        "agent_execution": {
            "tool_results": [{"invocation_id": tool_id, "status": "SUCCEEDED"}]
        },
    }
    claim = AgentClaim(
        statement="跨部门等待可能造成延期",
        kind=AgentClaimKind.HYPOTHESIS,
        supporting_refs=[
            f"formal://evidence_fragments/{fragment_id}",
            f"observation://{observation_id}/2",
            f"tool://invocation/{tool_id}",
            f"formal://evidence_fragments/{uuid4()}",
        ],
        counterevidence_refs=[f"potential://{uuid4()}/1"],
        unknowns=["尚未排除订单结构差异"],
    )

    [entry] = AgentRuntimeService._build_claim_ledger(
        str(uuid4()), [claim], context, references_visible=True
    )

    assert entry["validation_status"] == "UNRESOLVED_REFERENCES_REMOVED"
    assert len(entry["supporting_refs"]) == 3
    assert entry["counterevidence_refs"] == []
    assert entry["rejected_reference_count"] == 2
    assert len(entry["submitted_supporting_refs"]) == 4


def test_claim_ledger_never_calls_reference_existence_semantic_proof() -> None:
    fragment_id = str(uuid4())
    claim = AgentClaim(
        statement="记录显示本月退货 12 单",
        kind=AgentClaimKind.FACT,
        supporting_refs=[f"formal://evidence_fragments/{fragment_id}"],
    )

    [entry] = AgentRuntimeService._build_claim_ledger(
        str(uuid4()),
        [claim],
        {"evidence_fragments": [{"id": fragment_id}]},
        references_visible=True,
    )

    assert entry["validation_status"] == "REFERENCES_RESOLVED_NOT_SEMANTICALLY_VERIFIED"


def test_query_manifest_keeps_exact_read_and_model_visible_source_sets() -> None:
    fragment_id = str(uuid4())
    observation_id = str(uuid4())
    context = {
        "project": {"model_baseline": {"query_snapshot_id": str(uuid4())}},
        "task_route": {"selected_stores": ["formal", "observations"]},
        "evidence_fragments": [{"id": fragment_id}],
        "management_context": {
            "read_manifest": {
                "management_observation_refs": [
                    {"id": observation_id, "revision": 3}
                ],
                "potential_record_refs": [],
            },
            "coverage": {"observations": {"included": 1}},
        },
    }

    manifest = AgentRuntimeService._merge_query_manifest(
        None, context, model_context_shared=False
    )
    [read_set] = manifest["read_sets"]

    assert read_set["read_reference_uris"] == sorted(
        [
            f"formal://evidence_fragments/{fragment_id}",
            f"observation://{observation_id}/3",
        ]
    )
    assert read_set["model_context_shared"] is False
    assert read_set["model_visible_reference_uris"] == []


def test_claim_ledger_tracks_source_rows_returned_by_successful_read_tools() -> None:
    observation_id = str(uuid4())
    potential_id = str(uuid4())
    entity_id = str(uuid4())
    relation_id = str(uuid4())
    semantic_dataset_id = str(uuid4())
    query_snapshot_id = str(uuid4())
    query_root_id = str(uuid4())
    lineage_entity_id = str(uuid4())
    document_id = str(uuid4())
    fragment_id = str(uuid4())
    failed_observation_id = str(uuid4())
    context = {
        "agent_execution": {
            "tool_results": [
                {
                    "invocation_id": str(uuid4()),
                    "status": "SUCCEEDED",
                    "result": {
                        "resource": "MANAGEMENT_OBSERVATIONS",
                        "items": [{"id": observation_id, "revision": 4}],
                    },
                },
                {
                    "invocation_id": str(uuid4()),
                    "status": "SUCCEEDED",
                    "result": {
                        "resource": "POTENTIAL_RECORDS",
                        "items": [{"id": potential_id, "version": 2}],
                    },
                },
                {
                    "invocation_id": str(uuid4()),
                    "status": "SUCCEEDED",
                    "result": {
                        "resource": "GRAPH_NEIGHBORHOOD",
                        "entities": [{"id": entity_id}],
                        "relations": [{"id": relation_id}],
                    },
                },
                {
                    "invocation_id": str(uuid4()),
                    "status": "SUCCEEDED",
                    "result": {
                        "resource": "SEMANTIC_QUERY_RUN",
                        "id": str(uuid4()),
                        "dataset_id": semantic_dataset_id,
                        "query_snapshot_id": query_snapshot_id,
                        "row_count": 1,
                        "rows": [
                            {
                                "root_entity_id": query_root_id,
                                "_lineage": {"orders": [lineage_entity_id]},
                            }
                        ],
                    },
                },
                {
                    "invocation_id": str(uuid4()),
                    "status": "SUCCEEDED",
                    "result": {
                        "resource": "MATERIAL_FRAGMENTS",
                        "source_document_id": document_id,
                        "items": [{"id": fragment_id, "text": "来源正文"}],
                    },
                },
                {
                    "invocation_id": str(uuid4()),
                    "status": "FAILED",
                    "result": {
                        "resource": "MANAGEMENT_OBSERVATIONS",
                        "items": [{"id": failed_observation_id, "revision": 1}],
                    },
                },
            ]
        }
    }

    references = AgentRuntimeService._claim_allowed_references(
        context, references_visible=True
    )

    assert f"observation://{observation_id}/4" in references
    assert f"potential://{potential_id}/2" in references
    assert f"formal://entities/{entity_id}" in references
    assert f"formal://relations/{relation_id}" in references
    assert f"formal://semantic_datasets/{semantic_dataset_id}" in references
    assert f"formal://query_snapshots/{query_snapshot_id}" in references
    assert f"formal://entities/{query_root_id}" in references
    assert f"formal://entities/{lineage_entity_id}" in references
    assert f"formal://documents/{document_id}" in references
    assert f"formal://evidence_fragments/{fragment_id}" in references
    assert f"observation://{failed_observation_id}/1" not in references
    assert "formal://documents/来源正文" not in references


def test_query_manifest_captures_bounded_tool_coverage_without_query_content() -> None:
    invocation_id = str(uuid4())
    context = {
        "project": {"model_baseline": {"query_snapshot_id": str(uuid4())}},
        "counts": {"entities": 120, "documents": 8},
        "graph_coverage": {
            "available_entities": 120,
            "included_entities": 100,
            "available_relations": 60,
            "included_relations": 55,
            "truncated": True,
        },
        "material_coverage": [{"source_document_id": str(uuid4()), "included_fragments": 4}],
        "management_context": {"coverage": {"observations": {"included": 0}}},
        "agent_execution": {
            "tool_results": [
                {
                    "invocation_id": invocation_id,
                    "status": "SUCCEEDED",
                    "result": {
                        "resource": "MANAGEMENT_OBSERVATIONS",
                        "query": "不应复制进覆盖清单的敏感查询",
                        "available": 750,
                        "scan_offset": 0,
                        "scanned": 500,
                        "next_scan_offset": 500,
                        "matching_in_scanned": 7,
                        "matching_count_is_lower_bound": True,
                        "ranking_scope": "CURRENT_SCAN_WINDOW",
                        "offset": 0,
                        "limit": 20,
                        "returned": 7,
                        "truncated": True,
                        "items": [{"id": str(uuid4()), "revision": 1}],
                    },
                },
                {
                    "invocation_id": str(uuid4()),
                    "status": "SUCCEEDED",
                    "result": {
                        "resource": "SEMANTIC_QUERY_RUN",
                        "id": str(uuid4()),
                        "dataset_id": str(uuid4()),
                        "query_snapshot_id": str(uuid4()),
                        "row_count": 50,
                        "total_rows": 67,
                        "offset": 0,
                        "limit": 50,
                        "returned": 50,
                        "next_offset": 50,
                        "truncated": True,
                        "rows": [{"root_entity_id": str(uuid4()), "_lineage": {}}],
                    },
                },
            ]
        },
    }

    [read_set] = AgentRuntimeService._merge_query_manifest(None, context)["read_sets"]
    coverage = read_set["coverage"]

    assert coverage["observations"] == {"included": 0}
    assert coverage["formal_context"]["counts"] == {"entities": 120, "documents": 8}
    assert coverage["formal_context"]["graph_coverage"]["truncated"] is True
    assert coverage["formal_context"]["material_coverage"][0]["included_fragments"] == 4
    scan = next(
        item for item in coverage["tool_scans"]
        if item["resource"] == "MANAGEMENT_OBSERVATIONS"
    )
    assert scan["resource"] == "MANAGEMENT_OBSERVATIONS"
    assert scan["invocation_id"] == invocation_id
    assert scan["available"] == 750
    assert scan["scanned"] == 500
    assert scan["next_scan_offset"] == 500
    assert scan["matching_count_is_lower_bound"] is True
    assert scan["source_reference_count"] == 1
    assert "query" not in scan
    assert "不应复制进覆盖清单的敏感查询" not in str(coverage)
    [semantic_scan] = [
        item for item in coverage["tool_scans"]
        if item["resource"] == "SEMANTIC_QUERY_RUN"
    ]
    assert semantic_scan["total_rows"] == 67
    assert semantic_scan["next_offset"] == 50
    assert semantic_scan["truncated"] is True


def test_claim_ledger_marks_context_not_shared_and_fact_without_source() -> None:
    claims = [
        AgentClaim(
            statement="管理层需要及时掌握现金情况",
            kind=AgentClaimKind.FACT,
            supporting_refs=["formal://documents/00000000-0000-4000-8000-000000000000"],
        ),
        AgentClaim(
            statement="可能存在职责冲突",
            kind=AgentClaimKind.HYPOTHESIS,
        ),
    ]

    not_shared = AgentRuntimeService._build_claim_ledger(
        str(uuid4()), claims[:1], {}, references_visible=False
    )
    no_source = AgentRuntimeService._build_claim_ledger(
        str(uuid4()), claims[1:], {}, references_visible=True
    )

    assert not_shared[0]["validation_status"] == "CONTEXT_NOT_SHARED"
    assert not_shared[0]["supporting_refs"] == []
    assert no_source[0]["validation_status"] == "NO_SUPPORTING_SOURCE"


def test_agent_run_persists_and_exposes_its_claim_ledger(
    client: TestClient, monkeypatch
) -> None:
    profile = client.post(
        "/api/v3/model-profiles",
        json={
            "name": "Claim ledger E2E",
            "provider": "MOCK",
            "base_url": "mock://local",
            "model": "deterministic",
        },
    )
    assert profile.status_code == 201, profile.text
    company = client.post("/api/v3/companies", json={"name": "结论台账样本公司"}).json()
    project = client.post(
        f"/api/v3/companies/{company['id']}/projects", json={"name": "结论台账样本项目"}
    ).json()
    index = ControlIndexWorker(
        client.app.state.database,
        client.app.state.control_database,
        client.app.state.settings,
    )
    # Freeze the legacy backfill boundary before this run creates an outbox event.
    assert index.synchronize_once() == 0
    thread = client.post(
        f"/api/v3/projects/{project['id']}/agent-threads",
        json={"agent_kind": "PROJECTION"},
    ).json()
    accepted = client.post(
        f"/api/v3/projects/{project['id']}/agent-threads/{thread['id']}/messages",
        json={"content": "查看企业材料并列出判断", "model_profile_id": profile.json()["id"]},
    )
    assert accepted.status_code == 202, accepted.text

    nonexistent_document_id = str(uuid4())
    monkeypatch.setattr(
        AgentRuntimeService,
        "_invoke_model",
        lambda *_args, **_kwargs: AgentStructuredOutput(
            content="可能存在职责边界不清，需要进一步确认。",
            claims=[
                AgentClaim(
                    statement="可能存在职责边界不清",
                    kind=AgentClaimKind.HYPOTHESIS,
                    supporting_refs=[f"formal://documents/{nonexistent_document_id}"],
                    unknowns=["尚未获得岗位访谈证据"],
                )
            ],
        ),
    )
    run_id = accepted.json()["run"]["id"]
    completed = client.post(f"/api/v3/projects/{project['id']}/agent-runs/{run_id}/execute")
    assert completed.status_code == 200, completed.text
    assert completed.json()["status"] == "COMPLETED"

    steps = client.get(f"/api/v3/projects/{project['id']}/agent-runs/{run_id}/steps")
    assert steps.status_code == 200, steps.text
    ledger_steps = [item for item in steps.json()["items"] if item["kind"] == "CLAIM_LEDGER"]
    assert len(ledger_steps) == 1
    assert ledger_steps[0]["output_payload"]["semantic_support_verified"] is False
    assert ledger_steps[0]["output_payload"]["entries"][0]["supporting_refs"] == []

    with client.app.state.database.session_factory() as source_session:
        pending_events = (
            source_session.query(AgentReadSetOutboxRow)
            .filter_by(run_id=run_id, delivered_at=None)
            .all()
        )
        assert len(pending_events) == 1
        assert set(pending_events[0].payload) == {"read_set"}
        event_ids = [item.event_id for item in pending_events]
        pending_step_events = (
            source_session.query(AgentStepOutboxRow)
            .filter_by(run_id=run_id, delivered_at=None)
            .all()
        )
        assert len(pending_step_events) == 1
        step_event_ids = [item.event_id for item in pending_step_events]
        with client.app.state.control_database.session_factory() as control_session:
            indexed_count = ControlIndexService(
                control_session
            ).synchronize_query_manifest_outbox(source_session, pending_events)
            control_session.commit()
        assert indexed_count >= 1
        with client.app.state.control_database.session_factory() as control_session:
            indexed_step_count = ControlIndexService(
                control_session
            ).synchronize_step_outbox(source_session, pending_step_events)
            control_session.commit()
        assert indexed_step_count == 1

    # Simulate a crash after the control-store commit but before the source ack.
    with client.app.state.database.session_factory() as source_session:
        replay_events = (
            source_session.query(AgentReadSetOutboxRow)
            .filter(AgentReadSetOutboxRow.event_id.in_(event_ids))
            .all()
        )
        with client.app.state.control_database.session_factory() as control_session:
            assert ControlIndexService(
                control_session
            ).synchronize_query_manifest_outbox(source_session, replay_events) == 0
            control_session.commit()

        replay_step_events = (
            source_session.query(AgentStepOutboxRow)
            .filter(AgentStepOutboxRow.event_id.in_(step_event_ids))
            .all()
        )
        with client.app.state.control_database.session_factory() as control_session:
            assert ControlIndexService(
                control_session
            ).synchronize_step_outbox(source_session, replay_step_events) == 0
            control_session.commit()

    index.synchronize_once()
    with client.app.state.database.session_factory() as source_session:
        delivered = (
            source_session.query(AgentReadSetOutboxRow)
            .filter(AgentReadSetOutboxRow.event_id.in_(event_ids))
            .one()
        )
        assert delivered.delivered_at is not None
        assert delivered.attempt_count >= 1
        delivered_step = (
            source_session.query(AgentStepOutboxRow)
            .filter(AgentStepOutboxRow.event_id.in_(step_event_ids))
            .one()
        )
        assert delivered_step.delivered_at is not None
        assert delivered_step.attempt_count >= 1
        delivered_step.kind = "MUTATED"
        with pytest.raises(IntegrityError, match="immutable Agent step outbox"):
            source_session.flush()
        source_session.rollback()
    indexed = client.get(
        f"/api/v3/projects/{project['id']}/control/claim-ledger?run_id={run_id}"
    )
    assert indexed.status_code == 200, indexed.text
    claim = indexed.json()["items"][0]
    assert claim["statement"] == "可能存在职责边界不清"
    assert claim["supporting_refs"] == []
    assert claim["submitted_supporting_refs"] == [
        f"formal://documents/{nonexistent_document_id}"
    ]
    assert claim["validation_status"] == "UNRESOLVED_REFERENCES_REMOVED"
