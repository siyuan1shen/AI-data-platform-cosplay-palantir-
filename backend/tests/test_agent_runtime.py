from __future__ import annotations

import json
from uuid import UUID, uuid4

from fastapi.testclient import TestClient

from enterprise_insight_backend.actions import ActionService
from enterprise_insight_backend.agent_runtime import AgentRuntimeService
from enterprise_insight_backend.errors import DomainError
from enterprise_insight_backend.models import AgentRunRow
from enterprise_insight_backend.schemas import AgentActionProposal, AgentStructuredOutput


def _setup(client: TestClient) -> tuple[str, str]:
    profile = client.post(
        "/api/v3/model-profiles",
        json={
            "name": "本地测试模型",
            "provider": "MOCK",
            "base_url": "mock://local",
            "model": "deterministic",
        },
    )
    assert profile.status_code == 201, profile.text
    company = client.post("/api/v3/companies", json={"name": "Agent闭环样本"}).json()
    project = client.post(
        f"/api/v3/companies/{company['id']}/projects",
        json={"name": "Agent闭环"},
    ).json()
    installed = client.post(f"/api/v3/projects/{project['id']}/ontology/default-pack")
    assert installed.status_code == 200, installed.text
    return project["id"], profile.json()["id"]


def test_agent_uses_builtin_local_rules_without_model_profile(client: TestClient) -> None:
    """A fresh local install can run deterministic Agent tasks before model setup."""
    company = client.post("/api/v3/companies", json={"name": "无模型配置验收企业"}).json()
    project = client.post(
        f"/api/v3/companies/{company['id']}/projects",
        json={"name": "本地规则验收"},
    ).json()
    thread = client.post(
        f"/api/v3/projects/{project['id']}/agent-threads",
        json={"agent_kind": "PROJECTION"},
    ).json()
    accepted = client.post(
        f"/api/v3/projects/{project['id']}/agent-threads/{thread['id']}/messages",
        json={"content": "查询企业当前模型"},
    )
    assert accepted.status_code == 202, accepted.text

    completed = client.post(
        f"/api/v3/projects/{project['id']}/agent-runs/{accepted.json()['run']['id']}/execute"
    )
    assert completed.status_code == 200, completed.text
    payload = completed.json()
    assert payload["status"] == "COMPLETED", payload
    assert payload["context_manifest"]["model_profile_id"] == "local-rules"
    assert payload["error"] is None

    messages = client.get(
        f"/api/v3/projects/{project['id']}/agent-threads/{thread['id']}/messages"
    ).json()
    assert messages["items"][-1]["role"] == "ASSISTANT"
    assert "已读取当前项目上下文" in messages["items"][-1]["content"]


def test_management_agent_executes_safe_action_and_reads_it_back(client: TestClient) -> None:
    project_id, profile_id = _setup(client)
    thread = client.post(
        f"/api/v3/projects/{project_id}/agent-threads",
        json={"agent_kind": "MANAGEMENT"},
    ).json()
    accepted = client.post(
        f"/api/v3/projects/{project_id}/agent-threads/{thread['id']}/messages",
        json={
            "content": "保存假设：销售审批可能成为流程瓶颈",
            "model_profile_id": profile_id,
        },
    )
    assert accepted.status_code == 202, accepted.text
    run_id = accepted.json()["run"]["id"]
    completed = client.post(f"/api/v3/projects/{project_id}/agent-runs/{run_id}/execute")
    assert completed.status_code == 200, completed.text
    assert completed.json()["status"] == "COMPLETED", completed.text
    action_ids = completed.json()["action_invocation_ids"]
    assert len(action_ids) == 1

    messages = client.get(
        f"/api/v3/projects/{project_id}/agent-threads/{thread['id']}/messages"
    ).json()
    assert messages["total"] == 2
    assert "工具回读" in messages["items"][-1]["content"]
    invocation = client.get(f"/api/v3/projects/{project_id}/action-invocations/{action_ids[0]}")
    assert invocation.status_code == 200, invocation.text
    assert invocation.json()["status"] == "SUCCEEDED"
    assert client.get(f"/api/v3/projects/{project_id}/hypotheses").json()["total"] == 1
    steps = client.get(f"/api/v3/projects/{project_id}/agent-runs/{run_id}/steps").json()
    assert [item["kind"] for item in steps["items"]] == ["MODEL", "TOOL", "MODEL"]


def test_context_budget_pause_retains_checkpoint_and_can_resume(client, monkeypatch) -> None:
    project_id, profile_id = _setup(client)
    thread = client.post(f"/api/v3/projects/{project_id}/agent-threads", json={
        "agent_kind": "MANAGEMENT",
    }).json()
    accepted = client.post(
        f"/api/v3/projects/{project_id}/agent-threads/{thread['id']}/messages",
        json={"content": "查询企业", "model_profile_id": profile_id},
    ).json()
    base = f"/api/v3/projects/{project_id}/agent-runs/{accepted['run']['id']}"

    def too_large(*args, **kwargs):
        raise DomainError("AGENT_CONTEXT_BUDGET_EXCEEDED", "完整上下文超出预算")

    monkeypatch.setattr(AgentRuntimeService, "_invoke_model", too_large)
    paused = client.post(f"{base}/execute")
    assert paused.status_code == 200, paused.text
    assert paused.json()["status"] == "BUDGET_EXHAUSTED"
    assert paused.json()["error"]["code"] == "AGENT_CONTEXT_BUDGET_EXCEEDED"
    monkeypatch.setattr(AgentRuntimeService, "_invoke_model", lambda *args, **kwargs:
        AgentStructuredOutput(content="范围调整后完成查询")
    )
    resumed = client.post(f"{base}/continue")
    assert resumed.status_code == 202, resumed.text
    assert resumed.json()["status"] == "COMPLETED"


def test_management_information_request_quick_prompt_creates_a_real_request(
    client: TestClient,
) -> None:
    project_id, profile_id = _setup(client)
    thread = client.post(
        f"/api/v3/projects/{project_id}/agent-threads",
        json={"agent_kind": "MANAGEMENT"},
    ).json()
    accepted = client.post(
        f"/api/v3/projects/{project_id}/agent-threads/{thread['id']}/messages",
        json={
            "content": "请识别当前判断缺少的关键信息，并提出一条可执行的信息请求供我审核。",
            "model_profile_id": profile_id,
        },
    ).json()
    completed = client.post(
        f"/api/v3/projects/{project_id}/agent-runs/{accepted['run']['id']}/execute"
    )
    assert completed.status_code == 200, completed.text
    assert completed.json()["status"] == "COMPLETED"
    action_ids = completed.json()["action_invocation_ids"]
    assert len(action_ids) == 1
    action = client.get(
        f"/api/v3/projects/{project_id}/action-invocations/{action_ids[0]}"
    ).json()
    assert action["status"] == "SUCCEEDED"
    assert action["action_key"] == "save_information_request"
    assert action["result"]["id"]


def test_projection_agent_action_stops_for_approval(client: TestClient) -> None:
    project_id, profile_id = _setup(client)
    thread = client.post(
        f"/api/v3/projects/{project_id}/agent-threads",
        json={"agent_kind": "PROJECTION"},
    ).json()
    accepted = client.post(
        f"/api/v3/projects/{project_id}/agent-threads/{thread['id']}/messages",
        json={"content": "创建岗位：销售主管", "model_profile_id": profile_id},
    ).json()
    run_id = accepted["run"]["id"]
    waiting = client.post(f"/api/v3/projects/{project_id}/agent-runs/{run_id}/execute")
    assert waiting.status_code == 200, waiting.text
    assert waiting.json()["status"] == "WAITING_REVIEW"
    action_id = waiting.json()["action_invocation_ids"][0]
    assert (
        client.post(f"/api/v3/projects/{project_id}/graph/query", json={}).json()["entities"] == []
    )

    pending = client.get(f"/api/v3/projects/{project_id}/action-invocations/{action_id}")
    assert pending.status_code == 200, pending.text
    assert pending.json()["status"] == "WAITING_APPROVAL"
    approved = client.post(
        f"/api/v3/projects/{project_id}/action-invocations/{action_id}/approve",
        json={"approved_by": "developer"},
    )
    assert approved.status_code == 200, approved.text
    resumed = client.post(f"/api/v3/projects/{project_id}/agent-runs/{run_id}/continue")
    assert resumed.status_code == 202, resumed.text
    assert resumed.json()["status"] == "COMPLETED"
    executed = client.get(f"/api/v3/projects/{project_id}/action-invocations/{action_id}")
    assert executed.json()["status"] == "SUCCEEDED"
    graph = client.post(f"/api/v3/projects/{project_id}/graph/query", json={}).json()
    assert [item["name"] for item in graph["entities"]] == ["销售主管"]


def test_system_ontology_agent_creates_approved_semantic_mapping(client: TestClient) -> None:
    project_id, profile_id = _setup(client)
    source = client.post(
        f"/api/v3/projects/{project_id}/source-systems",
        json={"name": "HR CSV", "kind": "FILE", "connection_profile": {}},
    ).json()
    thread = client.post(
        f"/api/v3/projects/{project_id}/agent-threads",
        json={"agent_kind": "SYSTEM_ONTOLOGY"},
    ).json()
    mapping = {
        "source_system_id": source["id"],
        "source_asset": "*",
        "source_field": "岗位编码",
        "target_type_key": "role",
        "target_property_key": "__stable_key__",
        "transform_expression": "strip",
    }
    accepted = client.post(
        f"/api/v3/projects/{project_id}/agent-threads/{thread['id']}/messages",
        json={
            "content": f"创建映射：{json.dumps(mapping, ensure_ascii=False)}",
            "model_profile_id": profile_id,
        },
    ).json()
    run = client.post(
        f"/api/v3/projects/{project_id}/agent-runs/{accepted['run']['id']}/execute"
    ).json()
    assert run["status"] == "WAITING_REVIEW"
    assert len(run["action_invocation_ids"]) == 1
    invocation_id = run["action_invocation_ids"][0]

    preview = client.get(f"/api/v3/projects/{project_id}/action-invocations/{invocation_id}")
    assert preview.status_code == 200, preview.text
    assert preview.json()["status"] == "WAITING_APPROVAL"
    client.post(
        f"/api/v3/projects/{project_id}/action-invocations/{invocation_id}/approve",
        json={"approved_by": "integration-developer"},
    ).raise_for_status()
    resumed = client.post(
        f"/api/v3/projects/{project_id}/agent-runs/{accepted['run']['id']}/continue"
    )
    assert resumed.status_code == 202, resumed.text
    assert resumed.json()["status"] == "COMPLETED"
    executed = client.get(f"/api/v3/projects/{project_id}/action-invocations/{invocation_id}")
    assert executed.json()["result"]["resource"] == "SEMANTIC_MAPPING"
    mappings = client.get(f"/api/v3/projects/{project_id}/semantic-mappings").json()
    assert mappings["total"] == 1
    assert mappings["items"][0]["source_field"] == "岗位编码"


def test_system_ontology_agent_suggests_read_only_mapping_candidates(client: TestClient) -> None:
    project_id, profile_id = _setup(client)
    source = client.post(
        f"/api/v3/projects/{project_id}/source-systems",
        json={"name": "HR 字段样本", "kind": "FILE", "connection_profile": {}},
    ).json()
    preview = client.post(
        f"/api/v3/projects/{project_id}/source-systems/{source['id']}/imports/preview",
        files={
            "file": (
                "roles.csv",
                "id,name,headcount,unmapped\nrole.sales,销售主管,3,保留\n".encode(),
                "text/csv",
            )
        },
        data={"kind": "CSV"},
    )
    assert preview.status_code == 200, preview.text
    confirmed = client.post(
        f"/api/v3/projects/{project_id}/imports/confirm",
        json={"preview_id": preview.json()["id"]},
    )
    assert confirmed.status_code == 200, confirmed.text

    suggestions = client.get(
        f"/api/v3/projects/{project_id}/semantic-mapping-suggestions",
        params={"target_type_key": "role", "source_system_id": source["id"]},
    )
    assert suggestions.status_code == 200, suggestions.text
    payload = suggestions.json()
    assert payload["total"] == 3
    assert {item["source_field"] for item in payload["items"]} == {
        "id",
        "name",
        "headcount",
    }
    assert all(item["safe_to_auto_apply"] is False for item in payload["items"])
    assert all(item["existing_mapping_id"] is None for item in payload["items"])
    assert client.get(f"/api/v3/projects/{project_id}/semantic-mappings").json()["total"] == 0

    created_mapping = client.post(
        f"/api/v3/projects/{project_id}/semantic-mappings",
        json={
            "source_system_id": source["id"],
            "source_asset": "roles.csv",
            "source_field": "id",
            "target_type_key": "role",
            "target_property_key": "__stable_key__",
            "transform_expression": "strip",
        },
    )
    assert created_mapping.status_code == 201, created_mapping.text
    assert created_mapping.json()["status"] == "DRAFT"
    without_existing = client.get(
        f"/api/v3/projects/{project_id}/semantic-mapping-suggestions",
        params={
            "target_type_key": "role",
            "source_system_id": source["id"],
        },
    )
    assert without_existing.status_code == 200, without_existing.text
    assert {item["source_field"] for item in without_existing.json()["items"]} == {
        "name",
        "headcount",
    }
    with_existing = client.get(
        f"/api/v3/projects/{project_id}/semantic-mapping-suggestions",
        params={
            "target_type_key": "role",
            "source_system_id": source["id"],
            "include_existing": "true",
        },
    )
    assert with_existing.status_code == 200, with_existing.text
    existing_items = {
        item["source_field"]: item for item in with_existing.json()["items"]
    }
    assert set(existing_items) == {"id", "name", "headcount"}
    assert existing_items["id"]["existing_mapping_id"] == created_mapping.json()["id"]
    assert existing_items["id"]["safe_to_auto_apply"] is False

    invalid_target = client.get(
        f"/api/v3/projects/{project_id}/semantic-mapping-suggestions",
        params={
            "target_type_key": "contains",
            "source_system_id": source["id"],
        },
    )
    assert invalid_target.status_code == 422, invalid_target.text
    assert invalid_target.json()["error"]["code"] == "MAPPING_TARGET_ENTITY_TYPE_REQUIRED"

    missing_source = client.get(
        f"/api/v3/projects/{project_id}/semantic-mapping-suggestions",
        params={"target_type_key": "role", "source_system_id": str(uuid4())},
    )
    assert missing_source.status_code == 404, missing_source.text
    assert missing_source.json()["error"]["code"] == "SOURCE_SYSTEM_NOT_FOUND"

    missing_asset = client.get(
        f"/api/v3/projects/{project_id}/semantic-mapping-suggestions",
        params={
            "target_type_key": "role",
            "source_system_id": source["id"],
            "source_asset": "missing.csv",
        },
    )
    assert missing_asset.status_code == 404, missing_asset.text
    assert missing_asset.json()["error"]["code"] == "SOURCE_ASSET_NOT_FOUND"

    thread = client.post(
        f"/api/v3/projects/{project_id}/agent-threads",
        json={"agent_kind": "SYSTEM_ONTOLOGY"},
    ).json()
    accepted = client.post(
        f"/api/v3/projects/{project_id}/agent-threads/{thread['id']}/messages",
        json={
            "content": (
                "查找映射候选："
                + json.dumps(
                    {
                        "target_type_key": "role",
                        "source_system_id": source["id"],
                        "source_asset": "roles.csv",
                    },
                    ensure_ascii=False,
                )
            ),
            "model_profile_id": profile_id,
        },
    )
    assert accepted.status_code == 202, accepted.text
    run = client.post(
        f"/api/v3/projects/{project_id}/agent-runs/{accepted.json()['run']['id']}/execute"
    )
    assert run.status_code == 200, run.text
    assert run.json()["status"] == "COMPLETED"
    invocation_id = run.json()["action_invocation_ids"][0]
    invocation = client.get(
        f"/api/v3/projects/{project_id}/action-invocations/{invocation_id}"
    )
    assert invocation.status_code == 200, invocation.text
    assert invocation.json()["action_key"] == "suggest_semantic_mappings"
    assert invocation.json()["result"]["resource"] == "SEMANTIC_MAPPING_SUGGESTIONS"
    assert invocation.json()["result"]["total"] == 2
    assert client.get(f"/api/v3/projects/{project_id}/semantic-mappings").json()["total"] == 1


def test_management_agent_can_run_the_persistent_cross_analysis(client: TestClient) -> None:
    project_id, profile_id = _setup(client)
    thread = client.post(
        f"/api/v3/projects/{project_id}/agent-threads",
        json={"agent_kind": "MANAGEMENT"},
    ).json()
    accepted = client.post(
        f"/api/v3/projects/{project_id}/agent-threads/{thread['id']}/messages",
        json={
            "content": "请运行管理分析，结合当前企业投影、管理指标和近期记录，"
            "生成需要我确认的管理洞察与后续动作提案。",
            "model_profile_id": profile_id,
        },
    ).json()
    run = client.post(
        f"/api/v3/projects/{project_id}/agent-runs/{accepted['run']['id']}/execute"
    ).json()
    assert run["status"] == "COMPLETED"
    assert len(run["action_invocation_ids"]) == 1
    invocation_id = run["action_invocation_ids"][0]

    executed = client.get(f"/api/v3/projects/{project_id}/action-invocations/{invocation_id}")
    assert executed.status_code == 200, executed.text
    assert executed.json()["status"] == "SUCCEEDED"
    assert executed.json()["result"]["resource"] == "MANAGEMENT_ANALYSIS_RUN"
    analysis_runs = client.get(f"/api/v3/projects/{project_id}/management/analysis-runs").json()
    assert analysis_runs["total"] == 1
    assert analysis_runs["items"][0]["status"] == "COMPLETED"


def test_management_agent_uses_same_published_graph_as_executive(client: TestClient) -> None:
    project_id, _ = _setup(client)
    entity = client.post(
        f"/api/v3/projects/{project_id}/entities",
        json={"type_key": "role", "name": "已发布岗位", "properties": {}},
    ).json()
    client.post(
        f"/api/v3/projects/{project_id}/ontology/releases",
        json={"label": "本体基线"},
    ).raise_for_status()
    project = client.get(f"/api/v3/projects/{project_id}").json()
    client.post(
        f"/api/v3/projects/{project_id}/publications",
        json={"label": "管理基线", "expected_project_revision": project["revision"]},
    ).raise_for_status()
    renamed = client.patch(
        f"/api/v3/projects/{project_id}/entities/{entity['id']}",
        json={"name": "尚未发布的新名称", "expected_revision": entity["revision"]},
    )
    assert renamed.status_code == 200, renamed.text
    thread = client.post(
        f"/api/v3/projects/{project_id}/agent-threads",
        json={"agent_kind": "MANAGEMENT"},
    ).json()

    with client.app.state.database.session_factory() as session:
        context = AgentRuntimeService(session, client.app.state.settings)._build_context(
            UUID(project_id), thread["id"], []
        )
    executive = client.get(f"/api/v3/projects/{project_id}/executive/context").json()
    assert context["entities"][0]["name"] == "已发布岗位"
    assert context["entities"] == executive["graph"]["entities"]
    assert context["project"]["model_baseline"]["kind"] == "RELEASE"
    create_scenario = next(
        item for item in context["available_actions"] if item["key"] == "create_scenario"
    )
    assert create_scenario["input_schema"]["type"] == "object"
    assert {"name", "goal"} <= set(create_scenario["input_schema"]["properties"])


def test_management_agent_reads_separate_observation_and_potential_stores(
    client: TestClient,
) -> None:
    project_id, _ = _setup(client)
    project = client.get(f"/api/v3/projects/{project_id}").json()
    observation = client.post(
        f"/api/v3/projects/{project_id}/observations",
        json={
            "kind": "MEETING",
            "title": "交付会议记录",
            "content": "销售临时插单后，生产计划需要多次调整。",
        },
    )
    assert observation.status_code == 201, observation.text
    other_company = client.post(
        "/api/v3/companies", json={"name": "上下文隔离测试的另一企业"}
    ).json()
    other_project = client.post(
        f"/api/v3/companies/{other_company['id']}/projects",
        json={"name": "不可见项目"},
    ).json()
    other_observation = client.post(
        f"/api/v3/projects/{other_project['id']}/observations",
        json={
            "kind": "MEETING",
            "title": "不应串入的采购问题",
            "content": "采购流程跨公司隔离样本。",
        },
    )
    assert other_observation.status_code == 201, other_observation.text
    potential = client.post(
        f"/api/v3/projects/{project_id}/potential-records",
        json={
            "company_id": project["company_id"],
            "project_id": project_id,
            "potential_type": "PROBLEM_HYPOTHESIS",
            "claim": "临时插单可能增加排产变更",
            "applicability_scope": "当前项目的订单交付流程",
            "task_source": "测试管理上下文库隔离",
            "supporting_evidence": [
                {
                    "source_ref": f"observation:{observation.json()['id']}",
                    "excerpt": "销售临时插单后，生产计划需要多次调整。",
                }
            ],
            "counterevidence": [],
            "verification_method": "比较插单和非插单订单的排产变更次数",
            "evidence_status": "UNTESTED",
        },
    )
    assert potential.status_code == 201, potential.text
    thread = client.post(
        f"/api/v3/projects/{project_id}/agent-threads",
        json={"agent_kind": "MANAGEMENT"},
    ).json()

    with client.app.state.database.session_factory() as session:
        context = AgentRuntimeService(
            session,
            client.app.state.settings,
            client.app.state.observation_database,
            client.app.state.potential_database,
        )._build_context(UUID(project_id), thread["id"], [], query="插单排产")

    assert context["counts"]["management_observations"] == 1
    assert context["counts"]["potential_records"] == 1
    management_context = context["management_context"]
    assert management_context["management_observations"][0]["id"] == observation.json()["id"]
    assert management_context["management_observations"][0]["trust"] == (
        "UNVERIFIED_MANAGEMENT_OBSERVATION"
    )
    assert management_context["human_confirmed_potential_records"][0]["id"] == (
        potential.json()["id"]
    )
    assert management_context["human_confirmed_potential_records"][0]["trust"] == (
        "HUMAN_CONFIRMED_UNVERIFIED_POTENTIAL"
    )
    assert management_context["coverage"]["observations"]["included"] == 1
    assert management_context["coverage"]["potential_records"]["included"] == 1


def test_selected_material_is_prioritized_and_unconfirmed_filter_is_applied(
    client: TestClient,
) -> None:
    project_id, _ = _setup(client)

    def upload(name: str, content: str) -> dict:
        preview = client.post(
            f"/api/v3/projects/{project_id}/imports/preview",
            files={"file": (name, content.encode(), "text/csv")},
            data={"kind": "COMPANYCHECK_CSV"},
        ).json()
        result = client.post(
            f"/api/v3/projects/{project_id}/imports/confirm",
            json={"preview_id": preview["id"], "mapping": {}, "options": {}},
        )
        assert result.status_code == 200, result.text
        return preview

    rows = "\n".join(f"问题{i},回答{i}" for i in range(45))
    upload("first.csv", f"问题,回答\n{rows}\n")
    upload("selected.csv", "问题,回答\n关键问题,必须进入上下文的选中内容\n")
    documents = client.get(f"/api/v3/projects/{project_id}/documents").json()["items"]
    selected = next(item for item in documents if item["file_name"] == "selected.csv")
    large = next(item for item in documents if item["file_name"] == "first.csv")
    thread = client.post(
        f"/api/v3/projects/{project_id}/agent-threads",
        json={"agent_kind": "PROJECTION"},
    ).json()
    accepted = client.post(
        f"/api/v3/projects/{project_id}/agent-threads/{thread['id']}/messages",
        json={
            "content": "只处理选中材料",
            "attachment_ids": [selected["id"], large["id"]],
            "include_unconfirmed_material": False,
        },
    )
    assert accepted.status_code == 202, accepted.text
    manifest = accepted.json()["run"]["context_manifest"]
    with client.app.state.database.session_factory() as session:
        context = AgentRuntimeService(session, client.app.state.settings)._build_context(
            UUID(project_id), thread["id"], [], context_manifest=manifest
        )
    assert context["documents"][0]["id"] == selected["id"]
    assert any(
        item["source_document_id"] == selected["id"] and "必须进入上下文" in item["text"]
        for item in context["evidence_fragments"]
    )
    assert context["claims"] == []
    coverage = {item["source_document_id"]: item for item in context["material_coverage"]}
    assert coverage[selected["id"]]["included_fragments"] == 1
    assert coverage[large["id"]]["available_fragments"] == 45
    assert coverage[large["id"]]["included_fragments"] == 39
    assert len(context["evidence_fragments"]) == 40
    assert all("text_truncated" in item for item in context["evidence_fragments"])
    fragment_url = f"/api/v3/projects/{project_id}/documents/{large['id']}/fragments"
    first_page = client.get(fragment_url, params={"offset": 0, "limit": 40}).json()
    last_page = client.get(fragment_url, params={"offset": 40, "limit": 40}).json()
    assert first_page["total"] == last_page["total"] == 45
    assert len(first_page["items"]) == 40
    assert len(last_page["items"]) == 5
    assert not ({item["id"] for item in first_page["items"]} & {
        item["id"] for item in last_page["items"]
    })
    assert client.get(fragment_url, params={"offset": -1, "limit": 40}).status_code == 422


def test_projection_agent_can_read_material_fragments_on_demand(
    client: TestClient,
    monkeypatch,
) -> None:
    project_id, profile_id = _setup(client)
    csv_text = "问题,回答\n" + "\n".join(
        f"问题{i},这是第{i}条需要按需核对的访谈内容" for i in range(45)
    ) + "\n"
    preview = client.post(
        f"/api/v3/projects/{project_id}/imports/preview",
        files={"file": ("interview.csv", csv_text.encode("utf-8"), "text/csv")},
        data={"kind": "COMPANYCHECK_CSV"},
    ).json()
    confirmed = client.post(
        f"/api/v3/projects/{project_id}/imports/confirm",
        json={"preview_id": preview["id"]},
    )
    assert confirmed.status_code == 200, confirmed.text
    document = client.get(f"/api/v3/projects/{project_id}/documents").json()["items"][0]

    thread = client.post(
        f"/api/v3/projects/{project_id}/agent-threads",
        json={"agent_kind": "PROJECTION"},
    ).json()

    def scripted_model(_self, _profile, _run, _content, context):
        results = context.get("agent_execution", {}).get("tool_results", [])
        if not results:
            return AgentStructuredOutput(
                content="先读取未放入初始上下文的材料片段。",
                action_proposals=[
                    AgentActionProposal(
                        action_key="read_material_fragments",
                        input={
                            "source_document_id": document["id"],
                            "offset": 40,
                            "limit": 5,
                        },
                    )
                ],
            )
        result = next(item["result"] for item in results if item.get("result"))
        assert result["resource"] == "MATERIAL_FRAGMENTS"
        assert result["total"] == 45
        assert result["offset"] == 40
        assert result["returned"] == 5
        assert len(result["items"]) == 5
        assert "问题44" in result["items"][-1]["text"]
        return AgentStructuredOutput(content="已按需读取并核对最后一页材料。")

    monkeypatch.setattr(AgentRuntimeService, "_invoke_model", scripted_model)
    accepted = client.post(
        f"/api/v3/projects/{project_id}/agent-threads/{thread['id']}/messages",
        json={"content": "核对材料最后五条", "model_profile_id": profile_id},
    )
    assert accepted.status_code == 202, accepted.text
    run_id = accepted.json()["run"]["id"]
    completed = client.post(f"/api/v3/projects/{project_id}/agent-runs/{run_id}/execute")
    assert completed.status_code == 200, completed.text
    assert completed.json()["status"] == "COMPLETED", completed.text
    invocation_id = completed.json()["action_invocation_ids"][0]
    invocation = client.get(
        f"/api/v3/projects/{project_id}/action-invocations/{invocation_id}"
    ).json()
    assert invocation["status"] == "SUCCEEDED"
    assert invocation["result"]["returned"] == 5
    steps = client.get(f"/api/v3/projects/{project_id}/agent-runs/{run_id}/steps").json()
    assert [item["kind"] for item in steps["items"]] == ["MODEL", "TOOL", "MODEL"]


def test_agent_context_uses_a_bounded_graph_sample(client: TestClient, monkeypatch) -> None:
    project_id, _profile_id = _setup(client)
    for index in range(3):
        entity = client.post(
            f"/api/v3/projects/{project_id}/entities",
            json={"type_key": "role", "name": f"上下文岗位{index}", "properties": {}},
        )
        assert entity.status_code == 201, entity.text
    thread = client.post(
        f"/api/v3/projects/{project_id}/agent-threads",
        json={"agent_kind": "PROJECTION"},
    ).json()
    monkeypatch.setattr(client.app.state.settings, "agent_initial_graph_entities", 2)
    with client.app.state.database.session_factory() as session:
        context = AgentRuntimeService(session, client.app.state.settings)._build_context(
            UUID(project_id), thread["id"], []
        )
    assert context["counts"]["entities"] == 3
    assert len(context["entities"]) == 2
    assert context["graph_coverage"] == {
        "available_entities": 3,
        "included_entities": 2,
        "available_relations": 0,
        "included_relations": 0,
        "truncated": True,
        "read_tool": "read_graph_neighborhood",
    }


def test_agent_context_ranks_graph_by_current_question_and_keeps_relation_endpoints(
    client: TestClient,
) -> None:
    project_id, _profile_id = _setup(client)
    production = client.post(
        f"/api/v3/projects/{project_id}/entities",
        json={"type_key": "role", "name": "生产负责人", "properties": {}},
    ).json()
    sales = client.post(
        f"/api/v3/projects/{project_id}/entities",
        json={"type_key": "role", "name": "销售负责人", "properties": {}},
    ).json()
    relation = client.post(
        f"/api/v3/projects/{project_id}/relations",
        json={
            "type_key": "reports_to",
            "participants": [
                {"role_key": "reporter", "entity_id": production["id"]},
                {"role_key": "manager", "entity_id": sales["id"]},
            ],
        },
    )
    assert relation.status_code == 201, relation.text
    thread = client.post(
        f"/api/v3/projects/{project_id}/agent-threads",
        json={"agent_kind": "PROJECTION"},
    ).json()
    client.app.state.settings.agent_initial_graph_entities = 2
    with client.app.state.database.session_factory() as session:
        context = AgentRuntimeService(session, client.app.state.settings)._build_context(
            UUID(project_id), thread["id"], [], query="请检查生产负责人和销售负责人之间的协作关系"
        )
    entity_ids = [item["id"] for item in context["entities"]]
    assert entity_ids[:2] == [production["id"], sales["id"]]
    assert context["relations"][0]["id"] == relation.json()["id"]


def test_projection_agent_can_read_graph_neighborhood_on_demand(
    client: TestClient,
    monkeypatch,
) -> None:
    project_id, profile_id = _setup(client)
    manager = client.post(
        f"/api/v3/projects/{project_id}/entities",
        json={"type_key": "role", "name": "交付负责人", "properties": {}},
    ).json()
    planner = client.post(
        f"/api/v3/projects/{project_id}/entities",
        json={"type_key": "role", "name": "计划主管", "properties": {}},
    ).json()
    operator = client.post(
        f"/api/v3/projects/{project_id}/entities",
        json={"type_key": "role", "name": "生产主管", "properties": {}},
    ).json()
    relation = client.post(
        f"/api/v3/projects/{project_id}/relations",
        json={
            "type_key": "reports_to",
            "participants": [
                {"role_key": "reporter", "entity_id": planner["id"]},
                {"role_key": "manager", "entity_id": manager["id"]},
            ],
        },
    )
    assert relation.status_code == 201, relation.text
    relation = client.post(
        f"/api/v3/projects/{project_id}/relations",
        json={
            "type_key": "reports_to",
            "participants": [
                {"role_key": "reporter", "entity_id": operator["id"]},
                {"role_key": "manager", "entity_id": planner["id"]},
            ],
        },
    )
    assert relation.status_code == 201, relation.text
    thread = client.post(
        f"/api/v3/projects/{project_id}/agent-threads",
        json={"agent_kind": "PROJECTION"},
    ).json()

    def scripted_model(_self, _profile, _run, _content, context):
        results = context.get("agent_execution", {}).get("tool_results", [])
        if not results:
            return AgentStructuredOutput(
                content="先读取交付负责人的局部关系网络。",
                action_proposals=[
                    AgentActionProposal(
                        action_key="read_graph_neighborhood",
                        input={
                            "root_entity_id": manager["id"],
                            "depth": 2,
                            "include_observations": False,
                            "max_entities": 2,
                            "max_relations": 1,
                        },
                    )
                ],
            )
        result = next(item["result"] for item in results if item.get("result"))
        assert result["resource"] == "GRAPH_NEIGHBORHOOD"
        assert result["root_entity_id"] == manager["id"]
        assert result["available_entities"] == 3
        assert result["available_relations"] == 2
        assert result["returned_entities"] == 2
        assert result["returned_relations"] == 1
        assert result["truncated"] is True
        assert result["entities"][0]["id"] == manager["id"]
        return AgentStructuredOutput(content="已读取并核对局部图谱关系。")

    monkeypatch.setattr(AgentRuntimeService, "_invoke_model", scripted_model)
    accepted = client.post(
        f"/api/v3/projects/{project_id}/agent-threads/{thread['id']}/messages",
        json={"content": "核对交付负责人的上下游关系", "model_profile_id": profile_id},
    )
    assert accepted.status_code == 202, accepted.text
    run_id = accepted.json()["run"]["id"]
    completed = client.post(f"/api/v3/projects/{project_id}/agent-runs/{run_id}/execute")
    assert completed.status_code == 200, completed.text
    assert completed.json()["status"] == "COMPLETED"
    invocation_id = completed.json()["action_invocation_ids"][0]
    invocation = client.get(
        f"/api/v3/projects/{project_id}/action-invocations/{invocation_id}"
    ).json()
    assert invocation["status"] == "SUCCEEDED"
    assert invocation["result"]["returned_entities"] == 2
    steps = client.get(f"/api/v3/projects/{project_id}/agent-runs/{run_id}/steps").json()
    assert [item["kind"] for item in steps["items"]] == ["MODEL", "TOOL", "MODEL"]


def test_offline_projection_agent_builds_evidence_backed_reviewable_projection(
    client: TestClient,
) -> None:
    project_id, profile_id = _setup(client)
    csv_text = (
        "公司标识,问卷编号,问题,合并答案,访谈视角\n"
        "青岚精密,QN-01,订单流程是什么？,客户需求、报价、订单确认、排产、生产、检验、发货、开票、回款。,计划主管\n"
        "青岚精密,QN-01,岗位如何协作？,销售确认需求和交期，计划排产，生产执行，质量发现异常会暂停生产，财务跟进回款。,总经理\n"
        "青岚精密,QN-01,哪些信息难传递？,排产变化和质量放行状态难以及时在销售、计划、生产之间传递。,销售负责人\n"
    )
    preview = client.post(
        f"/api/v3/projects/{project_id}/imports/preview",
        files={"file": ("companycheck.csv", csv_text.encode("utf-8-sig"), "text/csv")},
        data={"kind": "COMPANYCHECK_CSV"},
    ).json()
    confirmed = client.post(
        f"/api/v3/projects/{project_id}/imports/confirm",
        json={"preview_id": preview["id"]},
    )
    assert confirmed.status_code == 200, confirmed.text

    document = client.get(f"/api/v3/projects/{project_id}/documents").json()["items"][0]
    thread = client.post(
        f"/api/v3/projects/{project_id}/agent-threads",
        json={"agent_kind": "PROJECTION"},
    ).json()
    accepted = client.post(
        f"/api/v3/projects/{project_id}/agent-threads/{thread['id']}/messages",
        json={
            "content": "请根据材料建立订单履约数字投影",
            "model_profile_id": profile_id,
            "attachment_ids": [document["id"]],
            "share_project_context_with_model": True,
        },
    ).json()
    run = client.post(f"/api/v3/projects/{project_id}/agent-runs/{accepted['run']['id']}/execute")
    assert run.status_code == 200, run.text
    assert run.json()["status"] == "WAITING_REVIEW"
    assert len(run.json()["action_invocation_ids"]) == 1

    invocation_id = run.json()["action_invocation_ids"][0]
    invocation = client.get(
        f"/api/v3/projects/{project_id}/action-invocations/{invocation_id}"
    ).json()
    assert invocation["action_key"] == "apply_projection_changes"
    operations = invocation["input"]["operations"]
    assert len([item for item in operations if item["kind"] == "CREATE_ENTITY"]) >= 10
    assert len([item for item in operations if item["kind"] == "CREATE_RELATION"]) >= 8
    assert all(item["payload"]["evidence"] for item in operations)
    # Agent-created actions are preflighted immediately; the UI can go straight
    # to the explicit human approval step.
    assert invocation["status"] == "WAITING_APPROVAL"
    approved = client.post(
        f"/api/v3/projects/{project_id}/action-invocations/{invocation_id}/approve",
        json={"approved_by": "offline-uat"},
    )
    assert approved.status_code == 200, approved.text
    executed = client.post(
        f"/api/v3/projects/{project_id}/action-invocations/{invocation_id}/execute"
    )
    assert executed.status_code == 200, executed.text
    assert executed.json()["status"] == "SUCCEEDED"
    graph = client.post(f"/api/v3/projects/{project_id}/graph/query", json={}).json()
    assert len(graph["entities"]) >= 10
    assert len(graph["relations"]) >= 8


def test_offline_projection_agent_recognizes_natural_modeling_request(
    client: TestClient,
) -> None:
    project_id, profile_id = _setup(client)
    imported = client.post(
        f"/api/v3/projects/{project_id}/imports/preview",
        files={
            "file": (
                "survey.csv",
                "公司标识,问卷编号,问题,合并答案,访谈视角\n"
                "青岚精密,QN-01,订单流程是什么？,销售接收订单后由计划排产，生产执行，质量检验后发货回款。,总经理\n"
                "青岚精密,QN-01,岗位如何协作？,销售、生产和质量围绕订单协同。,计划主管\n",
                "text/csv",
            )
        },
        data={"kind": "COMPANYCHECK_CSV"},
    )
    assert imported.status_code == 200, imported.text
    confirmed = client.post(
        f"/api/v3/projects/{project_id}/imports/confirm",
        json={"preview_id": imported.json()["id"]},
    )
    assert confirmed.status_code == 200, confirmed.text
    thread = client.post(
        f"/api/v3/projects/{project_id}/agent-threads",
        json={"agent_kind": "PROJECTION"},
    ).json()
    accepted = client.post(
        f"/api/v3/projects/{project_id}/agent-threads/{thread['id']}/messages",
        json={
            "content": "请根据当前项目材料提出结构化企业对象和关系草案，并等待我确认。",
            "model_profile_id": profile_id,
        },
    )
    assert accepted.status_code == 202, accepted.text
    run_id = accepted.json()["run"]["id"]
    completed = client.post(f"/api/v3/projects/{project_id}/agent-runs/{run_id}/execute")
    assert completed.status_code == 200, completed.text
    run_view = completed.json()
    assert run_view["status"] == "WAITING_REVIEW"
    assert len(run_view["action_invocation_ids"]) == 1
    invocation = client.get(
        f"/api/v3/projects/{project_id}/action-invocations/{run_view['action_invocation_ids'][0]}"
    )
    assert invocation.status_code == 200, invocation.text
    assert invocation.json()["action_key"] == "apply_projection_changes"
    assert "CREATE_ENTITY" in {
        item["kind"] for item in invocation.json()["input"]["operations"]
    }


def test_offline_projection_agent_has_conservative_non_manufacturing_fallback(
    client: TestClient,
) -> None:
    project_id, profile_id = _setup(client)
    csv_text = (
        "公司标识,问卷编号,问题,合并答案,访谈视角\n"
        "青岚软件,QN-SERVICE,组织如何协作？,产品研发、客户服务和财务共同负责产品上线、投诉处理与结算。,总经理\n"
        "青岚软件,QN-SERVICE,流程如何运行？,需求分析后经过审批和开发，最终上线并复盘改进。,运营负责人\n"
    )
    preview = client.post(
        f"/api/v3/projects/{project_id}/imports/preview",
        files={"file": ("service.csv", csv_text.encode("utf-8-sig"), "text/csv")},
        data={"kind": "COMPANYCHECK_CSV"},
    )
    assert preview.status_code == 200, preview.text
    confirmed = client.post(
        f"/api/v3/projects/{project_id}/imports/confirm",
        json={"preview_id": preview.json()["id"]},
    )
    assert confirmed.status_code == 200, confirmed.text
    thread = client.post(
        f"/api/v3/projects/{project_id}/agent-threads",
        json={"agent_kind": "PROJECTION"},
    ).json()
    accepted = client.post(
        f"/api/v3/projects/{project_id}/agent-threads/{thread['id']}/messages",
        json={
            "content": "请根据当前项目材料提出结构化企业对象和关系草案，并等待我确认。",
            "model_profile_id": profile_id,
        },
    )
    assert accepted.status_code == 202, accepted.text
    run = client.post(
        f"/api/v3/projects/{project_id}/agent-runs/{accepted.json()['run']['id']}/execute"
    )
    assert run.status_code == 200, run.text
    assert run.json()["status"] == "WAITING_REVIEW"
    invocation = client.get(
        f"/api/v3/projects/{project_id}/action-invocations/{run.json()['action_invocation_ids'][0]}"
    )
    assert invocation.status_code == 200, invocation.text
    payload = invocation.json()["input"]
    assert payload["created_by"] == "agent:projection:mock-generic-uat"
    assert any(
        item["payload"]["type_key"] == "organization_unit"
        for item in payload["operations"]
        if item["kind"] == "CREATE_ENTITY"
    )
    assert any(
        item["payload"]["type_key"] == "process"
        for item in payload["operations"]
        if item["kind"] == "CREATE_ENTITY"
    )
    assert all(item["evidence"] for item in payload["operations"])


def test_offline_projection_agent_generic_fallback_completes_after_approval(
    client: TestClient,
) -> None:
    """The non-manufacturing starter draft must be executable, not only printable."""
    project_id, profile_id = _setup(client)
    csv_text = (
        "公司标识,问卷编号,问题,合并答案,访谈视角\n"
        "青岚软件,QN-SERVICE,组织如何协作？,产品研发、客户服务和财务共同负责产品上线、投诉处理与结算。,总经理\n"
        "青岚软件,QN-SERVICE,流程如何运行？,需求分析后经过审批和开发，最终上线并复盘改进。,运营负责人\n"
    )
    preview = client.post(
        f"/api/v3/projects/{project_id}/imports/preview",
        files={"file": ("service.csv", csv_text.encode("utf-8-sig"), "text/csv")},
        data={"kind": "COMPANYCHECK_CSV"},
    )
    assert preview.status_code == 200, preview.text
    assert client.post(
        f"/api/v3/projects/{project_id}/imports/confirm",
        json={"preview_id": preview.json()["id"]},
    ).status_code == 200

    thread = client.post(
        f"/api/v3/projects/{project_id}/agent-threads",
        json={"agent_kind": "PROJECTION"},
    ).json()
    accepted = client.post(
        f"/api/v3/projects/{project_id}/agent-threads/{thread['id']}/messages",
        json={
            "content": "请根据当前项目材料提出结构化企业对象和关系草案，并等待我确认。",
            "model_profile_id": profile_id,
        },
    )
    assert accepted.status_code == 202, accepted.text
    run_id = accepted.json()["run"]["id"]
    waiting = client.post(
        f"/api/v3/projects/{project_id}/agent-runs/{run_id}/execute"
    )
    assert waiting.status_code == 200, waiting.text
    waiting_view = waiting.json()
    assert waiting_view["status"] == "WAITING_REVIEW"
    assert len(waiting_view["action_invocation_ids"]) == 1
    action_id = waiting_view["action_invocation_ids"][0]
    action = client.get(
        f"/api/v3/projects/{project_id}/action-invocations/{action_id}"
    ).json()
    operation_count = len(action["input"]["operations"])
    assert operation_count > 0
    assert all(item["evidence"] for item in action["input"]["operations"])

    approved = client.post(
        f"/api/v3/projects/{project_id}/action-invocations/{action_id}/approve",
        json={"approved_by": "developer"},
    )
    assert approved.status_code == 200, approved.text
    resumed = client.post(
        f"/api/v3/projects/{project_id}/agent-runs/{run_id}/continue"
    )
    assert resumed.status_code == 202, resumed.text
    assert resumed.json()["status"] == "COMPLETED"

    applied = client.get(
        f"/api/v3/projects/{project_id}/action-invocations/{action_id}"
    ).json()
    assert applied["status"] == "SUCCEEDED"
    graph = client.post(f"/api/v3/projects/{project_id}/graph/query", json={}).json()
    assert len(graph["entities"]) >= 2
    assert len(graph["relations"]) >= 1


def test_offline_projection_agent_does_not_invent_from_uninformative_material(
    client: TestClient,
) -> None:
    project_id, profile_id = _setup(client)
    preview = client.post(
        f"/api/v3/projects/{project_id}/imports/preview",
        files={
            "file": (
                "uninformative.csv",
                "公司标识,问卷编号,问题,合并答案,访谈视角\n"
                "青岚服务,QN-EMPTY,Q01,目前运行正常，没有更多补充。,总经理\n",
                "text/csv",
            )
        },
        data={"kind": "COMPANYCHECK_CSV"},
    )
    assert preview.status_code == 200, preview.text
    confirmed = client.post(
        f"/api/v3/projects/{project_id}/imports/confirm",
        json={"preview_id": preview.json()["id"]},
    )
    assert confirmed.status_code == 200, confirmed.text
    thread = client.post(
        f"/api/v3/projects/{project_id}/agent-threads",
        json={"agent_kind": "PROJECTION"},
    ).json()
    accepted = client.post(
        f"/api/v3/projects/{project_id}/agent-threads/{thread['id']}/messages",
        json={
            "content": "请根据当前项目材料提出结构化企业对象和关系草案，并等待我确认。",
            "model_profile_id": profile_id,
        },
    )
    assert accepted.status_code == 202, accepted.text
    run = client.post(
        f"/api/v3/projects/{project_id}/agent-runs/{accepted.json()['run']['id']}/execute"
    )
    assert run.status_code == 200, run.text
    assert run.json()["status"] == "COMPLETED"
    assert run.json()["action_invocation_ids"] == []


def test_agent_uses_real_tool_ids_across_approval_rounds(
    client: TestClient,
    monkeypatch,
) -> None:
    project_id, profile_id = _setup(client)
    thread = client.post(
        f"/api/v3/projects/{project_id}/agent-threads",
        json={"agent_kind": "PROJECTION"},
    ).json()

    def scripted_model(_self, _profile, _run, _content, context):
        results = [
            item
            for item in context.get("agent_execution", {}).get("tool_results", [])
            if item.get("status") == "SUCCEEDED" and item.get("result")
        ]
        entity_ids = [
            item["result"]["id"] for item in results if item["result"].get("resource") == "ENTITY"
        ]
        relation_ids = [
            item["result"]["id"] for item in results if item["result"].get("resource") == "RELATION"
        ]
        if not entity_ids:
            return AgentStructuredOutput(
                content="先创建两个岗位。",
                action_proposals=[
                    AgentActionProposal(
                        action_key="create_entity",
                        input={"type_key": "role", "name": "销售主管", "properties": {}},
                    ),
                    AgentActionProposal(
                        action_key="create_entity",
                        input={"type_key": "role", "name": "销售专员", "properties": {}},
                    ),
                ],
            )
        if len(entity_ids) == 2 and not relation_ids:
            return AgentStructuredOutput(
                content="使用工具返回的真实ID建立汇报关系。",
                action_proposals=[
                    AgentActionProposal(
                        action_key="create_relation",
                        input={
                            "type_key": "reports_to",
                            "participants": [
                                {"role_key": "reporter", "entity_id": entity_ids[1]},
                                {"role_key": "manager", "entity_id": entity_ids[0]},
                            ],
                            "properties": {},
                        },
                    )
                ],
            )
        return AgentStructuredOutput(content="对象和汇报关系均已回读确认。")

    monkeypatch.setattr(AgentRuntimeService, "_invoke_model", scripted_model)
    accepted = client.post(
        f"/api/v3/projects/{project_id}/agent-threads/{thread['id']}/messages",
        json={"content": "建立销售岗位和汇报关系", "model_profile_id": profile_id},
    ).json()
    run_id = accepted["run"]["id"]
    first = client.post(f"/api/v3/projects/{project_id}/agent-runs/{run_id}/execute").json()
    assert first["status"] == "WAITING_REVIEW"
    assert len(first["action_invocation_ids"]) == 2
    for action_id in first["action_invocation_ids"]:
        client.post(
            f"/api/v3/projects/{project_id}/action-invocations/{action_id}/approve",
            json={"approved_by": "model-builder"},
        ).raise_for_status()

    second = client.post(f"/api/v3/projects/{project_id}/agent-runs/{run_id}/continue").json()
    assert second["status"] == "WAITING_REVIEW"
    assert len(second["action_invocation_ids"]) == 3
    relation_action_id = second["action_invocation_ids"][-1]
    relation_action = client.get(
        f"/api/v3/projects/{project_id}/action-invocations/{relation_action_id}"
    ).json()
    entity_result_ids = {
        client.get(f"/api/v3/projects/{project_id}/action-invocations/{action_id}").json()[
            "result"
        ]["id"]
        for action_id in first["action_invocation_ids"]
    }
    assert {item["entity_id"] for item in relation_action["input"]["participants"]} == (
        entity_result_ids
    )
    client.post(
        f"/api/v3/projects/{project_id}/action-invocations/{relation_action_id}/approve",
        json={"approved_by": "model-builder"},
    ).raise_for_status()
    final = client.post(f"/api/v3/projects/{project_id}/agent-runs/{run_id}/continue")
    assert final.status_code == 202, final.text
    assert final.json()["status"] == "COMPLETED"
    graph = client.post(f"/api/v3/projects/{project_id}/graph/query", json={}).json()
    assert len(graph["entities"]) == 2
    assert len(graph["relations"]) == 1


def test_management_context_reads_are_gated_by_route_and_listed_in_manifest(
    client: TestClient,
) -> None:
    project_id, profile_id = _setup(client)
    observation = client.post(
        f"/api/v3/projects/{project_id}/observations",
        json={"kind": "INTERVIEW", "title": "管理访谈", "content": "未验证的现场陈述"},
    ).json()
    thread = client.post(
        f"/api/v3/projects/{project_id}/agent-threads",
        json={"agent_kind": "MANAGEMENT"},
    ).json()
    accepted = client.post(
        f"/api/v3/projects/{project_id}/agent-threads/{thread['id']}/messages",
        json={"content": "查询正式岗位模型", "model_profile_id": profile_id},
    ).json()
    manifest = accepted["run"]["context_manifest"]
    manifest["task_route"] = {"route": "SIMPLE", "model_access": "REAL_ONLY"}

    with client.app.state.database.session_factory() as session:
        run = session.get(AgentRunRow, accepted["run"]["id"])
        assert run is not None
        run.context_manifest = manifest
        ActionService(session).ensure_defaults(UUID(project_id))
        service = AgentRuntimeService(
            session,
            client.app.state.settings,
            client.app.state.observation_database,
            client.app.state.potential_database,
        )
        simple_context = service._build_context(
            UUID(project_id),
            thread["id"],
            [],
            query="查询正式岗位模型",
            context_manifest=manifest,
        )
        assert simple_context["management_context"] is None
        assert simple_context["management_context_access"] == {
            "decision": "EXCLUDED_BY_SIMPLE_ROUTE",
            "management_observations_read": False,
            "potential_records_read": False,
            "virtual_work_read": False,
        }
        action_keys = {item["key"] for item in simple_context["available_actions"]}
        assert "search_management_observations" not in action_keys
        assert "search_potential_records" not in action_keys
        assert "未验证的现场陈述" not in json.dumps(
            simple_context, ensure_ascii=False, default=str
        )

        complex_manifest = {
            **manifest,
            "task_route": {"route": "COMPLEX", "model_access": "REAL_ONLY"},
        }
        complex_context = service._build_context(
            UUID(project_id),
            thread["id"],
            [],
            query="调查现场流程",
            context_manifest=complex_manifest,
        )
        assert complex_context["management_context_access"]["management_observations_read"]
        refs = complex_context["management_context"]["read_manifest"]
        assert {item["id"] for item in refs["management_observation_refs"]} == {observation["id"]}
        query_manifest = service._merge_query_manifest(None, complex_context)
        assert query_manifest["read_sets"][0]["management_observation_refs"] == [
            {"id": observation["id"], "revision": 1}
        ]
