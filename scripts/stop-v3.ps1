[CmdletBinding()]
param(
    [string]$RuntimeDirectory
)

Set-StrictMode -Version Latest
$ErrorActionPreference = "Stop"

$LocalData = [Environment]::GetFolderPath("LocalApplicationData")
if ([string]::IsNullOrWhiteSpace($LocalData)) {
    throw "Local application data directory is unavailable."
}
$ApplicationRoot = Join-Path $LocalData "EnterpriseInsight"
$RuntimeDir = if ([string]::IsNullOrWhiteSpace($RuntimeDirectory)) {
    Join-Path $ApplicationRoot "runtime"
} else {
    [System.IO.Path]::GetFullPath($RuntimeDirectory)
}
$PidFile = Join-Path $RuntimeDir "v3-processes.json"
if (-not (Test-Path -LiteralPath $PidFile -PathType Leaf)) {
    Write-Host "No launcher-managed processes were found."
    exit 0
}

$processInfo = Get-Content -Raw -LiteralPath $PidFile | ConvertFrom-Json
$expectedPatterns = @{
    backend_pid = "enterprise_insight_backend\.app:create_app"
    frontend_pid = "vite[\\/]bin[\\/]vite\.js"
}
foreach ($property in @("backend_pid", "frontend_pid")) {
    $pidProperty = $processInfo.PSObject.Properties[$property]
    if ($null -ne $pidProperty -and $null -ne $pidProperty.Value) {
        $processId = [int]$pidProperty.Value
        $nativeProcess = Get-CimInstance -ClassName Win32_Process `
            -Filter "ProcessId = $processId" -ErrorAction SilentlyContinue
        if ($null -ne $nativeProcess) {
            if ([string]::IsNullOrWhiteSpace($nativeProcess.CommandLine) -or
                $nativeProcess.CommandLine -notmatch $expectedPatterns[$property]) {
                throw "PID $processId does not match the recorded V3 $property process; refusing to stop it."
            }
            Stop-Process -Id $processId -Force
        }
    }
}
Remove-Item -LiteralPath $PidFile -Force
Write-Host "Enterprise Insight was stopped."
