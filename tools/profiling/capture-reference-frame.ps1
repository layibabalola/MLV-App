<#
.SYNOPSIS
    Produce a REFERENCE FRAME CANDIDATE: one presented frame plus the full identity of what
    produced it. Emits reference-frame-candidate.v1. It does NOT promote anything.

.DESCRIPTION
    WHY THIS EXISTS.
    tools/gates/output-budget.json has carried
        "baseline": { "status": "pending_instrumented_known_good_bridge",
                      "commit": null, "executableSha256": null, "artifact": null }
    since 2026-08-18. output_budget.py's own validator says a promoted baseline needs
    status "reviewed_instrumented_known_good" plus a 40-hex commit, a 64-hex executableSha256
    and a nonempty artifact identifier. The gate machinery was built and never bound - the
    missing piece is the BRIDGE named in that status string, which is this script.

    WHAT IT DELIBERATELY WILL NOT DO.
    It never writes "reviewed_instrumented_known_good". The contract's own word is REVIEWED, and
    an actor that promotes its own capture to reviewed-known-good has granted itself the review.
    This emits status "candidate-unreviewed" and stops. A reviewer promotes it, or nobody does.

    WHY A CAPTURE WITHOUT IDENTITY IS WORTHLESS.
    On 2026-09-03 a single frame with no reference produced a confident "blue cast regression"
    verdict that a reference arm then refuted - the older build measured MARGINALLY BLUER on the
    same clip. A frame is evidence only when you can say exactly which binary, which clip, and
    which settings produced it, so the manifest carries all three by HASH, not by name.

.NOTES
    ASCII-only by project convention. Run ON the bench that has the GPU and the clips.
    NEVER screen-capture the bench: this uses the app's OWN --screenshot-output, because the
    bench is a machine its owner uses interactively and a desktop grab captures their screen,
    not the application. (Rule earned the hard way, 2026-09-03.)
#>
[CmdletBinding()]
param(
    [Parameter(Mandatory = $true)][string]$Exe,
    [Parameter(Mandatory = $true)][string]$Clip,
    [Parameter(Mandatory = $true)][string]$OutDir,
    [string]$Commit = '',
    [string]$ProfileId = 'shipping-default',
    [int]$Seconds = 30,
    [int]$SettleMs = 3000,
    [hashtable]$ExtraEnv = @{}
)
$ErrorActionPreference = 'Stop'
New-Item -ItemType Directory -Force -Path $OutDir | Out-Null

foreach ($p in @($Exe, $Clip)) {
    if (-not (Test-Path -LiteralPath $p)) { Write-Output "CAPTURE: CANNOT-DETERMINE - missing $p"; exit 3 }
}

# The SHIPPING DEFAULT is the absence of overrides, so this profile sets none. Only telemetry
# and unattended flags, which change what is PRINTED, not what is rendered.
$env:MLVAPP_PLAYBACK_PHASE3_UNATTENDED = '1'
$env:MLVAPP_PLAYBACK_SMOKE_TELEMETRY   = '1'
$env:MLVAPP_INTERACTIVE_TRACE          = '1'
$applied = @('MLVAPP_PLAYBACK_PHASE3_UNATTENDED','MLVAPP_PLAYBACK_SMOKE_TELEMETRY','MLVAPP_INTERACTIVE_TRACE')
foreach ($k in $ExtraEnv.Keys) { Set-Item -Path ("env:" + $k) -Value ([string]$ExtraEnv[$k]); $applied += $k }

$shot = Join-Path $OutDir 'reference-frame.png'
$proc = Start-Process -FilePath $Exe -NoNewWindow -PassThru -Wait `
    -ArgumentList @('--gui-smoke-playback','--input',$Clip,'--scope','none','--no-zebras',
                    '--seconds',[string]$Seconds,'--settle-ms',[string]$SettleMs,
                    '--start-frame','0','--loop','--screenshot-output',$shot) `
    -RedirectStandardOutput (Join-Path $OutDir 'capture.out.txt') `
    -RedirectStandardError  (Join-Path $OutDir 'capture.err.txt')

# A FAILED LAUNCH LEAVES $proc NULL and every downstream check silently passes against nothing.
# Measured 2026-09-03: an exe that died 0xC0000135 in the loader still produced a manifest full of
# stale numbers because nothing asserted the process had started.
if ($null -eq $proc -or $null -eq $proc.ExitCode) {
    Write-Output "CAPTURE: FAILED - process-never-started ($Exe)"; exit 4
}
if ($proc.ExitCode -ne 0) { Write-Output "CAPTURE: FAILED - exe exited $($proc.ExitCode)"; exit 4 }
if (-not (Test-Path -LiteralPath $shot)) { Write-Output "CAPTURE: FAILED - no frame written"; exit 5 }

$img = Get-Item -LiteralPath $shot
# A degenerate grab is a REAL outcome, not a pass: on the GL-window path this same flag yielded a
# 150x45 / 0.2 KB image because the embedded widget was not the surface being drawn to.
if ($img.Length -lt 51200) {
    Write-Output ("CAPTURE: FAILED - frame is {0} bytes; a degenerate grab is not a reference" -f $img.Length)
    exit 6
}

if (-not $Commit) {
    $Commit = (& git -C (Split-Path $PSScriptRoot -Parent | Split-Path -Parent) rev-parse HEAD 2>$null)
    if ($Commit) { $Commit = $Commit.Trim() }
}

$manifest = [ordered]@{
    schema      = 'mlvapp.reference-frame-candidate.v1'
    # NOT "reviewed_instrumented_known_good". This script cannot review its own output.
    status      = 'candidate-unreviewed'
    promotionRequires = 'a reviewer sets output-budget.json baseline.status=reviewed_instrumented_known_good with these identity fields'
    capturedUtc = (Get-Date).ToUniversalTime().ToString('o')
    host        = $env:COMPUTERNAME
    profileId   = $ProfileId
    commit      = $Commit
    executable  = [ordered]@{ path = $Exe; sha256 = (Get-FileHash -LiteralPath $Exe -Algorithm SHA256).Hash; bytes = (Get-Item -LiteralPath $Exe).Length }
    clip        = [ordered]@{ path = $Clip; sha256 = (Get-FileHash -LiteralPath $Clip -Algorithm SHA256).Hash; bytes = (Get-Item -LiteralPath $Clip).Length }
    artifact    = [ordered]@{ path = $shot; sha256 = (Get-FileHash -LiteralPath $shot -Algorithm SHA256).Hash; bytes = $img.Length }
    settings    = [ordered]@{ seconds = $Seconds; settleMs = $SettleMs; scopeFlags = '--scope none --no-zebras'; envApplied = $applied }
    exitCode    = $proc.ExitCode
}
$mf = Join-Path $OutDir 'reference-frame-candidate.json'
$manifest | ConvertTo-Json -Depth 8 | Set-Content -LiteralPath $mf -Encoding utf8
Write-Output ("CAPTURE: CANDIDATE commit={0} exe={1} frame={2} bytes={3}" -f `
    $Commit.Substring(0,[Math]::Min(12,$Commit.Length)), $manifest.executable.sha256.Substring(0,12),
    $manifest.artifact.sha256.Substring(0,12), $img.Length)
Write-Output "CAPTURE: status=candidate-unreviewed (this script does not promote) -> $mf"
exit 0
