# V3 全项目实现审查与修复清单

审查日期：2026-09-05。范围：当前桌面启动器对应的 `backend/`、`frontend/`、接口契约、部署与测试脚本，并对照旧 V2 残留。

## 1. 结论

目前是**具备真实基础能力、但关键业务闭环尚未完成的原型**，不能认定为“全部开发完成”，更不能认定为“无 bug”。

不要继续沿用“没有图、没有 Action、没有 Worker、评估完全不持久化”的旧判断：当前 V3 已有对象/关系图、类型校验、发布快照、Action 审批执行、后台运行、持久化管理分析与评估。这些不是纯计划。主要问题是：组件之间未统一数据语义，若干前端入口或 Agent 工具缺失，一些看起来成功的结果并不代表业务真正完成。

本轮只审查和隔离测试，**没有修改业务实现，也没有读写现有企业数据库**。仅新增审查文档和合成数据诊断脚本。原有 Git 改动保持不动。

### 验证范围与边界

| 检查 | 结果 | 能证明什么 |
| --- | --- | --- |
| V3 后端现有测试 | 23 项通过 | 现有 API 示例和断言成立；不能覆盖全部业务语义 |
| 前端现有测试 | 1 个文件、2 项通过 | 图元素转换的已测情况通过；不是完整页面验收 |
| 前端 TypeScript 检查 | 通过 | 静态类型可编译，不等于前后端业务契约正确 |
| 本轮定向探针 | 22 组异常或能力边界被复现 | 详见第 4 节，全部使用临时合成数据库 |
| 源码审查 | 28 组修复项 | 覆盖建模、导入、Agent、语义对齐、分析、反馈、导出、运行部署 |

没有在本轮调用真实付费模型或实际 ERP/MES，也没有重新做浏览器全流程操作。前端问题中未动态复现的，明确标为源码确认；并发 Worker 问题标为风险，未声称已发生重复执行。系统 Python 具备 pytest；启动用后端虚拟环境没有安装 pytest。首次前端测试受沙箱进程权限阻止，获准重跑后通过；这两项环境现象不计入产品缺陷。

隔离探针与机器可读结果保留在：

- [management_probes.py](../artifacts/audit-2026-09-05/management_probes.py)
- [workflow_probes.py](../artifacts/audit-2026-09-05/workflow_probes.py)
- [kernel_probes.py](../artifacts/audit-2026-09-05/kernel_probes.py)
- [results.json](../artifacts/audit-2026-09-05/results.json)

## 2. 按你的实际工作流程判断完成度

| 用户要做的事 | 已有能力 | 实际断点 |
| --- | --- | --- |
| 创建公司、项目，配置模型 | 公司/项目 API 与页面；模型配置 API | 新用户没有模型配置创建/测试的前端入口 |
| 提供访谈、问卷、报告 | CSV / Companycheck CSV 预览确认、片段与陈述存储 | 前端列出的 Word、PDF、Excel、TXT、JSON 不在当前导入实现中 |
| 让 Agent 初步建立组织、职责、权限和流程图 | 三类 Agent 的提示词、上下文组装、结构化动作提案；真实图数据库表 | 单次模型请求，不是工具执行结果驱动的循环；修改、建变更集、指标填充等能力未接通 |
| 人工查看、微调企业投影 | Cytoscape 图展示；对象与关系基本编辑/退役 | 编辑会清空证据；多参与方关系编辑退化为二元关系；部分类型无编辑入口 |
| 接入系统，让企业数据对齐 | 文件数据源、字段转换、实体物化、部分血缘 | 真实连接器缺失；优先级未生效；设计值被覆盖；关系/事件/指标未形成统一摄取流程 |
| 管理层探索结构风险、对照现实结果 | 假设、因果假设、会议、取舍、指标与分析结果持久化 | 两套指标模型脱节；时期比较不可靠；多对多印证退化为顺序匹配；部分信息只能看不能维护 |
| 管理层确认或否定，再持续使用 | 洞察状态/反馈、执行观察、可复用案例 | 下一轮分析不继承已确认/已否定反馈；案例参考有入口，但结果到案例的连续工作流未接通 |
| 发布、查询、导出、换机使用 | 图发布版本；JSON/CSV/XLSX/BUNDLE 导出 | 管理图与管理 Agent 使用版本不同；导出不是可恢复备份；旧部署和备份脚本仍指向 V2 |

产品方向可以继续沿用：前端保持简洁，主要让 Agent 工作；但必须把原子业务能力、校验、回读、修改和结果状态接通。不能通过再增加大量菜单来替代 Agent 能力的完成。

## 3. 28 组问题、证据和验收要求

优先级含义：P1 = 阻断主要工作流，或会污染模型/误导管理判断；P2 = 重要完整性、运行可靠性或维护问题。本文没有把未证明的风险标成线上故障。

### F01 · P1：新安装缺模型配置入口，系统本体 Agent 没有可达入口

**源码确认。** 前端只列出既有模型配置；后端没有配置时返回 `MODEL_PROFILE_REQUIRED`。`SYSTEM_ONTOLOGY` 虽出现在类型与标签中，页面实际只实例化投影 Agent 和管理 Agent。

证据：[api/index.ts:175](../frontend/src/api/index.ts#L175)、[AgentWorkspace.tsx:117](../frontend/src/features/agents/AgentWorkspace.tsx#L117)、[App.tsx:20](../frontend/src/app/App.tsx#L20)、[agent_runtime.py:177](../backend/src/enterprise_insight_backend/agent_runtime.py#L177)。

修复：增加一个轻量模型配置弹窗，支持创建、测试、默认选择；在数据接入场景连接现有系统本体 Agent，不另造大页面。验收：全新空实例，不调用 Swagger、不改数据库，能配置自己的模型并完成首次对话及语义对齐对话。

### F02 · P1：导入页面承诺的格式与后端不一致，映射输入也存在脱节

**已复现 + 源码确认。** `TXT` 导入返回 422 `IMPORT_KIND_NOT_SUPPORTED`；实际预览只支持 `CSV` / `COMPANYCHECK_CSV`。前端列出 XLSX、DOCX、PDF、TXT、JSON。系统文件界面提交的映射不等于已存的语义映射，且确认时没有明确传 `source_asset`，后端默认取文件名；“orders”规则可能匹配不到“orders.csv”。转换表达式提示 `trim(value)` 也不符合现有 DSL。

证据：[ImportWorkbench.tsx:8](../frontend/src/features/imports/ImportWorkbench.tsx#L8)、[SourceDataPanel.tsx:63](../frontend/src/features/imports/SourceDataPanel.tsx#L63)、[evidence.py:73](../backend/src/enterprise_insight_backend/evidence.py#L73)、[evidence.py:202](../backend/src/enterprise_insight_backend/evidence.py#L202)、[evidence.py:371](../backend/src/enterprise_insight_backend/evidence.py#L371)。

修复：由后端能力清单驱动文件选项；为访谈报告补文档解析器，扫描 PDF 明确提示需 OCR；把问卷列映射、源资产选择、语义映射分别定义清楚。验收：各种允许选择的文件都能完成预览—确认—材料可见—Agent 可引用；不支持的格式在上传前解释原因。

### F03 · P1：Agent 有 Action 提案，但没有完成用户任务的工具闭环

**源码确认。** 当前是一轮上下文拼装、一轮模型请求、保存提案，然后标记完成。投影 Agent 只允许创建对象、创建关系和应用已有变更集，不能创建变更集、更新/退役对象、创建指标或填写观测。系统 Agent 只允许创建语义映射。新对象的真实 ID 要执行后才有，缺少工具回读循环使连续建对象再连关系尤其困难。

证据：[agent_runtime.py:56](../backend/src/enterprise_insight_backend/agent_runtime.py#L56)、[agent_runtime.py:100](../backend/src/enterprise_insight_backend/agent_runtime.py#L100)、[agent_runtime.py:585](../backend/src/enterprise_insight_backend/agent_runtime.py#L585)。另外只处理前 20 个动作提案，较大模型可能不完整。

修复：补齐现有服务的原子工具、结果回读、引用解析、可恢复多轮执行与统一批量变更集；把“生成提案完成”和“用户任务完成”分开。保留人工批准，不等于去掉写入校验。验收：一个建模请求经一次批量确认可生成互相关联的对象；再说“把某岗位改属另一部门”，能生成准确差异并执行、回读和展示结果。

### F04 · P1：所选材料可能没有送给 Agent，上下文开关未真正生效

**已复现 + 源码确认。** 导入 45 个片段后再选择新材料，manifest 记录了附件，但模型上下文只有前 40 个片段，缺少所选片段；`include_unconfirmed_material=false` 时仍含 CANDIDATE 陈述。整个上下文序列化后又按字符硬截断，可能截掉动作工具、案例或对话，并破坏 JSON 完整性。

证据：[collaboration.py:158](../backend/src/enterprise_insight_backend/collaboration.py#L158)、[agent_runtime.py:214](../backend/src/enterprise_insight_backend/agent_runtime.py#L214)、[agent_runtime.py:258](../backend/src/enterprise_insight_backend/agent_runtime.py#L258)、[agent_runtime.py:523](../backend/src/enterprise_insight_backend/agent_runtime.py#L523)。

修复：以 run manifest 为唯一上下文规范；选中材料优先、按状态筛选、按对象检索与分块分页、按预算保留完整结构。验收：大量历史材料不挤掉本轮附件；开关影响实际模型输入；可回看每次真正使用的材料和版本。

### F05 · P1：Action 预演不能作为“可执行”的可靠判断

**已复现 + 源码确认。** 创建不存在的本体类型，预演返回 `valid=true`，批准后执行失败 `ONTOLOGY_TYPE_NOT_FOUND`。预演对部分动作仅校验请求形状。部分 handler 返回 `valid=false` 后，状态机仍进入待批准/预演完成；连接器动作实际不能执行。

证据：[actions.py:408](../backend/src/enterprise_insight_backend/actions.py#L408)、[actions.py:782](../backend/src/enterprise_insight_backend/actions.py#L782)、[actions.py:825](../backend/src/enterprise_insight_backend/actions.py#L825)、[actions.py:862](../backend/src/enterprise_insight_backend/actions.py#L862)、[actions.py:937](../backend/src/enterprise_insight_backend/actions.py#L937)。回滚也只覆盖创建对象/关系等有限 handler，并非所有成功动作。

修复：预演与执行共用领域校验；预演失败不能批准执行；批准绑定输入、定义版本及基线修订；按 handler 声明是否可执行、是否可补偿。验收：无效类型/关系/依赖在批准前失败；执行前基线变化必须重验；不支持回滚的动作不展示可用回滚。

### F06 · P1：人工编辑可能丢失证据，多方关系编辑可能丢参与方

**源码确认。** 表单保存对象、关系时固定构造 `evidence: []`，更新 API 会覆盖原证据。关系表单只构造两个参与方，因此对后端支持的多参与方关系，编辑可能丢角色或触发校验失败。

证据：[ProjectionModelPage.tsx:45](../frontend/src/pages/developer/ProjectionModelPage.tsx#L45)、[ProjectionModelPage.tsx:60](../frontend/src/pages/developer/ProjectionModelPage.tsx#L60)、[ProjectionModelPage.tsx:71](../frontend/src/pages/developer/ProjectionModelPage.tsx#L71)。

修复：PATCH 仅提交用户实际修改的字段；保持原证据与所有参与方，提供有意识的增删操作。验收：只改名称后证据 ID、其他属性、多方关系角色和参与者都保持不变。

### F07 · P1：管理图、管理 Agent、导出包不共享同一版本基线

**已复现 + 源码确认。** 发布后修改草稿：管理图显示旧发布名称，管理 Agent 上下文却包含未发布的新名称。指定 release 的导出只有图来自发布快照，本体、事件、指标等仍读当前表，生成混合版本数据。

证据：[agent_runtime.py:254](../backend/src/enterprise_insight_backend/agent_runtime.py#L254)、[exporting.py:116](../backend/src/enterprise_insight_backend/exporting.py#L116)、[exporting.py:131](../backend/src/enterprise_insight_backend/exporting.py#L131)。

修复：统一 `project_id + release_id / draft_revision + as_of` 上下文；正式管理查询默认发布基线，草稿预览必须显式选择。验收：同一基线下页面、Agent、分析和导出引用相同版本；发布后编辑草稿不改变原发布查询结果。

### F08 · P1：真实 ERP/MES 等接入仍只是能力边界声明

**源码确认。** FILE 数据源可用；非 FILE 连接测试返回 `CONNECTOR_REQUIRED`。没有实际 ERP/MES/数据库/REST 读取适配器、断点同步、增量水位和任务调度。现有文件物化主要写实体，尚未把外键关系、事件及指标观测贯通。

证据：[integration.py:49](../backend/src/enterprise_insight_backend/integration.py#L49)、[evidence.py:357](../backend/src/enterprise_insight_backend/evidence.py#L357)。

修复：先完整做好 FILE 和一个只读连接器，接口提供能力探测、分页、增量、失败重试；摄取目标覆盖实体、关系、事件、指标。验收：使用一个合成业务源完成首次导入、增量更新、重跑去重、关联建边与导出核对；没有适配器的系统明确显示未支持。

### F09 · P1：语义对齐退化为同键覆盖，权威优先级和设计基线没有落实

**已复现 + 源码确认。** 同一稳定键的设计对象被系统导入覆盖，视角变为 `SYSTEM_BOUND`；按 priority 1、999、1 的不同来源先后导入，最终总是最后一次值。`authority_priority` 被存储但没有参与合并。身份解析主要靠相同稳定键，缺少跨系统别名/编码映射与冲突决议。

证据：[integration.py:99](../backend/src/enterprise_insight_backend/integration.py#L99)、[evidence.py:364](../backend/src/enterprise_insight_backend/evidence.py#L364)、[evidence.py:391](../backend/src/enterprise_insight_backend/evidence.py#L391)、[evidence.py:427](../backend/src/enterprise_insight_backend/evidence.py#L427)、[evidence.py:470](../backend/src/enterprise_insight_backend/evidence.py#L470)。

修复：统一实体 ID 与来源标识分离；属性保留来源、版本、有效时间、设计值/观测值；显式定义权威顺序和冲突处理。不能把“同名/同 ID”直接当作“同一实体”。验收：相反导入顺序产生同一解析结果；异码同物可对齐、同码异物不误并；系统实况不能静默改掉组织设计基线。

### F10 · P1：先导入后补映射无法重算，前端容易误报成功

**已复现 + 源码确认。** 无映射时上传 CSV 得到 0 对象；补映射后再上传同一文件返回 DUPLICATE，仍为 0 对象。去重按项目和内容哈希，不包含来源、映射版本或物化状态。前端确认提示没有准确区分“仅存证据”“已写实体”和“重复跳过”。

证据：[evidence.py:155](../backend/src/enterprise_insight_backend/evidence.py#L155)、[SourceDataPanel.tsx:65](../frontend/src/features/imports/SourceDataPanel.tsx#L65)。

修复：将原始材料去重与物化任务幂等分离；映射发布后可从已有原始数据重新计算；提供映射更新/停用能力。验收：不必改文件内容即可应用新映射；重复运行不重复建对象/边；结果明确展示新建、更新、跳过、冲突与仅存证据的数量。

### F11 · P1：图里的 KPI 和分析引擎里的 KPI 是两套没有接通的数据

**已复现。** 建立 `strategy_objective → measured_by → metric` 后，分析仍产出 `strategy_without_metric`，因为分析读的是独立 `MetricDefinitionRow`，不识别图里已有 KPI 关系。

证据：[management_intelligence.py:478](../backend/src/enterprise_insight_backend/management_intelligence.py#L478)、[management_intelligence.py:561](../backend/src/enterprise_insight_backend/management_intelligence.py#L561)。

修复：指标应有统一业务身份，图中的战略/岗位/职责关联与时序定义共用身份；通过服务而不是多处各自解释关系。验收：Agent 或人工建出的 KPI，数据导入和管理分析都能识别；任何入口修改后不会形成两份不同的 KPI。

### F12 · P1：指标缺少达标计算与可比较的时间窗口

**已复现。** 目标 95、连续观测 40，如果没有手工标 MISS，不会自动识别未达标；同一个时期录入三次 MISS 被当成反复未达标；把 2022 年局部改善与 2026 年企业结果恶化并列，得到 CRITICAL、0.98 的局部整体背离信号。

证据：[management_intelligence.py:149](../backend/src/enterprise_insight_backend/management_intelligence.py#L149)、[management_intelligence.py:721](../backend/src/enterprise_insight_backend/management_intelligence.py#L721)、[management_intelligence.py:769](../backend/src/enterprise_insight_backend/management_intelligence.py#L769)、[management_intelligence.py:1137](../backend/src/enterprise_insight_backend/management_intelligence.py#L1137)。

修复：建立周期、口径、单位、目标方向、修订与可比较条件；按独立周期而非记录条数比较；没有可比数据时明确“不足以判断”。验收：同周期修订只占一期；数值自动计算达标状态；跨期不相关指标不输出“已相互印证”。

### F13 · P1：设计侧与结果侧印证不完整，“有反例”和“已接受取舍”容易失真

**已复现 + 源码确认。** 一个结果信号被第一个匹配的设计风险消耗，另一个同样相关的设计风险只能得到结构预警。匹配只要求同问题族与任意受影响实体重合，可能只是共享顶层战略。冲突分支依赖不同方向，但当前生成器主要只产 PROBLEM，HEALTHY 没有接入的生成路径。已接受取舍优先匹配，监控指标 ID 只校验存储，没有实际用于越界重告警。

证据：[management_intelligence.py:973](../backend/src/enterprise_insight_backend/management_intelligence.py#L973)、[management_intelligence.py:987](../backend/src/enterprise_insight_backend/management_intelligence.py#L987)、[management_intelligence.py:1069](../backend/src/enterprise_insight_backend/management_intelligence.py#L1069)。

修复：多对多证据关联，明确实体/机制/指标/时期的匹配理由；保留支持、反对与未知；取舍包含代价上限、监测和重新审议条件。潜在关系 Agent 仍可自由探索，但程序规则不得把弱关联包装成可靠诊断。固定 90%–99% 分值不能当作校准后的正确概率。验收：输入顺序不改变分类；一个结果可支持多条相关假设；反例可降低确信；已接受的成本越界仍提示管理层。

### F14 · P1：管理层反馈没有延续到下一轮，页面还可能清空原反馈

**已复现 + 源码确认。** 将洞察否定并写反馈，再分析同一输入，会生成新的 OPEN 洞察、反馈为空，列表累积重复。前端反馈框可以显示已存反馈，但仅点击状态更新时提交本地未编辑值 `null`，可能清空原反馈。

证据：[management_intelligence.py:435](../backend/src/enterprise_insight_backend/management_intelligence.py#L435)、[management_intelligence.py:1049](../backend/src/enterprise_insight_backend/management_intelligence.py#L1049)、[ManagementResults.tsx:36](../frontend/src/features/agents/ManagementResults.tsx#L36)。

修复：洞察稳定身份与每轮分析版本分离；反馈关联稳定身份，只有新证据变化才触发重新打开；PATCH 保留未改字段。验收：相同输入再分析不丢反馈、不新建重复工作项；新证据导致重开时展示变化原因与旧决策。

### F15 · P1：会议、指标、取舍、补充信息的维护流程不完整

**源码确认。** 管理结果面板多数是只读；没有方便地创建/修订指标观测、回答信息请求、更新会议行动状态、调整已接受取舍的入口，Agent 也没有相应更新工具。后端若干 PATCH 存在，但使用者无法完成后续工作。

证据：[ManagementResults.tsx:27](../frontend/src/features/agents/ManagementResults.tsx#L27)、[api/index.ts](../frontend/src/api/index.ts)、[agent_runtime.py:56](../backend/src/enterprise_insight_backend/agent_runtime.py#L56)、[management_intelligence.py:214](../backend/src/enterprise_insight_backend/management_intelligence.py#L214)。

修复：优先补 Agent 的创建/更新/查询/关闭工具，仅保留结果卡片上的修改、回答、确认等轻量操作。验收：从“需要补充信息”到“用户回答—证据进入上下文—重新分析”不用手工调 API；执行后观测也能更新并被分析使用。

### F16 · P1：Scenario 只是存储记录，没有真正作用于图查询

**已复现。** 保存带改名 overlay 的方案后，带 `scenario_id` 查询图，仅回显方案 ID，节点仍是原名称。不能据此声称已实现组织方案沙盘或方案效果模拟。

证据：[projection.py:297](../backend/src/enterprise_insight_backend/projection.py#L297)、[collaboration.py](../backend/src/enterprise_insight_backend/collaboration.py)。

修复：先实现有固定基线的隔离 overlay、图差异、有效性检查与批准应用；经营效果估算应明确假设和模型，不伪装为真实预测。验收：方案图体现修改，正式图保持不变；可比较多个方案；过期基线必须重验。

### F17 · P1：可信本体内核的类型、继承、引用约束存在漏洞

**已复现 + 源码确认。** DATE/UUID 可存 `not-a-date` / `not-a-uuid`；子类型可以缺父类型必填属性；新建 DRAFT 关系可引用退役对象，入库后又被图过滤掉；随机不存在的材料/片段 ID 能被指标观测接受为证据。模型回复的引用也主要做结构校验，没有形成逐条可验证的证据链。

证据：[ontology.py:593](../backend/src/enterprise_insight_backend/ontology.py#L593)、[ontology.py:658](../backend/src/enterprise_insight_backend/ontology.py#L658)、[projection.py:384](../backend/src/enterprise_insight_backend/projection.py#L384)、[management_intelligence.py:149](../backend/src/enterprise_insight_backend/management_intelligence.py#L149)、[agent_runtime.py:138](../backend/src/enterprise_insight_backend/agent_runtime.py#L138)。

修复：采用真实值解析；统一展开类型继承和端点类型判定；所有写入口共用引用有效性/生命周期校验；检查证据存在、所属项目与对应片段。注意：合法引用不等于陈述已被证据充分支持，后者仍需保留原文、推导和人工确认。验收：以上反例全部被清晰的 4xx 拒绝；正常继承实例和合法引用仍可写入。

### F18 · P1：无版本旧库的接管路径可能伪装成成功升级

**已复现。** 用最初 V3 schema 建临时库、移除版本标记模拟无版本旧库后升级：版本被盖章为最新 `9a821cf6d930`，但 `source_documents.source_system_id` 不存在。原因是 `create_all` 只能补表，不能补旧表的新增列。

证据：[migration.py:19](../backend/src/enterprise_insight_backend/migration.py#L19)、[test_migrations.py:47](../backend/tests/test_migrations.py#L47)。现有“旧库接管”测试用当前 metadata 建库，所以测不到真实缺列升级。

修复：识别受支持的真实旧 schema，执行版本化迁移并核验；不明 schema 明确拒绝并提供备份指引，不能直接 stamp head。验收：从每个保留版本升级后逐表核对列、索引、约束和数据；失败不得标记成功。

### F19 · P2：部分合法形状请求造成 500，跨字段状态校验不完整

**已复现。** 通过通用假设接口创建 `type_key=causal_hypothesis`，随后列举因果假设返回 500；会议标题 PATCH 为 null 返回 500；已回答的信息请求清空 answer 后仍为 ANSWERED。

证据：[collaboration.py:217](../backend/src/enterprise_insight_backend/collaboration.py#L217)、[management_intelligence.py:214](../backend/src/enterprise_insight_backend/management_intelligence.py#L214)、[management_intelligence.py:334](../backend/src/enterprise_insight_backend/management_intelligence.py#L334)。

修复：保留类型不能走未校验的通用入口；区分“未提供”和“显式 null”；PATCH 合并已有记录后验证整体不变式。验收：错误输入稳定返回可读 4xx，列表不被一条坏记录击穿，ANSWERED 必须有有效回答。

### F20 · P2：评估已持久化，但还不是基于真实运行轨迹的能力评测

**已复现 + 源码确认。** 调用者直接提交 `citation_count`、`action_keys` 等预测字段，即可获得满分，无须对应真实引用或 Action 执行。黄金集输入没有驱动实际 Agent 运行。

证据：[evaluation.py:119](../backend/src/enterprise_insight_backend/evaluation.py#L119)、[evaluation.py:218](../backend/src/enterprise_insight_backend/evaluation.py#L218)。这不是分数计算算错，而是当前只实现“对调用者提供的结果评分”，不能拿它证明 Agent 正确。

修复：把人工评分器与运行评测器分开；后者绑定实际 Agent run、输入快照、引用校验、动作日志、模型/提示版本。验收：虚构计数不能提高实际运行分数；同一黄金集能回放，比较不同版本且定位失败原因。

### F21 · P2：缓存刷新和项目选择造成“做了却看不到 / 选了又跳回”

**源码确认，未进行本轮浏览器复现。** Action 完成只刷新动作相关查询，没有刷新图、会议、指标、洞察等结果；管理结果没有持续刷新。管理图页面优先使用 URL 的 project，并反向覆盖全局工作区，侧栏切换可能被旧 URL 重新覆盖。

证据：[ActionCenter.tsx:55](../frontend/src/features/actions/ActionCenter.tsx#L55)、[ManagementResults.tsx:27](../frontend/src/features/agents/ManagementResults.tsx#L27)、[ProjectionPage.tsx:15](../frontend/src/pages/executive/ProjectionPage.tsx#L15)。

修复：统一动作副作用到 query invalidation 的映射；统一 URL 与选择器的状态来源；依据业务状态显示成功/失败。验收：分析执行完无须刷新即可看到结果；连续切换两个公司的两个项目不回跳、不混显示。

### F22 · P2：Worker 缺原子领取、租约和异常隔离

**源码风险，未做本轮并发/进程故障复现。** 领取是先 SELECT 再 UPDATE；启动时全量重新排队活跃任务，没有 owner/lease；Worker 外层未隔离所有异常；前端接受任务后还会主动执行，而后台 Worker 也能执行同一任务。运行状态仅对 completed/cancelled 有明确短路。

证据：[agent_worker.py:40](../backend/src/enterprise_insight_backend/agent_worker.py#L40)、[agent_worker.py:56](../backend/src/enterprise_insight_backend/agent_worker.py#L56)、[AgentWorkspace.tsx:87](../frontend/src/features/agents/AgentWorkspace.tsx#L87)、[agent_runtime.py:100](../backend/src/enterprise_insight_backend/agent_runtime.py#L100)。

修复：保留一种任务执行入口；原子 claim、租约/心跳、幂等效果、分事务与单任务异常隔离；恢复只处理过期租约。验收：双 Worker 抢同一任务只产生一次有效结果；进程终止可恢复；一个失败任务不停止后续任务。不能宣称外部调用天然 exactly-once，应以幂等效果保证业务结果。

### F23 · P2：案例参考存在，但结果积累到案例仍是分离操作

**源码确认。** 已有可复用案例和显式选择历史案例的上下文能力，这是符合“让 Agent 参考过去公司”的方向，不需要训练模型参数。欠缺的是从管理反馈、动作观察、最终效果到案例修订的连续操作；当前 Agent 工具也不能维护这些案例。

证据：[agent_runtime.py:113](../backend/src/enterprise_insight_backend/agent_runtime.py#L113)、[agent_runtime.py:56](../backend/src/enterprise_insight_backend/agent_runtime.py#L56)、[test_learning_cases.py:15](../backend/tests/test_learning_cases.py#L15)。

修复：给 Agent 增加引用/创建/修订案例工具，保留来源、适用条件、效果与反例；由管理层确认后成为可参考案例。验收：一次确认过的改进及结果可直接转为案例；新公司显式引用时能追溯来源与适用边界，不当作该公司的事实。

### F24 · P2：业务导出不是完整备份，也不能完成离线导出—导入往返

**源码确认。** 导出支持多格式，但 bundle 没覆盖可复用案例、完整对话、动作观察/日志、完整发布历史等全部运行资料；没有匹配的 bundle 恢复入口。若仍要求离线给总部或换机继续使用，当前不能当作完整项目迁移。

证据：[exporting.py:112](../backend/src/enterprise_insight_backend/exporting.py#L112)、[exporting.py:209](../backend/src/enterprise_insight_backend/exporting.py#L209)、[evidence.py:73](../backend/src/enterprise_insight_backend/evidence.py#L73)。

修复：分开“分析数据导出”“可复用案例包”“可恢复项目备份”；为需要往返的包定义版本、完整清单、校验、导入预演和 ID 冲突策略。验收：空实例恢复后对象、边、证据、案例、观察、发布基线及引用均一致；明确排除的配置必须列出。

### F25 · P1：根目录 Docker、CI、备份脚本仍主要面向旧 V2

**源码确认。** Docker 打包根 `src`、`migrations_v2`，启动 `enterprise_insight.app:app`，并非当前 `enterprise_insight_backend`；CI 在根目录安装测试旧项目，没有完整执行当前前后端流水线；旧备份脚本的数据目录也与 V3 启动目录不同。

证据：审计时的旧版 `Dockerfile`、旧备份脚本和旧启动链已在后续清理中删除；当前以 [Dockerfile.v3](../Dockerfile.v3)、[docker-compose.v3.yml](../docker-compose.v3.yml)、[launch.ps1](../scripts/launch.ps1) 和 [ci.yml](../.github/workflows/ci.yml) 为准。

修复：先声明唯一受支持运行版本，再统一依赖、镜像、迁移、契约生成、CI 和备份恢复。旧 V2 明确归档或在核对依赖后清理，不能混称当前部署。验收：干净克隆按 README 启动与桌面同一 V3；CI 执行 V3 后端与前端测试；备份从空目录可恢复。

### F26 · P2：启动器只检查健康，不保证启动的是最新代码

**源码确认。** 当前桌面快捷方式确实指向 `scripts/launch.ps1`，不是错误指向 V2。问题是健康的旧后端会被复用，启动没有 reload 或构建指纹；更新代码后点击启动可能仍用旧进程。自定义 BackendPort 没有同时更新 Vite 默认代理地址；端口占用检查也缺版本/进程身份验证。

证据：[launch.ps1:136](../scripts/launch.ps1#L136)、[launch.ps1:142](../scripts/launch.ps1#L142)、[vite.config.ts:6](../frontend/vite.config.ts#L6)。

修复：版本感知健康检查，提供明确重启/更新模式，统一端口与代理配置，验证进程归属后操作。验收：后端代码更新后重启显示新构建版本；自定义端口前后端仍连通；端口被其他服务占用时不误报启动成功。

### F27 · P2：可扩展模块多为静态元数据，新增能力仍依赖多处硬编码

**源码确认。** 探索模块目录主要是静态声明，动作白名单、提示词、参数校验、预演、执行与补偿分别分支维护。增加一个工具容易只接通其中一段，出现“可声明不可执行”。预览中间数据也没有完整过期清理生命周期。

证据：[collaboration.py:40](../backend/src/enterprise_insight_backend/collaboration.py#L40)、[agent_runtime.py:56](../backend/src/enterprise_insight_backend/agent_runtime.py#L56)、[actions.py:767](../backend/src/enterprise_insight_backend/actions.py#L767)、[actions.py:858](../backend/src/enterprise_insight_backend/actions.py#L858)。

修复：小型强类型工具注册表即可，不必先造庞大插件平台；一个定义包含 schema、权限范围、预演、执行、补偿、输出和回归用例。探索方向与规则可独立注册和版本化。验收：加一个新工具只需一处注册和对应测试；缺 handler 时启动或能力检查立即报错，而非运行才失败。

### F28 · P2：现有验收覆盖太窄，无法支撑“从用户流程全部完成”

**源码确认 + 回归结果。** 后端 23 个测试主要是小型 API 正常路径，Agent 测试使用 MOCK；前端只有两个图元素转换测试。真实文档、空实例配置、跨项目切换、较大材料量、实际模型工具执行、反馈复用、迁移老 schema、并发和恢复等缺少端到端验收。历史文档同时描述 V2 与多个未完全落实的方案。

证据：[test_agent_runtime.py](../backend/tests/test_agent_runtime.py)、[test_migrations.py:47](../backend/tests/test_migrations.py#L47)、[toElements.test.ts](../frontend/src/features/projection/toElements.test.ts)、[ci.yml:20](../.github/workflows/ci.yml#L20)。

修复：把本轮反例转为正式回归测试；补 API 契约、浏览器用户流程、固定合成公司数据集与可选真实模型冒烟测试；以可复现验收结果维护当前能力清单。验收标准应为“已测范围和剩余限制明确”，不能承诺证明绝对无 bug。

## 4. 22 组隔离探针的实测结果

这些是定向复现，不是“22 个现有自动化测试失败”。脚本输出实际状态供核对；修复时应转换为期待正确结果的断言。

| 编号 | 输入/操作 | 实际结果 | 对应问题 |
| --- | --- | --- | --- |
| M01 | 目标 95，三次 40，未手填 MISS | 未生成未达标信号 | F12 |
| M02 | 同一期三条 MISS | miss_count=3，判反复未达标 | F12 |
| M03 | 观测引用不存在的材料/片段 UUID | 接受 | F17 |
| M04 | 图中已建战略—指标关系 | 仍提示战略没有指标 | F11 |
| M05 | 否定洞察、留反馈，再分析 | 新 OPEN，无反馈，列表变两条 | F14 |
| M06 | 2022 局部指标与 2026 企业指标 | 判 CRITICAL 背离，confidence=0.98 | F12 |
| M07 | 两个设计风险共享相关结果信号 | 首个获印证，另一个只有结构预警 | F13 |
| M08 | 通用接口存保留因果类型后查询 | 因果列表 HTTP 500 | F19 |
| M09 | PATCH 会议 title=null | HTTP 500 | F19 |
| M10 | 已回答请求只清空 answer | HTTP 200，ANSWERED + null | F19 |
| M11 | 自报动作键和引用数量 | 评估满分 1.0 | F20 |
| W01 | 上传界面提供的 TXT 类型 | HTTP 422，格式不支持 | F02 |
| W02 | 先导入，再建映射，再导入同文件 | DUPLICATE，仍 0 对象 | F10 |
| W03 | 同键设计对象，按不同权威来源顺序导入 | 最后写入胜出，设计视角被改 | F09 |
| W04 | 发布后改草稿，再取管理图和 Agent 上下文 | 分别读发布值和草稿值 | F07 |
| W05 | 创建改名方案，再带 scenario_id 查询 | 仅回显 ID，图未体现 overlay | F16 |
| W06 | 不存在类型的创建动作 | 预演 valid=true，执行 FAILED | F05 |
| W07 | 大量旧材料后选择新附件，排除未确认材料 | 新片段缺失，仍有 CANDIDATE | F04 |
| K01 | 非法日期/UUID 字符串 | 两个属性均能入库 | F17 |
| K02 | 子类型缺父类型必填字段 | 能入库 | F17 |
| K03 | 新关系引用已退役对象 | DRAFT 关系能入库，但图不可见 | F17 |
| K04 | 无版本旧 schema 升级 | 标记最新版，却缺 source_system_id 列 | F18 |

复跑命令（在 `D:\project\palantir\backend` 执行；仅创建临时合成数据库）：

```powershell
.\.venv\Scripts\python.exe ..\artifacts\audit-2026-09-05\management_probes.py
.\.venv\Scripts\python.exe ..\artifacts\audit-2026-09-05\workflow_probes.py
.\.venv\Scripts\python.exe ..\artifacts\audit-2026-09-05\kernel_probes.py
```

## 5. 建议修复顺序与完成门槛

不建议再次推翻全部产品，也不建议先新增更多 Agent/菜单。保留三个工作范围：投影建模、系统语义对齐、管理探索；先把现有能力接通并使其可验证。

### 第一批：先停止错误写入与误导输出

处理 F05、F06、F07、F09、F10、F14、F17、F18、F19。先固定证据保留、PATCH 语义、内核引用与迁移；为版本、身份、设计/观测分离和洞察稳定身份确定统一契约后再改实现。

完成门槛：本轮相关反例变为正式回归且通过；旧数据有明确迁移路径；预演通过不再只是请求格式合法；不能悄悄清空证据/反馈、覆盖设计基线。

### 第二批：完整交付“没有系统数据也能建模”

处理 F01–F04、F15、F21；配合 F11 统一岗位、职责、战略、KPI 的身份。先让文档输入—抽取—建对象与关系—批量确认—人工微调—发布—管理查询完整可用。

完成门槛：三个合成公司、每公司两个项目，分别使用访谈文档、问卷导出、混合材料，从空实例用前端完成。所有内容均能追溯材料；没有证据的潜在关系保留为假设；跨公司项目不能混用上下文；不依赖 Swagger 或后台手填数据。

### 第三批：落实“系统数据补全本体”

处理 F08–F12：先统一原始记录、映射版本、身份解析、属性来源、时序观测，再补一个实际只读连接器。文件重算与连接器同步走同一内核，避免两套行为。

完成门槛：两个系统以不同编码描述同一实体，能明确对齐；同码异物不误并；实体、关系、事件、指标可查询和导出；相反导入顺序结果一致；重跑不重复，错误行可定位和重试。

### 第四批：使管理探索与反馈积累真正有用

处理 F12–F16、F20、F23：先让潜在关系 Agent 广泛提出有明确假设的管理方案，再用可比较的证据提供支持/反对/未知，不以几条启发式规则替代管理推理。

完成门槛：只见设计风险时预警，只见结果异常时待解释，两侧可比且相关时提示印证；反例和已接受取舍仍可再审；否定反馈下轮保留；结构调整方案可看图对比；确认后的行动和效果可沉淀案例，再被新公司显式参考。

### 第五批：可靠运行与可交付

处理 F22、F24–F28，并完成全部剩余浏览器与故障测试。统一 V3 构建、启动、迁移、备份、CI、当前使用说明。

完成门槛：干净安装、升级、双任务并发、中断恢复、备份还原都验证；任何功能未支持时明确说未支持，而不是空成功；真实模型测试应另行确认模型、费用与可发送的数据范围。

## 6. 修复复杂度与边界

可以较小范围处理：编辑保留证据/反馈、空值 PATCH、保留类型校验、日期 UUID 校验、退役端点校验、UI 缓存、格式能力提示、模型配置入口、启动端口联动。这些也需要回归，不能直接凭直觉批量改。

需要先定契约的结构性问题：Agent 工具循环与批量变更、设计/观测多来源存储、统一 KPI、发布快照上下文、洞察身份与反馈继承、多对多证据印证、真实旧 schema 迁移和可恢复包。应先确认数据模型与兼容策略，再分步实施，不再以“全面重构”一次性混改。

本轮交付的是审查结果，不表示以上修复已经实施。
