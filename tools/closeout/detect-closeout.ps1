param(
    [string]$RepoRoot = ".",
    [string]$WorkBlockId
)

$argsList = @("detect")
if ($WorkBlockId) {
    $argsList += @("--work-block-id", $WorkBlockId)
}
& (Join-Path $PSScriptRoot "Invoke-CloseoutCli.ps1") -RepoRoot $RepoRoot -Arguments $argsList
