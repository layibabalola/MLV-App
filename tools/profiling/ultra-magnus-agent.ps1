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
    [int]$JobTimeoutSec = 1800,
    [switch]$SelfTestIdentityGuard
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

function Get-ProcessIdentity {
    param([Parameter(Mandatory = $true)][int]$ProcessId)

    try {
        $process = Get-CimInstance Win32_Process -Filter "ProcessId=$ProcessId" -ErrorAction Stop
        if (-not $process) { throw "process not found" }
        $startUtc = if ($process.CreationDate) {
            ([DateTime]$process.CreationDate).ToUniversalTime().ToString("o")
        } else { $null }
        $startEpochMs = if ($startUtc) { [DateTimeOffset]::Parse($startUtc).ToUnixTimeMilliseconds() } else { $null }
        return [pscustomobject]@{
            exists = $true
            processId = [int]$process.ProcessId
            startUtc = $startUtc
            startEpochMs = $startEpochMs
            imagePath = $process.ExecutablePath
        }
    }
    catch {
        return [pscustomobject]@{
            exists = $false
            processId = $ProcessId
            startUtc = $null
            startEpochMs = $null
            imagePath = $null
        }
    }
}

function Get-StartedProcessIdentity {
    param([Parameter(Mandatory = $true)][System.Diagnostics.Process]$Process)

    try {
        $startUtc = $Process.StartTime.ToUniversalTime().ToString("o")
        return [pscustomobject]@{
            exists = $true
            processId = [int]$Process.Id
            startUtc = $startUtc
            startEpochMs = [DateTimeOffset]::Parse($startUtc).ToUnixTimeMilliseconds()
            imagePath = $null
        }
    }
    catch {
        return [pscustomobject]@{
            exists = $false
            processId = [int]$Process.Id
            startUtc = $null
            startEpochMs = $null
            imagePath = $null
        }
    }
}

function Test-EpochMillisClose {
    param($ActualEpochMs, $ExpectedEpochMs, [int]$ToleranceSeconds = 2)

    try {
        if ($null -eq $ActualEpochMs -or $null -eq $ExpectedEpochMs) { return $false }
        return ([Math]::Abs(([Int64]$ActualEpochMs) - ([Int64]$ExpectedEpochMs)) -le ($ToleranceSeconds * 1000))
    }
    catch { return $false }
}

function Test-ProcessIdentityMatch {
    param(
        [Parameter(Mandatory = $true)][int]$ProcessId,
        [string]$ExpectedStartUtc,
        $ExpectedStartEpochMs,
        [string]$ExpectedImagePath
    )

    $identity = Get-ProcessIdentity -ProcessId $ProcessId
    if (-not $identity.exists) {
        return [pscustomobject]@{ matches = $false; identity = $identity; reason = "not-running" }
    }
    $hasExpectedStart = ($null -ne $ExpectedStartEpochMs) -or (-not [string]::IsNullOrWhiteSpace($ExpectedStartUtc))
    $hasExpectedImage = -not [string]::IsNullOrWhiteSpace($ExpectedImagePath)
    if (-not $hasExpectedStart -and -not $hasExpectedImage) {
        return [pscustomobject]@{
            matches = $false
            identity = $identity
            reason = "missing-expected-identity-credentials"
            startMatches = $false
            imageMatches = $false
        }
    }

    $startMatches = $true
    if ($null -ne $ExpectedStartEpochMs) {
        $startMatches = Test-EpochMillisClose -ActualEpochMs $identity.startEpochMs -ExpectedEpochMs $ExpectedStartEpochMs
    }
    elseif (-not [string]::IsNullOrWhiteSpace($ExpectedStartUtc)) {
        $startMatches = ([string]$identity.startUtc -eq [string]$ExpectedStartUtc)
    }
    $imageMatches = if ($hasExpectedImage) {
        [string]::Equals($identity.imagePath, $ExpectedImagePath, [StringComparison]::OrdinalIgnoreCase)
    } else { $true }
    $reason = if ($startMatches -and $imageMatches) { "matched" } elseif (-not $startMatches) { "start-mismatch" } else { "image-mismatch" }
    return [pscustomobject]@{
        matches = ($startMatches -and $imageMatches)
        identity = $identity
        reason = $reason
        startMatches = $startMatches
        imageMatches = $imageMatches
    }
}

function Test-ProcessCreationAfterParent {
    param([Parameter(Mandatory = $true)]$ChildIdentity, [Parameter(Mandatory = $true)]$ParentIdentity)

    try {
        if ($null -eq $ChildIdentity.startEpochMs -or $null -eq $ParentIdentity.startEpochMs) { return $false }
        return ([Int64]$ChildIdentity.startEpochMs -ge [Int64]$ParentIdentity.startEpochMs)
    }
    catch { return $false }
}

function Get-DescendantProcessIdentities {
    param([Parameter(Mandatory = $true)]$ParentIdentity)

    $children = @(Get-CimInstance Win32_Process -Filter "ParentProcessId=$($ParentIdentity.processId)" -ErrorAction SilentlyContinue)
    foreach ($child in $children) {
        $childIdentity = Get-ProcessIdentity -ProcessId ([int]$child.ProcessId)
        if (-not $childIdentity.exists) { continue }
        if (-not (Test-ProcessCreationAfterParent -ChildIdentity $childIdentity -ParentIdentity $ParentIdentity)) {
            continue
        }
        Get-DescendantProcessIdentities -ParentIdentity $childIdentity
        $childIdentity
    }
}

function Stop-ProcessTree {
    param(
        [Parameter(Mandatory = $true)]$RootIdentity,
        [AllowEmptyCollection()][object[]]$KnownIdentities = @()
    )

    $rootMatch = Test-ProcessIdentityMatch -ProcessId ([int]$RootIdentity.processId) -ExpectedStartUtc $RootIdentity.startUtc -ExpectedStartEpochMs $RootIdentity.startEpochMs -ExpectedImagePath $RootIdentity.imagePath
    if (-not $rootMatch.matches) { return @() }

    $identities = [System.Collections.Generic.List[object]]::new()
    $seen = [System.Collections.Generic.HashSet[string]]::new()
    foreach ($expected in @($KnownIdentities) + @(Get-DescendantProcessIdentities -ParentIdentity $rootMatch.identity)) {
        if ($null -eq $expected -or -not $expected.exists) { continue }
        $key = "$($expected.processId):$($expected.startEpochMs):$($expected.startUtc)"
        if ($seen.Add($key)) { [void]$identities.Add($expected) }
    }
    [void]$identities.Add($rootMatch.identity)

    $killed = [System.Collections.Generic.List[int]]::new()
    foreach ($expected in $identities) {
        $match = Test-ProcessIdentityMatch -ProcessId ([int]$expected.processId) -ExpectedStartUtc $expected.startUtc -ExpectedStartEpochMs $expected.startEpochMs -ExpectedImagePath $expected.imagePath
        if (-not $match.matches) { continue }
        try {
            Stop-Process -Id ([int]$expected.processId) -Force -ErrorAction Stop
            [void]$killed.Add([int]$expected.processId)
        }
        catch {
            # The process may already have exited while the verified set was stopped.
        }
    }
    return @($killed)
}

if ($SelfTestIdentityGuard) {
    $probe = Test-ProcessIdentityMatch -ProcessId $PID
    $probe | ConvertTo-Json -Compress
    if ($probe.matches -or $probe.reason -ne "missing-expected-identity-credentials") { exit 1 }
    exit 0
}

function Quote-ProcessArgument {
    param([Parameter(Mandatory = $true)][string]$Value)
    return '"' + ($Value -replace '"', '\"') + '"'
}

function Write-AgentHeartbeat {
    param([string]$Activity = "")

    $line = "alive $((Get-Date).ToString('o')) pid=$PID host=$env:COMPUTERNAME"
    if (![string]::IsNullOrWhiteSpace($Activity)) {
        $line += " $Activity"
    }
    $line | Set-Content -Encoding ASCII $heartbeat
}

"agent start $((Get-Date).ToString('o')) pid=$PID host=$env:COMPUTERNAME root=$Root shell=$psExe" |
    Add-Content -Encoding ASCII (Join-Path $logs "agent.log")

while ($true) {
    # Liveness heartbeat (VM checks this file's age before submitting).
    Write-AgentHeartbeat

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
        $jobIdentity = $null
        $trackedDescendants = @{}
        try {
            $jobPathArgument = Quote-ProcessArgument -Value $job.FullName
            $jobProcess = Start-Process -FilePath $psExe `
                -ArgumentList @("-NoLogo", "-NoProfile", "-ExecutionPolicy", "Bypass", "-File", $jobPathArgument) `
                -RedirectStandardOutput $outFile `
                -RedirectStandardError $errFile `
                -WindowStyle Hidden `
                -PassThru

            $jobIdentity = Get-StartedProcessIdentity -Process $jobProcess
            if (-not $jobIdentity.exists) { throw "Failed to capture the launched job identity." }

            $deadline = (Get-Date).AddSeconds($JobTimeoutSec)
            $waitSliceMs = [Math]::Max(250, [Math]::Min(5000, $PollSeconds * 1000))
            while (!$jobProcess.HasExited -and (Get-Date) -lt $deadline) {
                foreach ($descendant in @(Get-DescendantProcessIdentities -ParentIdentity $jobIdentity)) {
                    $trackedDescendants["$($descendant.processId):$($descendant.startEpochMs)"] = $descendant
                }
                Write-AgentHeartbeat -Activity "job=$jobId"
                $remainingMs = [Math]::Max(1, [int][Math]::Min($waitSliceMs, ($deadline - (Get-Date)).TotalMilliseconds))
                [void]$jobProcess.WaitForExit($remainingMs)
            }

            if ($jobProcess.HasExited) {
                $exit = $jobProcess.ExitCode
            }
            else {
                $timedOut = $true
                $exit = 124
                $killedProcessIds = @(Stop-ProcessTree -RootIdentity $jobIdentity -KnownIdentities @($trackedDescendants.Values))
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
