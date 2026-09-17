param(
    [int]$Port = 8022,
    [switch]$NoOpen
)

$ErrorActionPreference = "Stop"
$root = (Resolve-Path (Join-Path $PSScriptRoot "..")).Path
$graph = Join-Path $root ".ua\knowledge-graph.json"
$viewer = Join-Path $root ".tools\understand-anything\understand-anything-plugin\packages\viewer\bin\viewer.mjs"

if (-not (Test-Path $graph)) {
    throw "没有找到知识图谱。请先运行：node scripts/generate-understand-anything-graph.mjs"
}
if (-not (Test-Path $viewer)) {
    throw "没有找到本地 Understand-Anything 查看器。请先按 docs/understand-anything-integration.md 安装。"
}

$viewerArgs = @($viewer, $root, "--port", "$Port")
if ($NoOpen) { $viewerArgs += "--no-open" }
Write-Host "正在打开 palantir 代码知识图谱： http://127.0.0.1:$Port/"
& node @viewerArgs
