param(
    [string]$RepoRoot = ".",
    [string]$ExePath = "",
    [Alias("Input")]
    [string]$ClipPath = "",
    [string]$Receipt = "",
    [string]$OutputDir = "",
    [string]$BuildId = "",
    [switch]$BaselineUsePayloadHandoff,
    [switch]$CandidateUsePayloadHandoff,
    [switch]$BaselineUseAsyncWriter,
    [switch]$CandidateUseAsyncWriter,
    [int]$BaselineAsyncWriterQueueDepth = 0,
    [int]$CandidateAsyncWriterQueueDepth = 0,
    [int]$MaxFrames = 0,
    [switch]$EnableGpuExport,
    [string]$GpuExportDll = "",
    [ValidateSet("BaselineFirst", "CandidateFirst")]
    [string]$RunOrder = "BaselineFirst",
    [double]$MaxFrameTotalRegressionPercent = 5.0,
    [double]$MaxFrameTotalP95RegressionPercent = 10.0,
    [switch]$FailOnRegression,
    [switch]$DryRun
)

$ErrorActionPreference = "Stop"

$root = (Resolve-Path -LiteralPath $RepoRoot).Path
$runner = Join-Path $root "tools\profiling\run-release-cdng-export-profile.ps1"
$comparator = Join-Path $root "tools\profiling\compare-export-stage-profiles.ps1"
if (-not (Test-Path -LiteralPath $runner)) {
    throw "Release CDNG export profiler not found: $runner"
}
if (-not (Test-Path -LiteralPath $comparator)) {
    throw "Export stage comparator not found: $comparator"
}

if ([string]::IsNullOrWhiteSpace($OutputDir)) {
    $stamp = (Get-Date).ToUniversalTime().ToString("yyyyMMdd-HHmmss")
    $OutputDir = Join-Path $root ".claude-state\profiling\$stamp-cdng-export-ab"
}
$bundleDir = $ExecutionContext.SessionState.Path.GetUnresolvedProviderPathFromPSPath($OutputDir)
$baselineDir = Join-Path $bundleDir "baseline"
$candidateDir = Join-Path $bundleDir "candidate"
$baselineDngDir = Join-Path $baselineDir "dng"
$candidateDngDir = Join-Path $candidateDir "dng"
$baselineProfile = Join-Path $baselineDir "profile.json"
$candidateProfile = Join-Path $candidateDir "profile.json"
$baselineLog = Join-Path $baselineDir "batch.log"
$candidateLog = Join-Path $candidateDir "batch.log"
$compareJson = Join-Path $bundleDir "compare.json"
$summaryJson = Join-Path $bundleDir "summary.json"

function New-ProfileArgs {
    param(
        [string]$Label,
        [string]$DngDir,
        [string]$ProfilePath,
        [string]$LogPath,
        [bool]$UsePayloadHandoff,
        [bool]$UseAsyncWriter,
        [int]$AsyncWriterQueueDepth
    )

    $args = @(
        "-NoLogo", "-NoProfile", "-NonInteractive", "-ExecutionPolicy", "Bypass",
        "-File", $runner,
        "-RepoRoot", $root,
        "-DngOutputDir", $DngDir,
        "-Output", $ProfilePath,
        "-Log", $LogPath
    )
    if (-not [string]::IsNullOrWhiteSpace($ExePath)) {
        $args += @("-ExePath", $ExePath)
    }
    if (-not [string]::IsNullOrWhiteSpace($ClipPath)) {
        $args += @("-Input", $ClipPath)
    }
    if (-not [string]::IsNullOrWhiteSpace($Receipt)) {
        $args += @("-Receipt", $Receipt)
    }
    if (-not [string]::IsNullOrWhiteSpace($BuildId)) {
        $args += @("-BuildId", "$BuildId-$Label")
    }
    if ($EnableGpuExport) {
        $args += "-EnableGpuExport"
        if (-not [string]::IsNullOrWhiteSpace($GpuExportDll)) {
            $args += @("-GpuExportDll", $GpuExportDll)
        }
    }
    if ($MaxFrames -gt 0) {
        $args += @("-MaxFrames", "$MaxFrames")
    }
    if ($UsePayloadHandoff) {
        $args += "-UsePayloadHandoff"
    }
    if ($UseAsyncWriter) {
        $args += "-UseAsyncWriter"
        if ($AsyncWriterQueueDepth -gt 0) {
            $args += @("-AsyncWriterQueueDepth", "$AsyncWriterQueueDepth")
        }
    }
    if ($DryRun) {
        $args += "-DryRun"
    }
    $args
}

function New-ElapsedDelta {
    param(
        [double]$BaselineMs,
        [double]$CandidateMs
    )

    $deltaMs = [Math]::Round($CandidateMs - $BaselineMs, 3)
    $deltaPercent = $null
    if ([Math]::Abs($BaselineMs) -gt 0.001) {
        $deltaPercent = [Math]::Round((($CandidateMs - $BaselineMs) / $BaselineMs) * 100.0, 3)
    }

    [pscustomobject]@{
        baselineMs = $BaselineMs
        candidateMs = $CandidateMs
        deltaMs = $deltaMs
        deltaPercent = $deltaPercent
    }
}

function Invoke-ProfileRun {
    param(
        [string]$Label,
        [string[]]$CommandArgs
    )

    $stopwatch = [System.Diagnostics.Stopwatch]::StartNew()
    & pwsh.exe @CommandArgs
    $exitCode = $LASTEXITCODE
    $stopwatch.Stop()

    [pscustomobject]@{
        label = $Label
        exitCode = $exitCode
        elapsedMs = [Math]::Round($stopwatch.Elapsed.TotalMilliseconds, 3)
    }
}

$baselineArgs = New-ProfileArgs `
    -Label "baseline" `
    -DngDir $baselineDngDir `
    -ProfilePath $baselineProfile `
    -LogPath $baselineLog `
    -UsePayloadHandoff ([bool]$BaselineUsePayloadHandoff) `
    -UseAsyncWriter ([bool]$BaselineUseAsyncWriter) `
    -AsyncWriterQueueDepth $BaselineAsyncWriterQueueDepth
$candidateArgs = New-ProfileArgs `
    -Label "candidate" `
    -DngDir $candidateDngDir `
    -ProfilePath $candidateProfile `
    -LogPath $candidateLog `
    -UsePayloadHandoff ([bool]$CandidateUsePayloadHandoff) `
    -UseAsyncWriter ([bool]$CandidateUseAsyncWriter) `
    -AsyncWriterQueueDepth $CandidateAsyncWriterQueueDepth

$isIdentityComparison = (
    [bool]$BaselineUsePayloadHandoff -eq [bool]$CandidateUsePayloadHandoff -and
    [bool]$BaselineUseAsyncWriter -eq [bool]$CandidateUseAsyncWriter -and
    $BaselineAsyncWriterQueueDepth -eq $CandidateAsyncWriterQueueDepth
)
$comparisonMode = if ($isIdentityComparison) { "identity-aa" } else { "feature-ab" }

if ($DryRun) {
    [pscustomobject]@{
        schema = "release-cdng-export-profile-ab-plan.v1"
        bundleDir = $bundleDir
        maxFrames = $MaxFrames
        comparisonMode = $comparisonMode
        isIdentityComparison = $isIdentityComparison
        runOrder = $RunOrder
        baseline = [pscustomobject]@{
            usePayloadHandoff = [bool]$BaselineUsePayloadHandoff
            useAsyncWriter = [bool]$BaselineUseAsyncWriter
            asyncWriterQueueDepth = $BaselineAsyncWriterQueueDepth
            args = $baselineArgs
        }
        candidate = [pscustomobject]@{
            usePayloadHandoff = [bool]$CandidateUsePayloadHandoff
            useAsyncWriter = [bool]$CandidateUseAsyncWriter
            asyncWriterQueueDepth = $CandidateAsyncWriterQueueDepth
            args = $candidateArgs
        }
        compareOutput = $compareJson
    } | ConvertTo-Json -Depth 8
    return
}

New-Item -ItemType Directory -Force -Path $baselineDir, $candidateDir | Out-Null

$baselineRun = $null
$candidateRun = $null
$runPlan = @(
    [pscustomobject]@{ label = "baseline"; args = $baselineArgs },
    [pscustomobject]@{ label = "candidate"; args = $candidateArgs }
)
if ($RunOrder -eq "CandidateFirst") {
    $runPlan = @(
        [pscustomobject]@{ label = "candidate"; args = $candidateArgs },
        [pscustomobject]@{ label = "baseline"; args = $baselineArgs }
    )
}

foreach ($runStep in $runPlan) {
    $runResult = Invoke-ProfileRun -Label $runStep.label -CommandArgs $runStep.args
    if ($runStep.label -eq "baseline") {
        $baselineRun = $runResult
    }
    else {
        $candidateRun = $runResult
    }
    if ($runResult.exitCode -ne 0) {
        throw "$($runStep.label) CDNG export profile failed with exit code $($runResult.exitCode)"
    }
}

$compareArgs = @(
    "-NoLogo", "-NoProfile", "-NonInteractive", "-ExecutionPolicy", "Bypass",
    "-File", $comparator,
    "-Baseline", $baselineProfile,
    "-Candidate", $candidateProfile,
    "-Output", $compareJson,
    "-MaxFrameTotalRegressionPercent", $MaxFrameTotalRegressionPercent,
    "-MaxFrameTotalP95RegressionPercent", $MaxFrameTotalP95RegressionPercent
)
if ($FailOnRegression) {
    $compareArgs += "-FailOnRegression"
}

& pwsh.exe @compareArgs
$compareExit = $LASTEXITCODE
$compare = $null
if (Test-Path -LiteralPath $compareJson) {
    $compare = Get-Content -LiteralPath $compareJson -Raw | ConvertFrom-Json -Depth 100
}
if ($compareExit -ne 0) {
    if ($null -eq $compare) {
        throw "Export stage comparison failed with exit code $compareExit and no compare JSON was written."
    }
}

$baselineProfileJson = Get-Content -LiteralPath $baselineProfile -Raw | ConvertFrom-Json -Depth 100
$candidateProfileJson = Get-Content -LiteralPath $candidateProfile -Raw | ConvertFrom-Json -Depth 100
$elapsedDelta = New-ElapsedDelta -BaselineMs $baselineRun.elapsedMs -CandidateMs $candidateRun.elapsedMs
$compareFailures = @()
if ($compare) {
    foreach ($failure in @($compare.failures)) {
        if ($null -ne $failure) {
            $compareFailures += [string]$failure
        }
    }
}
else {
    $compareFailures += "compare-json-missing"
}
$summary = [pscustomobject]@{
    schema = "release-cdng-export-profile-ab.v1"
    generatedAtUtc = (Get-Date).ToUniversalTime().ToString("o")
    bundleDir = $bundleDir
    maxFrames = $MaxFrames
    comparisonMode = $comparisonMode
    isIdentityComparison = $isIdentityComparison
    runOrder = $RunOrder
    baseline = [pscustomobject]@{
        usePayloadHandoff = [bool]$BaselineUsePayloadHandoff
        useAsyncWriter = [bool]$BaselineUseAsyncWriter
        profile = $baselineProfile
        log = $baselineLog
        dngOutputDir = $baselineDngDir
        buildId = $baselineProfileJson.build_id
        frameCount = $baselineProfileJson.frame_count
        payloadHandoffEnvEnabled = $baselineProfileJson.payload_handoff_env_enabled
        asyncWriterEnvEnabled = $baselineProfileJson.async_writer_env_enabled
        asyncWriterQueueCapacity = $baselineProfileJson.async_writer_queue_capacity
        asyncWriterMaxQueued = $baselineProfileJson.async_writer_max_queued
        elapsedMs = $baselineRun.elapsedMs
    }
    candidate = [pscustomobject]@{
        usePayloadHandoff = [bool]$CandidateUsePayloadHandoff
        useAsyncWriter = [bool]$CandidateUseAsyncWriter
        profile = $candidateProfile
        log = $candidateLog
        dngOutputDir = $candidateDngDir
        buildId = $candidateProfileJson.build_id
        frameCount = $candidateProfileJson.frame_count
        payloadHandoffEnvEnabled = $candidateProfileJson.payload_handoff_env_enabled
        asyncWriterEnvEnabled = $candidateProfileJson.async_writer_env_enabled
        asyncWriterQueueCapacity = $candidateProfileJson.async_writer_queue_capacity
        asyncWriterMaxQueued = $candidateProfileJson.async_writer_max_queued
        elapsedMs = $candidateRun.elapsedMs
    }
    compare = [pscustomobject]@{
        profile = $compareJson
        verdict = if ($compare) { $compare.verdict } else { "ERROR" }
        elapsedDeltaMs = $elapsedDelta.deltaMs
        elapsedDeltaPercent = $elapsedDelta.deltaPercent
        frameTotalAvgDeltaMs = if ($compare) { $compare.stages.frame_total_ms.avgMs.delta } else { $null }
        frameTotalAvgDeltaPercent = if ($compare) { $compare.stages.frame_total_ms.avgMs.deltaPercent } else { $null }
        frameTotalP95DeltaMs = if ($compare) { $compare.stages.frame_total_ms.p95Ms.delta } else { $null }
        frameTotalP95DeltaPercent = if ($compare) { $compare.stages.frame_total_ms.p95Ms.deltaPercent } else { $null }
        queueIdleAvgDeltaMs = if ($compare) { $compare.stages.queue_idle_ms.avgMs.delta } else { $null }
        queueIdleP95DeltaMs = if ($compare) { $compare.stages.queue_idle_ms.p95Ms.delta } else { $null }
        payloadCloneAvgDeltaMs = if ($compare -and $compare.stages.payload_clone_ms) { $compare.stages.payload_clone_ms.avgMs.delta } else { $null }
        payloadCloneP95DeltaMs = if ($compare -and $compare.stages.payload_clone_ms) { $compare.stages.payload_clone_ms.p95Ms.delta } else { $null }
        writerQueueWaitAvgDeltaMs = if ($compare -and $compare.stages.writer_queue_wait_ms) { $compare.stages.writer_queue_wait_ms.avgMs.delta } else { $null }
        writerQueueWaitP95DeltaMs = if ($compare -and $compare.stages.writer_queue_wait_ms) { $compare.stages.writer_queue_wait_ms.p95Ms.delta } else { $null }
        producerFrameAvgDeltaMs = if ($compare -and $compare.stages.producer_frame_ms) { $compare.stages.producer_frame_ms.avgMs.delta } else { $null }
        producerFrameP95DeltaMs = if ($compare -and $compare.stages.producer_frame_ms) { $compare.stages.producer_frame_ms.p95Ms.delta } else { $null }
        producerQueueIdleAvgDeltaMs = if ($compare -and $compare.stages.producer_queue_idle_ms) { $compare.stages.producer_queue_idle_ms.avgMs.delta } else { $null }
        producerQueueIdleP95DeltaMs = if ($compare -and $compare.stages.producer_queue_idle_ms) { $compare.stages.producer_queue_idle_ms.p95Ms.delta } else { $null }
        writerCompletionLagAvgDeltaMs = if ($compare -and $compare.stages.writer_completion_lag_ms) { $compare.stages.writer_completion_lag_ms.avgMs.delta } else { $null }
        writerCompletionLagP95DeltaMs = if ($compare -and $compare.stages.writer_completion_lag_ms) { $compare.stages.writer_completion_lag_ms.p95Ms.delta } else { $null }
        failures = $compareFailures
    }
    verdict = if ($compareExit -eq 0 -and $compare -and $compare.verdict -eq "PASS") { "PASS" } else { "FAIL" }
}

$summary | ConvertTo-Json -Depth 16 | Set-Content -LiteralPath $summaryJson -Encoding UTF8

Write-Host ((
    "CDNG-EXPORT-AB verdict={0} comparison_mode={1} run_order={2} " +
    "baseline_payload={3} candidate_payload={4} baseline_async={5} candidate_async={6} " +
    "baseline_async_queue_capacity={7} candidate_async_queue_capacity={8} " +
    "elapsed_delta_ms={9} elapsed_delta_percent={10} " +
    "frame_total_avg_delta_ms={11} frame_total_p95_delta_ms={12} " +
    "queue_idle_avg_delta_ms={13} payload_clone_avg_delta_ms={14} " +
    "writer_queue_wait_avg_delta_ms={15} producer_frame_avg_delta_ms={16} " +
    "producer_queue_idle_avg_delta_ms={17} writer_completion_lag_avg_delta_ms={18} " +
    "output={19}") -f
    $summary.verdict,
    $summary.comparisonMode,
    $summary.runOrder,
    $summary.baseline.usePayloadHandoff,
    $summary.candidate.usePayloadHandoff,
    $summary.baseline.useAsyncWriter,
    $summary.candidate.useAsyncWriter,
    $summary.baseline.asyncWriterQueueCapacity,
    $summary.candidate.asyncWriterQueueCapacity,
    $summary.compare.elapsedDeltaMs,
    $summary.compare.elapsedDeltaPercent,
    $summary.compare.frameTotalAvgDeltaMs,
    $summary.compare.frameTotalP95DeltaMs,
    $summary.compare.queueIdleAvgDeltaMs,
    $summary.compare.payloadCloneAvgDeltaMs,
    $summary.compare.writerQueueWaitAvgDeltaMs,
    $summary.compare.producerFrameAvgDeltaMs,
    $summary.compare.producerQueueIdleAvgDeltaMs,
    $summary.compare.writerCompletionLagAvgDeltaMs,
    $summaryJson
)

$summary | ConvertTo-Json -Depth 16

if ($summary.verdict -ne "PASS") {
    exit 1
}
