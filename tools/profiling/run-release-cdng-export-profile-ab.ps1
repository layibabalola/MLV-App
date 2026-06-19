param(
    [string]$RepoRoot = ".",
    [string]$ExePath = "",
    [Alias("Input")]
    [string]$ClipPath = "",
    [string]$Receipt = "",
    [ValidateSet("", "uncompressed", "lossless", "fast-pass")]
    [string]$CdngCodec = "",
    [string]$OutputDir = "",
    [string]$BuildId = "",
    [switch]$BaselineUsePayloadHandoff,
    [switch]$CandidateUsePayloadHandoff,
    [switch]$BaselineUseAsyncWriter,
    [switch]$CandidateUseAsyncWriter,
    [switch]$BaselineUseAsyncWriterCompression,
    [switch]$CandidateUseAsyncWriterCompression,
    [int]$BaselineAsyncWriterQueueDepth = 0,
    [int]$CandidateAsyncWriterQueueDepth = 0,
    [int]$BaselineAsyncWriterThreadCount = 0,
    [int]$CandidateAsyncWriterThreadCount = 0,
    [int]$MaxFrames = 0,
    [switch]$EnableGpuExport,
    [switch]$BaselineEnableGpuExport,
    [switch]$CandidateEnableGpuExport,
    [string]$GpuExportDll = "",
    [string]$BaselineGpuExportDll = "",
    [string]$CandidateGpuExportDll = "",
    [switch]$RequireBaselineNoGpuExportAttempt,
    [switch]$RequireCandidateGpuExportAttempt,
    [switch]$RequireCandidateGpuExportReplacement,
    [switch]$RequireDngHashMatch,
    [ValidateSet("BaselineFirst", "CandidateFirst")]
    [string]$RunOrder = "BaselineFirst",
    [double]$MaxFrameTotalRegressionPercent = 5.0,
    [double]$MaxFrameTotalP95RegressionPercent = 10.0,
    [switch]$FailOnRegression,
    [switch]$DryRun
)

$ErrorActionPreference = "Stop"

if ($BaselineUseAsyncWriterCompression -and -not $BaselineUseAsyncWriter) {
    throw "-BaselineUseAsyncWriterCompression requires -BaselineUseAsyncWriter."
}
if ($CandidateUseAsyncWriterCompression -and -not $CandidateUseAsyncWriter) {
    throw "-CandidateUseAsyncWriterCompression requires -CandidateUseAsyncWriter."
}

$root = (Resolve-Path -LiteralPath $RepoRoot).Path
$runner = Join-Path $root "tools\profiling\run-release-cdng-export-profile.ps1"
$comparator = Join-Path $root "tools\profiling\compare-export-stage-profiles.ps1"
$hashComparator = Join-Path $root "tools\profiling\compare-cdng-dng-output-hashes.ps1"
if (-not (Test-Path -LiteralPath $runner)) {
    throw "Release CDNG export profiler not found: $runner"
}
if (-not (Test-Path -LiteralPath $comparator)) {
    throw "Export stage comparator not found: $comparator"
}
if (-not (Test-Path -LiteralPath $hashComparator)) {
    throw "DNG hash comparator not found: $hashComparator"
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

$baselineGpuExportEnabled = [bool]$EnableGpuExport -or [bool]$BaselineEnableGpuExport
$candidateGpuExportEnabled = [bool]$EnableGpuExport -or [bool]$CandidateEnableGpuExport
$baselineGpuExportDllEffective = if (-not [string]::IsNullOrWhiteSpace($BaselineGpuExportDll)) {
    $BaselineGpuExportDll
}
else {
    $GpuExportDll
}
$candidateGpuExportDllEffective = if (-not [string]::IsNullOrWhiteSpace($CandidateGpuExportDll)) {
    $CandidateGpuExportDll
}
else {
    $GpuExportDll
}
$baselineGpuExportDllSummary = if ($baselineGpuExportEnabled) { $baselineGpuExportDllEffective } else { "" }
$candidateGpuExportDllSummary = if ($candidateGpuExportEnabled) { $candidateGpuExportDllEffective } else { "" }

function New-ProfileArgs {
    param(
        [string]$Label,
        [string]$DngDir,
        [string]$ProfilePath,
        [string]$LogPath,
        [bool]$UsePayloadHandoff,
        [bool]$UseAsyncWriter,
        [bool]$UseAsyncWriterCompression,
        [int]$AsyncWriterQueueDepth,
        [int]$AsyncWriterThreadCount,
        [bool]$UseGpuExport,
        [string]$GpuExportDllPath
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
    if (-not [string]::IsNullOrWhiteSpace($CdngCodec)) {
        $args += @("-CdngCodec", $CdngCodec)
    }
    if (-not [string]::IsNullOrWhiteSpace($BuildId)) {
        $args += @("-BuildId", "$BuildId-$Label")
    }
    if ($UseGpuExport) {
        $args += "-EnableGpuExport"
        if (-not [string]::IsNullOrWhiteSpace($GpuExportDllPath)) {
            $args += @("-GpuExportDll", $GpuExportDllPath)
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
        if ($UseAsyncWriterCompression) {
            $args += "-UseAsyncWriterCompression"
        }
        if ($AsyncWriterQueueDepth -gt 0) {
            $args += @("-AsyncWriterQueueDepth", "$AsyncWriterQueueDepth")
        }
        if ($AsyncWriterThreadCount -gt 0) {
            $args += @("-AsyncWriterThreadCount", "$AsyncWriterThreadCount")
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

function Get-CompareStageDelta {
    param(
        [object]$Compare,
        [string]$StageName,
        [ValidateSet("avgMs", "p50Ms", "p95Ms")]
        [string]$Statistic
    )

    if ($null -eq $Compare -or $null -eq $Compare.stages) {
        return $null
    }
    $stageProperty = $Compare.stages.PSObject.Properties[$StageName]
    if ($null -eq $stageProperty -or $null -eq $stageProperty.Value) {
        return $null
    }
    $statProperty = $stageProperty.Value.PSObject.Properties[$Statistic]
    if ($null -eq $statProperty -or $null -eq $statProperty.Value) {
        return $null
    }
    $statProperty.Value.delta
}

function Get-CompareCompressionValue {
    param(
        [object]$Compare,
        [string]$MetricName,
        [ValidateSet("baseline", "candidate", "delta", "deltaPercent")]
        [string]$ValueName
    )

    if ($null -eq $Compare -or $null -eq $Compare.compression) {
        return $null
    }
    $metricProperty = $Compare.compression.PSObject.Properties[$MetricName]
    if ($null -eq $metricProperty -or $null -eq $metricProperty.Value) {
        return $null
    }
    $valueProperty = $metricProperty.Value.PSObject.Properties[$ValueName]
    if ($null -eq $valueProperty) {
        return $null
    }
    $valueProperty.Value
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
    -UseAsyncWriterCompression ([bool]$BaselineUseAsyncWriterCompression) `
    -AsyncWriterQueueDepth $BaselineAsyncWriterQueueDepth `
    -AsyncWriterThreadCount $BaselineAsyncWriterThreadCount `
    -UseGpuExport $baselineGpuExportEnabled `
    -GpuExportDllPath $baselineGpuExportDllEffective
$candidateArgs = New-ProfileArgs `
    -Label "candidate" `
    -DngDir $candidateDngDir `
    -ProfilePath $candidateProfile `
    -LogPath $candidateLog `
    -UsePayloadHandoff ([bool]$CandidateUsePayloadHandoff) `
    -UseAsyncWriter ([bool]$CandidateUseAsyncWriter) `
    -UseAsyncWriterCompression ([bool]$CandidateUseAsyncWriterCompression) `
    -AsyncWriterQueueDepth $CandidateAsyncWriterQueueDepth `
    -AsyncWriterThreadCount $CandidateAsyncWriterThreadCount `
    -UseGpuExport $candidateGpuExportEnabled `
    -GpuExportDllPath $candidateGpuExportDllEffective

$isIdentityComparison = (
    [bool]$BaselineUsePayloadHandoff -eq [bool]$CandidateUsePayloadHandoff -and
    [bool]$BaselineUseAsyncWriter -eq [bool]$CandidateUseAsyncWriter -and
    [bool]$BaselineUseAsyncWriterCompression -eq [bool]$CandidateUseAsyncWriterCompression -and
    $BaselineAsyncWriterQueueDepth -eq $CandidateAsyncWriterQueueDepth -and
    $BaselineAsyncWriterThreadCount -eq $CandidateAsyncWriterThreadCount -and
    $baselineGpuExportEnabled -eq $candidateGpuExportEnabled -and
    (
        (-not $baselineGpuExportEnabled -and -not $candidateGpuExportEnabled) -or
        $baselineGpuExportDllEffective -eq $candidateGpuExportDllEffective
    )
)
$comparisonMode = if ($isIdentityComparison) { "identity-aa" } else { "feature-ab" }

if ($DryRun) {
    [pscustomobject]@{
        schema = "release-cdng-export-profile-ab-plan.v1"
        bundleDir = $bundleDir
        maxFrames = $MaxFrames
        cdngCodec = $CdngCodec
        comparisonMode = $comparisonMode
        isIdentityComparison = $isIdentityComparison
        runOrder = $RunOrder
        baseline = [pscustomobject]@{
            usePayloadHandoff = [bool]$BaselineUsePayloadHandoff
            useAsyncWriter = [bool]$BaselineUseAsyncWriter
            useAsyncWriterCompression = [bool]$BaselineUseAsyncWriterCompression
            enableGpuExport = $baselineGpuExportEnabled
            gpuExportDll = $baselineGpuExportDllSummary
            asyncWriterQueueDepth = $BaselineAsyncWriterQueueDepth
            asyncWriterThreadCount = $BaselineAsyncWriterThreadCount
            args = $baselineArgs
        }
        candidate = [pscustomobject]@{
            usePayloadHandoff = [bool]$CandidateUsePayloadHandoff
            useAsyncWriter = [bool]$CandidateUseAsyncWriter
            useAsyncWriterCompression = [bool]$CandidateUseAsyncWriterCompression
            enableGpuExport = $candidateGpuExportEnabled
            gpuExportDll = $candidateGpuExportDllSummary
            asyncWriterQueueDepth = $CandidateAsyncWriterQueueDepth
            asyncWriterThreadCount = $CandidateAsyncWriterThreadCount
            args = $candidateArgs
        }
        proofGates = [pscustomobject]@{
            requireBaselineNoGpuExportAttempt = [bool]$RequireBaselineNoGpuExportAttempt
            requireCandidateGpuExportAttempt = [bool]$RequireCandidateGpuExportAttempt
            requireCandidateGpuExportReplacement = [bool]$RequireCandidateGpuExportReplacement
            requireDngHashMatch = [bool]$RequireDngHashMatch
        }
        compareOutput = $compareJson
        dngHashOutput = Join-Path $bundleDir "dng-hash-comparison.json"
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
$proofFailures = @()
$baselineGpuExportAttemptedFrames = [int]$baselineProfileJson.gpu_export_attempted_frames
$candidateGpuExportAttemptedFrames = [int]$candidateProfileJson.gpu_export_attempted_frames
$candidateGpuExportReplacedFrames = [int]$candidateProfileJson.gpu_export_replaced_frames
$candidateFrameCount = [int]$candidateProfileJson.frame_count
if ($RequireBaselineNoGpuExportAttempt -and $baselineGpuExportAttemptedFrames -ne 0) {
    $proofFailures += "baseline-gpu-export-attempted-frames expected=0 actual=$baselineGpuExportAttemptedFrames"
}
if ($RequireCandidateGpuExportAttempt) {
    if (-not $candidateGpuExportEnabled) {
        $proofFailures += "candidate-gpu-export-not-enabled"
    }
    if ($candidateGpuExportAttemptedFrames -ne $candidateFrameCount) {
        $proofFailures += "candidate-gpu-export-attempted-frame-count expected=$candidateFrameCount actual=$candidateGpuExportAttemptedFrames"
    }
}
if ($RequireCandidateGpuExportReplacement) {
    if (-not $candidateGpuExportEnabled) {
        $proofFailures += "candidate-gpu-export-not-enabled"
    }
    if ($candidateGpuExportReplacedFrames -ne $candidateFrameCount) {
        $proofFailures += "candidate-gpu-export-replaced-frame-count expected=$candidateFrameCount actual=$candidateGpuExportReplacedFrames"
    }
}
$summaryFailures = @($compareFailures + $proofFailures)
$summary = [pscustomobject]@{
    schema = "release-cdng-export-profile-ab.v1"
    generatedAtUtc = (Get-Date).ToUniversalTime().ToString("o")
    bundleDir = $bundleDir
    maxFrames = $MaxFrames
    cdngCodec = $CdngCodec
    comparisonMode = $comparisonMode
    isIdentityComparison = $isIdentityComparison
    runOrder = $RunOrder
    baseline = [pscustomobject]@{
        usePayloadHandoff = [bool]$BaselineUsePayloadHandoff
        useAsyncWriter = [bool]$BaselineUseAsyncWriter
        useAsyncWriterCompression = [bool]$BaselineUseAsyncWriterCompression
        enableGpuExport = $baselineGpuExportEnabled
        gpuExportDll = $baselineGpuExportDllSummary
        profile = $baselineProfile
        log = $baselineLog
        dngOutputDir = $baselineDngDir
        buildId = $baselineProfileJson.build_id
        frameCount = $baselineProfileJson.frame_count
        payloadHandoffEnvEnabled = $baselineProfileJson.payload_handoff_env_enabled
        asyncWriterEnvEnabled = $baselineProfileJson.async_writer_env_enabled
        asyncWriterCompressEnvEnabled = $baselineProfileJson.async_writer_compress_env_enabled
        asyncWriterThreadCount = $baselineProfileJson.async_writer_thread_count
        asyncWriterQueueCapacity = $baselineProfileJson.async_writer_queue_capacity
        asyncWriterMaxQueued = $baselineProfileJson.async_writer_max_queued
        asyncWriterJobsStarted = $baselineProfileJson.async_writer_jobs_started
        asyncWriterJobsFinished = $baselineProfileJson.async_writer_jobs_finished
        asyncWriterMaxActive = $baselineProfileJson.async_writer_max_active
        gpuExportAttemptedFrames = $baselineProfileJson.gpu_export_attempted_frames
        gpuExportReplacedFrames = $baselineProfileJson.gpu_export_replaced_frames
        gpuExportAllocatedBytesValidFrames = $baselineProfileJson.gpu_export_allocated_bytes_valid_frames
        gpuExportMaxAllocatedBytes = $baselineProfileJson.gpu_export_max_allocated_bytes
        dngCompressBytesValidFrames = $baselineProfileJson.dng_compress_bytes_valid_frames
        dngCompressInputBytesTotal = $baselineProfileJson.dng_compress_input_bytes_total
        dngCompressOutputBytesTotal = $baselineProfileJson.dng_compress_output_bytes_total
        dngCompressPlacement = $baselineProfileJson.dng_compress_placement
        asyncWriterCanOverlapDngCompress = $baselineProfileJson.async_writer_can_overlap_dng_compress
        dngCompressInputMiBPerSecond = Get-CompareCompressionValue -Compare $compare -MetricName "dngCompressInputMiBPerSecond" -ValueName "baseline"
        dngCompressOutputMiBPerSecond = Get-CompareCompressionValue -Compare $compare -MetricName "dngCompressOutputMiBPerSecond" -ValueName "baseline"
        dngCompressOutputRatio = Get-CompareCompressionValue -Compare $compare -MetricName "dngCompressOutputRatio" -ValueName "baseline"
        elapsedMs = $baselineRun.elapsedMs
    }
    candidate = [pscustomobject]@{
        usePayloadHandoff = [bool]$CandidateUsePayloadHandoff
        useAsyncWriter = [bool]$CandidateUseAsyncWriter
        useAsyncWriterCompression = [bool]$CandidateUseAsyncWriterCompression
        enableGpuExport = $candidateGpuExportEnabled
        gpuExportDll = $candidateGpuExportDllSummary
        profile = $candidateProfile
        log = $candidateLog
        dngOutputDir = $candidateDngDir
        buildId = $candidateProfileJson.build_id
        frameCount = $candidateProfileJson.frame_count
        payloadHandoffEnvEnabled = $candidateProfileJson.payload_handoff_env_enabled
        asyncWriterEnvEnabled = $candidateProfileJson.async_writer_env_enabled
        asyncWriterCompressEnvEnabled = $candidateProfileJson.async_writer_compress_env_enabled
        asyncWriterThreadCount = $candidateProfileJson.async_writer_thread_count
        asyncWriterQueueCapacity = $candidateProfileJson.async_writer_queue_capacity
        asyncWriterMaxQueued = $candidateProfileJson.async_writer_max_queued
        asyncWriterJobsStarted = $candidateProfileJson.async_writer_jobs_started
        asyncWriterJobsFinished = $candidateProfileJson.async_writer_jobs_finished
        asyncWriterMaxActive = $candidateProfileJson.async_writer_max_active
        gpuExportAttemptedFrames = $candidateProfileJson.gpu_export_attempted_frames
        gpuExportReplacedFrames = $candidateProfileJson.gpu_export_replaced_frames
        gpuExportAllocatedBytesValidFrames = $candidateProfileJson.gpu_export_allocated_bytes_valid_frames
        gpuExportMaxAllocatedBytes = $candidateProfileJson.gpu_export_max_allocated_bytes
        dngCompressBytesValidFrames = $candidateProfileJson.dng_compress_bytes_valid_frames
        dngCompressInputBytesTotal = $candidateProfileJson.dng_compress_input_bytes_total
        dngCompressOutputBytesTotal = $candidateProfileJson.dng_compress_output_bytes_total
        dngCompressPlacement = $candidateProfileJson.dng_compress_placement
        asyncWriterCanOverlapDngCompress = $candidateProfileJson.async_writer_can_overlap_dng_compress
        dngCompressInputMiBPerSecond = Get-CompareCompressionValue -Compare $compare -MetricName "dngCompressInputMiBPerSecond" -ValueName "candidate"
        dngCompressOutputMiBPerSecond = Get-CompareCompressionValue -Compare $compare -MetricName "dngCompressOutputMiBPerSecond" -ValueName "candidate"
        dngCompressOutputRatio = Get-CompareCompressionValue -Compare $compare -MetricName "dngCompressOutputRatio" -ValueName "candidate"
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
        llrawprocTotalAvgDeltaMs = Get-CompareStageDelta -Compare $compare -StageName "llrawproc_total_ms" -Statistic "avgMs"
        llrawprocDualIsoAvgDeltaMs = Get-CompareStageDelta -Compare $compare -StageName "llrawproc_dual_iso_ms" -Statistic "avgMs"
        llrawprocChromaSmoothAvgDeltaMs = Get-CompareStageDelta -Compare $compare -StageName "llrawproc_chroma_smooth_ms" -Statistic "avgMs"
        llrawprocOtherAvgDeltaMs = Get-CompareStageDelta -Compare $compare -StageName "llrawproc_other_ms" -Statistic "avgMs"
        dngCompressAvgDeltaMs = Get-CompareStageDelta -Compare $compare -StageName "dng_compress_ms" -Statistic "avgMs"
        dngCompressEncodeAvgDeltaMs = Get-CompareStageDelta -Compare $compare -StageName "dng_compress_encode_ms" -Statistic "avgMs"
        dngCompressCopyAvgDeltaMs = Get-CompareStageDelta -Compare $compare -StageName "dng_compress_copy_ms" -Statistic "avgMs"
        dngCompressCleanupAvgDeltaMs = Get-CompareStageDelta -Compare $compare -StageName "dng_compress_cleanup_ms" -Statistic "avgMs"
        dngCompressInputBytesTotalDelta = Get-CompareCompressionValue -Compare $compare -MetricName "dngCompressInputBytesTotal" -ValueName "delta"
        dngCompressOutputBytesTotalDelta = Get-CompareCompressionValue -Compare $compare -MetricName "dngCompressOutputBytesTotal" -ValueName "delta"
        dngCompressInputMiBPerSecondDelta = Get-CompareCompressionValue -Compare $compare -MetricName "dngCompressInputMiBPerSecond" -ValueName "delta"
        dngCompressInputMiBPerSecondDeltaPercent = Get-CompareCompressionValue -Compare $compare -MetricName "dngCompressInputMiBPerSecond" -ValueName "deltaPercent"
        dngCompressOutputMiBPerSecondDelta = Get-CompareCompressionValue -Compare $compare -MetricName "dngCompressOutputMiBPerSecond" -ValueName "delta"
        dngCompressOutputMiBPerSecondDeltaPercent = Get-CompareCompressionValue -Compare $compare -MetricName "dngCompressOutputMiBPerSecond" -ValueName "deltaPercent"
        dngCompressOutputRatioDelta = Get-CompareCompressionValue -Compare $compare -MetricName "dngCompressOutputRatio" -ValueName "delta"
        dngCompressPlacement = if ($compare -and $compare.compression) { $compare.compression.dngCompressPlacement } else { $null }
        asyncWriterCanOverlapDngCompress = if ($compare -and $compare.compression) { $compare.compression.asyncWriterCanOverlapDngCompress } else { $null }
        failures = $summaryFailures
    }
    proofGates = [pscustomobject]@{
        requireBaselineNoGpuExportAttempt = [bool]$RequireBaselineNoGpuExportAttempt
        requireCandidateGpuExportAttempt = [bool]$RequireCandidateGpuExportAttempt
        requireCandidateGpuExportReplacement = [bool]$RequireCandidateGpuExportReplacement
        requireDngHashMatch = [bool]$RequireDngHashMatch
        failures = $proofFailures
    }
    verdict = if ($compareExit -eq 0 -and $compare -and $compare.verdict -eq "PASS" -and $proofFailures.Count -eq 0) { "PASS" } else { "FAIL" }
}

$summary | ConvertTo-Json -Depth 16 | Set-Content -LiteralPath $summaryJson -Encoding UTF8

if ($RequireDngHashMatch) {
    $hashArgs = @(
        "-NoLogo", "-NoProfile", "-NonInteractive", "-ExecutionPolicy", "Bypass",
        "-File", $hashComparator,
        "-AbSummary", $summaryJson,
        "-FailOnMismatch"
    )
    & pwsh.exe @hashArgs
    $hashExit = $LASTEXITCODE
    $hashOutput = Join-Path $bundleDir "dng-hash-comparison.json"
    $hashComparison = $null
    if (Test-Path -LiteralPath $hashOutput) {
        $hashComparison = Get-Content -LiteralPath $hashOutput -Raw | ConvertFrom-Json -Depth 100
    }

    $hashFailures = @()
    if ($hashExit -ne 0) {
        $hashFailures += "dng-hash-comparison-exit-code actual=$hashExit"
    }
    if ($null -eq $hashComparison) {
        $hashFailures += "dng-hash-comparison-json-missing"
    }
    elseif ([string]$hashComparison.verdict -ne "PASS") {
        $hashFailures += "dng-hash-comparison-verdict actual=$($hashComparison.verdict)"
    }

    $summary | Add-Member -NotePropertyName dngHash -NotePropertyValue ([pscustomobject]@{
        required = $true
        comparison = $hashOutput
        verdict = if ($hashComparison) { $hashComparison.verdict } else { "ERROR" }
        pairs = if ($hashComparison) { $hashComparison.totals.pairs } else { $null }
        matched = if ($hashComparison) { $hashComparison.totals.matched } else { $null }
        mismatched = if ($hashComparison) { $hashComparison.totals.mismatched } else { $null }
        missingBaseline = if ($hashComparison) { $hashComparison.totals.missingBaseline } else { $null }
        missingCandidate = if ($hashComparison) { $hashComparison.totals.missingCandidate } else { $null }
        failures = $hashFailures
    }) -Force

    if ($hashFailures.Count -gt 0) {
        $summary.compare.failures = @($summary.compare.failures + $hashFailures)
        $summary.verdict = "FAIL"
    }
    $summary | ConvertTo-Json -Depth 16 | Set-Content -LiteralPath $summaryJson -Encoding UTF8
}

Write-Host ((
    "CDNG-EXPORT-AB verdict={0} comparison_mode={1} run_order={2} cdng_codec={3} " +
    "baseline_payload={4} candidate_payload={5} baseline_async={6} candidate_async={7} " +
    "baseline_async_compress={8} candidate_async_compress={9} " +
    "baseline_async_threads={10} candidate_async_threads={11} " +
    "baseline_async_queue_capacity={12} candidate_async_queue_capacity={13} " +
    "baseline_async_max_active={14} candidate_async_max_active={15} " +
    "baseline_gpu_export_enabled={16} candidate_gpu_export_enabled={17} " +
    "baseline_gpu_export_attempted={18} candidate_gpu_export_attempted={19} " +
    "candidate_gpu_export_replaced={20} proof_gate_failures={21} " +
    "dng_hash_required={22} dng_hash_verdict={23} " +
    "elapsed_delta_ms={24} elapsed_delta_percent={25} " +
    "frame_total_avg_delta_ms={26} frame_total_p95_delta_ms={27} " +
    "queue_idle_avg_delta_ms={28} payload_clone_avg_delta_ms={29} " +
    "writer_queue_wait_avg_delta_ms={30} producer_frame_avg_delta_ms={31} " +
    "producer_queue_idle_avg_delta_ms={32} writer_completion_lag_avg_delta_ms={33} " +
    "llrawproc_total_avg_delta_ms={34} llrawproc_dual_iso_avg_delta_ms={35} " +
    "dng_compress_avg_delta_ms={36} dng_compress_output_mibps_delta={37} " +
    "dng_compress_output_bytes_delta={38} dng_compress_placement_candidate={39} " +
    "async_writer_can_overlap_dng_compress_candidate={40} output={41}") -f
    $summary.verdict,
    $summary.comparisonMode,
    $summary.runOrder,
    $(if ([string]::IsNullOrWhiteSpace($summary.cdngCodec)) { "default" } else { $summary.cdngCodec }),
    $summary.baseline.usePayloadHandoff,
    $summary.candidate.usePayloadHandoff,
    $summary.baseline.useAsyncWriter,
    $summary.candidate.useAsyncWriter,
    $summary.baseline.useAsyncWriterCompression,
    $summary.candidate.useAsyncWriterCompression,
    $summary.baseline.asyncWriterThreadCount,
    $summary.candidate.asyncWriterThreadCount,
    $summary.baseline.asyncWriterQueueCapacity,
    $summary.candidate.asyncWriterQueueCapacity,
    $summary.baseline.asyncWriterMaxActive,
    $summary.candidate.asyncWriterMaxActive,
    $summary.baseline.enableGpuExport,
    $summary.candidate.enableGpuExport,
    $summary.baseline.gpuExportAttemptedFrames,
    $summary.candidate.gpuExportAttemptedFrames,
    $summary.candidate.gpuExportReplacedFrames,
    $summary.proofGates.failures.Count,
    [bool]$RequireDngHashMatch,
    $(if ($summary.PSObject.Properties["dngHash"]) { $summary.dngHash.verdict } else { "SKIP" }),
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
    $summary.compare.llrawprocTotalAvgDeltaMs,
    $summary.compare.llrawprocDualIsoAvgDeltaMs,
    $summary.compare.dngCompressAvgDeltaMs,
    $summary.compare.dngCompressOutputMiBPerSecondDelta,
    $summary.compare.dngCompressOutputBytesTotalDelta,
    $summary.candidate.dngCompressPlacement,
    $summary.candidate.asyncWriterCanOverlapDngCompress,
    $summaryJson
)

$summary | ConvertTo-Json -Depth 16

if ($summary.verdict -ne "PASS") {
    exit 1
}
