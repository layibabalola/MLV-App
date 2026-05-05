param(
    [string]$RepoRoot = ".",
    [string]$WorkBlockId,
    [string]$ExpectedPinnedRefsFile
)

$argsList = @("finalize")
if ($WorkBlockId) {
    $argsList += @("--work-block-id", $WorkBlockId)
}
if ($ExpectedPinnedRefsFile) {
    $argsList += @("--expected-pinned-refs-file", $ExpectedPinnedRefsFile)
}
& (Join-Path $PSScriptRoot "Invoke-CloseoutCli.ps1") -RepoRoot $RepoRoot -Arguments $argsList
