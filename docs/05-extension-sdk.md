# 二次开发与扩展 SDK

## 1. 目标

平台允许行业团队、实施伙伴和企业开发者增加接入器、语义映射程序、Agent、工作流配方和诊断 Playbook，同时不依赖核心数据库结构，也不绕过权限、审计、证据和冲突规则。

扩展机制遵守四条原则：

1. 核心只提供稳定协议，不知道具体行业实现；
2. 扩展通过清单声明身份、版本、依赖、输入输出和权限；
3. 运行时授予最小能力，扩展不能直接取得数据库连接或长期密钥；
4. 已发布版本不可原地修改，新行为必须发布新版本并重新评测。

## 2. 扩展类型

| 类型 | 用途 | 典型输出 |
|---|---|---|
| Connector | 读取 ERP、MES、CRM、文档或文件 | SourceRecord、Evidence |
| Profiler | 统计字段分布、缺失率和异常值 | DataProfile |
| Mapping Program | 字段、枚举、单位和口径转换 | MappingProposal、FactProposal |
| Entity Resolver | 候选匹配与合并建议 | EntityLinkProposal |
| Agent | 执行有边界的专业推理任务 | ArtifactProposal |
| Recipe | 编排一组 Agent 和人工门禁 | WorkflowDefinition |
| Playbook | 定义某行业/场景的诊断方法 | QuestionPack、Rule、EvalCase |
| Renderer | 将已审核 Artifact 渲染为视图或报告 | ReportArtifact |
| Policy Pack | 补充租户、行业或地域规则 | PolicyDecision |

扩展之间只能通过版本化契约交换数据，不能调用对方内部类或内部表。

## 3. 稳定身份与命名空间

每个扩展具有稳定标识：

```text
urn:eip:extension:{publisher}:{kind}:{name}
```

示例：

```text
urn:eip:extension:acme:connector:kingdee-k3
urn:eip:extension:manufacturing:playbook:order-to-cash
```

资产自身也使用稳定命名空间。显示名称可以本地化和修改，但 `stable_id` 一经发布不能复用给另一含义。

## 4. 扩展清单

逻辑清单如下；实际可使用 JSON 或 YAML 表达，并由 SDK 解析为同一结构：

```yaml
manifest_version: "1"
stable_id: "urn:eip:extension:manufacturing:program:material-unit-normalizer"
name: "物料单位标准化"
extension_type: "mapping_program"
version: "1.2.0"
publisher: "manufacturing"
entrypoint: "material_units:run"
contract_version: "1.0"
inputs:
  - "urn:eip:contract:source-record:1"
outputs:
  - "urn:eip:contract:mapping-proposal:1"
permissions:
  - "evidence:read"
  - "proposal:write"
dependencies:
  - stable_id: "urn:eip:asset:ontology:core-product"
    version: ">=2.0,<3.0"
network_policy: "deny"
checksum: "sha256:..."
signature: "sigstore:..."
```

禁止把 API Key、数据库密码、企业标识或执行环境绝对路径写入清单。

## 5. 核心协议

扩展只依赖 `enterprise_insight.contracts` 中的公共模型：

- `ExtensionManifest`：扩展声明；
- `AgentTask` / `AgentResult`：Agent 调用边界；
- `ArtifactEnvelope`：黑板上的可追溯产物；
- `DomainEvent`：跨模块异步通知；
- `CapabilityGrant`：一次运行可用的能力；
- `VersionSnapshot`：本次运行锁定的模型、知识和程序版本。

任何尚未进入公共契约的 Python 类都视为内部实现，不承诺兼容。

## 6. 生命周期

```text
scaffold
→ local validate
→ contract test
→ security scan
→ fixed-suite evaluation
→ sign
→ publish candidate
→ human approval
→ active
→ deprecated
→ withdrawn
```

- `candidate` 版本不可用于无提示的生产默认路径；
- `deprecated` 仍可被已有锁文件解析，但新项目收到迁移警告；
- `withdrawn` 不再用于新运行，已有运行记录仍保留其摘要和版本；
- 发现安全问题时可发布撤回声明，不能删除历史审计事实。

## 7. 版本解析与锁定

Workspace 只声明期望范围，运行开始时解析并生成不可变 `ExtensionLock`：

```text
stable_id + exact_version + content_hash + dependency_hashes
```

同一次运行不得浮动升级。升级必须生成新快照并重新执行受影响的评测或工作流。依赖冲突必须在运行前显式失败，不能通过“最后加载者覆盖”解决。

兼容规则：

- 契约主版本变化表示破坏性变更；
- 增加可选字段属于向后兼容；
- 删除字段、改变含义或收紧枚举必须提升主版本；
- 扩展版本遵守 SemVer；
- 数据库迁移版本与 API/契约版本相互独立。

## 8. 权限与隔离

扩展不持有用户身份，只获得单次 `CapabilityGrant`。能力按资源、操作、Workspace、用途和过期时间约束。

默认策略：

- 无网络访问；
- 无宿主文件系统写权限；
- 无数据库连接；
- 无长期凭据；
- 只可读取任务显式绑定的 Artifact；
- 只可写入 Proposal 区；
- CPU、内存、时间、token 和输出大小均有限额。

当前 V1 只验证签名、登记版本、记录启停/撤回，并生成声明式 SandboxPolicy；API 进程不导入或执行扩展包。未来执行器必须位于隔离 worker 或容器中，隔离形态可变化，但能力协议保持不变。

## 9. 开发者工作流

SDK 最终提供以下命令语义：

```text
eip extension init
eip extension validate
eip extension test
eip extension eval
eip extension pack
eip extension sign
eip extension publish
```

脚手架必须自动生成：

- 扩展清单；
- 输入输出模型示例；
- 契约测试；
- 固定评测样例；
- 权限说明；
- 变更日志；
- 发布前检查清单。

## 10. 测试与发布门禁

每个扩展至少通过：

1. 清单模式校验；
2. 稳定 ID 和版本唯一性检查；
3. 输入输出契约测试；
4. 确定性重放测试；
5. 权限越界测试；
6. 敏感数据泄漏测试；
7. 固定 Eval Suite 回归；
8. 依赖解析与撤回模拟；
9. SBOM、摘要和签名验证；
10. 人工发布审核。

## 11. 二次开发边界

允许扩展：

- 行业本体和问卷；
- 诊断规则和评测集；
- 数据源接入和转换；
- 新的专业 Agent；
- 工作流节点和报告渲染器；
- 私有部署策略包。

必须通过 ADR 修改核心的情况：

- 改变 Evidence、Fact、Finding 或 Conflict 的语义；
- 允许绕过审核直接写正式知识；
- 修改租户隔离边界；
- 修改事件投递语义；
- 修改扩展签名或权限模型；
- 引入新的持久化真值源。
