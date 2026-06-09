# Headless playback-artifact detector.
# Parses an MLVApp interactive-trace log (run smoke with -ExtraEnvironment 'MLVAPP_INTERACTIVE_TRACE=1')
# and flags temporal artifacts the single-frame screenshot smoke cannot see:
#   - FLICKER: the presented frame jumping BACKWARD while the seek bar is still ahead (stale older frame)
#   - STALL:   the displayed frame lagging the seek bar (a freeze). The smoke's own window screenshot
#              grab blocks the UI ~2.5 s and would look like a stall, so a max-lag spike adjacent to a
#              gui_smoke.window_screenshot event is DISCOUNTED as a harness artifact.
#   - JITTER:  uneven present cadence (micro-stutter) - measured from present timestamps. This is what
#              "smoothness" actually means; flicker/stall can both be zero and playback still stutter.
# Emits a machine-readable verdict line and a non-zero exit code on FAIL.
param(
    [Parameter(Mandatory = $true)][string]$TraceLog,
    [int]$MaxLagFramesAllowed = 10,        # present lagging the seek bar by >= this many frames = stall
    [int]$MaxBackJumpAllowed  = 2,         # presented frame going backward by > this = flicker
    [double]$MaxHitchFractionAllowed = 0.05, # > this fraction of frames hitching = jittery FAIL
    [int]$HitchFreezeMs = 250              # any single present interval >= this = a visible freeze
)
$ErrorActionPreference = 'Stop'
if (-not (Test-Path -LiteralPath $TraceLog)) { Write-Error "trace log not found: $TraceLog"; exit 2 }

$rxBegin = [regex]'draw_frame_ready\.begin .*?display_frame=(\d+) play_checked=(\d+) position=(\d+)'
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
foreach ($line in [System.IO.File]::ReadLines((Resolve-Path $TraceLog))) {
    $tm = $rxTs.Match($line); $lineMs = if ($tm.Success) { Get-TsMs $tm } else { [double]-1 }
    if ($line -like '*gui_smoke.window_screenshot*' -and $lineMs -ge 0) { $shotTs.Add($lineMs) }
    $m = $rxBegin.Match($line)
    if ($m.Success -and $m.Groups[2].Value -eq '1') {
        $df.Add([int]$m.Groups[1].Value); $pos.Add([int]$m.Groups[3].Value); $ts.Add($lineMs)
    }
}
$n = $df.Count
if ($n -lt 5) { Write-Host 'ARTIFACT-CHECK verdict=no-data (too few playing presents traced)'; exit 0 }

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
for ($i=1; $i -lt $n; $i++) {
    if ($ts[$i] -ge 0 -and $ts[$i-1] -ge 0) {
        $d = $ts[$i]-$ts[$i-1]
        if ($d -gt 0) {
            $spansShot = $false
            foreach ($st in $shotTs) { if ($st -ge $ts[$i-1] -and $st -le $ts[$i]) { $spansShot = $true; break } }
            if ($spansShot) { $shotIntervals++ } else { $iv.Add($d) }
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

# discount a stall that is really the harness window-screenshot grab (blocks UI ~2.5 s)
$stallIsScreenshot = $false
if ($maxLagAt -ge 0 -and $ts[$maxLagAt] -ge 0) {
    foreach ($st in $shotTs) { $dt = $ts[$maxLagAt] - $st; if ($dt -ge -300 -and $dt -le 3500) { $stallIsScreenshot = $true; break } }
}

$realStall = ($maxLag -ge $MaxLagFramesAllowed) -and (-not $stallIsScreenshot)
$flicker   = ($backJumps -gt 0)
$jittery   = ($hitchFrac -gt $MaxHitchFractionAllowed) -or ($maxIv -ge $HitchFreezeMs -and -not $stallIsScreenshot)
$verdict = if ($realStall -or $flicker -or $jittery) { 'FAIL' } else { 'PASS' }
$medFps = if ($median -gt 0) { [math]::Round(1000.0/$median,1) } else { 0 }

Write-Host ("ARTIFACT-CHECK verdict={0} presents={1} median_fps={2} p90_ms={3} p99_ms={4} max_interval_ms={5} hitch_frac={6} flicker_back_jumps={7} max_back_jump={8} max_lag={9}{10}" -f `
    $verdict, $n, $medFps, [int]$p90, [int]$p99, [int]$maxIv, $hitchFrac, $backJumps, $maxBack, $maxLag, $(if($stallIsScreenshot){' (max_lag is screenshot-grab, discounted)'}else{''}))
if ($flicker)   { Write-Host ("  FLICKER: presented frame jumped backward by up to {0} frames {1} time(s)" -f $maxBack, $backJumps) }
if ($realStall) { Write-Host ("  STALL: image fell {0} frames behind the seek bar (~{1:N1}s freeze)" -f $maxLag, ($maxLag/24.0)) }
if ($jittery)   { Write-Host ("  JITTER: {0:P1} of frames hitch (interval > 2.5x median); worst {1} ms - visible micro-stutter" -f $hitchFrac, [int]$maxIv) }
if ($stallIsScreenshot) { Write-Host "  NOTE: max-lag spike coincides with a window-screenshot grab (harness artifact); re-run without -CaptureScreenshot for clean stall detection" }
if ($shotIntervals -gt 0) { Write-Host ("  NOTE: excluded {0} present interval(s) spanning a window-screenshot grab from the cadence stats (harness UI freeze, not playback)" -f $shotIntervals) }
if ($verdict -eq 'FAIL') { exit 1 } else { exit 0 }
