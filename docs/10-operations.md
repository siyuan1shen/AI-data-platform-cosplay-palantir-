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

默认地址为 `http://127.0.0.1:8012`。启动器会依次准备运行目录、检查依赖、执行 V3 Alembic 迁移、导出 OpenAPI 契约、构建前端、启动 Uvicorn，并等待 `/api/v3/health` 就绪。可用 `-BackendPort` 和 `-SkipOpen` 覆盖默认行为；独立复现或验收时，还可用 `-DataDirectory`、`-RuntimeDirectory` 和 `-LogDirectory` 指定隔离目录，避免碰到既有本地实例。

停止服务：

```powershell
.\scripts\stop-v3.ps1
```

桌面快捷方式 `scripts\desktop-launch.ps1` 只调用上述 V3 启动器，不会加载历史版根目录程序。

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

1. 先在“公司与工作区”确认目标公司和项目；
2. 在“问卷与答案”导入 CSV/JSON/XLSX，完成字段识别和预览后确认；
3. 在“证据中心”导入访谈纪要、报告等材料；
4. 在“系统数据接入”导入 ERP/MES/CRM 的 CSV，或配置只读 REST JSON 数据源；
5. 在“分部与总部交换”导出当前项目需要的普通 CSV/JSON 文件；
6. 总部通过网络传输、共享盘或 U 盘接收后，在自己的 V3 实例中预览、确认并归属到目标公司/项目。

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
