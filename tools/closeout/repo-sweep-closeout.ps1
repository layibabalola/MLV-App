param(
    [string]$RepoRoot = ".",
    [switch]$Apply,
    [switch]$PrintTuple,
    [string]$CandidateId,
    [string]$BulkOverrideFile
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
if ($BulkOverrideFile) {
    $argsList += @("--bulk-override-file", $BulkOverrideFile)
}
& (Join-Path $PSScriptRoot "Invoke-CloseoutCli.ps1") -RepoRoot $RepoRoot -Arguments $argsList
