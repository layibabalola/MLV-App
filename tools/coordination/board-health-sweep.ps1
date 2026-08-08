# Board lane-health sweep. Writes the health.log line every liveness claim on this board
# reads. Adapted from the AudioMile LANE-HEALTH FRAMEWORK. ASCII only.
#
# PROMOTED FROM `.claude-state/coordination/dual-lane/health/sweep-lane-health.ps1` under
# SWEEP-CLASS-1 (Layi authorised 2026-08-08). The original header declared the intent -
# "Tier-A lane-health sweep (untracked; Tier-B promotes a reviewed copy to tools/)" - but
# the promotion had never happened, so the board's most-read instrument lived in a
# gitignored path where `owner-of.ps1` returns `unknown`, `lane-commit` hard-blocks it, and
# no gated range could contain it. It could not be reviewed, only edited.
#
# It is deliberately NOT named `sweep-lane-health.ps1`: that name belongs to the
# machine-readable JSON sweep in this directory, which has its own consumers
# (`run-lane-heartbeat.ps1`, the AGENTS.md contract, `tests/coordination/test_lane_health_contract.py`)
# and a different output format. Two different instruments shared one filename in two
# directories, which is how the card's own line numbers came to point at a file the
# implementer would not have been editing. Both now read ONE class list.
param(
    [string]$HealthDir = (Join-Path (Split-Path (Split-Path $PSScriptRoot -Parent) -Parent) '.claude-state\coordination\dual-lane\health'),
    [switch]$Quiet
)
$ErrorActionPreference = 'SilentlyContinue'
$dualLane = Split-Path $HealthDir -Parent
$now = [DateTime]::UtcNow
$registryPath = Join-Path $dualLane 'seat-registry.json'

# SWEEP-CLASS-1 (a): one tracked authority for the class list and the DARK boundary.
$classes = Import-PowerShellDataFile -LiteralPath (Join-Path $PSScriptRoot 'lane-health-classes.psd1')
$darkCap = [double]$classes.DarkThresholdCapMinutes
$darkGrace = [double]$classes.DarkThresholdGraceMinutes
$hbFresh = [double]$classes.WatchHeartbeatFreshMinutes

$registryState = 'MISSING'
$registry = $null
if (Test-Path -LiteralPath $registryPath -PathType Leaf) {
    try {
        $registry = Get-Content -LiteralPath $registryPath -Raw | ConvertFrom-Json
        if (-not $registry.seats) { throw 'seat-registry.json has no seats object' }
        $registryState = 'VALID'
    } catch {
        $registryState = 'UNPARSEABLE'
    }
}

# lane -> journal file (last-write age is the comms proxy)
$journals = [ordered]@{
    'fable'         = Join-Path $dualLane 'fable.md'
    'codex'         = Join-Path $dualLane 'codex.md'
    'opus'          = Join-Path $dualLane 'opus.md'
    'sol'           = Join-Path $dualLane 'sol.md'
    'claude-review' = Join-Path (Split-Path $dualLane -Parent) 'gpu-lane-impl-review-sync.md'
}
$seatKeys = @{
    'fable' = 'fable'
    'codex' = 'codex'
    'opus' = 'opus'
    'sol' = 'sol'
    'claude-review' = 'claude'
}

$laneStates = @()
$journalAges = @{}
$laneGaps = @()          # SWEEP-CLASS-1 (b), cadence-keyed inter-renewal gaps
$leaseAges = @{}         # SWEEP-CLASS-1 (d), so watchhb can be read against lease age
foreach ($lane in $journals.Keys) {
    $leasePath = Join-Path $HealthDir "leases\$lane.json"
    $seatKey = $seatKeys[$lane]
    $seat = if ($registryState -eq 'VALID') { $registry.seats.$seatKey } else { $null }
    $registered = $null -ne $seat
    $expectedSession = if ($registered) { [string]$seat.session } else { $null }
    $state = if ($registered) { 'MISSING' } else { 'PENDING-ADOPTION' }
    $leaseAgeMin = $null
    $leaseSession = $null
    $declaredDormant = $false
    if (Test-Path $leasePath) {
        try {
            $lease = Get-Content $leasePath -Raw | ConvertFrom-Json
            if ([string]$lease.lane -ne $lane) { throw 'lane field mismatch' }
            $leaseSession = [string]$lease.session
            $renewed = [DateTime]::Parse($lease.renewed, $null, 'AdjustToUniversal')
            $leaseAgeMin = [math]::Round(($now - $renewed).TotalMinutes, 1)
            $cad = [double]$lease.leaseMinutes
            if ($cad -le 0) { throw 'leaseMinutes must be positive' }
            $note = [string]$lease.note
            $declaredDormant = $note -match '(?i)\bDORMANT-BY-BLOCKER\b|\bSEATED-UNLEASED\b|\bdeclared\b.{0,100}\b(?:dormant|dormancy)\b'
            # SWEEP-CLASS-1 (a): ONE boundary, from the shared class file. EXPIRED is
            # deleted - it existed only to name the interval between two boundary
            # functions that agreed at exactly declared=20 and disagreed everywhere else.
            $darkThreshold = [math]::Min($cad, $darkCap) + $darkGrace
            if (-not $registered) { $state = 'UNREGISTERED-LEASE' }
            elseif ($leaseSession -ne $expectedSession) { $state = 'DISPLACED' }
            elseif ($leaseAgeMin -le $darkThreshold) { $state = 'LIVE' }
            elseif ($declaredDormant) { $state = 'SEATED-UNLEASED' }
            else { $state = 'DARK' }
            # SWEEP-CLASS-1 (b): CADENCE-keyed inter-renewal gap. Deleting EXPIRED removes
            # the board's only signal that a lane had missed its OWN declared renewal
            # cadence while still being LIVE by the board threshold - that visibility was
            # accidental (a side effect of the second boundary) and is restored here
            # deliberately, keyed to cadence rather than to a second liveness rule. This is
            # the H3 instrument: it measures cadence, and it is NEVER a liveness verdict.
            if ($state -eq 'LIVE' -and $leaseAgeMin -gt $cad) {
                $laneGaps += ('gap:{0}=OVER({1}m/{2}m)' -f $lane, $leaseAgeMin, $cad)
            }
        } catch { $state = 'LEASE-UNPARSEABLE' }
    }
    $jAgeMin = $null
    $jp = $journals[$lane]
    if (Test-Path $jp) {
        $jAgeMin = [math]::Round(($now - (Get-Item $jp).LastWriteTimeUtc).TotalMinutes, 0)
    }
    $journalAges[$lane] = $jAgeMin
    $leaseAges[$lane] = @{ ageMin = $leaseAgeMin; state = $state }
    $laneStates += [pscustomobject]@{
        lane = $lane
        state = $state
        leaseAgeMin = $leaseAgeMin
        journalAgeMin = $jAgeMin
        registered = $registered
        expectedSession = $expectedSession
        leaseSession = $leaseSession
        declaredDormant = $declaredDormant
    }
}

# lead watcher processes (fable's heartbeat-check instances, marker match)
$watchers = 0
try {
    $watchers = @(Get-CimInstance Win32_Process -Filter "Name='pwsh.exe'" |
        Where-Object { $_.CommandLine -match 'heartbeat-check\.ps1' -and $_.CommandLine -match '\.fable-' }).Count
} catch {}

# overall: declared seated-unleased is intentional; mismatched or undeclared stale seats degrade.
$adopted = $laneStates | Where-Object { $_.registered }
$anyDark = [bool]($adopted | Where-Object { $_.state -in @('MISSING','DARK','DISPLACED','LEASE-UNPARSEABLE') })
$freshest = ($journalAges.Values | Where-Object { $_ -ne $null } | Measure-Object -Minimum).Minimum
$silentWhileOthersMove = [bool]($laneStates | Where-Object {
    $_.journalAgeMin -gt 90 -and
    $freshest -lt 30 -and
    $_.state -notin @('LIVE','PENDING-ADOPTION','SEATED-UNLEASED')
})
# EXPIRED is gone, so ATTENTION is now driven by the CADENCE signal that replaced it
# (SWEEP-CLASS-1 b). This keeps OVERALL a three-state reading rather than collapsing it to
# HEALTHY/DEGRADED: a lane still LIVE by the board threshold but past its own declared
# renewal cadence is exactly the condition EXPIRED used to surface, and it is worth
# attention without being a liveness failure. LEASE-UNPARSEABLE already degrades via
# $anyDark, so it is not double-counted here.
$anyCadenceGap = [bool]($laneGaps.Count -gt 0)

$overall = 'HEALTHY'
if ($registryState -ne 'VALID' -or $anyDark -or $silentWhileOthersMove) { $overall = 'DEGRADED' }
elseif ($anyCadenceGap) { $overall = 'ATTENTION' }

$parts = $laneStates | ForEach-Object {
    $la = if ($null -ne $_.leaseAgeMin) { "$($_.leaseAgeMin)m" } else { '-' }
    $ja = if ($null -ne $_.journalAgeMin) { "$($_.journalAgeMin)m" } else { '-' }
    "$($_.lane)=$($_.state)(lease:$la,journal:$ja)"
}
# CONSULT-1 consumer arm (fable SEQ 975): inbound-watch heartbeats are telemetry until the
# sole liveness authority CONSUMES them (sol SEQ 212). Additive tokens only; OK<=5m else STALE.
# Identity check (claude SEQ 333 / fable SEQ 976): the body's session= must match the registry
# seat for the lane, else FOREIGN - a filename is an identity CLAIM, not identity (SEQ 812 shape).
# STALE + lapsed lease + dormant transcript = coherent DEAD-SEAT signature, not a watch incident.
$watchHb = @()
try {
    Get-ChildItem -LiteralPath $HealthDir -Force -File -ErrorAction SilentlyContinue |
        Where-Object { $_.Name -like '.*-inbound-watch-heartbeat' } | ForEach-Object {
            $hbAge = [math]::Round(($now - $_.LastWriteTimeUtc).TotalMinutes, 1)
            $hbId = $_.Name -replace '^\.', '' -replace '-inbound-watch-heartbeat$', ''
            $hbState = if ($hbAge -le 5) { 'OK' } else { 'STALE' }
            $hbBody = ''
            try { $hbBody = Get-Content -LiteralPath $_.FullName -Raw -ErrorAction SilentlyContinue } catch {}
            $hbSession = [regex]::Match([string]$hbBody, 'session=([0-9a-fA-F-]{36})').Groups[1].Value
            $hbSeatKey = if ($seatKeys.ContainsKey($hbId)) { $seatKeys[$hbId] } else { $hbId }
            $hbRegistered = if ($registryState -eq 'VALID') { [string]$registry.seats.$hbSeatKey.session } else { $null }
            # Three-state identity (claude SEQ 334 / fable SEQ 977): UNKNOWN and UNATTRIBUTED are
            # distinct from OK - a green token that also means "could not tell" is worth less than
            # no token. UNKNOWN first: an unresolvable lane id makes the comparison meaningless.
            if (-not $hbRegistered)            { $hbState = 'UNKNOWN' }
            elseif (-not $hbSession)           { $hbState = 'UNATTRIBUTED' }
            elseif ($hbSession -ne $hbRegistered) { $hbState = 'FOREIGN' }
            # SWEEP-CLASS-1 (d): watchhb and lease age are read as a PAIR, not as two
            # independent tokens a human has to correlate by eye. A STALE watch beside a
            # DARK lease is a coherent DEAD-SEAT signature; a STALE watch beside a LIVE
            # lease is a watch incident on a living seat. Those demand opposite responses,
            # and reading either token alone cannot tell them apart - which is exactly how
            # a dead watch stayed indistinguishable from "nothing happened".
            $pairSuffix = ''
            if ($leaseAges.ContainsKey($hbId)) {
                $pairLease = $leaseAges[$hbId]
                if ($hbState -in @('STALE','UNKNOWN','UNATTRIBUTED','FOREIGN')) {
                    if ($pairLease.state -eq 'DARK') { $pairSuffix = ',pair=DEAD-SEAT' }
                    elseif ($pairLease.state -eq 'LIVE') { $pairSuffix = ',pair=WATCH-ONLY' }
                    else { $pairSuffix = (',pair={0}' -f $pairLease.state) }
                } elseif ($pairLease.state -eq 'DARK') {
                    # Fresh watch, dark lease: the seat is armed but not renewing. That is
                    # a seat that must TAKE A TURN, not a watch that must be re-armed.
                    $pairSuffix = ',pair=UNRENEWED'
                }
            }
            $watchHb += ('watchhb:{0}={1}({2}m{3})' -f $hbId, $hbState, $hbAge, $pairSuffix)
        }
} catch {}
# Codex delivery-watcher pair-link check (claude SEQ 342 / fable SEQ 993): the delivery watcher
# is codex's sole automated wake path since the poll retirement (01:02:45Z), and a process CENSUS
# cannot tell "no watcher" from "a watcher I failed to see" (fable SEQ 983's ~48h false-OK).
# This checks the exact proven failure shape: engine alive + wrapper parent DEAD = ORPHANED
# (wakes go nowhere while the lock blocks replacements). Fail-closed: errors emit UNKNOWN.
# Multi-instance: evaluate ALL matching engines and report the WORST state (claude SEQ 343 /
# fable SEQ 994) - a restart that leaves an orphan beside a healthy pair must read ORPHANED,
# because the orphan still holds .codex-marker.lock and wakes still go nowhere.
try {
    $cdEngines = @(Get-CimInstance Win32_Process -Filter "Name='pwsh.exe'" -ErrorAction Stop |
        Where-Object { $_.CommandLine -match 'heartbeat-check\.ps1' -and $_.CommandLine -match '\.codex-marker' })
    if ($cdEngines.Count -eq 0) { $watchHb += 'watchhb:codex-delivery=DEAD' }
    else {
        $cdOrphans = 0
        foreach ($cdEngine in $cdEngines) {
            $cdWrap = Get-CimInstance Win32_Process -Filter "ProcessId=$($cdEngine.ParentProcessId)" -ErrorAction SilentlyContinue
            if (-not ($cdWrap -and $cdWrap.CommandLine -match 'codex-delivery-watcher\.ps1')) { $cdOrphans++ }
        }
        $cdState = if ($cdOrphans -gt 0) { 'ORPHANED' } else { 'OK' }
        $cdSuffix = if ($cdEngines.Count -gt 1) { "($($cdEngines.Count)inst,$cdOrphans orph)" } else { '' }
        $watchHb += ('watchhb:codex-delivery={0}{1}' -f $cdState, $cdSuffix)
    }
} catch { $watchHb += 'watchhb:codex-delivery=UNKNOWN' }
# Internal nudge consumer (Layi directive 2026-07-29 / fable SEQ 997): rotation nudgers write
# health/.<lane>-nudge instead of cross-session UI messages (retired after a cross-project
# misdelivery). An unanswered nudge is surfaced here until the lane deletes the file.
try {
    Get-ChildItem -LiteralPath $HealthDir -Force -File -ErrorAction SilentlyContinue |
        Where-Object { $_.Name -match '^\.([a-z0-9-]+)-nudge$' } | ForEach-Object {
            $ndAge = [math]::Round(($now - $_.LastWriteTimeUtc).TotalMinutes, 1)
            $ndId = $_.Name -replace '^\.', '' -replace '-nudge$', ''
            $watchHb += ('nudge:{0}=PENDING({1}m)' -f $ndId, $ndAge)
        }
} catch {}
# Queue-vs-dispatch check (claude SEQ 345/346 / fable SEQ 997/998): "ALIVE BUT NOT DISPATCHING
# WHILE WORK IS QUEUED" was folded into the benign state for ~12.5h on 2026-07-29. The queue is
# now MACHINE-READABLE (../queue.json, hub-maintained, single authority - the prose queue in
# RESUME could not be counted by any instrument). Items with state "queued" are undispatched;
# any such item while fable.md has not advanced in >120m emits dispatch:fable=STALLED. A dead
# hub leaves the last state standing, so the alarm fails toward firing.
try {
    $qPath = Join-Path $dualLane 'queue.json'
    if (Test-Path -LiteralPath $qPath) {
        $qRaw = Get-Content -LiteralPath $qPath -Raw -ErrorAction SilentlyContinue
        $qOpen = ([regex]::Matches([string]$qRaw, '"state"\s*:\s*"queued')).Count
        $fableAge = $journalAges['fable']
        if (($qOpen -gt 0) -and ($null -ne $fableAge) -and ($fableAge -gt 120)) {
            $watchHb += ('dispatch:fable=STALLED(open={0},penAge={1}m)' -f $qOpen, $fableAge)
        }
    }
} catch {}
# SWEEP-CLASS-1 (c): CHECKPOINT-IDENTITY-1 detector. Each lane refreshes its own
# `<lane>-resume-CURRENT.md` in the same tool-call block as its lease renewal, so a fresh
# lease beside a checkpoint naming a DIFFERENT session means the checkpoint belongs to a
# predecessor - a successor would resume from another seat's state believing it was its
# own. The failure is silent by construction: both files exist and both look current.
# Emits OK / DRIFT / UNATTRIBUTED / MISSING; fail-closed, never a liveness verdict.
try {
    foreach ($ckLane in @('fable', 'codex')) {
        $ckPath = Join-Path $dualLane ("{0}-resume-CURRENT.md" -f $ckLane)
        if (-not (Test-Path -LiteralPath $ckPath)) {
            $watchHb += ('ckid:{0}=MISSING' -f $ckLane)
            continue
        }
        $ckAge = [math]::Round(($now - (Get-Item -LiteralPath $ckPath).LastWriteTimeUtc).TotalMinutes, 0)
        $ckSeatKey = if ($seatKeys.ContainsKey($ckLane)) { $seatKeys[$ckLane] } else { $ckLane }
        $ckExpected = if ($registryState -eq 'VALID') { [string]$registry.seats.$ckSeatKey.session } else { '' }
        $ckBody = ''
        try { $ckBody = Get-Content -LiteralPath $ckPath -Raw -ErrorAction SilentlyContinue } catch {}
        $ckFound = [regex]::Match([string]$ckBody, '[0-9a-fA-F]{8}-[0-9a-fA-F]{4}-[0-9a-fA-F]{4}-[0-9a-fA-F]{4}-[0-9a-fA-F]{12}').Value
        if (-not $ckExpected) { $ckState = 'UNKNOWN' }
        elseif (-not $ckFound) { $ckState = 'UNATTRIBUTED' }
        elseif ($ckFound -ieq $ckExpected) { $ckState = 'OK' }
        else { $ckState = 'DRIFT' }
        $watchHb += ('ckid:{0}={1}({2}m)' -f $ckLane, $ckState, $ckAge)
    }
} catch {}
if ($laneGaps.Count -gt 0) { $watchHb += $laneGaps }
$hbSuffix = if ($watchHb.Count -gt 0) { ' ' + ($watchHb -join ' ') } else { '' }
$line = "$($now.ToString('yyyy-MM-ddTHH:mm:ssZ')) OVERALL=$overall registry=$registryState watchers=$watchers " + ($parts -join ' ') + $hbSuffix
Add-Content -Path (Join-Path $HealthDir 'health.log') -Value $line -Encoding utf8
if (-not $Quiet) { Write-Output $line }
exit 0
