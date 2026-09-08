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

Use Bachelor for routine playback validation, with UltraMagnus available for
CUDA builds and additional GPU coverage. Record the host, renderer, build hash
and CUDA architecture before interpreting a result. Bachelor requires `sm_86`;
an UltraMagnus-only `sm_89` DLL does not validate its RTX 3060 laptop GPU. Build
from the same pinned source and verify the actual binary architectures. Stage
each run from the preserved clean package: a live app can update its `releases`
metadata, so a previously used runtime folder is not an immutable package.

Paint-fix build `688b505ce228` was verified on both hosts. On Bachelor, a separate
run with internal screenshots disabled produced 727 presented frames (first 0,
last 123, skipped/unpresented ratio zero), and all 13 HWND compositor captures
succeeded. Inspected captures showed changing footage. The 726 post-warmup
frames reported accepted, used and exact async uploads. A separate output
validation run had 794 presented frames (first 0, last 96, ratio zero), 80/80
texture parity matches and no mismatches. These checks do not establish exposure
correctness against an independently known-good build, and their instrumentation
precludes a no-overhead performance claim.

## Native image tests and continuous display geometry

The initial suite had 30 passes/10 failures before the paint fix and 32/8 after
it. Investigation of the remaining failures found an additional geometry bug:
`mapFromScene(rect).boundingRect()` converts continuous rectangle edges into
integer inclusive bounds, expanding a 4x4 frame to 5x5. The viewport now maps
the `QRectF` through `viewportTransform()` directly. The regression exercises
the actual target rectangle without GL, including fractional zoom, the caller's
de-squeezed scene rectangle and scrolling. Native zebra and pixel comparisons
also detect the resulting sampling error.

The tests contained two separate reference-construction defects. Their shader
comparison helper vertically flipped the submitted pixels, and their U16-to-U8
conversion could wrap 65535 to zero. The helpers now preserve image orientation
and round normalized values using division by 257. Endpoint and grayscale
roundtrip tests cover the latter. The two basic RGB pattern tests previously
checked only size and a frozen hash; they now also compare exact pixels against
the submitted pattern before checking the hash.

The scope goldens were captured at DPR 1, while native Windows used DPR 1.5
despite the existing scale-factor environment settings. Only the test process
now disables automatic Windows DPI scaling before creating `QApplication` and
asserts unit DPR for the scope fixture. Real application/WGC runs retain the
host's display settings. `MLVAPP_TEST_SCOPE_DIAGNOSTICS=1` records the effective
platform, DPR, pixmap geometry and raw hash. See the supported testing setting
in [Qt's high-DPI documentation](https://doc.qt.io/qt-6.10/highdpi.html).

Before the reference correction, the native suite had 40 passes and exactly
two frozen-hash failures, with no skips. Both old hashes encode a vertically
flipped 4x4 pattern inflated to 5x5 and cropped back to 4x4. The corrected
RGB888/RGB16 output matches the original pattern exactly. The owner delegated
this exact proposal to two adversarial Luna reviewers and the Fable hub; their
approval and bounded authority are recorded in `agents/release-and-regression.md`.
After applying only those two hashes, the unchanged native executable passed
all 42 tests with no failures or skips. Offscreen passed 33 tests with nine
native GL skips. The geometry fix still requires a fresh application build and
Bachelor real-footage validation before delivery; synthetic tests do not replace
the independently known-good build comparison.

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
