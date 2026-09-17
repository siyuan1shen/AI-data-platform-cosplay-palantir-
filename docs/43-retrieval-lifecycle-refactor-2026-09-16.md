# 检索与数据生命周期重构记录

## 本轮已落地

### 1. Agent 初始上下文改为有界工作集

- 图节点和关系支持 `entity_limit`、`relation_limit` 服务端上限；默认 Agent 上下文使用配置上限。
- 本体类型、来源系统、语义映射、动作定义、指标定义和设计权衡均使用 SQL `ORDER BY + LIMIT`。
- 超出上限时只保留前 `limit` 条，并在 `retrieval_policy.truncated` 中明确记录；不能把上下文样本当成项目全量。
- 继续扩大范围必须调用已注册的分页/邻域/数据集工具，Agent 无数据库连接和任意 SQL 权限。

### 2. 来源数据按索引和条件下推检索

- `raw_records.payload` 仍是原始权威值。
- `raw_record_values` 只保存可检索的标量索引，不复制业务正文。
- 来源观测按项目、资产、字段、来源记录标识和索引值在 SQL 层筛选，并返回有界结果及 `truncated`。
- 来源身份路由先精确查询，必要时只在有界候选窗口内做兼容匹配，不再把全项目身份加载成 Python 注册表。

### 3. 图查询保持结构化和可追溯

- 企业投影仍用对象、关系、参与者和发布快照表达；没有引入向量库作为事实来源。
- 图上下文按关系类型和邻域缩小；工作集受服务端上限保护。
- 图覆盖、上下文读取策略和截断状态会写入 Agent 运行上下文及控制读集。

### 4. 临时数据生命周期

自动清理范围只有：

- 过期导入预览；
- 超过保留期且未确认的工作观察预览；
- 到期或已消费的恢复临时包；
- 已完成/失败且超过保留期的导出文件。

主动删除必须填写原因，并写入 `lifecycle_deletion_audit`。正式企业模型、统一数据、管理观察、潜在记录和已确认工作观察不进入自动物理删除路径；这些内容使用版本、撤回、退役或独立审阅流程。

生命周期接口：

```text
GET    /api/v3/lifecycle/policies
GET    /api/v3/projects/{project_id}/lifecycle/cleanup/preview
POST   /api/v3/projects/{project_id}/lifecycle/cleanup
DELETE /api/v3/projects/{project_id}/lifecycle/resources/{kind}/{id}
```

`LifecycleWorker` 按周期清理临时资源。单文件部署时正式库和观察逻辑库复用同一 SQLAlchemy Session，避免 SQLite 双写锁争用；分离文件部署仍保持各自提交边界。

## 配置入口

所有变量使用 `EI_BACKEND_` 前缀，具体变量见 `docs/10-operations.md`。重点包括 Agent 初始上下文上限、来源身份候选上限、生命周期清理开关、预览保留天数和导出保留天数。

## 验证记录

- 后端 Ruff：通过。
- Agent、路由、动作、语义数据集、生命周期、查询快照和健康检查重点回归：通过。
- 前端 OpenAPI 类型生成、TypeScript 检查和 Vite 生产构建：通过。
- 运行实例健康检查：HTTP 200，schema revision `c3d4e5f6a7b8`。
- 历史全量测试中仍有按旧版本物理分库/旧迁移模型编写的基线用例；它们反映旧架构预期，不应通过修改产品代码强行掩盖，后续应单独迁移测试夹具。
