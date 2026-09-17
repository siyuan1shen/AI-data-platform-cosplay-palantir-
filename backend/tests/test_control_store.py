from __future__ import annotations

from datetime import UTC, datetime

import pytest
from fastapi.testclient import TestClient
from sqlalchemy import inspect
from sqlalchemy.exc import IntegrityError

from enterprise_insight_backend.control import (
    ControlAuditIndexRow,
    ControlClaimLedgerRow,
    ControlDatabase,
    ControlEvidenceReadRow,
    ControlIndexService,
    ControlQueryManifestRow,
    ControlSchemaRow,
    ControlTaskRouteRow,
)
from enterprise_insight_backend.control_worker import ControlIndexWorker
from enterprise_insight_backend.errors import DomainError
from enterprise_insight_backend.models import (
    AgentRunRow,
    AgentStepRow,
    AgentThreadRow,
    ProjectRow,
)


def _project(client: TestClient) -> str:
    company = client.post("/api/v3/companies", json={"name": "控制库测试公司"}).json()
    project = client.post(
        f"/api/v3/companies/{company['id']}/projects", json={"name": "控制库测试项目"}
    )
    assert project.status_code == 201, project.text
    return project.json()["id"]


def _seed_route(client: TestClient, project_id: str) -> tuple[str, str]:
    now = datetime.now(UTC)
    with client.app.state.database.session_factory() as session:
        project = session.get(ProjectRow, project_id)
        assert project is not None
        thread = AgentThreadRow(
            project_id=project_id,
            agent_kind="MANAGEMENT",
            title="控制库测试任务",
        )
        session.add(thread)
        session.flush()
        run = AgentRunRow(
            thread_id=thread.id,
            project_id=project_id,
            agent_kind="MANAGEMENT",
            status="COMPLETED",
            context_manifest={},
            attempt_count=1,
        )
        session.add(run)
        session.flush()
        step = AgentStepRow(
            project_id=project_id,
            run_id=run.id,
            position=0,
            kind="ROUTE",
            status="SUCCEEDED",
            input_payload={"candidate_source": "test"},
            output_payload={
                "route": "SIMPLE",
                "task_kind": "SIMPLE_READ",
                "rule_version": "task-routing-v1",
                "execution_authorized": False,
            },
            started_at=now,
            finished_at=now,
        )
        session.add(step)
        session.commit()
        return run.id, step.id


def _seed_claim_ledger(client: TestClient, project_id: str) -> tuple[str, str, str]:
    run_id, _route_step_id = _seed_route(client, project_id)
    now = datetime.now(UTC)
    claim_id = "12d3c0c3-44d4-47de-8977-53fae3c9650e"
    with client.app.state.database.session_factory() as session:
        run = session.get(AgentRunRow, run_id)
        assert run is not None
        step = AgentStepRow(
            project_id=project_id,
            run_id=run.id,
            position=1,
            kind="CLAIM_LEDGER",
            status="COMPLETED",
            input_payload={"query_manifest_sha256": "a" * 64},
            output_payload={
                "entries": [
                    {
                        "claim_id": claim_id,
                        "ordinal": 1,
                        "statement": "示例结论",
                        "kind": "FACT",
                        "supporting_refs": [
                            "formal://evidence_fragments/11111111-1111-4111-8111-111111111111"
                        ],
                        "counterevidence_refs": [],
                        "submitted_supporting_refs": [
                            "formal://evidence_fragments/11111111-1111-4111-8111-111111111111"
                        ],
                        "submitted_counterevidence_refs": [],
                        "scope": None,
                        "unknowns": [],
                        "validation_status": "REFERENCES_RESOLVED_NOT_SEMANTICALLY_VERIFIED",
                        "rejected_reference_count": 0,
                    }
                ],
                "semantic_support_verified": False,
            },
            started_at=now,
            finished_at=now,
        )
        session.add(step)
        session.commit()
        return run_id, step.id, claim_id


def _synchronize(client: TestClient) -> int:
    worker = ControlIndexWorker(
        client.app.state.database,
        client.app.state.control_database,
        client.app.state.settings,
    )
    return worker.synchronize_once()


def test_route_index_is_idempotent_readable_and_scope_bound(client: TestClient) -> None:
    project_id = _project(client)
    run_id, _step_id = _seed_route(client, project_id)

    assert _synchronize(client) == 1
    assert _synchronize(client) == 0

    listed = client.get(f"/api/v3/projects/{project_id}/control/task-routes")
    assert listed.status_code == 200, listed.text
    assert listed.json()["total"] == 1
    route = listed.json()["items"][0]
    assert route["run_id"] == run_id
    assert route["route"] == "SIMPLE"
    assert route["route_payload"]["execution_authorized"] is False
    assert len(route["payload_sha256"]) == 64

    detail = client.get(
        f"/api/v3/projects/{project_id}/control/task-routes/{run_id}"
    )
    assert detail.status_code == 200
    assert detail.json()["source_step_id"]

    other_project = _project(client)
    scoped = client.get(
        f"/api/v3/projects/{other_project}/control/task-routes/{run_id}"
    )
    assert scoped.status_code == 404
    assert scoped.json()["error"]["code"] == "CONTROL_TASK_ROUTE_NOT_FOUND"


def test_control_route_and_audit_index_are_immutable(client: TestClient) -> None:
    project_id = _project(client)
    run_id, _step_id = _seed_route(client, project_id)
    _synchronize(client)
    control_database = client.app.state.control_database

    with control_database.session_factory() as session:
        route = session.get(ControlTaskRouteRow, run_id)
        assert route is not None
        route.route = "COMPLEX"
        with pytest.raises(IntegrityError, match="immutable control route"):
            session.flush()
        session.rollback()

    with control_database.session_factory() as session:
        event = session.query(ControlAuditIndexRow).one()
        session.delete(event)
        with pytest.raises(IntegrityError, match="immutable control audit"):
            session.flush()
        session.rollback()


def test_changed_source_route_is_not_silently_overwritten(client: TestClient) -> None:
    project_id = _project(client)
    run_id, step_id = _seed_route(client, project_id)
    _synchronize(client)

    with client.app.state.database.session_factory() as formal_session:
        step = formal_session.get(AgentStepRow, step_id)
        assert step is not None
        step.output_payload = {
            "route": "COMPLEX",
            "task_kind": "COMPLEX_ANALYSIS",
            "rule_version": "task-routing-v1",
            "execution_authorized": False,
        }
        formal_session.commit()

    with client.app.state.database.session_factory() as formal_session:
        with client.app.state.control_database.session_factory() as control_session:
            with pytest.raises(DomainError) as raised:
                ControlIndexService(control_session).synchronize_routes(formal_session)
            assert getattr(raised.value, "code", None) == "CONTROL_ROUTE_IMMUTABLE_CONFLICT"
            control_session.rollback()

    with client.app.state.control_database.session_factory() as control_session:
        route = control_session.get(ControlTaskRouteRow, run_id)
        assert route is not None
        assert route.route == "SIMPLE"


def test_claim_ledger_sync_is_idempotent_readable_and_project_scoped(
    client: TestClient,
) -> None:
    project_id = _project(client)
    run_id, _step_id, claim_id = _seed_claim_ledger(client, project_id)

    assert _synchronize(client) == 2
    assert _synchronize(client) == 0

    response = client.get(
        f"/api/v3/projects/{project_id}/control/claim-ledger?run_id={run_id}"
    )
    assert response.status_code == 200, response.text
    body = response.json()
    assert body["total"] == 1
    assert body["items"][0]["claim_id"] == claim_id
    assert body["items"][0]["validation_status"] == (
        "REFERENCES_RESOLVED_NOT_SEMANTICALLY_VERIFIED"
    )
    assert body["items"][0]["supporting_refs"]

    other_project = _project(client)
    scoped = client.get(
        f"/api/v3/projects/{other_project}/control/claim-ledger?run_id={run_id}"
    )
    assert scoped.status_code == 200
    assert scoped.json()["items"] == []


def test_claim_ledger_index_is_immutable(client: TestClient) -> None:
    project_id = _project(client)
    _seed_claim_ledger(client, project_id)
    _synchronize(client)

    with client.app.state.control_database.session_factory() as session:
        row = session.get(ControlClaimLedgerRow, "12d3c0c3-44d4-47de-8977-53fae3c9650e")
        assert row is not None
        row.statement = "被篡改的结论"
        with pytest.raises(IntegrityError, match="immutable control claim"):
            session.flush()
        session.rollback()


def test_query_manifest_and_evidence_reads_are_durable_scoped_and_content_free(
    client: TestClient,
) -> None:
    project_id = _project(client)
    run_id, _route_step_id = _seed_route(client, project_id)
    formal_uri = "formal://entities/11111111-1111-4111-8111-111111111111"
    observation_uri = "observation://22222222-2222-4222-8222-222222222222/3"
    potential_uri = "potential://33333333-3333-4333-8333-333333333333/2"
    virtual_uri = (
        "virtual-work://models/44444444-4444-4444-8444-444444444444/revisions/1"
    )
    snapshot_id = "55555555-5555-4555-8555-555555555555"
    with client.app.state.database.session_factory() as formal_session:
        run = formal_session.get(AgentRunRow, run_id)
        assert run is not None
        run.context_manifest = {
            "query_manifest": {
                "read_sets": [
                    {
                        "recorded_at": datetime.now(UTC).isoformat(),
                        "query_snapshot_id": snapshot_id,
                        "route": "COMPLEX",
                        "selected_stores": [
                            "formal_query_snapshot",
                            "management_observations",
                            "potential_records",
                            "reviewed_virtual_work",
                        ],
                        "management_context_access": {
                            "decision": "INCLUDED",
                            "management_observations_read": True,
                            "potential_records_read": True,
                            "virtual_work_read": True,
                        },
                        "coverage": {"entities": {"available": 4, "included": 2}},
                        "read_reference_uris": [
                            formal_uri,
                            observation_uri,
                            potential_uri,
                            virtual_uri,
                            "https://not-a-reference.invalid/private-text",
                        ],
                        "model_visible_reference_uris": [formal_uri, observation_uri],
                        "model_context_shared": False,
                    }
                ]
            }
        }
        formal_session.commit()

    assert _synchronize(client) == 6
    assert _synchronize(client) == 0
    manifests = client.get(
        f"/api/v3/projects/{project_id}/control/query-manifests?run_id={run_id}"
    )
    assert manifests.status_code == 200, manifests.text
    manifest = manifests.json()["items"][0]
    assert manifest["run_id"] == run_id
    assert manifest["query_snapshot_id"] == snapshot_id
    assert manifest["model_context_shared"] is False
    assert manifest["model_visible_reference_uris"] == []
    assert set(manifest["read_reference_uris"]) == {
        formal_uri,
        observation_uri,
        potential_uri,
        virtual_uri,
    }

    reads = client.get(
        f"/api/v3/projects/{project_id}/control/evidence-reads?run_id={run_id}"
    )
    assert reads.status_code == 200, reads.text
    assert reads.json()["total"] == 4
    assert {item["source_store"] for item in reads.json()["items"]} == {
        "formal_query_snapshot",
        "management_observations",
        "potential_records",
        "reviewed_virtual_work",
    }
    assert all(item["model_visible"] is False for item in reads.json()["items"])
    assert "private-text" not in reads.text

    other_project = _project(client)
    assert client.get(
        f"/api/v3/projects/{other_project}/control/query-manifests?run_id={run_id}"
    ).json()["items"] == []
    assert client.get(
        f"/api/v3/projects/{other_project}/control/evidence-reads?run_id={run_id}"
    ).json()["items"] == []


def test_query_manifests_and_evidence_reads_are_immutable_and_detect_source_drift(
    client: TestClient,
) -> None:
    project_id = _project(client)
    run_id, _route_step_id = _seed_route(client, project_id)
    reference_uri = "formal://entities/11111111-1111-4111-8111-111111111111"
    with client.app.state.database.session_factory() as formal_session:
        run = formal_session.get(AgentRunRow, run_id)
        assert run is not None
        run.context_manifest = {
            "query_manifest": {
                "read_sets": [
                    {
                        "recorded_at": datetime.now(UTC).isoformat(),
                        "query_snapshot_id": None,
                        "route": "SIMPLE",
                        "selected_stores": ["formal_query_snapshot"],
                        "management_context_access": {},
                        "coverage": {},
                        "read_reference_uris": [reference_uri],
                        "model_visible_reference_uris": [reference_uri],
                        "model_context_shared": True,
                    }
                ]
            }
        }
        formal_session.commit()
    _synchronize(client)

    with client.app.state.control_database.session_factory() as session:
        manifest = session.query(ControlQueryManifestRow).one()
        manifest_id = manifest.id
        manifest.route = "CHANGED"
        with pytest.raises(IntegrityError, match="immutable control query manifest"):
            session.flush()
        session.rollback()

    with client.app.state.control_database.session_factory() as session:
        evidence = session.query(ControlEvidenceReadRow).one()
        evidence.reference_uri = "formal://entities/changed"
        with pytest.raises(IntegrityError, match="immutable control evidence read"):
            session.flush()
        session.rollback()

    with client.app.state.database.session_factory() as formal_session:
        run = formal_session.get(AgentRunRow, run_id)
        assert run is not None
        changed_manifest = dict(run.context_manifest)
        changed_query_manifest = dict(changed_manifest["query_manifest"])
        changed_read_sets = list(changed_query_manifest["read_sets"])
        changed_read_sets[0] = {
            **changed_read_sets[0],
            "coverage": {"entities": {"available": 2, "included": 2}},
        }
        changed_query_manifest["read_sets"] = changed_read_sets
        changed_manifest["query_manifest"] = changed_query_manifest
        run.context_manifest = changed_manifest
        formal_session.commit()
    with client.app.state.database.session_factory() as formal_session:
        with client.app.state.control_database.session_factory() as control_session:
            with pytest.raises(DomainError) as raised:
                ControlIndexService(control_session).backfill_query_manifests(formal_session)
            assert raised.value.code == "CONTROL_QUERY_MANIFEST_IMMUTABLE_CONFLICT"
            control_session.rollback()

    with client.app.state.control_database.session_factory() as session:
        assert session.get(ControlQueryManifestRow, manifest_id) is not None


@pytest.mark.parametrize("prior_version", [1, 2, 3, 4, 5])
def test_control_schema_additive_upgrade_preserves_existing_routes(
    tmp_path, prior_version: int
) -> None:
    database = ControlDatabase(f"sqlite:///{(tmp_path / 'control.db').as_posix()}")
    database.create_schema()
    now = datetime.now(UTC)
    suffix = f"{prior_version:012d}"
    run_id = f"00000000-0000-4000-8000-{suffix}"
    source_step_id = f"00000000-0000-4000-8001-{suffix}"
    with database.session_factory.begin() as session:
        session.add(
            ControlTaskRouteRow(
                run_id=run_id,
                source_step_id=source_step_id,
                company_id="fa2a7cc6-ac5a-45d2-842b-e8e8b48c13f2",
                project_id="e733b92a-df51-4aca-9590-f9af83a13517",
                thread_id="e2b80e23-b69f-4400-8bcb-3d932d43cc0f",
                agent_kind="MANAGEMENT",
                task_kind="SIMPLE_READ",
                route="SIMPLE",
                rule_version="task-routing-v1",
                route_payload={"route": "SIMPLE", "execution_authorized": False},
                payload_sha256="0" * 64,
                recorded_at=now,
            )
        )
    with database.engine.begin() as connection:
        connection.exec_driver_sql("DROP TABLE control_claim_ledger")
        connection.exec_driver_sql("DROP TABLE control_evidence_reads")
        connection.exec_driver_sql("DROP TABLE control_query_manifests")
        connection.exec_driver_sql("DROP TABLE control_potential_candidate_events")
        connection.exec_driver_sql("DROP TABLE control_potential_candidates")
        connection.exec_driver_sql(
            f"UPDATE control_schema SET version = {prior_version} WHERE id = 1"
        )

    database.create_schema()
    with database.session_factory() as session:
        assert session.get(ControlSchemaRow, 1).version == 6
        route = session.get(ControlTaskRouteRow, run_id)
        assert route is not None
        assert route.route == "SIMPLE"
    database.dispose()


def test_control_schema_v4_adds_backfill_checkpoint_column(tmp_path) -> None:
    database = ControlDatabase(f"sqlite:///{(tmp_path / 'control-v4.db').as_posix()}")
    with database.engine.begin() as connection:
        connection.exec_driver_sql(
            "CREATE TABLE control_schema ("
            "id INTEGER PRIMARY KEY, version INTEGER NOT NULL, updated_at DATETIME NOT NULL)"
        )
        connection.exec_driver_sql(
            "INSERT INTO control_schema (id, version, updated_at) "
            "VALUES (1, 4, '2026-09-12 00:00:00')"
        )

    database.create_schema()
    with database.session_factory() as session:
        schema = session.get(ControlSchemaRow, 1)
        assert schema is not None
        assert schema.version == 6
        assert schema.query_manifest_backfill_completed is False
        assert schema.step_index_backfill_completed is False
    database.dispose()


def test_control_schema_refuses_future_version_before_modifying_it(tmp_path) -> None:
    database = ControlDatabase(f"sqlite:///{(tmp_path / 'control-vfuture.db').as_posix()}")
    with database.engine.begin() as connection:
        connection.exec_driver_sql(
            "CREATE TABLE control_schema ("
            "id INTEGER PRIMARY KEY, version INTEGER NOT NULL, updated_at DATETIME NOT NULL)"
        )
        connection.exec_driver_sql(
            "INSERT INTO control_schema (id, version, updated_at) "
            "VALUES (1, 7, '2026-09-12 00:00:00')"
        )

    with pytest.raises(RuntimeError, match="版本高于"):
        database.create_schema()
    columns = {
        item["name"] for item in inspect(database.engine).get_columns("control_schema")
    }
    assert "query_manifest_backfill_completed" not in columns
    assert "step_index_backfill_completed" not in columns
    database.dispose()
