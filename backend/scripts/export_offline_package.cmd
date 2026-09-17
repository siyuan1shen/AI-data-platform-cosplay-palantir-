@echo off
setlocal
cd /d "%~dp0"

if not exist "EnterpriseInsightCollector.exe" (
    echo 未找到 EnterpriseInsightCollector.exe，请先完成打包。
    exit /b 2
)
if not exist "collector_config.json" (
    echo 未找到 collector_config.json，请先完成配置。
    exit /b 3
)

set "OUTPUT=%~1"
if "%OUTPUT%"=="" set "OUTPUT=observation.json"
EnterpriseInsightCollector.exe --config collector_config.json --offline "%OUTPUT%"
set "EXIT_CODE=%ERRORLEVEL%"
if "%EXIT_CODE%"=="0" echo 离线数据包已生成：%OUTPUT%
if not "%EXIT_CODE%"=="0" pause
exit /b %EXIT_CODE%
