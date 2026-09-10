from __future__ import annotations

import builtins
import csv
import io
import json
import os
import tempfile
from collections.abc import Callable
from pathlib import Path
from typing import Any
from uuid import UUID
from zipfile import ZIP_DEFLATED, ZipFile

from openpyxl import Workbook  # type: ignore[import-untyped]
from sqlalchemy import select
from sqlalchemy.orm import Session

from enterprise_insight_backend.config import Settings
from enterprise_insight_backend.errors import DomainError
from enterprise_insight_backend.models import (
    ActionDefinitionRow,
    ActionInvocationRow,
    ClaimRow,
    CompanyRow,
    DesignTradeoffRow,
    EvaluationCaseRow,
    EvaluationExecutionRow,
    EvaluationResultRow,
    EvaluationRunRow,
    EvaluationSuiteRow,
    EventRow,
    EvidenceFragmentRow,
    ExportJobRow,
    HypothesisRow,
    InformationRequestRow,
    ManagementAnalysisRunRow,
    ManagementInsightRow,
    ManagementIssueFeedbackRow,
    ManagementIssueRow,
    ManagementSignalRow,
    MeetingRecordRow,
    MetricDefinitionRow,
    MetricObservationRow,
    ObservationAssertionRow,
    OntologyTypeRow,
    ProjectRow,
    ScenarioRow,
    SemanticMappingRow,
    SourceDocumentRow,
    SourceIdentityRow,
    SourceSystemRow,
)
from enterprise_insight_backend.project_archives import (
    archive_manifest,
    build_project_archive,
    canonical_archive_bytes,
)
from enterprise_insight_backend.projection import ProjectionService
from enterprise_insight_backend.query_snapshots import QuerySnapshotService
from enterprise_insight_backend.schemas import ExportJobView, ExportRequest, GraphQuery
from enterprise_insight_backend.service_utils import json_ready, now_utc


def atomic_write(path: Path, writer: Callable[[Path], object]) -> None:
    """Write a generated file privately, then publish it atomically.

    The temporary file lives beside the destination so ``os.replace`` remains
    atomic on the target filesystem.  A failed writer or replacement never
    exposes partial output and leaves no temporary file behind.
    """

    temporary_path: Path | None = None
    try:
        with tempfile.NamedTemporaryFile(
            dir=path.parent,
            prefix=f".{path.name}.",
            suffix=".tmp",
            delete=False,
        ) as temporary:
            temporary_path = Path(temporary.name)
        writer(temporary_path)
        os.replace(temporary_path, path)
        temporary_path = None
    finally:
        if temporary_path is not None:
            try:
                temporary_path.unlink(missing_ok=True)
            except OSError:
                pass


class ExportService:
    def __init__(self, session: Session, settings: Settings) -> None:
        self.session = session
        self.settings = settings

    def list(self, project_id: UUID) -> list[ExportJobView]:
        self._require_project(project_id)
        rows = self.session.scalars(
            select(ExportJobRow)
            .where(ExportJobRow.project_id == str(project_id))
            .order_by(ExportJobRow.created_at.desc())
        ).all()
        return [ExportJobView.model_validate(row) for row in rows]

    def create(self, project_id: UUID, payload: ExportRequest) -> ExportJobView:
        self._require_project(project_id)
        row = ExportJobRow(
            project_id=str(project_id),
            format=payload.format,
            request_payload=json_ready(payload.model_dump(mode="json")),
            status="RUNNING",
        )
        self.session.add(row)
        self.session.flush()
        try:
            bundle = self._bundle(project_id, payload)
            restore_payload = (
                build_project_archive(self.session, project_id)
                if payload.format == "bundle"
                else None
            )
            path = self._path(row)
            path.parent.mkdir(parents=True, exist_ok=True)
            self._write(path, payload.format, bundle, restore_payload=restore_payload)
        except DomainError:
            raise
        except Exception as exc:
            row.status = "FAILED"
            detail = str(exc).strip() or "导出失败"
            row.error = f"{type(exc).__name__}: {detail[:500]}"
        else:
            row.status = "COMPLETED"
            row.download_url = (
                f"{self.settings.api_prefix}/projects/{project_id}/exports/{row.id}/download"
            )
        self.session.flush()
        return ExportJobView.model_validate(row)

    def get(self, project_id: UUID, export_id: UUID) -> ExportJobView:
        return ExportJobView.model_validate(self.require(project_id, export_id))

    def file(self, project_id: UUID, export_id: UUID) -> Path:
        row = self.require(project_id, export_id)
        if row.status != "COMPLETED":
            raise DomainError("EXPORT_NOT_READY", "导出文件尚未生成。", status_code=409)
        path = self._path(row)
        if not path.exists():
            raise DomainError(
                "EXPORT_FILE_MISSING", "导出文件不存在，请重新生成。", status_code=404
            )
        return path

    def require(self, project_id: UUID, export_id: UUID) -> ExportJobRow:
        row = self.session.get(ExportJobRow, str(export_id))
        if row is None or row.project_id != str(project_id):
            raise DomainError("EXPORT_NOT_FOUND", "导出任务不存在。", status_code=404)
        return row

    def _bundle(self, project_id: UUID, payload: ExportRequest) -> dict[str, Any]:
        project = self._require_project(project_id)
        company = self.session.get(CompanyRow, project.company_id)
        filters = payload.filters or {}
        if payload.query_snapshot_id is not None and payload.release_id is not None:
            raise DomainError(
                "QUERY_SCOPE_INVALID",
                "导出不能同时指定查询快照和发布版本。",
                status_code=422,
            )
        graph_query = GraphQuery(
            type_keys=filters.get("type_keys", []),
            relation_type_keys=filters.get("relation_type_keys", []),
            viewpoints=filters.get("viewpoints", []),
            include_retired=bool(filters.get("include_retired", False)),
            release_id=payload.release_id,
        )
        snapshot_row = None
        if payload.query_snapshot_id is not None:
            snapshot_service = QuerySnapshotService(self.session)
            snapshot_row = snapshot_service.require(project_id, payload.query_snapshot_id)
            graph = snapshot_service.graph(project_id, payload.query_snapshot_id, graph_query)
        else:
            graph = ProjectionService(self.session).graph(project_id, graph_query)
        entities = [item.model_dump(mode="json") for item in graph.entities]
        relations = [item.model_dump(mode="json") for item in graph.relations]
        if not payload.include_evidence:
            for item in entities + relations:
                item.pop("evidence", None)
        ontology = self.session.scalars(
            select(OntologyTypeRow).where(OntologyTypeRow.project_id == str(project_id))
        ).all()
        events = self.session.scalars(
            select(EventRow).where(EventRow.project_id == str(project_id))
        ).all()
        hypotheses = self.session.scalars(
            select(HypothesisRow).where(HypothesisRow.project_id == str(project_id))
        ).all()
        scenarios = self.session.scalars(
            select(ScenarioRow).where(ScenarioRow.project_id == str(project_id))
        ).all()
        definitions = self.session.scalars(
            select(ActionDefinitionRow).where(ActionDefinitionRow.project_id == str(project_id))
        ).all()
        invocations = self.session.scalars(
            select(ActionInvocationRow).where(ActionInvocationRow.project_id == str(project_id))
        ).all()
        metric_definitions = self.session.scalars(
            select(MetricDefinitionRow).where(MetricDefinitionRow.project_id == str(project_id))
        ).all()
        metric_statement = select(MetricObservationRow).where(
            MetricObservationRow.project_id == str(project_id)
        )
        if snapshot_row is not None:
            metric_statement = metric_statement.where(
                MetricObservationRow.id.in_(
                    snapshot_row.manifest.get("metric_observation_ids", [])
                )
            )
        else:
            metric_statement = metric_statement.where(
                MetricObservationRow.record_status == "ACTIVE"
            )
        metric_observations = self.session.scalars(metric_statement).all()
        source_identities = self.session.scalars(
            select(SourceIdentityRow).where(SourceIdentityRow.project_id == str(project_id))
        ).all()
        assertion_statement = select(ObservationAssertionRow).where(
            ObservationAssertionRow.project_id == str(project_id)
        )
        if snapshot_row is not None:
            assertion_statement = assertion_statement.where(
                ObservationAssertionRow.id.in_(
                    snapshot_row.manifest.get("observation_assertion_ids", [])
                )
            )
        observation_assertions = self.session.scalars(assertion_statement).all()
        meetings = self.session.scalars(
            select(MeetingRecordRow).where(MeetingRecordRow.project_id == str(project_id))
        ).all()
        tradeoffs = self.session.scalars(
            select(DesignTradeoffRow).where(DesignTradeoffRow.project_id == str(project_id))
        ).all()
        analysis_runs = self.session.scalars(
            select(ManagementAnalysisRunRow).where(
                ManagementAnalysisRunRow.project_id == str(project_id)
            )
        ).all()
        management_signals = self.session.scalars(
            select(ManagementSignalRow).where(ManagementSignalRow.project_id == str(project_id))
        ).all()
        management_insights = self.session.scalars(
            select(ManagementInsightRow).where(ManagementInsightRow.project_id == str(project_id))
        ).all()
        management_issues = self.session.scalars(
            select(ManagementIssueRow).where(ManagementIssueRow.project_id == str(project_id))
        ).all()
        management_issue_feedback = self.session.scalars(
            select(ManagementIssueFeedbackRow).where(
                ManagementIssueFeedbackRow.project_id == str(project_id)
            )
        ).all()
        information_requests = self.session.scalars(
            select(InformationRequestRow).where(
                InformationRequestRow.project_id == str(project_id)
            )
        ).all()
        evaluation_suites = self.session.scalars(
            select(EvaluationSuiteRow).where(EvaluationSuiteRow.project_id == str(project_id))
        ).all()
        evaluation_cases = self.session.scalars(
            select(EvaluationCaseRow).where(EvaluationCaseRow.project_id == str(project_id))
        ).all()
        evaluation_runs = self.session.scalars(
            select(EvaluationRunRow).where(EvaluationRunRow.project_id == str(project_id))
        ).all()
        evaluation_results = self.session.scalars(
            select(EvaluationResultRow).where(EvaluationResultRow.project_id == str(project_id))
        ).all()
        evaluation_executions = self.session.scalars(
            select(EvaluationExecutionRow).where(
                EvaluationExecutionRow.project_id == str(project_id)
            )
        ).all()
        result: dict[str, Any] = {
            "manifest": {
                "generated_at": now_utc().isoformat(),
                "project_revision": (
                    snapshot_row.project_revision if snapshot_row is not None else project.revision
                ),
                "query_snapshot_id": (
                    snapshot_row.id if snapshot_row is not None else None
                ),
                "data_cutoff": (
                    snapshot_row.data_cutoff.isoformat() if snapshot_row is not None else None
                ),
                "publication_id": (
                    snapshot_row.publication_id
                    if snapshot_row is not None
                    else str(payload.release_id)
                    if payload.release_id
                    else None
                ),
                "include_evidence": payload.include_evidence,
                "include_lineage": payload.include_lineage,
            },
            "company": {
                "id": company.id if company else None,
                "name": company.name if company else None,
                "industry": company.industry if company else None,
            },
            "project": {
                "id": project.id,
                "name": project.name,
                "description": project.description,
                "status": project.status,
                "revision": project.revision,
            },
            "ontology": [self._ontology(item) for item in ontology],
            "graph": {"revision": graph.revision, "entities": entities, "relations": relations},
            "events": [self._event(item) for item in events],
            "hypotheses": [self._hypothesis(item) for item in hypotheses],
            "scenarios": [self._scenario(item) for item in scenarios],
            "action_definitions": [self._definition(item) for item in definitions],
            "action_invocations": [self._invocation(item) for item in invocations],
            "metric_definitions": [self._metric_definition(item) for item in metric_definitions],
            "metric_observations": [
                self._metric_observation(item, payload.include_evidence)
                for item in metric_observations
            ],
            "source_identities": [
                {
                    "id": item.id,
                    "source_system_id": item.source_system_id,
                    "source_asset": item.source_asset,
                    "source_record_key": item.source_record_key,
                    "target_type_key": item.target_type_key,
                    "entity_id": item.entity_id,
                    "status": item.status,
                    "revision": item.revision,
                }
                for item in source_identities
            ],
            "observation_assertions": [
                {
                    "id": item.id,
                    "entity_id": item.entity_id,
                    "source_identity_id": item.source_identity_id,
                    "source_document_id": item.source_document_id,
                    "fragment_id": item.fragment_id,
                    "raw_record_id": item.raw_record_id,
                    "semantic_mapping_id": item.semantic_mapping_id,
                    "materialization_run_id": item.materialization_run_id,
                    "source_asset": item.source_asset,
                    "source_record_key": item.source_record_key,
                    "field_key": item.field_key,
                    "value": item.value,
                    "authority_priority": item.authority_priority,
                    "status": item.status,
                    "version": item.version,
                    "supersedes_id": item.supersedes_id,
                    "observed_at": item.observed_at.isoformat(),
                }
                for item in observation_assertions
            ],
            "meeting_records": [
                self._meeting(item, payload.include_evidence) for item in meetings
            ],
            "design_tradeoffs": [self._tradeoff(item) for item in tradeoffs],
            "management_analysis_runs": [self._analysis_run(item) for item in analysis_runs],
            "management_signals": [
                self._management_signal(item, payload.include_evidence)
                for item in management_signals
            ],
            "management_insights": [self._management_insight(item) for item in management_insights],
            "management_issues": [
                self._management_issue(item) for item in management_issues
            ],
            "management_issue_feedback": [
                self._management_issue_feedback(item)
                for item in management_issue_feedback
            ],
            "information_requests": [
                self._information_request(item) for item in information_requests
            ],
            "evaluations": {
                "suites": [self._evaluation_suite(item) for item in evaluation_suites],
                "cases": [self._evaluation_case(item) for item in evaluation_cases],
                "runs": [self._evaluation_run(item) for item in evaluation_runs],
                "results": [self._evaluation_result(item) for item in evaluation_results],
                "executions": [
                    self._evaluation_execution(item) for item in evaluation_executions
                ],
            },
        }
        if payload.include_evidence:
            result["evidence"] = self._evidence(project_id)
        if payload.include_lineage:
            result["lineage"] = self._lineage(project_id)
        return result

    def _evidence(self, project_id: UUID) -> dict[str, Any]:
        documents = self.session.scalars(
            select(SourceDocumentRow).where(SourceDocumentRow.project_id == str(project_id))
        ).all()
        fragments = self.session.scalars(
            select(EvidenceFragmentRow).where(
                EvidenceFragmentRow.source_document_id.in_([item.id for item in documents])
            )
        ).all()
        claims = self.session.scalars(
            select(ClaimRow).where(ClaimRow.project_id == str(project_id))
        ).all()
        return {
            "documents": [
                {
                    "id": item.id,
                    "file_name": item.file_name,
                    "kind": item.kind,
                    "sha256": item.sha256,
                    "status": item.status,
                    "metadata": item.extra_metadata,
                }
                for item in documents
            ],
            "fragments": [
                {
                    "id": item.id,
                    "source_document_id": item.source_document_id,
                    "locator": item.locator,
                    "text": item.text,
                    "metadata": item.extra_metadata,
                }
                for item in fragments
            ],
            "claims": [
                {
                    "id": item.id,
                    "fragment_id": item.fragment_id,
                    "subject": item.subject,
                    "predicate": item.predicate,
                    "value": item.value,
                    "status": item.status,
                }
                for item in claims
            ],
        }

    def _lineage(self, project_id: UUID) -> dict[str, Any]:
        sources = self.session.scalars(
            select(SourceSystemRow).where(SourceSystemRow.project_id == str(project_id))
        ).all()
        mappings = self.session.scalars(
            select(SemanticMappingRow).where(SemanticMappingRow.project_id == str(project_id))
        ).all()
        return {
            "source_systems": [
                {"id": item.id, "name": item.name, "kind": item.kind, "status": item.status}
                for item in sources
            ],
            "semantic_mappings": [
                {
                    "id": item.id,
                    "source_system_id": item.source_system_id,
                    "source_asset": item.source_asset,
                    "source_field": item.source_field,
                    "target_type_key": item.target_type_key,
                    "target_property_key": item.target_property_key,
                    "transform_expression": item.transform_expression,
                    "authority_priority": item.authority_priority,
                    "status": item.status,
                }
                for item in mappings
            ],
        }

    def _write(
        self,
        path: Path,
        export_format: str,
        bundle: dict[str, Any],
        *,
        restore_payload: dict[str, Any] | None = None,
    ) -> None:
        if export_format == "json":
            atomic_write(
                path,
                lambda temporary_path: temporary_path.write_text(
                    json.dumps(bundle, ensure_ascii=False, indent=2, default=str) + "\n",
                    encoding="utf-8",
                ),
            )
            return
        if export_format == "csv":
            atomic_write(
                path,
                lambda temporary_path: temporary_path.write_text(
                    self._csv(bundle), encoding="utf-8-sig"
                ),
            )
            return
        if export_format == "xlsx":
            atomic_write(path, lambda temporary_path: self._workbook(bundle).save(temporary_path))
            return
        if export_format == "bundle":
            if restore_payload is None:
                raise DomainError(
                    "RESTORE_PAYLOAD_MISSING",
                    "项目包缺少可恢复数据。",
                    status_code=500,
                )
            def write_bundle(temporary_path: Path) -> None:
                with ZipFile(temporary_path, "w", compression=ZIP_DEFLATED) as archive:
                    archive.writestr(
                        "enterprise.json",
                        json.dumps(bundle, ensure_ascii=False, indent=2, default=str),
                    )
                    archive.writestr("records.csv", "\ufeff" + self._csv(bundle))
                    workbook_bytes = io.BytesIO()
                    self._workbook(bundle).save(workbook_bytes)
                    archive.writestr("enterprise.xlsx", workbook_bytes.getvalue())
                    archive.writestr(
                        "restore.json", canonical_archive_bytes(restore_payload)
                    )
                    archive.writestr(
                        "restore.manifest.json",
                        json.dumps(
                            archive_manifest(restore_payload),
                            ensure_ascii=False,
                            indent=2,
                        ),
                    )

            atomic_write(path, write_bundle)
            return
        raise DomainError("EXPORT_FORMAT_UNSUPPORTED", "不支持的导出格式。", status_code=422)

    @staticmethod
    def _csv(bundle: dict[str, Any]) -> str:
        output = io.StringIO(newline="")
        fields = ["record_kind", "id", "type_key", "name", "status", "payload"]
        writer = csv.DictWriter(output, fieldnames=fields)
        writer.writeheader()
        graph = bundle.get("graph", {})
        groups = (
            ("entity", graph.get("entities", [])),
            ("relation", graph.get("relations", [])),
            ("event", bundle.get("events", [])),
            ("hypothesis", bundle.get("hypotheses", [])),
            ("scenario", bundle.get("scenarios", [])),
            ("metric_definition", bundle.get("metric_definitions", [])),
            ("metric_observation", bundle.get("metric_observations", [])),
            ("source_identity", bundle.get("source_identities", [])),
            ("observation_assertion", bundle.get("observation_assertions", [])),
            ("meeting", bundle.get("meeting_records", [])),
            ("design_tradeoff", bundle.get("design_tradeoffs", [])),
            ("management_signal", bundle.get("management_signals", [])),
            ("management_insight", bundle.get("management_insights", [])),
            ("management_issue", bundle.get("management_issues", [])),
            ("management_issue_feedback", bundle.get("management_issue_feedback", [])),
            ("information_request", bundle.get("information_requests", [])),
            ("evaluation_suite", bundle.get("evaluations", {}).get("suites", [])),
            ("evaluation_case", bundle.get("evaluations", {}).get("cases", [])),
            ("evaluation_run", bundle.get("evaluations", {}).get("runs", [])),
            ("evaluation_result", bundle.get("evaluations", {}).get("results", [])),
            ("evaluation_execution", bundle.get("evaluations", {}).get("executions", [])),
        )
        for kind, items in groups:
            for item in items:
                writer.writerow(
                    {
                        "record_kind": kind,
                        "id": item.get("id", ""),
                        "type_key": item.get("type_key", ""),
                        "name": item.get("name") or item.get("title", ""),
                        "status": item.get("status", ""),
                        "payload": json.dumps(item, ensure_ascii=False, default=str),
                    }
                )
        return output.getvalue()

    @staticmethod
    def _workbook(bundle: dict[str, Any]) -> Workbook:
        workbook = Workbook()
        project_sheet = workbook.active
        project_sheet.title = "项目"
        project_sheet.append(["字段", "值"])
        for key, value in bundle.get("project", {}).items():
            project_sheet.append([key, json.dumps(value, ensure_ascii=False, default=str)])
        graph = bundle.get("graph", {})
        ExportService._sheet(workbook, "对象", graph.get("entities", []))
        ExportService._sheet(workbook, "关系", graph.get("relations", []))
        ExportService._sheet(workbook, "事件", bundle.get("events", []))
        ExportService._sheet(workbook, "管理假设", bundle.get("hypotheses", []))
        ExportService._sheet(workbook, "管理方案", bundle.get("scenarios", []))
        ExportService._sheet(workbook, "指标定义", bundle.get("metric_definitions", []))
        ExportService._sheet(workbook, "指标结果", bundle.get("metric_observations", []))
        ExportService._sheet(workbook, "来源身份", bundle.get("source_identities", []))
        ExportService._sheet(workbook, "现实观测", bundle.get("observation_assertions", []))
        ExportService._sheet(workbook, "会议结果", bundle.get("meeting_records", []))
        ExportService._sheet(workbook, "设计取舍", bundle.get("design_tradeoffs", []))
        ExportService._sheet(workbook, "管理信号", bundle.get("management_signals", []))
        ExportService._sheet(workbook, "交叉判断", bundle.get("management_insights", []))
        ExportService._sheet(workbook, "稳定问题", bundle.get("management_issues", []))
        ExportService._sheet(
            workbook, "问题反馈历史", bundle.get("management_issue_feedback", [])
        )
        ExportService._sheet(workbook, "补充信息", bundle.get("information_requests", []))
        ExportService._sheet(
            workbook, "评测集", bundle.get("evaluations", {}).get("suites", [])
        )
        ExportService._sheet(
            workbook, "评测案例", bundle.get("evaluations", {}).get("cases", [])
        )
        ExportService._sheet(
            workbook, "评测运行", bundle.get("evaluations", {}).get("runs", [])
        )
        ExportService._sheet(
            workbook, "评测结果", bundle.get("evaluations", {}).get("results", [])
        )
        ExportService._sheet(
            workbook, "评测执行", bundle.get("evaluations", {}).get("executions", [])
        )
        ExportService._sheet(workbook, "证据声明", bundle.get("evidence", {}).get("claims", []))
        ExportService._sheet(
            workbook, "语义映射", bundle.get("lineage", {}).get("semantic_mappings", [])
        )
        return workbook

    @staticmethod
    def _sheet(
        workbook: Workbook, title: str, rows: builtins.list[dict[str, Any]]
    ) -> None:
        sheet = workbook.create_sheet(title=title[:31])
        if not rows:
            sheet.append(["暂无数据"])
            return
        columns = sorted({key for row in rows for key in row})
        sheet.append(columns)
        for row in rows:
            sheet.append(
                [
                    json.dumps(row.get(column), ensure_ascii=False, default=str)
                    if isinstance(row.get(column), (dict, list))
                    else row.get(column)
                    for column in columns
                ]
            )

    def _path(self, row: ExportJobRow) -> Path:
        extension = "zip" if row.format == "bundle" else row.format
        return self.settings.data_dir / "exports" / f"{row.project_id}-{row.id}.{extension}"

    def _require_project(self, project_id: UUID) -> ProjectRow:
        row = self.session.get(ProjectRow, str(project_id))
        if row is None:
            raise DomainError("PROJECT_NOT_FOUND", "项目不存在。", status_code=404)
        return row

    @staticmethod
    def _ontology(row: OntologyTypeRow) -> dict[str, Any]:
        return {
            "id": row.id,
            "key": row.key,
            "name": row.name,
            "kind": row.kind,
            "description": row.description,
            "properties": row.properties,
            "relation_roles": row.relation_roles,
            "status": row.status,
            "revision": row.revision,
        }

    @staticmethod
    def _event(row: EventRow) -> dict[str, Any]:
        return {
            "id": row.id,
            "type_key": row.type_key,
            "name": row.name,
            "occurred_at": row.occurred_at.isoformat(),
            "participants": row.participants,
            "properties": row.properties,
            "status": row.status,
        }

    @staticmethod
    def _hypothesis(row: HypothesisRow) -> dict[str, Any]:
        return {
            "id": row.id,
            "type_key": row.type_key,
            "title": row.title,
            "summary": row.summary,
            "status": row.status,
            "uncertainties": row.uncertainties,
            "validation_questions": row.validation_questions,
        }

    @staticmethod
    def _scenario(row: ScenarioRow) -> dict[str, Any]:
        return {
            "id": row.id,
            "name": row.name,
            "description": row.description,
            "goal": row.goal,
            "assumptions": row.assumptions,
            "expected_benefits": row.expected_benefits,
            "risks": row.risks,
            "validation_metrics": row.validation_metrics,
            "status": row.status,
        }

    @staticmethod
    def _definition(row: ActionDefinitionRow) -> dict[str, Any]:
        return {
            "id": row.id,
            "key": row.key,
            "name": row.name,
            "status": row.status,
            "risk_level": row.risk_level,
            "require_approval": row.require_approval,
        }

    @staticmethod
    def _invocation(row: ActionInvocationRow) -> dict[str, Any]:
        return {
            "id": row.id,
            "action_definition_id": row.action_definition_id,
            "status": row.status,
            "requested_by": row.requested_by,
            "input": row.input,
            "result": row.result,
            "error": row.error,
            "created_at": row.created_at.isoformat(),
        }

    @staticmethod
    def _metric_definition(row: MetricDefinitionRow) -> dict[str, Any]:
        return {
            "id": row.id,
            "entity_id": row.entity_id,
            "key": row.key,
            "name": row.name,
            "description": row.description,
            "scope": row.scope,
            "direction": row.direction,
            "owner_entity_id": row.owner_entity_id,
            "strategy_entity_id": row.strategy_entity_id,
            "outcome_entity_id": row.outcome_entity_id,
            "unit": row.unit,
            "target_value": row.target_value,
            "properties": row.properties,
            "active": row.active,
        }

    @staticmethod
    def _metric_observation(
        row: MetricObservationRow, include_evidence: bool
    ) -> dict[str, Any]:
        result = {
            "id": row.id,
            "metric_definition_id": row.metric_definition_id,
            "period_key": row.period_key,
            "period_start": row.period_start.isoformat() if row.period_start else None,
            "period_end": row.period_end.isoformat() if row.period_end else None,
            "dimensions": row.dimensions,
            "observed_at": row.observed_at.isoformat(),
            "value": row.value,
            "numeric_value": row.numeric_value,
            "status": row.status,
            "source": row.source,
            "unit": row.unit,
            "definition_revision": row.definition_revision,
            "version": row.version,
            "record_status": row.record_status,
            "supersedes_id": row.supersedes_id,
        }
        if include_evidence:
            result["evidence"] = row.evidence
        return result

    @staticmethod
    def _meeting(row: MeetingRecordRow, include_evidence: bool) -> dict[str, Any]:
        result = {
            "id": row.id,
            "title": row.title,
            "occurred_at": row.occurred_at.isoformat(),
            "participant_entity_ids": row.participant_entity_ids,
            "related_entity_ids": row.related_entity_ids,
            "topics": row.topics,
            "decisions": row.decisions,
            "action_items": row.action_items,
            "escalations": row.escalations,
        }
        if include_evidence:
            result["evidence"] = row.evidence
        return result

    @staticmethod
    def _tradeoff(row: DesignTradeoffRow) -> dict[str, Any]:
        return {
            "id": row.id,
            "title": row.title,
            "issue_family": row.issue_family,
            "description": row.description,
            "benefit": row.benefit,
            "cost": row.cost,
            "affected_entity_ids": row.affected_entity_ids,
            "monitoring_metric_ids": row.monitoring_metric_ids,
            "status": row.status,
            "accepted_by": row.accepted_by,
        }

    @staticmethod
    def _analysis_run(row: ManagementAnalysisRunRow) -> dict[str, Any]:
        return {
            "id": row.id,
            "requested_by": row.requested_by,
            "status": row.status,
            "parameters": row.parameters,
            "design_signal_count": row.design_signal_count,
            "outcome_signal_count": row.outcome_signal_count,
            "insight_count": row.insight_count,
            "created_at": row.created_at.isoformat(),
            "finished_at": row.finished_at.isoformat() if row.finished_at else None,
        }

    @staticmethod
    def _management_signal(
        row: ManagementSignalRow, include_evidence: bool
    ) -> dict[str, Any]:
        result = {
            "id": row.id,
            "run_id": row.run_id,
            "side": row.side,
            "signal_key": row.signal_key,
            "issue_family": row.issue_family,
            "title": row.title,
            "summary": row.summary,
            "severity": row.severity,
            "confidence": row.confidence,
            "expected_direction": row.expected_direction,
            "affected_entity_ids": row.affected_entity_ids,
            "facts": row.facts,
        }
        if include_evidence:
            result["evidence"] = row.evidence
        return result

    @staticmethod
    def _management_insight(row: ManagementInsightRow) -> dict[str, Any]:
        return {
            "id": row.id,
            "run_id": row.run_id,
            "issue_id": row.issue_id,
            "occurrence_number": row.occurrence_number,
            "occurrence_signature": row.occurrence_signature,
            "evidence_changed": row.evidence_changed,
            "classification": row.classification,
            "issue_family": row.issue_family,
            "title": row.title,
            "summary": row.summary,
            "severity": row.severity,
            "confidence": row.confidence,
            "design_signal_ids": row.design_signal_ids,
            "outcome_signal_ids": row.outcome_signal_ids,
            "affected_entity_ids": row.affected_entity_ids,
            "status": row.status,
            "rationale": row.rationale,
            "management_feedback": row.management_feedback,
        }

    @staticmethod
    def _management_issue(row: ManagementIssueRow) -> dict[str, Any]:
        return {
            "id": row.id,
            "issue_key": row.issue_key,
            "issue_family": row.issue_family,
            "title": row.title,
            "affected_entity_ids": row.affected_entity_ids,
            "status": row.status,
            "management_feedback": row.management_feedback,
            "last_occurrence_signature": row.last_occurrence_signature,
            "occurrence_count": row.occurrence_count,
            "revision": row.revision,
            "first_seen_at": row.first_seen_at.isoformat(),
            "last_seen_at": row.last_seen_at.isoformat(),
        }

    @staticmethod
    def _management_issue_feedback(row: ManagementIssueFeedbackRow) -> dict[str, Any]:
        return {
            "id": row.id,
            "issue_id": row.issue_id,
            "insight_id": row.insight_id,
            "from_status": row.from_status,
            "to_status": row.to_status,
            "feedback": row.feedback,
            "provided_by": row.provided_by,
            "created_at": row.created_at.isoformat(),
        }

    @staticmethod
    def _information_request(row: InformationRequestRow) -> dict[str, Any]:
        return {
            "id": row.id,
            "title": row.title,
            "question": row.question,
            "reason": row.reason,
            "priority": row.priority,
            "target_entity_ids": row.target_entity_ids,
            "requested_by": row.requested_by,
            "status": row.status,
            "answer": row.answer,
        }

    @staticmethod
    def _evaluation_suite(row: EvaluationSuiteRow) -> dict[str, Any]:
        return {
            "id": row.id,
            "name": row.name,
            "agent_kind": row.agent_kind,
            "description": row.description,
            "status": row.status,
            "revision": row.revision,
        }

    @staticmethod
    def _evaluation_case(row: EvaluationCaseRow) -> dict[str, Any]:
        return {
            "id": row.id,
            "suite_id": row.suite_id,
            "name": row.name,
            "input": row.input,
            "expected_action_keys": row.expected_action_keys,
            "required_terms": row.required_terms,
            "forbidden_terms": row.forbidden_terms,
            "minimum_citations": row.minimum_citations,
            "status": row.status,
        }

    @staticmethod
    def _evaluation_run(row: EvaluationRunRow) -> dict[str, Any]:
        return {
            "id": row.id,
            "suite_id": row.suite_id,
            "model_profile_id": row.model_profile_id,
            "label": row.label,
            "status": row.status,
            "total_cases": row.total_cases,
            "passed_cases": row.passed_cases,
            "average_score": row.average_score,
        }

    @staticmethod
    def _evaluation_result(row: EvaluationResultRow) -> dict[str, Any]:
        return {
            "id": row.id,
            "run_id": row.run_id,
            "case_id": row.case_id,
            "candidate_content": row.candidate_content,
            "candidate_action_keys": row.candidate_action_keys,
            "candidate_citation_count": row.candidate_citation_count,
            "score": row.score,
            "passed": row.passed,
            "checks": row.checks,
        }

    @staticmethod
    def _evaluation_execution(row: EvaluationExecutionRow) -> dict[str, Any]:
        return {
            "id": row.id,
            "suite_id": row.suite_id,
            "evaluation_run_id": row.evaluation_run_id,
            "model_profile_id": row.model_profile_id,
            "label": row.label,
            "status": row.status,
            "total_cases": row.total_cases,
            "completed_cases": row.completed_cases,
            "agent_run_ids": row.agent_run_ids,
            "error": row.error,
        }
