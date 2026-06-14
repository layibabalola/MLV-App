# install-ultra-magnus-agent.ps1
# ONE-TIME bootstrap. RUN THIS ONCE ON ULTRA-MAGNUS (console session).
# It starts the file-drop agent now (hidden, detached) and registers it to
# auto-start on every logon, so the VM can then drive the host hands-off and
# the agent survives reboots. No admin / no open ports required (SMB only).
#
# To stop/remove later:
#   schtasks /End /TN MlvGpuProfileAgent ; schtasks /Delete /TN MlvGpuProfileAgent /F
#   Get-CimInstance Win32_Process -Filter "Name='powershell.exe'" |
#     Where CommandLine -like '*ultra-magnus-agent*' | ForEach-Object { Stop-Process $_.ProcessId }

[CmdletBinding()]
param(
    [string]$AgentScript = (Join-Path $PSScriptRoot "ultra-magnus-agent.ps1"),
    [string]$TaskName    = "MlvGpuProfileAgent"
)

$ErrorActionPreference = "Stop"
if (-not (Test-Path $AgentScript)) { throw "Agent script not found: $AgentScript" }
$AgentScript = (Resolve-Path $AgentScript).Path

# Pre-create the mailbox dirs so the VM can submit immediately.
$root = Split-Path $AgentScript
foreach ($d in @("inbox", "outbox", "processed", "logs")) {
    New-Item -ItemType Directory -Force -Path (Join-Path $root $d) | Out-Null
}

# Prefer pwsh 7 for the agent; fall back to Windows PowerShell if absent.
$psExe = (Get-Command pwsh.exe -ErrorAction SilentlyContinue).Source
if (-not $psExe -and (Test-Path "$env:ProgramFiles\PowerShell\7\pwsh.exe")) { $psExe = "$env:ProgramFiles\PowerShell\7\pwsh.exe" }
if (-not $psExe) { $psExe = "powershell.exe" }
Write-Host "Agent shell: $psExe"

# 1) Start it now (detached + hidden) so it keeps running after this console closes.
Start-Process $psExe `
    -ArgumentList @("-NoProfile","-ExecutionPolicy","Bypass","-WindowStyle","Hidden","-File",$AgentScript) `
    -WindowStyle Hidden
Write-Host "Agent started now (hidden, detached)."

# 2) Register an ONLOGON task (current user, no elevation) so it auto-restarts.
# Quote the shell path (pwsh lives under Program Files, which contains a space).
$tr = '"{0}" -NoProfile -ExecutionPolicy Bypass -WindowStyle Hidden -File "{1}"' -f $psExe, $AgentScript
& schtasks.exe /Create /TN $TaskName /TR $tr /SC ONLOGON /RL LIMITED /F | Out-Null
if ($LASTEXITCODE -eq 0) {
    Write-Host "Registered logon auto-start task: $TaskName"
} else {
    Write-Warning "schtasks registration returned $LASTEXITCODE (agent is still running for this session)."
}

# 3) Confirm liveness.
$hb = Join-Path $root "heartbeat.txt"
Write-Host -NoNewline "Waiting for first heartbeat"
for ($i = 0; $i -lt 10; $i++) {
    if (Test-Path $hb) { Write-Host " ... OK"; Get-Content $hb; break }
    Write-Host -NoNewline "."
    Start-Sleep -Seconds 1
}
if (-not (Test-Path $hb)) { Write-Warning "No heartbeat yet - check $root\logs\agent.log" }

Write-Host ""
Write-Host "Agent is live. The VM can now submit jobs over SMB; you are out of the loop."
Write-Host "Mailbox root: $root"
