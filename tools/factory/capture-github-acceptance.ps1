[CmdletBinding()]
param(
    [Parameter(Mandatory = $true)][string]$RepoRoot,
    [Parameter(Mandatory = $true)][string]$Repository,
    [Parameter(Mandatory = $true)][string]$HeadSha,
    [string]$Ledger,
    [string]$Python = "py",
    [string[]]$PythonArguments = @("-3.13")
)

$ErrorActionPreference = "Stop"

if ($Repository -notmatch '^[A-Za-z0-9_.-]+/[A-Za-z0-9_.-]+$') {
    throw "Repository must be an owner/name identifier."
}
if ($HeadSha -cnotmatch '^[0-9a-f]{40}$') {
    throw "HeadSha must be a full lowercase Git SHA."
}

$root = (Resolve-Path -LiteralPath $RepoRoot).Path
$ledgerPath = if ($Ledger) { (Resolve-Path -LiteralPath $Ledger).Path } else { Join-Path $root '.claude-state\closeout\acceptance\latest.json' }
if (-not (Test-Path -LiteralPath $ledgerPath -PathType Leaf)) {
    throw "Candidate acceptance ledger is missing: $ledgerPath"
}
$ledgerObject = Get-Content -LiteralPath $ledgerPath -Raw | ConvertFrom-Json -Depth 100
if ([string]$ledgerObject.candidate.featureHead -cne $HeadSha) {
    throw "Ledger feature head does not match HeadSha."
}

$apiPath = "repos/$Repository/commits/$HeadSha/check-runs?per_page=100"
$raw = & gh api -H 'Accept: application/vnd.github+json' $apiPath
$ghExit = $LASTEXITCODE
if ($ghExit -ne 0) {
    throw "gh api check-run capture failed with exit $ghExit."
}
$payload = $raw | ConvertFrom-Json -Depth 100
if ([int]$payload.total_count -gt 100) {
    throw "Check-run response exceeds the fail-closed single-page bound."
}
$runs = @($payload.check_runs)
if ($runs.Count -ne [int]$payload.total_count) {
    throw "Check-run response is truncated or malformed."
}
foreach ($run in $runs) {
    if ([string]$run.head_sha -cne $HeadSha) {
        throw "Check-run response contains a different head SHA."
    }
}

$providerRoot = Join-Path $root ".claude-state\closeout\acceptance\provider\$HeadSha"
New-Item -ItemType Directory -Path $providerRoot -Force | Out-Null
$providerPath = Join-Path $providerRoot 'check-runs.json'
$payload | ConvertTo-Json -Depth 100 | Set-Content -LiteralPath $providerPath -Encoding utf8NoBOM

function Select-LatestCheck([string]$Name) {
    $matches = @($runs | Where-Object { [string]$_.name -ceq $Name })
    if ($matches.Count -eq 0) { return $null }
    return $matches | Sort-Object -Property @{Expression = { [string]$_.started_at }}, @{Expression = { [long]$_.id }} -Descending | Select-Object -First 1
}

function Get-ExpectedCheckAppId([string]$Name) {
    if ($Name -ceq 'CodeQL') { return [long]57789 }
    return [long]15368
}

function Record-Surface([string]$Surface, [string[]]$Names, [bool]$RequireZeroAnnotations) {
    $selected = @()
    foreach ($name in $Names) {
        $run = Select-LatestCheck $name
        if ($null -eq $run) {
            Write-Output "collecting: $Surface is missing $name"
            return
        }
        $selected += $run
    }
    $nonterminal = @($selected | Where-Object { [string]$_.status -cne 'completed' })
    if ($nonterminal.Count -gt 0) {
        Write-Output "collecting: $Surface has nonterminal checks"
        return
    }
    $failed = @($selected | Where-Object {
        $expectedAppId = Get-ExpectedCheckAppId ([string]$_.name)
        [string]$_.conclusion -cne 'success' -or
        [long]$_.app.id -ne $expectedAppId -or
        ($RequireZeroAnnotations -and [int]$_.output.annotations_count -ne 0)
    })
    $verdict = if ($failed.Count -eq 0) { 'APPROVE' } else { 'CHANGES_REQUESTED' }
    $sessionId = 'github-checks:' + $HeadSha + ':' + (($selected | ForEach-Object { [string]$_.id }) -join ',')
    $arguments = @($PythonArguments) + @(
        '-m', 'tools.repo_hygiene.candidate_acceptance', 'record',
        '--repo-root', $root,
        '--ledger', $ledgerPath,
        '--surface', $Surface,
        '--verdict', $verdict,
        '--reviewer', 'github-actions',
        '--session-id', $sessionId
    )
    foreach ($item in $failed) {
        $finding = @{
            id = "github-check-$($item.id)"
            invariant = "$($item.name) must complete successfully at the exact candidate head"
            falsifier = "status=$($item.status); conclusion=$($item.conclusion); app=$($item.app.id); annotations=$($item.output.annotations_count)"
            detail = [string]$item.details_url
        } | ConvertTo-Json -Compress
        $arguments += @('--finding', $finding)
    }
    & $Python @arguments
    $recordExit = $LASTEXITCODE
    if ($recordExit -ne 0) {
        throw "Candidate acceptance record failed for $Surface with exit $recordExit."
    }
}

Record-Surface 'hosted-tests' @(
    'Repo Hygiene Python (windows-latest)',
    'Repo Hygiene Python (ubuntu-latest)',
    'Factory Bridge Regressions',
    'Windows GUI Pilot',
    'Windows Product Oracles',
    'Protected Check Route'
) $false

Record-Surface 'hosted-codeql' @(
    'Analyze (actions)',
    'Analyze (c-cpp)',
    'Analyze (python)',
    'CodeQL'
) $true
