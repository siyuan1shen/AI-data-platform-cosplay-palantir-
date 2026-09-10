# 本体、因果与 Agent 技术蓝图

本文定义从当前“扁平 Fact + 提案 Agent”原型演进到企业决策投影平台的技术路径。

## 1. 目标架构原则

1. 本体是 Agent、分析、因果和应用共享的世界模型；
2. 对象、关系、事件、指标和动作都是一等类型；
3. 类型定义与实例数据分离；
4. 当前状态与历史事件分离；
5. 事实、假设、发现、决策和结果分离；
6. 所有派生结果都能回到来源和转换步骤；
7. Agent 通过工具使用本体，不直接拼 SQL 或修改任意表；
8. 因果图与业务关系图相连，但不能混为同一种边；
9. 相似企业经验是先验，不是目标企业的事实；
10. 优先完成单体内的完整闭环，再考虑拆服务。

## 2. Ontology Language

### 2.1 TypeDefinition

所有类型共享：

```text
type_id
stable_key
name
description
version
status
valid_from / valid_to
schema
constraints
display_config
```

### 2.2 ObjectType

定义企业中的名词：Company、Department、Customer、Product、Order、Equipment、Process、System。

```json
{
  "stable_key": "core.order",
  "name": "订单",
  "primary_key": "order_id",
  "properties": {
    "order_id": {"type": "string", "required": true},
    "amount": {"type": "decimal", "unit": "CNY"},
    "status": {"type": "enum", "enum_type": "core.order_status"}
  }
}
```

### 2.3 LinkType

定义对象之间可存在的关系：

```text
link_type_id
source_object_type_id
target_object_type_id
direction
cardinality
temporal
properties
inverse_name
```

示例：

```text
Department -RESPONSIBLE_FOR→ Process
Customer   -PLACED→ Order
Order      -CONTAINS→ Product
Order      -FULFILLED_BY→ WorkOrder
Equipment  -LOCATED_IN→ ProductionLine
```

### 2.4 EventType

事件描述状态变化：

```text
event_type_id
actor_object_types
subject_object_types
required_properties
occurred_at_field
state_transition
```

事件不等于对象当前状态。`OrderCreated`、`OrderChanged` 和 `OrderCancelled` 保留为时间序列，Order 投影保存当前状态。

### 2.5 MetricType

```text
metric_type_id
name
unit
grain
dimensions
formula
window
aggregation
business_definition
```

指标必须保存计算口径，不允许只保存一个数值。

### 2.6 ActionType

```text
action_type_id
name
target_object_types
input_schema
preconditions
effects
reversible
simulation_handler
execution_handler
outcome_metrics
```

动作既可以是平台内部任务，也可以在后期调用外部业务系统。

### 2.7 Interface

Interface 表达不同对象共享的能力，例如：

```text
Ownable
Locatable
Measurable
Actionable
ProcessParticipant
```

Agent 面向 Interface 编程，可在不同企业对象类型之间复用工具。

## 3. Ontology Instance Store

### 3.1 ObjectInstance

```text
object_id
object_type_id
external_keys
properties
valid_from / valid_to
recorded_at
version
```

### 3.2 LinkInstance

```text
link_id
link_type_id
source_object_id
target_object_id
properties
valid_from / valid_to
recorded_at
version
```

这是当前代码最重要的缺失。没有 LinkInstance 就无法形成企业图，也无法让 Agent 沿关系理解企业。

### 3.3 EventInstance

```text
event_id
event_type_id
subject_object_ids
actor_object_ids
properties
occurred_at
recorded_at
```

### 3.4 MetricObservation

```text
observation_id
metric_type_id
subject_object_ids
dimensions
value
window_start / window_end
observed_at
```

### 3.5 ProjectionVersion

```text
projection_id
company_id
version
as_of
ontology_version
pipeline_versions
status
summary
```

ProjectionVersion 是可重现视图，不复制所有数据；它固定查询所需的类型、转换和时间水位。

## 4. Graph Query

首版不需要引入独立图数据库。可以在 PostgreSQL 或 SQLite 中用规范化 Link 表和递归查询实现，再根据规模决定是否增加图索引。

首批查询能力：

```text
get_object(id)
find_objects(type, filters)
neighbors(object_id, link_type, direction)
traverse(start_ids, path, max_depth)
shortest_path(source_id, target_id, allowed_links)
subgraph(object_ids, depth)
aggregate(object_set, metric, group_by)
events(object_ids, event_types, time_range)
projection(as_of, scope)
```

管理层问题示例：

```text
哪些高价值客户的订单经过当前最拥堵的生产线？
哪些部门共同影响交付周期但没有明确流程负责人？
本月投诉增加与哪些产品、订单和交付事件路径重合？
```

## 5. 数据接入与血缘

### 5.1 Connector

```text
connector_id
connector_type
source_system
extraction_mode
cursor
schedule
schema_discovery
```

首版支持 CSV/Excel 文件连接器，随后增加数据库只读连接器和 API 连接器。

### 5.2 Pipeline

```text
Extract
→ Profile
→ Normalize
→ Map Object
→ Resolve Identity
→ Build Link
→ Build Event
→ Compute Metric
→ Validate
→ Project
```

每个步骤使用持久化 PipelineRun 和 StepRun，不能在 HTTP 请求内全程同步执行。

### 5.3 Lineage

```text
SourceRecord
→ TransformationStep
→ ObjectProperty / Link / Event / MetricObservation
→ Finding / Hypothesis / Decision / Outcome
```

LineageEdge 至少包含：

```text
from_ref
to_ref
relation
pipeline_run_id
program_version
created_at
```

## 6. Decision Knowledge Model

| 类型 | 含义 |
|---|---|
| Statement | 人表达的观点 |
| Observation | 对现场或数据的观察 |
| Fact | 在定义范围和时间内成立的陈述 |
| Hypothesis | 待验证解释 |
| Finding | 对管理有意义的问题或机会 |
| Recommendation | 候选改进方向 |
| Decision | 管理层选择及理由 |
| ActionPlan | 可执行步骤和目标 |
| ActionRun | 一次真实执行 |
| Outcome | 观察到的结果 |
| Learning | 对问题、模型或方案的更新 |

当前 `Fact` 保留为兼容层，但新事实应优先投影到对象属性、Link 或 Event；只有无法自然建模的原子断言才继续使用 Fact。

## 7. Causal Model

### 7.1 核心对象

```text
CausalVariable
CausalEdge
CausalHypothesis
Population
Intervention
OutcomeDefinition
Confounder
ComparisonGroup
Estimand
CausalEstimate
Experiment
Assumption
```

### 7.2 CausalEdge

因果边与业务 Link 分开：

```text
source_variable_id
target_variable_id
direction
mechanism
lag
scope
status
supporting_evidence
opposing_evidence
```

### 7.3 假设生命周期

```text
PROPOSED
→ NEEDS_DATA
→ TESTABLE
→ EVALUATING
→ SUPPORTED / REFUTED / INCONCLUSIVE
→ SUPERSEDED
```

### 7.4 估计方法阶梯

1. 描述性趋势；
2. 前后比较；
3. 匹配对照；
4. 双重差分；
5. 中断时间序列；
6. 工具变量或自然实验；
7. 随机或分阶段试点。

方法注册为 CausalMethod，声明输入要求、假设和诊断检查。Agent 可以选择方法，但估计由确定性统计程序执行。

## 8. Agent Tool System

### 8.1 工具分类

| 工具族 | 示例 |
|---|---|
| Research | list_questions、find_gaps、propose_followup |
| Ontology | inspect_type、propose_type、propose_link_type |
| Graph | neighbors、traverse、subgraph、aggregate |
| Data | profile_source、preview_mapping、run_pipeline |
| Analysis | compare_periods、detect_anomaly、segment_metric |
| Causal | build_dag、check_confounders、estimate_effect |
| Scenario | clone_projection、apply_action、compare_outcome |
| Action | create_plan、request_approval、execute_action |
| Learning | record_outcome、update_playbook、create_eval_case |

### 8.2 Agent Run

```text
Run
 ├─ Goal
 ├─ Context Snapshot
 ├─ Plan
 ├─ Tool Calls
 ├─ Intermediate Artifacts
 ├─ Decision Point
 ├─ Action Request
 ├─ Outcome
 └─ Evaluation
```

Run、Task、ToolCall、PlanStep 和 EvaluationResult 必须持久化。HTTP API 只创建 Run，Worker 执行任务。

### 8.3 Agent 角色

```text
Research Agent     获取认知并发现信息缺口
Modeling Agent     把调研内容转成对象、关系和事件候选
Integration Agent 建议映射和转换管道
Graph Analyst      沿企业关系图分析问题
Causal Analyst     设计因果假设和验证方案
Decision Planner   生成行动选项和情景
Critic Agent       检查证据、反例和约束
Execution Agent    执行已批准 ActionType
Learning Agent     从结果更新问题、模型和 Playbook
```

## 9. Agent 从提案到执行

### 阶段 A：本体感知

Agent 只能通过 Graph、Projection 和 Evidence 工具读取企业，不再把所有内容拼成长文本。

### 阶段 B：计划和模拟

Agent 生成结构化 Plan，调用 Scenario 工具验证行动对对象、指标和约束的影响。

### 阶段 C：内部动作

Agent 可创建追问、分析任务、行动项、指标验证和报告更新。

### 阶段 D：外部动作

ActionType 绑定外部系统写回适配器。首批只支持低风险、可逆动作；结果以 Event 回到投影。

## 10. Evaluation

Evaluation 不再由请求体临时传入，使用持久化资源：

```text
EvalSuite
EvalCase
BaselineRun
CandidateRun
MetricResult
Regression
ReleaseDecision
```

评测层级：

- 数据映射准确率；
- 实体和关系解析准确率；
- 图查询正确性；
- Finding 证据完整度；
- 因果假设质量；
- 行动计划可执行性；
- 实际 Outcome 改善；
- 相似企业方案迁移成功率。

## 11. 持久化飞轮

当前内存注册表应替换为 SQL 持久化聚合和增量计算：

```text
Project Learning Event
→ Candidate Asset
→ Validation Run
→ Asset Version
→ Installation
→ Usage Event
→ Outcome Feedback
→ Aggregate Quality
→ Next Asset Version
```

核心表：

```text
learning_event
asset_candidate
asset_version
asset_dependency
asset_installation
usage_event
outcome_feedback
quality_aggregate
peer_cohort
peer_membership
transfer_trial
```

统计和质量按增量事件更新，不在每次请求时重建所有历史对象。

## 12. 相似企业投影

### 12.1 CompanyFingerprint

```text
industry_features
scale_features
business_model_features
ontology_shape_features
process_features
metric_features
maturity_features
problem_features
```

### 12.2 相似度

```text
Similarity
= w1 × Industry Match
 + w2 × Scale Distance
 + w3 × Process Graph Similarity
 + w4 × Maturity Similarity
 + w5 × Problem Context Similarity
```

权重由具体问题决定。供应链问题不能使用与人力组织问题相同的相似度权重。

### 12.3 Transfer Trial

```text
source_pattern_id
target_company_id
applicability_assessment
adaptation
pilot_action_id
expected_outcome
observed_outcome
transfer_result
```

只有在目标企业完成试点并记录 Outcome 后，才更新该模式的跨企业质量。

## 13. 当前代码迁移映射

| 当前能力 | 新位置 | 处理 |
|---|---|---|
| Company / Workspace | Company / ProjectionProject | 兼容迁移 |
| CanonicalEntity | ObjectInstance | 扩展类型定义 |
| Alias / Identifier | Identity Resolution | 保留 |
| Fact | ObjectProperty / Link / Event / Fact | 分流迁移 |
| Mapping | MappingProgram | 扩展对象、关系和事件映射 |
| SemanticConflict | Projection Conflict | 保留 |
| Evidence | SourceRecord / Evidence | 保留 |
| Artifact | Agent Artifact | 保留为中间产物 |
| AgentRun | Durable Run / Task / ToolCall | 重构 |
| Finding / Recommendation | Decision Knowledge | 保留并关联图路径 |
| Action | ActionType / ActionPlan / ActionRun | 拆分 |
| Report | Management Brief / Report | 保留 |
| Flywheel Registry | Persistent Learning Store | 重构 |
| Exchange | Optional Deployment Adapter | 冻结，不投入当前主线 |
| Security | Minimal Runtime Infrastructure | 冻结，不作为产品模块 |

## 14. API 资源草案

```text
/ontology/object-types
/ontology/link-types
/ontology/event-types
/ontology/metric-types
/ontology/action-types

/projections
/projections/{id}/objects
/projections/{id}/links
/projections/{id}/events
/projections/{id}/metrics
/projections/{id}/graph/query
/projections/{id}/timeline

/sources
/connectors
/pipelines
/pipeline-runs
/lineage/query

/causal/hypotheses
/causal/experiments
/causal/estimates
/scenarios

/agent-runs
/agent-runs/{id}/plan
/agent-runs/{id}/tool-calls
/agent-runs/{id}/resume

/decisions
/action-plans
/action-runs
/outcomes

/peer-cohorts
/peer-patterns
/transfer-trials
```

## 15. 实施切片

### Slice 1：关系图内核

- ObjectType / ObjectInstance；
- LinkType / LinkInstance；
- 图邻居、两跳遍历和子图；
- 旧 Entity 兼容投影；
- 前端企业关系图。

### Slice 2：事件和数字投影

- EventType / EventInstance；
- ProjectionVersion；
- 时间线和 as-of 查询；
- 订单到回款示例。

### Slice 3：Connector 和血缘

- CSV Connector；
- PipelineRun / StepRun；
- Object/Link/Event Mapping；
- Lineage Query。

### Slice 4：本体感知 Agent

- Graph Tool；
- Durable Run；
- Research、Modeling、Graph Analyst；
- 结构化 Plan 和 Evaluation。

### Slice 5：因果和行动

- CausalHypothesis；
- Intervention / Outcome；
- 前后比较和匹配对照；
- ActionType / ActionRun；
- 结果回写投影。

### Slice 6：行业复用

- CompanyFingerprint；
- Peer Cohort；
- Pattern / Playbook；
- Transfer Trial；
- Outcome 驱动的增量质量更新。

每个切片必须独立完成前端、API、持久化、示例数据和一次端到端演示。
