param(
    [Parameter(Mandatory=$true)]
    [string]$Baseline,
    [Parameter(Mandatory=$true)]
    [string]$Candidate,
    [string]$Output = "",
    [double]$MaxFrameTotalRegressionPercent = 5.0,
    [double]$MaxFrameTotalP95RegressionPercent = 10.0,
    [switch]$FailOnRegression
)

$ErrorActionPreference = "Stop"

function Read-ProfileJson {
    param([string]$Path)

    $resolved = $ExecutionContext.SessionState.Path.GetUnresolvedProviderPathFromPSPath($Path)
    if (-not (Test-Path -LiteralPath $resolved)) {
        throw "Export stage profile not found: $resolved"
    }
    $profile = Get-Content -LiteralPath $resolved -Raw | ConvertFrom-Json -Depth 100
    if ([string]$profile.schema -ne "mlvapp.export_stage_profile.v1") {
        throw "Unexpected export stage profile schema in ${resolved}: $($profile.schema)"
    }
    $profile
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

function New-Delta {
    param(
        [object]$BaselineValue,
        [object]$CandidateValue
    )

    $base = Convert-ToNullableDouble $BaselineValue
    $cand = Convert-ToNullableDouble $CandidateValue
    $delta = $null
    $deltaPercent = $null
    if ($null -ne $base -and $null -ne $cand) {
        $delta = [Math]::Round($cand - $base, 6)
        if ([Math]::Abs($base) -gt 0.000001) {
            $deltaPercent = [Math]::Round((($cand - $base) / $base) * 100.0, 3)
        }
    }

    [pscustomobject]@{
        baseline = $base
        candidate = $cand
        delta = $delta
        deltaPercent = $deltaPercent
    }
}

function Get-StageObject {
    param(
        [object]$Profile,
        [string]$Name
    )

    if ($null -eq $Profile -or $null -eq $Profile.stages) {
        return $null
    }
    $property = $Profile.stages.PSObject.Properties[$Name]
    if ($property) {
        return $property.Value
    }
    $null
}

$baselineProfile = Read-ProfileJson -Path $Baseline
$candidateProfile = Read-ProfileJson -Path $Candidate

$stageNames = [System.Collections.Generic.SortedSet[string]]::new()
foreach ($property in $baselineProfile.stages.PSObject.Properties) {
    [void]$stageNames.Add($property.Name)
}
foreach ($property in $candidateProfile.stages.PSObject.Properties) {
    [void]$stageNames.Add($property.Name)
}

$stageComparisons = [ordered]@{}
foreach ($stageName in $stageNames) {
    $baseStage = Get-StageObject -Profile $baselineProfile -Name $stageName
    $candStage = Get-StageObject -Profile $candidateProfile -Name $stageName
    $stageComparisons[$stageName] = [pscustomobject]@{
        samples = New-Delta `
            -BaselineValue $(if ($baseStage) { $baseStage.samples } else { $null }) `
            -CandidateValue $(if ($candStage) { $candStage.samples } else { $null })
        avgMs = New-Delta `
            -BaselineValue $(if ($baseStage) { $baseStage.avg_ms } else { $null }) `
            -CandidateValue $(if ($candStage) { $candStage.avg_ms } else { $null })
        p50Ms = New-Delta `
            -BaselineValue $(if ($baseStage) { $baseStage.p50_ms } else { $null }) `
            -CandidateValue $(if ($candStage) { $candStage.p50_ms } else { $null })
        p95Ms = New-Delta `
            -BaselineValue $(if ($baseStage) { $baseStage.p95_ms } else { $null }) `
            -CandidateValue $(if ($candStage) { $candStage.p95_ms } else { $null })
    }
}

$failures = @()
$frameTotal = $stageComparisons["frame_total_ms"]
if ($FailOnRegression -and $null -ne $frameTotal) {
    $deltaPercent = $frameTotal.avgMs.deltaPercent
    if ($null -ne $deltaPercent -and $deltaPercent -gt $MaxFrameTotalRegressionPercent) {
        $failures += (
            "frame_total_ms avg regression $deltaPercent% exceeded " +
            "$MaxFrameTotalRegressionPercent%."
        )
    }

    $p95DeltaPercent = $frameTotal.p95Ms.deltaPercent
    if ($null -ne $p95DeltaPercent -and $p95DeltaPercent -gt $MaxFrameTotalP95RegressionPercent) {
        $failures += (
            "frame_total_ms p95 regression $p95DeltaPercent% exceeded " +
            "$MaxFrameTotalP95RegressionPercent%."
        )
    }
}

$result = [pscustomobject]@{
    schema = "export-stage-profile-compare.v1"
    generatedAtUtc = (Get-Date).ToUniversalTime().ToString("o")
    baseline = [pscustomobject]@{
        jsonPath = $ExecutionContext.SessionState.Path.GetUnresolvedProviderPathFromPSPath($Baseline)
        buildId = $baselineProfile.build_id
        frameCount = $baselineProfile.frame_count
        generatedUtc = $baselineProfile.generated_utc
        queueIdleSupported = $baselineProfile.queue_idle_supported
    }
    candidate = [pscustomobject]@{
        jsonPath = $ExecutionContext.SessionState.Path.GetUnresolvedProviderPathFromPSPath($Candidate)
        buildId = $candidateProfile.build_id
        frameCount = $candidateProfile.frame_count
        generatedUtc = $candidateProfile.generated_utc
        queueIdleSupported = $candidateProfile.queue_idle_supported
    }
    thresholds = [pscustomobject]@{
        failOnRegression = [bool]$FailOnRegression
        maxFrameTotalRegressionPercent = $MaxFrameTotalRegressionPercent
        maxFrameTotalP95RegressionPercent = $MaxFrameTotalP95RegressionPercent
    }
    stages = [pscustomobject]$stageComparisons
    failures = $failures
    verdict = if ($failures.Count -eq 0) { "PASS" } else { "FAIL" }
}

$json = $result | ConvertTo-Json -Depth 24
if (-not [string]::IsNullOrWhiteSpace($Output)) {
    $resolvedOutput = $ExecutionContext.SessionState.Path.GetUnresolvedProviderPathFromPSPath($Output)
    $outputDir = Split-Path -Parent $resolvedOutput
    if (-not [string]::IsNullOrWhiteSpace($outputDir)) {
        New-Item -ItemType Directory -Force -Path $outputDir | Out-Null
    }
    Set-Content -LiteralPath $resolvedOutput -Value $json -Encoding UTF8
}

Write-Host ((
    "EXPORT-STAGE-COMPARE verdict={0} frame_total_avg_delta_ms={1} " +
    "frame_total_avg_delta_percent={2} frame_total_p95_delta_ms={3} " +
    "frame_total_p95_delta_percent={4} queue_idle_avg_delta_ms={5} " +
    "queue_idle_p95_delta_ms={6} llrawproc_avg_delta_ms={7} output={8}") -f
    $result.verdict,
    $result.stages.frame_total_ms.avgMs.delta,
    $result.stages.frame_total_ms.avgMs.deltaPercent,
    $result.stages.frame_total_ms.p95Ms.delta,
    $result.stages.frame_total_ms.p95Ms.deltaPercent,
    $result.stages.queue_idle_ms.avgMs.delta,
    $result.stages.queue_idle_ms.p95Ms.delta,
    $result.stages.llrawproc_ms.avgMs.delta,
    $(if ([string]::IsNullOrWhiteSpace($Output)) { "<stdout-json>" } else { $resolvedOutput })
)

$json

if ($failures.Count -gt 0) {
    exit 1
}
