param(
    [string]$RepoRoot = ".",
    [string]$Surface = "codex-desktop",
    [switch]$MarkUnavailable,
    [switch]$CollectResults,
    [switch]$RetireStalePlanAbsent
)

if ($CollectResults) {
    $argsList = @("agent-results")
} elseif ($RetireStalePlanAbsent) {
    $argsList = @("agent-queue", "--retire-stale-plan-absent")
} else {
    $argsList = @("agent-queue", "--surface", $Surface)
    if ($MarkUnavailable) {
        $argsList += "--mark-unavailable"
    }
}

& (Join-Path $PSScriptRoot "Invoke-CloseoutCli.ps1") -RepoRoot $RepoRoot -Arguments $argsList
