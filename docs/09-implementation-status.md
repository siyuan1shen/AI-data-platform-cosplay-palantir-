# 历史实现状态（阶段 0 快照）

> 本文件保留项目最初工程基线的历史记录，不再表示当前状态。当前可运行 V1 的准确范围、测试边界与缺口请见 [V1 实现状态](09-v1-status.md)。

## 1. 当前里程碑

```text
阶段 0：工程与契约基线        COMPLETE
阶段 1：可信内核              IN PROGRESS
阶段 2：调研工作台            NOT STARTED
阶段 3：语义集成              NOT STARTED
阶段 4：受控多 Agent 诊断     NOT STARTED
阶段 5：企业解读产品          NOT STARTED
阶段 6：扩展平台              CONTRACT ONLY
阶段 7：行业共享飞轮          DESIGN ONLY
阶段 8：整改与规模化          DESIGN ONLY
```

## 2. 已实现

### 工程基线

- Python 3.12 `src` 布局和独立包；
- FastAPI Application Factory；
- `/health`、`/ready`、`/api/v1/meta`；
- Pydantic 严格模型、未知字段拒绝和顶层不可变；
- Ruff、mypy strict、pytest 和 GitHub Actions；
- 配置只接受 `EIP_` 前缀，普通配置模型不包含模型密钥。

### 公共契约

- `ArtifactEnvelope` 与 Evidence 引用；
- Agent Task、Budget、Usage、Result 和 Version Snapshot；
- Domain Event 信封；
- Extension Manifest、权限、依赖和 Lock；
- Capability Grant 有效期与网络权限组合校验；
- Problem Details 错误结构。

### 可信内核原型

- Tenant 范围的 Workspace；
- 原文与 Evidence 元数据分离；
- SHA-256 内容摘要；
- Artifact Proposal；
- Fact/Finding 的独立审核要求；
- 批准和拒绝生成新 Artifact ID/版本，原提案不被覆盖；
- Request Changes 保留原提案；
- 审计记录不保存 Evidence 原文；
- 同租户跨 Workspace 引用阻断；
- 跨租户 ID 查询按“不可用”处理，不泄露资源是否存在；
- 线程安全内存适配器，用于领域验证和测试。

## 3. 当前明确不是生产能力

- 内存适配器重启后丢失，不作为生产真值源；
- 尚未实现 SQLAlchemy Repository、Alembic 和事务 Outbox；
- 尚未实现 OIDC/SSO、RBAC/ABAC 和 PostgreSQL RLS；
- 尚未实现 Vault/BYOK 的密钥保存；
- 尚未开放 Workspace/Evidence/Review 业务 HTTP API；
- 尚未实现对象存储加密、保留和删除任务；
- 尚未接入任何外部模型，也没有后台 Agent Worker；
- 尚未实现问卷 UI、语义映射 UI 和企业解读前端；
- 尚未发布任何行业共享资产。

## 4. 下一编码批次

### 4.1 持久化与事务

1. 为可信内核增加 Unit of Work；
2. 建立 SQLAlchemy 表模型；
3. 建立 Alembic 初始迁移；
4. SQLite 用于 Community Local，PostgreSQL 用于集成测试；
5. 加入事务 Outbox 与幂等命令表；
6. 用数据库唯一约束锁定不可变身份与版本。

退出条件：任一业务命令要么完整提交 Evidence/Artifact/Review/Audit/Outbox，要么全部回滚。

### 4.2 身份与业务 API

1. 定义 `RequestContext` 和认证 Port；
2. 添加仅本地可用的开发身份适配器；
3. 添加 Workspace、Evidence、Proposal 和 Review API；
4. 服务端从身份解析 Tenant，忽略客户端伪造值；
5. 支持 Idempotency-Key、ETag 和稳定 Problem Details；
6. 加入跨租户、越权和重复命令 API 测试。

退出条件：业务 HTTP 写入不能绕开 Tenant、领域命令、审核或审计。

### 4.3 调研纵切

1. QuestionPack 与不可变版本；
2. Assessment、Respondent、Question 和 Answer；
3. 条件问题、附件和回答状态；
4. 同公司、同问卷、同问题的来源保留与合并视图；
5. 一键导出全部有效问题和回答，过滤无回答项；
6. 为 Follow-up Agent 留出结构化任务接口。

退出条件：可在新平台完成一次问卷导入、回答合并、证据化和导出，不需要读取外部问卷系统的私有数据库。

## 5. 当前测试覆盖的红线

- 正式 Fact/Finding 没有 Evidence 时模型校验失败；
- Artifact 顶层不能原地修改；
- Conflict 至少关联两个不同 Artifact；
- 网络扩展必须同时声明权限和允许列表；
- Capability Grant 不能永不过期；
- 失败 Agent Result 必须有结构化错误码；
- 工作流终态不能重新执行；
- 高可信 Artifact 不能由作者自行批准；
- 跨租户 Evidence 和原文不能被另一 Tenant 读取；
- 同 Tenant 不同 Workspace 的 Evidence 不能混用；
- 密钥样式原文不会进入 Evidence 元数据或审计记录；
- 审核不会覆盖原提案，而是创建可追溯的新版本。
