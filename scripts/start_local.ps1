param(
    [int]$Port = 8765
)

$ErrorActionPreference = "Stop"
[Console]::OutputEncoding = [System.Text.UTF8Encoding]::new()

$AppRoot = (Resolve-Path (Join-Path $PSScriptRoot "..")).Path
Set-Location $AppRoot

function Get-LocalPython {
    $python = Get-Command python.exe -ErrorAction SilentlyContinue
    if (-not $python) {
        $python = Get-Command python -ErrorAction SilentlyContinue
    }
    if ($python) {
        return @{
            Command = $python.Source
            Args = @()
        }
    }

    $pyLauncher = Get-Command py.exe -ErrorAction SilentlyContinue
    if ($pyLauncher -and $pyLauncher.Source.StartsWith($env:WINDIR, [System.StringComparison]::OrdinalIgnoreCase)) {
        return @{
            Command = $pyLauncher.Source
            Args = @("-3")
        }
    }

    throw "Python 3.10 이상이 필요합니다. Python을 설치한 뒤 다시 실행해 주세요."
}

$python = Get-LocalPython

function Quote-Argument {
    param(
        [string]$Value
    )

    return '"' + $Value.Replace('"', '\"') + '"'
}

function Test-ServerReady {
    param(
        [string]$Url
    )

    $client = $null
    $connection = $null
    try {
        $uri = [Uri]$Url
        $client = New-Object System.Net.Sockets.TcpClient
        $connection = $client.BeginConnect($uri.Host, $uri.Port, $null, $null)
        if (-not $connection.AsyncWaitHandle.WaitOne(250)) {
            return $false
        }
        $client.EndConnect($connection)
        return $true
    }
    catch {
        return $false
    }
    finally {
        if ($connection) {
            $connection.AsyncWaitHandle.Close()
        }
        if ($client) {
            $client.Close()
        }
    }
}

function New-ServerUrl {
    param(
        [int]$ServerPort
    )

    return "http://127.0.0.1:$ServerPort/"
}

function Normalize-PathForCompare {
    param(
        [string]$Value
    )

    try {
        return (Resolve-Path -LiteralPath $Value).Path.TrimEnd("\").ToLowerInvariant()
    }
    catch {
        return [System.IO.Path]::GetFullPath($Value).TrimEnd("\").ToLowerInvariant()
    }
}

function Test-SamePath {
    param(
        [string]$Left,
        [string]$Right
    )

    if (-not $Left -or -not $Right) {
        return $false
    }
    return (Normalize-PathForCompare $Left) -eq (Normalize-PathForCompare $Right)
}

function Get-ExistingAppRoot {
    param(
        [string]$Url
    )

    try {
        $response = Invoke-WebRequest -UseBasicParsing -Uri "$Url/api/app-info" -TimeoutSec 1
        $payload = $response.Content | ConvertFrom-Json
        if ($payload.ok -and $payload.app_root) {
            return [string]$payload.app_root
        }
    }
    catch {
        return $null
    }
    return $null
}

function Find-LaunchTarget {
    param(
        [int]$PreferredPort,
        [string]$CurrentAppRoot
    )

    for ($candidatePort = $PreferredPort; $candidatePort -lt ($PreferredPort + 50); $candidatePort++) {
        $candidateUrl = New-ServerUrl -ServerPort $candidatePort
        if (-not (Test-ServerReady -Url $candidateUrl)) {
            return @{
                Port = $candidatePort
                Url = $candidateUrl
                UseExisting = $false
            }
        }

        $existingRoot = Get-ExistingAppRoot -Url $candidateUrl
        if ($existingRoot -and (Test-SamePath -Left $existingRoot -Right $CurrentAppRoot)) {
            return @{
                Port = $candidatePort
                Url = $candidateUrl
                UseExisting = $true
            }
        }
    }

    throw "사용 가능한 로컬 포트를 찾지 못했습니다. 실행 중인 배움성장알리미 창을 닫고 다시 실행해 주세요."
}

function Wait-ServerReady {
    param(
        [string]$Url,
        [System.Diagnostics.Process]$Process
    )

    for ($i = 0; $i -lt 50; $i++) {
        if (Test-ServerReady -Url $Url) {
            return $true
        }
        if ($Process.HasExited) {
            return $false
        }
        Start-Sleep -Milliseconds 200
    }
    return $false
}

$requestedPort = $Port
$launchTarget = Find-LaunchTarget -PreferredPort $requestedPort -CurrentAppRoot $AppRoot
$Port = [int]$launchTarget.Port
$serverUrl = [string]$launchTarget.Url

Write-Host "배움성장알리미 로컬 프로그램을 시작합니다."
Write-Host "주소: $serverUrl"
if ($Port -ne $requestedPort) {
    Write-Host "기본 포트 $requestedPort 는 다른 서버가 사용 중이라 $Port 포트로 실행합니다."
}
Write-Host "종료하려면 이 창에서 Ctrl+C를 누르세요."
Write-Host ""

if ($launchTarget.UseExisting) {
    Write-Host "현재 폴더의 로컬 서버가 이미 실행 중입니다. 브라우저를 엽니다."
    Start-Process $serverUrl
    exit 0
}

$serverArgs = @($python.Args + @((Join-Path $AppRoot "run_server.py"), "--port", "$Port"))
$serverArgumentLine = ($serverArgs | ForEach-Object { Quote-Argument $_ }) -join " "
$serverProcess = Start-Process -FilePath $python.Command -ArgumentList $serverArgumentLine -WorkingDirectory $AppRoot -NoNewWindow -PassThru

try {
    if (-not (Wait-ServerReady -Url $serverUrl -Process $serverProcess)) {
        $exitCode = if ($serverProcess.HasExited) { $serverProcess.ExitCode } else { 1 }
        Write-Host ""
        Write-Host "로컬 서버가 준비되지 않아 브라우저를 열지 못했습니다."
        Write-Host "포트 $Port 사용 중 여부와 Python 설치 상태를 확인해 주세요."
        Read-Host "창을 닫으려면 Enter를 누르세요"
        exit $exitCode
    }

    Start-Process $serverUrl
    Wait-Process -Id $serverProcess.Id
    exit $serverProcess.ExitCode
}
finally {
    if ($serverProcess -and -not $serverProcess.HasExited) {
        Stop-Process -Id $serverProcess.Id -Force
    }
}
