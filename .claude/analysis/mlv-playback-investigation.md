# MLV Playback Investigation

Updated: 2026-04-21

This is the living note for playback-performance analysis in this workspace. It combines:
- Local code inspection performed in this worktree
- Cross-checked findings from the earlier Codex investigation
- The strongest ideas from the Claude report, with local verification called out where possible

## Executive Summary

### Verified locally
- Playback is slow because each preview frame still does heavy CPU work end-to-end: raw read/decode, low-level raw fixes, debayer, color processing, 16-bit to 8-bit reduction, UI-thread scaling, and optional scopes/overlays.
- The active Qt playback path is CPU `QImage`/`QPixmap`/`QGraphicsView`, not a GPU-backed renderer. See `platform/qt/MLVApp.pro:7-18`, `platform/qt/MainWindow.cpp:1297-1300`, `platform/qt/MainWindow.cpp:9588-9642`.
- The Windows Qt build is SSE-only today. AVX code exists in the tree, but the build files do not enable it. See `platform/qt/MLVApp.pro:90-99`, `platform/qt/Makefile.Debug:17-18`, `platform/qt/Makefile.Release:17-18`, `src/debayer/sleefsseavx.c:12`, `src/librtprocess/src/include/sleefsseavx.h:24`.
- Dual ISO is a major playback multiplier in this codebase. If Dual ISO is your normal workflow, it is very likely one of the main reasons playback feels slow.
- This branch already had a partial hot-path optimization in `src/mlv`: reusable unpack/debayer/process scratch buffers plus a single processed-frame cache slot were present in `src/mlv/mlv_object.h` and used by `src/mlv/video_mlv.c`.
- That processed-frame cache was incomplete: it keyed only on `frameIndex` and `threads`, so direct `processingObject_t` mutations could reuse stale processed pixels until some caller explicitly hit `resetMlvCachedFrame()`.
- The branch now computes a processed-frame signature inside `src/mlv/video_mlv.c` from the active MLV/raw-processing/processing state and stores it alongside the cached frame, which makes same-frame same-thread reuse safe across direct processing mutations.
- Real-time high-quality playback is plausible, but not with the current architecture. The most credible path is staged:
  1. Fix CPU-side hot spots and cache behavior
  2. Add a real playback-only fast path, especially for Dual ISO
  3. Move display and color pipeline work to GPU
  4. If needed later, target GPU RCD-quality debayer for realtime preview and keep AMaZE CPU-only for paused/export quality

### Needs runtime profiling
- Exact per-stage percentages on your machine
- Whether decode, Dual ISO, debayer, processing, or UI/scopes dominate most for your clips
- How much win AVX, playhead-aware cache, and Dual ISO preview mode deliver in practice

## Confirmed Current Pipeline

Playback currently follows this path:

1. UI timer advances the playhead and triggers `drawFrame()`
   - `platform/qt/MainWindow.cpp:253`
   - `platform/qt/MainWindow.cpp:603`
2. A single render thread wakes and renders into `m_pRawImage`
   - `platform/qt/RenderFrameThread.cpp:37`
   - `platform/qt/RenderFrameThread.cpp:74`
   - `platform/qt/RenderFrameThread.cpp:94`
3. Frame generation goes through:
   - `getMlvProcessedFrame8()` -> `src/mlv/video_mlv.c:566`
   - `getMlvProcessedFrame16()` -> `src/mlv/video_mlv.c:540`
   - `getMlvRawFrameDebayered()` -> `src/mlv/video_mlv.c:485`
   - `get_mlv_raw_frame_debayered()` -> `src/mlv/frame_caching.c:295`
   - `getMlvRawFrameFloat()` -> `src/mlv/video_mlv.c:380`
   - `applyLLRawProcObject()` -> `src/mlv/llrawproc/llrawproc.c:200`
   - `applyProcessingObject()` -> `src/processing/raw_processing.c:419`
4. The UI thread wraps `m_pRawImage` in `QImage`, scales it, converts to `QPixmap`, and updates a `QGraphicsPixmapItem`
   - `platform/qt/MainWindow.cpp:9588-9642`

Implication: there is no deep playback pipeline and no meaningful overlap between several future frames. One visible frame still carries most of the full processing burden.

## Confirmed Bottlenecks

### 1. Single-slot render scheduling
- The app effectively has one active render worker and one image buffer in the playback path.
- `RenderFrameThread` polls and renders one requested frame at a time rather than maintaining a larger playhead-centered queue.
- Relevant refs:
  - `platform/qt/MainWindow.cpp:603`
  - `platform/qt/RenderFrameThread.cpp:74-109`

### 2. CPU-only raw decode, low-level raw fixes, debayer, and grading
- Raw frame ingest happens on CPU, including internal mcraw/LJ92/manual unpack paths.
- Relevant refs:
  - `src/mlv/video_mlv.c:195`
  - `src/mlv/video_mlv.c:243`
  - `src/mlv/video_mlv.c:327`
  - `src/mlv/video_mlv.c:359`
- Low-level raw fixes run before debayer and can include dark-frame subtraction, stripes, focus pixels, bad pixels, pattern noise, Dual ISO, and chroma smoothing.
- Relevant refs:
  - `src/mlv/llrawproc/llrawproc.c:239`
  - `src/mlv/llrawproc/llrawproc.c:273`
  - `src/mlv/llrawproc/llrawproc.c:313`
  - `src/mlv/llrawproc/llrawproc.c:443`

### 3. Cache behavior is not playhead-aware enough
- Cache work is bounded by `cache_limit_frames` and walks from the start rather than truly centering around the playhead.
- `cache_start_frame` exists but is not effectively used in the current fill/search behavior.
- Cache threads also serialize the `getMlvRawFrameFloat()` stage under `cache_mutex`, which limits throughput.
- Relevant refs:
  - `src/mlv/frame_caching.c:167-180`
  - `src/mlv/frame_caching.c:256`
  - `src/mlv/mlv_object.h:144-149`
  - `src/mlv/video_mlv.c:511`

### 4. Per-frame allocation churn in hot paths

#### Verified locally
- The branch-specific reusable buffer work is now partially addressed for the `src/mlv/video_mlv.c` hot path:
  - unpacked RAW scratch is reused via `raw_unpacked_temp_frame`
  - debayer float scratch is reused via `raw_debayer_temp_frame`
  - processed RGB scratch is reused via `rgb_processed_temp_frame`
  - exact processed-frame reuse is now guarded by a signature-aware cache in `getMlvProcessedFrame16()` / `getMlvProcessedFrame8()`
- Median denoiser allocates three windows per pixel in the main loop:
  - `src/processing/denoiser/denoiser_2d_median.c:77-79`
- The RBF wrapper reserves/releases memory for every call:
  - `src/processing/rbfilter/rbf_wrapper.cpp:33-36`
- AMaZE and librtprocess debayer paths allocate full-frame float planes on each call:
  - `src/debayer/debayer.c:25-37`
  - `src/debayer/debayer.c:386-398`
- Dual ISO full20bit allocates many full-frame working buffers and optional extras:
  - `src/mlv/llrawproc/dualiso.c:2168-2201`

#### Cross-checked from prior analysis
- Pattern-noise and chroma-smooth paths are also allocation-heavy and worth profiling next.

### 5. UI-thread display and scopes still cost real time
- The main preview is scaled on the UI thread and converted through `QPixmap::fromImage()`.
- Relevant refs:
  - `platform/qt/MainWindow.cpp:9588-9642`
- In the AVIR smooth/stretch path, `drawFrameReady()` allocates a scaled RGB buffer and constructs an `avir_scale_thread_pool` in the hot path for that frame.
- Relevant refs:
  - `platform/qt/MainWindow.cpp:9614-9632`
- Histogram and vectorscope use `pixelColor()` in inner loops, which is costly for per-frame analysis widgets.
- Relevant refs:
  - `platform/qt/Histogram.cpp:39-41`
  - `platform/qt/VectorScope.cpp:52`

### 6. CPU build leaves AVX potential unused
- Windows flags currently stop at SSE/SSSE3. The AVX-capable code paths remain behind `ENABLE_AVX`/AVX flags that are not turned on in the Qt build.
- Relevant refs:
  - `platform/qt/MLVApp.pro:90-99`
  - `platform/qt/Makefile.Debug:17-18`
  - `platform/qt/Makefile.Release:17-18`
  - `src/debayer/sleefsseavx.c:12`
  - `src/librtprocess/src/include/sleefsseavx.h:24`

## Dual ISO-Specific Findings

### Short answer
Yes. Dual ISO can materially slow playback here.

### Why it hurts
- Dual ISO runs inside low-level raw processing before normal debayer and grading, so uncached preview frames pay its cost first.
- In full mode, `applyLLRawProcObject()` calls `diso_get_full20bit(...)`:
  - `src/mlv/llrawproc/llrawproc.c:349-364`
- `diso_get_full20bit()` is heavy by design:
  - promotes to 20-bit working data
  - allocates multiple full-frame buffers
  - optionally allocates smoothed buffers and alias maps
  - matches exposures
  - interpolates and reconstructs
  - blends and converts back for later stages
- Relevant refs:
  - `src/mlv/llrawproc/dualiso.c:2159-2207`
  - `src/mlv/llrawproc/dualiso.c:2233-2258`

### AMaZE vs Mean matters a lot
- Dual ISO interpolation method `0` is the expensive AMaZE-edge path:
  - `src/mlv/llrawproc/llrawproc_object.h:65`
  - `src/mlv/llrawproc/dualiso.c:2233-2239`
- The AMaZE-based Dual ISO path allocates per-row planes and thread arrays and runs a large multistage interpolation process:
  - `src/mlv/llrawproc/dualiso.c:1195-1365`
- Method `1` is the simpler `mean23_interpolate()` path:
  - `src/mlv/llrawproc/dualiso.c:1516`
  - `src/mlv/llrawproc/dualiso.c:2237-2239`
- The user manual already hints at this tradeoff:
  - `platform/qt/help/help.htm:120`

### There is a dormant preview path, but it is not active
- `llrawproc.c` still contains a `dual_iso == 2` preview branch that would call `diso_get_preview(...)`, but it is commented out:
  - `src/mlv/llrawproc/llrawproc.c:429-439`
- The Qt UI only exposes Dual ISO mode `0` or `1`, so there is no current way to use a dedicated playback-preview mode:
  - `platform/qt/MainWindow.cpp:5921-5924`
  - `platform/qt/MainWindow.cpp:5744-5746`

### Auto exposure matching likely repeats more than it should
- ISO-pattern detection is not the main recurring cost once it stabilizes.
- Exposure matching can still rerun every frame when auto modes remain active.
- Relevant refs from the Dual ISO analysis:
  - `src/mlv/llrawproc/dualiso.c:963`
  - `src/mlv/llrawproc/dualiso.c:974`
  - `src/mlv/llrawproc/dualiso.c:994`
  - `platform/qt/MainWindow.cpp:8909-8911`
  - `platform/qt/MainWindow.cpp:8924-8926`

### Alias map and full-res blending add more work
- Full-res blending adds more buffers and reconstruction work:
  - `src/mlv/llrawproc/dualiso.c:2184-2190`
  - `src/mlv/llrawproc/dualiso.c:2244`
- Alias map adds another full-frame allocation plus a build pass:
  - `src/mlv/llrawproc/dualiso.c:2197-2201`
  - `src/mlv/llrawproc/dualiso.c:1926-1928`
- Current defaults favor quality, not playback speed:
  - `src/mlv/llrawproc/llrawproc.c:168-170`

### Cache interaction
- Changing Dual ISO interpolation, alias map, or full-res blending resets the MLV cache, so experimentation has an immediate playback penalty.
- Relevant refs:
  - `platform/qt/MainWindow.cpp:8936-8959`

## Combined Assessment Of Claude's Strongest Ideas

### Confirmed and high-value
- Persistent scratch buffers would likely help a lot.
  - Strongest areas: Dual ISO buffers, debayer float planes, RBF scratch, denoiser windows.
- Scopes should stop using slow per-pixel Qt accessors.
- Playhead-aware caching is a better fit than the current front-loaded cache behavior.
- AVX should be evaluated on x86 builds after correctness testing.
- GPU can help, but the first meaningful win is not magic decode acceleration; it is moving scaling/compositing and then color-pipeline work off the UI/CPU path.

### Strong but not yet locally re-profiled
- `raw_processing.c` keeps `double expo_correction` in a hot loop:
  - `src/processing/raw_processing.c:762-765`
- A playback-oriented GPU roadmap centered on display + color processing, and possibly later GPU RCD, is more credible than trying to port AMaZE or Huffman-style decode first.

### Recommendation adjustment after combining both analyses
- For realtime preview quality, the strongest long-term target is probably:
  - CPU decode and low-level raw ingest
  - playback-oriented GPU display/color path
  - optionally GPU RCD-quality demosaic later
  - keep AMaZE CPU-only for paused/export fidelity
- This is stronger than a GPU bilinear-only endpoint, because it preserves more preview quality without turning AMaZE-on-GPU into a research project.

## Ranked Next Steps

### Highest-value immediate work
1. Add a real playback-only Dual ISO fast path.
   - Re-enable and validate `diso_get_preview()` support
   - Expose or internally force a playback mode distinct from paused/export mode
2. Force playback-time Dual ISO to the faster settings.
   - Use `Mean` interpolation during playback
   - Keep alias map off during playback
   - Consider disabling full-res blending during playback
3. Freeze Dual ISO auto matching after first successful solve.
   - Convert the auto result into stable playback values instead of rerunning every frame
4. Remove per-frame scratch allocation in the remaining hottest paths.
   - Dual ISO
   - RBF
   - denoiser
5. Continue improving cache behavior.
   - The exact processed-frame cache is now present for same-frame/same-thread/same-settings reuse in `src/mlv`
   - Remaining work is playhead-centered multi-frame behavior and any broader preview cache policy
6. Replace `pixelColor()`-based scope code with raw scanline access.
7. Evaluate AVX enablement on supported x86 builds.

### Medium-term architecture work
1. Move preview display/scaling/compositing to `QOpenGLWidget` or equivalent GPU-backed rendering in the Qt path.
2. Move final preview color-pipeline stages to GPU.
3. Keep export fidelity on CPU until GPU parity is proven.

### Long-term
1. Consider GPU RCD-quality preview demosaic.
2. Keep AMaZE as the slow/high-quality mode for paused frames and export rather than requiring it to be realtime.

## Regression Test Strategy

Update (2026-04-20): the seed scaffold described here has now landed. For the implemented state, local verification, and remaining gaps, see `C:/!Layi Wkspc/MLV-App/.claude/worktrees/festive-boyd/.claude/analysis/testing-scaffold-implementation.md`.

### Bottom line
Unit tests alone will not protect this work. The safe strategy is a layered suite:
1. Pure unit tests for deterministic helpers and state transitions
2. Core pipeline microtests on synthetic frame buffers
3. Golden-frame integration tests on tiny fixture clips
4. Headless batch/receipt regression tests
5. Performance benchmarks with alert thresholds, kept separate from correctness pass/fail

### Current test posture

#### Verified locally
- There is no real automated test suite today. Searches for common test frameworks returned nothing in repo code.
- The GitHub Actions workflows are build-and-package jobs, not behavior-validation jobs:
  - `.github/workflows/Windows.yml:11-69`
  - `.github/workflows/Linux.yml:11-80`
  - `.github/workflows/macOS-Intel.yml:12-40`
  - `.github/workflows/macOS-Arm64.yml:12-40`
- There is one standalone experimental harness under `platform/binning_test/`, which is useful proof that core image-processing code can be exercised outside the GUI.
- The app already has a headless batch mode that can be used for integration regression tests:
  - `platform/qt/main.cpp:18-159`
- Receipt application already has a structured runtime fingerprint hook, which is a strong seam for assertions:
  - `src/batch/ReceiptApplier.cpp:22-299`
  - `src/batch/ReceiptApplier.cpp:310-360`

### Test harness recommendation

#### Recommendation
- Add a dedicated `tests/` target for automated checks rather than trying to test through the full GUI app.
- Use one lightweight C++ test runner to orchestrate tests for C and C++ modules.
- Keep Qt-dependent tests separate from pure core tests.

#### Practical shape
- `tests/core/`
  - non-Qt tests for raw decode, llrawproc, debayer, cache logic, and processing logic
- `tests/batch/`
  - headless batch-mode and receipt-application tests
- `tests/fixtures/`
  - tiny synthetic arrays
  - tiny MLV/mcraw fixture clips
  - receipt files
  - golden outputs and metadata
- `tests/perf/`
  - benchmark-style tests, not gating every PR by default

#### Framework choice
- Prefer a minimal in-repo runner or a lightweight single-binary C++ test framework rather than introducing a large new dependency tree.
- If we want to lean on existing dependencies, Qt Test is reasonable for Qt-facing and batch tests because Qt is already required by CI/build.
- For core correctness tests, plain C/C++ assertions are enough if the runner reports failures clearly and supports fixture loading.

### Determinism rules

These matter more than the framework choice.

- Golden correctness tests should use `threads=1` wherever possible.
  - `src/mlv/video_mlv.h:58-63` explicitly warns that multi-threaded processed-frame generation is intended for preview, not export, and may have minor artifacts.
- Disable cache unless the test is specifically exercising cache behavior.
- Fix all mutable settings explicitly in the test case rather than inheriting defaults from GUI state.
- For floating-point or GPU comparisons, use tolerance metrics rather than exact byte equality.
- Record the exact fixture version and receipt used by each golden output.

### Layer 1: Pure unit tests

These should run fastest and catch logic regressions before any image fixture is involved.

#### Candidates
- Cache scheduling helpers
  - frame selection around playhead
  - eviction/LRU logic once introduced
  - `cache_start_frame` semantics once fixed
- Dual ISO state/control logic
  - `DISO_OFF`, `DISO_20BIT`, `DISO_FAST`
  - playback-vs-paused mode switching policy
  - freezing auto-match values after first solve
- Receipt-to-runtime mapping
  - `ReceiptApplier::applyToMlv()` should set the exact llrawproc/processing fields expected for a given receipt
- Small mathematical helpers
  - exposure correction rules
  - parameter clamping
  - stretch factor logic
  - black/white level adjustment logic

#### Assertions
- Exact enum/state values
- Exact clamped numeric outputs
- Exact fingerprint text where useful

### Layer 2: Core pipeline microtests on synthetic buffers

These should target deterministic functions with tiny in-memory frames.

#### Dual ISO
- `diso_get_preview()`
  - should detect valid interlaced rows on known synthetic patterns
  - should fail cleanly on invalid non-Dual-ISO patterns
  - should preserve black/white bounds
- `match_exposures()`
  - auto mode `-1` low/high ISO math
  - histogram mode `-2`
  - manual `ev_correction` override
  - manual `black_delta` override
- `mean23_interpolate()`
  - stable output shape/range
  - no out-of-bounds writes on tiny edge-case dimensions
- `amaze_interpolate()`
  - correctness on a tiny but valid buffer
  - deterministic result in single-thread mode

#### llrawproc / processing
- bad-pixel/focus-pixel fixes on known pixel maps
- chroma smooth no-op when disabled
- pattern-noise no-op when disabled
- `applyProcessingObject()` identity behavior
  - with all creative controls neutral, output should match expected baseline transform
- highlight reconstruction edge cases
  - especially Dual ISO branches in `raw_processing.c:858-900`

#### Why synthetic tests matter
- They are cheap to run on every PR
- They isolate logic bugs from decode/I/O/UI noise
- They catch boundary issues before full MLV fixtures are involved

### Layer 3: Golden-frame integration tests

This is the most important protection for the planned playback work.

#### Fixture set
- Tiny standard RAW clip
  - non-Dual-ISO
  - uncompressed if possible
- Tiny lossless/LJ92 clip
- Tiny Dual ISO clip
- Optional fixture clips with known bad pixels / focus pixels / stripes if licensing and size allow

#### For each fixture, store
- clip metadata
- one or more receipts
- one or more expected frame outputs
- metrics file containing hashes and tolerances

#### Golden outputs to generate and compare
- raw float frame after decode/llrawproc
- debayered 16-bit frame
- processed 16-bit frame
- processed 8-bit preview frame
- batch-exported DNG or rendered image for end-to-end checks

#### Comparison metrics
- Exact byte equality for deterministic single-thread CPU steps when feasible
- Otherwise:
  - max absolute channel error
  - mean absolute error
  - PSNR
  - optional SSIM for rendered 8-bit previews

#### Recommended policy
- CPU reference path is the oracle
- New GPU path must match CPU within a defined tolerance envelope
- Any deliberate golden update requires reviewer sign-off and a note explaining why the baseline changed

### Layer 4: Headless batch and receipt regression tests

This repo already has the best possible seam for higher-level regression testing.

#### Why this is valuable
- It bypasses the GUI but still exercises real application wiring
- It proves receipt settings reach the actual runtime pipeline
- It is cross-platform-friendly

#### Tests to add
- Batch CLI smoke test
  - valid input/output arguments
  - invalid argument handling
- Receipt application test
  - run with a known receipt and assert on `ReceiptApplier::printFingerprint()` output
- Resume behavior
  - partial output folder
  - already-complete output folder
- Dual ISO receipt tests
  - `Mean` vs `AMaZE`
  - alias map on/off
  - full-res blending on/off
  - future playback-preview mode once implemented

### Layer 5: Playback behavior tests

Strictly speaking these are integration tests, but they are necessary for the planned feature work.

#### Core behaviors to lock down
- playback mode chooses fast path, paused mode chooses quality path
- switching playback on/off invalidates or preserves the right caches
- playhead-centered cache requests the right frames
- drop-frame mode still advances correctly without corrupted displayed frames
- changing Dual ISO settings resets cache exactly when expected

#### Good non-GUI seam
- Prefer testing the underlying scheduler/cache selection logic separately from `QTimer` and `QGraphicsView`
- If UI-level testing is needed, keep it to a small smoke layer with `QSignalSpy`-style assertions rather than making the whole suite GUI-driven

### Performance regression strategy

Performance tests should exist, but they should not be the only protection.

#### Bench groups
- decode only
- llrawproc only
- Dual ISO preview path
- Dual ISO full20bit path
- debayer only
- processing only
- end-to-end processed frame generation
- `drawFrameReady()` display/scaling path

#### Fixtures
- at least one small standard clip
- one lossless clip
- one Dual ISO clip

#### Rules
- Use warmup runs
- Pin thread count
- Separate cold-cache and warm-cache timings
- Store historical medians in CI artifacts or benchmark logs
- Gate hard only on large regressions
  - example: fail if slower by more than 20% on the reference Linux runner

### Specific tests needed for the planned roadmap

#### If we revive `diso_get_preview()`
- preview path selected during playback
- full20bit path selected while paused/exporting
- preview output is stable and within expected tolerance of full20bit for representative fixtures
- switching modes does not corrupt black/white levels or cached frames

#### If we fix `cache_start_frame` / playhead-aware caching
- selected cache-fill order matches playhead-centered expectation
- no frame outside cache window is incorrectly reported as cached
- cache reset behavior remains correct after receipt changes

#### If we remove cache serialization
- repeated multithreaded cache fills produce identical outputs to single-thread reference
- no data races or cross-frame corruption

#### If we hoist scratch buffers
- outputs remain byte-identical to pre-refactor reference
- repeated frame processing does not leak memory
- dimension changes trigger safe reallocation

#### If we add GPU preview/render path
- CPU and GPU previews match within tolerance on the same receipt and fixture
- zoom/stretch/scaling paths match expected geometry
- disabling GPU falls back to the known CPU reference path

### CI rollout recommendation

#### Phase 1
- Add fast core tests and receipt tests to Linux CI on every PR

#### Phase 2
- Add golden-frame integration tests to Linux and Windows on every PR

#### Phase 3
- Run the heavier performance suite nightly or on demand

#### Phase 4
- Once GPU code lands, add at least one GPU-capable runner or local pre-merge benchmark procedure

### Non-negotiable policy for future performance work
- No playback or Dual ISO optimization lands without at least one new regression test
- Any bug fixed from a user report should add a fixture or assertion that would have caught it
- Goldens are versioned artifacts, not disposable outputs
- Performance claims should be backed by benchmark output stored in the analysis log or CI artifacts
- Exactness belongs to CPU reference tests; tolerance belongs to GPU and multithread preview tests

## Practical Advice For Dual ISO Users Right Now

If you mainly work in Dual ISO, the safest current-speed advice is:
- Use `Mean` instead of Dual ISO `AMaZE` for interactive playback
- Keep alias map off unless you really need it
- Consider turning off full-res blending while reviewing motion
- Avoid changing Dual ISO settings mid-review, because each change resets cache
- Keep scopes/zebras/lightweight UI during playback if you are judging timing rather than paused-frame quality

## Caveats
- This report is based on static code inspection, not a fresh runtime profiler capture in this workspace.
- Some optimization ideas are strong engineering bets but still need measurement on real Dual ISO clips before claiming exact frame-rate gains.

## Implementation Status (2026-04-20)

The first protected implementation pass is now landed locally:

- Dual ISO preview mode (`dual_iso == 2`) is active again in the
  low-level pipeline.
- Playback now uses a runtime-only Dual ISO preview override and restores
  receipt-selected HQ settings on stop/export/clip-switch.
- Direct in-process Dual ISO frame goldens exist under
  `tests/pipeline/`.
- The cache now uses a real `cache_start_frame` window and follows the
  requested frame instead of behaving like a permanent frame-zero fill.
- A first perf harness exists under `tests/perf/`, and
  `MLVAPP_STAGE_TIMING=1` now emits per-stage timing lines through the
  hot path.
- Scope hot paths were rewritten away from slow per-pixel Qt accessors.
- The exact-frame cache now exists at both the processed 16-bit and
  processed 8-bit layers, with a post-render cache signature so
  llrawproc/processing runtime mutations do not prevent cache reuse on
  repeated requests.
- An opt-in OpenGL viewport path exists behind
  `MLVAPP_EXPERIMENTAL_GL_VIEWPORT=1`, and it now accepts the final
  `QImage` preview handoff, uploads it into a persistent
  `QOpenGLTexture`, and draws that texture directly in the viewport
  while keeping the existing `QGraphicsView` scene/overlay stack alive.
- Console/export goldens, cache tests, GUI smoke, pipeline goldens, and
  the new perf harness all pass locally on Qt 6.10.2 + MinGW.

What is still not implemented from the larger roadmap:

- final-preview cache beyond the exact processed-frame reuse already
  landed
- broader per-frame allocation cleanup outside the buffers already moved
  to reusable storage
- moving the CPU resize/stretch path into the GPU viewport so the app
  stops paying software scaling cost before upload
- broader platform validation of overlay composition with the
  texture-backed viewport enabled
- perf baseline thresholds and regression gating

## GPU Viewport Follow-up (2026-04-21)

### Verified locally
- `platform/qt/GpuDisplayViewport.h` / `platform/qt/GpuDisplayViewport.cpp`
  now expose a small presenter API:
  - `installOn(...)`
  - `isInstalledOn(...)`
  - `presentImage(...)`
  - `clearPresentedImage(...)`
- When the environment gate is enabled, the viewport now:
  - keeps a persistent `QOpenGLTexture`
  - converts incoming frames to RGBA8 once per handoff
  - uploads the latest frame only when dirty
  - draws a textured quad covering the mapped scene rect
  - hides the legacy `QGraphicsPixmapItem` while the texture path is active
- `platform/qt/MainWindow.cpp` integration stayed intentionally tiny:
  - clear the GL-presented image when the placeholder frame is restored
  - attempt `GpuDisplayViewport::presentImage(...)` before falling back to
    `QPixmap::fromImage(...)`
- GUI smoke coverage now includes a narrow viewport test that verifies:
  - install succeeds when the environment variable is set
  - presenting an image hides the fallback pixmap item
  - clearing the image restores fallback visibility

### Needs runtime profiling / platform validation
- The current path still uploads a CPU-prepared `QImage`; it does not yet
  move debayer, processing, or scaling into GL.
- Overlay composition and repaint behavior still need validation on the
  main target platforms and Qt versions.
- The local GUI smoke test project did not fully rebuild end-to-end in this
  sandbox because `qmake` failed while parsing MinGW default include paths,
  even though `GpuDisplayViewport.cpp` itself passed a direct syntax check.

## Local Verification Follow-up (2026-04-21)

The local branch is green after the cache/perf/display pass and the
follow-up perf watchdog tightening:

- `console_tests --check-golden`: pass
- `alloc_tests`: pass
- `pipeline_tests --check-golden`: pass
- `gui_tests` with `QT_QPA_PLATFORM=offscreen`: pass
- `perf_tests --iterations 10`: pass

Additional implementation note:

- `tests/perf/` now enforces both:
  - profile-relative slowdown limits on the stored local median timings
  - wide absolute-ms ceilings as catastrophic-regression watchdogs

Current watchdog ceilings in `tests/perf/baselines.json`:

- `tiny_dual_iso.full16.average_ms.max = 5000`
- `tiny_dual_iso.preview16.average_ms.max = 5000`
- `tiny_dual_iso.full8.average_ms.max = 1000`
- `tiny_dual_iso.preview8.average_ms.max = 1000`

Current measured averages on the tiny Dual ISO fixture:

- `full16 = 249.558 ms`
- `preview16 = 209.123 ms`
- `full8 = 28.402 ms`
- `preview8 = 7.460 ms`

One subtle testing detail is now frozen intentionally: the clip-backed
export golden in `tests/console/test_clip_golden.cpp` tracks the
subprocess (`QProcess`) batch-export seam, not a manually shell-launched
`MLVApp.exe` invocation, because those two paths produced different but
stable DNG hashes in this workspace.

## Final local follow-up (2026-04-21)

Another local performance/correctness pass is now landed on top of the
work above.

Newly implemented:

- checked-in larger perf fixture:
  - `tests/fixtures/clips/large_dual_iso.mlv`
  - `tests/fixtures/receipts/large_dual_iso_hq.marxml`
- llrawproc chroma-smooth scratch reuse:
  - `src/mlv/llrawproc/pixelproc.{h,c}`
  - `src/mlv/llrawproc/llrawproc.c`
- processing sharpen-mask Sobel scratch reuse:
  - `src/processing/sobel/{sobel.h,sobel.c}`
  - `src/processing/raw_processing.c`
  - `src/processing/processing_object.h`
- new regression coverage:
  - `tests/pipeline/test_processing_filters.cpp`
  - `tests/pipeline/test_dual_iso_pipeline.cpp`
- stabilized local perf gating:
  - `tests/perf/perf_main.cpp`
  - local profile checks now gate the stable 16-bit medians (`full16`,
    `preview16`) while still reporting 8-bit paths and guarding them with
    absolute ceilings and relative speedup floors

Latest enforced local perf check (`perf_tests --iterations 5 --require-baseline`):

- `tiny_dual_iso.full16 median_ms = 207.213`
- `tiny_dual_iso.preview16 median_ms = 171.586`
- `large_dual_iso.full16 median_ms = 205.578`
- `large_dual_iso.preview16 median_ms = 179.166`
- `tiny_dual_iso.preview16 speedup vs full16 = 1.196x`
- `large_dual_iso.preview16 speedup vs full16 = 1.171x`

What this changes in practice:

- the repo now has a broader local Dual ISO perf fixture instead of relying
  only on the tiny correctness clip,
- chroma smooth and sharpen-mask edge detection no longer pay their
  frame-sized temp allocation cost on every render,
- and the local perf gate is stable enough to enforce again without
  flaking on the noisy 8-bit median path.

## GPU presenter follow-up (2026-04-21)

The experimental OpenGL preview path is no longer only an 8-bit texture
handoff.

Newly implemented:

- `GpuDisplayViewport` now accepts direct 16-bit RGB frame presentation in
  addition to the legacy `QImage` handoff.
- `MainWindow` + `RenderFrameThread` can request/render `getMlvProcessedFrame16`
  for the experimental viewport when zebras/scopes are off.
- clean preview playback on the GPU-present path can now skip the display-side
  CPU 16->8 reduction entirely and hand the processed 16-bit frame straight to
  the presenter texture upload.

This is still not GPU debayer or GPU color processing. The GPU is still only
handling presentation/scaling, but the display seam is now materially closer to
the eventual 16-bit preview renderer than the earlier 8-bit-only texture path.
