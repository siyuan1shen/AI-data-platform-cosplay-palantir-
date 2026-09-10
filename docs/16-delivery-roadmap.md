# 集中式交付路线图

本文是当前实施顺序。目标是在有限人力下尽快证明“管理层调研能够逐步形成可运行的企业数字投影”。

## 1. 资源原则

资源分配固定为：

```text
60%  本体引擎、关系图和数字投影
15%  数据接入、转换和血缘
10%  Agent 工具与工作流
10%  因果、行动和结果
 5%  行业复用和相似企业
```

安全治理、复杂交换、生产高可用、第三方扩展沙箱和微服务化当前分配为 0%。现有实现只修复阻断本地运行的问题。

## 2. 阶段 0：冻结旧主线

### 工作

- 将安全治理、Edge—Hub 加密交换和生产化标记为冻结；
- 不再扩展登录、权限、DLP、签名包和合规页面；
- 保留通用问卷导入、证据、语义映射、Agent、报告等可复用代码；
- 建立新领域模型和迁移清单；
- 用虚拟企业建立固定演示数据集。

### 退出条件

- 文档只有一套现行产品定位；
- 新工作全部能映射到端到端流程；
- 没有新需求以“企业级安全”名义进入 P0/P1。

## 3. 阶段 1：Object—Link 图内核

### 后端

- ObjectType、ObjectInstance；
- LinkType、LinkInstance；
- 类型约束、方向、基数和时间；
- 图邻居、路径、子图和简单聚合；
- 旧 CanonicalEntity 到 ObjectInstance 的兼容层。

### 前端

- 对象类型管理；
- 对象详情；
- 关系创建和编辑；
- 企业关系图；
- 点击对象查看上下游。

### 演示

展示公司、部门、客户、订单、产品、流程和系统之间的关系。

### 退出条件

- 至少 5 种 ObjectType；
- 至少 8 种 LinkType；
- 至少 50 个对象和 100 条关系；
- 三个跨对象问题可以通过图查询回答。

## 4. 阶段 2：Event—Metric—Projection

### 后端

- EventType、EventInstance；
- MetricType、MetricObservation；
- ProjectionVersion；
- as-of 查询和时间线；
- 对象当前状态由事件和观测投影形成。

### 前端

- 企业时间线；
- 指标趋势；
- Projection 版本对比；
- 价值流视图。

### 演示

展示订单从创建、排产、生产、交付、开票到回款的事件链。

### 退出条件

- 至少 6 种 EventType；
- 至少 5 种 MetricType；
- 能查看任意两个时间点的企业投影差异。

## 5. 阶段 3：调研到投影

### 后端

- ResearchMission；
- Statement、Observation、Unknown；
- 问题到对象、关系、事件和指标候选的结构化抽取；
- InformationGap 和 FollowUpProposal；
- 调研候选到 Projection 的确认流程。

### 前端

- 管理层调研任务；
- 问卷答案 CSV 预览和导入；
- 访谈记录；
- 候选对象和关系确认；
- 信息缺口和追问工作台。

### 演示

从一份问卷和一次访谈创建 Projection V1。

### 退出条件

- 调研内容能形成结构化对象和关系候选；
- 顾问可以确认、修改和拒绝候选；
- 管理层能看到调研形成的初始企业全景。

## 6. 阶段 4：首个数据接入闭环

### 后端

- CSV/Excel Connector；
- SourceSchema 和数据剖析；
- PipelineRun、StepRun；
- Object、Link、Event 和 Metric Mapping；
- LineageEdge；
- Pipeline 重放。

### 前端

- 文件导入预览；
- 字段映射；
- 映射结果预览；
- 导入确认；
- 运行状态；
- 来源到投影的血缘查看。

### 演示

导入 ERP 订单 CSV 和 CRM 客户 CSV，将两个来源映射到同一 Customer 和 Order 图。

### 退出条件

- 预览和确认导入都能工作；
- 失败批次可重放；
- 任一对象、关系、事件和指标可回溯来源记录。

## 7. 阶段 5：本体感知 Agent

### 后端

- Durable AgentRun；
- PlanStep、ToolCall、Task 和 EvaluationResult；
- Graph、Metric、Timeline、Evidence 工具；
- Research、Modeling、Graph Analyst、Critic 四类 Agent；
- 后台 Worker。

### 前端

- Agent 目标和范围；
- 计划预览；
- 工具调用时间线；
- 中间产物；
- 暂停、继续和取消；
- 结果评测。

### 演示

Agent 沿“客户—订单—生产—交付”关系图定位延迟订单的共同路径，而不是只总结问卷文字。

### 退出条件

- Agent 至少调用三种确定性工具；
- 运行可中断和恢复；
- 输出引用对象路径、指标和时间；
- 固定评测集可以回归比较。

## 8. 阶段 6：因果、情景和行动

### 后端

- CausalHypothesis 和 CausalEdge；
- Intervention、Outcome 和 Confounder；
- 前后比较和匹配对照；
- Scenario；
- ActionType、ActionPlan、ActionRun；
- 结果回写 Projection。

### 前端

- 因果图；
- 假设支持和反驳证据；
- 试点设计；
- 情景对比；
- 行动计划和结果；
- 有效、无效、无法判断的结果分类。

### 演示

验证“审批节点过多是否导致交付延迟”，设计试点，记录结果并更新因果假设。

### 退出条件

- 系统明确区分相关和因果；
- 至少一个 ActionRun 进入 Outcome；
- Outcome 能改变 Projection、Hypothesis 和 Playbook 质量。

## 9. 阶段 7：相似企业和行业飞轮

### 后端

- CompanyFingerprint；
- PeerCohort；
- Pattern、Playbook 和 EvalSuite；
- TransferTrial；
- UsageEvent 和 OutcomeFeedback；
- 增量质量聚合，替换请求级全量内存重建。

### 前端

- 相似企业解释；
- 基准差距；
- 可迁移模式；
- 适用条件；
- 试点记录；
- 模式跨项目效果。

### 演示

从一个制造企业提取“订单交付异常闭环”Playbook，在第二个虚拟企业中调整后试用，并记录迁移结果。

### 退出条件

- 相似度可解释；
- 同行经验明确标记为候选；
- TransferTrial 有目标企业的真实 Outcome；
- 行业资产质量由跨项目结果增量更新。

## 10. 阶段 8：管理层产品化

### 页面

- 企业全景；
- 价值流；
- 今日变化；
- 风险和机会；
- 原因与因果；
- 决策选项；
- 行动和结果；
- 管理问答；
- 同行参考。

### 管理问答回答结构

```text
结论
关键对象和关系路径
指标与时间变化
原因假设及强度
反对证据和未知
行动选项
预期结果
建议下一步
```

### 退出条件

核心管理层不进入建模和数据工程页面，也能理解企业当前状态、原因假设和行动进展。

## 11. 不允许并行展开的工作

在阶段 1–4 未完成前，不做：

- Agent 自动修改 ERP；
- 十种以上 Connector；
- 跨企业在线市场；
- 独立图数据库迁移；
- 微服务拆分；
- 移动端；
- 大规模实时流；
- 复杂多级账户体系；
- 行业大模型训练。

## 12. 每个切片的完成定义

每个切片必须同时包含：

1. 领域模型；
2. 数据库迁移；
3. API；
4. 前端完整操作；
5. 虚拟企业示例；
6. 单元和集成测试；
7. 从输入到管理层结果的演示；
8. 当前限制说明。

后端已有接口但前端无法操作，不算完成；前端有表单但后端契约不匹配，也不算完成。

## 13. 项目看板

统一使用以下 Epic：

```text
E0 Scope Freeze
E1 Ontology Language
E2 Graph Engine
E3 Projection & Time
E4 Executive Research
E5 Integration & Lineage
E6 Ontology-aware Agents
E7 Causal & Scenario
E8 Action & Outcome
E9 Peer Learning
E10 Executive Experience
```

任何新任务必须归入一个 Epic，并说明它推动了哪个端到端退出条件。
