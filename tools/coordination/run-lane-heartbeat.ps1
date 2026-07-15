param(
    [Parameter(Mandatory = $true)][string]$RepoRoot,
    [Parameter(Mandatory = $true)]
    [ValidateSet('codex', 'sol', 'claude-review')]
    [string]$Lane,
    [ValidateRange(1, 1440)][int]$LeaseMinutes = 30,
    [string]$AutomationPath = '',
    [string]$ExpectedThreadId = '',
    [switch]$Once
)

$ErrorActionPreference = 'Stop'
$PSNativeCommandUseErrorActionPreference = $false
if ([bool]$AutomationPath -xor [bool]$ExpectedThreadId) {
    throw 'AutomationPath and ExpectedThreadId must be provided together'
}
$root = (Resolve-Path -LiteralPath $RepoRoot).Path
$leaseRaw = & (Join-Path $PSScriptRoot 'renew-lane-lease.ps1') -RepoRoot $root -Lane $Lane -LeaseMinutes $LeaseMinutes -Note 'automation heartbeat'
$lease = $leaseRaw | ConvertFrom-Json

$healthDir = Join-Path $root '.claude-state\coordination\dual-lane\health'
if ($AutomationPath) {
    $sweepRaw = & (Join-Path $PSScriptRoot 'sweep-lane-health.ps1') `
        -HealthDir $healthDir `
        -AutomationPath $AutomationPath `
        -ExpectedThreadId $ExpectedThreadId 2>&1
} else {
    $sweepRaw = & (Join-Path $PSScriptRoot 'sweep-lane-health.ps1') `
        -HealthDir $healthDir 2>&1
}
$sweepExit = $LASTEXITCODE
$sweepText = (($sweepRaw | Out-String).Trim())
$sweep = $sweepText | ConvertFrom-Json

$watchdog = $null
$watchdogExit = 0
if ($Lane -eq 'codex') {
    $watchdogRaw = & (Join-Path $PSScriptRoot 'coordination-heartbeat.ps1') -RepoRoot $root -Once 2>&1
    $watchdogExit = $LASTEXITCODE
    $watchdogText = (($watchdogRaw | Out-String).Trim())
    $watchdog = $watchdogText | ConvertFrom-Json
}

$result = [ordered]@{
    schema = 'lane-heartbeat.v1'
    lane = $Lane
    observedUtc = [DateTime]::UtcNow.ToString('o')
    lease = $lease
    health = $sweep
    watchdog = $watchdog
    exit = [ordered]@{ sweep = $sweepExit; watchdog = $watchdogExit }
}
Write-Output ($result | ConvertTo-Json -Depth 10 -Compress)
if ($watchdogExit -ne 0) { exit $watchdogExit }
if ($sweepExit -ne 0) { exit $sweepExit }
exit 0
