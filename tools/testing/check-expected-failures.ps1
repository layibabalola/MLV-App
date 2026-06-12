# Machine-checked TESTS gate: compares a pipeline-suite output (the raw text
# captured from run-windows-test.ps1) against tests/pipeline/expected-failures.txt.
# Exit 0 = observed failures are a subset of the manifest (newly-passing names
# are listed for pruning). Exit 1 = NEW failure names (regression). Exit 2 =
# usage/input error.
param(
    [Parameter(Mandatory = $true)][string]$SuiteOutput,
    [string]$Manifest = ""
)
$ErrorActionPreference = 'Stop'
if (-not (Test-Path -LiteralPath $SuiteOutput)) { Write-Error "suite output not found: $SuiteOutput"; exit 2 }
if ([string]::IsNullOrWhiteSpace($Manifest)) {
    $Manifest = Join-Path (Split-Path -Parent (Split-Path -Parent $PSScriptRoot)) "tests\pipeline\expected-failures.txt"
}
if (-not (Test-Path -LiteralPath $Manifest)) { Write-Error "manifest not found: $Manifest"; exit 2 }

$expected = Get-Content -LiteralPath $Manifest |
    ForEach-Object { ($_ -replace '#.*$', '').Trim() } |
    Where-Object { $_ -ne '' }

$failMatches = Select-String -LiteralPath $SuiteOutput -Pattern '\[FAIL\] (\S+)'
$observed = @()
foreach ($m in $failMatches.Matches) {
    $observed += ($m.Groups[1].Value -replace '\x1b\[[0-9;]*m', '')
}
$observed = @($observed | Select-Object -Unique)

$new = @($observed | Where-Object { $_ -notin $expected })
$cleared = @($expected | Where-Object { $_ -notin $observed })

Write-Host ("EXPECTED-FAILURES-CHECK observed={0} expected={1} new={2} newly_passing={3}" -f `
    $observed.Count, $expected.Count, $new.Count, $cleared.Count)
if ($cleared.Count -gt 0) {
    Write-Host "  newly-passing (prune from the manifest or note the flake):"
    $cleared | ForEach-Object { Write-Host "    + $_" }
}
if ($new.Count -gt 0) {
    Write-Host "  NEW FAILURES (regression - not in the manifest):"
    $new | ForEach-Object { Write-Host "    ! $_" }
    exit 1
}
exit 0
