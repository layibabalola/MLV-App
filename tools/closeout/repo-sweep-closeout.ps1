param(
    [string]$RepoRoot = ".",
    [switch]$Apply,
    [switch]$PrintTuple,
    [string]$CandidateId
)

$argsList = @("sweep")
if ($Apply) {
    $argsList += "--apply"
}
if ($PrintTuple) {
    $argsList += "--print-tuple"
}
if ($CandidateId) {
    $argsList += @("--candidate-id", $CandidateId)
}
& (Join-Path $PSScriptRoot "Invoke-CloseoutCli.ps1") -RepoRoot $RepoRoot -Arguments $argsList
