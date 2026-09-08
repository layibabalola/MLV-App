# GPU screenshot evidence

Explicit GUI screenshot smokes capture internal viewport pixels before releasing
each presented frame and emit a separate gpu_present_content event. The evidence
consumer binds its sampled hash, serial, generation and dimensions to the same
request/ready/frame chain as the final screenshot. Raw Bayer parity hashes remain
a separate stream; neither proves that Windows composed the image into the visible
playback window.

## Visible playback and the paint-event regression

The September 8, 2026 UltraMagnus investigation reproduced a black playback
window on an RTX 4090 while the internal screenshot showed the footage and GPU
texture parity passed. An independent, HWND-only Windows Graphics Capture
filmstrip also showed black; the CPU control showed the dark scene. A synthetic
solid-red frame reproduced black without footage, receipts or Look Assist.

`QGraphicsView` installs a viewport event filter that routes ordinary paint
events to its scene. The GPU presenter hides the scene's fallback pixmap and
draws in `QOpenGLWidget::paintGL`. Without explicit paint ownership, normal
playback therefore paints the empty scene. A widget/framebuffer grab can force
the GPU render and conceal this failure. The viewport now handles its own paint
events while a frame is pending, through Qt's complete widget paint path; scene
fallback, resize and input events retain their normal routing.

`gpuViewportOwnsPaintOnlyWhileFramePending` exercises Qt's event routing and
fallback transitions without requiring OpenGL. The separate native test
`gpuViewportPresentsThroughNormalPaintEvents` must pass with a working desktop
OpenGL context and no grab before the presentation assertions. Run it with
`QT_QPA_PLATFORM=windows`; the offscreen suite explicitly skips this native
test. For an external capture of its synthetic red frame, set
`MLVAPP_TEST_VISIBLE_HOLD_MS=2500` (bounded to five seconds).

For any presentation-path change, inspect timestamped compositor captures during
playback as well as the associated internal images. Capture only the identified
application HWND, bind its PID/creation time and executable hash, bound the
capture helper, and preserve failed captures. Do not use a desktop capture or
PrintWindow alone as the GPU presentation oracle. Require visible content to
advance, not merely UI timecode or internal frame counters. Compositor captures
are additional evidence: the existing playback, parity, aspect, artifact and
independent known-good-build output A/B gates still apply. The known-good binding
in `tools/gates/output-budget.json` remains pending; same-build CPU parity cannot
substitute for it.

Look Assist's `applied` flag indicates completed analysis/application handling.
When `visualQuality.lookAssist.safetyFallback` is true, inspect `safetyWarning`,
`safetyDecision`, preset fields and the recorded visual state before claiming a
correction was applied. The CPU and GPU frame-120 controls both reported
`global-green-cast`, decision `none`, null preset exposure and exposure zero.

The initial native GUI suite comparison had 30 passes/10 failures before the
paint fix and 32 passes/8 failures after it. The two new paint regressions turned
green. The eight identical existing failures remain open: six GPU expectations
still vertically flip their reference image, and two `ScopesLabel` widget hashes
differ on native Windows. The offscreen run passed 31 tests and skipped nine
native OpenGL tests. These results do not constitute an all-green native suite,
and no golden hashes were changed to accept them.

This instrumentation adds a viewport readback to screenshot smokes. Their timing
is not evidence of no-readback playback performance. Ordinary playback and smoke
runs without screenshot capture retain their existing behavior. The separate GPU
display window currently refuses fresh screenshot capture explicitly because the
graphics-view viewport does not represent that window's displayed surface.

Validation: run tools/profiling/test-gui-smoke-screenshot-provenance.ps1, then the
GUI smoke on a CUDA host with RequireFreshScreenshotRender and the existing
parity and artifact checks. The regression fixtures reject missing hashes, wrong
frame/serial/generation/source/dimensions and later unassociated presentations.
Output-validation mode deliberately disables prepare-only; prove application
async-H2D acceptance/use/exact-match in a separate configuration. A DLL harness
pass alone does not establish application frame-ID plumbing, and the async leg
does not replace the output-parity leg.

Frame IDs remain zero-based in application state. Both upload and reconstruction
convert them with llrpGpuPlaybackReconFrameToken; frame zero is valid, and the
unrepresentable UINT64_MAX value falls back to synchronous work. Both display
adapters use llrpGpuPlaybackReconCombineTiming so reconstruction status survives
independently of scalar timer availability. Retained-device presentation that
does not reconstruct in that call reports no new preupload status. Check every
frame in the async leg for positive accepted/used/exact-match counts; a successful
smoke or zero counters alone cannot establish async operation.

Preupload is submitted from the admitted prepare-only RAW processing path after
bit-depth expansion and other RAW corrections, using the reconstruction input
snapshot. Decoded input can have different bytes for the same frame; the backend
keeps its exact token and byte comparison and safely falls back on a mismatch.
The pipeline regression expands a synthetic 12-bit frame and observes the real
submission boundary for frames zero and one. Disabled and ineligible paths must
not submit. That observer proves the input contract, while GPU smoke telemetry
must independently prove that the application actually consumed async uploads.
