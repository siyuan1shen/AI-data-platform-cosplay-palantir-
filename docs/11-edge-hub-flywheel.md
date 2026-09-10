# 分部边缘终端—总部数据飞轮总体架构（冻结）

> 本方案已冻结为可选的数据搬运能力，不再是产品主架构，也不再投入安全协议、在线传输和总部基础设施建设。当前主线是单机可完成调研、数字投影、分析、决策、行动与学习；需要汇总时优先使用普通文件导入导出。权威规划见[管理层企业决策投影总体规划](13-executive-decision-platform.md)。

本文记录旧版“分部边缘终端”和“总部中心枢纽”部署及交换包协议，只用于理解现有实现。若未来重新启用，必须根据届时真实部署需求重新立项和评估，不能直接作为现行产品要求。

截至 2026-08-24，运行档位、本地免登录、安装身份、EIP-XP/1 加密签名包、线下导入、在线断点续传、缺包自动追平、多版本回执、总部 Raw Landing/候选以及签名 Release 回流均已有代码和双节点测试。本文同时保留生产目标；Standardized/Trusted Fact 自动编排、恶意文件扫描、类型专用 Shadow Eval、OIDC/RLS/Worker/HA 等尚未完成的部分，以[V1 实现状态](09-v1-status.md)为准。

## 1. 核心决策

1. 分部终端负责采集、初步整理、质量检查、导出预览和发送；总部负责跨分部统一语义、冲突治理、企业解读、多 Agent 分析和数据飞轮。
2. 总部不得直接连接分部数据库，分部也不得直接写总部业务表。唯一跨边界载体是不可变、版本化、签名、加密的数据包及回执。
3. 在线传输和 U 盘、移动硬盘、内网摆渡等离线传输共用完全相同的 `EIP Exchange Bundle`。传输适配器只负责移动字节，不改变业务语义。
4. 分部原始数据可以按企业内部政策传给总部，但默认不进入跨企业行业共享。总部与分部属于同一 Tenant，分部由独立 `OrganizationUnit` 和 `EdgeNode` 标识。
5. 分部单机默认不要求平台账号密码；总部多人服务必须使用强身份认证、RBAC/ABAC 和完整审计。设备身份与人员身份分离。
6. 导入只追加版本，不覆盖来源。重复包幂等返回同一回执，更正通过新修订、取代或墓碑完成。
7. 总部飞轮产生的 QuestionPack、Ontology、Mapping Program、Agent Recipe、Playbook 和策略，以签名发布包回流分部；分部私有 Overlay 不自动回传。

## 2. 总体拓扑

```text
┌──────────────────────── 分部 A ────────────────────────┐
│ Edge Console → Local Store → Export Planner           │
│                         ↓                              │
│                 Bundle Builder/Signer                  │
└─────────────────────────┬──────────────────────────────┘
                          │
               HTTPS 上传 │ 或 U盘/移动硬盘拷贝
                          │ 同一 .eipbundle
┌─────────────────────────▼──────────────────────────────┐
│                    总部 Hub Ingress                    │
│ 收件箱 → 签名/解密 → 隔离扫描 → 契约校验 → 幂等登记      │
│                         ↓                              │
│ Raw Landing → Semantic Harmonization → Trusted Facts  │
│                         ↓                              │
│ Agent/Eval/Review → Enterprise Insight → HQ Flywheel  │
│                         ↓                              │
│ Signed Release Registry → 分部更新包/问题包/映射程序     │
└─────────────────────────┬──────────────────────────────┘
                          │
              .eipreceipt / .eiprelease
                          │
┌─────────────────────────▼──────────────────────────────┐
│ 分部验签 → 回执对账 → 更新包 Shadow Eval → 人工启用       │
└────────────────────────────────────────────────────────┘
```

分部 B、C 等使用同一流程。总部按 Tenant 隔离不同企业集团，在 Tenant 内按 OrganizationUnit 隔离分部来源并允许获授权的集团级分析。

## 3. 三种运行档位

| 档位 | 典型部署 | 默认身份方式 | 默认存储 | 允许的职责 |
|---|---|---|---|---|
| `edge_local` | 分部个人电脑，绑定 `127.0.0.1` | 本机所有者身份，不要求平台密码 | SQLite + 本地文件 | 采集、整理、导出、发送、接收总部发布 |
| `edge_team` | 分部内网小服务器 | 本地账户或企业 OIDC | PostgreSQL/SQLite | 多人采集、分部审核、导出审批 |
| `hq_hub` | 总部内网或私有云 | OIDC/SSO、MFA、RBAC/ABAC | PostgreSQL + 对象存储 + Worker | 收件、统一语义、飞轮、集团解读、发布 |

开发环境可使用 `all_in_one_dev` 同进程演示两个角色，但生产部署不得用该档位绕过边界。

### 3.1 本地免登录边界

`edge_local` 只有同时满足以下条件才可免平台密码：

- 监听地址是回环地址；
- 数据目录仅当前操作系统用户可写；
- 未启用远程管理；
- 内部仍创建不可伪造的 Local Owner Actor，用于审计和领域权限；
- 一旦改为非回环监听，启动必须拒绝，直到配置 `edge_team` 身份体系。

免登录不等于取消授权模型。API 内部仍使用 RequestContext、权限和审计，只是身份由受信任本机启动边界建立，而不是让用户重复输入账号密码。

### 3.2 总部身份边界

`hq_hub` 不提供免登录和默认密码。总部至少区分：Hub Operator、Data Intake Reviewer、Data Steward、Analyst、Agent Reviewer、Registry Publisher、Security Auditor。高风险导入放行、跨分部实体合并、行业发布和密钥轮换要求职责分离。

设备身份使用 EdgeNode 密钥，不复用人员登录令牌。人员离职不应导致历史数据包失去可验证性，设备吊销也不应删除历史签名记录。

## 4. 组织、分部和来源标识

```text
Tenant                         企业集团或独立公司
└─ OrganizationUnit           总部、事业部、工厂、区域公司或门店
   └─ EdgeNode                某个已登记的数据采集终端
      └─ SourceSystem         问卷、ERP、CRM、MES、Excel 等来源
         └─ OriginRecord      来源系统内稳定记录
```

每条交换记录至少携带：

- `tenant_id`：总部登记后分配的企业集团边界；
- `organization_unit_id`：分部稳定 ID；
- `edge_node_id`：产生数据包的终端；
- `source_system_id` 和 `source_schema_version`；
- `origin_record_id`、`origin_revision`；
- `observed_at`、`valid_from`、`valid_to`；
- `classification`、`distribution_scope`；
- 内容哈希与来源血缘。

总部不能用名称拼接代替这些 ID。分部改名只增加别名或新版本，不改变历史来源标识。

交换记录中的 `tenant_id`、`organization_unit_id` 和 `edge_node_id` 只是发送方声明。总部以“已登记的签名公钥 → EdgeNode → OrganizationUnit → Tenant”关系解析权威边界，并把包内声明仅用于一致性校验；发送方不能通过改写 Header 或 Payload 把数据送入其他 Tenant。

## 5. 数据范围是三个正交维度

避免用一个 L0/L1 标签同时表达敏感度、加工度和可见范围。每份记录分别标注：

| 维度 | 取值示例 | 用途 |
|---|---|---|
| `classification` | public / internal / confidential / restricted | 决定加密、审批、模型和日志策略 |
| `processing_level` | raw / deidentified / aggregate / generalized | 表达加工和匿名程度 |
| `distribution_scope` | branch_only / organization_hq / organization_all / consortium / public | 表达允许到达的边界 |

因此，原始问卷回答可以是 `confidential + raw + organization_hq`，允许传总部但不允许跨企业共享；模型密钥必须是 `restricted + raw + branch_only`，永不进入数据包；行业通用映射可为 `internal + generalized + consortium`。

现有行业飞轮 L0–L3 只描述跨企业贡献成熟度，不能代替这三个字段。总部内部汇聚不自动提升任何行业共享等级。

## 6. EIP Exchange Bundle

协议正式名称为 `EIP Exchange Package Protocol 1.0`，简称 `EIP-XP/1`。`.eipbundle` 是它的用户可见文件扩展名，不代表第二套格式。

### 6.1 外层结构

离线文件扩展名为 `.eipbundle`。在线协议上传的也是同一 Bundle 的 Header 和加密 Chunk，不另建在线专用业务格式。

```text
bundle.eipbundle
├─ header.json                 非敏感路由与协议元数据
├─ chunks/
│  ├─ 00000001.eipenc          加密内容块
│  ├─ 00000002.eipenc
│  └─ ...
└─ signature.ed25519           分部对规范化 Header 和全部 Chunk 哈希签名
```

物理容器使用确定性 ZIP64、`STORE` 模式和 UTF-8 路径：条目按字典序排列，时间戳固定，不允许注释、重复路径、绝对路径、`..`、符号链接或设备文件。实现必须在解包前限制总字节数、条目数、单条目大小、路径长度和嵌套深度。这样既可流式处理，也可避免不同打包工具生成不同签名输入。

`header.json` 只允许包含：

- 协议主/次版本、必需能力位和可忽略扩展；
- `bundle_id`、发送方和接收方 ID；
- `sender_key_id`、`recipient_key_id`；
- 创建时间、有效期、Bundle 类型；
- `stream_id`、`epoch_id`、单调递增 `sequence` 和本包前后 Watermark；
- `previous_bundle_hash`，用于发现缺包和乱序；
- Chunk 大小、数量、逐块 SHA-256 和整体内容摘要；
- 加密算法、密钥封装算法及所需非敏感参数。

Header 不包含企业名称、问卷文本、回答、字段值、文件名、密钥原文或可识别个人信息。

未知协议主版本或未知“必需能力位”必须拒绝；同一主版本内，未知的可忽略字段可以保留并转发。`stream_id + epoch_id` 定义一条增量链：恢复备份、重新登记或明确全量重置时新建 Epoch，不能把序列号悄悄归零。

### 6.2 加密内层

解密后的逻辑内容为：

```text
payload/
├─ manifest.json
├─ schemas/
│  └─ *.schema.json
├─ records/
│  ├─ questionnaires.ndjson
│  ├─ assessments.ndjson
│  ├─ respondents.ndjson
│  ├─ answers.ndjson
│  ├─ evidence.ndjson
│  ├─ source_records.ndjson
│  ├─ mappings.ndjson
│  ├─ ontology_overlays.ndjson
│  ├─ facts.ndjson
│  ├─ conflicts.ndjson
│  └─ feedback.ndjson
└─ blobs/
   └─ sha256/<digest>
```

V1 交换记录使用 NDJSON，便于流式生成、校验、断点处理和故障定位；大型分析表可以在后续次版本增加 Parquet，但不能改变现有记录语义。Blob 使用内容寻址，不信任原文件名。

`manifest.json` 记录筛选条件、时间边界、记录数、每种 Schema 版本、Bundle 生成器版本、数据等级统计、排除项、父 Bundle、所有内部文件哈希和可重放导入顺序。

### 6.3 密码学顺序

1. 生成内层 Manifest、记录和 Blob，并计算逐项哈希；
2. 按确定性顺序组装内容流；
3. 使用随机 256 位数据密钥和 AES-256-GCM 分块加密，每块使用唯一 nonce；
4. 使用标准 RFC 9180 HPKE 和总部当前 X25519 接收公钥封装数据密钥，不自创密钥封装协议；
5. 使用分部 EdgeNode 的 Ed25519 私钥签署规范化 Header 与全部加密 Chunk 哈希；
6. 私钥和模型 API Key 永不写入 Bundle。

总部先验证大小、路径和签名，再解密。签名错误、未知发送方、已吊销密钥、错误接收方、Chunk 哈希错误或 AEAD 校验失败均在业务解析前拒绝。

每个 EdgeNode 维护独立的 Ed25519 签名密钥和 X25519 接收密钥；总部也维护自己的签名和接收密钥。轮换后旧私钥至少保留到“最大离线携带窗口 + 最大待处理积压期”结束，旧公钥保留到审计保留期结束。签名只证明来源和内容未被改动，不证明来源数据真实；终端被攻陷仍需通过撤销、异常检测、人工复核和来源对账处理。

### 6.4 Bundle 类型

| 类型 | 用途 |
|---|---|
| `snapshot` | 首次全量或明确时间点快照 |
| `delta` | 自上次已确认游标后的新增修订 |
| `correction` | 取代错误记录或补充缺失血缘 |
| `tombstone` | 请求停止使用或撤回指定来源记录，不抹除审计 |
| `receipt` | 总部对接收、拒绝、缺包和最终提交的签名回执 |
| `release` | 总部向分部下发的受治理知识与程序资产 |

Receipt 和 Release 使用相同信封、安全和版本规则，文件扩展名可分别为 `.eipreceipt` 与 `.eiprelease`。

## 7. 状态机与幂等

### 7.1 分部导出状态

```text
draft → validated → sealed → queued
                     ├─ copied_offline → receipt_pending
                     └─ uploading → uploaded → receipt_pending
receipt_pending → accepted | rejected | quarantined
accepted → superseded（被后续更正包取代）
```

已 `sealed` 的 Bundle 永不修改。若筛选范围、数据或接收方变化，生成新 `bundle_id`。

V1 不允许“静默部分导入”：一个 Bundle 要么事务性提交全部业务记录，要么一条也不进入可信层。若不同数据组需要独立成功或重试，应在导出端拆成多个 Bundle；回执可以报告逐类校验问题，但不能把失败记录悄悄跳过。

### 7.2 总部导入状态

```text
received
→ envelope_verified
→ decrypted
→ malware_and_dlp_scanned
→ contract_validated
→ sequence_ready
→ staged
→ semantic_processing
→ committed
→ receipt_issued
```

缺少前序包时从 `contract_validated` 进入 `gap_waiting`，补齐后才进入 `sequence_ready`。任何阶段可进入 `rejected` 或 `quarantined`。Quarantine 与 `gap_waiting` 不允许被 Agent、搜索或分析读取；人工放行会产生独立审核记录，不能改写原始检测结果。

### 7.3 幂等规则

- 主键为“总部根据已登记发送方密钥解析出的权威 Tenant + `bundle_id`”，不信任包内 Tenant 声明；
- 相同 Bundle ID 与相同整体哈希再次到达，返回原回执，不重复导入；
- 相同 Bundle ID 但哈希不同，视为篡改或实现错误并拒绝；
- 每条来源记录以 `edge_node_id + source_system_id + origin_record_id + origin_revision` 去重；
- 重复导入不能重复产生 Fact、Agent 运行或正式报告；
- 分部同时保存 `sealed_cursor` 和 `hq_confirmed_cursor`，下一增量默认从总部已确认游标生成，不能因“已经复制到 U 盘”就推进确认游标；
- `previous_bundle_hash` 缺失时允许把密文收进隔离区并进入 `gap_waiting`，但不得提交 Raw Landing、语义层或触发 Agent；补齐前也不得声称数据完整。

## 8. 分部用户流程

### 8.1 首次登记

总部管理员生成一次性 Enrollment Bundle，包含 Tenant、OrganizationUnit、Hub 地址、总部接收公钥、协议兼容范围和一次性登记凭据。分部可在线兑换，也可离线导入。

分部终端生成本地 Ed25519/X25519 设备密钥，只把公钥和设备证明回传总部。登记完成后一次性凭据失效。

### 8.2 一键导出

分部控制台应把复杂度收敛为：

1. 选择“发给哪个总部”和时间范围；
2. 选择问卷、系统数据、附件、映射和反馈类别；
3. 查看记录数量、敏感等级、排除项、估算大小和所需审批；
4. 点击“加密打包”；
5. 选择“在线发送”或“保存到移动介质”。

默认支持“自上次总部确认后增量导出”。用户可生成全量快照，但系统必须明确显示大小和重复风险。导出完成后保留 Manifest、哈希和状态，不保留额外明文副本。

### 8.3 对账

在线模式自动拉取回执。离线模式由总部生成 `.eipreceipt`，分部从移动介质导入。回执显示接收数量、拒绝数量、缺失序列、Schema 问题、最终总部提交 ID 和时间，但不回显总部其他分部数据。

## 9. 在线传输

在线适配器使用 HTTPS，但业务安全不只依赖传输层：Bundle 本身仍签名和端到端加密。

目标接口：

```text
POST   /api/v1/exchange/upload-sessions
PUT    /api/v1/exchange/upload-sessions/{id}/chunks/{number}
GET    /api/v1/exchange/upload-sessions/{id}
POST   /api/v1/exchange/upload-sessions/{id}:complete
GET    /api/v1/exchange/upload-sessions/{id}/receipt
```

- 创建会话时提交 Header、签名和 Chunk 哈希；
- 总部返回已存在 Chunk 位图，实现断点续传；
- 默认 Chunk 目标大小为 8 MiB，可按环境配置但必须写入 Header；
- Chunk 可重试且按哈希幂等，传输结束后还必须验证整个 Bundle 摘要；
- `complete` 仅在全部 Chunk 到齐、逐块哈希和整体摘要一致时生效；
- 网络中断不会生成半个业务导入；
- 分部 Outbox 负责重试、退避、死信和人工重放；
- 总部限流按 Tenant、EdgeNode、并发会话和总字节数执行。

## 10. 离线传输

- 导出前检查目标移动介质剩余空间和文件系统限制；FAT32 的单文件上限必须在用户选择介质时明确提示；
- 先写入同目录 `.part` 临时文件，完成全量哈希和 `fsync` 后原子改名为 `.eipbundle`，避免把半成品当成正式包；
- 支持安全分卷，每卷都绑定相同 Bundle ID、卷号、总卷数、卷哈希和整体 Bundle 哈希；缺任一卷不得导入；
- 介质丢失时因端到端加密不泄露明文，但仍必须登记安全事件并在必要时吊销设备或接收密钥；
- 总部先把移动介质上的文件按只读方式复制到总部隔离区，校验副本后再解析，禁止直接在 U 盘上解包；
- 总部导入采用临时隔离目录、路径穿越防护、压缩炸弹上限、文件数量上限和恶意文件扫描；
- 不执行介质中的任何程序、快捷方式、宏或脚本；
- 导入成功后生成独立签名回执，不在原 Bundle 内修改状态；
- 无法返送回执时，分部保留 `receipt_pending`，下一次在线连接或离线对账再确认。

## 11. 总部数据分层

```text
Ingress Quarantine   原加密包、扫描结果、签名和接收审计
Raw Landing          解密后的不可变来源版本，按 Tenant/分部/Bundle 分区
Standardized         类型、单位、编码、时间和 Schema 已标准化的记录
Semantic Core        Canonical Entity、Alias、Identifier、Mapping、Lineage
Trusted Knowledge    Fact、Claim、Conflict、Finding、Recommendation、Outcome
Flywheel Assets      QuestionPack、Ontology、Program、Recipe、Playbook、EvalSuite
Serving Views        企业全景、跨分部指标、报告、搜索和 Agent 授权上下文
```

每层只读取上一层的版本化输出，保留 `bundle_id → origin_record → transformation → fact/artifact → report/release` 全链路。Raw Landing 永不因标准化成功而删除来源版本。

## 12. 跨分部语义统一

总部按以下顺序处理同物异名：

1. 确定性外部标识：统一社会信用代码、系统 GUID、合同号、物料主键等；
2. 分部已审核的 Identity Mapping；
3. 总部已发布的 Mapping Program；
4. 名称、地址、上下文和关系相似度仅产生候选；
5. 高风险实体合并由 Data Steward 审核，以可逆 Link/Supersede 表达。

字段、枚举、单位、公式和流程事件分别映射。总部权威策略按 Predicate、用途、时间和 OrganizationUnit 配置，例如财务金额以财务系统为主、完工时间以 MES 为主。非权威来源仍保留为并列 Fact，不能被覆盖。

当两个分部对同一对象给出不同值时：

- 若时间区间不同，形成时间版本；
- 若口径不同，保留各自口径并要求 Mapping；
- 若真实矛盾，进入 Conflict Center；
- 若证据不足，标记 Unknown，不允许 Agent 猜测补齐。

## 13. 总部数据飞轮

总部飞轮不是把新数据直接喂给模型，而是受控的五条循环：

### 13.1 数据质量飞轮

导入错误、缺字段、Schema Drift 和对账结果 → 质量规则候选 → 固定数据集验证 → 人工发布 → 分部导出前预检。

### 13.2 调研飞轮

跨分部未知信息槽、追问有效率和回答冲突 → Question/Follow-up 候选 → 专家审核 → QuestionPack 新版本 → 分部下发 → 新回答回总部。

### 13.3 语义飞轮

未映射字段、实体候选、枚举和单位冲突 → Ontology/Mapping 候选 → Data Steward 审核 → Shadow Replay → Mapping Program 发布 → 分部预处理与总部复用。

### 13.4 Agent 飞轮

Agent 提案、引用覆盖、人工修改、拒绝理由和后续结果 → Gold/Silver/Bronze 反馈分层 → 固定 Eval Suite → Recipe/Prompt/Retrieval 候选 → 独立评测 → 发布。

### 13.5 经营结果飞轮

Finding → Recommendation → Action → Outcome → 验证哪些诊断模式真实有效，并更新风险规则、Playbook 和报告模板。没有后续结果的点击行为不能作为正确标签。

所有候选先进入 Proposal，固定模型和固定评测集下证明提升，再由相应角色发布。默认优先改进规则、检索、问题、映射和工作流，不自动在线训练模型。

## 14. 总部向分部回流

`.eiprelease` 可包含：

- QuestionPack 与追问策略；
- Ontology 模块和兼容范围；
- Mapping/Validation Program；
- Agent Recipe 和结构化输出 Schema；
- Diagnostic Playbook；
- Eval Suite 的合成或获授权夹具；
- 数据分类、导出和模型路由策略；
- 撤回墓碑与补偿版本。

Release 必须包含稳定 ID、不可变版本、依赖锁、平台兼容范围、内容哈希、总部签名、评测摘要和撤回地址。分部先验签和 Shadow Eval，再人工启用；不得把收到的程序直接当脚本在主进程执行。

## 15. 模块边界

```text
enterprise_insight.exchange.contracts     Bundle/Receipt/Release 公共契约
enterprise_insight.exchange.bundle        规范化、分块、签名、加解密、校验
enterprise_insight.exchange.export        分部筛选、快照和增量游标
enterprise_insight.exchange.transports    file/http 可替换适配器
enterprise_insight.edge                    Enrollment、Outbox、对账和本地 UX
enterprise_insight.hub.ingress             收件箱、隔离区、导入状态机
enterprise_insight.hub.harmonization       标准化和语义合并编排
enterprise_insight.hub.flywheel            候选、评测、审核和发布
enterprise_insight.hub.distribution        Receipt/Release 生成与分发
```

约束：

- Edge 和 Hub 业务模块不能直接导入对方的 Repository；
- 两端只共享 Contract、密码学原语和兼容测试夹具；
- Transport 接口只接受密封 Bundle，不接触领域 ORM；
- Importer 先落 Raw Landing，再通过领域命令写标准层；
- 扩展点使用版本化 Port/Adapter，不允许插件直接访问数据库；
- 当前模块化单体可承载这些边界，只有吞吐、安全或团队所有权要求出现时再拆服务。

## 16. 关键事件

```text
eip.exchange.bundle.planned
eip.exchange.bundle.sealed
eip.exchange.bundle.uploaded
eip.exchange.bundle.received
eip.exchange.bundle.quarantined
eip.exchange.bundle.rejected
eip.exchange.bundle.committed
eip.exchange.receipt.issued
eip.exchange.sequence.gap_detected
eip.hub.semantic_conflict.detected
eip.hub.flywheel.candidate.created
eip.hub.release.published
eip.edge.release.activated
```

事件不携带默认原文，只携带引用、摘要、级别和哈希。Outbox 至少一次投递，消费者以事件 ID、Bundle ID 和聚合修订实现业务幂等。

## 17. 威胁与失败处理

| 风险 | 默认处理 |
|---|---|
| Bundle 被篡改 | 签名或哈希失败，业务解析前拒绝 |
| 错发给其他总部 | 接收方 Key ID 不匹配且无法解密 |
| 重放旧 Bundle | Bundle ID 幂等、序列链和有效期校验 |
| U 盘丢失 | 端到端加密、事件登记、必要时密钥吊销 |
| 压缩炸弹/路径穿越 | 解包上限、禁止绝对路径/`..`/链接、隔离扫描 |
| 在线中断 | Chunk 哈希、断点续传、Outbox 重试 |
| 重复导入 | 返回原回执，不重复产生业务对象 |
| 分部缺包或乱序 | `previous_bundle_hash` 和序列检测，进入 `gap_waiting`，补齐前不提交可信层 |
| Schema 新旧不兼容 | 未知主版本或未知必需能力拒绝；兼容次版本进入版本适配器 |
| 分部终端已被攻陷 | 签名不等于真实性证明；吊销设备、冻结流、来源对账并重建可信 Epoch |
| 错误实体合并污染全局 | 候选、人审、可逆 Link、影响分析和补偿版本 |
| 总部 Agent 越权读取 | ABAC 按 Tenant、分部、用途、级别过滤授权 Artifact |
| 总部发布错误程序 | 签名、固定 Eval、Shadow、人工发布、撤回墓碑 |
| 敏感值出现在日志/错误 | 只记录摘要、计数、哈希和关联 ID |

总部生产审计日志采用追加写、周期性哈希链检查点和独立签名；有监管要求时将检查点或归档写入 WORM 存储。日志管理员不能同时拥有修改业务数据和销毁审计证据的权限。

## 18. 可观测性与对账指标

- Bundle 生成成功率和耗时；
- 每分部最后已确认 Export Sequence；
- 在线重试率、续传字节和死信数；
- 离线 Bundle 平均待回执时间；
- 签名、解密、DLP、恶意文件和 Schema 拒绝率；
- 导入记录数与分部 Manifest 对账差异；
- Raw → Standardized → Fact 的损耗和冲突率；
- 跨分部实体自动候选/人工确认率；
- Release 安装率、Shadow Eval 通过率和撤回传播时间；
- 固定模型下的 Flywheel Lift；
- 跨 Tenant 泄漏、未授权行业发布和静默覆盖必须为零。

## 19. 实施阶段

### Phase A：契约与运行档位

- 增加 `edge_local`、`edge_team`、`hq_hub` 配置；
- 分离本机所有者、人员身份和设备身份；
- 定义 Bundle/Receipt/Release Pydantic 契约和 JSON Schema；
- 建立历史样例和兼容性测试。

### Phase B：离线最小闭环

- 分部导出问卷、答案、证据元数据和 Blob；
- Bundle 分块、加密、签名和导出预览；
- 总部文件收件箱、隔离、幂等导入和签名回执；
- 分部导入回执并推进增量游标。

离线先行可以在没有网络、OIDC 和复杂基础设施时验证真正的数据边界。

### Phase C：更正、缺包和语义汇聚

- Correction/Tombstone；
- 序列链和 Gap 检测；
- Raw Landing、标准化和血缘；
- OrganizationUnit、EdgeNode、SourceSystem；
- 跨分部实体候选、Mapping 和 Conflict Center。

### Phase D：在线可恢复传输

- Enrollment；
- Upload Session、Chunk、断点续传；
- Edge Outbox、Hub 限流、死信和回执拉取；
- Key Rotation/Revocation。

### Phase E：总部多 Agent 与飞轮

- 总部授权上下文和 Artifact Blackboard；
- 数据质量、调研、语义、Agent、经营结果五条飞轮；
- 固定 Eval、人工审核、版本 Registry；
- Release 构建、签名、Shadow 和撤回。

### Phase F：企业生产化

- PostgreSQL RLS、对象存储、队列 Worker；
- OIDC/SSO、MFA、Vault/HSM；
- HA、容量、备份恢复、合规保留；
- 多总部或区域 Hub 只在实际组织需要时增加。

## 20. 验收标准

1. 同一 Bundle 通过在线和离线两种路径导入，产生完全相同的 Raw Snapshot 哈希和业务结果；
2. 任意字节被修改、签名密钥被吊销或接收方错误时，Bundle 在业务解析前被拒绝；
3. 上传中断后只补传缺失 Chunk，不重新发送全部数据；
4. 同一 Bundle 导入十次仍只产生一份来源修订和一份回执；
5. 交换链路必须流式处理，内存占用不随总数据量线性增长；发布前的默认容量基线为 20 GiB、200 万记录、8 MiB Chunk、峰值内存不超过 512 MiB，并按真实分部规模重新校准；
6. 离线回执可推进分部增量游标，缺失回执不会误标记为总部已接收；
7. 更正不覆盖历史记录，所有受影响 Fact、报告和 Release 可被定位；
8. 同名客户或物料不会被静默合并，跨分部冲突进入审核中心；
9. 分部原始回答可以进入获授权的总部范围，但不能无审批进入 Consortium/Public Registry；
10. `edge_local` 回环部署无需账号密码；`hq_hub` 无强认证配置时拒绝启动；
11. 总部发布包在分部验签、兼容检查和 Shadow Eval 前不能启用；
12. 从 Bundle 到报告、从报告到来源记录的血缘查询可完整往返。
13. 缺少前序 Delta 时，后续包可以安全收件但不能进入 Raw Landing、触发 Agent 或推进总部确认游标；
14. 任一业务记录校验失败时整个 Bundle 不提交，导出端可按独立数据组拆包后重试。

## 21. 明确不做

- 不让总部直接查询分部数据库；
- 不把共享文件夹当作事务或消息队列；
- 不为在线和离线维护两套业务格式；
- 不以“最后写入者获胜”合并跨分部数据；
- 不把模型密钥、数据库密码或设备私钥放入 Bundle；
- 不因数据到达总部就自动进入跨企业行业共享；
- 不让 Agent 自动批准自身提案、自动发布映射或自动训练模型；
- 不在 V1 交换协议中引入区块链、复杂微服务或全局万能 EAV。
