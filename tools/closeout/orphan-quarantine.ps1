param(
    [string]$RepoRoot = ".",
    [switch]$Apply
)

$argsList = @("orphan-quarantine")
if ($Apply) {
    $argsList += "--apply"
}
& (Join-Path $PSScriptRoot "Invoke-CloseoutCli.ps1") -RepoRoot $RepoRoot -Arguments $argsList
