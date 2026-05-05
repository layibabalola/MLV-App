param(
    [string]$RepoRoot = ".",
    [int]$Limit = 20
)

$argsList = @("audit", "--limit", [string]$Limit)
& (Join-Path $PSScriptRoot "Invoke-CloseoutCli.ps1") -RepoRoot $RepoRoot -Arguments $argsList
