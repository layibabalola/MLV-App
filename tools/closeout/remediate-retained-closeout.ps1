param(
    [string]$RepoRoot = ".",
    [switch]$Apply,
    [string]$CandidateId
)

$argsList = @("remediate-retained")
if ($Apply) {
    $argsList += "--apply"
}
if ($CandidateId) {
    $argsList += @("--candidate-id", $CandidateId)
}
& (Join-Path $PSScriptRoot "Invoke-CloseoutCli.ps1") -RepoRoot $RepoRoot -Arguments $argsList
