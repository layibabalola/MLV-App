param(
    [Parameter(Mandatory = $true)]
    [string]$RepoRoot,
    [switch]$Once
)

$ErrorActionPreference = 'Stop'
$script = Join-Path $RepoRoot 'tools\coordination\coordination_watchdog.py'
$arguments = @($script, '--repo-root', $RepoRoot)
$result = & py -3 @arguments 2>&1
$exitCode = $LASTEXITCODE
$result | Write-Output

if (-not $Once) {
    while ($true) {
        Start-Sleep -Seconds 600
        $result = & py -3 @arguments 2>&1
        $exitCode = $LASTEXITCODE
        $result | Write-Output
    }
}

exit $exitCode
