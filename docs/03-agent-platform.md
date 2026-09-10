# 多 Agent 平台（历史版本）

> 本文记录旧的“Agent 只生成提案”架构。新的 Agent 必须本体感知、通过工具运行、持久化过程，并按授权等级创建关系、分析、行动和结果；权威方案见[本体、因果与 Agent 技术蓝图](15-ontology-causal-agent-blueprint.md)。

## 1. 设计目标

多 Agent 平台把复杂诊断拆成可观察、可评测、可预算、可回滚的专业步骤，而不是模拟多个角色自由讨论。

## 2. 运行架构

```text
API / Application Service
→ Workflow Orchestrator
→ Durable Run/Task Store
→ Agent Runtime
→ Tool & Knowledge Gateway
→ Artifact Store
→ Deterministic Validators
→ Human Review
→ Approved Projector
```

Orchestrator 负责状态、路由、预算和停止条件，不负责生成业务结论。

## 3. Agent 目录

### 数据与语义

- Schema Agent
- Profiling Agent
- Mapping Agent
- Entity Resolution Agent
- Data Quality Agent
- Conflict Agent
- Ontology Agent

### 调研与诊断

- Evidence Agent
- Fact Agent
- Coverage Agent
- Follow-up Agent
- Consistency Agent
- Summary/Diagnosis Agent
- Critic Agent
- Report Agent
- Interpretation Agent

### 飞轮与整改

- Remediation Agent
- Curator Agent
- Privacy Agent
- Evaluation Agent
- Release Assistant

## 4. 权限模型

每个 Agent Recipe 声明：

- 可读取的 Artifact 类型；
- 可用工具白名单；
- 可见租户与敏感级别；
- 输出 Artifact 类型；
- 最大步骤、调用、token、费用和时间；
- 是否需要人工门禁；
- 可用 ModelProfile。

Agent 默认没有任意 SQL、任意文件系统、任意网络、正式表写权限、本体 commit 权限和任意代码执行权限。

## 5. Artifact Blackboard

Agent 不共享可写聊天历史，只通过不可变 Artifact 和边协作。任务消息只携带引用、版本和预算。

```json
{
  "event_type": "agent.task.requested",
  "schema_version": "1.0",
  "run_id": "run_123",
  "task_id": "task_456",
  "workspace_id": "company_a",
  "node": "fact.extract",
  "idempotency_key": "sha256:...",
  "input_refs": ["evidence_17"],
  "versions": {
    "ontology": "manufacturing@6.3",
    "playbook": "auto-parts@2.0",
    "recipe": "fact-agent@1.2"
  },
  "budget": {
    "max_tokens": 3000,
    "deadline_ms": 30000
  }
}
```

## 6. 主要工作流

### 6.1 交互追问

```text
AnswerRecorded
→ Evidence/Fact
→ Coverage Matrix
→ Follow-up Proposal
→ Critic: 必要性/重复/敏感性
→ 顾问选择或编辑
→ Respondent Answer
→ 新一轮 Evidence
```

追问必须包含对应缺口、预期新增信息、已知证据、重复风险、敏感等级和停止建议。

### 6.2 诊断分析

```text
按维度并行 Fact/Finding
→ 每条 Finding 独立 Critic
→ 最多一次自动修订
→ 人工审核
→ 跨维度综合
→ Report
→ 引用/隐私/一致性检查
```

### 6.3 Ontology/Mapping

```text
Approved Facts / Unmapped Terms / Mapping Failures
→ Ontology or Mapping Proposal
→ Deterministic Compatibility Tests
→ Human Diff Review
→ New Version
```

### 6.4 行业贡献

```text
Approved Pattern
→ Contribution Candidate
→ Consent/DLP/De-identification
→ Privacy Agent
→ Eval
→ Human Publication Review
→ AssetVersion
```

## 7. Model Gateway

业务 Agent 不直接调用模型 SDK。ModelProfile 声明 provider、model、结构化输出、tool calling、vision/document、context limit、数据区域/留存策略、certified 状态及费用/时延基线。

自定义 BYOK 模型默认为 `unverified`，只承诺契约和最小安全检查，不承诺诊断质量。

## 8. Credential Lease

- V1 的 BYOK 密钥使用本地主密钥加密后存入 Credential Vault 表；
- API、任务、Artifact 和日志只保存 Credential ID、模型元数据和不可逆指纹；
- 模型调用时才在进程内短暂解密为 lease，结束后不持久化明文；
- 托管生产版应替换为企业 Vault/KMS 引用和短期工作负载身份；
- 密钥永远不是 Agent Prompt、Artifact 或审计内容。

## 9. 幂等、缓存与重试

幂等键：

```text
hash(run + node + input hashes + recipe + model config + ontology + playbook)
```

采用至少一次投递和幂等消费，成功 Artifact 可以复用。

| 故障 | 策略 |
|---|---|
| 网络/429/临时 5xx | 指数退避，限制次数 |
| Schema 不合法 | 一次格式修复，之后备用模型/人工 |
| 证据不足 | 不重试生成，转追问 |
| 权限/隐私失败 | 禁止自动重试 |
| 本体/映射冲突 | 生成 Conflict 并审核 |
| 模型不可用 | 显式规则/抽取式/人工降级 |

## 10. 预算

每个 WorkflowRun 冻结最大步骤、Agent 调用、token、费用、墙钟时间、追问轮数、节点子预算和模型名单。派发前预留预算，达到上限必须停止、降级或转人工。

## 11. Critic 原则

- 独立检索原始证据；
- 不把主 Agent 输出当证据；
- 只输出支持度、矛盾、遗漏和隐私风险；
- 不直接替换正式结果；
- 高风险、低置信或冲突时强制运行；
- 多 Agent 投票不能替代证据。

## 12. Agent Memory

| 类型 | 内容 | 生命周期 |
|---|---|---|
| Working | 当前 Run Artifact | Run 级 |
| Episodic | 同公司历史评估和审核 | 企业私有 |
| Semantic | 批准 Fact、Ontology、行业知识 | 版本化 |
| Procedural | Recipe、Program、Playbook | 不可变版本 |
| Evaluation | Gold、Counterfactual、Incident | 与生产检索隔离 |

自由聊天历史不作为可信共享记忆。

## 13. Agent 评测

- Evidence：定位准确率、抽取覆盖；
- Fact：事实和引用精确率；
- Follow-up：信息增益、重复率、可回答性；
- Summary：事实一致性、覆盖、冲突处理；
- Critic：错误检出率和误报率；
- Ontology：约束、重复概念、兼容性；
- Mapping：字段/值/实体准确率；
- Report：引用覆盖和新增事实率；
- Orchestrator：循环、预算、路由和权限；
- Privacy：泄漏检出与误报。
