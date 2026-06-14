# ultra-magnus-agent.ps1
# Runs ON Ultra-Magnus. A minimal file-drop job runner so the VM can execute
# work on the RTX 4090 host over SMB alone (no SSH/WinRM/admin/open ports).
#
# Protocol (all under <agent root>, which is this script's folder):
#   inbox\<jobId>.job.ps1   - VM drops a PowerShell script here (atomic rename)
#   outbox\<jobId>.result.json - agent writes {exitCode,stdout,stderr,timing}
#   processed\              - consumed job scripts are moved here
#   logs\                   - per-job stdout/stderr capture
#   heartbeat.txt           - rewritten every poll so the VM can prove liveness
#
# Security: this intentionally executes scripts dropped into inbox\. It is a
# private automation channel on the user's own LAN/host/account. Stop it by
# ending the scheduled task / process (see install-ultra-magnus-agent.ps1).

[CmdletBinding()]
param(
    [string]$Root = $PSScriptRoot,
    [int]$PollSeconds = 2,
    [int]$JobTimeoutSec = 1800
)

$ErrorActionPreference = "Continue"
$inbox     = Join-Path $Root "inbox"
$outbox    = Join-Path $Root "outbox"
$processed = Join-Path $Root "processed"
$logs      = Join-Path $Root "logs"
$heartbeat = Join-Path $Root "heartbeat.txt"
foreach ($d in @($inbox, $outbox, $processed, $logs)) {
    New-Item -ItemType Directory -Force -Path $d | Out-Null
}

# Prefer pwsh 7 for running jobs; fall back to Windows PowerShell if absent.
$psExe = (Get-Command pwsh.exe -ErrorAction SilentlyContinue).Source
if (-not $psExe -and (Test-Path "$env:ProgramFiles\PowerShell\7\pwsh.exe")) { $psExe = "$env:ProgramFiles\PowerShell\7\pwsh.exe" }
if (-not $psExe) { $psExe = "powershell.exe" }

"agent start $((Get-Date).ToString('o')) pid=$PID host=$env:COMPUTERNAME root=$Root shell=$psExe" |
    Add-Content -Encoding ASCII (Join-Path $logs "agent.log")

while ($true) {
    # Liveness heartbeat (VM checks this file's age before submitting).
    "alive $((Get-Date).ToString('o')) pid=$PID host=$env:COMPUTERNAME" |
        Set-Content -Encoding ASCII $heartbeat

    $jobs = Get-ChildItem $inbox -Filter *.job.ps1 -File -ErrorAction SilentlyContinue | Sort-Object Name
    foreach ($job in $jobs) {
        $jobId   = $job.BaseName -replace '\.job$', ''
        $outFile = Join-Path $logs "$jobId.out.txt"
        $errFile = Join-Path $logs "$jobId.err.txt"
        $started = (Get-Date).ToUniversalTime().ToString("o")
        $exit    = $null

        # Run the dropped script in a child PowerShell with a wall-clock timeout
        # so one hung job cannot freeze the agent. Output goes to files (no pipe
        # deadlock), exit code returned via the job.
        $bg = Start-Job -ScriptBlock {
            param($exe, $f, $o, $e)
            & $exe -NoProfile -ExecutionPolicy Bypass -File $f > $o 2> $e
            $LASTEXITCODE
        } -ArgumentList $psExe, $job.FullName, $outFile, $errFile

        if (Wait-Job $bg -Timeout $JobTimeoutSec) {
            $exit = Receive-Job $bg
            if ($null -eq $exit) { $exit = 0 }
        } else {
            Stop-Job $bg -ErrorAction SilentlyContinue
            $exit = 124
            "timed out after ${JobTimeoutSec}s" | Add-Content -Encoding ASCII $errFile
        }
        Remove-Job $bg -Force -ErrorAction SilentlyContinue

        $ended  = (Get-Date).ToUniversalTime().ToString("o")
        $stdout = if (Test-Path $outFile) { Get-Content $outFile -Raw } else { "" }
        $stderr = if (Test-Path $errFile) { Get-Content $errFile -Raw } else { "" }

        $result = [pscustomobject]@{
            jobId      = $jobId
            exitCode   = $exit
            startedUtc = $started
            endedUtc   = $ended
            host       = $env:COMPUTERNAME
            stdout     = $stdout
            stderr     = $stderr
        }
        # Atomic publish: write temp then rename so the VM never reads a partial file.
        $tmp = Join-Path $outbox "$jobId.result.tmp"
        $fin = Join-Path $outbox "$jobId.result.json"
        $result | ConvertTo-Json -Depth 6 | Set-Content -Encoding ASCII $tmp
        Move-Item -Force $tmp $fin
        Move-Item -Force $job.FullName (Join-Path $processed $job.Name)
    }

    Start-Sleep -Seconds $PollSeconds
}
