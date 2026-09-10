from __future__ import annotations

import io
import json
from pathlib import Path
from zipfile import ZIP_DEFLATED, ZipFile

import pytest
from fastapi.testclient import TestClient

from enterprise_insight_backend.app import create_app
from enterprise_insight_backend.config import Settings
from enterprise_insight_backend.project_archives import ARCHIVE_TABLES, archive_manifest


def _client(path: Path) -> TestClient:
    return TestClient(
        create_app(
            Settings(
                data_dir=path,
                database_url=f"sqlite:///{(path / 'app.db').as_posix()}",
                agent_worker_enabled=False,
            )
        )
    )


def _minimal_archive_payload() -> dict[str, object]:
    company_id = "00000000-0000-0000-0000-000000000001"
    project_id = "00000000-0000-0000-0000-000000000002"
    tables = {definition.name: [] for definition in ARCHIVE_TABLES}
    tables["companies"] = [{"id": company_id, "name": "测试企业"}]
    tables["projects"] = [{"id": project_id, "company_id": company_id, "name": "测试项目"}]
    return {
        "schema": "enterprise-insight.project-archive.v1",
        "project_id": project_id,
        "company_id": company_id,
        "tables": tables,
    }


def _archive_package(payload: dict[str, object]) -> bytes:
    buffer = io.BytesIO()
    with ZipFile(buffer, "w", ZIP_DEFLATED) as archive:
        archive.writestr("restore.json", json.dumps(payload))
        archive.writestr("restore.manifest.json", json.dumps(archive_manifest(payload)))
    return buffer.getvalue()


@pytest.mark.parametrize(
    ("mutation", "code"),
    [
        (
            lambda payload: payload.__setitem__("project_id", "other-project"),
            "RESTORE_PROJECT_BOUNDARY_INVALID",
        ),
        (
            lambda payload: payload.__setitem__("company_id", "other-company"),
            "RESTORE_PROJECT_BOUNDARY_INVALID",
        ),
        (
            lambda payload: payload["tables"]["entities"].append(  # type: ignore[index]
                {"id": "entity-1", "project_id": "other-project"}
            ),
            "RESTORE_PROJECT_BOUNDARY_INVALID",
        ),
        (
            lambda payload: payload["tables"]["relation_participants"].append(  # type: ignore[index]
                {
                    "id": "participant-1",
                    "relation_id": "missing-relation",
                    "entity_id": "missing-entity",
                }
            ),
            "RESTORE_FOREIGN_KEY_CLOSURE_INVALID",
        ),
        (
            lambda payload: payload["tables"]["evaluation_runs"].extend(  # type: ignore[index]
                [
                    {
                        "id": "run-1",
                        "project_id": payload["project_id"],
                        "suite_id": "suite-1",
                        "model_profile_id": "profile-1",
                    },
                ]
            ),
            "RESTORE_EXCLUDED_REFERENCE_INVALID",
        ),
    ],
)
def test_restore_validates_project_boundary_and_fk_closure_before_writing(
    tmp_path: Path, mutation: object, code: str
) -> None:
    path = tmp_path / "target"
    path.mkdir()
    payload = _minimal_archive_payload()
    mutation(payload)  # type: ignore[operator]

    with _client(path) as client:
        response = client.post(
            "/api/v3/restores/preview",
            files={"file": ("invalid.zip", _archive_package(payload), "application/zip")},
        )

        assert response.status_code == 422, response.text
        assert response.json()["error"]["code"] == code
        assert client.get("/api/v3/companies").json()["total"] == 0


def test_restore_accepts_legacy_v1_package_without_evaluation_executions() -> None:
    payload = _minimal_archive_payload()
    payload["tables"].pop("evaluation_executions")  # type: ignore[union-attr]

    from enterprise_insight_backend.project_archives import validate_archive_payload

    restored = validate_archive_payload(payload, archive_manifest(payload))

    assert restored["tables"]["evaluation_executions"] == []  # type: ignore[index]


def test_bundle_restores_a_complete_project_without_secrets_or_partial_writes(
    tmp_path: Path,
) -> None:
    source_path = tmp_path / "source"
    target_path = tmp_path / "target"
    source_path.mkdir()
    target_path.mkdir()
    with _client(source_path) as source:
        company = source.post(
            "/api/v3/companies", json={"name": "恢复样例企业", "industry": "制造业"}
        ).json()
        project = source.post(
            f"/api/v3/companies/{company['id']}/projects",
            json={"name": "企业投影恢复样例"},
        ).json()
        project_id = project["id"]
        assert (
            source.post(f"/api/v3/projects/{project_id}/ontology/default-pack").status_code
            == 200
        )
        role = source.post(
            f"/api/v3/projects/{project_id}/entities",
            json={
                "type_key": "role",
                "stable_key": "role.ops",
                "name": "运营负责人",
                "properties": {"purpose": "对交付负责"},
            },
        ).json()
        responsibility = source.post(
            f"/api/v3/projects/{project_id}/entities",
            json={
                "type_key": "responsibility",
                "stable_key": "responsibility.delivery",
                "name": "确保按期交付",
                "properties": {},
            },
        ).json()
        relation = source.post(
            f"/api/v3/projects/{project_id}/relations",
            json={
                "type_key": "holds_responsibility",
                "participants": [
                    {"role_key": "accountable", "entity_id": role["id"]},
                    {
                        "role_key": "responsibility",
                        "entity_id": responsibility["id"],
                    },
                ],
                "properties": {},
                "viewpoint": "DESIGNED",
                "evidence": [],
            },
        )
        assert relation.status_code == 201, relation.text
        evidence_preview = source.post(
            f"/api/v3/projects/{project_id}/imports/preview",
            files={
                "file": (
                    "interview.txt",
                    "运营负责人反馈跨部门协调困难。".encode(),
                    "text/plain",
                )
            },
            data={"kind": "TXT"},
        ).json()
        assert source.post(
            f"/api/v3/projects/{project_id}/imports/confirm",
            json={"preview_id": evidence_preview["id"]},
        ).status_code == 200
        system = source.post(
            f"/api/v3/projects/{project_id}/source-systems",
            json={
                "name": "ERP 只读库",
                "kind": "POSTGRESQL",
                "connection_profile": {
                    "host": "secret.internal",
                    "database": "erp",
                    "user": "reader",
                    "password": "top-secret",
                },
            },
        ).json()
        relation_mapping = source.post(
            f"/api/v3/projects/{project_id}/semantic-relation-mappings",
            json={
                "source_system_id": system["id"],
                "source_asset": "roles.csv",
                "source_type_key": "role",
                "source_field": "department_id",
                "relation_type_key": "contains",
                "source_role_key": "member",
                "target_type_key": "organization_unit",
                "target_role_key": "container",
            },
        )
        assert relation_mapping.status_code == 201, relation_mapping.text
        validated_mapping = source.post(
            f"/api/v3/projects/{project_id}/semantic-relation-mappings/{relation_mapping.json()['id']}/validate",
            json={"expected_revision": relation_mapping.json()["revision"]},
        )
        assert validated_mapping.status_code == 200, validated_mapping.text
        approved_mapping = source.post(
            f"/api/v3/projects/{project_id}/semantic-relation-mappings/{relation_mapping.json()['id']}/approve",
            json={"expected_revision": validated_mapping.json()["revision"]},
        )
        assert approved_mapping.status_code == 200, approved_mapping.text
        metric = source.post(
            f"/api/v3/projects/{project_id}/management/metrics",
            json={
                "key": "delivery_rate",
                "name": "按期交付率",
                "unit": "%",
                "target_value": 95,
                "direction": "HIGHER_IS_BETTER",
            },
        ).json()
        source.post(
            f"/api/v3/projects/{project_id}/management/metrics/{metric['id']}/observations",
            json={"period_key": "2026-08", "value": 82},
        )
        source.post(
            f"/api/v3/projects/{project_id}/management/analysis-runs", json={}
        )
        source.post(
            f"/api/v3/projects/{project_id}/query-snapshots",
            json={"model_scope": "DRAFT", "include_observations": True},
        )

        job = source.post(
            f"/api/v3/projects/{project_id}/exports",
            json={"format": "bundle", "include_evidence": True, "include_lineage": True},
        )
        assert job.status_code == 201, job.text
        assert job.json()["status"] == "COMPLETED", job.text
        package = source.get(job.json()["download_url"]).content
        with ZipFile(io.BytesIO(package)) as archive:
            restore_text = archive.read("restore.json").decode("utf-8")
            assert "top-secret" not in restore_text
            assert "secret.internal" not in restore_text

    with _client(target_path) as target:
        preview = target.post(
            "/api/v3/restores/preview",
            files={"file": ("project.zip", package, "application/zip")},
        )
        assert preview.status_code == 200, preview.text
        summary = preview.json()["summary"]
        assert summary["can_confirm"] is True
        assert summary["project"]["name"] == "企业投影恢复样例"
        assert summary["table_counts"]["entities"] == 3
        restored = target.post(
            "/api/v3/restores/confirm",
            json={"preview_id": preview.json()["id"]},
        )
        assert restored.status_code == 200, restored.text
        result = restored.json()
        assert result["project_id"] == project_id
        assert result["source_connections_reset"] == 1
        assert result["restored_counts"]["evidence_fragments"] == 1
        assert result["restored_counts"]["semantic_relation_mappings"] == 1

        graph = target.post(f"/api/v3/projects/{project_id}/graph/query", json={}).json()
        assert {item["stable_key"] for item in graph["entities"]} >= {
            "role.ops",
            "responsibility.delivery",
        }
        assert len(graph["relations"]) == 1
        assert target.get(f"/api/v3/projects/{project_id}/documents").json()["total"] == 1
        restored_metric = target.get(
            f"/api/v3/projects/{project_id}/management/metrics"
        ).json()["items"][0]
        assert restored_metric["key"] == "delivery_rate"
        observations = target.get(
            f"/api/v3/projects/{project_id}/management/metrics/{restored_metric['id']}/observations"
        ).json()["items"]
        assert observations[0]["value"] == 82
        restored_source = target.get(
            f"/api/v3/projects/{project_id}/source-systems"
        ).json()["items"][0]
        assert restored_source["id"] == system["id"]
        assert restored_source["configured_fields"] == []
        assert restored_source["status"] == "NEEDS_CONFIGURATION"
        reconfigured = target.patch(
            f"/api/v3/projects/{project_id}/source-systems/{restored_source['id']}",
            json={
                "connection_profile": {
                    "host": "new.internal",
                    "database": "erp",
                    "user": "reader",
                },
                "expected_revision": restored_source["revision"],
            },
        )
        assert reconfigured.status_code == 200, reconfigured.text
        assert reconfigured.json()["configured_fields"] == ["database", "host", "user"]
        restored_relation_mappings = target.get(
            f"/api/v3/projects/{project_id}/semantic-relation-mappings"
        )
        assert restored_relation_mappings.status_code == 200, restored_relation_mappings.text
        assert restored_relation_mappings.json()["total"] == 1
        assert restored_relation_mappings.json()["items"][0]["status"] == "APPROVED"
        assert target.get(f"/api/v3/projects/{project_id}/management/issues").json()[
            "total"
        ] >= 1

        consumed = target.post(
            "/api/v3/restores/confirm", json={"preview_id": preview.json()["id"]}
        )
        assert consumed.status_code == 409
        conflicting = target.post(
            "/api/v3/restores/preview",
            files={"file": ("project.zip", package, "application/zip")},
        )
        assert conflicting.status_code == 200
        assert conflicting.json()["status"] == "CONFLICT"
        before = target.get("/api/v3/companies").json()["total"]
        rejected = target.post(
            "/api/v3/restores/confirm",
            json={"preview_id": conflicting.json()["id"]},
        )
        assert rejected.status_code == 409
        assert target.get("/api/v3/companies").json()["total"] == before


def test_restore_rejects_a_tampered_payload(tmp_path: Path) -> None:
    path = tmp_path / "target"
    path.mkdir()
    payload = {
        "schema": "enterprise-insight.project-archive.v1",
        "tables": {},
    }
    buffer = io.BytesIO()
    with ZipFile(buffer, "w", ZIP_DEFLATED) as archive:
        archive.writestr("restore.json", json.dumps(payload))
        archive.writestr(
            "restore.manifest.json",
            json.dumps(
                {
                    "schema": "enterprise-insight.project-archive.v1",
                    "payload_sha256": "0" * 64,
                    "table_counts": {},
                }
            ),
        )
    with _client(path) as client:
        response = client.post(
            "/api/v3/restores/preview",
            files={"file": ("tampered.zip", buffer.getvalue(), "application/zip")},
        )
        assert response.status_code == 422
        assert response.json()["error"]["code"] == "RESTORE_HASH_MISMATCH"


def test_restore_preview_write_failure_cleans_temp_and_preview_file(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    path = tmp_path / "target"
    path.mkdir()
    package = _archive_package(_minimal_archive_payload())
    original_write_bytes = Path.write_bytes

    def partial_write(path: Path, data: bytes, *args: object, **kwargs: object) -> int:
        original_write_bytes(path, data[:1], *args, **kwargs)
        raise OSError("simulated disk-full")

    with _client(path) as client:
        monkeypatch.setattr(Path, "write_bytes", partial_write)

        with pytest.raises(OSError, match="disk-full"):
            client.post(
                "/api/v3/restores/preview",
                files={"file": ("project.zip", package, "application/zip")},
            )

    preview_dir = path / "restore-previews"
    assert list(preview_dir.iterdir()) == []
