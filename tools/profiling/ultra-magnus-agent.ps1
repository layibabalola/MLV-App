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
    [ValidateRange(1, 86400)]
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

function Get-DescendantProcessIds {
    param([Parameter(Mandatory = $true)][int]$ParentProcessId)

    $children = @(Get-CimInstance Win32_Process -Filter "ParentProcessId=$ParentProcessId" -ErrorAction SilentlyContinue)
    foreach ($child in $children) {
        $childProcessId = [int]$child.ProcessId
        Get-DescendantProcessIds -ParentProcessId $childProcessId
        $childProcessId
    }
}

function Stop-ProcessTree {
    param([Parameter(Mandatory = $true)][int]$RootProcessId)

    $killed = [System.Collections.Generic.List[int]]::new()
    $processIds = @(
        @(Get-DescendantProcessIds -ParentProcessId $RootProcessId)
        $RootProcessId
    ) | Select-Object -Unique

    foreach ($processIdToKill in $processIds) {
        try {
            $process = Get-Process -Id $processIdToKill -ErrorAction Stop
            Stop-Process -Id $processIdToKill -Force -ErrorAction Stop
            [void]$killed.Add($processIdToKill)
        }
        catch {
            # The child may already have exited while the tree was being walked.
        }
    }
    return @($killed)
}

function Quote-ProcessArgument {
    param([Parameter(Mandatory = $true)][string]$Value)
    return '"' + ($Value -replace '"', '\"') + '"'
}

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
        $timedOut = $false
        $killedProcessIds = @()
        Remove-Item -LiteralPath $outFile, $errFile -Force -ErrorAction SilentlyContinue

        # Run the dropped script in a child PowerShell with a wall-clock timeout
        # so one hung job cannot freeze the agent. Output goes to files (no pipe
        # deadlock). On timeout, kill the whole job process tree, not just the
        # PowerShell wrapper, so launched app/build children do not survive.
        $jobProcess = $null
        try {
            $jobPathArgument = Quote-ProcessArgument -Value $job.FullName
            $jobProcess = Start-Process -FilePath $psExe `
                -ArgumentList @("-NoLogo", "-NoProfile", "-ExecutionPolicy", "Bypass", "-File", $jobPathArgument) `
                -RedirectStandardOutput $outFile `
                -RedirectStandardError $errFile `
                -WindowStyle Hidden `
                -PassThru

            if ($jobProcess.WaitForExit($JobTimeoutSec * 1000)) {
                $exit = $jobProcess.ExitCode
            }
            else {
                $timedOut = $true
                $exit = 124
                $killedProcessIds = @(Stop-ProcessTree -RootProcessId $jobProcess.Id)
                "timed out after ${JobTimeoutSec}s; killed process tree pids=$($killedProcessIds -join ',')" |
                    Add-Content -Encoding ASCII $errFile
            }
        }
        catch {
            $exit = 1
            "agent failed to launch or monitor job: $($_.Exception.Message)" |
                Add-Content -Encoding ASCII $errFile
        }
        finally {
            if ($null -ne $jobProcess) {
                $jobProcess.Dispose()
            }
        }

        $ended  = (Get-Date).ToUniversalTime().ToString("o")
        $stdout = if (Test-Path $outFile) { Get-Content $outFile -Raw } else { "" }
        $stderr = if (Test-Path $errFile) { Get-Content $errFile -Raw } else { "" }

        $result = [pscustomobject]@{
            jobId      = $jobId
            exitCode   = $exit
            startedUtc = $started
            endedUtc   = $ended
            host       = $env:COMPUTERNAME
            timeoutSec = $JobTimeoutSec
            timedOut   = $timedOut
            killedProcessIds = @($killedProcessIds)
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
