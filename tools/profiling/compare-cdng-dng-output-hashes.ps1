param(
    [string]$MatrixDir = "",
    [string]$MatrixSummary = "",
    [Alias("SummaryJson")]
    [string]$AbSummary = "",
    [string]$Output = "",
    [switch]$FailOnMismatch
)

$ErrorActionPreference = "Stop"

function Resolve-InputSummary {
    $hasMatrixSummary = -not [string]::IsNullOrWhiteSpace($MatrixSummary)
    $hasMatrixDir = -not [string]::IsNullOrWhiteSpace($MatrixDir)
    $hasAbSummary = -not [string]::IsNullOrWhiteSpace($AbSummary)

    if (($hasMatrixSummary -or $hasMatrixDir) -and $hasAbSummary) {
        throw "Pass either -MatrixSummary/-MatrixDir or -AbSummary, not both."
    }
    if ($hasMatrixSummary) {
        return [pscustomobject]@{
            Kind = "matrix"
            Path = $ExecutionContext.SessionState.Path.GetUnresolvedProviderPathFromPSPath($MatrixSummary)
        }
    }
    if ($hasMatrixDir) {
        $resolvedDir = $ExecutionContext.SessionState.Path.GetUnresolvedProviderPathFromPSPath($MatrixDir)
        return [pscustomobject]@{
            Kind = "matrix"
            Path = (Join-Path $resolvedDir "matrix-summary.json")
        }
    }
    if ($hasAbSummary) {
        return [pscustomobject]@{
            Kind = "ab"
            Path = $ExecutionContext.SessionState.Path.GetUnresolvedProviderPathFromPSPath($AbSummary)
        }
    }
    throw "Pass -MatrixSummary, -MatrixDir, or -AbSummary."
}

function Read-JsonFile {
    param(
        [string]$Path,
        [string]$Label
    )

    $resolved = $ExecutionContext.SessionState.Path.GetUnresolvedProviderPathFromPSPath($Path)
    if (-not (Test-Path -LiteralPath $resolved -PathType Leaf)) {
        throw "$Label not found: $resolved"
    }
    Get-Content -LiteralPath $resolved -Raw | ConvertFrom-Json -Depth 100
}

function Get-DngMap {
    param([string]$Root)

    if (-not (Test-Path -LiteralPath $Root -PathType Container)) {
        throw "DNG directory not found: $Root"
    }

    $map = @{}
    Get-ChildItem -LiteralPath $Root -Recurse -File -Filter "*.DNG" | ForEach-Object {
        $relative = [System.IO.Path]::GetRelativePath($Root, $_.FullName).Replace('\', '/')
        $map[$relative] = $_
    }
    $map
}

function New-EmptyTotals {
    [ordered]@{
        pairs = 0
        matched = 0
        mismatched = 0
        missingBaseline = 0
        missingCandidate = 0
    }
}

function Add-Totals {
    param(
        [object]$Target,
        [object]$Source
    )

    $Target.pairs += $Source.pairs
    $Target.matched += $Source.matched
    $Target.mismatched += $Source.mismatched
    $Target.missingBaseline += $Source.missingBaseline
    $Target.missingCandidate += $Source.missingCandidate
}

function Compare-DngDirectories {
    param(
        [string]$BaselineDir,
        [string]$CandidateDir,
        [string]$CaseName,
        [object]$Repeat,
        [string]$RunOrder
    )

    $baselineMap = Get-DngMap -Root $BaselineDir
    $candidateMap = Get-DngMap -Root $CandidateDir
    $keys = @($baselineMap.Keys + $candidateMap.Keys | Sort-Object -Unique)
    $totals = New-EmptyTotals
    $mismatches = @()

    foreach ($key in $keys) {
        $baselineFile = $baselineMap[$key]
        $candidateFile = $candidateMap[$key]

        if ($null -eq $baselineFile) {
            $totals.missingBaseline++
            $mismatches += [ordered]@{
                case = $CaseName
                repeat = $Repeat
                runOrder = $RunOrder
                path = $key
                issue = "missingBaseline"
                candidateLength = $candidateFile.Length
                candidateSha256 = (Get-FileHash -LiteralPath $candidateFile.FullName -Algorithm SHA256).Hash
            }
            continue
        }

        if ($null -eq $candidateFile) {
            $totals.missingCandidate++
            $mismatches += [ordered]@{
                case = $CaseName
                repeat = $Repeat
                runOrder = $RunOrder
                path = $key
                issue = "missingCandidate"
                baselineLength = $baselineFile.Length
                baselineSha256 = (Get-FileHash -LiteralPath $baselineFile.FullName -Algorithm SHA256).Hash
            }
            continue
        }

        $totals.pairs++
        $baselineHash = (Get-FileHash -LiteralPath $baselineFile.FullName -Algorithm SHA256).Hash
        $candidateHash = (Get-FileHash -LiteralPath $candidateFile.FullName -Algorithm SHA256).Hash
        if ($baselineFile.Length -eq $candidateFile.Length -and $baselineHash -eq $candidateHash) {
            $totals.matched++
        }
        else {
            $totals.mismatched++
            $mismatches += [ordered]@{
                case = $CaseName
                repeat = $Repeat
                runOrder = $RunOrder
                path = $key
                issue = "hashOrLengthMismatch"
                baselineLength = $baselineFile.Length
                candidateLength = $candidateFile.Length
                baselineSha256 = $baselineHash
                candidateSha256 = $candidateHash
            }
        }
    }

    [pscustomobject]@{
        totals = $totals
        mismatches = $mismatches
    }
}

function Compare-AbRunSummary {
    param(
        [string]$RunSummaryPath,
        [string]$CaseName,
        [object]$Repeat,
        [string]$RunOrderOverride = ""
    )

    $runSummary = Read-JsonFile -Path $RunSummaryPath -Label "A/B run summary"
    if ([string]$runSummary.schema -ne "release-cdng-export-profile-ab.v1") {
        throw "Unexpected A/B run summary schema in ${RunSummaryPath}: $($runSummary.schema)"
    }

    $baselineDir = [string]$runSummary.baseline.dngOutputDir
    $candidateDir = [string]$runSummary.candidate.dngOutputDir
    $runOrder = [string]$runSummary.runOrder
    if ([string]::IsNullOrWhiteSpace($runOrder)) {
        $runOrder = $RunOrderOverride
    }
    $runComparison = Compare-DngDirectories `
        -BaselineDir $baselineDir `
        -CandidateDir $candidateDir `
        -CaseName $CaseName `
        -Repeat $Repeat `
        -RunOrder $runOrder

    [pscustomobject]@{
        summary = $runSummary
        baselineDir = $baselineDir
        candidateDir = $candidateDir
        runOrder = $runOrder
        comparison = $runComparison
    }
}

$inputSummary = Resolve-InputSummary
$inputSummaryPath = $ExecutionContext.SessionState.Path.GetUnresolvedProviderPathFromPSPath($inputSummary.Path)
$summaryDirResolved = Split-Path -Parent $inputSummaryPath
if ([string]::IsNullOrWhiteSpace($Output)) {
    $Output = Join-Path $summaryDirResolved "dng-hash-comparison.json"
}
$outputPath = $ExecutionContext.SessionState.Path.GetUnresolvedProviderPathFromPSPath($Output)
$outputDir = Split-Path -Parent $outputPath
if (-not (Test-Path -LiteralPath $outputDir -PathType Container)) {
    New-Item -ItemType Directory -Force -Path $outputDir | Out-Null
}

$caseRows = @()
$mismatches = @()
$totals = New-EmptyTotals
$comparisonMode = ""
$isIdentityComparison = $false
$matrixSummaryPath = $null
$abSummaryPath = $null

if ($inputSummary.Kind -eq "matrix") {
    $matrixSummaryPath = $inputSummaryPath
    $matrix = Read-JsonFile -Path $matrixSummaryPath -Label "Matrix summary"
    if ([string]$matrix.schema -ne "release-cdng-export-profile-matrix.v1") {
        throw "Unexpected matrix summary schema in ${matrixSummaryPath}: $($matrix.schema)"
    }
    $comparisonMode = [string]$matrix.comparisonMode
    $isIdentityComparison = [bool]$matrix.isIdentityComparison

    foreach ($case in @($matrix.cases)) {
        $caseTotals = New-EmptyTotals
        $runRows = @()
        foreach ($run in @($case.runs)) {
            $runSummaryPath = [string]$run.summary
            $abRun = Compare-AbRunSummary `
                -RunSummaryPath $runSummaryPath `
                -CaseName ([string]$case.name) `
                -Repeat $run.repeat `
                -RunOrderOverride ([string]$run.runOrder)

            Add-Totals -Target $totals -Source $abRun.comparison.totals
            Add-Totals -Target $caseTotals -Source $abRun.comparison.totals
            $mismatches += @($abRun.comparison.mismatches)
            $runRows += [pscustomobject]@{
                repeat = $run.repeat
                runOrder = $abRun.runOrder
                summary = $runSummaryPath
                baselineDngDir = $abRun.baselineDir
                candidateDngDir = $abRun.candidateDir
                totals = $abRun.comparison.totals
            }
        }

        $caseRows += [pscustomobject]@{
            name = [string]$case.name
            totals = $caseTotals
            runs = $runRows
        }
    }
}
elseif ($inputSummary.Kind -eq "ab") {
    $abSummaryPath = $inputSummaryPath
    $caseName = Split-Path -Leaf (Split-Path -Parent $abSummaryPath)
    if ([string]::IsNullOrWhiteSpace($caseName)) {
        $caseName = "single-ab"
    }
    $abRun = Compare-AbRunSummary `
        -RunSummaryPath $abSummaryPath `
        -CaseName $caseName `
        -Repeat 1

    Add-Totals -Target $totals -Source $abRun.comparison.totals
    $mismatches += @($abRun.comparison.mismatches)
    $comparisonMode = [string]$abRun.summary.comparisonMode
    $isIdentityComparison = [bool]$abRun.summary.isIdentityComparison
    $caseRows += [pscustomobject]@{
        name = $caseName
        totals = $abRun.comparison.totals
        runs = @(
            [pscustomobject]@{
                repeat = 1
                runOrder = $abRun.runOrder
                summary = $abSummaryPath
                baselineDngDir = $abRun.baselineDir
                candidateDngDir = $abRun.candidateDir
                totals = $abRun.comparison.totals
            }
        )
    }
}
else {
    throw "Unsupported input summary kind: $($inputSummary.Kind)"
}

$verdict = if ($totals.mismatched -eq 0 -and
               $totals.missingBaseline -eq 0 -and
               $totals.missingCandidate -eq 0) {
    "PASS"
}
else {
    "FAIL"
}

$result = [pscustomobject]@{
    schema = "release-cdng-dng-hash-comparison.v1"
    generatedAtUtc = (Get-Date).ToUniversalTime().ToString("o")
    sourceKind = [string]$inputSummary.Kind
    matrixSummary = $matrixSummaryPath
    abSummary = $abSummaryPath
    comparisonMode = $comparisonMode
    isIdentityComparison = $isIdentityComparison
    verdict = $verdict
    totals = $totals
    cases = $caseRows
    mismatches = $mismatches
}

$result | ConvertTo-Json -Depth 32 | Set-Content -LiteralPath $outputPath -Encoding UTF8

Write-Host ((
    "CDNG-DNG-HASH-COMPARE source={0} verdict={1} pairs={2} matched={3} mismatched={4} " +
    "missing_baseline={5} missing_candidate={6} output={7}") -f
    $result.sourceKind,
    $result.verdict,
    $result.totals.pairs,
    $result.totals.matched,
    $result.totals.mismatched,
    $result.totals.missingBaseline,
    $result.totals.missingCandidate,
    $outputPath)

$result | ConvertTo-Json -Depth 32

if ($FailOnMismatch -and $result.verdict -ne "PASS") {
    exit 1
}
