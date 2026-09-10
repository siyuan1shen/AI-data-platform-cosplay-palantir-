# V3 验收证据索引

更新时间：2026-09-10。

本索引把验收组、主要自动化测试、浏览器留痕和外部环境门槛放在一起，便于继续逐项关闭，而不是用“测试总数”替代需求验收。最后一次完整门禁：后端 137 项（134 通过、3 跳过），前端 9 个测试文件、23 项通过。

## 验收组索引

| 验收组 | 主要自动化证据 | 当前证据范围 | 状态 |
| --- | --- | --- | --- |
| A01 数据不丢失与领域约束 | `backend/tests/test_audit_regressions.py`、`test_core_workflow.py`、`test_change_sets.py`、`frontend/src/features/actions/ActionCenter.test.ts`、`frontend/src/api/index.test.ts` | PATCH、关系参与方、反馈、非法输入、结构化校验错误可读显示、原子变更和审批边界 | 本地已验证 |
| A02 空实例与材料 | `backend/tests/test_material_parsers.py`、`test_fde_acceptance.py`、`frontend/src/features/imports/ImportWorkbench.test.tsx` | CSV/TXT/DOCX/PDF/XLSX/JSON、CompanyCheck 预览确认、文件大小上限 | 本地已验证；扫描 PDF OCR 与更多损坏组合待外部环境 |
| A03 Agent 建模 | `backend/tests/test_agent_runtime.py`、`test_fde_acceptance.py`、`frontend/src/features/agents/AgentWorkspace.test.ts` | 材料上下文、结构化动作、预演/审批/执行、真实 ID 回读 | 本地 MOCK 已验证；外部模型质量待验证 |
| A04 上下文与发布 | `backend/tests/test_query_snapshots.py`、`test_agent_runtime.py`、`test_exports.py` | 固定快照、材料选择、按需片段、发布版本和导出一致性 | 本地已验证；浏览器全量回放待补 |
| A05 来源、映射与重算 | `backend/tests/test_system_integration.py`、`test_semantic_datasets.py` | 设计/现实分离、映射生命周期、血缘、外键和重算幂等 | SQLite/REST 本地已验证；PostgreSQL/厂商样本待验证 |
| A06 指标与时间 | `backend/tests/test_management_intelligence.py`、`test_semantic_datasets.py` | KPI 定义、观测周期、目标方向、重复修订和缺失期 | 合成数据本地已验证；真实财务口径待验证 |
| A07 管理探索与反馈 | `backend/tests/test_management_intelligence.py`、`test_scenarios.py`、`frontend/src/features/agents/ManagementOperations.test.ts`、`ManagementResults.test.ts` | 设计/结果信号、假设、信息请求、会议、反馈和取舍 | 合成数据局部已验证；复杂多对多与真实试点待验证 |
| A08 方案、执行与案例 | `backend/tests/test_scenarios.py`、`test_learning_cases.py`、`test_actions.py` | overlay/diff/rebase/apply、行动观察、案例草稿和跨项目匿名参考 | 本地已验证；长期效果和副作用待真实试点 |
| A09 任务与故障恢复 | `backend/tests/test_agent_worker.py`、`test_evaluations.py`、`test_actions.py` | Worker 租约、取消/恢复、幂等、审批与数据库活动槽位 | 本地已验证；多进程外部队列待部署环境 |
| A10 升级、恢复与部署 | `backend/tests/test_migrations.py`、`test_project_restore.py`、`scripts/verify-v3.ps1` | 迁移链、恢复预览/确认、哈希、构建指纹和本地启动 | 本地 SQLite/REST 已验证；Docker/干净安装/中文路径待验证 |
| A11 评估真实性 | `backend/tests/test_evaluations.py`、`frontend/src/features/agents/EvaluationPanel.tsx` | 持久化黄金集、真实运行轨迹引用和动作结果评分 | MOCK 机制已验证；真实外部模型运行待授权 |
| A12 性能 | `backend/tests/test_query_snapshots.py`、性能基准脚本 | 合成图查询和固定快照性能 | 当前本地图查询基准已通过；导入导出/首屏/内存生产基准待补 |

## F01–F28 映射

F01–F28 的逐项任务映射以 [30 号任务清单](30-v3-improvement-backlog.md) 第 9 节为准；当前完成状态以 [34 号覆盖矩阵](34-v3-requirement-coverage-matrix.md) 为准。该矩阵中的“部分验证”仍然有效；本索引只补充证据入口，不把局部测试提升为完整验收。

## 当前明确的外部门槛

- `EI_TEST_POSTGRES_DSN` 未设置，因此 3 个真实 PostgreSQL 用例没有运行；SQLite 通过不等价于 PostgreSQL 已验收。
- 当前没有可授权的真实外部模型运行记录；MOCK 只验证状态机、工具链、持久化和评分机制。
- 当前没有 ERP/MES/CRM 厂商样本；通用连接器不自动代表具体厂商兼容。
- Docker、干净机器安装、端口占用、中文路径和生产规模内存/首屏尚未形成本机证据。
- 公司/项目上下文的 3 条恢复与隔离用例已经全部通过；对应的真实浏览器刷新证据保留在 `synthetic_case_frontend_test_trace.md` 第 15 节。
