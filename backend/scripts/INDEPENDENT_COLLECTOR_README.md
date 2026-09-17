# 独立工作观察采集器

这是员工电脑上的独立程序，不包含管理平台页面，也不需要员工登录管理平台。

默认只记录粗粒度的前台应用和时间顺序，不记录键盘内容、鼠标点击、截图、录像、密码、窗口标题或完整 URL 参数。浏览器业务页面如需更准确识别，可以由企业另行部署浏览器扩展，并调用 `queue_activity()` 写入同一数据格式；没有扩展时浏览器活动只标记为 BROWSER，不猜测页面业务。

## 配置

```json
{
  "source_id": "device-001",
  "employee_key": "employee-001",
  "project_id": "项目 UUID",
  "role_key": "sales",
  "upload_url": "http://127.0.0.1:8012/api/v3/ingestion/work-observation/batches",
  "cache_dir": "./cache",
  "poll_seconds": 5,
  "idle_after_seconds": 300,
  "headers": {}
}
```

正式部署应由管理员完成设备到项目的关联，不信任员工端自行填写的企业或员工名称。

## 运行

```text
python independent_work_collector.py --config config.json --once
python independent_work_collector.py --config config.json --offline observation.json
python independent_work_collector.py --config config.json --flush
python independent_work_collector.py --config config.json
```

接收端不可用时，事件留在本地 SQLite 缓存中；在线成功返回后才删除缓存。离线文件可以在管理端的工作观察导入页面预览和确认，在线与离线使用同一数据格式和去重规则。

## 打包成无 Python 依赖的 Windows 程序

在开发电脑上打开 PowerShell，进入本目录，执行：

```powershell
powershell -ExecutionPolicy Bypass -File .\build_collector.ps1 -Clean
```

脚本优先使用 `pyinstaller`，也会尝试 `python -m PyInstaller`。如果缺少 PyInstaller，会明确提示：

```powershell
python -m pip install pyinstaller
```

成功后，`dist` 目录包含 `EnterpriseInsightCollector.exe` 和配置示例。员工电脑只需要运行 exe，不需要安装 Python。

## 配置补充

请复制 `collector_config.example.json` 为 `collector_config.json`，并填写 `source_id`、`employee_key`、`project_id` 和 `upload_url`。管理员负责分配这些标识，采集器不会自动创建或绑定企业人员。

如果员工电脑与平台不是同一台电脑，`127.0.0.1` 不能作为上传地址，应改为企业内网中平台的地址，例如：

```text
http://192.168.1.20:8012/api/v3/ingestion/work-observation/batches
```

## 启动与停止

把 `EnterpriseInsightCollector.exe`、`collector_config.json` 和 `start_collector.cmd` 放在同一目录，双击 `start_collector.cmd` 即可持续采集。按 `Ctrl+C` 停止。停止或异常退出不会删除本地缓存，下次启动会继续上传待处理事件。日志默认写入 `cache\collector.log`。

## 离线导出

平台不可访问或员工电脑不在内网时，双击 `export_offline_package.cmd`，或指定目标文件。离线导出会导出本地缓存中的全部待上传事件，而不是只导出前 1000 条：

```text
export_offline_package.cmd D:\transfer\employee-001-20260915.json
```

管理员在平台的“企业数字投影 → 工作观察”中选择 JSON 文件，执行“预览 → 检查 → 确认导入 → 生成分析”。在线上传和离线导入使用同一格式和事件去重规则，重复导入不会重复计数，同一事件标识对应不同内容时会报告冲突。

## 可恢复运行

- 待上传事件保存在 `cache\collector-cache.sqlite3`。
- 只有服务端成功响应后，事件才会从本地缓存删除。
- 断网、平台关闭、超时或无效响应时，程序记录日志并按 `retry_seconds` 重试。
- 进程被关闭后重新启动，会从 SQLite 缓存继续处理。
- 离线导出不会删除缓存，确认导入后可以安全重复导入。
- 不要手工删除 `collector-cache.sqlite3`，否则会丢失尚未上传的事件和序号状态。
- 更换电脑时，先离线导出并确认平台已经导入，再迁移配置；不要复制正在写入的 SQLite 文件。

## 支持的命令

```text
EnterpriseInsightCollector.exe --config collector_config.json --once
EnterpriseInsightCollector.exe --config collector_config.json --offline observation.json
EnterpriseInsightCollector.exe --config collector_config.json --flush
EnterpriseInsightCollector.exe --config collector_config.json
```

`--once` 采集一次后退出；`--offline` 采集一次并生成离线包；`--flush` 只上传缓存；不加选项则持续运行。

采集器产生的是观察证据，不是绩效评分。它不自动判断员工是否偷懒、哪个流程最佳或某个活动的因果关系。
