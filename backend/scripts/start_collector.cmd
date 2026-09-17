@echo off
setlocal
cd /d "%~dp0"

if not exist "EnterpriseInsightCollector.exe" (
    echo 未找到 EnterpriseInsightCollector.exe
    echo 请先运行 build_collector.ps1 完成打包，再将本启动脚本放到 exe 同目录。
    exit /b 2
)

if not exist "collector_config.json" (
    echo 未找到 collector_config.json
    echo 请复制 collector_config.example.json 为 collector_config.json 并填写配置。
    exit /b 3
)

EnterpriseInsightCollector.exe --config collector_config.json
set "EXIT_CODE=%ERRORLEVEL%"
if not "%EXIT_CODE%"=="0" pause
exit /b %EXIT_CODE%
