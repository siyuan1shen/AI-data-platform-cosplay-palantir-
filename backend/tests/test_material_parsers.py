from __future__ import annotations

import io
import json
import zipfile

from fastapi.testclient import TestClient
from openpyxl import Workbook

import enterprise_insight_backend.api as api
import enterprise_insight_backend.parsers as parsers


def _project(client: TestClient) -> str:
    company = client.post("/api/v3/companies", json={"name": "材料解析测试企业"}).json()
    project = client.post(
        f"/api/v3/companies/{company['id']}/projects", json={"name": "材料解析"}
    ).json()
    return f"/api/v3/projects/{project['id']}"


def _preview(client: TestClient, base: str, name: str, content: bytes, kind: str) -> dict:
    response = client.post(
        f"{base}/imports/preview",
        files={"file": (name, content, "application/octet-stream")},
        data={"kind": kind},
    )
    assert response.status_code == 200, response.text
    return response.json()


def _confirm(client: TestClient, base: str, preview: dict) -> dict:
    response = client.post(
        f"{base}/imports/confirm",
        json={"preview_id": preview["id"], "mapping": {}, "options": {}},
    )
    assert response.status_code == 200, response.text
    return response.json()


def test_text_and_json_materials_have_real_preview_and_confirm(client: TestClient) -> None:
    base = _project(client)
    text_preview = _preview(
        client,
        base,
        "interview.md",
        "销售负责接单。\n\n财务负责回款确认。".encode(),
        "TXT",
    )
    assert text_preview["columns"] == []
    assert text_preview["sample_rows"][0] == {
        "locator": "paragraph:1",
        "text": "销售负责接单。",
    }
    text_result = _confirm(client, base, text_preview)
    assert text_result["fragments_created"] == 2
    assert text_result["claims_created"] == 2

    json_preview = _preview(
        client,
        base,
        "roles.json",
        json.dumps([{"岗位": "销售", "人数": 3}, {"岗位": "财务", "人数": 2}]).encode(),
        "JSON",
    )
    assert json_preview["columns"] == ["岗位", "人数"]
    assert json_preview["sample_rows"][1]["人数"] == 2
    assert _confirm(client, base, json_preview)["fragments_created"] == 2


def test_xlsx_preserves_sheet_and_row_locator(client: TestClient) -> None:
    base = _project(client)
    workbook = Workbook()
    sheet = workbook.active
    sheet.title = "岗位清单"
    sheet.append(["岗位", "职责"])
    sheet.append(["销售经理", "审批报价"])
    buffer = io.BytesIO()
    workbook.save(buffer)
    workbook.close()

    preview = _preview(client, base, "roles.xlsx", buffer.getvalue(), "XLSX")
    assert preview["columns"] == ["岗位", "职责"]
    assert preview["sample_rows"][0]["__locator__"] == "sheet:岗位清单:row:2"
    result = _confirm(client, base, preview)
    assert result["fragments_created"] == 1
    document = client.get(f"{base}/documents").json()["items"][0]
    fragments = client.get(f"{base}/documents/{document['id']}/fragments").json()["items"]
    assert fragments[0]["locator"] == "sheet:岗位清单:row:2"


def test_docx_parses_paragraphs_and_table_rows(client: TestClient) -> None:
    base = _project(client)
    document_xml = """<?xml version="1.0" encoding="UTF-8" standalone="yes"?>
    <w:document xmlns:w="http://schemas.openxmlformats.org/wordprocessingml/2006/main">
      <w:body>
        <w:p><w:r><w:t>访谈结论</w:t></w:r></w:p>
        <w:tbl><w:tr><w:tc><w:p><w:r><w:t>岗位</w:t></w:r></w:p></w:tc><w:tc><w:p><w:r><w:t>职责</w:t></w:r></w:p></w:tc></w:tr></w:tbl>
      </w:body>
    </w:document>"""
    buffer = io.BytesIO()
    with zipfile.ZipFile(buffer, "w") as archive:
        archive.writestr("word/document.xml", document_xml)

    preview = _preview(client, base, "interview.docx", buffer.getvalue(), "DOCX")
    assert [row["locator"] for row in preview["sample_rows"]] == [
        "paragraph:1",
        "table:1:row:1",
    ]
    assert _confirm(client, base, preview)["fragments_created"] == 2


def test_compressed_material_limits_are_enforced_before_large_expansion(
    client: TestClient, monkeypatch
) -> None:
    base = _project(client)
    monkeypatch.setattr(parsers, "MAX_ARCHIVE_MEMBER_UNCOMPRESSED_BYTES", 32)
    buffer = io.BytesIO()
    with zipfile.ZipFile(buffer, "w") as archive:
        archive.writestr("word/document.xml", b"x" * 33)
    response = client.post(
        f"{base}/imports/preview",
        files={"file": ("large.docx", buffer.getvalue(), "application/octet-stream")},
        data={"kind": "DOCX"},
    )
    assert response.status_code == 413, response.text
    assert response.json()["error"]["code"] == "IMPORT_ARCHIVE_MEMBER_TOO_LARGE"


def test_xlsx_row_limit_is_enforced(client: TestClient, monkeypatch) -> None:
    base = _project(client)
    monkeypatch.setattr(parsers, "MAX_XLSX_ROWS", 1)
    workbook = Workbook()
    sheet = workbook.active
    sheet.append(["岗位"])
    sheet.append(["销售"])
    sheet.append(["财务"])
    buffer = io.BytesIO()
    workbook.save(buffer)
    workbook.close()
    response = client.post(
        f"{base}/imports/preview",
        files={"file": ("roles.xlsx", buffer.getvalue(), "application/octet-stream")},
        data={"kind": "XLSX"},
    )
    assert response.status_code == 413, response.text
    assert response.json()["error"]["code"] == "IMPORT_WORKBOOK_TOO_LARGE"


def test_selected_kind_must_match_file_extension(client: TestClient) -> None:
    base = _project(client)
    response = client.post(
        f"{base}/imports/preview",
        files={"file": ("notes.txt", b"hello", "text/plain")},
        data={"kind": "PDF"},
    )
    assert response.status_code == 422
    assert response.json()["error"]["code"] == "IMPORT_FILE_EXTENSION_MISMATCH"


def test_upload_size_limit_is_enforced_before_parser_work(client: TestClient, monkeypatch) -> None:
    base = _project(client)
    monkeypatch.setattr(api, "MAX_IMPORT_BYTES", 4)
    response = client.post(
        f"{base}/imports/preview",
        files={"file": ("notes.txt", b"hello", "text/plain")},
        data={"kind": "TXT"},
    )
    assert response.status_code == 413, response.text
    assert response.json()["error"]["code"] == "IMPORT_FILE_TOO_LARGE"


def test_failed_companycheck_confirmation_can_be_corrected_and_retried(
    client: TestClient,
) -> None:
    base = _project(client)
    preview = _preview(
        client,
        base,
        "answers.csv",
        "题目,回复值\n交期由谁确认,销售负责人\n".encode("utf-8-sig"),
        "COMPANYCHECK_CSV",
    )
    assert preview["suggested_mapping"] == {"question": "题目"}

    rejected = client.post(
        f"{base}/imports/confirm",
        json={"preview_id": preview["id"], "mapping": {}, "options": {}},
    )
    assert rejected.status_code == 422, rejected.text
    assert rejected.json()["error"]["code"] == "IMPORT_MAPPING_REQUIRED"

    confirmed = client.post(
        f"{base}/imports/confirm",
        json={
            "preview_id": preview["id"],
            "mapping": {"answer": "回复值"},
            "options": {},
        },
    )
    assert confirmed.status_code == 200, confirmed.text
    assert confirmed.json()["fragments_created"] == 1

    consumed = client.post(
        f"{base}/imports/confirm",
        json={"preview_id": preview["id"], "mapping": {"answer": "回复值"}},
    )
    assert consumed.status_code == 409, consumed.text
    assert consumed.json()["error"]["code"] == "IMPORT_PREVIEW_CONSUMED"


def test_companycheck_export_headers_are_detected_and_answers_are_merged(
    client: TestClient,
) -> None:
    base = _project(client)
    content = (
        "公司标识,问卷编号,问题,合并答案,访谈视角\n"
        "青岚精密,QN-01,订单承诺由谁确认？,销售先承诺,销售负责人\n"
        "青岚精密,QN-01,订单承诺由谁确认？,计划复核产能,计划主管\n"
        "青岚精密,QN-01,订单承诺由谁确认？,计划复核产能,计划主管\n"
        "青岚精密,QN-01,未回答问题,,总经理\n"
    ).encode("utf-8-sig")
    preview = _preview(client, base, "companycheck.csv", content, "COMPANYCHECK_CSV")
    assert preview["suggested_mapping"] == {
        "company": "公司标识",
        "questionnaire": "问卷编号",
        "question": "问题",
        "answer": "合并答案",
        "perspective": "访谈视角",
    }

    result = _confirm(client, base, preview)
    assert result["fragments_created"] == 1
    assert result["claims_created"] == 1
    assert result["rows_skipped"] == 1

    document = client.get(f"{base}/documents").json()["items"][0]
    fragments = client.get(f"{base}/documents/{document['id']}/fragments").json()["items"]
    assert fragments[0]["metadata"]["answer_count"] == 2
    assert "[销售负责人] 销售先承诺" in fragments[0]["text"]
    assert fragments[0]["text"].count("计划复核产能") == 1


def test_multi_company_companycheck_import_requires_and_applies_selection(
    client: TestClient,
) -> None:
    base = _project(client)
    content = (
        "公司标识,问卷编号,问题,合并答案\n"
        "甲公司,QN-01,订单由谁确认？,销售负责人\n"
        "乙公司,QN-01,订单由谁确认？,运营负责人\n"
    ).encode("utf-8-sig")
    preview = _preview(client, base, "multi-company.csv", content, "COMPANYCHECK_CSV")
    assert any("多个企业标识" in warning for warning in preview["warnings"])

    rejected = client.post(
        f"{base}/imports/confirm",
        json={"preview_id": preview["id"], "mapping": {}, "options": {}},
    )
    assert rejected.status_code == 422, rejected.text
    assert rejected.json()["error"]["code"] == "IMPORT_COMPANY_SELECTION_REQUIRED"
    assert "甲公司" in rejected.json()["error"]["details"][0]["company_values"]

    confirmed = client.post(
        f"{base}/imports/confirm",
        json={
            "preview_id": preview["id"],
            "mapping": {},
            "options": {"target_company": "甲公司"},
        },
    )
    assert confirmed.status_code == 200, confirmed.text
    result = confirmed.json()
    assert result["fragments_created"] == 1
    assert result["rows_skipped"] == 1

    document = client.get(f"{base}/documents").json()["items"][0]
    fragments = client.get(f"{base}/documents/{document['id']}/fragments").json()["items"]
    assert len(fragments) == 1
    assert fragments[0]["metadata"]["company"] == "甲公司"
    assert "销售负责人" in fragments[0]["text"]
    assert fragments[0]["metadata"]["company"] != "乙公司"
    assert "运营负责人" not in fragments[0]["text"]


def test_multi_company_companycheck_import_rejects_unknown_selection(
    client: TestClient,
) -> None:
    base = _project(client)
    content = (
        "公司,问题,答案\n"
        "甲公司,组织架构如何调整？,需要补充运营岗位\n"
        "乙公司,组织架构如何调整？,需要补充财务岗位\n"
    ).encode("utf-8-sig")
    preview = _preview(client, base, "multi-company-invalid.csv", content, "COMPANYCHECK_CSV")
    rejected = client.post(
        f"{base}/imports/confirm",
        json={
            "preview_id": preview["id"],
            "mapping": {},
            "options": {"target_company": "丙公司"},
        },
    )
    assert rejected.status_code == 422, rejected.text
    assert rejected.json()["error"]["code"] == "IMPORT_COMPANY_SELECTION_INVALID"
