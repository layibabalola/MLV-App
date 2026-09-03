[CmdletBinding()]
param(
    [Parameter(Mandatory)] [string] $ExpectedRunnerPath,
    [Parameter(Mandatory)] [string] $DeployedRunnerPath,
    [Parameter(Mandatory)] [string] $ExpectedModulePath,
    [Parameter(Mandatory)] [string] $DeployedModulePath,
    [string] $ScheduledTaskName,
    [string] $ResultPath
)

$ErrorActionPreference = 'Stop'
function Get-HashOrNull([string]$Path) {
    if (-not (Test-Path -LiteralPath $Path -PathType Leaf)) { return $null }
    return (Get-FileHash -LiteralPath $Path -Algorithm SHA256).Hash.ToUpperInvariant()
}
$expectedRunnerHash = Get-HashOrNull $ExpectedRunnerPath
$deployedRunnerHash = Get-HashOrNull $DeployedRunnerPath
$expectedModuleHash = Get-HashOrNull $ExpectedModulePath
$deployedModuleHash = Get-HashOrNull $DeployedModulePath
$taskXml = $null
$taskActionContainsRunner = $null
if ($ScheduledTaskName) {
    $taskXml = (& schtasks.exe /Query /TN $ScheduledTaskName /XML 2>$null | Out-String)
    $taskActionContainsRunner = $LASTEXITCODE -eq 0 -and $taskXml.IndexOf($DeployedRunnerPath, [StringComparison]::OrdinalIgnoreCase) -ge 0
}
$valid = $null -ne $expectedRunnerHash -and $expectedRunnerHash -ceq $deployedRunnerHash -and
    $null -ne $expectedModuleHash -and $expectedModuleHash -ceq $deployedModuleHash -and
    ($null -eq $taskActionContainsRunner -or $taskActionContainsRunner)
$result = [ordered]@{
    schema = 'mlvapp.presentmon-sidecar-deployment-proof.v1'
    verifiedUtc = [DateTimeOffset]::UtcNow.ToString('o')
    valid = $valid
    runner = [ordered]@{ expectedPath=$ExpectedRunnerPath; deployedPath=$DeployedRunnerPath; expectedSha256=$expectedRunnerHash; deployedSha256=$deployedRunnerHash; match=($expectedRunnerHash -ceq $deployedRunnerHash) }
    module = [ordered]@{ expectedPath=$ExpectedModulePath; deployedPath=$DeployedModulePath; expectedSha256=$expectedModuleHash; deployedSha256=$deployedModuleHash; match=($expectedModuleHash -ceq $deployedModuleHash) }
    scheduledTask = [ordered]@{ name=$ScheduledTaskName; actionContainsDeployedRunner=$taskActionContainsRunner }
}
$json = $result | ConvertTo-Json -Depth 10
if ($ResultPath) { [IO.File]::WriteAllText($ResultPath, $json + [Environment]::NewLine, [Text.UTF8Encoding]::new($false)) }
$json
if (-not $valid) { exit 1 }
