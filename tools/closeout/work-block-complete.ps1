param(
    [string]$RepoRoot = ".",
    [string]$WorkBlockId,
    [switch]$Finalize
)

$argsList = @("complete")
if ($WorkBlockId) {
    $argsList += @("--work-block-id", $WorkBlockId)
}
if ($Finalize) {
    $argsList += "--finalize"
}
& (Join-Path $PSScriptRoot "Invoke-CloseoutCli.ps1") -RepoRoot $RepoRoot -Arguments $argsList
