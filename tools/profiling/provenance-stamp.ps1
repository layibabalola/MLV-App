# provenance-stamp.ps1
# Shared helper: read the build-time provenance stamp OUT of an MLVApp.exe.
#
# P0 principle: the binary is the only source of truth about itself. The stamp is
# compiled in by tools/gen-buildinfo.ps1 as the uniquely-tagged field
#     MLVAPP_BUILDSTAMP_v1|sha=<40hex>|dirty=<0|1>
# and force-retained in the linked exe via __attribute__((used)) in MainWindow.cpp.
# Every downstream gate (kit name, deploy receipt, launch) must read THIS field back
# out of the exe and refuse on mismatch -- a label set anywhere but the linked binary
# is a lie waiting to happen.
#
# pwsh 7 NOTE: extract via raw bytes -> ASCII -> regex. Do NOT use
# `Select-String -Encoding Byte`: that switch is Windows PowerShell 5.1 only and
# THROWS under PowerShell 7. Matching the tagged field (not a bare 40-hex) avoids
# false-passing on some other 40-hex string that happens to live in the binary.

function Get-MlvAppBuildStamp {
    [CmdletBinding()]
    param([Parameter(Mandatory = $true)][string]$ExePath)

    if (-not (Test-Path -LiteralPath $ExePath -PathType Leaf)) {
        return [pscustomobject]@{
            exePath      = $ExePath
            exists       = $false
            found        = $false
            sha          = $null
            dirty        = $null
            stampField   = $null
            embeddedShas = @()
        }
    }

    # .ProviderPath (not .Path) so UNC paths resolve to a native filesystem path that
    # [System.IO.File] can read -- .Path returns a provider-prefixed form for \\unc\...
    $full  = (Resolve-Path -LiteralPath $ExePath).ProviderPath
    $bytes = [System.IO.File]::ReadAllBytes($full)
    $text  = [System.Text.Encoding]::ASCII.GetString($bytes)

    $m = [regex]::Match($text, 'MLVAPP_BUILDSTAMP_v1\|sha=([0-9a-f]{40})\|dirty=([01])')
    # Diagnostic only: every distinct 40-hex run in the exe (helps explain a mismatch).
    $embedded = @([regex]::Matches($text, '[0-9a-f]{40}') |
        ForEach-Object { $_.Value } | Select-Object -Unique)

    [pscustomobject]@{
        exePath      = $full
        exists       = $true
        found        = $m.Success
        sha          = if ($m.Success) { $m.Groups[1].Value } else { $null }
        dirty        = if ($m.Success) { [int]$m.Groups[2].Value } else { $null }
        stampField   = if ($m.Success) { $m.Value } else { $null }
        embeddedShas = $embedded
    }
}
