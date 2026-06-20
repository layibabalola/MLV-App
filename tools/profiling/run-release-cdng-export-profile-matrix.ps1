param(
    [string]$RepoRoot = ".",
    [string]$ExePath = "",
    [string]$CasesPath = "",
    [Alias("Input")]
    [string]$ClipPath = "",
    [string]$Receipt = "",
    [ValidateSet("", "uncompressed", "lossless", "fast-pass")]
    [string]$CdngCodec = "",
    [string]$CaseName = "",
    [string]$OutputDir = "",
    [string]$BuildId = "",
    [int]$Repeats = 1,
    [int]$MaxFrames = 0,
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
    [switch]$AlternateRunOrder,
    [switch]$EnableGpuExport,
    [switch]$BaselineEnableGpuExport,
    [switch]$CandidateEnableGpuExport,
    [switch]$GpuExportTrusted,
    [switch]$BaselineGpuExportTrusted,
    [switch]$CandidateGpuExportTrusted,
    [string]$GpuExportDll = "",
    [string]$BaselineGpuExportDll = "",
    [string]$CandidateGpuExportDll = "",
    [string]$GpuExportBackend = "",
    [string]$BaselineGpuExportBackend = "",
    [string]$CandidateGpuExportBackend = "",
    [switch]$RequireBaselineNoGpuExportAttempt,
    [switch]$RequireCandidateGpuExportAttempt,
    [switch]$RequireCandidateGpuExportReplacement,
    [switch]$RequireCandidateGpuExportTrusted,
    [switch]$RequireDngHashMatch,
    [switch]$RequireElapsedImprovement,
    [double]$MinElapsedImprovementPercent = 0.0,
    [double]$MaxFrameTotalRegressionPercent = 5.0,
    [double]$MaxFrameTotalP95RegressionPercent = 10.0,
    [switch]$FailOnRegression,
    [switch]$StopOnFailure,
    [switch]$DryRun
)

$ErrorActionPreference = "Stop"

if ($Repeats -lt 1) {
    throw "-Repeats must be >= 1."
}
if ($MaxFrames -lt 0) {
    throw "-MaxFrames must be >= 0."
}
if ($BaselineUseAsyncWriterCompression -and -not $BaselineUseAsyncWriter) {
    throw "-BaselineUseAsyncWriterCompression requires -BaselineUseAsyncWriter."
}
if ($CandidateUseAsyncWriterCompression -and -not $CandidateUseAsyncWriter) {
    throw "-CandidateUseAsyncWriterCompression requires -CandidateUseAsyncWriter."
}
if ($MinElapsedImprovementPercent -lt 0.0) {
    throw "-MinElapsedImprovementPercent must be >= 0."
}

$root = (Resolve-Path -LiteralPath $RepoRoot).Path
$abRunner = Join-Path $root "tools\profiling\run-release-cdng-export-profile-ab.ps1"
$hashComparator = Join-Path $root "tools\profiling\compare-cdng-dng-output-hashes.ps1"
if (-not (Test-Path -LiteralPath $abRunner)) {
    throw "Release CDNG export A/B profiler not found: $abRunner"
}
if (-not (Test-Path -LiteralPath $hashComparator)) {
    throw "DNG hash comparator not found: $hashComparator"
}

if ([string]::IsNullOrWhiteSpace($OutputDir)) {
    $stamp = (Get-Date).ToUniversalTime().ToString("yyyyMMdd-HHmmss")
    $OutputDir = Join-Path $root ".claude-state\profiling\$stamp-cdng-export-matrix"
}
$bundleDir = $ExecutionContext.SessionState.Path.GetUnresolvedProviderPathFromPSPath($OutputDir)
$summaryJson = Join-Path $bundleDir "matrix-summary.json"
$casesRoot = Join-Path $bundleDir "cases"

$baselineGpuExportEnabled = ([bool]$EnableGpuExport) -or ([bool]$BaselineEnableGpuExport)
$candidateGpuExportEnabled = ([bool]$EnableGpuExport) -or ([bool]$CandidateEnableGpuExport)
$baselineGpuExportTrustedEffective = ([bool]$GpuExportTrusted) -or ([bool]$BaselineGpuExportTrusted)
$candidateGpuExportTrustedEffective = ([bool]$GpuExportTrusted) -or ([bool]$CandidateGpuExportTrusted)
if ($baselineGpuExportTrustedEffective -and -not $baselineGpuExportEnabled) {
    throw "Baseline trusted GPU export requires baseline GPU export to be enabled."
}
if ($candidateGpuExportTrustedEffective -and -not $candidateGpuExportEnabled) {
    throw "Candidate trusted GPU export requires candidate GPU export to be enabled."
}
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
$baselineGpuExportBackendEffective = if (-not [string]::IsNullOrWhiteSpace($BaselineGpuExportBackend)) {
    $BaselineGpuExportBackend
}
else {
    $GpuExportBackend
}
$candidateGpuExportBackendEffective = if (-not [string]::IsNullOrWhiteSpace($CandidateGpuExportBackend)) {
    $CandidateGpuExportBackend
}
else {
    $GpuExportBackend
}
$baselineGpuExportDllSummary = if ($baselineGpuExportEnabled) { $baselineGpuExportDllEffective } else { "" }
$candidateGpuExportDllSummary = if ($candidateGpuExportEnabled) { $candidateGpuExportDllEffective } else { "" }
$baselineGpuExportBackendSummary = if ($baselineGpuExportEnabled) { $baselineGpuExportBackendEffective } else { "" }
$candidateGpuExportBackendSummary = if ($candidateGpuExportEnabled) { $candidateGpuExportBackendEffective } else { "" }

function Convert-ToBool {
    param(
        [object]$Value,
        [bool]$DefaultValue = $false
    )

    if ($null -eq $Value) {
        return $DefaultValue
    }
    if ($Value -is [bool]) {
        return [bool]$Value
    }
    $text = ([string]$Value).Trim()
    if ([string]::IsNullOrWhiteSpace($text)) {
        return $DefaultValue
    }
    if ($text -match '^(1|true|yes|on)$') {
        return $true
    }
    if ($text -match '^(0|false|no|off)$') {
        return $false
    }
    throw "Invalid boolean value '$Value'."
}

function Get-ObjectProperty {
    param(
        [object]$Object,
        [string[]]$Names
    )

    if ($null -eq $Object) {
        return $null
    }

    foreach ($name in $Names) {
        $property = $Object.PSObject.Properties[$name]
        if ($property) {
            return $property.Value
        }
    }
    $null
}

function Convert-ToIntOrDefault {
    param(
        [object]$Value,
        [int]$DefaultValue
    )

    if ($null -eq $Value -or [string]::IsNullOrWhiteSpace([string]$Value)) {
        return $DefaultValue
    }
    $parsed = 0
    if (-not [int]::TryParse(
            [string]$Value,
            [System.Globalization.NumberStyles]::Integer,
            [System.Globalization.CultureInfo]::InvariantCulture,
            [ref]$parsed)) {
        throw "Invalid integer value '$Value'."
    }
    $parsed
}

function Get-ThroughputClassificationRollup {
    param([AllowNull()]$CaseResults)

    $counts = [ordered]@{}
    $runCount = 0
    $classifiedRunCount = 0
    $wallClockImprovedCount = 0
    $attributionHoldCount = 0
    $defaultPromotionCandidateCount = 0
    $notPromotableCount = 0
    foreach ($caseResult in @($CaseResults)) {
        foreach ($run in @($caseResult.runs)) {
            $runCount++
            $classification = $run.throughputClassification
            if ($null -eq $classification) {
                continue
            }
            $classifiedRunCount++
            $token = [string]$classification.token
            if ([string]::IsNullOrWhiteSpace($token)) {
                $token = "unknown"
            }
            if (-not $counts.Contains($token)) {
                $counts[$token] = 0
            }
            $counts[$token] = [int]$counts[$token] + 1
            if ([bool]$classification.wallClockImproved) {
                $wallClockImprovedCount++
            }
            if ([bool]$classification.defaultPromotionCandidate) {
                $defaultPromotionCandidateCount++
            }
            if ($token -eq "wall_clock_improved_attribution_hold") {
                $attributionHoldCount++
            }
            if ($token -eq "not_promotable") {
                $notPromotableCount++
            }
        }
    }

    $status = "unclassified"
    $suggested = "collect_cdng_matrix_with_current_throughput_classification"
    if ($classifiedRunCount -gt 0) {
        if ($attributionHoldCount -gt 0) {
            $status = "attribution_hold"
            $suggested = "separate_writer_completion_lag_from_export_wall_clock_gate"
        }
        elseif ($notPromotableCount -gt 0) {
            $status = "not_promotable"
            $suggested = "improve_export_wall_clock_before_scheduler_promotion"
        }
        elseif ($defaultPromotionCandidateCount -eq $classifiedRunCount) {
            $status = "promotion_candidate"
            $suggested = "validate_broader_lossless_matrix_before_default_promotion"
        }
        else {
            $status = "mixed"
            $suggested = "review_per_run_throughput_classification_before_promotion"
        }
    }

    [pscustomobject]@{
        schema = "mlvapp.cdng_throughput_classification_rollup.v1"
        status = $status
        runCount = $runCount
        classifiedRunCount = $classifiedRunCount
        tokenCounts = [pscustomobject]$counts
        wallClockImprovedCount = $wallClockImprovedCount
        attributionHoldCount = $attributionHoldCount
        defaultPromotionCandidateCount = $defaultPromotionCandidateCount
        notPromotableCount = $notPromotableCount
        suggestedOptimization = $suggested
        proofBoundary = "Rollup classifies throughput evidence only; CDNG proof, DNG hash, and default export behavior remain separately gated."
    }
}

function Get-ThroughputClassificationGroupedRollups {
    param(
        [AllowNull()]$CaseResults,
        [Parameter(Mandatory = $true)][string]$PropertyName,
        [Parameter(Mandatory = $true)][string]$GroupBy
    )

    $groups = [ordered]@{}
    foreach ($caseResult in @($CaseResults)) {
        $value = $null
        if ($caseResult.PSObject.Properties.Name -contains $PropertyName) {
            $value = $caseResult.$PropertyName
        }
        $key = [string]$value
        if ([string]::IsNullOrWhiteSpace($key)) {
            $key = "unspecified"
        }
        if (-not $groups.Contains($key)) {
            $groups[$key] = @()
        }
        $groups[$key] = @($groups[$key]) + $caseResult
    }

    @($groups.Keys | Sort-Object | ForEach-Object {
        $key = [string]$_
        $groupCases = @($groups[$key])
        [pscustomobject]@{
            schema = "mlvapp.cdng_throughput_classification_grouped_rollup.v1"
            groupBy = $GroupBy
            value = $key
            caseCount = $groupCases.Count
            caseNames = @($groupCases | ForEach-Object { $_.name })
            rollup = Get-ThroughputClassificationRollup -CaseResults $groupCases
            proofBoundary = "Grouped rollup is scoped evidence only; top-level throughputClassification remains the fail-closed default promotion gate."
        }
    })
}

function Convert-ToCdngCodecOrDefault {
    param(
        [object]$Value,
        [string]$DefaultValue
    )

    if ($null -eq $Value -or [string]::IsNullOrWhiteSpace([string]$Value)) {
        return $DefaultValue
    }
    $normalized = ([string]$Value).Trim().ToLowerInvariant()
    switch ($normalized) {
        "default" { return "uncompressed" }
        "uncompressed" { return "uncompressed" }
        "lossless" { return "lossless" }
        "compressed" { return "lossless" }
        "fast" { return "fast-pass" }
        "fastpass" { return "fast-pass" }
        "fast-pass" { return "fast-pass" }
        default { throw "Invalid CDNG codec '$Value'. Use uncompressed, lossless, or fast-pass." }
    }
}

function Convert-ToPathForCase {
    param(
        [string]$Path,
        [string]$Label,
        [bool]$Required
    )

    if ([string]::IsNullOrWhiteSpace($Path)) {
        if ($Required) {
            throw "Missing required $Label path."
        }
        return ""
    }

    $candidatePath = $Path
    if (-not [System.IO.Path]::IsPathRooted($candidatePath)) {
        $candidatePath = Join-Path $root $candidatePath
    }

    if ($DryRun) {
        return $ExecutionContext.SessionState.Path.GetUnresolvedProviderPathFromPSPath($candidatePath)
    }

    (Resolve-Path -LiteralPath $candidatePath).Path
}

function Get-FailureList {
    param([object]$Summary)

    $items = @()
    if ($Summary -and $Summary.compare) {
        foreach ($failure in @($Summary.compare.failures)) {
            if ($null -ne $failure -and -not [string]::IsNullOrWhiteSpace([string]$failure)) {
                $items += [string]$failure
            }
        }
    }
    $items
}

function Convert-ToSafeName {
    param([string]$Name)

    $safe = $Name -replace '[\\/:*?"<>|]', '-'
    $safe = $safe -replace '\s+', '-'
    $safe = $safe.Trim(".-")
    if ([string]::IsNullOrWhiteSpace($safe)) {
        return "case"
    }
    $safe.ToLowerInvariant()
}

function Get-RunOrder {
    param(
        [object]$Case,
        [int]$RepeatIndex
    )

    if (-not $AlternateRunOrder) {
        return "BaselineFirst"
    }
    if ((($Case.index + $RepeatIndex) % 2) -eq 0) {
        return "BaselineFirst"
    }
    "CandidateFirst"
}

function Read-MatrixCases {
    $rawCases = @()

    if (-not [string]::IsNullOrWhiteSpace($CasesPath)) {
        $resolvedCases = (Resolve-Path -LiteralPath $CasesPath).Path
        $document = Get-Content -LiteralPath $resolvedCases -Raw | ConvertFrom-Json -Depth 64
        if ($document -is [array]) {
            $rawCases = @($document)
        }
        elseif ($document.PSObject.Properties["cases"]) {
            $rawCases = @($document.cases)
        }
        else {
            throw "Cases file must be a JSON array or an object with a 'cases' array: $resolvedCases"
        }
    }
    else {
        if ([string]::IsNullOrWhiteSpace($ClipPath)) {
            throw "Provide -CasesPath or -Input <clip>."
        }
        $rawCases = @([pscustomobject]@{
                name = $(if ([string]::IsNullOrWhiteSpace($CaseName)) { "case-1" } else { $CaseName })
                clipPath = $ClipPath
                receipt = $Receipt
                cdngCodec = $CdngCodec
                maxFrames = $MaxFrames
                repeats = $Repeats
            })
    }

    $cases = @()
    $index = 0
    foreach ($rawCase in $rawCases) {
        if ($null -eq $rawCase) {
            continue
        }
        if (-not (Convert-ToBool -Value (Get-ObjectProperty -Object $rawCase -Names @("enabled")) -DefaultValue $true)) {
            continue
        }

        $index++
        $rawName = [string](Get-ObjectProperty -Object $rawCase -Names @("name", "label", "id"))
        if ([string]::IsNullOrWhiteSpace($rawName)) {
            $rawName = "case-$index"
        }

        $caseClip = [string](Get-ObjectProperty -Object $rawCase -Names @("clipPath", "clip", "input", "inputPath"))
        $caseReceipt = [string](Get-ObjectProperty -Object $rawCase -Names @("receipt", "receiptPath"))
        $caseCdngCodec = Convert-ToCdngCodecOrDefault `
            -Value (Get-ObjectProperty -Object $rawCase -Names @("cdngCodec", "cdng_codec", "codec")) `
            -DefaultValue $CdngCodec
        $caseMaxFrames = Convert-ToIntOrDefault `
            -Value (Get-ObjectProperty -Object $rawCase -Names @("maxFrames", "max_frames")) `
            -DefaultValue $MaxFrames
        $caseRepeats = Convert-ToIntOrDefault `
            -Value (Get-ObjectProperty -Object $rawCase -Names @("repeats", "repeatCount")) `
            -DefaultValue $Repeats

        if ($caseMaxFrames -lt 0) {
            throw "Case '$rawName' has maxFrames < 0."
        }
        if ($caseRepeats -lt 1) {
            throw "Case '$rawName' has repeats < 1."
        }

        $cases += [pscustomobject]@{
            index = $index
            name = $rawName
            safeName = Convert-ToSafeName -Name $rawName
            clipPath = Convert-ToPathForCase -Path $caseClip -Label "clip" -Required $true
            receipt = Convert-ToPathForCase -Path $caseReceipt -Label "receipt" -Required $false
            cdngCodec = $caseCdngCodec
            maxFrames = $caseMaxFrames
            repeats = $caseRepeats
        }
    }

    if ($cases.Count -lt 1) {
        throw "No enabled matrix cases found."
    }
    $cases
}

function New-AbArgs {
    param(
        [object]$Case,
        [int]$RepeatIndex,
        [string]$CaseOutputDir
    )

    $args = @(
        "-NoLogo", "-NoProfile", "-NonInteractive", "-ExecutionPolicy", "Bypass",
        "-File", $abRunner,
        "-RepoRoot", $root,
        "-Input", $Case.clipPath,
        "-OutputDir", $CaseOutputDir,
        "-MaxFrameTotalRegressionPercent", "$MaxFrameTotalRegressionPercent",
        "-MaxFrameTotalP95RegressionPercent", "$MaxFrameTotalP95RegressionPercent",
        "-RunOrder", (Get-RunOrder -Case $Case -RepeatIndex $RepeatIndex)
    )

    if (-not [string]::IsNullOrWhiteSpace($ExePath)) {
        $args += @("-ExePath", $ExePath)
    }
    if (-not [string]::IsNullOrWhiteSpace($Case.receipt)) {
        $args += @("-Receipt", $Case.receipt)
    }
    if (-not [string]::IsNullOrWhiteSpace($Case.cdngCodec)) {
        $args += @("-CdngCodec", $Case.cdngCodec)
    }
    if (-not [string]::IsNullOrWhiteSpace($BuildId)) {
        $args += @("-BuildId", "$BuildId-$($Case.safeName)-r$RepeatIndex")
    }
    if ($Case.maxFrames -gt 0) {
        $args += @("-MaxFrames", "$($Case.maxFrames)")
    }
    if ($BaselineUsePayloadHandoff) {
        $args += "-BaselineUsePayloadHandoff"
    }
    if ($CandidateUsePayloadHandoff) {
        $args += "-CandidateUsePayloadHandoff"
    }
    if ($BaselineUseAsyncWriter) {
        $args += "-BaselineUseAsyncWriter"
    }
    if ($CandidateUseAsyncWriter) {
        $args += "-CandidateUseAsyncWriter"
    }
    if ($BaselineUseAsyncWriterCompression) {
        $args += "-BaselineUseAsyncWriterCompression"
    }
    if ($CandidateUseAsyncWriterCompression) {
        $args += "-CandidateUseAsyncWriterCompression"
    }
    if ($BaselineAsyncWriterQueueDepth -gt 0) {
        $args += @("-BaselineAsyncWriterQueueDepth", "$BaselineAsyncWriterQueueDepth")
    }
    if ($CandidateAsyncWriterQueueDepth -gt 0) {
        $args += @("-CandidateAsyncWriterQueueDepth", "$CandidateAsyncWriterQueueDepth")
    }
    if ($BaselineAsyncWriterThreadCount -gt 0) {
        $args += @("-BaselineAsyncWriterThreadCount", "$BaselineAsyncWriterThreadCount")
    }
    if ($CandidateAsyncWriterThreadCount -gt 0) {
        $args += @("-CandidateAsyncWriterThreadCount", "$CandidateAsyncWriterThreadCount")
    }
    if ($baselineGpuExportEnabled) {
        $args += "-BaselineEnableGpuExport"
        if ($baselineGpuExportTrustedEffective) {
            $args += "-BaselineGpuExportTrusted"
        }
        if (-not [string]::IsNullOrWhiteSpace($baselineGpuExportDllEffective)) {
            $args += @("-BaselineGpuExportDll", $baselineGpuExportDllEffective)
        }
        if (-not [string]::IsNullOrWhiteSpace($baselineGpuExportBackendEffective)) {
            $args += @("-BaselineGpuExportBackend", $baselineGpuExportBackendEffective)
        }
    }
    if ($candidateGpuExportEnabled) {
        $args += "-CandidateEnableGpuExport"
        if ($candidateGpuExportTrustedEffective) {
            $args += "-CandidateGpuExportTrusted"
        }
        if (-not [string]::IsNullOrWhiteSpace($candidateGpuExportDllEffective)) {
            $args += @("-CandidateGpuExportDll", $candidateGpuExportDllEffective)
        }
        if (-not [string]::IsNullOrWhiteSpace($candidateGpuExportBackendEffective)) {
            $args += @("-CandidateGpuExportBackend", $candidateGpuExportBackendEffective)
        }
    }
    if ($RequireBaselineNoGpuExportAttempt) {
        $args += "-RequireBaselineNoGpuExportAttempt"
    }
    if ($RequireCandidateGpuExportAttempt) {
        $args += "-RequireCandidateGpuExportAttempt"
    }
    if ($RequireCandidateGpuExportReplacement) {
        $args += "-RequireCandidateGpuExportReplacement"
    }
    if ($RequireCandidateGpuExportTrusted) {
        $args += "-RequireCandidateGpuExportTrusted"
    }
    if ($RequireElapsedImprovement) {
        $args += "-RequireElapsedImprovement"
    }
    if ($MinElapsedImprovementPercent -gt 0.0) {
        $args += @("-MinElapsedImprovementPercent", "$MinElapsedImprovementPercent")
    }
    if ($FailOnRegression) {
        $args += "-FailOnRegression"
    }
    if ($DryRun) {
        $args += "-DryRun"
    }

    $args
}

$isIdentityComparison = (
    [bool]$BaselineUsePayloadHandoff -eq [bool]$CandidateUsePayloadHandoff -and
    [bool]$BaselineUseAsyncWriter -eq [bool]$CandidateUseAsyncWriter -and
    [bool]$BaselineUseAsyncWriterCompression -eq [bool]$CandidateUseAsyncWriterCompression -and
    $BaselineAsyncWriterQueueDepth -eq $CandidateAsyncWriterQueueDepth -and
    $BaselineAsyncWriterThreadCount -eq $CandidateAsyncWriterThreadCount -and
    $baselineGpuExportEnabled -eq $candidateGpuExportEnabled -and
    $baselineGpuExportTrustedEffective -eq $candidateGpuExportTrustedEffective -and
    (
        (-not $baselineGpuExportEnabled -and -not $candidateGpuExportEnabled) -or
        (
            $baselineGpuExportDllEffective -eq $candidateGpuExportDllEffective -and
            $baselineGpuExportBackendEffective -eq $candidateGpuExportBackendEffective
        )
    )
)
$comparisonMode = if ($isIdentityComparison) { "identity-aa" } else { "feature-ab" }

$cases = Read-MatrixCases

$planCases = @()
$caseResults = @()
$runCount = 0
$passCount = 0
$failCount = 0

foreach ($case in $cases) {
    $repeatPlans = @()
    for ($repeat = 1; $repeat -le $case.repeats; $repeat++) {
        $caseDirName = "{0:D2}-{1}" -f $case.index, $case.safeName
        $repeatDirName = "repeat-{0:D2}" -f $repeat
        $caseOutputDir = Join-Path (Join-Path $casesRoot $caseDirName) $repeatDirName
        $repeatPlans += [pscustomobject]@{
            repeat = $repeat
            runOrder = Get-RunOrder -Case $case -RepeatIndex $repeat
            outputDir = $caseOutputDir
            args = New-AbArgs -Case $case -RepeatIndex $repeat -CaseOutputDir $caseOutputDir
        }
    }

    $planCases += [pscustomobject]@{
        index = $case.index
        name = $case.name
        clipPath = $case.clipPath
        receipt = $case.receipt
        cdngCodec = $case.cdngCodec
        maxFrames = $case.maxFrames
        repeats = $case.repeats
        runs = $repeatPlans
    }
}

if ($DryRun) {
    [pscustomobject]@{
        schema = "release-cdng-export-profile-matrix-plan.v1"
        bundleDir = $bundleDir
        summary = $summaryJson
        defaults = [pscustomobject]@{
            repeats = $Repeats
            maxFrames = $MaxFrames
            cdngCodec = $CdngCodec
            comparisonMode = $comparisonMode
            isIdentityComparison = $isIdentityComparison
            baselineUsePayloadHandoff = [bool]$BaselineUsePayloadHandoff
            candidateUsePayloadHandoff = [bool]$CandidateUsePayloadHandoff
            baselineUseAsyncWriter = [bool]$BaselineUseAsyncWriter
            candidateUseAsyncWriter = [bool]$CandidateUseAsyncWriter
            baselineUseAsyncWriterCompression = [bool]$BaselineUseAsyncWriterCompression
            candidateUseAsyncWriterCompression = [bool]$CandidateUseAsyncWriterCompression
            baselineAsyncWriterQueueDepth = $BaselineAsyncWriterQueueDepth
            candidateAsyncWriterQueueDepth = $CandidateAsyncWriterQueueDepth
            baselineAsyncWriterThreadCount = $BaselineAsyncWriterThreadCount
            candidateAsyncWriterThreadCount = $CandidateAsyncWriterThreadCount
            baselineEnableGpuExport = $baselineGpuExportEnabled
            candidateEnableGpuExport = $candidateGpuExportEnabled
            baselineGpuExportTrusted = $baselineGpuExportTrustedEffective
            candidateGpuExportTrusted = $candidateGpuExportTrustedEffective
            baselineGpuExportDll = $baselineGpuExportDllSummary
            candidateGpuExportDll = $candidateGpuExportDllSummary
            baselineGpuExportBackend = $baselineGpuExportBackendSummary
            candidateGpuExportBackend = $candidateGpuExportBackendSummary
            requireBaselineNoGpuExportAttempt = [bool]$RequireBaselineNoGpuExportAttempt
            requireCandidateGpuExportAttempt = [bool]$RequireCandidateGpuExportAttempt
            requireCandidateGpuExportReplacement = [bool]$RequireCandidateGpuExportReplacement
            requireCandidateGpuExportTrusted = [bool]$RequireCandidateGpuExportTrusted
            requireDngHashMatch = [bool]$RequireDngHashMatch
            requireElapsedImprovement = [bool]$RequireElapsedImprovement
            minElapsedImprovementPercent = $MinElapsedImprovementPercent
            alternateRunOrder = [bool]$AlternateRunOrder
            failOnRegression = [bool]$FailOnRegression
        }
        cases = $planCases
    } | ConvertTo-Json -Depth 24
    return
}

New-Item -ItemType Directory -Force -Path $bundleDir, $casesRoot | Out-Null

foreach ($case in $cases) {
    $runs = @()
    for ($repeat = 1; $repeat -le $case.repeats; $repeat++) {
        $runCount++
        $caseDirName = "{0:D2}-{1}" -f $case.index, $case.safeName
        $repeatDirName = "repeat-{0:D2}" -f $repeat
        $caseOutputDir = Join-Path (Join-Path $casesRoot $caseDirName) $repeatDirName
        $summaryPath = Join-Path $caseOutputDir "summary.json"
        $comparePath = Join-Path $caseOutputDir "compare.json"
        $runOrder = Get-RunOrder -Case $case -RepeatIndex $repeat
        $args = New-AbArgs -Case $case -RepeatIndex $repeat -CaseOutputDir $caseOutputDir

        $errorMessage = ""
        $abSummary = $null
        $exitCode = 0

        try {
            & pwsh.exe @args
            $exitCode = $LASTEXITCODE
            if (Test-Path -LiteralPath $summaryPath) {
                $abSummary = Get-Content -LiteralPath $summaryPath -Raw | ConvertFrom-Json -Depth 100
            }
            if ($exitCode -ne 0 -and $null -eq $abSummary) {
                $errorMessage = "A/B runner exited $exitCode and did not write summary.json."
            }
        }
        catch {
            $exitCode = 1
            $errorMessage = $_.Exception.Message
            if (Test-Path -LiteralPath $summaryPath) {
                $abSummary = Get-Content -LiteralPath $summaryPath -Raw | ConvertFrom-Json -Depth 100
            }
        }

        $verdict = "FAIL"
        if ($exitCode -eq 0 -and $null -ne $abSummary -and [string]$abSummary.verdict -eq "PASS") {
            $verdict = "PASS"
            $passCount++
        }
        else {
            $failCount++
            if ([string]::IsNullOrWhiteSpace($errorMessage) -and $null -ne $abSummary) {
                $errorMessage = "A/B runner verdict was $($abSummary.verdict)."
            }
        }

        $failureList = @(Get-FailureList -Summary $abSummary)
        $runs += [pscustomobject]@{
            repeat = $repeat
            runOrder = $runOrder
            outputDir = $caseOutputDir
            summary = $summaryPath
            compare = $comparePath
            exitCode = $exitCode
            verdict = $verdict
            cdngCodec = if ($abSummary) { $abSummary.cdngCodec } else { $case.cdngCodec }
            error = $errorMessage
            baselineFrameCount = if ($abSummary) { $abSummary.baseline.frameCount } else { $null }
            baselineMachineFingerprint = if ($abSummary) { $abSummary.baseline.machineFingerprint } else { $null }
            baselineBottleneck = if ($abSummary) { $abSummary.baseline.bottleneck } else { $null }
            baselineSuggestedOptimization = if ($abSummary) { $abSummary.baseline.suggestedOptimization } else { $null }
            candidateFrameCount = if ($abSummary) { $abSummary.candidate.frameCount } else { $null }
            candidateMachineFingerprint = if ($abSummary) { $abSummary.candidate.machineFingerprint } else { $null }
            candidateBottleneck = if ($abSummary) { $abSummary.candidate.bottleneck } else { $null }
            candidateSuggestedOptimization = if ($abSummary) { $abSummary.candidate.suggestedOptimization } else { $null }
            baselineDngCompressBytesValidFrames = if ($abSummary) { $abSummary.baseline.dngCompressBytesValidFrames } else { $null }
            baselineDngCompressInputBytesTotal = if ($abSummary) { $abSummary.baseline.dngCompressInputBytesTotal } else { $null }
            baselineDngCompressOutputBytesTotal = if ($abSummary) { $abSummary.baseline.dngCompressOutputBytesTotal } else { $null }
            baselineDngCompressPlacement = if ($abSummary) { $abSummary.baseline.dngCompressPlacement } else { $null }
            baselineAsyncWriterCanOverlapDngCompress = if ($abSummary) { $abSummary.baseline.asyncWriterCanOverlapDngCompress } else { $null }
            baselineDngCompressInputMiBPerSecond = if ($abSummary) { $abSummary.baseline.dngCompressInputMiBPerSecond } else { $null }
            baselineDngCompressOutputMiBPerSecond = if ($abSummary) { $abSummary.baseline.dngCompressOutputMiBPerSecond } else { $null }
            baselineDngCompressOutputRatio = if ($abSummary) { $abSummary.baseline.dngCompressOutputRatio } else { $null }
            candidateDngCompressBytesValidFrames = if ($abSummary) { $abSummary.candidate.dngCompressBytesValidFrames } else { $null }
            candidateDngCompressInputBytesTotal = if ($abSummary) { $abSummary.candidate.dngCompressInputBytesTotal } else { $null }
            candidateDngCompressOutputBytesTotal = if ($abSummary) { $abSummary.candidate.dngCompressOutputBytesTotal } else { $null }
            candidateDngCompressPlacement = if ($abSummary) { $abSummary.candidate.dngCompressPlacement } else { $null }
            candidateAsyncWriterCanOverlapDngCompress = if ($abSummary) { $abSummary.candidate.asyncWriterCanOverlapDngCompress } else { $null }
            candidateDngCompressInputMiBPerSecond = if ($abSummary) { $abSummary.candidate.dngCompressInputMiBPerSecond } else { $null }
            candidateDngCompressOutputMiBPerSecond = if ($abSummary) { $abSummary.candidate.dngCompressOutputMiBPerSecond } else { $null }
            candidateDngCompressOutputRatio = if ($abSummary) { $abSummary.candidate.dngCompressOutputRatio } else { $null }
            baselineElapsedMs = if ($abSummary) { $abSummary.baseline.elapsedMs } else { $null }
            candidateElapsedMs = if ($abSummary) { $abSummary.candidate.elapsedMs } else { $null }
            throughputClassification = if ($abSummary) { $abSummary.throughputClassification } else { $null }
            elapsedDeltaMs = if ($abSummary) { $abSummary.compare.elapsedDeltaMs } else { $null }
            elapsedDeltaPercent = if ($abSummary) { $abSummary.compare.elapsedDeltaPercent } else { $null }
            baselineAsyncWriterCompressEnvEnabled = if ($abSummary) { $abSummary.baseline.asyncWriterCompressEnvEnabled } else { $null }
            candidateAsyncWriterCompressEnvEnabled = if ($abSummary) { $abSummary.candidate.asyncWriterCompressEnvEnabled } else { $null }
            candidateAsyncWriterThreadCount = if ($abSummary) { $abSummary.candidate.asyncWriterThreadCount } else { $null }
            candidateAsyncWriterQueueCapacity = if ($abSummary) { $abSummary.candidate.asyncWriterQueueCapacity } else { $null }
            candidateAsyncWriterMaxQueued = if ($abSummary) { $abSummary.candidate.asyncWriterMaxQueued } else { $null }
            candidateAsyncWriterJobsStarted = if ($abSummary) { $abSummary.candidate.asyncWriterJobsStarted } else { $null }
            candidateAsyncWriterJobsFinished = if ($abSummary) { $abSummary.candidate.asyncWriterJobsFinished } else { $null }
            candidateAsyncWriterMaxActive = if ($abSummary) { $abSummary.candidate.asyncWriterMaxActive } else { $null }
            baselineGpuExportEnabled = if ($abSummary) { $abSummary.baseline.enableGpuExport } else { $null }
            baselineGpuExportTrusted = if ($abSummary) { $abSummary.baseline.gpuExportTrusted } else { $null }
            baselineGpuExportBackend = if ($abSummary) { $abSummary.baseline.gpuExportBackend } else { $null }
            baselineGpuExportAttemptedFrames = if ($abSummary) { $abSummary.baseline.gpuExportAttemptedFrames } else { $null }
            baselineGpuExportReplacedFrames = if ($abSummary) { $abSummary.baseline.gpuExportReplacedFrames } else { $null }
            baselineGpuExportTrustedFrames = if ($abSummary) { $abSummary.baseline.gpuExportTrustedFrames } else { $null }
            baselineGpuExportMaxAllocatedBytes = if ($abSummary) { $abSummary.baseline.gpuExportMaxAllocatedBytes } else { $null }
            baselineGpuExportSkippedFrames = if ($abSummary) { $abSummary.baseline.gpuExportSkippedFrames } else { $null }
            baselineGpuExportSkipReasonCounts = if ($abSummary) { $abSummary.baseline.gpuExportSkipReasonCounts } else { $null }
            candidateGpuExportEnabled = if ($abSummary) { $abSummary.candidate.enableGpuExport } else { $null }
            candidateGpuExportTrusted = if ($abSummary) { $abSummary.candidate.gpuExportTrusted } else { $null }
            candidateGpuExportBackend = if ($abSummary) { $abSummary.candidate.gpuExportBackend } else { $null }
            candidateGpuExportAttemptedFrames = if ($abSummary) { $abSummary.candidate.gpuExportAttemptedFrames } else { $null }
            candidateGpuExportReplacedFrames = if ($abSummary) { $abSummary.candidate.gpuExportReplacedFrames } else { $null }
            candidateGpuExportTrustedFrames = if ($abSummary) { $abSummary.candidate.gpuExportTrustedFrames } else { $null }
            candidateGpuExportMaxAllocatedBytes = if ($abSummary) { $abSummary.candidate.gpuExportMaxAllocatedBytes } else { $null }
            candidateGpuExportSkippedFrames = if ($abSummary) { $abSummary.candidate.gpuExportSkippedFrames } else { $null }
            candidateGpuExportSkipReasonCounts = if ($abSummary) { $abSummary.candidate.gpuExportSkipReasonCounts } else { $null }
            proofGateFailures = if ($abSummary -and $abSummary.proofGates) { @($abSummary.proofGates.failures) } else { @() }
            elapsedGateFailures = if ($abSummary -and $abSummary.elapsedGate) { @($abSummary.elapsedGate.failures) } else { @() }
            elapsedImprovementPercent = if ($abSummary -and $abSummary.elapsedGate) { $abSummary.elapsedGate.elapsedImprovementPercent } else { $null }
            frameTotalAvgDeltaMs = if ($abSummary) { $abSummary.compare.frameTotalAvgDeltaMs } else { $null }
            frameTotalP95DeltaMs = if ($abSummary) { $abSummary.compare.frameTotalP95DeltaMs } else { $null }
            producerFrameAvgDeltaMs = if ($abSummary) { $abSummary.compare.producerFrameAvgDeltaMs } else { $null }
            producerFrameP95DeltaMs = if ($abSummary) { $abSummary.compare.producerFrameP95DeltaMs } else { $null }
            producerQueueIdleAvgDeltaMs = if ($abSummary) { $abSummary.compare.producerQueueIdleAvgDeltaMs } else { $null }
            writerCompletionLagAvgDeltaMs = if ($abSummary) { $abSummary.compare.writerCompletionLagAvgDeltaMs } else { $null }
            writerCompletionLagP95DeltaMs = if ($abSummary) { $abSummary.compare.writerCompletionLagP95DeltaMs } else { $null }
            writerQueueWaitAvgDeltaMs = if ($abSummary) { $abSummary.compare.writerQueueWaitAvgDeltaMs } else { $null }
            payloadCloneAvgDeltaMs = if ($abSummary) { $abSummary.compare.payloadCloneAvgDeltaMs } else { $null }
            llrawprocTotalAvgDeltaMs = if ($abSummary) { $abSummary.compare.llrawprocTotalAvgDeltaMs } else { $null }
            llrawprocDualIsoAvgDeltaMs = if ($abSummary) { $abSummary.compare.llrawprocDualIsoAvgDeltaMs } else { $null }
            llrawprocChromaSmoothAvgDeltaMs = if ($abSummary) { $abSummary.compare.llrawprocChromaSmoothAvgDeltaMs } else { $null }
            llrawprocOtherAvgDeltaMs = if ($abSummary) { $abSummary.compare.llrawprocOtherAvgDeltaMs } else { $null }
            dngCompressAvgDeltaMs = if ($abSummary) { $abSummary.compare.dngCompressAvgDeltaMs } else { $null }
            dngCompressEncodeAvgDeltaMs = if ($abSummary) { $abSummary.compare.dngCompressEncodeAvgDeltaMs } else { $null }
            dngCompressCopyAvgDeltaMs = if ($abSummary) { $abSummary.compare.dngCompressCopyAvgDeltaMs } else { $null }
            dngCompressCleanupAvgDeltaMs = if ($abSummary) { $abSummary.compare.dngCompressCleanupAvgDeltaMs } else { $null }
            dngCompressInputBytesTotalDelta = if ($abSummary) { $abSummary.compare.dngCompressInputBytesTotalDelta } else { $null }
            dngCompressOutputBytesTotalDelta = if ($abSummary) { $abSummary.compare.dngCompressOutputBytesTotalDelta } else { $null }
            dngCompressInputMiBPerSecondDelta = if ($abSummary) { $abSummary.compare.dngCompressInputMiBPerSecondDelta } else { $null }
            dngCompressInputMiBPerSecondDeltaPercent = if ($abSummary) { $abSummary.compare.dngCompressInputMiBPerSecondDeltaPercent } else { $null }
            dngCompressOutputMiBPerSecondDelta = if ($abSummary) { $abSummary.compare.dngCompressOutputMiBPerSecondDelta } else { $null }
            dngCompressOutputMiBPerSecondDeltaPercent = if ($abSummary) { $abSummary.compare.dngCompressOutputMiBPerSecondDeltaPercent } else { $null }
            dngCompressOutputRatioDelta = if ($abSummary) { $abSummary.compare.dngCompressOutputRatioDelta } else { $null }
            dngCompressPlacement = if ($abSummary) { $abSummary.compare.dngCompressPlacement } else { $null }
            asyncWriterCanOverlapDngCompress = if ($abSummary) { $abSummary.compare.asyncWriterCanOverlapDngCompress } else { $null }
            failures = @($failureList)
        }

        if ($verdict -ne "PASS" -and $StopOnFailure) {
            break
        }
    }

    $caseVerdict = if (($runs | Where-Object { $_.verdict -ne "PASS" }).Count -eq 0) { "PASS" } else { "FAIL" }
    $caseResults += [pscustomobject]@{
        index = $case.index
        name = $case.name
        clipPath = $case.clipPath
        receipt = $case.receipt
        cdngCodec = $case.cdngCodec
        maxFrames = $case.maxFrames
        repeats = $case.repeats
        verdict = $caseVerdict
        runs = $runs
    }

    if ($caseVerdict -ne "PASS" -and $StopOnFailure) {
        break
    }
}

$matrixMachineFingerprint = $null
foreach ($caseResult in @($caseResults)) {
    foreach ($run in @($caseResult.runs)) {
        if ($run.candidateMachineFingerprint) {
            $matrixMachineFingerprint = $run.candidateMachineFingerprint
            break
        }
        if ($run.baselineMachineFingerprint) {
            $matrixMachineFingerprint = $run.baselineMachineFingerprint
            break
        }
    }
    if ($matrixMachineFingerprint) {
        break
    }
}

$matrix = [pscustomobject]@{
    schema = "release-cdng-export-profile-matrix.v1"
    generatedAtUtc = (Get-Date).ToUniversalTime().ToString("o")
    machineFingerprint = $matrixMachineFingerprint
    bundleDir = $bundleDir
    comparisonMode = $comparisonMode
    isIdentityComparison = $isIdentityComparison
    thresholds = [pscustomobject]@{
        failOnRegression = [bool]$FailOnRegression
        maxFrameTotalRegressionPercent = $MaxFrameTotalRegressionPercent
        maxFrameTotalP95RegressionPercent = $MaxFrameTotalP95RegressionPercent
        requireElapsedImprovement = [bool]$RequireElapsedImprovement
        minElapsedImprovementPercent = $MinElapsedImprovementPercent
    }
    options = [pscustomobject]@{
        baselineUsePayloadHandoff = [bool]$BaselineUsePayloadHandoff
        candidateUsePayloadHandoff = [bool]$CandidateUsePayloadHandoff
        baselineUseAsyncWriter = [bool]$BaselineUseAsyncWriter
        candidateUseAsyncWriter = [bool]$CandidateUseAsyncWriter
        baselineUseAsyncWriterCompression = [bool]$BaselineUseAsyncWriterCompression
        candidateUseAsyncWriterCompression = [bool]$CandidateUseAsyncWriterCompression
        baselineAsyncWriterQueueDepth = $BaselineAsyncWriterQueueDepth
        candidateAsyncWriterQueueDepth = $CandidateAsyncWriterQueueDepth
        baselineAsyncWriterThreadCount = $BaselineAsyncWriterThreadCount
        candidateAsyncWriterThreadCount = $CandidateAsyncWriterThreadCount
        cdngCodec = $CdngCodec
        alternateRunOrder = [bool]$AlternateRunOrder
        enableGpuExport = [bool]$EnableGpuExport
        baselineEnableGpuExport = $baselineGpuExportEnabled
        candidateEnableGpuExport = $candidateGpuExportEnabled
        baselineGpuExportTrusted = $baselineGpuExportTrustedEffective
        candidateGpuExportTrusted = $candidateGpuExportTrustedEffective
        baselineGpuExportDll = $baselineGpuExportDllSummary
        candidateGpuExportDll = $candidateGpuExportDllSummary
        baselineGpuExportBackend = $baselineGpuExportBackendSummary
        candidateGpuExportBackend = $candidateGpuExportBackendSummary
        requireBaselineNoGpuExportAttempt = [bool]$RequireBaselineNoGpuExportAttempt
        requireCandidateGpuExportAttempt = [bool]$RequireCandidateGpuExportAttempt
        requireCandidateGpuExportReplacement = [bool]$RequireCandidateGpuExportReplacement
        requireCandidateGpuExportTrusted = [bool]$RequireCandidateGpuExportTrusted
        requireDngHashMatch = [bool]$RequireDngHashMatch
        requireElapsedImprovement = [bool]$RequireElapsedImprovement
        minElapsedImprovementPercent = $MinElapsedImprovementPercent
        buildId = $BuildId
    }
    totals = [pscustomobject]@{
        caseCount = $caseResults.Count
        runCount = $runCount
        passCount = $passCount
        failCount = $failCount
    }
    throughputClassification = Get-ThroughputClassificationRollup -CaseResults $caseResults
    throughputClassificationByCodec = Get-ThroughputClassificationGroupedRollups -CaseResults $caseResults -PropertyName "cdngCodec" -GroupBy "cdngCodec"
    throughputClassificationByCase = Get-ThroughputClassificationGroupedRollups -CaseResults $caseResults -PropertyName "name" -GroupBy "case"
    cases = $caseResults
    verdict = if ($failCount -eq 0) { "PASS" } else { "FAIL" }
}

$matrix | ConvertTo-Json -Depth 32 | Set-Content -LiteralPath $summaryJson -Encoding UTF8

if ($RequireDngHashMatch) {
    $hashArgs = @(
        "-NoLogo", "-NoProfile", "-NonInteractive", "-ExecutionPolicy", "Bypass",
        "-File", $hashComparator,
        "-MatrixDir", $bundleDir,
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

    $matrix | Add-Member -NotePropertyName dngHash -NotePropertyValue ([pscustomobject]@{
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
        $matrix.verdict = "FAIL"
    }
    $matrix | ConvertTo-Json -Depth 32 | Set-Content -LiteralPath $summaryJson -Encoding UTF8
}

Write-Host (((
    "CDNG-EXPORT-MATRIX verdict={0} comparison_mode={1} cdng_codec={2} alternate_run_order={3} " +
    "cases={4} runs={5} pass={6} fail={7} candidate_payload={8} " +
    "candidate_async={9} candidate_async_compress={10} candidate_async_queue_depth={11} " +
    "baseline_gpu_export_enabled={12} candidate_gpu_export_enabled={13} " +
    "baseline_gpu_export_trusted={14} candidate_gpu_export_trusted={15} " +
    "require_candidate_gpu_export_replacement={16} require_candidate_gpu_export_trusted={17} " +
    "dng_hash_required={18} dng_hash_verdict={19} elapsed_delta_ms_field=True output={20}") -f
    $matrix.verdict,
    $matrix.comparisonMode,
    $(if ([string]::IsNullOrWhiteSpace($matrix.options.cdngCodec)) { "default" } else { $matrix.options.cdngCodec }),
    [bool]$AlternateRunOrder,
    $matrix.totals.caseCount,
    $matrix.totals.runCount,
    $matrix.totals.passCount,
    $matrix.totals.failCount,
    [bool]$CandidateUsePayloadHandoff,
    [bool]$CandidateUseAsyncWriter,
    [bool]$CandidateUseAsyncWriterCompression,
    $CandidateAsyncWriterQueueDepth,
    $baselineGpuExportEnabled,
    $candidateGpuExportEnabled,
    $baselineGpuExportTrustedEffective,
    $candidateGpuExportTrustedEffective,
    [bool]$RequireCandidateGpuExportReplacement,
    [bool]$RequireCandidateGpuExportTrusted,
    [bool]$RequireDngHashMatch,
    $(if ($matrix.PSObject.Properties["dngHash"]) { $matrix.dngHash.verdict } else { "SKIP" }),
    $summaryJson
))

$matrix | ConvertTo-Json -Depth 32

if ($matrix.verdict -ne "PASS") {
    exit 1
}
