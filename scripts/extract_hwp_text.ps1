param(
    [Parameter(Mandatory = $true)]
    [string]$InputPath,

    [Parameter(Mandatory = $true)]
    [string]$OutputPath
)

$ErrorActionPreference = "Stop"

$resolvedInput = (Resolve-Path -LiteralPath $InputPath).Path
$outputDirectory = Split-Path -Parent $OutputPath
if ($outputDirectory -and -not (Test-Path -LiteralPath $outputDirectory)) {
    New-Item -ItemType Directory -Path $outputDirectory | Out-Null
}

$hwp = $null
try {
    $hwp = New-Object -ComObject HWPFrame.HwpObject

    try {
        $hwp.XHwpWindows.Item(0).Visible = $false
    } catch {
        # Some HWP versions do not expose the visible flag before opening.
    }

    try {
        $hwp.RegisterModule("FilePathCheckDLL", "FilePathCheckerModule") | Out-Null
    } catch {
        # Continue; some environments already trust local file paths.
    }

    $opened = $hwp.Open($resolvedInput, "", "")
    if (-not $opened) {
        throw "HWP open failed: $resolvedInput"
    }

    $saved = $hwp.SaveAs($OutputPath, "TEXT", "")
    if (-not $saved) {
        throw "HWP text export failed: $OutputPath"
    }
} finally {
    if ($hwp -ne $null) {
        try {
            $hwp.Quit()
        } catch {
        }
    }
}

Write-Output $OutputPath
