# V2 核心重构执行计划

- 状态：执行中（R1–R8 核心纵向切片已开始）
- 范围：允许重构整个项目和破坏旧内部接口
- 编码门：已获准开始编码；旧 V1 仍保留，待 V2 替代能力验证后再下线
- 产品依据：[总体规划](13-executive-decision-platform.md)、[业务流程](14-end-to-end-workflows.md)、[技术蓝图](15-ontology-causal-agent-blueprint.md)、[交付路线](16-delivery-roadmap.md)

## 当前实现进度

已完成第一批可运行纵向切片：

- `/api/v2` 与独立 `eip-v2.db` 本机边界；
- 公司、解读项目、项目生命周期和双层公司/项目选择器；
- ObjectType、LinkType、Object、Link、邻居和有向最短路径；
- 通用问卷答案 CSV 的预览、确认、空答案跳过、重复合并和重复文件幂等；
- EventType、MetricType、Event、MetricObservation 和时间线；
- CSV 数据预览、对象映射确认、对象生成和来源行血缘；
- Finding、因果假设/对象边、决策选项、行动计划/执行和 Outcome 闭环；
- Agent Run、Step、确定性上下文工具和需人工确认的提案结果；
- `/v2` 原生静态前端和桌面启动器已指向 V2 页面。

已通过 `tests/test_v2_portfolio.py` 的 API/数据库切片测试，以及 V2 Python `ruff` 检查和前端 `node --check`。目前尚未声称完成 R9–R10；行业学习、相似企业迁移和旧代码物理删除必须在前置模型验证后继续推进。

## 1. 重构目标

把当前“功能很多但没有数字投影内核”的 V1，重构为面向核心管理层的企业决策投影平台。

V2 只围绕一条主线建设：

```text
管理问题
→ 问卷、访谈和实地调研
→ 初始企业对象关系图
→ 高价值业务数据接入
→ 事件、指标和时间投影
→ 发现、原因假设和情景
→ 管理层决策
→ 行动与结果
→ 更新投影、问题、模型和方法
```

重构成功的判断标准不是“旧 API 全部兼容”，而是：管理人员和分析人员能够在前端完成上述流程，任何关键对象、关系、结论和行动结果都能沿来源追溯。

## 2. 当前代码基线

只读审计结果：

| 项目 | 当前规模 |
|---|---:|
| Python 文件 | 128 |
| Python 代码 | 25,172 行 |
| 前端 HTML/CSS/JS | 4,396 行 |
| 测试 | 26 个文件、8,041 行 |
| SQLAlchemy 表 | 44 |
| API 路由文件 | 11 个业务路由、108 个端点 |
| 前端入口 | 1 个 2,400 行 `app.js` |
| Git 基线 | `main` 尚无提交，全部项目文件未跟踪 |

开发虚拟环境当前没有安装 `pytest`、`ruff` 和 `mypy`，因此现有测试通过情况尚不能作为重构起点。编码阶段第一步必须先恢复开发环境并创建可回滚的代码基线。

### 2.1 决定产品定位的缺口

1. 有 44 张表，但没有正式的 `LinkType`、`LinkInstance` 和图查询；
2. `Fact` 只能表达“实体—谓词—标量”，不能表达实体之间的业务关系；
3. 没有 Event、MetricObservation、ProjectionVersion 和 as-of 查询；
4. Agent 只有固定文本配方，缺少计划、步骤、工具调用、暂停恢复和结果评测；
5. 问卷答案能导入，但答案不会形成对象、关系和信息缺口候选；
6. 数据接入没有统一的预览、确认、转换运行、重放和血缘流程；
7. 报告、行动与结果模型没有形成闭环；
8. 飞轮围绕发布门禁而不是围绕行动结果和跨项目验证。

### 2.2 结构性冗余

- `exchange`、`extensions`、旧 `flywheel`、旧 `evaluation`、旧 `reliability` 本体加路由和存储约占一万行运行代码，却没有推进数字投影主线；
- `QuestionPack`、`AgentRecipe`、`EvalCase`、`EvalSuite`、`FixedBaseline`、`GateFailure`、`ReviewDecision` 等概念重复定义；
- 所有 ORM 模型集中在 1,139 行的 `persistence/models.py`，模块不能拥有自己的数据；
- API 路由直接创建 ORM Row、调用 Store 或组合领域对象，业务边界不一致；
- 全部路由依赖全局 Tenant/Auth Context，即使程序只在本机单用户使用；
- 前端把状态、API、路由、表单和渲染放在一个文件中，修改一处容易破坏无关页面；
- 核心表单暴露 JSON、UUID、Schema ID 等实现细节，不符合管理人员和调研人员的工作方式；
- 当前问卷答案 CSV 预览只返回计数，确认按钮捕获浏览器中的文件和当前工作区状态，不是稳定的后端暂存事务；
- 现有 Alembic 基线把大量非核心表写进首个迁移，继续增量修改只会固化错误结构。

## 3. 重构原则

1. **纵向切片优先**：每个阶段同时完成模型、数据库、服务、API、前端和测试。
2. **新内核旁路建立**：使用 `/api/v2` 和 V2 数据库建立正确模型，V1 只作为迁移来源。
3. **不长期双轨**：V2 完成对应能力后立即关闭 V1 页面和路由，最终物理删除旧代码。
4. **单机优先**：当前只有本机所有者，不建设账号体系、多租户和云生产能力。
5. **本体先于 Agent**：没有对象、关系、事件和指标工具，不开发新的 Agent 配方。
6. **结果先于飞轮**：没有行动和 Outcome，不建设行业学习或排名。
7. **不用 JSON 代替产品**：JSON 只用于底层扩展属性；核心操作必须有结构化页面。
8. **SQLite 先证明模型**：不提前引入图数据库、消息队列、对象存储和微服务。
9. **来源必须可回溯**：轻量化安全不等于放弃数据来源、转换过程和结论依据。
10. **删除必须有替代或明确终止**：避免保留“以后可能有用”的半成品。

## 4. 产品和命名收敛

V2 统一使用以下词汇，旧词不得继续扩散：

| V1 | V2 | 说明 |
|---|---|---|
| Tenant | 删除 | 当前是单机所有者，不作为领域概念 |
| Workspace | ProjectionProject | 一次有明确管理问题和范围的企业解读项目 |
| CanonicalEntity | ObjectInstance | 企业数字投影中的对象 |
| Fact | PropertyAssertion / Link / Event / MetricObservation | 按真实语义拆分 |
| Artifact | 删除总称 | 改为 Statement、Observation、Finding 等明确类型 |
| Review | Confirmation / Decision | 根据业务行为命名 |
| Report | ManagementBrief | 由投影和结论生成的管理视图或导出物 |
| Recommendation | DecisionOption | 进入管理层比较和选择流程 |
| Action | ActionPlan / ActionRun | 区分计划和实际执行 |
| Feedback | Outcome | 只接受明确业务结果作为学习依据 |
| Registry Asset | Pattern / Playbook | 表达可复用的问题、映射和改善方法 |

全局标识统一为 UUID；API 字段统一使用 `id`，不再同时出现 `company_id`、`workspace_id`、`artifact_id` 等不同响应主键风格。URL 中仍通过资源名称表达类型。

## 5. 目标模块化单体

```text
src/enterprise_insight/
├── app.py
├── config.py
├── platform/
│   ├── database.py
│   ├── errors.py
│   ├── local_context.py
│   ├── model_gateway.py
│   └── jobs.py                 # 到 Agent 阶段才启用
├── modules/
│   ├── portfolio/              # Company、ProjectionProject、项目状态
│   ├── research/               # 问卷、访谈、证据、陈述、未知、导入
│   ├── ontology/               # 类型系统、约束和版本
│   ├── projection/             # 对象、关系、事件、指标、图和时间投影
│   ├── integration/            # 文件接入、映射、转换、冲突和血缘
│   ├── decision/               # Finding、因果、情景、决策、行动、结果
│   ├── agents/                 # 持久化运行、步骤、工具和评测
│   └── learning/               # Pattern、Playbook、Peer、TransferTrial
└── web/
    ├── index.html
    ├── js/
    │   ├── api.js
    │   ├── store.js
    │   ├── router.js
    │   ├── components/
    │   └── pages/
    └── css/
```

每个业务模块最多包含：

```text
domain.py       纯领域对象和规则
models.py       本模块 SQLAlchemy 表
schemas.py      API 输入输出
service.py      用例编排和事务边界
repository.py   非简单查询才建立
api.py          薄路由
```

### 5.1 依赖规则

```text
web → api → service → domain
                    → repository → module models → platform.database
```

- `domain.py` 不得导入 FastAPI、SQLAlchemy 或其他基础设施；
- 路由不得直接创建 ORM Row；
- 模块只能通过对方的公开 service/facade 或稳定 ID 引用协作；
- ORM 表归属业务模块，不再放入全局 `persistence/models.py`；
- 跨模块引用优先保存目标 ID，不建立循环 ORM relationship；
- 一个 HTTP 请求只有一个明确事务边界。

## 6. 模块去留矩阵

| 当前模块 | 决策 | V2 去向 |
|---|---|---|
| `application/trusted_kernel` | 拆解 | Company/Project 进入 `portfolio`；Evidence 进入 `research` |
| `domain`、`contracts` | 重写 | 类型回到所属业务模块，删除全局大杂烩 |
| `survey` | 保留核心、重写边界 | 进入 `research`；保留问卷版本、答案修订和导出规则 |
| `semantics` | 保留算法、重写模型 | Entity → Object；Mapping → Integration；Fact 拆分 |
| `reporting` | 保留少量状态规则 | 进入 `decision`；删除独立大报告聚合和通用 reporting record |
| `orchestration` | 重写 | 进入 `agents`，从固定配方改为持久化工具工作流 |
| `model_gateway` | 保留并简化 | 进入 `platform/model_gateway.py` |
| `evaluation` | 删除旧实现后重建 | 只保留 Agent/Mapping/Playbook 的持久化评测 |
| `flywheel` | 删除 | 后期以 Outcome、Pattern、TransferTrial 重建 `learning` |
| `exchange` | 删除 | 当前只保留普通文件导入导出，不保留 Edge—Hub 协议 |
| `extensions` | 删除 | 核心稳定后再按真实 Connector 需求设计，不保留通用沙箱 |
| `reliability` | 删除旧实现 | Agent 阶段只实现真实使用的 Job/Worker，不保留死 Outbox |
| `security` | 删除产品模块 | 取消登录/RBAC/Tenant；仅保留本机模型密钥读写为平台工具 |
| `infrastructure/memory` | 删除 | 测试使用 SQLite 内存数据库，不维护第二套 Repository |
| `workflows/state_machine` | 合并 | 业务状态机归属 Project、ActionRun 等具体模块 |
| `api/routes/*` | 全部重组 | 按 V2 模块提供 `/api/v2` 路由 |
| 单文件前端 | 全部拆分 | 原生 ES Modules，无 Node 构建链 |

### 6.1 明确物理删除范围

对应 V2 替代完成后，物理删除：

- `src/enterprise_insight/exchange/`；
- `src/enterprise_insight/extensions/`；
- `src/enterprise_insight/flywheel/`；
- 旧 `evaluation/`、`reliability/`、`security/`；
- 旧 `persistence/*_store.py` 和集中式 `models.py`；
- `/api/v1` 的 exchange、extensions、flywheel、auth、evaluation 路由；
- 前端登录、分部总部交换、行业发布门禁、隐私 Agent 页面；
- 对应旧测试、交换脚本、容器档位和历史迁移；
- 已停用的安全治理、Edge—Hub 和旧交付文档；
- `SECURITY.md` 等不再代表实际产品能力的模板。

以下技术能力不会以“安全模块”保留，但有直接运行价值时可用更小实现替代：本机回环绑定、模型 API Key 本地保存、数据库备份、异常处理。

## 7. V2 数据模型

V2 不一次创建全部远景表。每个阶段只增加完成当前纵向闭环所需的表。

### 7.1 核心项目

```text
companies
projection_projects
project_decision_questions
```

`ProjectionProject` 必须包含公司、目标、范围、首个价值流、状态、成功指标和当前投影版本。

### 7.2 本体定义

```text
ontology_versions
object_types
link_types
event_types
metric_types
action_types
```

类型定义至少包含稳定 key、名称、说明、属性 Schema、约束和版本。`LinkType` 额外定义源类型、目标类型、方向和基数。

### 7.3 数字投影实例

```text
objects
object_identifiers
object_aliases
property_assertions
links
events
metric_observations
projection_versions
projection_changes
provenance_links
```

关键规则：

- `links` 的 source 和 target 必须都是 Object；
- Object、Link、Event 和 Property 都支持 `valid_from`、`valid_to` 和 `observed_at`；
- `projection_changes` 记录一次确认操作改变了什么；
- `provenance_links` 统一关联来源记录、问卷答案、Evidence、转换运行或 Agent ToolCall；
- 不允许用 JSON 属性伪装应当存在的关系、事件和指标。

### 7.4 调研

```text
research_missions
question_packs
questions
assessments
respondents
answers
evidence
research_records             # Statement / Observation / Unknown
projection_candidates        # 候选 Object / Link / Event / Metric / Hypothesis
information_gaps
imports                      # 预览、确认、结果
```

问卷答案 CSV 导入必须先持久化 `imports` 暂存记录，再通过 `import_id` 确认。确认时使用预览时的文件哈希、公司和项目，不读取已经变化的浏览器状态。

### 7.5 数据接入与血缘

```text
data_sources
source_schemas
import_batches
source_records
mapping_programs
pipeline_runs
pipeline_steps
lineage_links
data_issues
```

第一版只支持 CSV 和 Excel。一个 Mapping Program 能明确生成 Object、Property、Link、Event 或 MetricObservation。

### 7.6 决策闭环

```text
findings
causal_hypotheses
causal_edges
decision_options
decisions
action_plans
action_runs
outcomes
management_briefs
```

Finding 必须引用对象路径、指标或 Evidence；ActionRun 必须有 owner、目标指标、基线、预期结果和测量窗口；Outcome 必须区分有效、无效、无法判断、副作用和未执行。

### 7.7 Agent 和学习

```text
agent_runs
agent_steps
tool_calls
evaluation_cases
evaluation_results
patterns
playbooks
company_fingerprints
peer_cohorts
transfer_trials
```

这些表只在前置图内核和行动闭环可用后创建。

## 8. 数据迁移策略

当前数据库不原地改造成 V2。采用一次性、可重复的旁路迁移：

```text
data/eip.db              V1 只读来源，迁移前复制备份
data/eip-v2.db           V2 新数据库
V1 importer              读取旧表并写入 V2 用例服务
migration_report.json    数量、跳过项、错误和 ID 对照
```

迁移顺序：

1. Company → Company；
2. Workspace → ProjectionProject；
3. QuestionPack、Assessment、Respondent、Answer → Research；
4. Evidence → Evidence；
5. CanonicalEntity、ExternalIdentifier、Alias → Object；
6. Fact：标量进入 PropertyAssertion；能解析成实体引用的只生成待确认 Link Candidate；
7. Finding、Recommendation、Action → Decision 模块候选；
8. AgentRun 只保留为历史记录，不迁移为可恢复的新运行；
9. Tenant/User/Credential、Exchange、Extension、Contribution、Registry、Outbox、Idempotency 不迁移；
10. 生成数量对账和无法迁移清单，用户确认后才切换启动脚本。

V2 Alembic 从新的 `0001_core_projection` 开始。旧迁移在切换完成前保留，切换后和 V1 代码一起删除；V1 迁移读取逻辑通过一个固定测试数据库样例保留，不保留整套旧运行时。

## 9. V2 API 设计

统一前缀 `/api/v2`，使用资源式 URL、统一分页和 Problem Details。当前本机模式不需要 Bearer Token。

### 9.1 项目

```text
GET/POST   /companies
GET/PATCH  /companies/{company_id}
GET/POST   /companies/{company_id}/projects
GET/PATCH  /projects/{project_id}
POST       /projects/{project_id}/transitions
GET        /projects/{project_id}/overview
```

### 9.2 调研

```text
GET/POST   /projects/{project_id}/research/missions
GET/POST   /projects/{project_id}/question-packs
GET/POST   /projects/{project_id}/assessments
GET/POST   /assessments/{assessment_id}/answers
POST       /projects/{project_id}/imports/questionnaire-csv:preview
POST       /imports/{import_id}:commit
GET        /imports/{import_id}
GET        /projects/{project_id}/research/records
GET        /projects/{project_id}/projection-candidates
POST       /projection-candidates/{candidate_id}:confirm
POST       /projection-candidates/{candidate_id}:reject
```

### 9.3 本体和投影

```text
GET/POST   /projects/{project_id}/ontology/object-types
GET/POST   /projects/{project_id}/ontology/link-types
GET/POST   /projects/{project_id}/ontology/event-types
GET/POST   /projects/{project_id}/ontology/metric-types
GET/POST   /projects/{project_id}/graph/objects
GET/POST   /projects/{project_id}/graph/links
GET        /projects/{project_id}/graph/neighbors
POST       /projects/{project_id}/graph/paths:query
GET        /projects/{project_id}/graph/subgraph
GET/POST   /projects/{project_id}/events
GET/POST   /projects/{project_id}/metrics/observations
GET        /projects/{project_id}/projection
GET        /projects/{project_id}/projection:diff
```

### 9.4 接入、决策和 Agent

```text
POST       /projects/{project_id}/data-imports:preview
POST       /data-imports/{import_id}:map
POST       /data-imports/{import_id}:commit
GET        /pipeline-runs/{run_id}
GET        /projects/{project_id}/lineage

GET/POST   /projects/{project_id}/findings
GET/POST   /projects/{project_id}/causal-hypotheses
POST       /projects/{project_id}/scenarios:run
GET/POST   /projects/{project_id}/decisions
GET/POST   /projects/{project_id}/actions
POST       /actions/{action_id}/transitions
POST       /actions/{action_id}/outcomes

GET/POST   /projects/{project_id}/agent-runs
GET        /agent-runs/{run_id}
POST       /agent-runs/{run_id}:pause
POST       /agent-runs/{run_id}:resume
POST       /agent-runs/{run_id}:cancel
```

## 10. 前端重构

继续使用原生 HTML/CSS/ES Modules，不引入 React、Node 构建和大型状态框架。原因是当前核心风险是领域模型，不是前端技术栈。

### 10.1 一级导航

```text
企业项目
管理层调研
数字投影
数据接入
分析与因果
决策与行动
Agent 工作台
学习中心（后期开启）
```

模型服务和 API Key 放入右上角“设置”，不作为一级业务模块。登录、安全、交换和行业发布页面删除。

### 10.2 页面规则

- 页面同时显示当前公司和当前项目，切换公司后只展示该公司的项目；
- 所有导入必须具有“选择文件 → 预览内容和问题 → 配置映射 → 确认 → 结果与错误下载”；
- 核心页面不得要求用户手写 JSON 或 UUID；
- 每个页面都实现 loading、empty、error、success 和 partial 状态；
- 错误显示人能理解的字段名称、修复建议和 correlation id；
- 表单提交后使用后端响应更新状态，不依赖旧闭包中的公司、项目或文件；
- 图页面首版使用本地 SVG 渲染和 `GraphRenderer` 接口，不依赖 CDN；
- 管理层首页只展示变化、风险、原因、决策、行动和结果，不暴露数据工程细节。

### 10.3 前端文件边界

`api.js` 只负责 HTTP；`store.js` 只负责状态；`router.js` 只负责导航；每个页面模块只读取公开 store 和调用 API client。单文件建议不超过 400 行，超过时按组件拆分。

## 11. Agent、因果和飞轮的实现顺序

### 11.1 Agent

第一批只实现四个角色：

1. Research Agent：从答案和访谈提取对象、关系、未知和追问候选；
2. Modeling Agent：把确认内容映射到本体类型和投影候选；
3. Graph Analyst：使用图、指标和时间工具寻找异常路径；
4. Critic Agent：检查结论是否缺少来源、反证或时间范围。

Agent 只能通过工具接口读写：`research.search`、`graph.neighbors`、`graph.paths`、`metrics.series`、`evidence.get`、`candidate.propose`、`finding.propose`。任何写操作先产生业务候选，不允许 Agent 直接写 ORM。

### 11.2 因果

顺序为描述 → 时序 → 假设 → 试点 → 结果评估。首版只实现因果假设图、支持/反驳证据、前后比较和匹配对照；不声称自动发现真实因果。

### 11.3 飞轮

```text
人工确认 + 行动执行 + Outcome
→ EvaluationResult
→ 改进问题、映射、本体、Agent 工具或 Playbook
→ 第二个项目 TransferTrial
→ 新 Outcome
→ 调整适用条件和质量
```

没有两个独立项目的 Outcome 前，不计算“行业最佳实践”，只允许保存候选 Pattern。

## 12. 测试和质量门

### 12.1 编码前基线

1. 安装 `.[dev]`；
2. 执行现有 `pytest`、`ruff`、`mypy`；
3. 记录失败而不是为了绿灯删除测试；
4. 创建不含 `data/`、`private/`、`.env` 的 Git 基线提交；
5. 备份 V1 数据库并记录哈希和表数量。

### 12.2 每个切片必须通过

- 领域规则单元测试；
- SQLite Repository 集成测试；
- API 请求/响应契约测试；
- Alembic 全新升级测试；
- 从真实 V1 样例迁移和数量对账测试；
- 前端关键旅程浏览器测试；
- `ruff` 和 strict `mypy`；
- 文档链接与 OpenAPI 快照检查。

### 12.3 架构适应性测试

- domain 不得导入 FastAPI、SQLAlchemy、security、exchange 或旧 persistence；
- `/api/v2` 路由不得直接导入 ORM Row；
- V2 不得引用 exchange、extensions、旧 flywheel 和旧 auth；
- 所有 Project 资源查询必须显式带 `project_id`；
- 所有 Object/Link/Event/Metric 必须能找到至少一个 provenance 或明确标记为人工创建；
- 前端发布的每个 API 操作都必须存在端到端测试；
- 没有前端入口的后端功能不算完成。

## 13. 实施阶段与退出条件

### R0：建立可恢复基线

工作：恢复开发依赖、运行检查、确认忽略规则、备份数据库、创建基线提交和重构分支。

退出：代码可恢复；已知测试结果有记录；真实数据未进入 Git。

### R1：V2 壳与本机模式

工作：建立模块目录、V2 Base/Session、统一错误、`/api/v2`、ES Module 前端壳；取消登录页和 Tenant Context；设置页保留模型配置。

退出：一键启动直接进入项目页；V1 数据仍未改动；V2 健康检查和空数据库迁移通过。

### R2：Company 与 ProjectionProject

工作：实现公司、项目、决策问题和项目状态；重做公司/项目选择器；完成 V1 Company/Workspace 迁移。

退出：用户可创建公司和项目，切换时上下文准确，刷新页面后保持选择，迁移数量一致。

### R3：Object—Link 图内核

工作：OntologyVersion、ObjectType、LinkType、Object、Link、标识、别名、来源和图查询；建立 SVG 图页面。

退出：至少 5 类对象、8 类关系、50 个对象、100 条关系；邻居、路径、子图三个查询工作；前端可创建和查看关系。

### R4：Event—Metric—Projection

工作：EventType、MetricType、实例、时间字段、ProjectionVersion、change log、as-of 和 diff。

退出：订单到回款有至少 6 种事件和 5 种指标；可比较两个时间点；可显示价值流时间线。

### R5：调研到投影

工作：重构问卷、访谈、Evidence、Statement、Observation、Unknown、InformationGap 和 Candidate；重做问卷答案 CSV 暂存预览与确认。

退出：一份问卷答案 CSV 和一次访谈可以形成 Projection Candidate；前端能预览、确认、拒绝和修正；空答案不导入；重复导入有明确结果。

### R6：CSV/Excel 数据接入与血缘

工作：数据源、Schema 剖析、字段映射、PipelineRun、SourceRecord、LineageLink、冲突和重放。

退出：CRM 客户与 ERP 订单两个文件映射到同一图；每个结果可回溯原始行；失败批次可修正后重放。

### R7：分析、因果、决策、行动与 Outcome

工作：Finding、CausalHypothesis、Scenario、DecisionOption、Decision、ActionRun、Outcome 和管理简报。

退出：从图中形成一个可验证原因假设；管理层选择行动；导入行动后数据；结果被判定并更新投影。

### R8：本体感知 Agent

工作：持久化 AgentRun/Step/ToolCall、Worker、四类 Agent、图和指标工具、固定评测集、暂停恢复。

退出：Agent 使用至少三个确定性工具；运行可恢复；结论引用对象路径、指标、时间和来源；回归评测可比较。

### R9：学习与相似企业

工作：Pattern、Playbook、CompanyFingerprint、PeerCohort、TransferTrial 和 Outcome 聚合。

退出：一个经结果验证的模式在第二个虚拟企业中试用；系统解释相似度、适用差异和迁移结果。

### R10：切换与物理删除

工作：停止挂载 `/api/v1`；删除去留矩阵中的旧代码、路由、表、测试、页面、脚本和停用文档；更新 README、使用手册和桌面启动器。

退出：仓库不再导入旧模块；全新安装只创建 V2 Schema；V1 数据迁移报告通过；桌面一键启动完成完整演示；代码搜索无 exchange、RBAC、DLP、Tenant 和旧 Fact 主模型残留。

## 14. 每阶段提交与回滚

每个阶段至少有三个独立提交：

1. Schema/Domain；
2. API/Service；
3. Frontend/Test/Docs。

阶段结束打标签 `v2-rN-complete`。数据库切换前保留 V1 文件副本；发生问题时回退启动配置和代码标签，不对 V1 数据库执行逆向写入。

## 15. 风险控制

| 风险 | 应对 |
|---|---|
| 重构再次变成大而全 | 每个任务必须推动当前阶段退出条件，否则不进入看板 |
| 一次删太多导致无法运行 | 替代能力通过后再停止挂载并删除旧模块 |
| 新旧概念混用 | 建立统一词表和架构测试，禁止 V2 引用旧类型 |
| 数据迁移不可验证 | 独立 V2 数据库、重复迁移、数量对账、ID 映射和错误报告 |
| 后端完成但前端不可用 | 每个切片把浏览器旅程列入完成定义 |
| Agent 掩盖数据模型问题 | R8 才开发 Agent；此前只允许确定性服务和虚拟数据 |
| 过早引入图数据库 | SQLite 邻接表和查询接口先通过验收，再按真实规模决定 |
| 同行数据不足却给出结论 | 两个独立 Outcome 前只输出候选模式，不输出最佳实践 |

## 16. 首个编码批次

计划获准后，第一批只做 R0–R2，不并行开发 Agent、因果或行业飞轮：

1. 恢复开发环境并记录基线；
2. 建立安全的 Git 基线和重构分支；
3. 建立 `/api/v2` 与 V2 数据库；
4. 建立 `platform`、`portfolio` 模块；
5. 实现 Company 和 ProjectionProject；
6. 拆分前端壳并实现公司/项目切换；
7. 编写 V1 Company/Workspace 迁移器；
8. 完成测试、启动和桌面入口验证。

R2 通过后，立即进入 R3 的 Object—Link 图内核。这是整个项目最重要的开发阶段。

## 17. 最终验收

最终版本必须由一个固定的虚拟制造企业完成以下浏览器旅程：

1. 创建企业和决策项目；
2. 导入并预览问卷答案 CSV；
3. 从回答确认对象、关系和未知；
4. 导入 CRM 客户与 ERP 订单文件；
5. 展示客户—订单—生产—交付—开票—回款图和时间线；
6. 查询一个跨系统对象路径；
7. 形成一个 Finding 和两个原因假设；
8. 比较行动选项并记录管理层决策；
9. 执行行动、导入结果并评价 Outcome；
10. 更新企业投影和 Playbook；
11. 在第二个虚拟企业中试用该 Playbook；
12. 导出管理简报和可迁移的项目包。

上述任一步只能通过 API 或只能手工改数据库完成，均视为未完成。
