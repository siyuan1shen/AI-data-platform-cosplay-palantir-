from __future__ import annotations

from fastapi.testclient import TestClient


def _new_project(client: TestClient, company_name: str, project_name: str) -> str:
    company = client.post("/api/v3/companies", json={"name": company_name})
    assert company.status_code == 201, company.text
    project = client.post(
        f"/api/v3/companies/{company.json()['id']}/projects",
        json={"name": project_name},
    )
    assert project.status_code == 201, project.text
    return project.json()["id"]


def _mock_profile(client: TestClient) -> str:
    response = client.post(
        "/api/v3/model-profiles",
        json={
            "name": "验收离线模型",
            "provider": "MOCK",
            "base_url": "mock://acceptance",
            "model": "deterministic",
        },
    )
    assert response.status_code == 201, response.text
    return response.json()["id"]


def _approved_mapping(
    client: TestClient, project_id: str, payload: dict[str, object]
) -> None:
    created = client.post(
        f"/api/v3/projects/{project_id}/semantic-mappings", json=payload
    )
    assert created.status_code == 201, created.text
    mapping = created.json()
    validated = client.post(
        f"/api/v3/projects/{project_id}/semantic-mappings/{mapping['id']}/validate",
        json={"expected_revision": mapping["revision"]},
    )
    assert validated.status_code == 200, validated.text
    mapping = validated.json()
    approved = client.post(
        f"/api/v3/projects/{project_id}/semantic-mappings/{mapping['id']}/approve",
        json={"expected_revision": mapping["revision"]},
    )
    assert approved.status_code == 200, approved.text
    assert approved.json()["status"] == "APPROVED"


def test_no_system_company_can_reach_a_published_executive_projection(
    client: TestClient,
) -> None:
    """The primary no-system path must be executable as one repeatable acceptance case."""
    project_id = _new_project(client, "验收无系统企业", "组织与订单流程投影")
    csv_text = (
        "公司标识,问卷编号,问题,合并答案,访谈视角\n"
        "验收无系统企业,QN-01,订单流程是什么？,销售接单后由计划排产，生产执行，质量放行后交付并回款。,总经理\n"
        "验收无系统企业,QN-01,岗位如何协作？,销售负责承诺，计划负责排产，生产负责执行，质量负责放行。,计划主管\n"
    )
    preview = client.post(
        f"/api/v3/projects/{project_id}/imports/preview",
        files={"file": ("acceptance.csv", csv_text.encode("utf-8-sig"), "text/csv")},
        data={"kind": "COMPANYCHECK_CSV"},
    )
    assert preview.status_code == 200, preview.text
    confirmed = client.post(
        f"/api/v3/projects/{project_id}/imports/confirm",
        json={"preview_id": preview.json()["id"]},
    )
    assert confirmed.status_code == 200, confirmed.text
    assert confirmed.json()["fragments_created"] == 2
    document = client.get(f"/api/v3/projects/{project_id}/documents").json()["items"][0]

    thread = client.post(
        f"/api/v3/projects/{project_id}/agent-threads",
        json={"agent_kind": "PROJECTION"},
    )
    assert thread.status_code == 201, thread.text
    accepted = client.post(
        f"/api/v3/projects/{project_id}/agent-threads/{thread.json()['id']}/messages",
        json={
            "content": "请根据材料提出结构化企业对象和关系草案，并等待我确认。",
            "model_profile_id": _mock_profile(client),
            "attachment_ids": [document["id"]],
            "share_project_context_with_model": True,
        },
    )
    assert accepted.status_code == 202, accepted.text
    run = client.post(
        f"/api/v3/projects/{project_id}/agent-runs/{accepted.json()['run']['id']}/execute"
    )
    assert run.status_code == 200, run.text
    assert run.json()["status"] == "WAITING_REVIEW"

    action_id = run.json()["action_invocation_ids"][0]
    approved = client.post(
        f"/api/v3/projects/{project_id}/action-invocations/{action_id}/approve",
        json={"approved_by": "acceptance"},
    )
    assert approved.status_code == 200, approved.text
    continued = client.post(
        f"/api/v3/projects/{project_id}/agent-runs/{run.json()['id']}/continue"
    )
    assert continued.status_code == 202, continued.text
    assert continued.json()["status"] == "COMPLETED"

    graph = client.post(f"/api/v3/projects/{project_id}/graph/query", json={})
    assert graph.status_code == 200, graph.text
    assert len(graph.json()["entities"]) >= 2
    assert len(graph.json()["relations"]) >= 1

    project = client.get(f"/api/v3/projects/{project_id}").json()
    publication = client.post(
        f"/api/v3/projects/{project_id}/publications",
        json={
            "label": "无系统企业验收发布版",
            "expected_project_revision": project["revision"],
        },
    )
    assert publication.status_code == 201, publication.text
    executive = client.get(f"/api/v3/projects/{project_id}/executive/context")
    assert executive.status_code == 200, executive.text
    assert executive.json()["publication"]["version"] == 1
    assert len(executive.json()["graph"]["entities"]) >= 2


def test_system_data_can_bind_to_design_and_be_exported_semantically(
    client: TestClient,
) -> None:
    """The downstream path must keep design facts and observed system facts distinct."""
    project_id = _new_project(client, "验收有系统企业", "ERP 语义对齐")
    installed = client.post(f"/api/v3/projects/{project_id}/ontology/default-pack")
    assert installed.status_code == 200, installed.text
    designed = client.post(
        f"/api/v3/projects/{project_id}/entities",
        json={
            "type_key": "role",
            "stable_key": "role.sales",
            "name": "销售负责人",
            "properties": {"purpose": "负责商业结果", "headcount": 1},
        },
    )
    assert designed.status_code == 201, designed.text

    source = client.post(
        f"/api/v3/projects/{project_id}/source-systems",
        json={"name": "ERP 岗位导出", "kind": "FILE", "connection_profile": {}},
    )
    assert source.status_code == 201, source.text
    source_id = source.json()["id"]
    fields = (("id", "__stable_key__"), ("name", "__name__"), ("headcount", "headcount"))
    for field, target in fields:
        _approved_mapping(
            client,
            project_id,
            {
                "source_system_id": source_id,
                "source_asset": "*",
                "source_field": field,
                "target_type_key": "role",
                "target_property_key": target,
                "transform_expression": "strip|int" if field == "headcount" else "strip",
            },
        )

    preview = client.post(
        f"/api/v3/projects/{project_id}/source-systems/{source_id}/imports/preview",
        files={
            "file": (
                "roles.csv",
                "id,name,headcount\nrole.sales,系统中的销售主管,3\n".encode("utf-8-sig"),
                "text/csv",
            )
        },
        data={"kind": "CSV"},
    )
    assert preview.status_code == 200, preview.text
    imported = client.post(
        f"/api/v3/projects/{project_id}/imports/confirm",
        json={"preview_id": preview.json()["id"]},
    )
    assert imported.status_code == 200, imported.text
    assert imported.json()["entities_created"] == 0
    assert imported.json()["identities_bound"] == 1
    assert imported.json()["observations_created"] == 2

    entity = client.get(
        f"/api/v3/projects/{project_id}/entities/{designed.json()['id']}"
    )
    assert entity.status_code == 200, entity.text
    assert entity.json()["name"] == "销售负责人"
    assert entity.json()["observed_name"] == "系统中的销售主管"
    assert entity.json()["comparison"]["headcount"]["status"] == "DIVERGED"

    export = client.post(
        f"/api/v3/projects/{project_id}/exports",
        json={"format": "json", "include_evidence": False, "include_lineage": True},
    )
    assert export.status_code == 201, export.text
    bundle = client.get(export.json()["download_url"])
    assert bundle.status_code == 200, bundle.text
    assert len(bundle.json()["source_identities"]) == 1
    assert len(bundle.json()["observation_assertions"]) == 2


def test_three_company_six_project_projection_portfolio_replay_is_isolated(
    client: TestClient,
) -> None:
    """Replay the complete no-system path for a small multi-company portfolio."""
    profile_id = _mock_profile(client)
    portfolio = {
        "华南智造样本公司": ("新品市场验证", "订单交付改善"),
        "恒川精密样本公司": ("供应商质量提升", "产能交期协同"),
        "云桥软件样本公司": ("客户续约增长", "产品交付提速"),
    }
    created_projects: list[tuple[str, str, str, str]] = []

    for company_name, project_names in portfolio.items():
        company_response = client.post(
            "/api/v3/companies", json={"name": company_name}
        )
        assert company_response.status_code == 201, company_response.text
        company_id = company_response.json()["id"]
        listed_company_projects = client.get(
            f"/api/v3/projects?company_id={company_id}"
        )
        assert listed_company_projects.status_code == 200, listed_company_projects.text
        assert listed_company_projects.json()["items"] == []

        for project_name in project_names:
            project_response = client.post(
                f"/api/v3/companies/{company_id}/projects",
                json={"name": project_name},
            )
            assert project_response.status_code == 201, project_response.text
            created_projects.append(
                (company_id, company_name, project_response.json()["id"], project_name)
            )

    assert len(created_projects) == 6

    for company_id, company_name, project_id, project_name in created_projects:
        csv_text = (
            "公司标识,问卷编号,问题,合并答案,访谈视角\n"
            f"{company_name},QN-{project_id[:8]},关键流程如何运转？,"
            f"项目{project_name}由客户和销售提出需求，产品与研发明确方案，计划协调生产和质量执行，"
            "交付后由财务结算并由负责人复盘结果。,项目负责人\n"
            f"{company_name},QN-{project_id[:8]},当前最大的阻塞是什么？,"
            "跨部门交接缺少统一定义，需要管理层确认优先级。,总经理\n"
        )
        preview = client.post(
            f"/api/v3/projects/{project_id}/imports/preview",
            files={"file": ("portfolio.csv", csv_text.encode("utf-8-sig"), "text/csv")},
            data={"kind": "COMPANYCHECK_CSV"},
        )
        assert preview.status_code == 200, preview.text
        confirmed = client.post(
            f"/api/v3/projects/{project_id}/imports/confirm",
            json={"preview_id": preview.json()["id"]},
        )
        assert confirmed.status_code == 200, confirmed.text
        assert confirmed.json()["fragments_created"] == 2
        document = client.get(f"/api/v3/projects/{project_id}/documents").json()["items"][0]

        thread = client.post(
            f"/api/v3/projects/{project_id}/agent-threads",
            json={"agent_kind": "PROJECTION", "title": f"{project_name} 建模"},
        )
        assert thread.status_code == 201, thread.text
        accepted = client.post(
            f"/api/v3/projects/{project_id}/agent-threads/{thread.json()['id']}/messages",
            json={
                "content": "请根据材料提出结构化企业对象和关系草案，并等待我确认。",
                "model_profile_id": profile_id,
                "attachment_ids": [document["id"]],
                "share_project_context_with_model": True,
            },
        )
        assert accepted.status_code == 202, accepted.text
        run = client.post(
            f"/api/v3/projects/{project_id}/agent-runs/{accepted.json()['run']['id']}/execute"
        )
        assert run.status_code == 200, run.text
        assert run.json()["status"] == "WAITING_REVIEW"
        action_id = run.json()["action_invocation_ids"][0]
        approved = client.post(
            f"/api/v3/projects/{project_id}/action-invocations/{action_id}/approve",
            json={"approved_by": "portfolio-acceptance"},
        )
        assert approved.status_code == 200, approved.text
        continued = client.post(
            f"/api/v3/projects/{project_id}/agent-runs/{run.json()['id']}/continue"
        )
        assert continued.status_code == 202, continued.text
        assert continued.json()["status"] == "COMPLETED"

        graph = client.post(f"/api/v3/projects/{project_id}/graph/query", json={})
        assert graph.status_code == 200, graph.text
        assert len(graph.json()["entities"]) >= 2
        assert len(graph.json()["relations"]) >= 1
        project = client.get(f"/api/v3/projects/{project_id}")
        assert project.status_code == 200, project.text
        publication = client.post(
            f"/api/v3/projects/{project_id}/publications",
            json={
                "label": f"{project_name} 组合验收版",
                "expected_project_revision": project.json()["revision"],
            },
        )
        assert publication.status_code == 201, publication.text
        executive = client.get(f"/api/v3/projects/{project_id}/executive/context")
        assert executive.status_code == 200, executive.text
        assert executive.json()["company"]["id"] == company_id
        assert executive.json()["project"]["id"] == project_id

    for company_id, _, _, _ in created_projects[::2]:
        projects = client.get(f"/api/v3/projects?company_id={company_id}")
        assert projects.status_code == 200, projects.text
        assert len(projects.json()["items"]) == 2
