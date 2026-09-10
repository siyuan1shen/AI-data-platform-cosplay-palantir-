[CmdletBinding()]
param(
    [switch]$SkipFrontendBuild,
    [switch]$RequireRuntime
)

Set-StrictMode -Version Latest
$ErrorActionPreference = "Stop"

$ProjectRoot = (Resolve-Path -LiteralPath (Join-Path $PSScriptRoot "..")).Path
$BackendRoot = Join-Path $ProjectRoot "backend"
$FrontendRoot = Join-Path $ProjectRoot "frontend"
$PytestBaseTemp = Join-Path ([System.IO.Path]::GetTempPath()) (
    "enterprise-insight-pytest-" + [Guid]::NewGuid().ToString("N")
)
New-Item -ItemType Directory -Path $PytestBaseTemp -Force | Out-Null
$Python = Get-Command "python.exe" -ErrorAction SilentlyContinue
$Npm = Get-Command "npm.cmd" -ErrorAction SilentlyContinue

function Invoke-Checked {
    param(
        [Parameter(Mandatory = $true)][string]$FilePath,
        [Parameter(Mandatory = $true)][string[]]$ArgumentList,
        [Parameter(Mandatory = $true)][string]$Label,
        [Parameter(Mandatory = $true)][string]$WorkingDirectory
    )
    Write-Host "`n== $Label =="
    Push-Location $WorkingDirectory
    try {
        & $FilePath @ArgumentList
        if ($LASTEXITCODE -ne 0) {
            throw "$Label failed with exit code $LASTEXITCODE."
        }
    }
    finally {
        Pop-Location
    }
}

if ($null -eq $Python) {
    throw "python.exe was not found. Run scripts\doctor-v3.ps1 first."
}
if ($null -eq $Npm) {
    throw "npm.cmd was not found. Run scripts\doctor-v3.ps1 first."
}

Invoke-Checked -FilePath "powershell.exe" `
    -ArgumentList @("-NoProfile", "-ExecutionPolicy", "Bypass", "-File", (Join-Path $PSScriptRoot "doctor-v3.ps1"), "-Mode", "Local") `
    -Label "environment" -WorkingDirectory $ProjectRoot
Invoke-Checked -FilePath $Python.Source `
    -ArgumentList @("-m", "pytest", "backend\tests", "-q", "-p", "no:cacheprovider", "--basetemp", $PytestBaseTemp, "-r", "a") `
    -Label "backend tests" -WorkingDirectory $ProjectRoot
Invoke-Checked -FilePath $Python.Source `
    -ArgumentList @("-m", "ruff", "check", "--no-cache", "backend\src", "backend\tests") `
    -Label "backend lint" -WorkingDirectory $ProjectRoot
Invoke-Checked -FilePath $Npm.Source `
    -ArgumentList @("test", "--", "--run") `
    -Label "frontend tests" -WorkingDirectory $FrontendRoot
Invoke-Checked -FilePath $Npm.Source `
    -ArgumentList @("run", "typecheck") `
    -Label "frontend typecheck" -WorkingDirectory $FrontendRoot
if (-not $SkipFrontendBuild) {
    Invoke-Checked -FilePath $Npm.Source `
        -ArgumentList @("run", "build") `
        -Label "frontend build" -WorkingDirectory $FrontendRoot
}

$healthUri = "http://127.0.0.1:8012/api/v3/health"
$health = Invoke-RestMethod -Uri $healthUri -TimeoutSec 3 -ErrorAction SilentlyContinue
if ($null -eq $health -or $health.status -ne "ok" -or $health.database -ne "ready" -or $health.frontend -ne "ready") {
    if ($RequireRuntime) {
        throw "Runtime health check failed: $healthUri"
    }
    Write-Warning "No healthy service detected on port 8012; code gates completed. Use -RequireRuntime to fail when the service is unavailable."
} else {
    Write-Host "`n== runtime =="
    Write-Host ("PASS`t{0} build={1} schema={2}" -f $healthUri, $health.build_id, $health.schema_revision)
}

Write-Host "`nV3 local verification passed."
