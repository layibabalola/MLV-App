param(
    [Parameter(Mandatory = $true)][string]$RepoRoot,
    [Parameter(Mandatory = $true)]
    [ValidateSet('fable', 'codex', 'sol', 'opus', 'claude-review')]
    [string]$Lane,
    [ValidateRange(1, 1440)][int]$LeaseMinutes = 30,
    [string]$Note = ''
)

$ErrorActionPreference = 'Stop'
$root = (Resolve-Path -LiteralPath $RepoRoot).Path
$leasesDir = Join-Path $root '.claude-state\coordination\dual-lane\health\leases'
New-Item -ItemType Directory -Path $leasesDir -Force | Out-Null
$path = Join-Path $leasesDir "$Lane.json"
$payload = [ordered]@{
    lane = $Lane
    renewed = [DateTime]::UtcNow.ToString('o')
    leaseMinutes = $LeaseMinutes
}
if ($Note) { $payload.note = $Note }
$json = $payload | ConvertTo-Json -Compress
$temp = Join-Path $leasesDir (".{0}.{1}.tmp" -f $Lane, [Guid]::NewGuid().ToString('N'))
[IO.File]::WriteAllText($temp, $json + [Environment]::NewLine, [Text.UTF8Encoding]::new($false))
Move-Item -LiteralPath $temp -Destination $path -Force
Write-Output $json
