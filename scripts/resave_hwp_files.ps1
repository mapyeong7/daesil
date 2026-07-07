param(
    [Parameter(Mandatory = $true)]
    [string]$PathListJson
)

$ErrorActionPreference = "Stop"
[Console]::OutputEncoding = [System.Text.UTF8Encoding]::new()

function Get-ModuleCandidates {
    $moduleKeys = @(
        "HKCU:\Software\HNC\HwpAutomation\Modules",
        "HKCU:\Software\HNC\HwpCtrl\Modules",
        "HKCU:\Software\HNC\HwpUserAction\Modules"
    )
    $candidates = New-Object System.Collections.Generic.List[string]

    foreach ($key in $moduleKeys) {
        if (-not (Test-Path -LiteralPath $key)) {
            continue
        }
        $item = Get-ItemProperty -LiteralPath $key
        foreach ($property in $item.PSObject.Properties) {
            if ($property.Name -like "PS*") {
                continue
            }
            if ([string]::IsNullOrWhiteSpace([string]$property.Value)) {
                continue
            }
            if (-not $candidates.Contains($property.Name)) {
                $candidates.Add($property.Name) | Out-Null
            }
        }
    }

    foreach ($name in @("FilePathCheckerModuleExample_sm", "FilePathCheckerModule", "FilePathCheckerModuleExample")) {
        if (-not $candidates.Contains($name)) {
            $candidates.Add($name) | Out-Null
        }
    }
    return @($candidates)
}

function Sync-HwpAutomationModules {
    $sourceKey = "HKCU:\Software\HNC\HwpCtrl\Modules"
    $targetKey = "HKCU:\Software\HNC\HwpAutomation\Modules"
    if (-not (Test-Path -LiteralPath $sourceKey)) {
        return
    }
    if (-not (Test-Path -LiteralPath $targetKey)) {
        New-Item -Path $targetKey -Force | Out-Null
    }
    $item = Get-ItemProperty -LiteralPath $sourceKey
    foreach ($property in $item.PSObject.Properties) {
        if ($property.Name -like "PS*") {
            continue
        }
        $value = [string]$property.Value
        if ([string]::IsNullOrWhiteSpace($value) -or -not (Test-Path -LiteralPath $value)) {
            continue
        }
        New-ItemProperty -LiteralPath $targetKey -Name $property.Name -Value $value -PropertyType String -Force | Out-Null
    }
}

function Register-HwpFilePathChecker {
    param(
        [Parameter(Mandatory = $true)]
        $Hwp
    )

    Sync-HwpAutomationModules
    foreach ($candidate in Get-ModuleCandidates) {
        try {
            if ($Hwp.RegisterModule("FilePathCheckDLL", $candidate)) {
                return $candidate
            }
        } catch {
        }
    }
    throw "HWP FilePathCheck security module registration failed."
}

function Invoke-HwpAllReplace {
    param(
        [Parameter(Mandatory = $true)]
        $Hwp,

        [Parameter(Mandatory = $true)]
        [string]$Find,

        [AllowEmptyString()]
        [string]$Replace = ""
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

function Close-HwpObject {
    param($Hwp)

    if ($Hwp -eq $null) {
        return
    }
    try {
        $Hwp.Quit()
    } catch {
    }
    try {
        [System.Runtime.InteropServices.Marshal]::ReleaseComObject($Hwp) | Out-Null
    } catch {
    }
    [System.GC]::Collect()
    [System.GC]::WaitForPendingFinalizers()
}

function Invoke-HwpFinalizeOne {
    param(
        [Parameter(Mandatory = $true)]
        [string]$ResolvedPath,

        [object[]]$Replacements = @()
    )

    $attemptMessages = New-Object System.Collections.Generic.List[string]
    for ($attempt = 1; $attempt -le 4; $attempt += 1) {
        $hwp = $null
        try {
            $hwp = New-Object -ComObject HWPFrame.HwpObject
            Register-HwpFilePathChecker -Hwp $hwp | Out-Null
            try {
                $hwp.XHwpWindows.Item(0).Visible = $false
            } catch {
            }
            $opened = $hwp.Open($ResolvedPath, "", "")
            if (-not $opened) {
                throw "HWP open failed while finalizing: $ResolvedPath"
            }
            foreach ($replacement in $Replacements) {
                if ($replacement -eq $null) {
                    continue
                }
                $find = ""
                $replace = ""
                if ($replacement.PSObject.Properties.Name -contains "find") {
                    $find = [string]$replacement.find
                }
                if ($replacement.PSObject.Properties.Name -contains "replace") {
                    $replace = [string]$replacement.replace
                }
                Invoke-HwpAllReplace -Hwp $hwp -Find $find -Replace $replace | Out-Null
            }
            $saved = $hwp.SaveAs($ResolvedPath, "HWP", "")
            if (-not $saved) {
                throw "HWP save failed while finalizing: $ResolvedPath"
            }
            return $true
        } catch {
            $attemptMessages.Add("attempt ${attempt}: $($_.Exception.Message)") | Out-Null
        } finally {
            Close-HwpObject -Hwp $hwp
        }
        Start-Sleep -Milliseconds (350 * $attempt)
    }
    $detail = [string]::Join("`n", $attemptMessages)
    throw "HWP finalizing failed after retries: $ResolvedPath`n$detail"
}

$pathItems = Get-Content -Encoding UTF8 -Raw -LiteralPath $PathListJson | ConvertFrom-Json
foreach ($item in @($pathItems)) {
    $path = $null
    $replacements = @()
    if ($item -is [string]) {
        $path = [string]$item
    } else {
        if ($item.PSObject.Properties.Name -contains "path") {
            $path = [string]$item.path
        } elseif ($item.PSObject.Properties.Name -contains "Path") {
            $path = [string]$item.Path
        }
        if ($item.PSObject.Properties.Name -contains "replacements" -and $item.replacements) {
            $replacements = @($item.replacements)
        }
    }
    if ([string]::IsNullOrWhiteSpace($path)) {
        continue
    }
    $resolvedPath = (Resolve-Path -LiteralPath $path).Path
    Invoke-HwpFinalizeOne -ResolvedPath $resolvedPath -Replacements $replacements | Out-Null
    Write-Output $resolvedPath
}
