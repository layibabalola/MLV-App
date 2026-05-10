param(
    [string]$RepoRoot = ".",
    [switch]$Write,
    [switch]$LatestOnly,
    [string]$WorkBlockId
)

$argsList = @("repo-state")
if ($Write) {
    $argsList += "--write"
}
if ($LatestOnly) {
    $argsList += "--latest-only"
}
if ($WorkBlockId) {
    $argsList += @("--work-block-id", $WorkBlockId)
}
& (Join-Path $PSScriptRoot "Invoke-CloseoutCli.ps1") -RepoRoot $RepoRoot -Arguments $argsList
