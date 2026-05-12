param(
    [string]$RepoRoot = ".",
    [Parameter(Mandatory = $true)]
    [string]$ManifestPath
)

$argsList = @("validate-rollback-manifest", "--manifest-path", $ManifestPath)
& (Join-Path $PSScriptRoot "Invoke-CloseoutCli.ps1") -RepoRoot $RepoRoot -Arguments $argsList
exit $LASTEXITCODE
