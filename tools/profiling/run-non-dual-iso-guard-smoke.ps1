# Non-Dual-ISO guard smoke.
#
# WHY THIS EXISTS
#   The Dual-ISO P3 validation (run-ultramagnus-p3-validation.ps1) only exercises the SCOPED
#   Dual-ISO GPU recon path: it forces raw fixes on, arms the no-readback GL texture-present path,
#   and asserts GL-vs-CPU-oracle Dual-ISO parity. That proof says nothing about the ordinary
#   NON-Dual-ISO playback path that the overwhelming majority of end-user clips take. A green
#   Dual-ISO smoke is therefore NOT sufficient on its own for a broad end-user / shipping claim.
#
#   This guard plays a real NON-Dual-ISO clip and validates it BY PIXELS (not FPS) using the same
#   generic temporal-artifact detector the GUI smoke already drives (detect-playback-artifacts.ps1):
#   no flicker, no stall, no FROZEN CONTENT, and real frame-to-frame movement. It deliberately does
#   NOT assert any Dual-ISO recon / GL parity -- on a non-Dual-ISO clip the recon path never arms,
#   so those assertions are not applicable.
#
#   Canonical non-Dual-ISO source footage lives at \\ultra-magnus\L\!5D3 RAW\!Projects (mapped Z:);
#   every clip in that subfolder tree is non-Dual-ISO 5D3 RAW. M30-1209.MLV is a verified pick
#   (dual_iso_mode_effective=0, 1920x1080, 398 frames).
#
# EXIT CODES
#   0  guard passed (clip played and validated clean by pixels)
#   2  guard failed (playback artifact, frozen content, no pixel evidence, or smoke error)

param(
    [string]$RepoRoot = ".",
    [string]$ExePath = "",
    # Default = the verified non-Dual-ISO clip on the UltraMagnus L share (reachable over UNC).
    [string]$ClipPath = "\\ultra-magnus\L\!5D3 RAW\!Projects\Ben Harris Bachelor Weekend 2015\Video\M30-1209.MLV",
    [string]$Receipt = "",
    [int]$Seconds = 30,
    [int]$SettleMs = 2500,
    [string]$ScaleFactor = "4",
    [string]$OutputRoot = ".claude-state\profiling\non-dual-iso-guard-smoke",
    # By default this guard FAILS if the smoke reports the clip was actually processed as Dual ISO
    # (wrong clip pointed at the non-Dual-ISO guard). Pass -AllowDualIsoClip to downgrade that to a
    # warning instead.
    [switch]$AllowDualIsoClip,
    [string[]]$AdditionalSmokeArgs = @(),
    [switch]$DryRun
)

$ErrorActionPreference = "Stop"

function Resolve-RepoPath {
    param(
        [Parameter(Mandatory = $true)][string]$Root,
        [Parameter(Mandatory = $true)][string]$Path
    )
    if ([System.IO.Path]::IsPathRooted($Path)) { return $Path }
    return (Join-Path $Root $Path)
}

$repo = (Resolve-Path -LiteralPath $RepoRoot).Path
$smokeScript = Join-Path $repo "tools\profiling\run-release-gui-smoke.ps1"
if (!(Test-Path -LiteralPath $smokeScript)) {
    Write-Error "Missing GUI smoke script: $smokeScript"
    exit 2
}

$outDir = Resolve-RepoPath -Root $repo -Path $OutputRoot
New-Item -ItemType Directory -Force -Path $outDir | Out-Null
$screenshots = Join-Path $outDir "screenshots"
New-Item -ItemType Directory -Force -Path $screenshots | Out-Null
$clipOutput = Join-Path $outDir "guard-smoke-result.json"
$invokeScript = Join-Path $outDir "_invoke-non-dual-iso-smoke.ps1"
$summaryPath = Join-Path $outDir "latest.json"

$failures = [System.Collections.Generic.List[string]]::new()
$warnings = [System.Collections.Generic.List[string]]::new()

# Confirm the clip is reachable before launching the (slow) smoke.
if (!(Test-Path -LiteralPath $ClipPath)) {
    $failures.Add("Non-Dual-ISO clip not reachable: $ClipPath")
}

# Build the GUI-smoke invocation. The smoke is invoked as a CHILD pwsh process (not in-process)
# because run-release-gui-smoke.ps1 ends with `exit`, which would terminate this guard if dot-called.
$receiptLiteral = if ($Receipt) { "Receipt = '$Receipt'`n" } else { "" }
$exeLiteral = if ($ExePath) { "ExePath = '$ExePath'`n" } else { "" }
$extraLiteral = if ($AdditionalSmokeArgs.Count -gt 0) {
    "AdditionalArgs = @(" + (($AdditionalSmokeArgs | ForEach-Object { "'" + ($_ -replace "'", "''") + "'" }) -join ",") + ")`n"
} else { "" }

$invokeText = @"
`$ErrorActionPreference = 'Stop'
`$smokeParams = @{
    RepoRoot = '$repo'
    ClipPath = '$ClipPath'
    Output = '$clipOutput'
    Seconds = $Seconds
    SettleMs = $SettleMs
    ScaleFactor = '$ScaleFactor'
    DetectPlaybackArtifacts = `$true
    CaptureScreenshot = `$true
    FailOnColorArtifact = `$true
    ScreenshotOutputDir = '$screenshots'
    $exeLiteral$receiptLiteral$extraLiteral}
& '$smokeScript' @smokeParams
exit `$LASTEXITCODE
"@

$status = "planned"
$smokeExit = $null
$playbackArtifacts = $null
$dualIsoFull20Summary = $null

if ($DryRun) {
    $status = "planned"
}
elseif ($failures.Count -gt 0) {
    $status = "failed"
}
else {
    $invokeText | Set-Content -LiteralPath $invokeScript -Encoding UTF8
    & pwsh.exe -NoLogo -NoProfile -NonInteractive -ExecutionPolicy Bypass -File $invokeScript
    $smokeExit = $LASTEXITCODE

    if ($smokeExit -ne 0) {
        $failures.Add("GUI smoke exited with code $smokeExit.")
    }
    if (!(Test-Path -LiteralPath $clipOutput)) {
        $failures.Add("Smoke result JSON was not written: $clipOutput")
    }
    else {
        $result = Get-Content -LiteralPath $clipOutput -Raw | ConvertFrom-Json

        if (-not $result.validation.ok) {
            $detail = if ($result.validation.failures) { ($result.validation.failures -join "; ") } else { "(no detail)" }
            $failures.Add("Smoke validation not OK: $detail")
        }

        $playbackArtifacts = $result.playbackArtifacts
        if ($null -eq $playbackArtifacts) {
            $failures.Add("Smoke did not run the playback-artifact detector (no pixel evidence).")
        }
        else {
            switch ([string]$playbackArtifacts.verdict) {
                "PASS" { }
                "FAIL" {
                    $failures.Add("Playback artifact detector verdict FAIL: " + (([string]$playbackArtifacts.detail) -replace '\s+', ' '))
                }
                "no-data" {
                    $failures.Add("Playback artifact detector returned no-data (no per-frame content hashes captured).")
                }
                default {
                    $failures.Add("Playback artifact detector verdict not PASS: '$($playbackArtifacts.verdict)'.")
                }
            }
            # Validate BY PIXELS: real frame-to-frame movement and zero frozen-content runs.
            if ([int]$playbackArtifacts.distinctHashes -le 1) {
                $failures.Add("Only $([int]$playbackArtifacts.distinctHashes) distinct content hash(es): no real pixel movement (stuck/frozen frame).")
            }
            if ([int]$playbackArtifacts.frozenContentRuns -gt 0) {
                $failures.Add("Frozen-content runs detected: $([int]$playbackArtifacts.frozenContentRuns) (longest $([int]$playbackArtifacts.longestFrozenRun)).")
            }
        }

        # Sanity guard: this clip is supposed to be NON-Dual-ISO. If the smoke reports the Dual-ISO
        # recon path actually engaged, the wrong clip was pointed at this guard.
        $dualIsoFull20Summary = $result.log.dualIsoFull20Summary
        if ($null -ne $dualIsoFull20Summary) {
            $dualIsoActive = $false
            foreach ($prop in $dualIsoFull20Summary.PSObject.Properties) {
                if ($prop.Name -match '(?i)valid|effective|active|enabled') {
                    $v = [string]$prop.Value
                    if ($v -eq '1' -or $v -match '(?i)true') { $dualIsoActive = $true }
                }
            }
            if ($dualIsoActive) {
                $msg = "Clip reported active Dual-ISO processing; this guard expects a NON-Dual-ISO clip: $ClipPath"
                if ($AllowDualIsoClip) { $warnings.Add($msg) } else { $failures.Add($msg) }
            }
        }
    }

    $status = if ($failures.Count -eq 0) { "success" } else { "failed" }
}

$summary = [ordered]@{
    schema = "mlvapp-non-dual-iso-guard-smoke.v1"
    status = $status
    clipPath = $ClipPath
    repoRoot = $repo
    seconds = $Seconds
    scaleFactor = $ScaleFactor
    receipt = $Receipt
    smokeExitCode = $smokeExit
    smokeResultPath = $clipOutput
    playbackArtifacts = $playbackArtifacts
    dualIsoFull20Summary = $dualIsoFull20Summary
    failures = @($failures)
    warnings = @($warnings)
}

$summary | ConvertTo-Json -Depth 12 | Set-Content -LiteralPath $summaryPath -Encoding UTF8
$summary | ConvertTo-Json -Depth 6

if ($status -eq "success" -or $status -eq "planned") { exit 0 }
exit 2
