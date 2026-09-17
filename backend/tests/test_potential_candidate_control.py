from __future__ import annotations

from datetime import UTC, datetime
from uuid import uuid4

import pytest
from fastapi.testclient import TestClient

from enterprise_insight_backend.control import (
    ControlOperationReceiptRow,
    ControlPotentialCandidateService,
)
from enterprise_insight_backend.control_worker import ControlIndexWorker
from enterprise_insight_backend.models import AgentRunRow, AgentStepRow, AgentThreadRow
from enterprise_insight_backend.potential import (
    EvidenceStatus,
    PotentialCandidateDraft,
    PotentialEvidence,
    PotentialType,
)


def _project(client: TestClient) -> tuple[str, str]:
    company = client.post("/api/v3/companies", json={"name": "潜在候选测试企业"}).json()
    project = client.post(
        f"/api/v3/companies/{company['id']}/projects", json={"name": "潜在候选测试项目"}
    )
    assert project.status_code == 201, project.text
    return company["id"], project.json()["id"]


def _seed_proposal(client: TestClient, company_id: str, project_id: str) -> dict[str, object]:
    now = datetime.now(UTC)
    content = "采购申请经常在两个部门之间退回补充材料。"
    observation = client.post(
        f"/api/v3/projects/{project_id}/observations",
        json={"kind": "INTERVIEW", "title": "采购协作访谈", "content": content},
    )
    assert observation.status_code == 201, observation.text
    candidate = PotentialCandidateDraft(
        candidate_id=uuid4(),
        company_id=company_id,
        project_id=project_id,
        potential_type=PotentialType.PROBLEM_HYPOTHESIS,
        claim="采购审批等待时间可能与跨部门职责交接不清有关。",
        applicability_scope="本项目已调查的采购流程；尚待管理者核实。",
        task_source="复杂管理分析测试任务",
        supporting_evidence=[
            PotentialEvidence(
                source_ref=f"observation://{observation.json()['id']}/{observation.json()['revision']}",
                excerpt=content,
            )
        ],
        counterevidence=[],
        verification_method="检查后续两周的退回原因与交接时长。",
        evidence_status=EvidenceStatus.UNTESTED,
    )
    with client.app.state.database.session_factory() as session:
        thread = AgentThreadRow(
            project_id=project_id,
            agent_kind="MANAGEMENT",
            title="潜在候选来源任务",
        )
        session.add(thread)
        session.flush()
        run = AgentRunRow(
            thread_id=thread.id,
            project_id=project_id,
            agent_kind="MANAGEMENT",
            status="COMPLETED",
            context_manifest={"task_route": {"route": "COMPLEX"}},
            attempt_count=1,
        )
        session.add(run)
        session.flush()
        step = AgentStepRow(
            project_id=project_id,
            run_id=run.id,
            position=0,
            kind="POTENTIAL_CANDIDATE_PROPOSED",
            status="SUCCEEDED",
            input_payload={},
            output_payload={
                "candidate": candidate.model_dump(mode="json"),
                "candidate_hash": candidate.payload_hash(),
            },
            started_at=now,
            finished_at=now,
        )
        session.add(step)
        session.commit()
    worker = ControlIndexWorker(
        client.app.state.database,
        client.app.state.control_database,
        client.app.state.settings,
    )
    assert worker.synchronize_once() == 1
    return {
        "candidate_id": str(candidate.candidate_id),
        "payload_hash": candidate.payload_hash(),
        "payload": candidate.model_dump(mode="json"),
    }


def test_candidate_is_edited_confirmed_and_audited_idempotently(
    client: TestClient, monkeypatch: pytest.MonkeyPatch
) -> None:
    company_id, project_id = _project(client)
    proposal = _seed_proposal(client, company_id, project_id)
    base = f"/api/v3/projects/{project_id}/potential-candidates"
    candidate_id = str(proposal["candidate_id"])

    listed = client.get(base)
    assert listed.status_code == 200, listed.text
    current = listed.json()["items"][0]
    assert current["status"] == "PROPOSED"
    assert current["candidate"]["evidence_status"] == "UNTESTED"

    stale = client.post(
        f"{base}/{candidate_id}/accept",
        json={"expected_hash": "0" * 64},
    )
    assert stale.status_code == 409
    assert stale.json()["error"]["code"] == "CONTROL_CANDIDATE_STALE"

    edited_payload = dict(proposal["payload"])
    edited_payload["claim"] = "采购审批等待可能与职责交接信息不足有关，需要验证。"
    edited = client.patch(
        f"{base}/{candidate_id}",
        json={
            "expected_hash": current["payload_sha256"],
            "candidate": edited_payload,
            "reason": "管理者要求将结论改为待验证假设。",
        },
    )
    assert edited.status_code == 200, edited.text
    assert edited.json()["version"] == 2
    assert edited.json()["candidate"]["claim"].endswith("需要验证。")
    current_hash = edited.json()["payload_sha256"]

    original_finish = ControlPotentialCandidateService.finish_accept

    def simulate_control_store_interruption(*_args: object, **_kwargs: object) -> object:
        raise RuntimeError("simulated interruption after potential-store commit")

    monkeypatch.setattr(
        ControlPotentialCandidateService,
        "finish_accept",
        simulate_control_store_interruption,
    )
    with pytest.raises(RuntimeError, match="simulated interruption"):
        client.post(
            f"{base}/{candidate_id}/accept",
            json={"expected_hash": current_hash, "reason": "确认收入潜在库。"},
        )
    monkeypatch.setattr(ControlPotentialCandidateService, "finish_accept", original_finish)

    pending = client.get(f"{base}/{candidate_id}")
    assert pending.status_code == 200
    assert pending.json()["status"] == "ACCEPTING"
    pre_retry_records = client.get(f"/api/v3/projects/{project_id}/potential-records")
    assert pre_retry_records.json()["total"] == 1
    with client.app.state.control_database.session_factory() as control_session:
        receipt = control_session.query(ControlOperationReceiptRow).one()
        assert receipt.status == "RECEIVED"

    accepted = client.post(
        f"{base}/{candidate_id}/accept",
        json={"expected_hash": current_hash, "reason": "安全重试确认。"},
    )
    assert accepted.status_code == 200, accepted.text
    result = accepted.json()
    assert result["candidate"]["status"] == "ACCEPTED"
    assert result["candidate"]["potential_record_id"] == result["record"]["id"]
    assert result["record"]["human_status"] == "ACCEPTED"
    assert result["record"]["evidence_status"] == "UNTESTED"
    with client.app.state.control_database.session_factory() as control_session:
        receipt = control_session.query(ControlOperationReceiptRow).one()
        assert receipt.status == "COMMITTED"
        assert receipt.target_record_id == result["record"]["id"]

    retried = client.post(
        f"{base}/{candidate_id}/accept",
        json={"expected_hash": current_hash},
    )
    assert retried.status_code == 200, retried.text
    assert retried.json()["record"]["id"] == result["record"]["id"]
    records = client.get(f"/api/v3/projects/{project_id}/potential-records")
    assert records.status_code == 200
    assert records.json()["total"] == 1

    history = client.get(f"{base}/{candidate_id}/history")
    assert history.status_code == 200, history.text
    assert [item["operation"] for item in history.json()["items"]] == [
        "PROPOSED",
        "EDITED",
        "ACCEPTING",
        "ACCEPTED",
    ]


def test_candidate_can_be_rejected_without_creating_potential_record(client: TestClient) -> None:
    company_id, project_id = _project(client)
    proposal = _seed_proposal(client, company_id, project_id)
    candidate_id = str(proposal["candidate_id"])
    response = client.post(
        f"/api/v3/projects/{project_id}/potential-candidates/{candidate_id}/reject",
        json={
            "expected_hash": proposal["payload_hash"],
            "reason": "现有证据不足，暂不收入。",
        },
    )
    assert response.status_code == 200, response.text
    assert response.json()["status"] == "REJECTED"
    records = client.get(f"/api/v3/projects/{project_id}/potential-records")
    assert records.status_code == 200
    assert records.json()["total"] == 0


def test_candidate_edit_cannot_replace_evidence_or_change_scope(client: TestClient) -> None:
    company_id, project_id = _project(client)
    proposal = _seed_proposal(client, company_id, project_id)
    candidate_id = str(proposal["candidate_id"])
    payload = dict(proposal["payload"])
    payload["supporting_evidence"] = []
    response = client.patch(
        f"/api/v3/projects/{project_id}/potential-candidates/{candidate_id}",
        json={
            "expected_hash": proposal["payload_hash"],
            "candidate": payload,
            "reason": "尝试删除引用。",
        },
    )
    assert response.status_code == 422, response.text
    assert response.json()["error"]["code"] == "CONTROL_CANDIDATE_EVIDENCE_IMMUTABLE"

    wrong_project = client.post(
        "/api/v3/companies",
        json={"name": "其他企业"},
    ).json()
    other_project = client.post(
        f"/api/v3/companies/{wrong_project['id']}/projects",
        json={"name": "其他项目"},
    ).json()["id"]
    hidden = client.get(
        f"/api/v3/projects/{other_project}/potential-candidates/{candidate_id}"
    )
    assert hidden.status_code == 404
