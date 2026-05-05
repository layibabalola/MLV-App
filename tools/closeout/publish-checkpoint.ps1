param(
    [string]$RepoRoot = ".",
    [string]$WorkBlockId,
    [string]$Message = "brokered closeout checkpoint"
)

$argsList = @("checkpoint", "--message", $Message)
if ($WorkBlockId) {
    $argsList += @("--work-block-id", $WorkBlockId)
}
& (Join-Path $PSScriptRoot "Invoke-CloseoutCli.ps1") -RepoRoot $RepoRoot -Arguments $argsList
