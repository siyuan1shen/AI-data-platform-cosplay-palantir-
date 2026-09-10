# 企业决策投影平台总执行规划

- 状态：总体方向已收敛；精确实施以 19–21 号文档为准，确认前不继续编码
- 日期：2026-08-30
- 适用范围：`palantir` V2
- 上位产品定义：`13-executive-decision-platform.md`
- 业务流程依据：`14-end-to-end-workflows.md`
- 技术模型依据：`15-ontology-causal-agent-blueprint.md`
- 交付顺序依据：`16-delivery-roadmap.md` 与 `17-codebase-refactor-plan.md`
- 能力审计：`19-capability-coverage-audit.md`
- 精确工程规格：`20-executable-implementation-spec.md`
- 原子执行顺序：`21-atomic-execution-backlog.md`

本文不再创造新的产品方向，而是把现有设计、当前代码状态和真实端到端测试结果收敛成一份可执行计划。若本文与总体产品定位冲突，以 13 号文档为准；若实施细节冲突，以本文的阶段门和验收标准为准。

## 1. 最终产品定义

平台只服务企业核心管理层及其调研、分析和改善团队。

它不是 ERP、CRM、MES、OA、财务系统或通用 BI 的替代品，而是建立在这些系统之上的企业管理认知层和决策层：

```text
管理层问题
→ 问卷、访谈、现场观察和文件证据
→ 企业对象、关系、流程、事件和指标
→ 接入高价值业务数据验证投影
→ 形成 Finding、原因假设和行动选项
→ 管理层决策与真实执行
→ Outcome 和因果评估
→ 更新投影、问题、映射、Agent 和 Playbook
→ 在相似企业中试用并继续验证
```

一句话定义：

> 从管理层调研开始，逐步建立企业在数字世界中的可验证投影，让管理层更容易理解企业、判断原因、选择行动并从结果中学习。

## 2. 不变的产品原则

1. 先理解企业，再接入数据，再辅助决策。
2. 企业对象、关系、事件、指标和动作是平台共同语言。
3. 观点、观察、事实、假设、发现、决策和结果必须分开保存。
4. 调研、数据和 Agent 产物都不能未经确认直接成为企业事实。
5. 所有派生结果必须能追溯到来源、转换、时间和适用范围。
6. Agent 通过工具读取投影，不能绕过领域服务直接修改数据库。
7. 因果边与业务关系边分开；相关性不能自动升级为因果结论。
8. 同行经验只是先验和候选方案，必须在目标企业试点后才可评价。
9. 先完成模块化单体和真实闭环，再考虑微服务、图数据库和实时流。
10. 登录、复杂权限、DLP、RLS、加密交换和生产高可用不进入当前产品路线；最低限度的本地数据边界只作为运行基础设施。

## 3. 第一阶段目标用户和场景

### 3.1 主要用户

- 董事长、CEO、总经理和经营班子；
- 战略、经营分析和数字化负责人；
- 调研顾问、领域分析师和数据集成人员；
- 负责改进项目的部门负责人。

### 3.2 首个固定垂直场景

首个场景固定为制造企业“订单到回款”，不同时展开人力、财务、采购等全部场景。

```text
客户
→ 商机
→ 订单
→ 排产/采购
→ 生产/服务交付
→ 发票
→ 回款
```

首批必须覆盖：

- 对象：Company、Department、Customer、Product、Order、WorkOrder、Shipment、Invoice、Payment；
- 关系：OWNS、SERVES、PLACED、CONTAINS、FULFILLED_BY、PRODUCES、SHIPS、INVOICES、SETTLES、RESPONSIBLE_FOR；
- 事件：OpportunityWon、OrderCreated、PlanReleased、ProductionCompleted、ShipmentDelivered、InvoiceIssued、PaymentReceived；
- 指标：订单周期、审批等待、准时交付率、毛利率、回款周期；
- 动作：RequestEvidence、AssignOwner、StartImprovement、RunScenario、VerifyOutcome。

## 4. 北极星指标和成功标准

北极星指标：

```text
Decision Improvement Yield
= 被管理层采用且结果得到验证的决策改进数
  / 调研、建模和分析投入
```

辅助指标：

- 从项目创建到 Projection V1 的时间；
- 管理问题中可由对象路径、指标和证据回答的比例；
- 数据映射后可回溯来源的投影元素比例；
- Finding 拥有完整证据链的比例；
- ActionRun 拥有基线、目标、测量窗口和 Outcome 的比例；
- Agent 提案被人工修改、接受或拒绝的原因覆盖率；
- Playbook 在第二个项目中完成真实试点的比例；
- 重复项目的调研、映射和分析投入下降幅度。

禁止用页面数、Agent 数、连接器数量、模型大小或 Token 消耗作为产品成功指标。

## 5. 当前基线与真实状态

### 5.1 已经可运行

- `/api/v2`、独立 `eip-v2.db` 和本机页面；
- Company 和 ProjectionProject；
- 公司与项目双层选择；
- ObjectType、Object、LinkType、Link 和最短路径；
- 问卷答案 CSV 预览、确认、空答案跳过、重复合并和幂等；
- EventType、MetricType、Event、MetricObservation 和时间线；
- CSV 预览、对象映射、对象生成和来源行血缘；
- Finding、CausalHypothesis、DecisionOption、Decision、ActionPlan、ActionRun 和 Outcome；
- 持久化 AgentRun/Step 的确定性提案骨架；
- 本地重启后的数据恢复。

### 5.2 只完成了骨架或部分闭环

| 能力 | 当前状态 | 缺口 |
|---|---|---|
| 项目生命周期 | 后端状态机存在 | 前端不能推进、看不到阶段门 |
| 对象关系图 | 可创建和查询 | 缺编辑、纠错、版本、基数、时间和子图 |
| 调研 | 可导入答案 | 缺 Statement、Observation、Unknown、追问和候选确认 |
| 数据接入 | CSV 可生成 Object | 缺 Link/Event/Metric Mapping、PipelineRun、重放和冲突处理 |
| 血缘 | 有来源行关系 | 缺完整转换链、前端详情和导出 |
| 时间投影 | 有事件和指标 | 缺 ProjectionVersion、as-of 和版本差异 |
| 因果 | 有假设和简单对象边 | 缺变量、混杂、干预、估计方法、证据和生命周期 |
| 行动闭环 | 可记录计划、执行和结果 | Outcome 尚不会更新投影、假设和 Playbook |
| Agent | 同步读取计数并生成提案 | 缺计划、工具调用、Worker、暂停恢复、评测和多角色协作 |
| 行业飞轮 | 未实现 | 缺 Pattern、Playbook、Fingerprint、PeerCohort、TransferTrial |
| 导出与迁移 | 未形成 V2 闭环 | 缺管理简报和可迁移项目包 |

### 5.3 必须诚实下调的完成状态

当前不应把 R3–R8 标记为完全完成。它们已有可运行纵向骨架，但尚未达到各阶段退出条件。后续计划必须先补齐这些断点，再进入行业飞轮。

## 6. 完整用户旅程和阶段门

### 阶段 0：定义项目

用户操作：

1. 创建或选择公司；
2. 创建解读项目；
3. 输入 1–3 个管理问题；
4. 选择“订单到回款”范围；
5. 定义预期决策、成功指标和截止时间；
6. 选择首批调研对象和可获得的数据来源。

输出：Project Charter、Scope、Decision Questions、Initial Metrics、Research Plan。

阶段门：无法说明“管理层准备据此做什么决定”时，不得进入调研。

### 阶段 1：问卷与访谈

外部问卷系统保持独立：

- 当前稳定路径：外部问卷系统导出 CSV，Palantir 预览后确认导入；
- 后续可选路径：增加只读本地适配器，自动读取外部问卷系统导出目录；
- 不建立两个项目的共享数据库，不让任一项目成为另一项目的内部模块。

导入后，系统把每条回答拆成：

- Management Statement；
- 候选对象、关系、事件和指标；
- 待验证事实；
- Unknown；
- 冲突观点；
- Follow-up Proposal；
- 建议接入的数据源。

阶段门：至少形成一条端到端价值流和三个可验证管理假设。

### 阶段 2：现场调研和证据化

用户录入访谈、现场观察、文件和截图说明，并将证据关联到对象、关系、事件、指标或假设。

每条记录必须标明：来源、采集时间、记录人、适用范围、证据类型和可信度。

阶段门：每个高优先级问题至少有一个证据，或明确标记为未知。

### 阶段 3：Projection V1

候选对象和关系进入确认队列，分析师可以接受、修改、合并、拒绝，并记录理由。

系统输出对象目录、关系图、价值流、事件目录、指标目录和未知集合，形成 Projection Version 1。

阶段门：管理层能看懂企业由什么构成、关键流程如何运转、最大未知在哪里。

### 阶段 4：系统接入规划

按以下公式排序数据源，而不是按系统大小排序：

```text
Integration Priority
= Decision Relevance
× Information Gap Reduction
× Update Frequency
÷ Integration Cost
```

首批只选 ERP 订单 CSV 和 CRM 客户 CSV 两个来源。

阶段门：明确来源、字段、映射目标、预期补齐的问题和失败处理方式。

### 阶段 5：数据接入和 Projection V2

```text
Extract
→ Raw Record
→ Profile
→ Mapping Proposal
→ Mapping Preview
→ Confirm
→ Resolve Identity
→ Build Object/Link/Event/Metric
→ Validate
→ Project
```

阶段门：两个来源映射到同一 Customer/Order 图；任一投影元素可以回溯原始行、映射版本和运行批次。

### 阶段 6：管理分析

系统先描述，再定位异常，再提出多个原因假设，禁止直接输出“根因”。

重大 Finding 必须包含：

- 对象和关系路径；
- 指标和时间变化；
- 支持和反对证据；
- 缺失证据；
- 相关、机制或因果证据等级；
- 适用范围和置信度。

阶段门：至少形成一个可证伪原因假设和两个备选解释。

### 阶段 7：决策和情景

每个行动选项必须显示约束、影响路径、成本、风险、可逆性、预期指标和停止条件。

管理层必须明确接受、拒绝或要求补充证据。Agent 推荐不能自动成为 Decision。

阶段门：产生一个可执行、可测量、可回滚的试点计划。

### 阶段 8：执行和测量

ActionRun 使用完整状态机：

```text
PLANNED → READY → RUNNING
→ BLOCKED / COMPLETED / FAILED / CANCELLED
→ MEASURING
→ VERIFIED / INEFFECTIVE / INCONCLUSIVE
```

Outcome 必须关联基线、目标、测量窗口、对照或反事实、混杂因素和不确定性。

阶段门：记录真实执行状态和真实业务结果。

### 阶段 9：企业内部学习

Outcome 触发明确的更新任务：

- 更新 Projection；
- 支持、反驳或降级 CausalHypothesis；
- 更新 Research Question；
- 修正 Mapping Program；
- 生成 EvalCase；
- 创建或更新 Playbook Candidate。

阶段门：系统能展示“这次结果具体改变了什么”。

### 阶段 10：同行迁移

只有经过企业内部 Outcome 验证的模式才能进入 Peer Pattern 候选。

目标企业先评估适用差异，再做 TransferTrial，最后依据目标企业自己的 Outcome 判断采纳、修改或拒绝。

阶段门：至少一个模式在第二个虚拟企业完成真实试点。

## 7. 目标领域模型

### 7.1 Portfolio

- Company；
- ProjectionProject；
- ProjectCharter；
- DecisionQuestion；
- ScopeBoundary；
- ProjectStageGate。

### 7.2 Research

- ResearchMission；
- QuestionPack / QuestionVersion；
- SurveyResponse；
- InterviewRecord；
- Statement；
- Observation；
- Evidence；
- Unknown；
- Conflict；
- FollowUpProposal；
- ProjectionCandidate。

### 7.3 Ontology Language

- ObjectType；
- LinkType；
- EventType；
- MetricType；
- ActionType；
- Interface；
- Constraint；
- TypeVersion。

所有类型使用 stable key、版本、状态、有效时间、Schema 和显示配置。

### 7.4 Projection Instance Store

- ObjectInstance；
- LinkInstance；
- EventInstance；
- MetricObservation；
- ProjectionVersion；
- ProjectionChangeSet。

当前状态与历史事件分开，外部标识支持一个对象对应多个系统标识。

### 7.5 Integration and Lineage

- Source；
- Connector；
- SourceSchema；
- RawRecord；
- PipelineDefinition；
- PipelineRun；
- StepRun；
- MappingProgram / MappingVersion；
- IdentityResolutionDecision；
- LineageEdge；
- ImportConflict；
- ReplayRequest。

### 7.6 Decision and Causal

- Finding；
- CausalVariable；
- CausalHypothesis；
- CausalEdge；
- Confounder；
- Intervention；
- ComparisonGroup；
- Estimand；
- CausalEstimate；
- Scenario；
- DecisionOption；
- Decision；
- ActionPlan；
- ActionRun；
- Outcome。

### 7.7 Agent Runtime

- AgentRun；
- AgentTask；
- PlanStep；
- ToolCall；
- Artifact；
- ApprovalRequest；
- EvaluationSuite；
- EvaluationCase；
- EvaluationResult。

### 7.8 Learning and Peer Network

- LearningEvent；
- AssetCandidate；
- Pattern；
- Playbook；
- AssetVersion；
- CompanyFingerprint；
- PeerCohort；
- TransferTrial；
- OutcomeFeedback；
- QualityAggregate。

## 8. 模块化单体目标架构

```text
enterprise_insight/
├─ platform/       # 数据库、任务运行、时间、ID、配置
├─ portfolio/      # Company、Project、Scope、Stage Gate
├─ research/       # 问卷、访谈、证据、追问、候选
├─ ontology/       # 类型系统和约束
├─ projection/     # Object、Link、Event、Metric、版本和图查询
├─ integration/    # Source、Connector、Pipeline、Mapping、Lineage
├─ analysis/       # Finding、冲突、异常、管理简报
├─ causal/         # 假设、DAG、干预、估计、情景
├─ decision/       # Option、Decision、Action、Outcome
├─ agents/         # Run、Task、Plan、Tool、Evaluation
├─ learning/       # Pattern、Playbook、同行和迁移试验
├─ reporting/      # 管理简报、导出和项目包
└─ web/            # 管理层体验和专业工作台
```

依赖规则：

```text
web / api
→ application services
→ domain
→ repository interfaces
→ infrastructure adapters
```

- Domain 不依赖 FastAPI、SQLAlchemy、旧 V1 或具体模型供应商；
- 模块之间通过 ID、命令、查询和领域事件协作；
- Agent 只能调用注册工具；
- Integration 只能通过 Projection 服务写入对象、关系、事件和指标；
- Learning 只能消费 Outcome 和人工纠正，不能把 Agent 自评当作真值；
- V2 不引用旧 exchange、flywheel、RBAC、DLP 和 Fact 主模型。

## 9. 数据库和任务执行策略

### 9.1 数据库

- 本机单用户：SQLite；
- 总部多人协作：PostgreSQL；
- 两者使用相同领域模型和迁移链；
- V2 独立 Schema，完成迁移前不修改 V1 数据；
- 每个核心资源保存 company_id、project_id、created_at、updated_at 和版本字段；
- 唯一约束和外键在数据库级实现，应用层检查只提供友好错误。

### 9.2 长任务

HTTP 请求只创建任务，不同步执行完整数据管道或 Agent 流程。

首版使用数据库任务表和本地 Worker：

```text
Job
→ Claim
→ Step execution
→ Progress event
→ Success / Retry / Failure / Cancel
```

必须支持幂等键、步骤级重试、断点恢复、取消、超时和错误详情。初期不引入 Kafka、Redis 或分布式队列。

## 10. 数据接入和映射设计

### 10.1 Connector 顺序

1. CSV；
2. Excel；
3. 文件夹增量导入；
4. 数据库只读连接；
5. REST API；
6. 真实需求出现后再考虑定时和流式接入。

### 10.2 Mapping Program

Mapping 不是一次性表单结果，而是可版本化、可评测、可重放的程序：

- 输入 Schema 和字段口径；
- Object/Link/Event/Metric 目标；
- 数据清洗；
- 外部标识与实体解析；
- 冲突策略；
- 验证规则；
- 程序版本和生效范围。

### 10.3 导入闭环

任何导入必须经历：选择文件、Schema 预览、映射预览、冲突预览、确认导入、运行状态、结果汇总、血缘查看和失败重放。

## 11. 本体与数字投影引擎

### 11.1 类型约束

LinkType 必须保存方向、起止类型、基数、是否有时间属性和逆向名称。MetricType 必须保存单位、粒度、维度、窗口和计算口径。ActionType 必须保存前置条件、影响、可逆性和结果指标。

### 11.2 图查询

首批稳定工具：

- get_object；
- find_objects；
- neighbors；
- shortest_path；
- traverse；
- subgraph；
- aggregate；
- events；
- projection_as_of。

### 11.3 ProjectionVersion

ProjectionVersion 固定 ontology version、pipeline versions、时间水位和 ChangeSet，不复制全库。必须能比较任意两个版本之间新增、修改、失效的对象、关系、事件和指标。

## 12. 因果和情景能力

因果建设严格按阶梯推进：

```text
C0 描述和相关
→ C1 时序与前后比较
→ C2 匹配对照与准实验
→ C3 受控试点和自然实验
→ C4 可重复策略
→ C5 因果驱动情景优化
```

首个版本只承诺 C1，随后实现 C2。

每个因果估计保存：研究问题、对象范围、时间窗口、干预、结果指标、混杂因素、对照构造、估计方法、诊断、不确定性和适用范围。

情景模拟使用 Projection clone，不直接改写真实投影；Scenario 结果必须显示假设和未建模因素。

## 13. Agent 架构

### 13.1 Agent 角色

- Research Agent：信息缺口和追问；
- Modeling Agent：对象、关系、事件和指标候选；
- Integration Agent：映射和管道候选；
- Graph Analyst：关系路径和结构异常；
- Metric Analyst：趋势、分群和异常；
- Causal Analyst：验证方案和方法选择；
- Decision Planner：行动选项和情景；
- Critic Agent：证据、反例和约束；
- Learning Agent：Outcome 到 EvalCase 和 Playbook Candidate。

### 13.2 运行模型

```text
Run
├─ Goal and Scope
├─ Context Snapshot
├─ Plan
├─ Tool Calls
├─ Intermediate Artifacts
├─ Critic Review
├─ Human Decision Point
├─ Optional Action Request
├─ Outcome
└─ Evaluation
```

### 13.3 自主性顺序

- A0：只读解释；
- A1：结构化提案；
- A2：调用图、时间线、指标和证据工具；
- A3：计划和情景模拟；
- A4：人工确认后执行平台内部可逆动作；
- A5：有真实重复需求后才考虑规则内自动执行。

进入 A2 前不增加更多 Agent 名称；进入 A3 前不做外部系统写回。

## 14. 企业内部飞轮

飞轮不是“把所有历史重新喂给模型”，而是把真实反馈结构化并增量更新资产：

```text
Question
→ Projection Gap
→ Decision
→ Action
→ Outcome
→ Evaluation
→ LearningEvent
→ Candidate Asset
→ Validation
→ Asset Version
```

有效学习信号只有：

- 人工纠正和理由；
- 对象、关系或映射确认；
- 行动真实执行状态；
- 真实指标和业务 Outcome；
- 假设被支持、反驳或无法判断；
- Playbook 在明确条件下的成败。

统计采用 SQL 增量聚合，不在每次请求时全量重建内存注册表。

## 15. 行业共享飞轮

### 15.1 可共享资产

- 问题包和追问策略；
- 行业本体类型和显示配置；
- 字段映射程序和数据质量规则；
- 指标口径；
- 图查询和 Agent Recipe；
- Finding Pattern；
- 因果验证设计；
- Action Playbook；
- EvalSuite。

原始问卷回答和业务明细不是行业资产的默认组成部分。

### 15.2 CompanyFingerprint

企业指纹包含行业、商业模式、规模、产品复杂度、订单模式、流程图结构、组织层级、供应链、系统成熟度、问题上下文和投影完整度。

相似度按问题动态加权，必须解释“为什么相似”和“哪里不同”。

### 15.3 TransferTrial

```text
Source Pattern
→ Applicability Assessment
→ Target Adaptation
→ Pilot Action
→ Expected Outcome
→ Observed Outcome
→ Effective / Ineffective / Inconclusive
→ Cross-project Quality Update
```

至少两个独立企业 Outcome 前，资产只能称为“候选模式”，不能称为“行业最佳实践”。

## 16. 分部、本机和总部的数据流

不恢复旧 Edge—Hub 加密交换产品线。采用普通、可理解、可测试的项目包：

### 16.1 分部或顾问本机

- 本地创建公司和项目；
- 导入问卷答案、访谈和业务文件；
- 形成 Projection、Finding、Action 和 Outcome；
- 导出项目包。

### 16.2 项目包

项目包包含：

- manifest 和 Schema version；
- 企业和项目元数据；
- 调研结构化记录；
- 本体类型与实例；
- 事件、指标和投影版本；
- Mapping Program、Lineage 和冲突；
- Finding、Hypothesis、Decision、Action 和 Outcome；
- Agent Run 和 Evaluation；
- 用户明确选择附带的原始文件。

项目包支持在线上传或线下拷贝，使用同一导入、校验、预览和确认流程。

### 16.3 总部

总部导入多个项目包，运行同行分群、模式抽取、跨项目评测和 Playbook 质量聚合，再导出更新后的行业资产包供本机安装。

代码仓库、运行数据库、原始导入文件和导出包必须物理分离。Git 只保存程序、迁移、合成测试数据和模板。

## 17. 前端信息架构

### 17.1 管理层主页

- 企业全景；
- 当前价值流；
- 今日变化；
- 关键风险和机会；
- 原因假设和反对证据；
- 待决策事项；
- 行动进度和 Outcome；
- 管理问答；
- 同行参考。

### 17.2 专业工作台

- 企业与项目；
- 管理层调研；
- 数字投影；
- 数据接入；
- 映射、冲突和血缘；
- 管理分析；
- 因果与情景；
- 决策与行动；
- Agent 工作台；
- 学习中心；
- 行业经验；
- 导出和项目包。

### 17.3 前端完成规则

- 后端存在的核心命令必须有前端入口；
- 所有导入必须 Preview → Confirm；
- 所有 Agent 或映射提案必须 Accept / Edit / Reject；
- 所有错误必须显示字段、原因和修复方法；
- 长任务必须显示进度、步骤、失败原因和重试入口；
- 管理层页面不暴露数据库、模型参数和映射工程细节；
- 公司选择和项目选择始终分开。

## 18. 管理问答输出契约

任何管理问答统一输出：

1. 结论；
2. 关键对象和关系路径；
3. 指标和时间变化；
4. 原因假设及证据等级；
5. 反对证据和未知；
6. 行动选项；
7. 预期结果和风险；
8. 建议下一步；
9. 来源与更新时间。

当证据不足时，系统回答“不知道什么、为什么不知道、下一步如何知道”，不能补全一个看似合理的结论。

## 19. 后续实施顺序

### R0–R1F：基线、V2 本机壳和执行基础

状态：运行骨架已完成；编码前必须按 21 号文档补齐独立 V2 迁移、统一错误、revision、幂等、可恢复 Job、仓库外数据目录、模块化前端壳、健康诊断和冷启动合成数据。

退出条件：全新安装只需一个命令即可启动 V2；数据库路径稳定；重启恢复；无真实数据进入仓库。

### R2：Company、Project 和阶段门闭环

工作：公司/项目编辑、项目状态前端、Project Charter、Scope、成功指标、Decision Questions、阶段门检查。

退出条件：业务人员无需调用 API 即可定义项目并推进至调研阶段。

### R3：Object—Link 图内核补齐

工作：类型约束、基数、时间、外部标识、对象详情、编辑纠错、邻居、子图、遍历和固定订单到回款模板。

退出条件：5 类对象、8 类关系、50 个对象、100 条关系，三个管理问题可通过图查询回答。

### R4：Event—Metric—Projection 补齐

工作：指标口径、事件角色、ProjectionVersion、ChangeSet、as-of 查询、版本比较、价值流和趋势页面。

退出条件：6 类事件、5 类指标，任意两个时间点的投影差异可重现。

### R5：调研到 Projection V1

工作：ResearchMission、Statement、Observation、Evidence、Unknown、Conflict、FollowUpProposal、ProjectionCandidate 和人工确认队列。

退出条件：从问卷答案和访谈形成可人工确认的对象、关系、事件和指标候选，并生成 Projection V1。

### R6：两个来源的数据接入闭环

工作：CSV/Excel Connector、PipelineRun/StepRun、对象/关系/事件/指标映射、实体解析、冲突、血缘详情和重放。

退出条件：ERP 订单与 CRM 客户映射到同一图；任一结果可回溯原始行和映射版本；失败批次可修复后重放。

### R7：分析、因果、决策和 Outcome

工作：Finding 证据链、完整因果边、C1 前后比较、Scenario、ActionType、完整执行状态、Outcome 回写 Projection/Hypothesis。

退出条件：完成“审批层级是否导致交付延迟”的试点和结果评估，系统明确展示结果改变了哪些模型。

### R8：本体感知 Agent

工作：后台 Worker、PlanStep、ToolCall、Artifact、暂停恢复、四类首批 Agent、Graph/Metric/Timeline/Evidence 工具和持久化 EvalSuite。

退出条件：Agent 至少调用三种确定性工具；输出引用对象路径、指标、时间和来源；运行可中断恢复；固定评测集通过。

### R9：企业和行业学习

工作：LearningEvent、Pattern、Playbook、CompanyFingerprint、PeerCohort、TransferTrial 和增量质量聚合。

退出条件：一个经 Outcome 验证的 Playbook 在第二个虚拟企业中完成适配、试点和结果记录。

### R10：管理层产品化、导出和旧代码切换

工作：管理层主页、管理问答、管理简报、项目包、V1 数据迁移、停止挂载 `/api/v1`、删除冻结模块和旧页面。

退出条件：全新安装只创建 V2 Schema；最终浏览器旅程全部通过；仓库不再运行旧 auth、exchange、flywheel、DLP、RBAC 和 Fact 主模型。

## 20. 实施依赖和禁止并行项

```text
R1F
→ R2
→ R3
→ R4
→ R5
→ R6
→ R7
→ R8
→ R9
→ R10
```

R3–R6 未达到退出条件前，不并行建设：

- Agent 外部系统执行；
- 十种以上 Connector；
- 独立图数据库；
- 微服务拆分；
- 实时流和大数据平台；
- 行业市场；
- 行业大模型训练；
- 复杂账户、安全治理和高可用平台。

允许在当前阶段并行的只有：领域模型、迁移、API、前端、合成数据和测试，它们共同完成同一个纵向切片。

## 21. 每阶段完成定义

每个阶段必须同时具备：

1. 领域模型和不变量；
2. 数据库迁移；
3. API 和错误契约；
4. 前端完整操作；
5. 固定虚拟企业示例；
6. 单元、集成、负向、隔离和幂等测试；
7. 真实浏览器端到端旅程；
8. 停止/重启后的持久化验证；
9. 输入到管理结果的演示；
10. 当前限制、迁移和回滚说明。

只完成后端、只完成页面或只能手工改数据库，均不算完成。

## 22. 测试策略

### 22.1 测试层级

- Domain：状态机、类型约束、因果与学习不变量；
- Repository：外键、唯一约束、事务和迁移；
- API：正常、空白、重复、越界、跨项目和错误契约；
- Pipeline：重复导入、部分失败、重试、重放和血缘；
- Agent：工具白名单、计划、暂停恢复、评测和人工确认；
- Browser：从空库完成真实业务旅程；
- Restart：关闭服务、重启、恢复上下文和长任务；
- Migration：V1 副本到 V2，数量、ID、引用和错误报告对账；
- Package：项目包导出、校验、预览、导入和幂等。

### 22.2 固定验收数据

仓库只保留合成数据：

- 虚拟制造企业 A：存在订单交付延迟；
- 虚拟制造企业 B：规模相近但流程和系统成熟度不同；
- 合成问卷答案；
- CRM 客户 CSV；
- ERP 订单 CSV；
- 行动前后指标；
- 一组有效、一组无效、一组无法判断的 Outcome。

### 22.3 质量门

- 所有测试通过；
- 静态检查和前端语法检查通过；
- OpenAPI 与前端使用契约匹配；
- 新接口有正常和失败测试；
- 浏览器旅程不依赖开发者工具；
- 不把真实问卷、回答、数据库、密钥和导出包提交到 Git。

## 23. 数据迁移和旧代码删除

1. 冻结 V1 功能，只修启动阻断；
2. 建立 V1 → V2 映射表和只读迁移器；
3. 在数据库副本上重复迁移；
4. 输出数量、ID、引用、失败和人工处理报告；
5. V2 浏览器验收通过后切换桌面入口；
6. 保留一次可恢复的 V1 数据副本；
7. 停止挂载 `/api/v1`；
8. 删除旧运行模块、页面、路由、表和测试；
9. 更新 README、使用手册、开源说明和迁移说明。

任何阶段不得直接覆盖或逆向修改原 V1 数据库。

## 24. 最终验收旅程

固定虚拟制造企业必须完全通过以下浏览器旅程：

1. 创建公司和决策项目；
2. 定义管理问题、范围、指标和阶段门；
3. 导入问卷答案并形成追问与候选；
4. 确认对象、关系、事件和指标，生成 Projection V1；
5. 导入 CRM 客户和 ERP 订单；
6. 预览并确认对象、关系、事件和指标映射；
7. 查看完整血缘、冲突和 Pipeline 状态；
8. 展示订单到回款关系图、价值流和时间线；
9. 回答三个跨系统管理问题；
10. 形成 Finding、两个原因假设和缺失证据；
11. 设计两个行动选项和试点；
12. 管理层记录决策并启动行动；
13. 导入行动后数据并评价 Outcome；
14. Outcome 更新 Projection、Hypothesis 和 EvalCase；
15. 保存一个 Playbook；
16. 在虚拟企业 B 中完成适用性分析、调整和 TransferTrial；
17. 查看跨企业结果和模式质量；
18. 导出管理简报和可迁移项目包；
19. 重启程序后恢复全部状态；
20. 在全新实例导入项目包并完成数量与引用对账。

任一步只能调用 API、需要手工改数据库或无法由业务人员在前端完成，最终验收失败。

## 25. 首批后续工作包

确认本规划后，先执行 21 号文档 R1F 收口包，不同时进入 R2、R8 或 R9：

1. 冻结当前行为基线并建立新的测试目录；
2. 建立独立 V2 Alembic 迁移链；
3. 统一错误、revision、幂等和操作回执；
4. 建立可恢复数据库 Job 和 Worker；
5. 将运行数据迁到仓库外的本机应用目录；
6. 把单文件前端拆成路由、状态、API 和页面模块；
7. 补诊断、合成 fixture 和统一质量门；
8. R1F 验收通过后严格执行 R2–R10 原子任务。

## 26. 需要确认的决策

在继续编码前，默认采用以下决策：

1. 产品只服务核心管理层及其项目团队；
2. 首个场景固定为制造业订单到回款；
3. 外部问卷系统与 Palantir 保持程序和数据库独立，CSV 导入是稳定接口；
4. 本机使用 SQLite，总部多人场景使用 PostgreSQL；
5. 分部—总部使用普通项目包，不恢复旧加密交换产品线；
6. 当前模型只做到 A2–A3，不允许自动写 ERP；
7. 因果首版只承诺 C1，随后实现 C2；
8. 行业经验必须经过目标企业 TransferTrial；
9. 不开发安全治理产品模块，但保留代码、运行数据和项目包的物理分离；
10. R1F–R7 补齐前不开始 R9，不删除 V1。

确认这些决策后，实施严格按 R1F → R2 → … → R10、20 号工程契约和 21 号原子任务推进。
