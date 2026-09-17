from __future__ import annotations

import re
from math import isfinite
from typing import Any, cast
from uuid import UUID, uuid4

from sqlalchemy import func, or_, select, update
from sqlalchemy.exc import IntegrityError
from sqlalchemy.orm import Session

from enterprise_insight_backend.changes import ChangeSetService
from enterprise_insight_backend.collaboration import ExplorationService
from enterprise_insight_backend.config import Settings
from enterprise_insight_backend.erpnext_task_bridge import (
    ERPNextTaskBridgeClient,
    ERPNextTaskOperation,
    ERPNextTaskOperationReceipt,
    ERPNextTaskOperationStatus,
    ERPNextTaskOutcomeUnknown,
)
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
    EntityRow,
    MetricDefinitionRow,
    ObservationAssertionRow,
    RawRecordValueRow,
    RelationRow,
    ScenarioRow,
    SemanticMappingRow,
    SourceIdentityRow,
)
from enterprise_insight_backend.observation_conflicts import ObservationConflictService
from enterprise_insight_backend.observations import (
    ManagementObservationRow,
    ObservationDatabase,
)
from enterprise_insight_backend.ontology import OntologyService
from enterprise_insight_backend.portfolio import PortfolioService
from enterprise_insight_backend.potential import PotentialDatabase, PotentialRecordService
from enterprise_insight_backend.projection import ProjectionService
from enterprise_insight_backend.query_snapshots import QuerySnapshotService
from enterprise_insight_backend.scenario_engine import ScenarioRunService
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
    EnterpriseSummaryReadAction,
    EntityCreate,
    GraphNeighborhoodReadAction,
    GraphQuery,
    GraphView,
    HypothesisCreate,
    InformationRequestCreate,
    LearningCaseDraftFromActionCreate,
    LearningCaseDraftFromScenarioCreate,
    ManagementAnalysisRequest,
    ManagementObservationSearchAction,
    MaterialFragmentsReadAction,
    MeetingRecordCreate,
    MetricObservationCreate,
    ObservationConflictResolve,
    ObservationConflictResolveAction,
    PotentialRecordsSearchAction,
    RawBatchMaterializeAction,
    ScenarioCreate,
    ScenarioSimulationAction,
    ScenarioSimulationRequest,
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
    SourceObservationsReadAction,
    WorkObservationCompareAction,
    WorkObservationReadAction,
)
from enterprise_insight_backend.semantic_datasets import SemanticDatasetService
from enterprise_insight_backend.service_utils import json_ready, now_utc, require_revision
from enterprise_insight_backend.tool_registry import get_tool_spec
from enterprise_insight_backend.work_observation import WorkObservationService

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
        "key": "run_scenario_simulation",
        "name": "运行情景推演",
        "description": "使用显式输入运行可追溯的确定性情景计算，不修改企业正式数据。",
        "parameters": [
            {"key": "scenario_id", "name": "情景", "value_type": "UUID"},
            {"key": "cases", "name": "基线与事件方案", "value_type": "JSON"},
            {"key": "created_by", "name": "发起人", "value_type": "STRING", "required": False},
        ],
        "effects": [{"kind": "CREATE", "resource": "SCENARIO_RUN", "trusted": False}],
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
        "key": "read_source_observations",
        "name": "按来源标识读取 ERP 观测",
        "description": (
            "只按已导入来源记录标识读取已经物化的现实观测；复杂管理分析可留空标识读取"
            "当前项目的已物化来源观测；不把尚未确认身份的来源记录提升为正式企业事实。"
        ),
        "parameters": [
            {
                "key": "source_record_keys",
                "name": "来源记录标识（复杂分析可留空）",
                "value_type": "JSON",
            },
            {
                "key": "field_keys",
                "name": "字段",
                "value_type": "JSON",
                "required": False,
            },
            {
                "key": "source_assets",
                "name": "数据资产",
                "value_type": "JSON",
                "required": False,
            },
            {"key": "limit", "name": "返回数量", "value_type": "INTEGER", "required": False},
        ],
        "effects": [{"kind": "READ", "resource": "SOURCE_OBSERVATIONS", "trusted": False}],
        "execution_mode": ActionExecutionMode.INTERNAL.value,
        "risk_level": ActionRiskLevel.LOW.value,
        "require_approval": False,
    },
    {
        "key": "search_management_observations",
        "name": "检索管理观察",
        "description": "按项目和关键词分页检索独立的管理观察库；内容始终是未验证线索。",
        "parameters": [
            {"key": "query", "name": "检索关键词", "value_type": "STRING"},
            {"key": "offset", "name": "分页起点", "value_type": "INTEGER", "required": False},
            {"key": "limit", "name": "每页数量", "value_type": "INTEGER", "required": False},
        ],
        "effects": [{"kind": "READ", "resource": "MANAGEMENT_OBSERVATIONS", "trusted": False}],
        "execution_mode": ActionExecutionMode.INTERNAL.value,
        "risk_level": ActionRiskLevel.LOW.value,
        "require_approval": False,
    },
    {
        "key": "search_potential_records",
        "name": "检索潜在记录",
        "description": "按项目检索经人确认但仍待验证的潜在记录，可选择纳入已拒绝或撤回的历史。",
        "parameters": [
            {"key": "query", "name": "检索关键词", "value_type": "STRING"},
            {
                "key": "include_history",
                "name": "包含历史状态",
                "value_type": "BOOLEAN",
                "required": False,
            },
            {"key": "offset", "name": "分页起点", "value_type": "INTEGER", "required": False},
            {"key": "limit", "name": "每页数量", "value_type": "INTEGER", "required": False},
        ],
        "effects": [{"kind": "READ", "resource": "POTENTIAL_RECORDS", "trusted": False}],
        "execution_mode": ActionExecutionMode.INTERNAL.value,
        "risk_level": ActionRiskLevel.LOW.value,
        "require_approval": False,
    },
    {
        "key": "work_observation.read",
        "name": "读取工作观察模式",
        "description": "读取已确认工作观察的活动片段和路径统计；不代表绩效或因果。",
        "parameters": [
            {"key": "analysis_id", "name": "分析", "value_type": "UUID", "required": False},
            {"key": "employee_keys", "name": "员工筛选", "value_type": "JSON", "required": False},
            {
                "key": "include_segments",
                "name": "包含片段",
                "value_type": "BOOLEAN",
                "required": False,
            },
            {"key": "limit", "name": "返回数量", "value_type": "INTEGER", "required": False},
        ],
        "effects": [{"kind": "READ", "resource": "WORK_OBSERVATION", "trusted": False}],
        "execution_mode": ActionExecutionMode.INTERNAL.value,
        "risk_level": ActionRiskLevel.LOW.value,
        "require_approval": False,
    },
    {
        "key": "work_observation.compare",
        "name": "比较工作观察路径",
        "description": "比较两组员工的已观察路径差异；不进行绩效排名和因果推断。",
        "parameters": [
            {"key": "analysis_id", "name": "分析", "value_type": "UUID"},
            {"key": "left_employee_keys", "name": "左侧员工", "value_type": "JSON"},
            {"key": "right_employee_keys", "name": "右侧员工", "value_type": "JSON"},
        ],
        "effects": [{"kind": "READ", "resource": "WORK_OBSERVATION_COMPARISON", "trusted": False}],
        "execution_mode": ActionExecutionMode.INTERNAL.value,
        "risk_level": ActionRiskLevel.LOW.value,
        "require_approval": False,
    },
    {
        "key": "read_enterprise_summary",
        "name": "读取企业概览",
        "description": "只读取当前正式企业投影的对象和关系数量，不读取观察库或潜在库。",
        "parameters": [],
        "effects": [{"kind": "READ", "resource": "ENTERPRISE_SUMMARY", "trusted": True}],
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

_ERPNEXT_TASK_ACTION_PREFIX = "erpnext.task."
_ERPNEXT_TASK_ACTIONS = {
    "erpnext.task.create": "create",
    "erpnext.task.update": "update",
    "erpnext.task.cancel": "cancel",
}
_ERPNEXT_TASK_CREATE_FIELDS = {
    "subject",
    "project",
    "priority",
    "description",
    "exp_start_date",
    "exp_end_date",
    "expected_time",
}
_ERPNEXT_TASK_UPDATE_FIELDS = _ERPNEXT_TASK_CREATE_FIELDS | {"progress", "status"}
_ERPNEXT_TASK_STRING_FIELDS = {
    "subject",
    "project",
    "status",
    "priority",
    "description",
    "exp_start_date",
    "exp_end_date",
    "task_name",
    "expected_modified",
}


class ActionService:
    """Actions are the only execution boundary for Agent-initiated mutations."""

    def __init__(
        self,
        session: Session,
        observation_database: ObservationDatabase | None = None,
        potential_database: PotentialDatabase | None = None,
        settings: Settings | None = None,
    ) -> None:
        self.session = session
        self.portfolio = PortfolioService(session)
        self.ontology = OntologyService(session)
        self.projection = ProjectionService(session)
        self.observation_database = observation_database
        self.potential_database = potential_database
        self.settings = settings

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
        values = payload.model_dump(exclude_unset=True, exclude={"expected_revision"})
        mode = values.get("execution_mode", row.execution_mode)
        mode = mode.value if hasattr(mode, "value") else mode
        self._validate_connector_definition(
            row.key,
            mode,
            values.get("target_type_key", row.target_type_key),
            values.get("parameters", row.parameters),
            values.get("require_approval", row.require_approval),
        )
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
                ActionInvocationStatus.OUTCOME_UNKNOWN.value,
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
        except ERPNextTaskOutcomeUnknown as exc:
            row.error = {
                "code": "ACTION_OUTCOME_UNKNOWN",
                "message": (
                    "ERPNext 可能已提交该动作，但当前无法确认。请先回查操作回执；"
                    "不得重试或取消。"
                ),
                "details": [
                    {
                        "operation_id": str(exc.operation_id),
                        "reason_code": exc.reason_code,
                    }
                ],
            }
            row.finished_at = now_utc()
            self._transition(
                row,
                ActionInvocationStatus.OUTCOME_UNKNOWN.value,
                actor=row.requested_by,
                details=row.error,
            )
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

    def reconcile(
        self, project_id: UUID, invocation_id: UUID
    ) -> ActionInvocationView:
        """Read the remote receipt for an uncertain operation; never replays it."""
        row = self.require_invocation(project_id, invocation_id)
        if row.status not in {
            ActionInvocationStatus.RUNNING.value,
            ActionInvocationStatus.OUTCOME_UNKNOWN.value,
        }:
            raise DomainError(
                "ACTION_NOT_OUTCOME_UNKNOWN",
                "只有运行中或结果不确定的外部动作可以回查。",
                status_code=409,
            )
        definition = self.session.get(ActionDefinitionRow, row.action_definition_id)
        if definition is None:
            raise DomainError("ACTION_DEFINITION_NOT_FOUND", "动作定义不存在。", status_code=404)
        if definition.execution_mode != ActionExecutionMode.CONNECTOR.value:
            raise DomainError(
                "ACTION_RECONCILIATION_UNAVAILABLE",
                "该动作不是 ERPNext 外部连接器动作。",
                status_code=409,
            )
        source_id, operation = self._build_erpnext_task_operation(
            definition,
            row.input,
            operation_id=UUID(row.id),
            target_entity_ids=[UUID(item) for item in row.target_entity_ids],
        )
        profile = IntegrationService(self.session, self.settings).connector_profile(
            project_id, source_id
        )
        receipt = ERPNextTaskBridgeClient(cast(dict[str, Any], profile)).reconcile(
            operation.operation_id
        )
        if receipt.operation_id != operation.operation_id:
            raise DomainError(
                "ERPNEXT_TASK_RECEIPT_ID_MISMATCH",
                "回查回执的 operation_id 与原始动作不一致；状态保持不确定。",
                status_code=409,
            )
        if receipt.status == ERPNextTaskOperationStatus.NOT_FOUND:
            message = (
                "ERPNext 当前未返回该 operation_id 的回执。这不能证明原请求未提交，"
                "系统不会重放；请先在 ERPNext 核实后再处理。"
            )
            row.error = {
                "code": "ACTION_OUTCOME_STILL_UNKNOWN",
                "message": message,
                "details": [
                    {"operation_id": str(operation.operation_id), "replay_allowed": False}
                ],
            }
            self._log(
                row.project_id,
                invocation_id=row.id,
                event_type="REMOTE_RECONCILIATION_NOT_FOUND",
                actor=row.requested_by,
                from_status=row.status,
                to_status=row.status,
                details={"operation_id": str(operation.operation_id), "replay_allowed": False},
            )
            self.session.flush()
            return self._invocation_view(row)
        if receipt.status in {
            ERPNextTaskOperationStatus.IN_PROGRESS,
            ERPNextTaskOperationStatus.UNKNOWN,
        }:
            row.error = {
                "code": "ACTION_OUTCOME_STILL_UNKNOWN",
                "message": "ERPNext 操作仍在处理或未能确认；不得重试或取消。",
                "details": [
                    {
                        "operation_id": str(operation.operation_id),
                        "remote_status": receipt.status.value,
                        "replay_allowed": False,
                    }
                ],
            }
            self._log(
                row.project_id,
                invocation_id=row.id,
                event_type="REMOTE_RECONCILIATION_PENDING",
                actor=row.requested_by,
                from_status=row.status,
                to_status=row.status,
                details={
                    "operation_id": str(operation.operation_id),
                    "remote_status": receipt.status.value,
                },
            )
            self.session.flush()
            return self._invocation_view(row)
        if receipt.payload_sha256 != operation.payload_sha256:
            raise DomainError(
                "ERPNEXT_TASK_RECEIPT_HASH_MISMATCH",
                "回查回执内容哈希与原始请求不一致；状态保持不确定。",
                status_code=409,
            )
        if receipt.status == ERPNextTaskOperationStatus.COMMITTED:
            row.result = self._erpnext_task_result(operation, receipt)
            row.error = None
            row.finished_at = now_utc()
            self._transition(
                row,
                ActionInvocationStatus.SUCCEEDED.value,
                actor=row.requested_by,
                details=row.result,
            )
        elif receipt.status in {
            ERPNextTaskOperationStatus.CONFLICT,
            ERPNextTaskOperationStatus.REJECTED,
            ERPNextTaskOperationStatus.FAILED,
        }:
            row.error = {
                "code": f"ERPNEXT_TASK_{receipt.status.value}",
                "message": receipt.message or "ERPNext 回执确认该动作未提交。",
                "details": [{"operation_id": str(operation.operation_id)}],
            }
            row.finished_at = now_utc()
            self._transition(
                row,
                ActionInvocationStatus.FAILED.value,
                actor=row.requested_by,
                details=row.error,
            )
        else:
            row.error = {
                "code": "ACTION_OUTCOME_STILL_UNKNOWN",
                "message": "ERPNext 回执仍未确认提交结果；不得重试或取消。",
                "details": [
                    {
                        "operation_id": str(operation.operation_id),
                        "remote_status": receipt.status.value,
                        "replay_allowed": False,
                    }
                ],
            }
        self.session.flush()
        return self._invocation_view(row)

    def record_reconciliation_failure(
        self, project_id: UUID, invocation_id: UUID, *, error_code: str
    ) -> None:
        """Audit and throttle a failed background receipt read without changing outcome."""
        row = self.require_invocation(project_id, invocation_id)
        if row.status not in {
            ActionInvocationStatus.RUNNING.value,
            ActionInvocationStatus.OUTCOME_UNKNOWN.value,
        }:
            return
        safe_code = error_code if re.fullmatch(r"[A-Z0-9_]{1,64}", error_code) else "UNEXPECTED"
        row.updated_at = now_utc()
        self._log_state(
            row,
            "REMOTE_RECONCILIATION_FAILED",
            actor="action-recovery-worker",
            details={"error_code": safe_code, "replay_allowed": False},
        )
        self.session.flush()

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
        if row.status == ActionInvocationStatus.OUTCOME_UNKNOWN.value:
            raise DomainError(
                "ACTION_OUTCOME_UNKNOWN_CANNOT_CANCEL",
                "外部动作结果不确定，不能取消或重放；请先回查 ERPNext 回执。",
                status_code=409,
            )
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
        self._validate_connector_definition(
            payload.key,
            payload.execution_mode.value,
            payload.target_type_key,
            [item.model_dump(mode="json") for item in payload.parameters],
            payload.require_approval,
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

    @staticmethod
    def _validate_connector_definition(
        key: str,
        execution_mode: str,
        target_type_key: str | None,
        parameters: list[dict[str, Any]],
        require_approval: bool,
    ) -> None:
        is_connector_key = key in _ERPNEXT_TASK_ACTIONS
        if execution_mode != ActionExecutionMode.CONNECTOR.value:
            if is_connector_key:
                raise DomainError(
                    "ACTION_CONNECTOR_CONFIGURATION_INVALID",
                    "ERPNext Task 固定动作必须使用 CONNECTOR 执行模式。",
                    status_code=422,
                )
            return
        if not is_connector_key:
            raise DomainError(
                "ACTION_CONNECTOR_UNAVAILABLE",
                "只允许注册固定的 erpnext.task.create/update/cancel 连接器动作。",
                status_code=422,
                details=[{"supported_action_keys": sorted(_ERPNEXT_TASK_ACTIONS)}],
            )
        if not require_approval:
            raise DomainError(
                "ACTION_CONNECTOR_APPROVAL_REQUIRED",
                "所有 ERPNext 外部动作都必须经过人工审批。",
                status_code=422,
            )
        if target_type_key is not None or parameters:
            raise DomainError(
                "ACTION_CONNECTOR_CONFIGURATION_INVALID",
                "ERPNext Task 动作使用内置字段白名单，不接受自定义目标类型或参数定义。",
                status_code=422,
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
        if definition.execution_mode == ActionExecutionMode.CONNECTOR.value:
            self._build_erpnext_task_operation(
                definition,
                input_data,
                operation_id=UUID(int=0),
                target_entity_ids=target_entity_ids,
            )
            return
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

    def _build_erpnext_task_operation(
        self,
        definition: ActionDefinitionRow,
        input_data: dict[str, Any],
        *,
        operation_id: UUID,
        target_entity_ids: list[UUID],
    ) -> tuple[UUID, ERPNextTaskOperation]:
        action = _ERPNEXT_TASK_ACTIONS.get(definition.key)
        if (
            definition.execution_mode != ActionExecutionMode.CONNECTOR.value
            or action is None
        ):
            raise DomainError(
                "ACTION_CONNECTOR_UNAVAILABLE",
                "只允许执行固定的 ERPNext Task create/update/cancel 动作。",
                status_code=422,
            )
        if target_entity_ids:
            raise DomainError(
                "ERPNEXT_TASK_TARGETS_UNSUPPORTED",
                "ERPNext Task 动作不能附带任意本体实体目标。",
                status_code=422,
            )

        common = {"source_system_id"}
        if action == "create":
            allowed = common | _ERPNEXT_TASK_CREATE_FIELDS
            required = {"source_system_id", "subject", "project"}
        elif action == "update":
            allowed = common | _ERPNEXT_TASK_UPDATE_FIELDS | {
                "task_name",
                "expected_modified",
            }
            required = {"source_system_id", "task_name", "expected_modified"}
        else:
            allowed = common | {"task_name", "expected_modified"}
            required = allowed

        missing = sorted(required - set(input_data))
        if missing:
            raise DomainError(
                "ERPNEXT_TASK_REQUIRED_FIELDS_MISSING",
                "ERPNext Task 动作缺少必填字段。",
                status_code=422,
                details=[{"fields": missing}],
            )
        unknown = sorted(set(input_data) - allowed)
        if unknown:
            raise DomainError(
                "ERPNEXT_TASK_FIELDS_UNSUPPORTED",
                "ERPNext Task 动作包含未允许的字段。",
                status_code=422,
                details=[{"fields": unknown}],
            )

        try:
            source_id = UUID(str(input_data["source_system_id"]))
        except (ValueError, TypeError, AttributeError) as exc:
            raise DomainError(
                "ERPNEXT_TASK_SOURCE_ID_INVALID",
                "source_system_id 必须是有效的数据源标识。",
                status_code=422,
            ) from exc
        integration = IntegrationService(self.session, self.settings)
        source = integration.require_source(UUID(definition.project_id), source_id)
        if source.kind != "ERP":
            raise DomainError(
                "ACTION_ERP_SOURCE_REQUIRED",
                "ERPNext Task 动作只能使用当前项目内 kind=ERP 的数据源。",
                status_code=422,
                details=[{"source_system_id": str(source_id), "source_kind": source.kind}],
            )

        normalized: dict[str, Any] = {}
        max_lengths = {
            "subject": 140,
            "project": 140,
            "status": 32,
            "priority": 32,
            "description": 5000,
            "exp_start_date": 10,
            "exp_end_date": 10,
            "task_name": 140,
            "expected_modified": 64,
        }
        for key in _ERPNEXT_TASK_STRING_FIELDS:
            if key not in input_data:
                continue
            value = input_data[key]
            if not isinstance(value, str):
                raise DomainError(
                    "ERPNEXT_TASK_FIELD_TYPE_INVALID",
                    f"ERPNext Task 字段 {key} 必须是文本。",
                    status_code=422,
                    details=[{"field": key, "expected": "string"}],
                )
            value = value.strip()
            if key in required and not value:
                raise DomainError(
                    "ERPNEXT_TASK_REQUIRED_FIELDS_MISSING",
                    f"ERPNext Task 字段 {key} 不能为空。",
                    status_code=422,
                    details=[{"fields": [key]}],
                )
            if len(value) > max_lengths[key]:
                raise DomainError(
                    "ERPNEXT_TASK_FIELD_TOO_LONG",
                    f"ERPNext Task 字段 {key} 超出长度限制。",
                    status_code=422,
                    details=[{"field": key, "max_length": max_lengths[key]}],
                )
            normalized[key] = value

        for key in ("expected_time", "progress"):
            if key not in input_data:
                continue
            value = input_data[key]
            if not isinstance(value, (int, float)) or isinstance(value, bool):
                raise DomainError(
                    "ERPNEXT_TASK_FIELD_TYPE_INVALID",
                    f"ERPNext Task 字段 {key} 必须是数字。",
                    status_code=422,
                    details=[{"field": key, "expected": "number"}],
                )
            if not isfinite(value) or value < 0 or (key == "progress" and value > 100):
                raise DomainError(
                    "ERPNEXT_TASK_FIELD_VALUE_INVALID",
                    f"ERPNext Task 字段 {key} 超出允许范围。",
                    status_code=422,
                    details=[{"field": key}],
                )
            normalized[key] = value

        remote_payload = {
            key: value
            for key, value in normalized.items()
            if key not in {"expected_modified"}
        }
        operation = ERPNextTaskOperation(
            operation_id=operation_id,
            action=action,
            payload=remote_payload,
            expected_modified=normalized.get("expected_modified"),
        )
        return source_id, operation

    @staticmethod
    def _erpnext_task_result(
        operation: ERPNextTaskOperation,
        receipt: ERPNextTaskOperationReceipt,
    ) -> dict[str, Any]:
        return {
            "connector": "erpnext.task",
            "operation_id": str(operation.operation_id),
            "action": operation.action,
            "status": receipt.status.value,
            "payload_sha256": receipt.payload_sha256,
            "task_name": receipt.task_name,
            "task_modified": receipt.task_modified,
            "task": receipt.task,
        }

    @staticmethod
    def _handled_input_keys(action_key: str) -> set[str]:
        spec = get_tool_spec(action_key)
        return spec.input_keys if spec is not None else set()

    @staticmethod
    def _search_terms(query: str) -> list[str]:
        lowered = query.casefold()
        terms = re.findall(r"[a-z0-9_]+", lowered)
        for run in re.findall(r"[\u3400-\u9fff]+", lowered):
            if len(run) <= 3:
                terms.append(run)
            else:
                terms.extend(run[index : index + 2] for index in range(len(run) - 1))
                terms.extend(run[index : index + 3] for index in range(len(run) - 2))
        return list(dict.fromkeys(item for item in terms if item))

    @classmethod
    def _search_score(cls, text: str, query: str) -> int:
        terms = cls._search_terms(query)
        normalized_text = text.casefold()
        compact_query = "".join(query.casefold().split())
        score = 4 if compact_query and compact_query in "".join(normalized_text.split()) else 0
        return score + sum(2 if len(term) > 1 else 1 for term in terms if term in normalized_text)

    def _search_management_observations(
        self, project_id: UUID, payload: ManagementObservationSearchAction
    ) -> dict[str, Any]:
        if self.observation_database is None:
            raise DomainError(
                "OBSERVATION_STORE_UNAVAILABLE",
                "管理观察库当前不可用，检索未执行。",
                status_code=503,
            )
        project = self.portfolio.require_project(project_id)
        scan_limit = 500
        base = select(ManagementObservationRow).where(
            ManagementObservationRow.company_id == project.company_id,
            ManagementObservationRow.project_id == str(project_id),
            ManagementObservationRow.status == "ACTIVE",
        )
        with self.observation_database.session_factory() as observation_session:
            available = observation_session.scalar(
                select(func.count()).select_from(base.subquery())
            ) or 0
            rows = list(
                observation_session.scalars(
                    base.order_by(
                        ManagementObservationRow.created_at.desc(),
                        ManagementObservationRow.id,
                    ).offset(payload.scan_offset).limit(scan_limit)
                ).all()
            )
        scan_truncated = payload.scan_offset + len(rows) < available
        ranked = [
            (index, row, self._search_score(f"{row.title}\n{row.content}", payload.query))
            for index, row in enumerate(rows)
        ]
        matches = [(index, row, score) for index, row, score in ranked if score > 0]
        matches.sort(key=lambda item: (-item[2], item[0]))
        page = matches[payload.offset : payload.offset + payload.limit]
        return {
            "resource": "MANAGEMENT_OBSERVATIONS",
            "query": payload.query,
            "available": available,
            "scan_offset": payload.scan_offset,
            "scanned": len(rows),
            "next_scan_offset": payload.scan_offset + len(rows) if scan_truncated else None,
            "matching_in_scanned": len(matches),
            "returned": len(page),
            "offset": payload.offset,
            "limit": payload.limit,
            "truncated": scan_truncated or len(matches) > payload.offset + len(page),
            "matching_count_is_lower_bound": scan_truncated,
            "ranking_scope": "CURRENT_SCAN_WINDOW",
            "items": [
                {
                    "id": row.id,
                    "kind": row.kind,
                    "title": row.title,
                    "content_excerpt": row.content[:1800],
                    "content_truncated": len(row.content) > 1800,
                    "occurred_at": row.occurred_at,
                    "recorded_at": row.created_at,
                    "submitted_by": row.submitted_by,
                    "revision": row.revision,
                    "trust": "UNVERIFIED_MANAGEMENT_OBSERVATION",
                    "source_store": "management_observations",
                }
                for _, row, _score in page
            ],
            "trust_note": "观察是待核实线索，不能当作正式事实。",
        }

    def _search_potential_records(
        self, project_id: UUID, payload: PotentialRecordsSearchAction
    ) -> dict[str, Any]:
        if self.potential_database is None:
            raise DomainError(
                "POTENTIAL_STORE_UNAVAILABLE",
                "潜在库当前不可用，检索未执行。",
                status_code=503,
            )
        project = self.portfolio.require_project(project_id)
        page = PotentialRecordService(self.potential_database).list_records(
            company_id=UUID(project.company_id),
            project_id=project_id,
            include_history=payload.include_history,
            limit=500,
            offset=payload.scan_offset,
        )
        ranked = [
            (
                index,
                item,
                self._search_score(
                    "\n".join(
                        [item.claim, item.applicability_scope, item.task_source]
                        + [evidence.excerpt for evidence in item.supporting_evidence]
                        + [evidence.excerpt for evidence in item.counterevidence]
                    ),
                    payload.query,
                ),
            )
            for index, item in enumerate(page.items)
        ]
        matches = [(index, item, score) for index, item, score in ranked if score > 0]
        matches.sort(key=lambda item: (-item[2], item[0]))
        selected = matches[payload.offset : payload.offset + payload.limit]
        scan_truncated = payload.scan_offset + len(page.items) < page.total
        return {
            "resource": "POTENTIAL_RECORDS",
            "query": payload.query,
            "include_history": payload.include_history,
            "available": page.total,
            "scan_offset": payload.scan_offset,
            "scanned": len(page.items),
            "next_scan_offset": payload.scan_offset + len(page.items) if scan_truncated else None,
            "matching_in_scanned": len(matches),
            "returned": len(selected),
            "offset": payload.offset,
            "limit": payload.limit,
            "truncated": scan_truncated or len(matches) > payload.offset + len(selected),
            "matching_count_is_lower_bound": scan_truncated,
            "ranking_scope": "CURRENT_SCAN_WINDOW",
            "items": [
                {
                    "id": str(item.id),
                    "potential_type": item.potential_type.value,
                    "claim": item.claim,
                    "applicability_scope": item.applicability_scope,
                    "task_source": item.task_source,
                    "supporting_evidence": [
                        evidence.model_dump(mode="json")
                        for evidence in item.supporting_evidence[:5]
                    ],
                    "counterevidence": [
                        evidence.model_dump(mode="json") for evidence in item.counterevidence[:5]
                    ],
                    "human_status": item.human_status.value,
                    "evidence_status": item.evidence_status.value,
                    "version": item.version,
                    "updated_at": item.updated_at,
                    "trust": "HUMAN_CONFIRMED_UNVERIFIED_POTENTIAL",
                    "source_store": "potential_records",
                }
                for _, item, _score in selected
            ],
            "trust_note": "潜在记录经人确认，但并未因此被证明；核对证据状态和反例。",
        }

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

    def _read_enterprise_summary(
        self, row: ActionInvocationRow, payload: EnterpriseSummaryReadAction
    ) -> dict[str, Any]:
        """Read bounded counts from the formal model for a company-level query."""

        del payload
        project_id = UUID(row.project_id)
        project = self.portfolio.require_project(project_id)
        snapshot_id: UUID | None = None
        if row.source_agent_run_id:
            run = self.session.get(AgentRunRow, row.source_agent_run_id)
            snapshot_value = (run.context_manifest or {}).get("query_snapshot_id") if run else None
            if snapshot_value:
                snapshot_id = UUID(str(snapshot_value))

        if snapshot_id is not None:
            graph = QuerySnapshotService(self.session).graph(
                project_id,
                snapshot_id,
                GraphQuery(include_observations=False),
            )
            entity_counts: dict[str, int] = {}
            for entity in graph.entities:
                entity_counts[entity.type_key] = entity_counts.get(entity.type_key, 0) + 1
            relation_counts: dict[str, int] = {}
            for relation in graph.relations:
                relation_counts[relation.type_key] = relation_counts.get(relation.type_key, 0) + 1
            model_revision = graph.revision
            source = "formal_query_snapshot"
        else:
            entity_rows = self.session.execute(
                select(EntityRow.type_key, func.count(EntityRow.id))
                .where(
                    EntityRow.project_id == str(project_id),
                    EntityRow.status != "RETIRED",
                    EntityRow.design_membership == "MODELED",
                )
                .group_by(EntityRow.type_key)
                .order_by(EntityRow.type_key)
            ).all()
            relation_rows = self.session.execute(
                select(RelationRow.type_key, func.count(RelationRow.id))
                .where(
                    RelationRow.project_id == str(project_id),
                    RelationRow.status != "RETIRED",
                )
                .group_by(RelationRow.type_key)
                .order_by(RelationRow.type_key)
            ).all()
            entity_counts = {str(type_key): int(count) for type_key, count in entity_rows}
            relation_counts = {str(type_key): int(count) for type_key, count in relation_rows}
            model_revision = project.revision
            source = "formal_current_model"

        return {
            "resource": "ENTERPRISE_SUMMARY",
            "company_id": project.company_id,
            "project_id": project.id,
            "model_revision": model_revision,
            "source": source,
            "entity_count": sum(entity_counts.values()),
            "entities_by_type": entity_counts,
            "relation_count": sum(relation_counts.values()),
            "relations_by_type": relation_counts,
            "trusted": True,
            "trust_note": "只统计正式企业投影，不包含管理观察库、潜在库或工作观察。",
        }

    def _read_source_observations(
        self, row: ActionInvocationRow, payload: SourceObservationsReadAction
    ) -> dict[str, Any]:
        """Read materialized source observations without promoting source identities.

        This is intentionally a narrow reader for management queries. It accepts
        exact source record keys and, for cross-table lookups, matches a requested
        key against scalar values in the immutable raw row. It never resolves an
        identity, changes a mapping, or returns a source row as a formal fact.
        """

        project_id = str(row.project_id)
        requested_keys = {
            str(value).strip().casefold()
            for value in payload.source_record_keys
            if str(value).strip()
        }
        requested_fields = [
            str(value).strip()
            for value in payload.field_keys
            if str(value).strip()
        ]
        requested_assets = [
            str(value).strip()
            for value in payload.source_assets
            if str(value).strip()
        ]

        # Push every known predicate into SQL.  The previous implementation
        # loaded every active assertion and then filtered it in Python, so a
        # small exact lookup still cost a project-wide scan.
        assertion_conditions = [
            ObservationAssertionRow.project_id == project_id,
            ObservationAssertionRow.status == "ACTIVE",
        ]
        if requested_assets:
            assertion_conditions.append(ObservationAssertionRow.source_asset.in_(requested_assets))
        if requested_fields:
            assertion_conditions.append(ObservationAssertionRow.field_key.in_(requested_fields))
        if requested_keys:
            related_record_ids = select(RawRecordValueRow.raw_record_id).where(
                RawRecordValueRow.project_id == project_id,
                RawRecordValueRow.value_text.in_(requested_keys),
            )
            assertion_conditions.append(
                or_(
                    ObservationAssertionRow.source_record_key.in_(
                        list(payload.source_record_keys)
                    ),
                    ObservationAssertionRow.raw_record_id.in_(related_record_ids),
                )
            )

        assertion_rows = list(
            self.session.scalars(
                select(ObservationAssertionRow)
                .where(*assertion_conditions)
                .order_by(
                    ObservationAssertionRow.observed_at.desc(),
                    ObservationAssertionRow.created_at.desc(),
                    ObservationAssertionRow.id.desc(),
                )
                .limit(payload.limit + 1)
            ).all()
        )
        truncated = len(assertion_rows) > payload.limit
        assertions = assertion_rows[: payload.limit]
        identity_ids = {
            str(item.source_identity_id)
            for item in assertions
            if item.source_identity_id
        }
        mapping_ids = {
            str(item.semantic_mapping_id)
            for item in assertions
            if item.semantic_mapping_id
        }
        identities = self.session.scalars(
            select(SourceIdentityRow).where(
                SourceIdentityRow.project_id == project_id,
                SourceIdentityRow.id.in_(identity_ids),
            )
        ).all() if identity_ids else []
        identities_by_id = {str(item.id): item for item in identities}
        mappings_by_id = {
            str(item.id): item
            for item in self.session.scalars(
                select(SemanticMappingRow).where(
                    SemanticMappingRow.project_id == project_id,
                    SemanticMappingRow.id.in_(mapping_ids),
                )
            ).all()
        }
        raw_value_by_record: dict[str, set[str]] = {}
        if requested_keys:
            raw_ids = {
                str(item.raw_record_id)
                for item in assertions
                if item.raw_record_id is not None
            }
            if raw_ids:
                for item in self.session.scalars(
                    select(RawRecordValueRow).where(
                        RawRecordValueRow.project_id == project_id,
                        RawRecordValueRow.raw_record_id.in_(raw_ids),
                        RawRecordValueRow.value_text.in_(requested_keys),
                    )
                ).all():
                    raw_value_by_record.setdefault(str(item.raw_record_id), set()).add(
                        item.value_text
                    )

        rows: list[dict[str, Any]] = []
        unresolved_identity_count = 0
        for assertion in assertions:
            assertion_key = str(assertion.source_record_key).strip().casefold()
            match_kind: str | None = None
            matched_key: str | None = None
            if not requested_keys:
                match_kind = "ALL_SOURCE_OBSERVATIONS"
            elif assertion_key in requested_keys:
                match_kind = "SOURCE_RECORD_KEY"
                matched_key = assertion.source_record_key
            elif str(assertion.raw_record_id) in raw_value_by_record:
                match_kind = "RELATED_RECORD_VALUE"
                matched_key = next(
                    (
                        original
                        for original in payload.source_record_keys
                        if str(original).strip().casefold()
                        in raw_value_by_record[str(assertion.raw_record_id)]
                    ),
                    None,
                )
            if match_kind is None:
                continue

            identity = identities_by_id.get(str(assertion.source_identity_id))
            mapping = mappings_by_id.get(str(assertion.semantic_mapping_id))
            identity_status = identity.status if identity is not None else "UNKNOWN"
            mapping_status = mapping.status if mapping is not None else "UNKNOWN"
            trusted = identity_status == "BOUND" and mapping_status == "APPROVED"
            if identity_status != "BOUND":
                unresolved_identity_count += 1
            rows.append(
                {
                    "source_asset": assertion.source_asset,
                    "source_record_key": assertion.source_record_key,
                    "field_key": assertion.field_key,
                    "value": assertion.value,
                    "observed_at": assertion.observed_at,
                    "target_type_key": identity.target_type_key if identity else None,
                    "entity_id": identity.entity_id if trusted and identity else None,
                    "identity_status": identity_status,
                    "mapping_status": mapping_status,
                    "match_kind": match_kind,
                    "matched_source_record_key": matched_key,
                    "trusted": trusted,
                }
            )
            if len(rows) >= payload.limit:
                break

        warnings: list[str] = []
        if unresolved_identity_count:
            warnings.append(
                "部分来源身份尚未绑定到正式企业对象；结果仅作为已物化来源观测。"
            )
        if not rows:
            warnings.append(
                "没有找到与来源记录标识和字段筛选同时匹配的已物化观测。"
                if requested_keys
                else "当前项目没有符合字段筛选的已物化来源观测。"
            )
        return json_ready(
            {
                "resource": "SOURCE_OBSERVATIONS",
                "requested_source_record_keys": list(payload.source_record_keys),
                "requested_field_keys": list(payload.field_keys),
                "requested_source_assets": list(payload.source_assets),
                "rows": rows,
                "returned": len(rows),
                "truncated": truncated,
                "warnings": warnings,
                "trusted": bool(rows) and all(bool(item["trusted"]) for item in rows),
                "trust_note": (
                    "来源观测已物化；只有来源身份已绑定且字段映射已批准时才标记为可核验，"
                    "仍不等同于自动修改后的正式企业模型。"
                ),
            }
        )

    def _preflight(
        self, row: ActionInvocationRow, definition: ActionDefinitionRow
    ) -> dict[str, Any]:
        # This dispatcher intentionally validates several different Pydantic
        # payloads.  Keep the branch-local values dynamic instead of letting
        # mypy infer the first branch's concrete payload/service/view type and
        # report false cross-branch assignments.
        payload: Any
        service: Any
        if definition.execution_mode == ActionExecutionMode.CONNECTOR.value:
            source_id, operation = self._build_erpnext_task_operation(
                definition,
                row.input,
                operation_id=UUID(row.id),
                target_entity_ids=[UUID(item) for item in row.target_entity_ids],
            )
            profile = IntegrationService(self.session, self.settings).connector_profile(
                UUID(row.project_id), source_id
            )
            # Constructor validation is local only; no network/dry-run request is made.
            ERPNextTaskBridgeClient(cast(dict[str, Any], profile))
            return {
                "valid": True,
                "server_side_preview_ran": False,
                "warnings": [
                    "未向 ERPNext 发起远端预演；审批后仅提交一次，结果不确定时必须先回查。"
                ],
                "would_change": {
                    "action": operation.action,
                    "source_system_id": str(source_id),
                    "operation_id": str(operation.operation_id),
                    "payload": operation.payload,
                    "expected_modified": operation.expected_modified,
                    "payload_sha256": operation.payload_sha256,
                },
            }
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
        elif key == "run_scenario_simulation":
            payload = ScenarioSimulationAction.model_validate(row.input)
            self.portfolio.require_project(UUID(row.project_id))
            scenario = self.session.get(ScenarioRow, str(payload.scenario_id))
            if scenario is None or scenario.project_id != row.project_id:
                raise DomainError("SCENARIO_NOT_FOUND", "情景不存在。", status_code=404)
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
        elif key == "read_source_observations":
            payload = SourceObservationsReadAction.model_validate(row.input)
            self.portfolio.require_project(UUID(row.project_id))
            return {
                "valid": True,
                "warnings": [],
                "would_change": [],
                "source_record_keys": payload.source_record_keys,
                "field_keys": payload.field_keys,
                "source_assets": payload.source_assets,
                "limit": payload.limit,
            }
        elif key == "search_management_observations":
            ManagementObservationSearchAction.model_validate(row.input)
            if self.observation_database is None:
                raise DomainError(
                    "OBSERVATION_STORE_UNAVAILABLE",
                    "管理观察库当前不可用，检索未执行。",
                    status_code=503,
                )
        elif key == "search_potential_records":
            PotentialRecordsSearchAction.model_validate(row.input)
            if self.potential_database is None:
                raise DomainError(
                    "POTENTIAL_STORE_UNAVAILABLE",
                    "潜在库当前不可用，检索未执行。",
                    status_code=503,
                )
        elif key == "work_observation.read":
            payload = WorkObservationReadAction.model_validate(row.input)
            if self.observation_database is None:
                raise DomainError(
                    "OBSERVATION_STORE_UNAVAILABLE",
                    "工作观察库当前不可用，读取未执行。",
                    status_code=503,
                )
            if payload.analysis_id is not None:
                with self.observation_database.session_factory() as observation_session:
                    WorkObservationService(observation_session).get_analysis(
                        UUID(row.project_id), payload.analysis_id
                    )
        elif key == "work_observation.compare":
            payload = WorkObservationCompareAction.model_validate(row.input)
            if self.observation_database is None:
                raise DomainError(
                    "OBSERVATION_STORE_UNAVAILABLE",
                    "工作观察库当前不可用，比较未执行。",
                    status_code=503,
                )
            with self.observation_database.session_factory() as observation_session:
                    WorkObservationService(observation_session).get_analysis(
                        UUID(row.project_id), payload.analysis_id
                    )
        elif key == "read_enterprise_summary":
            payload = EnterpriseSummaryReadAction.model_validate(row.input)
            self.portfolio.require_project(UUID(row.project_id))
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
            source_id, operation = self._build_erpnext_task_operation(
                definition,
                row.input,
                operation_id=UUID(row.id),
                target_entity_ids=[UUID(item) for item in row.target_entity_ids],
            )
            profile = IntegrationService(self.session, self.settings).connector_profile(
                project_id, source_id
            )
            receipt = ERPNextTaskBridgeClient(cast(dict[str, Any], profile)).execute(operation)
            if receipt.operation_id != operation.operation_id:
                raise DomainError(
                    "ERPNEXT_TASK_RECEIPT_ID_MISMATCH",
                    "ERPNext 回执的 operation_id 与原始动作不一致。",
                    status_code=409,
                )
            if receipt.payload_sha256 != operation.payload_sha256:
                raise DomainError(
                    "ERPNEXT_TASK_RECEIPT_HASH_MISMATCH",
                    "ERPNext 回执内容哈希与原始请求不一致。",
                    status_code=409,
                )
            if receipt.status == ERPNextTaskOperationStatus.UNKNOWN:
                raise ERPNextTaskOutcomeUnknown(operation.operation_id, "REMOTE_STATUS_UNKNOWN")
            if receipt.status != ERPNextTaskOperationStatus.COMMITTED:
                raise DomainError(
                    f"ERPNEXT_TASK_{receipt.status.value}",
                    receipt.message or "ERPNext 未确认 Task 动作已提交。",
                    status_code=409,
                    details=[{"operation_id": str(operation.operation_id)}],
                )
            return self._erpnext_task_result(operation, receipt)
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
        if key == "run_scenario_simulation":
            payload = ScenarioSimulationAction.model_validate(row.input)
            view = ScenarioRunService(self.session).run(
                project_id,
                payload.scenario_id,
                ScenarioSimulationRequest(cases=payload.cases, created_by=payload.created_by),
            )
            return {
                "resource": "SCENARIO_RUN",
                "id": str(view.id),
                "status": view.status,
                "scenario_id": str(view.scenario_id),
                "result": view.result,
                "rule_snapshot": view.rule_snapshot,
                "trusted": False,
            }
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
                "total_rows": view.total_rows,
                "offset": view.offset,
                "limit": view.limit,
                "returned": len(view.rows),
                "next_offset": view.next_offset,
                "truncated": view.truncated,
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
        if key == "read_source_observations":
            return self._read_source_observations(
                row, SourceObservationsReadAction.model_validate(row.input)
            )
        if key == "search_management_observations":
            payload = ManagementObservationSearchAction.model_validate(row.input)
            return self._search_management_observations(project_id, payload)
        if key == "search_potential_records":
            payload = PotentialRecordsSearchAction.model_validate(row.input)
            return self._search_potential_records(project_id, payload)
        if key == "work_observation.read":
            payload = WorkObservationReadAction.model_validate(row.input)
            if self.observation_database is None:
                raise DomainError(
                    "OBSERVATION_STORE_UNAVAILABLE",
                    "工作观察库当前不可用，读取未执行。",
                    status_code=503,
                )
            with self.observation_database.session_factory() as observation_session:
                service = WorkObservationService(observation_session)
                analysis = (
                    service.get_analysis(project_id, payload.analysis_id)
                    if payload.analysis_id is not None
                    else service.latest_analysis(project_id)
                )
            if analysis is None:
                return {
                    "resource": "WORK_OBSERVATION",
                    "analysis_id": None,
                    "event_count": 0,
                    "employee_count": 0,
                    "employee_paths": {},
                    "segments": [],
                    "trusted": False,
                    "trust_note": "当前项目还没有已完成的工作观察分析。",
                }
            employee_paths = analysis.result.get("employee_paths", {})
            if payload.employee_keys:
                employee_paths = {
                    key: value
                    for key, value in employee_paths.items()
                    if key in payload.employee_keys
                }
            segments = analysis.result.get("segments", []) if payload.include_segments else []
            return {
                "resource": "WORK_OBSERVATION",
                "analysis_id": str(analysis.id),
                "event_count": analysis.event_count,
                "segment_count": analysis.segment_count,
                "employee_count": analysis.employee_count,
                "employee_paths": dict(list(employee_paths.items())[: payload.limit]),
                "segments": segments[: payload.limit],
                "limitations": analysis.result.get("limitations", []),
                "trusted": False,
                "trust_note": "工作观察是独立采集得到的描述性证据，不代表绩效或因果。",
            }
        if key == "work_observation.compare":
            payload = WorkObservationCompareAction.model_validate(row.input)
            if self.observation_database is None:
                raise DomainError(
                    "OBSERVATION_STORE_UNAVAILABLE",
                    "工作观察库当前不可用，比较未执行。",
                    status_code=503,
                )
            project = self.portfolio.require_project(project_id)
            with self.observation_database.session_factory() as observation_session:
                comparison = WorkObservationService(observation_session).compare(project, payload)
            return {
                "resource": "WORK_OBSERVATION_COMPARISON",
                **comparison.model_dump(mode="json"),
                "trusted": False,
                "trust_note": "比较结果只表示已观察路径差异，不表示绩效或因果。",
            }
        if key == "read_enterprise_summary":
            return self._read_enterprise_summary(
                row, EnterpriseSummaryReadAction.model_validate(row.input)
            )
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
