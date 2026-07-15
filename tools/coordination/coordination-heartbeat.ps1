param(
    [Parameter(Mandatory = $true)]
    [string]$RepoRoot,
    [switch]$Once
)

$ErrorActionPreference = 'Stop'
$script = Join-Path $PSScriptRoot 'coordination_watchdog.py'
$arguments = @($script, '--repo-root', $RepoRoot)
$python3 = Get-Command python3.exe -ErrorAction SilentlyContinue
$py = Get-Command py -ErrorAction SilentlyContinue
if ($python3) {
    $result = & $python3.Source @arguments 2>&1
} elseif ($py) {
    $result = & $py.Source -3 @arguments 2>&1
} else {
    throw 'Python 3 launcher not found (tried python3.exe and py)'
}
$exitCode = $LASTEXITCODE
$result | Write-Output

if (-not $Once) {
    while ($true) {
        Start-Sleep -Seconds 600
        if ($python3) {
            $result = & $python3.Source @arguments 2>&1
        } else {
            $result = & $py.Source -3 @arguments 2>&1
        }
        $exitCode = $LASTEXITCODE
        $result | Write-Output
    }
}

exit $exitCode
