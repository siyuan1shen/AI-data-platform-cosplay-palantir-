# API 与事件契约

## 1. 目标

API 和事件是模块边界，也是未来拆分服务、开发前端和接入第三方的兼容基础。所有接口必须可版本化、可幂等、可审计，并明确区分命令、查询、提案和正式知识。

本文同时记录目标契约。当前 V1 已挂载的精确路径以运行时 `/openapi.json` 为准，已实现范围见 [V1 实现状态](09-v1-status.md)；文中 Source、SSE、Webhook、统一分页和全局 HTTP 幂等要求仍包含后续生产化内容。

## 2. 基本约定

- HTTP API 前缀：`/api/v1`；
- JSON 字段使用 `snake_case`；
- 时间使用带时区的 ISO 8601 UTC；
- ID 使用服务端生成的 UUID；
- 稳定业务标识使用 URI；
- 金额使用字符串十进制值加货币；
- 数量使用字符串十进制值、单位和口径；
- 请求与响应模型默认拒绝未知字段；
- 所有写请求支持关联 ID，关键命令支持幂等键；
- API 不把内部 ORM 模型直接序列化给客户端。

## 3. 资源模型

第一阶段资源分组：

```text
/api/v1/workspaces
/api/v1/sources
/api/v1/evidence
/api/v1/entities
/api/v1/mappings
/api/v1/facts
/api/v1/conflicts
/api/v1/agent-runs
/api/v1/proposals
/api/v1/findings
/api/v1/reviews
/api/v1/reports
/api/v1/extensions
/api/v1/registry
/api/v1/exchange
/api/v1/hub/ingress
/api/v1/releases
/api/v1/audit-events
```

资源 URL 表达身份，动作端点仅用于无法自然表示为 CRUD 的领域命令，例如：

```text
POST /api/v1/sources/{source_id}:profile
POST /api/v1/mappings/{mapping_id}:validate
POST /api/v1/agent-runs
POST /api/v1/proposals/{proposal_id}:approve
POST /api/v1/conflicts/{conflict_id}:resolve
POST /api/v1/extensions/{extension_id}:publish
POST /api/v1/extensions/{extension_id}:withdraw
```

正式 Artifact 不提供无条件覆盖式 `PUT`。纠错通过新版本、撤回、取代或显式冲突解决完成。

## 4. 请求上下文

入口层从身份令牌和网关生成：

- `request_id`：单个 HTTP 请求；
- `correlation_id`：跨请求/事件业务链；
- `causation_id`：直接触发当前动作的命令或事件；
- `tenant_id`：从受验证身份解析；
- `actor_id` / `actor_type`：用户、服务或工作负载；
- `purpose`：调查、诊断、整改、评测或发布；
- `policy_snapshot_id`：本次授权依据。

客户端不能通过请求体覆盖 `tenant_id`、`actor_id` 或已验证权限。

在 `edge_local`，身份由回环监听与本机 Local Owner Provider 建立；在 `hq_hub`，身份来自 OIDC/SSO。数据包自报的 `tenant_id` 只可用于路由提示，总部必须从已登记的 EdgeNode 公钥和 Enrollment 绑定解析真实 Tenant/OrganizationUnit。

## 5. 响应与错误

单资源返回资源本身；集合使用统一信封：

```json
{
  "items": [],
  "next_cursor": null,
  "has_more": false
}
```

错误采用 Problem Details 语义：

```json
{
  "type": "urn:eip:error:workflow:invalid-transition",
  "title": "Invalid workflow transition",
  "status": 409,
  "detail": "A completed run cannot return to running.",
  "instance": "/api/v1/agent-runs/…",
  "error_code": "WORKFLOW_INVALID_TRANSITION",
  "request_id": "…",
  "violations": []
}
```

错误文本不得泄露密钥、数据库结构、跨租户资源存在性或 Evidence 原文。

## 6. 幂等与并发

生产化后以下命令要求 `Idempotency-Key`：

- 导入与同步；
- 启动 Agent/Workflow；
- 审核和发布；
- 导出；
- 外部系统回写。

V1 已实现按租户、操作和幂等键隔离的持久化服务、规范请求哈希、结果重放和冲突检测，但尚未对每个 HTTP 写端点统一强制该 Header。接入外部同步或异步 Worker 前必须完成入口集成。

可变聚合根包含 `revision` 和 `ETag`。更新要求 `If-Match`，版本不一致返回 `409` 或 `412` 并提供当前修订号。不得以最后写入者获胜隐藏业务冲突。

## 7. 分页、过滤和导出

- 在线列表使用不透明 cursor，不使用大 offset；
- 过滤字段白名单化并进行类型校验；
- 排序必须包含唯一稳定的次级键；
- 大型导出使用异步 Job，返回状态和短期下载句柄；
- 导出包附带筛选条件、时间、版本快照和内容摘要；
- Restricted 数据导出触发额外审批和审计。

面向人工阅读的 JSON/CSV 导出与面向总部交换的 `.eipbundle` 是两类产品：前者不能作为可恢复同步协议，后者必须包含版本、签名、加密、增量游标、来源血缘和回执。

### 7.1 分部—总部数据交换

在线和离线共享同一 [EIP Exchange Bundle](11-edge-hub-flywheel.md#6-eip-exchange-bundle)。目标在线接口：

```text
POST /api/v1/exchange/upload-sessions
PUT  /api/v1/exchange/upload-sessions/{session_id}/chunks/{number}
GET  /api/v1/exchange/upload-sessions/{session_id}
POST /api/v1/exchange/upload-sessions/{session_id}:complete
GET  /api/v1/exchange/bundles/{bundle_id}/receipt

POST /api/v1/hub/ingress/files
GET  /api/v1/hub/ingress/bundles/{bundle_id}
POST /api/v1/hub/ingress/bundles/{bundle_id}:approve
POST /api/v1/hub/ingress/bundles/{bundle_id}:reject

GET  /api/v1/releases
GET  /api/v1/releases/{stable_id}/versions/{version}/download
POST /api/v1/releases/{stable_id}/versions/{version}:activate
```

Upload Session 只移动密封 Chunk，不直接创建问卷、事实或报告。`:complete` 在整体哈希正确后生成收件记录，后续隔离、契约、DLP、语义和提交状态通过查询与签名 Receipt 返回。

文件导入和在线上传必须调用同一个 Bundle Validator 与 Import Application Port；不得为离线流程复制一套领域导入逻辑。

## 8. Agent 运行接口

创建 Agent 运行时，调用方提交任务意图和输入 Artifact 引用，不直接提交任意系统 Prompt：

```json
{
  "agent_id": "urn:eip:agent:diagnosis:root-cause",
  "agent_version": "1.4.0",
  "workspace_id": "…",
  "objective": "分析交付延期的主要根因",
  "input_artifact_ids": ["…"],
  "budget": {
    "max_tokens": 12000,
    "max_tool_calls": 8,
    "max_duration_seconds": 180
  },
  "required_review": true
}
```

响应只确认接受并返回 `run_id`。结果通过查询、SSE 或事件获取。Agent 产生的内容状态为 Proposal，不因 API 调用成功自动成为 Finding。

## 9. 事件信封

跨模块事件使用统一不可变信封：

```json
{
  "event_id": "…",
  "event_type": "eip.proposal.created",
  "schema_version": "1.0",
  "occurred_at": "2026-08-23T10:00:00Z",
  "tenant_id": "…",
  "workspace_id": "…",
  "aggregate_type": "proposal",
  "aggregate_id": "…",
  "aggregate_revision": 3,
  "correlation_id": "…",
  "causation_id": "…",
  "actor": {"type": "agent", "id": "…"},
  "classification": "confidential",
  "payload": {},
  "payload_hash": "sha256:…"
}
```

事件表示已经发生的事实，使用过去式；命令不能伪装成事件。

## 10. 核心事件目录

```text
eip.source.registered
eip.source.snapshot.created
eip.evidence.ingested
eip.schema.drift.detected
eip.mapping.proposed
eip.mapping.approved
eip.entity.link.proposed
eip.conflict.detected
eip.conflict.resolved
eip.agent_run.started
eip.agent_run.completed
eip.agent_run.failed
eip.proposal.created
eip.proposal.approved
eip.proposal.rejected
eip.finding.published
eip.finding.retracted
eip.extension.published
eip.extension.withdrawn
eip.registry.contribution.accepted
eip.exchange.bundle.planned
eip.exchange.bundle.sealed
eip.exchange.bundle.uploaded
eip.exchange.bundle.received
eip.exchange.bundle.quarantined
eip.exchange.bundle.rejected
eip.exchange.bundle.committed
eip.exchange.receipt.issued
eip.exchange.sequence.gap_detected
eip.hub.release.published
eip.edge.release.activated
```

目录记录事件负责人、模式、敏感级别、保留期和消费者。新事件必须先注册，不能临时拼接类型名。

## 11. 投递语义

第一阶段采用事务 Outbox：领域写入与 Outbox 同事务提交，后台发布器至少一次投递。消费者必须幂等：

- 用 `event_id` 去重；
- 检查聚合修订号；
- 成功后保存消费游标；
- 可重放且不产生重复正式 Artifact；
- 多次失败进入 Dead Letter Queue；
- 人工修复后从明确位置重放。

平台不宣称网络级“恰好一次”。业务上的恰好一次由幂等命令、唯一约束和状态机共同实现。

## 12. SSE、Webhook 与实时进度

前端运行进度使用 SSE：

```text
GET /api/v1/agent-runs/{run_id}/events
```

SSE 只提供授权用户可见的状态和已脱敏摘要。断线后通过 `Last-Event-ID` 恢复。

Webhook 仅发送允许外发的事件，要求：

- 目标 URL 预注册；
- HMAC 或非对称签名；
- 时间戳和重放窗口；
- 指数退避与最大重试；
- 投递日志和手动重放；
- Payload 最小化，敏感内容使用短期授权回取。

## 13. 契约演进

- HTTP 主版本放在路径中；
- 事件模式使用独立 `schema_version`；
- 同一主版本只能增加可选字段或新资源；
- 删除/重命名/改变语义需要新主版本；
- 消费者必须忽略已声明可扩展位置中的未知字段；
- 生产者不得悄悄改变枚举语义；
- 旧版至少保留一个迁移窗口，并发布弃用时间；
- CI 使用消费者契约和历史样例做兼容性测试。

OpenAPI、事件 JSON Schema 和 SDK 类型均由公共契约生成，不能手工维护多个互相漂移的版本。

## 14. 健康检查

- `GET /health`：进程存活，不访问依赖；
- `GET /ready`：关键依赖可用且迁移状态兼容；
- `GET /metrics`：仅在受控运维网络开放；
- 业务依赖降级必须反映在 readiness 或能力状态中，不能返回虚假的全绿。
