# Contributing

感谢参与 Enterprise Insight Platform。当前唯一支持的运行路径是 `backend/` + `frontend/` V3：零静默冲突、Agent 只写提案、正式结论可追溯、企业原始数据默认留在本机。

## 开发流程

1. 使用 Python 3.12 和 Node.js 24；
2. 安装后端 `python -m pip install -e "./backend[dev]"`，再在 `frontend/` 运行 `npm.cmd ci`；
3. 为行为变化增加后端和/或前端测试，并同步 OpenAPI 契约；
4. 修改 V3 ORM 时在 `backend/src/enterprise_insight_backend/migrations/versions/` 增加迁移，并用 `backend/alembic.ini` 检查；
5. 提交前运行：

```powershell
python -m pytest backend\tests -q -p no:cacheprovider
python -m ruff check --no-cache backend\src backend\tests
python backend\scripts\export_openapi.py
Set-Location frontend
npm.cmd test -- --run
npm.cmd run typecheck
npm.cmd run build
```

## 不可接受的贡献

- 真实企业问卷、回答、Evidence、数据库或日志；
- API Key、密码、Token、私钥或带秘密的 `.env`；
- 绕过项目范围、审核、证据或冲突门禁的快捷写入；
- 在 API 进程执行未信任扩展代码；
- 用模型输出或模糊匹配静默覆盖正式知识；
- 未记录 ADR 的核心语义或安全边界变更。

测试数据必须是明显虚构或合成内容。安全问题不要提交公开 Issue，请遵循 [SECURITY.md](SECURITY.md)。
