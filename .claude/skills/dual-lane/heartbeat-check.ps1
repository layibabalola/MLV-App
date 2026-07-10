# heartbeat-check.ps1  (shared by BOTH lanes)  -- SELF-HEALING, cursor-based, with LIVENESS + deadlock watch.
#
# Three symmetric jobs (param-driven, either lane runs it against the OTHER lane's ledger):
#   1. WAKE     -- the other lane posted SEQ blocks THIS lane has not PROCESSED (maxSeq > my cursor).
#                  Emits the new blocks; re-fires (self-heals a dropped wake) until my cursor advances.
#   2. STALE    -- the other lane has gone DARK: its latest entry is older than ~2x the liveness cadence
#                  WHILE the collaboration is still active (no COLLABORATION_END). This closes the blind
#                  spot the cursor-based WAKE cannot see: a dark lane posts NO new SEQ, so WAKE stays
#                  silent forever even though the peer quit/died (Codex quit ~2026-06-27 after a
#                  mis-signaled "done"; with only WAKE, Claude would have sat idle indefinitely).
#   3. DEADLOCK -- both lanes alive but the other lane has not advanced its cursor past an OPEN item of
#                  MINE for too long: a both-lanes-wait standoff. Nudges so neither side idles "waiting
#                  for the other."
#
# TEARDOWN CONTRACT: a watcher stands down ONLY when an explicit COLLABORATION_END control entry exists
# in EITHER lane. A work-block-done STATUS is NOT teardown -- the collaboration continues and the watcher
# stays alive (see docs/dual-lane-collaboration-protocol.md). So STALE/DEADLOCK are gated on the ABSENCE
# of COLLABORATION_END (the "collab-active" gate), which bounds false positives: the watch only alerts
# while the collaboration is genuinely live and both lanes owe each other a ~30-min liveness heartbeat.
#
# WHY cursor-based WAKE (2026-06-27): the old design advanced a script-owned .claude-marker the instant it
# EMITTED, decoupled from whether the agent received the wake. When wake delivery broke (a dead Monitor / a
# path-with-space invocation failure), a polling copy still advanced the marker, so wakes for SEQ 181..190
# were consumed with the agent never woken (it sat idle ~4h until a human nudge). Keying the wake condition
# off the agent's PROCESSED cursor makes a dropped wake SELF-HEAL: it re-fires every poll (full once, then a
# throttled terse re-nudge) until the agent advances its cursor. The script never advances the cursor.
#
# WHY liveness detection (2026-06-27): the cursor-based WAKE self-heals a DROPPED wake, but it is BLIND to
# the other lane simply going DARK (no new SEQ => nothing to wake on). STALE/DEADLOCK add the missing
# "the peer stopped talking" signal so a quit/dead peer surfaces instead of stalling the channel silently.
param(
    [Parameter(Mandatory = $true)][string]$OtherLaneFile,           # the other lane's ledger (e.g. codex.md)
    [Parameter(Mandatory = $true)][string]$MarkerFile,              # used ONLY for the single-instance lock path
    [string]$SelfLedgerFile = "",                                  # THIS lane's ledger (e.g. claude.md)
    [string]$CursorKey = "LAST_READ_CODEX_SEQ",                    # header key holding MY processed cursor
    [string]$OtherCursorKey = "",                                  # the OTHER lane's cursor-on-ME (default: opposite of CursorKey)
    [int]$LoopSeconds = 0,
    [int]$ReNudgeSeconds = 600,                                    # re-remind cadence for a delivered-but-unprocessed WAKE
    [int]$LivenessCadenceSeconds = 1800,                           # the ~30-min "I am alive" HEARTBEAT contract both lanes owe
    [int]$StaleAfterSeconds = 0,                                   # other lane presumed DARK after this; 0 => 2*LivenessCadence
    [int]$StaleReNudgeSeconds = 0,                                 # re-alert cadence while the other lane stays dark; 0 => LivenessCadence
    [int]$DeadlockAfterSeconds = 0,                                # my OPEN item unread by a LIVE peer this long => nudge; 0 => 2*LivenessCadence
    [switch]$Status                                                # one-shot: print AUTHORITATIVE coordination state (both cursors + gaps + verdict), then exit
)
$ErrorActionPreference = 'Continue'

# Derive thresholds from the liveness cadence so they stay principled and in one place.
if ($StaleAfterSeconds    -le 0) { $StaleAfterSeconds    = 2 * $LivenessCadenceSeconds }
if ($StaleReNudgeSeconds  -le 0) { $StaleReNudgeSeconds  = $LivenessCadenceSeconds }
if ($DeadlockAfterSeconds -le 0) { $DeadlockAfterSeconds = 2 * $LivenessCadenceSeconds }
if (-not $OtherCursorKey) {
    $OtherCursorKey = if ($CursorKey -eq 'LAST_READ_CODEX_SEQ') { 'LAST_READ_CLAUDE_SEQ' } else { 'LAST_READ_CODEX_SEQ' }
}

function Get-MaxSeq([string]$file) {
    if (-not (Test-Path -LiteralPath $file)) { return -1 }
    $c = Get-Content -LiteralPath $file -Raw
    if ([string]::IsNullOrWhiteSpace($c)) { return -1 }
    $m = [regex]::Matches($c, '(?m)^##\s+SEQ\s+(\d+)\b')
    if ($m.Count -eq 0) { return -1 }
    return ($m | ForEach-Object { [int]$_.Groups[1].Value } | Measure-Object -Maximum).Maximum
}
function Get-Cursor([string]$ledger, [string]$key, [string]$fallbackMarker) {
    # Authoritative: the processed cursor in the named ledger's header.
    if ($ledger -and (Test-Path -LiteralPath $ledger)) {
        foreach ($ln in (Get-Content -LiteralPath $ledger -TotalCount 15 -ErrorAction SilentlyContinue)) {
            if ($ln -match ('^\s*' + [regex]::Escape($key) + '\s*:\s*(\d+)')) { return [int]$Matches[1] }
        }
    }
    # Legacy fallback only (a bare numeric marker file); not advanced by this script anymore.
    if ($fallbackMarker -and (Test-Path -LiteralPath $fallbackMarker)) {
        $raw = (Get-Content -LiteralPath $fallbackMarker -Raw -ErrorAction SilentlyContinue)
        if ($raw) { $raw = $raw.Trim(); if ($raw -match '^\d+$') { return [int]$raw } }
    }
    return -1
}
function Emit-NewBlocks([string]$file, [int]$cursor) {
    $c = Get-Content -LiteralPath $file -Raw
    foreach ($b in [regex]::Split($c, '(?m)(?=^##\s+SEQ\s+\d+\b)')) {
        $m = [regex]::Match($b, '(?m)^##\s+SEQ\s+(\d+)\b')
        if ($m.Success -and [int]$m.Groups[1].Value -gt $cursor) { Write-Output ($b.TrimEnd()) }
    }
}
function Convert-ToUtcAge([string]$ts) {
    # ISO8601 (e.g. 2026-06-27T05:05:40Z) -> integer seconds since that instant, or -1 on parse failure.
    if (-not $ts) { return -1 }
    try {
        $styles = [Globalization.DateTimeStyles]::AdjustToUniversal -bor [Globalization.DateTimeStyles]::AssumeUniversal
        $dt = [datetime]::Parse($ts, [Globalization.CultureInfo]::InvariantCulture, $styles)
        return [int]([datetime]::UtcNow - $dt).TotalSeconds
    } catch { return -1 }
}
function Get-LatestEntryMeta([string]$file) {
    # The max-SEQ block's seq/type/age. Age in seconds (-1 if unparseable / no entries).
    $r = [pscustomobject]@{ Seq = -1; Type = ''; AgeSec = -1 }
    if (-not (Test-Path -LiteralPath $file)) { return $r }
    $c = Get-Content -LiteralPath $file -Raw
    if ([string]::IsNullOrWhiteSpace($c)) { return $r }
    $best = -1; $bestType = ''; $bestTs = ''
    # NOTE: the type token may contain '/', spaces, AND hyphens (e.g. "ACK/STATUS", "STATUS / CORRECTED-ROOT-SCOPE",
    # "ACK/BLOCKER/HOLD-GATES"). Match the WHOLE token up to the next '|' ([^|]+?), else hyphen/space-typed entries
    # are skipped and STALE falls back to an older simple-typed entry (false-positive; observed 2026-06-29 keying on
    # SEQ375 while SEQ376-391 with hyphenated types were live).
    foreach ($m in [regex]::Matches($c, '(?m)^##\s+SEQ\s+(\d+)\s*\|\s*([^|]+?)\s*\|\s*(\S+)')) {
        $s = [int]$m.Groups[1].Value
        if ($s -gt $best) { $best = $s; $bestType = $m.Groups[2].Value; $bestTs = $m.Groups[3].Value }
    }
    if ($best -lt 0) { return $r }
    return [pscustomobject]@{ Seq = $best; Type = $bestType; AgeSec = (Convert-ToUtcAge $bestTs) }
}
function Get-LatestOpenMeta([string]$file) {
    # The highest-SEQ block whose body carries `status: OPEN`, with its age. (-1 seq if none.)
    $r = [pscustomobject]@{ Seq = -1; AgeSec = -1 }
    if (-not $file -or -not (Test-Path -LiteralPath $file)) { return $r }
    $c = Get-Content -LiteralPath $file -Raw
    if ([string]::IsNullOrWhiteSpace($c)) { return $r }
    foreach ($b in [regex]::Split($c, '(?m)(?=^##\s+SEQ\s+\d+\b)')) {
        $hm = [regex]::Match($b, '(?m)^##\s+SEQ\s+(\d+)\s*\|\s*[^|]+?\s*\|\s*(\S+)')
        if (-not $hm.Success) { continue }
        if ([regex]::IsMatch($b, '(?m)^\s*status:\s*OPEN\b')) {
            $s = [int]$hm.Groups[1].Value
            if ($s -gt $r.Seq) { $r = [pscustomobject]@{ Seq = $s; AgeSec = (Convert-ToUtcAge $hm.Groups[2].Value) } }
        }
    }
    return $r
}
function Test-CollabEnd([string[]]$files) {
    foreach ($f in $files) {
        if ($f -and (Test-Path -LiteralPath $f)) {
            $c = Get-Content -LiteralPath $f -Raw -ErrorAction SilentlyContinue
            if ($c -and [regex]::IsMatch($c, '(?m)^##\s+SEQ\s+\d+\s*\|\s*COLLABORATION_END\b')) { return $true }
        }
    }
    return $false
}
function Get-State {
    $maxSeq  = Get-MaxSeq $OtherLaneFile
    $cursor  = Get-Cursor $SelfLedgerFile $CursorKey $MarkerFile
    $other   = Get-LatestEntryMeta $OtherLaneFile
    $myOpen  = Get-LatestOpenMeta $SelfLedgerFile
    $ended   = Test-CollabEnd @($OtherLaneFile, $SelfLedgerFile)
    $oCursor = Get-Cursor $OtherLaneFile $OtherCursorKey ""    # the other lane's header cursor-on-ME
    [pscustomobject]@{
        MaxSeq = $maxSeq; Cursor = $cursor; Other = $other; MyOpen = $myOpen
        Ended = $ended; OtherCursorOnMe = $oCursor
    }
}
function Now-Iso { (Get-Date).ToUniversalTime().ToString('o') }
function Min([int]$sec) { [int][math]::Round($sec / 60.0) }
$otherName = [IO.Path]::GetFileName($OtherLaneFile)

function Test-OtherAlive($st) { return ($st.Other.AgeSec -ge 0 -and $st.Other.AgeSec -lt $StaleAfterSeconds) }
function Test-Stale($st) {
    return ((-not $st.Ended) -and ($st.MaxSeq -le $st.Cursor) -and ($st.Other.Seq -ge 0) -and `
            ($st.Other.Type -ne 'COLLABORATION_END') -and ($st.Other.AgeSec -ge $StaleAfterSeconds))
}
function Test-Deadlock($st) {
    return ((-not $st.Ended) -and (Test-OtherAlive $st) -and ($st.MyOpen.Seq -ge 0) -and `
            ($st.OtherCursorOnMe -lt $st.MyOpen.Seq) -and ($st.MyOpen.AgeSec -ge $DeadlockAfterSeconds))
}
function StaleLine($st) {
    "DUAL-LANE STALENESS ALERT @ $(Now-Iso) : $otherName latest entry SEQ $($st.Other.Seq) $($st.Other.Type) is " +
    "$(Min $st.Other.AgeSec)m old (>= $(Min $StaleAfterSeconds)m stale threshold) while the collaboration is ACTIVE " +
    "-- the other lane may have QUIT/died (its watcher is silent). ACT: verify its watcher is alive; post a " +
    "RESUME-REQUEST in your lane; ask the human to restart the other agent (you cannot restart its process). " +
    "Re-alerting every $(Min $StaleReNudgeSeconds)m until it posts a fresh entry or COLLABORATION_END is recorded."
}
function DeadlockLine($st) {
    "DUAL-LANE DEADLOCK WATCH @ $(Now-Iso) : your OPEN SEQ $($st.MyOpen.Seq) has not been read by $otherName " +
    "(its cursor-on-you $($st.OtherCursorOnMe) < $($st.MyOpen.Seq)) for $(Min $st.MyOpen.AgeSec)m, though it posted " +
    "$(Min $st.Other.AgeSec)m ago (alive). Possible both-lanes-wait standoff -- send a direct STATUS nudge or confirm " +
    "it is processing. Re-alerting every $(Min $StaleReNudgeSeconds)m until its cursor advances or the item resolves."
}

if ($Status) {
    # AUTHORITATIVE one-shot coordination state. Run THIS (never a lagging WAKE/heartbeat snapshot) before
    # concluding split / stall / missed-message / idle. It reads the live ledgers NOW; the cursors are truth.
    $st = Get-State
    $selfName = [IO.Path]::GetFileName($SelfLedgerFile)
    $myMax = Get-MaxSeq $SelfLedgerFile
    # Ledger-bloat forcing function: the lane files are re-read every heartbeat, so a big OWN ledger bloats the
    # session. Surface the size + a PRUNE nudge every -Status so it cannot be silently forgotten (it was, once:
    # claude.md reached 108 blocks / 290KB before a prune). Threshold 45 live blocks.
    $selfBlocks = ([regex]::Matches((Get-Content -LiteralPath $SelfLedgerFile -Raw -ErrorAction SilentlyContinue), '(?m)^##\s+SEQ\s+\d+\b')).Count
    $selfKB = [int]((Get-Item -LiteralPath $SelfLedgerFile -ErrorAction SilentlyContinue).Length / 1024)
    $pruneTxt = if ($selfBlocks -gt 45) { "$selfBlocks live blocks / ${selfKB}KB -- BLOATED, PRUNE NOW: archive-ledger.ps1 -LaneFile <$selfName> -OtherLaneFile <$otherName> -Apply" } else { "$selfBlocks live blocks / ${selfKB}KB (ok; prune at >45)" }
    $inGap  = if ($st.MaxSeq -ge 0 -and $st.Cursor -ge 0)      { $st.MaxSeq - $st.Cursor }      else { 0 }
    $outGap = if ($myMax -ge 0 -and $st.OtherCursorOnMe -ge 0) { $myMax - $st.OtherCursorOnMe } else { 0 }
    $inTxt  = if ($inGap  -gt 0) { "$inGap UNREAD by you -- direct-read $otherName, then advance $CursorKey" } else { "in sync (you hold their latest)" }
    $outTxt = if ($outGap -gt 0) { "$outGap of yours not yet read by $otherName" }                          else { "in sync (they hold your latest)" }
    $aliveTxt = "age unknown"
    if (Test-OtherAlive $st) {
        $aliveTxt = "alive"
    }
    elseif ($st.Other.AgeSec -ge 0) {
        $aliveTxt = "STALE $(Min $st.Other.AgeSec)m (>= $(Min $StaleAfterSeconds)m threshold) -- possible dark lane"
    }
    if ($st.Ended) {
        $verdict = "ENDED (COLLABORATION_END recorded)"
    }
    elseif ($inGap -gt 0 -and $outGap -gt 0) {
        $verdict = "BOTH-PENDING -- likely an in-flight race; re-run -Status in a few seconds before concluding anything"
    }
    elseif ($inGap -gt 0) {
        $verdict = "INBOUND-PENDING -- you are behind; direct-read $otherName now"
    }
    elseif ($outGap -gt 0) {
        $verdict = "OUTBOUND-PENDING -- they have not read your latest yet (race or heads-down; NOT a split)"
    }
    elseif (Test-Stale $st) {
        $verdict = "STALE -- peer dark while collab active; verify its watcher, post a RESUME-REQUEST"
    }
    else {
        $verdict = "IN-SYNC -- both lanes hold each other's latest; no split, no missed message"
    }
    Write-Output ("DUAL-LANE COORDINATION STATUS @ $(Now-Iso)")
    Write-Output ("  AUTHORITATIVE live-file read. WAKE/heartbeat notifications and 'no new entries' prose are POINT-IN-TIME snapshots that LAG this -- trust the cursors below, not a snapshot.")
    Write-Output ("  $otherName : max SEQ $($st.MaxSeq) | your cursor ($CursorKey)=$($st.Cursor) -> inbound: $inTxt")
    Write-Output ("  $selfName : max SEQ $myMax | their cursor-on-you ($OtherCursorKey)=$($st.OtherCursorOnMe) -> outbound: $outTxt")
    Write-Output ("  LEDGER (yours): $pruneTxt")
    Write-Output ("  $otherName latest: SEQ $($st.Other.Seq) $($st.Other.Type), $(Min $st.Other.AgeSec)m ago -- $aliveTxt")
    Write-Output ("  VERDICT: $verdict")
    if ($verdict -like 'IN-SYNC*' -or $verdict -like 'OUTBOUND-PENDING*') {
        Write-Output ("  ANTI-PASSIVITY: no NEW inbound is NOT no work. If you have an OPEN assigned task or a clear forward direction (peer's 'forward on X' / the human's ask / a detour that just cleared), RESUME it or post a concrete BLOCKER -- do NOT idle in liveness/ledger mode. A real HOLD must name what it waits on + the release condition.")
    }
    exit 0
}

if ($LoopSeconds -le 0) {
    # One-shot diagnostic: full WAKE blocks if behind, else liveness/deadlock/stand-down assessment.
    $st = Get-State
    if ($st.MaxSeq -gt $st.Cursor) {
        Write-Output ("DUAL-LANE WAKE @ $(Now-Iso) : $otherName has SEQ $($st.MaxSeq) > processed cursor $($st.Cursor)")
        Emit-NewBlocks $OtherLaneFile $st.Cursor
    }
    elseif ($st.Ended) {
        Write-Output ("DUAL-LANE STAND-DOWN @ $(Now-Iso) : COLLABORATION_END recorded; liveness watch idle. This watcher may be stopped.")
    }
    else {
        if (Test-Stale $st)    { Write-Output (StaleLine $st) }
        if (Test-Deadlock $st) { Write-Output (DeadlockLine $st) }
    }
    exit 0
}

# Persistent (Monitor) mode: single-instance lock keyed to this lane's marker (stale PID self-heals).
$lock = "$MarkerFile.lock"
if (Test-Path -LiteralPath $lock) {
    $oldPid = ((Get-Content -LiteralPath $lock -Raw -ErrorAction SilentlyContinue) | Out-String).Trim()
    if ($oldPid -match '^\d+$' -and (Get-Process -Id ([int]$oldPid) -ErrorAction SilentlyContinue)) {
        Write-Output "DUAL-LANE HEARTBEAT: another instance (PID $oldPid) already watches $([IO.Path]::GetFileName($MarkerFile)); this one exits (single-instance)."
        exit 0
    }
}
Set-Content -LiteralPath $lock -Value ([string]$PID) -Encoding ASCII
$lastEmittedMax = -1
$lastWakeEpoch  = 0
$lastStaleEpoch = 0
$lastDeadlockEpoch = 0
$standDownEmitted = $false
try {
    while ($true) {
        $st = Get-State
        $now = [int][double](Get-Date -UFormat %s)
        if ($st.MaxSeq -gt $st.Cursor) {
            # WAKE (self-healing): full emit once per new max, then throttled terse re-nudge until cursor advances.
            if ($st.MaxSeq -ne $lastEmittedMax) {
                Write-Output ("DUAL-LANE WAKE @ $(Now-Iso) : $otherName advanced to SEQ $($st.MaxSeq) (processed cursor $($st.Cursor))")
                Emit-NewBlocks $OtherLaneFile $st.Cursor
                $lastEmittedMax = $st.MaxSeq; $lastWakeEpoch = $now
            }
            elseif (($now - $lastWakeEpoch) -ge $ReNudgeSeconds) {
                Write-Output ("DUAL-LANE RE-NUDGE @ $(Now-Iso) : " + ($st.MaxSeq - $st.Cursor) + " unprocessed $otherName SEQ (cursor $($st.Cursor) < max $($st.MaxSeq)) -- direct-read $otherName and advance $CursorKey.")
                $lastWakeEpoch = $now
            }
            $lastStaleEpoch = 0; $lastDeadlockEpoch = 0   # fresh inbound clears any stale/deadlock dedupe
        }
        elseif ($st.Ended) {
            if (-not $standDownEmitted) {
                Write-Output ("DUAL-LANE STAND-DOWN @ $(Now-Iso) : COLLABORATION_END recorded; liveness watch idle. Stop this Monitor when you are ready.")
                $standDownEmitted = $true
            }
        }
        else {
            $lastEmittedMax = -1
            # STALE: the other lane has gone dark while the collaboration is still active.
            if (Test-Stale $st) {
                if (($now - $lastStaleEpoch) -ge $StaleReNudgeSeconds) { Write-Output (StaleLine $st); $lastStaleEpoch = $now }
            } else { $lastStaleEpoch = 0 }
            # DEADLOCK: the other lane is alive but has not read my OPEN item -- both-lanes-wait standoff.
            if (Test-Deadlock $st) {
                if (($now - $lastDeadlockEpoch) -ge $StaleReNudgeSeconds) { Write-Output (DeadlockLine $st); $lastDeadlockEpoch = $now }
            } else { $lastDeadlockEpoch = 0 }
        }
        Start-Sleep -Seconds $LoopSeconds
    }
}
finally {
    if (Test-Path -LiteralPath $lock) {
        $cur = ((Get-Content -LiteralPath $lock -Raw -ErrorAction SilentlyContinue) | Out-String).Trim()
        if ($cur -eq [string]$PID) { Remove-Item -LiteralPath $lock -Force -ErrorAction SilentlyContinue }
    }
}
