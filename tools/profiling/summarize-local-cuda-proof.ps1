param(
    [string]$RepoRoot = ".",
    [string]$SummaryPath = "",
    [string]$Output = "",
    [switch]$Json
)

$ErrorActionPreference = "Stop"

function Resolve-RepoPath {
    param(
        [Parameter(Mandatory = $true)][string]$Root,
        [Parameter(Mandatory = $true)][string]$Path
    )
    if ([System.IO.Path]::IsPathRooted($Path)) {
        return $Path
    }
    return (Join-Path $Root $Path)
}

function Get-Field {
    param(
        [AllowNull()]$Object,
        [Parameter(Mandatory = $true)][string]$Name
    )
    if ($null -eq $Object) {
        return $null
    }
    $prop = $Object.PSObject.Properties[$Name]
    if ($null -eq $prop) {
        return $null
    }
    $prop.Value
}

function Convert-ToNullableDouble {
    param([object]$Value)
    if ($null -eq $Value -or [string]::IsNullOrWhiteSpace([string]$Value)) {
        return $null
    }
    $parsed = 0.0
    if ([double]::TryParse(
        [string]$Value,
        [System.Globalization.NumberStyles]::Float,
        [System.Globalization.CultureInfo]::InvariantCulture,
        [ref]$parsed)) {
        return $parsed
    }
    $null
}

function Convert-ToInt64 {
    param([object]$Value)
    if ($null -eq $Value -or [string]::IsNullOrWhiteSpace([string]$Value)) {
        return 0L
    }
    [long]$Value
}

function Format-Nullable {
    param(
        [object]$Value,
        [string]$Suffix = "",
        [int]$Digits = 3
    )
    $number = Convert-ToNullableDouble $Value
    if ($null -eq $number) {
        return "n/a"
    }
    $rounded = [Math]::Round($number, $Digits)
    $rounded.ToString("0.###", [System.Globalization.CultureInfo]::InvariantCulture) + $Suffix
}

function Format-Percent {
    param([object]$Value)
    $number = Convert-ToNullableDouble $Value
    if ($null -eq $number) {
        return "n/a"
    }
    $sign = if ($number -gt 0) { "+" } else { "" }
    $sign + $number.ToString("0.###", [System.Globalization.CultureInfo]::InvariantCulture) + "%"
}

function Format-MetricDeltaLine {
    param(
        [string]$Label,
        [AllowNull()]$Metric,
        [string]$Unit = "",
        [switch]$LowerIsBetter
    )
    if ($null -eq $Metric) {
        return "${Label}: n/a"
    }
    $baseline = Format-Nullable (Get-Field $Metric "baseline") $Unit
    $candidate = Format-Nullable (Get-Field $Metric "candidate") $Unit
    $delta = Format-Nullable (Get-Field $Metric "delta") $Unit
    $pct = Format-Percent (Get-Field $Metric "deltaPercent")
    $direction = if ($LowerIsBetter) { "lower is better" } else { "higher is better" }
    "${Label}: CPU $baseline -> CUDA $candidate, delta $delta ($pct, $direction)"
}

function New-SectionVerdict {
    param(
        [string]$Status,
        [bool]$Passed,
        [string[]]$Evidence,
        [string[]]$Blockers
    )
    [pscustomobject]@{
        status = $Status
        passed = [bool]$Passed
        evidence = @($Evidence)
        blockers = @($Blockers)
    }
}

function New-Diagnostic {
    param(
        [Parameter(Mandatory = $true)][string]$Area,
        [Parameter(Mandatory = $true)][string]$Code,
        [ValidateSet("blocker", "warning", "info")][string]$Severity = "blocker",
        [Parameter(Mandatory = $true)][string]$Message,
        [string[]]$Evidence = @(),
        [string]$NextAction = ""
    )
    [pscustomobject]@{
        area = $Area
        code = $Code
        severity = $Severity
        message = $Message
        evidence = @($Evidence | Where-Object { -not [string]::IsNullOrWhiteSpace([string]$_) })
        nextAction = $NextAction
    }
}

function Add-Diagnostic {
    param(
        [Parameter(Mandatory = $true)][AllowEmptyCollection()][System.Collections.Generic.List[object]]$Diagnostics,
        [Parameter(Mandatory = $true)][string]$Area,
        [Parameter(Mandatory = $true)][string]$Code,
        [ValidateSet("blocker", "warning", "info")][string]$Severity = "blocker",
        [Parameter(Mandatory = $true)][string]$Message,
        [string[]]$Evidence = @(),
        [string]$NextAction = ""
    )
    [void]$Diagnostics.Add((New-Diagnostic `
        -Area $Area `
        -Code $Code `
        -Severity $Severity `
        -Message $Message `
        -Evidence $Evidence `
        -NextAction $NextAction))
}

function Get-DiagnosticSummary {
    param([object[]]$Diagnostics)

    $blocking = @($Diagnostics | Where-Object { [string]$_.severity -eq "blocker" })
    $warnings = @($Diagnostics | Where-Object { [string]$_.severity -eq "warning" })
    $primaryPriority = @(
        "DRY_RUN_PLAN_ONLY",
        "NVIDIA_SMI_UNUSABLE",
        "TOP_LEVEL_STATUS_NOT_SUCCESS",
        "TOP_LEVEL_FAILURE",
        "PLAYBACK_PROOF_MISSING",
        "PLAYBACK_NO_GPU_TEXTURE_NR_FRAMES",
        "PLAYBACK_FALLBACK_FRAMES",
        "PLAYBACK_GL_PROBE_INACTIVE",
        "GL_RENDERER_NOT_REPORTED",
        "PLAYBACK_AB_MISSING",
        "CDNG_PROOF_MISSING",
        "DNG_HASH_MISSING"
    )
    $primary = $null
    foreach ($code in $primaryPriority) {
        $match = @($blocking | Where-Object { [string]$_.code -eq $code } | Select-Object -First 1)
        if ($match.Count -gt 0) {
            $primary = [string]$match[0].code
            break
        }
    }
    if ($null -eq $primary -and $blocking.Count -gt 0) {
        $primary = [string]$blocking[0].code
    }
    [pscustomobject]@{
        blockerCount = $blocking.Count
        warningCount = $warnings.Count
        infoCount = @($Diagnostics | Where-Object { [string]$_.severity -eq "info" }).Count
        primaryBlocker = $primary
        blockerCodes = @($blocking | ForEach-Object { [string]$_.code } | Select-Object -Unique)
        warningCodes = @($warnings | ForEach-Object { [string]$_.code } | Select-Object -Unique)
    }
}

$root = (Resolve-Path -LiteralPath $RepoRoot).Path
if ([string]::IsNullOrWhiteSpace($SummaryPath)) {
    $SummaryPath = Join-Path $root ".claude-state\profiling\local-cuda-playback-dng-smoke-latest.json"
}
else {
    $SummaryPath = Resolve-RepoPath -Root $root -Path $SummaryPath
}
if (!(Test-Path -LiteralPath $SummaryPath -PathType Leaf)) {
    throw "Summary not found: $SummaryPath"
}

$summaryFullPath = (Resolve-Path -LiteralPath $SummaryPath).Path
$summary = Get-Content -LiteralPath $summaryFullPath -Raw | ConvertFrom-Json -Depth 100
$failures = @($summary.failures | Where-Object { -not [string]::IsNullOrWhiteSpace([string]$_) } | ForEach-Object { [string]$_ })
$warnings = @($summary.warnings | Where-Object { -not [string]::IsNullOrWhiteSpace([string]$_) } | ForEach-Object { [string]$_ })

$playback = $summary.proof.playback
$playbackAb = $summary.proof.playbackAb
$cdng = $summary.proof.cdng
$diagnostics = [System.Collections.Generic.List[object]]::new()

$nvidiaRows = @($summary.host.nvidiaSmi.rows | ForEach-Object { [string]$_ })
$nvidiaOk = [bool]$summary.host.nvidiaSmi.ok
$dryRun = [bool]$summary.dryRun
$topStatus = [string]$summary.status

$playbackBlockers = [System.Collections.Generic.List[string]]::new()
if ($null -eq $playback) {
    [void]$playbackBlockers.Add("playback proof did not run or did not produce a summary")
    Add-Diagnostic `
        -Diagnostics $diagnostics `
        -Area "playback" `
        -Code "PLAYBACK_PROOF_MISSING" `
        -Message "Playback no-readback proof did not run or did not produce a summary." `
        -NextAction "Run the local CUDA proof wrapper with playback enabled on the target NVIDIA host."
}
else {
    if (-not [bool]$playback.correctnessValidated) {
        [void]$playbackBlockers.Add("correctnessValidated is not true")
        Add-Diagnostic `
            -Diagnostics $diagnostics `
            -Area "playback" `
            -Code "PLAYBACK_CORRECTNESS_NOT_VALIDATED" `
            -Message "Playback proof did not set correctnessValidated=true." `
            -Evidence @("correctnessValidated=$($playback.correctnessValidated)") `
            -NextAction "Inspect the P3 playback child summary and screenshot/frame-diff failures before trusting FPS."
    }
    if ((Convert-ToInt64 $playback.totalGpuTextureNoReadbackFrames) -le 0) {
        [void]$playbackBlockers.Add("no GPU texture no-readback frames were reported")
        Add-Diagnostic `
            -Diagnostics $diagnostics `
            -Area "playback" `
            -Code "PLAYBACK_NO_GPU_TEXTURE_NR_FRAMES" `
            -Message "Playback did not report any CUDA-to-GL texture no-readback frames." `
            -Evidence @("gpu_texture_no_readback_frames=$($playback.totalGpuTextureNoReadbackFrames)") `
            -NextAction "Check backend load, scoped Dual ISO settings, CUDA-to-GL texture-present request, and GL/CUDA adapter selection."
    }
    if ((Convert-ToInt64 $playback.totalFallbackFrames) -ne 0) {
        [void]$playbackBlockers.Add("fallback frames were reported: $($playback.totalFallbackFrames)")
        Add-Diagnostic `
            -Diagnostics $diagnostics `
            -Area "playback" `
            -Code "PLAYBACK_FALLBACK_FRAMES" `
            -Message "Playback reported fallback frames instead of staying on the scoped GPU no-readback path." `
            -Evidence @("fallback_frames=$($playback.totalFallbackFrames)") `
            -NextAction "Inspect fallback reasons in the playback child summary; unsupported receipts/settings must stay CPU until the scoped gate widens."
    }
    if ((Convert-ToInt64 $playback.totalGlProbeActiveFrames) -le 0) {
        [void]$playbackBlockers.Add("GL parity probe did not report active frames")
        Add-Diagnostic `
            -Diagnostics $diagnostics `
            -Area "hybrid-gpu" `
            -Code "PLAYBACK_GL_PROBE_INACTIVE" `
            -Message "GL parity probing did not become active during playback." `
            -Evidence @("gl_probe_active_frames=$($playback.totalGlProbeActiveFrames)") `
            -NextAction "On hybrid laptops, verify the Windows high-performance GPU preference and GL renderer before claiming no-readback presentation."
    }
    if ((Convert-ToInt64 $playback.totalGlParityMismatches) -ne 0) {
        [void]$playbackBlockers.Add("GL parity mismatches were reported: $($playback.totalGlParityMismatches)")
        Add-Diagnostic `
            -Diagnostics $diagnostics `
            -Area "playback" `
            -Code "PLAYBACK_GL_PARITY_MISMATCH" `
            -Message "GL parity probe reported mismatches." `
            -Evidence @("gl_mismatches=$($playback.totalGlParityMismatches)") `
            -NextAction "Treat FPS as untrusted until screenshot/frame-diff evidence is clean."
    }
    $rendererDescriptions = @($playback.rendererDescriptions | Where-Object {
            -not [string]::IsNullOrWhiteSpace([string]$_)
        } | ForEach-Object { [string]$_ })
    if ($rendererDescriptions.Count -eq 0) {
        [void]$playbackBlockers.Add("GL renderer descriptions were not reported")
        Add-Diagnostic `
            -Diagnostics $diagnostics `
            -Area "hybrid-gpu" `
            -Code "GL_RENDERER_NOT_REPORTED" `
            -Message "Playback proof did not report a GL renderer description." `
            -NextAction "Require GL renderer evidence for Dell hybrid-GPU claims; CUDA load alone is insufficient."
    }
    elseif (@($rendererDescriptions | Where-Object { $_ -match "NVIDIA|GeForce|RTX|GTX|Quadro" }).Count -eq 0) {
        Add-Diagnostic `
            -Diagnostics $diagnostics `
            -Area "hybrid-gpu" `
            -Code "GL_RENDERER_DOES_NOT_NAME_NVIDIA" `
            -Severity "warning" `
            -Message "The reported GL renderer does not name an NVIDIA adapter." `
            -Evidence @($rendererDescriptions | ForEach-Object { "renderer=$_" }) `
            -NextAction "For the Dell RTX 3060 laptop, confirm CUDA/GL interop is on the intended adapter before treating speed as representative."
    }
}
$playbackEvidence = @()
if ($playback) {
    $playbackEvidence = @(
        "status=$($playback.status)",
        "correctnessValidated=$($playback.correctnessValidated)",
        "gpu_texture_no_readback_frames=$($playback.totalGpuTextureNoReadbackFrames)",
        "fallback_frames=$($playback.totalFallbackFrames)",
        "gl_probe_active_frames=$($playback.totalGlProbeActiveFrames)",
        "gl_mismatches=$($playback.totalGlParityMismatches)"
    )
}
$playbackVerdict = New-SectionVerdict `
    -Status $(if ($playbackBlockers.Count -eq 0) { "PASS" } else { "BLOCKED" }) `
    -Passed ($playbackBlockers.Count -eq 0) `
    -Evidence $playbackEvidence `
    -Blockers @($playbackBlockers)

$playbackAbBlockers = [System.Collections.Generic.List[string]]::new()
if ($null -eq $playbackAb) {
    [void]$playbackAbBlockers.Add("playback A/B did not run or did not produce a summary")
    Add-Diagnostic `
        -Diagnostics $diagnostics `
        -Area "playback-speed" `
        -Code "PLAYBACK_AB_MISSING" `
        -Message "Playback CPU-vs-CUDA A/B did not run or did not produce a summary." `
        -NextAction "Run the local CUDA proof wrapper without -SkipPlaybackAb to quote playback speed on this host."
}
else {
    if ([string]$playbackAb.status -notin @("success", "planned")) {
        [void]$playbackAbBlockers.Add("playback A/B status was '$($playbackAb.status)'")
        Add-Diagnostic `
            -Diagnostics $diagnostics `
            -Area "playback-speed" `
            -Code "PLAYBACK_AB_STATUS_NOT_SUCCESS" `
            -Message "Playback A/B status was not success." `
            -Evidence @("status=$($playbackAb.status)") `
            -NextAction "Inspect the playback A/B child summary for GUI smoke, GL parity, or artifact-detection failures."
    }
    if ($null -eq $playbackAb.compare) {
        [void]$playbackAbBlockers.Add("playback A/B compare metrics were missing")
        Add-Diagnostic `
            -Diagnostics $diagnostics `
            -Area "playback-speed" `
            -Code "PLAYBACK_AB_COMPARE_MISSING" `
            -Message "Playback A/B compare metrics were missing." `
            -NextAction "Rerun the playback A/B stage; speed cannot be quoted without paired CPU/CUDA metrics."
    }
    foreach ($failure in @($playbackAb.proofFailures)) {
        if (-not [string]::IsNullOrWhiteSpace([string]$failure)) {
            [void]$playbackAbBlockers.Add([string]$failure)
            Add-Diagnostic `
                -Diagnostics $diagnostics `
                -Area "playback-speed" `
                -Code "PLAYBACK_AB_PROOF_FAILURE" `
                -Message ([string]$failure) `
                -NextAction "Fix the A/B proof failure before quoting playback speed."
        }
    }
    if ($playbackAb.compare -and $null -ne $playbackAb.compare.presentedFps) {
        $fpsDelta = Convert-ToNullableDouble (Get-Field $playbackAb.compare.presentedFps "delta")
        if ($null -ne $fpsDelta -and $fpsDelta -le 0) {
            Add-Diagnostic `
                -Diagnostics $diagnostics `
                -Area "playback-speed" `
                -Code "PLAYBACK_PRESENTED_FPS_NOT_IMPROVED" `
                -Severity "warning" `
                -Message "CUDA playback did not improve presented FPS in the paired A/B metrics." `
                -Evidence @((Format-MetricDeltaLine -Label "presented FPS" -Metric $playbackAb.compare.presentedFps)) `
                -NextAction "Treat the packet as a support/correctness result, not a speedup claim, and inspect per-stage timing deltas."
        }
    }
}
$playbackSpeedEvidence = @()
if ($playbackAb -and $playbackAb.compare) {
    $playbackSpeedEvidence = @(
        (Format-MetricDeltaLine -Label "presented FPS" -Metric $playbackAb.compare.presentedFps),
        (Format-MetricDeltaLine -Label "GUI status FPS" -Metric $playbackAb.compare.guiStatusFps),
        (Format-MetricDeltaLine -Label "avg present interval" -Metric $playbackAb.compare.avgPresentIntervalMs -Unit " ms" -LowerIsBetter),
        (Format-MetricDeltaLine -Label "render total" -Metric $playbackAb.compare.avgRenderTotalMs -Unit " ms" -LowerIsBetter)
    )
}
$playbackAbVerdict = New-SectionVerdict `
    -Status $(if ($playbackAbBlockers.Count -eq 0) { "PASS" } else { "BLOCKED" }) `
    -Passed ($playbackAbBlockers.Count -eq 0) `
    -Evidence $playbackSpeedEvidence `
    -Blockers @($playbackAbBlockers)

$trustedRequired = [bool]$summary.inputs.trustedGpuExport
$cdngBlockers = [System.Collections.Generic.List[string]]::new()
if ($null -eq $cdng) {
    [void]$cdngBlockers.Add("CDNG proof did not run or did not produce a matrix summary")
    Add-Diagnostic `
        -Diagnostics $diagnostics `
        -Area "dng-export" `
        -Code "CDNG_PROOF_MISSING" `
        -Message "CDNG export proof did not run or did not produce a matrix summary." `
        -NextAction "Run the local CUDA proof wrapper with CDNG enabled to prove DNG hash parity and export speed on this host."
}
else {
    if ([string]$cdng.verdict -ne "PASS") {
        [void]$cdngBlockers.Add("CDNG matrix verdict was '$($cdng.verdict)'")
        Add-Diagnostic `
            -Diagnostics $diagnostics `
            -Area "dng-export" `
            -Code "CDNG_VERDICT_NOT_PASS" `
            -Message "CDNG matrix verdict was not PASS." `
            -Evidence @("verdict=$($cdng.verdict)") `
            -NextAction "Inspect the CDNG matrix rows and DNG hash report before quoting export support."
    }
    if ($null -eq $cdng.dngHash) {
        [void]$cdngBlockers.Add("DNG hash result was missing")
        Add-Diagnostic `
            -Diagnostics $diagnostics `
            -Area "dng-export" `
            -Code "DNG_HASH_MISSING" `
            -Message "DNG hash result was missing from the CDNG proof." `
            -NextAction "Require CPU-vs-GPU DNG hash evidence before claiming export correctness."
    }
    elseif ([string]$cdng.dngHash.verdict -ne "PASS") {
        [void]$cdngBlockers.Add("DNG hash verdict was '$($cdng.dngHash.verdict)'")
        Add-Diagnostic `
            -Diagnostics $diagnostics `
            -Area "dng-export" `
            -Code "DNG_HASH_NOT_PASS" `
            -Message "DNG hash verdict was not PASS." `
            -Evidence @("dng_hash_verdict=$($cdng.dngHash.verdict)") `
            -NextAction "Do not use the GPU DNG path until CPU/GPU DNG hashes match."
    }
    if ($null -eq $cdng.speed) {
        [void]$cdngBlockers.Add("CDNG speed metrics were missing")
        Add-Diagnostic `
            -Diagnostics $diagnostics `
            -Area "dng-export-speed" `
            -Code "CDNG_SPEED_MISSING" `
            -Message "CDNG speed metrics were missing." `
            -NextAction "Run a CDNG matrix/A-B proof that records wall-time and stage deltas."
    }
    elseif ((Convert-ToInt64 $cdng.speed.runCount) -le 0) {
        [void]$cdngBlockers.Add("CDNG speed runCount was zero")
        Add-Diagnostic `
            -Diagnostics $diagnostics `
            -Area "dng-export-speed" `
            -Code "CDNG_SPEED_EMPTY" `
            -Message "CDNG speed metrics contained zero runs." `
            -Evidence @("runCount=$($cdng.speed.runCount)") `
            -NextAction "Rerun with at least one CDNG matrix case and repeat."
    }
    if ((Convert-ToInt64 $cdng.candidateFrameCount) -le 0) {
        [void]$cdngBlockers.Add("candidate frame count was zero")
        Add-Diagnostic `
            -Diagnostics $diagnostics `
            -Area "dng-export" `
            -Code "DNG_CANDIDATE_FRAME_COUNT_ZERO" `
            -Message "Candidate export frame count was zero." `
            -Evidence @("candidate_frames=$($cdng.candidateFrameCount)") `
            -NextAction "Confirm the clip, max-frame count, and batch export invocation produced measured frames."
    }
    if ((Convert-ToInt64 $cdng.candidateGpuExportAttemptedFrames) -lt (Convert-ToInt64 $cdng.candidateFrameCount)) {
        [void]$cdngBlockers.Add("candidate did not attempt GPU export for every frame")
        Add-Diagnostic `
            -Diagnostics $diagnostics `
            -Area "dng-export" `
            -Code "DNG_GPU_ATTEMPT_INCOMPLETE" `
            -Message "Candidate did not attempt GPU export for every measured frame." `
            -Evidence @("candidate_frames=$($cdng.candidateFrameCount)", "gpu_attempted=$($cdng.candidateGpuExportAttemptedFrames)") `
            -NextAction "Inspect GPU export skip codes; unsupported receipt/settings must stay CPU."
    }
    if ((Convert-ToInt64 $cdng.candidateGpuExportReplacedFrames) -lt (Convert-ToInt64 $cdng.candidateFrameCount)) {
        [void]$cdngBlockers.Add("candidate did not replace every frame with GPU output")
        Add-Diagnostic `
            -Diagnostics $diagnostics `
            -Area "dng-export" `
            -Code "DNG_GPU_REPLACEMENT_INCOMPLETE" `
            -Message "Candidate did not replace every measured frame with GPU output." `
            -Evidence @("candidate_frames=$($cdng.candidateFrameCount)", "gpu_replaced=$($cdng.candidateGpuExportReplacedFrames)") `
            -NextAction "Do not quote GPU export speed until the measured candidate path actually used GPU output for all required frames."
    }
    if ($trustedRequired -and
        (Convert-ToInt64 $cdng.candidateGpuExportTrustedFrames) -lt (Convert-ToInt64 $cdng.candidateFrameCount)) {
        [void]$cdngBlockers.Add("candidate trusted GPU export frames did not cover every frame")
        Add-Diagnostic `
            -Diagnostics $diagnostics `
            -Area "dng-export" `
            -Code "DNG_GPU_TRUSTED_INCOMPLETE" `
            -Message "Trusted GPU export frames did not cover every measured frame." `
            -Evidence @("candidate_frames=$($cdng.candidateFrameCount)", "gpu_trusted=$($cdng.candidateGpuExportTrustedFrames)") `
            -NextAction "Use trusted GPU export only when the candidate fully bypasses the CPU oracle for the measured eligible shape."
    }
    if ($cdng.speed) {
        $elapsedDelta = Convert-ToNullableDouble $cdng.speed.avgElapsedDeltaMs
        if ($null -ne $elapsedDelta -and $elapsedDelta -gt 0) {
            Add-Diagnostic `
                -Diagnostics $diagnostics `
                -Area "dng-export-speed" `
                -Code "DNG_EXPORT_ELAPSED_REGRESSED" `
                -Severity "warning" `
                -Message "CUDA DNG export wall time regressed versus CPU in the measured packet." `
                -Evidence @("avgElapsedDeltaMs=$($cdng.speed.avgElapsedDeltaMs)", "avgElapsedDeltaPercent=$($cdng.speed.avgElapsedDeltaPercent)") `
                -NextAction "Treat this as correctness proof plus E3 throughput input, not a speedup claim."
        }
        $dualIsoDelta = Convert-ToNullableDouble $cdng.speed.avgLlrawprocDualIsoDeltaMs
        $compressDelta = Convert-ToNullableDouble $cdng.speed.avgDngCompressDeltaMs
        if ($null -ne $dualIsoDelta -and $dualIsoDelta -lt 0 -and
            $null -ne $compressDelta -and $compressDelta -gt 0) {
            Add-Diagnostic `
                -Diagnostics $diagnostics `
                -Area "dng-export-speed" `
                -Code "DNG_COMPRESSION_DOMINATES_AFTER_GPU_GAIN" `
                -Severity "warning" `
                -Message "Dual ISO GPU work improved while DNG compression regressed, suggesting E3 compression/writer overlap is the next throughput lever." `
                -Evidence @("avgLlrawprocDualIsoDeltaMs=$($cdng.speed.avgLlrawprocDualIsoDeltaMs)", "avgDngCompressDeltaMs=$($cdng.speed.avgDngCompressDeltaMs)") `
                -NextAction "Prioritize E3 compression/writer overlap for lossless throughput instead of more recon tuning."
        }
    }
}
$cdngEvidence = @()
if ($cdng) {
    $cdngEvidence = @(
        "verdict=$($cdng.verdict)",
        "dngHash=$($cdng.dngHash.verdict)",
        "candidate_frames=$($cdng.candidateFrameCount)",
        "gpu_attempted=$($cdng.candidateGpuExportAttemptedFrames)",
        "gpu_replaced=$($cdng.candidateGpuExportReplacedFrames)",
        "gpu_trusted=$($cdng.candidateGpuExportTrustedFrames)"
    )
    if ($cdng.speed) {
        $cdngEvidence += "DNG wall time: CPU $(Format-Nullable $cdng.speed.avgBaselineElapsedMs ' ms') -> CUDA $(Format-Nullable $cdng.speed.avgCandidateElapsedMs ' ms'), delta $(Format-Nullable $cdng.speed.avgElapsedDeltaMs ' ms') ($(Format-Percent $cdng.speed.avgElapsedDeltaPercent))"
        $cdngEvidence += "DNG frame total avg delta: $(Format-Nullable $cdng.speed.avgFrameTotalDeltaMs ' ms')"
        $cdngEvidence += "DNG Dual ISO avg delta: $(Format-Nullable $cdng.speed.avgLlrawprocDualIsoDeltaMs ' ms')"
        $cdngEvidence += "DNG compression avg delta: $(Format-Nullable $cdng.speed.avgDngCompressDeltaMs ' ms')"
    }
}
$cdngVerdict = New-SectionVerdict `
    -Status $(if ($cdngBlockers.Count -eq 0) { "PASS" } else { "BLOCKED" }) `
    -Passed ($cdngBlockers.Count -eq 0) `
    -Evidence $cdngEvidence `
    -Blockers @($cdngBlockers)

$artifactEvidence = [pscustomobject]@{
    exeSha256 = $summary.artifacts.exe.sha256
    backendSha256 = $summary.artifacts.backend.sha256
    cudartSha256 = $summary.artifacts.cudart.sha256
    exePath = $summary.artifacts.exe.path
    backendPath = $summary.artifacts.backend.path
    cudartPath = $summary.artifacts.cudart.path
}

$overallBlockers = [System.Collections.Generic.List[string]]::new()
if ($dryRun) {
    [void]$overallBlockers.Add("dryRun=true; this is a plan, not hardware proof")
    Add-Diagnostic `
        -Diagnostics $diagnostics `
        -Area "run" `
        -Code "DRY_RUN_PLAN_ONLY" `
        -Message "dryRun=true; this packet is a plan, not hardware proof." `
        -NextAction "Run the proof helper without -DryRun on the target NVIDIA host."
}
if ($topStatus -ne "success") {
    [void]$overallBlockers.Add("top-level status is '$topStatus'")
    Add-Diagnostic `
        -Diagnostics $diagnostics `
        -Area "run" `
        -Code "TOP_LEVEL_STATUS_NOT_SUCCESS" `
        -Message "Top-level local CUDA proof status was not success." `
        -Evidence @("status=$topStatus") `
        -NextAction "Inspect child process outputs and failures in the summary."
}
if (-not $nvidiaOk) {
    [void]$overallBlockers.Add("nvidia-smi did not report a usable NVIDIA GPU")
    Add-Diagnostic `
        -Diagnostics $diagnostics `
        -Area "host" `
        -Code "NVIDIA_SMI_UNUSABLE" `
        -Message "nvidia-smi did not report a usable NVIDIA GPU." `
        -Evidence @("nvidiaSmiPath=$($summary.host.nvidiaSmi.path)", "nvidiaSmiError=$($summary.host.nvidiaSmi.error)") `
        -NextAction "Run on Dell/UltraMagnus with the NVIDIA driver visible, or fix NVIDIA driver/PATH discovery."
}
foreach ($failure in $failures) {
    [void]$overallBlockers.Add($failure)
    Add-Diagnostic `
        -Diagnostics $diagnostics `
        -Area "run" `
        -Code "TOP_LEVEL_FAILURE" `
        -Message $failure `
        -NextAction "Use the child process output tail in summary.json to locate the failing proof stage."
}
foreach ($section in @($playbackVerdict, $playbackAbVerdict, $cdngVerdict)) {
    if (-not [bool]$section.passed) {
        foreach ($blocker in @($section.blockers)) {
            [void]$overallBlockers.Add($blocker)
        }
    }
}
$overallStatus = if ($overallBlockers.Count -eq 0) { "QUOTABLE_PASS" } else { "NOT_QUOTABLE" }
$diagnosticList = @($diagnostics)
$diagnosticSummary = Get-DiagnosticSummary -Diagnostics $diagnosticList

$report = [pscustomobject]@{
    schema = "mlvapp-local-cuda-proof-report.v1"
    createdAtUtc = (Get-Date).ToUniversalTime().ToString("o")
    sourceSummary = $summaryFullPath
    status = $overallStatus
    topLevelStatus = $topStatus
    dryRun = $dryRun
    host = [pscustomobject]@{
        name = $summary.host.name
        nvidiaSmiOk = $nvidiaOk
        nvidiaSmiPath = $summary.host.nvidiaSmi.path
        nvidiaRows = $nvidiaRows
        gpuPreference = $summary.host.gpuPreference
    }
    artifacts = $artifactEvidence
    inputs = $summary.inputs
    playback = $playbackVerdict
    playbackSpeed = $playbackAbVerdict
    dngExport = $cdngVerdict
    warnings = @($warnings)
    blockers = @($overallBlockers | Select-Object -Unique)
    diagnostics = $diagnosticList
    diagnosticSummary = $diagnosticSummary
    proofBoundary = @(
        "Quote support or speed only when status is QUOTABLE_PASS and this summary was produced on the same host being claimed.",
        "Playback proof requires correctnessValidated=true, GPU Tex NR/no-readback frames, zero fallback frames, active GL parity probes, and zero GL mismatches.",
        "DNG proof requires CDNG PASS, DNG hash PASS, candidate GPU attempted/replaced frames for the full measured set, and trusted GPU frames when trusted export was requested.",
        "Speed deltas are scoped to this host, clip, receipt, codec set, max-frame count, release/backend hashes, and run order."
    )
}

$lines = [System.Collections.Generic.List[string]]::new()
[void]$lines.Add("# MLVApp Local CUDA Proof Report")
[void]$lines.Add("")
[void]$lines.Add("- Status: $($report.status)")
[void]$lines.Add("- Source summary: $summaryFullPath")
[void]$lines.Add("- Host: $($report.host.name)")
[void]$lines.Add("- NVIDIA: $(if ($nvidiaRows.Count -gt 0) { $nvidiaRows -join '; ' } else { 'not reported' })")
[void]$lines.Add("- Dry run: $dryRun")
[void]$lines.Add("")
[void]$lines.Add("## Artifacts")
[void]$lines.Add("- EXE SHA256: $($artifactEvidence.exeSha256)")
[void]$lines.Add("- CUDA backend SHA256: $($artifactEvidence.backendSha256)")
[void]$lines.Add("- CUDA runtime SHA256: $($artifactEvidence.cudartSha256)")
[void]$lines.Add("")
[void]$lines.Add("## Playback No-Readback")
[void]$lines.Add("- Verdict: $($playbackVerdict.status)")
foreach ($item in @($playbackVerdict.evidence)) {
    [void]$lines.Add("- $item")
}
if ($playbackVerdict.blockers.Count -gt 0) {
    [void]$lines.Add("- Blockers: $($playbackVerdict.blockers -join '; ')")
}
[void]$lines.Add("")
[void]$lines.Add("## Playback Speed")
[void]$lines.Add("- Verdict: $($playbackAbVerdict.status)")
foreach ($item in @($playbackAbVerdict.evidence)) {
    [void]$lines.Add("- $item")
}
if ($playbackAbVerdict.blockers.Count -gt 0) {
    [void]$lines.Add("- Blockers: $($playbackAbVerdict.blockers -join '; ')")
}
[void]$lines.Add("")
[void]$lines.Add("## DNG Export")
[void]$lines.Add("- Verdict: $($cdngVerdict.status)")
foreach ($item in @($cdngVerdict.evidence)) {
    [void]$lines.Add("- $item")
}
if ($cdngVerdict.blockers.Count -gt 0) {
    [void]$lines.Add("- Blockers: $($cdngVerdict.blockers -join '; ')")
}
[void]$lines.Add("")
[void]$lines.Add("## Boundaries")
foreach ($item in @($report.proofBoundary)) {
    [void]$lines.Add("- $item")
}
if ($warnings.Count -gt 0) {
    [void]$lines.Add("")
    [void]$lines.Add("## Warnings")
    foreach ($item in $warnings) {
        [void]$lines.Add("- $item")
    }
}
if ($overallBlockers.Count -gt 0) {
    [void]$lines.Add("")
    [void]$lines.Add("## Not Quotable Yet")
    foreach ($item in @($overallBlockers | Select-Object -Unique)) {
        [void]$lines.Add("- $item")
    }
}
if ($diagnosticList.Count -gt 0) {
    [void]$lines.Add("")
    [void]$lines.Add("## Diagnostic Classification")
    foreach ($diagnostic in $diagnosticList) {
        $line = "- [$($diagnostic.severity)] $($diagnostic.area)/$($diagnostic.code): $($diagnostic.message)"
        if (-not [string]::IsNullOrWhiteSpace([string]$diagnostic.nextAction)) {
            $line += " Next: $($diagnostic.nextAction)"
        }
        [void]$lines.Add($line)
        foreach ($item in @($diagnostic.evidence)) {
            [void]$lines.Add("  - evidence: $item")
        }
    }
}

$markdown = $lines -join [Environment]::NewLine
if (-not [string]::IsNullOrWhiteSpace($Output)) {
    $outputFullPath = $ExecutionContext.SessionState.Path.GetUnresolvedProviderPathFromPSPath(
        (Resolve-RepoPath -Root $root -Path $Output))
    $outputDir = Split-Path -Parent $outputFullPath
    if ($outputDir) {
        New-Item -ItemType Directory -Force -Path $outputDir | Out-Null
    }
    if ($Json) {
        $report | ConvertTo-Json -Depth 32 | Set-Content -LiteralPath $outputFullPath -Encoding UTF8
    }
    else {
        $markdown | Set-Content -LiteralPath $outputFullPath -Encoding UTF8
    }
}

if ($Json) {
    $report | ConvertTo-Json -Depth 32
}
else {
    $markdown
}

if ($overallStatus -ne "QUOTABLE_PASS") {
    exit 1
}
