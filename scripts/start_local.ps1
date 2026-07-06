param(
    [int]$Port = 8765
)

$ErrorActionPreference = "Stop"
[Console]::OutputEncoding = [System.Text.UTF8Encoding]::new()

$AppRoot = (Resolve-Path (Join-Path $PSScriptRoot "..")).Path
Set-Location $AppRoot

function Get-LocalPython {
    $pyLauncher = Get-Command py -ErrorAction SilentlyContinue
    if ($pyLauncher) {
        return @{
            Command = $pyLauncher.Source
            Args = @("-3")
        }
    }

    $python = Get-Command python -ErrorAction SilentlyContinue
    if ($python) {
        return @{
            Command = $python.Source
            Args = @()
        }
    }

    throw "Python 3.10 이상이 필요합니다. Python을 설치한 뒤 다시 실행해 주세요."
}

$python = Get-LocalPython
$serverUrl = "http://127.0.0.1:$Port/"

Write-Host "배움성장알리미 로컬 프로그램을 시작합니다."
Write-Host "주소: $serverUrl"
Write-Host "종료하려면 이 창에서 Ctrl+C를 누르세요."
Write-Host ""

Start-Process $serverUrl

& $python.Command @($python.Args + @((Join-Path $AppRoot "run_server.py"), "--port", "$Port"))
$exitCode = $LASTEXITCODE
if ($exitCode -ne 0) {
    Write-Host ""
    Write-Host "서버가 오류로 종료되었습니다. 위 메시지를 확인해 주세요."
    Read-Host "창을 닫으려면 Enter를 누르세요"
}
exit $exitCode
