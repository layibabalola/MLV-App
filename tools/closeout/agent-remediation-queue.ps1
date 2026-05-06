param(
    [string]$RepoRoot = ".",
    [string]$Surface = "codex-desktop",
    [switch]$MarkUnavailable,
    [switch]$CollectResults
)

if ($CollectResults) {
    $argsList = @("agent-results")
} else {
    $argsList = @("agent-queue", "--surface", $Surface)
    if ($MarkUnavailable) {
        $argsList += "--mark-unavailable"
    }
}

& (Join-Path $PSScriptRoot "Invoke-CloseoutCli.ps1") -RepoRoot $RepoRoot -Arguments $argsList
