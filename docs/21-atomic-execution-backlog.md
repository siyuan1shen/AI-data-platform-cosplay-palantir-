# V2 原子执行清单

- 状态：可执行
- 日期：2026-08-30
- 规格依据：20 号文档
- 执行原则：按 ID 顺序完成；未通过当前任务验证不得开始下一依赖任务

本文把 R1F–R10 拆成编码任务。每个任务已经指定输入、文件边界、输出和验证。编码阶段的默认动作是照单执行，不再重新设计。

## 1. 执行纪律

### 1.1 一个任务的固定循环

```text
确认依赖任务已通过
→ 只打开本任务列出的模块
→ 先增加/调整迁移和失败测试
→ 实现 domain/repository/service/API
→ 实现页面入口
→ 运行本任务测试
→ 运行受影响阶段回归
→ 更新能力状态
→ 提交一个可回滚 commit
```

### 1.2 禁止行为

- 不在同一个任务中顺手建设后续阶段；
- 不保留并行的新旧 V2 实现超过一个阶段；
- 不以临时 JSON 字段代替 20 号文档要求的核心关联；
- 不只做 API 而延后页面；若任务明确为基础后端任务，页面任务必须紧随其后；
- 不直接编辑用户运行数据库验证迁移；只在副本执行；
- 不使用真实企业、真实问卷或真实业务文件作为 fixture；
- 不因测试困难跳过并发、重启、幂等和跨项目负向测试；
- 不在 R10 前删除 V1。

### 1.3 提交与分支

- 当前仓库是 unborn repository（尚无 `HEAD`，所有文件均未进入首个 commit）。R1F-001 必须先完成 tracked-data/secret 检查，确认 `.gitignore` 后在默认分支创建一次 `chore: establish sanitized palantir baseline`；不得把数据库、导出包、`.env` 或真实资料放入该 commit；
- 建立 baseline commit 后再创建实施分支 `codex/v2-executable-rebuild`；
- commit 格式：`v2(<task-id>): <结果>`；
- 每个任务一个 commit；迁移与使用该迁移的首个模型必须同 commit；
- 阶段出口使用 tag：`v2-r1f`、`v2-r2` … `v2-r10`；
- 阶段出口前执行全量质量门并生成 `artifacts/acceptance/<stage>.json`，该目录只保存合成数据测试结果。

## 2. 目标测试目录

```text
tests/v2/
├─ unit/
│  ├─ platform/
│  ├─ portfolio/
│  ├─ research/
│  ├─ ontology/
│  ├─ projection/
│  ├─ integration/
│  ├─ causal/
│  ├─ decision/
│  ├─ agents/
│  └─ learning/
├─ integration/
│  ├─ test_migrations.py
│  ├─ test_scope_constraints.py
│  ├─ test_projection_commit.py
│  ├─ test_pipeline_recovery.py
│  ├─ test_agent_recovery.py
│  └─ test_package_roundtrip.py
├─ api/
├─ browser/
├─ migration/
├─ fixtures/
│  ├─ questionnaire_csv/
│  ├─ crm/
│  ├─ erp/
│  └─ packages/
└─ conftest.py
```

### 2.1 阶段文件所有权

| 阶段 | 允许新增/主要修改的后端目录 | 对应前端 page modules | 阶段 E2E |
|---|---|---|---|
| R1F | `v2/platform`、`v2/api.py`、`app.py`、`config.py` | `main/router/store/api-client/errors`、通用 components | `test_shell.py`、`test_job_recovery.py` |
| R2 | `v2/portfolio` | `companies.js`、`charter.js` | `test_r2_project_definition_journey.py` |
| R3 | `v2/ontology`、`v2/projection` 的 object/link 部分 | `ontology.js`、`projection.js` | `test_r3_graph_journey.py` |
| R4 | `v2/projection` 的 event/metric/version 部分 | `value-stream.js`、`projection-time.js` | `test_r4_temporal_projection_journey.py` |
| R5 | `v2/research` | `research.js`、`candidates.js` | `test_r5_research_projection_journey.py` |
| R6 | `v2/integration` | `integration.js`、`lineage.js` | `test_r6_integration_lineage_journey.py` |
| R7 | `v2/analysis`、`v2/causal`、`v2/decision` | `analysis.js`、`causal.js`、`decisions.js` | `test_r7_decision_outcome_journey.py` |
| R8 | `v2/agents` | `agents.js`、`settings-runtime.js` | `test_r8_agent_runtime_journey.py` |
| R9 | `v2/learning` | `learning.js`、`hq-peers.js`、`hq-assets.js` | `test_r9_peer_transfer_journey.py` |
| R10 | `v2/reporting`、migration reader、launcher | `home.js`、`reporting.js`、`packages.js` | `test_r10_complete_journey.py` |

跨模块改动只能发生在明确写有依赖的任务中。例如 R7 Outcome 回写可以调用 Projection service，但不得在 R7 直接改 `projection/models.py` 规避既有命令；若需要扩展 port，先在 Projection 侧增加向后兼容接口和测试。

## 3. R1F：执行基础收口

目标：先消除后续阶段会重复解决的迁移、错误、并发、任务、数据目录和前端壳问题。R1F 是对原 R1 的补完，不改变产品范围。

### R1F 任务表

| ID | 依赖 | 精确改动 | 验证 |
|---|---|---|---|
| R1F-001 | 无 | `pyproject.toml` 增加 Playwright dev 依赖；冻结当前 OpenAPI、204 项回归和 V2 浏览器 smoke；建立 `tests/v2` 目录及当前行为基线；执行 tracked-data/secret 检查并创建 sanitized 首个 baseline commit，再切实施分支 | 原测试全过；基线文件只含合成数据；`HEAD` 存在；Git index 无数据库/密钥/真实资料 |
| R1F-002 | 001 | 建立 20 号目录包；将 flat V2 文件移动为模块文件，先只改 import，不改行为；`v2/api.py` 聚合 router | OpenAPI 路径数量和响应 Schema 与基线等价 |
| R1F-003 | 002 | 新建 `platform/clock.py`、`ids.py`、`errors.py`；所有新代码注入 Clock/ID；旧 `utc_now` 仅兼容 | 固定时钟和确定性 ID 单测 |
| R1F-004 | 002 | 新建独立 V2 Alembic env/version table；`v2_0001_current_schema_baseline` 精确复刻当前 Schema，`v2_0002` 建 platform 表；实现备份+fingerprint 完全匹配后才 stamp；移除启动 `create_all` | 空库升级、重复升级、当前 V2 副本 stamp+升级、fingerprint 不匹配拒绝、备份测试 |
| R1F-005 | 003 | 实现 correlation middleware、20.5 错误对象和 FastAPI/Pydantic 转换；前端 `errors.js` 只解析该格式 | 每类 HTTP 错误 API 测试；字段错误准确落到表单 |
| R1F-006 | 004,005 | 建 `resource_revisions`、revision helper、`expected_revision` validator；先接 Company/Project | 两客户端旧 revision 更新返回 409 且数据未覆盖 |
| R1F-007 | 004,005 | 建 `operation_receipts`、request hash 和 decorator/service helper；接 import confirm | 同键同体只执行一次；同键异体 409；重启后仍幂等 |
| R1F-008 | 004 | 建 `jobs/job_steps/job_events`、JobService 和 claim SQL；不接业务 handler | SQLite 双 worker 只 claim 一次；lease/retry/cancel 单测 |
| R1F-009 | 008 | 建 `worker.py`、reaper、heartbeat、handler registry；应用 lifespan 本机启动线程；提供独立 worker CLI | 杀死 handler 后 lease 到期恢复；cancel 在 step 边界生效 |
| R1F-010 | 002 | 拆分开发者端与高管端静态资源；实现各自的 router/store/api client | 两个入口的页面 smoke；刷新恢复 |
| R1F-011 | 005,008,010 | 实现 `JobProgressDrawer`、polling、retry/cancel 和统一 Toast/Form error | 合成 3-step Job 可视进度、失败详情和重试 |
| R1F-012 | 004 | `config.py` 默认数据/private/package 路径迁到 `%LOCALAPPDATA%/EnterpriseInsight`；测试使用 tmp；补 `.gitignore` guard | 从仓库目录运行后无 db/upload/package 新文件 |
| R1F-013 | 009,012 | 实现 `/api/v2/diagnostics`：app/db/migration/worker/files/model；更新 launcher 等待 readiness 再开浏览器 | 数据库落后、worker 停止、目录不可写均有明确诊断 |
| R1F-014 | 001 | 建东岭精工/海川装备 fixture builder，不落运行库；固定 CSV、坏文件和预期计数 | fixture hash 固定；扫描确认无真实公司/人名/密钥 |
| R1F-015 | 001–014 | 建统一质量脚本：ruff、mypy、pytest、JS parse、OpenAPI/API client contract、tracked secret/data guard | 一条命令全部通过；输出 `r1f.json` |

### R1F 文件边界

- 新建：`src/enterprise_insight/v2/platform/*`；
- 修改：`app.py`、`config.py`、`api/dependencies.py`、`__main__.py`、`web/static_developer/*`、`web/static_executive/*`；
- 新建：`migrations_v2/*`；旧 `migrations/*` 不修改；
- 新建：`tests/v2/platform/*`、`tests/v2/browser/test_shell.py`；
- 旧业务模型只移动，不在此阶段重构其语义。

### R1F 出口

全新目录一键启动；Schema 由迁移生成；数据不写仓库；错误统一；Job 可恢复；公司/项目选择和现有功能无回归。

## 4. R2：Company、Project 与阶段门闭环

### R2 任务表

| ID | 依赖 | 精确改动 | 验证 |
|---|---|---|---|
| R2-001 | R1F | 用 `v2_0003_portfolio_stage_gates` 重构 companies/projects 并建 Portfolio 新表；回填当前数据 revision=1、stage 映射、archived null | 当前数据数量/ID/name/company 引用对账 |
| R2-002 | 001 | Company create/get/list/patch/archive 全部接 revision、分页和错误契约 | 空白、短名、重复策略、旧 revision、archive 过滤测试 |
| R2-003 | 002 | Companies 页面补编辑、归档、空状态、恢复选择；归档当前公司时清空项目 | 只用页面完成 Company 生命周期 |
| R2-004 | 001 | 建 project_charters、decision_questions、scope_boundaries、success_metric_targets、research_plans | FK、范围和 revision 约束测试 |
| R2-005 | 004 | 实现 Charter get/put 和 Project patch/archive；value_stream_key 首版只允许 `order_to_cash` | 必填 expected_decision、deadline 边界和并发测试 |
| R2-006 | 004 | 实现 DecisionQuestion CRUD；priority 1–5；至少一个 active 高优问题是前进条件 | 空白、跨项目、排序和归档测试 |
| R2-007 | 004 | 实现 ScopeBoundary、SuccessMetric、ResearchPlan 服务和 API | include/exclude 冲突、窗口顺序、空计划测试 |
| R2-008 | 005–007 | 在 `portfolio/domain.py` 实现项目状态机和每一阶段 gate rule；evaluate 不写 stage | 每个合法/非法边、全部 gate check 纯单测 |
| R2-009 | 008 | 实现 evaluate/transition；transition 同事务保存 evaluation、revision snapshot 和 stage | 未满足 gate 返回 checks；重复幂等；并发 409 |
| R2-010 | 003,005–009 | Charter 页面实现五个编辑区、stage rail、gate drawer 和修复链接 | 业务人员从 draft 推进到 research；失败不丢表单 |
| R2-011 | 010 | overview 聚合当前阶段、完成率、未知/冲突/Job 待办占位；不跨模块直查 Row | 聚合 query count 上限和空项目结果测试 |
| R2-012 | 001–011 | 浏览器从空库创建公司、项目、章程、问题、范围、指标、计划并推进；重启恢复 | `test_r2_project_definition_journey.py` 通过 |

### R2 出口

- 所有项目定义操作有页面；
- 公司/项目选择始终分开；
- 每次前进能解释阻断项；
- 重启后仍处于正确公司、项目和阶段。

## 5. R3：Object—Link 本体与图内核

### R3 任务表

| ID | 依赖 | 精确改动 | 验证 |
|---|---|---|---|
| R3-001 | R2 | 建 ontology_types/type_versions/releases/release_items 和约束表迁移 `v2_0004` | kind/stable_key/version/release 唯一与状态约束 |
| R3-002 | 001 | 实现 Type/TypeVersion create/list/get/publish；published immutable | 同 key 冲突、非法 kind、修改 published 失败 |
| R3-003 | 001,002 | 实现 OntologyRelease create/validate/publish；验证引用 published type version | 缺版本、重复 type、draft item 均阻断发布 |
| R3-004 | 002 | 建完整订单到回款模板 JSON 与 installer：9 ObjectType、10 LinkType、7 EventType、5 MetricType、5 ActionType、4 Interface；R3 只启用 Object/Link 工作流 | 重复安装幂等；模板 hash/version 固定；类型数量与 20 号规格一致 |
| R3-005 | 001 | 建 objects/object_versions/external_identities/object_aliases 和 ChangeSet 基础迁移 `v2_0005` | version 连续、外部身份唯一、项目范围约束 |
| R3-006 | 005 | 实现 draft ChangeSet、Object create/version/retire；properties 按发布 Schema 校验 | 编辑不覆盖 v1；退役历史可查；无半写 |
| R3-007 | 005,006 | 实现 external identity、alias、merge；merge 重定向 future query，不改旧历史 | exact identity、合并环、跨类型/跨项目负向测试 |
| R3-008 | 001,005 | 建 links/link_versions；实现端点类型、方向、基数和有效时间校验 | 1:1/1:n/n:m、时间重叠和错误端点测试 |
| R3-009 | 008 | 实现 Link create/version/retire；所有变更经 ChangeSet | 两条错误 item 导致整个 ChangeSet 不提交 |
| R3-010 | 006,009 | 实现 ChangeSet validate/commit 和 ProjectionVersion sequence 1 基础 | 并发 base version 冲突；提交原子性；幂等 |
| R3-011 | 006,009 | 实现 get/find/neighbors/shortest_path，全部支持 projection version 与 valid_at | 有向/逆向、无路径、截断、退役、as-of 测试 |
| R3-012 | 011 | 实现 traverse/subgraph/aggregate；限制 depth≤6、nodes≤500、edges≤1000 | 循环图不死循环；稳定排序；truncated 标志 |
| R3-013 | 002–012 | Ontology 页面：模板、类型版本、release；Projection 页面：对象/关系详情、纠错、合并、退役、路径/子图 | 所有 R3 核心命令有前端入口 |
| R3-014 | 004–013 | 建 50 对象/100 关系 fixture；浏览器回答“订单由谁负责、客户到回款路径、瓶颈涉及哪些对象” | 三问题答案路径与预期节点完全一致 |

### R3 出口

一个发布本体、稳定对象/关系身份、不可变历史、ChangeSet 原子提交和完整图查询同时可用；当前 flat graph 服务不再作为写路径。

## 6. R4：Event—Metric—Projection 时间内核

### R4 任务表

| ID | 依赖 | 精确改动 | 验证 |
|---|---|---|---|
| R4-001 | R3 | 在 ontology specs 启用并完成模板已有的 7 EventType/5 MetricType 的 EventRole、MetricType、Constraint 工作流 | role cardinality、unit/grain/aggregation 校验 |
| R4-001A | 001 | 用 `v2_0006_projection_temporal` 建 EventParticipant、Decimal Metric 和完整 ProjectionVersion 列；立即回填当前 Event/Metric | 当前事件/指标数量、时间、对象引用和数值对账 |
| R4-002 | 001A | 建 events/event_participants；实现创建、纠正、void；全部进入 ChangeSet | 多参与者、角色错误、修订链和 as-of 测试 |
| R4-003 | 001 | 建 Numeric metric observations；实现创建、纠正、void、series | Decimal 精度、维度、重复、时间窗口测试 |
| R4-004 | R3-010,002,003 | 完成 ProjectionVersion 的 ontology release/data watermark/change items | 每次提交 sequence 连续且 parent 唯一 |
| R4-005 | 004 | 实现 projection as-of：valid_at、recorded_at、version 三种选择 | 迟到数据与业务有效时间区别测试 |
| R4-006 | 004 | 实现 version diff，覆盖 Object/Link/Event/Metric added/changed/retired | diff 可交换性基线、相同版本空 diff |
| R4-007 | 003 | 实现 metric aggregate/compare：sum/mean/median/min/max/count；口径来自 MetricType | 维度过滤、空集、不同口径拒绝合并 |
| R4-008 | 002,003 | 实现 value-stream query：阶段、事件、等待、指标、来源、未知 | 合成订单完整与缺失路径结果测试 |
| R4-009 | 004–008 | Projection/Value-stream 页面增加时间控制、版本选择、diff、时间线、趋势 | 在 UI 切换两个版本并看到准确变化 |
| R4-010 | 001–009 | fixture 构造 6 个月订单事件/指标；浏览器验证两个时间点重现 | `test_r4_temporal_projection_journey.py` |

### R4 出口

任意历史时点可重现；事件参与者和指标精度正确；版本差异可解释到 ChangeSet 与来源。

## 7. R5：调研到 Projection V1

### R5 任务表

| ID | 依赖 | 精确改动 | 验证 |
|---|---|---|---|
| R5-001 | R4 | 建 research 全表迁移 `v2_0007`；回填当前 ResearchImport/Answer 保持 ID | 旧答案数量、合并计数、prompt/answer hash 对账 |
| R5-002 | 001 | 重写问卷答案 CSV parser 为纯函数；固定列别名、空答案、同问合并、编码/BOM | 中文/英文列、空格、重复、坏 CSV、超大字段测试 |
| R5-003 | 002,R1F-007 | Preview 写 durable import_preview；Confirm 校验 hash/token/expiry/idempotency | 刷新后可确认；换文件确认失败；重复 no-op |
| R5-004 | 001 | 实现 ResearchMission/QuestionPack/QuestionVersion/SurveyResponse 查询 | question key 版本与未知题保留测试 |
| R5-005 | 001 | 实现 Interview、Fragment、Observation、Evidence create/edit；附件接 stored_files | 来源、时间、项目范围和文件 hash 测试 |
| R5-006 | 001,004,005 | 实现 Statement 提取候选接口；无模型时使用确定性人工创建路径 | Statement 不自动成为 Object/Fact |
| R5-007 | 001 | 实现 Unknown、Conflict、ConflictMember、FollowUp 状态机与 API | 冲突至少 2 member；关闭 Unknown 必填 resolution |
| R5-008 | 006,007 | 实现 projection candidate 生成：object/link/event/metric；保存来源和置信 | payload 按目标 type Schema 校验 |
| R5-009 | 008 | 实现 Candidate Accept/Edit/Merge/Reject；review immutable，candidate revision 前进 | 二次决定冲突、merge target 错误、理由必填 |
| R5-010 | 009,R3-010 | 实现 candidates commit：只取 accepted/edited，生成单一 Projection V1 ChangeSet | 一项失败整体不提交；已提交候选不重复 |
| R5-011 | 003–010 | Research/Candidate 页面完成导入、访谈、观察、证据、未知、冲突、追问、Review | 所有操作只用 UI；Preview/Confirm 统一组件 |
| R5-012 | 004–010 | 实现研究覆盖率与 project gate：每个高优问题有 evidence 或 Unknown | 缺口列表能跳转到对应页面 |
| R5-013 | 001–012 | 浏览器从问卷答案+访谈形成 1 价值流、3 假设、候选并提交 Projection V1 | `test_r5_research_projection_journey.py` |

### R5 出口

观点、观察、证据、未知和冲突语义分开；任何调研产物都经过人工 Review 和原子 ChangeSet 后才进入 Projection。

## 8. R6：ERP/CRM 数据接入、孤岛映射与血缘

### R6 任务表

| ID | 依赖 | 精确改动 | 验证 |
|---|---|---|---|
| R6-001 | R5 | 建 integration/lineage 全表迁移 `v2_0008`；回填当前 batches/raw/mapping/lineage | 当前对象映射结果与 source row 数对账 |
| R6-002 | 001 | 实现 Source/ConnectorConfig/SourceSchema CRUD；首版 connector kind `csv|excel` | 配置、归档、Schema version 测试 |
| R6-003 | 002 | `pyproject.toml` 增加 `openpyxl`；CSV reader 流式读取；Excel reader 只读首个指定 sheet；生成 profile | BOM、编码、空列、数字/日期推断、20MB 上限 |
| R6-004 | 003 | data-import preview/confirm；RawRecord 不可变，source_record_key 明确 | 重复文件幂等；同 key 新 revision；坏行报告 |
| R6-005 | 001 | 实现 Mapping DSL AST/Pydantic models 和白名单 evaluator | 每个 op 单测；拒绝 eval/SQL/未知 op/递归过深 |
| R6-006 | 005 | Object MappingProgram/Version create/preview/publish | name/identity/properties、过滤、去重和 Schema 校验 |
| R6-007 | 005 | Link Mapping；两端 object_ref exact/alias/rule/candidate | 端点缺失、类型错、重复边、时间测试 |
| R6-008 | 005 | Event Mapping；occurred_at、role participants、properties | 缺必需 role、坏日期、多对象参与测试 |
| R6-009 | 005 | Metric Mapping；Decimal、object、dimensions、quality | 精度、单位、维度和坏数值测试 |
| R6-010 | 006–009 | 实现统一 mapping preview 汇总和 20 样例上限；不写 Projection | create/reuse/conflict/error/skip 计数准确 |
| R6-011 | 001 | 实现 identity resolution 和 alias rule；模糊候选只排序 | CRM“东岭公司”与 ERP“东岭精工有限公司”人工合并案例 |
| R6-012 | 001,011 | 实现 ImportConflict 队列与五种解决动作；resume checkpoint | 每种动作及跨项目候选负向测试 |
| R6-013 | R1F-009,006–012 | 实现 Pipeline Job steps：extract/profile/map/resolve/validate/stage/commit | step 4 崩溃重启后不重复前 3 步；进度准确 |
| R6-014 | 013 | 实现 Replay：固定 batch，选择新 mapping versions，从指定 step 开始 | 原 run immutable；新 run/ChangeSet/lineage 独立 |
| R6-015 | 001,013 | 实现 lineage node/edge 写入和 upstream/downstream/explain/impact | 任一 Object 回到文件/行/mapping/step；撤销标 stale |
| R6-016 | 002–015 | Integration/Lineage 页面完成 source、file、mapping wizard、preview、job、conflict、replay、drawer | 无开发者工具完成两个来源导入 |
| R6-017 | 003–016 | 浏览器导入 CRM+ERP，解析同一 Customer，生成 Object/Link/Event/Metric，修错并 replay | `test_r6_integration_lineage_journey.py` |

### R6 出口

两个来源在同一关系图中完成身份解析；任何结果可追溯到源行、映射版本和步骤；失败可恢复，映射可修复后重放。

## 9. R7：分析、因果、决策、行动和 Outcome

### R7 任务表

| ID | 依赖 | 精确改动 | 验证 |
|---|---|---|---|
| R7-001 | R6 | 建 analysis/causal/decision 全表迁移 `v2_0009`；迁移现有行 revision=1 | 当前 Finding/Decision/Action/Outcome ID 和引用对账 |
| R7-002 | 001,R6-015 | Finding create/edit/confirm/reject；support/contrary evidence 都用 lineage node | 无支持证据不能 confirmed；反证必须显示 |
| R7-003 | 001 | CausalVariable 与 Hypothesis CRUD；变量只引用 metric/event/expression 之一 | stable key、项目范围、expression 白名单测试 |
| R7-004 | 003 | CausalEdge/Confounder；DAG cycle 和 lag 时间方向校验 | 环、self-loop、跨假设和未测量混杂提示 |
| R7-005 | 003,004 | 实现 C1 pre_post；保存数据 query、样本、估计和 limitation | 固定数据均值/中位数/变化精确测试；样本不足 |
| R7-006 | 003,004 | 实现 interrupted_trend/event_window；至少 6+6 点 | 斜率、时间间隔、缺点和不等间隔错误测试 |
| R7-007 | 001,R4 | Scenario baseline/overlay/change/result；只运行显式 metric effect rule | 真实 Projection 无变化；未建模因素必显 |
| R7-008 | 001 | DecisionOption/OptionEvidence 完整 CRUD；关联 Scenario 和 ActionType | 成本/风险/依赖与 evidence 范围测试 |
| R7-009 | 001,008 | Decision 状态机和页面命令；selected option 必须同项目 | record/approve/cancel/execute 全状态测试 |
| R7-010 | R4-001,009 | 完成 ActionType；ActionPlan 基线/目标/窗口/里程碑；ActionRun 状态机 | baseline target 类型、非法状态、重复 start 幂等 |
| R7-011 | 010 | Outcome record；classification 与 metric evidence；先生成 draft effect | completed 前不能 Outcome；inconclusive 允许无 effect |
| R7-012 | 011,R3-010 | Outcome effect → draft ChangeSet、Hypothesis evaluation、LearningEvent/EvalCase placeholder；人工 commit | 一次 Outcome 不重复回写；拒绝 ChangeSet 不改模型 |
| R7-013 | 002–012 | Decision timeline 和页面：Finding/反证、DAG、估计、Scenario、Option、Decision、Action、Outcome | 页面能看到每步来源和状态 |
| R7-014 | 002–013 | 管理问答 deterministic assembler：九段契约；暂不调用模型 | 证据不足明确 unknown；所有数字有 lineage/time |
| R7-015 | 001–014 | 浏览器试点“审批层级是否导致交付延迟”，记录行动前后 Outcome 并查看模型变化 | `test_r7_decision_outcome_journey.py` |

### R7 出口

系统完成一个真实结构的管理问题闭环；因果措辞不超过证据等级；Outcome 明确改变或未改变哪些投影与假设。

## 10. R8：本体感知 Agent 与持久化评测

### R8 任务表

| ID | 依赖 | 精确改动 | 验证 |
|---|---|---|---|
| R8-001 | R7 | 建 agent/eval 全表迁移 `v2_0010`；旧 AgentRun 转 legacy artifact | 旧记录可读但不生成伪 ToolCall |
| R8-002 | 001 | ModelProfile CRUD 和 runtime secret memory store；诊断模型 readiness | API key 不出现在 GET/log/db；进程重启后按选择行为 |
| R8-003 | 001 | Tool registry 与 decorator；固定 17 个 @1 工具 input/output Schema | 重名/未知版本/坏输入/超时/结果过大测试 |
| R8-004 | 003 | 实现 portfolio/research/projection 图工具 | 工具结果固定在 context ProjectionVersion |
| R8-005 | 003 | 实现 metric/lineage/analysis/causal 工具 | 每个结果包含 resource IDs、time 和 source refs |
| R8-006 | R1F-009,001 | Agent Job handler、plan/task/step/tool call checkpoint、暂停/恢复/取消 | 进程在第 2 ToolCall 崩溃后只重跑未完成调用 |
| R8-007 | 002,006 | Model gateway 结构化响应和两次重试；无模型返回 dependency error | timeout、invalid JSON、429、500 和 cancel 测试 |
| R8-008 | 004–007 | Research Agent：coverage/unknown/evidence → FollowUp Artifact | 不能直接写 FollowUp；引用至少 1 Unknown/Evidence |
| R8-009 | 004–007 | Modeling Agent：Statement/graph → ProjectionCandidate Artifact | 候选按本体 Schema；不直接 commit |
| R8-010 | 004–007 | Analyst Agent：path/metric/events/lineage → Finding Artifact | 至少三类工具；结论含反证/未知 |
| R8-011 | 004–007 | Critic Agent：检查每个 artifact 的来源、时间、越权和遗漏 | 删除一个来源时 Critic 必须 fail 对应 invariant |
| R8-012 | 008–011 | ApprovalRequest Accept/Edit/Reject；accept 调现有业务 service，不直写 Row | edit payload 再校验；重复决定 409 |
| R8-013 | 001 | EvalSuite/Case/Run/Result Job；固定离线 fake model 和可选 live profile | 同 fixture 结果可重复；live 失败不破坏离线门 |
| R8-014 | 002–013 | Agent 页面：配置、run、plan、tools、artifact、approval、pause/resume/cancel、eval | 页面完整观察与控制运行 |
| R8-015 | 001–014 | 浏览器运行四 Agent，故意暂停/重启/恢复，确认/拒绝提案并运行 Eval | `test_r8_agent_runtime_journey.py` |

### R8 出口

Agent 不再只是固定文本生成器；它通过工具读取冻结投影、留下持久轨迹、可恢复、可评测，且所有写入仍经人工确认和领域服务。

## 11. R9：企业学习与行业共享飞轮

### R9 任务表

| ID | 依赖 | 精确改动 | 验证 |
|---|---|---|---|
| R9-001 | R8 | 建 learning/peer 表迁移 `v2_0011` | asset/version/trial/aggregate 状态约束 |
| R9-002 | 001 | 监听 9 类受控领域事件生成 LearningEvent；去重 source type/id/kind | 重放 event 不重复；浏览行为不生成学习 |
| R9-003 | 002 | AssetCandidate review → LearningAsset/Version；支持 question/ontology/mapping/metric/recipe/pattern/playbook/eval | 未确认 event 不能发布资产 |
| R9-004 | 003 | Playbook schema validator：适用条件、诊断、动作、基线、目标、步骤、风险、测量 | 缺任何必需项 publish 失败 |
| R9-005 | R3,R4,R6 | CompanyFingerprint extractor；固定 feature groups 和 completeness | 同 ProjectionVersion 结果确定；缺失项不补零 |
| R9-006 | 005 | 相似度分组算法与解释；categorical/Jaccard/numeric/cosine/缺失权重 | 手算 fixture 与结果一致；主要差异准确 |
| R9-007 | 003,006 | PeerCohort compute；必须带 question context 和 feature weights | 不同管理问题得到不同相似排序 |
| R9-008 | 004,007 | TransferTrial applicability/adaptation/approve 状态机 | 目标项目不同；适用差异必须明确处理 |
| R9-009 | R7,008 | TransferTrial 关联 ActionRun/Outcome 并完成最终状态 | 无 Outcome 不能 effective；harmful 单独统计 |
| R9-010 | 009 | SQL 增量 QualityAggregate；Wilson lower bound、coverage 和独立公司数 | 重放幂等；手算成功/失败/未知案例 |
| R9-011 | 003,010 | Asset publish gate：至少 2 独立企业有效才可行业 published | 单企业强制保持 validated |
| R9-012 | 002–011 | Learning/Peer/Asset 页面：事件、候选、版本、指纹解释、cohort、trial、quality | 管理人员能看懂“为什么相似/哪里不同” |
| R9-013 | 001–012 | 企业 A Playbook 迁移企业 B，调整后试点并记录 inconclusive；查看质量变化 | `test_r9_peer_transfer_journey.py` |

### R9 出口

飞轮由真实纠正和 Outcome 驱动；同行经验显示适用条件和差异；单企业经验不会被称为行业最佳实践。

## 12. R10：管理层产品化、项目包、迁移和 V1 退出

### R10 任务表

| ID | 依赖 | 精确改动 | 验证 |
|---|---|---|---|
| R10-001 | R9 | `pyproject.toml` 增加 `jsonschema` 和 optional `hq` 的 `psycopg[binary]`；建 reporting/package 表迁移 `v2_0012`；建立 package schema v1 JSON Schema | manifest/member/hash/path 约束测试；PostgreSQL migration smoke |
| R10-002 | 001 | ProjectPackage exporter Job；按稳定顺序 JSONL，内容选择，root hash | 同数据同选择产生相同逻辑 hash；不默认含 raw files |
| R10-003 | 001 | Package zip 安全 validator 和 preview importer | zip slip、symlink、炸弹、坏 hash、未知版本拒绝 |
| R10-004 | 002,003 | Package confirm Job：new/merge/no-op/hard conflict 和 ID 引用对账 | 同包幂等；冲突不部分导入；全新实例 roundtrip |
| R10-005 | R9-011,001 | IndustryAssetPackage export/import，只允许资产白名单 | 原始回答/RawRecord/Object 被 Schema 拒绝 |
| R10-006 | R7,R9 | 管理层 Home query/read model：全景、变化、风险、原因/反证、待决策、行动、Outcome、同行 | 单请求固定上限；所有项可点击来源 |
| R10-007 | 006,R8 | 管理问答：deterministic context + 可选模型措辞，强制九段 output validator | 模型遗漏反证/未知时 validator fail/fallback |
| R10-008 | 006 | 管理简报 Report Job 生成 HTML+JSON 和 print stylesheet；页面打开独立打印视图，由系统浏览器“打印/另存为 PDF”，当前不引入服务端 PDF 引擎 | 数字、图和结论均带来源/时间；打印预览无截断/乱码 |
| R10-009 | 002–008 | Home/Reporting/Package/HQ 页面完成；专业工作台导航收敛 | 核心管理层不进入工程页完成查看、决策和导出 |
| R10-010 | R1F | 实现 SQLite 一致性 backup、restore validation 和 UI/launcher 入口 | 备份恢复到临时实例后最终计数/hash 相同 |
| R10-011 | 所有阶段 | 建最终约束迁移 `v2_0013`；复查各阶段已经完成的当前 V2 数据回填，并在真实数据库副本执行全链 | 原库备份不变；对账报告 0 悬空引用；无待迁 legacy 表 |
| R10-012 | 011 | 建 V1→V2 只读迁移器、ID map、失败/人工清单；重复演练 | 两次迁移结果逻辑 hash 相同；原 V1 hash 不变 |
| R10-013 | 004,009,011,012 | 在全新实例执行 20 步最终浏览器旅程、项目包 roundtrip、重启和迁移验收 | 全部自动化且无 API/DB 手工操作 |
| R10-014 | 013 | 桌面入口切 `/v2/#/home`；停止挂载 V1 API/page；保留 feature flag 一个发布周期 | 默认进程无 V1 route；回滚 flag 有离线备份 |
| R10-015 | 014 | 删除旧 auth/security/exchange/flywheel/Fact/admin page 运行代码和专属测试；保留迁移读取器所需最小 DTO | `rg`/import graph 证明生产代码不引用旧模块 |
| R10-016 | 015 | 更新 README、使用手册、运行/备份/迁移/开源说明；建立 release checklist | 新 Windows 账户按手册从零安装、运行、导入、备份 |
| R10-017 | 001–016 | 全量质量门、性能基线、tracked data/secret scan、最终验收报告和 release tag | `v2-r10`；所有能力矩阵目标项变为 implemented |

### R10 出口

产品以 V2 为唯一运行路径；管理层闭环、专业工作台、分部/总部包、开源数据隔离、备份恢复和迁移均通过自动化与真实浏览器验收。

## 13. 最终浏览器验收脚本

以下步骤由一个 orchestrated Playwright suite 执行，各阶段脚本也可独立运行：

1. 在空应用数据目录启动；
2. 验证 diagnostics 全绿且 Git 工作树未产生数据文件；
3. 创建东岭精工、订单到回款项目和章程；
4. 创建三个管理问题、范围、成功指标和调研计划；
5. 评价阶段门并推进 research；
6. Preview/Confirm 问卷答案 CSV，添加访谈、观察和文件证据；
7. 处理 Unknown、Conflict 和 FollowUp；
8. 安装订单到回款本体并发布 release；
9. Review 候选并提交 Projection V1；
10. 查看对象、关系、路径、时间线、价值流和 as-of；
11. Preview/Confirm CRM 与 ERP；配置四类 mapping；
12. 处理客户名冲突，完成 pipeline，查看血缘；
13. 故意使用错误 mapping，观察失败，修复并 replay；
14. 比较 Projection V1/V2；
15. 创建 Finding、反证、Unknown、Causal DAG 和 C1 estimate；
16. 比较两个 Scenario，记录 Decision 和 Action；
17. 导入行动后指标，完成 Outcome，确认回写 ChangeSet；
18. 运行四类 Agent，暂停、重启、恢复、审批并评测；
19. 生成 Playbook 和企业指纹；
20. 创建海川装备并完成 TransferTrial；
21. 查看管理层 Home、问答和简报；
22. 导出 ProjectPackage 和 IndustryAssetPackage；
23. 备份、关闭、重启并验证上下文和全部计数；
24. 在全新数据目录导入 ProjectPackage；
25. 对账 ID、revision、ProjectionVersion、lineage 和 hash；
26. 验证源实例与目标实例管理问答的证据链一致。

任一步出现以下情况即失败：按钮不存在、需要开发者工具、需要手工 API、需要改数据库、错误信息不可行动、重启丢状态、重复命令产生重复数据、跨项目数据可见、结论无来源、Agent 越过人工确认。

## 14. 性能基线

性能不是当前核心卖点，但必须防止架构退化。固定在开发机/CI 相对基线上测量：

- 10,000 Object、20,000 Link：邻居 p95 < 300ms；最短路径 depth≤6 p95 < 1s；
- 100,000 RawRecord：preview 首结果 < 3s，完整 profile 为后台 Job；
- 50,000 Event、100,000 MetricObservation：单指标一年序列 p95 < 500ms；
- Project overview/Home p95 < 1s，查询数有上限；
- Job claim 不出现重复执行；
- 项目包 100MB 在本机可流式导出/校验，内存峰值 < 300MB；
- 任何列表不全量加载总项目数据；
- 飞轮聚合只处理新增 LearningEvent，不按请求全量扫描。

性能测试使用合成数据，不进入普通单元测试；在 R3、R6、R9、R10 出口执行。

## 15. 风险触发器与唯一允许暂停的条件

实现过程中只有以下情况允许暂停并回到规划：

1. SQLite 和 PostgreSQL 无法在同一数据语义下实现关键约束；
2. 当前真实 V2 数据迁移会不可逆丢失用户记录；
3. 外部问卷系统实际导出格式与已冻结契约无法兼容且无适配办法；
4. Projection ChangeSet 在所需规模下无法满足原子性或可接受性能；
5. 因果估计输入不足以支持文档宣称的 C1；
6. ProjectPackage 无法无损表达某个核心资源及其引用；
7. 规格之间存在可复现的逻辑矛盾。

普通 bug、测试失败、实现工作量大、需要迁移旧 helper 或页面复杂，均不是暂停规划的理由，应在当前任务内解决。

## 16. 编码启动点

正式 coding 从 `R1F-001` 开始，而不是直接继续扩展现有 flat V2 model。第一批连续执行到 `R1F-015`，通过 R1F 出口后再进入 R2。任何后续 Agent、飞轮或管理主页工作都不得越过 R3–R7 的阶段门。
