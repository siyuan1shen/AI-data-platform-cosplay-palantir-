# V2 精确工程实施规格

- 状态：已冻结
- 日期：2026-08-30
- 上位需求：13–19 号文档
- 下位执行清单：21 号文档
- 规范级别：编码依据

本文冻结编码所需的技术选择、数据语义、API、任务、前端、包格式和测试契约。除非实现事实证明规格自相矛盾，否则编码阶段不得改名、换状态机、跳过层次或临时增加第二套机制。

## 1. 规范词义和优先级

- “必须”：阶段验收必需；
- “应当”：除非测试证明不可行，否则实现；
- “可以”：显式延后，不得阻断当前阶段；
- “不得”：架构边界或业务不变量；
- “当前”：指 R1F–R10；
- “最终”：指 R10 验收结束，不指无限远愿景。

冲突优先级：

```text
13 产品边界
→ 14 业务流程
→ 19 能力范围
→ 20 工程契约
→ 21 原子任务
→ 当前代码
```

## 2. 冻结技术栈

### 2.1 后端

- Python 3.12；
- FastAPI；
- Pydantic v2；
- SQLAlchemy 2.x；
- Alembic；
- 本机 SQLite 3，`foreign_keys=ON`、`journal_mode=WAL`、`busy_timeout=5000`；
- 总部 PostgreSQL 16；
- HTTPX 作为模型与 REST Connector 客户端；
- 所有领域服务同步执行短事务，长流程由数据库 Job Worker 驱动。

### 2.2 前端

- 保留无构建步骤的 HTML/CSS/ES2022 Modules；
- 不引入 React、Vue、Node 运行时或前端状态框架；
- 浏览器路由使用 hash route，刷新不产生服务端 404；
- API 客户端、状态仓库、页面和通用组件拆成 ES 模块；
- 自动浏览器测试使用 Python Playwright，CI 优先使用 Chromium，本机允许 Edge channel。

选择无构建前端的原因是本产品首先是一键启动的本地工具。复杂度通过模块边界、路由和组件契约控制，不通过引入第二套构建链解决。

### 2.3 部署拓扑

本机模式：

```text
Desktop Launcher
├─ FastAPI process
├─ in-process JobWorker thread
├─ SQLite database
├─ local files directory
└─ system browser
```

总部模式：

```text
FastAPI process(es)
├─ PostgreSQL
├─ one or more standalone JobWorker processes
└─ shared package/file directory
```

当前不引入 Redis、Kafka、Celery、图数据库、对象存储或微服务。

### 2.4 依赖变更

`pyproject.toml` 当前依赖继续保留到 R10 清理。V2 只新增：

- runtime：`openpyxl`（R6 读取 xlsx）、`jsonschema`（ProjectPackage/IndustryAssetPackage manifest 校验）；
- optional `hq`：`psycopg[binary]`；
- dev：`pytest-playwright` 与 `playwright`；
- 不新增 pandas、NumPy、SciPy、NetworkX、Celery、Redis client 或前端 npm dependency。

图查询、C1 统计和相似度首版使用标准库与 SQL 实现；若性能基线证明不足，必须先有 benchmark 和 ADR 才能增加数值/图依赖。

## 3. 目标代码目录

```text
src/enterprise_insight/v2/
├─ api.py                         # 只聚合模块 router
├─ platform/
│  ├─ clock.py                    # UTC Clock port 和测试时钟
│  ├─ ids.py                      # UUID4 生成 port
│  ├─ errors.py                   # 领域错误与 API code
│  ├─ database.py                 # engine/session，不创建 Schema
│  ├─ models.py                   # operation/job/file/revision 基础表
│  ├─ idempotency.py              # 命令幂等
│  ├─ revisions.py                # optimistic revision 与快照
│  ├─ jobs.py                     # Job application service
│  ├─ worker.py                   # claim/lease/retry/cancel
│  ├─ handlers.py                 # 统一异常处理
│  └─ diagnostics.py              # health/readiness/diagnostic
├─ portfolio/
├─ research/
├─ ontology/
├─ projection/
├─ integration/
├─ analysis/
├─ causal/
├─ decision/
├─ agents/
├─ learning/
└─ reporting/
```

每个业务模块固定使用：

```text
module/
├─ domain.py       # 枚举、值对象、不变量、纯函数
├─ models.py       # SQLAlchemy Row
├─ schemas.py      # HTTP request/response
├─ repository.py   # 查询与持久化实现
├─ service.py      # 用例和事务边界
├─ api.py          # HTTP 适配器
└─ tests/          # 不建立包内测试；统一放根 tests/
```

依赖方向：API → Service → Domain/Repository → Platform。模块不得导入另一个模块的 SQLAlchemy Row；跨模块通过 service/query port 或稳定资源 ID 协作。

前端固定目录：

```text
src/enterprise_insight/web/
├─ static_developer/
│  ├─ index.html
│  ├─ styles.css
│  └─ app.js
└─ static_executive/
   ├─ index.html
   ├─ styles.css
   └─ app.js
   ├─ router.js
   ├─ store.js
   ├─ api-client.js
   ├─ errors.js
   ├─ components/
   └─ pages/
```

## 4. 全局数据和命令约定

### 4.1 ID、键与时间

- 数据库主键统一 UUID4；
- 人可读的类型和资产使用 `stable_key`，格式 `^[a-z][a-z0-9_]{1,99}$`；
- 所有时间写入 UTC aware datetime，API 使用 ISO-8601 `Z`；
- 业务有效区间为 `[valid_from, valid_to)`，`valid_to=null` 表示开放；
- `recorded_at` 是系统首次记录时间；
- `superseded_at` 是该版本被另一版本取代的系统时间；
- 日期、业务时间和记录时间不得用同一字段代替。

### 4.2 资源身份、版本与 revision

三种版本概念严格分开：

1. `id`：稳定业务身份；
2. `version_no`：不可变内容版本，从 1 递增；
3. `revision`：可变聚合的乐观并发号，从 1 递增。

Object、Link 和 OntologyType 使用身份表 + 不可变版本表。Project、Unknown、Conflict、Finding、Decision、ActionPlan 等工作流聚合保存当前行，并在每次修改前写 `resource_revisions` JSON 快照。

所有 PATCH、状态转换、确认、拒绝、合并、提交命令必须携带 `expected_revision`。不匹配返回 HTTP 409 `REVISION_CONFLICT`，响应包含当前 revision；服务不得自动覆盖。

### 4.3 归档、退役、失效和删除

- Company/Project 使用 `archived_at`；
- 类型使用 `deprecated`；
- Object/Link 使用版本状态 `active/retired/merged`；
- Event/Metric 修订使用 `supersedes_id` 和 `voided_at`；
- Finding/Hypothesis 使用工作流状态；
- 只有未被引用的 draft、过期 preview、临时文件和测试数据允许物理删除。

### 4.4 幂等命令

以下命令必须接受 `Idempotency-Key` 请求头：

- 所有 import confirm；
- ChangeSet commit；
- Job create/retry；
- Agent run create/resume；
- project package export/import confirm；
- ActionRun start/complete 和 Outcome record；
- Learning asset publish。

`operation_receipts` 唯一键为 `(operation_kind, project_id nullable, idempotency_key)`。同键同请求哈希返回原响应；同键不同请求哈希返回 409 `IDEMPOTENCY_KEY_REUSED`。

### 4.5 项目范围不变量

所有业务资源必须能确定唯一 `company_id` 和 `project_id`。API 中 project 是主要范围。任何两个被关联资源必须属于同一 project，除以下显式跨项目资源外：PeerCohort、TransferTrial、ProjectPackageImport 和总部 QualityAggregate。

数据库实现：

- project 资源表保留 `project_id`；
- 被其他 project 资源引用的稳定表增加唯一 `(project_id, id)`；
- 可行处使用复合外键 `(project_id, referenced_id)`；
- SQLite 无法表达的多态引用由 repository 在同一事务校验，并有跨项目负向测试；
- API 查询不得只按 `id` 获取项目资源，必须按 `(project_id, id)`。

### 4.6 数值、JSON 和文件

- 业务指标使用 `Numeric(24, 8)`/Python `Decimal`，API 以字符串传输，避免 float 精度损失；
- JSON 只保存可扩展 Schema、规则、显示配置和原始 payload，不用于替代应被查询、约束或关联的核心列；
- 上传内容先流式计算 SHA-256，再移动到应用数据目录；
- 文件名只作显示，磁盘名使用 hash；
- 原始文件默认不进入 ProjectPackage，必须由用户显式勾选。

### 4.7 列表、筛选和分页

列表统一返回：

```json
{
  "items": [],
  "next_cursor": null,
  "total": 0
}
```

- 小目录也遵守同一结构；
- 默认 `limit=50`，最大 200；
- cursor 使用 `(created_at,id)` 或资源适合的稳定排序键编码；
- 图查询不使用分页 cursor，使用 `max_depth`、`max_nodes`、`max_edges`，超限返回截断标志。

## 5. 统一错误和操作响应

### 5.1 错误响应

```json
{
  "error": {
    "code": "REQUEST_VALIDATION_FAILED",
    "message": "请检查输入内容",
    "field_errors": [
      {"path": "name", "code": "too_short", "message": "至少输入 2 个字符"}
    ],
    "resource": "company",
    "resource_id": null,
    "operation_id": null,
    "retryable": false,
    "correlation_id": "uuid",
    "details": {}
  }
}
```

固定 HTTP 映射：

| HTTP | code | 使用场景 |
|---:|---|---|
| 400 | `MALFORMED_REQUEST` | JSON、CSV 或命令结构不能解析 |
| 404 | `RESOURCE_NOT_FOUND` | 当前 project 范围内不存在 |
| 409 | `REVISION_CONFLICT` | 乐观并发失败 |
| 409 | `DUPLICATE_RESOURCE` | stable key 或业务身份重复 |
| 409 | `IDEMPOTENCY_KEY_REUSED` | 同键不同请求 |
| 409 | `INVALID_STATE_TRANSITION` | 状态机不允许 |
| 413 | `UPLOAD_TOO_LARGE` | 文件超限 |
| 422 | `REQUEST_VALIDATION_FAILED` | 字段合法但不满足业务校验 |
| 422 | `DOMAIN_INVARIANT_VIOLATION` | 类型、时间、端点或范围不变量 |
| 424 | `DEPENDENCY_NOT_READY` | 模型或外部 Source 未配置 |
| 500 | `UNEXPECTED_RESPONSE` | 未归类服务错误，保留 correlation ID |
| 503 | `WORKER_UNAVAILABLE` | 需要长任务但 Worker 不健康 |

### 5.2 命令操作响应

同步命令返回目标资源。异步命令返回 HTTP 202：

```json
{
  "operation_id": "uuid",
  "job_id": "uuid",
  "status": "queued",
  "resource_type": "pipeline_run",
  "resource_id": "uuid",
  "poll_url": "/api/v2/jobs/{job_id}"
}
```

## 6. 数据库与迁移链

### 6.1 数据库文件和 Schema

- 本机默认数据库：`%LOCALAPPDATA%/EnterpriseInsight/data/eip-v2.db`；
- 测试数据库：pytest `tmp_path`；
- 仓库中的 `data/` 只保存 README，不保存运行数据库；
- PostgreSQL 使用 schema `enterprise_insight_v2`；
- V1 migration chain 保持只读，V2 建立独立 Alembic version location 和 version table `alembic_version_v2`。

### 6.2 V2 迁移序列

迁移文件固定按以下主题建立；每个迁移必须可在空 SQLite、已有当前 V2 SQLite 副本和空 PostgreSQL 上执行：

1. `v2_0001_current_schema_baseline`；
2. `v2_0002_jobs_operations_revisions`；
3. `v2_0003_portfolio_stage_gates`；
4. `v2_0004_ontology_releases`；
5. `v2_0005_projection_identity_versions`；
6. `v2_0006_projection_temporal`；
7. `v2_0007_research_evidence_candidates`；
8. `v2_0008_integration_pipeline_lineage`；
9. `v2_0009_analysis_causal_decision`；
10. `v2_0010_agent_runtime_evaluation`；
11. `v2_0011_learning_peer`；
12. `v2_0012_reporting_packages`；
13. `v2_0013_constraints_and_indexes`。

`v2_0001` 必须精确描述当前 create-all V2 Schema，不改变其内容。全新数据库从该迁移正常创建；已有但尚未由 Alembic 管理的当前 V2 数据库，Launcher 先创建完整备份，再比较表、列、外键和索引 fingerprint。只有 fingerprint 与 `v2_0001` 完全一致时才允许 stamp 到 `v2_0001`，随后执行后续迁移；不一致时停止并输出差异，禁止猜测 stamp。

现有数据不延后到最后统一回填。每个阶段迁移在引入目标表时立即迁移对应旧表：R2 迁 Company/Project，R3–R4 迁类型和投影，R5 迁 Research，R6 迁 Data/Lineage，R7 迁 Decision，R8 迁 Agent。旧表只有在该阶段对账通过后才删除或改为只读 legacy 表。

迁移不得调用应用 service。数据回填使用迁移内纯 SQL 或小型确定性 helper；失败必须回滚并保留原 `eip-v2.db`。

### 6.3 Schema 创建规则

- `V2Database.create_schema()` 在生产路径删除；
- 应用启动执行只读 migration status；
- Launcher 可执行 `upgrade head`；
- 测试 fixture 显式执行迁移；
- readiness 在版本落后时返回 not ready 和所需迁移，不静默改表。

## 7. 表目录与关键不变量

以下为目标表的规范目录。除通用 `id/created_at/updated_at` 外，列名不得在编码时自由替换。

### 7.1 Platform

| 表 | 必需字段 | 不变量 |
|---|---|---|
| `operation_receipts` | operation_kind, project_id, idempotency_key, request_hash, status, response_json | 唯一 operation/project/key |
| `resource_revisions` | project_id, resource_type, resource_id, revision, snapshot_json, reason, actor_label | 唯一 resource/revision |
| `jobs` | project_id, kind, status, payload_json, progress_current, progress_total, attempt, max_attempts, available_at, lease_owner, lease_expires_at, cancel_requested_at, error_json | lease 原子 claim |
| `job_steps` | job_id, position, key, status, input_json, output_json, attempt, started_at, completed_at, error_json | 唯一 job/position |
| `job_events` | job_id, sequence, level, event_type, message, payload_json, created_at | 唯一 job/sequence |
| `stored_files` | project_id, sha256, size_bytes, media_type, original_name, storage_path, purpose, status | path 必须位于 data root |
| `import_previews` | project_id, preview_kind, source_hash, request_json, result_json, expires_at, consumed_at | confirm 后不可复用为不同请求 |

Job 状态：`queued → running → succeeded|failed|cancelled`，`running → queued` 只允许 lease 过期且 attempt 未超限。

### 7.2 Portfolio

| 表 | 必需字段 |
|---|---|
| `companies` | name, industry, description, attributes_json, revision, archived_at |
| `projection_projects` | company_id, name, description, value_stream_key, stage, revision, archived_at |
| `project_charters` | project_id, expected_decision, deadline, sponsor, project_lead, revision |
| `project_decision_questions` | project_id, prompt, priority, status, revision |
| `scope_boundaries` | project_id, scope_kind, reference_text, inclusion, rationale, revision |
| `success_metric_targets` | project_id, metric_type_id nullable, label, baseline, target, window_start, window_end, data_availability, revision |
| `research_plans` | project_id, objectives_json, participants_json, sources_json, schedule_json, revision |
| `stage_gate_evaluations` | project_id, from_stage, target_stage, status, checks_json, evaluated_at, committed_at |

项目 stage：

```text
draft → research → projection → integration → analysis
→ decision → execution → review → closed
```

允许 `closed → review` 重开；其他后退必须使用 `reopen` 命令并记录理由。阶段门只阻止前进，不阻止保存未完成工作。

阶段门检查项固定如下；`blocking=true` 的失败阻止 transition，非阻断项只形成 warning：

| 转换 | 阻断检查 |
|---|---|
| draft→research | Charter 的 expected_decision、sponsor、project_lead、deadline 完整；value_stream=`order_to_cash`；至少 1 个 priority≤2 的 active DecisionQuestion；至少 1 条 include ScopeBoundary；至少 1 个 SuccessMetricTarget；ResearchPlan 至少 1 个 participant 和 1 个 source |
| research→projection | 至少 1 个已确认 ResearchImport 或 completed Interview；每个 priority≤2 问题至少关联 1 Evidence 或 1 open Unknown；至少 1 个 object、1 个 link、1 个 event/metric Candidate；所有 blocking Conflict 已 resolved/acknowledged |
| projection→integration | 存在 published OntologyRelease；存在 committed ProjectionVersion；至少 5 个 active ObjectType、8 个 active LinkType；至少一条从 Customer 到 Payment 的价值流路径或明确 Unknown；accepted Candidate 全部已提交或撤回 |
| integration→analysis | `crm_customers` 与 `erp_orders` 两个 Source 各有 succeeded PipelineRun；无 blocking ImportConflict；导入生成的 projection item lineage coverage≥95%；最新 MappingVersion 均 published |
| analysis→decision | 至少 1 个 confirmed Finding；每个 confirmed Finding 有 support evidence、contrary evidence 或明确“未发现反证”；至少 1 个 Hypothesis 已 planned/testing/evaluated；至少 2 个 DecisionOption |
| decision→execution | 至少 1 个 approved Decision；其 ActionPlan 为 approved，且 owner、baseline、target、measurement window 完整 |
| execution→review | 每个 approved ActionPlan 至少 1 个 completed/aborted ActionRun；每个 completed ActionRun 有 Outcome |
| review→closed | 每个 Outcome 的 draft ChangeSet 已 committed/rejected；对应 Hypothesis 已评估；LearningEvent 已 processed/waived；至少 1 份 management brief succeeded |

warning 固定包括：低优先问题无证据、ResearchPlan 无文件型 Source、Projection completeness<80%、lineage coverage 95%–99%、存在 inconclusive Outcome、未导出 ProjectPackage。warning 不得被前端显示为成功检查。

### 7.3 Research

| 表 | 必需字段 |
|---|---|
| `research_missions` | project_id, title, objective, status, owner, revision |
| `question_packs` | stable_key, name, industry, status, revision |
| `question_versions` | question_pack_id, version_no, question_key, prompt, answer_type, options_json, tags_json |
| `survey_responses` | project_id, mission_id, import_id, questionnaire_key, respondent_label, submitted_at |
| `survey_answers` | response_id, question_version_id nullable, question_key, prompt, answer_text, source_row_count |
| `interview_records` | project_id, mission_id, participant_label, role_label, occurred_at, interviewer, status, revision |
| `research_fragments` | project_id, source_kind, source_id, sequence, content, occurred_at, author_label |
| `statements` | project_id, fragment_id, speaker_label, claim_text, scope_json, confidence, status, revision |
| `observations` | project_id, fragment_id, observation_text, location, observed_at, observer_label, confidence, status, revision |
| `evidence_items` | project_id, evidence_kind, source_kind, source_id, excerpt, occurred_at, reliability, revision |
| `evidence_targets` | project_id, evidence_id, target_type, target_id, polarity, relevance_note |
| `unknowns` | project_id, question_id nullable, description, impact, priority, resolution_plan, status, revision |
| `conflicts` | project_id, title, scope_json, status, resolution, revision |
| `conflict_members` | conflict_id, member_type, member_id, position |
| `follow_up_proposals` | project_id, target_label, question, rationale, priority, status, revision |
| `projection_candidates` | project_id, candidate_kind, proposed_payload, source_refs_json, confidence, status, revision |
| `candidate_reviews` | candidate_id, decision, edited_payload, merge_target_id, reason, reviewer_label, created_at |

Statement、Observation、Evidence、Unknown、Conflict 和 Candidate 是不同资源，禁止用单一 Fact 表合并。

候选状态：`proposed → accepted|edited|merged|rejected`。accepted/edited 只产生 draft ChangeSet，必须由“生成 Projection V1”命令统一提交。

### 7.4 Ontology

统一稳定身份表：

| 表 | 必需字段 |
|---|---|
| `ontology_types` | project_id nullable, kind, stable_key, origin, archived_at |
| `ontology_type_versions` | type_id, version_no, status, name, description, schema_json, display_json, semantics_json, created_by |
| `ontology_releases` | project_id, release_no, status, name, parent_release_id, revision, published_at |
| `ontology_release_items` | release_id, type_id, type_version_id |
| `link_type_specs` | type_version_id, source_type_id, target_type_id, direction, inverse_name, source_cardinality, target_cardinality, temporal |
| `event_role_specs` | type_version_id, role_key, name, object_type_id, cardinality, required |
| `metric_type_specs` | type_version_id, unit, value_type, grain, aggregation, window_kind, dimensions_json, formula_json |
| `action_type_specs` | type_version_id, preconditions_json, inputs_json, effects_json, reversible, outcome_metric_type_ids_json |
| `interface_specs` | type_version_id, system_kind, direction, operations_json, object_type_ids_json, action_type_ids_json |
| `ontology_constraints` | type_version_id, constraint_kind, expression_json, severity, message |

`kind` 固定为 `object|link|event|metric|action|interface`。`origin` 固定为 `project|template|industry_asset`。

TypeVersion 状态：`draft → published → deprecated`。published 不可修改。OntologyRelease 状态：`draft → validated → published → superseded`；published release 内容不可修改。

### 7.4.1 订单到回款模板 v1

模板 stable key 为 `manufacturing_order_to_cash`，asset version 为 1。安装时复制到项目 origin=`template` 的 draft TypeVersion，再由用户发布项目 OntologyRelease。

ObjectType：

| stable_key | 名称 | 最低属性 |
|---|---|---|
| `company` | 企业 | legal_name, industry |
| `department` | 部门 | code, function |
| `customer` | 客户 | customer_code, segment |
| `product` | 产品 | product_code, category |
| `sales_order` | 销售订单 | order_no, amount, promised_date, status |
| `work_order` | 工单 | work_order_no, planned_start, planned_finish, status |
| `shipment` | 发货 | shipment_no, promised_at, delivered_at, status |
| `invoice` | 发票 | invoice_no, amount, issued_at, due_at, status |
| `payment` | 回款 | payment_no, amount, received_at, status |

LinkType：

| stable_key | source→target | 基数 |
|---|---|---|
| `owns` | company→department | 1:n |
| `serves` | company→customer | n:m |
| `placed` | customer→sales_order | 1:n |
| `contains` | sales_order→product | n:m |
| `fulfilled_by` | sales_order→work_order | 1:n |
| `produces` | work_order→product | n:1 |
| `ships` | shipment→sales_order | n:1 |
| `invoices` | invoice→sales_order | n:1 |
| `settles` | payment→invoice | n:1 |
| `responsible_for` | department→sales_order | n:m |

EventType：`opportunity_won`、`order_created`、`plan_released`、`production_completed`、`shipment_delivered`、`invoice_issued`、`payment_received`。每类至少有 `subject` role；订单相关事件增加 `order`，对象流转事件按需增加 `customer/work_order/shipment/invoice/payment` role。

MetricType：

- `order_cycle_days`，day，sales_order grain，mean/median；
- `approval_wait_hours`，hour，sales_order grain，mean/median；
- `on_time_delivery_rate`，percent，shipment grain，mean；
- `gross_margin_rate`，percent，sales_order grain，mean；
- `cash_collection_days`，day，invoice grain，mean/median。

ActionType：`request_evidence`、`assign_owner`、`start_improvement`、`run_scenario`、`verify_outcome`。全部是平台内部动作；`start_improvement` 与 `verify_outcome` 必须关联 outcome metric。

Interface：`crm_customer_read`、`erp_order_read`、`mes_work_order_read`、`finance_receivable_read`，direction=`read`。R6 只启用前两个 CSV Connector；后两个只作为投影中的待接入接口定义。

### 7.5 Projection

| 表 | 必需字段 |
|---|---|
| `projection_change_sets` | project_id, kind, status, reason, base_projection_version_id, revision, committed_at |
| `projection_change_items` | change_set_id, position, resource_kind, resource_id, resource_version_id, operation, source_ref_json |
| `projection_versions` | project_id, sequence, parent_id, ontology_release_id, change_set_id, data_watermark_json, committed_at |
| `objects` | project_id, object_type_id, created_in_change_set_id |
| `object_versions` | object_id, version_no, change_set_id, name, properties_json, valid_from, valid_to, status, supersedes_version_id |
| `external_identities` | project_id, object_id, source_id nullable, namespace, external_key, status, resolution_decision_id nullable |
| `object_aliases` | project_id, object_id, alias, alias_kind, source_ref_json, status |
| `links` | project_id, link_type_id, source_object_id, target_object_id, created_in_change_set_id |
| `link_versions` | link_id, version_no, change_set_id, properties_json, valid_from, valid_to, status, supersedes_version_id |
| `events` | project_id, event_type_id, occurred_at, properties_json, change_set_id, supersedes_event_id, voided_at |
| `event_participants` | event_id, role_key, object_id |
| `metric_observations` | project_id, metric_type_id, object_id nullable, observed_at, value_decimal, raw_value, dimensions_json, quality_status, change_set_id, supersedes_observation_id, voided_at |

ChangeSet 状态：`draft → validating → committed|rejected`。commit 必须在一个数据库事务中完成：锁 base revision、验证全部 item、写 ProjectionVersion、标记 committed、发送持久化领域事件。任何 item 失败则全部不生效。

as-of 查询参数：

- `valid_at`：业务时间，默认当前；
- `recorded_at`：系统认知时间，默认最新 committed ProjectionVersion；
- `projection_version_id`：若提供则覆盖 recorded_at；
- 返回每个资源的稳定 ID、内容 version、来源 ChangeSet 和 ProjectionVersion。

### 7.6 Integration 与 Lineage

| 表 | 必需字段 |
|---|---|
| `sources` | project_id, stable_key, name, source_kind, system_name, status, revision |
| `connector_configs` | source_id, connector_kind, config_json, schedule_json, status, revision |
| `source_schemas` | source_id, version_no, columns_json, profile_json, content_hash, observed_at |
| `data_import_batches` | project_id, source_id, file_id, content_hash, status, row_count, revision |
| `raw_records` | project_id, batch_id, source_record_key, source_revision, row_number, observed_at, payload_json, content_hash |
| `mapping_programs` | project_id, source_id, stable_key, name, mapping_kind, status, revision |
| `mapping_versions` | program_id, version_no, input_schema_id, target_type_id, rules_json, validation_json, conflict_policy, status |
| `pipeline_runs` | project_id, source_id, batch_id, mapping_version_ids_json, job_id, status, preview_summary_json, output_change_set_id, revision |
| `pipeline_step_runs` | pipeline_run_id, position, step_kind, status, input_count, output_count, error_count, checkpoint_json |
| `identity_resolution_decisions` | project_id, source_id, external_key, candidate_object_ids_json, decision, selected_object_id, reason, status, revision |
| `import_conflicts` | project_id, pipeline_run_id, conflict_kind, source_record_ids_json, candidate_json, status, resolution_json, revision |
| `replay_requests` | pipeline_run_id, from_step, mapping_version_ids_json, reason, job_id, status |
| `lineage_nodes` | project_id, node_kind, resource_id, resource_version, label, metadata_json |
| `lineage_edges` | project_id, from_node_id, to_node_id, relation, operation, step_run_id nullable, created_at |

lineage relation 固定初始集合：`extracted_from`、`transformed_by`、`mapped_to`、`resolved_as`、`supports`、`contradicts`、`derived_from`、`selected_by`、`executed_as`、`measured_by`、`learned_from`。

### 7.7 Analysis、Causal 与 Decision

| 表 | 必需字段 |
|---|---|
| `findings` | project_id, title, conclusion, scope_json, confidence_level, status, revision |
| `finding_evidence` | finding_id, evidence_id or lineage_node_id, polarity, weight, note |
| `causal_variables` | project_id, stable_key, name, variable_kind, metric_type_id nullable, event_type_id nullable, expression_json nullable, revision |
| `causal_hypotheses` | project_id, statement, scope_json, status, confidence_level, revision |
| `causal_edges` | hypothesis_id, from_variable_id, to_variable_id, lag_json, mechanism, status, revision |
| `confounders` | hypothesis_id, variable_id, measurement_status, handling_strategy, note |
| `causal_estimates` | hypothesis_id, method, intervention_variable_id, outcome_variable_id, population_json, pre_window, post_window, estimate, uncertainty_json, diagnostics_json, limitations_json, status |
| `scenarios` | project_id, name, baseline_projection_version_id, hypothesis_ids_json, status, revision |
| `scenario_changes` | scenario_id, target_kind, target_id, operation, payload_json |
| `scenario_results` | scenario_id, method, metric_results_json, assumptions_json, unmodeled_factors_json, created_at |
| `decision_options` | project_id, title, action_type_id nullable, expected_effect, cost_note, risk_note, dependencies_json, status, revision |
| `option_evidence` | option_id, lineage_node_id, polarity, note |
| `decisions` | project_id, question_id nullable, selected_option_id, rationale, participants_json, assumptions_json, status, decided_at, revision |
| `action_plans` | project_id, decision_id, action_type_id, title, owner, milestones_json, baseline_json, target_json, measurement_window_json, status, revision |
| `action_runs` | project_id, action_plan_id, status, started_at, paused_at, completed_at, aborted_at, notes, revision |
| `outcomes` | project_id, action_run_id, classification, observed_metrics_json, evidence_json, limitations_json, observed_at, revision |
| `outcome_effects` | outcome_id, effect_kind, target_type, target_id, before_json, after_json, change_set_id nullable, learning_event_id nullable |

Finding 状态：`draft → proposed → confirmed|rejected → superseded`。

Hypothesis 状态：`proposed → planned → testing → supported|weakened|refuted|inconclusive → superseded`。

Decision 状态：`draft → recorded → approved → executing → completed|cancelled`。

ActionRun 状态：`planned → approved → running ↔ paused → completed|aborted`。

Outcome classification：`effective|ineffective|inconclusive|harmful`。记录 Outcome 不得直接篡改已有 ProjectionVersion；它创建 outcome ChangeSet，用户确认后提交。

### 7.8 Agent 与 Evaluation

| 表 | 必需字段 |
|---|---|
| `model_profiles` | name, provider_kind, base_url, model_name, capabilities_json, status, revision |
| `agent_runs` | project_id, role, goal, scope_json, context_projection_version_id, model_profile_id nullable, status, budget_json, job_id, result_artifact_id nullable, revision |
| `agent_tasks` | run_id, position, title, objective, status, depends_on_json |
| `plan_steps` | task_id, position, step_kind, tool_name nullable, input_json, status, retry_policy_json |
| `tool_calls` | run_id, plan_step_id, tool_name, tool_version, input_json, output_json, status, started_at, completed_at, error_json |
| `artifacts` | project_id, run_id nullable, artifact_kind, title, content_json, status, revision |
| `approval_requests` | project_id, run_id, action_kind, target_type, target_id nullable, proposed_payload, status, decision_reason, revision |
| `evaluation_suites` | stable_key, name, scope, status, revision |
| `evaluation_cases` | suite_id, case_key, input_fixture_json, expected_invariants_json, tags_json |
| `evaluation_runs` | suite_id, agent_role, model_profile_id, status, started_at, completed_at |
| `evaluation_results` | evaluation_run_id, case_id, score_json, passed, violations_json, trace_ref_json |

Agent 状态：`queued → planning → running ↔ waiting_approval|paused → completed|failed|cancelled`。

模型密钥不进入 `model_profiles`。`POST /runtime/model-secret` 只保存在当前进程内存；用户显式选择“记住”时才写应用 private 目录，且该目录永远不在 Git 工作树内。模型不可用不影响非 Agent 功能。

### 7.9 Learning、Peer 与 Reporting

| 表 | 必需字段 |
|---|---|
| `learning_events` | project_id, event_kind, source_type, source_id, payload_json, status, created_at |
| `learning_assets` | asset_kind, stable_key, industry, status, current_version_id, revision |
| `learning_asset_versions` | asset_id, version_no, content_json, applicability_json, evidence_summary_json, status |
| `company_fingerprints` | company_id, project_id, version_no, features_json, completeness, projection_version_id |
| `peer_cohorts` | question_context, member_fingerprint_ids_json, weights_json, explanation_json, status |
| `transfer_trials` | source_asset_version_id, target_project_id, applicability_json, adaptation_json, expected_outcome_json, action_run_id nullable, outcome_id nullable, status, revision |
| `quality_aggregates` | asset_version_id, effective_count, ineffective_count, harmful_count, inconclusive_count, coverage_count, score, updated_at |
| `report_runs` | project_id, report_kind, projection_version_id, options_json, job_id, status, file_id nullable |
| `project_packages` | project_id, package_id, schema_version, content_selection_json, manifest_json, job_id, status, file_id nullable |
| `package_imports` | package_id, source_file_id, status, preview_json, conflict_json, target_project_id nullable, job_id, revision |

Asset 状态：`draft → validated → published → deprecated`。TransferTrial 状态：`assessing → adapted → approved → running → effective|ineffective|inconclusive|harmful`。

## 8. Projection 提交、历史和血缘算法

### 8.1 ChangeSet 提交

固定流程：

1. 读取 project 当前 `latest_projection_version_id` 和 revision；
2. 创建 draft ChangeSet，保存 base version；
3. 添加不可变候选版本和 change items；
4. validate：项目范围、类型版本、Schema、端点、基数、有效时间、外部身份和引用；
5. 若有 error，ChangeSet 保持 draft 并返回结构化问题；
6. commit 命令检查 project expected_revision 和 base version 未变化；
7. 在一个事务创建 sequence+1 的 ProjectionVersion；
8. 标记 ChangeSet committed，project revision+1；
9. 写 operation receipt 和 lineage；
10. 提交后发布持久化 `projection.version_committed` job event。

### 8.2 当前状态查询

“当前 Object”不是 objects 表一行，而是：该 Object 在目标 ProjectionVersion 可见的、ChangeSet 已提交的最高 `version_no`，且状态 active，valid_at 落在有效区间。Link 同理。

Event 和 MetricObservation 本身不可变；修订创建新行并指向 `supersedes_*`。查询排除已被目标 recorded_at 之前的 committed 修订取代或 void 的记录。

### 8.3 版本差异

diff 返回：

```json
{
  "from_version": 3,
  "to_version": 5,
  "added": [{"kind":"object","id":"...","version_no":1}],
  "changed": [{"kind":"link","id":"...","from":1,"to":2}],
  "retired": [],
  "events_added": [],
  "metrics_added": [],
  "change_sets": [],
  "source_summary": {}
}
```

### 8.4 血缘

每个可追溯资源创建 lineage node。导入步骤、人工确认、Agent 提案、Finding、Decision、Action 和 Outcome 通过 edge 连接。血缘查询支持：

- `upstream(depth, relation filters)`；
- `downstream(depth, relation filters)`；
- `impact(resource)`；
- `explain(resource)`，返回最短来源链、冲突和失效来源。

撤销 Source/Mapping 不自动删除下游结论；下游 node 标记 `stale` 并进入重算/人工复核队列。

## 9. Mapping Program 声明式契约

### 9.0 Connector port

所有数据源适配器实现同一 Python Protocol：

```text
connector_kind: str
validate_config(config) -> ConfigDiagnostic
health_check(config) -> SourceHealth
discover_schema(config, checkpoint?) -> SourceSchemaDraft
extract(config, checkpoint?) -> Iterator[RawEnvelope]
next_checkpoint(previous, envelope) -> JSON
```

`RawEnvelope` 固定含 `source_record_key`、`source_revision`、`observed_at`、`payload`、`content_hash`。Connector 只产生 RawEnvelope，不得导入 Projection/Decision Row。R6 注册 `csv@1`、`xlsx@1`；后续 folder/database/rest 只能增加 adapter 和 config schema，不改变 Pipeline、Mapping、Identity、Lineage 或 Projection 契约。这保证“先文件导入、后直连系统”不是两套架构。

### 9.1 禁止任意代码

MappingVersion 的 `rules_json` 只允许白名单表达式：

- `source(field)`、`constant(value)`；
- `trim`、`lower`、`upper`；
- `coalesce`、`concat`；
- `parse_date(format, timezone)`；
- `parse_decimal(locale)`；
- `lookup(map)`；
- `normalize_identifier`；
- `hash_sha256`；
- `eq/ne/gt/gte/lt/lte/in/is_blank` 条件；
- `and/or/not`；
- `object_ref(source, namespace, external_key_expr, expected_type)`。

不得使用 Python `eval`、SQL 文本、模板执行或上传脚本。

### 9.2 四类映射

Object mapping：type、name、external identity、properties、validity。

Link mapping：type、source object_ref、target object_ref、properties、validity。

Event mapping：type、occurred_at、participant role→object_ref、properties。

Metric mapping：type、object_ref optional、observed_at、value、dimensions、quality status。

### 9.3 Preview 和 Confirm

Preview 固定输出：输入行数、有效行、将创建、将复用、待解析身份、冲突、错误、跳过、每类最多 20 条样例和预计 ChangeSet。Preview 不创建投影实例。

Confirm 只能引用未过期 preview、相同 source hash 和 mapping version。Confirm 创建 PipelineRun Job；所有输出先写 draft ChangeSet。若冲突策略要求人工处理，Job 进入 `waiting_input`，冲突解决后 resume。

### 9.4 身份解析顺序

1. 同 Source/namespace/external key exact；
2. 已确认 alias exact；
3. 已发布 deterministic normalization rule；
4. 生成候选，人工 `merge|link|keep_separate|reject`；
5. 模型相似度只可排序候选，不可自动合并。

## 10. 因果与情景精确边界

### 10.1 C1 方法

R7 只实现：

- `pre_post`: 同一总体行动前后均值、中位数、样本数和变化；
- `interrupted_trend`: 至少各 6 个时间点时比较行动前后趋势斜率；
- `event_window`: 围绕明确事件的固定窗口比较。

每次估计必须保存数据集 ProjectionVersion、查询定义、缺失率、异常处理、窗口、估计值和限制。样本不足时状态 `insufficient_data`，不得输出因果措辞。

Causal DAG 禁止 directed cycle；若业务上存在反馈，使用带 lag 的不同时间变量，不创建同时间环。

### 10.2 证据等级

固定显示：

```text
L0 观点/待验证
L1 描述相关或时序合理
L2 前后比较支持
L3 对照或准实验支持
L4 受控试点支持
L5 多企业重复验证
```

R7 最多自动达到 L2。人工不得把无估计的假设手工标成 L3 以上。

### 10.3 Scenario

Scenario 以 baseline ProjectionVersion 为只读基线，change 只存在 overlay。首版规则引擎只计算用户明确给出的 metric effect rule，不推测未定义关系。结果必须同时显示 assumptions、unmodeled_factors、affected objects 和 metric deltas。

## 11. Agent 运行时和工具契约

### 11.1 首批四类 Agent

R8 只交付：Research、Modeling、Analyst（Graph+Metric）、Critic。Decision Planner 在四类稳定后启用；其他角色只保留数据枚举，不建页面入口。

### 11.2 工具等级

- `read`: 不产生业务写入；
- `propose`: 只创建 Artifact/Candidate；
- `internal_reversible`: 人工批准后执行平台内 ChangeSet；
- `external`: 当前禁用。

首批工具名与版本固定：

```text
portfolio.project_context@1
research.coverage@1
research.unknowns@1
projection.get_object@1
projection.find_objects@1
projection.neighbors@1
projection.shortest_path@1
projection.subgraph@1
projection.events@1
projection.as_of@1
metrics.series@1
metrics.aggregate@1
lineage.explain@1
analysis.findings@1
causal.hypothesis_context@1
candidate.create@1
artifact.create@1
```

每个工具注册 input/output JSON Schema、timeout、最大结果、等级、幂等性和 handler。ToolCall 必须保存截断摘要与完整 artifact 引用。

### 11.3 执行循环

1. Run 固定 context ProjectionVersion；
2. Worker 创建 Plan；
3. 每个步骤验证工具白名单和 input Schema；
4. 执行确定性工具；
5. 模型只能基于 tool outputs 生成结构化 artifact；
6. Critic 检查来源、反证、未知和越权动作；
7. propose 结果进入 ApprovalRequest；
8. 人工 Accept/Edit/Reject；
9. 运行 evaluation；
10. 完成 Run。

暂停恢复以已 completed ToolCall 为检查点，不重复执行已成功且幂等的调用。

### 11.4 模型响应失败

模型超时、格式错误、拒绝或额度不足记录在 step error；按 `max_attempts=2` 重试。第二次失败 Run 进入 failed，保留全部工具结果。不得用空字符串或虚构 artifact 伪装成功。

## 12. 企业与行业飞轮算法

### 12.1 LearningEvent 来源

只接受：candidate review、identity resolution、conflict resolution、mapping correction、finding confirmation、hypothesis evaluation、Outcome、Agent evaluation 和 TransferTrial。浏览次数、模型自评和未确认提案不是学习信号。

### 12.2 企业指纹

features 固定分组：

- categorical：industry、business_model、order_mode、production_mode；
- numeric：revenue_band、employee_band、product_complexity、customer_concentration；
- graph：type counts、degree distribution、value-stream motif counts；
- process：cycle-time distribution、handoff count、approval depth；
- systems：source coverage、identity conflict rate、update latency；
- context：decision question tags、projection completeness。

相似度：categorical 用 exact/Jaccard；numeric 用 cohort 范围归一化距离；graph/process 用 cosine；缺失特征从分母排除。结果必须返回分组得分、实际权重、缺失项和主要差异，不只返回一个数字。

### 12.3 Playbook 质量

`effective=success`，`ineffective/harmful=failure`，`inconclusive` 不进入成功率但降低 coverage。质量显示 observed rate、Wilson 95% lower bound、独立公司数和 applicability 范围。少于两个独立公司有效 Outcome 时状态只能是 candidate/validated，不能 published 为行业模式。

## 13. ProjectPackage 与 IndustryAssetPackage

### 13.1 文件格式

均为 ZIP，UTF-8，根目录必须有 `manifest.json`。禁止可执行文件、绝对路径、`..` 和符号链接。每个成员记录 SHA-256、字节数、记录数和 Schema version。

ProjectPackage 目录：

```text
manifest.json
portfolio/*.jsonl
research/*.jsonl
ontology/*.jsonl
projection/*.jsonl
integration/*.jsonl
analysis/*.jsonl
decision/*.jsonl
agents/*.jsonl
learning/*.jsonl
files/*                 # 仅显式选择
```

manifest 必需字段：`package_id`、`package_kind`、`schema_version`、`created_at`、`source_installation_id`、`company_id`、`project_id`、`project_revision`、`latest_projection_version`、`content_selection`、`members`、`root_hash`。

### 13.2 导入策略

1. 上传/选择文件；
2. 校验 ZIP 路径、大小、manifest、hash 和版本；
3. 建立 preview：新增、相同、冲突、缺失引用、不可支持版本；
4. 用户选择新建项目、合并同一项目或取消；
5. confirm 创建 Job；
6. 保留原 UUID；若相同 package_id 已成功导入则幂等返回；
7. 同 resource ID 同 revision 同 hash 为 no-op；
8. 同 revision 不同 hash 为 hard conflict，不自动覆盖；
9. 导入完成生成数量、引用和 hash 对账报告。

IndustryAssetPackage 不含 Company、原始回答、RawRecord、业务 Object/Event/Metric。它只允许 QuestionPack、Ontology template、Mapping template、Metric definition、Agent recipe、EvalSuite、Pattern 和 Playbook。

## 14. HTTP API 精确资源面

根路径固定 `/api/v2`。以下路径为目标，不在编码阶段另起同义路径。

### 14.1 Platform 与 Portfolio

```text
GET  /diagnostics
GET  /jobs/{job_id}
GET  /jobs/{job_id}/events
POST /jobs/{job_id}/cancel
POST /jobs/{job_id}/retry
POST /runtime/model-secret
DELETE /runtime/model-secret

GET  /companies
POST /companies
GET  /companies/{company_id}
PATCH /companies/{company_id}
POST /companies/{company_id}/archive
GET  /projects?company_id=
POST /companies/{company_id}/projects
GET  /projects/{project_id}
PATCH /projects/{project_id}
POST /projects/{project_id}/archive
GET  /projects/{project_id}/overview
GET  /projects/{project_id}/charter
PUT  /projects/{project_id}/charter
GET  /projects/{project_id}/decision-questions
POST /projects/{project_id}/decision-questions
PATCH /projects/{project_id}/decision-questions/{question_id}
GET  /projects/{project_id}/scope-boundaries
POST /projects/{project_id}/scope-boundaries
GET  /projects/{project_id}/success-metrics
POST /projects/{project_id}/success-metrics
GET  /projects/{project_id}/research-plan
PUT  /projects/{project_id}/research-plan
POST /projects/{project_id}/stage-gates/evaluate
POST /projects/{project_id}/transitions
```

### 14.2 Research

```text
POST /projects/{project_id}/research-imports/preview
POST /projects/{project_id}/research-imports/confirm
GET  /projects/{project_id}/research-imports
GET  /projects/{project_id}/survey-responses
GET  /projects/{project_id}/research-missions
POST /projects/{project_id}/research-missions
GET  /projects/{project_id}/interviews
POST /projects/{project_id}/interviews
PATCH /projects/{project_id}/interviews/{interview_id}
POST /projects/{project_id}/interviews/{interview_id}/fragments
GET  /projects/{project_id}/statements
GET  /projects/{project_id}/observations
POST /projects/{project_id}/observations
GET  /projects/{project_id}/evidence
POST /projects/{project_id}/evidence
GET  /projects/{project_id}/unknowns
POST /projects/{project_id}/unknowns
PATCH /projects/{project_id}/unknowns/{unknown_id}
GET  /projects/{project_id}/conflicts
POST /projects/{project_id}/conflicts/{conflict_id}/resolve
GET  /projects/{project_id}/follow-ups
PATCH /projects/{project_id}/follow-ups/{follow_up_id}
GET  /projects/{project_id}/projection-candidates
POST /projects/{project_id}/projection-candidates/{candidate_id}/review
POST /projects/{project_id}/projection-candidates/commit
```

### 14.3 Ontology 与 Projection

```text
GET  /projects/{project_id}/ontology/types?kind=
POST /projects/{project_id}/ontology/types
GET  /projects/{project_id}/ontology/types/{type_id}
POST /projects/{project_id}/ontology/types/{type_id}/versions
POST /projects/{project_id}/ontology/type-versions/{version_id}/publish
GET  /projects/{project_id}/ontology/releases
POST /projects/{project_id}/ontology/releases
POST /projects/{project_id}/ontology/releases/{release_id}/validate
POST /projects/{project_id}/ontology/releases/{release_id}/publish
POST /projects/{project_id}/ontology/templates/order-to-cash/install

GET  /projects/{project_id}/objects
POST /projects/{project_id}/objects
GET  /projects/{project_id}/objects/{object_id}
POST /projects/{project_id}/objects/{object_id}/versions
POST /projects/{project_id}/objects/{object_id}/retire
POST /projects/{project_id}/objects/{object_id}/merge
GET  /projects/{project_id}/objects/{object_id}/neighbors
GET  /projects/{project_id}/links
POST /projects/{project_id}/links
GET  /projects/{project_id}/links/{link_id}
POST /projects/{project_id}/links/{link_id}/versions
POST /projects/{project_id}/links/{link_id}/retire
GET  /projects/{project_id}/graph/path
POST /projects/{project_id}/graph/traverse
POST /projects/{project_id}/graph/subgraph
GET  /projects/{project_id}/events
POST /projects/{project_id}/events
POST /projects/{project_id}/events/{event_id}/correct
GET  /projects/{project_id}/metrics/observations
POST /projects/{project_id}/metrics/observations
POST /projects/{project_id}/metrics/observations/{observation_id}/correct
GET  /projects/{project_id}/projection-versions
GET  /projects/{project_id}/projection-versions/{version_id}
GET  /projects/{project_id}/projection-diffs?from_version_id=&to_version_id=
GET  /projects/{project_id}/projection/as-of
GET  /projects/{project_id}/value-stream
GET  /projects/{project_id}/projection-completeness
GET  /projects/{project_id}/change-sets/{change_set_id}
POST /projects/{project_id}/change-sets/{change_set_id}/validate
POST /projects/{project_id}/change-sets/{change_set_id}/commit
```

### 14.4 Integration 与 Lineage

```text
GET  /projects/{project_id}/sources
POST /projects/{project_id}/sources
PATCH /projects/{project_id}/sources/{source_id}
POST /projects/{project_id}/data-imports/preview
POST /projects/{project_id}/data-imports/confirm
GET  /projects/{project_id}/data-imports
GET  /projects/{project_id}/data-imports/{batch_id}
GET  /projects/{project_id}/source-schemas
GET  /projects/{project_id}/mapping-programs
POST /projects/{project_id}/mapping-programs
GET  /projects/{project_id}/mapping-programs/{program_id}
POST /projects/{project_id}/mapping-programs/{program_id}/versions
POST /projects/{project_id}/mapping-versions/{version_id}/preview
POST /projects/{project_id}/mapping-versions/{version_id}/publish
GET  /projects/{project_id}/pipeline-runs
POST /projects/{project_id}/pipeline-runs
GET  /projects/{project_id}/pipeline-runs/{run_id}
POST /projects/{project_id}/pipeline-runs/{run_id}/resume
POST /projects/{project_id}/pipeline-runs/{run_id}/replay
GET  /projects/{project_id}/import-conflicts
POST /projects/{project_id}/import-conflicts/{conflict_id}/resolve
GET  /projects/{project_id}/lineage/{node_kind}/{resource_id}/upstream
GET  /projects/{project_id}/lineage/{node_kind}/{resource_id}/downstream
GET  /projects/{project_id}/lineage/{node_kind}/{resource_id}/explain
```

### 14.5 Analysis、Causal、Decision

```text
GET/POST  /projects/{project_id}/findings
GET/PATCH /projects/{project_id}/findings/{finding_id}
POST      /projects/{project_id}/findings/{finding_id}/confirm
GET/POST  /projects/{project_id}/causal-variables
GET/POST  /projects/{project_id}/causal-hypotheses
PATCH     /projects/{project_id}/causal-hypotheses/{hypothesis_id}
POST      /projects/{project_id}/causal-hypotheses/{hypothesis_id}/edges
GET       /projects/{project_id}/causal-hypotheses/{hypothesis_id}/graph
POST      /projects/{project_id}/causal-hypotheses/{hypothesis_id}/estimates
GET       /projects/{project_id}/causal-estimates/{estimate_id}
GET/POST  /projects/{project_id}/scenarios
POST      /projects/{project_id}/scenarios/{scenario_id}/run
GET       /projects/{project_id}/scenarios/{scenario_id}/results
GET/POST  /projects/{project_id}/decision-options
GET/POST  /projects/{project_id}/decisions
PATCH     /projects/{project_id}/decisions/{decision_id}
GET/POST  /projects/{project_id}/action-plans
PATCH     /projects/{project_id}/action-plans/{plan_id}
POST      /projects/{project_id}/action-plans/{plan_id}/runs
POST      /projects/{project_id}/action-runs/{run_id}/transitions
POST      /projects/{project_id}/action-runs/{run_id}/outcomes
POST      /projects/{project_id}/outcomes/{outcome_id}/projection-change-set
GET       /projects/{project_id}/decision-timeline
```

### 14.6 Agent、Learning 与 Reporting

```text
GET/POST /projects/{project_id}/agent-runs
GET      /projects/{project_id}/agent-runs/{run_id}
POST     /projects/{project_id}/agent-runs/{run_id}/pause
POST     /projects/{project_id}/agent-runs/{run_id}/resume
POST     /projects/{project_id}/agent-runs/{run_id}/cancel
GET      /projects/{project_id}/agent-runs/{run_id}/artifacts
GET      /projects/{project_id}/approval-requests
POST     /projects/{project_id}/approval-requests/{approval_id}/decide
GET/POST /evaluation-suites
POST     /evaluation-suites/{suite_id}/runs
GET      /evaluation-runs/{run_id}

GET      /projects/{project_id}/learning-events
GET/POST /learning-assets
GET      /learning-assets/{asset_id}
POST     /learning-assets/{asset_id}/versions
POST     /learning-asset-versions/{version_id}/publish
POST     /companies/{company_id}/fingerprints
POST     /peer-cohorts/compute
GET/POST /projects/{project_id}/transfer-trials
POST     /projects/{project_id}/transfer-trials/{trial_id}/transitions

POST /projects/{project_id}/reports
GET  /projects/{project_id}/reports
POST /projects/{project_id}/packages/preview-export
POST /projects/{project_id}/packages/export
POST /package-imports/preview
POST /package-imports/{import_id}/confirm
GET  /package-imports/{import_id}
POST /industry-asset-packages/export
POST /industry-asset-packages/import/preview
POST /industry-asset-packages/import/confirm
```

## 15. 前端路由和页面完成契约

固定路由：

```text
#/home
#/companies
#/projects/:projectId/charter
#/projects/:projectId/research
#/projects/:projectId/candidates
#/projects/:projectId/ontology
#/projects/:projectId/projection
#/projects/:projectId/value-stream
#/projects/:projectId/integration
#/projects/:projectId/lineage
#/projects/:projectId/analysis
#/projects/:projectId/causal
#/projects/:projectId/decisions
#/projects/:projectId/agents
#/projects/:projectId/learning
#/projects/:projectId/reporting
#/hq/peers
#/hq/assets
#/settings/runtime
```

### 15.1 全局壳

- 顶部：产品名、诊断状态、当前用户标签；
- 左栏顶部：公司 selector、项目 selector，永远分开；
- 左栏导航：按 project stage 显示完成度、阻断数和当前页面；
- 主区：route outlet；
- 全局 drawer：Job 进度、错误详情、来源/血缘；
- 刷新恢复：URL projectId 优先，localStorage 只保存最后公司/项目 ID，不保存访问令牌或业务数据。

### 15.2 通用组件

固定实现：`FormField`、`FieldError`、`AsyncButton`、`ResourceTable`、`StatusBadge`、`RevisionConflictDialog`、`PreviewConfirmWizard`、`JobProgressDrawer`、`EvidenceDrawer`、`LineageDrawer`、`CandidateReviewCard`、`EmptyState`、`ConfirmDialog`、`Toast`。

所有表单提交：客户端只做即时易用性校验，服务端错误按 field path 回填；失败不得清空用户输入。

所有后端核心命令必须可由页面执行。专业工作台允许工程细节；`#/home` 不显示表名、JSON、模型参数或 mapping DSL。

### 15.3 页面验收摘要

| 页面 | 必须完成的动作 |
|---|---|
| Companies | 新建、编辑、归档、选择公司和项目 |
| Charter | 章程、管理问题、范围、指标、调研计划、阶段门 |
| Research | 问卷答案导入、访谈、观察、证据、未知、冲突、追问 |
| Candidates | 接受、编辑、合并、拒绝、生成 Projection V1 |
| Ontology | 模板安装、类型版本、约束、release 校验发布 |
| Projection | 对象/关系纠错、路径、子图、as-of、diff |
| Value stream | 对象、事件、等待、指标趋势联动 |
| Integration | Source、文件、Schema、mapping、preview、pipeline、conflict、replay |
| Lineage | 双向展开、来源解释、stale 影响 |
| Analysis/Causal | Finding 证据、DAG、估计、Scenario |
| Decisions | Option、Decision、Action、Outcome、回写 ChangeSet |
| Agents | 配置、运行、步骤、工具、审批、暂停恢复、评测 |
| Learning | LearningEvent、资产候选、Playbook、TransferTrial |
| Reporting | 管理简报、项目包预览/导出/导入 |

## 16. Job Worker 精确行为

Claim 使用单条数据库原子更新：选择 `queued` 且 `available_at<=now`，设置 running、lease owner、lease expiry、attempt+1。PostgreSQL 使用 `FOR UPDATE SKIP LOCKED`；SQLite 使用短 `BEGIN IMMEDIATE`。

- lease 30 秒；运行中每 10 秒续租；
- 默认 max attempts 3；
- retry delay 5s、30s、120s；
- step 成功后持久化 checkpoint；
- cancel_requested 在步骤边界检查；
- 进程崩溃后 lease 过期，reaper 将 job 重置 queued；
- handler 必须幂等，使用 Job ID/Step ID 作为内部幂等键；
- Job progress 仅递增；
- UI 每 1 秒轮询运行中 Job，完成后停止；后续可换 SSE，但当前不实现。

初始 handler：`pipeline.run`、`pipeline.replay`、`agent.run`、`evaluation.run`、`report.render`、`project_package.export`、`project_package.import`、`industry_asset_package.import`。

## 17. 测试规格和质量门

### 17.1 固定测试层

1. Domain unit：纯状态机、版本、时间、基数、因果 DAG、相似度；
2. Repository integration：迁移、FK、唯一、事务、并发、SQLite/Postgres；
3. API contract：每个路径正常、字段错误、not found、跨项目、revision、idempotency；
4. Pipeline：四映射、冲突、失败、resume、replay、lineage；
5. Agent：工具 Schema、白名单、暂停恢复、审批和 Eval；
6. Package：zip 安全、hash、preview、冲突、幂等和引用对账；
7. Browser：从空库执行最终用户旅程；
8. Restart：任务中断、服务重启、Worker 恢复和页面上下文恢复；
9. Migration：当前 V2 副本和 V1 副本只读迁移；
10. Open-source guard：Git tracked files 不得出现数据库、导出包、`.env`、密钥或真实数据。

### 17.2 合成数据集

固定企业 A“东岭精工”：订单延迟、审批层级深、CRM/ERP 客户名不一致、行动后周期改善。

固定企业 B“海川装备”：规模相近、生产模式不同，同一 Playbook 需要调整且结果 inconclusive。

固定文件：问卷答案、访谈记录、CRM 客户、ERP 订单/工单/发货/发票/回款、行动前后指标、一个重复文件、一个坏日期文件、一个跨项目身份冲突文件。

三个固定管理问题：

1. 当前订单延期主要发生在哪个环节？
2. 审批等待是否是交付延迟的重要原因？
3. 哪些客户或订单的回款风险最高，应采取什么行动？

问卷答案 canonical CSV 列：`company_label, questionnaire_key, question_key, prompt, answer`。解析器兼容当前中文列别名，但进入系统后只使用 canonical key。

CRM canonical CSV 列：`customer_id, customer_name, segment, sales_owner, created_at`。

ERP order-to-cash canonical CSV 列：`order_id, customer_code, customer_name, product_code, department_code, order_created_at, approval_completed_at, promised_date, work_order_id, plan_released_at, production_completed_at, shipment_id, shipment_delivered_at, invoice_id, invoice_issued_at, invoice_due_at, payment_id, payment_received_at, order_amount, gross_margin_rate, status`。

东岭精工 fixture 必须包含 100 个订单：60 个按时、40 个延期；延期组的审批等待明显更长；行动后窗口审批等待和延期率下降。海川装备包含 80 个订单，但延期主因设置为物料等待，使企业 A 的审批 Playbook 在 B 中只得到 inconclusive，防止测试把“相似”误写成“相同”。

### 17.3 每个原子任务完成门

- 新模型有 migration 和约束测试；
- 新 service 有正常、负向和状态测试；
- 新 API 有 OpenAPI schema 和错误测试；
- 新核心命令有页面入口和浏览器测试；
- 新异步功能有重启恢复测试；
- 变更后 `ruff`、`mypy`、`pytest`、前端语法、OpenAPI 前端契约全部通过；
- 无 TODO 代替当前任务验收；
- 文档中的能力状态同步更新。

## 18. V1/V2 迁移和切换

### 18.1 当前 V2 数据升级

先备份当前 `eip-v2.db`。迁移将：

- 当前 ObjectType/LinkType/EventType/MetricType 转为 ontology type version 1；
- 建立 release 1；
- 当前 Object/Link 转稳定身份 + version 1；
- 当前 Event/Metric 进入 initial migration ChangeSet；
- 当前 ResearchImport/Answer 保留原 ID 并映射 batch/response/answer；
- 当前 MappingProgram 转 object mapping version 1；
- 当前 Lineage 转通用 node/edge；
- 当前 Finding/Decision/Action/Outcome 转当前聚合 revision 1；
- 当前 AgentRun/Step 转 legacy deterministic artifact，不能伪装成新工具调用。

### 18.2 V1 切换

R10 之前 V1 只读冻结。迁移器读取 V1 副本并输出：输入数量、成功、跳过、冲突、失败、ID map、hash 和人工清单。V2 最终浏览器验收通过后：桌面快捷方式改到 `/v2/#/home`，停止挂载 V1 router/page，保留一次离线 V1 数据备份，然后删除旧运行代码。

## 19. 执行中禁止重新决策的事项

以下选择已经冻结：

1. 外部问卷系统通过版本化 CSV 接口连接，不共享数据库；
2. 首场景固定制造业订单到回款；
3. 使用模块化单体、关系数据库和邻接表图查询；
4. 投影使用 ChangeSet + ProjectionVersion，不复制整库；
5. Object/Link 和 OntologyType 使用稳定身份 + 不可变版本；
6. 数据接入使用声明式 Mapping DSL，不执行用户代码；
7. 长任务使用数据库 Job，不引入外部队列；
8. 前端使用 buildless ES modules；
9. Agent 当前最高 A2–A3，外部写回禁用；
10. 因果首版最高 C1/L2；
11. 同行经验必须经过 TransferTrial；
12. 项目包统一支持在线上传和线下拷贝；
13. 真实数据默认在仓库外；
14. R10 验收前不删除 V1；
15. 不增加安全治理、微服务、实时流和独立图数据库产品线。

若执行中需要改变以上任一项，必须先停止相应任务，写 ADR，并更新 19–21 号文档后继续。
