from __future__ import annotations

from fastapi.testclient import TestClient


def test_capability_manifest_reports_external_action_and_backup_boundaries(
    client: TestClient,
) -> None:
    response = client.get("/api/v3/meta/capabilities")

    assert response.status_code == 200, response.text
    capabilities = {
        item["key"]: item for item in response.json()["capabilities"]
    }
    assert capabilities["action_engine"]["status"] == "PARTIAL"
    assert "外部系统写入连接器尚未接通" in capabilities["action_engine"]["description"]
    assert capabilities["vendor_erp_connectors"]["status"] == "PARTIAL"
    assert "ERPNext/Frappe 只读参考适配" in capabilities["vendor_erp_connectors"]["description"]
    assert capabilities["multi_store_backup_restore"]["status"] == "PARTIAL"
    assert "活动数据切换尚未完成" in capabilities["multi_store_backup_restore"]["description"]
