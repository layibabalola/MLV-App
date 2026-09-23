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
    # ATTR3-FOOTAGE-STAGE-SUBMIT-RETRY-1 round 2 (sol major 3): how long the client waits to see the
    # job CLAIMED (running\<id>.started.json, written by a compatible agent) before giving up on it
    # ever running at all. Jobs are processed sequentially, so a job ahead in the queue can delay a
    # claim by up to its own budget -- as much as 86400s, the same max any caller can request -- so
    # that is the default. Once claimed, patience is governed by -TimeoutSec + the grace below
    # instead, measured from the claim, not from submission.
    [int]$MaxQueueWaitSec = 86400,
    [string[]]$SideFile = @(),
    # Keep a generator's own job id (so outbox\<JobId>.result.json and its artifacts line up).
    [string]$JobId = '',
    # Test-only: invoked with no arguments the instant the queue deadline is judged reached and no
    # claim has been seen yet, immediately BEFORE the recheck that follows it -- lets a test land a
    # claim marker write deterministically inside what is otherwise a sub-millisecond window between
    # this script's last (negative) marker check and its final diagnosis (round 7, sol blocker).
    # Production never passes this.
    [scriptblock]$TestHookAtQueueDeadline = $null
)

$ErrorActionPreference = "Stop"
# ATTR3-FOOTAGE-STAGE-SUBMIT-RETRY-1 round 2 (sol major 3): 0 is a sentinel INSIDE UmRunDrop.psm1
# ("write no metadata"; an internal contract used directly by tests), but forwarding it here from a
# public caller collided with the grace formula below -- a caller asking for "no budget" got a
# 5-SECOND client patience while a compatible agent quietly fell back to ITS OWN default (which can
# be 1800s or more). The public contract is therefore explicit: 0 has no defined per-job budget and
# is refused here, before it ever reaches the module.
if ($TimeoutSec -le 0) {
    throw "UMRUN_TIMEOUT_SEC_INVALID -TimeoutSec must be a positive integer (1..86400); 0 has no defined client patience"
}
Import-Module (Join-Path $PSScriptRoot 'UmRunDrop.psm1') -Force
$inbox   = Join-Path $AgentShare "inbox"
$outbox  = Join-Path $AgentShare "outbox"
$running = Join-Path $AgentShare "running"
$hb      = Join-Path $AgentShare "heartbeat.txt"

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

$resultFile    = Join-Path $outbox "$jobId.result.json"
$startedMarker = Join-Path $running "$jobId.started.json"
# ATTR3-FOOTAGE-STAGE-SUBMIT-RETRY-1: -TimeoutSec is now the AGENT's budget too (written into the
# job metadata above), so the client must outlast it -- otherwise the two deadlines race and the
# caller throws its own generic timeout instead of reading the agent's receipt, which is the only
# artifact that says WHY the job ended (exitCode 124, "killed process tree", the pids). The grace is
# queue wait plus the agent's own result write; a caller that wants the agent to stop sooner lowers
# -TimeoutSec, which lowers both.
# Proportional, so a 1-second probe does not wait three minutes for a receipt that will never come,
# and an hour-long placement still gets a usable margin.
#
# ATTR3-FOOTAGE-STAGE-SUBMIT-RETRY-1 round 8 (sol BLOCKER, fable minor): the old 5 s floor was a
# round number, not a bound on what it has to cover. NONE of the following, all in
# ultra-magnus-agent.ps1, are inside the agent's own -TimeoutSec countdown -- $deadline is set only
# AFTER setup, and everything after $deadline fires still has to complete before the receipt can
# land:
#   - setup (claim marker write, metadata read, Start-Process, one Get-ProcessIdentity CIM query
#     before the countdown even starts): CIM/WMI queries and a fresh pwsh.exe launch can each take
#     low seconds under load -- bounded here at 5 s;
#   - poll-slice detection lag: the agent's own wait loop is capped at
#     [Math]::Min(5000, ...) regardless of -PollSeconds, so this is a HARD 5 s ceiling, not a guess;
#   - process-tree termination (Stop-ProcessTree: a CIM enumeration pass plus one
#     Test-ProcessIdentityMatch CIM call and one Stop-Process per process): bounded at 5 s for the
#     small process trees these jobs actually spawn;
#   - receipt publication (JSON-encode the result, Set-Content to the outbox share, then an atomic
#     rename): bounded at 5 s to allow for a slow SMB write.
# Sum: 20 s. That is the floor -- derived from what it has to outlast, not rounded from nothing.
# At the large budgets this board actually uses for staging (attr3-footage-stage.ps1 requests
# 30..86400 s; a real placement is typically hundreds to thousands of seconds), 10% of -TimeoutSec
# passes 20 s once -TimeoutSec exceeds 200 s and reaches the 180 s cap once -TimeoutSec reaches
# 1800 s -- so the 20 s floor matters only for small/probe-sized budgets, exactly where the fixed
# per-job overhead above is proportionally largest; every staging-scale job rides the proportional
# term or the 180 s cap, both of which already dwarf the same bounded overhead by a wide margin.
$clientGraceSec = [math]::Min(180, [math]::Max(20, [int]($TimeoutSec * 0.1)))

# ATTR3-FOOTAGE-STAGE-SUBMIT-RETRY-1 round 2 (fable/sol major 3): the agent's own deadline starts
# when it CLAIMS the job, not when this client submitted it, and jobs on a shared agent are
# processed sequentially -- a job ahead in the queue can delay the claim by up to its own budget.
# Anchoring the client's deadline to submission time (as before) makes it race and lose against a
# merely-queued job, then throw a message that affirmatively misdiagnoses a healthy, still-running
# job as a dead agent. So there are two phases, and the client always knows which one it is in:
#   - QUEUED: waiting to see running\<jobId>.started.json, a marker a compatible agent writes the
#     instant it claims the job (read here rather than guessed at). Ceiling: $MaxQueueWaitSec.
#   - CLAIMED: once the marker appears, patience becomes claim time + -TimeoutSec + the grace above,
#     exactly as if the client had started counting at the claim.
# A queue ceiling of exactly $TimeoutSec+grace here would BE the pre-fix behaviour (misdiagnosing a
# queued job the moment ITS OWN naive deadline passes); the point of $MaxQueueWaitSec is to be able
# to outlast an unrelated job ahead of it.
$submittedAt   = Get-Date
$queueDeadline = $submittedAt.AddSeconds($MaxQueueWaitSec)
$claimedAt     = $null
$execDeadline  = $null

function Get-UmRunResultIfPresent {
    param([string]$Path)
    if (Test-Path -LiteralPath $Path) {
        Start-Sleep -Milliseconds 400   # let the atomic rename settle
        return Get-Content -LiteralPath $Path -Raw | ConvertFrom-Json
    }
    return $null
}

# ATTR3-FOOTAGE-STAGE-SUBMIT-RETRY-1 round 7 (sol/fable blocker): claimedAt used to come from the
# marker's own startedUtc field -- a timestamp stamped by the AGENT HOST's clock -- while every
# deadline built from it is compared against THIS CLIENT's Get-Date a few lines down. Those are two
# different clocks with no guarantee they agree; sufficient skew (or slow client-side setup after
# the marker is published) computes an already-expired deadline and throws before the agent's own
# timeout receipt can land. Round 6 hit the same class in the orphan self-heal and fixed it there by
# probing a THIRD, shared clock domain (the file server's) -- right for that call, because both
# timestamps being compared there are remotely authored. Here the client is setting a deadline for
# its OWN wait, so the simpler and stricter fix is to never leave the client's own clock domain at
# all: $claimedAt is the client's own Get-Date at the instant it first observes the marker, never
# the agent's stamp of when it wrote it. That can only differ from the agent's real claim instant by
# at most one -PollSeconds of propagation -- i.e. it errs toward MORE client patience, never less,
# and no cross-machine clock comparison is possible because only one clock is ever read.
while ($true) {
    $r = Get-UmRunResultIfPresent -Path $resultFile
    if ($null -ne $r) { return $r }

    if ($null -eq $claimedAt -and (Test-Path -LiteralPath $startedMarker)) {
        $claimedAt    = Get-Date
        $execDeadline = $claimedAt.AddSeconds($TimeoutSec + $clientGraceSec)
    }

    $activeDeadline = if ($null -ne $execDeadline) { $execDeadline } else { $queueDeadline }
    if ((Get-Date) -ge $activeDeadline) {
        # round 7 (sol blocker): the comment below has always promised a receipt-OR-claim recheck,
        # but this break was the only path out of the loop and it never rechecked the claim marker --
        # so a claim landing after the Test-Path above found nothing, but before this break/throw
        # completes, was still reported as "never claimed" for a job the agent had, in fact, just
        # started. One more look at the marker right here: if it has now appeared, switch onto the
        # job's own (finite, just-computed) execution deadline instead of ending the loop.
        if ($null -eq $claimedAt) {
            if ($TestHookAtQueueDeadline) { & $TestHookAtQueueDeadline }
            if (Test-Path -LiteralPath $startedMarker) {
                $claimedAt    = Get-Date
                $execDeadline = $claimedAt.AddSeconds($TimeoutSec + $clientGraceSec)
                continue
            }
        }
        break
    }
    Start-Sleep -Seconds $PollSeconds
}

# fable/sol major 3 (second half): re-check the receipt once more before throwing -- one that lands
# in the instant between the deadline check above and this line must still be read, not missed. (The
# claim marker's equivalent recheck now happens at the break itself, above, since a claim discovered
# there must extend the wait rather than merely relabel a diagnosis moments before it fires anyway.)
$r = Get-UmRunResultIfPresent -Path $resultFile
if ($null -ne $r) { return $r }

if ($null -ne $claimedAt) {
    $execElapsedSec = [int]((Get-Date) - $claimedAt).TotalSeconds
    throw "Timed out ${execElapsedSec}s after $jobId was claimed by the agent (budget ${TimeoutSec}s + ${clientGraceSec}s grace); $resultFile still has not appeared -- the agent claimed the job but has not finished or published a result"
} else {
    $queuedElapsedSec = [int]((Get-Date) - $submittedAt).TotalSeconds
    throw "Timed out after ${queuedElapsedSec}s: $jobId was never claimed (no $startedMarker marker appeared) and $resultFile never appeared -- it may still be queued behind other work on the agent, or the agent may be unreachable"
}
