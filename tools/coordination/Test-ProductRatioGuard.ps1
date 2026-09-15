[CmdletBinding()]
param(
    [string]$RepoRoot = $(if ($env:MLV_BOARD_ROOT) { $env:MLV_BOARD_ROOT } else { Split-Path -Parent (Split-Path -Parent $PSScriptRoot) }),
    [string]$SourceRef = 'fork/master',
    [long]$AsOfEpoch = 0,
    [string]$ReservationsPath = '',
    [string]$LegacyDispatchPath = ''
)

$ErrorActionPreference = 'Stop'
Set-StrictMode -Version Latest

$WindowSeconds = 7 * 24 * 60 * 60
$EmptyTreeSha = '4b825dc642cb6eb9a060e54bf8d69288fbee4904'

function New-Decision {
    param(
        [AllowNull()]$ProductShare,
        [AllowNull()]$DispatchRate,
        [string]$Coverage,
        [bool]$EvidenceAvailable,
        [bool]$ProvenanceComplete,
        [bool]$HasProductLandings,
        [int]$MalformedRows = 0
    )

    $reasons = [System.Collections.Generic.List[string]]::new()
    if ($null -eq $ProductShare -or [double]$ProductShare -lt 0.50) { $reasons.Add('RED_PRODUCT_SHARE') }
    if ($Coverage -ne 'COMPLETE') { $reasons.Add('RED_DISPATCH_COVERAGE_PARTIAL') }
    if (-not $EvidenceAvailable) { $reasons.Add('UNAVAILABLE_DISPATCH_EVIDENCE') }
    if (-not $ProvenanceComplete) { $reasons.Add('UNAVAILABLE_LANDING_PROVENANCE') }
    elseif (-not $HasProductLandings) { $reasons.Add('NO_PRODUCT_LANDINGS') }
    elseif ($null -ne $DispatchRate -and [double]$DispatchRate -gt 4.0) { $reasons.Add('RED_DISPATCH_RATE') }
    if ($MalformedRows -gt 0) { $reasons.Add('ERROR_DISPATCH_EVIDENCE_MALFORMED') }

    $green = $null -ne $ProductShare -and
        [double]$ProductShare -ge 0.50 -and
        $Coverage -eq 'COMPLETE' -and
        $EvidenceAvailable -and
        $ProvenanceComplete -and
        $HasProductLandings -and
        $null -ne $DispatchRate -and
        [double]$DispatchRate -le 4.0 -and
        $MalformedRows -eq 0

    return [pscustomobject]@{
        verdict = $(if ($green) { 'GREEN' } else { 'RED' })
        reasons = @($reasons)
    }
}

function Invoke-GitLines {
    param([Parameter(Mandatory)][string[]]$Arguments)
    $lines = @(& git -C $RepoRoot @Arguments 2>$null)
    if ($LASTEXITCODE -ne 0) { throw 'git command failed' }
    return @($lines | ForEach-Object { [string]$_ })
}

function Get-CommitInfo {
    param([Parameter(Mandatory)][string]$Line)
    $parts = $line -split "`t", 4
    if ($parts.Count -ne 4 -or $parts[0] -notmatch '^[0-9a-fA-F]{40}$' -or $parts[1] -notmatch '^\d+$') {
        throw 'malformed git history'
    }
    return [pscustomobject]@{
        sha = $parts[0].ToLowerInvariant()
        epoch = [long]$parts[1]
        parents = @($parts[2] -split ' ' | Where-Object { $_ })
        subject = $parts[3]
    }
}

function Test-ProductDiff {
    param([Parameter(Mandatory)][string]$Commit, [Parameter(Mandatory)][string]$Base)
    & git -C $RepoRoot diff --quiet --no-ext-diff --no-textconv $Base $Commit -- src/ platform/ 2>$null
    $diffExit = $LASTEXITCODE
    if ($diffExit -gt 1 -or $diffExit -lt 0) { throw 'git product diff failed' }
    return $diffExit -eq 1
}

function Read-DispatchEvidence {
    param([long]$StartEpoch, [long]$EndEpoch)

    $path = $null
    $source = 'none'
    $reservationMode = $false
    try {
        if (Test-Path -LiteralPath $ReservationsPath -ErrorAction Stop) {
            $path = $ReservationsPath
            $source = 'dispatch-reservations'
            $reservationMode = $true
        } elseif (Test-Path -LiteralPath $LegacyDispatchPath -ErrorAction Stop) {
            $path = $LegacyDispatchPath
            $source = 'legacy-dispatch-log'
        }
    } catch {
        return [pscustomobject]@{ source = 'unavailable'; coverage = 'PARTIAL'; available = $false; observed = 0; malformed = 0 }
    }

    if ($null -eq $path) {
        return [pscustomobject]@{ source = 'none'; coverage = 'PARTIAL'; available = $false; observed = 0; malformed = 0 }
    }

    # Selection is final: once reservations win, any read failure is unavailable evidence.
    # It must never fall through to or combine with the legacy log.
    try {
        $lines = @(Get-Content -LiteralPath $path -ErrorAction Stop)
    } catch {
        return [pscustomobject]@{ source = 'unavailable'; coverage = 'PARTIAL'; available = $false; observed = 0; malformed = 0 }
    }

    $observed = 0
    $malformed = 0
    foreach ($line in $lines) {
        if ([string]::IsNullOrWhiteSpace($line)) { continue }
        try {
            # Preserve ISO timestamp offsets and precision across pwsh versions.
            $document = [System.Text.Json.JsonDocument]::Parse($line)
            try {
                $row = $document.RootElement
                if ($reservationMode -and $row.GetProperty('state').GetString() -ne 'reserved') { continue }
                $stamp = $row.GetProperty($(if ($reservationMode) { 'recordedUtc' } else { 'dispatchedUtc' })).GetString()
            } finally { $document.Dispose() }
            if ($stamp -notmatch '(Z|[+-]\d{2}:\d{2})$') { throw 'timestamp timezone missing' }
            $dto = [datetimeoffset]::Parse($stamp, [Globalization.CultureInfo]::InvariantCulture, [Globalization.DateTimeStyles]::RoundtripKind)
            $epoch = $dto.ToUnixTimeSeconds()
            if ($epoch -ge $StartEpoch -and $epoch -le $EndEpoch) { $observed++ }
        } catch {
            $malformed++
        }
    }

    return [pscustomobject]@{
        source = $source
        coverage = 'PARTIAL'
        available = $true
        observed = $observed
        malformed = $malformed
    }
}

function Write-ErrorResult {
    param([string]$Code, [string]$ResolvedSha = '')
    $endEpoch = if ($AsOfEpoch -gt 0) { $AsOfEpoch } else { [datetimeoffset]::UtcNow.ToUnixTimeSeconds() }
    $startEpoch = $endEpoch - $WindowSeconds
    [ordered]@{
        schema = 'mlv-app/product-ratio-guard/v1'
        asOfUtc = [datetimeoffset]::FromUnixTimeSeconds($endEpoch).UtcDateTime.ToString('o')
        windowStartUtc = [datetimeoffset]::FromUnixTimeSeconds($startEpoch).UtcDateTime.ToString('o')
        windowEndUtc = [datetimeoffset]::FromUnixTimeSeconds($endEpoch).UtcDateTime.ToString('o')
        sourceRef = $SourceRef
        sourceSha = $ResolvedSha
        commitPopulation = 0
        productCommitCount = 0
        productShare7d = $null
        productShareThreshold = 0.50
        recognizedProductPrCount = 0
        recognizedProductPrIds = @()
        unrecognizedProductLandings = @()
        landingProvenanceComplete = $false
        hasProductLandings = $false
        dispatchEvidenceSource = 'unavailable'
        dispatchCoverage = 'PARTIAL'
        dispatchEvidenceAvailable = $false
        dispatchesObserved = 0
        malformedDispatchRows = 0
        dispatchesPerLandedProductPr7dLowerBound = $null
        dispatchRateThreshold = 4.0
        verdict = 'ERROR'
        reasons = @($Code)
        errorCode = $Code
    } | ConvertTo-Json -Depth 6 -Compress
    exit 3
}

if (-not $ReservationsPath) {
    $ReservationsPath = Join-Path $RepoRoot '.claude-state\coordination\dual-lane\receipts\dispatch-reservations.jsonl'
}
if (-not $LegacyDispatchPath) {
    $LegacyDispatchPath = Join-Path $RepoRoot '.claude-state\coordination\dual-lane\workstream-dispatch-log.jsonl'
}
if ($AsOfEpoch -le 0) { $AsOfEpoch = [datetimeoffset]::UtcNow.ToUnixTimeSeconds() }
$windowStart = $AsOfEpoch - $WindowSeconds

$sourceSha = ''
try {
    $resolved = @(& git -C $RepoRoot rev-parse --verify "$SourceRef^{commit}" 2>$null)
    if ($LASTEXITCODE -ne 0 -or $resolved.Count -ne 1 -or $resolved[0] -notmatch '^[0-9a-fA-F]{40}$') {
        Write-ErrorResult 'ERROR_REF_UNRESOLVED'
    }
    $sourceSha = ([string]$resolved[0]).ToLowerInvariant()
} catch {
    Write-ErrorResult 'ERROR_REF_UNRESOLVED'
}

try {
    # Read metadata in two bounded process invocations, not one child per historic
    # commit. Only commits inside the window need a diff process.
    $allLines = Invoke-GitLines @('log','--no-show-signature','--no-color','--format=%H%x09%ct%x09%P%x09%s',$sourceSha)
    $population = 0
    $productCount = 0
    foreach ($line in $allLines) {
        $info = Get-CommitInfo $line
        if ($info.epoch -gt $AsOfEpoch) { continue }
        if ($info.epoch -lt $windowStart) { continue }
        if ($info.parents.Count -gt 1) { continue }
        $population++
        $base = if ($info.parents.Count -eq 0) { $EmptyTreeSha } else { $info.parents[0] }
        if (Test-ProductDiff -Commit $info.sha -Base $base) { $productCount++ }
    }

    $firstParentLines = Invoke-GitLines @('log','--first-parent','--no-show-signature','--no-color','--format=%H%x09%ct%x09%P%x09%s',$sourceSha)
    $recognized = @{}
    $unknown = [System.Collections.Generic.List[string]]::new()
    foreach ($line in $firstParentLines) {
        $info = Get-CommitInfo $line
        if ($info.epoch -gt $AsOfEpoch -or $info.epoch -lt $windowStart) { continue }
        $base = if ($info.parents.Count -eq 0) { $EmptyTreeSha } else { $info.parents[0] }
        $productLanding = Test-ProductDiff -Commit $info.sha -Base $base
        if (-not $productLanding) { continue }

        $pr = $null
        if ($info.parents.Count -eq 2 -and $info.subject -match '^Merge pull request #(\d+)\b') {
            $pr = $Matches[1]
        } elseif ($info.parents.Count -eq 1 -and $info.subject -match '\(#(\d+)\)$') {
            $pr = $Matches[1]
        }

        if ($null -eq $pr) { $unknown.Add($info.sha) }
        else { $recognized[$pr] = $true }
    }
} catch {
    Write-ErrorResult 'ERROR_GIT_HISTORY' $sourceSha
}

$evidence = Read-DispatchEvidence -StartEpoch $windowStart -EndEpoch $AsOfEpoch
$productShare = if ($population -eq 0) { $null } else { [double]$productCount / [double]$population }
$prIds = @($recognized.Keys | ForEach-Object { [int]$_ } | Sort-Object)
$provenanceComplete = $unknown.Count -eq 0
$hasProductLandings = ($prIds.Count + $unknown.Count) -gt 0
$rate = $null
if ($evidence.available -and $provenanceComplete -and $prIds.Count -gt 0) {
    $rate = [double]$evidence.observed / [double]$prIds.Count
}
$decision = New-Decision -ProductShare $productShare -DispatchRate $rate -Coverage $evidence.coverage -EvidenceAvailable $evidence.available -ProvenanceComplete $provenanceComplete -HasProductLandings $hasProductLandings -MalformedRows $evidence.malformed

[ordered]@{
    schema = 'mlv-app/product-ratio-guard/v1'
    asOfUtc = [datetimeoffset]::FromUnixTimeSeconds($AsOfEpoch).UtcDateTime.ToString('o')
    windowStartUtc = [datetimeoffset]::FromUnixTimeSeconds($windowStart).UtcDateTime.ToString('o')
    windowEndUtc = [datetimeoffset]::FromUnixTimeSeconds($AsOfEpoch).UtcDateTime.ToString('o')
    sourceRef = $SourceRef
    sourceSha = $sourceSha
    commitPopulation = $population
    productCommitCount = $productCount
    productShare7d = $productShare
    productShareThreshold = 0.50
    recognizedProductPrCount = $prIds.Count
    recognizedProductPrIds = $prIds
    unrecognizedProductLandings = @($unknown)
    landingProvenanceComplete = $provenanceComplete
    hasProductLandings = $hasProductLandings
    dispatchEvidenceSource = $evidence.source
    dispatchCoverage = $evidence.coverage
    dispatchEvidenceAvailable = $evidence.available
    dispatchesObserved = $evidence.observed
    malformedDispatchRows = $evidence.malformed
    dispatchesPerLandedProductPr7dLowerBound = $rate
    dispatchRateThreshold = 4.0
    verdict = $decision.verdict
    reasons = @($decision.reasons)
    errorCode = $null
} | ConvertTo-Json -Depth 6 -Compress
exit 0
