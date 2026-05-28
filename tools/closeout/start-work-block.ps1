param(
    [string]$RepoRoot = ".",
    [string]$WorkBlockId,
    [string]$Actor = "codex",
    [string[]]$Claim = @(),
    [string]$Summary
)

$argsList = @("start", "--actor", $Actor)
if ($WorkBlockId) {
    $argsList += @("--work-block-id", $WorkBlockId)
}
if ($Summary) {
    $argsList += @("--summary", $Summary)
}
foreach ($item in $Claim) {
    $argsList += @("--claim", $item)
}
& (Join-Path $PSScriptRoot "Invoke-CloseoutCli.ps1") -RepoRoot $RepoRoot -Arguments $argsList
