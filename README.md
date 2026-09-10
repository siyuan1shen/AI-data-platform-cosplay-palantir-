# 管理层企业决策投影平台

`palantir` 面向企业核心管理层和为其服务的调研、分析团队。平台先通过问卷、访谈和实地调研建立企业的初始概念模型，再逐步接入 ERP、MES、CRM、财务等业务系统，把企业表达为可查询、可演进的对象、关系、事件、指标、因果、决策、行动和结果，最终让管理层更轻松地理解企业、作出决策并从执行结果中学习。

它不是另一个 ERP，也不替代业务系统。ERP 负责记录和执行交易；本项目位于其上方，负责跨系统理解、解释、推演和行动闭环。

核心闭环：

- 先记录管理层初始关注方向，不把它当成事实结论；
- 调研和材料整理形成可追溯证据；
- 上游企业设计投影描述组织、岗位、流程、信息流和资金流，并发布不可变设计基线；
- 有 ERP、MES、CRM 或财务数据的企业，再基于设计基线创建下游运行投影；
- 本体把上游设计概念和下游系统字段放到同一语义层，系统接入用对象、事件和指标持续校准实际投影；
- 设计层就绪后即可供管理层查询，运行数据不是所有企业的前置条件；
- 因果与场景分析帮助管理层比较可能的干预；
- Agent 使用本体、图查询和专业工具完成研究、建模、分析与行动协作；
- 决策、执行和结果回流形成企业内部数据飞轮；
- 相似企业只提供经过验证的模式和先验，再由目标企业的数据校准。

## 现行规划

- [产品定位与总体架构](docs/13-executive-decision-platform.md)
- [端到端业务流程](docs/14-end-to-end-workflows.md)
- [本体、因果与 Agent 技术蓝图](docs/15-ontology-causal-agent-blueprint.md)
- [集中交付路线图](docs/16-delivery-roadmap.md)
- [全部设计文档索引](docs/README.md)
- [上游设计与下游运行投影](docs/27-upstream-downstream-projection-layers.md)

当前第一优先级不是增加页面、Agent 数量或云端基础设施，而是完成“订单到回款”纵向样板：调研转投影、对象与关系图、事件和指标、投影就绪校验、证据化管理诊断、因果假设、行动与结果学习。

## 当前可运行产品

仓库当前运行的是 `backend/` + `frontend/` 组成的 V3 本地平台。历史版根目录运行代码、迁移、脚本和容器文件已清理；旧设计文档仍只用于理解演进过程，不被桌面启动器加载。
当前核心流程覆盖：

- 公司、项目、材料导入、证据片段和可追溯声明；
- 强类型对象、关系、事件、指标、本体版本和不可变发布快照；
- 企业投影 Agent、系统语义对齐 Agent 与面向高管的管理 Agent；
- Agent 动作预演、审批、执行、日志、回滚和效果观察；
- 文件数据源的预览、只读字段映射候选、字段映射、确认导入与对象级血缘；
- PostgreSQL 与通用 REST JSON 只读数据源的提取预览、游标和快照同步；具体 ERP/MES/CRM 厂商插件仍需单独适配；
- 战略—能力—流程—岗位—职责—权限—绩效—企业结果的管理链；
- 设计侧结构信号与会议、绩效、企业结果侧信号的交叉验证；
- 可证伪因果假设、设计取舍、信息请求、管理反馈、方案与学习案例；
- 持久化 Agent 黄金评测集、逐案例评分和版本回归记录；
- JSON、CSV、XLSX 和离线 ZIP 全量导出。

V3 API 的唯一前后端契约为 [OpenAPI](contracts/openapi.json)。

## 文档入口

- [V3 使用手册](docs/31-v3-user-manual.md)
- [V3 验收记录与待完成项](docs/32-v3-verification-report.md)
- [V3 交付证据矩阵](docs/33-v3-delivery-evidence.md)
- [V3 要求覆盖与未关闭项](docs/34-v3-requirement-coverage-matrix.md)
- [V3 证据索引](docs/35-v3-evidence-index.md)
- [V3 后端说明](backend/README.md)
- [设计文档索引](docs/README.md)
- [运行手册](docs/10-operations.md)
- [架构决议](docs/adr/README.md)

## 本地运行

```powershell
.\scripts\doctor-v3.ps1
.\scripts\launch.ps1
```

脚本会准备 Python 3.12 虚拟环境、安装依赖、执行 Alembic 迁移并等待健康检查：

- 开发者平台：`http://127.0.0.1:8012/developer/`
- 高管平台：`http://127.0.0.1:8012/executive/`
- 后端 OpenAPI：`http://127.0.0.1:8012/api/v3/docs`
- 健康检查：`http://127.0.0.1:8012/api/v3/health`

停止 V3 后台服务：

```powershell
.\scripts\stop-v3.ps1
```

## 测试

启动器构建前端，由 8012 同时提供页面与 API；5173 仅用于单独运行 Vite 调试。
V3 容器使用 `docker compose -f docker-compose.v3.yml up --build -d`。
当前验收机器未安装 Docker，容器运行验收状态见上述验收记录。

```powershell
cd backend
pytest
ruff check src tests
```

也可以在项目根目录用一个命令执行本地验收门禁（环境、后端回归、前端测试、类型检查和当前 8012 运行实例）：

```powershell
powershell -NoProfile -ExecutionPolicy Bypass -File scripts/verify-v3.ps1 -RequireRuntime
```

需要跳过前端生产构建时加 `-SkipFrontendBuild`；这不会跳过前端测试或类型检查。真实 PostgreSQL 仍需显式设置 `EI_TEST_POSTGRES_DSN`，否则对应用例会诚实跳过。

要验证“新实例可独立启动”而不触碰默认本地数据库，可给 `scripts\launch.ps1` 传入 `-DataDirectory`、`-RuntimeDirectory` 和 `-LogDirectory`，并使用一个未占用的 `-BackendPort`。这些参数只改变运行目录，不改变默认用户路径。

## 本地数据与开源代码

V3 本地平台不要求账号密码，也不会因为启动而上传企业数据。运行数据默认保存在
`%LOCALAPPDATA%\EnterpriseInsight\data`，不位于 Git 仓库中。只有逐次允许外部模型并允许共享项目上下文时，Agent 才会把相应内容发送给所配置的模型。

`data/`、`private/`、`.env`、数据库、密钥、证书和备份压缩包均被 Git 忽略。代码可以提交到 GitHub，真实问卷、回答、Evidence、主密钥和数据库副本不能放入 `src/`、`tests/`、`docs/` 或 Issue。备份及恢复要求见 [运行手册](docs/10-operations.md)。

项目采用 [Apache License 2.0](LICENSE)。

## 项目边界

当前阶段明确不做：

- 不把所有企业原始数据放进全局向量库；
- 不用 Agent 自由群聊代替可验证的工具工作流；
- 不执行 Agent 任意生成的代码；
- 不在没有评测和审核的情况下自动训练；
- 不提前拆分微服务；
- 不把派生索引当作事实真值源。
- 不投入复杂多租户、安全治理、加密交换、云端高可用和扩展沙箱；
- 不把分部—总部传输协议当作平台核心，确需汇总时先使用普通文件导入导出。
