param(
    [string]$OutputRoot
)

$ErrorActionPreference = "Stop"
[Console]::OutputEncoding = [System.Text.UTF8Encoding]::new()

$RepoRoot = (Resolve-Path (Join-Path $PSScriptRoot "..")).Path
if (-not $OutputRoot) {
    $OutputRoot = Join-Path $RepoRoot "dist"
}

$stamp = Get-Date -Format "yyyyMMdd-HHmmss"
$packageName = "hwp-alimi-local-$stamp"
$packageDir = Join-Path $OutputRoot $packageName
$zipPath = Join-Path $OutputRoot "$packageName.zip"

New-Item -ItemType Directory -Force -Path $packageDir | Out-Null

foreach ($file in @("index.html", "run_server.py", "README.md", "start_local.bat")) {
    Copy-Item -LiteralPath (Join-Path $RepoRoot $file) -Destination $packageDir
}

$srcPackageDir = Join-Path $packageDir "src\hwp_alimi"
New-Item -ItemType Directory -Force -Path $srcPackageDir | Out-Null
Get-ChildItem -Path (Join-Path $RepoRoot "src\hwp_alimi") -Filter "*.py" |
    Copy-Item -Destination $srcPackageDir

$scriptPackageDir = Join-Path $packageDir "scripts"
New-Item -ItemType Directory -Force -Path $scriptPackageDir | Out-Null
foreach ($script in @("generate_hwp_reports.ps1", "start_local.ps1")) {
    Copy-Item -LiteralPath (Join-Path $RepoRoot "scripts\$script") -Destination $scriptPackageDir
}

Compress-Archive -Path (Join-Path $packageDir "*") -DestinationPath $zipPath -CompressionLevel Optimal

Write-Host "패키지 폴더: $packageDir"
Write-Host "ZIP 파일: $zipPath"
