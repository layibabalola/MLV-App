$ErrorActionPreference = 'Stop'
Set-StrictMode -Version Latest
. (Join-Path $PSScriptRoot 'gui-smoke-screenshot-provenance.ps1')

function Assert-True([bool]$Condition, [string]$Message) {
    if (-not $Condition) { throw $Message }
}

function Assert-Failure([object]$Result, [string]$ExpectedFailure) {
    Assert-True (-not $Result.validFresh) "Expected invalid provenance for $ExpectedFailure"
    Assert-True ($Result.failures -contains $ExpectedFailure) `
        "Expected failure '$ExpectedFailure'; observed: $($Result.failures -join ', ')"
}

function Assert-PairFailure([object]$Result, [string]$ExpectedFailure) {
    Assert-True (-not $Result.validComparable) "Expected invalid pair for $ExpectedFailure"
    Assert-True ($Result.failures -contains $ExpectedFailure) `
        "Expected pair failure '$ExpectedFailure'; observed: $($Result.failures -join ', ')"
}

function New-V2ProvenanceFixture {
    param(
        [int]$StartFrame,
        [int[]]$Frames,
        [string]$TargetHash = 'target-content',
        [int]$TargetRawPrefetch = 1,
        [int]$Temperature = 6000
    )

    $lines = [System.Collections.Generic.List[string]]::new()
    $lines.Add("[INFO] interaction_trace event=gui_smoke.visual_state temperature=$Temperature tint=0 exposure=0 chroma_smooth=0 scale_request=4 quality_mode=2")
    $lines.Add('[INFO] interaction_trace event=gui_smoke.playback_policy playback_debayer_effective=bilinear playback_processing_selected=receipt drop_frame=0')
    $lines.Add("[INFO] playback_smoke.start session=1 position=$StartFrame start_serial=4 scale_request=4 quality_mode=2")
    for ($index = 0; $index -lt $Frames.Count; ++$index) {
        $frame = $Frames[$index]
        $serial = 4 + $index
        $presentIndex = 1 + $index
        $hash = if ($index -eq ($Frames.Count - 1)) { $TargetHash } else { "content-$frame" }
        $rawPrefetch = if ($index -eq ($Frames.Count - 1)) { $TargetRawPrefetch } else { 1 }
        $lines.Add("[INFO] interaction_trace event=draw_frame.request serial=$serial requested_frame=$frame requested_scale=4 generation=8")
        $lines.Add("[INFO] interaction_trace event=draw_frame_ready.begin serial=$serial display_frame=$frame requested_scale=4 active_scale=4 generation=8")
        $lines.Add("[INFO] interaction_trace event=draw_frame_ready.present_content display_frame=$frame hash=$hash")
        $lines.Add("[INFO] playback_smoke.frame session=1 index=$presentIndex elapsed_ms=99.0 display_frame=$frame serial=$serial scale_request=4 scale_active=4 worker_threads=1 worker_thread_cap_active=1 openmp_threads=1 openmp_thread_cap_active=1")
        $lines.Add("[INFO] playback_smoke.render_manifest session=1 index=$presentIndex path_code=3 path_label=full-xy-pre-recon path_source=render_thread processed8_cache_hit=0 raw_prefetch=$rawPrefetch dual_iso_valid=1 rendered_w=452 rendered_h=567 reduced=1")
        $lines.Add("[INFO] interaction_trace event=draw_frame_ready.end serial=$serial display_frame=$frame")
    }
    $lines.Add('[INFO] interaction_trace event=gui_smoke.screenshot path="frame.png" width=1958 height=818 method=app_internal_presented_pixmap')
    return @($lines)
}

$fresh = @(
    '[INFO] interaction_trace event=draw_frame_ready.present_content display_frame=498 play_checked=1 position=499 hash=1a520c58f1985a4b',
    '[INFO] playback_smoke.render_manifest session=1 index=3 path_source=render_thread processed8_cache_hit=0 raw_prefetch=1',
    '[INFO] interaction_trace event=gui_smoke.screenshot path="frame.png" width=1958 height=818 method=app_internal_presented_pixmap'
)
$freshResult = Get-GuiSmokeScreenshotProvenance $fresh
Assert-True $freshResult.validFresh 'Fresh render provenance should pass.'
Assert-True ($freshResult.displayFrame -eq 498) 'Fresh render display frame was not parsed.'
Assert-True ($freshResult.contentHash -eq '1a520c58f1985a4b') 'Fresh render content hash was not parsed.'

$multiFrame = @(
    '[INFO] interaction_trace event=draw_frame_ready.present_content display_frame=497 play_checked=1 position=498 hash=old497',
    '[INFO] playback_smoke.render_manifest session=1 index=2 path_source=processed8_cache processed8_cache_hit=1 raw_prefetch=0',
    '[INFO] interaction_trace event=draw_frame_ready.present_content display_frame=498 play_checked=1 position=499 hash=new498',
    '[INFO] playback_smoke.render_manifest session=1 index=3 path_source=render_thread processed8_cache_hit=0 raw_prefetch=1',
    '[INFO] interaction_trace event=gui_smoke.screenshot path="frame.png" width=1958 height=818 method=app_internal_presented_pixmap'
)
$multiFrameResult = Get-GuiSmokeScreenshotProvenance $multiFrame
Assert-True $multiFrameResult.validFresh 'The last complete frame association should win.'
Assert-True ($multiFrameResult.displayFrame -eq 498) 'The last display frame was not selected.'
Assert-True ($multiFrameResult.contentHash -eq 'new498') 'The last presented hash was not selected.'

$multipleScreenshots = @(
    $fresh[0],
    $fresh[1],
    '[INFO] interaction_trace event=gui_smoke.screenshot path="first.png" width=1958 height=818 method=app_internal_presented_pixmap',
    '[INFO] interaction_trace event=draw_frame_ready.present_content display_frame=499 play_checked=1 position=500 hash=last499',
    '[INFO] playback_smoke.render_manifest session=1 index=4 path_source=render_thread processed8_cache_hit=0 raw_prefetch=1',
    '[INFO] interaction_trace event=gui_smoke.screenshot path="last.png" width=1958 height=818 method=app_internal_presented_pixmap'
)
$multipleScreenshotsResult = Get-GuiSmokeScreenshotProvenance $multipleScreenshots
Assert-True $multipleScreenshotsResult.validFresh 'The last screenshot association should win.'
Assert-True ($multipleScreenshotsResult.displayFrame -eq 499) 'The last screenshot did not bind to the last frame.'

$unpairedLastPresent = @(
    $fresh[0],
    $fresh[1],
    '[INFO] interaction_trace event=draw_frame_ready.present_content display_frame=499 play_checked=1 position=500 hash=unpaired499',
    $fresh[2]
)
Assert-Failure (Get-GuiSmokeScreenshotProvenance $unpairedLastPresent) 'present-manifest-order-mismatch'

$cacheHit = @(
    '[INFO] interaction_trace event=draw_frame_ready.present_content display_frame=498 play_checked=1 position=499 hash=ba9020b0014c8745',
    '[INFO] playback_smoke.render_manifest session=1 index=3 path_source=processed8_cache processed8_cache_hit=1 raw_prefetch=0',
    '[INFO] interaction_trace event=gui_smoke.screenshot path="frame.png" width=1958 height=818 method=app_internal_presented_pixmap'
)
$cacheHitResult = Get-GuiSmokeScreenshotProvenance $cacheHit
Assert-Failure $cacheHitResult 'processed8-cache-hit'
Assert-Failure $cacheHitResult 'render-path-not-fresh'

$missingManifest = @($fresh[0], $fresh[2])
Assert-Failure (Get-GuiSmokeScreenshotProvenance $missingManifest) `
    'missing-render-manifest-before-screenshot'

$manifestAfterScreenshot = @($fresh[0], $fresh[2], $fresh[1])
Assert-Failure (Get-GuiSmokeScreenshotProvenance $manifestAfterScreenshot) `
    'missing-render-manifest-before-screenshot'

$badOrder = @($fresh[1], $fresh[0], $fresh[2])
Assert-Failure (Get-GuiSmokeScreenshotProvenance $badOrder) 'present-manifest-order-mismatch'

$missingHash = @(
    '[INFO] interaction_trace event=draw_frame_ready.present_content display_frame=498 play_checked=1 position=499',
    $fresh[1],
    $fresh[2]
)
Assert-Failure (Get-GuiSmokeScreenshotProvenance $missingHash) 'missing-present-content-hash'

# The observed false-positive shape must stay rejected even though its reported
# display frame and screenshot dimensions look valid.
$observedFalsePositive = @(
    '[INFO] interaction_trace event=draw_frame_ready.present_content display_frame=498 play_checked=1 position=499 hash=ba9020b0014c8745',
    '[INFO] playback_smoke.render_manifest session=1 index=3 path_code=3 path_label=full-xy-pre-recon path_source=processed8_cache processed8_cache_hit=1 raw_prefetch=0 rendered_w=452 rendered_h=567',
    '[INFO] interaction_trace event=gui_smoke.screenshot path="M16-1347.png" width=1958 height=818 method=app_internal_presented_pixmap'
)
Assert-Failure (Get-GuiSmokeScreenshotProvenance $observedFalsePositive) 'processed8-cache-hit'

# A stale PNG must never rescue a failed smoke child. This pure gate is called
# before the review script reads dimensions or computes any image metrics.
$failedChildWasRejected = $false
try {
    Assert-GuiSmokeChildEvidenceReady -ExitCode 7 -ResultExists $true -ScreenshotExists $true | Out-Null
}
catch {
    $failedChildWasRejected = $_.Exception.Message -like '*smoke-child-exit-7*'
}
Assert-True $failedChildWasRejected `
    'A nonzero smoke child with pre-existing result/PNG evidence must fail closed.'
$successfulChildGate = Assert-GuiSmokeChildEvidenceReady `
    -ExitCode 0 -ResultExists $true -ScreenshotExists $true
Assert-True $successfulChildGate.ok 'A complete successful smoke child should pass the evidence gate.'

# Regression replay: both sides of the observed M15-1320 false pair were
# individually fresh and displayed frame 93, but they reached it through
# different start/render histories. That is not a comparable A/B.
$observedParentLines = @(New-V2ProvenanceFixture `
    -StartFrame 89 -Frames @(90, 91, 92, 93) `
    -TargetHash 'd52742ab04ce0100' -TargetRawPrefetch 1)
$observedCandidateLines = @(New-V2ProvenanceFixture `
    -StartFrame 92 -Frames @(93) `
    -TargetHash 'f543f7dcde926553' -TargetRawPrefetch 0)
$observedParentProof = Get-GuiSmokeScreenshotProvenance `
    -OrderedLogLines $observedParentLines -RequestedStartFrame 89
$observedCandidateProof = Get-GuiSmokeScreenshotProvenance `
    -OrderedLogLines $observedCandidateLines -RequestedStartFrame 92
Assert-True $observedParentProof.validFresh 'Observed parent replay should be individually fresh.'
Assert-True $observedCandidateProof.validFresh 'Observed candidate replay should be individually fresh.'
Assert-True ($observedParentProof.displayFrame -eq 93 -and $observedCandidateProof.displayFrame -eq 93) `
    'Observed false-pair replay must retain the deceptively equal displayed frame.'
$observedPair = Test-GuiSmokeScreenshotPair `
    -Left $observedParentProof -Right $observedCandidateProof
foreach ($expected in @(
    'requested-start-frame-mismatch',
    'effective-start-frame-mismatch',
    'presentation-index-mismatch',
    'request-serial-mismatch',
    'request-serial-offset-mismatch',
    'presented-history-mismatch'
)) {
    Assert-PairFailure $observedPair $expected
}

# Controlled adjudication replay: same start, frames, presentation ordinal,
# and serial chain. Content hashes may differ because output is what the A/B
# measures; association, state, and history are the comparability gate.
$controlledParentLines = @(New-V2ProvenanceFixture `
    -StartFrame 90 -Frames @(91, 92, 93) -TargetHash 'parent-output')
$controlledCandidateLines = @(New-V2ProvenanceFixture `
    -StartFrame 90 -Frames @(91, 92, 93) -TargetHash 'candidate-output')
$controlledParentProof = Get-GuiSmokeScreenshotProvenance `
    -OrderedLogLines $controlledParentLines -RequestedStartFrame 90
$controlledCandidateProof = Get-GuiSmokeScreenshotProvenance `
    -OrderedLogLines $controlledCandidateLines -RequestedStartFrame 90
$controlledPair = Test-GuiSmokeScreenshotPair `
    -Left $controlledParentProof -Right $controlledCandidateProof
Assert-True $controlledPair.validComparable `
    "Controlled equal-history replay should pass: $($controlledPair.failures -join ', ')"
Assert-True ($controlledParentProof.presentationIndex -eq 3) 'Controlled parent index should be 3.'
Assert-True ($controlledParentProof.requestSerial -eq 6) 'Controlled parent serial should be 6.'

$requestFrameMismatchLines = [System.Collections.Generic.List[string]]::new()
$requestFrameMismatchLines.AddRange([string[]]$controlledParentLines)
for ($index = $requestFrameMismatchLines.Count - 1; $index -ge 0; --$index) {
    if ($requestFrameMismatchLines[$index] -like '*draw_frame.request*requested_frame=93*') {
        $requestFrameMismatchLines[$index] = $requestFrameMismatchLines[$index] -replace 'requested_frame=93', 'requested_frame=92'
        break
    }
}
Assert-Failure (Get-GuiSmokeScreenshotProvenance `
    -OrderedLogLines @($requestFrameMismatchLines) -RequestedStartFrame 90) `
    'request-display-frame-mismatch'

$manifestMismatchLines = [System.Collections.Generic.List[string]]::new()
$manifestMismatchLines.AddRange([string[]]$controlledParentLines)
for ($index = $manifestMismatchLines.Count - 1; $index -ge 0; --$index) {
    if ($manifestMismatchLines[$index] -like '*playback_smoke.render_manifest*index=3*') {
        $manifestMismatchLines[$index] = $manifestMismatchLines[$index] -replace 'session=1', 'session=2'
        break
    }
}
Assert-Failure (Get-GuiSmokeScreenshotProvenance `
    -OrderedLogLines @($manifestMismatchLines) -RequestedStartFrame 90) `
    'missing-associated-render-manifest'

$missingEndLines = @($controlledParentLines | Where-Object {
    $_ -notlike '*draw_frame_ready.end*serial=6*'
})
Assert-Failure (Get-GuiSmokeScreenshotProvenance `
    -OrderedLogLines $missingEndLines -RequestedStartFrame 90) `
    'missing-associated-ready-end'

$laterPresentLines = [System.Collections.Generic.List[string]]::new()
$laterPresentLines.AddRange([string[]]$controlledParentLines)
$laterPresentLines.Insert($laterPresentLines.Count - 1,
    '[INFO] interaction_trace event=draw_frame_ready.present_content display_frame=94 hash=unassociated')
Assert-Failure (Get-GuiSmokeScreenshotProvenance `
    -OrderedLogLines @($laterPresentLines) -RequestedStartFrame 90) `
    'later-unassociated-present-content'

$differentStateLines = @(New-V2ProvenanceFixture `
    -StartFrame 90 -Frames @(91, 92, 93) -Temperature 5000)
$differentStateProof = Get-GuiSmokeScreenshotProvenance `
    -OrderedLogLines $differentStateLines -RequestedStartFrame 90
$differentStatePair = Test-GuiSmokeScreenshotPair `
    -Left $controlledParentProof -Right $differentStateProof
Assert-PairFailure $differentStatePair 'effective-state-mismatch:effectiveState.visualState.temperature'
$allowedStatePair = Test-GuiSmokeScreenshotPair `
    -Left $controlledParentProof -Right $differentStateProof `
    -AllowedEffectiveStateDifferences @('effectiveState.visualState.temperature')
Assert-True $allowedStatePair.validComparable `
    'An explicit effective-state allowlist should admit only its named difference.'

# A state/policy line emitted after the selected playback transaction cannot
# retroactively describe the screenshot. The old latest-before-screenshot scan
# admitted this shape and could make differently rendered legs look equal.
$lateStateLines = [System.Collections.Generic.List[string]]::new()
$lateStateLines.AddRange([string[]]$controlledParentLines)
$lateStateLines.Insert($lateStateLines.Count - 1,
    '[INFO] interaction_trace event=gui_smoke.visual_state temperature=5000 tint=0 exposure=0 chroma_smooth=0 scale_request=4 quality_mode=2')
$lateStateProof = Get-GuiSmokeScreenshotProvenance `
    -OrderedLogLines @($lateStateLines) -RequestedStartFrame 90
Assert-Failure $lateStateProof 'late-visual-state-replacement'

$latePolicyLines = [System.Collections.Generic.List[string]]::new()
$latePolicyLines.AddRange([string[]]$controlledParentLines)
$latePolicyLines.Insert($latePolicyLines.Count - 1,
    '[INFO] interaction_trace event=gui_smoke.playback_policy playback_debayer_effective=amaze playback_processing_selected=receipt drop_frame=0')
$latePolicyProof = Get-GuiSmokeScreenshotProvenance `
    -OrderedLogLines @($latePolicyLines) -RequestedStartFrame 90
Assert-Failure $latePolicyProof 'late-playback-policy-replacement'

# Screenshot paths are necessarily leg-specific, but the capture surface and
# geometry are part of the evidence and must compare exactly.
$differentPathLines = [System.Collections.Generic.List[string]]::new()
$differentPathLines.AddRange([string[]]$controlledCandidateLines)
$differentPathLines[$differentPathLines.Count - 1] =
    '[INFO] interaction_trace event=gui_smoke.screenshot path="other-leg.png" width=1958 height=818 method=app_internal_presented_pixmap'
$differentPathProof = Get-GuiSmokeScreenshotProvenance `
    -OrderedLogLines @($differentPathLines) -RequestedStartFrame 90
$differentPathPair = Test-GuiSmokeScreenshotPair `
    -Left $controlledParentProof -Right $differentPathProof
Assert-True $differentPathPair.validComparable `
    'A screenshot output-path difference alone must not invalidate a pair.'

$differentMethodLines = [System.Collections.Generic.List[string]]::new()
$differentMethodLines.AddRange([string[]]$differentPathLines)
$differentMethodLines[$differentMethodLines.Count - 1] =
    $differentMethodLines[$differentMethodLines.Count - 1] -replace 'app_internal_presented_pixmap', 'app_internal_viewport_grab'
$differentMethodProof = Get-GuiSmokeScreenshotProvenance `
    -OrderedLogLines @($differentMethodLines) -RequestedStartFrame 90
$differentMethodPair = Test-GuiSmokeScreenshotPair `
    -Left $controlledParentProof -Right $differentMethodProof
Assert-PairFailure $differentMethodPair 'screenshot-method-mismatch'

$differentGeometryLines = [System.Collections.Generic.List[string]]::new()
$differentGeometryLines.AddRange([string[]]$differentPathLines)
$differentGeometryLines[$differentGeometryLines.Count - 1] =
    $differentGeometryLines[$differentGeometryLines.Count - 1] `
        -replace 'width=1958 height=818', 'width=1900 height=800'
$differentGeometryProof = Get-GuiSmokeScreenshotProvenance `
    -OrderedLogLines @($differentGeometryLines) -RequestedStartFrame 90
$differentGeometryPair = Test-GuiSmokeScreenshotPair `
    -Left $controlledParentProof -Right $differentGeometryProof
Assert-PairFailure $differentGeometryPair 'screenshot-width-mismatch'
Assert-PairFailure $differentGeometryPair 'screenshot-height-mismatch'

$wrapperPath = Join-Path $PSScriptRoot 'run-release-gui-smoke.ps1'
$parseErrors = $null
$parseTokens = $null
[System.Management.Automation.Language.Parser]::ParseFile(
    $wrapperPath,
    [ref]$parseTokens,
    [ref]$parseErrors) | Out-Null
Assert-True ($parseErrors.Count -eq 0) `
    "run-release-gui-smoke.ps1 has parser errors: $($parseErrors -join '; ')"
$wrapperText = Get-Content -LiteralPath $wrapperPath -Raw
foreach ($requiredSymbol in @(
    '[switch]$RequireFreshScreenshotRender',
    '$FrameTelemetry = $true',
    'Get-GuiSmokeScreenshotProvenance `',
    '-RequestedStartFrame $StartFrame',
    'Screenshot fresh-render provenance failed:',
    '-NotePropertyName freshScreenshotRenderValidated'
)) {
    Assert-True $wrapperText.Contains($requiredSymbol) `
        "run-release-gui-smoke.ps1 is missing integration symbol: $requiredSymbol"
}
$blockingAbText = Get-Content -LiteralPath (
    Join-Path $PSScriptRoot 'review-dualiso-fullres-recon.ps1') -Raw
Assert-True $blockingAbText.Contains("'-RequireFreshScreenshotRender'") `
    'The blocking dual-ISO golden A/B must opt into fresh screenshot provenance.'
Assert-True $blockingAbText.Contains('$smokeExitCode = $LASTEXITCODE') `
    'The blocking dual-ISO A/B must capture the smoke child exit code immediately.'
Assert-True $blockingAbText.Contains('Assert-GuiSmokeChildEvidenceReady') `
    'The blocking dual-ISO A/B must gate child evidence before consuming the PNG.'
Assert-True $blockingAbText.Contains('legEvidence = $legEvidence') `
    'The final dual-ISO report must include each leg validation/provenance record.'
Assert-True $blockingAbText.Contains('Assert-GuiSmokeScreenshotPair') `
    'The blocking dual-ISO A/B must reject incomparable frame histories.'
Assert-True $blockingAbText.Contains("'-FrameTelemetry'") `
    'The blocking dual-ISO A/B must request serial/index frame telemetry.'
$immediateExitCapturePattern = '(?s)& pwsh\.exe .*?\*> \$smokeLogPath\r?\n\s*\$smokeExitCode = \$LASTEXITCODE'
Assert-True ([regex]::IsMatch($blockingAbText, $immediateExitCapturePattern)) `
    'The smoke child exit code must be captured on the statement immediately after invocation.'
$childGateIndex = $blockingAbText.IndexOf('Assert-GuiSmokeChildEvidenceReady')
$pngAcceptedIndex = $blockingAbText.IndexOf('$grabs["scale$sf"] = $png')
Assert-True ($childGateIndex -ge 0 -and $pngAcceptedIndex -gt $childGateIndex) `
    'The child evidence gate must run before a screenshot can enter the metrics input set.'
$pairGateIndex = $blockingAbText.IndexOf('Assert-GuiSmokeScreenshotPair')
$metricIndex = $blockingAbText.IndexOf('$metricsJson =')
Assert-True ($pairGateIndex -ge 0 -and $metricIndex -gt $pairGateIndex) `
    'The pair comparability assertion must run before image metrics consume either screenshot.'

Write-Host 'PASS: GUI smoke screenshot fresh-render provenance tests'
