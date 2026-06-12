# Headless playback-artifact detector.
# Parses an MLVApp interactive-trace log (run smoke with -ExtraEnvironment 'MLVAPP_INTERACTIVE_TRACE=1')
# and flags temporal artifacts the single-frame screenshot smoke cannot see:
#   - FLICKER: the presented frame jumping BACKWARD while the seek bar is still ahead (stale older frame)
#   - STALL:   the displayed frame lagging the seek bar (a freeze). The smoke's own window screenshot
#              grab blocks the UI ~2.5 s and would look like a stall, so a max-lag spike adjacent to a
#              gui_smoke.window_screenshot event is DISCOUNTED as a harness artifact.
#   - JITTER:  uneven present cadence (micro-stutter) - measured from present timestamps. This is what
#              "smoothness" actually means; flicker/stall can both be zero and playback still stutter.
#   - FROZEN CONTENT: the displayed BYTES not changing while the playback position advances. Frame
#              numbers / fps / timecode all advance on metadata and cannot see this (the 2026-06-10
#              poisoned-prefetch stuck-frame shipped green on every cadence metric); the
#              draw_frame_ready.present_content hash is the content ground truth. Logs from builds
#              without that event get a NOTE and the check is skipped (no false pass/fail).
# Emits a machine-readable verdict line and a non-zero exit code on FAIL.
param(
    [Parameter(Mandatory = $true)][string]$TraceLog,
    [int]$MaxLagFramesAllowed = 10,        # present lagging the seek bar by >= this many frames = stall
    [int]$MaxBackJumpAllowed  = 2,         # presented frame going backward by > this = flicker
    [double]$MaxHitchFractionAllowed = 0.05, # > this fraction of frames hitching = jittery FAIL
    [int]$HitchFreezeMs = 250,             # any single present interval >= this = a visible freeze
    [int]$MaxPlausibleHitchMs = 2000,      # intervals above this are playback discontinuities (pause/seek/
                                           # settle/end boundary), excluded from cadence stats as long_gaps
    [string]$Session = 'latest'            # 'latest' (default), 'all' (legacy whole-file), or 1-based index.
                                           # The day log appends across launches; inter-launch idle reads as
                                           # a mid-playback freeze unless sessions are segmented.
)
$ErrorActionPreference = 'Stop'
if (-not (Test-Path -LiteralPath $TraceLog)) { Write-Error "trace log not found: $TraceLog"; exit 2 }

$rxBegin = [regex]'draw_frame_ready\.begin .*?display_frame=(\d+) play_checked=(\d+) position=(\d+)'
$rxContent = [regex]'draw_frame_ready\.present_content .*?display_frame=(\d+) play_checked=(\d+) position=(\d+) hash=([0-9a-fA-F]+)'
$rxTs    = [regex]'^\[\d{4}-\d{2}-(\d{2})T(\d{2}):(\d{2}):(\d{2})\.(\d+)Z\]'
function Get-TsMs($m) {
    $d=[int]$m.Groups[1].Value; $h=[int]$m.Groups[2].Value; $mi=[int]$m.Groups[3].Value
    $s=[int]$m.Groups[4].Value; $ms=[int]$m.Groups[5].Value
    return [double]((((($d*24)+$h)*60+$mi)*60+$s)*1000 + $ms)
}

$df = New-Object System.Collections.Generic.List[int]
$pos = New-Object System.Collections.Generic.List[int]
$ts = New-Object System.Collections.Generic.List[double]
$shotTs = New-Object System.Collections.Generic.List[double]
$chPos = New-Object System.Collections.Generic.List[int]
$chHash = New-Object System.Collections.Generic.List[string]
# Every app launch writes run_metadata= as its first log line (CrashForensics::logStartupMetadata,
# called once from main() right after the message handler is installed), so it is a reliable
# session boundary. Record the parallel-list COUNTS at each marker; they become slice indices.
$rxSessionMarker = [regex]'^\[[^\]]+\] \[INFO\] \[0x[0-9a-fA-F]+\] run_metadata=\{'
$sessBoundDf = New-Object System.Collections.Generic.List[int]
$sessBoundCh = New-Object System.Collections.Generic.List[int]
$sessBoundShot = New-Object System.Collections.Generic.List[int]
foreach ($line in [System.IO.File]::ReadLines((Resolve-Path $TraceLog))) {
    $tm = $rxTs.Match($line); $lineMs = if ($tm.Success) { Get-TsMs $tm } else { [double]-1 }
    if ($rxSessionMarker.IsMatch($line)) {
        $sessBoundDf.Add($df.Count); $sessBoundCh.Add($chHash.Count); $sessBoundShot.Add($shotTs.Count)
    }
    if ($line -like '*gui_smoke.window_screenshot*' -and $lineMs -ge 0) { $shotTs.Add($lineMs) }
    $m = $rxBegin.Match($line)
    if ($m.Success -and $m.Groups[2].Value -eq '1') {
        $df.Add([int]$m.Groups[1].Value); $pos.Add([int]$m.Groups[3].Value); $ts.Add($lineMs)
    }
    $cm = $rxContent.Match($line)
    if ($cm.Success -and $cm.Groups[2].Value -eq '1') {
        $chPos.Add([int]$cm.Groups[3].Value); $chHash.Add($cm.Groups[4].Value)
    }
}
# --- Session segmentation ---------------------------------------------------
# The crash-forensics log file is per-DAY and append-mode: multiple launches into
# the same log dir append sessions, and the inter-launch idle time is
# indistinguishable from a mid-playback freeze to the long-gap rule below (the
# round-1 item-3 false FAIL, max_gap_ms=190289). Default: analyze ONLY the
# latest session. -Session all restores the legacy whole-file behavior.
$segStartsDf = New-Object System.Collections.Generic.List[int]
$segStartsCh = New-Object System.Collections.Generic.List[int]
$segStartsShot = New-Object System.Collections.Generic.List[int]
if ($sessBoundDf.Count -eq 0 -or $sessBoundDf[0] -gt 0 -or $sessBoundCh[0] -gt 0 -or $sessBoundShot[0] -gt 0) {
    # Data precedes the first marker (or no marker at all): implicit leading session.
    $segStartsDf.Add(0); $segStartsCh.Add(0); $segStartsShot.Add(0)
}
for ($b = 0; $b -lt $sessBoundDf.Count; $b++) {
    $segStartsDf.Add($sessBoundDf[$b]); $segStartsCh.Add($sessBoundCh[$b]); $segStartsShot.Add($sessBoundShot[$b])
}
$sessionCount = $segStartsDf.Count
$analyzedSession = if ($Session -eq 'all') { 'all' } else { "$sessionCount" }
if ($Session -ne 'all' -and $Session -ne 'latest') {
    $parsedIndex = 0
    if (-not [int]::TryParse($Session, [ref]$parsedIndex) -or $parsedIndex -lt 1 -or $parsedIndex -gt $sessionCount) {
        Write-Error ("invalid -Session '{0}': expected 'latest', 'all', or 1..{1}" -f $Session, $sessionCount); exit 2
    }
    $analyzedSession = "$parsedIndex"
}
if ($Session -ne 'all' -and $sessionCount -gt 1) {
    $segIndex = [int]$analyzedSession
    $sDf = $segStartsDf[$segIndex-1]
    $eDf = if ($segIndex -lt $sessionCount) { $segStartsDf[$segIndex] } else { $df.Count }
    $sCh = $segStartsCh[$segIndex-1]
    $eCh = if ($segIndex -lt $sessionCount) { $segStartsCh[$segIndex] } else { $chHash.Count }
    $sShot = $segStartsShot[$segIndex-1]
    $eShot = if ($segIndex -lt $sessionCount) { $segStartsShot[$segIndex] } else { $shotTs.Count }
    $df = $df.GetRange($sDf, $eDf - $sDf); $pos = $pos.GetRange($sDf, $eDf - $sDf); $ts = $ts.GetRange($sDf, $eDf - $sDf)
    $chPos = $chPos.GetRange($sCh, $eCh - $sCh); $chHash = $chHash.GetRange($sCh, $eCh - $sCh)
    $shotTs = $shotTs.GetRange($sShot, $eShot - $sShot)
    Write-Host ("MULTI-SESSION TRACE: {0} app sessions found in {1}; analyzing session {2} of {3}{4}. Use -Session all for the legacy whole-file behavior." -f `
        $sessionCount, $TraceLog, $segIndex, $sessionCount, $(if($segIndex -eq $sessionCount){' (latest)'}else{''}))
}
elseif ($sessionCount -gt 1) {
    Write-Host ("MULTI-SESSION TRACE: {0} app sessions found in {1}; -Session all requested - analyzing the whole file as one stream (inter-session gaps may read as freezes)." -f $sessionCount, $TraceLog)
}

$n = $df.Count
if ($n -lt 5) { Write-Host ("ARTIFACT-CHECK verdict=no-data (too few playing presents traced) sessions={0} analyzed_session={1}" -f $sessionCount, $analyzedSession); exit 0 }

# lag (stall) + backward (flicker)
$lagSum=0; $maxLag=0; $maxLagAt=-1; $lagGE=0; $backJumps=0; $maxBack=0; $prevDf=-1
for ($i=0; $i -lt $n; $i++) {
    $lag = $pos[$i] - $df[$i]; if ($lag -lt 0) { $lag = 0 }
    $lagSum += $lag; if ($lag -ge $MaxLagFramesAllowed) { $lagGE++ }
    if ($lag -gt $maxLag) { $maxLag = $lag; $maxLagAt = $i }
    if ($prevDf -ge 0 -and ($prevDf - $df[$i]) -gt 0 -and $pos[$i] -ge $prevDf) {
        $back = $prevDf - $df[$i]
        if ($back -gt $maxBack) { $maxBack = $back }
        if ($back -gt $MaxBackJumpAllowed) { $backJumps++ }
    }
    $prevDf = $df[$i]
}
$avgLag = [math]::Round($lagSum / $n, 2)

# jitter: present-to-present intervals (ms). An interval that SPANS a window-screenshot grab is the
# harness freezing the UI ~2.5 s, not a playback stutter, so it is excluded from the cadence stats.
$iv = New-Object System.Collections.Generic.List[double]
$shotIntervals = 0
$longGaps = 0
$maxGap = 0.0
$interiorLongGap = $false
for ($i=1; $i -lt $n; $i++) {
    if ($ts[$i] -ge 0 -and $ts[$i-1] -ge 0) {
        $d = $ts[$i]-$ts[$i-1]
        if ($d -gt 0) {
            $spansShot = $false
            foreach ($st in $shotTs) { if ($st -ge $ts[$i-1] -and $st -le $ts[$i]) { $spansShot = $true; break } }
            if ($spansShot) { $shotIntervals++; continue }
            if ($d -gt $MaxPlausibleHitchMs) {
                # A multi-second gap is a playback discontinuity (pause/seek/settle/end), not a frame hitch.
                # If it sits between real playback on BOTH sides it is a genuine mid-playback freeze (FAIL);
                # at the sequence start/end it is just a boundary and is discounted.
                $longGaps++
                if ($d -gt $maxGap) { $maxGap = $d }
                if ($i -ge 3 -and $i -le ($n - 3)) { $interiorLongGap = $true }
                continue
            }
            $iv.Add($d)
        }
    }
}
$ic = $iv.Count
$median=0; $p90=0; $p99=0; $maxIv=0; $hitches=0; $hitchFrac=0
if ($ic -ge 5) {
    $sorted = ($iv | Sort-Object)
    $median = $sorted[[int]($ic*0.5)]; $p90 = $sorted[[int]($ic*0.9)]; $p99 = $sorted[[int]($ic*0.99)]; $maxIv = $sorted[$ic-1]
    $hitchThresh = [math]::Max(60.0, 2.5*$median)
    foreach ($v in $iv) { if ($v -gt $hitchThresh) { $hitches++ } }
    $hitchFrac = [math]::Round($hitches / $ic, 4)
}

# frozen content: identical displayed-byte hashes across many consecutive presents while the
# playback position advances. Only meaningful on builds that emit present_content; a static
# scene cannot trip it in practice (sensor noise varies the sampled bytes frame to frame).
$contentEvents = $chHash.Count
$distinctHashes = 0
$frozenRuns = 0
$longestFrozenRun = 0
$FrozenRunMinPresents = 10
if ($contentEvents -gt 0) {
    $distinctHashes = ($chHash | Select-Object -Unique).Count
    $runLen = 1
    for ($i = 1; $i -le $contentEvents; $i++) {
        if ($i -lt $contentEvents -and $chHash[$i] -eq $chHash[$i-1]) { $runLen++; continue }
        if ($runLen -ge $FrozenRunMinPresents) {
            $runStart = $i - $runLen
            $posAdvance = $chPos[$i-1] - $chPos[$runStart]
            if ($posAdvance -ge $FrozenRunMinPresents) {
                $frozenRuns++
                if ($runLen -gt $longestFrozenRun) { $longestFrozenRun = $runLen }
            }
        }
        $runLen = 1
    }
}
$frozenContent = ($frozenRuns -gt 0)

# discount a stall that is really the harness window-screenshot grab (blocks UI ~2.5 s)
$stallIsScreenshot = $false
if ($maxLagAt -ge 0 -and $ts[$maxLagAt] -ge 0) {
    foreach ($st in $shotTs) { $dt = $ts[$maxLagAt] - $st; if ($dt -ge -300 -and $dt -le 3500) { $stallIsScreenshot = $true; break } }
}

$realStall = ($maxLag -ge $MaxLagFramesAllowed) -and (-not $stallIsScreenshot)
$flicker   = ($backJumps -gt 0)
$jittery   = ($hitchFrac -gt $MaxHitchFractionAllowed) -or ($maxIv -ge $HitchFreezeMs -and -not $stallIsScreenshot) -or $interiorLongGap
$verdict = if ($realStall -or $flicker -or $jittery -or $frozenContent) { 'FAIL' } else { 'PASS' }
$medFps = if ($median -gt 0) { [math]::Round(1000.0/$median,1) } else { 0 }

Write-Host ("ARTIFACT-CHECK verdict={0} presents={1} median_fps={2} p90_ms={3} p99_ms={4} max_interval_ms={5} hitch_frac={6} flicker_back_jumps={7} max_back_jump={8} max_lag={9} long_gaps={10} max_gap_ms={11} content_events={12} distinct_hashes={13} frozen_content_runs={14} longest_frozen_run={15} sessions={16} analyzed_session={17}{18}" -f `
    $verdict, $n, $medFps, [int]$p90, [int]$p99, [int]$maxIv, $hitchFrac, $backJumps, $maxBack, $maxLag, $longGaps, [int]$maxGap, $contentEvents, $distinctHashes, $frozenRuns, $longestFrozenRun, $sessionCount, $analyzedSession, $(if($stallIsScreenshot){' (max_lag is screenshot-grab, discounted)'}else{''}))
if ($flicker)   { Write-Host ("  FLICKER: presented frame jumped backward by up to {0} frames {1} time(s)" -f $maxBack, $backJumps) }
if ($realStall) { Write-Host ("  STALL: image fell {0} frames behind the seek bar (~{1:N1}s freeze)" -f $maxLag, ($maxLag/24.0)) }
if ($jittery)   { Write-Host ("  JITTER: {0:P1} of frames hitch (interval > 2.5x median); worst {1} ms - visible micro-stutter" -f $hitchFrac, [int]$maxIv) }
if ($frozenContent) { Write-Host ("  FROZEN-CONTENT: displayed bytes identical across {0} consecutive presents while the position advanced - the image is stuck even though cadence looks healthy (validate by pixels)" -f $longestFrozenRun) }
if ($contentEvents -eq 0) { Write-Host "  NOTE: no draw_frame_ready.present_content telemetry in this log (build predates the content hash) - frozen-content check skipped" }
if ($stallIsScreenshot) { Write-Host "  NOTE: max-lag spike coincides with a window-screenshot grab (harness artifact); re-run without -CaptureScreenshot for clean stall detection" }
if ($shotIntervals -gt 0) { Write-Host ("  NOTE: excluded {0} present interval(s) spanning a window-screenshot grab from the cadence stats (harness UI freeze, not playback)" -f $shotIntervals) }
if ($longGaps -gt 0) { Write-Host ("  NOTE: {0} long gap(s) > {1} ms excluded from cadence stats as playback discontinuities (worst {2} ms){3}" -f $longGaps, $MaxPlausibleHitchMs, [int]$maxGap, $(if($interiorLongGap){' - at least one is mid-playback, counted as a freeze (FAIL)'}else{' - sequence boundary only, discounted'})) }
if ($verdict -eq 'FAIL') { exit 1 } else { exit 0 }
