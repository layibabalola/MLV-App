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

# CUDA-ATTRIBUTION-BASELINE-1 round 7 (astra major, "the telemetry-off
# session reports a CONFIDENT 100% loss"): a telemetry-off record's
# source_frame_loss_ratio is the literal token "unmeasured", not a number --
# Convert-PlaybackLogLineToObject must store it as a string (its numeric
# TryParse fallback path), not silently coerce or drop it, so the consumer's
# guard against reading a fake ratio actually has something to check.
$unmeasuredLine = 'playback_smoke.source_frame_population session=2 offered_source_frames=60 never_requested_source_frames=0 requested_then_discarded_lookahead_frames=0 requested_then_skipped_target_frames=0 presented_via_target_frames=0 presented_via_lookahead_frames=0 presented_frames=0 partition_sound=0 source_frame_loss_ratio=unmeasured source_frame_attribution_measured=0 population_basis="telemetry was off for this session"'
$unmeasuredPopulation = Convert-PlaybackLogLineToObject $unmeasuredLine
Assert-Equal "unmeasured" $unmeasuredPopulation.source_frame_loss_ratio `
    "source_frame_loss_ratio must parse as the literal string 'unmeasured', not be dropped or coerced to a number"
Assert-Equal 0 $unmeasuredPopulation.source_frame_attribution_measured `
    "source_frame_attribution_measured must survive parsing"
Assert-Equal 0 $unmeasuredPopulation.partition_sound `
    "partition_sound must be false on an unmeasured record"

# CUDA-ATTRIBUTION-BASELINE-1 round 9 (astra MAJOR, "request-based
# accounting fails OPEN when the source-frame population record is
# missing"): Get-SourceFrameAttributionValidationFailures replaces
# run-release-gui-smoke.ps1's old `if ($null -ne $sourceFramePopulation)`
# gate, which skipped validation entirely -- and silently fell back to a
# weaker request-based ratio -- whenever the record was absent, even with
# -FrameTelemetry requested. astra's repro: 60 offered, 20 presented, 0
# request-based skips; with the record present the gate reports 66.7% loss
# and fails; with the record simply missing, the old code produced 0
# failures. These six cases are the full state matrix: present+sound,
# present+unsound (a real bucket-arithmetic bug), present+unmeasured (both
# with and without telemetry requested), and missing (both with and
# without telemetry requested) -- the last of which is the round-9 fix.
$soundResult = @(Get-SourceFrameAttributionValidationFailures `
    -SourceFramePopulation ([pscustomobject]@{ partition_sound = 1 }) `
    -AttributionMeasured $true -PartitionSound $true -FrameTelemetryRequested $true)
Assert-Equal 0 $soundResult.Count `
    "a present, sound, measured record must produce 0 validation failures"

$unsoundResult = @(Get-SourceFrameAttributionValidationFailures `
    -SourceFramePopulation ([pscustomobject]@{ partition_sound = 0 }) `
    -AttributionMeasured $true -PartitionSound $false -FrameTelemetryRequested $true)
Assert-Equal 1 $unsoundResult.Count `
    "a present, measured-but-unsound record must fail closed"
if ($unsoundResult.Count -eq 1) {
    Assert-Equal $true ([bool]($unsoundResult[0] -like "*partition was not sound*")) `
        "the unsound failure message must name the unsound partition"
}

$unmeasuredTelemetryOnResult = @(Get-SourceFrameAttributionValidationFailures `
    -SourceFramePopulation ([pscustomobject]@{ partition_sound = 0 }) `
    -AttributionMeasured $false -PartitionSound $false -FrameTelemetryRequested $true)
Assert-Equal 1 $unmeasuredTelemetryOnResult.Count `
    "a present-but-unmeasured record must fail closed when telemetry was requested (round 7's wiring-failure state)"

$unmeasuredTelemetryOffResult = @(Get-SourceFrameAttributionValidationFailures `
    -SourceFramePopulation ([pscustomobject]@{ partition_sound = 0 }) `
    -AttributionMeasured $false -PartitionSound $false -FrameTelemetryRequested $false)
Assert-Equal 0 $unmeasuredTelemetryOffResult.Count `
    "a present-but-unmeasured record is legitimate when telemetry was never requested"

$missingTelemetryOnResult = @(Get-SourceFrameAttributionValidationFailures `
    -SourceFramePopulation $null `
    -AttributionMeasured $null -PartitionSound $null -FrameTelemetryRequested $true)
Assert-Equal 1 $missingTelemetryOnResult.Count `
    "round 9 fix: a MISSING source_frame_population record must fail closed when -FrameTelemetry was requested, never fall back silently"
if ($missingTelemetryOnResult.Count -eq 1) {
    Assert-Equal $true ([bool]($missingTelemetryOnResult[0] -like "*did not include a source_frame_population record at all*")) `
        "the missing-record failure message must say the record was absent, not merely unsound"
}

$missingTelemetryOffResult = @(Get-SourceFrameAttributionValidationFailures `
    -SourceFramePopulation $null `
    -AttributionMeasured $null -PartitionSound $null -FrameTelemetryRequested $false)
Assert-Equal 0 $missingTelemetryOffResult.Count `
    "a missing record is legitimate when telemetry was never requested (e.g. -FrameTelemetry:`$false)"

if ($failures.Count -gt 0) {
    foreach ($failure in $failures) {
        Write-Host "[FAIL] $failure"
    }
    throw "$($failures.Count) playback-smoke-log-parsing test(s) failed."
}

Write-Host "[SUMMARY] playback-smoke-log-parsing tests=16 failed=0"
