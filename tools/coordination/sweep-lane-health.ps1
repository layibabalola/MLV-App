param(
    [string]$HealthDir = (Join-Path (Get-Location) '.claude-state\coordination\dual-lane\health'),
    [string]$AutomationPath = '',
    [string]$ExpectedThreadId = '',
    [string]$HeartbeatMarker = '',
    [int]$NowOffsetMinutes = 0,
    [switch]$Quiet
)

$ErrorActionPreference = 'Stop'
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
            if ($leaseAge -le $minutes) { $leaseState = 'LIVE'; $leaseReason = 'adopted lease is fresh' }
            elseif ($leaseAge -le (2 * $minutes)) { $leaseState = 'EXPIRED'; $leaseReason = 'lease exceeded its renewal window' }
            else { $leaseState = 'DARK'; $leaseReason = 'lease exceeded two renewal windows' }
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
$expiredAdopted = @($lanes | Where-Object { $_.adopted -and $_.leaseState -eq 'EXPIRED' })
$overall = 'HEALTHY'
if ($badAdopted.Count -gt 0 -or $target.state -eq 'INVALID') { $overall = 'DEGRADED' }
elseif ($expiredAdopted.Count -gt 0 -or ($target.state -eq 'NOT_CONFIGURED' -and $ExpectedThreadId)) { $overall = 'ATTENTION' }
$result = [ordered]@{ schema = 'lane-health.v3'; observedUtc = $now.ToString('o'); overall = $overall; target = $target; lanes = $lanes; markers = $markers; policy = [ordered]@{ journalAgeIsAdvisory = $true; adoptedLeaseRequiredFor = $adopted; missingAdoptedLeaseIsDegraded = $true; unadoptedMissingLeaseIsAdvisory = $true } }
$json = $result | ConvertTo-Json -Depth 8 -Compress
if (-not $Quiet) { Write-Output $json }
if (Test-Path -LiteralPath $HealthDir -PathType Container) { Add-Content -LiteralPath (Join-Path $HealthDir 'health.log') -Value $json -Encoding utf8 }
if ($overall -eq 'DEGRADED') { exit 2 }
exit 0
