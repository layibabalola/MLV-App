param(
    [string]$RepoRoot = ".",
    [string]$HostName = "127.0.0.1",
    [int]$Port = 8765,
    [switch]$Foreground
)

$ErrorActionPreference = "Stop"

$resolvedRepo = (Resolve-Path -LiteralPath $RepoRoot).Path
$actionsUri = "http://$HostName`:$Port/api/closeout/actions"
$dashboardUrl = "http://$HostName`:$Port/closeout"

function Get-CloseoutDashboardActions {
    try {
        return Invoke-RestMethod -Uri $actionsUri -Method Get -TimeoutSec 2
    } catch {
        return $null
    }
}

function Test-SameDashboardRepo {
    param(
        [string]$Left,
        [string]$Right
    )
    if (-not $Left -or -not $Right) {
        return $false
    }
    try {
        $leftItem = Get-Item -LiteralPath $Left -ErrorAction Stop
        $rightItem = Get-Item -LiteralPath $Right -ErrorAction Stop
        return [string]::Equals($leftItem.FullName, $rightItem.FullName, [System.StringComparison]::OrdinalIgnoreCase)
    } catch {
        return [string]::Equals($Left, $Right, [System.StringComparison]::OrdinalIgnoreCase)
    }
}

function Quote-ProcessArgument {
    param([string]$Value)
    if ($Value -match '[\s"]') {
        return '"' + ($Value -replace '"', '\"') + '"'
    }
    return $Value
}

$existing = Get-CloseoutDashboardActions
if ($existing) {
    $existingRoot = [string]$existing.repoRoot
    if (Test-SameDashboardRepo -Left $existingRoot -Right $resolvedRepo) {
        [PSCustomObject]@{
            status = "reused"
            url = $dashboardUrl
            repoRoot = $resolvedRepo
            serverProcessId = $existing.serverProcessId
            duplicateLaunchPolicy = $existing.dashboard.duplicateLaunchPolicy
        } | ConvertTo-Json -Depth 6
        exit 0
    }
    throw "Port $Port already serves a closeout dashboard for '$existingRoot', not '$resolvedRepo'."
}

if ($Foreground) {
    Set-Location -LiteralPath $resolvedRepo
    $pythonExe = (& py -3 -c "import sys; print(sys.executable)").Trim()
    & $pythonExe -m tools.repo_hygiene.closeout_dashboard --repo-root $resolvedRepo --host $HostName --port $Port
    exit $LASTEXITCODE
}

$pythonExe = (& py -3 -c "import sys; print(sys.executable)").Trim()
$argsList = @(
    "-m",
    "tools.repo_hygiene.closeout_dashboard",
    "--repo-root",
    $resolvedRepo,
    "--host",
    $HostName,
    "--port",
    [string]$Port
)
$argumentString = ($argsList | ForEach-Object { Quote-ProcessArgument -Value ([string]$_) }) -join " "
$process = Start-Process -FilePath $pythonExe -ArgumentList $argumentString -WorkingDirectory $resolvedRepo -WindowStyle Hidden -PassThru

$deadline = (Get-Date).AddSeconds(10)
do {
    Start-Sleep -Milliseconds 250
    $started = Get-CloseoutDashboardActions
    if ($started -and (Test-SameDashboardRepo -Left ([string]$started.repoRoot) -Right $resolvedRepo)) {
        [PSCustomObject]@{
            status = "started"
            url = $dashboardUrl
            repoRoot = $resolvedRepo
            serverProcessId = $started.serverProcessId
            launcherProcessId = $PID
            childProcessId = $process.Id
            duplicateLaunchPolicy = $started.dashboard.duplicateLaunchPolicy
        } | ConvertTo-Json -Depth 6
        exit 0
    }
} while ((Get-Date) -lt $deadline -and -not $process.HasExited)

if ($process.HasExited) {
    throw "Closeout dashboard process exited before readiness check passed."
}
throw "Closeout dashboard readiness check failed for $actionsUri."
