from __future__ import annotations

from typing import Any, cast
from uuid import UUID, uuid4

from sqlalchemy import select, update
from sqlalchemy.exc import IntegrityError
from sqlalchemy.orm import Session

from enterprise_insight_backend.changes import ChangeSetService
from enterprise_insight_backend.collaboration import ExplorationService
from enterprise_insight_backend.errors import DomainError
from enterprise_insight_backend.evidence import EvidenceService
from enterprise_insight_backend.integration import IntegrationService
from enterprise_insight_backend.learning import LearningCaseService
from enterprise_insight_backend.management_intelligence import ManagementIntelligenceService
from enterprise_insight_backend.models import (
    ActionDefinitionRow,
    ActionInvocationRow,
    ActionLogRow,
    ActionObservationRow,
    AgentRunRow,
    MetricDefinitionRow,
)
from enterprise_insight_backend.observation_conflicts import ObservationConflictService
from enterprise_insight_backend.ontology import OntologyService
from enterprise_insight_backend.portfolio import PortfolioService
from enterprise_insight_backend.projection import ProjectionService
from enterprise_insight_backend.query_snapshots import QuerySnapshotService
from enterprise_insight_backend.schemas import (
    ActionApprovalRequest,
    ActionDefinitionCreate,
    ActionDefinitionStatus,
    ActionDefinitionUpdate,
    ActionDefinitionView,
    ActionExecutionMode,
    ActionInvocationCreate,
    ActionInvocationStatus,
    ActionInvocationView,
    ActionLogView,
    ActionObservationCreate,
    ActionObservationKind,
    ActionObservationView,
    ActionRetryRequest,
    ActionRiskLevel,
    CausalHypothesisCreate,
    ChangeSetCreate,
    DesignTradeoffCreate,
    EntityCreate,
    GraphNeighborhoodReadAction,
    GraphQuery,
    GraphView,
    HypothesisCreate,
    InformationRequestCreate,
    LearningCaseDraftFromActionCreate,
    LearningCaseDraftFromScenarioCreate,
    ManagementAnalysisRequest,
    MaterialFragmentsReadAction,
    MeetingRecordCreate,
    MetricObservationCreate,
    ObservationConflictResolve,
    ObservationConflictResolveAction,
    RawBatchMaterializeAction,
    ScenarioCreate,
    SemanticDatasetCreate,
    SemanticDatasetQuery,
    SemanticDatasetQueryAction,
    SemanticMappingAdvanceAction,
    SemanticMappingCommand,
    SemanticMappingCreate,
    SemanticMappingSuggestionRequest,
    SemanticMappingSuggestionsReadAction,
    SourceIdentityBind,
    SourceIdentityBindAction,
)
from enterprise_insight_backend.semantic_datasets import SemanticDatasetService
from enterprise_insight_backend.service_utils import json_ready, now_utc, require_revision
from enterprise_insight_backend.tool_registry import get_tool_spec

DEFAULT_ACTIONS: tuple[dict[str, Any], ...] = (
    {
        "key": "save_hypothesis",
        "name": "保存管理假设",
        "description": "将管理 Agent 的潜在关系或问题假设保存到探索空间，不进入可信图。",
        "parameters": [
            {"key": "type_key", "name": "假设类型", "value_type": "STRING"},
            {"key": "title", "name": "标题", "value_type": "STRING"},
            {"key": "summary", "name": "摘要", "value_type": "STRING"},
        ],
        "effects": [{"kind": "CREATE", "resource": "HYPOTHESIS", "trusted": False}],
        "execution_mode": ActionExecutionMode.INTERNAL.value,
        "risk_level": ActionRiskLevel.LOW.value,
        "require_approval": False,
    },
    {
        "key": "create_scenario",
        "name": "保存管理方案",
        "description": "将管理 Agent 提出的组织或流程方案保存为可比较的情景。",
        "parameters": [
            {"key": "name", "name": "方案名称", "value_type": "STRING"},
            {"key": "goal", "name": "方案目标", "value_type": "STRING"},
        ],
        "effects": [{"kind": "CREATE", "resource": "SCENARIO", "trusted": False}],
        "execution_mode": ActionExecutionMode.INTERNAL.value,
        "risk_level": ActionRiskLevel.LOW.value,
        "require_approval": False,
    },
    {
        "key": "save_causal_hypothesis",
        "name": "保存因果假设",
        "description": "保存原因、结果、作用机制、可证伪预测和干预验证方案。",
        "parameters": [
            {"key": "title", "name": "标题", "value_type": "STRING"},
            {"key": "cause_entity_ids", "name": "原因对象", "value_type": "JSON"},
            {"key": "effect_entity_ids", "name": "结果对象", "value_type": "JSON"},
            {"key": "mechanism", "name": "作用机制", "value_type": "STRING"},
            {"key": "predictions", "name": "可证伪预测", "value_type": "STRING_LIST"},
        ],
        "effects": [{"kind": "CREATE", "resource": "CAUSAL_HYPOTHESIS", "trusted": False}],
        "execution_mode": ActionExecutionMode.INTERNAL.value,
        "risk_level": ActionRiskLevel.LOW.value,
        "require_approval": False,
    },
    {
        "key": "create_entity",
        "name": "创建企业对象",
        "description": "在企业可信投影中创建经过本体校验的对象。",
        "parameters": [
            {"key": "type_key", "name": "对象类型", "value_type": "STRING"},
            {"key": "name", "name": "对象名称", "value_type": "STRING"},
        ],
        "effects": [{"kind": "CREATE", "resource": "ENTITY", "trusted": True}],
        "execution_mode": ActionExecutionMode.INTERNAL.value,
        "risk_level": ActionRiskLevel.MEDIUM.value,
        "require_approval": True,
    },
    {
        "key": "create_relation",
        "name": "创建企业关系",
        "description": "在企业可信投影中创建经过角色、基数和类型校验的关系。",
        "parameters": [
            {"key": "type_key", "name": "关系类型", "value_type": "STRING"},
            {"key": "participants", "name": "关系参与者", "value_type": "JSON"},
        ],
        "effects": [{"kind": "CREATE", "resource": "RELATION", "trusted": True}],
        "execution_mode": ActionExecutionMode.INTERNAL.value,
        "risk_level": ActionRiskLevel.HIGH.value,
        "require_approval": True,
    },
    {
        "key": "apply_change_set",
        "name": "应用已批准变更集",
        "description": "执行已经校验并批准的投影变更集。",
        "parameters": [
            {"key": "change_set_id", "name": "变更集", "value_type": "UUID"},
        ],
        "effects": [{"kind": "APPLY", "resource": "CHANGE_SET", "trusted": True}],
        "execution_mode": ActionExecutionMode.INTERNAL.value,
        "risk_level": ActionRiskLevel.HIGH.value,
        "require_approval": True,
    },
    {
        "key": "apply_projection_changes",
        "name": "原子应用企业投影变更",
        "description": "把对象和关系的新建、修改、退役作为一批校验并原子应用。",
        "parameters": [
            {"key": "title", "name": "变更标题", "value_type": "STRING"},
            {"key": "base_revision", "name": "项目基准修订", "value_type": "INTEGER"},
            {"key": "operations", "name": "变更操作", "value_type": "JSON"},
        ],
        "effects": [{"kind": "APPLY", "resource": "PROJECTION_CHANGE_SET", "trusted": True}],
        "execution_mode": ActionExecutionMode.INTERNAL.value,
        "risk_level": ActionRiskLevel.HIGH.value,
        "require_approval": True,
    },
    {
        "key": "create_semantic_mapping",
        "name": "创建语义映射",
        "description": "把源系统字段映射到本体属性；通过预演和批准后才写入。",
        "parameters": [
            {"key": "source_system_id", "name": "数据源", "value_type": "UUID"},
            {"key": "source_asset", "name": "源数据资产", "value_type": "STRING"},
            {"key": "source_field", "name": "源字段", "value_type": "STRING"},
            {"key": "target_type_key", "name": "目标本体类型", "value_type": "STRING"},
            {"key": "target_property_key", "name": "目标属性", "value_type": "STRING"},
        ],
        "effects": [{"kind": "CREATE", "resource": "SEMANTIC_MAPPING", "trusted": True}],
        "execution_mode": ActionExecutionMode.INTERNAL.value,
        "risk_level": ActionRiskLevel.MEDIUM.value,
        "require_approval": True,
    },
    {
        "key": "advance_semantic_mapping",
        "name": "推进语义映射状态",
        "description": "校验、批准或停用一条语义映射；批准后才可用于物化。",
        "parameters": [
            {"key": "mapping_id", "name": "映射", "value_type": "UUID"},
            {"key": "command", "name": "命令", "value_type": "STRING"},
            {"key": "expected_revision", "name": "预期修订", "value_type": "INTEGER"},
        ],
        "effects": [{"kind": "UPDATE", "resource": "SEMANTIC_MAPPING", "trusted": True}],
        "execution_mode": ActionExecutionMode.INTERNAL.value,
        "risk_level": ActionRiskLevel.MEDIUM.value,
        "require_approval": True,
    },
    {
        "key": "bind_source_identity",
        "name": "绑定来源身份",
        "description": "把源系统记录精确绑定到已建模企业对象。",
        "parameters": [
            {"key": "identity_id", "name": "来源身份", "value_type": "UUID"},
            {"key": "entity_id", "name": "目标对象", "value_type": "UUID"},
            {"key": "expected_revision", "name": "预期修订", "value_type": "INTEGER"},
        ],
        "effects": [{"kind": "UPDATE", "resource": "SOURCE_IDENTITY", "trusted": True}],
        "execution_mode": ActionExecutionMode.INTERNAL.value,
        "risk_level": ActionRiskLevel.HIGH.value,
        "require_approval": True,
    },
    {
        "key": "resolve_observation_conflict",
        "name": "裁决来源数据冲突",
        "description": "选择可信来源、明确覆盖值或保留忽略结论。",
        "parameters": [
            {"key": "conflict_id", "name": "冲突", "value_type": "UUID"},
            {"key": "resolution_kind", "name": "裁决方式", "value_type": "STRING"},
            {"key": "rationale", "name": "裁决依据", "value_type": "STRING"},
            {"key": "expected_revision", "name": "预期修订", "value_type": "INTEGER"},
        ],
        "effects": [{"kind": "UPDATE", "resource": "OBSERVATION_CONFLICT", "trusted": True}],
        "execution_mode": ActionExecutionMode.INTERNAL.value,
        "risk_level": ActionRiskLevel.HIGH.value,
        "require_approval": True,
    },
    {
        "key": "materialize_raw_batch",
        "name": "按批准映射物化原始批次",
        "description": "使用当前已批准映射，将不可变原始批次转换为对象和现实观测。",
        "parameters": [
            {"key": "raw_batch_id", "name": "原始批次", "value_type": "UUID"},
        ],
        "effects": [{"kind": "MATERIALIZE", "resource": "RAW_BATCH", "trusted": True}],
        "execution_mode": ActionExecutionMode.INTERNAL.value,
        "risk_level": ActionRiskLevel.HIGH.value,
        "require_approval": True,
    },
    {
        "key": "create_semantic_dataset",
        "name": "创建类型化语义数据集",
        "description": "定义从本体对象出发的关系路径、字段和显式聚合口径。",
        "parameters": [
            {"key": "key", "name": "数据集标识", "value_type": "STRING"},
            {"key": "name", "name": "数据集名称", "value_type": "STRING"},
            {"key": "root_type_key", "name": "根对象类型", "value_type": "STRING"},
            {"key": "columns", "name": "类型化列", "value_type": "JSON"},
        ],
        "effects": [{"kind": "CREATE", "resource": "SEMANTIC_DATASET", "trusted": True}],
        "execution_mode": ActionExecutionMode.INTERNAL.value,
        "risk_level": ActionRiskLevel.MEDIUM.value,
        "require_approval": True,
    },
    {
        "key": "query_semantic_dataset",
        "name": "查询语义数据集",
        "description": "在固定查询快照上执行类型化关系路径和防重复聚合。",
        "parameters": [
            {"key": "dataset_id", "name": "语义数据集", "value_type": "UUID"},
        ],
        "effects": [{"kind": "READ", "resource": "SEMANTIC_DATASET", "trusted": True}],
        "execution_mode": ActionExecutionMode.INTERNAL.value,
        "risk_level": ActionRiskLevel.LOW.value,
        "require_approval": False,
    },
    {
        "key": "suggest_semantic_mappings",
        "name": "查找语义映射候选",
        "description": "依据来源字段与本体键/标签生成只读候选；不会自动写入或批准。",
        "parameters": [
            {"key": "target_type_key", "name": "目标本体类型", "value_type": "STRING"},
            {"key": "source_system_id", "name": "数据源", "value_type": "UUID", "required": False},
            {"key": "source_asset", "name": "来源资产", "value_type": "STRING", "required": False},
            {
                "key": "include_existing",
                "name": "包含已有映射",
                "value_type": "BOOLEAN",
                "required": False,
            },
        ],
        "effects": [{"kind": "READ", "resource": "SEMANTIC_MAPPING_SUGGESTIONS", "trusted": False}],
        "execution_mode": ActionExecutionMode.INTERNAL.value,
        "risk_level": ActionRiskLevel.LOW.value,
        "require_approval": False,
    },
    {
        "key": "read_material_fragments",
        "name": "按需读取材料片段",
        "description": "按稳定分页读取当前项目的问卷、访谈或报告片段，供 Agent 核对上下文。",
        "parameters": [
            {"key": "source_document_id", "name": "材料", "value_type": "UUID"},
            {
                "key": "offset",
                "name": "起始位置",
                "value_type": "INTEGER",
                "required": False,
            },
            {
                "key": "limit",
                "name": "读取数量",
                "value_type": "INTEGER",
                "required": False,
            },
        ],
        "effects": [{"kind": "READ", "resource": "MATERIAL_FRAGMENTS", "trusted": True}],
        "execution_mode": ActionExecutionMode.INTERNAL.value,
        "risk_level": ActionRiskLevel.LOW.value,
        "require_approval": False,
    },
    {
        "key": "read_graph_neighborhood",
        "name": "按需读取图谱邻域",
        "description": (
            "按深度读取当前模型基线中某个企业对象的局部关系网络，"
            "避免把大型图谱一次性塞进 Agent 上下文。"
        ),
        "parameters": [
            {"key": "root_entity_id", "name": "图谱起点", "value_type": "UUID"},
            {
                "key": "depth",
                "name": "关系深度",
                "value_type": "INTEGER",
                "required": False,
            },
            {
                "key": "include_observations",
                "name": "包含现实观测",
                "value_type": "BOOLEAN",
                "required": False,
            },
            {
                "key": "max_entities",
                "name": "最多对象数",
                "value_type": "INTEGER",
                "required": False,
            },
            {
                "key": "max_relations",
                "name": "最多关系数",
                "value_type": "INTEGER",
                "required": False,
            },
        ],
        "effects": [{"kind": "READ", "resource": "GRAPH_NEIGHBORHOOD", "trusted": True}],
        "execution_mode": ActionExecutionMode.INTERNAL.value,
        "risk_level": ActionRiskLevel.LOW.value,
        "require_approval": False,
    },
    {
        "key": "save_information_request",
        "name": "保存补充信息请求",
        "description": "把无法验证的管理判断转化为明确、可回答的信息请求。",
        "parameters": [
            {"key": "title", "name": "标题", "value_type": "STRING"},
            {"key": "question", "name": "问题", "value_type": "STRING"},
            {"key": "reason", "name": "原因", "value_type": "STRING"},
        ],
        "effects": [{"kind": "CREATE", "resource": "INFORMATION_REQUEST", "trusted": False}],
        "execution_mode": ActionExecutionMode.INTERNAL.value,
        "risk_level": ActionRiskLevel.LOW.value,
        "require_approval": False,
    },
    {
        "key": "record_design_tradeoff",
        "name": "记录设计取舍",
        "description": "记录管理层明确接受的局部代价、整体收益和后续监控指标。",
        "parameters": [
            {"key": "title", "name": "标题", "value_type": "STRING"},
            {"key": "issue_family", "name": "问题族", "value_type": "STRING"},
            {"key": "description", "name": "说明", "value_type": "STRING"},
            {"key": "benefit", "name": "收益", "value_type": "STRING"},
            {"key": "cost", "name": "代价", "value_type": "STRING"},
        ],
        "effects": [{"kind": "CREATE", "resource": "DESIGN_TRADEOFF", "trusted": False}],
        "execution_mode": ActionExecutionMode.INTERNAL.value,
        "risk_level": ActionRiskLevel.MEDIUM.value,
        "require_approval": True,
    },
    {
        "key": "record_meeting_observation",
        "name": "记录会议现实结果",
        "description": "把会议议题、决策、行动项和升级事项保存为可分析的结果侧记录。",
        "parameters": [
            {"key": "title", "name": "会议名称", "value_type": "STRING"},
            {"key": "occurred_at", "name": "发生时间", "value_type": "DATETIME"},
        ],
        "effects": [{"kind": "CREATE", "resource": "MEETING_RECORD", "trusted": False}],
        "execution_mode": ActionExecutionMode.INTERNAL.value,
        "risk_level": ActionRiskLevel.MEDIUM.value,
        "require_approval": True,
    },
    {
        "key": "run_management_analysis",
        "name": "运行管理交叉分析",
        "description": "运行确定性设计检查、现实结果检查和双侧交叉验证。",
        "parameters": [],
        "effects": [{"kind": "ANALYZE", "resource": "MANAGEMENT_SIGNAL", "trusted": False}],
        "execution_mode": ActionExecutionMode.INTERNAL.value,
        "risk_level": ActionRiskLevel.LOW.value,
        "require_approval": False,
    },
    {
        "key": "draft_learning_case",
        "name": "从行动结果沉淀案例草稿",
        "description": "引用同一项目中的行动及其真实结果观察，生成可审核案例草稿。",
        "parameters": [
            {"key": "action_invocation_id", "name": "行动", "value_type": "UUID"},
            {"key": "action_observation_ids", "name": "结果观察", "value_type": "JSON"},
            {"key": "title", "name": "案例标题", "value_type": "STRING"},
            {"key": "challenge", "name": "问题", "value_type": "STRING"},
        ],
        "effects": [{"kind": "CREATE", "resource": "LEARNING_CASE_DRAFT", "trusted": False}],
        "execution_mode": ActionExecutionMode.INTERNAL.value,
        "risk_level": ActionRiskLevel.LOW.value,
        "require_approval": False,
    },
    {
        "key": "draft_scenario_learning_case",
        "name": "从方案沉淀待验证案例",
        "description": "把尚未产生现实结果的方案保存为待验证案例草稿，避免与已验证经验混淆。",
        "parameters": [
            {"key": "scenario_id", "name": "方案", "value_type": "UUID"},
            {"key": "title", "name": "案例标题", "value_type": "STRING"},
            {"key": "challenge", "name": "问题", "value_type": "STRING"},
        ],
        "effects": [{"kind": "CREATE", "resource": "LEARNING_CASE_DRAFT", "trusted": False}],
        "execution_mode": ActionExecutionMode.INTERNAL.value,
        "risk_level": ActionRiskLevel.LOW.value,
        "require_approval": False,
    },
)


class ActionService:
    """Actions are the only execution boundary for Agent-initiated mutations."""

    def __init__(self, session: Session) -> None:
        self.session = session
        self.portfolio = PortfolioService(session)
        self.ontology = OntologyService(session)
        self.projection = ProjectionService(session)

    def ensure_defaults(self, project_id: UUID | str) -> None:
        self.portfolio.require_project(project_id)
        existing = {
            key
            for key in self.session.scalars(
                select(ActionDefinitionRow.key).where(
                    ActionDefinitionRow.project_id == str(project_id)
                )
            ).all()
        }
        for definition in DEFAULT_ACTIONS:
            if definition["key"] in existing:
                continue
            row = ActionDefinitionRow(
                project_id=str(project_id),
                status=ActionDefinitionStatus.VALIDATED.value,
                **definition,
                created_by="system",
            )
            self.session.add(row)
        self.session.flush()

    def list_definitions(self, project_id: UUID) -> list[ActionDefinitionView]:
        self.ensure_defaults(project_id)
        rows = self.session.scalars(
            select(ActionDefinitionRow)
            .where(ActionDefinitionRow.project_id == str(project_id))
            .order_by(ActionDefinitionRow.key)
        ).all()
        return [ActionDefinitionView.model_validate(row) for row in rows]

    def create_definition(
        self, project_id: UUID, payload: ActionDefinitionCreate
    ) -> ActionDefinitionView:
        self.portfolio.require_project(project_id)
        self._validate_definition_payload(project_id, payload)
        row = ActionDefinitionRow(
            project_id=str(project_id),
            key=payload.key,
            name=payload.name,
            description=payload.description,
            target_type_key=payload.target_type_key,
            parameters=json_ready([item.model_dump(mode="json") for item in payload.parameters]),
            preconditions=json_ready(payload.preconditions),
            effects=json_ready(payload.effects),
            execution_mode=payload.execution_mode.value,
            risk_level=payload.risk_level.value,
            require_approval=payload.require_approval,
            enabled=payload.enabled,
            created_by=payload.created_by,
            status=ActionDefinitionStatus.DRAFT.value,
        )
        self.session.add(row)
        try:
            self.session.flush()
        except IntegrityError as exc:
            raise DomainError(
                "ACTION_DEFINITION_KEY_CONFLICT",
                "项目内动作标识不能重复。",
                status_code=409,
            ) from exc
        self._log_definition_event(project_id, row, "CREATED", actor=payload.created_by)
        return ActionDefinitionView.model_validate(row)

    def get_definition(self, project_id: UUID, action_id: UUID) -> ActionDefinitionView:
        return ActionDefinitionView.model_validate(self.require_definition(project_id, action_id))

    def update_definition(
        self,
        project_id: UUID,
        action_id: UUID,
        payload: ActionDefinitionUpdate,
    ) -> ActionDefinitionView:
        row = self.require_definition(project_id, action_id)
        if row.status == ActionDefinitionStatus.RETIRED.value:
            raise DomainError(
                "ACTION_DEFINITION_RETIRED",
                "已退役的动作不能修改。",
                status_code=409,
            )
        require_revision(row.revision, payload.expected_revision, resource="动作定义")
        if row.execution_mode == ActionExecutionMode.CONNECTOR.value or (
            payload.execution_mode == ActionExecutionMode.CONNECTOR
        ):
            raise DomainError(
                "ACTION_CONNECTOR_UNAVAILABLE",
                "当前版本只支持平台内部动作；外部系统动作连接器尚未安装。",
                status_code=422,
                details=[{"supported_execution_mode": ActionExecutionMode.INTERNAL.value}],
            )
        values = payload.model_dump(exclude_unset=True, exclude={"expected_revision"})
        if "target_type_key" in values:
            self._validate_target_type(project_id, values["target_type_key"])
        if "parameters" in values:
            values["parameters"] = json_ready(
                [item.model_dump(mode="json") for item in payload.parameters or []]
            )
        for key, value in values.items():
            setattr(row, key, value.value if hasattr(value, "value") else value)
        row.revision += 1
        row.status = ActionDefinitionStatus.DRAFT.value
        self.session.flush()
        self._log_definition_event(project_id, row, "UPDATED", actor="developer")
        return ActionDefinitionView.model_validate(row)

    def validate_definition(self, project_id: UUID, action_id: UUID) -> ActionDefinitionView:
        row = self.require_definition(project_id, action_id)
        payload = self._definition_payload(row)
        self._validate_definition_payload(project_id, payload)
        if not row.enabled:
            raise DomainError("ACTION_DEFINITION_DISABLED", "动作定义已禁用。", status_code=409)
        old_status = row.status
        row.status = ActionDefinitionStatus.VALIDATED.value
        row.revision += 1
        self.session.flush()
        self._log(
            project_id,
            invocation_id=None,
            event_type="ACTION_DEFINITION_VALIDATED",
            actor="developer",
            from_status=old_status,
            to_status=row.status,
            details={"action_definition_id": row.id},
        )
        return ActionDefinitionView.model_validate(row)

    def list_invocations(
        self, project_id: UUID, *, status: ActionInvocationStatus | None = None
    ) -> list[ActionInvocationView]:
        self.portfolio.require_project(project_id)
        statement = select(ActionInvocationRow).where(
            ActionInvocationRow.project_id == str(project_id)
        )
        if status is not None:
            statement = statement.where(ActionInvocationRow.status == status.value)
        rows = self.session.scalars(statement.order_by(ActionInvocationRow.created_at.desc())).all()
        return [self._invocation_view(row) for row in rows]

    def create_invocation(
        self, project_id: UUID, payload: ActionInvocationCreate
    ) -> ActionInvocationView:
        self.portfolio.require_project(project_id)
        definition = self.require_definition(project_id, payload.action_definition_id)
        if definition.status not in {
            ActionDefinitionStatus.VALIDATED.value,
            ActionDefinitionStatus.PUBLISHED.value,
        }:
            raise DomainError(
                "ACTION_DEFINITION_NOT_READY",
                "动作定义必须先校验通过。",
                status_code=409,
            )
        if not definition.enabled:
            raise DomainError("ACTION_DEFINITION_DISABLED", "动作定义已禁用。", status_code=409)
        if payload.source_agent_run_id is not None:
            self._require_agent_run(project_id, payload.source_agent_run_id)
        key = payload.idempotency_key or str(uuid4())
        existing = self.session.scalar(
            select(ActionInvocationRow).where(
                ActionInvocationRow.project_id == str(project_id),
                ActionInvocationRow.idempotency_key == key,
            )
        )
        if existing is not None:
            if existing.action_definition_id != str(payload.action_definition_id) or (
                existing.input != json_ready(payload.input)
                or existing.target_entity_ids != [str(item) for item in payload.target_entity_ids]
            ):
                raise DomainError(
                    "IDEMPOTENCY_KEY_REUSED",
                    "幂等键已经对应另一组动作输入。",
                    status_code=409,
                )
            return self._invocation_view(existing)
        self._validate_invocation_input(definition, payload.input, payload.target_entity_ids)
        row = ActionInvocationRow(
            project_id=str(project_id),
            action_definition_id=definition.id,
            source_agent_run_id=(
                str(payload.source_agent_run_id) if payload.source_agent_run_id else None
            ),
            idempotency_key=key,
            requested_by=payload.requested_by,
            target_entity_ids=[str(item) for item in payload.target_entity_ids],
            input=json_ready(payload.input),
            status=ActionInvocationStatus.DRAFT.value,
        )
        self.session.add(row)
        try:
            self.session.flush()
        except IntegrityError as exc:
            raise DomainError(
                "ACTION_INVOCATION_IDEMPOTENCY_CONFLICT",
                "动作请求已存在，请刷新后重试。",
                status_code=409,
            ) from exc
        self._log_state(row, "CREATED", actor=payload.requested_by, details={})
        return self._invocation_view(row)

    def get_invocation(self, project_id: UUID, invocation_id: UUID) -> ActionInvocationView:
        return self._invocation_view(self.require_invocation(project_id, invocation_id))

    def dry_run(self, project_id: UUID, invocation_id: UUID) -> ActionInvocationView:
        row = self.require_invocation(project_id, invocation_id)
        if row.status not in {
            ActionInvocationStatus.DRAFT.value,
            ActionInvocationStatus.DRY_RUN_COMPLETED.value,
            ActionInvocationStatus.FAILED.value,
        }:
            raise DomainError(
                "ACTION_NOT_DRY_RUNNABLE",
                "当前动作状态不能重新预演。",
                status_code=409,
            )
        definition = self.session.get(ActionDefinitionRow, row.action_definition_id)
        if definition is None:
            raise DomainError("ACTION_DEFINITION_NOT_FOUND", "动作定义不存在。", status_code=404)
        self._validate_invocation_input(
            definition,
            row.input,
            [UUID(item) for item in row.target_entity_ids],
        )
        preflight = self._preflight(row, definition)
        if not preflight.get("valid", False):
            raise DomainError(
                "ACTION_PREFLIGHT_FAILED",
                "动作预演未通过，不能进入审批或执行。",
                status_code=422,
                details=[{"warnings": preflight.get("warnings", [])}],
            )
        row.preflight = json_ready(preflight)
        row.error = None
        target_status = (
            ActionInvocationStatus.WAITING_APPROVAL.value
            if definition.require_approval
            else ActionInvocationStatus.DRY_RUN_COMPLETED.value
        )
        self._transition(row, target_status, actor=row.requested_by, details=preflight)
        self.session.flush()
        return self._invocation_view(row)

    def approve(
        self, project_id: UUID, invocation_id: UUID, payload: ActionApprovalRequest
    ) -> ActionInvocationView:
        row = self.require_invocation(project_id, invocation_id)
        if row.status != ActionInvocationStatus.WAITING_APPROVAL.value:
            raise DomainError(
                "ACTION_NOT_WAITING_APPROVAL",
                "动作当前不在待审批状态。",
                status_code=409,
            )
        row.approved_by = payload.approved_by
        row.approved_at = now_utc()
        self._transition(
            row,
            ActionInvocationStatus.APPROVED.value,
            actor=payload.approved_by,
            details={},
        )
        self.session.flush()
        return self._invocation_view(row)

    def execute(self, project_id: UUID, invocation_id: UUID) -> ActionInvocationView:
        claimed = self._claim_invocation(project_id, invocation_id)
        if claimed is None:
            row = self.require_invocation(project_id, invocation_id)
            definition = self.session.get(ActionDefinitionRow, row.action_definition_id)
            if definition is None:
                raise DomainError(
                    "ACTION_DEFINITION_NOT_FOUND", "动作定义不存在。", status_code=404
                )
            if row.status in {
                ActionInvocationStatus.SUCCEEDED.value,
                ActionInvocationStatus.EFFECTIVE.value,
                ActionInvocationStatus.ROLLED_BACK.value,
                ActionInvocationStatus.RUNNING.value,
            }:
                # A concurrent caller either observes the completed idempotent
                # result or the in-flight claim. In both cases it must not run
                # the handler a second time.
                return self._invocation_view(row)
            allowed = {ActionInvocationStatus.APPROVED.value}
            if not definition.require_approval:
                allowed.add(ActionInvocationStatus.DRY_RUN_COMPLETED.value)
            if row.status not in allowed:
                raise DomainError(
                    "ACTION_NOT_EXECUTABLE",
                    "动作必须先完成预演，并在需要时获得审批。",
                    status_code=409,
                )
            # The conditional update above is the only path to execution. A
            # row still in an executable state here can only be a stale read;
            # keep the existing contract and ask the caller to retry.
            raise DomainError(
                "ACTION_NOT_EXECUTABLE",
                "动作状态已发生变化，请刷新后重试。",
                status_code=409,
            )

        row, definition = claimed
        try:
            with self.session.begin_nested():
                result = self._execute_handler(project_id, definition, row)
        except DomainError as exc:
            row.error = {"code": exc.code, "message": exc.message, "details": exc.details}
            row.finished_at = now_utc()
            self._transition(
                row,
                ActionInvocationStatus.FAILED.value,
                actor=row.requested_by,
                details=row.error,
            )
        except Exception as exc:  # pragma: no cover - defensive boundary
            row.error = {
                "code": "ACTION_EXECUTION_FAILED",
                "message": "动作执行失败，请查看日志。",
                "details": [{"exception": type(exc).__name__}],
            }
            row.finished_at = now_utc()
            self._transition(
                row,
                ActionInvocationStatus.FAILED.value,
                actor=row.requested_by,
                details=row.error,
            )
        else:
            row.result = json_ready(result)
            row.finished_at = now_utc()
            self._transition(
                row,
                ActionInvocationStatus.SUCCEEDED.value,
                actor=row.requested_by,
                details=row.result or {},
            )
        self.session.flush()
        return self._invocation_view(row)

    def _claim_invocation(
        self, project_id: UUID, invocation_id: UUID
    ) -> tuple[ActionInvocationRow, ActionDefinitionRow] | None:
        """Atomically claim an executable invocation before invoking its handler."""
        timestamp = now_utc()
        claim_attempts = (
            (
                ActionInvocationStatus.APPROVED.value,
                ActionInvocationRow.status == ActionInvocationStatus.APPROVED.value,
            ),
            (
                ActionInvocationStatus.DRY_RUN_COMPLETED.value,
                (
                    ActionInvocationRow.status == ActionInvocationStatus.DRY_RUN_COMPLETED.value
                )
                & ActionInvocationRow.action_definition_id.in_(
                    select(ActionDefinitionRow.id).where(
                        ActionDefinitionRow.require_approval.is_(False)
                    )
                ),
            ),
        )
        for claimed_from, executable_status in claim_attempts:
            claimed_id = self.session.execute(
                update(ActionInvocationRow)
                .where(
                    ActionInvocationRow.id == str(invocation_id),
                    ActionInvocationRow.project_id == str(project_id),
                    executable_status,
                )
                .values(
                    status=ActionInvocationStatus.RUNNING.value,
                    started_at=timestamp,
                )
                .returning(ActionInvocationRow.id)
            ).scalar_one_or_none()
            if claimed_id is None:
                continue

            row = self.require_invocation(project_id, invocation_id)
            definition = self.session.get(ActionDefinitionRow, row.action_definition_id)
            if definition is None:
                raise DomainError(
                    "ACTION_DEFINITION_NOT_FOUND", "动作定义不存在。", status_code=404
                )
            self._log(
                row.project_id,
                invocation_id=row.id,
                event_type="STATE_CHANGED",
                actor=row.requested_by,
                from_status=claimed_from,
                to_status=ActionInvocationStatus.RUNNING.value,
                details={},
            )
            # Make RUNNING visible before the handler performs graph/resource
            # writes, so a concurrent request observes the claim and returns
            # the current invocation instead of entering the handler.
            self.session.commit()
            return row, definition
        return None

    def cancel(self, project_id: UUID, invocation_id: UUID) -> ActionInvocationView:
        row = self.require_invocation(project_id, invocation_id)
        if row.status in {
            ActionInvocationStatus.SUCCEEDED.value,
            ActionInvocationStatus.EFFECTIVE.value,
            ActionInvocationStatus.ROLLED_BACK.value,
        }:
            raise DomainError("ACTION_ALREADY_FINISHED", "已完成的动作不能取消。", status_code=409)
        if row.status == ActionInvocationStatus.RUNNING.value:
            raise DomainError(
                "ACTION_ALREADY_RUNNING", "运行中的动作不能直接取消。", status_code=409
            )
        self._transition(
            row, ActionInvocationStatus.CANCELLED.value, actor=row.requested_by, details={}
        )
        self.session.flush()
        return self._invocation_view(row)

    def retry(
        self, project_id: UUID, invocation_id: UUID, payload: ActionRetryRequest
    ) -> ActionInvocationView:
        row = self.require_invocation(project_id, invocation_id)
        if row.status != ActionInvocationStatus.FAILED.value:
            raise DomainError("ACTION_NOT_RETRYABLE", "只有失败动作可以重试。", status_code=409)
        create = ActionInvocationCreate(
            action_definition_id=UUID(row.action_definition_id),
            input=row.input,
            target_entity_ids=[UUID(item) for item in row.target_entity_ids],
            requested_by=payload.requested_by,
            source_agent_run_id=UUID(row.source_agent_run_id) if row.source_agent_run_id else None,
            idempotency_key=payload.idempotency_key,
        )
        return self.create_invocation(project_id, create)

    def rollback(self, project_id: UUID, invocation_id: UUID) -> ActionInvocationView:
        row = self.require_invocation(project_id, invocation_id)
        if row.status not in {
            ActionInvocationStatus.SUCCEEDED.value,
            ActionInvocationStatus.INEFFECTIVE.value,
        }:
            raise DomainError(
                "ACTION_NOT_ROLLBACKABLE", "当前动作没有可回滚的成功结果。", status_code=409
            )
        definition = self.session.get(ActionDefinitionRow, row.action_definition_id)
        if definition is None:
            raise DomainError("ACTION_DEFINITION_NOT_FOUND", "动作定义不存在。", status_code=404)
        try:
            with self.session.begin_nested():
                result = self._compensate(project_id, definition, row)
        except DomainError:
            raise
        row.result = json_ready({**(row.result or {}), "rollback": result})
        self._transition(
            row, ActionInvocationStatus.ROLLED_BACK.value, actor=row.requested_by, details=result
        )
        self.session.flush()
        return self._invocation_view(row)

    def list_logs(self, project_id: UUID, invocation_id: UUID | None = None) -> list[ActionLogView]:
        self.portfolio.require_project(project_id)
        statement = select(ActionLogRow).where(ActionLogRow.project_id == str(project_id))
        if invocation_id is not None:
            self.require_invocation(project_id, invocation_id)
            statement = statement.where(ActionLogRow.invocation_id == str(invocation_id))
        rows = self.session.scalars(
            statement.order_by(ActionLogRow.created_at, ActionLogRow.id)
        ).all()
        return [ActionLogView.model_validate(row) for row in rows]

    def list_observations(
        self, project_id: UUID, invocation_id: UUID
    ) -> list[ActionObservationView]:
        self.require_invocation(project_id, invocation_id)
        rows = self.session.scalars(
            select(ActionObservationRow)
            .where(ActionObservationRow.project_id == str(project_id))
            .where(ActionObservationRow.invocation_id == str(invocation_id))
            .order_by(ActionObservationRow.observed_at.desc())
        ).all()
        return [ActionObservationView.model_validate(row) for row in rows]

    def add_observation(
        self,
        project_id: UUID,
        invocation_id: UUID,
        payload: ActionObservationCreate,
    ) -> ActionObservationView:
        invocation = self.require_invocation(project_id, invocation_id)
        if invocation.status not in {
            ActionInvocationStatus.SUCCEEDED.value,
            ActionInvocationStatus.OBSERVING.value,
            ActionInvocationStatus.EFFECTIVE.value,
            ActionInvocationStatus.INEFFECTIVE.value,
        }:
            raise DomainError("ACTION_NOT_OBSERVABLE", "动作尚未产生可观察结果。", status_code=409)
        observed_at = payload.observed_at or now_utc()
        metric_definition_id: str | None = None
        metric_observation_id: str | None = None
        observation_period_key = payload.period_key
        if payload.observation_kind == ActionObservationKind.METRIC:
            metric = self.session.scalar(
                select(MetricDefinitionRow).where(
                    MetricDefinitionRow.project_id == str(project_id),
                    MetricDefinitionRow.key == payload.metric_key,
                    MetricDefinitionRow.active.is_(True),
                )
            )
            if metric is None:
                raise DomainError(
                    "ACTION_METRIC_NOT_FOUND",
                    "结果观察引用的指标不存在或已停用。",
                    status_code=422,
                    details=[{"metric_key": payload.metric_key}],
                )
            metric_observation = ManagementIntelligenceService(
                self.session
            ).add_metric_observation(
                project_id,
                UUID(metric.id),
                MetricObservationCreate(
                    period_key=payload.period_key or observed_at.date().isoformat(),
                    dimensions=payload.dimensions,
                    observed_at=observed_at,
                    value=payload.observed_value,
                    source=f"ACTION:{invocation.id}",
                ),
            )
            metric_definition_id = metric.id
            metric_observation_id = str(metric_observation.id)
            observation_period_key = metric_observation.period_key
        observation = ActionObservationRow(
            project_id=str(project_id),
            invocation_id=str(invocation_id),
            observation_kind=payload.observation_kind.value,
            metric_key=payload.metric_key,
            metric_definition_id=metric_definition_id,
            metric_observation_id=metric_observation_id,
            period_key=observation_period_key,
            dimensions=json_ready(payload.dimensions),
            observed_value=json_ready(payload.observed_value),
            outcome=payload.outcome.value,
            note=payload.note,
            observed_at=observed_at,
        )
        self.session.add(observation)
        if payload.outcome.value == "EFFECTIVE":
            self._transition(
                invocation, ActionInvocationStatus.EFFECTIVE.value, actor="management", details={}
            )
        elif payload.outcome.value == "INEFFECTIVE":
            self._transition(
                invocation, ActionInvocationStatus.INEFFECTIVE.value, actor="management", details={}
            )
        elif invocation.status == ActionInvocationStatus.SUCCEEDED.value:
            self._transition(
                invocation, ActionInvocationStatus.OBSERVING.value, actor="management", details={}
            )
        self.session.flush()
        return ActionObservationView.model_validate(observation)

    def require_definition(
        self, project_id: UUID | str, action_id: UUID | str
    ) -> ActionDefinitionRow:
        row = self.session.get(ActionDefinitionRow, str(action_id))
        if row is None or row.project_id != str(project_id):
            raise DomainError("ACTION_DEFINITION_NOT_FOUND", "动作定义不存在。", status_code=404)
        return row

    def require_invocation(
        self, project_id: UUID | str, invocation_id: UUID | str
    ) -> ActionInvocationRow:
        row = self.session.get(ActionInvocationRow, str(invocation_id))
        if row is None or row.project_id != str(project_id):
            raise DomainError("ACTION_INVOCATION_NOT_FOUND", "动作调用不存在。", status_code=404)
        return row

    def _validate_definition_payload(
        self, project_id: UUID, payload: ActionDefinitionCreate
    ) -> None:
        self._validate_target_type(project_id, payload.target_type_key)
        if payload.execution_mode == ActionExecutionMode.CONNECTOR:
            raise DomainError(
                "ACTION_CONNECTOR_UNAVAILABLE",
                "当前版本只支持平台内部动作；外部系统动作连接器尚未安装。",
                status_code=422,
                details=[{"supported_execution_mode": ActionExecutionMode.INTERNAL.value}],
            )
        keys = [item.key for item in payload.parameters]
        if len(keys) != len(set(keys)):
            raise DomainError(
                "ACTION_PARAMETER_DUPLICATE", "动作参数标识不能重复。", status_code=422
            )
        if (
            payload.execution_mode == ActionExecutionMode.INTERNAL
            and get_tool_spec(payload.key) is None
        ):
            raise DomainError(
                "ACTION_HANDLER_UNAVAILABLE",
                "内部动作必须先注册输入契约和执行器。",
                status_code=422,
                details=[{"action_key": payload.key}],
            )

    def _validate_target_type(self, project_id: UUID, type_key: str | None) -> None:
        if type_key is None:
            return
        self.ontology.require_type_by_key(project_id, type_key)

    def _validate_invocation_input(
        self,
        definition: ActionDefinitionRow,
        input_data: dict[str, Any],
        target_entity_ids: list[UUID],
    ) -> None:
        parameters = definition.parameters or []
        known_keys = {item["key"] for item in parameters}
        known_keys.update(self._handled_input_keys(definition.key))
        missing = [
            item["key"]
            for item in parameters
            if item.get("required", True) and item["key"] not in input_data
        ]
        if missing:
            raise DomainError(
                "ACTION_REQUIRED_INPUT_MISSING",
                "动作缺少必填参数。",
                status_code=422,
                details=[{"keys": missing}],
            )
        unknown = sorted(set(input_data) - known_keys)
        if unknown:
            raise DomainError(
                "ACTION_UNKNOWN_INPUT",
                "动作包含未定义参数。",
                status_code=422,
                details=[{"keys": unknown}],
            )
        for item in parameters:
            if item["key"] not in input_data:
                continue
            self._check_value_type(item["key"], input_data[item["key"]], item["value_type"])
        if definition.target_type_key and not target_entity_ids:
            raise DomainError(
                "ACTION_TARGET_REQUIRED", "动作定义要求至少一个目标实体。", status_code=422
            )

    @staticmethod
    def _check_value_type(key: str, value: Any, value_type: str) -> None:
        valid = {
            "STRING": isinstance(value, str),
            "INTEGER": isinstance(value, int) and not isinstance(value, bool),
            "NUMBER": isinstance(value, (int, float)) and not isinstance(value, bool),
            "BOOLEAN": isinstance(value, bool),
            "UUID": isinstance(value, str),
            "JSON": True,
            "DATE": isinstance(value, str),
            "DATETIME": isinstance(value, str),
            "STRING_LIST": isinstance(value, list) and all(isinstance(item, str) for item in value),
        }
        if not valid.get(value_type, True):
            raise DomainError(
                "ACTION_INPUT_TYPE_MISMATCH",
                f"参数 {key} 的类型不符合动作定义。",
                status_code=422,
                details=[{"key": key, "expected": value_type}],
            )

    @staticmethod
    def _handled_input_keys(action_key: str) -> set[str]:
        spec = get_tool_spec(action_key)
        return spec.input_keys if spec is not None else set()

    def _read_graph_neighborhood(
        self, row: ActionInvocationRow, payload: GraphNeighborhoodReadAction
    ) -> GraphView:
        """Read a bounded graph slice using the Agent run's immutable baseline."""
        project_id = UUID(row.project_id)
        query = GraphQuery(
            root_entity_id=payload.root_entity_id,
            depth=payload.depth,
            include_observations=payload.include_observations,
        )
        if row.source_agent_run_id:
            run = self.session.get(AgentRunRow, row.source_agent_run_id)
            manifest = run.context_manifest if run is not None else {}
            snapshot_id = manifest.get("query_snapshot_id")
            if snapshot_id:
                return QuerySnapshotService(self.session).graph(
                    project_id, UUID(str(snapshot_id)), query
                )
        return self.projection.graph(project_id, query)

    def _preflight(
        self, row: ActionInvocationRow, definition: ActionDefinitionRow
    ) -> dict[str, Any]:
        # This dispatcher intentionally validates several different Pydantic
        # payloads.  Keep the branch-local values dynamic instead of letting
        # mypy infer the first branch's concrete payload/service/view type and
        # report false cross-branch assignments.
        payload: Any
        service: Any
        target_ids = [UUID(item) for item in row.target_entity_ids]
        for entity_id in target_ids:
            entity = self.projection.require_entity(row.project_id, entity_id)
            if definition.target_type_key and not self._entity_matches_target(
                entity, definition.target_type_key
            ):
                raise DomainError(
                    "ACTION_TARGET_TYPE_MISMATCH",
                    f"目标实体 {entity.name} 不符合动作目标类型。",
                    status_code=422,
                )
        key = definition.key
        if key == "create_entity":
            payload = EntityCreate.model_validate(row.input)
            self.projection.validate_entity_payload(UUID(row.project_id), payload)
        elif key == "create_relation":
            from enterprise_insight_backend.schemas import RelationCreate

            payload = RelationCreate.model_validate(row.input)
            self.projection.validate_relation_payload(UUID(row.project_id), payload)
        elif key == "save_hypothesis":
            HypothesisCreate.model_validate(row.input)
        elif key == "create_scenario":
            ScenarioCreate.model_validate(row.input)
        elif key == "save_causal_hypothesis":
            payload = CausalHypothesisCreate.model_validate(row.input)
            ExplorationService(self.session).validate_causal_hypothesis(row.project_id, payload)
        elif key == "apply_change_set":
            UUID(str(row.input["change_set_id"]))
        elif key == "apply_projection_changes":
            payload = ChangeSetCreate.model_validate(row.input)
            validation = ChangeSetService(self.session).validate_payload(
                UUID(row.project_id), payload
            )
            return {
                "valid": validation.valid,
                "warnings": [item.message for item in validation.issues],
                "would_change": [
                    {
                        "kind": operation.kind.value,
                        "operation_id": str(operation.operation_id),
                        "target_id": str(operation.target_id) if operation.target_id else None,
                    }
                    for operation in payload.operations
                ],
            }
        elif key == "create_semantic_mapping":
            payload = SemanticMappingCreate.model_validate(row.input)
            service = IntegrationService(self.session)
            service.require_source(row.project_id, payload.source_system_id)
            target = self.ontology.require_type_by_key(row.project_id, payload.target_type_key)
            allowed_properties = {item["key"] for item in target.properties} | {
                "__stable_key__",
                "__name__",
            }
            if payload.target_property_key not in allowed_properties:
                raise DomainError(
                    "MAPPING_TARGET_PROPERTY_NOT_FOUND",
                    "目标本体属性不存在。",
                    status_code=422,
                )
        elif key == "advance_semantic_mapping":
            payload = SemanticMappingAdvanceAction.model_validate(row.input)
            mapping = IntegrationService(self.session).require_mapping(
                UUID(row.project_id), payload.mapping_id
            )
            require_revision(
                mapping.revision, payload.expected_revision, resource="语义映射"
            )
        elif key == "bind_source_identity":
            payload = SourceIdentityBindAction.model_validate(row.input)
            service = IntegrationService(self.session)
            identity = service.require_identity(row.project_id, payload.identity_id)
            require_revision(
                identity.revision, payload.expected_revision, resource="来源身份"
            )
            self.projection.require_entity(row.project_id, payload.entity_id)
        elif key == "resolve_observation_conflict":
            payload = ObservationConflictResolveAction.model_validate(row.input)
            conflict = ObservationConflictService(self.session).require(
                row.project_id, payload.conflict_id
            )
            require_revision(
                conflict.revision, payload.expected_revision, resource="观测冲突"
            )
        elif key == "materialize_raw_batch":
            payload = RawBatchMaterializeAction.model_validate(row.input)
            EvidenceService(self.session).require_raw_batch(
                UUID(row.project_id), payload.raw_batch_id
            )
        elif key == "create_semantic_dataset":
            payload = SemanticDatasetCreate.model_validate(row.input)
            SemanticDatasetService(self.session)._validate(
                UUID(row.project_id), payload.root_type_key, payload.columns
            )
        elif key == "query_semantic_dataset":
            payload = SemanticDatasetQueryAction.model_validate(row.input)
            SemanticDatasetService(self.session).require(
                row.project_id, payload.dataset_id
            )
        elif key == "suggest_semantic_mappings":
            payload = SemanticMappingSuggestionsReadAction.model_validate(row.input)
            IntegrationService(self.session).suggest_mappings(
                UUID(row.project_id),
                SemanticMappingSuggestionRequest.model_validate(payload.model_dump()),
            )
        elif key == "read_material_fragments":
            payload = MaterialFragmentsReadAction.model_validate(row.input)
            total = EvidenceService(self.session).count_fragments(
                UUID(row.project_id), payload.source_document_id
            )
            return {
                "valid": True,
                "warnings": [],
                "would_change": [],
                "available_fragments": total,
                "offset": payload.offset,
                "limit": payload.limit,
            }
        elif key == "read_graph_neighborhood":
            payload = GraphNeighborhoodReadAction.model_validate(row.input)
            graph = self._read_graph_neighborhood(row, payload)
            return {
                "valid": True,
                "warnings": [],
                "would_change": [],
                "root_entity_id": str(payload.root_entity_id),
                "depth": payload.depth,
                "available_entities": len(graph.entities),
                "available_relations": len(graph.relations),
            }
        elif key == "save_information_request":
            payload = InformationRequestCreate.model_validate(row.input)
            ManagementIntelligenceService(self.session).validate_information_request(
                row.project_id, payload
            )
        elif key == "record_design_tradeoff":
            payload = DesignTradeoffCreate.model_validate(row.input)
            ManagementIntelligenceService(self.session).validate_tradeoff(row.project_id, payload)
        elif key == "record_meeting_observation":
            payload = MeetingRecordCreate.model_validate(row.input)
            ManagementIntelligenceService(self.session).validate_meeting(row.project_id, payload)
        elif key == "run_management_analysis":
            ManagementAnalysisRequest.model_validate(row.input)
        elif key == "draft_learning_case":
            payload = LearningCaseDraftFromActionCreate.model_validate(row.input)
            LearningCaseService(self.session).resolve_action_source(
                UUID(row.project_id), payload
            )
        elif key == "draft_scenario_learning_case":
            payload = LearningCaseDraftFromScenarioCreate.model_validate(row.input)
            LearningCaseService(self.session).resolve_scenario_source(
                UUID(row.project_id), payload.scenario_id
            )
        elif definition.execution_mode == ActionExecutionMode.CONNECTOR.value:
            return {
                "valid": False,
                "warnings": ["当前版本只记录连接器动作，尚未安装可执行连接器。"],
                "would_change": definition.effects,
            }
        elif get_tool_spec(key) is None:
            return {
                "valid": False,
                "warnings": ["动作定义没有已注册的执行器。"],
                "would_change": definition.effects,
            }
        return {
            "valid": True,
            "warnings": [],
            "would_change": definition.effects,
            "target_entity_ids": row.target_entity_ids,
        }

    def _execute_handler(
        self,
        project_id: UUID,
        definition: ActionDefinitionRow,
        row: ActionInvocationRow,
    ) -> dict[str, Any]:
        payload: Any
        service: Any
        view: Any
        if definition.execution_mode == ActionExecutionMode.CONNECTOR.value:
            raise DomainError(
                "ACTION_CONNECTOR_UNAVAILABLE",
                "当前版本没有可执行的外部系统连接器。",
                status_code=409,
            )
        key = definition.key
        if key == "save_hypothesis":
            view = ExplorationService(self.session).create_hypothesis(
                project_id, HypothesisCreate.model_validate(row.input), source="AGENT"
            )
            return {"resource": "HYPOTHESIS", "id": str(view.id), "trusted": False}
        if key == "create_scenario":
            view = ExplorationService(self.session).create_scenario(
                project_id, ScenarioCreate.model_validate(row.input)
            )
            return {"resource": "SCENARIO", "id": str(view.id), "trusted": False}
        if key == "save_causal_hypothesis":
            view = ExplorationService(self.session).create_causal_hypothesis(
                project_id,
                CausalHypothesisCreate.model_validate(row.input),
                source="AGENT",
            )
            return {"resource": "CAUSAL_HYPOTHESIS", "id": str(view.id), "trusted": False}
        if key == "create_entity":
            view = self.projection.create_entity(project_id, EntityCreate.model_validate(row.input))
            return {"resource": "ENTITY", "id": str(view.id), "trusted": True}
        if key == "create_relation":
            from enterprise_insight_backend.schemas import RelationCreate

            view = self.projection.create_relation(
                project_id, RelationCreate.model_validate(row.input)
            )
            return {"resource": "RELATION", "id": str(view.id), "trusted": True}
        if key == "apply_change_set":
            change_set_id = UUID(str(row.input["change_set_id"]))
            view = ChangeSetService(self.session).apply(project_id, change_set_id)
            return {"resource": "CHANGE_SET", "id": str(view.id), "status": view.status.value}
        if key == "apply_projection_changes":
            service = ChangeSetService(self.session)
            payload = ChangeSetCreate.model_validate(row.input)
            created = service.create(project_id, payload)
            service.validate(project_id, created.id)
            service.approve(project_id, created.id)
            applied = service.apply(project_id, created.id)
            return {
                "resource": "CHANGE_SET",
                "id": str(applied.id),
                "status": applied.status.value,
                "operation_count": len(applied.operations),
                "trusted": True,
            }
        if key == "create_semantic_mapping":
            view = IntegrationService(self.session).create_mapping(
                project_id, SemanticMappingCreate.model_validate(row.input)
            )
            return {"resource": "SEMANTIC_MAPPING", "id": str(view.id), "trusted": True}
        if key == "advance_semantic_mapping":
            payload = SemanticMappingAdvanceAction.model_validate(row.input)
            service = IntegrationService(self.session)
            command = SemanticMappingCommand(expected_revision=payload.expected_revision)
            if payload.command == "VALIDATE":
                view = service.validate_mapping(project_id, payload.mapping_id, command)
            elif payload.command == "APPROVE":
                view = service.approve_mapping(project_id, payload.mapping_id, command)
            else:
                view = service.disable_mapping(project_id, payload.mapping_id, command)
            return {
                "resource": "SEMANTIC_MAPPING",
                "id": str(view.id),
                "status": view.status.value,
                "revision": view.revision,
                "trusted": True,
            }
        if key == "bind_source_identity":
            payload = SourceIdentityBindAction.model_validate(row.input)
            view = IntegrationService(self.session).bind_identity(
                project_id,
                payload.identity_id,
                SourceIdentityBind(
                    entity_id=payload.entity_id,
                    expected_revision=payload.expected_revision,
                ),
            )
            return {
                "resource": "SOURCE_IDENTITY",
                "id": str(view.id),
                "entity_id": str(view.entity_id),
                "status": view.status.value,
                "trusted": True,
            }
        if key == "resolve_observation_conflict":
            payload = ObservationConflictResolveAction.model_validate(row.input)
            view = ObservationConflictService(self.session).resolve(
                project_id,
                payload.conflict_id,
                ObservationConflictResolve.model_validate(
                    payload.model_dump(exclude={"conflict_id"})
                ),
            )
            return {
                "resource": "OBSERVATION_CONFLICT",
                "id": str(view.id),
                "status": view.status.value,
                "trusted": True,
            }
        if key == "materialize_raw_batch":
            payload = RawBatchMaterializeAction.model_validate(row.input)
            view = EvidenceService(self.session).materialize_raw_batch(
                project_id, payload.raw_batch_id
            )
            return {
                "resource": "MATERIALIZATION_RUN",
                "id": str(view.id),
                "status": view.status,
                "mappings_applied": view.mappings_applied,
                "trusted": True,
            }
        if key == "create_semantic_dataset":
            view = SemanticDatasetService(self.session).create(
                project_id, SemanticDatasetCreate.model_validate(row.input)
            )
            return {
                "resource": "SEMANTIC_DATASET",
                "id": str(view.id),
                "status": view.status,
                "trusted": True,
            }
        if key == "query_semantic_dataset":
            payload = SemanticDatasetQueryAction.model_validate(row.input)
            view = SemanticDatasetService(self.session).query(
                project_id,
                payload.dataset_id,
                SemanticDatasetQuery.model_validate(
                    payload.model_dump(exclude={"dataset_id"})
                ),
            )
            return {
                "resource": "SEMANTIC_QUERY_RUN",
                "id": str(view.run_id),
                "query_snapshot_id": str(view.query_snapshot_id),
                "row_count": len(view.rows),
                "rows": view.rows[:50],
                "warnings": view.warnings,
                "trusted": True,
            }
        if key == "suggest_semantic_mappings":
            payload = SemanticMappingSuggestionsReadAction.model_validate(row.input)
            suggestions = IntegrationService(self.session).suggest_mappings(
                project_id,
                SemanticMappingSuggestionRequest.model_validate(payload.model_dump()),
            )
            return {
                "resource": "SEMANTIC_MAPPING_SUGGESTIONS",
                "target_type_key": payload.target_type_key,
                "total": suggestions.total,
                "items": [item.model_dump(mode="json") for item in suggestions.items],
                "warnings": suggestions.warnings,
                "trusted": False,
            }
        if key == "read_material_fragments":
            payload = MaterialFragmentsReadAction.model_validate(row.input)
            evidence = EvidenceService(self.session)
            total = evidence.count_fragments(project_id, payload.source_document_id)
            fragments = evidence.list_fragments(
                project_id,
                payload.source_document_id,
                offset=payload.offset,
                limit=payload.limit,
            )
            return {
                "resource": "MATERIAL_FRAGMENTS",
                "source_document_id": str(payload.source_document_id),
                "offset": payload.offset,
                "limit": payload.limit,
                "total": total,
                "returned": len(fragments),
                "items": [item.model_dump(mode="json") for item in fragments],
                "trusted": True,
            }
        if key == "read_graph_neighborhood":
            payload = GraphNeighborhoodReadAction.model_validate(row.input)
            graph = self._read_graph_neighborhood(row, payload)
            entity_by_id = {item.id: item for item in graph.entities}
            root = entity_by_id[payload.root_entity_id]
            entities = [root]
            selected_ids = {root.id}
            # Keep the result a useful subgraph under a node cap: select
            # relation neighbors before unrelated entities.
            for relation in graph.relations:
                for participant in relation.participants:
                    candidate = entity_by_id.get(participant.entity_id)
                    if (
                        candidate is not None
                        and candidate.id not in selected_ids
                        and len(entities) < payload.max_entities
                    ):
                        entities.append(candidate)
                        selected_ids.add(candidate.id)
                if len(entities) >= payload.max_entities:
                    break
            if len(entities) < payload.max_entities:
                entities.extend(
                    item
                    for item in graph.entities
                    if item.id not in selected_ids
                    and len(entities) < payload.max_entities
                )
            truncated_entities = len(graph.entities) > len(entities)
            visible_ids = {item.id for item in entities}
            relations = [
                item
                for item in graph.relations
                if all(participant.entity_id in visible_ids for participant in item.participants)
            ]
            truncated_relations = len(relations) > payload.max_relations
            relations = relations[: payload.max_relations]
            return {
                "resource": "GRAPH_NEIGHBORHOOD",
                "root_entity_id": str(payload.root_entity_id),
                "depth": payload.depth,
                "include_observations": payload.include_observations,
                "revision": graph.revision,
                "query_snapshot_id": (
                    str(graph.query_snapshot_id) if graph.query_snapshot_id is not None else None
                ),
                "release_id": str(graph.release_id) if graph.release_id is not None else None,
                "available_entities": len(graph.entities),
                "available_relations": len(graph.relations),
                "returned_entities": len(entities),
                "returned_relations": len(relations),
                "truncated": truncated_entities or truncated_relations,
                "entities": [item.model_dump(mode="json") for item in entities],
                "relations": [item.model_dump(mode="json") for item in relations],
                "trusted": True,
            }
        if key == "save_information_request":
            view = ManagementIntelligenceService(self.session).create_information_request(
                project_id, InformationRequestCreate.model_validate(row.input)
            )
            return {"resource": "INFORMATION_REQUEST", "id": str(view.id), "trusted": False}
        if key == "record_design_tradeoff":
            view = ManagementIntelligenceService(self.session).create_tradeoff(
                project_id, DesignTradeoffCreate.model_validate(row.input)
            )
            return {"resource": "DESIGN_TRADEOFF", "id": str(view.id), "trusted": False}
        if key == "record_meeting_observation":
            view = ManagementIntelligenceService(self.session).create_meeting(
                project_id, MeetingRecordCreate.model_validate(row.input)
            )
            return {"resource": "MEETING_RECORD", "id": str(view.id), "trusted": False}
        if key == "run_management_analysis":
            view = ManagementIntelligenceService(self.session).run_analysis(
                project_id, ManagementAnalysisRequest.model_validate(row.input)
            )
            return {
                "resource": "MANAGEMENT_ANALYSIS_RUN",
                "id": str(view.id),
                "status": view.status.value,
                "trusted": False,
            }
        if key == "draft_learning_case":
            view = LearningCaseService(self.session).create_from_action(
                project_id, LearningCaseDraftFromActionCreate.model_validate(row.input)
            )
            return {
                "resource": "LEARNING_CASE",
                "id": str(view.id),
                "status": view.status.value,
                "source_action_invocation_id": str(view.source_action_invocation_id),
                "trusted": False,
            }
        if key == "draft_scenario_learning_case":
            view = LearningCaseService(self.session).create_from_scenario(
                project_id, LearningCaseDraftFromScenarioCreate.model_validate(row.input)
            )
            return {
                "resource": "LEARNING_CASE",
                "id": str(view.id),
                "status": view.status.value,
                "source_scenario_id": str(view.source_scenario_id),
                "trusted": False,
            }
        raise DomainError(
            "ACTION_HANDLER_UNAVAILABLE",
            "动作定义没有已注册的执行器。",
            status_code=409,
            details=[{"action_key": definition.key}],
        )

    def _compensate(
        self,
        project_id: UUID,
        definition: ActionDefinitionRow,
        row: ActionInvocationRow,
    ) -> dict[str, Any]:
        view: Any
        result = row.result or {}
        resource_id = result.get("id")
        if definition.key == "create_entity" and resource_id:
            entity = self.projection.require_entity(project_id, UUID(resource_id))
            view = self.projection.retire_entity(project_id, UUID(str(entity.id)), entity.revision)
            return {"resource": "ENTITY", "id": str(view.id), "status": "RETIRED"}
        if definition.key == "create_relation" and resource_id:
            relation = self.projection.require_relation(project_id, UUID(resource_id))
            view = self.projection.retire_relation(
                project_id, UUID(str(relation.id)), relation.revision
            )
            return {"resource": "RELATION", "id": str(view.id), "status": "RETIRED"}
        raise DomainError(
            "ACTION_COMPENSATION_UNAVAILABLE",
            "当前动作没有可安全执行的补偿操作。",
            status_code=409,
        )

    def _entity_matches_target(self, entity: Any, target_type_key: str) -> bool:
        if entity.type_key == target_type_key:
            return True
        type_row = self.ontology.require_type_by_key(entity.project_id, entity.type_key)
        return target_type_key in (type_row.interface_keys or [])

    def _definition_payload(self, row: ActionDefinitionRow) -> ActionDefinitionCreate:
        return ActionDefinitionCreate(
            key=row.key,
            name=row.name,
            description=row.description,
            target_type_key=row.target_type_key,
            parameters=cast(Any, row.parameters),
            preconditions=row.preconditions,
            effects=row.effects,
            execution_mode=cast(Any, row.execution_mode),
            risk_level=cast(Any, row.risk_level),
            require_approval=row.require_approval,
            enabled=row.enabled,
            created_by=row.created_by,
        )

    def _invocation_view(self, row: ActionInvocationRow) -> ActionInvocationView:
        definition = self.session.get(ActionDefinitionRow, row.action_definition_id)
        if definition is None:
            raise DomainError("ACTION_DEFINITION_NOT_FOUND", "动作定义不存在。", status_code=404)
        return ActionInvocationView(
            id=UUID(row.id),
            project_id=UUID(row.project_id),
            action_definition_id=UUID(row.action_definition_id),
            action_key=definition.key,
            action_name=definition.name,
            source_agent_run_id=(
                UUID(row.source_agent_run_id) if row.source_agent_run_id else None
            ),
            idempotency_key=row.idempotency_key,
            requested_by=row.requested_by,
            target_entity_ids=[UUID(item) for item in row.target_entity_ids],
            input=row.input,
            status=ActionInvocationStatus(row.status),
            risk_level=ActionRiskLevel(definition.risk_level),
            require_approval=definition.require_approval,
            preflight=row.preflight,
            result=row.result,
            error=row.error,
            approved_by=row.approved_by,
            approved_at=row.approved_at,
            started_at=row.started_at,
            finished_at=row.finished_at,
            created_at=row.created_at,
            updated_at=row.updated_at,
        )

    def _transition(
        self,
        row: ActionInvocationRow,
        to_status: str,
        *,
        actor: str,
        details: dict[str, Any],
    ) -> None:
        old_status = row.status
        row.status = to_status
        self._log(
            row.project_id,
            invocation_id=row.id,
            event_type="STATE_CHANGED",
            actor=actor,
            from_status=old_status,
            to_status=to_status,
            details=details,
        )

    def _log_state(
        self,
        row: ActionInvocationRow,
        event_type: str,
        *,
        actor: str,
        details: dict[str, Any],
    ) -> None:
        self._log(
            row.project_id,
            invocation_id=row.id,
            event_type=event_type,
            actor=actor,
            from_status=None,
            to_status=row.status,
            details=details,
        )

    def _log_definition_event(
        self,
        project_id: UUID,
        row: ActionDefinitionRow,
        event_type: str,
        *,
        actor: str,
    ) -> None:
        self._log(
            project_id,
            invocation_id=None,
            event_type=f"{event_type}_ACTION_DEFINITION",
            actor=actor,
            from_status=None,
            to_status=row.status,
            details={"action_definition_id": row.id},
        )

    def _log(
        self,
        project_id: UUID | str,
        *,
        invocation_id: UUID | str | None,
        event_type: str,
        actor: str,
        from_status: str | None,
        to_status: str | None,
        details: dict[str, Any],
    ) -> None:
        if invocation_id is None:
            return
        self.session.add(
            ActionLogRow(
                project_id=str(project_id),
                invocation_id=str(invocation_id),
                event_type=event_type,
                from_status=from_status,
                to_status=to_status,
                actor=actor,
                details=json_ready(details),
            )
        )

    def _require_agent_run(self, project_id: UUID, run_id: UUID) -> AgentRunRow:
        row = self.session.get(AgentRunRow, str(run_id))
        if row is None or row.project_id != str(project_id):
            raise DomainError("AGENT_RUN_NOT_FOUND", "Agent运行不存在。", status_code=404)
        return row
