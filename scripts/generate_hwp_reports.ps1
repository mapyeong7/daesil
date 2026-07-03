param(
    [Parameter(Mandatory = $true)]
    [string]$ManifestPath,

    [Parameter(Mandatory = $true)]
    [string]$OutputDir,

    [int]$Limit = 0
)

$ErrorActionPreference = "Stop"

function Get-SafeFileName([string]$Name) {
    $safe = $Name -replace '[<>:"/\\|?*\x00-\x1f]', '_'
    $safe = $safe.Trim()
    if ([string]::IsNullOrWhiteSpace($safe)) {
        return "student"
    }
    return $safe
}

function Get-UniqueOutputPath {
    param(
        [Parameter(Mandatory = $true)]
        [string]$OutputDir,

        [Parameter(Mandatory = $true)]
        [string]$BaseName,

        [Parameter(Mandatory = $true)]
        [hashtable]$UsedNames
    )

    $counter = 1
    while ($true) {
        if ($counter -eq 1) {
            $fileName = "{0}.hwp" -f $BaseName
        } else {
            $fileName = "{0}_{1}.hwp" -f $BaseName, $counter
        }
        $key = $fileName.ToLowerInvariant()
        if (-not $UsedNames.ContainsKey($key)) {
            $UsedNames[$key] = $true
            return (Join-Path $OutputDir $fileName)
        }
        $counter += 1
    }
}

function Invoke-HwpAllReplace {
    param(
        [Parameter(Mandatory = $true)]
        $Hwp,

        [Parameter(Mandatory = $true)]
        [string]$Find,

        [Parameter(Mandatory = $true)]
        [string]$Replace
    )

    if ([string]::IsNullOrEmpty($Find)) {
        return $false
    }
    try {
        $Hwp.HAction.Run("MoveDocBegin") | Out-Null
    } catch {
    }
    $set = $Hwp.HParameterSet.HFindReplace
    $Hwp.HAction.GetDefault("AllReplace", $set.HSet) | Out-Null
    $set.FindString = $Find
    $set.ReplaceString = $Replace
    $set.IgnoreMessage = 1
    $set.Direction = $Hwp.FindDir("AllDoc")
    $set.MatchCase = 0
    $set.WholeWordOnly = 0
    $set.UseWildCards = 0
    $set.SeveralWords = 0
    $set.AllWordForms = 0
    $set.IgnoreFindString = 0
    $set.IgnoreReplaceString = 0
    $set.FindType = 1
    $set.ReplaceMode = 1
    return $Hwp.HAction.Execute("AllReplace", $set.HSet)
}

$resolvedManifest = (Resolve-Path -LiteralPath $ManifestPath).Path
$manifest = Get-Content -Encoding UTF8 -Raw -LiteralPath $resolvedManifest | ConvertFrom-Json
$manifestPlaceholders = @()
if (($manifest.PSObject.Properties.Name -contains "student_placeholders") -and $manifest.student_placeholders) {
    foreach ($placeholder in @($manifest.student_placeholders)) {
        if ($placeholder.PSObject.Properties.Name -contains "find" -and -not [string]::IsNullOrWhiteSpace([string]$placeholder.find)) {
            $manifestPlaceholders += $placeholder
        }
    }
}

if (-not $manifest.ready) {
    $messages = @($manifest.blocking_issues | ForEach-Object { $_.message }) -join "`n - "
    throw "Phase 3 manifest is not ready. Resolve blocking issues first.`n - $messages"
}

$sourceHwp = (Resolve-Path -LiteralPath $manifest.source_hwp).Path
if (-not (Test-Path -LiteralPath $OutputDir)) {
    New-Item -ItemType Directory -Path $OutputDir | Out-Null
}
$resolvedOutputDir = (Resolve-Path -LiteralPath $OutputDir).Path

$students = @($manifest.students)
if ($Limit -gt 0) {
    $students = @($students | Select-Object -First $Limit)
}

$created = New-Object System.Collections.Generic.List[string]
$usedOutputNames = @{}

foreach ($student in $students) {
    $number = [string]$student.number
    $name = [string]$student.name
    $safeBase = Get-SafeFileName ("{0:D2}_{1}" -f ([int]($number -as [int])), $name)
    if (-not ($number -as [int])) {
        $safeBase = Get-SafeFileName ("{0}_{1}" -f $number, $name)
    }
    $outputPath = Get-UniqueOutputPath -OutputDir $resolvedOutputDir -BaseName $safeBase -UsedNames $usedOutputNames

    $hwp = $null
    try {
        $hwp = New-Object -ComObject HWPFrame.HwpObject

        try {
            $hwp.XHwpWindows.Item(0).Visible = $false
        } catch {
        }

        try {
            $hwp.RegisterModule("FilePathCheckDLL", "FilePathCheckerModule") | Out-Null
        } catch {
        }

        $opened = $hwp.Open($sourceHwp, "", "")
        if (-not $opened) {
            throw "HWP open failed: $sourceHwp"
        }

        $wordNumber = [string][char]0xBC88
        $wordName = -join ([char]0xC774, [char]0xB984)
        $wordFullName = -join ([char]0xC131, [char]0xBA85)
        $wordStudentName = -join ([char]0xD559, [char]0xC0DD, [char]0xBA85)
        $circleToken = -join ([char]0x25CB, [char]0x25CB, [char]0x25CB)
        $replaceSucceeded = $false

        if ($manifestPlaceholders.Count -gt 0) {
            foreach ($placeholder in $manifestPlaceholders) {
                $find = [string]$placeholder.find
                $label = [string]$placeholder.label
                if ([string]::IsNullOrWhiteSpace($label)) {
                    $label = $wordName
                }
                $includesNumber = $false
                if ($placeholder.PSObject.Properties.Name -contains "includes_number") {
                    $includesNumber = [System.Convert]::ToBoolean($placeholder.includes_number)
                }
                if ($includesNumber) {
                    $replace = "{0}{1} {2}: {3}" -f $number, $wordNumber, $label, $name
                } else {
                    $replace = "{0}: {1}" -f $label, $name
                }
                if (Invoke-HwpAllReplace -Hwp $hwp -Find $find -Replace $replace) {
                    $replaceSucceeded = $true
                    break
                }
            }
        } else {
            $nameLabels = @($wordName, $wordFullName, $wordStudentName)
            $placeholderTokens = @("000", "OOO", $circleToken)
            foreach ($label in $nameLabels) {
                foreach ($token in $placeholderTokens) {
                    $studentLine = "{0}{1} {2}: {3}" -f $number, $wordNumber, $label, $name
                    $nameLine = "{0}: {1}" -f $label, $name
                    $numberFinds = @(
                        "0{0} {1}: {2}" -f $wordNumber, $label, $token,
                        "0{0} {1} : {2}" -f $wordNumber, $label, $token,
                        "0{0} {1}:{2}" -f $wordNumber, $label, $token,
                        "0{0} {1} :{2}" -f $wordNumber, $label, $token
                    )
                    $nameFinds = @(
                        "{0}: {1}" -f $label, $token,
                        "{0} : {1}" -f $label, $token,
                        "{0}:{1}" -f $label, $token,
                        "{0} :{1}" -f $label, $token
                    )
                    foreach ($find in $numberFinds) {
                        if (Invoke-HwpAllReplace -Hwp $hwp -Find $find -Replace $studentLine) {
                            $replaceSucceeded = $true
                            break
                        }
                    }
                    if ($replaceSucceeded) {
                        break
                    }
                    foreach ($find in $nameFinds) {
                        if (Invoke-HwpAllReplace -Hwp $hwp -Find $find -Replace $nameLine) {
                            $replaceSucceeded = $true
                            break
                        }
                    }
                    if ($replaceSucceeded) {
                        break
                    }
                }
                if ($replaceSucceeded) {
                    break
                }
            }
        }
        if (-not $replaceSucceeded) {
            throw "HWP student placeholder replace failed: $outputPath"
        }

        $saved = $hwp.SaveAs($outputPath, "HWP", "")
        if (-not $saved) {
            throw "HWP save failed: $outputPath"
        }
        $created.Add($outputPath) | Out-Null
    } finally {
        if ($hwp -ne $null) {
            try {
                $hwp.Quit()
            } catch {
            }
        }
    }
}

$created | ForEach-Object { Write-Output $_ }
