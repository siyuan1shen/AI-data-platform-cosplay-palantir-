[CmdletBinding()]
param()

Set-StrictMode -Version Latest
$ErrorActionPreference = "Stop"

$ProjectRoot = Split-Path -Parent $PSScriptRoot
$StartScript = Join-Path $ProjectRoot "scripts\launch.ps1"
$Port = 8012
$Url = "http://127.0.0.1:$Port/developer/"
$LocalApplicationData = $env:LOCALAPPDATA
if ([string]::IsNullOrWhiteSpace($LocalApplicationData)) {
    $LocalApplicationData = [Environment]::GetFolderPath("LocalApplicationData")
}
if ([string]::IsNullOrWhiteSpace($LocalApplicationData)) {
    $LocalApplicationData = Join-Path $ProjectRoot "instances"
}
$DesktopDataDir = Join-Path $LocalApplicationData "EnterpriseInsight"
try {
    & $StartScript `
        -BackendPort $Port `
        -SkipOpen
}
catch {
    $logPath = Join-Path $DesktopDataDir "desktop-launch-error.log"
    New-Item -ItemType Directory -Path (Split-Path -Parent $logPath) -Force | Out-Null
    $_ | Out-String | Set-Content -LiteralPath $logPath -Encoding UTF8
    Write-Error "Enterprise Insight 启动失败。详细信息已写入 $logPath"
    exit 1
}

Start-Process $Url
