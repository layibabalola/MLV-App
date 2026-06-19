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

function Get-MatrixRuns {
    param([object]$Matrix)

    $runs = @()
    foreach ($case in @($Matrix.cases)) {
        foreach ($run in @($case.runs)) {
            $runs += [pscustomobject]@{
                caseName = [string]$case.name
                repeat = $run.repeat
                runOrder = [string]$run.runOrder
                verdict = [string]$run.verdict
                elapsedDeltaMs = $run.elapsedDeltaMs
                frameTotalAvgDeltaMs = $run.frameTotalAvgDeltaMs
                frameTotalP95DeltaMs = $run.frameTotalP95DeltaMs
                payloadCloneAvgDeltaMs = $run.payloadCloneAvgDeltaMs
                writerCompletionLagAvgDeltaMs = $run.writerCompletionLagAvgDeltaMs
                writerQueueWaitAvgDeltaMs = $run.writerQueueWaitAvgDeltaMs
                failures = @($run.failures)
            }
        }
    }
    $runs
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

    [pscustomobject]@{
        metric = $Name
        identityAverage = if ($identityValues.Count -gt 0) { [Math]::Round(($identityValues | Measure-Object -Average).Average, 6) } else { $null }
        featureAverage = if ($featureValues.Count -gt 0) { [Math]::Round(($featureValues | Measure-Object -Average).Average, 6) } else { $null }
        identityPositiveMax = if ($null -ne $identityPositiveMax) { [Math]::Round($identityPositiveMax, 6) } else { $null }
        featurePositiveMax = if ($null -ne $featurePositiveMax) { [Math]::Round($featurePositiveMax, 6) } else { $null }
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
    "payloadCloneAvgDeltaMs",
    "writerCompletionLagAvgDeltaMs",
    "writerQueueWaitAvgDeltaMs"
)
$metrics = @()
foreach ($metricName in $metricNames) {
    $metrics += New-MetricEnvelope -Name $metricName -IdentityRuns $identityRuns -FeatureRuns $featureRuns
}

$frameMetrics = @($metrics | Where-Object { $_.metric -in @("frameTotalAvgDeltaMs", "frameTotalP95DeltaMs") })
$exceededFrameMetrics = @($frameMetrics | Where-Object { -not $_.withinIdentityEnvelope })
$verdict = if ($exceededFrameMetrics.Count -eq 0) {
    "WITHIN_IDENTITY_ENVELOPE"
}
else {
    "EXCEEDS_IDENTITY_ENVELOPE"
}

$identityFailures = @($identityRuns | Where-Object { $_.verdict -ne "PASS" })
$featureFailures = @($featureRuns | Where-Object { $_.verdict -ne "PASS" })

$result = [pscustomobject]@{
    schema = "release-cdng-export-matrix-calibration.v1"
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
    metrics = $metrics
    exceededFrameMetrics = @($exceededFrameMetrics | Select-Object metric, identityPositiveMax, featurePositiveMax)
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
    "identity_fail={2} feature_fail={3} frame_avg_within={4} frame_p95_within={5} output={6}") -f
    $result.verdict,
    $result.identityRawGateUnstable,
    $result.identity.failCount,
    $result.feature.failCount,
    ($metrics | Where-Object { $_.metric -eq "frameTotalAvgDeltaMs" }).withinIdentityEnvelope,
    ($metrics | Where-Object { $_.metric -eq "frameTotalP95DeltaMs" }).withinIdentityEnvelope,
    $(if ([string]::IsNullOrWhiteSpace($Output)) { "<stdout-json>" } else { $resolvedOutput })
))

$json

if ($verdict -ne "WITHIN_IDENTITY_ENVELOPE") {
    exit 1
}
