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

if ($DryRun) {
    [pscustomobject]@{
        schema = "release-cdng-export-profile-ab-plan.v1"
        bundleDir = $bundleDir
        maxFrames = $MaxFrames
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

& pwsh.exe @baselineArgs
if ($LASTEXITCODE -ne 0) {
    throw "Baseline CDNG export profile failed with exit code $LASTEXITCODE"
}

& pwsh.exe @candidateArgs
if ($LASTEXITCODE -ne 0) {
    throw "Candidate CDNG export profile failed with exit code $LASTEXITCODE"
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
    }
    compare = [pscustomobject]@{
        profile = $compareJson
        verdict = if ($compare) { $compare.verdict } else { "ERROR" }
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
        failures = $compareFailures
    }
    verdict = if ($compareExit -eq 0 -and $compare -and $compare.verdict -eq "PASS") { "PASS" } else { "FAIL" }
}

$summary | ConvertTo-Json -Depth 16 | Set-Content -LiteralPath $summaryJson -Encoding UTF8

Write-Host ((
    "CDNG-EXPORT-AB verdict={0} baseline_payload={1} candidate_payload={2} " +
    "baseline_async={3} candidate_async={4} " +
    "baseline_async_queue_capacity={5} candidate_async_queue_capacity={6} " +
    "frame_total_avg_delta_ms={7} frame_total_p95_delta_ms={8} " +
    "queue_idle_avg_delta_ms={9} payload_clone_avg_delta_ms={10} " +
    "writer_queue_wait_avg_delta_ms={11} output={12}") -f
    $summary.verdict,
    $summary.baseline.usePayloadHandoff,
    $summary.candidate.usePayloadHandoff,
    $summary.baseline.useAsyncWriter,
    $summary.candidate.useAsyncWriter,
    $summary.baseline.asyncWriterQueueCapacity,
    $summary.candidate.asyncWriterQueueCapacity,
    $summary.compare.frameTotalAvgDeltaMs,
    $summary.compare.frameTotalP95DeltaMs,
    $summary.compare.queueIdleAvgDeltaMs,
    $summary.compare.payloadCloneAvgDeltaMs,
    $summary.compare.writerQueueWaitAvgDeltaMs,
    $summaryJson
)

$summary | ConvertTo-Json -Depth 16

if ($summary.verdict -ne "PASS") {
    exit 1
}
