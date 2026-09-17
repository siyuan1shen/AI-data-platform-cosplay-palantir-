param(
    [string]$OutputDir = "dist",
    [switch]$Clean
)

$ErrorActionPreference = "Stop"
$scriptDir = Split-Path -Parent $MyInvocation.MyCommand.Path
$entryPoint = Join-Path $scriptDir "independent_work_collector.py"
$outputPath = Join-Path $scriptDir $OutputDir

if (-not (Test-Path -LiteralPath $entryPoint)) {
    throw "Collector entry point not found: $entryPoint"
}

function Invoke-PyInstaller {
    param([string]$Executable, [string[]]$Arguments)
    & $Executable @Arguments
    if ($LASTEXITCODE -ne 0) {
        throw "PyInstaller build failed. Exit code: $LASTEXITCODE"
    }
}

$pyinstaller = Get-Command pyinstaller -ErrorAction SilentlyContinue
$python = Get-Command python -ErrorAction SilentlyContinue

if (-not $pyinstaller -and $python) {
    $previousErrorAction = $ErrorActionPreference
    $ErrorActionPreference = "Continue"
    & $python.Source -c "import PyInstaller" 2>$null
    $moduleExitCode = $LASTEXITCODE
    $ErrorActionPreference = $previousErrorAction
    if ($moduleExitCode -eq 0) {
        $pyinstaller = [pscustomobject]@{ Source = $python.Source; UseModule = $true }
    }
}

if (-not $pyinstaller) {
    Write-Host @"
PyInstaller was not found; a Python-free Windows collector cannot be built.
Install Python 3.10+ first, then run:
  python -m pip install pyinstaller
Then run again:
  powershell -ExecutionPolicy Bypass -File .\build_collector.ps1
"@
    exit 2
}

if ($Clean -and (Test-Path -LiteralPath $outputPath)) {
    Remove-Item -LiteralPath $outputPath -Recurse -Force
}

$arguments = @(
    "--noconfirm",
    "--clean",
    "--onefile",
    "--name", "EnterpriseInsightCollector",
    "--distpath", $outputPath,
    "--workpath", (Join-Path $scriptDir "build"),
    "--specpath", (Join-Path $scriptDir "build"),
    $entryPoint
)

if ($pyinstaller.UseModule) {
    Invoke-PyInstaller -Executable $pyinstaller.Source -Arguments (@("-m", "PyInstaller") + $arguments)
} else {
    Invoke-PyInstaller -Executable $pyinstaller.Source -Arguments $arguments
}

$example = Join-Path $scriptDir "collector_config.example.json"
Copy-Item -LiteralPath $example -Destination (Join-Path $outputPath "collector_config.example.json") -Force
$exePath = Join-Path $outputPath "EnterpriseInsightCollector.exe"
Write-Host "Build complete: $exePath"
Write-Host "Copy the example config and set source_id, employee_key, project_id, and upload_url."
