from __future__ import annotations

import hashlib
import json
from dataclasses import dataclass
from datetime import datetime
from typing import Any
from uuid import UUID

from sqlalchemy import select
from sqlalchemy.inspection import inspect as sa_inspect
from sqlalchemy.orm import Session
from sqlalchemy.sql.sqltypes import DateTime

from enterprise_insight_backend.errors import DomainError
from enterprise_insight_backend.models import (
    ActionDefinitionRow,
    ActionInvocationRow,
    ActionLogRow,
    ActionObservationRow,
    AgentMessageRow,
    AgentRunRow,
    AgentStepRow,
    AgentThreadRow,
    ChangeSetRow,
    ClaimRow,
    CompanyRow,
    DesignTradeoffRow,
    EntityRow,
    EvaluationCaseRow,
    EvaluationExecutionRow,
    EvaluationResultRow,
    EvaluationRunRow,
    EvaluationSuiteRow,
    EventRow,
    EvidenceFragmentRow,
    HypothesisFeedbackRow,
    HypothesisRow,
    InformationRequestRow,
    LearningCaseRow,
    ManagementAnalysisRunRow,
    ManagementInsightRow,
    ManagementIssueFeedbackRow,
    ManagementIssueRow,
    ManagementSignalRow,
    MaterializationRunRow,
    MeetingRecordRow,
    MetricDefinitionRow,
    MetricObservationRow,
    ObservationAssertionRow,
    ObservationConflictRow,
    OntologyReleaseRow,
    OntologyTypeRow,
    ProjectRow,
    PublicationRow,
    QuerySnapshotRow,
    RawBatchRow,
    RawRecordRow,
    RelationParticipantRow,
    RelationRow,
    ScenarioRow,
    SemanticDatasetRow,
    SemanticMappingRow,
    SemanticQueryRunRow,
    SemanticRelationMappingRow,
    SourceAssetRow,
    SourceDocumentRow,
    SourceIdentityRow,
    SourceSystemRow,
)
from enterprise_insight_backend.service_utils import json_ready, now_utc

ARCHIVE_SCHEMA = "enterprise-insight.project-archive.v1"


@dataclass(frozen=True, slots=True)
class ArchiveTable:
    name: str
    model: type[Any]


# This order is also the foreign-key-safe restore order.
ARCHIVE_TABLES: tuple[ArchiveTable, ...] = (
    ArchiveTable("companies", CompanyRow),
    ArchiveTable("projects", ProjectRow),
    ArchiveTable("ontology_types", OntologyTypeRow),
    ArchiveTable("ontology_releases", OntologyReleaseRow),
    ArchiveTable("source_systems", SourceSystemRow),
    ArchiveTable("source_assets", SourceAssetRow),
    ArchiveTable("source_documents", SourceDocumentRow),
    ArchiveTable("evidence_fragments", EvidenceFragmentRow),
    ArchiveTable("claims", ClaimRow),
    ArchiveTable("entities", EntityRow),
    ArchiveTable("relations", RelationRow),
    ArchiveTable("relation_participants", RelationParticipantRow),
    ArchiveTable("event_occurrences", EventRow),
    ArchiveTable("change_sets", ChangeSetRow),
    ArchiveTable("agent_threads", AgentThreadRow),
    ArchiveTable("agent_messages", AgentMessageRow),
    ArchiveTable("agent_runs", AgentRunRow),
    ArchiveTable("agent_steps", AgentStepRow),
    ArchiveTable("action_definitions", ActionDefinitionRow),
    ArchiveTable("metric_definitions", MetricDefinitionRow),
    ArchiveTable("metric_observations", MetricObservationRow),
    ArchiveTable("action_invocations", ActionInvocationRow),
    ArchiveTable("action_logs", ActionLogRow),
    ArchiveTable("action_observations", ActionObservationRow),
    ArchiveTable("hypotheses", HypothesisRow),
    ArchiveTable("hypothesis_feedback", HypothesisFeedbackRow),
    ArchiveTable("scenarios", ScenarioRow),
    ArchiveTable("learning_cases", LearningCaseRow),
    ArchiveTable("meeting_records", MeetingRecordRow),
    ArchiveTable("design_tradeoffs", DesignTradeoffRow),
    ArchiveTable("management_analysis_runs", ManagementAnalysisRunRow),
    ArchiveTable("management_signals", ManagementSignalRow),
    ArchiveTable("management_issues", ManagementIssueRow),
    ArchiveTable("management_insights", ManagementInsightRow),
    ArchiveTable("management_issue_feedback", ManagementIssueFeedbackRow),
    ArchiveTable("information_requests", InformationRequestRow),
    ArchiveTable("evaluation_suites", EvaluationSuiteRow),
    ArchiveTable("evaluation_cases", EvaluationCaseRow),
    ArchiveTable("evaluation_runs", EvaluationRunRow),
    ArchiveTable("evaluation_results", EvaluationResultRow),
    ArchiveTable("evaluation_executions", EvaluationExecutionRow),
    ArchiveTable("publications", PublicationRow),
    ArchiveTable("query_snapshots", QuerySnapshotRow),
    ArchiveTable("raw_batches", RawBatchRow),
    ArchiveTable("raw_records", RawRecordRow),
    ArchiveTable("materialization_runs", MaterializationRunRow),
    ArchiveTable("source_identities", SourceIdentityRow),
    ArchiveTable("semantic_mappings", SemanticMappingRow),
    ArchiveTable("semantic_relation_mappings", SemanticRelationMappingRow),
    ArchiveTable("observation_assertions", ObservationAssertionRow),
    ArchiveTable("observation_conflicts", ObservationConflictRow),
    ArchiveTable("semantic_datasets", SemanticDatasetRow),
    ArchiveTable("semantic_query_runs", SemanticQueryRunRow),
)

TABLE_BY_NAME = {item.name: item for item in ARCHIVE_TABLES}


def build_project_archive(session: Session, project_id: UUID) -> dict[str, Any]:
    project = session.get(ProjectRow, str(project_id))
    if project is None:
        raise DomainError("PROJECT_NOT_FOUND", "项目不存在。", status_code=404)
    company = session.get(CompanyRow, project.company_id)
    if company is None:
        raise DomainError("COMPANY_NOT_FOUND", "项目所属企业不存在。", status_code=409)

    project_key = str(project_id)
    relation_ids = _ids(session, RelationRow, project_key)
    thread_ids = _ids(session, AgentThreadRow, project_key)
    hypothesis_ids = _ids(session, HypothesisRow, project_key)
    document_ids = _ids(session, SourceDocumentRow, project_key)
    tables: dict[str, list[dict[str, Any]]] = {}
    for definition in ARCHIVE_TABLES:
        model = definition.model
        rows: list[Any]
        if model is CompanyRow:
            rows = [company]
        elif model is ProjectRow:
            rows = [project]
        elif model is RelationParticipantRow:
            rows = _rows_by_ids(session, model, "relation_id", relation_ids)
        elif model is AgentMessageRow:
            rows = _rows_by_ids(session, model, "thread_id", thread_ids)
        elif model is HypothesisFeedbackRow:
            rows = _rows_by_ids(session, model, "hypothesis_id", hypothesis_ids)
        elif model is EvidenceFragmentRow:
            rows = _rows_by_ids(session, model, "source_document_id", document_ids)
        else:
            rows = list(
                session.scalars(
                    select(model).where(model.project_id == project_key)
                ).all()
            )
        tables[definition.name] = [_archive_record(row) for row in rows]

    source_connections_reset = len(tables["source_systems"])
    for record in tables["source_systems"]:
        record["connection_profile"] = {}
        record["status"] = "NEEDS_CONFIGURATION"
        record["last_tested_at"] = None
    for record in tables["evaluation_runs"]:
        record["model_profile_id"] = None
    for record in tables["evaluation_executions"]:
        record["model_profile_id"] = None

    return {
        "schema": ARCHIVE_SCHEMA,
        "exported_at": now_utc().isoformat(),
        "project_id": project.id,
        "company_id": company.id,
        "source_connections_reset": source_connections_reset,
        "excluded": ["model_profiles", "import_previews", "export_jobs"],
        "tables": tables,
    }


def canonical_archive_bytes(payload: dict[str, Any]) -> bytes:
    return json.dumps(
        payload,
        ensure_ascii=False,
        sort_keys=True,
        separators=(",", ":"),
        default=str,
    ).encode("utf-8")


def archive_manifest(payload: dict[str, Any]) -> dict[str, Any]:
    raw = canonical_archive_bytes(payload)
    return {
        "schema": ARCHIVE_SCHEMA,
        "payload_sha256": hashlib.sha256(raw).hexdigest(),
        "table_counts": {
            key: len(value) for key, value in payload.get("tables", {}).items()
        },
    }


def validate_archive_payload(payload: Any, manifest: Any) -> dict[str, Any]:
    if not isinstance(payload, dict) or payload.get("schema") != ARCHIVE_SCHEMA:
        raise DomainError(
            "RESTORE_SCHEMA_UNSUPPORTED",
            "恢复包版本不受支持。",
            status_code=422,
        )
    if not isinstance(manifest, dict) or manifest.get("schema") != ARCHIVE_SCHEMA:
        raise DomainError("RESTORE_MANIFEST_INVALID", "恢复包清单无效。", status_code=422)
    expected_hash = manifest.get("payload_sha256")
    actual_hash = hashlib.sha256(canonical_archive_bytes(payload)).hexdigest()
    if expected_hash != actual_hash:
        raise DomainError(
            "RESTORE_HASH_MISMATCH",
            "恢复包内容与完整性清单不一致。",
            status_code=422,
        )
    tables = payload.get("tables")
    if not isinstance(tables, dict):
        raise DomainError("RESTORE_TABLES_INVALID", "恢复包缺少数据表。", status_code=422)
    unknown = sorted(set(tables) - set(TABLE_BY_NAME))
    missing = sorted(set(TABLE_BY_NAME) - set(tables))
    if missing == ["evaluation_executions"]:
        missing = []
    if unknown or missing:
        raise DomainError(
            "RESTORE_TABLE_SET_INVALID",
            "恢复包的数据表集合与当前版本不一致。",
            status_code=422,
            details=[{"unknown": unknown, "missing": missing}],
        )
    invalid_tables = sorted(
        table_name
        for table_name, records in tables.items()
        if not isinstance(records, list)
    )
    if invalid_tables:
        raise DomainError(
            "RESTORE_TABLES_INVALID",
            "恢复包的数据表必须是记录列表。",
            status_code=422,
            details=[{"tables": invalid_tables}],
        )
    if len(tables["companies"]) != 1 or len(tables["projects"]) != 1:
        raise DomainError(
            "RESTORE_PROJECT_BOUNDARY_INVALID",
            "恢复包必须且只能包含一个企业和一个项目。",
            status_code=422,
        )
    expected_counts = manifest.get("table_counts")
    actual_counts = {key: len(value) for key, value in tables.items()}
    # Evaluation executions were added without changing the archive schema:
    # older v1 packages are still valid and simply contain no in-flight jobs.
    if "evaluation_executions" not in tables:
        tables["evaluation_executions"] = []
        actual_counts["evaluation_executions"] = 0
        if isinstance(expected_counts, dict):
            expected_counts = dict(expected_counts)
            expected_counts["evaluation_executions"] = 0
    if expected_counts != actual_counts:
        raise DomainError(
            "RESTORE_COUNT_MISMATCH",
            "恢复包记录数与清单不一致。",
            status_code=422,
        )
    _validate_archive_integrity(payload)
    return payload


def archive_conflicts(session: Session, payload: dict[str, Any]) -> list[dict[str, Any]]:
    conflicts: list[dict[str, Any]] = []
    for table_name, records in payload["tables"].items():
        model = TABLE_BY_NAME[table_name].model
        mapper = sa_inspect(model)
        pk_keys = [mapper.get_property_by_column(column).key for column in mapper.primary_key]
        for record in records:
            if not isinstance(record, dict) or any(key not in record for key in pk_keys):
                raise DomainError(
                    "RESTORE_RECORD_INVALID",
                    f"恢复包中的 {table_name} 记录缺少主键。",
                    status_code=422,
                )
            statement = select(model)
            for key in pk_keys:
                statement = statement.where(getattr(model, key) == record[key])
            if session.scalar(statement.limit(1)) is not None:
                conflicts.append(
                    {
                        "table": table_name,
                        "primary_key": {key: record[key] for key in pk_keys},
                    }
                )
                if len(conflicts) >= 100:
                    return conflicts
    return conflicts


def restore_archive(session: Session, payload: dict[str, Any]) -> dict[str, Any]:
    # Keep this guard here as well as at package parsing time.  The restore
    # function is also used directly by maintenance code and must never begin
    # inserting rows for a package that is not a closed project boundary.
    _validate_archive_integrity(payload)
    conflicts = archive_conflicts(session, payload)
    if conflicts:
        raise DomainError(
            "RESTORE_ID_CONFLICT",
            "目标实例中已存在恢复包使用的标识，未写入任何记录。",
            status_code=409,
            details=conflicts,
        )
    counts: dict[str, int] = {}
    active_runs_cancelled = 0
    for definition in ARCHIVE_TABLES:
        records = payload["tables"][definition.name]
        objects: list[Any] = []
        for raw_record in _sort_self_references(records):
            record = dict(raw_record)
            if definition.model is AgentRunRow and record.get("status") in {
                "QUEUED",
                "RUNNING",
                "WAITING_APPROVAL",
            }:
                record["status"] = "CANCELLED"
                record["worker_id"] = None
                record["lease_expires_at"] = None
                record["heartbeat_at"] = None
                record["error"] = {
                    "code": "RESTORED_ACTIVE_RUN_CANCELLED",
                    "message": "恢复时终止了原实例中的活动任务，请重新发起。",
                }
                active_runs_cancelled += 1
            if definition.model is ActionInvocationRow and record.get("status") == "RUNNING":
                record["status"] = "CANCELLED"
                record["error"] = {
                    "code": "RESTORED_ACTIVE_ACTION_CANCELLED",
                    "message": "恢复时终止了状态不确定的动作。",
                }
                active_runs_cancelled += 1
            if definition.model is MaterializationRunRow and record.get("status") == "RUNNING":
                record["status"] = "FAILED"
                record["errors"] = [
                    {
                        "code": "RESTORED_ACTIVE_MATERIALIZATION_FAILED",
                        "message": "恢复时关闭了未完成的物化运行。",
                    }
                ]
                active_runs_cancelled += 1
            if definition.model is EvaluationExecutionRow and record.get("status") in {
                "QUEUED",
                "RUNNING",
                "FINALIZING",
            }:
                record["status"] = "FAILED"
                record["error"] = {
                    "code": "RESTORED_ACTIVE_EVALUATION_CANCELLED",
                    "message": "恢复时关闭了未完成的评测执行，请重新发起。",
                }
                active_runs_cancelled += 1
            objects.append(definition.model(**_decode_record(definition.model, record)))
        session.add_all(objects)
        session.flush()
        counts[definition.name] = len(objects)
    return {
        "company_id": payload["company_id"],
        "project_id": payload["project_id"],
        "restored_counts": counts,
        "source_connections_reset": int(payload.get("source_connections_reset", 0)),
        "active_runs_cancelled": active_runs_cancelled,
        "warnings": [
            "模型密钥、导出历史和临时导入预览不在恢复包中。",
            "所有外部系统连接参数已清空，需要在目标实例重新配置并测试。",
        ],
    }


def _archive_record(row: Any) -> dict[str, Any]:
    mapper = sa_inspect(type(row))
    return {
        attribute.key: json_ready(getattr(row, attribute.key))
        for attribute in mapper.column_attrs
    }


def _decode_record(model: type[Any], record: dict[str, Any]) -> dict[str, Any]:
    mapper = sa_inspect(model)
    attributes = {attribute.key: attribute for attribute in mapper.column_attrs}
    unknown = sorted(set(record) - set(attributes))
    if unknown:
        raise DomainError(
            "RESTORE_RECORD_FIELDS_INVALID",
            f"恢复包中的 {model.__tablename__} 含未知字段。",
            status_code=422,
            details=[{"unknown": unknown}],
        )
    result: dict[str, Any] = {}
    for key, value in record.items():
        column = attributes[key].columns[0]
        if value is not None and isinstance(column.type, DateTime) and isinstance(value, str):
            try:
                value = datetime.fromisoformat(value.replace("Z", "+00:00"))
            except ValueError as exc:
                raise DomainError(
                    "RESTORE_RECORD_INVALID",
                    f"恢复包中的 {model.__tablename__} 包含无效的时间字段。",
                    status_code=422,
                    details=[{"field": key, "value": value}],
                ) from exc
        result[key] = value
    return result


def _validate_archive_integrity(payload: dict[str, Any]) -> None:
    """Validate the package as one project before any row can be inserted."""

    tables = payload.get("tables")
    if not isinstance(tables, dict):
        raise DomainError("RESTORE_TABLES_INVALID", "恢复包缺少数据表。", status_code=422)

    root_project_id = payload.get("project_id")
    root_company_id = payload.get("company_id")
    companies = tables.get("companies")
    projects = tables.get("projects")
    if not isinstance(companies, list) or not isinstance(projects, list):
        raise DomainError(
            "RESTORE_PROJECT_BOUNDARY_INVALID",
            "恢复包必须包含企业和项目记录。",
            status_code=422,
        )

    boundary_details: list[dict[str, Any]] = []
    if not isinstance(root_project_id, str) or not isinstance(root_company_id, str):
        boundary_details.append(
            {
                "reason": "root_ids_required",
                "project_id": root_project_id,
                "company_id": root_company_id,
            }
        )

    if len(companies) == 1 and isinstance(companies[0], dict):
        if companies[0].get("id") != root_company_id:
            boundary_details.append(
                {
                    "table": "companies",
                    "field": "id",
                    "expected": root_company_id,
                    "actual": companies[0].get("id"),
                }
            )
    if len(projects) == 1 and isinstance(projects[0], dict):
        project_record = projects[0]
        if project_record.get("id") != root_project_id:
            boundary_details.append(
                {
                    "table": "projects",
                    "field": "id",
                    "expected": root_project_id,
                    "actual": project_record.get("id"),
                }
            )
        if project_record.get("company_id") != root_company_id:
            boundary_details.append(
                {
                    "table": "projects",
                    "field": "company_id",
                    "expected": root_company_id,
                    "actual": project_record.get("company_id"),
                }
            )
    if boundary_details:
        raise DomainError(
            "RESTORE_PROJECT_BOUNDARY_INVALID",
            "恢复包的根企业、项目及项目归属必须一致。",
            status_code=422,
            details=boundary_details[:100],
        )

    record_details: list[dict[str, Any]] = []
    for definition in ARCHIVE_TABLES:
        records = tables.get(definition.name)
        if not isinstance(records, list):
            # validate_archive_payload reports this with a table-level error;
            # this branch keeps direct restore_archive calls structured too.
            raise DomainError(
                "RESTORE_TABLES_INVALID",
                "恢复包的数据表必须是记录列表。",
                status_code=422,
                details=[{"table": definition.name}],
            )
        mapper = sa_inspect(definition.model)
        pk_keys = [
            mapper.get_property_by_column(column).key for column in mapper.primary_key
        ]
        seen: set[str] = set()
        for index, record in enumerate(records):
            if not isinstance(record, dict):
                record_details.append(
                    {"table": definition.name, "index": index, "reason": "record_not_object"}
                )
                continue
            missing_pk = [key for key in pk_keys if key not in record]
            if missing_pk:
                record_details.append(
                    {
                        "table": definition.name,
                        "index": index,
                        "reason": "primary_key_missing",
                        "fields": missing_pk,
                    }
                )
                continue
            identity = json.dumps(
                [record[key] for key in pk_keys],
                ensure_ascii=False,
                sort_keys=True,
                default=str,
            )
            if identity in seen:
                record_details.append(
                    {
                        "table": definition.name,
                        "index": index,
                        "reason": "duplicate_primary_key",
                        "primary_key": {key: record[key] for key in pk_keys},
                    }
                )
            seen.add(identity)
            try:
                _decode_record(definition.model, record)
            except DomainError as exc:
                record_details.extend(exc.details or [{"table": definition.name}])

            for field in ("project_id", "company_id"):
                if field in record:
                    expected = root_project_id if field == "project_id" else root_company_id
                    if record[field] != expected:
                        boundary_details.append(
                            {
                                "table": definition.name,
                                "index": index,
                                "field": field,
                                "expected": expected,
                                "actual": record[field],
                            }
                        )

    if record_details:
        raise DomainError(
            "RESTORE_RECORD_INVALID",
            "恢复包包含无效记录，未执行恢复。",
            status_code=422,
            details=record_details[:100],
        )
    if boundary_details:
        raise DomainError(
            "RESTORE_PROJECT_BOUNDARY_INVALID",
            "恢复包中的记录必须属于同一个项目和企业。",
            status_code=422,
            details=boundary_details[:100],
        )

    excluded_references: list[dict[str, Any]] = []
    missing_references: list[dict[str, Any]] = []
    for definition in ARCHIVE_TABLES:
        records = tables[definition.name]
        for index, record in enumerate(records):
            for foreign_key in definition.model.__table__.foreign_keys:
                field = foreign_key.parent.name
                target_table = foreign_key.column.table.name
                if field not in record:
                    if not foreign_key.parent.nullable:
                        missing_references.append(
                            {
                                "table": definition.name,
                                "index": index,
                                "field": field,
                                "reason": "foreign_key_missing",
                            }
                        )
                    continue
                value = record[field]
                if value is None:
                    continue
                if target_table not in TABLE_BY_NAME:
                    excluded_references.append(
                        {
                            "table": definition.name,
                            "index": index,
                            "field": field,
                            "target_table": target_table,
                            "target_id": value,
                        }
                    )
                    continue
                target_column = foreign_key.column.name
                target_ids = _archive_column_values(tables[target_table], target_column)
                if str(value) not in target_ids:
                    missing_references.append(
                        {
                            "table": definition.name,
                            "index": index,
                            "field": field,
                            "target_table": target_table,
                            "target_id": value,
                        }
                    )

    if excluded_references:
        raise DomainError(
            "RESTORE_EXCLUDED_REFERENCE_INVALID",
            "恢复包引用了未包含在归档中的对象。",
            status_code=422,
            details=excluded_references[:100],
        )
    if missing_references:
        raise DomainError(
            "RESTORE_FOREIGN_KEY_CLOSURE_INVALID",
            "恢复包包含未闭合的内部外键引用。",
            status_code=422,
            details=missing_references[:100],
        )


def _archive_column_values(records: list[dict[str, Any]], column: str) -> set[str]:
    return {
        str(record[column])
        for record in records
        if column in record and record[column] is not None
    }


def _ids(session: Session, model: type[Any], project_id: str) -> list[str]:
    return list(
        session.scalars(select(model.id).where(model.project_id == project_id)).all()
    )


def _rows_by_ids(
    session: Session, model: type[Any], key: str, values: list[str]
) -> list[Any]:
    if not values:
        return []
    return list(session.scalars(select(model).where(getattr(model, key).in_(values))).all())


def _sort_self_references(records: list[dict[str, Any]]) -> list[dict[str, Any]]:
    if records and "supersedes_id" in records[0]:
        return sorted(records, key=lambda item: (int(item.get("version", 1)), item.get("id", "")))
    return records
