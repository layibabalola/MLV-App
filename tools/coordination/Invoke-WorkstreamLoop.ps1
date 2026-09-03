<#
.SYNOPSIS
    One unattended cycle of the workstream driver: rotate across tracks, dispatch a
    bounded number of lanes, write a cycle receipt. Designed to be driven by a
    Windows Scheduled Task so the board advances without a chat session awake.

.DESCRIPTION
    WHY THIS IS A SCRIPT AND NOT A CHAT LOOP.

    A lane is already a bounded CLI process (Invoke-Lane.ps1), so "the orchestrator
    drives lanes over the CLI" has been true since 2026-08-29. What was NOT true is
    that the ORCHESTRATOR survived anything. When an interactive session drives the
    lanes, the board advances only while that session has context and quota - and on
    2026-08-31 both ran out inside one hour: one lane died `prompt_too_long` after
    burning $51.62, the next died on a 429 session cap.

    A scheduled task holds no session, no lease, no seat and no MCP handle. It
    belongs to the OS user, which is why MLV-BoardStateHeartbeat survives an account
    rotation and every seat-era producer did not. This script is the same shape.

    SPENDING IS BOUNDED THREE WAYS, because an unattended loop that dispatches
    models is an unattended loop that spends money:
      1. -MaxDispatchesPerCycle  (default 2)
      2. -DailyBudget            (default 12) counted from the dispatch log's own
                                 records for the current UTC day - derived, not tracked
                                 in a counter that can drift from reality
      3. A KILL SWITCH FILE. If it exists, this script exits 0 having dispatched
         nothing. Stopping the fleet must not require finding a process.

    IT NEVER MUTATES queue.json. RESUME.md STEP 4/5 bars that under the recovery
    authority, and an unattended actor is exactly the wrong thing to relax it for.

.NOTES
    ASCII-only by project convention. Install with -Install; verify BY ITS RECEIPTS
    under .claude-state\fleet-runs\loop-cycles\, never by the task's config existing.
#>
[CmdletBinding()]
param(
    [int]$MaxDispatchesPerCycle = 2,
    [int]$DailyBudget = 12,
    [int]$TimeoutSec = 1500,
    [int]$StaleHours = 12,

    # Tracks to rotate through, in preference order.
    [string[]]$Tracks = @('playback','factory','product','UNSET'),

    [switch]$DryRun,
    [switch]$Install,
    [switch]$Status
)

$ErrorActionPreference = 'Stop'
Set-StrictMode -Version Latest

$RepoRoot   = 'C:\!Layi Wkspc\MLV-App'
$DualLane   = Join-Path $RepoRoot '.claude-state\coordination\dual-lane'
$LogPath    = Join-Path $DualLane 'workstream-dispatch-log.jsonl'
$KillSwitch = Join-Path $DualLane 'WORKSTREAM-LOOP-DISABLED'
$CycleDir   = Join-Path $RepoRoot '.claude-state\fleet-runs\loop-cycles'
$TaskName   = 'MLV-WorkstreamLoop'

# THE DRIVER WORKTREE, AND WHY IT EXISTS.
#
# The dispatcher is a TRACKED file under tools\. The canonical checkout is
# routinely parked on a peer branch - right now `diag/async-h2d-preupload-exits`,
# another actor's do-not-merge diagnostic branch - and a tracked script simply does
# not exist on a ref that predates it. This repo has already been bitten by exactly
# that: on 2026-08-30 all three hooks in .claude\settings.json pointed at scripts
# living only on peer branches, and every one began erroring the moment the checkout
# moved. The rule that came out of it is "a hook's script must live on the SAME REF
# as the tree it guards."
#
# An unattended loop cannot rely on where a human left the checkout. So it drives
# from its OWN worktree, pinned to the target branch and refreshed each cycle. This
# is a LOAD-BEARING worktree, not sweep debris: it is recreated on demand and its
# absence is a CANNOT-DETERMINE, never a silent no-op.
$DriverRoot   = 'C:\mlvtmp\ws-driver'
$TargetRef    = 'fork/master'
$Dispatcher   = Join-Path $DriverRoot 'tools\coordination\Invoke-Workstream.ps1'

function Sync-DriverWorktree {
    # Returns $null on success, or a CANNOT-DETERMINE reason string.
    try {
        if (-not (Test-Path -LiteralPath (Join-Path $DriverRoot '.git'))) {
            if (Test-Path -LiteralPath $DriverRoot) { Remove-Item -LiteralPath $DriverRoot -Recurse -Force }
            & git -C $RepoRoot fetch fork --quiet 2>&1 | Out-Null
            & git -C $RepoRoot -c core.longpaths=true worktree add --detach $DriverRoot $TargetRef 2>&1 | Out-Null
            if ($LASTEXITCODE -ne 0) { return "could not create driver worktree at $DriverRoot" }
            return $null
        }
        & git -C $RepoRoot fetch fork --quiet 2>&1 | Out-Null
        if ($LASTEXITCODE -ne 0) { return "git fetch fork failed (exit $LASTEXITCODE); driver worktree may be stale" }
        # Hard reset is safe here and ONLY here: this worktree is tool-owned, is
        # never edited by hand, and holds nothing anyone can lose.
        & git -C $DriverRoot reset --hard $TargetRef --quiet 2>&1 | Out-Null
        if ($LASTEXITCODE -ne 0) { return "could not reset driver worktree to $TargetRef" }
        return $null
    } catch {
        return "driver worktree sync threw: $($_.Exception.Message)"
    }
}

if (-not (Test-Path -LiteralPath $CycleDir)) { New-Item -ItemType Directory -Path $CycleDir -Force | Out-Null }

# ------------------------------------------------------------------ -Status
if ($Status) {
    $task = Get-ScheduledTask -TaskName $TaskName -ErrorAction SilentlyContinue
    if (-not $task) {
        Write-Output "LOOP: task '$TaskName' NOT REGISTERED (use -Install)"
    } else {
        $info = Get-ScheduledTaskInfo -TaskName $TaskName
        Write-Output "LOOP: task state=$($task.State) lastRun=$($info.LastRunTime) lastResult=$($info.LastTaskResult) nextRun=$($info.NextRunTime)"
    }
    # RECEIPTS ARE THE PROOF, NOT THE CONFIG. A registered task that has never
    # produced a cycle receipt has never run, and looks identical to a healthy one.
    $cycles = @(Get-ChildItem -LiteralPath $CycleDir -Filter '*.json' -ErrorAction SilentlyContinue |
                Sort-Object LastWriteTimeUtc -Descending)
    Write-Output "LOOP: cycle receipts = $($cycles.Count)"
    if ($cycles.Count -gt 0) {
        $newest = $cycles[0]
        $ageMin = [math]::Round(((Get-Date).ToUniversalTime() - $newest.LastWriteTimeUtc).TotalMinutes, 1)
        Write-Output "LOOP: newest receipt $($newest.Name) age ${ageMin} m"
    } else {
        Write-Output 'LOOP: NO CYCLE RECEIPTS - this loop has never run. A registered task that never fired looks exactly like a healthy one.'
    }
    if (Test-Path -LiteralPath $KillSwitch) { Write-Output "LOOP: KILL SWITCH PRESENT at $KillSwitch - cycles will dispatch NOTHING" }
    exit 0
}

# ------------------------------------------------------------------ -Install
if ($Install) {
    # BUDGET FLAGS MUST SURVIVE A REINSTALL. Until 2026-09-03 this line was hardcoded with
    # no budget arguments, so the task ALWAYS ran the defaults no matter what -Install was
    # given. When the measured spend (USD 22-25 per fable lane) forced an emergency cap, it
    # had to be applied by editing the live task action - a change that any later -Install
    # would silently have thrown away. Pass them through, so the registered task states its
    # own bound and the bound is auditable from the task itself.
    $argLine = ('-NoProfile -NonInteractive -ExecutionPolicy Bypass -File "{0}" ' +
                '-DailyBudget {1} -MaxDispatchesPerCycle {2} -TimeoutSec {3} -StaleHours {4}') -f `
               $PSCommandPath, $DailyBudget, $MaxDispatchesPerCycle, $TimeoutSec, $StaleHours
    $action  = New-ScheduledTaskAction -Execute 'pwsh.exe' `
        -Argument $argLine `
        -WorkingDirectory $RepoRoot
    # No -RepetitionDuration: [TimeSpan]::MaxValue serialises to a value the task XML
    # validator REJECTS, and omitting the duration is how you say "indefinitely".
    # (Fleet TRAPS.md, appended by MLV-App 2026-08-31.)
    $trigger = New-ScheduledTaskTrigger -Once -At (Get-Date).AddMinutes(2) `
        -RepetitionInterval (New-TimeSpan -Minutes 45)
    $settings = New-ScheduledTaskSettingsSet -AllowStartIfOnBatteries `
        -DontStopIfGoingOnBatteries -StartWhenAvailable `
        -MultipleInstances IgnoreNew -ExecutionTimeLimit (New-TimeSpan -Hours 2)
    Register-ScheduledTask -TaskName $TaskName -Action $action -Trigger $trigger `
        -Settings $settings -Description 'MLV-App: advance workstreams by dispatching bounded lanes. Budget-capped; kill switch at .claude-state\coordination\dual-lane\WORKSTREAM-LOOP-DISABLED' `
        -Force | Out-Null
    Write-Output "LOOP: registered '$TaskName' every 45 min with dailyBudget=$DailyBudget perCycle=$MaxDispatchesPerCycle."
    Write-Output "LOOP: PROVE IT BY ITS RECEIPTS, not by this message - run with -Status after the first fire."
    exit 0
}

# ------------------------------------------------------------------ cycle
$cycleStart = (Get-Date).ToUniversalTime()
$stamp      = $cycleStart.ToString('yyyyMMddTHHmmssZ')
$dispatched = @()
$skipped    = @()
$halted     = $null

$syncError = $null
if (Test-Path -LiteralPath $KillSwitch) {
    $halted = "kill-switch present at $KillSwitch"
    Write-Output "LOOP: HALTED - $halted"
} else {
    $syncError = Sync-DriverWorktree
    if ($syncError) { Write-Output "LOOP: driver worktree - $syncError" }
    else { Write-Output "LOOP: driver worktree at $DriverRoot pinned to $TargetRef ($(& git -C $DriverRoot rev-parse --short HEAD 2>$null))" }
}

if ($halted) {
    # already halted by the kill switch
} elseif ($syncError) {
    $halted = $syncError
    Write-Output "LOOP: CANNOT-DETERMINE - $halted"
} elseif (-not (Test-Path -LiteralPath $Dispatcher)) {
    $halted = "dispatcher missing at $Dispatcher even after syncing to $TargetRef - it is not on the target branch yet"
    Write-Output "LOOP: CANNOT-DETERMINE - $halted"
} else {
    # Budget is DERIVED from the log's own records for today, so it cannot drift
    # from what actually happened the way a stored counter can.
    $todayUtc = $cycleStart.ToString('yyyy-MM-dd')
    $spentToday = 0
    if (Test-Path -LiteralPath $LogPath) {
        foreach ($line in (Get-Content -LiteralPath $LogPath)) {
            if (-not $line.Trim()) { continue }
            try {
                $row = $line | ConvertFrom-Json
                $t = $row.PSObject.Properties['dispatchedUtc']
                if ($t -and $t.Value -and ([datetime]::Parse($t.Value)).ToUniversalTime().ToString('yyyy-MM-dd') -eq $todayUtc) {
                    $spentToday++
                }
            } catch { }
        }
    }
    Write-Output "LOOP: budget $spentToday/$DailyBudget used today (UTC $todayUtc); cycle cap $MaxDispatchesPerCycle"

    if ($spentToday -ge $DailyBudget) {
        $halted = "daily budget exhausted ($spentToday/$DailyBudget)"
        Write-Output "LOOP: HALTED - $halted"
    } else {
        foreach ($track in $Tracks) {
            if ($dispatched.Count -ge $MaxDispatchesPerCycle) { break }
            if (($spentToday + $dispatched.Count) -ge $DailyBudget) {
                $halted = 'daily budget reached mid-cycle'
                break
            }

            $argv = @('-NoProfile','-NonInteractive','-ExecutionPolicy','Bypass','-File',$Dispatcher,
                      '-Track',$track,'-TimeoutSec',$TimeoutSec,'-StaleHours',$StaleHours)
            if ($DryRun) { $argv += '-DryRun' }

            $out = & pwsh @argv 2>&1
            $rc  = $LASTEXITCODE
            # THE DISPATCH LINE, not merely the first WORKSTREAM: line. PR #26 added
            # "WORKSTREAM: landing-probe ..." and "WORKSTREAM: SKIP-LANDED ..." which now
            # print BEFORE the dispatch line, so First-1 recorded a probe summary as the
            # dispatch detail. Match the line that actually names the card; fall back to
            # the last WORKSTREAM: line, never the first.
            $line = ($out | Where-Object { $_ -match 'WORKSTREAM: track=' } | Select-Object -First 1)
            if (-not $line) { $line = ($out | Where-Object { $_ -match 'WORKSTREAM:' } | Select-Object -Last 1) }

            switch ($rc) {
                0 { $dispatched += [ordered]@{ track = $track; detail = "$line" } ; Write-Output "LOOP: [$track] dispatched - $line" }
                5 { $skipped    += [ordered]@{ track = $track; reason = 'ALL-RECENTLY-DISPATCHED'; detail = "$line" } ; Write-Output "LOOP: [$track] ALL-RECENTLY-DISPATCHED - no slot consumed" }
                4 { $skipped    += [ordered]@{ track = $track; reason = 'NO-LIVE-CARDS'; detail = "$line" } ; Write-Output "LOOP: [$track] NO-LIVE-CARDS - backlog gap, nothing to dispatch" }
                3 { $skipped    += [ordered]@{ track = $track; reason = 'CANNOT-DETERMINE'; detail = "$line" } ; Write-Output "LOOP: [$track] CANNOT-DETERMINE - $line" }
                default { $skipped += [ordered]@{ track = $track; reason = "exit-$rc"; detail = "$line" } ; Write-Output "LOOP: [$track] exit=$rc - $line" }
            }
        }
    }
}

$cycleEnd = (Get-Date).ToUniversalTime()
$receipt = [ordered]@{
    schema        = 'mlv-app/workstream-loop-cycle/v1'
    startedUtc    = $cycleStart.ToString('o')
    endedUtc      = $cycleEnd.ToString('o')
    durationSec   = [math]::Round(($cycleEnd - $cycleStart).TotalSeconds, 1)
    dryRun        = [bool]$DryRun
    tracks        = $Tracks
    maxPerCycle   = $MaxDispatchesPerCycle
    dailyBudget   = $DailyBudget
    dispatched    = $dispatched
    skipped       = $skipped
    haltedReason  = $halted
}
$receiptPath = Join-Path $CycleDir "cycle-$stamp.json"
[System.IO.File]::WriteAllText($receiptPath, ($receipt | ConvertTo-Json -Depth 6), [System.Text.UTF8Encoding]::new($false))
Write-Output "LOOP: cycle receipt $receiptPath (dispatched=$($dispatched.Count) skipped=$($skipped.Count))"
exit 0
