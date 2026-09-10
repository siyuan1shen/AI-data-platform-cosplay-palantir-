[CmdletBinding()]
param(
    [ValidateRange(1, 65535)]
    [int]$BackendPort = 8012,

    [switch]$SkipOpen,

    [switch]$RefreshDependencies,

    [string]$DataDirectory,

    [string]$RuntimeDirectory,

    [string]$LogDirectory
)

Set-StrictMode -Version Latest
$ErrorActionPreference = "Stop"

$ProjectRoot = (Resolve-Path -LiteralPath (Join-Path $PSScriptRoot "..")).Path
$BackendRoot = Join-Path $ProjectRoot "backend"
$FrontendRoot = Join-Path $ProjectRoot "frontend"
$Python = Join-Path $BackendRoot ".venv\Scripts\python.exe"
$LocalData = [Environment]::GetFolderPath("LocalApplicationData")
if ([string]::IsNullOrWhiteSpace($LocalData)) {
    $LocalData = Join-Path $ProjectRoot ".local-runtime"
}
$ApplicationRoot = Join-Path $LocalData "EnterpriseInsight"
$DataDir = if ([string]::IsNullOrWhiteSpace($DataDirectory)) {
    Join-Path $ApplicationRoot "data"
} else {
    [System.IO.Path]::GetFullPath($DataDirectory)
}
$RuntimeDir = if ([string]::IsNullOrWhiteSpace($RuntimeDirectory)) {
    Join-Path $ApplicationRoot "runtime"
} else {
    [System.IO.Path]::GetFullPath($RuntimeDirectory)
}
$LogDir = if ([string]::IsNullOrWhiteSpace($LogDirectory)) {
    Join-Path $ApplicationRoot "logs"
} else {
    [System.IO.Path]::GetFullPath($LogDirectory)
}
$PidFile = Join-Path $RuntimeDir "v3-processes.json"

foreach ($directory in @($DataDir, $RuntimeDir, $LogDir)) {
    New-Item -ItemType Directory -Path $directory -Force | Out-Null
}

function Test-HttpOk {
    param([Parameter(Mandatory = $true)][string]$Uri)
    try {
        $response = Invoke-WebRequest -Uri $Uri -UseBasicParsing -TimeoutSec 2
        return $response.StatusCode -ge 200 -and $response.StatusCode -lt 400
    }
    catch {
        return $false
    }
}

function Wait-HttpOk {
    param(
        [Parameter(Mandatory = $true)][string]$Uri,
        [Parameter(Mandatory = $true)][System.Diagnostics.Process]$Process,
        [Parameter(Mandatory = $true)][string]$LogPath
    )
    $deadline = (Get-Date).AddSeconds(60)
    while ((Get-Date) -lt $deadline) {
        if ($Process.HasExited) {
            throw "Process exited during startup. See log: $LogPath"
        }
        if (Test-HttpOk -Uri $Uri) {
            return
        }
        Start-Sleep -Milliseconds 500
    }
    throw "Service was not ready within 60 seconds. See log: $LogPath"
}

if (-not (Test-Path -LiteralPath $Python -PathType Leaf)) {
    $SystemPython = Get-Command "python.exe" -ErrorAction SilentlyContinue
    if ($null -eq $SystemPython) {
        throw "Python 3.12 was not found."
    }
    $SystemPythonPath = $SystemPython.Source
    $version = & $SystemPythonPath -c 'import sys; print(sys.version_info.major, sys.version_info.minor, sep=chr(46))'
    if ($LASTEXITCODE -ne 0 -or $version.Trim() -ne "3.12") {
        throw "Python 3.12 is required. Current version: $version"
    }
    & $SystemPythonPath -m venv (Join-Path $BackendRoot ".venv")
    if ($LASTEXITCODE -ne 0) {
        throw "Failed to create the backend virtual environment."
    }
}

$DependencyMarker = Join-Path $BackendRoot ".venv\.enterprise-insight-ready"
$BackendProjectFile = Join-Path $BackendRoot "pyproject.toml"
$NeedBackendInstall = $RefreshDependencies -or -not (Test-Path -LiteralPath $DependencyMarker)
if (-not $NeedBackendInstall) {
    $NeedBackendInstall = (Get-Item $BackendProjectFile).LastWriteTimeUtc -gt `
        (Get-Item $DependencyMarker).LastWriteTimeUtc
}
if ($NeedBackendInstall) {
    & $Python -m pip install --disable-pip-version-check -e $BackendRoot
    if ($LASTEXITCODE -ne 0) {
        throw "Failed to install backend dependencies."
    }
    New-Item -ItemType File -Path $DependencyMarker -Force | Out-Null
}

$Npm = Get-Command "npm.cmd" -ErrorAction SilentlyContinue
if ($null -eq $Npm) {
    throw "Node.js/npm was not found."
}
if ($RefreshDependencies -or -not (Test-Path -LiteralPath (Join-Path $FrontendRoot "node_modules"))) {
    Push-Location $FrontendRoot
    try {
        & $Npm.Source ci
        if ($LASTEXITCODE -ne 0) {
            throw "Failed to install frontend dependencies."
        }
    }
    finally {
        Pop-Location
    }
}

& $Python (Join-Path $BackendRoot "scripts\export_openapi.py")
if ($LASTEXITCODE -ne 0) {
    throw "Failed to refresh the OpenAPI contract."
}
Push-Location $FrontendRoot
try {
    & $Npm.Source run build
    if ($LASTEXITCODE -ne 0) {
        throw "Failed to build the frontend."
    }
}
finally {
    Pop-Location
}

$BackendHealth = "http://127.0.0.1:$BackendPort/api/v3/health"
$ApplicationUrl = "http://127.0.0.1:$BackendPort/developer/"
$timestamp = (Get-Date).ToUniversalTime().ToString("yyyyMMddTHHmmssZ")
$processInfo = @{}
if (Test-HttpOk -Uri $BackendHealth) {
    if (-not (Test-Path -LiteralPath $PidFile -PathType Leaf)) {
        throw "Port $BackendPort is already occupied by an unmanaged service. Free the port and retry."
    }
    & (Join-Path $PSScriptRoot "stop-v3.ps1") -RuntimeDirectory $RuntimeDir
}

$env:EI_BACKEND_DATA_DIR = $DataDir
$env:EI_BACKEND_FRONTEND_DIST_DIR = Join-Path $FrontendRoot "dist"
$env:EI_BACKEND_BUILD_ID = $timestamp
$backendOut = Join-Path $LogDir "backend-$timestamp.out.log"
$backendErr = Join-Path $LogDir "backend-$timestamp.err.log"
$backend = Start-Process -FilePath $Python `
    -ArgumentList @(
        "-m", "uvicorn", "enterprise_insight_backend.app:create_app", "--factory",
        "--host", "127.0.0.1", "--port", $BackendPort.ToString()
    ) `
    -WorkingDirectory $BackendRoot `
    -WindowStyle Hidden `
    -RedirectStandardOutput $backendOut `
    -RedirectStandardError $backendErr `
    -PassThru
Wait-HttpOk -Uri $BackendHealth -Process $backend -LogPath $backendErr
$processInfo["backend_pid"] = $backend.Id
$processInfo["started_at"] = (Get-Date).ToUniversalTime().ToString("o")
$processInfo["build_id"] = $timestamp
$processInfo["health_url"] = $BackendHealth
$processInfo["app_url"] = $ApplicationUrl
$processInfo | ConvertTo-Json | Set-Content -LiteralPath $PidFile -Encoding UTF8

Write-Host "Enterprise Insight is ready: $ApplicationUrl"
Write-Host "Build: $timestamp"
Write-Host "Local data directory: $DataDir"
if (-not $SkipOpen) {
    Start-Process $ApplicationUrl
}
