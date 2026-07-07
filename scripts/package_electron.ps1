param(
    [string]$OutputRoot,
    [switch]$SkipInstall
)

$ErrorActionPreference = "Stop"
[Console]::OutputEncoding = [System.Text.UTF8Encoding]::new()

$RepoRoot = (Resolve-Path (Join-Path $PSScriptRoot "..")).Path
if (-not $OutputRoot) {
    $OutputRoot = Join-Path $RepoRoot "dist\electron"
}

if (-not (Get-Command npm -ErrorAction SilentlyContinue)) {
    throw "Node.js and npm are required to package the Electron app."
}

Set-Location $RepoRoot

if (-not $SkipInstall -and -not (Test-Path -LiteralPath (Join-Path $RepoRoot "node_modules\electron"))) {
    npm install
    if ($LASTEXITCODE -ne 0) {
        throw "npm install failed."
    }
}

$packager = Join-Path $RepoRoot "node_modules\.bin\electron-packager.cmd"
if (-not (Test-Path -LiteralPath $packager)) {
    throw "electron-packager was not found. Run npm install first."
}

New-Item -ItemType Directory -Force -Path $OutputRoot | Out-Null

$ignoreArgs = @(
    "--ignore=^/\.git",
    "--ignore=^/dist",
    "--ignore=^/node_modules/\.cache",
    "--ignore=^/uploads",
    "--ignore=^/extracted",
    "--ignore=^/phase1_output",
    "--ignore=^/phase2_output",
    "--ignore=^/phase3_output",
    "--ignore=^/examples",
    "--ignore=^/tests",
    "--ignore=^/server_state\.json",
    "--ignore=^/server\.out\.log",
    "--ignore=^/server\.err\.log",
    "--ignore=^/excel_api_response\.json",
    "--ignore=^/handoff\.md"
)

$appName = "HwpAlimi"

& $packager $RepoRoot $appName `
    --platform=win32 `
    --arch=x64 `
    --no-asar `
    --out $OutputRoot `
    --overwrite `
    --executable-name "HwpAlimi" `
    @ignoreArgs

if ($LASTEXITCODE -ne 0) {
    throw "Electron packaging failed."
}

Write-Host "Electron package folder: $OutputRoot"
Get-ChildItem -Path $OutputRoot -Directory | Sort-Object LastWriteTime -Descending | Select-Object -First 1 |
    ForEach-Object {
        Write-Host "Executable: $($_.FullName)\HwpAlimi.exe"
    }
