<#
.SYNOPSIS
    Pick the next actionable card on a workstream, build a COMPACT self-contained
    lane brief, and dispatch a bounded lane. No seat, no lease, no queue mutation.

.DESCRIPTION
    THIS SCRIPT EXISTS BECAUSE OF A MEASURED COST, NOT A STYLE PREFERENCE.

    On 2026-08-31 the same card was dispatched twice to the same lane:

      run 1  prompt 5.7 KB, no capability line, no reading budget
             -> 16 turns, 1,956,254 cache-creation tokens, DIED prompt_too_long, $51.61
      run 2  prompt 7.0 KB, facts inlined, byte-sized reading budget, capability stated
             -> 3 turns, 40,759 cache-creation tokens, $1.18

    A 48x reduction in tokens ingested and 44x in cost, from prompt shape alone.
    The lane in run 1 spent its context reading this board's own coordination
    surface, which is ~1.6 MB:
        .claude-state\coordination\gpu-lane-impl-review-sync.md   ~1.34 MB
        .claude-state\coordination\dual-lane\queue.json           ~270 KB

    So the token economy is NOT about the conversation. It is about never letting
    bulk state reach a model. This script reads the bulk itself, in PowerShell,
    and hands the lane a few KB with the facts already in it.

    THREE THINGS THE BRIEF ALWAYS CARRIES, because each was a measured failure:
      1. THE LANE'S REAL TOOLSET. Invoke-Lane grants tools BY ENGINE. A claude
         lane without -AllowEdits gets Read,Grep,Glob and NO SHELL; a codex lane
         gets ALL tools under a read-only sandbox and CAN execute. A prompt that
         says "prove by execution" is unsatisfiable for the former, and nothing
         told it so.
      2. A READING BUDGET WITH BYTE SIZES, so a lane knows which files will eat
         its context before it opens one.
      3. THE FACTS INLINED. The dispatcher already holds them; making the lane
         re-derive them is what costs the 1.9M tokens.

    QUEUE IS NEVER MUTATED. RESUME.md STEP 4/5 bars it under the recovery
    authority. Dispatch records go to their own append-only log.

.NOTES
    ASCII-only by project convention. The lane/model table lives in
    Invoke-Lane.ps1 and is deliberately NOT duplicated here.
#>
[CmdletBinding()]
param(
    [ValidateSet('factory','playback','product','continuity','fleet','gate','UNSET','auto')]
    [string]$Track = 'auto',

    [string]$CardId,

    [switch]$DryRun,

    [ValidateSet('opus','sonnet','fable','sol','luna')]
    [string]$Lane,

    [int]$TimeoutSec = 1800,

    [switch]$Force,
    [int]$StaleHours = 12,

    # Skip the merged-PR landing probe entirely (offline, or gh deliberately not consulted).
    [switch]$NoLandingProbe,

    # Read the queue from somewhere other than the canonical path. EXISTS FOR FALSIFICATION:
    # the landed-card guard below can only be proven by a queue in which a landed card is the
    # TOP pick, and the real queue must never be mutated to manufacture that. Never used in
    # production; the default is the canonical queue.
    [string]$QueuePath = ''
)

$ErrorActionPreference = 'Stop'
Set-StrictMode -Version Latest

$RepoRoot   = 'C:\!Layi Wkspc\MLV-App'
$DualLane   = Join-Path $RepoRoot '.claude-state\coordination\dual-lane'
if (-not $QueuePath) { $QueuePath = Join-Path $DualLane 'queue.json' }
else { Write-Output "WORKSTREAM: NON-CANONICAL QUEUE in use: $QueuePath" }
$LogPath    = Join-Path $DualLane 'workstream-dispatch-log.jsonl'
$PromptDir  = Join-Path $RepoRoot '.claude-state\fleet-runs\prompts'
# THE LANE RUNNER MUST COME FROM THE TREE THIS SCRIPT LIVES IN, NOT FROM $RepoRoot.
# Invoke-WorkstreamLoop pins a driver worktree to fork/master precisely so the unattended loop
# runs reviewed code - and then this line reached OUTSIDE that pin, back to the canonical
# checkout, which is parked on a peer branch (diag/async-h2d-preupload-exits). Measured
# 2026-09-03: every dispatched lane ran the DIAG BRANCH's Invoke-Lane.ps1, so PR #27's read
# deny-list, turn cap and spend telemetry had NO EFFECT on any production dispatch. A fable lane
# at 20:16Z - three hours after #27 merged - cost USD 22.01 at 948,598 cache-creation tokens,
# the exact unprotected signature #27 was written to stop, and its receipt carried no spend field.
# $PSScriptRoot is the pinned sibling when driven by the loop, and the local sibling when run by
# hand: correct in both cases, and it can never silently cross into another branch's checkout.
$LaneRunner = Join-Path $PSScriptRoot 'Invoke-Lane.ps1'

foreach ($p in @($QueuePath, $LaneRunner)) {
    if (-not (Test-Path -LiteralPath $p)) {
        Write-Output "WORKSTREAM: CANNOT-DETERMINE - missing $p"
        exit 3
    }
}
if (-not (Test-Path -LiteralPath $PromptDir)) {
    New-Item -ItemType Directory -Path $PromptDir -Force | Out-Null
}

# A card in one of these states is done. Anything else is non-terminal (live) work.
$Terminal = @(
    'closed-fixed','closed-not-this-board','closed-root-caused','closed-superseded',
    'closed-transformed','landed','landed-evidence','landed-local-proof','CLEARED',
    'RETIRED','withdrawn','superseded','retracted-and-fixed','fixed','answered-folded'
)

# ALLOWLIST, not blocklist. Measured 2026-09-05: card B2-TOOLING-BASELINE carried
# state=booked, track=UNSET, priority=999 and was still dispatched, burning a lane slot on
# work no lane can act on - queue.json's OWN top-level "note" field defines 'booked' as "a
# named trigger, not schedulable". A blocklist here would require enumerating every state
# that ALSO names a party or trigger other than a lane (booked-rule, consult-open,
# deferred-nonblocking, open-risk, optional-validation, surfaced-awaiting-ordering,
# handed-off-awaiting-review, changes-requested-awaiting-acceptance-evidence,
# blocked-operator, ...) and the queue keeps growing that vocabulary. An allowlist of the
# states the note itself says a LANE owns needs no such enumeration: anything non-terminal
# and not in this list is reported, never dispatched.
#
# 'dispatched-untracked-target' IS schedulable, despite the name - CHECKED AGAINST THE LIVE
# QUEUE, not assumed: SIDECAR-COVERAGE-1 (track=factory, priority=7, owner=codex) carries
# it right now. Its own stateReason explains the word "untracked": the deliverable lives
# under gitignored .claude-state/, so it has no git-trackable range and no gate - "untracked"
# there means git tracking, unrelated to this script's own `track` field. The work is real
# and still owned by a lane; excluding this state would silently strand it, which is the
# same class of defect this fix exists to remove, just aimed at a different card.
$Schedulable = @('queued', 'dispatched', 'in-review', 'waiting-evidence', 'dispatched-untracked-target')

function Get-Prop($obj, [string]$name) {
    if ($null -eq $obj) { return $null }
    $p = $obj.PSObject.Properties[$name]
    if ($null -eq $p) { return $null }
    return $p.Value
}

function Get-Track($item) {
    $t = Get-Prop $item 'track'
    if (-not $t) { return 'UNSET' }
    return $t
}

$queue = Get-Content -LiteralPath $QueuePath -Raw | ConvertFrom-Json
$items = @($queue.items)
$live  = @($items | Where-Object { $Terminal -notcontains (Get-Prop $_ 'state') })

# ------------------------------------------------------------------ dispatch history
$history = @{}
$malformed = 0
if (Test-Path -LiteralPath $LogPath) {
    foreach ($line in (Get-Content -LiteralPath $LogPath)) {
        if (-not $line.Trim()) { continue }
        try {
            $row = $line | ConvertFrom-Json
            $id = Get-Prop $row 'cardId'
            if ($id) { $history[$id] = $row }
        } catch { $malformed++ }
    }
}
# A malformed row is COUNTED, never silently treated as "no dispatch". Absent and
# unreadable are different facts and this script will not merge them.
if ($malformed -gt 0) { Write-Output "WORKSTREAM: WARNING - $malformed malformed dispatch-log row(s) skipped" }

function Get-LastDispatchAgeHours([string]$id) {
    if (-not $history.ContainsKey($id)) { return $null }
    $t = Get-Prop $history[$id] 'dispatchedUtc'
    if (-not $t) { return $null }
    # TIMEZONE, MEASURED 2026-09-03. ConvertFrom-Json already materialises an ISO-8601 "...Z"
    # string as a [datetime] with Kind=Utc. The previous body called [datetime]::Parse($t) on
    # that object, which stringifies it to a zone-less local-looking form, re-parses it as
    # Kind=Unspecified, and then ToUniversalTime() adds the local offset a SECOND time. On this
    # host (UTC-5) a card dispatched 0.21 h ago computed as -4.79 h, so `$age -ge $StaleHours`
    # was false and the card was filtered for ~5 h longer than configured.
    # It is invisible on a UTC machine, which is why it survived: the bug's size IS the offset.
    if ($t -is [datetime]) { return ((Get-Date).ToUniversalTime() - $t.ToUniversalTime()).TotalHours }
    try {
        $parsed = [datetime]::Parse(
            [string]$t, [cultureinfo]::InvariantCulture,
            [System.Globalization.DateTimeStyles]::AdjustToUniversal -bor
            [System.Globalization.DateTimeStyles]::AssumeUniversal)
        return ((Get-Date).ToUniversalTime() - $parsed).TotalHours
    } catch { return $null }
}

# ------------------------------------------------------------------ landed-elsewhere probe
# WHY THIS EXISTS. Card liveness above is derived from queue.json's `state` field alone.
# queue.json is the DISPATCH authority - it is NOT the authority on whether the WORK landed,
# and this script never mutates it (RESUME.md STEP 4/5). So a card whose fix merged stays
# `dispatched` forever and this selector re-picks it every cycle, spending real model budget
# on work that is already on master. Measured 2026-09-03: the loop's first unattended fire
# dispatched a lane for SIDECAR-FIX-1, which PR #23 had merged nine hours earlier.
#
# RAW OUTRANKS THE QUEUE, so ask GitHub, not the card. THE MATCH RULES, THE LANDING VOCABULARY
# AND THE NEAR-MISS ASYMMETRY NOW LIVE IN ONE PLACE: landing-probe.ps1, in this directory.
# queue-derive.ps1 asks GitHub the same question for a different purpose, and two copies of this
# vocabulary would be widened once and left stale - the single most frequently paid failure on
# this board. Read that file for the rules and for the measured incident behind each one.
. (Join-Path $PSScriptRoot 'landing-probe.ps1')

$landedById = @{}
$landedHow = @{}
$nearMisses = @()
$landingProbe = 'skipped'
if (-not $NoLandingProbe) {
    $probe = Get-CardLandingEvidence -CardId @($live | ForEach-Object { Get-Prop $_ 'id' } | Where-Object { $_ })
    $landingProbe = $probe.status
    foreach ($id in $probe.landed.Keys) {
        $landedById[$id] = $probe.landed[$id]
        $landedHow[$id]  = $probe.landed[$id].how
    }
    $nearMisses = @($probe.nearMiss)
    if ($nearMisses.Count -gt 0) {
        Write-Output ("WORKSTREAM: landing-probe NEAR-MISS ($($nearMisses.Count)): " +
            ($nearMisses -join ', ') + " - named in a merged PR without a landing verb, NOT skipped")
    }
}
Write-Output "WORKSTREAM: landing-probe $landingProbe"
foreach ($id in $landedById.Keys) {
    $pr = $landedById[$id]
    Write-Output ("WORKSTREAM: SKIP-LANDED card={0} merged in PR #{1} ({2}) [matched by {4}] but queue.json still says '{3}'. The queue is STALE on this card; this script does not mutate it - reconcile it." -f `
        $id, $pr.number, $pr.title, (Get-Prop (@($live | Where-Object { (Get-Prop $_ 'id') -eq $id })[0]) 'state'), $landedHow[$id])
}

# ------------------------------------------------------------------ schedulability
# TWO independent rules, both structural (like $Terminal above): neither is bypassable by
# -Force, which only ever waived the landed-probe and staleness filters below.
#   1. state must be in $Schedulable.
#   2. AUTO track selection must not fall through to an untracked card. 'UNSET' remains a
#      real, INTENTIONAL dispatch target (Invoke-WorkstreamLoop.ps1 rotates through it
#      explicitly as its own -Track value) - this only closes the silent fallthrough where
#      the default $Track='auto' pool never filtered by track at all, so an untracked card
#      was always a candidate whenever the tracked pools ran dry.
function Get-NotSchedulableReason($item) {
    $state = Get-Prop $item 'state'
    if ($Schedulable -notcontains $state) {
        return "state '$state' is not in the schedulable allowlist (queued, dispatched, in-review, waiting-evidence, dispatched-untracked-target) - queue.json's own note names this class a named trigger, not schedulable"
    }
    if ($Track -eq 'auto' -and (Get-Track $item) -eq 'UNSET') {
        # NAMED DELIBERATELY UNLIKE THE STATE 'dispatched-untracked-target' above, which
        # means something unrelated (its DELIVERABLE is untracked BY GIT). This is about the
        # QUEUE'S OWN `track` FIELD (factory/playback/product/...) being absent.
        return 'no-board-track-set - auto-select requires an explicit track field on the card; pass -Track UNSET to pick this card on purpose'
    }
    return $null
}

foreach ($item in $live) {
    $reason = Get-NotSchedulableReason $item
    if ($reason) {
        Write-Output ("WORKSTREAM: NOT-SCHEDULABLE card={0} state={1} reason={2}" -f (Get-Prop $item 'id'), (Get-Prop $item 'state'), $reason)
    }
}

# ------------------------------------------------------------------ selection
$script:WarnedNonNumericPriority = @{}
function Get-Rank($item) {
    # Unset priority sorts LAST, so an unprioritised card never outranks a p1.
    $pr = Get-Prop $item 'priority'
    if ($null -eq $pr) { return 999 }
    $n = 0
    if ([int]::TryParse([string]$pr, [ref]$n)) { return $n }

    # A bare [int] cast here THROWS on a non-numeric string, and the throw lands inside
    # Sort-Object -Property { Get-Rank $_ }, so the whole selection dies -- while the script
    # still exits 0. The loop then records a normal cycle that dispatched nothing. Silent
    # starvation is worse than a halt, because nothing reports it.
    #
    # This is not hypothetical: queue.json carries prose in this field on DISPATCH-CDX-14
    # ("HIGHEST - outranks the control-plane three") and DISPATCH-CDX-15 ("CRITICAL PATH -
    # the cap is now two blocks away, not one"). Both happen to be terminal today, so they
    # never reach this function -- the dispatcher is one live prose-priority card away from
    # dispatching nothing at all, and the authoring habit that produces them is demonstrated.
    #
    # Rank it as unset and SAY SO on stderr. Not stdout: the [WORKSTREAM] lines are parsed,
    # and emitting into a Sort-Object property block would make the sort key an array.
    $id = [string](Get-Prop $item 'id')
    if (-not $script:WarnedNonNumericPriority.ContainsKey($id)) {
        $script:WarnedNonNumericPriority[$id] = $true
        [Console]::Error.WriteLine(("WORKSTREAM: NON-NUMERIC PRIORITY on card '{0}': {1}. Ranked as unset (999) so selection continues; give the card a numeric priority to restore its rank." -f $id, ([string]$pr).Trim()))
    }
    return 999
}

if ($CardId) {
    $candidates = @($items | Where-Object { (Get-Prop $_ 'id') -eq $CardId })
    if ($candidates.Count -eq 0) {
        Write-Output "WORKSTREAM: CANNOT-DETERMINE - no card with id '$CardId'"
        exit 3
    }
} else {
    $pool = @($live | Where-Object { -not (Get-NotSchedulableReason $_) })
    if ($Track -ne 'auto') {
        $pool = @($pool | Where-Object { (Get-Track $_) -eq $Track })
    }
    # Landed-elsewhere cards are excluded here, never earlier: $live must keep its meaning
    # ("non-terminal per the queue") so the NO-LIVE-CARDS vs ALL-FILTERED distinction below
    # still reports the truth about the track.
    if ($landedById.Count -gt 0 -and -not $Force) {
        $pool = @($pool | Where-Object { -not $landedById.ContainsKey((Get-Prop $_ 'id')) })
    }
    if (-not $Force) {
        $pool = @($pool | Where-Object {
            $age = Get-LastDispatchAgeHours (Get-Prop $_ 'id')
            ($null -eq $age) -or ($age -ge $StaleHours)
        })
    }
    $candidates = @($pool | Sort-Object -Property `
        @{ Expression = { Get-Rank $_ } }, `
        @{ Expression = { Get-Prop $_ 'id' } })
}

if ($candidates.Count -eq 0) {
    # THREE OUTCOMES, NEVER TWO. "no card exists" and "every card was filtered out"
    # are different facts about the board and this script will not merge them - an
    # empty track is a GAP to be filled, a fully-dispatched track is HEALTHY.
    $onTrackLive = if ($Track -eq 'auto') { $live.Count }
                   else { @($live | Where-Object { (Get-Track $_) -eq $Track }).Count }
    if ($onTrackLive -eq 0) {
        Write-Output "WORKSTREAM: NO-LIVE-CARDS on track '$Track'. This track has ZERO non-terminal work - it is not idle, it is EMPTY. That is a backlog gap, not a healthy queue. (live overall = $($live.Count))"
        exit 4
    }
    # EXIT 5, NOT 0. This script preaches "three outcomes, never two" and then returned the
    # SAME code for "I dispatched a lane" and "I dispatched nothing". Invoke-WorkstreamLoop
    # counts exit 0 as a dispatch, so every ALL-RECENTLY-DISPATCHED cycle consumed a slot of
    # -MaxDispatchesPerCycle and inflated the cycle receipt with work that never happened.
    # Measured 2026-09-03: cycle-20260903T180119Z recorded dispatched=1 with no dispatch-log
    # row and no run directory to match it.
    Write-Output "WORKSTREAM: ALL-RECENTLY-DISPATCHED on track '$Track' - $onTrackLive live card(s), every one carrying a dispatch newer than $StaleHours h. Use -Force or lower -StaleHours to re-dispatch."
    exit 5
}

$card      = $candidates[0]
$cardId    = Get-Prop $card 'id'
$cardTrack = Get-Track $card

# ------------------------------------------------------------------ engine choice
# Cards asking for derivation or measurement need a SHELL. Only codex lanes have
# one when invoked read-only, so route those to codex and pure analysis to claude.
$cardText   = ($card | ConvertTo-Json -Depth 8)
$needsShell = $cardText -match '(?i)re-derive|derive|measure|reproduce|proving command|prove by|verify by execution|run the'
if (-not $Lane) { $Lane = if ($needsShell) { 'luna' } else { 'fable' } }
$engine = if ($Lane -eq 'sol' -or $Lane -eq 'luna') { 'codex' } else { 'claude' }

# The run directory is named here, not at dispatch, because the brief has to be able to NAME
# files that this script is about to write into it. -DryRun still exports, and still writes into
# this path, so what you inspect under -DryRun is byte-identical to what a lane would receive.
$stamp  = (Get-Date).ToUniversalTime().ToString('yyyyMMddTHHmmssZ')
$runDir = Join-Path $RepoRoot ".claude-state\fleet-runs\ws-$cardId-$stamp"

# ------------------------------------------------------------------ hosted GitHub evidence
# WHY THIS EXISTS, AND WHY A WARNING IN THE BRIEF WAS NOT ENOUGH.
#
# MEASURED 2026-09-05. Two priority-1 cards, FACTORY-MATURITY-1-CLAUDE and FACTORY-MATURITY-1-OPUS,
# consumed two lane slots each and both returned the SAME wall: `gh` answers "Access is denied"
# inside the read-only lane sandbox, so every hosted fact stayed UNVERIFIED. The same `gh`, the same
# token and the same machine work from an interactive session - `gh` resolves its token through the
# Windows credential keyring, and the sandbox denies that read. The failure is the VENUE, not the
# installation and not the credential.
#
# PR #55 had ALREADY told lanes that `gh` may be unusable and that saying so is a FINDING. Both
# lanes did exactly that, correctly, and were dispatched into the wall anyway - because the brief
# only taught them how to REPORT the limitation, never removed it. A WARNING IN A PROMPT IS NOT A
# FIX; IT IS A NICER WAY TO FAIL. This script already runs `gh` for the landing probe from the venue
# where it works, so the evidence was always one command away from the process doing the dispatching.
#
# THE ASYMMETRY IS THE OPPOSITE OF THE LANDING PROBE'S, and that is deliberate. There, a false
# positive SKIPS live work, so the match is kept narrow. Here, a false positive costs a handful of
# read-only API calls and ~30 KB in a run directory nobody reads, while a false NEGATIVE costs a
# whole lane slot. So the trigger below is broad ON PURPOSE. Over-exporting is not a defect.
#
# FAIL-OPEN, exactly like the landing probe: if `gh` is missing, denied, offline or slow, the brief
# SAYS SO IN THOSE WORDS and the lane is dispatched anyway. Silence would put the lane straight back
# into the wall while looking like a clean brief.
$needsHostedEvidence = $cardText -match '(?i)\bgh\b|github|branch protection|ruleset|status check|actions run|workflow run|ci history|consecutive failure|hosted evidence|pull request|\bPR #'
$ghRows    = @()   # one row per attempted export: name, file, bytes-or-reason
$ghSection = ''

if ($needsHostedEvidence) {
    $ghDir = Join-Path $runDir 'github-evidence'
    New-Item -ItemType Directory -Path $ghDir -Force | Out-Null

    # A FIXED, PREDICTABLE SET. Not derived from the card text: a lane must be able to rely on the
    # same filenames every time, and a card-shaped guess would silently omit whatever the card
    # forgot to name. Anything missing is a FINDING the lane reports, not a gap it works around.
    $ghJobs = [ordered]@{
        'repo-metadata.json' = @(
            'api','repos/layibabalola/MLV-App','--jq',
            '{visibility,private,fork,archived,default_branch,pushed_at}')
        'branch-protection-master.json' = @(
            'api','repos/layibabalola/MLV-App/branches/master/protection')
        'rulesets.json' = @('api','repos/layibabalola/MLV-App/rulesets')
        'runs-tests-master-push-completed.json' = @(
            'run','list','--repo','layibabalola/MLV-App','--workflow','tests.yml',
            '--branch','master','--event','push','--status','completed','--limit','50',
            '--json','databaseId,conclusion,createdAt,headSha,displayTitle')
    }

    $gh = Get-Command gh -ErrorAction SilentlyContinue
    if (-not $gh) {
        $ghRows += [pscustomobject]@{ Name = '(all)'; Result = 'CANNOT-DETERMINE: gh not on PATH' }
    } else {
        foreach ($name in $ghJobs.Keys) {
            $outFile = Join-Path $ghDir $name
            $errFile = "$outFile.err.txt"
            try {
                $ghArgs = $ghJobs[$name]
                & gh @ghArgs 1>$outFile 2>$errFile
                $code = $LASTEXITCODE
            } catch {
                $code = -1
                Set-Content -LiteralPath $errFile -Value $_.Exception.Message -Encoding UTF8
            }
            $len = if (Test-Path -LiteralPath $outFile) { (Get-Item -LiteralPath $outFile).Length } else { 0 }
            if ($code -eq 0 -and $len -gt 0) {
                Remove-Item -LiteralPath $errFile -ErrorAction SilentlyContinue
                $ghRows += [pscustomobject]@{ Name = $name; Result = "$len bytes" }
            } else {
                # THE REASON IS KEPT, NOT JUST THE EXIT CODE. "Access is denied" and "404" send a
                # reader to completely different places, and collapsing both to "gh failed" is how
                # a venue problem gets misfiled as an authorization problem for a second time.
                $why = ''
                if (Test-Path -LiteralPath $errFile) {
                    $why = ((Get-Content -LiteralPath $errFile -Raw) -replace '\s+', ' ').Trim()
                }
                if (-not $why) { $why = "gh exit $code, no stderr" }
                if ($why.Length -gt 160) { $why = $why.Substring(0, 160) + '...' }
                Remove-Item -LiteralPath $outFile -ErrorAction SilentlyContinue
                $ghRows += [pscustomobject]@{ Name = $name; Result = "FAILED - $why" }
            }
        }

        # The job graph is a SECOND call keyed on the first one's newest row, so it can only be made
        # after the run list lands. Skipped without complaint when that export failed.
        $runsFile = Join-Path $ghDir 'runs-tests-master-push-completed.json'
        if (Test-Path -LiteralPath $runsFile) {
            try {
                $runs = @(Get-Content -LiteralPath $runsFile -Raw | ConvertFrom-Json |
                          Sort-Object -Property createdAt -Descending)
                if ($runs.Count -gt 0) {
                    $newest  = $runs[0].databaseId
                    $jobsOut = Join-Path $ghDir "jobs-latest-$newest.json"
                    $jobsErr = "$jobsOut.err.txt"
                    & gh api "repos/layibabalola/MLV-App/actions/runs/$newest/jobs" 1>$jobsOut 2>$jobsErr
                    $jcode = $LASTEXITCODE
                    $jlen  = if (Test-Path -LiteralPath $jobsOut) { (Get-Item -LiteralPath $jobsOut).Length } else { 0 }
                    if ($jcode -eq 0 -and $jlen -gt 0) {
                        Remove-Item -LiteralPath $jobsErr -ErrorAction SilentlyContinue
                        $ghRows += [pscustomobject]@{ Name = "jobs-latest-$newest.json"; Result = "$jlen bytes (run $newest, newest by createdAt)" }
                    } else {
                        Remove-Item -LiteralPath $jobsOut -ErrorAction SilentlyContinue
                        $ghRows += [pscustomobject]@{ Name = "jobs-latest-$newest.json"; Result = "FAILED - gh exit $jcode" }
                    }
                }
            } catch {
                $ghRows += [pscustomobject]@{ Name = '(job graph)'; Result = "FAILED - $($_.Exception.Message)" }
            }
        }
    }

    $ghLines  = ($ghRows | ForEach-Object { "    {0,-42} {1}" -f $_.Name, $_.Result }) -join "`n"
    $ghOk     = @($ghRows | Where-Object { $_.Result -notlike 'FAILED*' -and $_.Result -notlike 'CANNOT-DETERMINE*' }).Count
    $ghFailed = $ghRows.Count - $ghOk

    # THE HEADING AND THE CLOSING INSTRUCTION BOTH TRACK THE OUTCOME. A section headed "already
    # exported for you" that closes with "read these files" above a list of five FAILED rows tells
    # the lane to read files that do not exist - which is the same class of defect as the brief line
    # that told two lanes their own correct observation must be false.
    if ($ghOk -eq 0) {
        $ghHead    = 'HOSTED GITHUB EVIDENCE - THE EXPORT FAILED. THERE IS NOTHING TO READ.'
        $ghVerdict = @"
EVERY EXPORT FAILED ($ghFailed of $($ghRows.Count)) - the rows above carry the exact reason.
So the hosted facts are UNVERIFIED and you must SAY SO rather than substituting a remembered value.
This is a VENUE finding about the dispatcher, not a failing of yours and not ``operator-only``.
Do the parts of the card that need no hosted evidence, and report the rest as UNVERIFIED.
"@
    } else {
        # Single backticks here, NOT doubled: this string is INTERPOLATED into the here-string
        # below as a value, so the here-string's backtick escaping never runs over it.
        $ghHead    = 'HOSTED GITHUB EVIDENCE - ALREADY EXPORTED FOR YOU. DO NOT RUN `gh`.'
        $ghVerdict = if ($ghFailed -eq 0) {
            "ALL $ghOk EXPORTS SUCCEEDED. Read them instead of calling ``gh``: they are plain JSON`nfrom the GitHub API, so cite the file name and the field you relied on."
        } else {
            "$ghOk EXPORT(S) SUCCEEDED, $ghFailed FAILED - the rows above say why. Read the ones that landed`nand cite file and field. Treat every fact the failed ones would have carried as UNVERIFIED and`nSAY SO; do not substitute a remembered value."
        }
    }

    $ghSection = @"

## $ghHead
This card needs facts that live on GitHub. ``gh`` DOES NOT WORK FROM YOUR SANDBOX - it reads its
token from the Windows credential keyring and your sandbox denies that read, so it answers
``Access is denied``, which looks like an authorization failure and is not one. MEASURED 2026-09-05:
two lanes burned two slots each proving exactly that. So this dispatcher, which runs in a venue
where ``gh`` usually works, ran the calls for you. Attempted at $stamp into:
    $ghDir
$ghLines
$ghVerdict
DO NOT RETRY ``gh`` YOURSELF - it will fail the same way and cost you the card. If you need a fact
that is not in the exports, do not work around it: report it as a FINDING naming the exact ``gh``
command that would produce it, so the export set can be widened.
"@
}

# ------------------------------------------------------------------ the brief
if ($engine -eq 'codex') {
    $capability = @'
You are invoked READ-ONLY on the codex engine: ALL tools under a `read-only` sandbox.
YOU CAN EXECUTE (git, python, pwsh) but CANNOT write files. Prove by EXECUTION and PRINT the
ref you bound to. Run a FALSIFIER beside every subject check - a control and a subject that
return the same reason prove nothing.
'@
} else {
    $capability = @'
You are invoked READ-ONLY on a claude engine. YOUR ONLY TOOLS ARE `Read`, `Grep`, `Glob`.
YOU HAVE NO SHELL - `Bash` and `PowerShell` calls are DENIED by the runner, so no git, no
python, no pwsh. The board's standing "prove by EXECUTION" rule is UNSATISFIABLE for you on
this invocation; that is a known runner asymmetry, not your failing. Substitute: cite a FILE
PATH AND LINE via Grep/Read, and label anything you could not check
CANNOT-VERIFY-WITHOUT-SHELL. Never fold CANNOT-VERIFY into a pass.
'@
}

$cardJson = $card | ConvertTo-Json -Depth 8
$fence    = '```'

$brief = @"
# LANE BRIEF - card $cardId (track: $cardTrack)

## YOUR CAPABILITIES ON THIS INVOCATION - read before planning
$capability
$ghSection
## HARD READING BUDGET - a previous lane died of prompt_too_long after 16 turns and 51 dollars
It blew its context reading this board's coordination surface whole. NEVER read these without a
narrow offset/limit, or a grep-then-slice:
    .claude-state\coordination\gpu-lane-impl-review-sync.md   ~1.34 MB
    .claude-state\coordination\dual-lane\queue.json           ~270 KB
    .claude-state\RESUME.md                                   ~27 KB (fine to read once)
THE CARD IS INLINED BELOW IN FULL. You do not need to open the queue. Budget ~12 tool calls.

## THOSE PATHS ARE AT THE BOARD ROOT, NOT IN YOUR WORKDIR. ABSENT THERE MEANS NOTHING.
``.claude-state/`` is GITIGNORED, so it is never checked out into a worktree - and your workDir IS a
worktree. Resolve those paths against the BOARD ROOT by absolute path, never against your cwd.
MEASURED 2026-09-05: a lane on C2-PROV-1 reported the content-review gate file had been ``rotated
away`` because it was not in the driver worktree. It was intact at the board root: 1,342,684 bytes,
modified 2026-08-26, exactly where closeout.config.json points. The worktree even HAS a nearly-empty
``.claude-state/``, so the absence reads as deletion rather than as never-having-been-there.
DO NOT report a ``.claude-state`` file missing, deleted or rotated away unless you checked the board
root by absolute path. brokered_closeout.py already resolves it that way (GATE-ID-4); only
hand-reading gets this wrong.

## STANDING OPERATOR RULING (2026-08-31), binding on your output
Layi, verbatim: "use the wisdom of the hub lanes to adjudicate decisions rather than ask me my
opinion. I am not qualified." DO NOT return "ask Layi". Return a DECISION. If some part is
genuinely operator-only you must be able to write this line in full, or it is not operator-only:
    BLOCKED ON Layi (<action>) -- delegation check: <lane> <why not> ... Operator-only because
    <1 physical access | 2 external account/UI-only surface | 3 policy-reserved>.
NOTE: ``gh`` IS installed and authenticated on this box, so GitHub PR create/merge is NOT
operator-only. A GitHub blocker is USUALLY wrong - but VERIFY before asserting either way.
MEASURED 2026-09-05: two lanes (FACTORY-MATURITY-1-CLAUDE and -OPUS) both got ``gh auth status`` ->
``Access is denied`` under a read-only sandbox, and both correctly returned UNVERIFIED. This brief
previously stated flatly that gh IS authenticated and that any GitHub blocker is wrong - which told
both lanes their own correct observation must be false. If ``gh`` fails for you, say so plainly and
name it a VENUE limitation: that is a real finding, not a lane error, and it is NOT the same as
``operator-only``. Do not spend the card working around it. AND SINCE 2026-09-05 YOU SHOULD NOT
NEED TO: when a card wants hosted facts, this dispatcher runs the ``gh`` calls itself, from the
venue where they work, and a HOSTED GITHUB EVIDENCE section above names the exported JSON. If that
section is absent, this card was not classified as needing hosted evidence - report that as the
finding rather than reaching for ``gh``.

## STANDING ROUTING FACT
This host (VIRTUAL-TEN) is a VMware VM with ZERO NVIDIA hardware. CUDA build and GPU playback
legs are ROUTED to the GPU hosts (\\bachelor\mlv-agent). "CUDA blocked locally" is a ROUTING
decision, never a lane blocker.
A PATH UNDER \\bachelor\... OR C:\mlvtmp\mlv-agent\... IS ON ANOTHER MACHINE. That whole
root does not exist here, so Test-Path/Get-FileHash on it from this host reports MISSING for
EVERY file it contains. A LOCAL PROBE OF A REMOTE PATH IS A STATEMENT ABOUT THIS HOST, NOT
ABOUT THE FILE. Report it as UNVERIFIED and name the venue - never as absent, and never as
contradicting a deployment claim. A control proving your HASH CHECK discriminates does NOT
prove you tested the right MACHINE: a falsifier on the mechanism is not a falsifier on the
venue. (Measured 2026-09-04: a lane read PresentMon MISSING at C:\mlvtmp\mlv-agent\cache\
from this VM and reported it as contradicting the card, while the GPU host was unreachable.)

## MEASUREMENT DISCIPLINE THAT ALREADY COST THIS BOARD REAL TIME
- EVERY PLAYBACK NUMBER CARRIES ITS CONFIGURATION OR IT CARRIES NOTHING. Legs here have run with
  MLVAPP_PLAYBACK_SCALE_FACTOR=1 while the shipping default is scale 4 (HighQuality), and the
  overrides appear in NO downstream artifact - only in smoke-stdout.txt prose.
- The stage-timing clock was 1 ms until 2026-09-02. Any median-based attribution computed before
  that is biased toward "unattributed"; the mean is the unbiased estimator.
- An empty grep is a statement about the SEARCH, not about the tree.
- A 0% or 100% rate is a STRUCTURAL claim, not an extreme measurement.
- With n=1, report the observation and STOP.

## THE CARD, verbatim from queue.json
$fence json
$cardJson
$fence

## WHAT I NEED
1. State the card's CURRENT truth: is its blocker still real? Run or cite the proving command.
   A blocker repeated without a fresh proving run is not a blocker, it is a memory.
2. If it is actionable, DO THE ANALYSIS and return the decision, with evidence.
3. If it is NOT actionable, say precisely why, and name the party in the format above.
4. Name anything you find that contradicts the card's own text. A card that describes a retired
   mechanism is worse than an empty card.
5. End with a block headed DECISION containing: the card's live status, the single next action,
   and who owns it.
"@

$promptPath = Join-Path $PromptDir ("ws-$cardId-$stamp.md")
[System.IO.File]::WriteAllText($promptPath, $brief, [System.Text.UTF8Encoding]::new($false))

$briefKb   = [math]::Round(($brief.Length / 1KB), 1)
$onTrack   = @($live | Where-Object { (Get-Track $_) -eq $cardTrack }).Count

Write-Output "WORKSTREAM: track=$cardTrack card=$cardId priority=$(Get-Rank $card) state=$(Get-Prop $card 'state')"
Write-Output "WORKSTREAM: lane=$Lane engine=$engine needsShell=$needsShell briefKB=$briefKb"
Write-Output "WORKSTREAM: live cards on this track = $onTrack (live overall = $($live.Count))"
Write-Output "WORKSTREAM: prompt=$promptPath"
if ($needsHostedEvidence) {
    $okCount = @($ghRows | Where-Object { $_.Result -notlike 'FAILED*' -and $_.Result -notlike 'CANNOT-DETERMINE*' }).Count
    Write-Output "WORKSTREAM: gh-evidence $okCount/$($ghRows.Count) export(s) ok -> $(Join-Path $runDir 'github-evidence')"
} else {
    Write-Output 'WORKSTREAM: gh-evidence not-needed (card text names no hosted-evidence subject)'
}

if ($DryRun) {
    # The exports above ALREADY RAN and are on disk. Saying "nothing dispatched" without saying
    # that would be a lie by omission about a directory this command created.
    Write-Output 'WORKSTREAM: DRY RUN - no lane dispatched. Any gh-evidence export above is real and on disk.'
    exit 0
}

& pwsh -NoProfile -NonInteractive -ExecutionPolicy Bypass -File $LaneRunner `
    -Lane $Lane -PromptFile $promptPath -Card $cardId -RunDir $runDir -TimeoutSec $TimeoutSec
$laneExit = $LASTEXITCODE

# Read the lane's SPEND from the receipt it just wrote, so the dispatch log is
# self-sufficient. The loop derives its daily budget from this log in ONE pass over ONE
# file; making that scan open every runDir receipt instead would turn it into N file opens
# and couple the budget to receipt layout. One read here, at the moment the receipt is
# freshly written, is the cheap place to pay for it.
#
# costReported is carried SEPARATELY and is not a convenience: an UNREPORTED cost must
# never be read as free. See the refund rule in Invoke-WorkstreamLoop.ps1.
$laneCostUsd = $null
$laneCostReported = $false
try {
    $rcpt = Get-ChildItem -LiteralPath $runDir -Filter '*.receipt.json' -ErrorAction Stop |
            Sort-Object LastWriteTimeUtc -Descending | Select-Object -First 1
    if ($rcpt) {
        $spend = (Get-Content -LiteralPath $rcpt.FullName -Raw | ConvertFrom-Json).spend
        if ($null -ne $spend) {
            $laneCostReported = [bool]$spend.costReported
            if ($laneCostReported) { $laneCostUsd = [double]$spend.costUsd }
        }
    }
} catch {
    # A missing or unparsable receipt leaves costReported FALSE, which the refund rule
    # treats as "not free". Failing to read the cost must never look like a zero cost.
}

$record = [ordered]@{
    schema          = 'mlv-app/workstream-dispatch/v1'
    cardId          = $cardId
    track           = $cardTrack
    priority        = (Get-Rank $card)
    stateAtDispatch = (Get-Prop $card 'state')
    lane            = $Lane
    engine          = $engine
    needsShell      = [bool]$needsShell
    promptPath      = $promptPath
    promptBytes     = $brief.Length
    runDir          = $runDir
    ghEvidence      = if ($needsHostedEvidence) { @($ghRows | ForEach-Object { "$($_.Name)=$($_.Result)" }) } else { @() }
    dispatchedUtc   = (Get-Date).ToUniversalTime().ToString('o')
    laneExitCode    = $laneExit
    laneCostUsd     = $laneCostUsd
    laneCostReported = $laneCostReported
}
Add-Content -LiteralPath $LogPath -Value ($record | ConvertTo-Json -Compress) -Encoding UTF8

Write-Output ("WORKSTREAM: dispatched, laneExit={0} cost={1} runDir={2}" -f $laneExit, $(if ($laneCostReported) { "USD $laneCostUsd" } else { "unreported" }), $runDir)
exit 0
