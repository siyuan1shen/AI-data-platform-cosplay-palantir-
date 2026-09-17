from __future__ import annotations

from datetime import timedelta

from fastapi.testclient import TestClient

from enterprise_insight_backend.models import ImportPreviewRow, LifecycleDeletionAuditRow
from enterprise_insight_backend.service_utils import now_utc


def _project(client: TestClient) -> str:
    company = client.post("/api/v3/companies", json={"name": "生命周期测试企业"}).json()
    project = client.post(
        f"/api/v3/companies/{company['id']}/projects", json={"name": "临时数据测试"}
    ).json()
    return project["id"]


def test_expired_import_preview_is_previewable_deleted_and_audited(client: TestClient) -> None:
    project_id = _project(client)
    preview_id = "00000000-0000-0000-0000-000000000099"
    with client.app.state.database.session_factory() as session:
        session.add(
            ImportPreviewRow(
                id=preview_id,
                project_id=project_id,
                source_system_id=None,
                file_name="expired.csv",
                kind="CSV",
                content_sha256="a" * 64,
                detected_encoding="utf-8",
                columns=["name"],
                sample_rows=[{"name": "temporary"}],
                all_rows=[{"name": "temporary"}],
                suggested_mapping={},
                warnings=[],
                preview_metadata={},
                expires_at=now_utc() - timedelta(minutes=1),
            )
        )
        session.commit()

    preview = client.get(f"/api/v3/projects/{project_id}/lifecycle/cleanup/preview")
    assert preview.status_code == 200, preview.text
    candidates = preview.json()["candidates"]
    assert any(item["resource_id"] == preview_id for item in candidates)

    result = client.post(
        f"/api/v3/projects/{project_id}/lifecycle/cleanup",
        json={
            "resource_kinds": ["IMPORT_PREVIEW"],
            "candidate_ids": [preview_id],
            "reason": "测试完成，清理临时预览",
        },
    )
    assert result.status_code == 200, result.text
    assert result.json()["deleted"] == 1

    with client.app.state.database.session_factory() as session:
        assert session.get(ImportPreviewRow, preview_id) is None
        audit = session.query(LifecycleDeletionAuditRow).one()
        assert audit.resource_id == preview_id
        assert audit.deletion_mode == "MANUAL"
        assert audit.reason == "测试完成，清理临时预览"


def test_manual_delete_requires_reason_and_cannot_delete_formal_data(client: TestClient) -> None:
    project_id = _project(client)
    missing = client.request(
        "DELETE",
        f"/api/v3/projects/{project_id}/lifecycle/resources/IMPORT_PREVIEW/"
        "00000000-0000-0000-0000-000000000098",
        json={"reason": "不存在"},
    )
    assert missing.status_code == 404

    invalid_kind = client.request(
        "DELETE",
        f"/api/v3/projects/{project_id}/lifecycle/resources/RESTORE_PREVIEW/"
        "00000000-0000-0000-0000-000000000097",
        json={"reason": "不允许删除正式数据"},
    )
    assert invalid_kind.status_code == 422
