param(
    [string]$RepoRoot = ".",
    [string]$WorkBlockId,
    [switch]$Finalize,
    [switch]$AutoApprove,
    [switch]$RequireRepoClosed
)

$argsList = @("complete")
if ($WorkBlockId) {
    $argsList += @("--work-block-id", $WorkBlockId)
}
if ($Finalize) {
    $argsList += "--finalize"
}
if ($AutoApprove) {
    $argsList += "--auto-approve"
}
if ($RequireRepoClosed) {
    $argsList += "--require-repo-closed"
}
& (Join-Path $PSScriptRoot "Invoke-CloseoutCli.ps1") -RepoRoot $RepoRoot -Arguments $argsList
