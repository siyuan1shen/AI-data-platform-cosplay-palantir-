[CmdletBinding()]
param(
    [ValidateSet("Local", "Container")]
    [string]$Mode = "Local"
)

Set-StrictMode -Version Latest
$ErrorActionPreference = "Stop"

$ProjectRoot = (Resolve-Path -LiteralPath (Join-Path $PSScriptRoot "..")).Path
$python = Get-Command "python.exe" -ErrorAction SilentlyContinue
$pythonOk = $false
if ($null -ne $python) {
    $pythonVersion = (& $python.Source -c 'import sys; print(sys.version_info.major, sys.version_info.minor, sep=chr(46))').Trim()
    $pythonOk = $pythonVersion -eq "3.12"
}
$node = Get-Command "node.exe" -ErrorAction SilentlyContinue
$nodeOk = $false
if ($null -ne $node) {
    $nodeVersion = (& $node.Source --version).Trim()
    $nodeOk = $nodeVersion -match '^v(2[4-9]|[3-9][0-9])\.'
}
$checks = @(
    @{ Name = "Backend project"; Ok = Test-Path (Join-Path $ProjectRoot "backend\pyproject.toml") },
    @{ Name = "Frontend project"; Ok = Test-Path (Join-Path $ProjectRoot "frontend\package.json") },
    @{ Name = "OpenAPI contract"; Ok = Test-Path (Join-Path $ProjectRoot "contracts\openapi.json") },
    @{ Name = "Python 3.12"; Ok = $pythonOk },
    @{ Name = "Node.js 24+"; Ok = $nodeOk },
    @{ Name = "npm"; Ok = $null -ne (Get-Command "npm.cmd" -ErrorAction SilentlyContinue) }
)

if ($Mode -eq "Container") {
    $docker = Get-Command "docker.exe" -ErrorAction SilentlyContinue
    $compose = Join-Path $ProjectRoot "docker-compose.v3.yml"
    $composeOk = $false
    if ($null -ne $docker -and (Test-Path -LiteralPath $compose)) {
        & $docker.Source compose -f $compose config --quiet
        $composeOk = $LASTEXITCODE -eq 0
    }
    $checks += @{ Name = "Docker Compose V3"; Ok = $composeOk }
}

$failed = $false
foreach ($check in $checks) {
    $label = if ($check.Ok) { "PASS" } else { "FAIL" }
    Write-Host "$label`t$($check.Name)"
    if (-not $check.Ok) {
        $failed = $true
    }
}
if ($failed) {
    exit 1
}
Write-Host "Runtime checks passed."
