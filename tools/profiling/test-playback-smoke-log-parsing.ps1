# test-playback-smoke-log-parsing.ps1
# CUDA-ATTRIBUTION-BASELINE-1 round 4: regression test for
# Select-PlaybackSmokeRecordLine (playback-smoke-log-parsing.ps1). Reproduces
# astra's round-3 finding verbatim: a two-record log where
# playback_smoke.source_frame_population's own population_basis prose names
# its companion playback_smoke.frame_population field. A bare
# "*playback_smoke.frame_population*" wildcard matches BOTH lines and, with
# -Last 1, silently resolves to the prose line -- losing loop_wrap_count and
# reviving the equal-endpoint false "stuck" failure this field exists to
# prevent (run-release-gui-smoke.ps1:1158 pre-fix). This test fails if that
# collision comes back.

param([string]$RepoRoot = ".")

$ErrorActionPreference = "Stop"
$root = (Resolve-Path -LiteralPath $RepoRoot).Path
. (Join-Path $root "tools\profiling\playback-smoke-log-parsing.ps1")

$failures = @()

function Assert-Equal {
    param($Expected, $Actual, [string]$Message)
    if ($Expected -ne $Actual) {
        $script:failures += "$Message (expected '$Expected', got '$Actual')"
    }
}

# Verbatim two-record shape emitted by MainWindow::finishPlaybackSmokeTelemetry:
# the frame_population record (with a real, nonzero loop_wrap_count) followed
# by the source_frame_population record, whose population_basis prose names
# "playback_smoke.frame_population" without a trailing " session=".
$frameLine = 'playback_smoke.frame_population session=1 requested_frames_by_serial=60 presented_frames=20 skipped_or_unpresented_frames_by_serial=40 skipped_or_unpresented_frames_by_timeline_position=40 loop_wrap_count=40 timeline_position_basis_sound=0 requested_target_frames_by_serial=20 skipped_or_unpresented_frames_by_target_serial=0 lookahead_requests_by_serial=40 population_basis="requested_target_frames_by_serial is the authoritative population"'
$sourceLine = 'playback_smoke.source_frame_population session=1 offered_source_frames=60 never_requested_source_frames=40 requested_then_discarded_lookahead_frames=0 requested_then_skipped_target_frames=0 presented_via_target_frames=0 presented_via_lookahead_frames=20 presented_frames=20 partition_sound=1 source_frame_loss_ratio=0.666667 population_basis="...companion playback_smoke.frame_population -- this is the fix for the source-frame loss those figures cannot see"'
$lines = @($frameLine, $sourceLine)

# Sanity check on the fixture itself: prove the collision this test guards
# against is real, i.e. a bare wildcard DOES match both lines (so -Last 1
# would pick the prose line, not the record).
$bareWildcardMatches = @($lines | Where-Object { $_ -like "*playback_smoke.frame_population*" })
Assert-Equal 2 $bareWildcardMatches.Count `
    "fixture sanity: bare wildcard should match both the record and the prose reference"

$selectedFrameLine = Select-PlaybackSmokeRecordLine -Lines $lines -FieldName "playback_smoke.frame_population"
Assert-Equal $frameLine $selectedFrameLine `
    "Select-PlaybackSmokeRecordLine must select the frame_population RECORD, not the source_frame_population line naming it in prose"

$framePopulation = Convert-PlaybackLogLineToObject $selectedFrameLine
Assert-Equal 40 $framePopulation.loop_wrap_count `
    "loop_wrap_count must survive selection (round-3 regression: this came back `$null)"
Assert-Equal 20 $framePopulation.requested_target_frames_by_serial `
    "requested_target_frames_by_serial must survive selection"

$selectedSourceLine = Select-PlaybackSmokeRecordLine -Lines $lines -FieldName "playback_smoke.source_frame_population"
Assert-Equal $sourceLine $selectedSourceLine `
    "Select-PlaybackSmokeRecordLine must select the source_frame_population record"

$sourcePopulation = Convert-PlaybackLogLineToObject $selectedSourceLine
Assert-Equal 40 $sourcePopulation.never_requested_source_frames `
    "never_requested_source_frames must survive selection"

# Order independence: put the prose-bearing line first, to prove the fix does
# not depend on emission order (only -Last 1 luck did).
$reorderedLines = @($sourceLine, $frameLine)
$selectedFromReordered = Select-PlaybackSmokeRecordLine -Lines $reorderedLines -FieldName "playback_smoke.frame_population"
Assert-Equal $frameLine $selectedFromReordered `
    "selection must not depend on record emission order"

if ($failures.Count -gt 0) {
    foreach ($failure in $failures) {
        Write-Host "[FAIL] $failure"
    }
    throw "$($failures.Count) playback-smoke-log-parsing test(s) failed."
}

Write-Host "[SUMMARY] playback-smoke-log-parsing tests=6 failed=0"
