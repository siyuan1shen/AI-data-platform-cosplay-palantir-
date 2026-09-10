# 领域模型（历史版本）

> 本文描述当前 V1 的旧领域模型，仅用于理解现有代码。新的对象、关系、事件、指标、行动和投影模型以[本体、因果与 Agent 技术蓝图](15-ontology-causal-agent-blueprint.md)为准。

## 1. 建模目标

领域模型必须保证：受访者陈述不直接成为事实，模型推断不直接成为知识，不同系统的冲突值不被覆盖，所有正式结论都可追溯，所有正式资产均可版本化和回滚。

## 2. 限界上下文

| 上下文 | 所有权 | 不负责 |
|---|---|---|
| Workspace | 企业、项目、成员、策略 | 业务证据和诊断 |
| Assessment | 题库、评估、问题和回答 | 将回答判定为事实 |
| Source & Evidence | 来源、版本、证据片段和血缘 | 诊断推理 |
| Semantics | 本体、实体、映射和标准化 | 报告文案 |
| Knowledge | Fact、Claim、Finding、Recommendation | 原始文件 |
| Conflict | 冲突、决议和可逆合并 | 擅自选择来源 |
| Agent Runtime | Run、Task、Artifact、预算 | 正式审批 |
| Review | 接受、编辑、拒绝及原因 | 生成 AI 内容 |
| Program | 程序、版本、测试和权限 | 行业发布审批 |
| Playbook | 行业资产组合、依赖和安装 | 企业私有事实 |
| Remediation | 整改行动、指标和结果 | 重写历史证据 |
| Registry | 贡献、发布、许可和安装 | 保存企业原始 Workspace |
| Evaluation | 黄金集、评测和发布门禁 | 生产真值维护 |

模块只能通过应用服务、契约或领域事件协作，禁止直接修改其他上下文的数据表。

## 3. 稳定标识和命名空间

长期对象使用稳定 URI，数据库自增 ID 仅作为内部实现：

```text
urn:eip:core:customer
urn:eip:manufacturing:material
urn:eip:company/acme:entity/customer/8f31
urn:eip:artifact/evidence/01J...
```

命名空间层级：

```text
core → industry → subindustry → vendor → organization → company
```

同名显示标签可以存在于不同命名空间，但不能静默共用同一个语义 ID。

## 4. 企业与评估

### Workspace

企业数据与策略边界：`workspace_id`、部署模式、数据区域、默认敏感级别和当前 ReleaseBundle。

### QuestionnaireDefinition / QuestionnaireVersion

定义与版本分离。发布版本不可变，记录内容哈希、问题顺序、目标信息槽位、适用条件、期待证据、敏感级别和关联本体版本。

### Assessment

一次诊断边界。启动时冻结 Questionnaire、Ontology、Playbook、Recipe、ModelProfile 和 Policy 版本。

### Response

保存受访者输入和附件引用。Response 是 Evidence 来源，不是 Approved Fact。

## 5. 来源与证据

### SourceSystem

ERP、MES、CRM、财务、问卷、访谈、API 或文件来源。

### SourceSchemaVersion

来源系统某一时刻的结构快照，包含字段、类型、约束、样例统计和内容哈希。结构变化创建新版本。

### SourceAssetVersion

不可变原始资产版本，例如一份回答、附件、API 抽取批次、表快照或访谈记录。

### EvidenceFragment

精确定位原始资产：文本字符区间、PDF 页码和坐标、Excel 单元格、图片区域、数据库表/主键/字段或问卷回答版本。

Evidence 不可覆盖。新 OCR、Parser 或模型抽取产生新的派生版本。

## 6. 企业语义对象

### CanonicalEntity

公司内稳定统一的客户、物料、订单、设备、组织等实体。

### ExternalIdentifier

将来源系统 ID 关联到统一实体：

```text
namespace = sap-business-partner
external_id = 10086
entity_id = customer:8f31
```

### Alias

某部门、系统或时间范围内的名称。名称相同或相似不能单独证明实体相同。

### EntityMention

Evidence 中出现的实体文字及位置，是实体解析的输入。

### EntityResolutionCandidate / Decision

候选保存匹配特征、程序版本和分项置信度。Decision 记录接受、拒绝、合并、拆分或延期。合并必须可逆，不物理删除原实体。

## 7. Fact、Claim 与 Finding

### FactAssertion

类型化、带时间和证据的原子陈述：

```json
{
  "subject": "urn:eip:company/acme:entity/material/2039",
  "predicate": "urn:eip:manufacturing:inventory_quantity",
  "object_value": 1200,
  "unit": "kg",
  "valid_from": "2026-08-01",
  "evidence_refs": ["urn:eip:artifact/evidence/01J..."],
  "status": "approved"
}
```

必须区分 `false`、`unknown`、`not_provided`、`not_applicable` 和 `conflicted`。新事实使用 `supersedes` 连接旧事实，禁止原地覆盖。

### Claim

对一个或多个 Fact 的解释、假设或判断。记录支持与反驳 Fact，以及规则、Program 和 Recipe 版本。

### Finding

经过审核、可进入正式诊断的发现。包含范围、严重度、影响、根因 Claim、支持/反驳证据、不确定性、审核状态和 as-of 时间。

### Recommendation

与 Finding 关联的建议，不能与 Fact 混为一体。

## 8. 冲突模型

### Conflict

冲突是一等对象：类型、主体、竞争 Artifact、重要性、影响范围、状态和解决策略。

### ResolutionDecision

保存最终决定、审核人、原因、证据、生效时间、补偿操作和被替代决定。未解决冲突不能进入默认 Approved Knowledge 视图。

## 9. Agent Artifact

所有 Agent/Program 输出先进入统一外壳：

```text
artifact_id / artifact_type / schema_version
workspace_id / sensitivity / lifecycle_status
payload / created_by / source_run_id
content_hash / created_at
```

Artifact 边包括：`derived_from`、`supports`、`contradicts`、`supersedes`、`maps_to`、`reviews`、`generated_by`。

稳定、高频查询的正式对象投影到规范化表；Artifact Store 不作为万能 EAV 替代所有领域表。

## 10. 时间与置信度

关键事实采用双时态：

- `valid_from/valid_to`：现实中何时有效；
- `recorded_at/superseded_at`：系统何时知道；
- `observed_at`：证据何时观察；
- `as_of`：报告针对的时间点。

置信度保存分项：抽取、来源可靠度、实体解析、时间、交叉验证和推理。人工批准状态与置信度分开。

## 11. EnterpriseSnapshot

某个 `as_of` 时点批准知识的版本化投影：组织、产品、客户、供应商、流程、系统、数据流、指标、风险、Finding、整改以及未知/冲突摘要。

Interpretation Agent 只能基于 Snapshot、批准知识和必要 Evidence 回答问题。
