# 行业共享飞轮（历史版本）

> 本文记录早期交换导向的飞轮。现行飞轮以单企业“观察—建模—决策—行动—结果—学习”为核心，再将可复用模式用于相似企业；权威方案见[管理层企业决策投影总体规划](13-executive-decision-platform.md)和[本体、因果与 Agent 技术蓝图](15-ontology-causal-agent-blueprint.md)。

## 1. 原则

平台区分两层飞轮：

1. **总部企业飞轮**：同一 Tenant 内接收各分部显式导出的原始或派生数据，在总部进行统一语义、Agent 分析、评测和知识发布；
2. **跨企业行业飞轮**：只共享经过独立治理的资产，不共享企业 Workspace。

分部到总部是企业集团内部传输，不等于行业贡献。企业私有数据进入跨企业共享层必须另有用途授权、脱敏、人工审核、评测和可撤回血缘。总部飞轮的完整拓扑见[分部边缘终端—总部数据飞轮总体架构](11-edge-hub-flywheel.md)。

## 2. 四类共享通道

### 调研问题

`QuestionAssetVersion` 保存问题文本、目标信息槽位、本体概念、适用行业/规模/成熟度、期待证据、追问策略、敏感级别、失败边界及信息增益指标。共享问题策略，不共享企业回答。

### 分析知识

共享 FindingPattern、RiskRule、EvidenceRequirement、CounterExample、RecommendationPattern、DiagnosisRubric 和 ReportTemplate。Summary Agent 用本企业批准 Fact 匹配模式，不能检索其他企业原始报告。

### 程序

共享 Parser/OCR、Mapping、Value/Unit Normalizer、Entity Resolution、Data Quality Validator、Scoring、Privacy 和 Renderer。ProgramVersion 必须包含契约、权限、资源限制、兼容范围、测试、哈希、签名和许可。

### 数据

| 级别 | 内容 | 默认策略 |
|---|---|---|
| L0 | 原始问答、附件、实体、事实、私有向量 | 不离开企业 |
| L1 | 去标识的企业派生记录 | 明确授权、匿名阈值、重识别测试和人工批准 |
| L2 | 满足最小群体阈值的聚合模式和指标 | 授权、DLP、罕见组合检查和人工批准 |
| L3 | 通用知识、程序、合成数据和公共标准 | 标注来源、签名和人工发布 |

Embedding 和摘要仍是私有派生数据，不能自动视为匿名。

L0 “不离开企业”并不禁止它在企业内部由分部进入总部；是否允许由 `distribution_scope=organization_hq` 和企业政策决定。L0 仍不得因此进入 Consortium/Public Registry。敏感度、加工度和分发范围是三个独立维度，不得只靠 L0–L3 推导权限。

## 2.1 总部飞轮与行业飞轮的交界

总部先完成 Raw → Standardized → Fact/Conflict → Agent/Review → Knowledge Asset。只有明确标记为行业贡献候选的知识资产，才进入本文第 3 节的贡献晋升管线。

总部可以向本企业分部发布包含私有 Ontology、Mapping 和 QuestionPack 的 `organization` 级 Release；这不需要行业发布审批，但仍需要版本、签名、评测和撤回。跨企业发布必须使用独立 Registry、独立权限域和独立审批记录。

## 3. 贡献晋升管线

```text
Approved Tenant Artifact
→ Contribution Candidate
→ Purpose/License Check
→ DLP and De-identification
→ Rare Combination/Re-identification Test
→ Human Privacy Review
→ Independent Eval
→ Shadow/Canary
→ Signed AssetVersion
```

发布后发现问题时标记 `retracted`、停止新安装、通知安装方、按血缘撤回派生资产并发布补偿版本；不删除审计历史。

## 4. Industry Asset Registry

核心对象：AssetDefinition、AssetVersion、AssetDependency、ContributionCandidate、PublicationReview、PrivacyAssessment、LicenseGrant、Installation、UsageMetric、DatasetRelease 和 EvalResult。

可见范围：`private`、`organization`、`consortium`、`public`。

## 5. Diagnostic Playbook

```text
DiagnosticPlaybookVersion
├─ questionnaire_versions
├─ ontology_modules
├─ mapping_program_versions
├─ agent_recipe_versions
├─ diagnosis_rubrics
├─ retrieval_profiles
├─ report_templates
├─ privacy_policies
├─ evaluation_suites
├─ dependencies
├─ compatibility
└─ content_hash/signature
```

发布后不可变，安装生成锁定全部传递依赖的 lockfile。

继承：

```text
core@2
└─ manufacturing@4
   └─ auto-parts@1
      └─ company-overlay
```

公司 Overlay 默认不回传共享库。

## 6. 联邦评测

```text
Industry Hub 发布候选 Playbook
→ 企业本地 Shadow Eval
→ 原始输入输出留在本地
→ 只回传授权的聚合质量/成本指标
→ Hub 汇总跨企业提升
```

可回传新旧版本质量差、引用准确率、人工修改率、追问有效率、Program 成功率、成本和时延。

在单一企业集团内部，总部可以集中运行完整评测；只有跨企业评测才采用上述联邦模式，并继续让各企业原始输入输出留在自己的边界内。

## 7. 反馈等级

### Gold

顾问字段级修正并给出证据、专家复核、映射/本体冲突明确解决、整改后指标验证。

### Silver

追问补齐已验证事实、多项目稳定接受同一映射、只修改措辞而语义目标不变。

### Bronze

单次点击、停留、复制、重新生成、默认选项。只能做产品分析，不能当知识真值。

## 8. FeedbackEvent

```text
proposal_id
action: accept/edit/reject/regenerate/defer
before / after
reason_code
evidence_added / evidence_removed
reviewer_role
downstream_outcome
context_snapshot
consent_scope
```

无操作或超时不是负样本；无理由拒绝进入待复核池。

## 9. 飞轮改进顺序

1. 公司私有检索、术语和实体记忆；
2. 检索排序、规则和工作流；
3. Recipe、输出 schema 和问题策略；
4. Ontology 和 Mapping Program；
5. Playbook 组合与路由；
6. 高质量授权数据足够后再考虑微调。

## 10. 指标

- 固定模型下 Flywheel Lift；
- 每问题新增批准 Fact；
- FindingPattern 跨公司通过率；
- Mapping Program 接入时间降低；
- Playbook 安装前后审核时间差；
- 贡献候选通过率；
- 隐私审核拒绝率；
- 资产撤回级联完成时间；
- 跨租户泄漏为零。

## 11. 伪飞轮禁令

禁止“所有企业原始数据进入全局向量库、Agent 输出再写回、用点击接受当正确标签”的污染循环。
