# V3 运行手册

## 1. 运行目录

- `%LOCALAPPDATA%\EnterpriseInsight\data`：SQLite 数据库、导入包和导出文件；
- `%LOCALAPPDATA%\EnterpriseInsight\runtime`：运行时元数据；
- `%LOCALAPPDATA%\EnterpriseInsight\logs`：启动日志和服务日志。

这些目录不进入 Git。仓库只保存程序、迁移、契约、文档和测试；当前实例中的企业问卷、答案、证据、凭据和数据库不会随代码提交。

## 2. 启动前诊断

```powershell
.\scripts\doctor-v3.ps1
```

诊断脚本只做启动前的轻量检查：项目关键文件、Python/Node/npm 版本；`Container` 模式额外校验 Docker Compose 配置。它不安装依赖、不执行迁移、不构建前端，也不替代启动器的健康检查。容器诊断使用：

```powershell
.\scripts\doctor-v3.ps1 -Mode Container
```

## 3. 本地启动与停止

```powershell
.\scripts\launch.ps1
```

默认地址为 `http://127.0.0.1:8012`。启动器会依次准备运行目录、检查依赖、执行 V3 Alembic 迁移、使用仓库中已核对的 OpenAPI/前端类型契约构建前端、启动 Uvicorn，并等待 `/api/v3/health` 就绪。契约刷新是开发或 CI 步骤，不是普通启动前置条件。可用 `-BackendPort` 和 `-SkipOpen` 覆盖默认行为；独立复现或验收时，还可用 `-DataDirectory`、`-RuntimeDirectory` 和 `-LogDirectory` 指定隔离目录，避免碰到既有本地实例。

停止服务：

```powershell
.\scripts\stop-v3.ps1
```

桌面快捷方式 `scripts\desktop-launch.ps1` 只调用上述 V3 启动器，不会加载历史版根目录程序。

桌面可直接双击“企业数字投影平台”或兼容入口“企业全面检查”。它们调用 `scripts\start-enterprise-insight.cmd`，会为当前项目临时绕过 PowerShell 执行策略，并通过 Windows Python 启动器优先选择 Python 3.12；启动失败时窗口不会立即关闭，详细信息在 `%LOCALAPPDATA%\EnterpriseInsight\desktop-launch-error.log`。

## 4. 容器启动

```powershell
docker compose -f docker-compose.v3.yml up --build -d
```

Compose 默认绑定主机 `8012` 端口；需要并行实例时可设置 `EI_PORT=18012`，容器内部端口仍为 `8012`。

停止容器（保留卷）：

```powershell
docker compose -f docker-compose.v3.yml down
```

删除卷会删除容器卷中的数据库和运行数据，不属于普通停止流程。

## 5. 导入、导出与分部交付

1. 先在“企业”确认目标公司；系统自动打开该公司的唯一企业投影；
2. 在“问卷与答案”导入 CSV/JSON/XLSX，完成字段识别和预览后确认；
3. 在“证据中心”导入访谈纪要、报告等材料；
4. 在“系统数据接入”导入 ERP/MES/CRM 的 CSV，或配置只读 REST JSON 数据源；
5. 在对应的接入或导出区域导出当前企业需要的普通 CSV/JSON 文件；
6. 总部通过网络传输、共享盘或 U 盘接收后，在自己的 V3 实例中预览、确认并归属到目标公司。

问卷与答案页面的一键导出会去掉无回答项，并合并同一租户、公司、问卷和问题的重复内容。原始系统记录接口采用服务端分页，默认只返回 100 行并返回总数。

当前版本的交换边界是可审阅的普通文件；不要把整个实例目录、数据库文件、凭据目录或备份文件当作业务交换包。

## 6. 数据库迁移

本地启动器会自动执行 V3 迁移。手工检查或升级时：

```powershell
python -m alembic -c backend\alembic.ini check
python -m alembic -c backend\alembic.ini upgrade head
```

迁移文件位于 `backend\src\enterprise_insight_backend\migrations`。不要使用已删除的根目录旧迁移链。

## 7. 健康检查与故障定位

- `GET /api/v3/health`：服务、数据库、Worker、前端和构建版本状态；
- `GET /api/v3/meta/capabilities`：当前能力矩阵；
- `GET /api/v3/openapi.json`：运行中的 API 契约；
- 启动失败查看 `%LOCALAPPDATA%\EnterpriseInsight\logs`；
- 导入失败先查看预览响应中的字段错误和行号；
- 模型失败检查本地凭据、基础地址、模型名和重试配置；错误响应不回显密钥。

## 8. 发布前检查

```powershell
python -m pytest backend\tests -q -p no:cacheprovider
python -m ruff check --no-cache backend\src backend\tests
python backend\scripts\export_openapi.py
Set-Location frontend
npm.cmd test -- --run
npm.cmd run typecheck
npm.cmd run build
```

确认 `.env`、数据库、导入数据、导出文件、运行目录和日志目录均处于 ignored 状态，并确认发布包只包含代码、契约、文档和测试。

## 9. 检索与临时数据生命周期

Agent 的来源检索分为三层：

- 企业模型和本体关系使用带类型、版本和状态条件的 SQL 查询；图查询只按请求的关系类型加载邻域，不把整库交给模型；
- Agent 的初始上下文对图节点、关系、本体类型、来源系统、映射、动作定义、指标定义和设计权衡均有硬上限；截断状态会随上下文记录，继续检索必须调用有范围的工具；
- 原始系统记录保留完整 JSON 作为权威值，同时维护 `raw_record_values` 索引，用于按项目、字段值和原始记录定位候选；
- 观察和潜在信息仍按各自服务边界读取，并受任务类型和结果上限约束。索引只用于缩小候选集，不替代原始来源。

临时资源可在以下接口预览和清理：

```text
GET    /api/v3/projects/{project_id}/lifecycle/cleanup/preview
POST   /api/v3/projects/{project_id}/lifecycle/cleanup
DELETE /api/v3/projects/{project_id}/lifecycle/resources/{kind}/{id}
GET    /api/v3/lifecycle/policies
```

系统自动清理过期导入预览、未确认工作观察预览、恢复临时包和过期导出文件。主动删除同样只允许这些临时资源，必须填写原因；删除记录写入 `lifecycle_deletion_audit`。正式企业模型、已确认统一数据、管理观察、潜在记录和已确认工作观察不会被该任务物理删除，正式数据继续使用退役、撤回或版本替代。

可用环境变量调整临时资源策略：

```text
EI_BACKEND_LIFECYCLE_CLEANUP_ENABLED=true
EI_BACKEND_LIFECYCLE_CLEANUP_INTERVAL_SECONDS=3600
EI_BACKEND_LIFECYCLE_WORK_OBSERVATION_PREVIEW_DAYS=7
EI_BACKEND_LIFECYCLE_EXPORT_DAYS=7
```

Agent 初始上下文上限也可以按部署规模调整：

```text
EI_BACKEND_AGENT_INITIAL_GRAPH_ENTITIES=200
EI_BACKEND_AGENT_INITIAL_GRAPH_RELATIONS=400
EI_BACKEND_AGENT_CONTEXT_ONTOLOGY_TYPES=500
EI_BACKEND_AGENT_CONTEXT_SOURCE_SYSTEMS=100
EI_BACKEND_AGENT_CONTEXT_SEMANTIC_MAPPINGS=500
EI_BACKEND_AGENT_CONTEXT_ACTION_DEFINITIONS=100
EI_BACKEND_AGENT_CONTEXT_METRIC_DEFINITIONS=500
EI_BACKEND_AGENT_CONTEXT_DESIGN_TRADEOFFS=100
EI_BACKEND_AGENT_ROUTE_IDENTITY_CANDIDATES=500
```
