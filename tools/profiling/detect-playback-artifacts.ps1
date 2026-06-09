# Headless playback-artifact detector.
# Parses an MLVApp interactive-trace log (run smoke with -ExtraEnvironment 'MLVAPP_INTERACTIVE_TRACE=1')
# and flags temporal artifacts the single-frame screenshot smoke cannot see:
#   - displayed frame lagging the playback position (image != seek bar) => stalls / desync
#   - the presented frame jumping BACKWARD (flicker to an older / first frame)
# Emits a machine-readable verdict line and a non-zero exit code on FAIL.
param(
    [Parameter(Mandatory = $true)][string]$TraceLog,
    [int]$MaxLagFramesAllowed = 10,     # any present lagging the seek bar by >= this many frames = stall/desync
    [int]$MaxBackJumpAllowed  = 2        # presented frame going backward by > this = flicker to older frame
)
$ErrorActionPreference = 'Stop'
if (-not (Test-Path -LiteralPath $TraceLog)) { Write-Error "trace log not found: $TraceLog"; exit 2 }

$rx = [regex]'draw_frame_ready\.begin .*?display_frame=(\d+) play_checked=(\d+) position=(\d+)'
$df = New-Object System.Collections.Generic.List[int]
$pos = New-Object System.Collections.Generic.List[int]
foreach ($line in [System.IO.File]::ReadLines((Resolve-Path $TraceLog))) {
    $m = $rx.Match($line)
    if ($m.Success -and $m.Groups[2].Value -eq '1') {   # play_checked == 1 (actively playing)
        $df.Add([int]$m.Groups[1].Value); $pos.Add([int]$m.Groups[3].Value)
    }
}
$n = $df.Count
if ($n -eq 0) { Write-Host 'ARTIFACT-CHECK verdict=no-data (no playing presents traced)'; exit 0 }

$lagSum = 0; $maxLag = 0; $maxLagAt = -1; $lagGE = 0; $backJumps = 0; $maxBack = 0; $prevDf = -1
for ($i = 0; $i -lt $n; $i++) {
    $lag = $pos[$i] - $df[$i]
    if ($lag -lt 0) { $lag = 0 }   # display ahead of target is harmless rounding
    $lagSum += $lag
    if ($lag -ge $MaxLagFramesAllowed) { $lagGE++ }
    if ($lag -gt $maxLag) { $maxLag = $lag; $maxLagAt = $i }
    # A backward-moving presented frame is only a FLICKER when the seek bar is still
    # ahead of the previous frame (forward playback presenting a stale older frame).
    # Loop-wrap and scrub move the playback position itself backward, so the position
    # is NOT ahead and those legitimate rewinds are not flagged.
    if ($prevDf -ge 0 -and ($prevDf - $df[$i]) -gt 0 -and $pos[$i] -ge $prevDf) {
        $back = $prevDf - $df[$i]
        if ($back -gt $maxBack) { $maxBack = $back }
        if ($back -gt $MaxBackJumpAllowed) { $backJumps++ }
    }
    $prevDf = $df[$i]
}
$avgLag = [math]::Round($lagSum / $n, 2)
$stall   = ($maxLag -ge $MaxLagFramesAllowed)
$flicker = ($backJumps -gt 0)
$verdict = if ($stall -or $flicker) { 'FAIL' } else { 'PASS' }

Write-Host ("ARTIFACT-CHECK verdict={0} presents={1} avg_lag={2} max_lag={3} stall_presents(>= {4})={5} max_back_jump={6} flicker_back_jumps={7}" -f `
    $verdict, $n, $avgLag, $maxLag, $MaxLagFramesAllowed, $lagGE, $maxBack, $backJumps)
if ($stall)   { Write-Host ("  STALL: image fell {0} frames behind the seek bar (a ~{1:N1}s freeze)" -f $maxLag, ($maxLag/24.0)) }
if ($flicker) { Write-Host ("  FLICKER: presented frame jumped backward by up to {0} frames {1} time(s)" -f $maxBack, $backJumps) }
if ($verdict -eq 'FAIL') { exit 1 } else { exit 0 }
