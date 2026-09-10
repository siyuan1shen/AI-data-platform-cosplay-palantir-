from __future__ import annotations

import json
from pathlib import Path

from fastapi.testclient import TestClient


def test_openapi_uses_only_v3_paths(client: TestClient) -> None:
    document = client.get("/api/v3/openapi.json").json()
    assert document["paths"]
    assert all(path.startswith("/api/v3/") for path in document["paths"])
    assert "/api/v3/projects/{project_id}/graph/query" in document["paths"]
    assert (
        "/api/v3/projects/{project_id}/semantic-mapping-suggestions"
        in document["paths"]
    )
    assert "/api/v3/projects/{project_id}/executive/context" in document["paths"]
    assert "/api/v3/projects/{project_id}/management/analysis-runs" in document["paths"]
    assert "/api/v3/projects/{project_id}/management/insights" in document["paths"]
    assert "/api/v3/projects/{project_id}/causal-hypotheses" in document["paths"]
    assert "/api/v3/projects/{project_id}/evaluations/suites" in document["paths"]
    assert (
        "/api/v3/projects/{project_id}/evaluations/suites/{suite_id}/execute-async"
        in document["paths"]
    )
    assert (
        "/api/v3/projects/{project_id}/evaluations/executions/{execution_id}"
        in document["paths"]
    )
    assert "/api/v3/projects/{project_id}/evaluations/runs" in document["paths"]
    assert (
        "/api/v3/projects/{project_id}/evaluations/runs/{run_id}/results"
        in document["paths"]
    )
    assert (
        "/api/v3/projects/{project_id}/evaluations/suites/{suite_id}/runs"
        in document["paths"]
    )


def test_validation_error_contract(client: TestClient) -> None:
    response = client.post("/api/v3/companies", json={"name": ""})
    assert response.status_code == 422
    payload = response.json()
    assert payload["error"]["code"] == "REQUEST_VALIDATION_FAILED"
    assert payload["error"]["details"]


def test_checked_in_openapi_contract_matches_runtime(client: TestClient) -> None:
    contract_path = Path(__file__).resolve().parents[2] / "contracts" / "openapi.json"
    checked_in = json.loads(contract_path.read_text(encoding="utf-8"))
    runtime = client.get("/api/v3/openapi.json").json()
    assert checked_in["info"] == runtime["info"]
    assert checked_in["paths"] == runtime["paths"]
    assert checked_in["components"] == runtime["components"]
