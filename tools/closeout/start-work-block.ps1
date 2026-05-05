param(
    [string]$RepoRoot = ".",
    [string]$WorkBlockId,
    [string]$Actor = "codex",
    [string[]]$Claim = @()
)

$argsList = @("start", "--actor", $Actor)
if ($WorkBlockId) {
    $argsList += @("--work-block-id", $WorkBlockId)
}
foreach ($item in $Claim) {
    $argsList += @("--claim", $item)
}
& (Join-Path $PSScriptRoot "Invoke-CloseoutCli.ps1") -RepoRoot $RepoRoot -Arguments $argsList
