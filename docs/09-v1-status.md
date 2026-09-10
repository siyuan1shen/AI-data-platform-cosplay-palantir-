# V1 实现状态（旧原型盘点）

> 本文用于说明现有代码里已经有什么，不再决定产品方向。现行定位与下一建设顺序见[管理层企业决策投影总体规划](13-executive-decision-platform.md)和[集中交付路线图](16-delivery-roadmap.md)。

本文区分“代码已实现并有测试覆盖的 V1 能力”和“仍属于后续生产化的目标”，避免把设计文档中的远景误报为成品。

## 1. 当前里程碑

| 领域 | 当前状态 | 已验证范围 |
|---|---|---|
| 工程与契约基线 | V1 COMPLETE | 独立仓库、严格契约、CI、静态迁移 |
| 可信内核 | V1 COMPLETE | Tenant/Workspace、Evidence、Artifact、独立审核、审计、Outbox |
| 调研工作台 | V1 COMPLETE | 问卷版本、调研、答卷修订、追问候选、合并导出 |
| 语义集成 | V1 COMPLETE | 实体、标识、别名、映射版本、事实、冲突、漂移 |
| 受控多 Agent | V1 COMPLETE | 五种固定配方、BYOK 网关、预算、提案输出 |
| 评测 | V1 COMPLETE | 合成样例哈希、固定基线、九类指标回归门禁 |
| 报告与整改 | V1 COMPLETE | Finding、Recommendation、Action、Report、Review、影响传播 |
| 扩展注册中心 | V1 COMPLETE | Ed25519 信任、签名注册、可见性、启停、撤回、策略生成 |
| 飞轮治理内核 | V1 COMPLETE | 默认拒绝、授权、DLP、人审、发布、撤回、反馈 |
| Edge—Hub 数据交换 | V1 COMPLETE | EIP-XP/1、Ed25519/X25519、离线包、在线续传、序列追平、多版本回执、发布回流 |
| 总部数据飞轮 | V1 FOUNDATION | 跨分部 Raw Landing、质量/调研/语义候选、人审发布；自动标准化和集团 Fact 编排仍待生产化 |
| 管理台与运维 | V1 COMPLETE | 中文 Web UI、交换中心、Windows 多实例脚本、Compose、备份、健康检查 |
| 企业级生产化 | NOT COMPLETE | OIDC、RLS、对象存储、隔离 Worker、HA、容量与合规 |

“V1 COMPLETE”表示该边界内已有可运行代码、持久化或明确的纯领域实现及自动化测试，不表示远景路线全部完成。

## 2. 可信与安全边界

- 首次启动无默认密码；Bootstrap 成功后不能重复执行；
- Tenant 从签名访问令牌解析，业务请求体不能伪造租户；
- Evidence 原文和模型密钥使用 AES-256-GCM，密文绑定用途上下文；
- 密码使用带随机盐的 scrypt；
- 凭据只写不回显，模型异常不会回传供应商可能包含的秘密文本；
- 高可信 Artifact 和 Report 不允许作者自审；
- Agent 只读取当前 Workspace 的已批准 Artifact，输出为 Proposal；
- 扩展包验证签名后仅登记，不在 API 进程执行；
- 行业贡献默认为 L0/原始/敏感，必须显式授权、DLP 清除和独立人审；
- 企业数据、主密钥、`.env`、数据库和备份均被 Git 忽略。
- `edge_local` 只允许回环监听，并通过隐藏 Local Owner 建立审计上下文，无需重复设置平台账号密码；
- 交换包不包含用户、密码、凭据、API Key、访问令牌或设备私钥；包内容端到端加密并由发送设备签名；
- 总部按登记公钥把发送设备绑定到 Tenant/OrganizationUnit，客户端声明不能改写权威租户边界。

## 3. 数据与语义闭环

```text
Question / System Record / Document
→ encrypted Evidence
→ immutable Artifact proposal
→ independent Review
→ approved Artifact / Fact
→ Canonical Entity + Mapping + Conflict
→ controlled Agent proposal
→ Finding → Recommendation → Action
→ reviewed Report
→ evidence revocation impact / feedback
```

实体解析优先采用外部确定性标识。别名和模糊相似度只产生候选，不能直接合并。字段、枚举、单位和公式映射按不可变版本发布；冲突保留双方事实和解决记录。

## 4. 调研和答案导出

- QuestionPack 按稳定 ID 和 SemVer 发布；
- Assessment 固定引用一个问题包版本；
- Respondent 和 Answer 保留来源；
- Answer 修改产生新 revision，旧记录不覆盖；
- 导出过滤空回答；
- 按租户、公司、问卷、问题合并当前有效回答；
- 相同内容合并来源数，不同内容保留为冲突变体；
- 支持 JSON 行视图和 CSV 下载。

## 5. Agent 与评测

固定配方包括 Follow-up、Summary、Ontology、Diagnosis 和 Privacy。每个配方由预定义阶段组成，不能让模型动态增加工具或无限循环。运行保存配方版本、模型、输入 Artifact、阶段输出、Token 使用、警告和错误。

模型网关支持 OpenAI Responses API、DeepSeek/OpenAI-compatible Chat Completions 和确定性 Mock。OpenAI 请求关闭服务端存储，结构化输出在本地再次通过 JSON Schema 验证。

评测只接受合成样例的内容哈希和聚合计数，不保存模型原始输出。固定基线不会自动滚动，候选按正确性、引用覆盖、越界推断、冲突检出、隐私泄漏、Schema 合规、时延、Token 和成本比较。

## 6. 扩展与飞轮治理内核

扩展注册中心实现发布者公钥信任、签名内容校验、平台版本兼容、权限声明、租户私有/行业共享可见性、启停事件和撤回墓碑。Credential 只能通过 `credential://vault/<uuid>` 引用，并再次检查租户和启用状态。

行业资产领域内核支持 QuestionPack、Ontology、MappingProgram、AgentRecipe、Playbook 和 EvalSuite。发布要求用途授权、可共享级别、DLP、匿名阈值（适用时）和独立人工批准；发布版本不可覆盖，撤回保留历史。分部—总部交换已具备独立安装身份、线下包和在线传输；跨企业 Industry Registry 仍是不同边界，不能因总部汇聚而自动共享企业原始数据。

## 7. Edge—Hub 交换闭环

- 四种部署档位：`edge_local`、`edge_team`、`hq_hub`、`all_in_one_dev`；
- 每个安装生成加密保存的 Ed25519 签名密钥和 X25519 接收密钥，只交换自签名公钥登记文件；
- 分部显式选择工作区、快照/增量和证据正文策略，生成确定性 ZIP64 外层、AES-GCM 分片和 HPKE 封装数据密钥；
- 总部先验签、校验目标身份、全包哈希、路径和大小，再进入不可变 Raw Landing；
- 同一来源修订按稳定身份幂等，内容冲突拒绝静默覆盖；后序包先到时进入等待区，补齐前序后自动连续提交；
- “等待”与“已接收”回执是不可变多版本记录，分部只推进连续确认水位；
- 在线会话支持乱序分片、逐片哈希、整体哈希、加密保存上传令牌和失败后只补传缺失分片；
- 总部将导入结果生成数据质量、调研和语义候选；只有人工勾选的泛化候选才进入面向指定分部的签名发布包；
- 两个以上分部出现相同“来源系统 + 对象类型 + 强外部标识”但本地实体名称不同时，总部生成不可静默合并的 `identity_resolution` 候选；候选保留各分部实体身份和名称，隐藏原外部标识值并要求人工裁决；
- 分部导入发布包后状态为 `received`，不会执行脚本或自动激活，必须由本机授权人员显式启用；应用到某个工作区时，平台以“总部签名服务为作者、本机用户为审核人”生成已审核数据 Artifact。Agent 不会自动读取这些资产，仍需用户在一次具体运行中手动选择，避免把总部载荷未经目的授权发送给外部模型。

## 8. 可靠性与运行

- SQLAlchemy Unit of Work；
- 固定 Alembic V1 基线，升级/降级/再升级和模型一致性检查；
- Outbox 租约、原子状态转换、退避、死信和安全重放；
- 幂等记录按 Tenant + Operation + Key 隔离；
- SQLite 保存点参与真实外层事务；
- `/health` 不访问依赖，`/ready` 检查数据库；
- Windows 本地启动、停止、诊断、备份脚本；
- Compose 生产模式、非 root、只读根文件系统、最小权限和一次性密钥初始化。

## 9. 明确缺口

- 本地账号体系不是 OIDC/SSO；
- 默认 SQLite 尚未提供 PostgreSQL RLS 和 HA；
- Evidence 使用数据库加密 Blob，尚未接对象存储和恶意文件扫描；
- Agent 同步执行，尚未有独立队列 Worker、SSE 和暂停恢复；
- 扩展只有注册和策略层，尚无第三方代码隔离执行器；
- `edge_team` 和 `hq_hub` 当前仍使用本地账号，尚未接 OIDC/SSO/MFA；
- Exchange Quarantine 已有格式、签名、大小和路径防护，但尚未接企业恶意文件扫描器；
- Correction/Tombstone 可被协议识别和总部接收，但管理台尚未提供专门的更正/撤回编排；
- 总部已生成 Raw Landing 和飞轮候选，尚未把所有来源自动编排到跨分部 Standardized、Semantic Core 和 Trusted Fact Serving View；
- 分部发布导入具备签名、Schema 和链校验及人工激活，尚未接独立 Worker 中的自动 Shadow Replay/Eval；
- 在线发送为人工触发并可断点重试，尚未增加定时退避调度器、带宽配额和运维告警；
- 尚无独立跨企业 Industry Registry 服务；企业集团总部的数据默认不会越过企业边界；
- 管理台覆盖核心操作，但还不是完整图谱、价值流和报表设计器。

## 10. 下一步优先级

1. 建立 `ObjectType`、`LinkType`、`ObjectInstance`、`LinkInstance` 和图查询，补上真正的企业关系图；
2. 增加事件、指标和投影版本，让管理层能够查看企业状态及其变化；
3. 把问卷、访谈、现场证据转成候选对象、关系、问题和因果假设，并提供人工确认界面；
4. 完成首个 CSV/业务系统连接器、转换管道和字段级血缘，以订单到回款作为纵向样板；
5. 将 Agent 接入本体、图查询、因果、行动和结果工具，再建设相似企业指导与持久化数据飞轮。
