param(
    [Parameter(Mandatory = $true)][string]$RepoRoot,
    [Parameter(Mandatory = $true)][string]$Owner,
    [Parameter(Mandatory = $true)][string]$FilePath,
    [string[]]$ArgumentList = @(),
    [string]$WorkingDirectory = "",
    [int]$TimeoutSeconds = 0
)

$ErrorActionPreference = 'Stop'
$root = (Resolve-Path -LiteralPath $RepoRoot).Path
$working = if ($WorkingDirectory) { (Resolve-Path -LiteralPath $WorkingDirectory).Path } else { $root }
$coordination = Join-Path $root '.claude-state\coordination\build-ownership.jsonl'
$lockDir = Join-Path $root '.claude-state\coordination\locks'
$lockPath = Join-Path $lockDir 'mlvapp-exclusive.lock'
New-Item -ItemType Directory -Force -Path $lockDir | Out-Null
New-Item -ItemType Directory -Force -Path (Split-Path -Parent $coordination) | Out-Null

$record = [ordered]@{
    owner = $Owner
    startTimeUtc = (Get-Date).ToUniversalTime().ToString('o')
    pid = $null
    pidTree = @()
    command = @($FilePath) + @($ArgumentList)
    outcome = 'not-started'
    exitCode = $null
    cleanupState = 'not-started'
}
$stream = $null
$process = $null
function Get-Snapshot {
    @(Get-CimInstance Win32_Process | Select-Object ProcessId, ParentProcessId, Name, CreationDate, CommandLine)
}
function Get-Descendants([int]$ParentId, [object[]]$Snapshot) {
    $children = @($Snapshot | Where-Object { [int]$_.ParentProcessId -eq $ParentId })
    $result = New-Object System.Collections.Generic.List[object]
    foreach ($child in $children) {
        $result.Add($child)
        foreach ($nested in (Get-Descendants -ParentId ([int]$child.ProcessId) -Snapshot $Snapshot)) { $result.Add($nested) }
    }
    return $result.ToArray()
}
function Stop-OwnedTree([int]$RootPid) {
    # Exactly one cached process snapshot is used for this cleanup pass.
    $snapshot = Get-Snapshot
    $descendants = @(Get-Descendants -ParentId $RootPid -Snapshot $snapshot | Sort-Object @{Expression={($_.CommandLine -split '\s+').Count};Descending=$true})
    foreach ($child in $descendants) {
        try { Stop-Process -Id ([int]$child.ProcessId) -Force -ErrorAction Stop } catch { }
    }
    return @($descendants | ForEach-Object { [int]$_.ProcessId })
}
try {
    try {
        $stream = [System.IO.File]::Open($lockPath, [System.IO.FileMode]::OpenOrCreate, [System.IO.FileAccess]::ReadWrite, [System.IO.FileShare]::None)
    } catch {
        throw "MLV-App exclusive runner is busy: $lockPath"
    }
    $lockText = ([System.Text.Encoding]::UTF8.GetBytes(($record | ConvertTo-Json -Compress)))
    $stream.SetLength(0); $stream.Write($lockText, 0, $lockText.Length); $stream.Flush()
    $process = Start-Process -FilePath $FilePath -ArgumentList $ArgumentList -WorkingDirectory $working -PassThru -NoNewWindow
    $record.pid = $process.Id
    $record.outcome = 'running'
    # Cache one ownership snapshot immediately after spawn; never recursively poll CIM.
    $spawnSnapshot = Get-Snapshot
    $record.pidTree = @($process.Id) + @(Get-Descendants -ParentId $process.Id -Snapshot $spawnSnapshot | ForEach-Object { [int]$_.ProcessId })
    if ($TimeoutSeconds -gt 0 -and -not $process.WaitForExit($TimeoutSeconds * 1000)) {
        $record.outcome = 'timeout'
        $record.cleanupState = 'terminating-owned-descendants'
        [void](Stop-OwnedTree -RootPid $process.Id)
        try { Stop-Process -Id $process.Id -Force -ErrorAction SilentlyContinue } catch { }
        throw "MLV-App exclusive command timed out after ${TimeoutSeconds}s (pid=$($process.Id))"
    }
    $process.WaitForExit()
    $record.exitCode = $process.ExitCode
    $record.outcome = if ($process.ExitCode -eq 0) { 'succeeded' } else { 'failed' }
    $record.cleanupState = 'awaited; no owned descendants expected'
    exit $process.ExitCode
} finally {
    if ($process -and $process.HasExited -eq $false) {
        $record.cleanupState = 'terminating-owned-descendants'
        [void](Stop-OwnedTree -RootPid $process.Id)
        try { Stop-Process -Id $process.Id -Force -ErrorAction SilentlyContinue } catch { }
    }
    if ($stream) { $stream.Dispose() }
    $record.endTimeUtc = (Get-Date).ToUniversalTime().ToString('o')
    Add-Content -LiteralPath $coordination -Value ($record | ConvertTo-Json -Compress) -Encoding UTF8
}
