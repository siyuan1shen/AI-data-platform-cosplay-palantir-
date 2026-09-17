from __future__ import annotations

from io import BytesIO

from fastapi.testclient import TestClient

# Field names are taken from the downloaded ERPNext Purchase Order, Purchase Order Item,
# and Supplier DocType JSON schemas. Values follow ERPNext's own
# buying/doctype/purchase_order/test_purchase_order.py helper defaults. This is a small,
# synthetic offline export fixture—not a database dump or a file exported by a running
# ERPNext instance. It deliberately includes an empty supplier_name and an unresolved
# supplier foreign key to verify that the importer preserves evidence without guessing.
SUPPLIERS_CSV = b"""name,supplier_name,supplier_group,supplier_type
_Test Supplier,Test Supplier,Local,Company
"""

PURCHASE_ORDERS_CSV = (
    b"name,supplier,supplier_name,transaction_date,schedule_date,"
    b"company,status,item_code,qty,rate,amount\n"
    b"PUR-ORD-0001,_Test Supplier,Test Supplier,2026-09-01,2026-09-02,"
    b"_Test Company,To Receive and Bill,_Test Item,10,500,5000\n"
    b"PUR-ORD-0002,SUPPLIER-NOT-IN-EXPORT,,2026-09-02,2026-09-03,"
    b"_Test Company,To Receive and Bill,_Test Item,10,500,5000\n"
)


def _project(client: TestClient) -> str:
    company = client.post("/api/v3/companies", json={"name": "ERPNext 接入验收"})
    assert company.status_code == 201, company.text
    project = client.post(
        f"/api/v3/companies/{company.json()['id']}/projects",
        json={"name": "采购订单导入验证"},
    )
    assert project.status_code == 201, project.text
    return project.json()["id"]


def _create_type(
    client: TestClient, project_id: str, payload: dict[str, object]
) -> dict[str, object]:
    response = client.post(
        f"/api/v3/projects/{project_id}/ontology/types", json=payload
    )
    assert response.status_code == 201, response.text
    return response.json()


def _approve_mapping(
    client: TestClient, project_id: str, payload: dict[str, object]
) -> dict[str, object]:
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
    return approved.json()


def _approve_relation_mapping(
    client: TestClient, project_id: str, payload: dict[str, object]
) -> dict[str, object]:
    created = client.post(
        f"/api/v3/projects/{project_id}/semantic-relation-mappings", json=payload
    )
    assert created.status_code == 201, created.text
    mapping = created.json()
    validated = client.post(
        f"/api/v3/projects/{project_id}/semantic-relation-mappings/{mapping['id']}/validate",
        json={"expected_revision": mapping["revision"]},
    )
    assert validated.status_code == 200, validated.text
    mapping = validated.json()
    approved = client.post(
        f"/api/v3/projects/{project_id}/semantic-relation-mappings/{mapping['id']}/approve",
        json={"expected_revision": mapping["revision"]},
    )
    assert approved.status_code == 200, approved.text
    assert approved.json()["status"] == "APPROVED"
    return approved.json()


def _import_csv(
    client: TestClient,
    project_id: str,
    source_id: str,
    file_name: str,
    content: bytes,
) -> dict[str, object]:
    preview = client.post(
        f"/api/v3/projects/{project_id}/source-systems/{source_id}/imports/preview",
        files={"file": (file_name, BytesIO(content), "text/csv")},
        data={"kind": "CSV"},
    )
    assert preview.status_code == 200, preview.text
    preview_data = preview.json()
    assert preview_data["columns"]
    confirmed = client.post(
        f"/api/v3/projects/{project_id}/imports/confirm",
        json={"preview_id": preview_data["id"], "options": {}},
    )
    assert confirmed.status_code == 200, confirmed.text
    return confirmed.json()


def test_erpnext_purchase_export_maps_entities_and_preserves_missing_link_evidence(
    client: TestClient,
) -> None:
    project_id = _project(client)

    _create_type(
        client,
        project_id,
        {
            "key": "erp_supplier",
            "name": "ERPNext 供应商",
            "kind": "OBJECT",
            "properties": [
                {"key": "supplier_group", "name": "供应商组", "value_type": "STRING"},
                {"key": "supplier_type", "name": "供应商类型", "value_type": "STRING"},
            ],
        },
    )
    _create_type(
        client,
        project_id,
        {
            "key": "erp_purchase_order",
            "name": "ERPNext 采购订单",
            "kind": "OBJECT",
            "properties": [
                {"key": "supplier_code", "name": "供应商编码", "value_type": "STRING"},
                {"key": "transaction_date", "name": "订单日期", "value_type": "STRING"},
                {"key": "schedule_date", "name": "要求日期", "value_type": "STRING"},
                {"key": "company_name", "name": "公司", "value_type": "STRING"},
                {"key": "status", "name": "状态", "value_type": "STRING"},
                {"key": "item_code", "name": "物料编码", "value_type": "STRING"},
                {"key": "quantity", "name": "数量", "value_type": "NUMBER"},
                {"key": "unit_rate", "name": "单价", "value_type": "NUMBER"},
                {"key": "amount", "name": "金额", "value_type": "NUMBER"},
            ],
        },
    )
    _create_type(
        client,
        project_id,
        {
            "key": "purchase_from_supplier",
            "name": "采购自供应商",
            "kind": "RELATION",
            "relation_roles": [
                {
                    "key": "order",
                    "name": "采购订单",
                    "allowed_type_keys": ["erp_purchase_order"],
                    "minimum": 1,
                    "maximum": 1,
                },
                {
                    "key": "supplier",
                    "name": "供应商",
                    "allowed_type_keys": ["erp_supplier"],
                    "minimum": 1,
                    "maximum": 1,
                },
            ],
        },
    )

    source_response = client.post(
        f"/api/v3/projects/{project_id}/source-systems",
        json={"name": "ERPNext 离线导出", "kind": "FILE", "connection_profile": {}},
    )
    assert source_response.status_code == 201, source_response.text
    source_id = source_response.json()["id"]

    mappings = [
        ("suppliers.csv", "name", "erp_supplier", "__stable_key__", "strip"),
        ("suppliers.csv", "supplier_name", "erp_supplier", "__name__", "strip"),
        ("suppliers.csv", "supplier_group", "erp_supplier", "supplier_group", "strip"),
        ("suppliers.csv", "supplier_type", "erp_supplier", "supplier_type", "strip"),
        ("purchase_orders.csv", "name", "erp_purchase_order", "__stable_key__", "strip"),
        (
            "purchase_orders.csv",
            "supplier_name",
            "erp_purchase_order",
            "__name__",
            "strip|empty_to_null",
        ),
        ("purchase_orders.csv", "supplier", "erp_purchase_order", "supplier_code", "strip"),
        (
            "purchase_orders.csv",
            "transaction_date",
            "erp_purchase_order",
            "transaction_date",
            "strip",
        ),
        (
            "purchase_orders.csv",
            "schedule_date",
            "erp_purchase_order",
            "schedule_date",
            "strip",
        ),
        ("purchase_orders.csv", "company", "erp_purchase_order", "company_name", "strip"),
        ("purchase_orders.csv", "status", "erp_purchase_order", "status", "strip"),
        ("purchase_orders.csv", "item_code", "erp_purchase_order", "item_code", "strip"),
        ("purchase_orders.csv", "qty", "erp_purchase_order", "quantity", "float"),
        ("purchase_orders.csv", "rate", "erp_purchase_order", "unit_rate", "float"),
        ("purchase_orders.csv", "amount", "erp_purchase_order", "amount", "float"),
    ]
    for asset, field, type_key, property_key, transform in mappings:
        _approve_mapping(
            client,
            project_id,
            {
                "source_system_id": source_id,
                "source_asset": asset,
                "source_field": field,
                "target_type_key": type_key,
                "target_property_key": property_key,
                "transform_expression": transform,
            },
        )

    relation_mapping = _approve_relation_mapping(
        client,
        project_id,
        {
            "source_system_id": source_id,
            "source_asset": "purchase_orders.csv",
            "source_type_key": "erp_purchase_order",
            "source_field": "supplier",
            "relation_type_key": "purchase_from_supplier",
            "source_role_key": "order",
            "target_type_key": "erp_supplier",
            "target_role_key": "supplier",
            "target_asset": "suppliers.csv",
            "transform_expression": "strip",
        },
    )

    suppliers_result = _import_csv(
        client, project_id, source_id, "suppliers.csv", SUPPLIERS_CSV
    )
    assert suppliers_result["entities_created"] == 1
    assert suppliers_result["mappings_applied"] == 4

    orders_result = _import_csv(
        client, project_id, source_id, "purchase_orders.csv", PURCHASE_ORDERS_CSV
    )
    assert orders_result["entities_created"] == 2
    assert orders_result["relations_created"] == 1
    assert any("来源关系未建立" in warning for warning in orders_result["warnings"])

    identities = client.get(
        f"/api/v3/projects/{project_id}/source-identities",
        params={"source_system_id": source_id},
    ).json()["items"]
    order_identities = {
        item["source_record_key"]: item
        for item in identities
        if item["target_type_key"] == "erp_purchase_order"
    }
    assert set(order_identities) == {"PUR-ORD-0001", "PUR-ORD-0002"}

    observations = client.get(
        f"/api/v3/projects/{project_id}/observation-assertions"
    ).json()["items"]
    by_identity_and_field = {
        (item["source_record_key"], item["field_key"]): item["value"]
        for item in observations
    }
    assert by_identity_and_field[("PUR-ORD-0001", "supplier_code")] == "_Test Supplier"
    assert by_identity_and_field[("PUR-ORD-0001", "quantity")] == 10.0
    assert by_identity_and_field[("PUR-ORD-0001", "unit_rate")] == 500.0
    assert ("PUR-ORD-0002", "__name__") not in by_identity_and_field

    relations = client.get(f"/api/v3/projects/{project_id}/relations").json()["items"]
    assert len(relations) == 1
    assert relations[0]["type_key"] == "purchase_from_supplier"
    assert {item["role_key"] for item in relations[0]["participants"]} == {
        "order",
        "supplier",
    }

    runs = client.get(f"/api/v3/projects/{project_id}/materialization-runs").json()["items"]
    order_run = next(item for item in runs if item["records_processed"] == 2)
    assert order_run["relations_created"] == 1
    assert len(order_run["errors"]) == 1
    assert order_run["errors"][0]["code"] == "SOURCE_RELATION_TARGET_NOT_FOUND"
    assert order_run["mapping_ids"]
    assert relation_mapping["id"] in order_run["mapping_ids"]

    raw_batches = client.get(f"/api/v3/projects/{project_id}/raw-batches").json()["items"]
    order_batch = next(item for item in raw_batches if item["record_count"] == 2)
    raw_records = client.get(
        f"/api/v3/projects/{project_id}/raw-batches/{order_batch['id']}/records"
    ).json()["items"]
    missing_supplier_row = next(
        item for item in raw_records if item["payload"]["name"] == "PUR-ORD-0002"
    )
    assert missing_supplier_row["payload"]["supplier"] == "SUPPLIER-NOT-IN-EXPORT"
    assert missing_supplier_row["payload"]["supplier_name"] == ""
