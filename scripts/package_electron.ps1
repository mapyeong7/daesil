param(
    [string]$OutputRoot,
    [switch]$SkipInstall,
    [switch]$BundlePython,
    [string]$PythonRoot,
    [switch]$CreateZip
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

function Assert-PathInside {
    param(
        [Parameter(Mandatory = $true)][string]$ChildPath,
        [Parameter(Mandatory = $true)][string]$ParentPath
    )

    $childFull = [System.IO.Path]::GetFullPath($ChildPath).TrimEnd([System.IO.Path]::DirectorySeparatorChar, [System.IO.Path]::AltDirectorySeparatorChar)
    $parentFull = [System.IO.Path]::GetFullPath($ParentPath).TrimEnd([System.IO.Path]::DirectorySeparatorChar, [System.IO.Path]::AltDirectorySeparatorChar)

    if ($childFull.Equals($parentFull, [System.StringComparison]::OrdinalIgnoreCase)) {
        return
    }
    if (-not $childFull.StartsWith($parentFull + [System.IO.Path]::DirectorySeparatorChar, [System.StringComparison]::OrdinalIgnoreCase)) {
        throw "Refusing to modify a path outside the package folder: $ChildPath"
    }
}

function Resolve-PythonRoot {
    param([string]$ExplicitRoot)

    if ($ExplicitRoot) {
        return (Resolve-Path -LiteralPath $ExplicitRoot).Path
    }

    $pythonCommand = Get-Command python -ErrorAction SilentlyContinue
    if (-not $pythonCommand) {
        $pythonCommand = Get-Command py -ErrorAction SilentlyContinue
    }
    if (-not $pythonCommand) {
        throw "Python is required on this build PC when using -BundlePython."
    }

    if ($pythonCommand.Name -ieq "py.exe") {
        $resolved = & $pythonCommand.Source -3 -c "import sys; print(sys.prefix)"
    }
    else {
        $resolved = & $pythonCommand.Source -c "import sys; print(sys.prefix)"
    }
    if ($LASTEXITCODE -ne 0 -or -not $resolved) {
        throw "Could not locate the current Python runtime root."
    }

    return (Resolve-Path -LiteralPath $resolved.Trim()).Path
}

function Copy-PythonRuntime {
    param(
        [Parameter(Mandatory = $true)][string]$SourceRoot,
        [Parameter(Mandatory = $true)][string]$AppRoot
    )

    $pythonExe = Join-Path $SourceRoot "python.exe"
    if (-not (Test-Path -LiteralPath $pythonExe)) {
        throw "python.exe was not found in PythonRoot: $SourceRoot"
    }

    $targetRoot = Join-Path $AppRoot "python"
    Assert-PathInside -ChildPath $targetRoot -ParentPath $AppRoot

    if (Test-Path -LiteralPath $targetRoot) {
        Remove-Item -LiteralPath $targetRoot -Recurse -Force
    }
    New-Item -ItemType Directory -Force -Path $targetRoot | Out-Null

    $skipTopLevel = @("Doc", "include", "libs", "Scripts", "tcl")
    Get-ChildItem -LiteralPath $SourceRoot | Where-Object { $skipTopLevel -notcontains $_.Name } | ForEach-Object {
        Copy-Item -LiteralPath $_.FullName -Destination $targetRoot -Recurse -Force
    }

    $runtimeTestDirs = @(
        (Join-Path $targetRoot "Lib\test"),
        (Join-Path $targetRoot "Lib\idlelib\idle_test"),
        (Join-Path $targetRoot "Lib\tkinter\test"),
        (Join-Path $targetRoot "Lib\unittest\test")
    )
    foreach ($dir in $runtimeTestDirs) {
        if (Test-Path -LiteralPath $dir) {
            Assert-PathInside -ChildPath $dir -ParentPath $targetRoot
            Remove-Item -LiteralPath $dir -Recurse -Force
        }
    }

    Get-ChildItem -LiteralPath $targetRoot -Recurse -Directory -Filter "__pycache__" | ForEach-Object {
        Assert-PathInside -ChildPath $_.FullName -ParentPath $targetRoot
        Remove-Item -LiteralPath $_.FullName -Recurse -Force
    }

    Write-Host "Bundled Python runtime: $targetRoot"
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

$packageDir = Join-Path $OutputRoot "$appName-win32-x64"
if (-not (Test-Path -LiteralPath $packageDir)) {
    $packageDir = (Get-ChildItem -Path $OutputRoot -Directory | Sort-Object LastWriteTime -Descending | Select-Object -First 1).FullName
}

if ($BundlePython) {
    $resolvedPythonRoot = Resolve-PythonRoot -ExplicitRoot $PythonRoot
    $appRoot = Join-Path $packageDir "resources\app"
    Copy-PythonRuntime -SourceRoot $resolvedPythonRoot -AppRoot $appRoot
}

if ($CreateZip) {
    $zipPath = Join-Path $OutputRoot "$appName-win32-x64-python-included.zip"
    Assert-PathInside -ChildPath $zipPath -ParentPath $OutputRoot
    if (Test-Path -LiteralPath $zipPath) {
        Remove-Item -LiteralPath $zipPath -Force
    }
    Compress-Archive -LiteralPath $packageDir -DestinationPath $zipPath -Force
    Write-Host "Zip package: $zipPath"
}

Write-Host "Electron package folder: $OutputRoot"
Write-Host "Executable: $packageDir\HwpAlimi.exe"
