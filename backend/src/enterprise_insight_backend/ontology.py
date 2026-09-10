from __future__ import annotations

from collections.abc import Callable
from datetime import date, datetime
from typing import Any
from uuid import UUID

from sqlalchemy import func, select
from sqlalchemy.exc import IntegrityError
from sqlalchemy.orm import Session

from enterprise_insight_backend.errors import DomainError
from enterprise_insight_backend.models import OntologyReleaseRow, OntologyTypeRow
from enterprise_insight_backend.portfolio import PortfolioService
from enterprise_insight_backend.schemas import (
    LifecycleStatus,
    OntologyReleaseCreate,
    OntologyReleaseView,
    OntologyTypeCreate,
    OntologyTypeUpdate,
    OntologyTypeView,
    TypeKind,
)
from enterprise_insight_backend.service_utils import json_ready, require_revision

DEFAULT_TYPES: tuple[dict[str, Any], ...] = (
    {
        "key": "organizational_subject",
        "name": "组织主体",
        "kind": "INTERFACE",
        "description": "能够承担职责、拥有权限或参与工作的组织主体。",
    },
    {
        "key": "work_bearer",
        "name": "工作承担者",
        "kind": "INTERFACE",
        "description": "能够负责、执行、审批或协作完成工作的主体。",
    },
    {
        "key": "information_asset",
        "name": "信息资产",
        "kind": "INTERFACE",
        "description": "能够被产生、传递、使用和治理的信息或数据。",
    },
    {
        "key": "company",
        "name": "公司",
        "kind": "OBJECT",
        "interface_keys": ["organizational_subject"],
        "properties": [
            {"key": "purpose", "name": "企业目的", "value_type": "STRING"},
            {"key": "industry", "name": "行业", "value_type": "STRING"},
        ],
    },
    {
        "key": "organization_unit",
        "name": "组织单元",
        "kind": "OBJECT",
        "interface_keys": ["organizational_subject", "work_bearer"],
        "properties": [
            {"key": "mandate", "name": "设立目的", "value_type": "STRING"},
        ],
    },
    {
        "key": "role",
        "name": "岗位角色",
        "kind": "OBJECT",
        "interface_keys": ["organizational_subject", "work_bearer"],
        "properties": [
            {"key": "purpose", "name": "岗位意义", "value_type": "STRING"},
            {"key": "headcount", "name": "编制人数", "value_type": "INTEGER"},
        ],
    },
    {
        "key": "person",
        "name": "人员",
        "kind": "OBJECT",
        "interface_keys": ["organizational_subject", "work_bearer"],
    },
    {
        "key": "responsibility",
        "name": "职责",
        "kind": "OBJECT",
        "properties": [
            {"key": "rationale", "name": "为什么需要", "value_type": "STRING"},
            {"key": "accountability", "name": "结果责任", "value_type": "STRING"},
        ],
    },
    {
        "key": "decision_right",
        "name": "决策权",
        "kind": "OBJECT",
        "properties": [
            {"key": "scope", "name": "权限范围", "value_type": "STRING"},
            {"key": "limit", "name": "权限边界", "value_type": "STRING"},
        ],
    },
    {
        "key": "work_activity",
        "name": "工作活动",
        "kind": "OBJECT",
        "properties": [
            {"key": "rationale", "name": "工作意义", "value_type": "STRING"},
            {"key": "quantifiability", "name": "可量化程度", "value_type": "STRING"},
            {"key": "critical", "name": "是否关键工作", "value_type": "BOOLEAN"},
        ],
    },
    {"key": "capability", "name": "能力", "kind": "OBJECT"},
    {
        "key": "knowledge_domain",
        "name": "知识领域",
        "kind": "OBJECT",
        "properties": [
            {"key": "critical", "name": "是否关键知识", "value_type": "BOOLEAN"},
        ],
    },
    {"key": "process", "name": "业务流程", "kind": "OBJECT"},
    {
        "key": "process_step",
        "name": "流程步骤",
        "kind": "OBJECT",
        "properties": [
            {"key": "sequence", "name": "顺序", "value_type": "INTEGER"},
            {"key": "control_purpose", "name": "控制目的", "value_type": "STRING"},
            {"key": "step_kind", "name": "步骤类型", "value_type": "STRING"},
        ],
    },
    {
        "key": "strategy_objective",
        "name": "战略目标",
        "kind": "OBJECT",
        "properties": [
            {"key": "timeframe", "name": "时间范围", "value_type": "STRING"},
            {"key": "priority", "name": "优先级", "value_type": "STRING"},
            {"key": "success_definition", "name": "成功定义", "value_type": "STRING"},
        ],
    },
    {
        "key": "business_outcome",
        "name": "企业结果",
        "kind": "OBJECT",
        "properties": [
            {"key": "definition", "name": "结果定义", "value_type": "STRING"},
            {"key": "timeframe", "name": "时间范围", "value_type": "STRING"},
        ],
    },
    {"key": "business_object", "name": "业务对象", "kind": "OBJECT"},
    {"key": "application", "name": "信息系统", "kind": "OBJECT"},
    {
        "key": "data_asset",
        "name": "数据资产",
        "kind": "OBJECT",
        "interface_keys": ["information_asset"],
    },
    {
        "key": "metric",
        "name": "指标",
        "kind": "METRIC",
        "properties": [
            {"key": "unit", "name": "单位", "value_type": "STRING"},
            {"key": "scope", "name": "指标范围", "value_type": "STRING"},
            {"key": "direction", "name": "改善方向", "value_type": "STRING"},
            {"key": "target_value", "name": "目标值", "value_type": "JSON"},
            {"key": "active", "name": "是否启用", "value_type": "BOOLEAN"},
            {"key": "proxy_risk", "name": "替代指标风险", "value_type": "STRING"},
            {
                "key": "controllable_by_owner",
                "name": "责任人是否可控",
                "value_type": "BOOLEAN",
            },
        ],
    },
    {
        "key": "contains",
        "name": "包含",
        "kind": "RELATION",
        "relation_roles": [
            {"key": "container", "name": "上级", "minimum": 1, "maximum": 1},
            {"key": "member", "name": "成员", "minimum": 1},
        ],
    },
    {
        "key": "holds_responsibility",
        "name": "承担职责",
        "kind": "RELATION",
        "relation_roles": [
            {
                "key": "accountable",
                "name": "责任主体",
                "allowed_type_keys": ["role", "organization_unit"],
                "minimum": 1,
                "maximum": 1,
            },
            {
                "key": "responsibility",
                "name": "职责",
                "allowed_type_keys": ["responsibility"],
                "minimum": 1,
                "maximum": 1,
            },
        ],
    },
    {
        "key": "holds_authority",
        "name": "拥有权限",
        "kind": "RELATION",
        "relation_roles": [
            {
                "key": "holder",
                "name": "权限主体",
                "allowed_type_keys": ["role", "organization_unit"],
                "minimum": 1,
                "maximum": 1,
            },
            {
                "key": "authority",
                "name": "决策权",
                "allowed_type_keys": ["decision_right"],
                "minimum": 1,
                "maximum": 1,
            },
        ],
    },
    {
        "key": "performs",
        "name": "执行工作",
        "kind": "RELATION",
        "relation_roles": [
            {"key": "performer", "name": "执行者", "minimum": 1},
            {
                "key": "activity",
                "name": "工作",
                "allowed_type_keys": ["work_activity", "process_step"],
                "minimum": 1,
            },
        ],
    },
    {
        "key": "information_flow",
        "name": "信息流",
        "kind": "RELATION",
        "properties": [
            {"key": "content", "name": "信息内容", "value_type": "STRING"},
            {"key": "frequency", "name": "频率", "value_type": "STRING"},
        ],
        "relation_roles": [
            {"key": "sender", "name": "发送方", "minimum": 1},
            {"key": "receiver", "name": "接收方", "minimum": 1},
            {"key": "information", "name": "信息", "minimum": 0, "maximum": 1},
        ],
    },
    {
        "key": "command_flow",
        "name": "指令流",
        "kind": "RELATION",
        "relation_roles": [
            {"key": "issuer", "name": "发出方", "minimum": 1},
            {"key": "receiver", "name": "接收方", "minimum": 1},
        ],
    },
    {
        "key": "money_flow",
        "name": "资金流",
        "kind": "RELATION",
        "relation_roles": [
            {"key": "payer", "name": "付款方", "minimum": 1},
            {"key": "payee", "name": "收款方", "minimum": 1},
        ],
    },
    {
        "key": "material_flow",
        "name": "物流",
        "kind": "RELATION",
        "relation_roles": [
            {"key": "origin", "name": "来源", "minimum": 1},
            {"key": "destination", "name": "目的地", "minimum": 1},
        ],
    },
    {
        "key": "data_flow",
        "name": "数据流",
        "kind": "RELATION",
        "relation_roles": [
            {"key": "source", "name": "数据来源", "minimum": 1},
            {"key": "target", "name": "数据去向", "minimum": 1},
        ],
    },
    {
        "key": "reports_to",
        "name": "汇报关系",
        "kind": "RELATION",
        "relation_roles": [
            {"key": "reporter", "name": "汇报方", "minimum": 1, "maximum": 1},
            {"key": "manager", "name": "管理方", "minimum": 1, "maximum": 1},
        ],
    },
    {
        "key": "supports_strategy",
        "name": "支撑战略",
        "kind": "RELATION",
        "relation_roles": [
            {"key": "subject", "name": "支撑对象", "minimum": 1},
            {
                "key": "objective",
                "name": "战略目标",
                "allowed_type_keys": ["strategy_objective"],
                "minimum": 1,
                "maximum": 1,
            },
        ],
    },
    {
        "key": "measured_by",
        "name": "由指标度量",
        "kind": "RELATION",
        "relation_roles": [
            {"key": "subject", "name": "被度量对象", "minimum": 1},
            {
                "key": "metric",
                "name": "指标",
                "allowed_type_keys": ["metric"],
                "minimum": 1,
            },
        ],
    },
    {
        "key": "conflicts_with",
        "name": "目标冲突",
        "kind": "RELATION",
        "relation_roles": [
            {
                "key": "left",
                "name": "指标一",
                "allowed_type_keys": ["metric"],
                "minimum": 1,
                "maximum": 1,
            },
            {
                "key": "right",
                "name": "指标二",
                "allowed_type_keys": ["metric"],
                "minimum": 1,
                "maximum": 1,
            },
        ],
    },
    {
        "key": "holds_knowledge",
        "name": "掌握知识",
        "kind": "RELATION",
        "relation_roles": [
            {
                "key": "holder",
                "name": "知识持有人",
                "allowed_type_keys": ["person", "role"],
                "minimum": 1,
            },
            {
                "key": "knowledge",
                "name": "知识领域",
                "allowed_type_keys": ["knowledge_domain"],
                "minimum": 1,
                "maximum": 1,
            },
        ],
    },
    {
        "key": "requires_capability",
        "name": "需要能力",
        "kind": "RELATION",
        "relation_roles": [
            {"key": "subject", "name": "需求方", "minimum": 1},
            {
                "key": "capability",
                "name": "能力",
                "allowed_type_keys": ["capability"],
                "minimum": 1,
            },
        ],
    },
    {
        "key": "produces_outcome",
        "name": "产生企业结果",
        "kind": "RELATION",
        "relation_roles": [
            {"key": "producer", "name": "产生方", "minimum": 1},
            {
                "key": "outcome",
                "name": "企业结果",
                "allowed_type_keys": ["business_outcome"],
                "minimum": 1,
            },
        ],
    },
    {
        "key": "governs",
        "name": "权限约束工作",
        "kind": "RELATION",
        "relation_roles": [
            {
                "key": "authority",
                "name": "决策权",
                "allowed_type_keys": ["decision_right"],
                "minimum": 1,
            },
            {"key": "subject", "name": "受约束对象", "minimum": 1},
        ],
    },
)


class OntologyService:
    def __init__(self, session: Session) -> None:
        self.session = session

    def list_types(
        self,
        project_id: UUID,
        *,
        kind: TypeKind | None = None,
    ) -> list[OntologyTypeView]:
        PortfolioService(self.session).require_project(project_id)
        statement = select(OntologyTypeRow).where(OntologyTypeRow.project_id == str(project_id))
        if kind is not None:
            statement = statement.where(OntologyTypeRow.kind == kind.value)
        rows = self.session.scalars(
            statement.order_by(OntologyTypeRow.kind, OntologyTypeRow.key)
        ).all()
        return [self._view(row) for row in rows]

    def create_type(self, project_id: UUID, payload: OntologyTypeCreate) -> OntologyTypeView:
        PortfolioService(self.session).require_project(project_id)
        self._validate_type_references(project_id, payload)
        row = OntologyTypeRow(
            project_id=str(project_id),
            key=payload.key,
            name=payload.name,
            kind=payload.kind.value,
            description=payload.description,
            parent_type_key=payload.parent_type_key,
            interface_keys=payload.interface_keys,
            properties=json_ready([item.model_dump(mode="json") for item in payload.properties]),
            relation_roles=json_ready(
                [item.model_dump(mode="json") for item in payload.relation_roles]
            ),
            action_parameters=json_ready(
                [item.model_dump(mode="json") for item in payload.action_parameters]
            ),
            extra_metadata=json_ready(payload.metadata),
        )
        self.session.add(row)
        try:
            self.session.flush()
        except IntegrityError as exc:
            raise DomainError(
                "ONTOLOGY_TYPE_KEY_CONFLICT",
                f"本体类型键 {payload.key} 已存在。",
                status_code=409,
            ) from exc
        return self._view(row)

    def type(self, project_id: UUID, type_id: UUID) -> OntologyTypeView:
        return self._view(self.require_type_by_id(project_id, type_id))

    def update_type(
        self,
        project_id: UUID,
        type_id: UUID,
        payload: OntologyTypeUpdate,
    ) -> OntologyTypeView:
        row = self.require_type_by_id(project_id, type_id)
        require_revision(row.revision, payload.expected_revision, resource="本体类型")
        changes = payload.model_dump(exclude_unset=True, exclude={"expected_revision"})
        for key, value in changes.items():
            if key == "metadata":
                row.extra_metadata = json_ready(value)
            elif key in {"properties", "relation_roles", "action_parameters"}:
                setattr(row, key, json_ready(value))
            else:
                setattr(row, key, json_ready(value))
        candidate = OntologyTypeCreate.model_validate(
            {
                "key": row.key,
                "name": row.name,
                "kind": TypeKind(row.kind),
                "description": row.description,
                "parent_type_key": row.parent_type_key,
                "interface_keys": row.interface_keys,
                "properties": row.properties,
                "relation_roles": row.relation_roles,
                "action_parameters": row.action_parameters,
                "metadata": row.extra_metadata,
            }
        )
        self._validate_type_references(project_id, candidate, current_type_id=type_id)
        row.revision += 1
        row.status = LifecycleStatus.DRAFT.value
        self.session.flush()
        return self._view(row)

    def install_default_pack(self, project_id: UUID) -> list[OntologyTypeView]:
        PortfolioService(self.session).require_project(project_id)
        existing = {
            item
            for item in self.session.scalars(
                select(OntologyTypeRow.key).where(OntologyTypeRow.project_id == str(project_id))
            ).all()
        }
        for definition in DEFAULT_TYPES:
            if definition["key"] in existing:
                self._merge_default_properties(project_id, definition)
                continue
            payload = OntologyTypeCreate.model_validate(definition)
            self.create_type(project_id, payload)
            existing.add(payload.key)
        # Return the installed pack rather than only rows created in this call.
        # The endpoint is therefore useful and predictable when retried.
        rows = {
            item.key: item
            for item in self.session.scalars(
                select(OntologyTypeRow).where(
                    OntologyTypeRow.project_id == str(project_id),
                    OntologyTypeRow.key.in_([item["key"] for item in DEFAULT_TYPES]),
                )
            ).all()
        }
        return [self._view(rows[item["key"]]) for item in DEFAULT_TYPES]

    def _merge_default_properties(self, project_id: UUID, definition: dict[str, Any]) -> None:
        """Apply additive default-pack upgrades without overwriting project customization."""
        additions = definition.get("properties", [])
        if not additions:
            return
        row = self.require_type_by_key(project_id, definition["key"])
        existing_keys = {item["key"] for item in row.properties}
        missing = [item for item in additions if item["key"] not in existing_keys]
        if not missing:
            return
        row.properties = [*row.properties, *json_ready(missing)]
        row.revision += 1
        row.status = LifecycleStatus.DRAFT.value
        self.session.flush()

    def create_release(
        self,
        project_id: UUID,
        payload: OntologyReleaseCreate,
    ) -> OntologyReleaseView:
        types = self.list_types(project_id)
        if not types:
            raise DomainError("ONTOLOGY_EMPTY", "没有可发布的本体类型。", status_code=409)
        latest = self.session.scalar(
            select(func.max(OntologyReleaseRow.version)).where(
                OntologyReleaseRow.project_id == str(project_id)
            )
        )
        row = OntologyReleaseRow(
            project_id=str(project_id),
            version=(latest or 0) + 1,
            label=payload.label,
            notes=payload.notes,
            snapshot=json_ready([item.model_dump(mode="json") for item in types]),
        )
        self.session.add(row)
        for type_row in self.session.scalars(
            select(OntologyTypeRow).where(OntologyTypeRow.project_id == str(project_id))
        ):
            type_row.status = LifecycleStatus.PUBLISHED.value
        self.session.flush()
        return self._release_view(row)

    def list_releases(self, project_id: UUID) -> list[OntologyReleaseView]:
        PortfolioService(self.session).require_project(project_id)
        rows = self.session.scalars(
            select(OntologyReleaseRow)
            .where(OntologyReleaseRow.project_id == str(project_id))
            .order_by(OntologyReleaseRow.version.desc())
        ).all()
        return [self._release_view(row) for row in rows]

    def require_type_by_key(
        self,
        project_id: UUID | str,
        key: str,
        *,
        kind: TypeKind | None = None,
    ) -> OntologyTypeRow:
        row = self.session.scalar(
            select(OntologyTypeRow).where(
                OntologyTypeRow.project_id == str(project_id), OntologyTypeRow.key == key
            )
        )
        if row is None:
            raise DomainError(
                "ONTOLOGY_TYPE_NOT_FOUND", f"本体类型 {key} 不存在。", status_code=422
            )
        if kind is not None and row.kind != kind.value:
            raise DomainError(
                "ONTOLOGY_TYPE_KIND_MISMATCH",
                f"本体类型 {key} 不是 {kind.value} 类型。",
                status_code=422,
            )
        return row

    def require_type_by_id(self, project_id: UUID, type_id: UUID) -> OntologyTypeRow:
        row = self.session.get(OntologyTypeRow, str(type_id))
        if row is None or row.project_id != str(project_id):
            raise DomainError("ONTOLOGY_TYPE_NOT_FOUND", "本体类型不存在。", status_code=404)
        return row

    def validate_properties(self, type_row: OntologyTypeRow, values: dict[str, Any]) -> None:
        definitions = self.property_definitions(type_row)
        unknown = sorted(set(values) - set(definitions))
        if unknown:
            raise DomainError(
                "UNKNOWN_PROPERTIES",
                "包含本体中未定义的属性。",
                status_code=422,
                details=[{"properties": unknown, "type_key": type_row.key}],
            )
        missing = [
            key
            for key, definition in definitions.items()
            if definition.get("required") and key not in values
        ]
        if missing:
            raise DomainError(
                "REQUIRED_PROPERTIES_MISSING",
                "缺少必填属性。",
                status_code=422,
                details=[{"properties": missing, "type_key": type_row.key}],
            )
        for key, value in values.items():
            self._validate_property_value(key, value, definitions[key])

    def property_definitions(self, type_row: OntologyTypeRow) -> dict[str, dict[str, Any]]:
        definitions: dict[str, dict[str, Any]] = {}
        for current in reversed(self.type_lineage(type_row)):
            for item in current.properties:
                inherited = definitions.get(item["key"])
                merged = dict(item)
                if inherited is not None:
                    merged["required"] = bool(inherited.get("required") or item.get("required"))
                definitions[item["key"]] = merged
        return definitions

    def type_lineage(self, type_row: OntologyTypeRow) -> list[OntologyTypeRow]:
        lineage: list[OntologyTypeRow] = []
        visited: set[str] = set()
        current: OntologyTypeRow | None = type_row
        while current is not None:
            if current.key in visited:
                raise DomainError(
                    "ONTOLOGY_INHERITANCE_CYCLE",
                    "本体类型继承关系存在循环。",
                    status_code=422,
                    details=[{"type_key": current.key}],
                )
            visited.add(current.key)
            lineage.append(current)
            current = (
                self.require_type_by_key(current.project_id, current.parent_type_key)
                if current.parent_type_key
                else None
            )
        return lineage

    def entity_type_satisfies(self, type_row: OntologyTypeRow, allowed: set[str]) -> bool:
        for current in self.type_lineage(type_row):
            if current.key in allowed or set(current.interface_keys) & allowed:
                return True
        return False

    def _validate_type_references(
        self,
        project_id: UUID,
        payload: OntologyTypeCreate,
        *,
        current_type_id: UUID | None = None,
    ) -> None:
        keys = [item.key for item in payload.properties]
        if len(keys) != len(set(keys)):
            raise DomainError("DUPLICATE_PROPERTY_KEY", "属性键不能重复。", status_code=422)
        role_keys = [item.key for item in payload.relation_roles]
        if len(role_keys) != len(set(role_keys)):
            raise DomainError("DUPLICATE_ROLE_KEY", "关系参与角色键不能重复。", status_code=422)
        if payload.parent_type_key:
            parent = self.require_type_by_key(project_id, payload.parent_type_key)
            if current_type_id is not None and parent.id == str(current_type_id):
                raise DomainError("ONTOLOGY_SELF_PARENT", "类型不能继承自身。", status_code=422)
            if current_type_id is not None:
                for ancestor in self.type_lineage(parent):
                    if ancestor.id == str(current_type_id):
                        raise DomainError(
                            "ONTOLOGY_INHERITANCE_CYCLE",
                            "本体类型继承关系不能形成循环。",
                            status_code=422,
                        )
            inherited = self.property_definitions(parent)
            for item in payload.properties:
                previous = inherited.get(item.key)
                if previous is None:
                    continue
                if (
                    previous.get("value_type") != item.value_type.value
                    or bool(previous.get("multiple")) != item.multiple
                ):
                    raise DomainError(
                        "INCOMPATIBLE_INHERITED_PROPERTY",
                        f"属性 {item.key} 与父类型定义不兼容。",
                        status_code=422,
                    )
        for key in payload.interface_keys:
            self.require_type_by_key(project_id, key, kind=TypeKind.INTERFACE)
        for role in payload.relation_roles:
            for key in role.allowed_type_keys:
                self.require_type_by_key(project_id, key)

    @staticmethod
    def _validate_property_value(key: str, value: Any, definition: dict[str, Any]) -> None:
        if value is None:
            if definition.get("required"):
                raise DomainError(
                    "PROPERTY_VALUE_REQUIRED", f"属性 {key} 不能为空。", status_code=422
                )
            return
        if definition.get("multiple"):
            if not isinstance(value, list):
                raise DomainError(
                    "PROPERTY_TYPE_MISMATCH", f"属性 {key} 必须是数组。", status_code=422
                )
            candidates = value
        else:
            candidates = [value]
        expected = definition["value_type"]

        def valid_date(item: Any) -> bool:
            if not isinstance(item, str):
                return False
            try:
                date.fromisoformat(item)
            except ValueError:
                return False
            return True

        def valid_datetime(item: Any) -> bool:
            if not isinstance(item, str):
                return False
            try:
                datetime.fromisoformat(item.replace("Z", "+00:00"))
            except ValueError:
                return False
            return True

        def valid_uuid(item: Any) -> bool:
            if not isinstance(item, str):
                return False
            try:
                UUID(item)
            except (ValueError, AttributeError):
                return False
            return True

        validators: dict[str, Callable[[Any], bool]] = {
            "STRING": lambda item: isinstance(item, str),
            "INTEGER": lambda item: isinstance(item, int) and not isinstance(item, bool),
            "NUMBER": lambda item: isinstance(item, (int, float)) and not isinstance(item, bool),
            "BOOLEAN": lambda item: isinstance(item, bool),
            "DATE": valid_date,
            "DATETIME": valid_datetime,
            "UUID": valid_uuid,
            "JSON": lambda item: True,
            "STRING_LIST": lambda item: (
                isinstance(item, list) and all(isinstance(entry, str) for entry in item)
            ),
        }
        if not all(validators[expected](item) for item in candidates):
            raise DomainError(
                "PROPERTY_TYPE_MISMATCH",
                f"属性 {key} 不符合 {expected} 类型。",
                status_code=422,
            )

    @staticmethod
    def _view(row: OntologyTypeRow) -> OntologyTypeView:
        return OntologyTypeView.model_validate(
            {
                "id": row.id,
                "project_id": row.project_id,
                "key": row.key,
                "name": row.name,
                "kind": row.kind,
                "description": row.description,
                "parent_type_key": row.parent_type_key,
                "interface_keys": row.interface_keys,
                "properties": row.properties,
                "relation_roles": row.relation_roles,
                "action_parameters": row.action_parameters,
                "metadata": row.extra_metadata,
                "status": row.status,
                "revision": row.revision,
                "created_at": row.created_at,
                "updated_at": row.updated_at,
            }
        )

    @staticmethod
    def _release_view(row: OntologyReleaseRow) -> OntologyReleaseView:
        return OntologyReleaseView.model_validate(
            {
                "id": row.id,
                "project_id": row.project_id,
                "version": row.version,
                "label": row.label,
                "notes": row.notes,
                "type_count": len(row.snapshot),
                "created_at": row.created_at,
            }
        )
