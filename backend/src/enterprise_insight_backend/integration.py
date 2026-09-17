from __future__ import annotations

import hashlib
import json
import re
from collections.abc import Mapping, Sequence
from uuid import UUID

from sqlalchemy import select, update
from sqlalchemy.orm import Session

from enterprise_insight_backend.config import Settings, get_settings
from enterprise_insight_backend.connectors import connector_for
from enterprise_insight_backend.errors import DomainError
from enterprise_insight_backend.evidence import EvidenceService
from enterprise_insight_backend.model_profiles import LocalSecretVault
from enterprise_insight_backend.models import (
    EntityRow,
    ImportPreviewRow,
    ObservationAssertionRow,
    SemanticMappingRow,
    SemanticRelationMappingRow,
    SourceAssetRow,
    SourceIdentityRow,
    SourceSystemRow,
)
from enterprise_insight_backend.ontology import OntologyService
from enterprise_insight_backend.portfolio import PortfolioService
from enterprise_insight_backend.schemas import (
    ImportConfirmRequest,
    ImportKind,
    ObservationAssertionView,
    SemanticMappingCommand,
    SemanticMappingCreate,
    SemanticMappingStatus,
    SemanticMappingSuggestionPage,
    SemanticMappingSuggestionRequest,
    SemanticMappingSuggestionView,
    SemanticMappingView,
    SemanticRelationMappingCommand,
    SemanticRelationMappingCreate,
    SemanticRelationMappingStatus,
    SemanticRelationMappingView,
    SourceAssetCreate,
    SourceAssetUpdate,
    SourceAssetView,
    SourceConnectorExtractRequest,
    SourceConnectorExtractView,
    SourceConnectorSyncRequest,
    SourceConnectorSyncView,
    SourceIdentityBind,
    SourceIdentityView,
    SourceSystemCreate,
    SourceSystemTestView,
    SourceSystemUpdate,
    SourceSystemView,
    TypeKind,
)
from enterprise_insight_backend.service_utils import json_ready, now_utc, require_revision
from enterprise_insight_backend.transforms import validate_transform_expression

SecretPath = tuple[str | int, ...]


def _is_secret_field(name: str) -> bool:
    normalized = re.sub(r"[^a-z0-9]", "", name.casefold())
    return (
        any(marker in normalized for marker in ("password", "passwd", "secret"))
        or normalized.endswith("token")
        or normalized in {"authorization", "credential", "credentials"}
        or any(
            normalized.endswith(marker)
            for marker in ("apikey", "accesskey", "privatekey")
        )
    )


def _split_connection_profile(
    profile: Mapping[str, object],
) -> tuple[dict[str, object], dict[SecretPath, object], set[SecretPath]]:
    secrets: dict[SecretPath, object] = {}
    cleared: set[SecretPath] = set()

    def visit(value: object, path: SecretPath) -> object:
        if isinstance(value, Mapping):
            cleaned: dict[str, object] = {}
            for raw_key, child in value.items():
                key = str(raw_key)
                child_path = (*path, key)
                if _is_secret_field(key):
                    if child is None or child == "":
                        cleared.add(child_path)
                    else:
                        secrets[child_path] = child
                else:
                    cleaned[key] = visit(child, child_path)
            return cleaned
        if isinstance(value, list):
            return [visit(child, (*path, index)) for index, child in enumerate(value)]
        return value

    return visit(profile, ()), secrets, cleared  # type: ignore[return-value]


def _merge_secret_values(
    existing: dict[SecretPath, object],
    updates: Mapping[SecretPath, object],
    cleared: set[SecretPath],
) -> dict[SecretPath, object]:
    merged = dict(existing)
    for path in cleared:
        merged = {key: value for key, value in merged.items() if key[: len(path)] != path}
    for path, value in updates.items():
        merged = {
            key: item
            for key, item in merged.items()
            if key[: len(path)] != path and path[: len(key)] != key
        }
        merged[path] = value
    return merged


def _apply_secret_values(
    profile: dict[str, object], secrets: Mapping[SecretPath, object]
) -> dict[str, object]:
    result: object = json.loads(json.dumps(profile, ensure_ascii=False))
    for path, value in secrets.items():
        if not path:
            continue
        node = result
        for index, part in enumerate(path[:-1]):
            next_part = path[index + 1]
            if isinstance(part, int):
                if not isinstance(node, list):
                    raise DomainError(
                        "SOURCE_SECRET_PROFILE_INVALID",
                        "数据源凭据配置无法读取。",
                        status_code=409,
                    )
                while len(node) <= part:
                    node.append([] if isinstance(next_part, int) else {})
                if not isinstance(node[part], (dict, list)):
                    node[part] = [] if isinstance(next_part, int) else {}
                node = node[part]
            else:
                if not isinstance(node, dict):
                    raise DomainError(
                        "SOURCE_SECRET_PROFILE_INVALID",
                        "数据源凭据配置无法读取。",
                        status_code=409,
                    )
                if not isinstance(node.get(part), (dict, list)):
                    node[part] = [] if isinstance(next_part, int) else {}
                node = node[part]
        last = path[-1]
        if isinstance(last, int):
            if not isinstance(node, list):
                raise DomainError(
                    "SOURCE_SECRET_PROFILE_INVALID",
                    "数据源凭据配置无法读取。",
                    status_code=409,
                )
            while len(node) <= last:
                node.append(None)
            node[last] = value
        else:
            if not isinstance(node, dict):
                raise DomainError(
                    "SOURCE_SECRET_PROFILE_INVALID",
                    "数据源凭据配置无法读取。",
                    status_code=409,
                )
            node[last] = value
    if not isinstance(result, dict):
        raise DomainError(
            "SOURCE_SECRET_PROFILE_INVALID",
            "数据源凭据配置无法读取。",
            status_code=409,
        )
    return result


class IntegrationService:
    def __init__(self, session: Session, settings: Settings | None = None) -> None:
        self.session = session
        self.settings = settings
        self._secret_vault: LocalSecretVault | None = None
        self.portfolio = PortfolioService(session)
        self.ontology = OntologyService(session)

    def list_sources(self, project_id: UUID) -> list[SourceSystemView]:
        self.portfolio.require_project(project_id)
        rows = self.session.scalars(
            select(SourceSystemRow)
            .where(SourceSystemRow.project_id == str(project_id))
            .order_by(SourceSystemRow.name)
        ).all()
        return [self._source_view(row) for row in rows]

    def create_source(self, project_id: UUID, payload: SourceSystemCreate) -> SourceSystemView:
        self.portfolio.require_project(project_id)
        if payload.kind.value in {"MYSQL", "SQLSERVER"}:
            raise DomainError(
                "CONNECTOR_UNAVAILABLE",
                (
                    "当前版本尚未安装该数据库连接器，请先导出为 SQLite/CSV "
                    "或使用 PostgreSQL/REST 只读源。"
                ),
                status_code=422,
                details=[{"kind": payload.kind.value}],
            )
        profile, secrets, _ = _split_connection_profile(payload.connection_profile)
        row = SourceSystemRow(
            project_id=str(project_id),
            name=payload.name,
            kind=payload.kind.value,
            description=payload.description,
            connection_profile=json_ready(profile),
            encrypted_connection_secrets=self._encrypt_secret_values(secrets),
        )
        self.session.add(row)
        self.session.flush()
        return self._source_view(row)

    def update_source(
        self, project_id: UUID, source_id: UUID, payload: SourceSystemUpdate
    ) -> SourceSystemView:
        row = self.require_source(project_id, source_id)
        require_revision(row.revision, payload.expected_revision, resource="数据源")
        values = payload.model_dump(exclude_unset=True, exclude={"expected_revision"})
        if "connection_profile" in values:
            if values["connection_profile"] is None:
                raise DomainError(
                    "SOURCE_CONNECTION_PROFILE_INVALID",
                    "连接配置必须是 JSON 对象。",
                    status_code=422,
                )
            existing_secrets = self._ensure_secret_values(row)
            profile, secret_updates, cleared = _split_connection_profile(
                values.pop("connection_profile")
            )
            merged_secrets = _merge_secret_values(existing_secrets, secret_updates, cleared)
            row.connection_profile = json_ready(profile)
            row.encrypted_connection_secrets = self._encrypt_secret_values(merged_secrets)
            row.status = "CONFIGURED"
            row.last_tested_at = None
        for key, value in values.items():
            setattr(row, key, value)
        row.revision += 1
        self.session.flush()
        return self._source_view(row)

    def test_source(self, project_id: UUID, source_id: UUID) -> SourceSystemTestView:
        row = self.require_source(project_id, source_id)
        if row.kind == "FILE":
            ok, message = True, "文件数据源配置有效。"
        else:
            try:
                ok, message = connector_for(row.kind, self._resolved_profile(row)).test()
            except DomainError as exc:
                ok, message = False, exc.message
        row.last_tested_at = now_utc()
        row.status = "READY" if ok else "CONNECTOR_REQUIRED"
        self.session.flush()
        return SourceSystemTestView.model_validate(
            {
                "source_system_id": row.id,
                "ok": ok,
                "message": message,
                "checked_at": row.last_tested_at,
            }
        )

    def extract_preview(
        self,
        project_id: UUID,
        source_id: UUID,
        payload: SourceConnectorExtractRequest,
    ) -> SourceConnectorExtractView:
        row = self.require_source(project_id, source_id)
        page = connector_for(row.kind, self._resolved_profile(row)).extract(
            payload.asset_key,
            limit=payload.limit,
            watermark_column=payload.watermark_column,
            tie_breaker_column=payload.tie_breaker_column,
            after_watermark=payload.after_watermark,
            after_tie_breaker=payload.after_tie_breaker,
        )
        serialized_rows = json_ready(page.rows)
        content = json.dumps(
            serialized_rows, ensure_ascii=False, sort_keys=True, default=str
        ).encode("utf-8")
        preview = EvidenceService(self.session).preview_csv(
            project_id,
            file_name=f"{payload.asset_key}.json",
            content=content,
            kind=ImportKind.JSON,
            source_system_id=source_id,
            preview_metadata={
                "kind": "CONNECTOR_EXTRACT",
                "source_asset": payload.asset_key,
                "next_watermark": json_ready(page.next_watermark),
                "next_tie_breaker": json_ready(page.next_tie_breaker),
            },
        )
        return SourceConnectorExtractView.model_validate(
            {
                "preview_id": preview.id,
                "content_sha256": hashlib.sha256(content).hexdigest(),
                "source_system_id": row.id,
                "asset_key": payload.asset_key,
                "columns": page.columns,
                "rows": serialized_rows,
                "next_watermark": json_ready(page.next_watermark),
                "next_tie_breaker": json_ready(page.next_tie_breaker),
            }
        )

    def sync(
        self,
        project_id: UUID,
        source_id: UUID,
        payload: SourceConnectorSyncRequest,
    ) -> SourceConnectorSyncView:
        self.require_source(project_id, source_id)
        preview = self.session.get(ImportPreviewRow, str(payload.preview_id))
        if (
            preview is None
            or preview.project_id != str(project_id)
            or preview.source_system_id != str(source_id)
        ):
            raise DomainError(
                "CONNECTOR_PREVIEW_NOT_FOUND",
                "数据库提取预览不存在或不属于当前数据源。",
                status_code=404,
            )
        metadata = preview.preview_metadata or {}
        if metadata.get("kind") != "CONNECTOR_EXTRACT" or not metadata.get(
            "source_asset"
        ):
            raise DomainError(
                "CONNECTOR_PREVIEW_INVALID",
                "该预览不是可确认的数据库提取快照。",
                status_code=409,
            )
        extraction = SourceConnectorExtractView.model_validate(
            {
                "preview_id": preview.id,
                "content_sha256": preview.content_sha256,
                "source_system_id": source_id,
                "asset_key": str(metadata["source_asset"]),
                "columns": preview.columns,
                "rows": json_ready(preview.all_rows),
                "next_watermark": json_ready(metadata.get("next_watermark")),
                "next_tie_breaker": json_ready(metadata.get("next_tie_breaker")),
            }
        )
        imported = EvidenceService(self.session).confirm(
            project_id,
            ImportConfirmRequest(
                preview_id=UUID(preview.id),
                options={"source_asset": extraction.asset_key},
            ),
        )
        return SourceConnectorSyncView(extraction=extraction, import_result=imported)

    def list_assets(self, project_id: UUID, source_id: UUID) -> list[SourceAssetView]:
        self.require_source(project_id, source_id)
        rows = self.session.scalars(
            select(SourceAssetRow)
            .where(SourceAssetRow.source_system_id == str(source_id))
            .order_by(SourceAssetRow.name, SourceAssetRow.asset_key)
        ).all()
        return [SourceAssetView.model_validate(row) for row in rows]

    def create_asset(
        self, project_id: UUID, source_id: UUID, payload: SourceAssetCreate
    ) -> SourceAssetView:
        self.require_source(project_id, source_id)
        existing = self.session.scalar(
            select(SourceAssetRow).where(
                SourceAssetRow.source_system_id == str(source_id),
                SourceAssetRow.asset_key == payload.asset_key,
            )
        )
        if existing is not None:
            raise DomainError(
                "SOURCE_ASSET_KEY_EXISTS",
                "该数据源中已经存在相同资产标识。",
                status_code=409,
            )
        row = SourceAssetRow(
            project_id=str(project_id),
            source_system_id=str(source_id),
            asset_key=payload.asset_key,
            name=payload.name,
            description=payload.description,
        )
        self.session.add(row)
        self.session.flush()
        return SourceAssetView.model_validate(row)

    def update_asset(
        self,
        project_id: UUID,
        source_id: UUID,
        asset_id: UUID,
        payload: SourceAssetUpdate,
    ) -> SourceAssetView:
        row = self.require_asset(project_id, source_id, asset_id)
        require_revision(row.revision, payload.expected_revision, resource="来源资产")
        values = payload.model_dump(exclude_unset=True, exclude={"expected_revision"})
        if "status" in values and values["status"] not in {"ACTIVE", "DISABLED"}:
            raise DomainError(
                "SOURCE_ASSET_STATUS_INVALID",
                "来源资产只能人工设为 ACTIVE 或 DISABLED。",
                status_code=422,
            )
        for key, value in values.items():
            setattr(row, key, value)
        row.revision += 1
        self.session.flush()
        return SourceAssetView.model_validate(row)

    def list_mappings(self, project_id: UUID) -> list[SemanticMappingView]:
        self.portfolio.require_project(project_id)
        rows = self.session.scalars(
            select(SemanticMappingRow)
            .where(SemanticMappingRow.project_id == str(project_id))
            .order_by(SemanticMappingRow.source_asset, SemanticMappingRow.source_field)
        ).all()
        return [SemanticMappingView.model_validate(row) for row in rows]

    def suggest_mappings(
        self, project_id: UUID, payload: SemanticMappingSuggestionRequest
    ) -> SemanticMappingSuggestionPage:
        """Return conservative field candidates without creating or approving mappings.

        The matcher only uses exact normalized ontology keys/labels and a small set of
        reserved identity/name aliases.  It deliberately returns candidates for human
        review; the result is never treated as an approved semantic fact.
        """
        self.portfolio.require_project(project_id)
        target = self.ontology.require_type_by_key(project_id, payload.target_type_key)
        if target.kind not in {TypeKind.OBJECT.value, TypeKind.METRIC.value}:
            raise DomainError(
                "MAPPING_TARGET_ENTITY_TYPE_REQUIRED",
                "字段映射建议的目标必须是对象或指标类型。",
                status_code=422,
            )
        sources_statement = select(SourceSystemRow).where(
            SourceSystemRow.project_id == str(project_id)
        )
        if payload.source_system_id is not None:
            sources_statement = sources_statement.where(
                SourceSystemRow.id == str(payload.source_system_id)
            )
        sources = self.session.scalars(sources_statement.order_by(SourceSystemRow.name)).all()
        if payload.source_system_id is not None and not sources:
            raise DomainError("SOURCE_SYSTEM_NOT_FOUND", "数据源不存在。", status_code=404)
        source_ids = [item.id for item in sources]
        if not source_ids:
            return SemanticMappingSuggestionPage(
                items=[],
                total=0,
                warnings=["当前项目还没有数据源或可供分析的来源资产。"],
            )
        assets_statement = select(SourceAssetRow).where(
            SourceAssetRow.project_id == str(project_id),
            SourceAssetRow.source_system_id.in_(source_ids),
            SourceAssetRow.status != "DISABLED",
        )
        if payload.source_asset:
            assets_statement = assets_statement.where(
                SourceAssetRow.asset_key == payload.source_asset
            )
        assets = self.session.scalars(
            assets_statement.order_by(SourceAssetRow.asset_key)
        ).all()
        if payload.source_asset and not assets:
            raise DomainError("SOURCE_ASSET_NOT_FOUND", "来源资产不存在。", status_code=404)

        mapping_rows: Sequence[SemanticMappingRow] = []
        if assets:
            mapping_rows = self.session.scalars(
                select(SemanticMappingRow).where(
                    SemanticMappingRow.project_id == str(project_id),
                    SemanticMappingRow.source_system_id.in_(source_ids),
                    SemanticMappingRow.source_asset.in_([item.asset_key for item in assets]),
                )
            ).all()
        existing = {
            (
                item.source_system_id,
                item.source_asset,
                item.source_field,
                item.target_type_key,
                item.target_property_key,
            ): item
            for item in mapping_rows
        }
        target_properties = [
            (
                str(item.get("key", "")),
                str(item.get("name", "")),
                str(item.get("value_type", "STRING")),
            )
            for item in (target.properties or [])
            if item.get("key")
        ]
        reserved = [
            (
                "__stable_key__",
                "稳定标识",
                "STRING",
                {"id", "code", "key", "uuid", "编码", "编号", "唯一标识"},
            ),
            ("__name__", "显示名称", "STRING", {"name", "名称", "标题", "显示名称"}),
        ]
        candidates: list[SemanticMappingSuggestionView] = []
        source_by_id = {item.id: item for item in sources}
        for asset in assets:
            for field in asset.schema_fields or []:
                normalized_field = self._normalize_mapping_label(field)
                matches: list[tuple[str, str, str, str]] = []
                for key, name, value_type, aliases in reserved:
                    normalized_aliases = {
                        self._normalize_mapping_label(item) for item in aliases
                    }
                    if normalized_field in normalized_aliases:
                        matches.append((key, name, value_type, "RESERVED_ALIAS"))
                for key, name, value_type in target_properties:
                    aliases = {key, name}
                    if normalized_field in {
                        self._normalize_mapping_label(item) for item in aliases if item
                    }:
                        kind = (
                            "EXACT_KEY"
                            if normalized_field == self._normalize_mapping_label(key)
                            else "ONTOLOGY_LABEL"
                        )
                        matches.append((key, name or key, value_type, kind))
                if not matches:
                    continue
                for target_key, target_name, value_type, match_kind in matches:
                    existing_row = existing.get(
                        (asset.source_system_id, asset.asset_key, field, target.key, target_key)
                    )
                    if existing_row is not None and not payload.include_existing:
                        continue
                    candidates.append(
                        SemanticMappingSuggestionView.model_validate(
                            {
                            "source_system_id": asset.source_system_id,
                            "source_system_name": source_by_id[asset.source_system_id].name,
                            "source_asset": asset.asset_key,
                            "source_field": field,
                            "target_type_key": target.key,
                            "target_type_name": target.name,
                            "target_property_key": target_key,
                            "target_property_name": target_name,
                            "transform_expression": (
                                "strip|int" if value_type == "INTEGER" else "strip"
                            ),
                            "match_kind": match_kind,
                            "confidence": 1.0 if match_kind == "EXACT_KEY" else 0.9,
                            "rationale": (
                                "来源字段与本体属性键规范化后完全一致。"
                                if match_kind == "EXACT_KEY"
                                else "来源字段与保留标识或本体显示名规范化后匹配；仍需人工确认。"
                            ),
                            "existing_mapping_id": (
                                UUID(existing_row.id) if existing_row else None
                            ),
                            "safe_to_auto_apply": False,
                            }
                        )
                    )
        return SemanticMappingSuggestionPage(
            items=candidates,
            total=len(candidates),
            warnings=[
                "候选只依据字段名与本体键/标签生成，不读取或推断字段值。",
                "候选不会自动创建、批准或应用映射；请逐条核对后再保存。",
            ],
        )

    @staticmethod
    def _normalize_mapping_label(value: str) -> str:
        return re.sub(r"[^\w\u4e00-\u9fff]+", "", value.strip().lower())

    def list_relation_mappings(self, project_id: UUID) -> list[SemanticRelationMappingView]:
        self.portfolio.require_project(project_id)
        rows = self.session.scalars(
            select(SemanticRelationMappingRow)
            .where(SemanticRelationMappingRow.project_id == str(project_id))
            .order_by(
                SemanticRelationMappingRow.source_asset,
                SemanticRelationMappingRow.source_field,
            )
        ).all()
        return [SemanticRelationMappingView.model_validate(row) for row in rows]

    def create_relation_mapping(
        self, project_id: UUID, payload: SemanticRelationMappingCreate
    ) -> SemanticRelationMappingView:
        self.portfolio.require_project(project_id)
        self.require_source(project_id, payload.source_system_id)
        self._validate_relation_mapping_payload(project_id, payload)
        row = SemanticRelationMappingRow(
            project_id=str(project_id),
            source_system_id=str(payload.source_system_id),
            **json_ready(payload.model_dump(exclude={"source_system_id"})),
        )
        self.session.add(row)
        self.session.flush()
        return SemanticRelationMappingView.model_validate(row)

    def validate_relation_mapping(
        self,
        project_id: UUID,
        mapping_id: UUID,
        payload: SemanticRelationMappingCommand,
    ) -> SemanticRelationMappingView:
        row = self.require_relation_mapping(project_id, mapping_id)
        require_revision(row.revision, payload.expected_revision, resource="关系映射")
        if row.status in {
            SemanticRelationMappingStatus.APPROVED.value,
            SemanticRelationMappingStatus.DISABLED.value,
        }:
            raise DomainError(
                "RELATION_MAPPING_NOT_VALIDATABLE",
                "已批准或已停用的关系映射不能重新校验。",
                status_code=409,
            )
        self._validate_relation_mapping_payload(
            project_id, SemanticRelationMappingCreate.model_validate(row)
        )
        row.status = SemanticRelationMappingStatus.VALIDATED.value
        row.validation_report = {
            "valid": True,
            "checks": [
                "source_system",
                "source_type",
                "relation_type",
                "relation_roles",
                "target_type",
                "transform",
            ],
            "validated_at": now_utc().isoformat(),
        }
        row.revision += 1
        self.session.flush()
        return SemanticRelationMappingView.model_validate(row)

    def approve_relation_mapping(
        self,
        project_id: UUID,
        mapping_id: UUID,
        payload: SemanticRelationMappingCommand,
    ) -> SemanticRelationMappingView:
        row = self.require_relation_mapping(project_id, mapping_id)
        require_revision(row.revision, payload.expected_revision, resource="关系映射")
        if row.status != SemanticRelationMappingStatus.VALIDATED.value:
            raise DomainError(
                "RELATION_MAPPING_VALIDATION_REQUIRED",
                "关系映射必须先通过校验才能批准。",
                status_code=409,
            )
        row.status = SemanticRelationMappingStatus.APPROVED.value
        row.revision += 1
        self.session.flush()
        return SemanticRelationMappingView.model_validate(row)

    def disable_relation_mapping(
        self,
        project_id: UUID,
        mapping_id: UUID,
        payload: SemanticRelationMappingCommand,
    ) -> SemanticRelationMappingView:
        row = self.require_relation_mapping(project_id, mapping_id)
        require_revision(row.revision, payload.expected_revision, resource="关系映射")
        if row.status != SemanticRelationMappingStatus.DISABLED.value:
            row.status = SemanticRelationMappingStatus.DISABLED.value
            row.revision += 1
            self.session.flush()
        return SemanticRelationMappingView.model_validate(row)

    def create_mapping(
        self, project_id: UUID, payload: SemanticMappingCreate
    ) -> SemanticMappingView:
        self.portfolio.require_project(project_id)
        self.require_source(project_id, payload.source_system_id)
        target = self.ontology.require_type_by_key(project_id, payload.target_type_key)
        if target.kind not in {TypeKind.OBJECT.value, TypeKind.METRIC.value}:
            raise DomainError(
                "MAPPING_TARGET_ENTITY_TYPE_REQUIRED",
                "字段映射的目标必须是对象或指标类型。",
                status_code=422,
            )
        property_keys = {item["key"] for item in target.properties}
        reserved_fields = {"__stable_key__", "__name__"}
        if payload.target_property_key not in property_keys | reserved_fields:
            raise DomainError(
                "MAPPING_TARGET_PROPERTY_NOT_FOUND",
                "目标本体属性不存在。",
                status_code=422,
            )
        validate_transform_expression(payload.transform_expression)
        row = SemanticMappingRow(
            project_id=str(project_id),
            source_system_id=str(payload.source_system_id),
            source_asset=payload.source_asset,
            source_field=payload.source_field,
            target_type_key=payload.target_type_key,
            target_property_key=payload.target_property_key,
            transform_expression=payload.transform_expression,
            authority_priority=payload.authority_priority,
        )
        self.session.add(row)
        self.session.flush()
        return SemanticMappingView.model_validate(row)

    def validate_mapping(
        self,
        project_id: UUID,
        mapping_id: UUID,
        payload: SemanticMappingCommand,
    ) -> SemanticMappingView:
        row = self.require_mapping(project_id, mapping_id)
        require_revision(row.revision, payload.expected_revision, resource="语义映射")
        if row.status in {
            SemanticMappingStatus.APPROVED.value,
            SemanticMappingStatus.SUPERSEDED.value,
            SemanticMappingStatus.DISABLED.value,
        }:
            raise DomainError(
                "MAPPING_NOT_VALIDATABLE",
                "已批准、已替代或已停用的映射不能重新校验。",
                status_code=409,
            )
        self.require_source(project_id, UUID(row.source_system_id))
        target = self.ontology.require_type_by_key(project_id, row.target_type_key)
        if target.kind not in {TypeKind.OBJECT.value, TypeKind.METRIC.value}:
            raise DomainError(
                "MAPPING_TARGET_ENTITY_TYPE_REQUIRED",
                "字段映射的目标必须是对象或指标类型。",
                status_code=422,
            )
        property_keys = {item["key"] for item in target.properties}
        if row.target_property_key not in property_keys | {"__stable_key__", "__name__"}:
            raise DomainError(
                "MAPPING_TARGET_PROPERTY_NOT_FOUND",
                "目标本体属性不存在。",
                status_code=422,
            )
        validate_transform_expression(row.transform_expression)
        row.status = SemanticMappingStatus.VALIDATED.value
        row.validation_report = {
            "valid": True,
            "checks": ["source_exists", "target_type", "target_property", "transform"],
            "validated_at": now_utc().isoformat(),
        }
        row.revision += 1
        self.session.flush()
        return SemanticMappingView.model_validate(row)

    def approve_mapping(
        self,
        project_id: UUID,
        mapping_id: UUID,
        payload: SemanticMappingCommand,
    ) -> SemanticMappingView:
        row = self.require_mapping(project_id, mapping_id)
        require_revision(row.revision, payload.expected_revision, resource="语义映射")
        if row.status != SemanticMappingStatus.VALIDATED.value:
            raise DomainError(
                "MAPPING_VALIDATION_REQUIRED",
                "映射必须先通过校验才能批准。",
                status_code=409,
            )
        row.status = SemanticMappingStatus.APPROVED.value
        row.revision += 1
        self.session.flush()
        return SemanticMappingView.model_validate(row)

    def disable_mapping(
        self,
        project_id: UUID,
        mapping_id: UUID,
        payload: SemanticMappingCommand,
    ) -> SemanticMappingView:
        row = self.require_mapping(project_id, mapping_id)
        require_revision(row.revision, payload.expected_revision, resource="语义映射")
        if row.status == SemanticMappingStatus.DISABLED.value:
            return SemanticMappingView.model_validate(row)
        row.status = SemanticMappingStatus.DISABLED.value
        row.revision += 1
        self.session.flush()
        return SemanticMappingView.model_validate(row)

    def list_identities(
        self,
        project_id: UUID,
        *,
        source_system_id: UUID | None = None,
    ) -> list[SourceIdentityView]:
        self.portfolio.require_project(project_id)
        statement = select(SourceIdentityRow).where(
            SourceIdentityRow.project_id == str(project_id)
        )
        if source_system_id is not None:
            self.require_source(project_id, source_system_id)
            statement = statement.where(
                SourceIdentityRow.source_system_id == str(source_system_id)
            )
        rows = self.session.scalars(
            statement.order_by(
                SourceIdentityRow.status.desc(),
                SourceIdentityRow.source_asset,
                SourceIdentityRow.source_record_key,
            )
        ).all()
        return [self._identity_view(row) for row in rows]

    def bind_identity(
        self,
        project_id: UUID,
        identity_id: UUID,
        payload: SourceIdentityBind,
    ) -> SourceIdentityView:
        identity = self.require_identity(project_id, identity_id)
        require_revision(identity.revision, payload.expected_revision, resource="来源身份")
        target = self.session.get(EntityRow, str(payload.entity_id))
        if target is None or target.project_id != str(project_id):
            raise DomainError("ENTITY_NOT_FOUND", "目标实体不存在。", status_code=404)
        if target.design_membership != "MODELED" or target.status == "RETIRED":
            raise DomainError(
                "IDENTITY_TARGET_NOT_MODELED",
                "来源身份只能绑定到有效的正式设计实体。",
                status_code=422,
            )
        target_type = self.ontology.require_type_by_key(project_id, target.type_key)
        if not self.ontology.entity_type_satisfies(
            target_type, {identity.target_type_key}
        ):
            raise DomainError(
                "IDENTITY_TARGET_TYPE_MISMATCH",
                "来源记录类型与目标实体类型不兼容。",
                status_code=422,
            )
        old_entity_id = identity.entity_id
        self.session.execute(
            update(ObservationAssertionRow)
            .where(ObservationAssertionRow.source_identity_id == identity.id)
            .values(entity_id=target.id)
        )
        identity.entity_id = target.id
        identity.status = "BOUND"
        identity.revision += 1
        if old_entity_id != target.id:
            old_entity = self.session.get(EntityRow, old_entity_id)
            remaining_bindings = self.session.scalar(
                select(SourceIdentityRow.id)
                .where(
                    SourceIdentityRow.entity_id == old_entity_id,
                    SourceIdentityRow.id != identity.id,
                )
                .limit(1)
            )
            if (
                old_entity is not None
                and old_entity.design_membership == "UNMODELED"
                and remaining_bindings is None
            ):
                old_entity.status = "RETIRED"
                old_entity.revision += 1
        self.session.flush()
        return self._identity_view(identity)

    def list_observations(
        self,
        project_id: UUID,
        *,
        entity_id: UUID | None = None,
    ) -> list[ObservationAssertionView]:
        self.portfolio.require_project(project_id)
        statement = select(ObservationAssertionRow).where(
            ObservationAssertionRow.project_id == str(project_id)
        )
        if entity_id is not None:
            target = self.session.get(EntityRow, str(entity_id))
            if target is None or target.project_id != str(project_id):
                raise DomainError("ENTITY_NOT_FOUND", "目标实体不存在。", status_code=404)
            statement = statement.where(
                ObservationAssertionRow.entity_id == str(entity_id)
            )
        rows = self.session.scalars(
            statement.order_by(
                ObservationAssertionRow.observed_at.desc(),
                ObservationAssertionRow.created_at.desc(),
            )
        ).all()
        return [ObservationAssertionView.model_validate(row) for row in rows]

    def require_identity(
        self, project_id: UUID, identity_id: UUID
    ) -> SourceIdentityRow:
        row = self.session.get(SourceIdentityRow, str(identity_id))
        if row is None or row.project_id != str(project_id):
            raise DomainError("SOURCE_IDENTITY_NOT_FOUND", "来源身份不存在。", status_code=404)
        return row

    def require_mapping(
        self, project_id: UUID, mapping_id: UUID
    ) -> SemanticMappingRow:
        row = self.session.get(SemanticMappingRow, str(mapping_id))
        if row is None or row.project_id != str(project_id):
            raise DomainError("SEMANTIC_MAPPING_NOT_FOUND", "语义映射不存在。", status_code=404)
        return row

    def require_relation_mapping(
        self, project_id: UUID, mapping_id: UUID
    ) -> SemanticRelationMappingRow:
        row = self.session.get(SemanticRelationMappingRow, str(mapping_id))
        if row is None or row.project_id != str(project_id):
            raise DomainError(
                "SEMANTIC_RELATION_MAPPING_NOT_FOUND",
                "语义关系映射不存在。",
                status_code=404,
            )
        return row

    def _validate_relation_mapping_payload(
        self, project_id: UUID, payload: SemanticRelationMappingCreate
    ) -> None:
        self.require_source(project_id, payload.source_system_id)
        source_type = self.ontology.require_type_by_key(
            project_id, payload.source_type_key, kind=TypeKind.OBJECT
        )
        target_type = self.ontology.require_type_by_key(
            project_id, payload.target_type_key, kind=TypeKind.OBJECT
        )
        relation_type = self.ontology.require_type_by_key(
            project_id, payload.relation_type_key, kind=TypeKind.RELATION
        )
        role_by_key = {item["key"]: item for item in relation_type.relation_roles}
        missing_roles = sorted(
            {payload.source_role_key, payload.target_role_key} - set(role_by_key)
        )
        if missing_roles:
            raise DomainError(
                "RELATION_MAPPING_ROLE_NOT_FOUND",
                "关系映射使用了当前本体关系未定义的角色。",
                status_code=422,
                details=[
                    {
                        "missing_roles": missing_roles,
                        "relation_type_key": payload.relation_type_key,
                        "allowed_roles": sorted(role_by_key),
                    }
                ],
            )
        for role_key, type_key in (
            (payload.source_role_key, source_type.key),
            (payload.target_role_key, target_type.key),
        ):
            allowed = set(role_by_key[role_key].get("allowed_type_keys") or [])
            if allowed and type_key not in allowed:
                raise DomainError(
                    "RELATION_MAPPING_TYPE_MISMATCH",
                    f"关系角色 {role_key} 不允许本体类型 {type_key}。",
                    status_code=422,
                    details=[{"role": role_key, "allowed_type_keys": sorted(allowed)}],
                )
        validate_transform_expression(payload.transform_expression)

    def require_source(self, project_id: UUID, source_id: UUID) -> SourceSystemRow:
        row = self.session.get(SourceSystemRow, str(source_id))
        if row is None or row.project_id != str(project_id):
            raise DomainError("SOURCE_SYSTEM_NOT_FOUND", "数据源不存在。", status_code=404)
        return row

    def connector_profile(self, project_id: UUID, source_id: UUID) -> dict[str, object]:
        """Return a decrypted profile for trusted in-process connector execution only."""
        row = self.require_source(project_id, source_id)
        return self._resolved_profile(row)

    def require_asset(
        self, project_id: UUID, source_id: UUID, asset_id: UUID
    ) -> SourceAssetRow:
        row = self.session.get(SourceAssetRow, str(asset_id))
        if (
            row is None
            or row.project_id != str(project_id)
            or row.source_system_id != str(source_id)
        ):
            raise DomainError("SOURCE_ASSET_NOT_FOUND", "来源资产不存在。", status_code=404)
        return row

    def _identity_view(self, row: SourceIdentityRow) -> SourceIdentityView:
        entity = self.session.get(EntityRow, row.entity_id)
        if entity is None:
            raise DomainError("ENTITY_NOT_FOUND", "来源身份对应的实体不存在。", status_code=409)
        return SourceIdentityView.model_validate(
            {
                "id": row.id,
                "project_id": row.project_id,
                "source_system_id": row.source_system_id,
                "source_asset": row.source_asset,
                "source_record_key": row.source_record_key,
                "target_type_key": row.target_type_key,
                "entity_id": row.entity_id,
                "entity_name": entity.name,
                "status": row.status,
                "revision": row.revision,
                "created_at": row.created_at,
                "updated_at": row.updated_at,
            }
        )

    def require_file_source(self, project_id: UUID, source_id: UUID) -> SourceSystemRow:
        row = self.require_source(project_id, source_id)
        if row.kind != "FILE":
            raise DomainError(
                "FILE_CONNECTOR_REQUIRED",
                "当前上传接口只适用于文件数据源；该数据源需要对应连接器。",
                status_code=409,
            )
        row.status = "READY"
        row.last_tested_at = now_utc()
        self.session.flush()
        return row

    def _vault(self, *, require_existing: bool = False) -> LocalSecretVault:
        if self._secret_vault is not None:
            return self._secret_vault
        settings = self.settings or get_settings()
        key_path = settings.data_dir / "model-profile.key"
        if require_existing and not key_path.is_file():
            raise DomainError(
                "SOURCE_SECRET_KEY_MISSING",
                "本机数据源凭据密钥文件缺失，请恢复 model-profile.key 后重试。",
                status_code=409,
            )
        try:
            self._secret_vault = LocalSecretVault(settings)
        except (OSError, ValueError) as exc:
            raise DomainError(
                "SOURCE_SECRET_KEY_UNREADABLE",
                "本机数据源凭据密钥无法读取。",
                status_code=409,
            ) from exc
        return self._secret_vault

    def _encrypt_secret_values(self, values: Mapping[SecretPath, object]) -> str | None:
        if not values:
            return None
        payload = [
            {"path": list(path), "value": value}
            for path, value in sorted(values.items(), key=lambda item: repr(item[0]))
        ]
        serialized = json.dumps(payload, ensure_ascii=False, sort_keys=True, separators=(",", ":"))
        return self._vault().encrypt(serialized)

    def _decrypt_secret_values(self, encrypted: str) -> dict[SecretPath, object]:
        try:
            decoded = json.loads(self._vault(require_existing=True).decrypt(encrypted))
            if not isinstance(decoded, list):
                raise ValueError("invalid secret payload")
            values: dict[SecretPath, object] = {}
            for item in decoded:
                if not isinstance(item, dict) or not isinstance(item.get("path"), list):
                    raise ValueError("invalid secret entry")
                path_parts = item["path"]
                if not path_parts or any(type(part) not in (str, int) for part in path_parts):
                    raise ValueError("invalid secret path")
                values[tuple(path_parts)] = item["value"]
            return values
        except (DomainError, ValueError, TypeError, json.JSONDecodeError) as exc:
            raise DomainError(
                "SOURCE_SECRET_KEY_UNREADABLE",
                "本机数据源凭据无法解密，请恢复正确的 model-profile.key。",
                status_code=409,
            ) from exc

    def _ensure_secret_values(self, row: SourceSystemRow) -> dict[SecretPath, object]:
        if not isinstance(row.connection_profile, Mapping):
            raise DomainError(
                "SOURCE_CONNECTION_PROFILE_INVALID",
                "数据源连接配置无法读取。",
                status_code=409,
            )
        secrets = (
            self._decrypt_secret_values(row.encrypted_connection_secrets)
            if row.encrypted_connection_secrets
            else {}
        )
        cleaned, legacy_secrets, cleared = _split_connection_profile(row.connection_profile)
        combined = _merge_secret_values(secrets, legacy_secrets, cleared)
        if (
            cleaned != row.connection_profile
            or legacy_secrets
            or cleared
        ):
            row.connection_profile = json_ready(cleaned)
            row.encrypted_connection_secrets = self._encrypt_secret_values(combined)
            self.session.flush()
        return combined

    def _resolved_profile(self, row: SourceSystemRow) -> dict[str, object]:
        secrets = self._ensure_secret_values(row)
        return _apply_secret_values(row.connection_profile, secrets)

    def _source_view(self, row: SourceSystemRow) -> SourceSystemView:
        profile = self._resolved_profile(row)
        return SourceSystemView.model_validate(
            {
                "id": row.id,
                "project_id": row.project_id,
                "name": row.name,
                "kind": row.kind,
                "description": row.description,
                "configured_fields": sorted(profile),
                "status": row.status,
                "last_tested_at": row.last_tested_at,
                "revision": row.revision,
                "created_at": row.created_at,
                "updated_at": row.updated_at,
            }
        )
