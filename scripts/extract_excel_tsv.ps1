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

$excel = $null
$workbook = $null
try {
    $excel = New-Object -ComObject Excel.Application
    $excel.Visible = $false
    $excel.DisplayAlerts = $false

    $workbook = $excel.Workbooks.Open($resolvedInput)
    $worksheet = $workbook.Worksheets.Item(1)
    $range = $worksheet.UsedRange

    $rows = $range.Rows.Count
    $cols = $range.Columns.Count
    $lines = New-Object System.Collections.Generic.List[string]

    for ($r = 1; $r -le $rows; $r++) {
        $cells = @()
        for ($c = 1; $c -le $cols; $c++) {
            $value = [string]$worksheet.Cells.Item($r, $c).Text
            $value = ($value -replace "`t", " " -replace "`r?`n", " ").Trim()
            $cells += $value
        }
        $lines.Add(($cells -join "`t"))
    }

    [System.IO.File]::WriteAllLines($OutputPath, $lines, [System.Text.Encoding]::UTF8)
} finally {
    if ($workbook -ne $null) {
        try {
            $workbook.Close($false)
        } catch {
        }
    }
    if ($excel -ne $null) {
        try {
            $excel.Quit()
        } catch {
        }
    }
}

Write-Output $OutputPath
