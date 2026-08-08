param(
    [string]$HealthDir = (Join-Path (Get-Location) '.claude-state\coordination\dual-lane\health'),
    [string]$AutomationPath = '',
    [string]$ExpectedThreadId = '',
    [string]$HeartbeatMarker = '',
    [int]$NowOffsetMinutes = 0,
    [switch]$Quiet
)

$ErrorActionPreference = 'Stop'
# SWEEP-CLASS-1 (a): the class list and the DARK boundary come from ONE tracked file that
# every sweep reads, so the vocabularies converge by construction instead of by three
# places being edited in step. See lane-health-classes.psd1 for why EXPIRED was deleted.
$classes = Import-PowerShellDataFile -LiteralPath (Join-Path $PSScriptRoot 'lane-health-classes.psd1')
$darkCap = [double]$classes.DarkThresholdCapMinutes
$darkGrace = [double]$classes.DarkThresholdGraceMinutes
$now = [DateTime]::UtcNow.AddMinutes($NowOffsetMinutes)
$dualLane = Split-Path -Parent $HealthDir
$leasesDir = Join-Path $HealthDir 'leases'
$journalPaths = [ordered]@{
    fable = Join-Path $dualLane 'fable.md'
    codex = Join-Path $dualLane 'codex.md'
    sol = Join-Path $dualLane 'sol.md'
    opus = Join-Path $dualLane 'opus.md'
    'claude-review' = Join-Path (Split-Path -Parent $dualLane) 'gpu-lane-impl-review-sync.md'
}
$adopted = @('codex', 'sol', 'claude-review')
$lanes = @()
foreach ($lane in $journalPaths.Keys) {
    $leasePath = Join-Path $leasesDir "$lane.json"
    $leaseState = if ($adopted -contains $lane) { 'MISSING' } else { 'PENDING-ADOPTION' }
    $leaseAge = $null
    $leaseReason = if ($adopted -contains $lane) { 'required adopted lease is missing' } else { 'lane has not adopted lease gating' }
    if (Test-Path -LiteralPath $leasePath -PathType Leaf) {
        try {
            $lease = Get-Content -LiteralPath $leasePath -Raw | ConvertFrom-Json
            if ([string]$lease.lane -ne $lane) { throw "lane field mismatch" }
            $renewed = [DateTime]::Parse([string]$lease.renewed, $null, 'AdjustToUniversal')
            $minutes = [double]$lease.leaseMinutes
            if ($minutes -le 0) { throw 'leaseMinutes must be positive' }
            $leaseAge = [math]::Round(($now - $renewed).TotalMinutes, 1)
            # ONE boundary. The old second function (2 * declared) agreed with the board
            # threshold at exactly declared=20 and disagreed everywhere else -- at the
            # 30-minute default it called DARK ten minutes LATE, board-wide, for its whole
            # history; at sol's declared 5 it called DARK at 10m against a 25m board
            # threshold, so sol spent its turns reporting its own false alarm.
            $darkThreshold = [math]::Min($minutes, $darkCap) + $darkGrace
            if ($leaseAge -le $darkThreshold) { $leaseState = 'LIVE'; $leaseReason = 'lease is within the board threshold' }
            else { $leaseState = 'DARK'; $leaseReason = "lease exceeded the board threshold of $darkThreshold minutes" }
        } catch { $leaseState = 'LEASE-UNPARSEABLE'; $leaseReason = $_.Exception.Message }
    }
    $journalAge = $null
    $journal = $journalPaths[$lane]
    if (Test-Path -LiteralPath $journal -PathType Leaf) {
        $journalAge = [math]::Round(($now - (Get-Item -LiteralPath $journal).LastWriteTimeUtc).TotalMinutes, 1)
    }
    $lanes += [ordered]@{ lane = $lane; leaseState = $leaseState; leaseAgeMinutes = $leaseAge; journalAgeMinutes = $journalAge; leaseReason = $leaseReason; adopted = ($adopted -contains $lane) }
}

$target = [ordered]@{ state = 'NOT_CONFIGURED'; valid = $false }
if ($AutomationPath -and $ExpectedThreadId) {
    try {
        $check = & (Join-Path $PSScriptRoot 'validate-heartbeat-target.ps1') -AutomationPath $AutomationPath -ExpectedThreadId $ExpectedThreadId 2>&1
        $target = (($check -join '') | ConvertFrom-Json)
        $target | Add-Member -NotePropertyName state -NotePropertyValue 'VALID' -Force
    } catch { $target = [ordered]@{ state = 'INVALID'; valid = $false; error = $_.Exception.Message } }
}
$markers = @()
if ($HeartbeatMarker) {
    $markerState = if (Test-Path -LiteralPath $HeartbeatMarker -PathType Leaf) { 'PRESENT' } else { 'MISSING' }
    $markers += [ordered]@{ surface = 'heartbeat-marker'; state = $markerState; path = $HeartbeatMarker }
}
$badAdopted = @($lanes | Where-Object { $_.adopted -and $_.leaseState -in @('MISSING','DARK','LEASE-UNPARSEABLE') })
$overall = 'HEALTHY'
if ($badAdopted.Count -gt 0 -or $target.state -eq 'INVALID') { $overall = 'DEGRADED' }
elseif ($target.state -eq 'NOT_CONFIGURED' -and $ExpectedThreadId) { $overall = 'ATTENTION' }
# The policy block publishes the class list and the boundary it actually used, so a
# consumer can tell which vocabulary produced a reading instead of assuming.
$result = [ordered]@{ schema = 'lane-health.v3'; observedUtc = $now.ToString('o'); overall = $overall; target = $target; lanes = $lanes; markers = $markers; policy = [ordered]@{ journalAgeIsAdvisory = $true; adoptedLeaseRequiredFor = $adopted; missingAdoptedLeaseIsDegraded = $true; unadoptedMissingLeaseIsAdvisory = $true; laneStateClasses = $classes.LaneStateClasses; darkThresholdFormula = $classes.DarkThresholdFormula } }
$json = $result | ConvertTo-Json -Depth 8 -Compress
if (-not $Quiet) { Write-Output $json }
if (Test-Path -LiteralPath $HealthDir -PathType Container) { Add-Content -LiteralPath (Join-Path $HealthDir 'health.log') -Value $json -Encoding utf8 }
if ($overall -eq 'DEGRADED') { exit 2 }
exit 0
