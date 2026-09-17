# um-run.ps1
# RUN THIS ON THE VM. Submits a PowerShell script to the Ultra-Magnus file-drop
# agent over SMB, waits for the result, and returns it (exitCode/stdout/stderr).
# Requires the host agent to be running (install-ultra-magnus-agent.ps1).
#
# Usage:
#   $r = .\um-run.ps1 -ScriptPath .\some-host-job.ps1
#   $r.exitCode; $r.stdout
#   # or inline:
#   $r = .\um-run.ps1 -Command 'nvidia-smi; nvcc --version'
#   # a generated job that expects side-files next to it in inbox\ (e.g. <jobId>-source.zip):
#   $r = .\um-run.ps1 -ScriptPath .\out\<jobId>.job.ps1 -SideFile '.\out\<jobId>-source.zip' -JobId <jobId>
#
# SIDE-FILES. Generated jobs (tools/profiling/ultramagnus/playback-attr-3-cuda-dll-job.ps1,
# tools/profiling/bachelor/playback-attr-3-cuda-stage-job.ps1) read their inputs from inbox\ by
# name and verify them against hashes baked in at generation time. Placing those inputs is a
# write to the agent share, so it happens HERE, in this tracked submitter, never by hand
# (NA-7 refuses a hooked write to the share, and hiding the destination in a variable is not a
# workaround). Each side-file is copied to "<name>.sidepart", its sha256 re-read FROM THE SHARE
# and compared with the local file, then renamed to its final name; only after every side-file
# is in place is the job itself dropped. The agent executes only *.job.ps1, so a half-copied
# side-file is never run and the job never starts before its inputs exist. An existing
# side-file with the same name is replaced only if its hash already matches (idempotent re-run);
# a different file with that name is refused. `pwsh -File` cannot pass arrays, so -SideFile also
# accepts a ';'-separated list.

[CmdletBinding(DefaultParameterSetName = 'Script')]
param(
    [Parameter(Mandatory, ParameterSetName = 'Script')]
    [string]$ScriptPath,
    [Parameter(Mandatory, ParameterSetName = 'Command')]
    [string]$Command,
    [string]$AgentShare = "\\Ultra-Magnus\g\Temp\mlv-gpu-profile\agent",
    [int]$TimeoutSec = 1800,
    [int]$PollSeconds = 3,
    [int]$MaxHeartbeatAgeSec = 30,
    [string[]]$SideFile = @(),
    # Keep a generator's own job id (so outbox\<JobId>.result.json and its artifacts line up).
    [ValidatePattern('^[A-Za-z0-9][A-Za-z0-9._-]{0,127}$')]
    [string]$JobId = ''
)

$ErrorActionPreference = "Stop"
$inbox  = Join-Path $AgentShare "inbox"
$outbox = Join-Path $AgentShare "outbox"
$hb     = Join-Path $AgentShare "heartbeat.txt"

# Liveness gate: never submit into a dead agent (mirrors the bridge Monitor lesson).
if (-not (Test-Path $hb)) { throw "No agent heartbeat at $hb - run install-ultra-magnus-agent.ps1 on the host first." }
$age = [math]::Abs(((Get-Date).ToUniversalTime() - (Get-Item $hb).LastWriteTimeUtc).TotalSeconds)
if ($age -gt $MaxHeartbeatAgeSec) { Write-Warning ("agent heartbeat is {0:N0}s old - it may be down" -f $age) }

# Build the job script body.
if ($PSCmdlet.ParameterSetName -eq 'Command') {
    $tmpSrc = [System.IO.Path]::GetTempFileName() + ".ps1"
    Set-Content -Encoding ASCII $tmpSrc $Command
    $ScriptPath = $tmpSrc
}
if (-not (Test-Path $ScriptPath)) { throw "Script not found: $ScriptPath" }

$jobId = if ($JobId) { $JobId } else { "job_{0}_{1}" -f (Get-Date -Format "yyyyMMdd_HHmmss"), (Get-Random -Maximum 99999) }
if ($JobId -and (Test-Path -LiteralPath (Join-Path $outbox "$jobId.result.json"))) {
    throw "outbox already holds $jobId.result.json; refusing to reuse a job id whose result would be ambiguous"
}

# --- side-files first, verified on the share, then the job ------------------------------------
$SideFile = @($SideFile | ForEach-Object { $_ -split ';' } | Where-Object { $_ })
foreach ($path in $SideFile) {
    if (-not (Test-Path -LiteralPath $path -PathType Leaf)) { throw "Side-file not found: $path" }
    $name = [System.IO.Path]::GetFileName($path)
    if ($name -notmatch '^[A-Za-z0-9][A-Za-z0-9._-]{0,200}$' -or $name -match '\.job\.(ps1|tmp)$' -or $name -match '\.sidepart$') {
        throw "Side-file name is not a plain, non-job basename: $name"
    }
    $localSha = (Get-FileHash -LiteralPath $path -Algorithm SHA256).Hash
    $final = Join-Path $inbox $name
    if (Test-Path -LiteralPath $final) {
        $existing = Get-Item -LiteralPath $final -Force
        if ($existing.PSIsContainer -or (($existing.Attributes -band [IO.FileAttributes]::ReparsePoint) -ne 0)) {
            throw "inbox\$name exists and is not a plain file; refusing"
        }
        if ((Get-FileHash -LiteralPath $final -Algorithm SHA256).Hash -eq $localSha) {
            Write-Host "side-file already present with matching sha256: $name"
            continue
        }
        throw "inbox\$name already exists with DIFFERENT content; refusing to overwrite"
    }
    $part = Join-Path $inbox "$name.sidepart"
    Copy-Item -LiteralPath $path -Destination $part -Force
    $remoteSha = (Get-FileHash -LiteralPath $part -Algorithm SHA256).Hash
    if ($remoteSha -ne $localSha) {
        Remove-Item -LiteralPath $part -Force -ErrorAction SilentlyContinue
        throw "side-file $name did not round-trip to the share (local $localSha, share $remoteSha)"
    }
    Move-Item -LiteralPath $part -Destination $final
    Write-Host ("side-file placed: {0} sha256={1}" -f $name, $localSha.ToLowerInvariant())
}

$tmp   = Join-Path $inbox "$jobId.job.tmp"
$fin   = Join-Path $inbox "$jobId.job.ps1"
if (Test-Path -LiteralPath $fin) { throw "inbox already holds $jobId.job.ps1 (not yet picked up); refusing to replace it" }
Copy-Item $ScriptPath $tmp -Force
Move-Item $tmp $fin -Force   # atomic-ish: agent only picks up *.job.ps1
Write-Host "submitted $jobId -> $fin"

$resultFile = Join-Path $outbox "$jobId.result.json"
$deadline = (Get-Date).AddSeconds($TimeoutSec)
while ((Get-Date) -lt $deadline) {
    if (Test-Path $resultFile) {
        Start-Sleep -Milliseconds 400   # let the atomic rename settle
        $r = Get-Content $resultFile -Raw | ConvertFrom-Json
        return $r
    }
    Start-Sleep -Seconds $PollSeconds
}
throw "Timed out after ${TimeoutSec}s waiting for $resultFile"
