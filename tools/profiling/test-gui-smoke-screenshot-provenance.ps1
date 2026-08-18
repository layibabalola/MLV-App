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
    'Get-GuiSmokeScreenshotProvenance -OrderedLogLines $recentLines',
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
$immediateExitCapturePattern = '(?s)& pwsh\.exe .*?\*> \$smokeLogPath\r?\n\s*\$smokeExitCode = \$LASTEXITCODE'
Assert-True ([regex]::IsMatch($blockingAbText, $immediateExitCapturePattern)) `
    'The smoke child exit code must be captured on the statement immediately after invocation.'
$childGateIndex = $blockingAbText.IndexOf('Assert-GuiSmokeChildEvidenceReady')
$pngAcceptedIndex = $blockingAbText.IndexOf('$grabs["scale$sf"] = $png')
Assert-True ($childGateIndex -ge 0 -and $pngAcceptedIndex -gt $childGateIndex) `
    'The child evidence gate must run before a screenshot can enter the metrics input set.'

Write-Host 'PASS: GUI smoke screenshot fresh-render provenance tests'
