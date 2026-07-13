param(
    [Parameter(Mandatory = $true)][string]$AutomationPath,
    [Parameter(Mandatory = $true)][string]$ExpectedThreadId
)

$ErrorActionPreference = 'Stop'
if (-not (Test-Path -LiteralPath $AutomationPath -PathType Leaf)) {
    throw "heartbeat automation is missing: $AutomationPath"
}
$text = Get-Content -LiteralPath $AutomationPath -Raw
$match = [regex]::Match($text, '(?m)^target_thread_id\s*=\s*"(?<id>[^"]+)"\s*$')
if (-not $match.Success) { throw 'heartbeat automation has no target_thread_id' }
$actual = $match.Groups['id'].Value
if ($actual -notmatch '^[0-9a-f]{8}-[0-9a-f]{4}-[0-9a-f]{4}-[0-9a-f]{4}-[0-9a-f]{12}$') {
    throw "heartbeat target is not a UUID: $actual"
}
if ($actual -ne $ExpectedThreadId) {
    throw "heartbeat target mismatch: expected $ExpectedThreadId, actual $actual"
}
Write-Output ([ordered]@{ valid = $true; targetThreadId = $actual; automationPath = (Resolve-Path -LiteralPath $AutomationPath).Path } | ConvertTo-Json -Compress)
