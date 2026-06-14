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

[CmdletBinding(DefaultParameterSetName = 'Script')]
param(
    [Parameter(Mandatory, ParameterSetName = 'Script')]
    [string]$ScriptPath,
    [Parameter(Mandatory, ParameterSetName = 'Command')]
    [string]$Command,
    [string]$AgentShare = "\\Ultra-Magnus\g\Temp\mlv-gpu-profile\agent",
    [int]$TimeoutSec = 1800,
    [int]$PollSeconds = 3,
    [int]$MaxHeartbeatAgeSec = 30
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

$jobId = "job_{0}_{1}" -f (Get-Date -Format "yyyyMMdd_HHmmss"), (Get-Random -Maximum 99999)
$tmp   = Join-Path $inbox "$jobId.job.tmp"
$fin   = Join-Path $inbox "$jobId.job.ps1"
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
