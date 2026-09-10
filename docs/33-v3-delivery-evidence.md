# V3 交付证据矩阵

更新时间：2026-09-10。

本文只记录当前仓库和本机运行结果，不把规划文档、接口存在或 MOCK 结果当成完成证据。完整业务范围见 [30 号任务清单](30-v3-improvement-backlog.md)，当前门禁见 [32 号验收记录](32-v3-verification-report.md)。

## 已有直接证据

| 范围 | 证据 | 当前结论 |
| --- | --- | --- |
| 数据库迁移 | `backend/tests/test_migrations.py`；26 个历史版本、失败回滚、外键检查 | V3 迁移链本地通过 |
| 领域 API | `backend/tests` 全量回归、`test_fde_acceptance.py` 和 `test_system_integration.py` | 137 项收集、134 项通过、3 项 PostgreSQL 环境跳过；公司、项目、证据、投影、关系、系统接入、发布等主链可运行；无系统企业建模与有系统数据语义对齐各有一条可重复验收链；三公司六项目组合回放、多公司 CompanyCheck 选择隔离、SQLite 只读接入、字段候选和外键关系物化已回归；评测并发执行增加数据库级唯一约束 |
| Agent | `test_agent_runtime.py`、`test_agent_worker.py`、`test_model_transport.py` | 工具提案、预演/执行、Worker、上下文边界、按需读取、自然语言建模触发、非制造业通用建模批准/执行和传输重试已覆盖 |
| 真实轨迹评测 | `test_evaluations.py::test_evaluation_execute_runs_active_cases_through_persisted_agent_runtime`、`test_evaluations.py::test_evaluation_async_execution_is_reconciled_by_worker` | 评测集可通过同步关闭 Worker 或持久化异步任务驱动真实 Agent 运行；结果评分不接受调用方自报动作/引用计数；本地 MOCK 已通过，浏览器按钮已完成 1/1 案例 |
| 导入与映射 | `test_system_integration.py`、`test_agent_runtime.py`、`test_contract.py`；建设端浏览器复验 | 文件导入预览/确认、服务端分页、SQLite/PostgreSQL/REST 参考连接器、只读字段候选、候选人工保存为 DRAFT、字段映射、外键关系映射和契约可用；候选不会自动批准映射 |
| 前端 | `frontend` 的 9 个测试文件、类型检查、Vite 构建；浏览器高级工具验收 | 23 个前端测试通过；建设端和使用端均可构建；公司/项目上下文刷新恢复和切换隔离通过；结构化 API 校验详情可读显示；关系角色表单按本体动态选择；关系图组件按需加载，评测页可轮询异步执行并回读最近一次运行结果，旧未路由发布页只保留兼容出口 |
| 启动与运行 | `scripts/launch.ps1`、`scripts/doctor-v3.ps1`、`scripts/stop-v3.ps1` | schema `c38d9e1a2b74` 的本地实例已重新启动；服务、数据库、Worker、前端均为 ready |
| 合成性能 | `backend/scripts/benchmark_v3.py`；10,000/30,000 与 20,000/60,000 两档 | 局部图查询 p95 为 66.701ms / 115.206ms，筛选列表 p95 为 11.653ms / 17.496ms；两档均通过脚本 500ms 门槛，完整生产规模仍未验收 |
| 浏览器流程 | 本机浏览器实际打开 `/developer/`、`/developer/build`、`/developer/model`、`/executive/`、`/executive/agent`；合成 C 第二项目实际导入问卷并由 Agent 创建 21 个对象/22 条关系；重启最新构建后再次回放四家公司项目切换 | 公司/项目选择、预览/确认导入、Agent 待审批动作、审批/执行、模型页回读、建设端到使用端切换、正式发布图和管理 Agent 页面可见；本体角色从下拉框选择后关系可提交；当前 4 家公司项目数显示为 1/1/2/2，切换不串项目 |
| OpenAPI 契约 | `backend/tests/test_contract.py::test_checked_in_openapi_contract_matches_runtime` | 运行时契约与 `contracts/openapi.json` 结构一致 |
| 仓库边界 | 根目录 V1/V2 运行代码、旧迁移、旧启动脚本已删除；运行数据在 `%LOCALAPPDATA%\EnterpriseInsight` | 当前只保留 V3 运行路径；合成样例不等于真实企业数据 |

## 当前仍是部分验证

| 范围 | 已做到 | 还缺什么 |
| --- | --- | --- |
| T00–T19 | 多数领域闭环已有实现和测试 | 尚未把每个任务逐一绑定到实现、契约、测试和浏览器证据 |
| T20 / A01–A12 | 浏览器已实际验证 4 家合成公司、6 个项目的选择与隔离；后端新增三家公司六项目全链路组合回放；C 第二项目已完成问卷预览/确认、Agent 建模、审批/执行和模型回读 | 尚未完成浏览器侧三家公司六项目的逐项回放、全部异常矩阵和完整可回放证据 |
| T21–T25 | 文件、PostgreSQL 适配器测试、REST JSON、映射和血缘 | 没有本机真实 PostgreSQL/ERP/MES/CRM 厂商环境；厂商兼容性不能推定 |
| T26–T32 | 指标、问题、探索、会议/结果、行动、案例服务存在 | 复杂多对多、真实管理判断和方案效果仍需更多合成与试点验证 |
| T33 | 评测集、运行评分和导出已持久化；评测执行接口已接入 Agent Runtime，并支持 Worker 队列任务、进度查询和前端轮询 | 本地 MOCK 轨迹已通过；真实外部模型轨迹尚未在明确预算和授权下运行 |
| T34–T35 | 项目恢复、V3 Docker 文件、启动器、CI 配置和旧代码清理完成 | Docker、干净安装、远程 CI 和完整恢复演练尚未在本机闭环 |

## 明确未纳入本地单体完成声明

- 向量/混合检索、分布式对象存储、流式计算、超大规模图查询和生产级队列；
- 总部自动汇聚、行业共享基准库、相似企业自动迁移评估和自动因果实验；
- 具体 ERP/MES/CRM/财务厂商的专用生产连接器；
- 真实企业管理收益、维护成本和分析岗位节省的统计证明。

## 可复现实验命令

在项目根目录运行：

```powershell
python -m pytest backend\tests -q -p no:cacheprovider
python -m ruff check --no-cache backend\src backend\tests
Set-Location frontend
npm.cmd test -- --run
npm.cmd run typecheck
npm.cmd run build
```

真实 PostgreSQL 用例只有在设置 `EI_TEST_POSTGRES_DSN` 时才执行；没有 DSN 时的跳过是诚实的环境限制，不是通过证明。
