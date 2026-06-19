param(
    [string]$MatrixDir = "",
    [string]$MatrixSummary = "",
    [string]$Output = "",
    [switch]$FailOnMismatch
)

$ErrorActionPreference = "Stop"

function Resolve-MatrixSummaryPath {
    if (-not [string]::IsNullOrWhiteSpace($MatrixSummary)) {
        return $ExecutionContext.SessionState.Path.GetUnresolvedProviderPathFromPSPath($MatrixSummary)
    }
    if (-not [string]::IsNullOrWhiteSpace($MatrixDir)) {
        $resolvedDir = $ExecutionContext.SessionState.Path.GetUnresolvedProviderPathFromPSPath($MatrixDir)
        return (Join-Path $resolvedDir "matrix-summary.json")
    }
    throw "Pass -MatrixSummary or -MatrixDir."
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

$matrixSummaryPath = Resolve-MatrixSummaryPath
$matrixSummaryPath = $ExecutionContext.SessionState.Path.GetUnresolvedProviderPathFromPSPath($matrixSummaryPath)
$matrixDirResolved = Split-Path -Parent $matrixSummaryPath
if ([string]::IsNullOrWhiteSpace($Output)) {
    $Output = Join-Path $matrixDirResolved "dng-hash-comparison.json"
}
$outputPath = $ExecutionContext.SessionState.Path.GetUnresolvedProviderPathFromPSPath($Output)
$outputDir = Split-Path -Parent $outputPath
if (-not (Test-Path -LiteralPath $outputDir -PathType Container)) {
    New-Item -ItemType Directory -Force -Path $outputDir | Out-Null
}

$matrix = Read-JsonFile -Path $matrixSummaryPath -Label "Matrix summary"
if ([string]$matrix.schema -ne "release-cdng-export-profile-matrix.v1") {
    throw "Unexpected matrix summary schema in ${matrixSummaryPath}: $($matrix.schema)"
}

$caseRows = @()
$mismatches = @()
$totals = New-EmptyTotals

foreach ($case in @($matrix.cases)) {
    $caseTotals = New-EmptyTotals
    $runRows = @()
    foreach ($run in @($case.runs)) {
        $runSummaryPath = [string]$run.summary
        $runSummary = Read-JsonFile -Path $runSummaryPath -Label "A/B run summary"
        if ([string]$runSummary.schema -ne "release-cdng-export-profile-ab.v1") {
            throw "Unexpected A/B run summary schema in ${runSummaryPath}: $($runSummary.schema)"
        }

        $baselineDir = [string]$runSummary.baseline.dngOutputDir
        $candidateDir = [string]$runSummary.candidate.dngOutputDir
        $runComparison = Compare-DngDirectories `
            -BaselineDir $baselineDir `
            -CandidateDir $candidateDir `
            -CaseName ([string]$case.name) `
            -Repeat $run.repeat `
            -RunOrder ([string]$run.runOrder)

        Add-Totals -Target $totals -Source $runComparison.totals
        Add-Totals -Target $caseTotals -Source $runComparison.totals
        $mismatches += @($runComparison.mismatches)
        $runRows += [pscustomobject]@{
            repeat = $run.repeat
            runOrder = [string]$run.runOrder
            summary = $runSummaryPath
            baselineDngDir = $baselineDir
            candidateDngDir = $candidateDir
            totals = $runComparison.totals
        }
    }

    $caseRows += [pscustomobject]@{
        name = [string]$case.name
        totals = $caseTotals
        runs = $runRows
    }
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
    matrixSummary = $matrixSummaryPath
    comparisonMode = [string]$matrix.comparisonMode
    verdict = $verdict
    totals = $totals
    cases = $caseRows
    mismatches = $mismatches
}

$result | ConvertTo-Json -Depth 32 | Set-Content -LiteralPath $outputPath -Encoding UTF8

Write-Host ((
    "CDNG-DNG-HASH-COMPARE verdict={0} pairs={1} matched={2} mismatched={3} " +
    "missing_baseline={4} missing_candidate={5} output={6}") -f
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
