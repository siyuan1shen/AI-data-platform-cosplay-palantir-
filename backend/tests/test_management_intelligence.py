from __future__ import annotations

import json

from fastapi.testclient import TestClient
from pytest import MonkeyPatch

from enterprise_insight_backend.management_intelligence import ManagementIntelligenceService


def _project(client: TestClient) -> str:
    company = client.post(
        "/api/v3/companies", json={"name": "管理智能测试公司", "industry": "制造业"}
    ).json()
    project = client.post(
        f"/api/v3/companies/{company['id']}/projects", json={"name": "管理智能闭环"}
    ).json()
    project_id = project["id"]
    response = client.post(f"/api/v3/projects/{project_id}/ontology/default-pack")
    assert response.status_code == 200, response.text
    return project_id


def _entity(
    client: TestClient,
    project_id: str,
    type_key: str,
    name: str,
    properties: dict[str, object] | None = None,
) -> dict[str, object]:
    response = client.post(
        f"/api/v3/projects/{project_id}/entities",
        json={
            "type_key": type_key,
            "name": name,
            "properties": properties or {},
            "viewpoint": "DESIGNED",
            "evidence": [],
        },
    )
    assert response.status_code == 201, response.text
    return response.json()


def test_failed_analysis_rolls_back_partial_signals(
    client: TestClient, monkeypatch: MonkeyPatch
) -> None:
    project_id = _project(client)
    _entity(client, project_id, "responsibility", "订单交付责任")

    def fail_reconcile(
        _service: ManagementIntelligenceService,
        _project_id: object,
        _run: object,
        _signals: object,
    ) -> list[object]:
        raise RuntimeError("synthetic reconcile failure")

    monkeypatch.setattr(ManagementIntelligenceService, "_reconcile", fail_reconcile)
    response = client.post(f"/api/v3/projects/{project_id}/management/analysis-runs", json={})
    assert response.status_code == 201, response.text
    run = response.json()
    assert run["status"] == "FAILED"
    signals = client.get(
        f"/api/v3/projects/{project_id}/management/signals",
        params={"run_id": run["id"]},
    )
    insights = client.get(
        f"/api/v3/projects/{project_id}/management/insights",
        params={"run_id": run["id"]},
    )
    assert signals.status_code == 200
    assert insights.status_code == 200
    assert signals.json()["items"] == []
    assert insights.json()["items"] == []


def test_metric_observations_are_versioned_by_period_dimension_and_source(
    client: TestClient,
) -> None:
    project_id = _project(client)
    metric = client.post(
        f"/api/v3/projects/{project_id}/management/metrics",
        json={
            "key": "finance.revenue",
            "name": "收入",
            "scope": "ENTERPRISE_OUTCOME",
            "direction": "HIGHER_IS_BETTER",
            "unit": "CNY",
        },
    ).json()
    first = client.post(
        f"/api/v3/projects/{project_id}/management/metrics/{metric['id']}/observations",
        json={
            "period_key": "2026-Q1",
            "period_start": "2026-01-01T00:00:00Z",
            "period_end": "2026-03-31T23:59:59Z",
            "dimensions": {"region": "east"},
            "observed_at": "2026-04-01T08:00:00Z",
            "value": 100,
            "source": "ERP",
        },
    )
    assert first.status_code == 201, first.text
    second = client.post(
        f"/api/v3/projects/{project_id}/management/metrics/{metric['id']}/observations",
        json={
            "period_key": "2026-Q1",
            "period_start": "2026-01-01T00:00:00Z",
            "period_end": "2026-03-31T23:59:59Z",
            "dimensions": {"region": "east"},
            "observed_at": "2026-04-02T08:00:00Z",
            "value": 110,
            "source": "ERP",
        },
    )
    assert second.status_code == 201, second.text
    assert second.json()["version"] == 2
    assert second.json()["supersedes_id"] == first.json()["id"]
    current = client.get(
        f"/api/v3/projects/{project_id}/management/metrics/{metric['id']}/observations"
    ).json()
    assert current["total"] == 1
    assert current["items"][0]["value"] == 110
    history = client.get(
        f"/api/v3/projects/{project_id}/management/metrics/{metric['id']}/observations",
        params={"include_history": True},
    ).json()
    assert history["total"] == 2
    assert {item["record_status"] for item in history["items"]} == {
        "ACTIVE",
        "SUPERSEDED",
    }
    revised = client.patch(
        f"/api/v3/projects/{project_id}/management/metrics/{metric['id']}/observations/{second.json()['id']}",
        json={
            "value": 105,
            "status": "UNKNOWN",
            "expected_revision": second.json()["revision"],
        },
    )
    assert revised.status_code == 200, revised.text
    assert revised.json()["version"] == 3
    assert revised.json()["dimensions"] == {"region": "east"}
    retired = client.post(
        f"/api/v3/projects/{project_id}/management/metrics/{metric['id']}/observations/{revised.json()['id']}/retire",
        json={"expected_revision": revised.json()["revision"]},
    )
    assert retired.status_code == 200, retired.text
    assert retired.json()["record_status"] == "RETRACTED"
    assert client.get(
        f"/api/v3/projects/{project_id}/management/metrics/{metric['id']}/observations"
    ).json()["total"] == 0


def test_design_and_outcome_signals_are_reconciled_and_reviewable(
    client: TestClient,
) -> None:
    project_id = _project(client)
    role = _entity(client, project_id, "role", "运营负责人")
    responsibility = _entity(client, project_id, "responsibility", "保障准时交付")
    strategy = _entity(client, project_id, "strategy_objective", "提高交付可靠性")
    outcome = _entity(client, project_id, "business_outcome", "客户按期收货")

    relation = client.post(
        f"/api/v3/projects/{project_id}/relations",
        json={
            "type_key": "holds_responsibility",
            "name": "运营负责人承担交付责任",
            "participants": [
                {"role_key": "accountable", "entity_id": role["id"]},
                {"role_key": "responsibility", "entity_id": responsibility["id"]},
            ],
            "properties": {},
            "viewpoint": "DESIGNED",
            "evidence": [],
        },
    )
    assert relation.status_code == 201, relation.text

    local_metric = client.post(
        f"/api/v3/projects/{project_id}/management/metrics",
        json={
            "key": "operations.output",
            "name": "车间产量",
            "scope": "LOCAL",
            "direction": "HIGHER_IS_BETTER",
            "owner_entity_id": role["id"],
            "strategy_entity_id": strategy["id"],
            "properties": {"proxy_risk": "HIGH", "controllable_by_owner": True},
        },
    )
    assert local_metric.status_code == 201, local_metric.text
    outcome_metric = client.post(
        f"/api/v3/projects/{project_id}/management/metrics",
        json={
            "key": "enterprise.on_time_delivery",
            "name": "按期交付率",
            "scope": "ENTERPRISE_OUTCOME",
            "direction": "HIGHER_IS_BETTER",
            "strategy_entity_id": strategy["id"],
            "outcome_entity_id": outcome["id"],
            "unit": "%",
        },
    )
    assert outcome_metric.status_code == 201, outcome_metric.text

    for metric_id, values, statuses in (
        (local_metric.json()["id"], [100, 130], ["ON_TARGET", "ON_TARGET"]),
        (outcome_metric.json()["id"], [95, 80], ["MISS", "MISS"]),
    ):
        for month, value, metric_status in zip((1, 2), values, statuses, strict=True):
            response = client.post(
                f"/api/v3/projects/{project_id}/management/metrics/{metric_id}/observations",
                json={
                    "period_key": f"2026-{month:02d}",
                    "observed_at": f"2026-{month:02d}-28T08:00:00Z",
                    "value": value,
                    "status": metric_status,
                    "source": "MES",
                },
            )
            assert response.status_code == 201, response.text

    meeting = client.post(
        f"/api/v3/projects/{project_id}/management/meetings",
        json={
            "title": "二月经营会",
            "occurred_at": "2026-02-28T09:00:00Z",
            "participant_entity_ids": [role["id"]],
            "topics": ["交付延期"],
            "action_items": [
                {
                    "title": "恢复交付稳定性",
                    "owner_entity_id": role["id"],
                    "status": "OPEN",
                }
            ],
            "decisions": [],
            "escalations": [],
            "evidence": [],
        },
    )
    assert meeting.status_code == 201, meeting.text

    run = client.post(
        f"/api/v3/projects/{project_id}/management/analysis-runs",
        json={"requested_by": "董事会"},
    )
    assert run.status_code == 201, run.text
    assert run.json()["status"] == "COMPLETED", run.text
    assert run.json()["design_signal_count"] >= 2
    assert run.json()["outcome_signal_count"] >= 3

    signals = client.get(
        f"/api/v3/projects/{project_id}/management/signals",
        params={"run_id": run.json()["id"]},
    ).json()["items"]
    signal_keys = {item["signal_key"] for item in signals}
    assert "accountability_without_authority" in signal_keys
    assert "proxy_metric_risk" in signal_keys
    assert "repeated_metric_miss" in signal_keys
    assert "local_global_divergence" in signal_keys
    assert "meeting_action_incomplete_control" in signal_keys

    insights = client.get(
        f"/api/v3/projects/{project_id}/management/insights",
        params={"run_id": run.json()["id"]},
    ).json()["items"]
    classifications = {item["classification"] for item in insights}
    assert "CORROBORATED" in classifications
    assert "UNEXPLAINED_ANOMALY" in classifications

    selected = next(item for item in insights if item["classification"] == "CORROBORATED")
    updated = client.patch(
        f"/api/v3/projects/{project_id}/management/insights/{selected['id']}",
        json={
            "status": "CONFIRMED",
            "management_feedback": "确认：运营负责人承担结果但缺少跨部门协调权。",
            "expected_revision": selected["revision"],
        },
    )
    assert updated.status_code == 200, updated.text
    assert updated.json()["status"] == "CONFIRMED"

    rerun = client.post(
        f"/api/v3/projects/{project_id}/management/analysis-runs", json={}
    )
    rerun_insights = client.get(
        f"/api/v3/projects/{project_id}/management/insights",
        params={"run_id": rerun.json()["id"]},
    ).json()["items"]
    repeated = next(item for item in rerun_insights if item["title"] == selected["title"])
    assert repeated["status"] == "CONFIRMED"
    assert repeated["management_feedback"] == "确认：运营负责人承担结果但缺少跨部门协调权。"
    assert repeated["issue_id"] == selected["issue_id"]
    assert repeated["occurrence_number"] == 2
    assert repeated["evidence_changed"] is False

    issues = client.get(f"/api/v3/projects/{project_id}/management/issues").json()[
        "items"
    ]
    issue = next(item for item in issues if item["id"] == selected["issue_id"])
    assert issue["occurrence_count"] == 2
    assert issue["status"] == "CONFIRMED"
    occurrences = client.get(
        f"/api/v3/projects/{project_id}/management/issues/{issue['id']}/occurrences"
    ).json()["items"]
    assert [item["occurrence_number"] for item in occurrences] == [2, 1]
    feedback = client.get(
        f"/api/v3/projects/{project_id}/management/issues/{issue['id']}/feedback"
    ).json()["items"]
    assert feedback[0]["to_status"] == "CONFIRMED"
    reopened = client.post(
        f"/api/v3/projects/{project_id}/management/issues/{issue['id']}/reopen",
        json={
            "reason": "发现新的跨部门审批证据，要求重新检查。",
            "expected_revision": issue["revision"],
        },
    )
    assert reopened.status_code == 200, reopened.text
    assert reopened.json()["status"] == "OPEN"


def test_accepted_tradeoff_information_request_and_export_complete_the_loop(
    client: TestClient,
) -> None:
    project_id = _project(client)
    role = _entity(client, project_id, "role", "财务负责人")
    responsibility = _entity(client, project_id, "responsibility", "控制经营风险")
    response = client.post(
        f"/api/v3/projects/{project_id}/relations",
        json={
            "type_key": "holds_responsibility",
            "participants": [
                {"role_key": "accountable", "entity_id": role["id"]},
                {"role_key": "responsibility", "entity_id": responsibility["id"]},
            ],
            "properties": {},
            "viewpoint": "DESIGNED",
            "evidence": [],
        },
    )
    assert response.status_code == 201, response.text

    tradeoff = client.post(
        f"/api/v3/projects/{project_id}/management/tradeoffs",
        json={
            "title": "集中审批换取风险一致性",
            "issue_family": "ACCOUNTABILITY",
            "description": "保留部分集中审批。",
            "benefit": "降低重大资金风险。",
            "cost": "财务负责人承担责任但业务授权较少。",
            "affected_entity_ids": [role["id"]],
            "monitoring_metric_ids": [],
            "status": "ACCEPTED",
            "accepted_by": "总经理",
        },
    )
    assert tradeoff.status_code == 201, tradeoff.text

    run = client.post(
        f"/api/v3/projects/{project_id}/management/analysis-runs", json={}
    )
    assert run.status_code == 201
    insights = client.get(
        f"/api/v3/projects/{project_id}/management/insights",
        params={"run_id": run.json()["id"]},
    ).json()["items"]
    assert any(item["classification"] == "ACCEPTED_TRADEOFF" for item in insights)

    request = client.post(
        f"/api/v3/projects/{project_id}/management/information-requests",
        json={
            "title": "补充授权边界",
            "question": "财务负责人可以独立决定哪些事项？",
            "reason": "验证有责无权是否超过已接受取舍的边界。",
            "priority": "HIGH",
            "target_entity_ids": [role["id"]],
        },
    )
    assert request.status_code == 201, request.text
    answered = client.patch(
        f"/api/v3/projects/{project_id}/management/information-requests/{request.json()['id']}",
        json={
            "status": "ANSWERED",
            "answer": "十万元以内可独立审批。",
            "expected_revision": request.json()["revision"],
        },
    )
    assert answered.status_code == 200, answered.text

    exported = client.post(
        f"/api/v3/projects/{project_id}/exports",
        json={"format": "json", "include_evidence": False, "include_lineage": True},
    )
    assert exported.status_code == 201, exported.text
    download = client.get(exported.json()["download_url"])
    assert download.status_code == 200
    bundle = json.loads(download.content)
    assert bundle["design_tradeoffs"]
    assert bundle["management_analysis_runs"]
    assert bundle["management_signals"]
    assert bundle["management_insights"]
    assert bundle["information_requests"][0]["status"] == "ANSWERED"


def test_targets_are_computed_and_same_period_revisions_are_not_three_periods(
    client: TestClient,
) -> None:
    project_id = _project(client)
    metric = client.post(
        f"/api/v3/projects/{project_id}/management/metrics",
        json={
            "key": "delivery.target",
            "name": "按期交付率",
            "target_value": 95,
            "direction": "HIGHER_IS_BETTER",
            "scope": "ENTERPRISE_OUTCOME",
        },
    ).json()
    for month in (1, 2, 3):
        response = client.post(
            f"/api/v3/projects/{project_id}/management/metrics/{metric['id']}/observations",
            json={"period_key": f"2026-{month:02d}", "value": 40},
        )
        assert response.status_code == 201, response.text
        assert response.json()["status"] == "MISS"
    run = client.post(f"/api/v3/projects/{project_id}/management/analysis-runs", json={})
    signals = client.get(
        f"/api/v3/projects/{project_id}/management/signals",
        params={"run_id": run.json()["id"]},
    ).json()["items"]
    assert any(item["signal_key"] == "repeated_metric_miss" for item in signals)

    revised = client.post(
        f"/api/v3/projects/{project_id}/management/metrics",
        json={
            "key": "delivery.revision",
            "name": "单期反复修订",
            "target_value": 95,
            "scope": "ENTERPRISE_OUTCOME",
        },
    ).json()
    for hour in (1, 2, 3):
        response = client.post(
            f"/api/v3/projects/{project_id}/management/metrics/{revised['id']}/observations",
            json={
                "period_key": "2026-04",
                "observed_at": f"2026-04-30T0{hour}:00:00Z",
                "value": 40,
            },
        )
        assert response.status_code == 201, response.text
    run = client.post(f"/api/v3/projects/{project_id}/management/analysis-runs", json={})
    signals = client.get(
        f"/api/v3/projects/{project_id}/management/signals",
        params={"run_id": run.json()["id"]},
    ).json()["items"]
    repeated = [
        item
        for item in signals
        if item["signal_key"] == "repeated_metric_miss"
        and item["facts"].get("metric_id") == revised["id"]
    ]
    assert repeated == []


def test_metric_definition_and_projection_entity_share_one_identity(
    client: TestClient,
) -> None:
    project_id = _project(client)
    created = client.post(
        f"/api/v3/projects/{project_id}/management/metrics",
        json={
            "key": "sales.win_rate",
            "name": "销售赢单率",
            "scope": "ENTERPRISE_OUTCOME",
            "direction": "HIGHER_IS_BETTER",
            "unit": "%",
            "target_value": 35,
            "properties": {"proxy_risk": "MEDIUM"},
        },
    )
    assert created.status_code == 201, created.text
    metric = created.json()
    assert metric["id"] == metric["entity_id"]
    entity = client.get(
        f"/api/v3/projects/{project_id}/entities/{metric['entity_id']}"
    ).json()
    assert entity["type_key"] == "metric"
    assert entity["name"] == "销售赢单率"
    assert entity["properties"]["target_value"] == 35
    assert entity["properties"]["direction"] == "HIGHER_IS_BETTER"

    updated = client.patch(
        f"/api/v3/projects/{project_id}/management/metrics/{metric['id']}",
        json={
            "name": "销售机会赢单率",
            "target_value": 40,
            "expected_revision": metric["revision"],
        },
    )
    assert updated.status_code == 200, updated.text
    entity = client.get(
        f"/api/v3/projects/{project_id}/entities/{metric['entity_id']}"
    ).json()
    assert entity["name"] == "销售机会赢单率"
    assert entity["properties"]["target_value"] == 40
    graph = client.post(f"/api/v3/projects/{project_id}/graph/query", json={}).json()
    assert [item["id"] for item in graph["entities"]] == [metric["entity_id"]]


def test_divergence_requires_shared_periods_and_outcome_can_support_multiple_risks(
    client: TestClient,
) -> None:
    project_id = _project(client)
    strategy = _entity(client, project_id, "strategy_objective", "提高企业交付能力")
    old_local = client.post(
        f"/api/v3/projects/{project_id}/management/metrics",
        json={
            "key": "old.local",
            "name": "历史局部效率",
            "scope": "LOCAL",
            "strategy_entity_id": strategy["id"],
        },
    ).json()
    current_outcome = client.post(
        f"/api/v3/projects/{project_id}/management/metrics",
        json={
            "key": "current.outcome",
            "name": "当前企业结果",
            "scope": "ENTERPRISE_OUTCOME",
            "strategy_entity_id": strategy["id"],
        },
    ).json()
    for metric, year, values in (
        (old_local, 2022, (100, 130)),
        (current_outcome, 2026, (95, 80)),
    ):
        for month, value in enumerate(values, start=1):
            response = client.post(
                f"/api/v3/projects/{project_id}/management/metrics/{metric['id']}/observations",
                json={
                    "period_key": f"{year}-{month:02d}",
                    "observed_at": f"{year}-{month:02d}-28T08:00:00Z",
                    "value": value,
                },
            )
            assert response.status_code == 201, response.text
    run = client.post(f"/api/v3/projects/{project_id}/management/analysis-runs", json={})
    signals = client.get(
        f"/api/v3/projects/{project_id}/management/signals",
        params={"run_id": run.json()["id"]},
    ).json()["items"]
    assert not any(item["signal_key"] == "local_global_divergence" for item in signals)

    for key in ("proxy.a", "proxy.b"):
        response = client.post(
            f"/api/v3/projects/{project_id}/management/metrics",
            json={
                "key": key,
                "name": key,
                "scope": "LOCAL",
                "strategy_entity_id": strategy["id"],
                "properties": {"proxy_risk": "HIGH"},
            },
        )
        assert response.status_code == 201, response.text
    missed = client.post(
        f"/api/v3/projects/{project_id}/management/metrics",
        json={
            "key": "shared.outcome",
            "name": "共享结果",
            "scope": "ENTERPRISE_OUTCOME",
            "strategy_entity_id": strategy["id"],
        },
    ).json()
    for month in (1, 2):
        response = client.post(
            f"/api/v3/projects/{project_id}/management/metrics/{missed['id']}/observations",
            json={"period_key": f"2026-{month:02d}", "value": 40, "status": "MISS"},
        )
        assert response.status_code == 201, response.text
    run = client.post(f"/api/v3/projects/{project_id}/management/analysis-runs", json={})
    insights = client.get(
        f"/api/v3/projects/{project_id}/management/insights",
        params={"run_id": run.json()["id"]},
    ).json()["items"]
    proxy_insights = [item for item in insights if "替代指标" in item["title"]]
    assert len(proxy_insights) == 2
    assert all(item["classification"] == "CORROBORATED" for item in proxy_insights)


def test_causal_hypothesis_is_typed_falsifiable_and_kept_out_of_trusted_graph(
    client: TestClient,
) -> None:
    project_id = _project(client)
    cause = _entity(client, project_id, "process", "集中审批流程")
    effect = _entity(client, project_id, "business_outcome", "客户交付周期")
    response = client.post(
        f"/api/v3/projects/{project_id}/causal-hypotheses",
        json={
            "title": "集中审批可能延长交付周期",
            "cause_entity_ids": [cause["id"]],
            "effect_entity_ids": [effect["id"]],
            "mechanism": "审批排队增加等待时间，并使异常订单不能及时决策。",
            "predictions": ["减少一个审批节点后，异常订单等待时间应下降"],
            "intervention_test": "选择同类订单做四周分组试验。",
            "alternative_explanations": ["排产能力不足也可能延长交付周期"],
            "uncertainties": ["尚未取得订单级审批时间戳"],
            "validation_questions": ["审批等待占总交付周期的比例是多少？"],
        },
    )
    assert response.status_code == 201, response.text
    assert response.json()["status"] == "EXPLORING"
    assert response.json()["predictions"]
    listed = client.get(f"/api/v3/projects/{project_id}/causal-hypotheses").json()
    assert listed["total"] == 1
    graph = client.post(f"/api/v3/projects/{project_id}/graph/query", json={}).json()
    assert len(graph["entities"]) == 2
    assert graph["relations"] == []
