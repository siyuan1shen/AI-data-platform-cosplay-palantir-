from __future__ import annotations

import hashlib
import json
import re
from collections import defaultdict
from datetime import timedelta
from typing import Any
from uuid import NAMESPACE_URL, UUID, uuid4, uuid5

from sqlalchemy import func, select, update
from sqlalchemy.orm import Session

from enterprise_insight_backend.errors import DomainError
from enterprise_insight_backend.models import (
    ClaimRow,
    EntityRow,
    EvidenceFragmentRow,
    ImportPreviewRow,
    MaterializationRunRow,
    ObservationAssertionRow,
    RawBatchRow,
    RawRecordRow,
    RelationParticipantRow,
    RelationRow,
    SemanticMappingRow,
    SemanticRelationMappingRow,
    SourceAssetRow,
    SourceDocumentRow,
    SourceIdentityRow,
)
from enterprise_insight_backend.parsers import parse_import
from enterprise_insight_backend.portfolio import PortfolioService
from enterprise_insight_backend.projection import ProjectionService
from enterprise_insight_backend.schemas import (
    ClaimView,
    DesignMembership,
    EntityCreate,
    EvidenceFragmentView,
    EvidenceReference,
    ImportConfirmRequest,
    ImportKind,
    ImportPreviewView,
    ImportResultView,
    LifecycleStatus,
    MaterializationRunView,
    RawBatchView,
    RawRecordView,
    RelationCreate,
    RelationParticipantInput,
    SourceDocumentView,
    Viewpoint,
)
from enterprise_insight_backend.service_utils import json_ready, now_utc
from enterprise_insight_backend.transforms import apply_transform

FIELD_ALIASES: dict[str, tuple[str, ...]] = {
    "company": (
        "company",
        "companyname",
        "企业",
        "公司",
        "公司名称",
        "企业名称",
        "公司标识",
        "企业标识",
    ),
    "questionnaire": (
        "questionnaire",
        "survey",
        "问卷",
        "问卷名称",
        "问卷标题",
        "问卷编号",
        "问卷ID",
    ),
    "question": ("question", "questiontext", "问题", "题目", "问题内容"),
    "answer": (
        "answer",
        "answertext",
        "回答",
        "答案",
        "回答内容",
        "合并回答",
        "合并答案",
    ),
    "respondent": ("respondent", "姓名", "受访者", "答题人", "填写人"),
    "perspective": ("perspective", "角色", "岗位", "部门", "视角", "访谈视角"),
}


class EvidenceService:
    def __init__(self, session: Session) -> None:
        self.session = session
        self.portfolio = PortfolioService(session)

    def preview_csv(
        self,
        project_id: UUID,
        *,
        file_name: str,
        content: bytes,
        kind: ImportKind,
        source_system_id: UUID | None = None,
        preview_metadata: dict[str, Any] | None = None,
    ) -> ImportPreviewView:
        self.portfolio.require_project(project_id)
        parsed = parse_import(file_name, content, kind)
        columns = parsed.columns
        rows = parsed.rows
        mapping = self._suggest_mapping(columns)
        warnings = list(parsed.warnings)
        if kind == ImportKind.COMPANYCHECK_CSV:
            missing = [key for key in ("question", "answer") if key not in mapping]
            if missing:
                warnings.append(f"未自动识别字段：{', '.join(missing)}，确认导入前需要手动映射。")
            company_values = self._company_values(rows, mapping.get("company"))
            if len(company_values) > 1:
                visible_values = company_values[:20]
                suffix = " 等" if len(company_values) > len(visible_values) else ""
                warnings.append(
                    "检测到多个企业标识；确认导入时必须填写 options.target_company，"
                    f"可选企业：{'、'.join(visible_values)}{suffix}；"
                    "系统只会写入所选企业的回答。"
                )
        sha256 = hashlib.sha256(content).hexdigest()
        duplicate = self.session.scalar(
            select(SourceDocumentRow.id).where(
                SourceDocumentRow.project_id == str(project_id),
                SourceDocumentRow.sha256 == sha256,
                SourceDocumentRow.source_system_id
                == (str(source_system_id) if source_system_id else None),
            )
        )
        if duplicate:
            warnings.append("相同内容此前已经导入，确认时会按重复文件处理。")
        row = ImportPreviewRow(
            project_id=str(project_id),
            source_system_id=str(source_system_id) if source_system_id else None,
            file_name=file_name,
            kind=kind.value,
            content_sha256=sha256,
            detected_encoding=parsed.encoding,
            columns=columns,
            sample_rows=rows[:20],
            all_rows=rows,
            suggested_mapping=mapping,
            warnings=warnings,
            preview_metadata=json_ready(preview_metadata or {}),
            expires_at=now_utc() + timedelta(hours=24),
        )
        self.session.add(row)
        self.session.flush()
        return self._preview_view(row)

    def confirm(self, project_id: UUID, payload: ImportConfirmRequest) -> ImportResultView:
        self.portfolio.require_project(project_id)
        preview = self.session.get(ImportPreviewRow, str(payload.preview_id))
        if preview is None or preview.project_id != str(project_id):
            raise DomainError("IMPORT_PREVIEW_NOT_FOUND", "导入预览不存在。", status_code=404)
        if preview.consumed_at is not None:
            raise DomainError("IMPORT_PREVIEW_CONSUMED", "该预览已经确认过。", status_code=409)
        expires_at = preview.expires_at
        if expires_at.tzinfo is None:
            expires_at = expires_at.replace(tzinfo=now_utc().tzinfo)
        if expires_at < now_utc():
            raise DomainError(
                "IMPORT_PREVIEW_EXPIRED", "导入预览已过期，请重新上传。", status_code=410
            )
        mapping = {**preview.suggested_mapping, **payload.mapping}
        kind = ImportKind(preview.kind)
        if kind == ImportKind.COMPANYCHECK_CSV:
            for required in ("question", "answer"):
                if not mapping.get(required):
                    raise DomainError(
                        "IMPORT_MAPPING_REQUIRED",
                        f"缺少 {required} 字段映射。",
                        status_code=422,
                    )
            selected_rows, company_rows_skipped = self._select_companycheck_rows(
                preview.all_rows, mapping, payload.options
            )
        else:
            selected_rows = preview.all_rows
            company_rows_skipped = 0
        sha256 = preview.content_sha256
        existing = self.session.scalar(
            select(SourceDocumentRow).where(
                SourceDocumentRow.project_id == str(project_id),
                SourceDocumentRow.sha256 == sha256,
                SourceDocumentRow.source_system_id == preview.source_system_id,
            )
        )
        consumed_at = now_utc()
        claimed_preview = self.session.execute(
            update(ImportPreviewRow)
            .where(
                ImportPreviewRow.id == preview.id,
                ImportPreviewRow.project_id == str(project_id),
                ImportPreviewRow.consumed_at.is_(None),
            )
            .values(consumed_at=consumed_at)
            .returning(ImportPreviewRow.id)
        ).scalar_one_or_none()
        if claimed_preview is None:
            raise DomainError(
                "IMPORT_PREVIEW_CONSUMED",
                "该预览已经确认过。",
                status_code=409,
            )
        if existing is not None:
            if preview.source_system_id:
                source_asset_key = str(payload.options.get("source_asset") or existing.file_name)
                _, raw_batch, drifted = self._ensure_raw_layer(
                    project_id,
                    existing,
                    preview.all_rows,
                    source_asset=source_asset_key,
                    columns=preview.columns,
                )
                materialization_result = (
                    None if drifted else self._materialize_raw_batch(project_id, raw_batch)
                )
                materialization = materialization_result[0] if materialization_result else None
                materialized_now = materialization_result[1] if materialization_result else False
                entities_created = (
                    materialization.entities_created if materialization and materialized_now else 0
                )
                entities_updated = 0
                mappings_applied = materialization.mappings_applied if materialization else 0
                identities_bound = (
                    materialization.identities_bound if materialization and materialized_now else 0
                )
                observations_created = (
                    materialization.observations_created
                    if materialization and materialized_now
                    else 0
                )
                relations_created = (
                    materialization.relations_created
                    if materialization and materialized_now
                    else 0
                )
                duplicate_warnings = [
                    "原始文件已存在，本次按当前语义映射重新计算，未重复保存材料。"
                ]
                if drifted:
                    duplicate_warnings.append(
                        "检测到来源资产字段变化；原始批次已保存，但需复核映射后再物化。"
                    )
                if mappings_applied == 0:
                    duplicate_warnings.append("当前没有可执行语义映射，未生成或更新企业对象。")
                if materialization and materialization.relations_created == 0:
                    relation_errors = materialization.model_dump().get("errors") or []
                    if relation_errors:
                        duplicate_warnings.append(
                            f"有 {len(relation_errors)} 条来源关系未建立，"
                            "请检查外键映射和目标身份。"
                        )
                self.session.flush()
                return ImportResultView(
                    id=uuid4(),
                    project_id=project_id,
                    status="REPROCESSED",
                    documents_created=0,
                    fragments_created=0,
                    claims_created=0,
                    entities_created=entities_created,
                    entities_updated=entities_updated,
                    identities_bound=identities_bound,
                    observations_created=observations_created,
                    relations_created=relations_created,
                    mappings_applied=mappings_applied,
                    rows_skipped=0,
                    warnings=duplicate_warnings,
                    created_at=now_utc(),
                )
            self.session.flush()
            return ImportResultView(
                id=uuid4(),
                project_id=project_id,
                status="DUPLICATE",
                documents_created=0,
                fragments_created=0,
                claims_created=0,
                rows_skipped=len(preview.all_rows),
                warnings=["文件内容已经导入，本次未重复写入。"],
                created_at=now_utc(),
            )
        source = SourceDocumentRow(
            project_id=str(project_id),
            source_system_id=preview.source_system_id,
            file_name=preview.file_name,
            kind=preview.kind,
            sha256=sha256,
            extra_metadata={
                "columns": preview.columns,
                "mapping": mapping,
                "parser_version": "v1",
                "content_kind": "TABLE" if preview.columns else "DOCUMENT",
            },
        )
        self.session.add(source)
        self.session.flush()
        entities_created = 0
        entities_updated = 0
        mappings_applied = 0
        identities_bound = 0
        observations_created = 0
        relations_created = 0
        warnings: list[str] = []
        if kind == ImportKind.COMPANYCHECK_CSV:
            fragments, claims, skipped = self._import_companycheck(
                source, selected_rows, mapping
            )
        elif self._is_document_preview(preview):
            fragments, claims, skipped = self._import_generic_document(source, preview.all_rows)
        else:
            fragments, claims, skipped = self._import_generic_csv(source, preview.all_rows)
            if preview.source_system_id:
                source_asset_key = str(payload.options.get("source_asset") or source.file_name)
                _, raw_batch, drifted = self._ensure_raw_layer(
                    project_id,
                    source,
                    preview.all_rows,
                    source_asset=source_asset_key,
                    columns=preview.columns,
                )
                materialization_result = (
                    None if drifted else self._materialize_raw_batch(project_id, raw_batch)
                )
                materialization = materialization_result[0] if materialization_result else None
                entities_created = materialization.entities_created if materialization else 0
                mappings_applied = materialization.mappings_applied if materialization else 0
                identities_bound = materialization.identities_bound if materialization else 0
                observations_created = (
                    materialization.observations_created if materialization else 0
                )
                relations_created = materialization.relations_created if materialization else 0
                if drifted:
                    warnings.append(
                        "检测到来源资产字段变化；原始批次已保存，但需复核映射后再物化。"
                    )
                if mappings_applied == 0:
                    warnings.append(
                        "文件已作为证据导入，但没有可执行语义映射；"
                        "请为该数据源配置 __stable_key__、__name__ 和本体属性映射。"
                    )
                if materialization:
                    relation_errors = materialization.model_dump().get("errors") or []
                    if relation_errors:
                        warnings.append(
                            f"有 {len(relation_errors)} 条来源关系未建立，"
                            "请检查外键映射和目标身份。"
                        )
        self.session.flush()
        return ImportResultView(
            id=uuid4(),
            project_id=project_id,
            status="COMPLETED",
            documents_created=1,
            fragments_created=fragments,
            claims_created=claims,
            entities_created=entities_created,
            entities_updated=entities_updated,
            identities_bound=identities_bound,
            observations_created=observations_created,
            relations_created=relations_created,
            mappings_applied=mappings_applied,
            rows_skipped=skipped + company_rows_skipped,
            warnings=warnings,
            created_at=now_utc(),
        )

    @staticmethod
    def _company_values(
        rows: list[dict[str, Any]], company_column: str | None
    ) -> list[str]:
        if not company_column:
            return []
        return sorted(
            {
                str(row.get(company_column) or "").strip()
                for row in rows
                if str(row.get(company_column) or "").strip()
            }
        )

    def _select_companycheck_rows(
        self,
        rows: list[dict[str, Any]],
        mapping: dict[str, str],
        options: dict[str, Any],
    ) -> tuple[list[dict[str, Any]], int]:
        company_column = mapping.get("company")
        values = self._company_values(rows, company_column)
        target_raw = options.get("target_company")
        if target_raw is not None and not isinstance(target_raw, str):
            raise DomainError(
                "IMPORT_COMPANY_SELECTION_INVALID",
                "options.target_company 必须是文本。",
                status_code=422,
            )
        target = target_raw.strip() if isinstance(target_raw, str) else ""
        if len(values) > 1 and not target:
            raise DomainError(
                "IMPORT_COMPANY_SELECTION_REQUIRED",
                "文件包含多个企业的回答，请填写 options.target_company 后再确认。",
                status_code=422,
                details=[{"company_values": values}],
            )
        if target and target not in values:
            raise DomainError(
                "IMPORT_COMPANY_SELECTION_INVALID",
                "所选企业标识不在当前文件中。",
                status_code=422,
                details=[{"company_values": values, "target_company": target}],
            )
        if not target:
            return rows, 0
        selected = [
            row for row in rows if str(row.get(company_column or "") or "").strip() == target
        ]
        return selected, len(rows) - len(selected)

    def list_raw_batches(
        self, project_id: UUID, *, source_asset_id: UUID | None = None
    ) -> list[RawBatchView]:
        self.portfolio.require_project(project_id)
        statement = select(RawBatchRow).where(RawBatchRow.project_id == str(project_id))
        if source_asset_id is not None:
            statement = statement.where(RawBatchRow.source_asset_id == str(source_asset_id))
        rows = self.session.scalars(
            statement.order_by(RawBatchRow.created_at.desc(), RawBatchRow.id.desc())
        ).all()
        return [RawBatchView.model_validate(row) for row in rows]

    def list_raw_records(
        self,
        project_id: UUID,
        raw_batch_id: UUID,
        *,
        offset: int = 0,
        limit: int = 100,
    ) -> tuple[list[RawRecordView], int]:
        batch = self.require_raw_batch(project_id, raw_batch_id)
        total = int(
            self.session.scalar(
                select(func.count(RawRecordRow.id)).where(
                    RawRecordRow.raw_batch_id == batch.id
                )
            )
            or 0
        )
        rows = self.session.scalars(
            select(RawRecordRow)
            .where(RawRecordRow.raw_batch_id == batch.id)
            .order_by(RawRecordRow.row_number)
            .offset(offset)
            .limit(limit)
        ).all()
        return [RawRecordView.model_validate(row) for row in rows], total

    def list_materialization_runs(
        self, project_id: UUID, *, raw_batch_id: UUID | None = None
    ) -> list[MaterializationRunView]:
        self.portfolio.require_project(project_id)
        statement = select(MaterializationRunRow).where(
            MaterializationRunRow.project_id == str(project_id)
        )
        if raw_batch_id is not None:
            statement = statement.where(MaterializationRunRow.raw_batch_id == str(raw_batch_id))
        rows = self.session.scalars(
            statement.order_by(
                MaterializationRunRow.created_at.desc(), MaterializationRunRow.id.desc()
            )
        ).all()
        return [MaterializationRunView.model_validate(row) for row in rows]

    def materialize_raw_batch(self, project_id: UUID, raw_batch_id: UUID) -> MaterializationRunView:
        batch = self.require_raw_batch(project_id, raw_batch_id)
        return self._materialize_raw_batch(project_id, batch)[0]

    def require_raw_batch(self, project_id: UUID, raw_batch_id: UUID) -> RawBatchRow:
        row = self.session.get(RawBatchRow, str(raw_batch_id))
        if row is None or row.project_id != str(project_id):
            raise DomainError("RAW_BATCH_NOT_FOUND", "原始数据批次不存在。", status_code=404)
        return row

    def list_documents(self, project_id: UUID) -> list[SourceDocumentView]:
        self.portfolio.require_project(project_id)
        rows = self.session.scalars(
            select(SourceDocumentRow)
            .where(SourceDocumentRow.project_id == str(project_id))
            .order_by(SourceDocumentRow.created_at.desc())
        ).all()
        return [self._document_view(row) for row in rows]

    def list_fragments(
        self, project_id: UUID, source_document_id: UUID, *, offset: int = 0,
        limit: int | None = None,
    ) -> list[EvidenceFragmentView]:
        source = self.session.get(SourceDocumentRow, str(source_document_id))
        if source is None or source.project_id != str(project_id):
            raise DomainError("SOURCE_DOCUMENT_NOT_FOUND", "材料不存在。", status_code=404)
        if offset < 0 or (limit is not None and not 1 <= limit <= 100):
            raise DomainError("FRAGMENT_PAGE_INVALID", "材料分页参数无效。", status_code=422)
        statement = (
            select(EvidenceFragmentRow)
            .where(EvidenceFragmentRow.source_document_id == str(source_document_id))
            .order_by(EvidenceFragmentRow.created_at, EvidenceFragmentRow.id)
            .offset(offset)
        )
        if limit is not None:
            statement = statement.limit(limit)
        rows = self.session.scalars(statement).all()
        return [self._fragment_view(row) for row in rows]

    def count_fragments(self, project_id: UUID, source_document_id: UUID) -> int:
        source = self.session.get(SourceDocumentRow, str(source_document_id))
        if source is None or source.project_id != str(project_id):
            raise DomainError("SOURCE_DOCUMENT_NOT_FOUND", "材料不存在。", status_code=404)
        return self.session.scalar(select(func.count(EvidenceFragmentRow.id)).where(
            EvidenceFragmentRow.source_document_id == str(source_document_id)
        )) or 0

    def list_claims(self, project_id: UUID) -> list[ClaimView]:
        self.portfolio.require_project(project_id)
        rows = self.session.scalars(
            select(ClaimRow)
            .where(ClaimRow.project_id == str(project_id))
            .order_by(ClaimRow.created_at.desc())
        ).all()
        return [ClaimView.model_validate(row) for row in rows]

    def _import_companycheck(
        self,
        source: SourceDocumentRow,
        rows: list[dict[str, Any]],
        mapping: dict[str, str],
    ) -> tuple[int, int, int]:
        grouped: dict[tuple[str, str, str], list[dict[str, str]]] = defaultdict(list)
        skipped = 0
        for row in rows:
            answer_text = str(row.get(mapping["answer"], "")).strip()
            question = str(row.get(mapping["question"], "")).strip()
            if not answer_text or not question:
                skipped += 1
                continue
            company = str(row.get(mapping.get("company", ""), "")).strip()
            questionnaire = str(row.get(mapping.get("questionnaire", ""), "")).strip()
            grouped[(company, questionnaire, question)].append(
                {
                    "answer": answer_text,
                    "respondent": str(row.get(mapping.get("respondent", ""), "")).strip(),
                    "perspective": str(row.get(mapping.get("perspective", ""), "")).strip(),
                }
            )
        fragments = 0
        claims = 0
        fragment_base_time = now_utc()
        for index, ((company, questionnaire, question), answers) in enumerate(
            grouped.items(), start=1
        ):
            unique_answers: list[dict[str, str]] = []
            seen: set[tuple[str, str, str]] = set()
            for answer_record in answers:
                answer_key = (
                    answer_record["answer"],
                    answer_record["respondent"],
                    answer_record["perspective"],
                )
                if answer_key not in seen:
                    seen.add(answer_key)
                    unique_answers.append(answer_record)
            lines: list[str] = []
            for answer_record in unique_answers:
                label = answer_record["perspective"] or answer_record["respondent"]
                lines.append(
                    f"[{label}] {answer_record['answer']}"
                    if label
                    else answer_record["answer"]
                )
            fragment = EvidenceFragmentRow(
                source_document_id=source.id,
                locator=f"question:{index}",
                text=f"问题：{question}\n回答：\n" + "\n".join(lines),
                # SQLite can truncate rapidly-created default timestamps to the same
                # value.  The explicit sequence keeps paginated reads in source order.
                created_at=fragment_base_time + timedelta(microseconds=index),
                extra_metadata={
                    "company": company,
                    "questionnaire": questionnaire,
                    "question": question,
                    "answer_count": len(unique_answers),
                    "answers": unique_answers,
                },
            )
            self.session.add(fragment)
            self.session.flush()
            self.session.add(
                ClaimRow(
                    project_id=source.project_id,
                    fragment_id=fragment.id,
                    subject=company or "当前企业",
                    predicate=question,
                    value={"answers": unique_answers, "questionnaire": questionnaire},
                )
            )
            fragments += 1
            claims += 1
        return fragments, claims, skipped

    def _import_generic_csv(
        self, source: SourceDocumentRow, rows: list[dict[str, Any]]
    ) -> tuple[int, int, int]:
        fragments = 0
        claims = 0
        skipped = 0
        fragment_base_time = now_utc()
        for index, row in enumerate(rows, start=1):
            locator = str(row.get("__locator__") or f"row:{index}")
            nonblank = {
                key: value
                for key, value in row.items()
                if key != "__locator__" and str(value).strip()
            }
            if not nonblank:
                skipped += 1
                continue
            text = "；".join(f"{key}：{value}" for key, value in nonblank.items())
            fragment = EvidenceFragmentRow(
                source_document_id=source.id,
                locator=locator,
                text=text,
                created_at=fragment_base_time + timedelta(microseconds=index),
                extra_metadata={"row": index, "source_locator": locator},
            )
            self.session.add(fragment)
            self.session.flush()
            self.session.add(
                ClaimRow(
                    project_id=source.project_id,
                    fragment_id=fragment.id,
                    subject=f"CSV第{index}行",
                    predicate="row_payload",
                    value=nonblank,
                )
            )
            fragments += 1
            claims += 1
        return fragments, claims, skipped

    def _import_generic_document(
        self, source: SourceDocumentRow, rows: list[dict[str, Any]]
    ) -> tuple[int, int, int]:
        fragments = 0
        claims = 0
        skipped = 0
        fragment_base_time = now_utc()
        for index, row in enumerate(rows, start=1):
            text = str(row.get("text") or "").strip()
            if not text:
                skipped += 1
                continue
            locator = str(row.get("locator") or f"fragment:{index}")
            fragment = EvidenceFragmentRow(
                source_document_id=source.id,
                locator=locator,
                text=text,
                created_at=fragment_base_time + timedelta(microseconds=index),
                extra_metadata={"source_locator": locator},
            )
            self.session.add(fragment)
            self.session.flush()
            self.session.add(
                ClaimRow(
                    project_id=source.project_id,
                    fragment_id=fragment.id,
                    subject=source.file_name,
                    predicate="document_fragment",
                    value={"text": text, "locator": locator},
                )
            )
            fragments += 1
            claims += 1
        return fragments, claims, skipped

    def _materialize_system_rows(
        self,
        project_id: UUID,
        source: SourceDocumentRow,
        rows: list[dict[str, Any]],
        *,
        source_asset: str,
        lineage: dict[str, Any] | None = None,
        materialization_run_id: str | None = None,
        raw_record_by_locator: dict[str, str] | None = None,
    ) -> tuple[int, int, int, int, int, int]:
        mappings = self.session.scalars(
            select(SemanticMappingRow).where(
                SemanticMappingRow.project_id == str(project_id),
                SemanticMappingRow.source_system_id == source.source_system_id,
                SemanticMappingRow.status == "APPROVED",
            )
        ).all()
        mappings = [item for item in mappings if item.source_asset in {"*", source_asset}]
        relation_mappings = self.session.scalars(
            select(SemanticRelationMappingRow).where(
                SemanticRelationMappingRow.project_id == str(project_id),
                SemanticRelationMappingRow.source_system_id == source.source_system_id,
                SemanticRelationMappingRow.status == "APPROVED",
            )
        ).all()
        relation_mappings = [
            item for item in relation_mappings if item.source_asset in {"*", source_asset}
        ]
        grouped: dict[str, list[SemanticMappingRow]] = defaultdict(list)
        for item in mappings:
            grouped[item.target_type_key].append(item)

        approved_mapping_ids = {item.id for item in mappings}
        asset_identity_ids = list(
            self.session.scalars(
                select(SourceIdentityRow.id).where(
                    SourceIdentityRow.project_id == str(project_id),
                    SourceIdentityRow.source_system_id == source.source_system_id,
                    SourceIdentityRow.source_asset == source_asset,
                )
            ).all()
        )
        if asset_identity_ids:
            active_assertions = self.session.scalars(
                select(ObservationAssertionRow).where(
                    ObservationAssertionRow.source_identity_id.in_(asset_identity_ids),
                    ObservationAssertionRow.status == "ACTIVE",
                )
            ).all()
            for active_assertion in active_assertions:
                if (
                    active_assertion.semantic_mapping_id is not None
                    and active_assertion.semantic_mapping_id not in approved_mapping_ids
                ):
                    active_assertion.status = "SUPERSEDED"

        fragments = {
            item.locator: item
            for item in self.session.scalars(
                select(EvidenceFragmentRow).where(
                    EvidenceFragmentRow.source_document_id == source.id
                )
            ).all()
        }
        projection = ProjectionService(self.session)
        created = 0
        identities_bound = 0
        observations_created = 0
        relations_created = 0
        applied = 0
        for row_number, raw_row in enumerate(rows, start=1):
            source_locator = str(raw_row.get("__locator__") or f"row:{row_number}")
            fragment = fragments.get(source_locator)
            identities_by_type: dict[str, SourceIdentityRow] = {}
            for type_key, type_mappings in grouped.items():
                by_target = {item.target_property_key: item for item in type_mappings}
                identity_mapping = by_target.get("__stable_key__")
                if identity_mapping is None:
                    continue
                identity = self._mapped_value(raw_row, identity_mapping)
                if identity is None or str(identity).strip() == "":
                    continue
                stable_key = str(identity).strip()
                if len(stable_key) > 200:
                    raise DomainError(
                        "MAPPING_STABLE_KEY_TOO_LONG",
                        "映射后的稳定标识超过 200 个字符。",
                        status_code=422,
                    )
                if raw_record_by_locator:
                    raw_record_id = raw_record_by_locator.get(source_locator)
                    raw_record = (
                        self.session.get(RawRecordRow, raw_record_id) if raw_record_id else None
                    )
                    if raw_record is not None and raw_record.source_record_key is None:
                        raw_record.source_record_key = stable_key
                name_mapping = by_target.get("__name__")
                name_value = (
                    self._mapped_value(raw_row, name_mapping) if name_mapping else stable_key
                )
                source_identity = self.session.scalar(
                    select(SourceIdentityRow).where(
                        SourceIdentityRow.project_id == str(project_id),
                        SourceIdentityRow.source_system_id == source.source_system_id,
                        SourceIdentityRow.source_asset == source_asset,
                        SourceIdentityRow.source_record_key == stable_key,
                        SourceIdentityRow.target_type_key == type_key,
                    )
                )
                if source_identity is None:
                    existing = self.session.scalar(
                        select(EntityRow).where(
                            EntityRow.project_id == str(project_id),
                            EntityRow.stable_key == stable_key,
                        )
                    )
                    if existing is not None and existing.type_key != type_key:
                        raise DomainError(
                            "MAPPING_IDENTITY_TYPE_CONFLICT",
                            "同一稳定标识已被映射为另一种本体类型。",
                            status_code=409,
                            details=[
                                {
                                    "stable_key": stable_key,
                                    "existing_type": existing.type_key,
                                    "incoming_type": type_key,
                                }
                            ],
                        )
                    if existing is not None and existing.status == LifecycleStatus.RETIRED.value:
                        existing = None
                    if existing is None:
                        entity_payload = EntityCreate(
                            type_key=type_key,
                            stable_key=self._system_stable_key(
                                str(source.source_system_id),
                                source_asset,
                                type_key,
                                stable_key,
                            ),
                            name=f"待对齐 · {str(name_value or stable_key)}"[:300],
                            properties={},
                            viewpoint=Viewpoint.SYSTEM_BOUND,
                        )
                        projection.validate_entity_payload(project_id, entity_payload)
                        existing = EntityRow(
                            project_id=str(project_id),
                            type_key=entity_payload.type_key,
                            stable_key=entity_payload.stable_key,
                            name=entity_payload.name,
                            properties={},
                            design_membership=DesignMembership.UNMODELED.value,
                            viewpoint=Viewpoint.SYSTEM_BOUND.value,
                            evidence=[],
                        )
                        self.session.add(existing)
                        self.session.flush()
                        created += 1
                        if lineage is not None:
                            lineage["entity_ids"].append(existing.id)
                    identity_status = (
                        "BOUND"
                        if existing.design_membership == DesignMembership.MODELED.value
                        else "UNRESOLVED"
                    )
                    source_identity = SourceIdentityRow(
                        project_id=str(project_id),
                        source_system_id=source.source_system_id,
                        source_asset=source_asset,
                        source_record_key=stable_key,
                        target_type_key=type_key,
                        entity_id=existing.id,
                        status=identity_status,
                    )
                    self.session.add(source_identity)
                    self.session.flush()
                    if lineage is not None:
                        lineage["identity_ids"].append(source_identity.id)
                    if identity_status == "BOUND":
                        identities_bound += 1
                else:
                    bound_entity = self.session.get(EntityRow, source_identity.entity_id)
                    if bound_entity is None or bound_entity.status == LifecycleStatus.RETIRED.value:
                        raise DomainError(
                            "SOURCE_IDENTITY_REBIND_REQUIRED",
                            "来源身份原先绑定的正式对象已退役，请先将它重新对齐到有效对象。",
                            status_code=409,
                            details=[{"source_identity_id": source_identity.id}],
                        )
                    if source_identity.status == "BOUND":
                        identities_bound += 1
                if lineage is not None:
                    lineage["entity_ids"].append(source_identity.entity_id)
                    lineage["identity_ids"].append(source_identity.id)
                identities_by_type[type_key] = source_identity

                assertion_values = {
                    target_key: self._mapped_value(raw_row, item)
                    for target_key, item in by_target.items()
                    if target_key != "__stable_key__" and item.source_field in raw_row
                }
                assertion_values = {
                    key: value for key, value in assertion_values.items() if value is not None
                }
                for field_key, value in assertion_values.items():
                    mapping_row = by_target[field_key]
                    previous = self.session.scalar(
                        select(ObservationAssertionRow)
                        .where(
                            ObservationAssertionRow.source_identity_id == source_identity.id,
                            ObservationAssertionRow.field_key == field_key,
                        )
                        .order_by(ObservationAssertionRow.version.desc())
                    )
                    assertion: ObservationAssertionRow | None = self.session.scalar(
                        select(ObservationAssertionRow).where(
                            ObservationAssertionRow.materialization_run_id
                            == materialization_run_id,
                            ObservationAssertionRow.raw_record_id
                            == (
                                raw_record_by_locator.get(source_locator)
                                if raw_record_by_locator
                                else None
                            ),
                            ObservationAssertionRow.entity_id == source_identity.entity_id,
                            ObservationAssertionRow.field_key == field_key,
                        )
                    )
                    if assertion is None:
                        if previous is not None:
                            previous.status = "SUPERSEDED"
                        assertion = ObservationAssertionRow(
                            project_id=str(project_id),
                            entity_id=source_identity.entity_id,
                            source_identity_id=source_identity.id,
                            source_document_id=source.id,
                            fragment_id=fragment.id if fragment is not None else None,
                            raw_record_id=(
                                raw_record_by_locator.get(source_locator)
                                if raw_record_by_locator
                                else None
                            ),
                            semantic_mapping_id=mapping_row.id,
                            materialization_run_id=materialization_run_id,
                            source_asset=source_asset,
                            source_record_key=stable_key,
                            field_key=field_key,
                            value=value,
                            authority_priority=mapping_row.authority_priority,
                            status="ACTIVE",
                            version=previous.version + 1 if previous is not None else 1,
                            supersedes_id=previous.id if previous is not None else None,
                            observed_at=source.created_at,
                        )
                        self.session.add(assertion)
                        self.session.flush()
                        observations_created += 1
                        if lineage is not None:
                            lineage["assertion_ids"].append(assertion.id)
                    if lineage is not None:
                        lineage["assertion_ids"].append(assertion.id)
                    applied += 1
                applied += 1  # The identity mapping was applied.
            for relation_mapping in relation_mappings:
                source_identity = identities_by_type.get(relation_mapping.source_type_key)
                if source_identity is None or relation_mapping.source_field not in raw_row:
                    continue
                target_value = apply_transform(
                    raw_row[relation_mapping.source_field],
                    relation_mapping.transform_expression,
                )
                if target_value is None or str(target_value).strip() == "":
                    continue
                target_key = str(target_value).strip()
                target_statement = select(SourceIdentityRow).where(
                    SourceIdentityRow.project_id == str(project_id),
                    SourceIdentityRow.source_system_id == source.source_system_id,
                    SourceIdentityRow.source_record_key == target_key,
                    SourceIdentityRow.target_type_key == relation_mapping.target_type_key,
                )
                if relation_mapping.target_asset:
                    target_statement = target_statement.where(
                        SourceIdentityRow.source_asset == relation_mapping.target_asset
                    )
                target_identities = self.session.scalars(target_statement).all()
                if len(target_identities) != 1:
                    error_code = (
                        "SOURCE_RELATION_TARGET_NOT_FOUND"
                        if not target_identities
                        else "SOURCE_RELATION_TARGET_AMBIGUOUS"
                    )
                    if lineage is not None:
                        lineage.setdefault("relation_errors", []).append(
                            {
                                "code": error_code,
                                "mapping_id": relation_mapping.id,
                                "source_record_key": source_identity.source_record_key,
                                "target_record_key": target_key,
                                "target_type_key": relation_mapping.target_type_key,
                            }
                        )
                    continue
                target_identity = target_identities[0]
                source_entity = self.session.get(EntityRow, source_identity.entity_id)
                target_entity = self.session.get(EntityRow, target_identity.entity_id)
                if source_entity is None or target_entity is None:
                    if lineage is not None:
                        lineage.setdefault("relation_errors", []).append(
                            {
                                "code": "SOURCE_RELATION_ENTITY_NOT_FOUND",
                                "mapping_id": relation_mapping.id,
                                "target_record_key": target_key,
                            }
                        )
                    continue
                relation_id = uuid5(
                    NAMESPACE_URL,
                    "source-relation:" + ":".join(
                        [
                            str(project_id),
                            str(source.source_system_id),
                            relation_mapping.id,
                            source_asset,
                            source_identity.source_record_key,
                        ]
                    ),
                )
                relation_payload = RelationCreate(
                    type_key=relation_mapping.relation_type_key,
                    participants=[
                        RelationParticipantInput(
                            role_key=relation_mapping.source_role_key,
                            entity_id=UUID(source_entity.id),
                        ),
                        RelationParticipantInput(
                            role_key=relation_mapping.target_role_key,
                            entity_id=UUID(target_entity.id),
                        ),
                    ],
                    properties={},
                    viewpoint=Viewpoint.SYSTEM_BOUND,
                    evidence=[
                        EvidenceReference(
                            source_document_id=UUID(source.id),
                            fragment_id=UUID(fragment.id) if fragment is not None else None,
                            note=json.dumps(
                                {
                                    "source_system_id": str(source.source_system_id),
                                    "source_asset": source_asset,
                                    "source_record_key": source_identity.source_record_key,
                                    "target_asset": target_identity.source_asset,
                                    "target_record_key": target_identity.source_record_key,
                                    "relation_mapping_id": relation_mapping.id,
                                    "raw_record_id": (
                                        raw_record_by_locator.get(source_locator)
                                        if raw_record_by_locator
                                        else None
                                    ),
                                },
                                ensure_ascii=False,
                                sort_keys=True,
                            ),
                        )
                    ],
                )
                try:
                    projection.validate_system_relation_payload(project_id, relation_payload)
                except DomainError as exc:
                    if lineage is not None:
                        lineage.setdefault("relation_errors", []).append(
                            {
                                "code": exc.code,
                                "message": exc.message,
                                "mapping_id": relation_mapping.id,
                                "source_record_key": source_identity.source_record_key,
                                "target_record_key": target_key,
                            }
                        )
                    continue
                relation_row = self.session.get(RelationRow, str(relation_id))
                if relation_row is None:
                    relation_row = RelationRow(
                        id=str(relation_id),
                        project_id=str(project_id),
                        type_key=relation_payload.type_key,
                        name=None,
                        properties=json_ready(relation_payload.properties),
                        viewpoint=Viewpoint.SYSTEM_BOUND.value,
                        evidence=json_ready(
                            [item.model_dump(mode="json") for item in relation_payload.evidence]
                        ),
                    )
                    relation_row.participants = [
                        RelationParticipantRow(
                            role_key=item.role_key,
                            entity_id=str(item.entity_id),
                            ordinal=item.ordinal,
                        )
                        for item in relation_payload.participants
                    ]
                    self.session.add(relation_row)
                    self.session.flush()
                    relations_created += 1
                    self.portfolio.bump_project_revision(project_id)
                else:
                    existing_participants = {
                        (item.role_key, item.entity_id, item.ordinal)
                        for item in relation_row.participants
                    }
                    expected_participants = {
                        (item.role_key, str(item.entity_id), item.ordinal)
                        for item in relation_payload.participants
                    }
                    if existing_participants != expected_participants:
                        relation_row.participants.clear()
                        self.session.flush()
                        relation_row.participants.extend(
                            RelationParticipantRow(
                                role_key=item.role_key,
                                entity_id=str(item.entity_id),
                                ordinal=item.ordinal,
                            )
                            for item in relation_payload.participants
                        )
                        relation_row.evidence = json_ready(
                            [item.model_dump(mode="json") for item in relation_payload.evidence]
                        )
                        relation_row.revision += 1
                        relation_row.status = LifecycleStatus.DRAFT.value
                        self.session.flush()
                        relations_created += 1
                        self.portfolio.bump_project_revision(project_id)
                if lineage is not None:
                    lineage.setdefault("relation_ids", []).append(relation_row.id)
        return created, 0, applied, identities_bound, observations_created, relations_created

    def _ensure_raw_layer(
        self,
        project_id: UUID,
        source: SourceDocumentRow,
        rows: list[dict[str, Any]],
        *,
        source_asset: str,
        columns: list[str],
    ) -> tuple[SourceAssetRow, RawBatchRow, bool]:
        schema_fields = sorted(
            {key for key in columns for key in [str(key)] if key != "__locator__"}
            or {str(key) for row in rows for key in row if str(key) != "__locator__"}
        )
        schema_fingerprint = hashlib.sha256(
            json.dumps(schema_fields, ensure_ascii=False, separators=(",", ":")).encode("utf-8")
        ).hexdigest()
        asset = self.session.scalar(
            select(SourceAssetRow).where(
                SourceAssetRow.project_id == str(project_id),
                SourceAssetRow.source_system_id == source.source_system_id,
                SourceAssetRow.asset_key == source_asset,
            )
        )
        drifted = False
        if asset is None:
            asset = SourceAssetRow(
                project_id=str(project_id),
                source_system_id=str(source.source_system_id),
                asset_key=source_asset,
                name=source_asset[:300],
                schema_fingerprint=schema_fingerprint,
                schema_fields=schema_fields,
            )
            self.session.add(asset)
            self.session.flush()
        elif asset.schema_fingerprint and asset.schema_fingerprint != schema_fingerprint:
            drifted = True
            asset.status = "SCHEMA_DRIFT"
            asset.schema_fingerprint = schema_fingerprint
            asset.schema_fields = schema_fields
            asset.revision += 1
        elif asset.schema_fingerprint is None:
            asset.schema_fingerprint = schema_fingerprint
            asset.schema_fields = schema_fields
            asset.status = "ACTIVE"
            asset.revision += 1
        drifted = drifted or asset.status == "SCHEMA_DRIFT"

        batch = self.session.scalar(
            select(RawBatchRow).where(
                RawBatchRow.source_asset_id == asset.id,
                RawBatchRow.content_sha256 == source.sha256,
            )
        )
        if batch is None:
            batch = RawBatchRow(
                project_id=str(project_id),
                source_asset_id=asset.id,
                source_document_id=source.id,
                content_sha256=source.sha256,
                parser_version="v1",
                schema_fingerprint=schema_fingerprint,
                status="SCHEMA_DRIFT" if drifted else "INGESTED",
                record_count=len(rows),
            )
            self.session.add(batch)
            self.session.flush()
            for row_number, raw in enumerate(rows, start=1):
                payload = {
                    str(key): json_ready(value)
                    for key, value in raw.items()
                    if str(key) != "__locator__"
                }
                locator = str(raw.get("__locator__") or f"row:{row_number}")
                canonical = json.dumps(
                    payload,
                    ensure_ascii=False,
                    sort_keys=True,
                    separators=(",", ":"),
                    default=str,
                )
                self.session.add(
                    RawRecordRow(
                        project_id=str(project_id),
                        raw_batch_id=batch.id,
                        row_number=row_number,
                        source_locator=locator,
                        payload=payload,
                        payload_sha256=hashlib.sha256(canonical.encode("utf-8")).hexdigest(),
                    )
                )
            self.session.flush()
        return asset, batch, drifted

    def _materialize_raw_batch(
        self, project_id: UUID, batch: RawBatchRow
    ) -> tuple[MaterializationRunView, bool]:
        asset = self.session.get(SourceAssetRow, batch.source_asset_id)
        source = self.session.get(SourceDocumentRow, batch.source_document_id)
        if asset is None or source is None:
            raise DomainError(
                "RAW_BATCH_LINEAGE_BROKEN",
                "原始批次的来源资产或材料不存在。",
                status_code=409,
            )
        if asset.status == "SCHEMA_DRIFT":
            raise DomainError(
                "SOURCE_SCHEMA_REVIEW_REQUIRED",
                "来源字段已经变化；请复核映射并将资产状态恢复为 ACTIVE 后再物化。",
                status_code=409,
            )
        if asset.status == "DISABLED":
            raise DomainError("SOURCE_ASSET_DISABLED", "来源资产已停用。", status_code=409)
        mappings = self.session.scalars(
            select(SemanticMappingRow).where(
                SemanticMappingRow.project_id == str(project_id),
                SemanticMappingRow.source_system_id == asset.source_system_id,
                SemanticMappingRow.status == "APPROVED",
            )
        ).all()
        mappings = [item for item in mappings if item.source_asset in {"*", asset.asset_key}]
        relation_mappings = self.session.scalars(
            select(SemanticRelationMappingRow).where(
                SemanticRelationMappingRow.project_id == str(project_id),
                SemanticRelationMappingRow.source_system_id == asset.source_system_id,
                SemanticRelationMappingRow.status == "APPROVED",
            )
        ).all()
        relation_mappings = [
            item for item in relation_mappings if item.source_asset in {"*", asset.asset_key}
        ]
        mapping_manifest = [
            {
                "id": item.id,
                "revision": item.revision,
                "source_asset": item.source_asset,
                "source_field": item.source_field,
                "target_type_key": item.target_type_key,
                "target_property_key": item.target_property_key,
                "transform_expression": item.transform_expression,
                "authority_priority": item.authority_priority,
            }
            for item in sorted(mappings, key=lambda item: item.id)
        ]
        mapping_manifest.extend(
            {
                "kind": "RELATION",
                "id": item.id,
                "revision": item.revision,
                "source_asset": item.source_asset,
                "source_type_key": item.source_type_key,
                "source_field": item.source_field,
                "relation_type_key": item.relation_type_key,
                "source_role_key": item.source_role_key,
                "target_type_key": item.target_type_key,
                "target_role_key": item.target_role_key,
                "target_asset": item.target_asset,
                "transform_expression": item.transform_expression,
            }
            for item in sorted(relation_mappings, key=lambda item: item.id)
        )
        mapping_fingerprint = hashlib.sha256(
            json.dumps(
                mapping_manifest,
                ensure_ascii=False,
                sort_keys=True,
                separators=(",", ":"),
            ).encode("utf-8")
        ).hexdigest()
        existing = self.session.scalar(
            select(MaterializationRunRow).where(
                MaterializationRunRow.raw_batch_id == batch.id,
                MaterializationRunRow.mapping_fingerprint == mapping_fingerprint,
            )
        )
        if existing is not None:
            return MaterializationRunView.model_validate(existing), False
        run = MaterializationRunRow(
            project_id=str(project_id),
            raw_batch_id=batch.id,
            mapping_fingerprint=mapping_fingerprint,
            mapping_ids=[item.id for item in mappings] + [item.id for item in relation_mappings],
        )
        self.session.add(run)
        self.session.flush()
        raw_records = self.session.scalars(
            select(RawRecordRow)
            .where(RawRecordRow.raw_batch_id == batch.id)
            .order_by(RawRecordRow.row_number)
        ).all()
        rows = [{**item.payload, "__locator__": item.source_locator} for item in raw_records]
        raw_record_by_locator = {item.source_locator: item.id for item in raw_records}
        lineage: dict[str, Any] = {
            "entity_ids": [],
            "identity_ids": [],
            "assertion_ids": [],
            "relation_ids": [],
            "relation_errors": [],
        }
        (
            run.entities_created,
            _,
            run.mappings_applied,
            run.identities_bound,
            run.observations_created,
            run.relations_created,
        ) = self._materialize_system_rows(
            project_id,
            source,
            rows,
            source_asset=asset.asset_key,
            lineage=lineage,
            materialization_run_id=run.id,
            raw_record_by_locator=raw_record_by_locator,
        )
        run.records_processed = len(rows)
        run.output_entity_ids = sorted(set(lineage["entity_ids"]))
        run.output_identity_ids = sorted(set(lineage["identity_ids"]))
        run.output_assertion_ids = sorted(set(lineage["assertion_ids"]))
        run.output_relation_ids = sorted(set(lineage["relation_ids"]))
        run.errors = lineage["relation_errors"]
        run.status = "COMPLETED"
        run.finished_at = now_utc()
        batch.status = "MATERIALIZED" if (mappings or relation_mappings) else "NO_APPROVED_MAPPING"
        self.session.flush()
        return MaterializationRunView.model_validate(run), True

    @staticmethod
    def _system_stable_key(
        source_system_id: str,
        source_asset: str,
        type_key: str,
        source_record_key: str,
    ) -> str:
        digest = hashlib.sha256(f"{source_asset}\0{source_record_key}".encode()).hexdigest()[:24]
        return f"system:{source_system_id}:{type_key}:{digest}"

    @staticmethod
    def _mapped_value(row: dict[str, Any], mapping: SemanticMappingRow) -> Any:
        if mapping.source_field not in row:
            return None
        return apply_transform(row[mapping.source_field], mapping.transform_expression)

    @staticmethod
    def _is_document_preview(preview: ImportPreviewRow) -> bool:
        return not preview.columns and all("text" in row for row in preview.all_rows)

    @staticmethod
    def _suggest_mapping(columns: list[str]) -> dict[str, str]:
        normalized = {EvidenceService._normalize(column): column for column in columns}
        result: dict[str, str] = {}
        for target, aliases in FIELD_ALIASES.items():
            for alias in aliases:
                if EvidenceService._normalize(alias) in normalized:
                    result[target] = normalized[EvidenceService._normalize(alias)]
                    break
        return result

    @staticmethod
    def _normalize(value: str) -> str:
        return re.sub(r"[^a-z0-9\u4e00-\u9fff]", "", value.lower())

    @staticmethod
    def _preview_view(row: ImportPreviewRow) -> ImportPreviewView:
        return ImportPreviewView.model_validate(row)

    @staticmethod
    def _document_view(row: SourceDocumentRow) -> SourceDocumentView:
        return SourceDocumentView.model_validate(
            {
                "id": row.id,
                "project_id": row.project_id,
                "source_system_id": row.source_system_id,
                "file_name": row.file_name,
                "kind": row.kind,
                "sha256": row.sha256,
                "status": row.status,
                "metadata": row.extra_metadata,
                "created_at": row.created_at,
            }
        )

    @staticmethod
    def _fragment_view(row: EvidenceFragmentRow) -> EvidenceFragmentView:
        return EvidenceFragmentView.model_validate(
            {
                "id": row.id,
                "source_document_id": row.source_document_id,
                "locator": row.locator,
                "text": row.text,
                "metadata": row.extra_metadata,
                "created_at": row.created_at,
            }
        )
