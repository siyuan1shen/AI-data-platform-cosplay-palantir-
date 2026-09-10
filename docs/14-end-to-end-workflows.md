# 端到端业务流程

本文把总体规划转化为可以实施、演示和验收的业务流程。所有页面、API、Agent 和数据模型都必须服务于这些流程。

## 1. 总流程与状态

一个企业解读项目使用以下状态：

```text
DRAFT
→ RESEARCHING
→ MODELING
→ INTEGRATING
→ ANALYZING
→ DECIDING
→ EXECUTING
→ MEASURING
→ LEARNING
→ ACTIVE
```

项目不是一次性结束。进入 `ACTIVE` 后，新的事件、指标变化或管理问题可以触发新一轮 `RESEARCHING`、`ANALYZING` 或 `DECIDING`。

## 2. 角色

| 角色 | 主要责任 | 核心产物 |
|---|---|---|
| Executive Sponsor | 定义目标、选择问题、批准行动 | Decision、Action Mandate |
| Management Team | 提供管理认知并讨论方案 | Statement、Priority、Decision |
| Research Lead | 设计和执行调研 | Research Plan、Evidence |
| Domain Analyst | 建模企业和解释业务 | Projection、Finding、Hypothesis |
| Data Integrator | 接入系统并建立映射 | Connector、Pipeline、Lineage |
| Department Owner | 校验本部门流程和结果 | Confirmation、Correction、Outcome |
| Agent Team | 追问、建模、分析、规划和复盘 | Proposal、Plan、Estimate |
| Platform | 保存状态、运行工具和更新投影 | Object、Link、Event、Metric |

## 3. 阶段 0：项目定义

### 输入

- 企业名称和行业；
- 管理层最关心的 1–3 个问题；
- 首个业务范围；
- 预期决策和结果指标；
- 可接触的人员、文件和系统。

### 操作

1. 创建 Company；
2. 创建 Enterprise Projection；
3. 创建 Research Mission；
4. 选择行业问题包；
5. 明确首个价值流；
6. 定义成功指标和截止时间。

### 输出

- Project Charter；
- Scope Boundary；
- Decision Questions；
- Initial Metric Set；
- Research Plan。

### 阶段门

如果无法明确“管理层准备据此做什么决定”，项目不能进入调研阶段。

## 4. 阶段 1：管理层问卷和访谈

### 输入

- Decision Questions；
- 行业问题包；
- 管理层名单和职责；
- 已有组织图、流程图和经营材料。

### 操作

```text
Question Pack
→ 管理层回答
→ Follow-up Agent 识别缺口
→ 顾问选择追问
→ 深度访谈
→ Statement 和 Observation
```

调研 Agent 为每个回答生成：

- 回答涉及的候选对象；
- 候选关系和流程节点；
- 候选指标；
- 管理判断；
- 待验证事实；
- 相互矛盾的观点；
- 下一轮追问；
- 建议接入的数据源。

### 输出

- Management Statement；
- Initial Object Candidate；
- Initial Link Candidate；
- Process Sketch；
- Information Gap；
- System Access Backlog；
- Causal Hypothesis Candidate。

### 阶段门

至少形成一条端到端价值流和三个可验证的管理假设。

## 5. 阶段 2：实地调研和证据化

### 输入

- 访谈形成的假设；
- 信息缺口；
- 流程草图；
- 部门和现场清单。

### 操作

1. 观察真实工作流程；
2. 采集制度、表单、报表和系统截图；
3. 记录人员角色和交接点；
4. 识别实际流程与制度流程的差异；
5. 将每条观察精确关联到对象、关系、事件或指标；
6. 区分 Statement、Observation、Fact Candidate 和 Unknown。

### 输出

- Evidence Record；
- Actual Process；
- Exception Pattern；
- Role Handoff；
- Hypothesis Update；
- Connector Priority。

### 阶段门

每个高优先级问题至少有一个现场或文件证据，或被明确标记为未知。

## 6. 阶段 3：建立初始企业数字投影

### 输入

- 调研对象和关系候选；
- 流程和证据；
- 行业本体模板。

### 操作

```text
候选概念
→ ObjectType / LinkType / EventType / MetricType
→ 企业对象实例
→ 企业关系实例
→ 价值流图
→ 时间和状态
→ Projection Version 1
```

### 输出

- Enterprise Projection V1；
- Object Catalog；
- Link Graph；
- Event Catalog；
- Metric Catalog；
- Unresolved Question Set。

### 阶段门

管理层能通过投影回答：企业由什么构成、关键流程如何运转、当前最大未知在哪里。

## 7. 阶段 4：系统接入规划

### 输入

- Projection V1；
- Unknown 和 Hypothesis；
- 系统清单和数据样例。

### 操作

对每个系统计算接入价值：

```text
Integration Priority
= Decision Relevance
× Information Gap Reduction
× Update Frequency
÷ Integration Cost
```

优先接入能够验证管理假设、补齐关键关系或提供行动结果的数据，不按系统规模排序。

### 输出

- Source Catalog；
- Integration Backlog；
- Source-to-Ontology Map；
- Pipeline Specification；
- Expected Decision Lift。

### 阶段门

首批只选择 1–3 个高价值来源。

## 8. 阶段 5：数据接入和转换

### 主流程

```text
Source Extract
→ Raw Record
→ Schema Profile
→ Mapping Proposal
→ Mapping Program
→ Entity Resolution
→ Link Construction
→ Event Construction
→ Metric Computation
→ Projection Update
```

### 每条结果必须保留

- 来源系统；
- 来源表、文件或 API；
- 来源记录 ID；
- 提取批次；
- 使用的 Mapping Program；
- 目标对象、关系、事件或指标；
- 转换前值和转换后值；
- 质量问题和冲突。

### 输出

- Projection V2；
- Data Lineage；
- Mapping Version；
- Conflict Queue；
- Data Quality Finding。

### 阶段门

至少两个来源能够投影到同一组对象或同一条价值流，并可从管理视图回溯来源。

## 9. 阶段 6：管理分析和原因假设

### Agent 分工

```text
Coverage Agent     → 哪些问题仍缺信息
Graph Agent        → 哪些对象和关系异常
Metric Agent       → 哪些指标发生显著变化
Conflict Agent     → 哪些来源或观点不一致
Causal Agent       → 哪些原因值得验证
Critic Agent       → 哪些结论证据不足
```

### 分析顺序

1. 描述当前状态；
2. 找出偏差和异常；
3. 沿对象、关系和事件路径定位影响范围；
4. 生成多个原因假设；
5. 列出支持、反驳和缺失证据；
6. 区分相关性、机制解释和因果证据；
7. 形成可验证 Finding。

### 输出

- Finding；
- Causal Hypothesis；
- Evidence Gap；
- Scenario Candidate；
- Management Brief。

### 阶段门

每条重大 Finding 必须能展示对象路径、时间范围、指标变化和原因假设。

## 10. 阶段 7：决策与情景模拟

### 输入

- Finding；
- Causal Hypothesis；
- 可用 ActionType；
- 资源和业务约束；
- 同行经验。

### 操作

```text
问题
→ 多个行动选项
→ 约束检查
→ 影响路径
→ 成本与收益估计
→ 情景模拟
→ 风险和可逆性
→ 管理层决策
```

### 输出

- Decision Option；
- Scenario Result；
- Decision Record；
- Selected Action Plan；
- Expected Outcome；
- Stop/Review Condition。

### 阶段门

管理层必须明确选择、拒绝或要求补充信息。Agent 不能把推荐自动当成决策。

## 11. 阶段 8：行动执行

### 动作层级

| 层级 | 示例 | 执行方式 |
|---|---|---|
| Informational | 请求补充证据、通知负责人 | 平台直接创建任务 |
| Analytical | 重算指标、运行情景 | 工具自动执行 |
| Managerial | 发起改进项目、变更责任人 | 管理层确认后执行 |
| Operational | 调整排产、修改订单 | 后期接入业务系统执行 |

### Action Run 状态

```text
PLANNED
→ READY
→ RUNNING
→ BLOCKED / COMPLETED / FAILED / CANCELLED
→ MEASURING
→ VERIFIED / INEFFECTIVE / INCONCLUSIVE
```

### 输出

- Action Run；
- Owner；
- Milestone；
- Execution Event；
- Outcome Measurement Plan。

## 12. 阶段 9：结果测量与因果评估

### 必要元素

- 干预对象；
- 目标指标；
- 基线时间窗；
- 干预后时间窗；
- 对照或反事实构造；
- 混杂因素；
- 估计方法；
- 不确定性；
- 适用范围。

### 结果分类

```text
EFFECTIVE       有足够证据支持预期改善
INEFFECTIVE     结果未改善或明显恶化
INCONCLUSIVE    数据或设计不足，不能判断
SIDE_EFFECT     目标改善但产生重要副作用
NOT_EXECUTED    行动未按计划执行
```

### 输出

- Outcome；
- Causal Estimate；
- Updated Finding；
- Playbook Result；
- Projection Update。

## 13. 阶段 10：企业内部飞轮

```text
Research Question
→ Projection Gap
→ Decision
→ Action
→ Outcome
→ Evaluation
→ 更新问题、映射、本体、Agent 和 Playbook
```

系统只把以下反馈视为学习信号：

- 人工纠正且有原因；
- 对象或关系确认；
- 行动真实执行状态；
- 指标和业务结果；
- 假设被支持或推翻；
- Playbook 在明确条件下的成败。

点击、浏览和 Agent 自己接受自己的输出不是真值反馈。

## 14. 阶段 11：相似企业指导

### Peer Cohort 建立

从以下特征形成企业向量和结构相似度：

- 行业与商业模式；
- 规模；
- 产品和订单复杂度；
- 价值流结构；
- 组织层级；
- 供应链结构；
- 系统成熟度；
- 当前问题；
- 投影完整度。

### 指导流程

```text
目标企业问题
→ 检索相似企业群组
→ 找到相似对象路径和问题模式
→ 检索有效/无效行动
→ 判断适用条件和差异
→ 生成目标企业候选方案
→ 小范围试点
→ 记录结果
```

Peer Projection 只能提供先验、基准和方案候选，不能替代目标企业的验证。

## 15. 页面与流程映射

| 页面 | 服务的业务流程 |
|---|---|
| 企业项目 | 阶段 0、状态和范围 |
| 管理层调研 | 阶段 1–2 |
| 数字投影 | 阶段 3、对象、关系和价值流 |
| 数据接入 | 阶段 4–5 |
| 冲突与映射 | 阶段 5 |
| 管理分析 | 阶段 6 |
| 因果与情景 | 阶段 7、9 |
| 决策与行动 | 阶段 7–9 |
| Agent 工作台 | 阶段 1–10 的协作运行 |
| 行业经验 | 阶段 11 |
| 学习中心 | 企业和行业飞轮 |

旧管理台中的“模型与密钥”“安全登录”“分部与总部交换”不再占据主要导航位置。

## 16. 首次交付演示脚本

1. 创建一家虚拟制造企业；
2. 导入问卷和答案 CSV；
3. 创建部门、客户、订单、产品和流程对象；
4. 建立部门负责流程、客户下订单等关系；
5. 导入一份 ERP 订单 CSV；
6. 把 ERP 记录映射成对象、关系和事件；
7. 展示“客户—订单—生产—交付—回款”图；
8. 发现交付延迟集中在某类订单；
9. 生成两个原因假设并说明缺失证据；
10. 创建一个改善行动及目标指标；
11. 导入行动后的结果数据；
12. 判断行动有效、无效或无法判断；
13. 将问题、映射和行动方案保存为可复用 Playbook。

这 13 步全部通过，才算完成第一个真实业务闭环。
