param(
    [Parameter(Mandatory=$true)]
    [string]$IdentityMatrix,
    [Parameter(Mandatory=$true)]
    [string]$FeatureMatrix,
    [string]$Output = ""
)

$ErrorActionPreference = "Stop"

function Read-MatrixSummary {
    param(
        [string]$Path,
        [string]$Label
    )

    $resolved = $ExecutionContext.SessionState.Path.GetUnresolvedProviderPathFromPSPath($Path)
    if (-not (Test-Path -LiteralPath $resolved)) {
        throw "$Label matrix summary not found: $resolved"
    }
    $matrix = Get-Content -LiteralPath $resolved -Raw | ConvertFrom-Json -Depth 100
    if ([string]$matrix.schema -ne "release-cdng-export-profile-matrix.v1") {
        throw "Unexpected $Label matrix schema in ${resolved}: $($matrix.schema)"
    }
    [pscustomobject]@{
        path = $resolved
        matrix = $matrix
    }
}

$script:CompareJsonCache = @{}

function Get-CompareJson {
    param([string]$Path)

    if ([string]::IsNullOrWhiteSpace($Path)) {
        return $null
    }

    $resolved = $ExecutionContext.SessionState.Path.GetUnresolvedProviderPathFromPSPath($Path)
    if (-not (Test-Path -LiteralPath $resolved)) {
        return $null
    }

    if (-not $script:CompareJsonCache.ContainsKey($resolved)) {
        $script:CompareJsonCache[$resolved] =
            Get-Content -LiteralPath $resolved -Raw | ConvertFrom-Json -Depth 100
    }

    $script:CompareJsonCache[$resolved]
}

function Get-CompareStageDelta {
    param(
        [object]$Run,
        [string]$StageName,
        [ValidateSet("avgMs", "p95Ms")]
        [string]$Statistic
    )

    $compare = Get-CompareJson -Path ([string]$Run.compare)
    if ($null -eq $compare -or $null -eq $compare.stages) {
        return $null
    }

    $stageProperty = $compare.stages.PSObject.Properties[$StageName]
    if (-not $stageProperty) {
        return $null
    }

    $statProperty = $stageProperty.Value.PSObject.Properties[$Statistic]
    if (-not $statProperty) {
        return $null
    }

    $deltaProperty = $statProperty.Value.PSObject.Properties["delta"]
    if (-not $deltaProperty) {
        return $null
    }

    $deltaProperty.Value
}

function Get-MatrixRuns {
    param([object]$Matrix)

    $runs = @()
    foreach ($case in @($Matrix.cases)) {
        foreach ($run in @($case.runs)) {
            $runs += [pscustomobject]@{
                caseName = [string]$case.name
                repeat = $run.repeat
                runOrder = [string]$run.runOrder
                compare = [string]$run.compare
                verdict = [string]$run.verdict
                elapsedDeltaMs = $run.elapsedDeltaMs
                frameTotalAvgDeltaMs = $run.frameTotalAvgDeltaMs
                frameTotalP95DeltaMs = $run.frameTotalP95DeltaMs
                rawReadDecodeUnpackAvgDeltaMs =
                    Get-CompareStageDelta -Run $run -StageName "raw_read_decode_unpack_ms" -Statistic "avgMs"
                rawReadAvgDeltaMs =
                    Get-CompareStageDelta -Run $run -StageName "raw_read_ms" -Statistic "avgMs"
                rawDecodeAvgDeltaMs =
                    Get-CompareStageDelta -Run $run -StageName "raw_decode_ms" -Statistic "avgMs"
                rawUnpackAvgDeltaMs =
                    Get-CompareStageDelta -Run $run -StageName "raw_unpack_ms" -Statistic "avgMs"
                llrawprocAvgDeltaMs =
                    Get-CompareStageDelta -Run $run -StageName "llrawproc_ms" -Statistic "avgMs"
                dngHeaderAvgDeltaMs =
                    Get-CompareStageDelta -Run $run -StageName "dng_header_ms" -Statistic "avgMs"
                dngPackAvgDeltaMs =
                    Get-CompareStageDelta -Run $run -StageName "dng_pack_ms" -Statistic "avgMs"
                dngCompressAvgDeltaMs =
                    Get-CompareStageDelta -Run $run -StageName "dng_compress_ms" -Statistic "avgMs"
                diskWriteAvgDeltaMs =
                    Get-CompareStageDelta -Run $run -StageName "disk_write_ms" -Statistic "avgMs"
                payloadCloneAvgDeltaMs = $run.payloadCloneAvgDeltaMs
                writerCompletionLagAvgDeltaMs = $run.writerCompletionLagAvgDeltaMs
                writerQueueWaitAvgDeltaMs = $run.writerQueueWaitAvgDeltaMs
                failures = @($run.failures)
            }
        }
    }
    $runs
}

function New-RunKey {
    param([object]$Run)

    "{0}|repeat={1}|order={2}" -f $Run.caseName, $Run.repeat, $Run.runOrder
}

function Compare-RunKeys {
    param(
        [object[]]$IdentityRuns,
        [object[]]$FeatureRuns
    )

    $identityKeys = @{}
    foreach ($run in $IdentityRuns) {
        $identityKeys[(New-RunKey -Run $run)] = $true
    }

    $featureKeys = @{}
    foreach ($run in $FeatureRuns) {
        $featureKeys[(New-RunKey -Run $run)] = $true
    }

    $identityOnly = @(
        $identityKeys.Keys |
            Where-Object { -not $featureKeys.ContainsKey($_) } |
            Sort-Object
    )
    $featureOnly = @(
        $featureKeys.Keys |
            Where-Object { -not $identityKeys.ContainsKey($_) } |
            Sort-Object
    )
    $common = @(
        $identityKeys.Keys |
            Where-Object { $featureKeys.ContainsKey($_) } |
            Sort-Object
    )

    [pscustomobject]@{
        compatible = $identityOnly.Count -eq 0 -and $featureOnly.Count -eq 0
        identityRunCount = $IdentityRuns.Count
        featureRunCount = $FeatureRuns.Count
        commonRunCount = $common.Count
        identityOnly = $identityOnly
        featureOnly = $featureOnly
    }
}

function Convert-ToNullableDouble {
    param([object]$Value)

    if ($null -eq $Value) {
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

function Get-Percentile {
    param(
        [double[]]$Values,
        [double]$Percentile
    )

    if ($null -eq $Values -or $Values.Count -eq 0) {
        return $null
    }

    $sorted = @($Values | Sort-Object)
    if ($sorted.Count -eq 1) {
        return [double]$sorted[0]
    }

    $rank = ($sorted.Count - 1) * $Percentile
    $lower = [int][Math]::Floor($rank)
    $upper = [int][Math]::Ceiling($rank)
    if ($lower -eq $upper) {
        return [double]$sorted[$lower]
    }

    $weight = $rank - $lower
    ([double]$sorted[$lower] * (1.0 - $weight)) + ([double]$sorted[$upper] * $weight)
}

function New-MetricEnvelope {
    param(
        [string]$Name,
        [object[]]$IdentityRuns,
        [object[]]$FeatureRuns
    )

    $identityValues = @()
    $featureValues = @()
    foreach ($run in $IdentityRuns) {
        $value = Convert-ToNullableDouble $run.$Name
        if ($null -ne $value) {
            $identityValues += $value
        }
    }
    foreach ($run in $FeatureRuns) {
        $value = Convert-ToNullableDouble $run.$Name
        if ($null -ne $value) {
            $featureValues += $value
        }
    }

    $identityPositiveMax = if ($identityValues.Count -gt 0) {
        [Math]::Max(0.0, ($identityValues | Measure-Object -Maximum).Maximum)
    }
    else { $null }
    $featurePositiveMax = if ($featureValues.Count -gt 0) {
        [Math]::Max(0.0, ($featureValues | Measure-Object -Maximum).Maximum)
    }
    else { $null }

    $identityMedian = Get-Percentile -Values ([double[]]$identityValues) -Percentile 0.5
    $featureMedian = Get-Percentile -Values ([double[]]$featureValues) -Percentile 0.5
    $identityP95 = Get-Percentile -Values ([double[]]$identityValues) -Percentile 0.95
    $featureP95 = Get-Percentile -Values ([double[]]$featureValues) -Percentile 0.95

    [pscustomobject]@{
        metric = $Name
        identityCount = $identityValues.Count
        featureCount = $featureValues.Count
        identityAverage = if ($identityValues.Count -gt 0) { [Math]::Round(($identityValues | Measure-Object -Average).Average, 6) } else { $null }
        featureAverage = if ($featureValues.Count -gt 0) { [Math]::Round(($featureValues | Measure-Object -Average).Average, 6) } else { $null }
        identityMedian = if ($null -ne $identityMedian) { [Math]::Round($identityMedian, 6) } else { $null }
        featureMedian = if ($null -ne $featureMedian) { [Math]::Round($featureMedian, 6) } else { $null }
        identityP95 = if ($null -ne $identityP95) { [Math]::Round($identityP95, 6) } else { $null }
        featureP95 = if ($null -ne $featureP95) { [Math]::Round($featureP95, 6) } else { $null }
        identityPositiveMax = if ($null -ne $identityPositiveMax) { [Math]::Round($identityPositiveMax, 6) } else { $null }
        featurePositiveMax = if ($null -ne $featurePositiveMax) { [Math]::Round($featurePositiveMax, 6) } else { $null }
        positiveMaxMarginMs = if ($null -ne $identityPositiveMax -and $null -ne $featurePositiveMax) {
            [Math]::Round($identityPositiveMax - $featurePositiveMax, 6)
        }
        else { $null }
        withinIdentityEnvelope = if ($null -ne $identityPositiveMax -and $null -ne $featurePositiveMax) {
            $featurePositiveMax -le $identityPositiveMax
        }
        else { $false }
    }
}

$identity = Read-MatrixSummary -Path $IdentityMatrix -Label "Identity"
$feature = Read-MatrixSummary -Path $FeatureMatrix -Label "Feature"

$identityRuns = @(Get-MatrixRuns -Matrix $identity.matrix)
$featureRuns = @(Get-MatrixRuns -Matrix $feature.matrix)

$metricNames = @(
    "elapsedDeltaMs",
    "frameTotalAvgDeltaMs",
    "frameTotalP95DeltaMs",
    "rawReadDecodeUnpackAvgDeltaMs",
    "rawReadAvgDeltaMs",
    "rawDecodeAvgDeltaMs",
    "rawUnpackAvgDeltaMs",
    "llrawprocAvgDeltaMs",
    "dngHeaderAvgDeltaMs",
    "dngPackAvgDeltaMs",
    "dngCompressAvgDeltaMs",
    "diskWriteAvgDeltaMs",
    "payloadCloneAvgDeltaMs",
    "writerCompletionLagAvgDeltaMs",
    "writerQueueWaitAvgDeltaMs"
)
$metrics = @()
foreach ($metricName in $metricNames) {
    $metrics += New-MetricEnvelope -Name $metricName -IdentityRuns $identityRuns -FeatureRuns $featureRuns
}

$runKeyCompatibility = Compare-RunKeys -IdentityRuns $identityRuns -FeatureRuns $featureRuns
$modeChecks = [pscustomobject]@{
    identityExpectedMode = "identity-aa"
    identityActualMode = [string]$identity.matrix.comparisonMode
    identityModeOk = [string]$identity.matrix.comparisonMode -eq "identity-aa"
    featureExpectedMode = "feature-ab"
    featureActualMode = [string]$feature.matrix.comparisonMode
    featureModeOk = [string]$feature.matrix.comparisonMode -eq "feature-ab"
    alternateRunOrderMatches =
        [bool]$identity.matrix.options.alternateRunOrder -eq
        [bool]$feature.matrix.options.alternateRunOrder
}

$caseMetrics = @()
$caseNames = @(
    @($identityRuns | ForEach-Object { $_.caseName }) +
    @($featureRuns | ForEach-Object { $_.caseName }) |
        Sort-Object -Unique
)
foreach ($caseName in $caseNames) {
    $identityCaseRuns = @($identityRuns | Where-Object { $_.caseName -eq $caseName })
    $featureCaseRuns = @($featureRuns | Where-Object { $_.caseName -eq $caseName })
    $caseMetricRows = @()
    foreach ($metricName in $metricNames) {
        $caseMetricRows += New-MetricEnvelope `
            -Name $metricName `
            -IdentityRuns $identityCaseRuns `
            -FeatureRuns $featureCaseRuns
    }
    $caseFrameMetrics = @(
        $caseMetricRows |
            Where-Object { $_.metric -in @("frameTotalAvgDeltaMs", "frameTotalP95DeltaMs") }
    )
    $caseMetrics += [pscustomobject]@{
        caseName = $caseName
        identityRunCount = $identityCaseRuns.Count
        featureRunCount = $featureCaseRuns.Count
        frameMetricsWithinIdentityEnvelope =
            @($caseFrameMetrics | Where-Object { -not $_.withinIdentityEnvelope }).Count -eq 0
        metrics = $caseMetricRows
    }
}

$frameMetrics = @($metrics | Where-Object { $_.metric -in @("frameTotalAvgDeltaMs", "frameTotalP95DeltaMs") })
$exceededFrameMetrics = @($frameMetrics | Where-Object { -not $_.withinIdentityEnvelope })
$blockingReasons = @()
if (-not $modeChecks.identityModeOk) {
    $blockingReasons += "identity_matrix_not_identity_aa"
}
if (-not $modeChecks.featureModeOk) {
    $blockingReasons += "feature_matrix_not_feature_ab"
}
if (-not $modeChecks.alternateRunOrderMatches) {
    $blockingReasons += "alternate_run_order_mismatch"
}
if (-not $runKeyCompatibility.compatible) {
    $blockingReasons += "run_key_mismatch"
}
foreach ($metric in $exceededFrameMetrics) {
    $blockingReasons += "feature_exceeds_identity_$($metric.metric)"
}
$hasCompatibilityBlocker =
    -not $modeChecks.identityModeOk `
    -or -not $modeChecks.featureModeOk `
    -or -not $modeChecks.alternateRunOrderMatches `
    -or -not $runKeyCompatibility.compatible
$verdict = if ($blockingReasons.Count -eq 0) {
    "WITHIN_IDENTITY_ENVELOPE"
}
elseif ($hasCompatibilityBlocker) {
    "INCOMPATIBLE_MATRICES"
}
else {
    "EXCEEDS_IDENTITY_ENVELOPE"
}

$identityFailures = @($identityRuns | Where-Object { $_.verdict -ne "PASS" })
$featureFailures = @($featureRuns | Where-Object { $_.verdict -ne "PASS" })

$result = [pscustomobject]@{
    schema = "release-cdng-export-matrix-calibration.v2"
    generatedAtUtc = (Get-Date).ToUniversalTime().ToString("o")
    identity = [pscustomobject]@{
        path = $identity.path
        comparisonMode = $identity.matrix.comparisonMode
        alternateRunOrder = $identity.matrix.options.alternateRunOrder
        verdict = $identity.matrix.verdict
        runCount = $identity.matrix.totals.runCount
        passCount = $identity.matrix.totals.passCount
        failCount = $identity.matrix.totals.failCount
    }
    feature = [pscustomobject]@{
        path = $feature.path
        comparisonMode = $feature.matrix.comparisonMode
        alternateRunOrder = $feature.matrix.options.alternateRunOrder
        verdict = $feature.matrix.verdict
        runCount = $feature.matrix.totals.runCount
        passCount = $feature.matrix.totals.passCount
        failCount = $feature.matrix.totals.failCount
    }
    thresholds = $feature.matrix.thresholds
    identityRawGateUnstable = $identityFailures.Count -gt 0
    modeChecks = $modeChecks
    runKeyCompatibility = $runKeyCompatibility
    metrics = $metrics
    perCaseMetrics = $caseMetrics
    exceededFrameMetrics = @(
        $exceededFrameMetrics |
            Select-Object metric, identityPositiveMax, featurePositiveMax, positiveMaxMarginMs
    )
    blockingReasons = $blockingReasons
    verdict = $verdict
}

$json = $result | ConvertTo-Json -Depth 16
if (-not [string]::IsNullOrWhiteSpace($Output)) {
    $resolvedOutput = $ExecutionContext.SessionState.Path.GetUnresolvedProviderPathFromPSPath($Output)
    $outputDir = Split-Path -Parent $resolvedOutput
    if (-not [string]::IsNullOrWhiteSpace($outputDir)) {
        New-Item -ItemType Directory -Force -Path $outputDir | Out-Null
    }
    Set-Content -LiteralPath $resolvedOutput -Value $json -Encoding UTF8
}

Write-Host (((
    "CDNG-EXPORT-MATRIX-CALIBRATION verdict={0} identity_unstable={1} " +
    "identity_fail={2} feature_fail={3} compatible_keys={4} modes_ok={5} " +
    "frame_avg_within={6} frame_p95_within={7} output={8}") -f
    $result.verdict,
    $result.identityRawGateUnstable,
    $result.identity.failCount,
    $result.feature.failCount,
    $result.runKeyCompatibility.compatible,
    ($result.modeChecks.identityModeOk -and $result.modeChecks.featureModeOk -and
        $result.modeChecks.alternateRunOrderMatches),
    ($metrics | Where-Object { $_.metric -eq "frameTotalAvgDeltaMs" }).withinIdentityEnvelope,
    ($metrics | Where-Object { $_.metric -eq "frameTotalP95DeltaMs" }).withinIdentityEnvelope,
    $(if ([string]::IsNullOrWhiteSpace($Output)) { "<stdout-json>" } else { $resolvedOutput })
))

$json

if ($verdict -ne "WITHIN_IDENTITY_ENVELOPE") {
    exit 1
}
