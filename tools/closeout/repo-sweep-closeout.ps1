param(
    [string]$RepoRoot = ".",
    [switch]$Apply,
    [switch]$PrintTuple
)

$argsList = @("sweep")
if ($Apply) {
    $argsList += "--apply"
}
if ($PrintTuple) {
    $argsList += "--print-tuple"
}
& (Join-Path $PSScriptRoot "Invoke-CloseoutCli.ps1") -RepoRoot $RepoRoot -Arguments $argsList
