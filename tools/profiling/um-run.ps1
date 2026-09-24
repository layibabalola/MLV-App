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
    # ATTR3-FOOTAGE-STAGE-SUBMIT-RETRY-1 round 10: once past claimedAt + -TimeoutSec, this client
    # keeps waiting as long as heartbeat.txt proves the agent is still alive -- there is no longer
    # a fixed budget+grace cutoff (see the liveness wait below). This is the ABSOLUTE outer ceiling
    # on top of that: even a genuinely, provably still-alive agent is only trusted for this much
    # longer than the job's own requested budget before this client gives up regardless. Default
    # mirrors -MaxQueueWaitSec's own reasoning (a bound sized to outlast a lot of legitimate extra
    # work, not a guessed round number) -- generous enough that a job which legitimately consumes
    # its whole budget and then needs real recovery time (a slow kill, a slow SMB receipt write
    # under load) is never cut off while still proving progress, but still a genuine ceiling: a
    # stuck-but-heartbeating agent can hold this client for at most -TimeoutSec + this long, never
    # forever.
    [int]$MaxClaimedWaitSec = 86400,
    # ATTR3-FOOTAGE-STAGE-SUBMIT-RETRY-1 round 10: forwarded to Invoke-UmRunDrop. Claim-first means
    # this is now a promise about THIS caller's own worst-case time from claim to every side-file
    # and the job itself being in place (see UmRunDrop.psm1's own header) -- production callers
    # (attr3-footage-stage.ps1) pass no -SideFile and need no more than the unchanged default; a
    # caller that DOES pass a large -SideFile (the manual ATTR3-FIXTURE-REHEARSAL-1 operator
    # workflow) states its own real transfer time here instead of relying on an implicit assumption.
    [int]$OrphanMetaGraceSec = 60,
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

$dropLines = @(Invoke-UmRunDrop -Inbox $inbox -Outbox $outbox -ScriptPath $ScriptPath -JobId $JobId -SideFile $SideFile -JobTimeoutSec $TimeoutSec -OrphanMetaGraceSec $OrphanMetaGraceSec)
$jobId = $null
foreach ($line in $dropLines) {
    if ($line -like 'UMRUN_JOBID=*') { $jobId = $line.Substring('UMRUN_JOBID='.Length) } else { Write-Host $line }
}
if (-not $jobId) { throw "UmRunDrop returned no job id" }

$resultFile    = Join-Path $outbox "$jobId.result.json"
$startedMarker = Join-Path $running "$jobId.started.json"

# ATTR3-FOOTAGE-STAGE-SUBMIT-RETRY-1 round 2 (fable/sol major 3): the agent's own deadline starts
# when it CLAIMS the job, not when this client submitted it, and jobs on a shared agent are
# processed sequentially -- a job ahead in the queue can delay the claim by up to its own budget.
# Anchoring the client's deadline to submission time (as before) makes it race and lose against a
# merely-queued job, then throw a message that affirmatively misdiagnoses a healthy, still-running
# job as a dead agent. So there are two phases, and the client always knows which one it is in:
#   - QUEUED: waiting to see running\<jobId>.started.json, a marker a compatible agent writes the
#     instant it claims the job (read here rather than guessed at). Ceiling: $MaxQueueWaitSec.
#   - CLAIMED: once the marker appears, patience past the job's own -TimeoutSec no longer comes
#     from a constant -- round 10 replaces it with the liveness wait below.
$submittedAt    = Get-Date
$queueDeadline  = $submittedAt.AddSeconds($MaxQueueWaitSec)
$claimedAt      = $null
$budgetDeadline = $null

function Get-UmRunResultIfPresent {
    param([string]$Path)
    if (Test-Path -LiteralPath $Path) {
        Start-Sleep -Milliseconds 400   # let the atomic rename settle
        return Get-Content -LiteralPath $Path -Raw | ConvertFrom-Json
    }
    return $null
}

# ATTR3-FOOTAGE-STAGE-SUBMIT-RETRY-1 round 10: replaces the old fixed budget+grace cutoff. Once
# past claimedAt + -TimeoutSec, the client's patience comes from PROOF the agent is still working
# on this job -- heartbeat.txt fresh by the SHARE's own clock (Get-UmRunShareNowUtc, the same
# round-6 probe UmRunDrop.psm1 uses for its own orphan-age check -- reused here, not duplicated,
# so a submitter and this client never disagree about what "fresh" means) -- rather than a
# constant nobody could derive a real bound for. The agent tags heartbeat.txt with " job=<id>"
# (normal execution) or " adopt job=<id>" (post-restart adoption) every wait-slice while a job is
# genuinely running -- ultra-magnus-agent.ps1's own wait-slice is hard-capped at 5s regardless of
# its own -PollSeconds -- so a tag naming a DIFFERENT job proves the agent has moved off this one
# without ever producing a receipt, which is liveness-lost for OUR purposes even if the agent
# itself is fine. A heartbeat line with no job= tag at all (a plain between-jobs heartbeat, or one
# this client happened to read mid-write) is not treated as a mismatch -- only an EXPLICIT
# different job id is.
function Get-UmRunAgentLiveness {
    param(
        [Parameter(Mandatory = $true)][string]$HeartbeatPath,
        [Parameter(Mandatory = $true)][string]$Inbox,
        [Parameter(Mandatory = $true)][string]$JobId,
        [Parameter(Mandatory = $true)][int]$MaxHeartbeatAgeSec
    )
    if (-not (Test-Path -LiteralPath $HeartbeatPath)) {
        return [pscustomobject]@{ Fresh = $false; AgeSec = $null; HeartbeatUtc = $null; JobMismatch = $false; OtherJobId = $null }
    }
    $shareNowUtc = Get-UmRunShareNowUtc -Directory $Inbox
    $heartbeatUtc = (Get-Item -LiteralPath $HeartbeatPath -Force).LastWriteTimeUtc
    $ageSec = ($shareNowUtc - $heartbeatUtc).TotalSeconds
    $jobMismatch = $false
    $otherJobId = $null
    $line = Get-Content -LiteralPath $HeartbeatPath -Raw -ErrorAction SilentlyContinue
    if ($line -and $line -match '(?:^|\s)job=(\S+)\s*$') {
        if ($Matches[1] -ne $JobId) { $jobMismatch = $true; $otherJobId = $Matches[1] }
    }
    return [pscustomobject]@{
        Fresh        = (-not $jobMismatch) -and ($ageSec -le $MaxHeartbeatAgeSec)
        AgeSec       = $ageSec
        HeartbeatUtc = $heartbeatUtc
        JobMismatch  = $jobMismatch
        OtherJobId   = $otherJobId
    }
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
# and no cross-machine clock comparison is possible because only one clock is ever read. Round 10:
# $budgetDeadline (claimedAt + -TimeoutSec) is no longer a hard stop -- it is the instant patience
# stops being unconditional and starts depending on liveness (below).
while ($true) {
    $r = Get-UmRunResultIfPresent -Path $resultFile
    if ($null -ne $r) { return $r }

    if ($null -eq $claimedAt -and (Test-Path -LiteralPath $startedMarker)) {
        $claimedAt      = Get-Date
        $budgetDeadline = $claimedAt.AddSeconds($TimeoutSec)
    }

    if ($null -ne $claimedAt) {
        # CLAIMED phase. THE THREE OUTCOMES from here: (1) a receipt appears -- returned above on
        # the next iteration; (2) liveness is lost -- thrown below, naming since when; (3) the
        # absolute outer ceiling is reached despite proven liveness -- thrown below, naming it.
        if ((Get-Date) -ge $budgetDeadline) {
            $liveness = Get-UmRunAgentLiveness -HeartbeatPath $hb -Inbox $inbox -JobId $jobId -MaxHeartbeatAgeSec $MaxHeartbeatAgeSec
            if (-not $liveness.Fresh) {
                $r = Get-UmRunResultIfPresent -Path $resultFile
                if ($null -ne $r) { return $r }
                if ($liveness.JobMismatch) {
                    throw "$jobId was claimed by the agent, but heartbeat.txt now names a DIFFERENT job ($($liveness.OtherJobId)) -- the agent has moved on without ever publishing $resultFile for $jobId; this client is giving up, but the agent may still be alive and $resultFile may still land later if this is a stale read"
                }
                if ($null -eq $liveness.HeartbeatUtc) {
                    throw "$jobId was claimed by the agent, but $hb no longer exists -- this client is giving up, but the agent still owns $jobId and its receipt may still land at $resultFile after this"
                }
                throw "$jobId was claimed by the agent, but it stopped proving liveness: $hb was last written at $($liveness.HeartbeatUtc.ToString('o')) (the share's own clock), now $([int]$liveness.AgeSec)s old -- past the -MaxHeartbeatAgeSec $MaxHeartbeatAgeSec s freshness threshold; this client is giving up, but the agent still owns $jobId and its receipt may still land at $resultFile after this"
            }
            $outerCeiling = $claimedAt.AddSeconds($TimeoutSec + $MaxClaimedWaitSec)
            if ((Get-Date) -ge $outerCeiling) {
                $r = Get-UmRunResultIfPresent -Path $resultFile
                if ($null -ne $r) { return $r }
                throw "$jobId was claimed by the agent and has kept proving liveness past its own budget (${TimeoutSec}s) for the full -MaxClaimedWaitSec (${MaxClaimedWaitSec}s) -- this client is giving up at its absolute outer ceiling regardless of liveness, but the agent still owns $jobId and its receipt may still land at $resultFile after this"
            }
        }
    } else {
        # QUEUED phase (unchanged ceiling/recheck logic).
        if ((Get-Date) -ge $queueDeadline) {
            # round 7 (sol blocker): the comment above has always promised a receipt-OR-claim
            # recheck, but a plain break was the only path out of the loop and it never rechecked
            # the claim marker -- so a claim landing after the Test-Path above found nothing, but
            # before this break/throw completes, was still reported as "never claimed" for a job
            # the agent had, in fact, just started. One more look at the marker right here: if it
            # has now appeared, switch onto the CLAIMED phase (one more loop iteration) instead of
            # ending the loop.
            if ($TestHookAtQueueDeadline) { & $TestHookAtQueueDeadline }
            if (Test-Path -LiteralPath $startedMarker) {
                $claimedAt      = Get-Date
                $budgetDeadline = $claimedAt.AddSeconds($TimeoutSec)
                continue
            }
            break
        }
    }
    Start-Sleep -Seconds $PollSeconds
}

# fable/sol major 3 (second half): re-check the receipt once more before throwing -- one that lands
# in the instant between the deadline check above and this line must still be read, not missed. Only
# the QUEUED (never-claimed) outcome can still reach here -- every CLAIMED-phase outcome throws (or
# returns) from inside the loop above, since round 10 needs to name WHICH of the three outcomes fired
# from the exact context that observed it.
$r = Get-UmRunResultIfPresent -Path $resultFile
if ($null -ne $r) { return $r }
$queuedElapsedSec = [int]((Get-Date) - $submittedAt).TotalSeconds
throw "Timed out after ${queuedElapsedSec}s: $jobId was never claimed (no $startedMarker marker appeared) and $resultFile never appeared -- it may still be queued behind other work on the agent, or the agent may be unreachable"
