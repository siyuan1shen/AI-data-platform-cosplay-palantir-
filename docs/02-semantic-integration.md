# 语义集成与冲突处理（历史版本）

> 本文保留已有映射与冲突设计。后续实现必须把映射放入对象—关系—事件图、转换管道和完整血缘中，权威方案见[本体、因果与 Agent 技术蓝图](15-ontology-causal-agent-blueprint.md)。

## 1. 目标

解决异名同物、同名异物、编码/枚举/单位/口径/时间不同造成的数据孤岛，为 Agent 提供统一但不丢失来源差异的企业语义视图。

## 2. 子系统

| 组件 | 责任 |
|---|---|
| Connector Registry | 数据库、文件、API 和问卷连接器 |
| Source Catalog | 来源系统、表、字段和负责人 |
| Schema Registry | 不可变 SchemaVersion 和漂移 |
| Data Profiler | 类型、分布、空值、唯一度、格式和样例 |
| Mapping Studio | 字段、值、单位、事件和流程映射审核 |
| Entity Resolver | 识别相同客户、物料、订单、设备 |
| Canonical Ontology | 提供统一语义目标 |
| Program Registry | 转换、验证和解析程序 |
| Conflict Center | 冲突检测、隔离、审核和撤销 |
| Lineage Service | 事实到来源字段和程序的完整血缘 |
| Drift Monitor | Schema、值域和质量变化 |

## 3. 接入原则

- 连接器默认只读；
- 先采集元数据和样例统计，再决定记录范围；
- 每次导入有批次 ID、来源版本和内容哈希；
- 原始值不能被规范化结果覆盖；
- 外部系统写回是独立 Action 能力，默认关闭；
- 接入凭据不进入 Agent 上下文和普通日志。

## 4. 五类映射

### Schema Mapping

```text
CRM.customer_name
ERP.partner_name
MES.client
→ core:customer.legal_name
```

映射包含来源 SchemaVersion、目标 OntologyVersion、适用条件、转换程序、优先级和测试。

### Value Mapping

```text
CRM: won / ERP: 30 / MES: RELEASED
→ order.status = confirmed
```

保留原值，并记录 MappingVersion。

### Unit Mapping

标准化金额、重量、时间和比例，保存单位、精度、舍入策略和 ProgramVersion。

### Identity Mapping

把来源记录关联到 CanonicalEntity。名称相似度只能是一个特征，不能单独自动合并。

### Event/Process Mapping

将不同系统状态变化映射到统一业务事件和流程节点。CRM 赢单、ERP 订单创建和 MES 任务下达是相邻但不同事件。

## 5. Schema Agent 工作流

```text
SourceSchemaVersion
→ 字段名/描述/类型/约束/样例分析
→ 候选业务对象和本体属性
→ MappingProposal
→ 确定性类型与约束检查
→ 人工审核
→ MappingVersion
```

Proposal 包含候选目标、匹配特征、反对证据、转换需求、适用范围、置信度分项和待确认问题。

## 6. 实体解析

```text
EntityMention / SourceRecord
→ 名称、编码、地址、单位标准化
→ 确定性唯一标识匹配
→ Candidate Blocking
→ 规则/统计/语义模型评分
→ 本体与业务约束验证
→ 自动接受 / 人工审核 / 拒绝
```

特征包括强标识、外部 ID、名称/别名、地址、产品规格、BOM、上下游关系、时间、业务语境和历史决议。

高置信度且硬约束通过的低风险项可按策略自动接受；灰区进入审核；低置信度保留独立。高影响实体默认人工审核。

## 7. 权威来源

不设置一个全局绝对权威系统，而是按概念、时间和用途配置：

```text
customer.legal_name      → 主数据/CRM
invoice.amount           → 财务
production.completed_at  → MES
shipment.signed_at       → 物流
```

非权威来源的冲突值仍保留可见。

## 8. 冲突分类

| 冲突 | 处理 |
|---|---|
| Identity | 可逆合并/拆分审核 |
| Schema | 分离语义 ID，补充上下文 |
| Value | 保留来源，应用权威/时间策略 |
| Temporal | 用双时态区分，不误判同一时点 |
| Definition | 创建 MetricDefinitionVersion |
| Ontology | OntologyPatch + 兼容评测 |
| Mapping | MappingConflict + 人工选择 |
| Dependency | 依赖解析失败，禁止安装 |

## 9. 不变量

- Approved Fact 不能被新值原地修改；
- 未解决 Conflict 不进入默认批准视图；
- 冲突解决保存竞争项、理由和版本；
- 自动策略输入与版本可追踪；
- merge/split 有补偿操作；
- 报告使用的 Conflict 状态随 ReleaseBundle 固定。

## 10. Mapping Program

处理字段组合、单位/日期/编码转换、枚举归一、多字段派生、事件重构和质量验证。第一阶段优先受限 DSL 与白名单函数；复杂程序使用隔离进程/容器。

## 11. Schema Drift

来源结构变化时：生成新版本、计算 diff、找出受影响资产、运行兼容测试、必要时暂停管线、审核新映射并重放受影响批次。

## 12. 质量指标

- 字段映射覆盖和准确率；
- 人工修改率；
- 实体自动匹配精确率；
- 错误合并率；
- 未解析实体比例；
- 冲突发现率和平均解决时间；
- Drift 导致的失败；
- 单系统接入耗时；
- Program 跨企业复用提升。

## 13. 首个试点

选择“客户 → 销售订单 → 生产/交付 → 发票 → 回款”，接入 2–3 个来源，验证实体统一、字段/状态/金额/时间映射、冲突中心、数据血缘和跨部门诊断。
