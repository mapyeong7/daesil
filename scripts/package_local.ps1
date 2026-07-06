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
$launcherPath = Join-Path $packageDir "HwpAlimi.exe"

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

$cscCandidates = @(
    (Join-Path $env:WINDIR "Microsoft.NET\Framework64\v4.0.30319\csc.exe"),
    (Join-Path $env:WINDIR "Microsoft.NET\Framework\v4.0.30319\csc.exe")
)
$cscPath = $cscCandidates | Where-Object { Test-Path -LiteralPath $_ } | Select-Object -First 1
if (-not $cscPath) {
    throw "Windows C# 컴파일러(csc.exe)를 찾지 못해 HwpAlimi.exe를 만들 수 없습니다."
}

& $cscPath /nologo /target:exe /out:$launcherPath (Join-Path $RepoRoot "launcher\HwpAlimiLauncher.cs")
if ($LASTEXITCODE -ne 0) {
    throw "HwpAlimi.exe 생성에 실패했습니다."
}

Compress-Archive -Path (Join-Path $packageDir "*") -DestinationPath $zipPath -CompressionLevel Optimal

Write-Host "패키지 폴더: $packageDir"
Write-Host "ZIP 파일: $zipPath"
Write-Host "실행 파일: $launcherPath"
