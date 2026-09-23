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
# workaround). The placement rules live in UmRunDrop.psm1 (tested with observing and faulty
# copiers): plain allowlisted names that normalise to themselves, per-submission unique temporary
# names, share-side hash verification before each rename, renames that never overwrite, and every
# side-file in place before the job is dropped. `pwsh -File` cannot pass arrays, so -SideFile also
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
    [string]$JobId = ''
)

$ErrorActionPreference = "Stop"
Import-Module (Join-Path $PSScriptRoot 'UmRunDrop.psm1') -Force
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

$dropLines = @(Invoke-UmRunDrop -Inbox $inbox -Outbox $outbox -ScriptPath $ScriptPath -JobId $JobId -SideFile $SideFile -JobTimeoutSec $TimeoutSec)
$jobId = $null
foreach ($line in $dropLines) {
    if ($line -like 'UMRUN_JOBID=*') { $jobId = $line.Substring('UMRUN_JOBID='.Length) } else { Write-Host $line }
}
if (-not $jobId) { throw "UmRunDrop returned no job id" }

$resultFile = Join-Path $outbox "$jobId.result.json"
# ATTR3-FOOTAGE-STAGE-SUBMIT-RETRY-1: -TimeoutSec is now the AGENT's budget too (written into the
# job metadata above), so the client must outlast it -- otherwise the two deadlines race and the
# caller throws its own generic timeout instead of reading the agent's receipt, which is the only
# artifact that says WHY the job ended (exitCode 124, "killed process tree", the pids). The grace is
# queue wait plus the agent's own result write; a caller that wants the agent to stop sooner lowers
# -TimeoutSec, which lowers both.
# Proportional, so a 1-second probe does not wait three minutes for a receipt that will never come,
# and an hour-long placement still gets a usable margin. Floor 5 s covers the queue-and-write gap.
$clientGraceSec = [math]::Min(180, [math]::Max(5, [int]($TimeoutSec * 0.1)))
$deadline = (Get-Date).AddSeconds($TimeoutSec + $clientGraceSec)
while ((Get-Date) -lt $deadline) {
    if (Test-Path $resultFile) {
        Start-Sleep -Milliseconds 400   # let the atomic rename settle
        $r = Get-Content $resultFile -Raw | ConvertFrom-Json
        return $r
    }
    Start-Sleep -Seconds $PollSeconds
}
throw "Timed out after $($TimeoutSec + $clientGraceSec)s waiting for $resultFile (agent budget ${TimeoutSec}s + ${clientGraceSec}s grace; no agent receipt appeared, so the job never ran or the agent is down)"
