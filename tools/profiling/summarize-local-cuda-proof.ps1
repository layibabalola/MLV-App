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

$nvidiaRows = @($summary.host.nvidiaSmi.rows | ForEach-Object { [string]$_ })
$nvidiaOk = [bool]$summary.host.nvidiaSmi.ok
$dryRun = [bool]$summary.dryRun
$topStatus = [string]$summary.status

$playbackBlockers = [System.Collections.Generic.List[string]]::new()
if ($null -eq $playback) {
    [void]$playbackBlockers.Add("playback proof did not run or did not produce a summary")
}
else {
    if (-not [bool]$playback.correctnessValidated) {
        [void]$playbackBlockers.Add("correctnessValidated is not true")
    }
    if ((Convert-ToInt64 $playback.totalGpuTextureNoReadbackFrames) -le 0) {
        [void]$playbackBlockers.Add("no GPU texture no-readback frames were reported")
    }
    if ((Convert-ToInt64 $playback.totalFallbackFrames) -ne 0) {
        [void]$playbackBlockers.Add("fallback frames were reported: $($playback.totalFallbackFrames)")
    }
    if ((Convert-ToInt64 $playback.totalGlProbeActiveFrames) -le 0) {
        [void]$playbackBlockers.Add("GL parity probe did not report active frames")
    }
    if ((Convert-ToInt64 $playback.totalGlParityMismatches) -ne 0) {
        [void]$playbackBlockers.Add("GL parity mismatches were reported: $($playback.totalGlParityMismatches)")
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
}
else {
    if ([string]$playbackAb.status -notin @("success", "planned")) {
        [void]$playbackAbBlockers.Add("playback A/B status was '$($playbackAb.status)'")
    }
    if ($null -eq $playbackAb.compare) {
        [void]$playbackAbBlockers.Add("playback A/B compare metrics were missing")
    }
    foreach ($failure in @($playbackAb.proofFailures)) {
        if (-not [string]::IsNullOrWhiteSpace([string]$failure)) {
            [void]$playbackAbBlockers.Add([string]$failure)
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
}
else {
    if ([string]$cdng.verdict -ne "PASS") {
        [void]$cdngBlockers.Add("CDNG matrix verdict was '$($cdng.verdict)'")
    }
    if ($null -eq $cdng.dngHash) {
        [void]$cdngBlockers.Add("DNG hash result was missing")
    }
    elseif ([string]$cdng.dngHash.verdict -ne "PASS") {
        [void]$cdngBlockers.Add("DNG hash verdict was '$($cdng.dngHash.verdict)'")
    }
    if ($null -eq $cdng.speed) {
        [void]$cdngBlockers.Add("CDNG speed metrics were missing")
    }
    elseif ((Convert-ToInt64 $cdng.speed.runCount) -le 0) {
        [void]$cdngBlockers.Add("CDNG speed runCount was zero")
    }
    if ((Convert-ToInt64 $cdng.candidateFrameCount) -le 0) {
        [void]$cdngBlockers.Add("candidate frame count was zero")
    }
    if ((Convert-ToInt64 $cdng.candidateGpuExportAttemptedFrames) -lt (Convert-ToInt64 $cdng.candidateFrameCount)) {
        [void]$cdngBlockers.Add("candidate did not attempt GPU export for every frame")
    }
    if ((Convert-ToInt64 $cdng.candidateGpuExportReplacedFrames) -lt (Convert-ToInt64 $cdng.candidateFrameCount)) {
        [void]$cdngBlockers.Add("candidate did not replace every frame with GPU output")
    }
    if ($trustedRequired -and
        (Convert-ToInt64 $cdng.candidateGpuExportTrustedFrames) -lt (Convert-ToInt64 $cdng.candidateFrameCount)) {
        [void]$cdngBlockers.Add("candidate trusted GPU export frames did not cover every frame")
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
}
if ($topStatus -ne "success") {
    [void]$overallBlockers.Add("top-level status is '$topStatus'")
}
if (-not $nvidiaOk) {
    [void]$overallBlockers.Add("nvidia-smi did not report a usable NVIDIA GPU")
}
foreach ($failure in $failures) {
    [void]$overallBlockers.Add($failure)
}
foreach ($section in @($playbackVerdict, $playbackAbVerdict, $cdngVerdict)) {
    if (-not [bool]$section.passed) {
        foreach ($blocker in @($section.blockers)) {
            [void]$overallBlockers.Add($blocker)
        }
    }
}
$overallStatus = if ($overallBlockers.Count -eq 0) { "QUOTABLE_PASS" } else { "NOT_QUOTABLE" }

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
