## Direct-8 Loop Profiling (2026-04-24)

## 2026-05-30 - pink wash remains in playback preview; blur-prep hypothesis rejected

### Verified locally

- The current playback preview still shows a broad pink wash across the entire frame, not just a top stripe. I rechecked the current capture set from `.claude-state/profiling/20260530-shfix-check/` and the raw stage files for `M16-1446`.
- The raw preview evidence stays pink in both `S5_processed8` and `S6_displayImage`, while the export frame remains clean. The mean RGB for the preview stage is still heavily biased toward red and blue, which matches what I saw on screen.
- I tested the shadow/highlight blur-prep hypothesis by removing the playback-preview suppression in `src/processing/raw_processing.c`, rebuilding `platform/qt/build-release/release/MLVApp.exe`, and re-smoke-testing `M16-1446`. The frame stayed pink and the direct8 preview statistics did not meaningfully change, so that hypothesis is rejected.
- The current release build remained valid on the x1 Quality / settled Auto Look Assist smoke gate after the revert back to the safe baseline.

### Cross-checked from prior analysis

- Earlier export checks were already clean, so the artifact remains preview-only rather than a source-frame issue.
- The new observation corrects the earlier narrower description: the frame wash is broad enough that the entire preview reads pink, even if the top edge makes it easiest to spot.

### Needs runtime profiling

- Revisit the direct8 preview generation path in `src/processing/raw_processing.c` next.
- Compare preview artifacts against export or an earlier decode path to pinpoint where the pink enters.
- Keep the next pass focused on preview generation rather than more GUI handoff code; the blur-prep detour did not move the artifact.

### Ranked next steps

1. High impact / medium risk: re-trace the direct8 preview generation path and its playback-only guards.
2. Medium impact / low risk: keep the x1 Quality / Auto Look Assist smoke harness fixed for apples-to-apples comparisons.
3. Low impact / low risk: leave export untouched as the control path.

## Chroma 2x2 Median Helper (2026-05-30)

### Verified locally

- The accepted `chroma_smooth_med5` helper in [`src/mlv/llrawproc/chroma_smooth.c`](C:\!Layi Wkspc\MLV-App\src\mlv\llrawproc\chroma_smooth.c) remains the current keep-path for the 2x2 chroma smoother. It keeps the direct value-based median network and avoids the pointer-array `opt_med5` form on this hot path.
- The user-facing release exe is rebuilt at [`platform/qt/build-release/release/MLVApp.exe`](C:\!Layi Wkspc\MLV-App\platform\qt\build-release\release\MLVApp.exe), `LastWriteTime=5/30/2026 6:55:40 AM`, `Length=8791552`, `SHA256=9D91C53CB4B6C4C47B17C0DF59E773FD496279B8041B1525D3634DFAB906D713`.
- The x1 Quality visible smoke set with settled Auto Look Assist stayed valid on all three clips after the accepted helper was restored:
  - `M16-1327`: `9.688 fps`, `avg_mix_chroma_ms=26.804`, `avg_chroma_copy_ms=3.052`, `avg_chroma_fullres_ms=12.649`, `avg_chroma_halfres_ms=11.103`
  - `M16-1347`: `8.885 fps`, `avg_mix_chroma_ms=29.180`, `avg_chroma_copy_ms=3.056`, `avg_chroma_fullres_ms=13.213`, `avg_chroma_halfres_ms=12.910`
  - `M16-1446`: `13.093 fps`, `avg_mix_chroma_ms=0.000`

### Cross-checked from prior analysis

- The rejected `__restrict` aliasing-hint probe in the same file was not worth shipping. It regressed the chroma-heavy `M16-1327` clip to `8.697 fps` while leaving `M16-1347` at `8.989 fps` and `M16-1446` at `13.361 fps`, so it was backed out immediately.
- The accepted helper still looks like the right tradeoff: it is stable across the visible gate and keeps the current playback preview improvement without widening the blast radius.

### Needs runtime profiling

- The remaining hot path is still `avg_mix_chroma_ms`, not the tiny `chroma_copy` slice. The next safe gain likely needs a deeper loop-shape change or a row reuse strategy, but only if it can clear the three-clip playback gate again.
- Rejected a `final_blend_row_avx2` alias-map branch-hoist probe on the same visible GUI smoke set: the smoke state stayed valid, but the measured `avg_llrawproc_dual_iso_ms` regressed on `M16-1327` (`81.297 -> 106.766`) and `M16-1347` (`89.291 -> 91.185`) while only `M16-1446` improved (`56.803 -> 50.247`), so the probe was reverted immediately.

## Presentation Split Handoff (2026-04-24, current)

### Verified locally

- `render_thread_queue_wait_ms` is the current known blocker only when it is non-zero and sustained; in recent overlap evidence it is mostly `0.0`, so queue-depth expansion stays out of scope for this block unless telemetry turns it back on.
  - `.claude-state/profiling/20260423-frontback-overlap-v2/large_dual_iso_preview_t4_overlap_v2_run1.json` → median `0.0` (`n=16`)
  - `.claude-state/profiling/20260423-frontback-overlap-v7-keepcheck/large_dual_iso_preview_t4_overlap_v7_keepcheck_run1.json` → median `0.0` (`n=16`)
- `244c03a1` (immutable payload handoff) is now in-tree and satisfies the “no mutable render context at draw time” requirement:
  - `platform/qt/RenderFrameThread.h/.cpp` now emit request-level immutable `PresentationContext` and request metadata through the ready queue.
  - `platform/qt/MainWindow.cpp` now uses that context from `ReadyFrame`.
- Current async-prep WIP builds and app-profile smokes cleanly after the worker image lifetime fix:
  - Release build: `build-3slot/release/MLVApp.exe`.
  - Smoke outputs: `.claude-state/profiling/20260424-async-prep-smoke/tiny_dual_iso_async_prep_final_smoke.json` and `.claude-state/profiling/20260424-async-prep-smoke/large_dual_iso_async_prep_final_run1.json`.
  - Large Dual ISO run: 16 frames completed, warm `cadence_ms` median `47.5054`, warm `draw_frame_ready_image_ms` median `0.0`, final `prep_stale_drops` / replacement counters all `0`.
  - Worker image handoff now keeps local vector-backed `QImage` storage alive until packing and copies rows by `bytesPerLine()` so padded RGB888 images remain correct.
- Tier 1 measurement protocol from `.claude-state/profiling/20260424-m2-dualiso-lanes/areas.md` is now measured over 4 runs per case:
  - Artifacts and summary: `.claude-state/profiling/20260424-tier1-prefetch-measurement/summary.md`.
  - T0 (`--threads 1`): median warm cadence `46.6874 ms`.
  - T1 (`--threads 8`): median warm cadence `33.5722 ms`; direct-8 color median drops from `15.25 ms` to `3.0 ms`.
  - T2 (`--threads 1`, processed8 prefetch): median warm cadence `48.2558 ms`, processed8 hits `0/48`.
  - T3 (`--threads 8`, processed8 prefetch): median warm cadence `35.8976 ms`, processed8 hits `1/48`.
  - T4 (`--threads 8`, raw16 prefetch): median warm cadence `18.8752 ms`, raw16 hits `48/48`, raw decompress median `0.0 ms`.
  - Conclusion: raw16 prefetch plus 8 threads clears M2 strongly; processed8 prefetch does not help this profile shape and regresses against T1.

### Cross-checked from prior analysis

- `MainWindow::drawFrameReady()` is still the boundary where the largest fixed GUI work remains (`drawFrameReady.image`), but it is now structurally splittable.
- `drawFrameReady()` currently performs both:
  - presentation-prep work (image construction, scaling, zebras, image cache writes), and
  - final present/overlay/scope side effects.
  This is the right next hard split point.
- Dual-ISO sync scope:
  - `platform/qt/MainWindow.cpp:11020-11054` is **not** pure display; it includes runtime state writes (`ACTIVE_RECEIPT`, `m_pMlvObject->llrawproc` fields), so keep it in the final commit and preserve exact behavior.
- Zebras/scope execution:
  - `MainWindow::drawZebras` and scope widgets (`ui->labelScope->setScope(...)`) are still in GUI-thread-only paths today.
  - The split should avoid moving scope painting into a non-UI worker in this milestone.

### Plan (next 2 commits)

#### Locked execution order

1. **Commit A — payload boundary hardening (tracked first)**
   - Keep frame handoff immutable and side-effect free on the producer.
   - Implement stale-serial guards before introducing async delivery.
   - Target symbols:
     - `platform/qt/MainWindow.h`: `PlaybackPrepTask`, `PlaybackPrepResult`, private worker state, method declarations.
     - `platform/qt/MainWindow.cpp`: constructor/destructor boot/shutdown for worker thread.
2. **Commit B — async `draw_frame_ready_image` split**
   - Move image prep work off the GUI thread path behind serial-safe queue.
   - Preserve `draw_frame_ready_scene`, `draw_frame_ready_present`, `draw_frame_ready_scopes`, `draw_frame_ready_overlay`, and all Dual-ISO widget sync and audio/scope side effects on the GUI thread.
   - Target symbols:
     - `platform/qt/MainWindow.cpp: drawFrameReady`, `enqueuePlaybackPrepTask`, `playbackPrepThreadLoop`, `buildPlaybackPrepResult`, `onPlaybackPrepResultReady`, `presentPlaybackPreparedFrame`.
     - `platform/qt/MainWindow.cpp`: telemetry keys `draw_frame_ready_image_ms`, `draw_frame_ready_present_ms` may be reported from split stages, but keep profile payload contract and key names stable.

1. **Payload commit already done (done)**
   - Commit: `244c03a1` (`Playback: pass immutable presentation context with ready frame`)
   - Scope: request-level immutability + slot metadata for safe overlap.

2. **Commit B — front/back split (single bounded async prepare stage)**
   - **Scope now, implementation split exactly into two parts:**
     - `platform/qt/RenderFrameThread.h/.cpp`:
       - Keep as-is from `244c03a1` for immutable context.
       - No slot-depth changes in this commit.
     - `platform/qt/MainWindow.h/.cpp`:
       - add `struct PlaybackPreparedFrame` (output-only result of expensive prep, no UI side-effects).
       - add `struct PlaybackPrepRequest` (captured, immutable request context + frame pointers + geometry booleans).
       - add one bounded worker queue (`optional current + optional pending`) and one background thread loop that:
         - takes one request,
         - runs pure compute,
         - posts completion back to UI with `QMetaObject::invokeMethod(..., Qt::QueuedConnection, ...)`.
       - add `bool isPlaybackPrepReadyForAsync(const RenderFrameThread::ReadyFrame &, const PresentationRequestContext &) const`.
       - add `PlaybackPreparedFrame buildPlaybackPreparedFrame(const PlaybackPrepRequest &, const RenderFrameThread::ReadyFrame &, const PresentationRequestContext &)`.
       - add `void presentPreparedPlaybackFrame(PlaybackPreparedFrame &&)`.
       - add `void finishPlaybackFrameFromPrepared(...)` helper to own only final `recordPresentedFrame/finishPresentedFrame` + scene/scopes/overlay/present flow.
   - **What runs in background**
     - `MainWindow::drawFrameReady()` extraction boundary:
       - pure image preparation from source pixels to final `QImage`/`underOver` bundle, including:
         - pre-scaled fast path `QImage` wrapping,
         - `build_fast_playback_scaled_image` fallback path,
         - `scanZebrasRgb8` and `drawZebras` raster pass.
       - no GUI object reads/writes except captured values from context.
     - keep UI-thread boundary for:
       - scene geometry application,
       - `GpuDisplayViewport::presentImage` / `presentRgb16` call,
       - audio sync and overlay label updates,
       - scope widgets and dual-iso edit widgets (`ACTIVE_RECEIPT`/`llrawproc` sync),
       - `recordPresentedFrame` / `finishPresentedFrame` state updates.
   - **Data-race protection rule (important)**
     - do not pass raw slot pointers to worker without ownership transfer.
     - for async path, use `PlaybackPreparedFrame` inputs built from copied byte vectors when `readyFrame.playbackFastScaleActive`:
       - copy of `readyFrame.playbackScaledImage8` into request-owned buffer before worker use.
     - if copy path cannot be enabled for a case, force that frame down legacy synchronous path and keep behavior unchanged.
   - **Execution order inside `drawFrameReady()`**
     - keep metadata sync and `timerFrameEvent()` queue-advance exactly as today.
     - if `playbackPolicyActive()` and async-prep guard passes:
       - build/queue prep request and return quickly after scheduling.
       - on completion invoke `presentPreparedPlaybackFrame(...)` on UI thread.
     - else:
       - preserve current synchronous branch unchanged.

### Needs runtime profiling

- Keep same command and filtering:
  - `--profile-playback --input tests/fixtures/clips/large_dual_iso.mlv --receipt tests/fixtures/receipts/large_dual_iso_hq.marxml --frames 16 --threads 1 --raw-cache-mb 0`
  - warm filter `sample_index >= 4`
- Capture under `.claude-state/profiling/20260424-payload-split-v1/`.
- Compare paired 4-run medians:
  - `cadence_ms`
  - `render_thread_work_ms`
  - `draw_frame_ready_image_ms`
  - `draw_frame_ready_present_ms`
- Decision gates:
  - if `cadence_ms <= 41.7 ms` warm median → M1 accepted for this block,
  - else proceed to Finding #4 (playback preview quality mode) for clip set that is not already fast-path eligible.

### Non-negotiables before merge

- No behavior change for pause/export/CPU fallback paths.
- Keep Dual-ISO UI sync writes in place for now (separate setting-controlled optimization only later).
- Keep scoped telemetry updates and any new split-stage timing under the existing profile contract.

## Playback Realization Plan (2026-04-24)

### Verified locally

- M1 is not the immediate blocking constraint anymore on this branch's current evidence set; the structural bottleneck for M2 is still end-to-end handoff overlap and presentation work, not raw decode math.
- Existing keep-set is still valid:
  - direct processed8 fast path remains active and bit-identical when enabled.
  - `getMlvLastProcessed8DirectPathActive` gating and curve/hash parity guards are in place.
  - queue/slot overlap in `RenderFrameThread` is now safer than earlier WIP (no visible corruption from the reviewed overlap patches).
- Repeated crashes/popups from Qt DLL lookup are still environment/runtime-path sensitive (not source-level functional bugs), and should be handled via run-env bootstrapping scripts rather than UI logic changes.
- Quick Step-0 telemetry check: warm `render_thread_queue_wait_ms` medians are still mostly `0.0` across recent runs (rare one-frame spikes only, not a sustained stall pattern), so we should prioritize payload immutability + presentation split before more queue-depth tuning.

### Cross-checked from prior analysis

- The dominant remaining cost is the serialized boundary around `MainWindow::drawFrameReady()` despite overlap in `RenderFrameThread`.
- The shared mutable handoff is still both:
  - the live `m_pRawImage/m_pRawImage16` buffers and
  - render-policy state (`m_renderThreadUsing*`, active receipt fields, llrawproc mutators in playback path).
- A correct M2 pass needs one immutable per-frame payload contract so we can decouple render completion from presentation consumption.

### Needs runtime profiling

- Keep the same-session paired protocol from earlier as a hard rule: warm medians on:
  - `--profile-playback --input tests/fixtures/clips/large_dual_iso.mlv --receipt tests/fixtures/receipts/large_dual_iso_hq.marxml --frames 16 --threads 1 --raw-cache-mb 0`
  - warm filter `sample_index >= 4`
  - runs A/B within one profile session per code change.
- Priority order for this next block (ranked by impact/effort):

  1. **Per-frame render snapshot object** (`platform/qt/MainWindow.h/.cpp`):
     - Introduce an immutable `RenderedFramePayload` (QImage/QImage16 copy or shared-pointer image blob, frameNumber, outputMode, display signature, all render-policy fields needed for present/scopes).
     - Emit payload from `RenderFrameThread` via `frameReady` arguments instead of re-reading mutable `MainWindow` state.
     - `drawFrameReady()` should validate generation before consuming.
  2. **Presentation tail offload to prep worker** (`platform/qt/MainWindow.cpp`):
     - Keep decode/debayer/processing on render thread.
     - Move image build/scaling/caching work into a bounded producer-consumer step that can run while next frame rendering advances.
     - Keep final UI presentation (scene/layout/overlay/scope paint and `draw()` path) on GUI thread and short.
  3. **Render/ready queue depth bump** (`platform/qt/RenderFrameThread.h/.cpp`):
     - Expand frame slot depth so render can stay ahead of present:
       - at least 3 slots first (1 rendering, 1 ready, 1 presenting/standby)
       - consider 4 only if telemetry shows queue starvation persists.
     - Preserve invariant: no slot reused while presenting or marked ready.
  4. **Playback subset processing gate refinement** (`src/processing/raw_processing.c`, `platform/qt/MainWindow.cpp`, tests):
     - Add a constrained playback-only processing-mode branch only after step 1/2 are stable.
     - Preserve pause/export unchanged.
     - Gate by explicit playback-fast-path predicate and add explicit telemetry key showing branch usage.
  5. **Re-introduce AVX2 only after structural gains** (`src/processing/raw_processing_8bit_kernel.inc`):
     - Keep dispatcher + parity tests, but only attempt hand-tuned intrinsics if the above reduces cadence sufficiently and M2 remains open.
     - Treat any AVX2 gain below 5% on paired `cadence_ms` as non-actionable noise.

- Safety rails to preserve during all steps:
  - never mix "display state" writes (scope updates, UI sync, receipt writes) into the critical render/present boundary.
  - continue preserving Dual ISO preview correctness contract and restore previous state on playback stop.
  - if Dual ISO UI sync is optimized, split into:
    - widget reflection-only defer-able block (can be delayed/throttled), and
    - llrawproc/receipt state writes (must remain exact unless scoped behind explicit advanced preference).

### Definition of done

- M2 acceptance on this VM:
  - `cadence_ms` warm median `<= 33.3 ms` across 4 paired runs.
  - all existing golden tests green:
    - `console_tests --check-golden`
    - `pipeline_tests --check-golden`
    - app-backed sanity pass that previously passed on this branch family.
- Mandatory run-artefact capture:
  - `.claude-state/profiling/` run trees with summary+raw JSON for each side of A/B step.
- Post-step notes update:
  - `Dual ISO playback hypotheses` + `ANALYSIS_LOG.md` append with outcome and next ranked target.

### Verified locally

- Update from same-session re-run on this VM/worktree (`ed3e5a17^` vs `ed3e5a17`) with `--threads 1 --raw-cache-mb 0 --frames 16` and warm filter `sample_index >= 4`:
  - `cadence_ms`: base `47.8288`, inline `48.0156` (Δ `+0.1868`, `+0.39%`).
  - `processed8_total_ms`: base `45.75`, inline `46.0` (Δ `+0.25`, `+0.55%`).
  - `processing_core_color_ms`: base `15.75`, inline `16.0` (Δ `+0.25`).
  - `raw_uint16_ms`: base `17.0`, inline `16.75` (Δ `-0.25`).
  - primary data in:
    - `.claude-state/profiling/20260424-reinhard-inline-pair/re-runs/base/run{1..4}.json`
    - `.claude-state/profiling/20260424-reinhard-inline-pair/re-runs/inline/run{1..4}.json`
    - `.claude-state/profiling/20260424-reinhard-inline-pair/re-runs/summary-stats.csv`
  - interpretation: this pass is not a robust perf win; cadence moved within noise and should not be shipped as an independent claim.

- `ReinhardTonemap_f` is now inlined on its definition side:
  - `src/processing/processing.c:126` changed from external function to `static inline`.
  - `src/processing/raw_processing.h:431` prototype removed, so direct-8 call sites no longer rely on TU-level external linkage.
- Direct-8 sub-loop telemetry is wired in-tree and can emit:
  - `processing_direct8_matrix_ms`
  - `processing_direct8_gamma_ms`
  - `processing_direct8_curves_ms`
- The telemetry wiring is visible in:
  - `src/processing/raw_processing.c` (timing split + getters + environment-gated probe path).
  - `platform/qt/RenderFrameThread.cpp` (per-slot stage telemetry export).
  - `src/processing/raw_processing_8bit_kernel.inc` (probe-only branch under `MLVAPP_PROFILE_DIRECT8_SUBLOOPS`).
- Historical paired evidence (outdated; kept for audit only):
  - Same-session paired build/profile evidence now exists for current-tree baseline vs inline pass:
  - Baseline branch built from `ed3e5a17^`.
  - Inline branch built from `ed3e5a17` with `ReinhardTonemap_f` inlined.
  - Runs executed on `C:\!Layi Wkspc\MLV-App\.claude\worktrees\festive-boyd-integration` with:
    - `--profile-playback --input tests/fixtures/clips/large_dual_iso.mlv --receipt tests/fixtures/receipts/large_dual_iso_hq.marxml --frames 16 --threads 1 --raw-cache-mb 0`
  - Warm medians over 4 runs each (sample_index>=4), using `sample_index` filtered frame medians:
    - `cadence_ms`: base `46.6504`, inline `45.9953` (Δ `-0.6551`, `-1.40%`, same-session 95% mean interval roughly `±4.0ms` base / `±1.87ms` inline across 4 medians).
    - `processed8_total_ms`: base `44.5000`, inline `43.7500` (Δ `-0.75`, `-1.69%`).
    - `processing_core_color_ms`: `15.0001` both (no delta).
    - `raw_uint16_ms`: base `17.0000`, inline `16.5000` (Δ `-0.50`).
- Artifacts captured here:
  - `.claude-state/profiling/20260424-reinhard-inline-pair/base-run1.json`
  - `.claude-state/profiling/20260424-reinhard-inline-pair/base-run2.json`
  - `.claude-state/profiling/20260424-reinhard-inline-pair/base-run3.json`
  - `.claude-state/profiling/20260424-reinhard-inline-pair/base-run4.json`
  - `.claude-state/profiling/20260424-reinhard-inline-pair/inline-run1.json`
  - `.claude-state/profiling/20260424-reinhard-inline-pair/inline-run2.json`
  - `.claude-state/profiling/20260424-reinhard-inline-pair/inline-run3.json`
  - `.claude-state/profiling/20260424-reinhard-inline-pair/inline-run4.json`
  - `.claude-state/profiling/20260424-reinhard-inline-pair/summary.md`
  - `.claude-state/profiling/20260424-reinhard-inline-pair/summary.json`
- Interpretation:
  - Inline Reinhard flattening is below the 5% keep threshold and inside measured run variance; do not ship as an independent performance claim.
  - It remains behavior-safe and useful only as a cleanup hypothesis, with priority lower than queue-depth/structural work at this point.

### Cross-checked from prior analysis

- This matches the static hypothesis ordering: matrix/tonemap math is the highest-confidence target before wider SIMD work, and creative curves are effectively inactive on the current Dual ISO preview receipt (`processing_direct8_curves_ms` is expected to remain zero in this shape).
- The direct-8 split hooks were added with the existing keep-set in place (no behavior change to pause/export pathways), and this aligns with the M1 milestone constraint.

### Needs runtime profiling

- This rerun is complete on this branch (`re-runs/summary-stats.csv`).
- Next micro-pass should remain gated by the same rule: only proceed if paired end-to-end `cadence_ms` gain is >= 5%.

### Needs runtime profiling

- Direct8 sub-loop telemetry (`processing_direct8_*`) did not materially move this clip in honest same-session A/B and therefore is not the first-order target for the immediate 30fps stretch pass.
- Re-prioritize remaining work to:
  - deepen playback overlap queue depth beyond current slot depth,
  - keep/reduce render-slot presentation work only when it contributes additional overlap,
  - pursue AVX2 dispatch only after a structural step shows stable headroom.

### Visual artifact check (2026-05-30)

#### Verified locally

- Live screen captures taken during `--gui-smoke-playback` on `M16-1446.MLV` show a magenta/pink horizontal band across the upper portion of the rendered video area.
- The same artifact remained after removing the borrowed playback-scaled RGB8 handoff in `platform/qt/MainWindow.cpp`, so the band is not explained by that one lifetime optimization.
- The current evidence is ambiguous between actual clip content and an upstream preview/color path issue; telemetry alone does not distinguish those two.

#### Needs runtime profiling

- Compare the same frame against an export or an alternate decode path before attributing the band to the playback pipeline.
- If the band is source content, keep the current playback speed work unchanged.
- If the band is generated by preview processing, narrow the culprit to the playback-scene conversion path before touching more hot loops.

#### Follow-up result

- The borrowed-vs-owned playback handoff check was reverted after it did not change the visible band and reduced playback throughput on the smoke set.
- Fresh smoke after the revert returned the build to the prior fast path:
  - `M16-1446`: `presented_fps=7.861`, `avg_llrawproc_ms=53.889`, `avg_draw_total_ms=26.063`
- The pink band remains unresolved and still needs a source-frame comparison before we can call it a pipeline bug.

## Safe Overlap + Fast-Scale Keep Point (2026-04-23, current)

### Verified locally

- The current safe keep-set on this branch is now narrower and more honest than the earlier overlap WIP.
  - `platform/qt/RenderFrameThread.cpp` keeps the wait-condition worker wakeup plus the active-request snapshot, and now restores the old external exclusivity contract by making `RenderFrameThread::lock()` wait for true worker idleness before returning.
  - `platform/qt/MainWindow.cpp` keeps the end-to-end `drawFrameReady()` split (`scene`, `image`, `present`, `scopes`, `overlay`), the scene-rect guard, playback display-preview-cache bypass, the post-`emit frameReady()` continuation boundary, the headless profiling determinism fix, and the fast playback scaler.
  - `src/processing/raw_processing.c` now exposes `processingResetLastTimingTelemetry()` so cache-hit/direct-path samples stop reporting stale substage timings.
- The fast playback scaler is now slightly cheaper in the common playback path.
  - `platform/qt/MainWindow.cpp:132-212` now reuses precomputed `x`/`y` source-index maps for each `(sourceWidth, sourceHeight, targetWidth, targetHeight)` tuple instead of paying the divide/min math inside the inner pixel loop on every frame.
- The render-thread correctness regression from the unlocked WIP is fixed for the kept path.
  - `platform/qt/RenderFrameThread.h` / `platform/qt/RenderFrameThread.cpp` now make `lock()` wait until `!(m_renderFrame || m_renderingFrame)` before granting exclusive access again.
  - `platform/qt/MainWindow.cpp:632-645` now waits for the in-flight frame to drain before `resizeEvent()` queues a replacement render, so a resize no longer stomps the shared display buffers before the queued `drawFrameReady()` consumes them.
- Processed8 playback prefetch is now intentionally treated as experimental-only, not part of the kept default path.
  - `src/mlv/video_mlv.c` now gates the worker behind `MLVAPP_EXPERIMENTAL_PROCESSED8_PREFETCH`.
  - The default path does not start that worker and does not accept prefetched hits unless the environment variable is explicitly enabled.
  - The speculative lookahead is also reset back to `2` for the opt-in experiment path.
- The processed-frame state signature had one real llrawproc hole independent of the prefetch decision.
  - `src/mlv/video_mlv.c` now hashes `llrawproc->diso_pattern` inside `mlv_hash_llrawproc_state(...)`, so processed frame cache keys track that runtime state as well.
- Fresh safe-path large Dual ISO artifacts with processed8 prefetch forced off live in:
  - `.claude/profiling/20260423-safe-overlap-fastscale/large_dual_iso_preview_t4_safe_run1.json`
  - `.claude/profiling/20260423-safe-overlap-fastscale/large_dual_iso_preview_t4_safe_run2.json`
  - `.claude/profiling/20260423-safe-overlap-fastscale/large_dual_iso_preview_t4_safe_run3.json`
  - `.claude/profiling/20260423-safe-overlap-fastscale/large_dual_iso_preview_t4_safe_run4.json`
- Warm medians from those runs (discard `5`, `--threads 4`, `--frames 16`, `processed8_prefetch_hit = false` on all warm samples) are currently:
  - run1: `cadence_ms 53.903`, `processed8_total_ms 39.000`, `render_thread_work_ms 39.000`, `draw_frame_ready_total_ms 14.000`
  - run2: `cadence_ms 70.897`, `processed8_total_ms 48.000`, `render_thread_work_ms 48.000`, `draw_frame_ready_total_ms 17.000`
  - run3: `cadence_ms 68.076`, `processed8_total_ms 47.000`, `render_thread_work_ms 47.000`, `draw_frame_ready_total_ms 16.000`
  - run4: `cadence_ms 49.614`, `processed8_total_ms 37.000`, `render_thread_work_ms 37.000`, `draw_frame_ready_total_ms 12.000`
- Honest safe claim after those reruns:
  - the kept overlap/presentation work is still real; the low-end safe runs (`49.6-53.9 ms`) beat the older `59.299 ms` direct-8-bit baseline without relying on processed8 background rendering
  - the result is not stable enough to claim `24 fps` on this VM yet; the safe path still misses the `41.708 ms` native budget and the run-to-run spread is wide
  - the critical path is still structurally serialized: `render_thread_work_ms + draw_frame_ready_image_ms`, because the renderer and presenter still share a single live output buffer
- Fresh current-tree validation after the kept overlap fixes, processed8 prefetch gate, `diso_pattern` hash fix, and updated scaffold assertions:
  - plain `console_tests --check-golden`: `41 tests / 160 assertions / 17 skips / 0 failures`
  - app-backed `console_tests --check-golden` with `platform/qt/build-codex-current/release/MLVApp.exe`: `41 tests / 750 assertions / 1 skip / 0 failures`
  - `pipeline_tests --check-golden`: `46 tests / 526 assertions / 4 skips / 0 failures`

### Cross-checked from prior analysis

- The processed8 prefetch audit narrowed the real blocker.
  - `src/mlv/video_mlv.c` already hashed `use_amaze`, `ca_red`, `ca_blue`, and most of `llrawproc`.
  - The remaining correctness blocker is not â€œmissing all raw/debayer stateâ€ but rather â€œworker still renders against shared live per-frame/raw state.â€
  - The concrete holes identified on the current code are:
    - `llrawproc->diso_pattern` was missing from the hash and is now fixed
    - per-frame `video->VIDF.panPosX` / `panPosY` are still read from shared live state during llrawproc interpolation, so processed8 prefetch is not field-safe enough to ship default-on without a real raw/VIDF snapshot
- The presentation-side audit also confirmed the current hot UI bucket.
  - On the safe path, `draw_frame_ready_present_ms` is effectively zero on the large preview runs above.
  - The common-path UI cost is still `draw_frame_ready_image_ms`, not `setPixmap()` / viewport presentation.

### Needs runtime profiling

- The next honest performance step is a real front/back buffer handoff plus per-frame presentation metadata, so the cadence ceiling becomes `max(render_thread_work_ms, drawFrameReady_tail_ms)` instead of their sum.
- If the branch still misses `24 fps` after that buffer/pipeline split, the next safe presentation trims to revisit are:
  1. a cheaper fast-scaler execution model if the current OpenMP wakeup cost is part of the VM variance
  2. cached scope backing images / grids when scopes are visible
  3. zebra reduction folded into an existing RGB8 conversion/scale pass instead of a separate scan

## Render/Present Handoff Audit (2026-04-23, current)

### Verified locally

- Playback is still explicitly serialized at the Qt handoff boundary.
  - `timerFrameEvent()` bails out when `m_frameStillDrawing` is true and only records `m_playbackFrameAdvancePending` during playback (`platform/qt/MainWindow.cpp:533-545`).
  - `drawFrame()` sets `m_frameStillDrawing = true` before queueing the worker request (`platform/qt/MainWindow.cpp:1119-1121`).
  - `drawFrameReady()` clears it only after image build, present, scopes, overlay, `emit frameReady()`, and the optional next-frame kickoff (`platform/qt/MainWindow.cpp:11286-11405`).
  - Resize/open paths also wait on `m_frameStillDrawing` and/or `RenderFrameThread::lock()` / `isIdle()` before touching render-owned state (`platform/qt/MainWindow.cpp:632-640`, `platform/qt/MainWindow.cpp:1868-1871`).
- The concrete shared display buffers are the single `MainWindow::m_pRawImage` / `m_pRawImage16` allocations.
  - They live on `MainWindow` (`platform/qt/MainWindow.h:561-562`), are passed into `RenderFrameThread::init(...)` (`platform/qt/RenderFrameThread.cpp:52-58`), and are stored as worker members (`platform/qt/RenderFrameThread.h:60-62`).
  - The worker writes them in `RenderFrameThread::drawFrame()` through `getMlvProcessedFrame16(...)`, `getMlvRawFrameDebayered(...)`/GPU bilinear fallback, and `getMlvProcessedFrame8(...)` (`platform/qt/RenderFrameThread.cpp:233-312`).
  - `drawFrameReady()` reads those same addresses for presentation and scopes via `rgb8DisplaySource = m_pRawImage`, `GpuDisplayViewport::presentRgb16(..., m_pRawImage16, ...)`, and `ui->labelScope->setScope( m_pRawImage, ...)` (`platform/qt/MainWindow.cpp:10957`, `11078-11085`, `11317-11331`).
  - `GpuDisplayViewport::setPresentedImage(...)` / `setPresentedRgb16(...)` already copy into owned viewport storage (`platform/qt/GpuDisplayViewport.cpp:424-468`), so the current serialization blocker is not the viewport widget itself but the shared raw buffer that `drawFrameReady()` still borrows through no-copy `QImage` wrappers and scope generation.
- The handoff metadata is also global, not per-frame.
  - `drawFrame()` stores request-specific policy in `m_renderThreadUsing*` and `m_lastQueuedGpu*` members (`platform/qt/MainWindow.cpp:1149-1179`; `platform/qt/MainWindow.h:601-623`), then `drawFrameReady()` re-reads those shared members later (`platform/qt/MainWindow.cpp:10967-11080`).
  - `RenderFrameThread::frameReady` carries no payload (`platform/qt/RenderFrameThread.h:55`; `platform/qt/MainWindow.cpp:417`), so the UI side has to pull "last frame" data from mutable shared fields.
  - Worker-side per-frame telemetry and fallback data live in mutable `m_last*` members (`platform/qt/RenderFrameThread.h:75-87`, `platform/qt/RenderFrameThread.cpp:220-228`, `635-646`) and are later read from `MainWindow` (`platform/qt/MainWindow.cpp:10936`, `1506-1566`).
- `drawFrameReady()` still derives presentation identity from live UI/MLV state instead of an immutable render result.
  - The displayed frame number comes from `ui->horizontalSliderPosition` / `m_newPosDropMode`, not from the worker (`platform/qt/MainWindow.cpp:10924-10926`).
  - The display cache key reads live `m_pMlvObject->current_processed_frame_8bit_signature` / `current_processed_frame_signature` (`platform/qt/MainWindow.cpp:11060-11069`), which the MLV pipeline updates when new processed results are produced (`src/mlv/video_mlv.c:2459-2462`, `2520-2523`, `1305-1308`).
- There are real presentation-path state mutations that would race with overlapping render work.
  - `drawFrameReady()` mutates `ACTIVE_RECEIPT` and `m_pMlvObject->llrawproc` for Dual ISO auto-correction publication (`platform/qt/MainWindow.cpp:11022-11053`).
  - When playback stops, `drawFrameReady()` also restores debayer / Dual ISO runtime state via `selectDebayerAlgorithm()` and `applyEffectiveDualIsoPlaybackSettings()` (`platform/qt/MainWindow.cpp:11381-11386`), and that helper resets processing/cache state (`platform/qt/MainWindow.cpp:9978-9992`).
- There is already an immutable-copy pattern in the paused preview cache, but playback deliberately bypasses it.
  - `DisplayPreviewCacheEntry` stores copied `QImage`/`QPixmap` plus cache metadata (`platform/qt/MainWindow.h:520-531`).
  - Playback disables that path with `displayPreviewCachingAllowed = !playbackPolicyActive()` (`platform/qt/MainWindow.cpp:11072`, `11249-11280`).
- `MainWindow::resizeEvent()` is correct on the current safe path, but only because it still drains the single live handoff before redrawing.
  - `platform/qt/MainWindow.cpp:632-640` waits for `RenderFrameThread::lock()` / `unlock()` and then spins on `m_frameStillDrawing` before calling `drawFrame()`.
  - That is sufficient today because `m_frameStillDrawing` stays true until `drawFrameReady()` has finished consuming the shared output (`platform/qt/MainWindow.cpp:1121`, `11400`), and `RenderFrameThread::frameReady` still implies exactly one ready frame slot.
  - Safe conclusion: there is no new resize race in the staged keep-set, but a future double-buffer design needs a real frame-generation / slot token so a stale pre-resize `frameReady` cannot present after the resize-triggered rerender.

### Cross-checked from prior analysis

- The earlier "single live output buffer" diagnosis is still right, but the exact serialization unit is broader: one live pixel-buffer pair plus one live "last frame/request" metadata bundle.
- The Dual ISO UI-sync audit still matters here: if `drawFrameReady()` keeps mutating `llrawproc` / receipt state mid-presentation, safe overlap requires either a render-request state snapshot or deferring those writes until no render is in flight.
- The current `resizeEvent()` drain is evidence that the existing code already assumes "at most one ready frame plus one buffer owner." Any double-buffer step needs to preserve that user-visible safety via explicit generations rather than by leaning on the old implicit singleton contract.

### Needs runtime profiling

- The smallest safe overlap experiment is a `RenderedFrameSnapshot` or front/back slot that owns:
  - one pixel payload (`rgb8` or `rgb16`, depending on output mode)
  - frame index
  - render output mode
  - presentation policy/config (`m_renderThreadUsing*`, `m_lastQueuedGpu*`)
  - render telemetry / GPU fallback data now stored in `RenderFrameThread::m_last*`
  - a stable display signature captured when the render finishes
- The resize/stop/clip-switch paths should be re-audited after that handoff exists, with a specific check that stale queued `frameReady` deliveries can be dropped by generation instead of being inferred away by `m_frameStillDrawing`.
- If that lands, the next honest check is whether cadence shifts toward `max(render_thread_work_ms, draw_frame_ready_total_ms)` without reintroducing UI/`llrawproc` mismatches.

## Qt Playback Overlap WIP Audit (2026-04-23, current)

### Verified locally

- The new `timerFrameEvent() -> drawFrameReady()` continuation path in `platform/qt/MainWindow.cpp` changes ordering, not just scheduling.
  - `platform/qt/MainWindow.cpp:11224-11230` now calls `timerFrameEvent()` synchronously from inside `drawFrameReady()` after scopes, but before audio sync, frame-number label updates, playback-stop restoration, `notePlayToFirstFramePresentation(...)`, and `emit frameReady()`.
  - `timerFrameEvent()` immediately runs `playbackHandling(...)` at `platform/qt/MainWindow.cpp:463-464`, which can advance `ui->horizontalSliderPosition` / `m_newPosDropMode` via `platform/qt/MainWindow.cpp:2187-2223`, and may queue the next render via `drawFrame()` at `platform/qt/MainWindow.cpp:475-489` and `platform/qt/MainWindow.cpp:1024-1145`.
  - Because `drawFrameNumberLabel()` reads `ui->horizontalSliderPosition->value()` at `platform/qt/MainWindow.cpp:6729-6735`, and audio sync uses `m_newPosDropMode` at `platform/qt/MainWindow.cpp:11235-11240`, the current frame can now be presented with next-frame metadata/audio state layered on top.
- The continuation path also weakens the old `MainWindow::frameReady()` completion contract.
  - `drawFrameReady()` now sets `m_frameStillDrawing = false`, immediately starts the next `timerFrameEvent()`/`drawFrame()` when pending, and only later emits `frameReady()` at `platform/qt/MainWindow.cpp:11223-11230` and `platform/qt/MainWindow.cpp:11290-11293`.
  - Any observer treating `MainWindow::frameReady()` as “current frame done and no new render in flight yet” no longer gets that guarantee once playback continuation is active.
- I did not find a direct headless-profiling break from this new continuation path itself.
  - The continuation is gated on `ui->actionPlay->isChecked()` in `platform/qt/MainWindow.cpp:11224-11225`.
  - Headless playback profiling uses `m_headlessPlaybackProfileUsePlaybackPolicy` while leaving `ui->actionPlay` false (`platform/qt/MainWindow.cpp:784-786`, `platform/qt/MainWindow.cpp:853-899`, `platform/qt/MainWindow.cpp:1148-1155`), so the synchronous continuation path should stay inactive during `runHeadlessPlaybackProfile(...)`.
- The render-thread wait-condition rewrite is directionally fine, but it still emits `RenderFrameThread::frameReady()` while the worker owns `m_mutex`.
  - `platform/qt/RenderFrameThread.cpp:155-166` holds `m_mutex` across `drawFrame()`, and `platform/qt/RenderFrameThread.cpp:599-606` emits `frameReady()` before the loop releases that mutex again.
  - The current headless direct-connection lambda only stores an atomic timestamp, so it is safe today, but any future direct slot that calls back into `lastFrameReadyEmitStageTime()`, `isIdle()`, or other lock-taking getters would deadlock on this signal path.

## Qt Playback Overlap Re-Audit (2026-04-23, current)

### Verified locally

- The queued post-frame boundary in `MainWindow::drawFrameReady()` fixes the earlier UI/audio ordering issue.
  - `platform/qt/MainWindow.cpp:11282-11290` now emits `MainWindow::frameReady()` first, then posts the next `timerFrameEvent()` with `QMetaObject::invokeMethod(..., Qt::QueuedConnection)`, so the old synchronous continuation bug is gone.
- The `RenderFrameThread::frameReady()` emit is now out from under the mutex, but the render-thread rewrite still has two blocking correctness risks:
  - `platform/qt/RenderFrameThread.cpp:164-166` unlocks before `drawFrame()`, while `platform/qt/RenderFrameThread.cpp:76-81` still lets `renderFrame(...)` overwrite shared request fields (`m_frameNumber`, `m_outputMode`, `m_useGpuBilinearDebayer`, `m_frameRequestStageTime`) under that same mutex.
  - `drawFrame()` then reads those same fields without any local snapshot at `platform/qt/RenderFrameThread.cpp:181-205` and throughout the rest of the function.
  - Safe conclusion: a second `renderFrame(...)` call can now race with an in-flight `drawFrame()` and change which frame / mode is being rendered mid-flight.
- `RenderFrameThread::isIdle()` no longer reports actual worker idleness.
  - `platform/qt/RenderFrameThread.cpp:95-100` still defines idle as `!m_renderFrame`.
  - But `platform/qt/RenderFrameThread.cpp:164-166` now clears `m_renderFrame = false` before the expensive `drawFrame()` work starts.
  - So `isIdle()` becomes true while the worker is still actively rendering.
  - This is already relied on by GUI code to gate state mutation and teardown:
    - `platform/qt/MainWindow.cpp:7536-7547`
    - `platform/qt/MainWindow.cpp:7569-7580`
    - `platform/qt/MainWindow.cpp:7587-7608`
    - `platform/qt/MainWindow.cpp:7613-7618`
    - `platform/qt/MainWindow.cpp:12247-12250`
    - `platform/qt/MainWindow.cpp:1773-1776`
  - Safe conclusion: callers can now reset caches, mutate llrawproc/processing state, or begin teardown while render-thread work is still using those structures.

### Needs runtime profiling

- Re-audit after the render thread either:
  - snapshots all request fields into locals before unlocking, and
  - introduces a real “worker busy” state for `isIdle()`, or delays clearing `m_renderFrame` until the render work actually completes.

### Needs runtime profiling

- If the overlap path is kept, move the continuation trigger to after the current frame’s overlay/state-finalization work, or split overlay work so any parts that read playback position/audio state stay ahead of the next-frame kickoff.
- If `MainWindow::frameReady()` is still meant to mean “frame presentation is fully complete,” restore that ordering before more profiling is built on top of it.

## Dual ISO Playback UI Sync Audit (2026-04-23, current)

### Verified locally

- The Dual ISO block inside `MainWindow::drawFrameReady()` is not purely read-only UI bookkeeping.
  - `platform/qt/MainWindow.cpp:10900-10933` mixes three different kinds of work:
    - receipt mutation: `ACTIVE_RECEIPT->setDualIsoAutoCorrected( 1 )` at `platform/qt/MainWindow.cpp:10902`
    - llrawproc state normalization: `m_pMlvObject->llrawproc->diso_pattern = -m_pMlvObject->llrawproc->diso_pattern` at `platform/qt/MainWindow.cpp:10904-10907` and `m_pMlvObject->llrawproc->diso_auto_correction = -m_pMlvObject->llrawproc->diso_auto_correction` at `platform/qt/MainWindow.cpp:10931`
    - widget reflection only: the `blockSignals(true)` / `setCurrentIndex(...)` / `setValue(...)` calls plus label text updates at `platform/qt/MainWindow.cpp:10908-10910` and `platform/qt/MainWindow.cpp:10917-10928`
- The widget reflection sub-block is safe to defer from a render-state perspective.
  - The combobox and sliders are updated with signals blocked, so they do not re-enter `on_DualIsoPatternComboBox_currentIndexChanged(...)`, `on_horizontalSliderDualIsoEvCorrection_valueChanged(...)`, or `on_horizontalSliderDualIsoBlackDelta_valueChanged(...)`.
  - Those slots are the ones that actually mutate llrawproc state and invalidate caches: `platform/qt/MainWindow.cpp:7570-7596`, `platform/qt/MainWindow.cpp:7598-7619`, and `platform/qt/MainWindow.cpp:10155-10172`.
- The receipt mutation in `drawFrameReady()` is not render-critical for the current frame, but it is not display-only either.
  - `ReceiptSettings::setDualIsoAutoCorrected(...)` is just a field write in `platform/qt/ReceiptSettings.h:89`, and `drawFrameReady()` writes it every frame when Dual ISO is active.
  - That field is later consumed by receipt application logic in `platform/qt/MainWindow.cpp:5801-5855` and mirrored in batch mode by `src/batch/ReceiptApplier.cpp:116-196`, where `dualIsoAutoCorrected()` decides whether the receipt should auto-resolve Dual ISO defaults or reuse explicit pattern / EV / black-delta values.
  - Safe conclusion: skipping `ACTIVE_RECEIPT->setDualIsoAutoCorrected( 1 )` during playback would not change the pixels of the frame already being shown, but it could leave the active receipt stale for later reapplication, export, or clip switching.
- The llrawproc writes in `drawFrameReady()` are stateful and should not be treated as mere UI sync.
  - `diso_pattern` can be auto-discovered during preview processing. Preview accepts either sign via `ABS(iso_pattern)` in `src/mlv/llrawproc/dualiso.c:47-60`, so normalizing a negative value to positive in `drawFrameReady()` is not needed for the current preview frame itself.
  - However, `diso_auto_correction` sign changes are not cosmetic. `drawFrameReady()` flips it positive after publishing the auto-matched EV / black-delta values to the sliders at `platform/qt/MainWindow.cpp:10913-10931`.
  - The full Dual ISO path checks the sign to decide whether to publish or reuse auto-match values in `src/mlv/llrawproc/llrawproc.c:1006-1025`, and the GUI receipt setup also deliberately normalizes negative signs in `platform/qt/MainWindow.cpp:5796-5800`.
  - Safe conclusion: skipping the whole `10900-10933` block would risk changing later Dual ISO behavior, not just the UI.
- Nearby playback-policy code is also definitively stateful, not display bookkeeping.
  - `platform/qt/MainWindow.cpp:9851-9885` (`applyEffectiveDualIsoPlaybackSettings`) writes `llrawproc` mode / interpolation / alias-map / full-res blending, resets black/white levels, resets caches, and marks `m_frameChanged = true`.
  - `platform/qt/MainWindow.cpp:9942-9960` calls that helper on play toggles, and `platform/qt/MainWindow.cpp:11231-11236` calls it again when playback stops to restore the non-preview receipt/runtime state.

### Cross-checked from prior analysis

- The batch-side `ReceiptApplier` clone of the GUI Dual ISO logic confirms that `dualIsoAutoCorrected`, `diso_pattern`, `diso_ev_correction`, and `diso_black_delta` are part of the real processing contract, not just widget cosmetics.

### Needs runtime profiling

- If we want to save playback-time UI cost here, split the current block into:
  - state publication/normalization that must remain (`ACTIVE_RECEIPT->setDualIsoAutoCorrected( 1 )`, the `llrawproc` sign normalization)
  - widget reflection that can be throttled or deferred (`setCurrentIndex`, `setValue`, label text updates)
- Measure that narrower split before deleting it. The full `drawFrameReady()` bucket is large enough that this may be worth doing, but only the widget-reflection subset is clearly safe to defer.

## Cadence Gap Split + Direct Processed8 Path (2026-04-23, current)

### Verified locally

- Landed the first real step-1 + step-2 pass for the current Dual ISO playback plan.
  - `platform/qt/RenderFrameThread.cpp:138`, `platform/qt/RenderFrameThread.cpp:177`, and `platform/qt/MainWindow.cpp:1442` now export the previously opaque playback gap as:
    - `render_thread_queue_wait_ms`
    - `render_thread_work_ms`
    - `render_thread_total_ms`
    - `draw_frame_ready_queue_ms`
    - `draw_frame_ready_total_ms`
  - `src/mlv/video_mlv.c:1791-2014` now has a direct processed-8-bit path for the display consumer instead of always materializing full `processed16` and then shifting it down.
  - `src/processing/raw_processing.c:876-928` and `src/processing/raw_processing.c:1668-1755` were widened from the first too-narrow version so the direct path now preserves the real preview-receipt shape here:
    - neutral creative flags no longer block it by themselves
    - the direct path now applies the same post-gamma contrast / gradation curves as the 16-bit path
    - `exr_mode` now follows the existing CPU semantics by skipping gamut compression instead of rejecting the path outright
- The key activation mistake in the first cut is now understood and fixed.
  - The preview receipts leave `allowCreativeAdjustments` at the legacy default `true`, and the current GUI path also keeps the contrast-curve controls (`DS/DR/LS/LR`) active on this receipt.
  - The first narrow gate compiled and passed the math-only pipeline subset test, but it did not activate on the app-backed preview receipt until the direct path learned those post-gamma curve steps and the `exr_mode` skip-gamut behavior.
- Fresh current-tree large-receipt artifacts for the real direct-8-bit path now live in:
  - `.claude/profiling/20260423-direct8bit-playback-gap/large_dual_iso_preview_t4_direct8_run1.json`
  - `.claude/profiling/20260423-direct8bit-playback-gap/large_dual_iso_preview_t4_direct8_run2.json`
  - `.claude/profiling/20260423-direct8bit-playback-gap/large_dual_iso_preview_t4_direct8_run3.json`
- Aggregate warm-sample medians versus the kept bilinear direct-`uint16` baseline moved from:
  - `cadence_ms 75.439 -> 59.299`
  - `latency_ms 74.678 -> 58.193`
  - `processed8_total_ms 54.000 -> 37.000`
  - `processed16_total_ms 48.000 -> 34.000`
  - `processed16_to_8bit_ms 2.000 -> 0.000`
  - `raw_uint16_ms 19.000 -> 17.000`
  - `llrawproc_ms 6.000 -> 5.000`
  - `debayered_frame_ms 29.000 -> 27.000`
  - `processing_ms 13.000 -> 7.000`
  - `processing_core_color_ms 8.000 -> 5.000`
- The new telemetry split makes the remaining non-engine gap much clearer on the same warm aggregate:
  - `render_thread_queue_wait_ms = 11.000`
  - `render_thread_work_ms = 37.000`
  - `render_thread_total_ms = 47.000`
  - `draw_frame_ready_queue_ms = 0.000`
  - `draw_frame_ready_total_ms = 10.000`
  - `engine_latency_ms = 47.655`
  - `presentation_overhead_ms = 10.497`
- Safe claim after the reruns:
  - this pass is no longer a plumbing-only change; `processed8_direct_path_active` was `true` on every warm frame in the kept large-receipt reruns
  - the direct-8-bit path is worth keeping as a real VM win (`~16 ms` off warm cadence, `~17 ms` off warm processed8 total)
  - it is still not enough for realtime on this VM; `59.299 ms` is materially better than `75.439 ms`, but still above the native `41.708 ms` budget for `23.976 fps`
- Fresh current-tree validation after the telemetry split, kept direct-8-bit path, and activation guards:
  - plain `console_tests --check-golden`: `41 tests / 160 assertions / 17 skips / 0 failures`
  - app-backed `console_tests --check-golden` with `platform/qt/build-codex-current/release/MLVApp.exe`: `41 tests / 726 assertions / 1 skip / 0 failures`
  - `pipeline_tests --check-golden`: `46 tests / 526 assertions / 4 skips / 0 failures`
- Current ranked next steps after this result:
  1. High impact / medium effort: overlap stages across frames so the new `~37 ms` render-thread work no longer sits on the critical path by itself.
  2. High impact / low-medium effort: trim the newly measured `~11 ms` render-thread queue wait plus `~10 ms` `drawFrameReady()` cost before assuming more CPU math work is the next best lever.
  3. Medium impact / medium effort: add the playback-only processing subset for Dual ISO on top of this direct-8-bit path rather than going straight to more decoder work.
  4. Medium impact / medium-high effort: only then spend time on runtime-dispatched AVX2 kernels for the surviving hot loops.

### Cross-checked from prior analysis

- The locked step order was the right call. If we had gone straight to overlap or AVX2, we would have missed that the existing preview receipt still had real post-gamma curve work that the first direct-8-bit version was silently skipping.
- The earlier “24 fps first, 60 fps aspirational” framing is even stronger now:
  - `59.299 ms` is a real step forward
  - the remaining `~17.6 ms` to native realtime is still substantial, but no longer looks like a decode-only problem

### Needs runtime profiling

- Re-run the same three-artifact shape on the host before promoting the new `~16 ms` cadence win into a broader performance claim.
- When the overlap pass lands, compare it against this new direct-8-bit baseline rather than the older bilinear/u16 baseline; this is now the honest current keep point.

## Integration Branch Post-Decode Follow-Up (2026-04-23, current)

### Verified locally

- Replayed the reconstructed April playback history into this clean integration tree by merging `codex/reconstruct-festive-boyd-history` onto `codex/festive-boyd-integration`.
  - The checked merge base against `fork/master` was `c1d23e60`.
  - The merge landed cleanly; the only overlap points were the auto-merges in `platform/qt/MLVApp.pro` and `src/mlv/video_mlv.c`.
  - The safety refs named in the handoff (`fork/festive-boyd` and `fork/codex/reconstruct-festive-boyd-history`) were left untouched.
- The integration tree exposed one real build seam from the upstream MCraw parser sync: the local test qmake files were compiling `src/mlv/mcraw/mcraw.c` without `src/mlv/mcraw/cJSON.c`.
  - Fixed in `tests/common/pipeline_runtime.pri:18`, `tests/pipeline/pipeline_tests.pro:33`, and `tests/perf/perf_tests.pro:28`.
  - Fresh current-tree validation after the fix:
    - plain `console_tests --check-golden`: `41 tests / 160 assertions / 17 skips / 0 failures`
    - app-backed `console_tests --check-golden` with `platform/qt/build-codex-current/release/MLVApp.exe`: `41 tests / 695 assertions / 1 skip / 0 failures`
    - `pipeline_tests --check-golden`: `45 tests / 515 assertions / 4 skips / 0 failures`
- The earlier multithread blind spot in `processing_core_*` is now fixed on this branch.
  - `src/processing/raw_processing.h:323` adds `processing_core_timing_t`.
  - `src/processing/raw_processing.c:576` and `src/processing/raw_processing.c:616` now capture per-worker core timings and collapse them back with `max(...)` on the `threads > 1` path, so the large Dual ISO `--threads 4` profile finally shows nonzero `processing_core_levels_ms`, `processing_core_color_ms`, and `processing_core_output_ms`.
- Fresh current-tree t4 playback-profile artifact before any new processing-tail optimization:
  - `.claude/profiling/20260423-postdecode-t4-breakdown/large_dual_iso_preview_t4_breakdown.json`
  - warm medians after discard-5:
    - `latency_ms = 79.667`
    - `processed16_total_ms = 52.000`
    - `debayered_frame_ms = 30.000`
    - `processing_ms = 17.000`
    - `raw_uint16_ms = 19.000`
    - `processing_core_ms = 10.000`
    - `processing_core_levels_ms = 2.000`
    - `processing_core_color_ms = 7.000`
    - `processing_core_output_ms = 1.000`
    - `processing_other_ms = 8.000`
    - `debayer_exclusive_ms = 6.000`
    - `debayer_pipeline_other_ms = 3.000`
- That t4 breakdown made the next code change concrete: the common preview receipt was still paying for two no-op full-frame copies after the core stage even when chroma separation, sharpening, and grain were all off.
  - `src/processing/raw_processing.c:686` now detects that inactive tail shape and returns early before those copies.
  - The post-copy-skip reruns live in:
    - `.claude/profiling/20260423-postdecode-t4-breakdown/large_dual_iso_preview_t4_breakdown_postcopyskip.json`
    - `.claude/profiling/20260423-postdecode-t4-breakdown/large_dual_iso_preview_t4_breakdown_postcopyskip_run2.json`
    - `.claude/profiling/20260423-postdecode-t4-breakdown/large_dual_iso_preview_t4_breakdown_postcopyskip_run3.json`
  - Warm medians from those three reruns:
    - `processed16_total_ms = 51.000`, `48.000`, `49.000`
    - `processing_ms = 14.000`, `13.000`, `14.000`
    - `processing_other_ms = 3.000`, `3.000`, `4.000`
    - `raw_uint16_ms = 19.000`, `19.000`, `19.000`
    - `latency_ms = 85.426`, `76.246`, `76.098`
- Safe claim from the reruns: the copy-skip removes real dead post-core work on this receipt (`processing_other_ms` falls from `8.000` into the `3-4 ms` band, with `processing_ms` falling from `17.000` into the `13-14 ms` band) while leaving raw decode unchanged.
- I then tried a narrowly scoped follow-up on the common basic-matrix fast path under `src/processing/raw_processing.c:939-989`.
  - Kept source change:
    - scalarized the hot loop
    - hoisted `proper_wb_matrix` entries into local `float` coefficients
    - removed the per-pixel temporary arrays and inner channel loop
    - added a zero-denominator guard around the desaturation step while preserving the existing non-red tonemap behavior
  - Kept profiling artifacts:
    - `.claude/profiling/20260423-postdecode-t4-breakdown/large_dual_iso_preview_t4_breakdown_colorfast_run1.json`
    - `.claude/profiling/20260423-postdecode-t4-breakdown/large_dual_iso_preview_t4_breakdown_colorfast_run2.json`
    - `.claude/profiling/20260423-postdecode-t4-breakdown/large_dual_iso_preview_t4_breakdown_colorfast_run3.json`
  - Warm medians from those three reruns:
    - `latency_ms = 71.292`, `76.565`, `74.294`
    - `processed16_total_ms = 47.000`, `49.000`, `49.000`
    - `debayered_frame_ms = 29.000`, `29.000`, `30.000`
    - `processing_ms = 12.000`, `14.000`, `12.000`
    - `processing_core_ms = 10.000`, `10.000`, `10.000`
    - `processing_core_color_ms = 8.000`, `8.000`, `9.000`
    - `processing_other_ms = 3.000`, `3.000`, `3.000`
    - `raw_uint16_ms = 19.000`, `19.000`, `19.000`
  - Aggregate warm-sample medians versus the kept post-copy-skip baseline moved from:
    - `latency_ms 76.032 -> 74.294`
    - `processed16_total_ms 49.000 -> 47.000`
    - `debayered_frame_ms 30.000 -> 29.000`
    - `processing_ms 13.000 -> 12.000`
    - `processing_core_color_ms 8.000 -> 8.000`
    - `raw_uint16_ms 19.000 -> 19.000`
- I also tried a heavier precomputed-LUT version of that same color-path idea and then reverted it.
  - Rejected profiling artifacts:
    - `.claude/profiling/20260423-postdecode-t4-breakdown/large_dual_iso_preview_t4_breakdown_colorlut_run1.json`
    - `.claude/profiling/20260423-postdecode-t4-breakdown/large_dual_iso_preview_t4_breakdown_colorlut_run2.json`
    - `.claude/profiling/20260423-postdecode-t4-breakdown/large_dual_iso_preview_t4_breakdown_colorlut_run3.json`
  - Aggregate warm-sample medians for that rejected variant were effectively back near the post-copy-skip baseline:
    - `latency_ms = 76.450`
    - `processed16_total_ms = 49.000`
    - `debayered_frame_ms = 30.000`
    - `processing_ms = 13.000`
    - `processing_core_color_ms = 8.000`
    - `raw_uint16_ms = 19.000`
- Current keep/revert call on the color-path follow-up:
  - keep the smaller scalar rewrite
  - do not keep the LUT-backed version
  - safe claim is only a modest post-decode trim (`~1-2 ms` on the aggregate warm `processed16` / `processing` path here), not a decisive `processing_core_color_ms` breakthrough yet
- I then rechecked the current receipt/runtime path before touching debayer.
  - The playback-profile metadata on the large Dual ISO preview receipt reports `playback_debayer_effective = bilinear` and `playback_debayer_engine_mode = 0` on this branch, so the current hot path is bilinear preview debayer, not the grayscale `none` mode.
  - I kept a direct-`uint16` fast path for the `none` preview mode as a side cleanup, but the relevant current-tree follow-up was wiring the bilinear path to consume processed `uint16` raw data directly instead of round-tripping through `getMlvRawFrameFloat(...)`.
  - Current kept debayer-side source changes:
    - `src/mlv/video_mlv.c:1525` adds `getMlvRawFrameProcessedUint16(...)`, a shared helper that stops after `raw_uint16 + llrawproc` and returns the required bit-depth shift.
    - `src/debayer/debayer.c:19` adds `debayerNoneU16(...)` for the grayscale preview path.
    - `src/debayer/debayer.c:43` adds `debayerBasicU16(...)` for the current bilinear preview path.
    - `src/mlv/frame_caching.c:686-722` now routes preview debayer types `0` and `2` through those direct-`uint16` helpers before falling back to the older float-based paths.
  - Kept bilinear artifacts:
    - `.claude/profiling/20260423-postdecode-t4-breakdown/large_dual_iso_preview_t4_breakdown_bilinearu16_run1.json`
    - `.claude/profiling/20260423-postdecode-t4-breakdown/large_dual_iso_preview_t4_breakdown_bilinearu16_run2.json`
    - `.claude/profiling/20260423-postdecode-t4-breakdown/large_dual_iso_preview_t4_breakdown_bilinearu16_run3.json`
  - Warm medians from those three reruns:
    - `latency_ms = 77.018`, `72.890`, `74.678`
    - `processed16_total_ms = 49.000`, `48.000`, `49.000`
    - `debayered_frame_ms = 29.000`, `29.000`, `30.000`
    - `raw_float_convert_ms = 0.000`, `0.000`, `0.000`
    - `debayer_exclusive_ms = 4.000`, `4.000`, `4.000`
    - `debayer_kernel_ms = 2.000`, `2.000`, `2.000`
    - `debayer_pipeline_other_ms = 2.000`, `2.000`, `3.000`
    - `processing_ms = 14.000`, `13.000`, `13.000`
    - `processing_core_color_ms = 8.000`, `8.000`, `8.000`
    - `raw_uint16_ms = 19.000`, `19.000`, `18.000`
  - Aggregate warm-sample medians versus the kept post-copy-skip baseline moved from:
    - `latency_ms 76.032 -> 74.678`
    - `processed16_total_ms 49.000 -> 48.000`
    - `debayered_frame_ms 30.000 -> 29.000`
    - `raw_float_convert_ms 1.000 -> 0.000`
    - `debayer_exclusive_ms 6.000 -> 4.000`
    - `raw_uint16_ms 19.000 -> 19.000`
  - Relative to the earlier scalar colorfast reruns, the safe reading is narrower: the bilinear direct-`uint16` change clearly removes the warm `raw_float_convert` bucket and trims exclusive debayer, but total `processed16_total_ms` still lands in the same general `47-49 ms` band on this VM. Keep it as a low-risk debayer-side cleanup, not as a new major throughput breakthrough.
- I also pinned the target math to the checked-in large fixture and the current kept warm medians before deciding how seriously to treat the `60 fps` ask.
  - `tests/fixtures/clips/large_dual_iso.mlv` carries `sourceFpsNom = 23976` and `sourceFpsDenom = 1000`, so the native source rate is `23.976 fps`.
  - That means the real native-rate playback budget is `41.708 ms/frame`; `60 fps` would require `16.667 ms/frame`.
  - Current kept aggregate warm medians from the three bilinear direct-`uint16` reruns are:
    - `cadence_ms = 75.7867`
    - `processed16_total_ms = 49.000`
    - `processed8_total_ms = 54.000`
    - `raw_uint16_ms = 19.000`
    - `llrawproc_ms = 6.000`
    - `debayer_exclusive_ms = 4.000`
    - `processing_ms = 13.000`
  - The practical lower-bound read from those buckets is important:
    - a receipt-preserving overlap of `raw_uint16 + llrawproc` is still about `25 ms`
    - the downstream `processed8 - (raw_uint16 + llrawproc)` remainder is still about `29 ms`
    - so even an idealized steady-state overlap only points to the high-`20 ms` range on this VM, not to `16.667 ms`
  - Safe planning conclusion:
    - `>24 fps` on this VM still looks plausible if we overlap stages and stop paying full serial receipt costs while playing
    - `60 fps` is not a credible same-quality CPU-only target on this VM
    - if "lossless quality" means paused/export output stays exact, we can preserve that by using a playback-only fast path and restoring the full receipt when playback stops
    - if "lossless quality" means every displayed playback frame must stay pixel-identical to the current receipt/bilinear path, target `24+ fps` first; `60 fps` would need a materially faster runtime path than this VM currently has

### Cross-checked from prior analysis

- This validates the April closeout recommendation to stop reopening predictor-1 LJ92 churn once raw decode stopped dominating the t4 playback path. The first honest next pass really was to split the post-decode work, not to queue more decoder micro-candidates.
- The new t4 breakdown also matches the earlier receipt reading: the remaining processing time is still concentrated in the basic color/core path and a smaller post-core tail, not in the disabled creative / denoise / RBF / highlight branches for this receipt.
- The scalar color-loop cleanup was directionally useful, but the rejected LUT experiment reinforces the earlier caution against mistaking micro-hoists for the next major breakthrough. The next bigger win is more likely to come from algorithmic simplification or the debayer side than from stacking more local table churn onto this loop.
- The bilinear reruns sharpen that ranking further: once the float handoff is removed, the current receipt still spends much more time in `processing_ms` / `processing_core_color_ms` than it does in warm exclusive debayer. That means the next bigger win is probably in the processing core or in a bigger preview/debayer policy shift, not in another tiny bilinear micro-pass.

### Needs runtime profiling

- I do not have a strong end-to-end latency claim from the copy-skip alone yet. The three post-change reruns still show VM jitter (`76.098-85.426 ms`) large enough that the safe conclusion is "processing tail reduced", not "steady-state playback latency definitely improved by X ms".
- The next host rerun should keep using the same large Dual ISO preview receipt and `--threads 4` shape so we can see whether the current VM-local split carries over:
  - `processing_core_color_ms` is still the largest honest inner bucket here at about `8 ms`.
  - `debayer_exclusive_ms` is now down around `4 ms` on the current kept bilinear path, with `debayer_pipeline_other_ms` still around `2-3 ms`.
  - `raw_float_convert_ms` is now gone on warm bilinear frames, so any remaining debayer-side work has to come from the kernel or policy/runtime shape rather than from more format-conversion cleanup.
- If we want a formal keep/revert decision on the copy-skip itself, capture repeated t4 runs on the real host and compare the same discard-5 warm medians rather than trusting a single VM replay.
- If we want to turn the scalar color-loop keep into a stronger claim, repeat the same comparison on the real host. On this VM the improvement is modest enough that host confirmation matters before we count it as a stable throughput gain.
- The same caution now applies to the bilinear direct-`uint16` cleanup: it is behavior-preserving and measurably deletes a warm bucket, but host reruns still matter before claiming a larger FPS win from the resulting `~1 ms` aggregate trim.

### Ranked next steps

1. Highest impact / medium effort: treat `60 fps same-quality playback` as out of scope on this VM and target `better than native-rate 23.976 fps` first. The next work should optimize for `<= 41.708 ms/frame`, not for `16.667 ms/frame`.
2. High impact / medium effort: build a true playback pipeline so `raw_uint16 + llrawproc` can overlap with downstream debayer/processing/display work. The current kept bucket shape says overlap is the main honest lever left.
3. High impact / medium effort: add a playback-only fast-processing / direct-to-8-bit path that preserves paused/export quality. If we keep paying the full current receipt-shaped post-decode cost while playing, the VM budget stays too tight even after the recent `1-2 ms` trims.
4. Medium-high impact / medium effort: if we stay in the bilinear preview path, inspect the remaining `debayer_pipeline_other_ms` / runtime policy seams instead of another tiny arithmetic cleanup inside the same bilinear loop.
5. High impact / low-medium effort: rerun the same `large_dual_iso_preview` playback-profile shape on the real host before locking in more policy or architecture changes. The VM still moves enough frame-to-frame and run-to-run that host confirmation matters.

## Dual ISO Playback Status Snapshot (2026-04-23, summary)

### Verified locally

- The current kept Dual ISO playback state is the combination already landed across:
  - predictor-1 LJ92 fast-path work in [src/mlv/liblj92/lj92.c:416](</C:/!Layi%20Wkspc/MLV-App/.claude/worktrees/festive-boyd/src/mlv/liblj92/lj92.c:416>), [lj92.c:695](</C:/!Layi%20Wkspc/MLV-App/.claude/worktrees/festive-boyd/src/mlv/liblj92/lj92.c:695>), and [lj92.c:926](</C:/!Layi%20Wkspc/MLV-App/.claude/worktrees/festive-boyd/src/mlv/liblj92/lj92.c:926>)
  - receipt-shaped processing fast path and `highest_green` gating in [src/processing/raw_processing.c:468](</C:/!Layi%20Wkspc/MLV-App/.claude/worktrees/festive-boyd/src/processing/raw_processing.c:468>) and [raw_processing.c:825](</C:/!Layi%20Wkspc/MLV-App/.claude/worktrees/festive-boyd/src/processing/raw_processing.c:825>)
  - playback-profile timing export through [src/mlv/video_mlv.c:1210](</C:/!Layi%20Wkspc/MLV-App/.claude/worktrees/festive-boyd/src/mlv/video_mlv.c:1210>), [platform/qt/RenderFrameThread.cpp:215](</C:/!Layi%20Wkspc/MLV-App/.claude/worktrees/festive-boyd/platform/qt/RenderFrameThread.cpp:215>), and [platform/qt/MainWindow.cpp:1397](</C:/!Layi%20Wkspc/MLV-App/.claude/worktrees/festive-boyd/platform/qt/MainWindow.cpp:1397>)
  - small play-start cache preroll in [src/mlv/frame_caching.c:300](</C:/!Layi%20Wkspc/MLV-App/.claude/worktrees/festive-boyd/src/mlv/frame_caching.c:300>) and [platform/qt/MainWindow.cpp:9907](</C:/!Layi%20Wkspc/MLV-App/.claude/worktrees/festive-boyd/platform/qt/MainWindow.cpp:9907>)
- The latest sustained-playback pivot on the kept source state is still [large_dual_iso_preview_t4_final_pivot.json](</C:/!Layi%20Wkspc/MLV-App/.claude/worktrees/festive-boyd/.claude/profiling/20260423-pred1-fastpath-final-pivot/large_dual_iso_preview_t4_final_pivot.json:1>):
  - warm median `latency_ms = 77.952`
  - average cadence `79.107 ms`
  - effective throughput on this VM is therefore about `12.6-12.8 fps`
  - warm median `processed16_total_ms = 53.000`
  - warm median `processed8_total_ms = 58.000`
  - warm median `debayered_frame_ms = 29.000`
  - warm median `processing_ms = 18.000`
  - warm median `raw_uint16_ms = 18.000`
  - warm median `raw_uint16_decompress_execute_ms = 16.000`
- Against common real-time targets on this same measured path, the remaining cadence gap is still large:
  - `24 fps` target cadence is `41.667 ms`, so the current gap is about `37.440 ms/frame`
  - `30 fps` target cadence is `33.333 ms`, so the current gap is about `45.774 ms/frame`
- The recent improvement trend is real and substantial on this VM:
  - early outer-stage snapshot: frame latency `310.48 ms`, `processing_ms 149.00`, `raw_uint16_ms 48.00`, `dual_iso_preview_total_ms 8.00`
  - later processing-stage snapshot: frame latency `212.89 ms`, `processing_ms 99.43`, `raw_uint16_ms 41.29`, `dual_iso_preview_total_ms 5.57`
  - post processing-fast-path snapshot: single-thread frame latency `112.30 ms`, `processing_ms 45.75`, `raw_uint16_ms 37.75`
  - final kept decoder pivot: warm median `77.952 ms`, `processing_ms 18.000`, `raw_uint16_ms 18.000`
- The decoder work has done what we needed it to do for now:
  - corrected predictor-1 baseline at `--threads 4` was `raw_uint16_ms = 39.000`
  - kept candidate-1 state brought that to `29.000`
  - kept candidate-6 state brought that to `18.000`
  - `raw_uint16_ms` is no longer the dominant playback stage; `processed16_total_ms` is

### Cross-checked from prior analysis

- The earlier recommendation to stop treating Dual ISO preview rowscale as the main blocker is now confirmed. On the current kept path, the preview-specific work is materially smaller than the post-decode path.
- The earlier recommendation to stop decoder churn once LJ92 fell out of the dominant slot is also confirmed by the final pivot. The next worthwhile seam is downstream of decode, not another round of predictor-1 micro-candidates.
- The cache preroll work remains a play-start improvement, not a sustained-FPS fix. Same-mode cached-AMaZE A/B only showed about a `37 ms` first-frame improvement on this VM, and the final sustained pivot here is still on the non-cached bilinear playback path.

### Needs runtime profiling

- These numbers are still VM-local, CPU-only, and headless-profiled. The host may move the absolute totals materially, especially for debayer/GPU-backed paths and thread scheduling.
- `processed16_total_ms` and `debayered_frame_ms` are still too coarse to tell us exactly where the remaining `~37-46 ms/frame` real-time gap should come from. The next pass needs a deeper breakdown inside the post-decode path, not just more top-level timing.
- The final pivot metadata still reports `playback_processing_effective = receipt` and `playback_processing_supported = false` for this receipt, so there is not yet a validated “subset processing” shortcut available to close the real-time gap on this exact workload.

### Ranked next steps

1. High impact / medium effort: treat `processed16_total_ms` as the primary real-time blocker and split the post-decode path under [src/processing/raw_processing.c:468](</C:/!Layi%20Wkspc/MLV-App/.claude/worktrees/festive-boyd/src/processing/raw_processing.c:468>) into actionable buckets that survive multithreaded playback. Right now we know the gap is there, but not which inner branch will buy the next `10-20+ ms`.
2. High impact / medium effort: re-measure the kept source state on the real host with the same `large_dual_iso_preview` receipt before changing playback policy. The VM says we are around `12.6 fps`; we should not promise or tune for real-time until we know whether the host is meaningfully closer.
3. High impact / medium effort: investigate whether this receipt can safely gain a supported playback-processing subset or a similarly narrow “preview fidelity” mode. The current receipt fast path removed dead branches, but the metadata says the broader subset path still does not apply here.
4. Medium impact / medium effort: only revisit deeper LJ92 work if a host rerun makes decode large again. On the current kept VM path, another `5-10 ms` out of decode alone would still not get us to `24 fps`; the bigger remaining win has to come from post-decode.
5. Medium impact / low effort: keep the play-start preroll path as a UX improvement, but do not count it toward the sustained real-time target. It helps the first frame arrive sooner; it does not solve the steady-state cadence gap.

## Predictor-1 Final Keep (2026-04-23, latest)

### Verified locally

- The predictor-1 fast path remains aligned with the real Dual ISO LJ92 decode shape in [src/mlv/liblj92/lj92.c:554](</C:/!Layi%20Wkspc/MLV-App/.claude/worktrees/festive-boyd/src/mlv/liblj92/lj92.c:554>), [lj92.c:568](</C:/!Layi%20Wkspc/MLV-App/.claude/worktrees/festive-boyd/src/mlv/liblj92/lj92.c:568>), [lj92.c:646](</C:/!Layi%20Wkspc/MLV-App/.claude/worktrees/festive-boyd/src/mlv/liblj92/lj92.c:646>), and [lj92.c:903](</C:/!Layi%20Wkspc/MLV-App/.claude/worktrees/festive-boyd/src/mlv/liblj92/lj92.c:903>).
- Candidate 1 is now kept in [src/mlv/liblj92/lj92.c:374](</C:/!Layi%20Wkspc/MLV-App/.claude/worktrees/festive-boyd/src/mlv/liblj92/lj92.c:374>), [lj92.c:568](</C:/!Layi%20Wkspc/MLV-App/.claude/worktrees/festive-boyd/src/mlv/liblj92/lj92.c:568>), [lj92.c:646](</C:/!Layi%20Wkspc/MLV-App/.claude/worktrees/festive-boyd/src/mlv/liblj92/lj92.c:646>), and [lj92.c:903](</C:/!Layi%20Wkspc/MLV-App/.claude/worktrees/festive-boyd/src/mlv/liblj92/lj92.c:903>).
  - It is a combined candidate:
    - hot/cold split of the predictor-1 fast path so the default hot path no longer pays the measurement-wrapper shape
    - `LJ92_ALWAYS_INLINE` on `nextdiff_fast(...)`
    - cached `data` pointer use in the second refill loop
  - The kept win should therefore be attributed to the combined candidate, not to the inline annotation in isolation.
- The first multi-component fast-path attempt that wrote directly from the output buffer still remains a useful guardrail: it regressed pipeline goldens, so the kept implementation continues to preserve the generic row-buffer behavior.
- The earlier `baseline_metrics.txt` text summary was inconsistent. The Phase B baseline was recomputed locally from the raw playback-profile JSON artifacts, and those recomputed medians now supersede the older text-only copy.
- Corrected Phase B baseline medians from the raw JSON artifacts in [20260423-pred1-fastpath-baseline](</C:/!Layi%20Wkspc/MLV-App/.claude/worktrees/festive-boyd/.claude/profiling/20260423-pred1-fastpath-baseline>):
  - `--threads 1` median-of-run-medians:
    - `raw_uint16_ms = 32.500`
    - `raw_uint16_decompress_execute_ms = 31.000`
    - `raw_uint16_lj92_pred1_fast_path_total_ms = 31.000`
    - `raw_uint16_lj92_pred1_fast_path_bitstream_ms = 29.500`
    - `raw_uint16_lj92_pred1_fast_path_predictor_ms = 4.000`
  - `--threads 4` median-of-run-medians:
    - `raw_uint16_ms = 39.000`
    - `raw_uint16_decompress_execute_ms = 38.000`
    - `raw_uint16_lj92_pred1_fast_path_total_ms = 38.000`
    - `raw_uint16_lj92_pred1_fast_path_bitstream_ms = 28.000`
    - `raw_uint16_lj92_pred1_fast_path_predictor_ms = 4.000`
  - Every baseline run kept the fast path active on all decode-active warm samples.
- Candidate 1 artifacts now live under [20260423-pred1-fastpath-candidate1-split-hot-cold](</C:/!Layi%20Wkspc/MLV-App/.claude/worktrees/festive-boyd/.claude/profiling/20260423-pred1-fastpath-candidate1-split-hot-cold>) with the saved summary in [candidate1_metrics.txt](</C:/!Layi%20Wkspc/MLV-App/.claude/worktrees/festive-boyd/.claude/profiling/20260423-pred1-fastpath-candidate1-split-hot-cold/candidate1_metrics.txt:1>).
  - `--threads 1` median-of-run-medians: `raw_uint16_ms = 29.000`
  - `--threads 4` median-of-run-medians: `raw_uint16_ms = 29.000`
  - improvement vs corrected baseline:
    - `--threads 1`: `10.770%` faster
    - `--threads 4`: `25.641%` faster
  - all six candidate runs kept the fast path active on all decode-active warm samples
- Candidate 2, candidate 3, candidate 4, and candidate 5 were all measured and then reverted.
  - refill split: [candidate2_metrics.txt](</C:/!Layi%20Wkspc/MLV-App/.claude/worktrees/festive-boyd/.claude/profiling/20260423-pred1-fastpath-candidate2-refill-split/candidate2_metrics.txt:1>)
    - still beat the raw baseline (`7.692%` faster at `--threads 1`, `20.513%` faster at `--threads 4`)
    - but regressed the kept candidate-1 state (`3.449%` slower at `--threads 1`, `6.896%` slower at `--threads 4`)
  - pointer-walk loops: [candidate3_metrics.txt](</C:/!Layi%20Wkspc/MLV-App/.claude/worktrees/festive-boyd/.claude/profiling/20260423-pred1-fastpath-candidate3-pointer-walk/candidate3_metrics.txt:1>)
    - still beat the raw baseline
    - but was effectively flat at `--threads 1` and `3.449%` slower at `--threads 4` versus the kept candidate-1 state
  - branch trimming: [candidate4_metrics.txt](</C:/!Layi%20Wkspc/MLV-App/.claude/worktrees/festive-boyd/.claude/profiling/20260423-pred1-fastpath-candidate4-branch-trim/candidate4_metrics.txt:1>)
    - median result was only `3.448%` better than candidate 1 at `--threads 1` with `0.000%` change at `--threads 4`
    - repeat-level movement stayed mixed, so it did not satisfy the `3-5%` keep rule cleanly enough to survive
  - zero-diff cleanup: [candidate5_metrics.txt](</C:/!Layi%20Wkspc/MLV-App/.claude/worktrees/festive-boyd/.claude/profiling/20260423-pred1-fastpath-candidate5-huffman-zero-diff/candidate5_metrics.txt:1>)
    - still beat the raw baseline (`4.615%` faster at `--threads 1`, `20.513%` faster at `--threads 4`)
    - but regressed the kept candidate-1 state by about `6.9%` at both thread counts
- Candidate 6 is now also kept in [src/mlv/liblj92/lj92.c:97](</C:/!Layi%20Wkspc/MLV-App/.claude/worktrees/festive-boyd/src/mlv/liblj92/lj92.c:97>), [lj92.c:123](</C:/!Layi%20Wkspc/MLV-App/.claude/worktrees/festive-boyd/src/mlv/liblj92/lj92.c:123>), [lj92.c:142](</C:/!Layi%20Wkspc/MLV-App/.claude/worktrees/festive-boyd/src/mlv/liblj92/lj92.c:142>), [lj92.c:319](</C:/!Layi%20Wkspc/MLV-App/.claude/worktrees/festive-boyd/src/mlv/liblj92/lj92.c:319>), [lj92.c:322](</C:/!Layi%20Wkspc/MLV-App/.claude/worktrees/festive-boyd/src/mlv/liblj92/lj92.c:322>), and [lj92.c:449](</C:/!Layi%20Wkspc/MLV-App/.claude/worktrees/festive-boyd/src/mlv/liblj92/lj92.c:449>).
  - It widens `hufflut` entries so the fast path can return a fully decoded `diff` directly when the current `huffbits` peek window already contains the whole symbol.
  - The old refill / receive path remains in place as fallback for entries that do not fit in the predecoded window.
  - Candidate 6 artifacts now live under [20260423-pred1-fastpath-candidate6-huffman-direct-lut](</C:/!Layi%20Wkspc/MLV-App/.claude/worktrees/festive-boyd/.claude/profiling/20260423-pred1-fastpath-candidate6-huffman-direct-lut>) with the saved summary in [candidate6_metrics.txt](</C:/!Layi%20Wkspc/MLV-App/.claude/worktrees/festive-boyd/.claude/profiling/20260423-pred1-fastpath-candidate6-huffman-direct-lut/candidate6_metrics.txt:1>).
    - `--threads 1` median-of-run-medians: `raw_uint16_ms = 18.000`
    - `--threads 4` median-of-run-medians: `raw_uint16_ms = 18.000`
    - improvement vs corrected baseline:
      - `--threads 1`: `44.615%` faster
      - `--threads 4`: `53.846%` faster
    - improvement vs kept candidate-1 state:
      - `--threads 1`: `37.930%` faster
      - `--threads 4`: `37.931%` faster
    - all six candidate runs kept the fast path active on all decode-active warm samples
- Current kept source state is candidate 1 plus candidate 6.
- The predictor-1 measurement seam still exports through [src/mlv/video_mlv.c:1386](</C:/!Layi%20Wkspc/MLV-App/.claude/worktrees/festive-boyd/src/mlv/video_mlv.c:1386>), [video_mlv.c:1392](</C:/!Layi%20Wkspc/MLV-App/.claude/worktrees/festive-boyd/src/mlv/video_mlv.c:1392>), [platform/qt/RenderFrameThread.cpp:427](</C:/!Layi%20Wkspc/MLV-App/.claude/worktrees/festive-boyd/platform/qt/RenderFrameThread.cpp:427>), [RenderFrameThread.cpp:433](</C:/!Layi%20Wkspc/MLV-App/.claude/worktrees/festive-boyd/platform/qt/RenderFrameThread.cpp:433>), and [RenderFrameThread.cpp:465](</C:/!Layi%20Wkspc/MLV-App/.claude/worktrees/festive-boyd/platform/qt/RenderFrameThread.cpp:465>).
- App-backed coverage remains strict in [tests/console/test_clip_golden.cpp:1091](</C:/!Layi%20Wkspc/MLV-App/.claude/worktrees/festive-boyd/tests/console/test_clip_golden.cpp:1091>): the predictor-1 measurement test requires the fast path and the measurement path to both be active on `tiny_dual_iso`.
- Final green validation on the final kept build:
  - [console_tests.exe](</C:/!Layi%20Wkspc/MLV-App/.claude/worktrees/festive-boyd/tests/build/console/release/console_tests.exe>) `--check-golden`: `41 tests / 160 assertions / 17 skips / 0 failures`
  - app-backed [console_tests.exe](</C:/!Layi%20Wkspc/MLV-App/.claude/worktrees/festive-boyd/tests/build/console/release/console_tests.exe>) with `MLVAPP_PROFILE_EXE=[MLVApp.exe](</C:/!Layi%20Wkspc/MLV-App/.claude/worktrees/festive-boyd/platform/qt/build/Desktop_Qt_6_10_2_MinGW_64_bit-Release/release/MLVApp.exe:1>)`: `41 tests / 695 assertions / 1 skip / 0 failures`
  - [pipeline_tests.exe](</C:/!Layi%20Wkspc/MLV-App/.claude/worktrees/festive-boyd/tests/build/pipeline/release/pipeline_tests.exe>) `--check-golden`: `44 tests / 507 assertions / 4 skips / 0 failures`
- Phase B and candidate 1 both used the same comparison rule:
  - clip: `large_dual_iso` preview receipt
  - repeats: `3x --threads 1`, `3x --threads 4`
  - frame count: `16` total frames
  - warm discard: first `5` total frames
  - metric rule: medians over remaining warm samples with `raw_uint16_ms > 0`

### Cross-checked from prior analysis

- The earlier activation blocker was real, but the root cause was the fast-path contract rather than a missing predictor-1 dispatch. The shipped Dual ISO receipts are predictor-1, contiguous, and two-component at LJ92 decode time.
- The measurement seam remains coarse and trustworthy enough for candidate triage because it wraps the active fast path without changing the old generic split field meanings.
- The final kept stack remains consistent with the earlier raw-stage diagnosis: the real decoder leverage was in the bitstream / refill side, not in more predictor-loop surgery.

### Final pivot profile

- Final pivot artifact on the accepted `candidate 1 + candidate 6` build now lives at [large_dual_iso_preview_t4_final_pivot.json](</C:/!Layi%20Wkspc/MLV-App/.claude/worktrees/festive-boyd/.claude/profiling/20260423-pred1-fastpath-final-pivot/large_dual_iso_preview_t4_final_pivot.json:1>).
- Warm medians from that `--threads 4`, `--frames 16`, discard-`5` pivot run:
  - `latency_ms = 77.952`
  - `processed16_total_ms = 53.000`
  - `processed8_total_ms = 58.000`
  - `debayered_frame_ms = 29.000`
  - `processing_ms = 18.000`
  - `raw_uint16_ms = 18.000`
  - `raw_uint16_decompress_execute_ms = 16.000`
  - `raw_uint16_lj92_pred1_fast_path_total_ms = 16.000`
- The pivot confirms the accepted decoder work moved LJ92 out of the dominant-playback slot on this VM:
  - `raw_uint16_ms` is now about `23.091%` of warm playback latency
  - the measured LJ92 fast path itself is about `20.525%`
  - `processed16_total_ms` is now the largest warm stage at about `67.991%`
  - `debayered_frame_ms` remains material at about `37.202%`

### Closeout

1. High impact / medium effort: pivot the next performance pass to the post-decode path, starting with the warm `processed16_total_ms` / `debayered_frame_ms` stages rather than reopening predictor-1 LJ92 candidate churn. The most relevant next seam remains [src/processing/raw_processing.c:448](</C:/!Layi%20Wkspc/MLV-App/.claude/worktrees/festive-boyd/src/processing/raw_processing.c:448>).
2. High impact / medium effort: compare any final decoder candidate against both the corrected raw-JSON Phase B baseline in [baseline_metrics.txt](</C:/!Layi%20Wkspc/MLV-App/.claude/worktrees/festive-boyd/.claude/profiling/20260423-pred1-fastpath-baseline/baseline_metrics.txt:1>) and the current kept candidate-1 state, since candidates 2-4 showed that “still above baseline” is not enough to justify stacking a regression.
3. Low impact / low effort: the current source, doc, and artifact set is the final reviewable state for this predictor-1 decoder pass. No additional Phase C queue work remains.

## Predictor-1 Fast-Path Measurement Pass (2026-04-23, implementation)

### Verified locally

- Added separate predictor-1 fast-path telemetry behind `MLVAPP_PRED1_FASTPATH_MEASUREMENT=1` in [src/mlv/liblj92/lj92.c:471](</C:/!Layi%20Wkspc/MLV-App/.claude/worktrees/festive-boyd/src/mlv/liblj92/lj92.c:471>), [lj92.c:548](</C:/!Layi%20Wkspc/MLV-App/.claude/worktrees/festive-boyd/src/mlv/liblj92/lj92.c:548>), and [lj92.c:860](</C:/!Layi%20Wkspc/MLV-App/.claude/worktrees/festive-boyd/src/mlv/liblj92/lj92.c:860>).
  - The new seam keeps the old intrusive generic split unchanged and exports:
    - `raw_uint16_lj92_pred1_fast_path_active`
    - `raw_uint16_lj92_pred1_fast_path_measurement_requested`
    - `raw_uint16_lj92_pred1_fast_path_measurement_active`
    - `raw_uint16_lj92_pred1_fast_path_total_ms`
    - `raw_uint16_lj92_pred1_fast_path_bitstream_ms`
    - `raw_uint16_lj92_pred1_fast_path_predictor_ms`
    - `raw_uint16_lj92_pred1_fast_path_other_ms`
- Threaded those fields through [src/mlv/video_mlv.c:1365](</C:/!Layi%20Wkspc/MLV-App/.claude/worktrees/festive-boyd/src/mlv/video_mlv.c:1365>), [platform/qt/RenderFrameThread.cpp:413](</C:/!Layi%20Wkspc/MLV-App/.claude/worktrees/festive-boyd/platform/qt/RenderFrameThread.cpp:413>), and app-backed playback-profile coverage in [tests/console/test_clip_golden.cpp:1091](</C:/!Layi%20Wkspc/MLV-App/.claude/worktrees/festive-boyd/tests/console/test_clip_golden.cpp:1091>).
- Rebuilt the Qt app and refreshed the green gates:
  - [tests/build/console/release/console_tests.exe](</C:/!Layi%20Wkspc/MLV-App/.claude/worktrees/festive-boyd/tests/build/console/release/console_tests.exe>) `--check-golden`: `41 tests / 160 assertions / 17 skips / 0 failures`
  - app-backed [console_tests.exe](</C:/!Layi%20Wkspc/MLV-App/.claude/worktrees/festive-boyd/tests/build/console/release/console_tests.exe>) `--check-golden` with `MLVAPP_PROFILE_EXE=[MLVApp.exe](</C:/!Layi%20Wkspc/MLV-App/.claude/worktrees/festive-boyd/platform/qt/build/Desktop_Qt_6_10_2_MinGW_64_bit-Release/release/MLVApp.exe:1>)`: `41 tests / 692 assertions / 1 skip / 0 failures`
  - [tests/build/pipeline/release/pipeline_tests.exe](</C:/!Layi%20Wkspc/MLV-App/.claude/worktrees/festive-boyd/tests/build/pipeline/release/pipeline_tests.exe>) `--check-golden`: `44 tests / 507 assertions / 4 skips / 0 failures`
- Fresh smoke artifacts from the new env gate:
  - [tiny_dual_iso_preview_t1_pred1_fastpath_measurement_smoke.json](</C:/!Layi%20Wkspc/MLV-App/.claude/worktrees/festive-boyd/.claude/profiling/20260423-pred1-fastpath-measurement/tiny_dual_iso_preview_t1_pred1_fastpath_measurement_smoke.json:1>)
  - [large_dual_iso_preview_t1_pred1_fastpath_measurement_smoke.json](</C:/!Layi%20Wkspc/MLV-App/.claude/worktrees/festive-boyd/.claude/profiling/20260423-pred1-fastpath-measurement/large_dual_iso_preview_t1_pred1_fastpath_measurement_smoke.json:1>)
- Both smoke runs used preview receipts, `--threads 1`, and `MLVAPP_PRED1_FASTPATH_MEASUREMENT=1`; both currently report:
  - `raw_uint16_lj92_predictor = 1`
  - `raw_uint16_lj92_pred1_fast_path_measurement_requested = true`
  - `raw_uint16_lj92_pred1_fast_path_active = false`
  - `raw_uint16_lj92_pred1_fast_path_measurement_active = false`

### Cross-checked from prior analysis

- The implementation followed the planned Phase A boundary: it added a separate coarse measurement seam without changing the old generic split field meanings.
- The new runtime smoke result contradicts the earlier working assumption that the shipped Dual ISO benchmark fixtures are already exercising the landed mono predictor-1 fast path.

### Needs runtime profiling

- Do not treat Phase B as started yet. With `raw_uint16_lj92_pred1_fast_path_active = false` on both benchmark fixtures, the new clean fast-path metric stays zero and cannot serve as the baseline decision metric yet.
- Inference from the current telemetry plus the existing `video_mlv` decode call shape: the likely ineligibility seam is the fast-path contract in [src/mlv/liblj92/lj92.c:536](</C:/!Layi%20Wkspc/MLV-App/.claude/worktrees/festive-boyd/src/mlv/liblj92/lj92.c:536>) rather than the predictor gate itself. Revalidate the real decode shape before assuming `single component` is true.

### Ranked next steps

1. High impact / low-medium effort: explain why predictor `1` fixtures are still missing the fast path before taking any baseline numbers. The most likely first probe is the eligibility contract in [src/mlv/liblj92/lj92.c:536](</C:/!Layi%20Wkspc/MLV-App/.claude/worktrees/festive-boyd/src/mlv/liblj92/lj92.c:536>).
2. High impact / medium effort: once `large_dual_iso` actually reports `raw_uint16_lj92_pred1_fast_path_active = true`, run the repeated Phase B baseline exactly as planned (`3x` threads=`1`, `3x` threads=`4`, fixed warm window, warm medians only).
3. High impact / medium effort: keep the candidate queue order unchanged after the activation gap is resolved.

## Predictor-1 Overnight Execution Plan (2026-04-23, final handoff)

### Verified locally

- The next implementation pass should measure and optimize the active predictor-1 fast path at [src/mlv/liblj92/lj92.c:509](</C:/!Layi%20Wkspc/MLV-App/.claude/worktrees/festive-boyd/src/mlv/liblj92/lj92.c:509>) rather than the fallback generic path alone.
- The clean next-step measurement should coexist with the current intrusive generic split, not replace it.
- Fresh app-backed validation is part of the real gate for this seam:
  - `console_tests --check-golden`
  - app-backed `console_tests --check-golden` with `MLVAPP_PROFILE_EXE` pointing at the rebuilt [MLVApp.exe](</C:/!Layi%20Wkspc/MLV-App/.claude/worktrees/festive-boyd/platform/qt/build/Desktop_Qt_6_10_2_MinGW_64_bit-Release/release/MLVApp.exe:1>)
  - `pipeline_tests --check-golden`

### Cross-checked from prior analysis

- Claude's feedback usefully tightened the overnight plan: on this VM, a `3%` floor is only meaningful when it comes from repeated long-run warm medians rather than short smoke runs.
- Threads=`1` should stay the decoder-clarity metric, while threads=`4` remains the playback-relevance guardrail.

### Needs runtime profiling

- Baseline and candidate comparisons should use:
  - `3` repeated runs at `--threads 1`
  - `3` repeated runs at `--threads 4`
  - the large `large_dual_iso` fixture as the primary overnight benchmark
- Frame-count rule:
  - target `100+` frames if practical
  - if that is too slow, pick the longest stable frame count available, record it once, and reuse it exactly for every candidate comparison in the pass
- Warm-window rule:
  - if total frames are `>= 100`, discard the first `20`
  - otherwise discard `max(5, ceil(20% of total frames))`
  - compute medians only from the remaining warm frames
- Decision thresholds:
  - `<3%`: noise, revert
  - `3-5%`: keep only if all repeats improve with no regression outlier
  - `>=5%`: keep if threads=`4` `raw_uint16_ms` does not regress beyond roughly `2%`

### Ranked next steps

1. High impact / medium effort: add separate coarse fast-path telemetry behind `MLVAPP_PRED1_FASTPATH_MEASUREMENT=1` without changing the current intrusive generic split fields.
2. High impact / medium effort: establish a repeated large-clip baseline before trying more decoder surgery.
3. High impact / medium effort: work the candidate queue in this order:
   - forced inlining / helper-boundary cleanup
   - bit-buffer refill split
   - pointer-walk loops
   - branch / reload trimming
   - Huffman-side widening only if earlier candidates show real movement

## Predictor-1 Mono Fast Path (2026-04-23, later)

### Verified locally

- Added a narrow predictor-1 mono fast path in [src/mlv/liblj92/lj92.c:497](</C:/!Layi%20Wkspc/MLV-App/.claude/worktrees/festive-boyd/src/mlv/liblj92/lj92.c:497>) and [lj92.c:509](</C:/!Layi%20Wkspc/MLV-App/.claude/worktrees/festive-boyd/src/mlv/liblj92/lj92.c:509>).
  - It activates only when the generic split profiler is off and the decode shape matches the current hot playback path:
    - predictor `1`
    - single component
    - no linearize table
    - `skiplen == 0`
    - contiguous output (`writelen == x * y`)
- `parseScan()` now dispatches to that fast path before the generic profiled loop at [src/mlv/liblj92/lj92.c:740](</C:/!Layi%20Wkspc/MLV-App/.claude/worktrees/festive-boyd/src/mlv/liblj92/lj92.c:740>).
- Current decode call sites that match this fast-path contract:
  - [src/mlv/video_mlv.c:1329](</C:/!Layi%20Wkspc/MLV-App/.claude/worktrees/festive-boyd/src/mlv/video_mlv.c:1329>)
  - [src/dng/dng.c:864](</C:/!Layi%20Wkspc/MLV-App/.claude/worktrees/festive-boyd/src/dng/dng.c:864>)
- Rebuilt the Qt app and pipeline target against the new decoder path.
- Fresh green validation after the fast path:
  - [tests/build/console/release/console_tests.exe](</C:/!Layi%20Wkspc/MLV-App/.claude/worktrees/festive-boyd/tests/build/console/release/console_tests.exe) `--check-golden`: `40 tests / 676 assertions / 0 skips / 0 failures`
  - [tests/build/pipeline/release/pipeline_tests.exe](</C:/!Layi%20Wkspc/MLV-App/.claude/worktrees/festive-boyd/tests/build/pipeline/release/pipeline_tests.exe) `--check-golden`: `44 tests / 507 assertions / 4 skips / 0 failures`
- Fresh smoke artifact:
  - [large_dual_iso_preview_t1_pred1_fast_path.json](</C:/!Layi%20Wkspc/MLV-App/.claude/worktrees/festive-boyd/.claude/profiling/20260423-pred1-fast-path/large_dual_iso_preview_t1_pred1_fast_path.json:1>)
- On the current VM, a 12-frame `--profile-playback` smoke run for `large_dual_iso.mlv` + `large_dual_iso_preview.marxml`, `--threads 1`, produced warm averages of:
  - `raw_uint16_decompress_execute_ms`: `47.78`
  - `raw_uint16_ms`: `50.11`

### Cross-checked from prior analysis

- Splitting the profiled generic loop from the default predictor-1 hot path matches the earlier "lower-overhead measurement before more decoder surgery" direction: when profiling is off, the hot path no longer pays the generic split timing branches or predictor switch.
- The fast path is deliberately narrower than the full LJ92 API surface, which keeps it aligned with the real Dual ISO playback call shape instead of speculating about unused decode forms.

### Needs runtime profiling

- The fresh playback-profile smoke run is still inside the already-documented noisy post-hoist VM band (`~32.5-60.0 ms` warm `raw_uint16_decompress_execute_ms`), so I am not claiming a decisive decoder throughput win from this pass alone.
- If we want the next honest predictor-1 measurement, build it around this new unprofiled fast path with coarse/block-level instrumentation or a decode-only harness, not per-sample timers inside the hot loop.

### Ranked next steps

1. High impact / medium effort: add a lower-overhead predictor-1 measurement seam around [src/mlv/liblj92/lj92.c:509](</C:/!Layi%20Wkspc/MLV-App/.claude/worktrees/festive-boyd/src/mlv/liblj92/lj92.c:509>) and [lj92.c:740](</C:/!Layi%20Wkspc/MLV-App/.claude/worktrees/festive-boyd/src/mlv/liblj92/lj92.c:740>) so the next decoder decision is based on honest timing.
2. High impact / medium effort: if we keep optimizing before more profiling, stay on the same fast-path contract and test the next bitstream-side win around `nextdiff_fast(...)`.
3. Medium impact / low effort: only widen the specialization beyond the current mono/no-linearize/contiguous contract if a real caller shows up that benefits.

## Dual ISO Preview Rowscale Pass (2026-04-22)

### Verified locally

- Instrumented `diso_get_preview()` in [src/mlv/llrawproc/dualiso.c:500](</C:/!Layi%20Wkspc/MLV-App/.claude/worktrees/festive-boyd/src/mlv/llrawproc/dualiso.c:500>) with three stage timers:
  - histogram: [dualiso.c:506-590](</C:/!Layi%20Wkspc/MLV-App/.claude/worktrees/festive-boyd/src/mlv/llrawproc/dualiso.c:506>)
  - regression: [dualiso.c:592-703](</C:/!Layi%20Wkspc/MLV-App/.claude/worktrees/festive-boyd/src/mlv/llrawproc/dualiso.c:592>)
  - rowscale: [dualiso.c:704-756](</C:/!Layi%20Wkspc/MLV-App/.claude/worktrees/festive-boyd/src/mlv/llrawproc/dualiso.c:704>)
- Added preview scratch output storage in [src/mlv/llrawproc/dualiso.h:29](</C:/!Layi%20Wkspc/MLV-App/.claude/worktrees/festive-boyd/src/mlv/llrawproc/dualiso.h:29>) and refactored the preview rowscale pass to read from the source frame and write to a scratch output buffer before copying back.
- The serial out-of-place refactor preserves current output on this branch:
  - [tests/build/pipeline/release/pipeline_tests.exe](</C:/!Layi%20Wkspc/MLV-App/.claude/worktrees/festive-boyd/tests/build/pipeline/release/pipeline_tests.exe) `--check-golden`: `43 tests / 491 assertions / 4 skips / 0 failures`
- Playback-profile telemetry now surfaces the three preview timings per frame through [platform/qt/RenderFrameThread.cpp:140](</C:/!Layi%20Wkspc/MLV-App/.claude/worktrees/festive-boyd/platform/qt/RenderFrameThread.cpp:140>) and [platform/qt/MainWindow.cpp:1419](</C:/!Layi%20Wkspc/MLV-App/.claude/worktrees/festive-boyd/platform/qt/MainWindow.cpp:1419>).
- App-backed playback-profile coverage is green with the new fields:
  - [tests/build/console/release/console_tests.exe](</C:/!Layi%20Wkspc/MLV-App/.claude/worktrees/festive-boyd/tests/build/console/release/console_tests.exe) `--check-golden`: `35 tests / 358 assertions / 0 skips / 0 failures`
- Fresh measurement artifact:
  - [large_dual_iso_preview_t1_2a.json](</C:/!Layi%20Wkspc/MLV-App/.claude/worktrees/festive-boyd/.claude/profiling/20260422-dualiso-preview-rowscale-2a/large_dual_iso_preview_t1_2a.json:1>)
  - [large_dual_iso_preview_t1_2a_metrics.txt](</C:/!Layi%20Wkspc/MLV-App/.claude/worktrees/festive-boyd/.claude/profiling/20260422-dualiso-preview-rowscale-2a/large_dual_iso_preview_t1_2a_metrics.txt:1>)
- Current preview-mode timings on this VM for `large_dual_iso_preview.marxml`, CPU backend, `OMP_NUM_THREADS=1`, `MLVAPP_FORCE_THREADS=1`:
  - average latency: `159.87 ms`
  - average cadence: `140.74 ms`
  - warm latency: `139.02 ms`
  - warm histogram: `0.14 ms`
  - warm regression: `0.00 ms`
  - warm rowscale: `4.43 ms`

### Cross-checked from prior analysis

- Claude correctly identified that a naive `#pragma omp parallel for` over the original in-place rowscale loop would be unsafe because the current algorithm depends on already-mutated `y-2` rows and raw `y+2` rows.
- Claude also correctly noted that regression scratch pooling already existed; the remaining preview-path scratch work in this area was the output buffer and later histogram object reuse.

### Needs runtime profiling

- The new numbers say `diso_get_preview()` is no longer the dominant Dual ISO playback cost on this VM. Before attempting an OMP or AVX2 rowscale pass, profile the rest of the frame again to identify the new dominant stage.
- The regression substage currently reads as `0.00 ms` in the playback-profile samples. That may be "below timer resolution" rather than literally zero; if it becomes interesting later, capture a deeper perf-harness measurement around just that block.

### Ranked next steps

1. High impact / low effort: keep the timer instrumentation and use it to locate the new Dual ISO preview bottleneck outside `diso_get_preview()`.
2. Medium impact / medium effort: if a future measurement still shows rowscale as material on a faster host, parallelize only after a dependency-safe redesign, not by adding OMP to the current loop directly.
3. Medium impact / medium effort: reuse histogram objects in preview mode to remove the remaining `hist_create` / `hist_destroy` churn in [src/mlv/llrawproc/hist.c:33](</C:/!Layi%20Wkspc/MLV-App/.claude/worktrees/festive-boyd/src/mlv/llrawproc/hist.c:33>) and [hist.c:80](</C:/!Layi%20Wkspc/MLV-App/.claude/worktrees/festive-boyd/src/mlv/llrawproc/hist.c:80>).

## Dual ISO Outer Stage Timing Pass (2026-04-22)

### Verified locally

- Added llrawproc substage timers in [src/mlv/llrawproc/llrawproc.c:607](</C:/!Layi%20Wkspc/MLV-App/.claude/worktrees/festive-boyd/src/mlv/llrawproc/llrawproc.c:607>) with getters in [llrawproc.h:28-35](</C:/!Layi%20Wkspc/MLV-App/.claude/worktrees/festive-boyd/src/mlv/llrawproc/llrawproc.h:28>):
  - `llrawproc_total_ms`
  - `llrawproc_dark_frame_ms`
  - `llrawproc_vertical_stripes_ms`
  - `llrawproc_focus_pixels_ms`
  - `llrawproc_bad_pixels_ms`
  - `llrawproc_pattern_noise_ms`
  - `llrawproc_dual_iso_ms`
  - `llrawproc_chroma_smooth_ms`
- Added direct `video_mlv` timing getters in [src/mlv/video_mlv.h:65-72](</C:/!Layi%20Wkspc/MLV-App/.claude/worktrees/festive-boyd/src/mlv/video_mlv.h:65>) and [video_mlv.c:34-41](</C:/!Layi%20Wkspc/MLV-App/.claude/worktrees/festive-boyd/src/mlv/video_mlv.c:34>) for:
  - `raw_uint16_ms`
  - `llrawproc_ms`
  - `debayered_frame_ms`
  - `processing_ms`
  - `processed16_total_ms`
  - `processed16_for_8bit_ms`
  - `processed16_to_8bit_ms`
  - `processed8_total_ms`
- `RenderFrameThread` now ferries those timings into per-frame playback-profile samples through [platform/qt/RenderFrameThread.cpp:215-252](</C:/!Layi%20Wkspc/MLV-App/.claude/worktrees/festive-boyd/platform/qt/RenderFrameThread.cpp:215>) and [platform/qt/MainWindow.cpp:1419-1427](</C:/!Layi%20Wkspc/MLV-App/.claude/worktrees/festive-boyd/platform/qt/MainWindow.cpp:1419>).
- App-backed playback-profile coverage stays green with the new fields:
  - [tests/build/console/release/console_tests.exe](</C:/!Layi%20Wkspc/MLV-App/.claude/worktrees/festive-boyd/tests/build/console/release/console_tests.exe) `--check-golden`: `35 tests / 432 assertions / 0 skips / 0 failures`
- Pipeline coverage remains green:
  - [tests/build/pipeline/release/pipeline_tests.exe](</C:/!Layi%20Wkspc/MLV-App/.claude/worktrees/festive-boyd/tests/build/pipeline/release/pipeline_tests.exe) `--check-golden`: `44 tests / 507 assertions / 4 skips / 0 failures`
- Fresh measurement artifact:
  - [large_dual_iso_preview_outer.json](</C:/!Layi%20Wkspc/MLV-App/.claude/worktrees/festive-boyd/.claude/profiling/20260422-dualiso-outer-stage-timing/large_dual_iso_preview_outer.json:1>)
  - [large_dual_iso_preview_outer_metrics.txt](</C:/!Layi%20Wkspc/MLV-App/.claude/worktrees/festive-boyd/.claude/profiling/20260422-dualiso-outer-stage-timing/large_dual_iso_preview_outer_metrics.txt:1>)
- Warm averages for `large_dual_iso_preview.marxml`, CPU backend, `MLVAPP_FORCE_THREADS=1`, `OMP_NUM_THREADS=1`:
  - frame latency: `310.48 ms`
  - `raw_uint16_ms`: `48.00 ms`
  - `llrawproc_ms`: `16.71 ms`
  - `llrawproc_dual_iso_ms`: `16.71 ms`
  - `dual_iso_preview_total_ms`: `8.00 ms`
  - `debayered_frame_ms`: `82.71 ms`
  - `processing_ms`: `149.00 ms`
  - `processed16_to_8bit_ms`: `8.86 ms`
  - `processed8_total_ms`: `267.71 ms`

### Cross-checked from prior analysis

- Claude's warning about `StageTiming.h` snapshots being translation-unit local was effectively correct in practice for this use: the initial RenderFrameThread snapshot harvest saw zeros because it was reading its own snapshot, not `video_mlv.c`'s. That approach is superseded by the direct `video_mlv` getters.
- Claude's framing about chasing the new dominant cost is confirmed by measurement: `diso_get_preview()` and even total `llrawproc` are now much smaller than processing + debayer on this VM.

### Needs runtime profiling

- Repeat the same outer-stage profile on the real 4090 host before carrying the exact percentages forward; the absolute stage balance can shift materially once llvmpipe/VM overhead is out of the way.
- The current receipt uses the full receipt processing path. If CPU-only playback stays the focus, the next useful breakdown is inside [src/processing/raw_processing.c:448](</C:/!Layi%20Wkspc/MLV-App/.claude/worktrees/festive-boyd/src/processing/raw_processing.c:448>) rather than deeper llrawproc surgery.

### Ranked next steps

1. High impact / medium effort: instrument `applyProcessingObject()` in [src/processing/raw_processing.c:448](</C:/!Layi%20Wkspc/MLV-App/.claude/worktrees/festive-boyd/src/processing/raw_processing.c:448>) to split the now-dominant `processing_ms` stage into actionable substeps.
2. Medium impact / medium effort: re-profile the explicit playback-processing subset against the current preview receipt before changing defaults again; prior analysis showed it was slower overall on this VM despite reducing processing scope.
3. Medium impact / medium effort: only revisit Dual ISO preview OMP/AVX2 work on a faster host if the preview-specific cost grows back into a material share. On this VM it no longer justifies the risk.

## Processing Stage Timing Pass (2026-04-22)

### Verified locally

- Added per-thread processing timing getters in [src/processing/raw_processing.h:90-100](</C:/!Layi%20Wkspc/MLV-App/.claude/worktrees/festive-boyd/src/processing/raw_processing.h:90>) and [raw_processing.c:44-55](</C:/!Layi%20Wkspc/MLV-App/.claude/worktrees/festive-boyd/src/processing/raw_processing.c:44>) for:
  - `processing_setup_ms`
  - `processing_shadows_highlights_prep_ms`
  - `processing_highest_green_ms`
  - `processing_core_ms`
  - `processing_denoise_ms`
  - `processing_rbf_ms`
  - `processing_ca_ms`
  - `processing_core_levels_ms`
  - `processing_core_color_ms`
  - `processing_core_creative_ms`
  - `processing_core_output_ms`
- Instrumented `applyProcessingObject()` in [raw_processing.c:467-641](</C:/!Layi%20Wkspc/MLV-App/.claude/worktrees/festive-boyd/src/processing/raw_processing.c:467>) and the single-thread core path in [raw_processing.c:854-1387](</C:/!Layi%20Wkspc/MLV-App/.claude/worktrees/festive-boyd/src/processing/raw_processing.c:854>) to populate those timings.
- `RenderFrameThread` now emits the processing breakdown into playback-profile samples in [platform/qt/RenderFrameThread.cpp:300-347](</C:/!Layi%20Wkspc/MLV-App/.claude/worktrees/festive-boyd/platform/qt/RenderFrameThread.cpp:300>), including coarse rollups:
  - `processing_other_ms`
  - `processing_core_other_ms`
- App-backed playback-profile coverage stayed green after the new fields landed:
  - [tests/build/console/release/console_tests.exe](</C:/!Layi%20Wkspc/MLV-App/.claude/worktrees/festive-boyd/tests/build/console/release/console_tests.exe) `--check-golden`: `35 tests / 524 assertions / 0 skips / 0 failures`
- Pipeline coverage stayed green:
  - [tests/build/pipeline/release/pipeline_tests.exe](</C:/!Layi%20Wkspc/MLV-App/.claude/worktrees/festive-boyd/tests/build/pipeline/release/pipeline_tests.exe) `--check-golden`: `44 tests / 507 assertions / 4 skips / 0 failures`
- Fresh measurement artifact:
  - [large_dual_iso_preview_processing.json](</C:/!Layi%20Wkspc/MLV-App/.claude/worktrees/festive-boyd/.claude/profiling/20260422-processing-stage-timing/large_dual_iso_preview_processing.json:1>)
  - [large_dual_iso_preview_processing_metrics.txt](</C:/!Layi%20Wkspc/MLV-App/.claude/worktrees/festive-boyd/.claude/profiling/20260422-processing-stage-timing/large_dual_iso_preview_processing_metrics.txt:1>)
- Warm averages for `large_dual_iso_preview.marxml`, CPU backend, `MLVAPP_FORCE_THREADS=1`, `OMP_NUM_THREADS=1`:
  - frame latency: `212.89 ms`
  - `processing_ms`: `99.43 ms` (`46.70%` of warm latency)
  - `debayered_frame_ms`: `62.43 ms` (`29.32%`)
  - `raw_uint16_ms`: `41.29 ms` (`19.39%`)
  - `llrawproc_ms`: `8.14 ms` (`3.82%`)
  - `dual_iso_preview_total_ms`: `5.57 ms` (`2.62%`)
  - inside `processing_ms`:
    - `processing_core_ms`: `77.43 ms`
    - `processing_core_color_ms`: `66.00 ms`
    - `processing_highest_green_ms`: `10.71 ms`
    - `processing_other_ms`: `11.29 ms`
    - `processing_core_levels_ms`: `6.57 ms`
    - `processing_core_output_ms`: `4.86 ms`

### Cross-checked from prior analysis

- Claude's warning that deeper preview-path work had fallen below the meaningful threshold is confirmed by measurement. `dual_iso_preview_total_ms` is now materially smaller than processing, debayer, and even raw unpack on this VM.
- The earlier “processing first, debayer second, raw unpack third” ranking is now verified with a real processing sub-breakdown rather than inferred from a top-level timer alone.

### Needs runtime profiling

- The detailed `processing_core_*` split is only captured in the single-thread path today. On multithreaded playback the top-level `processing_ms` timer still works, but the deep core sub-breakdown should be treated as unavailable rather than representative.
- Re-run the same breakdown on the 4090 host before treating the exact percentages as portable outside this llvmpipe VM.

### Ranked next steps

1. High impact / medium effort: instrument the color-heavy core loop inside [src/processing/raw_processing.c:867-1183](</C:/!Layi%20Wkspc/MLV-App/.claude/worktrees/festive-boyd/src/processing/raw_processing.c:867>) to separate the dominant `processing_core_color_ms` block into white balance / highlight reconstruction / camera-matrix / gamma / gradient segments.
2. Medium impact / medium effort: profile or optimize `analyse_frame_highest_green(...)` next if the color-loop split leaves it as the second-largest processing substage on the host too.
3. Medium impact / medium effort: revisit debayer only after the processing core is better understood, because debayer is now clearly behind processing on this Dual ISO preview workload.

## Exclusive Debayer Timing + Processing Fast Path (2026-04-22)

### Verified locally

- Added exclusive debayer telemetry:
  - [src/mlv/video_mlv.h:73-79](</C:/!Layi%20Wkspc/MLV-App/.claude/worktrees/festive-boyd/src/mlv/video_mlv.h:73>)
  - [src/mlv/video_mlv.c:37-44](</C:/!Layi%20Wkspc/MLV-App/.claude/worktrees/festive-boyd/src/mlv/video_mlv.c:37>)
  - [src/mlv/frame_caching.c:22-59](</C:/!Layi%20Wkspc/MLV-App/.claude/worktrees/festive-boyd/src/mlv/frame_caching.c:22>) and [frame_caching.c:614-664](</C:/!Layi%20Wkspc/MLV-App/.claude/worktrees/festive-boyd/src/mlv/frame_caching.c:614>)
  - [platform/qt/RenderFrameThread.cpp:235-258](</C:/!Layi%20Wkspc/MLV-App/.claude/worktrees/festive-boyd/platform/qt/RenderFrameThread.cpp:235>)
- New playback-profile fields now include:
  - `raw_float_convert_ms`
  - `debayer_exclusive_ms`
  - `debayer_wb_prepare_ms`
  - `debayer_ca_ms`
  - `debayer_kernel_ms`
  - `debayer_wb_undo_ms`
  - `debayer_pipeline_other_ms`
- Fixed the console-runner linker issue by giving [src/mlv/frame_caching.c:30-46](</C:/!Layi%20Wkspc/MLV-App/.claude/worktrees/festive-boyd/src/mlv/frame_caching.c:30>) a local timing helper that falls back to `QueryPerformanceCounter` / `clock_gettime` instead of requiring direct `omp_get_wtime()` linkage in the console target.
- Added a narrow processing fast path for the common preview-playback receipt shape in [src/processing/raw_processing.c:822-836](</C:/!Layi%20Wkspc/MLV-App/.claude/worktrees/festive-boyd/src/processing/raw_processing.c:822>) and [raw_processing.c:879-928](</C:/!Layi%20Wkspc/MLV-App/.claude/worktrees/festive-boyd/src/processing/raw_processing.c:879>):
  - `use_cam_matrix > 0`
  - `allow_creative_adjustments == 0`
  - `highlight_reconstruction == 0`
  - `gradient_enable == 0`
  - `vignette_strength == 0`
  - `exr_mode == 0`
  - `AgX == 0`
- That fast path keeps the same matrix / gamut-desaturate / gamma math as the general path, but avoids dead per-pixel branches for disabled playback features on the preview receipt.
- Gated `analyse_frame_highest_green(...)` in [src/processing/raw_processing.c:536-541](</C:/!Layi%20Wkspc/MLV-App/.claude/worktrees/festive-boyd/src/processing/raw_processing.c:536>) so it only runs when `highlight_reconstruction` is enabled. For the current preview receipt, that cost is now correctly zero.
- Fresh green verification after these changes:
  - [tests/build/console/release/console_tests.exe](</C:/!Layi%20Wkspc/MLV-App/.claude/worktrees/festive-boyd/tests/build/console/release/console_tests.exe) `--check-golden`: `35 tests / 558 assertions / 0 skips / 0 failures`
  - [tests/build/pipeline/release/pipeline_tests.exe](</C:/!Layi%20Wkspc/MLV-App/.claude/worktrees/festive-boyd/tests/build/pipeline/release/pipeline_tests.exe) `--check-golden`: `44 tests / 507 assertions / 4 skips / 0 failures`
- Fresh artifacts:
  - [large_dual_iso_preview_processing_auto.json](</C:/!Layi%20Wkspc/MLV-App/.claude/worktrees/festive-boyd/.claude/profiling/20260422-processing-stage-timing/large_dual_iso_preview_processing_auto.json:1>)
  - [large_dual_iso_preview_processing_auto_metrics.txt](</C:/!Layi%20Wkspc/MLV-App/.claude/worktrees/festive-boyd/.claude/profiling/20260422-processing-stage-timing/large_dual_iso_preview_processing_auto_metrics.txt:1>)
  - [large_dual_iso_preview_processing_t1_exclusive.json](</C:/!Layi%20Wkspc/MLV-App/.claude/worktrees/festive-boyd/.claude/profiling/20260422-processing-stage-timing/large_dual_iso_preview_processing_t1_exclusive.json:1>)
  - [large_dual_iso_preview_processing_t1_exclusive_metrics.txt](</C:/!Layi%20Wkspc/MLV-App/.claude/worktrees/festive-boyd/.claude/profiling/20260422-processing-stage-timing/large_dual_iso_preview_processing_t1_exclusive_metrics.txt:1>)
- Current warm averages after the fast path + highest-green gate:
  - default threads (`worker_threads_effective=8`):
    - frame latency: `128.65 ms`
    - `raw_uint16_ms`: `37.71 ms`
    - `processing_ms`: `24.14 ms`
    - `debayer_exclusive_ms`: `14.71 ms`
    - `llrawproc_ms`: `6.71 ms`
  - single-thread:
    - frame latency: `112.30 ms`
    - `raw_uint16_ms`: `37.75 ms`
    - `processing_ms`: `45.75 ms`
    - `processing_core_color_ms`: `27.50 ms`
    - `debayer_exclusive_ms`: `12.25 ms`
    - `processing_highest_green_ms`: `0.00 ms`
- The immediately preceding single-thread run on the same VM / clip / receipt, before the processing fast path and `highest_green` gate, was:
  - frame latency: `193.19 ms`
  - `processing_ms`: `80.57 ms`
  - `processing_core_color_ms`: `41.14 ms`
  - `processing_highest_green_ms`: `6.71 ms`
  - `debayer_exclusive_ms`: `14.14 ms`
- So the controlled single-thread pass improved from `193.19 ms` to `112.30 ms` warm latency on this preview receipt, while preserving goldens.

### Cross-checked from prior analysis

- Claude's warning about inclusive `debayered_frame_ms` was correct. The exclusive breakdown shows pure debayer is materially smaller than the old inclusive `debayered_frame_ms` number implied.
- Claude's receipt-backed reading was also correct: `large_dual_iso_preview.marxml` has gradient, creative adjustments, highlight reconstruction, vignette, AgX, denoise, and CA disabled, which makes a receipt-specific playback fast path in `raw_processing.c` a good fit instead of a broader algorithm rewrite.

### Needs runtime profiling

- The auto-thread profile remains noisier run-to-run than the controlled single-thread path on this VM, so carry the exact default-thread milliseconds forward cautiously until they are repeated on the host.
- The next timing question is now inside `raw_uint16_ms`, not inside Dual ISO preview. The default-thread run now spends more time in raw unpack than in processing on this VM.

### Ranked next steps

1. High impact / medium effort: instrument [src/mlv/video_mlv.c:820-986](</C:/!Layi%20Wkspc/MLV-App/.claude/worktrees/festive-boyd/src/mlv/video_mlv.c:820>) to split `raw_uint16_ms` into read/decompress/bit-unpack/copy phases. On the latest default-thread run it is the largest exclusive leaf stage.
2. Medium impact / medium effort: keep refining the narrow processing fast path only when the receipt/profile evidence shows the same disabled-feature combination. The current fast path already removed the biggest dead branches for this preview receipt.
3. Medium impact / medium effort: revisit debayer only after raw unpack is understood; the new exclusive numbers show it is no longer the main blocker on this Dual ISO preview workload.

## Raw `raw_uint16` Split + Thread Matrix (2026-04-22)

### Verified locally

- Added playback-profile sample fields for the raw-uint16 sub-stages:
  - `raw_uint16_disk_read_ms`
  - `raw_uint16_decompress_ms`
  - `raw_uint16_unpack_ms`
  - `raw_uint16_copy_ms`
  - `raw_uint16_other_ms`
- The timings are emitted from:
  - [src/mlv/video_mlv.c:821-1021](</C:/!Layi%20Wkspc/MLV-App/.claude/worktrees/festive-boyd/src/mlv/video_mlv.c:821>)
  - [platform/qt/RenderFrameThread.cpp:215-248](</C:/!Layi%20Wkspc/MLV-App/.claude/worktrees/festive-boyd/platform/qt/RenderFrameThread.cpp:215>)
  - [tests/console/test_clip_golden.cpp:313-399](</C:/!Layi%20Wkspc/MLV-App/.claude/worktrees/festive-boyd/tests/console/test_clip_golden.cpp:313>)
- Fresh green verification on the telemetry-bearing build:
  - `console_tests --check-golden`: `35 tests / 586 assertions / 0 skips / 0 failures`
  - `pipeline_tests --check-golden`: `44 tests / 507 assertions / 4 skips / 0 failures`
- Single-thread warm reference after the raw split, same Dual ISO preview receipt:
  - frame latency: `159.83 ms`
  - `raw_uint16_ms`: `36.43 ms`
  - `raw_uint16_disk_read_ms`: `2.57 ms`
  - `raw_uint16_decompress_ms`: `33.29 ms`
  - `processing_ms`: `56.43 ms`
  - `debayer_exclusive_ms`: `14.14 ms`
- Clean current thread matrix on the VM (`large_dual_iso_preview.marxml`, CPU-only):
  - `t1`: `152.16 ms` warm latency
  - `t2`: `121.70 ms`
  - `t4`: `111.84 ms`
  - `t8`: `158.73 ms`
- Corresponding warm raw split from that matrix:
  - `t1`: `raw_uint16_ms 36.43`, `disk 1.71`, `decompress 34.29`
  - `t2`: `raw_uint16_ms 39.29`, `disk 1.29`, `decompress 37.29`
  - `t4`: `raw_uint16_ms 33.43`, `disk 1.43`, `decompress 31.43`
  - `t8`: `raw_uint16_ms 49.14`, `disk 3.00`, `decompress 46.14`
- I also tested a thread-local raw input buffer reuse candidate in `getMlvRawFrameUint16(...)` and reverted it in the same session. It did not earn a clear measured win and introduced avoidable lifetime / sizing questions, so the branch keeps the telemetry but not that speculative optimization.

### Cross-checked from prior analysis

- Claude's guidance was correct: splitting disk I/O from CPU work changed the optimization target materially. On this clip the remaining raw bottleneck is decode, not disk reads and not bit-unpack.
- Claude's thread-count caution was also correct. With the current telemetry in place, `8` threads are not a safe default on this VM for this workload; `4` is the best clean point from the current matrix.

### Needs runtime profiling

- The `t8` slowdown should be repeated on the host before hard-coding any global thread cap. The VM now shows it clearly, but host topology may behave differently.
- The next raw pass should isolate whether the remaining `raw_uint16_decompress_ms` is best attacked through decoder work, asynchronous prefetch/caching, or clip/receipt policy rather than another low-level micro-optimization guess.

### Ranked next steps

1. High impact / medium effort: profile the compressed raw decode path itself ([src/mlv/video_mlv.c:964-988](</C:/!Layi%20Wkspc/MLV-App/.claude/worktrees/festive-boyd/src/mlv/video_mlv.c:964>)) and decide whether the next win is decoder-side or cache/prefetch-side. The raw split shows decode is the actual dominant leaf in `raw_uint16_ms`.
2. High impact / low effort: use `4` worker threads as the local benchmarking point on this VM for Dual ISO preview work. The current matrix makes `4` the best clean thread count here.
3. Medium impact / medium effort: only return to deeper `processing` work if the decode path is left unchanged. On the single-thread reference run, `processing_ms` is still the largest non-raw exclusive stage.

## Raw Decode-Ahead Prototype + Internal Decode Split (2026-04-22)

### Verified locally

- Prototyped a compressed-raw `uint16` decode-ahead ring in [src/mlv/video_mlv.c:463-734](</C:/!Layi%20Wkspc/MLV-App/.claude/worktrees/festive-boyd/src/mlv/video_mlv.c:463>) and [src/mlv/mlv_object.h:203-220](</C:/!Layi%20Wkspc/MLV-App/.claude/worktrees/festive-boyd/src/mlv/mlv_object.h:203>), then measured it directly on the Dual ISO preview path.
- With the experimental path enabled, warm samples showed `raw_uint16_prefetch_hit=true` on `7/7` warm frames and effectively hid raw decode in the foreground sample (`raw_uint16_ms ~1.4-1.7 ms`, `raw_uint16_decompress_ms = 0`), but end-to-end latency did not improve at the thread counts that matter on this VM.
- Measured warm latency with the prototype enabled:
  - `t1`: `176.39 ms`
  - `t4`: `167.86 ms`
  - `t8`: `153.69 ms`
- Baseline before that prototype on the same VM / receipt:
  - `t1`: `152.16 ms`
  - `t4`: `111.84 ms`
  - `t8`: `158.73 ms`
- Conclusion: on this VM the decode-ahead worker hides foreground decode time but steals enough CPU / scheduling budget that total playback gets worse at `t1` and `t4`. It is therefore now **experimental-only**, behind `MLVAPP_EXPERIMENTAL_RAW_UINT16_PREFETCH`, and not part of the default playback path.
- I also fixed the easy correctness/telemetry edges while backing it off:
  - closed the prefetch thread-start race in [src/mlv/video_mlv.c:742-763](</C:/!Layi%20Wkspc/MLV-App/.claude/worktrees/festive-boyd/src/mlv/video_mlv.c:742>)
  - reset raw-stage telemetry on cache-hit paths in [src/mlv/video_mlv.c:1361](</C:/!Layi%20Wkspc/MLV-App/.claude/worktrees/festive-boyd/src/mlv/video_mlv.c:1361>), [video_mlv.c:1494](</C:/!Layi%20Wkspc/MLV-App/.claude/worktrees/festive-boyd/src/mlv/video_mlv.c:1494>), [video_mlv.c:1598](</C:/!Layi%20Wkspc/MLV-App/.claude/worktrees/festive-boyd/src/mlv/video_mlv.c:1598>), and [video_mlv.c:1715](</C:/!Layi%20Wkspc/MLV-App/.claude/worktrees/festive-boyd/src/mlv/video_mlv.c:1715>)
- Added a deeper compressed-decode split:
  - `raw_uint16_decompress_prepare_ms`
  - `raw_uint16_decompress_execute_ms`
  - emitted from [src/mlv/video_mlv.c:1250-1288](</C:/!Layi%20Wkspc/MLV-App/.claude/worktrees/festive-boyd/src/mlv/video_mlv.c:1250>) and surfaced through [platform/qt/RenderFrameThread.cpp:215-248](</C:/!Layi%20Wkspc/MLV-App/.claude/worktrees/festive-boyd/platform/qt/RenderFrameThread.cpp:215>)
- On the current default path (prefetch disabled), the new split shows:
  - `raw_uint16_decompress_prepare_ms` is effectively `0.00 ms`
  - `raw_uint16_decompress_execute_ms` tracks essentially all of `raw_uint16_decompress_ms`
  - so the remaining raw bottleneck is the actual LJ92 decode body, not setup/open work
- Current-source controlled validation on the reverted/default path (temporary relinked app binary because Windows kept the normal `MLVApp.exe` locked):
  - warm `t1` latency: `153.13 ms`
  - `raw_uint16_ms`: `25.43 ms`
  - `raw_uint16_decompress_ms`: `23.86 ms`
  - `raw_uint16_decompress_prepare_ms`: `0.00 ms`
  - `raw_uint16_decompress_execute_ms`: `23.86 ms`
- I also tested one obvious LJ92 fast-path micro-optimization (hoisting the `linearize` branch out of `parsePred6`) and reverted it in the same pass. The quick controlled reruns did not produce a trustworthy speedup, so it is not carried forward on this branch.
- Fresh validation after gating decode-ahead off by default and adding the deeper split:
  - app-backed `console_tests --check-golden` with `MLVAPP_BATCH_EXE` unset: `35 tests / 590 assertions / 1 skip / 0 failures`
  - `pipeline_tests --check-golden`: `44 tests / 507 assertions / 4 skips / 0 failures`

### Cross-checked from prior analysis

- Claude's suggested priority order was right in spirit: pipelining was worth testing before decoder micro-optimization. The measurement just says this VM is not the place to make that path default.
- The latest background review also matched the measured behavior: the prototype increases contention against existing cache/debayer workers, so hiding `raw_uint16_ms` in the sample does not guarantee a frame-latency win.

### Needs runtime profiling

- Default-thread timings are still noisy on this VM. A fresh `t4` repeat on the current default path came in much slower (`157.01 ms`) than the first rerun (`91.36 ms`), so the current thread-count story should still be treated cautiously outside controlled single-thread comparisons.
- The next clean question is inside LJ92 decode itself, not in file I/O, unpack, or decoder setup.

### Ranked next steps

1. High impact / medium effort: instrument [src/mlv/liblj92/lj92.c:404-514](</C:/!Layi%20Wkspc/MLV-App/.claude/worktrees/festive-boyd/src/mlv/liblj92/lj92.c:404>) to split the fast `pred=6` decode body into at least bitstream/Huffman work versus pixel prediction/writeback work. The new `prepare/execute` split shows setup is already negligible.
2. Medium impact / low effort: keep `MLVAPP_EXPERIMENTAL_RAW_UINT16_PREFETCH` as an opt-in host experiment only. Do not enable it by default on this VM.
3. Medium impact / medium effort: if host data later shows spare-core headroom, revisit decode-ahead only after integrating it more cleanly with existing cache invalidation and worker lifetimes.

## Play-start Raw Cache Preroll (2026-04-22)

### Verified locally

- Implemented a small non-blocking play-start preroll hook at [platform/qt/MainWindow.cpp:9850-9878](</C:/!Layi%20Wkspc/MLV-App/.claude/worktrees/festive-boyd/platform/qt/MainWindow.cpp:9850>) that runs after playback debayer selection and Dual ISO playback settings settle.
- The Qt layer now calls [mlv_cache_request_playback_preroll(...)](</C:/!Layi%20Wkspc/MLV-App/.claude/worktrees/festive-boyd/src/mlv/frame_caching.c:302>) when playback is toggled on.
- The new C-layer helper:
  - clamps the current frame / playback end
  - keeps the request non-blocking by reusing the existing cache window/workers
  - primes at most a 2-frame lookahead
  - requests the first uncached future frame through `cache_next`
  - wakes idle cache workers only when caching is already enabled and a concrete future request exists
- Important scope limit: this only helps when playback is already in the cached debayer path (`AMaZE Cached`). It does not turn caching on for bilinear / receipt / non-cached playback modes.
- Added console unit coverage in [tests/console/test_cache_behavior.cpp:232-271](</C:/!Layi%20Wkspc/MLV-App/.claude/worktrees/festive-boyd/tests/console/test_cache_behavior.cpp:232>):
  - `PlaybackPrerollRequestsFirstFutureUncachedFrame`
  - `PlaybackPrerollSlidesWindowTowardLookahead`
- Fresh verification after the change:
  - `console_tests --check-golden`: `37 tests / 160 assertions / 13 skips / 0 failures`
  - `pipeline_tests --check-golden`: `44 tests / 507 assertions / 4 skips / 0 failures`
  - `gui_tests`: `19 passed / 0 failed / 6 skipped`

### Cross-checked from prior analysis

- Claude's and Curie's caution was correct: the safe implementation seam is after `selectDebayerAlgorithm()` / `applyEffectiveDualIsoPlaybackSettings()`, not before, because those paths can reset caches.
- The helper deliberately avoids direct `cache_start_frame` field writes from Qt. Window shifts still go through the existing safe cache-window logic in [src/mlv/frame_caching.c:254-297](</C:/!Layi%20Wkspc/MLV-App/.claude/worktrees/festive-boyd/src/mlv/frame_caching.c:254>).
- Dirac's framing also holds: this is a play-start UX improvement, not a replacement for sustained-FPS work inside LJ92 decode.

### Needs runtime profiling

- I have not yet measured user-visible benefit for this preroll on the real UI path. Headless playback-profile mode does not exercise `on_actionPlay_toggled(true)`, so this pass is currently verified by build/test coverage rather than a new FPS number.
- The next runtime check for this seam should compare first 2-3 played frames with and without cached AMaZE playback active, not steady-state latency.

### Ranked next steps

1. High impact / medium effort: continue the sustained-FPS path inside [src/mlv/liblj92/lj92.c:404-514](</C:/!Layi%20Wkspc/MLV-App/.claude/worktrees/festive-boyd/src/mlv/liblj92/lj92.c:404>). This preroll helps play-start feel; LJ92 work is still the better sustained Dual ISO CPU lever.
2. Medium impact / low effort: if we want to prove the preroll quantitatively, add a small UI-oriented playback-start benchmark that measures the first 2-3 frames under cached AMaZE playback.
3. Low impact / low effort: if future cache work expands beyond the AMaZE-cached path, revisit whether this helper should request more than a 2-frame lookahead.

## Play-start first-frame metric and LJ92 local-state hoist (2026-04-22, late)

### Verified locally

- Added a `play_to_first_frame_ms` metric to the playback-profile path and tightened it so it now latches on the first **requested** render after arming, not just the next `drawFrameReady()` that happens to arrive.
  - state and declarations: [platform/qt/MainWindow.h:595-603](</C:/!Layi%20Wkspc/MLV-App/.claude/worktrees/festive-boyd/platform/qt/MainWindow.h:595>)
  - arming and preroll request: [platform/qt/MainWindow.cpp:1476-1478](</C:/!Layi%20Wkspc/MLV-App/.claude/worktrees/festive-boyd/platform/qt/MainWindow.cpp:1476>)
  - target-frame latching inside `drawFrame()`: [platform/qt/MainWindow.cpp:1087-1096](</C:/!Layi%20Wkspc/MLV-App/.claude/worktrees/festive-boyd/platform/qt/MainWindow.cpp:1087>)
  - presentation completion: [platform/qt/MainWindow.cpp:11205-11206](</C:/!Layi%20Wkspc/MLV-App/.claude/worktrees/festive-boyd/platform/qt/MainWindow.cpp:11205>)
  - exported metadata: [platform/qt/MainWindow.cpp:1614-1620](</C:/!Layi%20Wkspc/MLV-App/.claude/worktrees/festive-boyd/platform/qt/MainWindow.cpp:1614>)
- Refined the preroll metadata so `play_start_preroll_active` now means a preroll request was actually issued, and `play_start_preroll_eligible` captures the broader cached-playback mode check.
- Added app-backed console assertions for the new metadata in [tests/console/test_clip_golden.cpp:329-332](</C:/!Layi%20Wkspc/MLV-App/.claude/worktrees/festive-boyd/tests/console/test_clip_golden.cpp:329>) and [tests/console/test_clip_golden.cpp:775-778](</C:/!Layi%20Wkspc/MLV-App/.claude/worktrees/festive-boyd/tests/console/test_clip_golden.cpp:775>).
- Added a first LJ92 local-state hoist in [src/mlv/liblj92/lj92.c:340-433](</C:/!Layi%20Wkspc/MLV-App/.claude/worktrees/festive-boyd/src/mlv/liblj92/lj92.c:340>) by keeping `b/cnt/ix` in locals through `parsePred6()` and only writing them back on exit.
- Hardened that hoist for the dormant `SLOW_HUFF` branch too, so the helper now syncs local state back through the old decode helpers instead of silently diverging if that path is ever re-enabled: [src/mlv/liblj92/lj92.c:340-348](</C:/!Layi%20Wkspc/MLV-App/.claude/worktrees/festive-boyd/src/mlv/liblj92/lj92.c:340>).
- Fresh green validation after the metric refinement and LJ92 hardening:
  - `console_tests --check-golden`: `37 tests / 604 assertions / 1 skip / 0 failures`
  - `pipeline_tests --check-golden`: `44 tests / 507 assertions / 4 skips / 0 failures`
  - `gui_tests`: `19 passed / 0 failed / 6 skipped`
- Fresh playback-profile artifacts:
  - non-cached preview path: [large_dual_iso_preview_t1_refined.json](</C:/!Layi%20Wkspc/MLV-App/.claude/worktrees/festive-boyd/.claude/profiling/20260422-play-to-first-frame-and-lj92/large_dual_iso_preview_t1_refined.json:1>)
    - `play_start_preroll_active=false`
    - `play_start_preroll_eligible=false`
    - `play_to_first_frame_ms=175.9999`
  - cached AMaZE path: [large_dual_iso_preview_amaze_cached_refined.json](</C:/!Layi%20Wkspc/MLV-App/.claude/worktrees/festive-boyd/.claude/profiling/20260422-play-to-first-frame-and-lj92/large_dual_iso_preview_amaze_cached_refined.json:1>)
    - `play_start_preroll_active=true`
    - `play_start_preroll_eligible=true`
    - `play_to_first_frame_ms=1013.9999`

### Cross-checked from prior analysis

- Claude's concern was right: the earlier version of `play_to_first_frame_ms` was too eager and could have been satisfied by whichever frame completed next. Targeting the first requested render makes the metric much more trustworthy.
- The LJ92 local-hoist direction still matches the earlier decoder reading: predictor math is cheap; state churn around the bitstream walk is the reasonable place to try a low-risk first cut.

### Needs runtime profiling

- The new first-frame metric is still `frameReady after drawFrameReady, before guaranteed window paint`, not literal “button to painted pixel.” That is already recorded in the playback-profile metadata and should stay explicit.
- The cached/non-cached `play_to_first_frame_ms` numbers above validate the instrumentation path, but they are **not** an A/B preroll proof yet because they exercise different playback modes.
- The LJ92 hoist is compiled and validated, but I do **not** have a clean isolated decode-only speedup claim from this VM yet. Current whole-frame t1 profiles improved overall versus the earlier raw-decode split artifact, but the raw substage numbers moved enough that this should still be treated as “plausible small win, needs tighter repeat profiling” rather than a decisive decoder breakthrough.

### Ranked next steps

1. High impact / medium effort: keep the next sustained-FPS pass inside `liblj92`, but instrument `parsePred6()` further before claiming gains from more decoder surgery.
2. Medium impact / low effort: use the new `play_to_first_frame_ms` field for an interactive cached-AMaZE preroll A/B before treating the play-start helper as fully proven UX value.
3. Medium impact / medium effort: if the decoder remains the dominant raw leaf on the next repeat, test the next cheapest bitstream-side win (forced inlining / wider Huffman LUT) before revisiting larger pipeline ideas.

## Same-mode preroll A/B + honest LJ92 predictor telemetry (2026-04-22, near midnight)

### Verified locally

- Added a same-mode preroll control for playback-profile runs via `MLVAPP_DISABLE_PLAY_START_PREROLL`.
  - gate: [platform/qt/MainWindow.cpp:240-257](</C:/!Layi%20Wkspc/MLV-App/.claude/worktrees/festive-boyd/platform/qt/MainWindow.cpp:240>)
  - exported metadata: [platform/qt/MainWindow.cpp:1631-1634](</C:/!Layi%20Wkspc/MLV-App/.claude/worktrees/festive-boyd/platform/qt/MainWindow.cpp:1631>)
- Added an app-backed cached-AMaZE contract test that proves the env gate actually flips the preroll metadata without changing playback mode:
  - [tests/console/test_clip_golden.cpp:794-853](</C:/!Layi%20Wkspc/MLV-App/.claude/worktrees/festive-boyd/tests/console/test_clip_golden.cpp:794>)
- Added honest LJ92 predictor telemetry so the decoder profile can distinguish "pred6 split requested" from "pred6 split applicable":
  - decoder state capture: [src/mlv/liblj92/lj92.c:430-450](</C:/!Layi%20Wkspc/MLV-App/.claude/worktrees/festive-boyd/src/mlv/liblj92/lj92.c:430>) and [src/mlv/liblj92/lj92.c:640-645](</C:/!Layi%20Wkspc/MLV-App/.claude/worktrees/festive-boyd/src/mlv/liblj92/lj92.c:640>)
  - video-level telemetry: [src/mlv/video_mlv.c:39-46](</C:/!Layi%20Wkspc/MLV-App/.claude/worktrees/festive-boyd/src/mlv/video_mlv.c:39>) and [src/mlv/video_mlv.c:1291-1299](</C:/!Layi%20Wkspc/MLV-App/.claude/worktrees/festive-boyd/src/mlv/video_mlv.c:1291>)
  - playback-profile export: [platform/qt/RenderFrameThread.cpp:353-371](</C:/!Layi%20Wkspc/MLV-App/.claude/worktrees/festive-boyd/platform/qt/RenderFrameThread.cpp:353>)
  - app-backed console contract: [tests/console/test_clip_golden.cpp:885-917](</C:/!Layi%20Wkspc/MLV-App/.claude/worktrees/festive-boyd/tests/console/test_clip_golden.cpp:885>)
- Fresh green verification after the telemetry contract change:
  - `console_tests --check-golden`: `39 tests / 644 assertions / 1 skip / 0 failures`
  - `pipeline_tests --check-golden`: `44 tests / 507 assertions / 4 skips / 0 failures`
- Same-mode cached-AMaZE preroll A/B on `large_dual_iso.mlv` + `large_dual_iso_preview.marxml`, `--threads 1 --raw-cache-mb 128 --cache-cpu-cores 1 --playback-debayer amaze-cached`:
  - preroll on artifact: [large_dual_iso_preview_amaze_cached_preroll_on_cached.json](</C:/!Layi%20Wkspc/MLV-App/.claude/worktrees/festive-boyd/.claude/profiling/20260422-preroll-ab/large_dual_iso_preview_amaze_cached_preroll_on_cached.json:1>)
    - `play_to_first_frame_ms = 696.0001`
    - `play_start_preroll_active = true`
    - `play_start_preroll_eligible = true`
  - preroll off artifact: [large_dual_iso_preview_amaze_cached_preroll_off_cached.json](</C:/!Layi%20Wkspc/MLV-App/.claude/worktrees/festive-boyd/.claude/profiling/20260422-preroll-ab/large_dual_iso_preview_amaze_cached_preroll_off_cached.json:1>)
    - `play_to_first_frame_ms = 733.0000`
    - `play_start_preroll_active = false`
    - `play_start_preroll_eligible = true`
  - direct same-mode delta: preroll improved `play_to_first_frame_ms` by about `37 ms` on this VM.
- LJ92 split outcome on the current Dual ISO fixtures:
  - large clip artifact: [large_dual_iso_preview_t1_pred6_split.json](</C:/!Layi%20Wkspc/MLV-App/.claude/worktrees/festive-boyd/.claude/profiling/20260422-lj92-pred6-split/large_dual_iso_preview_t1_pred6_split.json:1>)
  - tiny clip artifact: [tiny_dual_iso_t1_pred6_split.json](</C:/!Layi%20Wkspc/MLV-App/.claude/worktrees/festive-boyd/.claude/profiling/20260422-lj92-pred6-split/tiny_dual_iso_t1_pred6_split.json:1>)
  - both report:
    - `raw_uint16_lj92_pred6_split_requested = true`
    - `raw_uint16_lj92_predictor = 1`
    - `raw_uint16_lj92_pred6_split_active = false`
  - So the current fixtures do not use predictor 6 at all; they are going through predictor 1, not the `parsePred6()` fast path.

### Cross-checked from prior analysis

- Claude's caution was right: the preroll A/B is only meaningful when both runs are truly in cached-AMaZE mode. The first attempt without `--raw-cache-mb` / `--cache-cpu-cores` was not a valid comparison because both runs were preroll-ineligible.
- Claude's earlier prediction about `parsePred6()` was still useful engineering guidance, but the new telemetry shows it simply is not the path taken by the current Dual ISO fixtures.

### Needs runtime profiling

- The cached-AMaZE preroll A/B does show a smaller first-frame wait on this VM, but the later frame latencies remain noisy and mode-specific. I would still treat the measured `~37 ms` first-frame gain as a play-start UX signal, not a sustained-FPS claim.
- The current LJ92 question is no longer "is `nextdiff_fast()` dominating predictor 6?" It is now "what dominates predictor 1 decode on these clips?" The next decoder instrumentation pass should broaden from pred6-only to the generic predictor-1 path.

### Ranked next steps

1. High impact / medium effort: instrument the generic predictor-1 LJ92 path inside [src/mlv/liblj92/lj92.c:740](</C:/!Layi%20Wkspc/MLV-App/.claude/worktrees/festive-boyd/src/mlv/liblj92/lj92.c:740>) so decode time is split into bitstream/Huffman versus predictor/writeback on the path these clips actually use.
2. Medium impact / low effort: keep preroll measured as a play-start UX helper, but do not market it as a sustained-speed win without a cleaner interactive or repeated same-mode benchmark.
3. Medium impact / low effort: once the generic LJ92 split lands, compare it against the earlier `raw_uint16_decompress_execute_ms` leaf before trying wider Huffman LUT or other decoder micro-opts.

## Predictor-1 LJ92 split + generic-path local-state hoist (2026-04-23)

### Verified locally

- Added a generic non-pred6 LJ92 profiling split and exported it through playback-profile JSON:
- decoder capture: [src/mlv/liblj92/lj92.c:740](</C:/!Layi%20Wkspc/MLV-App/.claude/worktrees/festive-boyd/src/mlv/liblj92/lj92.c:740>)
  - video-level telemetry: [src/mlv/video_mlv.c:1268](</C:/!Layi%20Wkspc/MLV-App/.claude/worktrees/festive-boyd/src/mlv/video_mlv.c:1268>)
  - playback-profile export: [platform/qt/RenderFrameThread.cpp:353](</C:/!Layi%20Wkspc/MLV-App/.claude/worktrees/festive-boyd/platform/qt/RenderFrameThread.cpp:353>)
- Tightened the split so the predictor bucket now includes both:
  - the pre-`nextdiff_fast(...)` predictor branch
  - the post-diff reconstruct / linearize / store work
- Hardened the app-backed clip-golden tests so env-gated LJ92 telemetry looks for the first frame with real raw-decode data instead of assuming `frames[0]` always carries it:
  - helper + contracts: [tests/console/test_clip_golden.cpp:121](</C:/!Layi%20Wkspc/MLV-App/.claude/worktrees/festive-boyd/tests/console/test_clip_golden.cpp:121>) and [tests/console/test_clip_golden.cpp:933](</C:/!Layi%20Wkspc/MLV-App/.claude/worktrees/festive-boyd/tests/console/test_clip_golden.cpp:933>)
- Fresh green verification after the predictor-1 pass:
  - `console_tests --check-golden`: `40 tests / 676 assertions / 0 skips / 0 failures`
  - `pipeline_tests --check-golden`: `44 tests / 507 assertions / 4 skips / 0 failures`
- Fresh predictor-1 artifacts:
  - large preview fixture: [large_dual_iso_preview_t1_generic_split_refined.json](</C:/!Layi%20Wkspc/MLV-App/.claude/worktrees/festive-boyd/.claude/profiling/20260423-lj92-generic-split-refined/large_dual_iso_preview_t1_generic_split_refined.json:1>)
  - tiny fixture: [tiny_dual_iso_t1_generic_split_refined.json](</C:/!Layi%20Wkspc/MLV-App/.claude/worktrees/festive-boyd/.claude/profiling/20260423-lj92-generic-split-refined/tiny_dual_iso_t1_generic_split_refined.json:1>)
- Both fixtures confirm the current Dual ISO decode path is still predictor `1`, and the generic split is the active one:
  - large, first measured frame:
    - `raw_uint16_lj92_predictor = 1`
    - `raw_uint16_lj92_generic_split_requested = true`
    - `raw_uint16_lj92_generic_split_active = true`
    - `raw_uint16_lj92_generic_total_ms = 495.0`
    - `raw_uint16_lj92_generic_bitstream_ms = 68.0`
    - `raw_uint16_lj92_generic_predictor_ms = 139.0`
    - `raw_uint16_lj92_generic_other_ms = 288.0`
  - tiny, first measured frame:
    - `raw_uint16_lj92_predictor = 1`
    - `raw_uint16_lj92_generic_total_ms = 532.0`
    - `raw_uint16_lj92_generic_bitstream_ms = 113.0`
    - `raw_uint16_lj92_generic_predictor_ms = 154.0`
    - `raw_uint16_lj92_generic_other_ms = 265.0`
- Landed the first generic-path local-state hoist in the non-pred6 decode loop by switching `parseScan()` from per-symbol `nextdiff(self)` state mutation to local `b/cnt/ix` state with `nextdiff_fast(...)`, writing back once on exit:
- [src/mlv/liblj92/lj92.c:740](</C:/!Layi%20Wkspc/MLV-App/.claude/worktrees/festive-boyd/src/mlv/liblj92/lj92.c:740>)

### Cross-checked from prior analysis

- Claude and Pascal were both directionally right that the next real decoder work was on the generic predictor-1 path, not `parsePred6()`.
- Curie's warning also held up: the honest predictor bucket needs to include the predictor branch before the entropy decode, not just the post-diff math.

### Needs runtime profiling

- The predictor-1 split is informative but intrusive. Because it calls the stage timer inside the per-sample loop, it materially inflates absolute decode time. Treat the split as a relative-shape probe, not as a trustworthy absolute `ms` measurement for the decoder body.
- The generic-path local-state hoist is green and safe, but the sustained playback win on this VM is still noisy:
  - older reference artifact: [large_dual_iso_preview_t1_refined.json](</C:/!Layi%20Wkspc/MLV-App/.claude/worktrees/festive-boyd/.claude/profiling/20260422-play-to-first-frame-and-lj92/large_dual_iso_preview_t1_refined.json:1>) showed warm `raw_uint16_decompress_execute_ms ~37.0`
  - post-hoist reruns currently span roughly `32.5` to `60.0 ms` warm on this VM
  - so the hoist is a plausible small win, but not yet a decisive measured decoder breakthrough

### Ranked next steps

1. High impact / medium effort: keep the next decoder pass inside [src/mlv/liblj92/lj92.c](</C:/!Layi%20Wkspc/MLV-App/.claude/worktrees/festive-boyd/src/mlv/liblj92/lj92.c:740>), but use lower-overhead instrumentation or coarse counters before trusting another per-sample split.
2. Medium impact / low effort: if we want the next actual optimization rather than more profiling, the cheapest remaining candidate is still bitstream/Huffman-side work in the predictor-1 path (forced inlining / local-state cleanup / wider LUT experimentation), but only after the measurement overhead story is cleaner.
3. Medium impact / low effort: keep preroll as a measured UX helper (`~37 ms` first-frame win on this VM), but treat it separately from sustained-FPS decoder work.

## `drawFrameReady()` presentation audit (2026-04-23, festive-boyd-integration)

### Verified locally

- Default playback still uses CPU-side zoom-fit scaling, not the experimental GPU viewport path.
  - `ui->actionZoomFit` is enabled by default at `platform/qt/MainWindow.cpp:2414`.
  - The OpenGL viewport is opt-in through `MLVAPP_EXPERIMENTAL_GL_VIEWPORT` at `platform/qt/GpuDisplayViewport.cpp:100` and `platform/qt/GpuDisplayViewport.cpp:105`.
  - On the default path, `drawFrameReady()` falls into the playback fast-scaling branch at `platform/qt/MainWindow.cpp:11155`.
- The hot presentation bucket is the image/scaling stage, not the pixmap handoff.
  - The main image work sits in `platform/qt/MainWindow.cpp:11152` through `platform/qt/MainWindow.cpp:11280`.
  - The final fallback presentation handoff is `QPixmap::fromImage(displayImage)` plus `m_pGraphicsItem->setPixmap(pic)` at `platform/qt/MainWindow.cpp:11299` through `platform/qt/MainWindow.cpp:11304`.
  - Fresh playback-profile artifacts in `.claude/profiling/20260423-safe-overlap-fastscale/large_dual_iso_preview_t4_safe_run{1,2,3,4}.json` show warm `draw_frame_ready_image_ms` averages of `13.091`, `17.000`, `16.636`, and `12.727`, while `draw_frame_ready_present_ms` averages are `0`, `0`, `0.182`, and `0`.
- The existing safe cache inside the default playback path is geometry-only.
  - `build_fast_playback_scaled_image(...)` at `platform/qt/MainWindow.cpp:132` through `platform/qt/MainWindow.cpp:218` already memoizes `FastPlaybackScaleCache::xOffsets` and `FastPlaybackScaleCache::yOffsets` by `(sourceWidth, sourceHeight, targetWidth, targetHeight)`.
- The full preview cache is intentionally disabled during playback today.
  - `displayPreviewCachingAllowed = !playbackPolicyActive()` at `platform/qt/MainWindow.cpp:11072`.
  - The reusable cache container is `DisplayPreviewCacheEntry` at `platform/qt/MainWindow.h:512`, stored in `m_displayPreviewCache[8]` at `platform/qt/MainWindow.h:627`.
- Any playback cache must own its pixels before the next frame is queued.
  - `drawFrameReady()` initially points `rgb8DisplaySource` at the live render buffer `m_pRawImage` at `platform/qt/MainWindow.cpp:10957`.
  - The other reused staging buffers in this function are `fastPlaybackScaledPic` at `platform/qt/MainWindow.cpp:11169`, `gpu16FallbackProcessed` / `gpu16FallbackRgb8` at `platform/qt/MainWindow.cpp:11088`, and `cpuPreviewProcessed` / `cpuPreviewRgb8` at `platform/qt/MainWindow.cpp:11112`.
  - The current non-playback cache is safe because it deep-copies into `cacheEntry.image = displayImage.copy()` at `platform/qt/MainWindow.cpp:11271` before building `cacheEntry.pixmap`.
- A few state-caching trims are already present and are not the next place to spend effort.
  - Scene-rect churn is already guarded by `m_lastDisplaySceneWidth` / `m_lastDisplaySceneHeight` at `platform/qt/MainWindow.cpp:11012` through `platform/qt/MainWindow.cpp:11018` and `platform/qt/MainWindow.h:625`.
  - The smooth `QImage::scaled(...)` and AVIR branches at `platform/qt/MainWindow.cpp:11182` through `platform/qt/MainWindow.cpp:11220` are not the default playback branch because playback uses `Qt::FastTransformation` unless playback is off, none-debayer is enabled, or caching is enabled at `platform/qt/MainWindow.cpp:10960` through `platform/qt/MainWindow.cpp:10965`.

### Cross-checked from prior analysis

- The earlier safe-overlap note was directionally correct: the common UI-side cost is `draw_frame_ready_image_ms`, not `setPixmap()`.
- The safest default-on presentation trims still sit ahead of `m_pGraphicsItem->setPixmap(...)`, inside scaling and image-ownership work.

### Needs runtime profiling

- `QPixmap::fromImage(displayImage)` at `platform/qt/MainWindow.cpp:11299` still deserves one targeted desktop run even though it is negligible on the current VM traces.
- Zebra cost should stay separate from the default playback ranking because zebras are off by default.
  - Scan-only path: `scanZebrasRgb8(...)` at `platform/qt/MainWindow.cpp:6702`.
  - Mutating path: `drawZebras(...)` at `platform/qt/MainWindow.cpp:6724`.

### Ranked next steps

1. High impact / low-medium effort: keep optimizing `build_fast_playback_scaled_image(...)` at `platform/qt/MainWindow.cpp:132`.
Safe caching opportunity: extend the existing geometry cache to precompute more target-to-source mapping than `xOffsets` / `yOffsets` alone, because this path is hit on default zoom-fit playback and still regenerates pixels every frame.
2. Medium impact / low effort: add a playback-only exact-reuse cache for the last fully owned preview result, keyed by the same playback-visible state the current preview cache already uses at `platform/qt/MainWindow.cpp:11129` through `platform/qt/MainWindow.cpp:11141`.
Exact fields to reuse: `frameIndex`, `signature`, `sourceWidth`, `sourceHeight`, `sceneWidth`, `sceneHeight`, `zoomFit`, `betterResizer`, `zebras`, `gpuScaling`, `transformationMode`, and `devicePixelRatioMilli`.
Safety rule: only cache owned outputs equivalent to `displayImage.copy()` and `QPixmap::fromImage(cacheEntry.image)`. Do not cache borrowed wrappers over `m_pRawImage`, `m_pRawImage16`, `fastPlaybackScaledPic`, `gpu16FallbackRgb8`, or `cpuPreviewRgb8`.
Likely payoff: repeated-frame or held-frame playback reuse, not steady-state unique-frame playback.
3. Medium impact / low effort: if we want the lightest playback-safe trim before enabling any larger playback cache, add a one-entry `QPixmap` reuse path around `cachedPixmapAvailable` / `QPixmap::fromImage(...)` at `platform/qt/MainWindow.cpp:11299`.
This keeps the same visible output while avoiding repeated image-to-pixmap conversion on exact replay of the last owned frame.
4. Low-medium impact / low effort: keep using `m_lastDisplaySceneWidth` / `m_lastDisplaySceneHeight` as the model for safe invalidation.
Any new playback cache should invalidate off the same geometry and presentation-key changes rather than broad playback state toggles.
5. Low impact / low effort: treat zebra-result caching as optional and non-default.
A tiny `underOver` memo keyed by `frameIndex` plus `signature` is safe when shader zebras are active, but it is not a default playback win.

## Overlap follow-up experiments (2026-04-23, current keep)

### Verified locally

- The front/back `ReadyFrame` / `PresentationRequestContext` handoff remains the large real win and is still the foundation to keep.
  - The best 4-run folder median from the overlap artifacts remains well below the pre-overlap `~59 ms` cadence keep point.
  - Current kept profiling comparison:
    - `.claude/profiling/20260423-frontback-overlap/`: `cadence_ms 45.1343`, `processed8_total_ms 33.9999`, `render_thread_work_ms 33.9999`, `draw_frame_ready_total_ms 11.0`, `draw_frame_ready_image_ms 10.5`, `presentation_overhead_ms 10.9745`
    - `.claude/profiling/20260423-frontback-overlap-v5/`: `cadence_ms 44.6134`, `processed8_total_ms 33.5`, `render_thread_work_ms 34.0`, `draw_frame_ready_total_ms 11.0`, `draw_frame_ready_image_ms 10.0`, `presentation_overhead_ms 10.9452`
- The best follow-up on top of the overlap handoff was smaller than hoped but still worth keeping.
  - Current keep choice for `build_fast_playback_scaled_image(...)` in `platform/qt/MainWindow.cpp` is the serial row loop with 4-pixel unrolling.
  - Honest claim: this trims the playback image path slightly on this VM, but it does not change the overall conclusion that we are still short of the `41.7 ms` realtime bar.
- Several plausible follow-ups did not beat the kept overlap path and should stay out of the code:
  - Flattened `pixelOffsets` scaler (`.claude/profiling/20260423-frontback-overlap-v3/`): `cadence_ms 51.4075`
  - Early-slot-release copy experiment (`.claude/profiling/20260423-frontback-overlap-v4/`): `cadence_ms 47.1552`
  - Direct-8 prefetch enabled (`.claude/profiling/20260423-frontback-overlap-v6-prefetch/`): `cadence_ms 53.0956`, with only `2/11` to `3/11` warm `processed8_prefetch_hit` frames per run
- Thread-count selection is not the next honest lever on this VM.
  - Existing sweep artifacts under `.claude/profiling/20260423-thread-sweep/` show a `4-8` thread plateau, not a hidden `2-3 ms` win from picking a different worker count.
  - Safe conclusion: thread-count cleanup is worthwhile for consistency later, but it is not the step most likely to reach realtime on this clip.
- Validation on the kept state is green after the last scaler keep decision:
  - plain `console_tests --check-golden`: `41/160/17/0`
  - `pipeline_tests --check-golden`: `46/526/4/0`
  - app-backed `console_tests --check-golden`: `41/750/1/0`

### Cross-checked from prior analysis

- The remaining dominant buckets are still `render_thread_work_ms` and `draw_frame_ready_image_ms`; the experiments above only changed how much each bucket contributes, not the ranking.
- The direct-8 prefetch worker exists, but on this VM/receipt it is not hitting often enough to beat the simpler overlap keep state.

### Ranked next steps

1. Highest impact / medium effort: add a real third playback stage so UI image-build work is no longer paid on the same critical path as render completion.
   Concrete target: detached scale/present preparation that can overlap with the next render instead of sitting inside `drawFrameReady()`.
2. High impact / medium-high effort: deepen the playback queue beyond the single active request mailbox so `N+2` work can exist while `N+1` is already ready.
   The current two-slot handoff is safe and useful, but it still does not give the worker a true future-frame queue.
3. Medium impact / medium effort: if another structural stage still leaves us short, spend the next performance budget on post-decode processing, not more scaler/prefetch churn.
   Best candidates remain the playback-only processing subset and then runtime-dispatched AVX2 on the surviving color-core / Dual ISO hot loops.

## H2/H3 follow-up and queue prototype (2026-04-23, current dirty pass)

### Verified locally

- I tried the next deeper playback queue / render-request mailbox step and backed it out from the runtime path in this worktree.
  - Parallel background review agreed the next structural idea is a bounded request queue plus deeper overlap, but the local prototype was not keepable in its first form.
  - The prototype broke the app-backed headless playback-profile seam, so it is not the right next commit shape on this branch.
  - The queue code was reverted from the live tree before the current validation sweep; the remaining tracked changes are back to the low-level Dual ISO preview pass plus the earlier `drawFrameReady()` helper extraction.
- The current remaining low-level pass is behavior-safe after one real edge fix.
  - `src/mlv/llrawproc/llrawproc.c` now replaces the scalar restricted-range scale loop with a 14-bit LUT in `scale_restricted_range(...)`.
  - `src/mlv/llrawproc/dualiso.c` / `.h` now reuse preview histogram storage inside `dualiso_preview_scratch_t`, build the preview rowscale curve through a LUT, and only copy dark rows into the preview scratch output buffer before the shadow-fix pass.
  - The first dark-row LUT version was wrong on the tiny app-backed fixture because it dereferenced the `+2` source row unconditionally near the bottom edge; that is now fixed by only reading `source_row_next2` on the branches that actually need it.
- Validation is green again on the current dirty tree after backing out the queue prototype and fixing the dark-row edge bug.
  - plain `console_tests --check-golden`: `41/160/17/0`
  - app-backed `console_tests --check-golden` with fresh `MLVApp.exe`: `41/758/0/0`
  - `pipeline_tests --check-golden`: `46/526/4/0`

### Needs runtime profiling

- The low-level H2/H3 pass still does **not** have an honest throughput win on this VM.
  - Fresh artifacts under `.claude/profiling/20260423-h2h3-keepcheck/` are too noisy and too slow to justify a commit as a playback optimization keep.
  - What the current reruns do show:
    - `dual_iso_preview_histogram_ms` is effectively `0` on most warm samples
    - `dual_iso_preview_rowscale_ms` is still in the `~5-9 ms` band
    - end-to-end warm medians are currently worse than the earlier `72b41aa9` overlap keep, so I am not counting this as a real playback win yet
- Safe conclusion:
  - the low-level pass is plausible as allocator/churn cleanup
  - it is **not** yet strong enough to commit as a performance improvement without a cleaner A/B showing that it beats the current overlap baseline on the same VM conditions

### Ranked next steps

1. Highest impact / medium effort: revisit the queue / deeper-overlap idea in an isolated follow-up branch, but make it generation-aware from the start and keep it out of the current branch until the app-backed profile seam is green.
2. High impact / medium effort: if the next pass stays on this branch, target `drawFrameReady()` image cost again rather than more llrawproc churn.
   The strongest code-level suggestion from the background review was a slot-owned pre-scaled playback image / third stage, not another predictor or preview micro-pass.
3. Medium impact / low-medium effort: if we want to keep exploring H2/H3 locally, add a tighter same-session A/B harness before committing more preview-loop tweaks.
   Right now the VM variance is large enough that small llrawproc wins are being drowned out by bigger render/presentation swings.

## Render-slot pre-scale keep (2026-04-23, current keep)

### Verified locally

- The non-winning H2/H3 llrawproc pass was restored out of the live tree before this keep.
  - Current tracked runtime changes are all on the Qt playback path plus the note updates.
- The kept implementation moves the default zoom-fit playback scale result into the render slot itself.
  - Added shared fast-scaling helpers in `platform/qt/PlaybackScaling.h`.
  - `platform/qt/RenderFrameThread.h` / `.cpp` now accept per-request presentation-prep options and can publish a slot-owned `playbackScaledImage8` buffer alongside the raw processed8 frame.
  - `platform/qt/MainWindow.cpp` now queues the target presentation geometry with each render request and consumes the slot-owned pre-scaled image on the default playback path.
- The first render-slot pre-scale attempt built and tested green but did **not** change runtime behavior because `drawFrameReady()` still decided between smooth and fast presentation from `ui->actionPlay->isChecked()` instead of `playbackPolicyActive()`.
  - Fresh probe artifact showing the false start: `.claude/profiling/20260423-render-prescale-v1/large_dual_iso_preview_t4_prescale_run1c.json`
  - In that false-start run, `render_thread_playback_scale_active = true` but `draw_frame_ready_prescaled_image_active = false` on every frame, so `draw_frame_ready_image_ms` stayed around `10-12`.
- The actual keep was the follow-up fix in `platform/qt/MainWindow.cpp`:
  - align the presentation fast-path gate with `playbackPolicyActive()`
  - consume the slot-owned pre-scaled image from the actual frame payload instead of depending on the side `PresentationRequestContext` deque for fast-path eligibility
- Fresh final artifacts live in `.claude/profiling/20260423-render-prescale-v2-final/`.
  - warm medians by run:
    - `run1`: `cadence_ms 39.263`, `render_thread_work_ms 37.9999`, `draw_frame_ready_image_ms 0`, `draw_frame_ready_total_ms 0`, `render_thread_playback_scale_ms ~1`
    - `run2`: `cadence_ms 43.2626`, `render_thread_work_ms 40.9999`, `draw_frame_ready_image_ms 0`, `draw_frame_ready_total_ms 0`, `render_thread_playback_scale_ms ~1`
    - `run3`: `cadence_ms 37.9273`, `render_thread_work_ms 36.0000`, `draw_frame_ready_image_ms 0`, `draw_frame_ready_total_ms 0`, `render_thread_playback_scale_ms ~1`
    - `run4`: `cadence_ms 39.8684`, `render_thread_work_ms 38.0001`, `draw_frame_ready_image_ms 0`, `draw_frame_ready_total_ms 0`, `render_thread_playback_scale_ms 0`
  - across-run median of warm medians: `39.8684 ms`
  - native realtime budget for the large fixture at `23.976 fps`: `41.708 ms`
- Honest claim:
  - this block clears the committed M1 bar on this VM for the target clip/receipt
  - the gain came from deleting the serial UI-side image-build bucket, not from more decoder churn
  - the remaining dominant warm bucket is now `render_thread_work_ms ~36-41`
- Fresh validation on the kept state:
  - plain `console_tests --check-golden`: `41/160/17/0`
  - app-backed `console_tests --check-golden` with fresh `MLVApp.exe`: `41/758/0/0`
  - `pipeline_tests --check-golden`: `46/526/4/0`

### Cross-checked from prior analysis

- This confirms the earlier ranking that `draw_frame_ready_image_ms` was the last large serial presentation bucket worth attacking before deeper queue work.
- It also confirms that the render/request metadata seam is still fragile enough to avoid using the side deque as a hard requirement for hot-path eligibility.

### Ranked next steps

1. High impact / medium effort: deepen the overlap beyond the current two-slot handoff so `N+2` can exist while `N+1` is already ready.
   With `drawFrameReady.image` deleted on the target path, the next ceiling is the render slot / mailbox depth rather than UI image work.
2. High impact / medium effort: introduce the playback-only processing subset now that realtime is met.
   This should be the shortest path from realtime to a more comfortable `>24 fps` margin and toward the `30 fps` stretch target.
3. Medium impact / medium effort: add runtime-dispatched AVX2 on the surviving hot loops after the playback-only subset lands.
   Best candidates remain the color-core processing path and the Dual ISO blend path, not more predictor-1 work.

## Direct processed8 OpenMP keep (2026-04-23, current keep)

### Verified locally

- I tried a render-thread direct-8 playback-subset pass first and explicitly did **not** keep it.
  - Fresh scratch artifact: `.claude/profiling/20260423-subset-renderthread-v1/large_dual_iso_preview_t4_subset_run1.json`
  - The large Dual ISO preview receipt does support subset mode when explicitly requested (`playback_processing_effective = subset`), but the CPU subset path is not the honest next VM lever on this receipt.
  - Warm subset numbers on that scratch run were worse than the current receipt path:
    - `warm cadence_ms ~60.4`
    - `warm render_thread_work_ms ~56`
    - `render_thread_cpu_preview_processing_ms ~21`
  - Safe conclusion: do not keep the render-thread subset experiment as a playback optimization on this branch.
- The kept follow-up was lower-risk and directly on the hot current receipt path in `src/processing/raw_processing.c`.
  - `applyProcessingObject8(...)` no longer spins up per-frame pthread chunks for the direct processed-8 color pass.
  - The kept change now partitions rows across stable OpenMP workers and reuses the existing direct-8 math unchanged.
  - This keeps the exact direct processed-8 output contract while deleting frame-by-frame thread creation / join overhead from the hot playback path.
- Fresh final artifacts live in `.claude/profiling/20260423-direct8-omp-v2-final/`.
  - warm cadence medians by run:
    - `run1`: `37.7476`
    - `run2`: `36.6793`
    - `run3`: `38.5429`
    - `run4`: `37.7434`
  - across-run upper median of warm medians: `37.7476`
  - comparable prior keep folder `.claude/profiling/20260423-render-prescale-v2-final/` now recomputes to an across-run upper median around `39.263`
  - warm `processed8_total_ms` moved from about `37.0` down to about `35.0`
  - warm `render_thread_work_ms` moved from roughly `37-40` down to roughly `35-36`
- Honest claim:
  - this is a real smaller follow-up win on top of the render-slot pre-scale keep, not another structural breakthrough
  - it pushes the target receipt from “just below realtime” toward a more comfortable margin on this VM
  - it does **not** by itself close the full gap to the `30 fps` stretch target (`33.333 ms`)
- Fresh validation on the kept state:
  - plain `console_tests --check-golden`: `41/160/17/0`
  - app-backed `console_tests --check-golden` with fresh `MLVApp.exe`: `41/750/1/0`
  - `pipeline_tests --check-golden`: `46/526/4/0`

### Cross-checked from prior analysis

- The receipt is still on the direct processed-8 playback path, so the most honest post-M1 CPU optimization remains the direct-8 kernel and its worker orchestration, not a subset mode the current receipt does not need.
- The next largest bucket is still `render_thread_work_ms`; this keep only trims that bucket modestly, which matches the measured `~1-2 ms` cadence gain.

### Ranked next steps

1. High impact / medium effort: deepen the overlap beyond the current two-slot handoff so `N+2` can exist while `N+1` is already ready.
   This is still the clearest structural lever if we want more margin without depending on receipt-specific subset compatibility.
2. High impact / medium effort: add runtime-dispatched AVX2 on the surviving hot current-receipt loops.
   The best target is still the direct processed-8 color core in `src/processing/raw_processing.c`, because that path is active on the real large Dual ISO preview receipt today.
3. Medium impact / medium effort: only revisit playback-only subset work after we define a subset that is actually cheaper than the current direct processed-8 path on the target receipt.

## Direct processed8 follow-up rejects + creative-curve guard (2026-04-23)

### Verified locally

- I ran three additional post-decode micro-passes on top of the current direct processed8 keep and rejected all three as playback keeps on this VM:
  - fused `pre_calc_levels` into the direct-8 row kernel
    - scratch folder: `.claude/profiling/20260423-direct8-fused-levels-v1/`
    - result: clearly slower; not kept
  - precomposed the direct-8 creative-curve chain into single-hop tables
    - scratch folders: `.claude/profiling/20260423-direct8-curvecache-v1/` and `.claude/profiling/20260423-direct8-curvecache-v1-keepcheck/`
    - result: did not hold up across reruns; not kept
  - forced the direct-8 levels pass to honor the caller thread count explicitly
    - scratch folders: `.claude/profiling/20260423-direct8-levelthreads-v1-keepcheck/` and `.claude/profiling/20260423-direct8-levelthreads-v1-staggered/`
    - result: mixed single-run signal, but not a stable keep; not kept
- The only code change I kept from this follow-up block is a stronger direct-8 regression guard in `tests/pipeline/test_dual_iso_pipeline.cpp`.
  - `DirectProcessed8FastPathMatchesShiftedProcessed16WithCreativeCurveCache` forces a non-identity gradation curve while the direct processed8 path stays active and asserts zero-diff against `(processed16 >> 8)`.
  - This protects the hot path against future lookup-table shortcuts that only happen to pass the neutral-curve case.
- Fresh validation on the kept tree after reverting the non-winners:
  - plain `console_tests --check-golden`: `41/160/17/0`
  - app-backed `console_tests --check-golden` with fresh `MLVApp.exe`: `41/750/1/0`
  - `pipeline_tests --check-golden`: `47/537/4/0`

### Cross-checked from prior analysis

- The current kept runtime baseline is still the direct processed8 OpenMP path from `.claude/profiling/20260423-direct8-omp-v2-final/`, with warm cadence medians in the high-`37 ms` band.
- These rejected follow-ups reinforce the earlier ranking:
  - the next honest CPU lever is a true SIMD/runtime-dispatch pass on the surviving direct processed8 color core
  - not more table-lookup reshaping or small llrawproc preview churn without tighter same-session A/B controls

### Ranked next steps

1. High impact / medium effort: add runtime-dispatched AVX2 on the direct processed8 color core in `src/processing/raw_processing.c`, with scalar fallback preserved and parity checked against the existing direct-8 zero-diff tests.
2. High impact / medium effort: revisit playback-only subset work only if it is measurably cheaper than the current direct processed8 path on the real large Dual ISO preview receipt.
3. Medium impact / low-medium effort: if another micro-pass is attempted, capture same-session control and candidate artifacts before and after the change so VM drift does not masquerade as a real keep.

## Async-prep follow-up status (2026-05-25)

### Verified locally

- The playback async-prep branch remains clean at `codex/playback-async-split` with commit `6288cb80ef112f826268b4364ae7d73b9401b00c`.
- The repo closeout path is still blocked on the protected target because the root checkout carries preexisting tracked dirty files and the work-block closeout tool classifies the branch as a stale transaction branch.
- Fresh closeout output reports:
  - `repo_closed_postcondition_failed`
  - non-exempt dirty tracked files in the protected root checkout
  - stale transaction branch `codex/playback-async-split`
- The async-prep profile payloads confirm the GUI-side prep is no longer the ceiling:
  - `draw_frame_ready_image_ms = 0` on the warm path
  - `draw_frame_ready_present_ms = 0`
  - worker conflation counters stayed at `0`
- The remaining warm buckets on the large Dual ISO fixture are still in the render/process path:
  - `render_thread_work_ms`
  - `processed16_total_ms`
  - `llrawproc_ms`
  - `processing_core_ms` stays much smaller than those buckets

### Cross-checked from prior analysis

- The render-thread queue is already non-trivial: `RenderFrameThread` keeps four render request slots and a four-entry request queue, so the next easy win is not just a wider mailbox.
- The current async split already removed the serial GUI image-build bucket, which means the next performance step should target the render/process hot loops or a truly cheaper playback subset.

### Ranked next steps

1. High impact / medium effort: prototype the playback-only subset only if we can prove it beats the current processed path on the same receipt.
2. High impact / medium effort: add runtime-dispatched AVX2 on the surviving hot current-receipt loops if subset work does not win cleanly.
3. Medium impact / medium effort: keep a close eye on queue depth only after the hot compute buckets are trimmed, because the current telemetry says compute still dominates.

## Async follow-up experiments (2026-05-25)

### Verified locally

- I ran three compute-side experiment families against the same large Dual ISO fixture after the async-prep split:
  - `MLVAPP_EXPERIMENTAL_PROCESSED8_PREFETCH=1`
  - `MLVAPP_ENABLE_AVX2_INTRIN_DIRECT8=1`
  - `--playback-processing subset`
- The processed8 prefetch path did not produce a warm hit on the measured frames and did not improve cadence.
  - smoke warm cadence: about `25.966 ms`
  - `processed8_prefetch_hit`: `False`
- The hand-tuned AVX2 direct8 intrinsics path was a clear regression on this receipt.
  - 4-run across-run warm median: `52.4097 ms`
  - best run: `51.1612 ms`
  - worst run: `53.5724 ms`
- Forcing playback subset was also a regression on this receipt, even though it was supported.
  - `playback_processing_effective = subset`
  - `playback_processing_supported = True`
  - warm cadence on the smoke run: about `49.965 ms`
- Worker scaling to `--threads 2` did not help overall cadence on this fixture.
  - 4-run across-run warm median: `33.9629 ms`
  - best run: `19.7496 ms`
  - worst run: `34.5155 ms`

### Cross-checked from prior analysis

- These runs reinforce the earlier conclusion that the current 1-thread receipt path is still the best measured lane on this VM.
- The compute-side experiments did reduce some sub-buckets (`llrawproc_ms`, `processed16_total_ms`) in isolation, but they did not improve end-to-end cadence enough to justify a branch-wide switch.

### Ranked next steps

1. High impact / medium effort: stop chasing the known regressions above and return to the current direct receipt path as the baseline.
2. High impact / medium effort: look for a smaller compute-side reduction inside the current receipt path rather than a wholesale path swap.
3. Medium impact / medium effort: if a new compute candidate is tried, measure it against the same 1-thread receipt baseline before touching the docs or commit set.

## Playback scale-threshold keep (2026-05-25)

### Verified locally

- I rejected a black/white sync shortcut in `src/mlv/video_mlv.c` after it regressed the warm cadence on the same large Dual ISO fixture:
  - across-run warm cadence median moved from about `53.24 ms` to `54.95 ms`
  - `render_thread_work_ms` moved from about `49.17 ms` to `52.25 ms`
  - `processing_core_ms` moved from about `8.12 ms` to `12.25 ms`
- I then tightened the playback scaling helper in [`platform/qt/PlaybackScaling.h`](C:/!Layi%20Wkspc/MLV-App/platform/qt/PlaybackScaling.h) so the OMP loop only fans out when the scaled image is at least `262144` pixels.
- That change produced a repeatable improvement on the same `large_dual_iso.mlv` + `large_dual_iso_hq.marxml` receipt with `MLVAPP_PLAYBACK_SCALE_FACTOR=4`:
  - first 4-run warm median: `40.507475 ms` latency, `39.2499566078186 ms` render-thread total, `2.00003385543823 ms` playback-scale time
  - repeat 4-run warm median: `28.148825 ms` latency, `27.9999971389771 ms` render-thread total, `0.749945640563965 ms` playback-scale time
  - both runs were materially faster than the earlier kept baseline (`53.240183333333334 ms` latency, `53.2499551773071 ms` render-thread total, `6.25008344650269 ms` playback-scale time)
- A matching threshold on the visible-path RGB16→RGB8 converter in `platform/qt/MainWindow.cpp` did not help the UI smoke:
  - `draw_frame_ready_image_ms` rose from about `20 ms` to about `43 ms`
  - that revert left the better headless/display-scale win intact and avoided a visible-path regression
- A matching threshold on the raw `pre_calc_levels` sweep in `src/processing/raw_processing.c` also failed to improve the end-to-end warm cadence on this receipt, so that idea was reverted as well.

### Cross-checked from prior analysis

- The current playback-scale hotspot is not the GUI image build anymore; it is the render-thread scale helper that was still parallelizing a relatively small 4x playback job.
- The repeat run confirms the threshold change is not a single-run fluke.

### Ranked next steps

1. High impact / low-medium effort: keep the current playback-scale threshold and fold it into the main branch once the repo is ready for another commit.
2. Medium impact / medium effort: if more headroom is needed, profile the `playbackBuildFastScaledRgb8` row loop itself now that the OMP overhead is reduced.
3. Medium impact / low effort: run one sanity pass on a different playback scale or fixture before trying more invasive scaling changes.

## 2026-05-25 - closeout stabilization and repo closeout completion

### Verified locally

- I committed the closeout-policy and smoke-suite fixes to the work-block branch, then re-ran the brokered finalize path successfully.
- The current branch has been integrated into `master`, the remote `fork/master` was pushed, and the working tree is clean.
- The final closeout blockers were a stale managed integration worktree under `.claude-state/closeout/integration-worktrees` plus a registry entry in Git's worktree metadata; removing the directory and pruning the worktree registry cleared the repo-closed postcondition.

### Cross-checked from prior analysis

- The playback scale-threshold keep remains the strongest measured playback win from this iteration.
- The rejected GUI threshold and raw `pre_calc_levels` threshold are still dead ends and remain reverted.

### Ranked next steps

1. High impact / low effort: if more playback headroom is needed later, profile the render-thread row loop behind the current `PlaybackScaling.h` threshold.
2. Medium impact / low effort: keep the repo closeout workflow changes that made validation budgets and stale-worktree cleanup explicit.
3. Low effort: use the current `master` branch as the next baseline for any new playback experiment.

## Stale-worktree cleanup regression and threshold repeat check (2026-05-25)

### Verified locally

- I added a regression test in [`tools/repo_hygiene/test_brokered_closeout.py`](C:/!Layi%20Wkspc/MLV-App/tools/repo_hygiene/test_brokered_closeout.py) proving that `remove_worktree()` still clears Git's worktree registry with `git worktree prune` even if the on-disk detached worktree path has already been removed.
- The new test covers the stale-registry case that blocked earlier closeout cleanup, so the prune-after-remove behavior is now locked into the repo's executable contract.
- I reran the same `large_dual_iso.mlv` + `large_dual_iso_hq.marxml` threshold profile shape in a fresh scratch folder with `MLVAPP_PLAYBACK_SCALE_FACTOR=4`, `--frames 16`, and `--threads 1`.
- Warm-window medians for the repeat set came back as:
  - `run1`: `cadence_ms 23.70`, `render_thread_work_ms 23.00`, `render_thread_playback_scale_ms 0.50`
  - `run2`: `cadence_ms 27.28`, `render_thread_work_ms 26.50`, `render_thread_playback_scale_ms 0.50`
  - `run3`: `cadence_ms 17.66`, `render_thread_work_ms 16.00`, `render_thread_playback_scale_ms 0.00`
  - `run4`: `cadence_ms 24.30`, `render_thread_work_ms 22.00`, `render_thread_playback_scale_ms 0.00`
- Across-run medians of those warm medians were `cadence_ms 23.9969`, `render_thread_work_ms 22.5`, and `render_thread_playback_scale_ms 0.25`.
- I compared that against the immediately prior threshold repeat set using the same warm-window calculation, which landed at `cadence_ms 28.1488`, `render_thread_work_ms 27.2501`, and `render_thread_playback_scale_ms 0.7499`.
- The new repeat set looks faster, but the run-to-run spread is large enough that I am not claiming a new code win from the same code path; the current `PlaybackScaling.h` threshold remains the best kept playback change, and this pass did not uncover a safer follow-up optimization.

### Cross-checked from prior analysis

- The stale-worktree cleanup helper already had the right runtime shape (`worktree remove` followed by `worktree prune`); the new regression test makes that behavior explicit and durable.
- The scale-threshold keep still appears to be the right baseline, but the measured deltas in this pass sit close to the normal variance band for this VM.

### Ranked next steps

1. High impact / low effort: keep the current `PlaybackScaling.h` threshold as the active playback baseline.
2. Medium impact / medium effort: only profile the render-thread row loop behind the threshold if we need more headroom later.
3. Low effort: preserve the new stale-worktree cleanup regression as part of the closeout safety net and avoid reintroducing prune regressions.

## GUI geometry investigation (2026-05-25)

### Verified locally

- I re-read the visible smoke JSON for `large_dual_iso.mlv` with overlays enabled (`scope=waveform`, `zebras=true`) and confirmed the first frame reported `render_thread_rendered_width=452` and `render_thread_rendered_height=567`.
- I then re-ran a clean visible smoke with overlays disabled (`scope=none`, `zebras=false`) and the same receipt/preset path; the first frame still reported `render_thread_rendered_width=452` and `render_thread_rendered_height=567`.
- The ratio `567 / 452 = 1.254425` matches the portrait ratio `2268 / 1808 = 1.254425` used throughout the repo's Dual ISO fixture tests and scaling tests, so the on-screen frame shape is consistent with a portrait source rather than an aspect-ratio distortion.
- In the clean visible pass, `draw_frame_ready_prescaled_image_active=true`, `draw_frame_ready_image_ms=0`, `draw_frame_ready_overlay_ms=0`, `draw_frame_ready_scopes_ms=0`, and `draw_frame_ready_total_ms=20.999908447265625`, which means the fast preview path is active but the GUI overlay stack is not what makes the picture look red or strange.
- The earlier red/pink appearance came from the explicit zebra/scope smoke settings, because the overlay path intentionally recolors pixels and draws scopes on top of the image.

### Cross-checked from prior analysis

- `computeDisplaySceneGeometry()` preserves aspect ratio and only fits the source into the current viewport or stretch factors; it does not contain an aspect-squashing branch.
- The receipt for `large_dual_iso.hq.marxml` uses `stretchFactorX=1` and `stretchFactorY=1`, so the receipt itself is not asking for anamorphic correction.
- The repo's Dual ISO scaling tests and comments repeatedly use `1808x2268` as the canonical portrait-ish shape for this family of clips.

### Ranked next steps

1. High impact / low effort: when visually judging playback, keep `scope=none` and `zebras=false` so the clean preview path is not conflated with the scope/overlay presentation.
2. High impact / low effort: treat the current `large_dual_iso` visible playback look as a portrait preview, not a stretch regression, unless a clean HQ/Auto run shows a different width/height ratio.
3. Medium impact / medium effort: if we still want better GUI UX, investigate whether the fast preview quality tier is simply too low-fidelity for this clip shape, rather than chasing geometry that is already consistent.

## Preferred visual smoke recipe (2026-05-25)

### Verified locally

- For the `large_dual_iso.mlv` fixture, the cleanest UX-facing smoke is `HQ x4` with a vertical stretch correction of `0.3333`, not the default `Fast` preview tier.
- The working receipt recipe is:
  - `stretchFactorX=1`
  - `stretchFactorY=0.3333`
  - `chromaSmooth=2`
  - `patternNoise=1`
- The useful runtime overrides for visual smoke are:
  - `MLVAPP_PLAYBACK_PREFER_HQ_MEAN23=1`
  - `MLVAPP_PLAYBACK_SCALE_FACTOR=4`
- The GUI presentation should keep `scope=none` and `zebras=false` unless the user is explicitly testing overlays.

### Cross-checked from prior analysis

- `playback_processing=auto` on this receipt resolves to the `receipt` processing path.
- The `master` build executable is available at [`platform/qt/build-release/release/MLVApp.exe`](C:/!Layi%20Wkspc/MLV-App/platform/qt/build-release/release/MLVApp.exe).

### Ranked next steps

1. High impact / low effort: use the `HQ x4` visual recipe above as the default starting point for new clip smoke tests.
2. High impact / low effort: vary one clip at a time, keeping the receipt and environment overrides fixed so the visual result stays comparable.
3. Medium impact / low effort: only drop to `Fast` when the goal is performance profiling rather than judging the frame’s final appearance.

## UI playback scale override and raw auto-fix helper (2026-05-25)

### Verified locally

- I added a `Playback -> Playback Quality -> Scale Factor` submenu with `Auto`, `x1`, `x2`, and `x4` choices, persisted through `QSettings` key `Playback/ScaleFactorOverride`.
- I also added a RAW levels helper button, `Auto Fix`, next to the RAW White Level slider so a clip can auto-correct black and restore white from metadata in one click.
- The scale-factor UI override now wins over the legacy `MLVAPP_PLAYBACK_SCALE_FACTOR` env var when both are present, which makes the menu usable even if a developer shell still has the old env override set.
- The request-context profile smoke confirmed the new priority chain:
  - registry/UI scale override set to `1`
  - `MLVAPP_PLAYBACK_SCALE_FACTOR=4`
  - rendered request scale still came back as `1`
  - the stage log recorded `Loaded playback scale override setting = 1` followed by `Playback scale override = 1 (ui override; GUI dial is bypassed).`
- The same smoke also confirmed the visible-request shape stayed full-res when `x1` was selected:
  - `render_thread_playback_scale_factor_request = 1`
  - `render_thread_rendered_width = 1808`
  - `render_thread_rendered_height = 2268`
- I updated the user guide and the raw-level help text so the new UI controls are documented instead of living only in code.

### Cross-checked from prior analysis

- The earlier visible-smoke recipe remains the right default for judging a clip’s final look when `stretchFactorY=0.3333` is required.
- The raw black/white helper complements, rather than replaces, the existing manual slider controls and the black-level `Repair` action.

### Ranked next steps

1. High impact / low effort: use the new `Scale Factor` menu in the app instead of relying on env vars for interactive playback experiments.
2. High impact / low effort: use `Auto Fix` on clips that show green/pink shadow cast before spending time judging geometry or color.
3. Medium effort: if a clip still looks wrong after the auto-fix helper, vary only the receipt stretch factors and keep the scale override fixed so the visual diagnosis stays isolated.

## Playback presets and auto-exposure caution (2026-05-25)

### Verified locally

- The app now starts in a sane visual state without manual cleanup because the persisted UI scale override is authoritative over the old `MLVAPP_PLAYBACK_SCALE_FACTOR` shell override.
- The practical interactive presets are:
  - sharpest paused view: `Playback Quality = HQ`, `Scale Factor = x1`
  - smoothest playback: `Playback Quality = HQ`, `Scale Factor = x4`
  - safest general browsing: `Playback Quality = Auto`, `Scale Factor = Auto`, `scope=none`, `zebras=false`
- `RAW -> Auto Fix` is still the right explicit tool when a clip shows the classic green/pink raw-level cast.
- I did **not** add silent automatic exposure correction on clip load. The existing `Exposure Correction` slider is display-referred and subjective, so making it auto-drive every clip would risk clipping highlights or flattening the look just to satisfy a histogram target.

### Cross-checked from prior analysis

- The user-guide guidance already treats `Exposure Correction` as a manual grade control, not a clip-autonomy control.
- The raw black/white helpers are the safer place for true “clip looks wrong on open” automation because they restore metadata and fix sensor-domain mistakes rather than guessing taste.

### Ranked next steps

1. High impact / low effort: keep the new UI scale override and raw auto-fix helpers as the default clip-opening workflow.
2. High impact / low effort: treat any future auto-exposure feature as an opt-in helper, not a silent default.
3. Medium effort: if we later add auto-exposure, make it a “suggested starting point” based on histogram percentiles and clipping guardrails, not a hard rule that mutates every clip on open.

## Auto look assist design note (2026-05-25)

### Recommended approach

- If we automate clip appearance on load, it should be a clip-scoped `Look Assist` rather than a silent global grade.
- `Look Assist` should be applied automatically on open, with a per-clip toggle to disable it.
- The auto step should combine:
  - raw technical correction first (`RAW Black Level`, `RAW White Level`)
  - exposure placement second (`Exposure Correction`)
  - tone shaping third (`Contrast`, `Pivot`, `Shadows/Highlights` or `Dark/Light`)
  - color polish last (`Temperature/Tint`, `Vibrance`, maybe `Saturation`)
- `RAW Black Level` and `RAW White Level` are useful because they fix the sensor-domain problem that creates green/pink casts and broken highlights.
- `Exposure Correction` is useful because it places the midtones, but it should not be the only auto control.
- `Contrast`, `Pivot`, and shadows/highlights controls are what make the image feel “pretty” after exposure is placed.

### Scene-aware defaults

- Night:
  - modest exposure lift
  - protect highlights aggressively
  - lift shadows a little
  - keep saturation conservative
- Artificial lights:
  - small exposure lift
  - moderate contrast
  - keep highlight guardrails strict
  - avoid heavy vibrance
- Bright sun:
  - little or no exposure lift
  - preserve highlights first
  - modest contrast / pivot shaping
  - avoid over-lifting shadows
- Shade / overcast:
  - moderate exposure lift
  - gentle contrast
  - a little shadow lift
  - mild vibrance if the clip is flat

### Guardrails

- Never silently save the auto-look result back to the receipt unless the user explicitly asks.
- If the scene is already well exposed, the auto step should stay close to neutral rather than forcing a “pretty” look.
- If the clip is extreme or ambiguous, the helper should prefer a conservative starting point and let the user adjust manually.

### Cross-checked from prior analysis

- The current `RAW -> Auto Fix` button is already the safe technical fix for raw black/white mistakes.
- The new scale-factor UI override proved that user-facing controls can safely replace environment-only overrides without changing the underlying pipeline behavior.

### Ranked next steps

1. High impact / low effort: if we build auto-look later, make it clip-scoped and togglable per clip.
2. High impact / low effort: keep raw black/white auto-fix separate from creative exposure/tone shaping.
3. Medium effort: if a future auto-look is added, surface it as a visible “assist” with a reset button and a toggle state, not as a silent mutation.

## 2026-05-25 - look assist geometry guardrail

- The first look-assist pass went too far by also auto-selecting vertical stretch from `getMlvAspectRatio()`.
- That turned a pure appearance helper into a geometry mutator, which is the wrong layer for clip-specific aspect decisions.
- The corrected rule is: Look Assist may auto-fix raw levels and tone, but it must not silently change the frame shape or stretch preset.
- If geometry ever needs an assist mode, it should be a separate explicit `Auto Aspect` action with per-clip opt-in, not part of Look Assist.

## 2026-05-25 - Qt/MinGW build and GUI smoke blocker resolved

### Verified locally

- The local runnable Windows build must use the internally consistent Qt/MinGW pair:
  - `C:\Qt\6.10.2\mingw_64\bin\qmake.exe`
  - `C:\Qt\Tools\mingw1310_64\bin\mingw32-make.exe`
- Do not let LLVM/Clang-built objects remain in `platform/qt/build-release`. The stale link failure showed `std::__1` symbols mixed into a MinGW/libstdc++ link. If `mingw32-make clean` cannot remove objects because `rm` is unavailable, delete the generated build outputs directly before rebuilding.
- Deploy the release folder with `windeployqt --release --compiler-runtime`, then ensure the MinGW/OpenMP runtime DLLs are present beside `MLVApp.exe`, especially `libgomp-1.dll`.
- The visible profile shutdown crash was a real app lifetime bug, not just a deployment issue. `MainWindow::~MainWindow()` now stops playback prep/render/audio and frees the active `mlvObject_t` plus `processingObject_t`, allowing the C-side cache/prefetch threads to join before process teardown.
- The profile paint probe now removes its event filter and disarms per-frame stack pointers after paint or timeout, preventing a later paint event from touching a dead `QEventLoop`.
- Look Assist is still auto-on for normal GUI clip load, but `--profile-playback --receipt ...` disables Look Assist for old receipts that do not explicitly contain `lookAssistEnabled`. This keeps profiling deterministic and prevents old smoke fixtures from silently changing processing mode.
- Look Assist must not restore or mutate stretch/aspect. It may adjust raw levels and tone controls, but geometry belongs to the receipt or to an explicit future `Auto Aspect` action.

### Validation

- Full Qt app rebuild succeeded with Qt 6.10.2 and MinGW 13.1.
- GUI profile trigger matrix passed for hidden/visible and frameReady/paint-wait modes: all four runs exited `0`, wrote JSON, and measured 2/2 frames.
- Console suite with `MLVAPP_PROFILE_EXE=platform/qt/build-release/release/MLVApp.exe` passed: 67 tests, 865 assertions, 1 expected batch-export skip, 0 failures.

### Guardrails

- Future GUI smoke recipes should not judge appearance with zebras/scopes enabled unless the overlay stack itself is under test.
- Future profiling receipts that intentionally want Look Assist should save an explicit `<lookAssistEnabled>1</lookAssistEnabled>` element.
- If aspect ratio regresses again, inspect Look Assist restore/copy paths first and confirm no stretch combo or `stretchFactorX/Y` write is coupled to the appearance helper.

## 2026-05-26 - Look Assist load-path parity and quality controls

### Verified locally

- Initial clip load and manual Look Assist toggle now share the same effective receipt state. The prior asymmetry was that `setSliders()` applied derived Look Assist UI values, but did not write those derived values back to the active receipt until a later full `setReceipt()` path. That made some clips look better only after toggling Look Assist off/on.
- Look Assist now syncs its derived values back to the receipt immediately after apply or restore:
  - raw technical correction: `rawFixesEnabled`, `rawBlack`, `rawWhite`
  - tone: `Exposure Correction`, `Contrast`, `Pivot`, `Shadows`, `Highlights`, `Vibrance`
  - color polish: `Temperature`, `Tint`
- Look Assist baseline persistence now includes `Temperature` and `Tint`, so toggling the assist off can return to the original clip state instead of leaving color-balance changes behind.
- `--profile-playback` metadata now records Look Assist diagnostics and chosen values, including scene classification, luma percentiles, RGB medians, balance medians, exposure/contrast/pivot/shadow/highlight/vibrance preset values, and temperature/tint deltas. This is the runtime breadcrumb trail to use when a clip still looks wrong.
- The M16-1446 dark/green report exposed that the night-scene cap was too timid for extremely dark frames. For very dark night frames (`p99 < 55`), Look Assist can now lift exposure correction up to `+380` instead of topping out at the old `+140` range.
- A headless profile smoke on `C:\temp\MLV\M16-1446.MLV` after this change reported:
  - `look_assist_scene = night`
  - `look_assist_median = 2`
  - `look_assist_p99 = 27`
  - `look_assist_exposure = 380`
  - `look_assist_shadows = 38`
  - `look_assist_highlights = -18`
  - `look_assist_temperature = 6000`
  - `look_assist_tint = 0`
  - `look_assist_raw_black = 20470`
  - `look_assist_raw_white = 16200`
  - artifact: `.claude-state/profiling/look-assist-userclips/20260526-final-smoke-rerun/M16-1446.MLV.json`
- The playback quality toolbar dropdown now exposes the existing scale-factor actions (`Auto`, `x1`, `x2`, `x4`) directly under `Scale Factor`, so users do not need to rely on environment variables or hunt through only the top-level menu.
- The receipt loader test now covers the new Look Assist baseline temperature/tint fields. The profile-backed console test suite passed after the loader was updated: `67` tests, `867` assertions, `1` expected skip, `0` failures.
- The release Qt app rebuilt successfully after the code changes using the known-good Qt 6.10.2 / MinGW 13.1 kit.

### Cross-checked from prior analysis

- Fast Preview / max-cadence playback can still show Dual ISO color cast, especially magenta/pink, because that mode prioritizes speed and lower-fidelity preview cadence. For color judgment on Dual ISO clips, prefer `High Quality (cast-closed)` rather than Fast Preview.
- Bilinear remains the practical playback debayer recommendation for smooth GUI playback. AMaZE/RCD are better for paused quality or export judgment, but they are not the default recommendation for choppy interactive playback.
- `Use Fast Processing for Playback` is a playback-only speed/quality tradeoff. It should be treated like a preview acceleration switch, not an export-quality guarantee.
- Look Assist remains an appearance helper only. It must not change stretch/aspect/geometry. If a future clip needs automatic geometry help, that belongs in a separate explicit `Auto Aspect` control.
- Scale-factor behavior is quality/performance sensitive:
  - `x1` is sharpest and slowest.
  - `x2` is the hoped-for middle ground when the clip/policy allows it.
  - `x4` is the safest smooth playback and artifact-hiding option for heavier Dual ISO cases.

### Needs runtime profiling

- GUI smoke still needs human visual confirmation on the user clip set after this commit, especially:
  - M16-1446.MLV should no longer open as too dark/green.
  - M15-1321.MLV and M29-1756.MLV should not require toggling Look Assist off/on to get the intended auto look.
  - Fast Preview should still be documented as a lower-fidelity preview mode if it remains pink during playback.
- If M16-1446.MLV still looks green after the stronger night lift, use the new profile metadata to decide whether the next fix should be stronger tint correction, a raw-level correction guard, or a scene-specific white-balance rule.

### Ranked next steps

1. High impact / low effort: smoke the GUI on M15-1321.MLV, M15-1355.MLV, M16-1446.MLV, and M29-1756.MLV with Look Assist enabled from initial load, then compare only against manual toggle if the first frame looks wrong.
2. High impact / low effort: use `High Quality (cast-closed)` plus toolbar `Scale Factor` for visual judgment, and reserve `Fast Preview` for cadence checks.
3. Medium impact / medium effort: if some clips still need a prettier first look, tune the scene-aware Look Assist preset using the new diagnostics rather than adding unbounded histogram exposure guesses.

## 2026-05-27 - Floor-lifted night color-balance guard

### Verified locally

- `M16-1243.MLV` and `M17-1152.MLV` exposed a second failure mode after the midtone/highlight cap landed: the raw Look Assist thumbnail was floor-lifted to nearly neutral values around `R=G=B=32`, so raw-thumbnail balance could not see the visible green/yellow cast.
- The fix keeps raw-thumbnail stats authoritative for scene classification, exposure, and highlight caps, but uses a processed thumbnail for color balance only when the clip is classified as a floor-lifted night thumbnail.
- The profile metadata now records `look_assist_balance_source`, `look_assist_balance_green_axis`, and `look_assist_balance_blue_amber_axis` so future smoke failures can distinguish raw exposure placement from processed color-balance decisions.
- Final local smokes after the patch:
  - `M16-1243.MLV`: exposure `+0.96 EV`, projected `p95=71.98`, projected `p99=75.87`, `temperature=6000`, `tint=4`, balance source `processed`, toggle stable, unsettled count `0`.
  - `M17-1152.MLV`: exposure `+1.08 EV`, projected `p95=71.88`, projected `p99=76.11`, `temperature=5952`, `tint=4`, balance source `processed`, toggle stable, unsettled count `0`.
  - `M15-1321.MLV`: exposure `+0.96 EV`, projected `p95=71.98`, projected `p99=73.92`, preserving the midtone/highlight cap behavior.
- Pipeline capture on the displayed `S5_processed8` buffers shows the green-vs-blue cast moved toward neutral:
  - `M16-1243.MLV`: midtone `G/B` changed from `1.243` to `1.061`.
  - `M17-1152.MLV`: midtone `G/B` changed from `2.143` to `1.079`.

### Cross-checked from prior analysis

- This follows the earlier rule that Look Assist may fix raw/tone/color appearance but must not mutate geometry.
- This does not revert to the previous `+380` floor-lift exposure behavior; highlight placement remains capped near the midtone target.

### Needs runtime profiling

- Human GUI smoke is still the final judge for the exact tint amount on `M16-1243.MLV` and `M17-1152.MLV`, because dark Dual ISO scene content can make channel medians overstate red/magenta after green suppression.

## 2026-05-28 - Restricted-lossless raw-white split, exposure recovery, and workflow guardrails

### Verified locally

- The exposure/green regression on restricted-lossless Dual ISO clips was caused by writing the post-expansion DNG white level back into the editable RAWI/UI white level. Their headers report original RAW white `6000`, while HQ Dual ISO expands the output range to about `15812`. `llrawproc` uses `RAWI.white_level < 15000` as the restricted-lossless expansion signal, so setting RAW White to `15812` disabled that expansion and made playback too dark with green/magenta artifacts.
- `MainWindow::autoCorrectRawWhiteLevel()` now keeps the editable RAW White at the original restricted-lossless header value (`6000`) and `restrictedLosslessDualIsoOutputWhiteLevel()` reports the post-expansion output white separately for diagnostics and tests. Final local HQ profile smokes:
  - `M16-1347.MLV`: RAW White `6000`, restricted output white `15812`, exposure `+1.33 EV`, projected `p95=108.10`, projected `p99=113.13`, temperature `6770 K`, tint `18`, Chroma Smooth auto-applied to `2`, warning `none`.
  - `M16-1327.MLV`: RAW White `6000`, restricted output white `15812`, exposure `+1.67 EV`, projected `p95=108.19`, projected `p99=114.56`, tint `22`, Chroma Smooth auto-applied to `2`, post green artifact ratio `0.0745`. A residual `global-green-cast` warning remains, but the neon/high-white regression is reduced and bounded by the local regression test.
  - `M15-1355.MLV`: RAW White `6000`, restricted output white `15812`, exposure `+1.71 EV`, projected `p95=107.96`, projected `p99=121.05`, Chroma Smooth auto-applied to `2`. A residual localized artifact warning remains for this clip.
- `M17-1152.MLV` now uses processed-neutral-patch balance in the floor-lifted path: exposure `+1.67 EV`, projected `p95=108.19`, projected `p99=114.56`, temperature `5500 K`, tint `12`, warning `none`. This is the warmer-clip balance path to check first if it regresses again.
- Scale-factor telemetry now has regression coverage for the x4-to-x1 UI toggle and the explicit x2 request. The request settles in render telemetry, but HQ mean23 can still remain cadence-bound by full-recon work; a large FPS win requires the pre-recon fast path to be enabled or promoted into the explicit UI tradeoff.
- Closeout workflow prevention is covered by the broker policy and tests already in this repo: `closeout.config.json` allows clean-at-start existing `.claude/analysis/*.md` notes to be auto-claimed despite the sensitive root, while new ad-hoc notes under `.claude/` still block. The tooling-baseline tests are `test_existing_analysis_note_clean_at_start_is_auto_claimed_despite_sensitive_root` and `test_new_analysis_note_under_sensitive_root_still_blocks_as_unowned`.

### Cross-checked from prior analysis

- This keeps Look Assist in the appearance layer: raw levels, tone, temperature, and tint only. Geometry/stretch remains untouched.
- The scale result is consistent with the earlier quality/speed split: x4 only becomes a large FPS win when the pre-recon Phase4B path is allowed. HQ mean23 currently preserves quality by defaulting to full-recon fallback on heavier Dual ISO clips.
- Existing tracked durable analysis notes should be updated in-place. New scratch/profiling artifacts stay under `.claude-state/`.

### Needs runtime profiling

- Human GUI smoke should verify that `M16-1347.MLV` opens brighter, with RAW White still showing `6000` rather than the post-expansion `15812`.
- Human GUI smoke should judge the remaining residual green warnings on `M15-1355.MLV` and `M16-1327.MLV`; the automated guard now catches the high-white/dark regression, not every localized artifact.
- If users expect scale factor to produce a much larger FPS change in HQ mean23, decide whether explicit x4 should opt into the existing fast pre-recon path or whether the toolbar label should make the quality/speed limitation clearer.

### Ranked next steps

1. High impact / low effort: consider making explicit UI `Scale x4` opt into the existing fast pre-recon path, or expose the quality/speed tradeoff in the toolbar label, because users reasonably expect x4 to buy FPS.
2. High impact / low effort: keep the local optional M16 profile tests asserting RAW White stays `6000`, restricted output white stays `>15000`, Chroma Smooth is auto-applied for the affected HQ clips, and exposure places projected `p95` back near the midtone target.
3. Medium impact / medium effort: if `M15-1355.MLV` or `M16-1327.MLV` still look too green in GUI smoke, tune a second-pass localized artifact cleanup rather than changing the RAW White split.

## 2026-05-28 - M16-1446 flat-noise-floor Look Assist cap

### Verified locally

- `M16-1446.MLV` was not a recurrence of the Dual ISO pattern mapping bug. A post-fix profile from frame 0 through frame 371 reported frame 369 with `presented_dual_iso_core_pattern = -4` and `presented_dual_iso_ui_pattern = 3`, which is the expected UI/core pair for `HIGH-low-low-HIGH`.
- The clip-specific regression came from Look Assist analyzing a non-representative load frame. The initial analysis thumbnail was a flat lifted noise floor: `p05 = 32`, `median = 32`, `p95 = 32`, and `p99 = 32`. The previous broad night rescue treated that as a very dark scene needing the full `+1.75 EV` lift, then that static lift was still applied when scrubbing to the brighter frame 369 screenshot.
- Chroma Smooth was not the primary fix. A frame-369 A/B with Chroma Smooth off versus `2x2` only nudged the localized artifact sample count; it did not address the over-lifted look caused by the flat load frame.
- `MainWindow.cpp` now detects the extreme flat-noise-floor night thumbnail shape and caps that case at `+1.40 EV`. The gate requires night classification plus `median`, `p05`, `p95`, and `p99` all at or below `34`, with `p99 - p05 <= 2`.
- Post-fix local profiles with `MLVAPP_PLAYBACK_PREFER_HQ_MEAN23=1`:
  - `M16-1446.MLV`: `p99 = 32`, exposure `+1.40 EV`, RAW White `16200`, Chroma Smooth `0`, warning `none`.
  - `M16-1243.MLV`: `p99 = 39`, exposure `+1.58 EV`, RAW White `6000`, Chroma Smooth auto-applied to `2`, warning `none`.
  - `M16-1327.MLV`: `p99 = 36`, exposure `+1.67 EV`, RAW White `6000`, Chroma Smooth auto-applied to `2`, warning `global-green-cast`.
  - `M16-1347.MLV`: `p99 = 45`, exposure `+1.33 EV`, RAW White `6000`, Chroma Smooth auto-applied to `2`, warning `none`.
  - `M17-1152.MLV`: `p99 = 36`, exposure `+1.67 EV`, RAW White `16200`, Chroma Smooth `0`, warning `none`.
  - `M29-1756.MLV`: `p99 = 37`, exposure `+1.63 EV`, RAW White `6000`, Chroma Smooth auto-applied to `2`, warning `none`.
- The longer `M16-1446.MLV` frame-369 profile after the cap reported exposure `+1.40 EV`, frame 369 green artifact ratio `0.0031`, mean green axis `29.62`, and visible green axis `3.84`.
- The local release build only deploys the `windows` Qt platform plugin. GUI/profile smokes should use `QT_QPA_PLATFORM=windows`; forcing `offscreen` against this release tree can show the Qt platform plugin popup even though the normal GUI launch path is valid.
- Workflow hardening for that plugin failure now lives in `tools\profiling\run-release-playback-profile.ps1`. The wrapper pins `QT_QPA_PLATFORM=windows`, points `QT_QPA_PLATFORM_PLUGIN_PATH` at the release `platforms\qwindows.dll` directory, and offers `-DryRun` so agents can verify the launch environment before running a clip.
- A wrapper smoke on `tests\fixtures\clips\tiny_dual_iso.mlv` measured `1` frame and profile metadata reported `qt_qpa_platform_environment = windows`; the trace ended with `profile-complete`.
- Regression coverage: `ClipGolden.LocalM16LookAssistCapsOnlyFlatNoiseFloorNightWhenAvailable` asserts the flat M16-1446 case is capped while M16-1243 remains above `+1.50 EV`. The related local M16 Look Assist tests passed: `3` tests, `58` assertions, `0` failures. Repo-hygiene coverage also asserts the release playback profile wrapper remains pinned to the Windows platform plugin.

### Cross-checked from prior analysis

- The restricted-lossless RAW White split still stands: affected restricted clips keep editable RAW White at `6000` while diagnostics expose the expanded output white separately. This M16-1446 change does not touch that path.
- `M17-1152.MLV` remains a white-balance control clip for this investigation, not a green-pixel-pattern regression clip. Auto Look Assist is deliberately more conservative than the manual picker because it only applies a detected neutral patch when the patch passes stability and green-clamp guards.
- The fix narrows the previous strong night rescue rather than reverting it globally. Clips with slightly wider load-frame percentiles (`p99 > 34`) keep their prior rescue exposure.

### Needs runtime profiling

- Human GUI smoke should verify the M16-1446 frame-369 screenshot area after the cap, because the automated guard measures the representative exposure and artifact telemetry but not subjective scene intent.
- If M16-1446 still feels too green after the exposure cap, the next candidate should be a localized artifact cleanup or per-frame representative Look Assist sampling, not another global night exposure change.

### Ranked next steps

1. High impact / low effort: keep M16-1446 and M16-1243 paired in the optional local golden test so the flat-noise cap cannot silently spread to the clips that benefited from the stronger night rescue.
2. Medium impact / medium effort: if more clips show non-representative first-frame analysis, add a small representative-frame sampler for Look Assist instead of tuning one more first-frame percentile threshold.
3. Medium impact / low effort: keep profile/smoke launch recipes on `tools\profiling\run-release-playback-profile.ps1` unless the release folder is explicitly deployed with an offscreen platform plugin and a separate wrapper records that fact.

## 2026-05-28 - x1 HQ playback scale-factor investigation

### Verified locally

- The user-facing release executable profiled for this pass was `platform/qt/build-release/release/MLVApp.exe`, timestamp `2026-05-28 13:18:30`, size `8223744`, SHA256 `6C37516650A8B9C24DBDF6EB75B6DADED4414CCC0DF6A95CB3B95627A97C8525`.
- Scratch artifacts live under `.claude-state/profiling/20260528-x1-hq-playback-investigation/`.
- On `tests/fixtures/clips/large_dual_iso.mlv` with `tests/fixtures/receipts/large_dual_iso_hq.marxml`, `MLVAPP_PLAYBACK_PREFER_HQ_MEAN23=1`, and warm frame `sample_index > 0`, x4 reduces presentation/copy work much more than it reduces the HQ engine work:
  - `large_hq_x1_auto_threads.json`: median `latency_ms 180.373`, `engine_latency_ms 143.683`, `presentation_overhead_ms 32.691`, `draw_frame_ready_total_ms 31.000`, `llrawproc_ms 128.000`, `processed8_total_ms 143.000`.
  - `large_hq_x4_auto_threads.json`: median `latency_ms 144.485`, `engine_latency_ms 141.251`, `presentation_overhead_ms 2.431`, `draw_frame_ready_total_ms 2.000`, `llrawproc_ms 136.000`, `processed8_total_ms 141.000`.
  - x1 copied `12301632` owned RGB8 bytes per frame into playback prep, while x4 copied `768852`; output pixels dropped from `4100544` to `256284`.
- Paint-aware runs show the same direction but with normal GUI variance:
  - x1 wait-for-paint median `cadence_ms 219.664`, `paint_latency_ms 208.002`, `presentation_overhead_ms 58.908`, `draw_frame_ready_total_ms 56.000`.
  - x4 wait-for-paint median `cadence_ms 199.290`, `paint_latency_ms 190.248`, `presentation_overhead_ms 20.483`, `draw_frame_ready_total_ms 19.000`.
- The optional `MLVAPP_ENABLE_DUAL_ISO_FAST_X4_IN_HQ=1` run still reported `render_thread_phase4b_path_label = none-or-full-recon-fallback` on every warm frame, so it did not prove the fast pre-recon x4 path. The fixture receipt has `<focusPixels>1</focusPixels>`, and `mlv_phase4bv2_receipt_compatible()` rejects focus pixels, bad pixels, vertical stripes, and pattern noise before downsample-before-llrawproc can run.
- The FPS counter in `MainWindow::timerFrameEvent()` reports GUI cadence from recent presentation timing, so it will not show a large x4 jump when the dominant `llrawproc` / processed8 work remains full-recon-bound.

### Cross-checked from prior analysis

- `platform/qt/PlaybackQualityPolicy.h` currently returns scale `4` for Fast, High Quality, Auto, Phase3Fast, and Phase3HQ unless overridden by `MLVAPP_PLAYBACK_SCALE_FACTOR`; Auto only drops to `Decision{ 1, false }` after it misses target, which means Fast preview rather than x1 HQ.
- `src/mlv/video_mlv.c` only allows scale factors `1`, `2`, and `4`, and current HQ Dual ISO quality guards default x4 to the full-recon fallback unless a narrower fast path is explicitly allowed and the receipt is compatible.
- `platform/qt/MainWindow.cpp` still copies the full ready-frame RGB8 payload into `PlaybackPrepTask::ownedSourceImage` before async prep. At x1 HQ this is a `12.3 MB` copy per frame even though the render slot is already pinned until the frame is released.
- The GPU16 presentation path still has full-frame conversion/copy candidates (`gpu16FallbackRgb8`, viewport upload staging), so GL presentation alone should not be assumed to fix x1 HQ cadence without removing those copies.

### Needs runtime profiling

- The highest-confidence x1 HQ improvement target is the full-recon Dual ISO/processed8 engine path, not the scale factor. In this fixture the warm median `llrawproc_ms` alone is about `128-147 ms`, so true x1 HQ needs a faster HQ path or a carefully defined playback-quality tradeoff.
- Next best x1-specific UI target: avoid or shrink full-frame playback-prep copies by borrowing pinned render-slot buffers across the async worker lifetime, with stale-serial/release safety. Expected benefit is presentation overhead, not the main HQ reconstruction time.
- If explicit x4 is meant to buy a bigger FPS difference, design the tradeoff openly: either promote a focus-pixel-compatible fast pre-recon/downsample path, or label x4 HQ as primarily reducing presentation load while preserving full-recon quality.

### Ranked next steps

1. High impact / medium effort: profile and optimize the full-recon HQ Dual ISO/processed8 hot path at x1, starting from `llrawproc_ms` and `processed8_total_ms`, because scale cannot remove that cost.
2. Medium impact / medium effort: prototype a borrowed-buffer async playback-prep handoff so x1 does not copy `12.3 MB` of RGB8 every frame before presentation.
3. Medium impact / medium effort: evaluate a receipt-compatible fast pre-recon path for x4/HQ as an explicit speed/quality mode, especially with focus-pixel receipts that currently force fallback.
4. Low-medium impact / low effort: clarify the toolbar/menu wording if users expect x4 to change HQ FPS dramatically; current behavior mainly reduces presentation work when full-recon remains active.

## 2026-05-28 - x1 HQ CPU iteration 1

### Verified locally

- The first CPU-focused x1 HQ implementation keeps the user-visible scale factor at `x1` and targets two costs that still matter when scale cannot remove full-recon Dual ISO work:
  - playback-prep no longer deep-copies the ready RGB8 frame into `PlaybackPrepTask::ownedSourceImage`; `platform/qt/MainWindow.cpp:16475` borrows the pinned render-slot buffer and records borrowed-byte telemetry.
  - full HQ Dual ISO no longer rebuilds post-reconstruction EV LUTs unless post-recon focus/bad-pixel interpolation will actually use them; `src/mlv/llrawproc/llrawproc.c:1241` tracks `post_recon_luts_active` and restores the original LUT only when it was switched.
- The render-slot lifetime audit found the borrowed RGB8 handoff safe for the current async prep path: `RenderFrameThread::acquireLatestReadyFrame()` pins a slot as presenting, free-slot selection skips presenting slots, and existing release-by-serial paths release the slot after stale/drop/present decisions.
- Regression coverage now asserts the profile telemetry for borrowed versus owned prep bytes in `tests/console/test_clip_golden.cpp:337`; warm x1 HQ profiles report owned RGB8 bytes at `0` and borrowed RGB8 bytes at `12301632`.
- Focused correctness checks passed after the lazy-LUT change:
  - `ClipGolden.TinyDualIsoHeadlessPlaybackProfileProducesJson`
  - `ClipGolden.TinyDualIsoBatchExportMatchesGolden`
  - `DualIsoPipeline.TinyDualIsoFullFramesMatchGolden`
  - `DualIsoPipeline.StablePixelMaps*`
  - `DualIsoPipeline.ForcedReEntryFullDualIsoStabilizesFromFirstRender`
  - `DualIsoPipeline.DualIsoPlaybackForcesMean23WhenOverrideActive`
- `tests/fixtures/clips/large_dual_iso.mlv` with `tests/fixtures/receipts/large_dual_iso_hq.marxml`, x1 HQ, auto worker threads, warm frames only:
  - Previous x1 baseline: `latency_ms 176.415`, `engine_latency_ms 143.318`, `presentation_overhead_ms 31.892`, `draw_frame_ready_total_ms 31.000`, `llrawproc_ms 127.000`, `llrawproc_other_ms 48.000`, owned RGB8 bytes `12301632`.
  - Borrowed-prep run: `latency_ms 136.971`, `engine_latency_ms 122.876`, `presentation_overhead_ms 14.178`, `draw_frame_ready_total_ms 14.000`, `llrawproc_ms 108.000`, `llrawproc_other_ms 41.000`, owned RGB8 bytes `0`, borrowed RGB8 bytes `12301632`.
  - Lazy-LUT run 1: `latency_ms 89.985`, `engine_latency_ms 76.003`, `presentation_overhead_ms 14.239`, `draw_frame_ready_total_ms 14.000`, `llrawproc_ms 65.000`, `llrawproc_other_ms 0.000`.
  - Lazy-LUT run 2: `latency_ms 126.109`, `engine_latency_ms 110.827`, `presentation_overhead_ms 14.763`, `draw_frame_ready_total_ms 15.000`, `llrawproc_ms 92.000`, `llrawproc_other_ms 0.000`.
- The release executable rebuilt successfully after the GUI/playback changes: `platform/qt/build-release/release/MLVApp.exe`, timestamp `2026-05-28 17:18:57`, size `8224768`, SHA256 `25835782478349E8ED1E8A8F9143B78A8BE948AFAF8820F192EB61887B76CD6F`.

### Cross-checked from prior analysis

- The scale-factor investigation remains valid: x4 mostly reduced presentation/copy work while the full HQ engine path stayed dominant. These iteration-1 changes improve x1 without relying on the x4 pre-recon fast path.
- The hidden `llrawproc_other_ms` slice matched the suspected EV LUT rebuild work around the full HQ Dual ISO post-recon path. The repeated lazy-LUT profile kept that slice at `0.000 ms`, which is stronger evidence than total FPS on this noisy CPU-bound VM.
- This change is not playback-only inside `llrawproc`; export/full-render correctness is covered by the tiny Dual ISO batch golden, but wider Dual ISO exports should remain part of future validation if the LUT gating is broadened.

### Needs runtime profiling

- The VM timing still varies materially between runs. Treat the stable findings as owned RGB8 copy removal and `llrawproc_other_ms` elimination, not as a precise FPS promise.
- If more headroom is needed at x1 HQ, the next measured target is the remaining `llrawproc_dual_iso_ms` / `processed8_total_ms` slice; after lazy LUTs, that is the dominant CPU work again.

### Ranked next steps

1. High impact / medium effort: profile the remaining full HQ Dual ISO reconstruction slice after lazy LUTs, especially `diso_get_full20bit()` and direct processed8 conversion.
2. Medium impact / medium effort: add finer `llrawproc` telemetry for LUT rebuild time and Dual ISO subphases so future improvements are attributable without inference.
3. Medium impact / low effort: run a human GUI smoke on the user VM with the rebuilt release executable and compare FPS/cadence at x1 HQ before chasing further presentation-side copies.

## 2026-05-28 - additional no-regression speed audit

### Verified locally

- A full audit is useful only if it stays playback-hot-path and profile-led. A broad repo sweep will find ideas, but the no-regression path is: measure the remaining slice, add missing telemetry, then implement one byte-identical or policy-only change at a time.
- Fresh x1 HQ profile after the prior copy/LUT fixes on `tests/fixtures/clips/large_dual_iso.mlv` with `tests/fixtures/receipts/large_dual_iso_hq.marxml`, `MLVAPP_PLAYBACK_PREFER_HQ_MEAN23=1`, and auto worker threads:
  - `latency_ms 142.720`, `engine_latency_ms 125.920`, `cadence_ms 143.930`, `presentation_overhead_ms 14.300`
  - `render_thread_work_ms 126.000`, `processed8_total_ms 126.000`, `llrawproc_ms 102.000`, `llrawproc_dual_iso_ms 102.000`
  - `processing_ms 8.000`, `raw_uint16_ms 1.000`, `raw_uint16_prefetch_hit 15/15`
- Worker thread count is a real low-risk lever on this CPU-bound VM. With the same x1 HQ fixture:
  - `threads=2`: median `latency_ms 125.084`, `engine_latency_ms 109.222`, `llrawproc_ms 87.000`
  - `threads=4`: median `latency_ms 116.862`, `engine_latency_ms 101.998`, `llrawproc_ms 85.000`
  - `threads=8`/auto: median `latency_ms ~141-143`, `engine_latency_ms ~126-127`, `llrawproc_ms 102.000`
  - This suggests auto chose too many workers for the VM's contention profile; a production policy should be adaptive or user-controlled, not a hard global cap.
- Raw uint16 prefetch should stay enabled for this fixture. Disabling it at `threads=4` changed raw hits from `15/15` to `0/15`, raised median raw decode from `1 ms` to `17 ms`, and slightly worsened median latency despite lower-looking `llrawproc_ms` in that single run.
- Experimental processed8 prefetch produced only `3/15` warm hits. Hit frames were very fast, but the median barely moved, so it is not ready as a default-on feature without better scheduling/state snapshotting.
- `MLVAPP_ENABLE_AVX2_INTRIN_DIRECT8=1` is worth controlled benchmarking, but not a conclusion yet. One run improved total timing, but it also lowered `llrawproc_ms`, which the direct8 flag should not directly affect. The processing/color slice itself moved only about `1 ms`.

### Cross-checked from prior analysis

- Remaining x1 HQ engine work is dominated by full HQ Dual ISO reconstruction in `diso_get_full20bit()` (`src/mlv/llrawproc/dualiso.c:3468`) called from `applyLLRawProcObjectWorker()` (`src/mlv/llrawproc/llrawproc.c:1210`). Any math change there needs byte-identity or very explicit quality tradeoff coverage.
- The current profile lacks Dual ISO substage timing inside `diso_get_full20bit()`. The high-confidence next instrumentation points are around noise/pattern identification, `convert_to_20bit`, exposure matching, interpolation, `mix_images`, `final_blend`, and `convert_20_to_16bit`.
- The remaining Qt-side presentation overhead is about `14-15 ms` in headless profiles. A read-only audit points at `drawFrameReady()` calling `timerFrameEvent()` before current-frame presentation (`platform/qt/MainWindow.cpp:16341`) as a likely unbucketed chunk, but this needs telemetry before behavior changes.
- Stage telemetry itself is inserted into a `QJsonObject` every render (`platform/qt/RenderFrameThread.cpp:1980` through `platform/qt/RenderFrameThread.cpp:2142`). Gating rich telemetry outside profile/debug mode could help normal GUI playback, but profile golden tests must continue to see every field.

### Needs runtime profiling

- Repeat the thread-count sweep on the user's actual VM and clips. The fixture strongly favors `4` over auto/`8`, but the best cap may differ with CPU topology, clip resolution, and whether background prefetch/cache workers are active.
- Add Dual ISO substage telemetry before optimizing `dualiso.c`; otherwise improvements inside the largest slice remain hard to attribute.
- Add `draw_frame_ready_advance_ms` or equivalent around the current `timerFrameEvent()` call before splitting/slimming the presentation path.
- Benchmark direct8 AVX2 intrinsics with parity tests and repeated A/B profiles. Do not default it on from a single noisy VM run.

### Ranked next steps

1. High impact / low risk: expose or auto-tune a playback worker-thread cap for CPU-bound VMs, starting from a profile-proven `4` worker default/candidate rather than auto `8`.
2. High diagnostic value / low risk: add Dual ISO substage telemetry inside `diso_get_full20bit()` so future engine changes are measured and byte-identity guarded.
3. Medium impact / low risk: add Qt presentation telemetry around the `timerFrameEvent()` advance work, disabled interaction-trace string construction, and timecode/status updates.
4. Medium impact / medium risk: improve processed8 prefetch scheduling/state snapshots only if profiles show consistent hit rates above the current `3/15`.
5. Low-medium impact / low-medium risk: controlled direct8 AVX2-intrinsics default evaluation after parity and repeated A/B runs.

## 2026-05-29 - visible GUI playback CPU optimizations

### Verified locally

- Implemented the low-regression CPU-side playback optimizations identified by the audit:
  - GUI render/playback calls now use `mlvappEffectivePlaybackWorkerThreadCount()` from `src/batch/WorkerThreadCount.h`, preserving `MLVAPP_FORCE_THREADS` and `MLVAPP_FORCE_SINGLETHREAD` as exact overrides while capping auto playback workers by default.
  - The default cap was tuned from a sequential visible sweep on this VM; cap `6` beat cap `4`, cap `8`, and uncapped auto for the large x1 HQ fixture.
  - Hot trace-only playback logs in `timerFrameEvent()`, `drawFrame()`, and `drawFrameReady()` now guard `QString::arg()` construction behind `interactiveTraceEnabled()`.
  - `updatePlaybackQualityIndicator()` no longer re-applies identical text/style/visibility every presented frame.
  - Profile telemetry now records `render_thread_worker_threads`, `render_thread_worker_thread_cap_active`, and `draw_frame_ready_advance_ms`.
- Benchmark artifacts live under `.claude-state/profiling/20260529-visible-gui-playback/`.
- Non-headless user-facing release profiling used `tools/profiling/run-release-playback-profile.ps1 -WaitForPaint` against `platform/qt/build-release/release/MLVApp.exe`; the wrapper pins the normal Windows Qt platform plugin, not offscreen.
- Baseline release before this work block, `large_dual_iso.mlv` + `large_dual_iso_hq.marxml`, `MLVAPP_PLAYBACK_PREFER_HQ_MEAN23=1`, auto threads:
  - `baseline_auto_waitpaint.json`: average `latency_ms 231.152`, median `latency_ms 195.893`, average `cadence_ms 231.361`, median paint latency `198.131`, median `llrawproc_ms 138`, `worker_threads_effective 8`.
  - `baseline_auto_waitpaint_playaction.json`: average `latency_ms 228.191`, average `cadence_ms 247.370`, Play-action smoke elapsed `674 ms`, `worker_threads_effective 8`.
- Final release after the patch, same visible wait-for-paint setup, default auto playback cap:
  - `final_default_waitpaint.json`: average `latency_ms 136.329`, median `latency_ms 118.918`, average `cadence_ms 135.029`, median paint latency `121.032`, median `llrawproc_ms 76`, `worker_threads_effective 6`, cap active.
  - `final_default_waitpaint_playaction.json`: average `latency_ms 214.822`, average `cadence_ms 237.346`, Play-action smoke elapsed `454 ms`, `worker_threads_effective 6`, cap active.
- Sequential cap sweep on the final code before changing the default confirmed the VM-specific ordering:
  - cap `4`: average `latency_ms 191.138`, average `cadence_ms 189.900`
  - cap `6`: average `latency_ms 176.207`, average `cadence_ms 174.495`
  - cap `8`: average `latency_ms 226.214`, average `cadence_ms 201.610`
  - disabled cap: average `latency_ms 213.233`, average `cadence_ms 205.223`
- Final user-facing release executable after the cap-6 rebuild: `platform/qt/build-release/release/MLVApp.exe`, timestamp `2026-05-28 19:42:35`, size `8230400`, SHA256 `CEC020199674A0C0DCCA27AFF67C12D12C714094E8306A716FC50274F0F33EA4`.
- Regression coverage passed:
  - `WorkerThreadCount.*`
  - `ClipGolden.TinyDualIsoHeadlessPlaybackProfileProducesJson` against the rebuilt release exe
  - full console golden suite against the rebuilt release exe: `81` tests, `1178` assertions, `1` expected skip for missing `MLVAPP_BATCH_EXE`, `0` failures.

### Cross-checked from prior analysis

- The worker-count cap is a policy/control change, not a Dual ISO math change. Explicit `--threads N` profiles and `MLVAPP_FORCE_THREADS=N` still bypass the cap.
- The final default differs from the earlier read-only audit's `4`-worker candidate because the required non-headless GUI sweep showed `6` workers winning on the user-facing release path.
- The remaining biggest CPU slice is still full HQ Dual ISO reconstruction; the cap reduces contention around that work but does not replace the need for future Dual ISO substage telemetry.

### Needs runtime profiling

- Repeat the visible sweep on the user's real clips if they differ materially from `large_dual_iso.mlv`; the cap is overrideable with `MLVAPP_PLAYBACK_MAX_THREADS=<n>` and disableable with `MLVAPP_DISABLE_PLAYBACK_THREAD_CAP=1`.
- Add Dual ISO substage telemetry before changing `dualiso.c` math or quality policy.

### Ranked next steps

1. High impact / medium effort: add Dual ISO substage telemetry inside `diso_get_full20bit()` so future engine work can target the largest remaining slice without guessing.
2. Medium impact / low effort: if the user's own clips favor another cap, promote a small adaptive chooser or GUI preference using the existing environment override behavior as the safety valve.
3. Medium impact / medium risk: revisit processed8 prefetch only after its hit rate is high enough to move median visible cadence, not just isolated frames.

## 2026-05-29 - manual smoke follow-up: stale work, scopes, audio, and CPU buckets

### Verified locally

- The user's manual smoke from `C:\Users\obabalola\AppData\Roaming\magiclantern\MLVApp\logs\mlvapp-20260528.log` used `MLVAPP_PLAYBACK_MAX_THREADS=4`, `quality_mode=1`, audio on, scopes on, zebras off, and no per-frame smoke telemetry.
- Manual x1 HQ remained CPU-bound: representative x1 sessions presented about `2.4 fps`, timeline advanced about `23.6 fps`, average render was `399-406 ms`, average `processed8_total_ms` was `396-404 ms`, average `llrawproc_ms` was `173-185 ms`, and GUI draw total was only `17-18 ms`.
- Implemented the next low-regression GUI playback batch:
  - queued drop-frame playback requests now coalesce older queued drop-frame requests from the same presentation generation before the render thread starts them, with `render_thread_queued_playback_drops_before_start` telemetry;
  - active playback scopes update at a default `150 ms` cadence (`MLVAPP_PLAYBACK_SCOPE_INTERVAL_MS=0` disables the throttle), leaving paused/scrubbed scope updates immediate;
  - audio playback no longer suspends a freshly initialized sink, no longer calls `start()` again when already running, and duplicate same-frame audio syncs within `100 ms` are coalesced;
  - smoke logs now emit `playback_smoke.cpu_summary` with raw decode, Dual ISO/llrawproc, debayer, processing, presentation, scope, audio-sync, and stale-queue counters.
- Rebuilt the user-facing release tree successfully after these source changes.
- Non-headless release profiles in `.claude-state/profiling/20260529-cpu-playback-optimizations/` used `tools/profiling/run-release-playback-profile.ps1 -ShowWindow -WaitForPaint` against `platform/qt/build-release/release/MLVApp.exe`.
- Fixture profiles confirm the remaining hot path is still engine CPU, not paint:
  - `large_hq_scope_cap4.json`: average `latency_ms 118.4`, `render_thread_total_ms 95.4`, `processed8_total_ms 88.9`, `llrawproc_ms 72.1`, `draw_frame_ready_total_ms 21.6`.
  - `large_hq_histogram_cap4.json`: average `latency_ms 133.2`, `render_thread_total_ms 104.6`, `processed8_total_ms 97.8`, `llrawproc_ms 78.2`, `draw_frame_ready_total_ms 26.7`.
  - The play-action smoke for the histogram cap-4 run reported first-frame `llrawproc_dual_iso_ms 374`, `processed8_total_ms 432`, and `draw_scopes_ms 5`.
- `tests\build-ci-console\release\console_tests.exe --check-golden` passed with Qt/MinGW on PATH: `78` tests, `298` assertions, `26` expected profile/batch-exe skips, `0` failures.

### Cross-checked from prior analysis

- This batch intentionally avoids Dual ISO math or quality-policy changes. The x1 HQ quality path still spends most of its time in full HQ Dual ISO reconstruction and processed8 generation.
- The user's forced `MLVAPP_PLAYBACK_MAX_THREADS=4` is still a key runtime variable. Earlier visible profiles showed cap `6` winning on the release fixture, while this later fixture sample favored cap `4` slightly in steady-state JSON; the safest user-facing posture remains overrideable caps plus per-clip measurement.
- Scope and audio changes reduce GUI-side jank and warnings, but they cannot by themselves turn a `~400 ms` full-HQ render into real-time playback on a CPU-bound VM.

### Needs runtime profiling

- Have the user rerun the real clip with the rebuilt release and `MLVAPP_PLAYBACK_SMOKE_TELEMETRY=1`; judge `playback_smoke.cpu_summary` for `llrawproc_dual_iso_ms`, `processed8_total_ms`, scope skips/updates, and audio-sync counters.
- Compare `MLVAPP_PLAYBACK_MAX_THREADS=4` versus unset/default cap `6` on the user's real clip; their last manual x1 run used cap `4`, but prior release profiles sometimes favored `6`.
- If x1 HQ still needs a large step-change, the next no-regression investigation must split `diso_get_full20bit()` telemetry before optimizing reconstruction internals.

### Ranked next steps

1. High impact / low risk: run the user's manual smoke again with `MLVAPP_PLAYBACK_SMOKE_TELEMETRY=1`, once with `MLVAPP_PLAYBACK_MAX_THREADS=4` and once with the variable unset/default cap `6`.
2. High diagnostic value / low risk: add Dual ISO substage telemetry inside `diso_get_full20bit()` to break down pattern/noise, exposure match, interpolation, mix/final blend, and 20-to-16 conversion.
3. High impact / higher risk: prototype an x1 Phase 3 reconned-raw consumer only after parity tests, because it touches pixel/state semantics even though it could reduce duplicate work in future pipelined playback modes.

## 2026-05-29 - Dual ISO full20 substage telemetry

### Verified locally

- Added coarse, low-overhead timing around the full HQ Dual ISO path in `diso_get_full20bit()`:
  - pattern/field identification, noise measurement, scratch allocation/clears, 14-to-20 promotion, exposure match, interpolation+border, full-res reconstruction, `mix_images`, `final_blend`, 20-to-16 conversion, and residual `other`.
  - The timing is stored thread-locally in `dualiso.c`, copied through `llrawproc` after the call, then surfaced in render telemetry as `dual_iso_full20_*`.
  - Smoke logs now emit `playback_smoke.dual_iso_full20_frame` when per-frame smoke telemetry is enabled and `playback_smoke.dual_iso_full20_summary` at play-stop.
- Rebuilt the user-facing release tree at `platform/qt/build-release/release/MLVApp.exe`.
- Visible release profile artifacts live under `.claude-state/profiling/20260529-dualiso-substage/`.
- `large_hq_cap6.json`, visible `--profile-playback`, `MLVAPP_PLAYBACK_MAX_THREADS=6`, x1 HQ fixture:
  - all frames: average `latency_ms 128.028`, `render_thread_total_ms 104.562`, `processed8_total_ms 103.000`, `llrawproc_dual_iso_ms 86.875`, `dual_iso_full20_total_ms 86.375`.
  - warm frames excluding first cold frame: average `render_thread_total_ms 85.000`, `processed8_total_ms 83.600`, `dual_iso_full20_total_ms 69.867`.
  - warm Dual ISO substages: `mix_images 42.333 ms`, scratch `9.533 ms`, interpolation+border `5.067 ms`, final blend `4.200 ms`.
- `large_hq_histogram_cap6_playaction.json`, visible play-action smoke, cap 6:
  - average `latency_ms 129.596`, `render_thread_total_ms 105.500`, `processed8_total_ms 99.125`, `dual_iso_full20_total_ms 79.813`.
  - the app log emitted `playback_smoke.dual_iso_full20_summary`; the first presented play-action frame was cold and showed `mix_images 132 ms`, interpolation `96 ms`, final blend `75 ms`.
- Regression coverage:
  - `git diff --check` clean except Git's CRLF checkout notices.
  - `tests\build-ci-console\release\console_tests.exe --check-golden`: `78` tests, `298` assertions, `26` expected profile/batch-exe skips, `0` failures.
- Release executable after rebuild: `platform/qt/build-release/release/MLVApp.exe`, timestamp `2026-05-28 22:04:23`, size `8286208`, SHA256 `47D358965693994D0CF0D2BA3499F38AC75E7B1214A67587D0395E77C9A46004`.

### Cross-checked from prior analysis

- The remaining x1 HQ cost is still engine CPU, not GUI paint. This pass does not change pixel math or quality policy; it only reveals where the CPU is going.
- Previous smoke logs that showed `llrawproc_dual_iso_ms ~108 ms` are consistent with the new breakdown: the cold/warm mix varies, but `mix_images` is now the largest steady substage on the visible fixture.

### Needs runtime profiling

- Have the user rerun the real clip with the rebuilt release and `MLVAPP_PLAYBACK_SMOKE_TELEMETRY=1`; the new `playback_smoke.dual_iso_full20_summary` line will rank the same substage buckets on their actual x1 Quality-mode smoke.
- Re-check cap `4` versus cap `6` with the new substage summary, because `mix_images` and scratch clears may respond differently to worker count than interpolation.

### Ranked next steps

1. High impact / medium risk: inspect and optimize `mix_images()` first; it is the largest warm Dual ISO substage on the visible fixture.
2. Medium impact / low-to-medium risk: reduce scratch clear cost by narrowing `memset` scope or reusing known-overwritten buffers, guarded by pixel parity tests.
3. Medium impact / medium risk: split `mix_images()` into internal timing buckets before changing math if the user's real clip shows different dominance than the fixture.

## 2026-05-29 - x1 Quality CPU playback optimizations

### Verified locally

- Implemented the next x1 Quality CPU-side optimization batch:
  - `mix_images()` now reuses its 1M-entry blend curve when the exact black/white/correction/DR inputs match the prior call on the same worker scratch.
  - AMaZE squeeze input preparation now computes the same squeeze mapping first, then copies mapped rows in parallel without remapping skipped fallback rows.
  - Dual ISO full20 scratch clears now zero the active per-pixel buffers in one parallel pass.
  - The recursive bilateral filter horizontal passes now run per-row in parallel, and its range-table setup no longer races on a shared loop temporary.
  - Processing telemetry now splits chroma, sharpen, and grain timing out of the previous `processing_other_ms` bucket and includes those fields in profile/smoke JSON.
- Visible, non-headless release profile artifacts live under `.claude-state/profiling/20260529-x1-quality-rbf-row-parallel/`.
- Baseline for this pass, `large_dual_iso.mlv` + `large_dual_iso_hq.marxml`, x1 Quality, full Dual ISO enabled with `MLVAPP_PROFILE_DISABLE_DUALISO_OVERRIDE=1`, 6 threads:
  - `large_dual_iso_hq_x1_threads6_full_dual_iso_visible.json`: average `latency_ms 279.641`, average `cadence_ms 257.702`, warm `render_thread_total_ms 219.000`, warm `dual_iso_full20_total_ms 200.556`, warm `mix_images_ms 45.111`, warm paint latency `243.630`.
- Final release after this batch, same visible setup:
  - `large_dual_iso_hq_x1_threads6_full_dual_iso_final_visible.json`: average `latency_ms 229.433`, average `cadence_ms 212.648`, warm `render_thread_total_ms 178.222`, warm `dual_iso_full20_total_ms 163.556`, warm `mix_images_ms 28.444`, warm paint latency `201.236`.
  - This is about `3.88 fps` to `4.70 fps` by cadence, a roughly `21%` GUI-playback cadence improvement on the same x1 full-quality fixture.
- App-backed and targeted regression coverage after the final rebuild:
  - `tests\build-ci-console\release\console_tests.exe` with `MLVAPP_PROFILE_EXE` and `MLVAPP_BATCH_EXE` pointed at the rebuilt release app: `81` tests, `1210` assertions, `0` skips, `0` failures.
  - Targeted pipeline checks passed for `ProcessingFilters.RbfFilterReuseMatchesFreshResultAfterResize`, `ProcessingFilters.RbfFilterReuseStaysStableAfterStateChanges`, `DualIsoPipeline.TinyDualIsoFullFramesMatchGolden`, `DualIsoPipeline.HQ_FullBlendAvx2ByteIdentity`, `DualIsoPipeline.HQ_AliasMapAvx2ByteIdentity`, and `DualIsoPipeline.PhaseE1_AMaZEEdgeDirectionAvx2ByteIdentity`.
- Release executable after rebuild: `platform/qt/build-release/release/MLVApp.exe`, timestamp `2026-05-28 23:35:49`, size `8288768`, SHA256 `152F87FC6306014405C8A023A2A48ABB9C1A93110FE4A9C4430192D29DE477D4`.

### Cross-checked from prior analysis

- The x1 Quality bottleneck is still CPU-side full Dual ISO and processing work, not GUI paint or scale-factor logic.
- The current batch keeps authored quality policy intact. It changes reuse and scheduling of existing work, and adds telemetry; it does not intentionally change Dual ISO output.
- A temporary regression in the AMaZE row-copy prototype was caught by the app-backed DNG golden and fixed before final profiling. The fix preserves the old fallback behavior for unmapped squeeze rows.

### Needs runtime profiling

- After the next user smoke, inspect `playback_smoke.cpu_summary`, `playback_smoke.dual_iso_full20_summary`, and the new `avg_processing_chroma_ms`, `avg_processing_sharpen_ms`, and `avg_processing_grain_ms` fields.
- If the real clip still shows high `avg_processing_total_ms` with low direct8 usage, profile the Look Assist/RBF path next.
- If the real clip still shows high `avg_dual_iso_full20_interp_ms`, add finer AMaZE substage timing before changing interpolation internals.

### Ranked next steps

1. High impact / medium effort: use the new smoke buckets from the user's real clip to choose the next target automatically; do not guess from the fixture if the bucket mix differs.
2. Medium impact / low risk: if mix remains dominant, add internal `mix_images()` telemetry around LUT, blend loop, chroma smooth, and alias map.
3. Medium impact / medium risk: if interpolation remains dominant, split AMaZE timing and only then consider deeper interpolation reuse or thread-scheduling changes.

## 2026-05-29 - manual smoke follow-up: Dual ISO mix curve alternation

### Verified locally

- The latest interactive GUI smoke found in `C:\Users\obabalola\AppData\Roaming\magiclantern\MLVApp\logs\mlvapp-20260528.log` is process `0xe78c`, launched as the normal release GUI at `2026-05-29T04:41:18Z`.
- Later entries in `mlvapp-20260529.log` are automated `--profile-playback` or `--batch` launches from this investigation, not a newer manual GUI smoke.
- The long `0xe78c` x1 Quality smoke ended at `2026-05-29T04:46:16Z` with `presented_fps=3.715`, `avg_present_interval_ms=264.514`, `avg_render_total_ms=263.830`, `avg_processed8_ms=262.689`, `avg_llrawproc_ms=102.906`, and `avg_draw_total_ms=16.085`.
- The same smoke's Dual ISO substage summary was dominated by `mix_images`: `avg_total_ms=100.840`, `avg_mix_ms=69.811`, `avg_interp_ms=7.632`, `avg_final_blend_ms=6.274`, `last_fullres=1`, `last_threads=6`.
- Shorter earlier sessions in the same process reached `presented_fps=4.871` and `4.699` while `avg_mix_ms` was only `13.225` and `12.000`, so the practical regression inside the smoke is the mix-curve cost returning later in playback.
- Implemented an exact four-slot LRU mix-curve cache in `src/mlv/llrawproc/dualiso.c` and `src/mlv/llrawproc/dualiso.h`. It reuses a 1M-entry curve only when `black`, `white`, `corr_ev`, and `lowiso_dr` match exactly; otherwise it rebuilds the selected slot with the existing math.
- Rebuilt the user-facing release executable at `platform\qt\build-release\release\MLVApp.exe`; timestamp `2026-05-29 00:22:19`, size `8288768`, SHA256 `37FD598F9B38DD7F4491D9491CCD1C9AFCA7AB9C7EC12CFF61BD46EFF65F596F`.
- Regression checks after the final rebuild passed for the full app-backed console suite (`81` tests, `1210` assertions, `0` failures) and focused Dual ISO pipeline checks. The broader pipeline suite still has four failures, but those same failures reproduce at base commit `cb618210ab81df8df23bb77f762a4f78f7d9acae`, so they are not introduced by this change.

### Cross-checked from prior analysis

- This change does not alter Dual ISO blend math or quality policy. It is a reuse/cache change for exact repeated curve inputs.
- The local visible fixture remains noisy under VM load; the exact-cache profile is therefore diagnostic rather than proof of user-visible speedup. The user's real smoke is the better judge because it already showed `avg_mix_ms` alternating between about `12 ms` and `70 ms`.

### Needs runtime profiling

- Rerun the real clip in the normal GUI release with `MLVAPP_PLAYBACK_SMOKE_TELEMETRY=1`, x1 scale, and Quality mode.
- Judge the next run primarily by `playback_smoke.dual_iso_full20_summary avg_mix_ms`; success means it stays closer to the earlier `12-13 ms` sessions instead of returning to about `70 ms`.
- If `avg_mix_ms` remains high, add hit/miss and key-drift telemetry to the mix-curve cache before widening the cache or changing curve representation.

### Ranked next steps

1. High impact / low risk: after the user's next smoke, compare `avg_mix_ms`, `avg_llrawproc_dual_iso_ms`, and `presented_fps` against the `0xe78c` session.
2. Medium impact / low risk: if the cache misses, instrument exact mix-curve keys and hit/miss counters in smoke logs.
3. Medium impact / medium risk: if mix improves but FPS remains about `4`, target the remaining `avg_processing_ms` and `avg_processing_core_ms` buckets next.

## 2026-05-29 - manual smoke follow-up: noise reduction determinism

### Verified locally

- The latest normal GUI smoke in `C:\Users\obabalola\AppData\Roaming\magiclantern\MLVApp\logs\mlvapp-20260529.log` is process `0x1f1f4`, launched at `2026-05-29T05:47:14Z` from `platform\qt\build-release\release\MLVApp.exe`.
- The smoke used `MLVAPP_PLAYBACK_MAX_THREADS=6`, `MLVAPP_PLAYBACK_SMOKE_TELEMETRY=1`, x1 scale, scopes on, and later Quality mode (`quality_mode=1`).
- Quality-mode sessions showed the mix-cache win was unstable:
  - sessions `8-10`: `presented_fps=4.689-4.832`, `avg_mix_ms=12.027-12.115`.
  - final session `11`: `presented_fps=3.945`, `avg_mix_ms=63.316`, `avg_llrawproc_dual_iso_ms=95.842`.
- `compute_black_noise()` had OpenMP loops updating shared `black`, `num`, and `stdev` accumulators without reductions. Added explicit OpenMP reductions for those accumulators so the noise estimate feeding full20 reconstruction is deterministic under multi-threaded playback.
- Added a console-suite `PlaybackSettingsSnapshot` guard so automated console tests no longer leave the user's GUI `Playback/QualityMode` and `Playback/ScaleFactorOverride` registry settings changed.
- Rebuilt the user-facing release executable at `platform\qt\build-release\release\MLVApp.exe`.
- Visible release profile, x1 Quality, 6 threads, `large_dual_iso.mlv` + `large_dual_iso_hq.marxml`:
  - no scope: `7.497 fps` including cold first frame; warm frames `8.666 fps`, warm `dual_iso_full20_total_ms=65.600`, warm `mix_ms=38.467`.
  - histogram requested: `7.194 fps` including cold first frame; warm frames `8.482 fps`, warm `dual_iso_full20_total_ms=66.067`, warm `mix_ms=36.600`.
- Regression checks:
  - `git diff --check` reports no whitespace errors, only Git CRLF checkout notices.
  - full console suite passed: `81` tests, `301` assertions, `26` expected profile/batch-exe skips, `0` failures; registry before/after stayed `QualityMode=1`, `ScaleFactorOverride=1`.
  - focused Dual ISO pipeline checks passed individually: `TinyDualIsoFullFramesMatchGolden`, `HQ_FullBlendAvx2ByteIdentity`, `HQ_AliasMapAvx2ByteIdentity`, and `PhaseE1_AMaZEEdgeDirectionAvx2ByteIdentity`.

### Cross-checked from prior analysis

- This is both a correctness and performance fix: the old shared accumulators could change `dark_noise_ev` / `lowiso_dr` from frame to frame on a CPU-bound VM, which can defeat exact mix-curve reuse and produce unstable HQ playback cost.
- A float32 mix-curve prototype was measured and reverted because repeat profiles did not show a clear average speedup over the reduction-only fix.

### Needs runtime profiling

- Have the user rerun the normal GUI smoke from the rebuilt release with `MLVAPP_PLAYBACK_SMOKE_TELEMETRY=1`, x1 scale, and Quality mode.
- Judge success by whether `playback_smoke.dual_iso_full20_summary avg_mix_ms` stays below the old `~63-83 ms` fallback band and whether `presented_fps` stays above the prior `3-4 fps` range.

### Ranked next steps

1. High impact / low risk: inspect the user's next smoke log before adding more code; this fix specifically targets the observed instability rather than a synthetic-only path.
2. Medium impact / medium effort: if `mix_ms` remains dominant, add internal `mix_images()` telemetry around half-res blend, alias-map filtering, overexposure map, and chroma smooth before changing math.
3. Medium impact / higher risk: prototype alias-map filtering or overexposure-map AVX2 only behind byte-identity pipeline coverage, because these touch visible HQ reconstruction details.

## 2026-05-29 - skipped-frame raw prefetch and x1 processing cleanup

### Verified locally

- The latest normal GUI smoke found before this batch was still the `2026-05-29T10:43:27Z` release GUI launch in `mlvapp-20260529.log`; its Quality x1 session ended at `presented_fps=3.886`, `avg_render_total_ms=255.000`, `avg_processed8_ms=253.726`, `avg_llrawproc_ms=105.077`, and every sampled frame had `raw_prefetch=0`.
- Controlled sequential `--profile-playback` runs did get raw prefetch hits, so the miss pattern was specific to real GUI playback skipping ahead by several timeline frames at low FPS.
- Implemented stride-aware raw uint16 prefetch in `src/mlv/video_mlv.c`: forward jumps up to 32 frames no longer invalidate the prefetch generation, and the worker predicts `base + stride` / `base + 2*stride` instead of only `base + 1` / `base + 2`.
- Added profiler-only `--frame-step` support so the release harness can reproduce dropped-frame request patterns. With `--frame-step 6`, requests `0,6,12` produced `raw_uint16_ms=0,30,0`; frame `12` was a raw-prefetch hit. The old sequential-only prefetch policy would have invalidated at frame `6` and prefetched `7/8`, missing frame `12`.
- Removed a redundant full-frame copy in the active Shadows/Highlights/clarity path; `recursive_bf_wrap()` overwrites `blur_image`, so the pre-copy is only kept for inactive tone-local paths.
- Visible release x1 profile, `large_dual_iso.mlv`, 6 threads, histogram:
  - Earlier profile baseline in this thread: warm `render_thread_total_ms=242.7`, `processing_ms=109.1`, `processing_shadows_highlights_prep_ms=72.7`.
  - Latest comparable profile after this batch: warm `render_thread_total_ms=238.1`, `processing_ms=98.7`, `processing_shadows_highlights_prep_ms=61.1`. VM noise still moves Dual ISO timing, but the S/H prep bucket remains below the original baseline.
- Regression checks passed for focused pipeline coverage (`ProcessingFilters.*`, `TinyDualIsoFullFramesMatchGolden`, `HQ_FullBlendAvx2ByteIdentity`, `HQ_AliasMapAvx2ByteIdentity`, `PhaseE1_AMaZEEdgeDirectionAvx2ByteIdentity`) and the full console harness (`81` tests, `301` assertions, `26` expected external-exe skips, `0` failures).
- Rebuilt the user-facing release executable at `platform\qt\build-release\release\MLVApp.exe`; timestamp `2026-05-29 06:35:41`, size `8292864`, SHA256 `B80881193C5E3FD04ED439A818B6D214ECFF83794ED8D7EA32542819568B255E`.

### Cross-checked from prior analysis

- This batch targets CPU-bound GUI playback without changing scale factor or authored quality policy.
- The raw prefetch change does not serve approximate frames; cached slots are still keyed by exact frame and generation. The new behavior only avoids throwing away valid future work during ordinary forward playback skips.

### Needs runtime profiling

- Have the user smoke the rebuilt normal GUI again with the same environment. Success should show `raw_prefetch=1` after the first skipped-frame miss in `playback_smoke.cpu_frame` samples and a lower `avg_raw_uint16_ms` contribution.
- If FPS remains around `3-4`, judge the next target from the new smoke buckets: `avg_llrawproc_ms`/Dual ISO mix versus `avg_processing_ms`/S-H prep versus draw/scopes.

### Ranked next steps

1. High impact / low risk: inspect the next manual smoke for `raw_prefetch` hits and `avg_raw_uint16_ms`; if they remain zero, add request-frame/stride telemetry to the smoke log.
2. Medium impact / medium effort: if processing remains above `~90 ms`, split Shadows/Highlights core timing further around recursive BF versus curve application.
3. Medium impact / higher risk: if Dual ISO mix remains dominant, continue with byte-identity-guarded AVX2/filter optimizations rather than changing reconstruction math.

## 2026-05-29 - GUI smoke visual-quality gate

### Verified locally

- Some early automated GUI playback benchmarks were not trustworthy as visual comparisons because the result artifact did not prove that Auto Look Assist had settled before playback.
- The current GUI smoke harness now records a `visualQuality` block and fails validation unless the same run proves:
  - Auto Look Assist applied and diagnostics settled.
  - `scale_request=1` / x1 playback.
  - `quality_mode=1` / Quality mode.
- `look_assist.apply.result` now logs the full Auto Look Assist tone state, including `preset_shadows`, `preset_highlights`, and `preset_vibrance`, not only exposure/contrast/temp/tint.
- A short rebuilt-release smoke on `C:\temp\MLV\M16-1327.MLV` validated the full look state: `scene=night`, `preset_exp=167`, `preset_contrast=14`, `preset_shadows=32`, `preset_highlights=-26`, `preset_vibrance=3`, `final_temp=6250`, `final_tint=22`, `scale_request=1`, and `quality_mode=1`.
- The risky Dual ISO `chroma_smooth_method==2` copy-avoidance candidate was reverted. It improved one M16-1327 run but regressed or became noisy on other clips, and it duplicated image-processing math in a way that was not worth keeping while visual quality was under question.
- The user-facing release executable was rebuilt after the revert and logging change: `platform\qt\build-release\release\MLVApp.exe`, timestamp `2026-05-29 11:53:38`, size `8338432`, SHA256 `ACE49250B5D198099350F6D729854F8FBDD76303EE3C60BC20850BB814253FE1`.

### Cross-checked from prior analysis

- The strongest current safe bottleneck signal is still CPU-side Shadows/Highlights prep: clean validated x1 Quality GUI runs show `processing_shadows_highlights_prep_ms` around `53-55 ms/frame`.
- `OMP_NUM_THREADS=6` and `OMP_NUM_THREADS=4` probes did not improve the clean M16-1327 run; `MLVAPP_PLAYBACK_MAX_THREADS=4` was worse than the 6-thread cap in the latest gated comparisons.
- Raising `MLVAPP_PLAYBACK_SCOPE_INTERVAL_MS` to `300` reduced scope work but did not improve end-to-end FPS under the current VM load, so it remains an optional diagnostic knob rather than a default change.

### Needs runtime profiling

- Continue accepting only GUI smoke runs where `validation.ok=true` and `visualQuality.lookAssist.applied=true`.
- Treat FPS runs without the visual gate as timing-only data, not as evidence that Auto Look Assist visual quality matches manual playback.

### Ranked next steps

1. High impact / low risk: audit Shadows/Highlights recursive bilateral prep for byte-identical savings before changing Dual ISO reconstruction math again.
2. Medium impact / low risk: keep rejecting candidates that improve a single clip but regress the validated multi-clip GUI set.
3. Medium impact / medium effort: add narrower timing inside the Shadows/Highlights prep bucket if the next candidate is not obvious from code audit.

## 2026-05-29 - visual-quality mismatch root cause and GUI no-copy playback

### Verified locally

- The guarded GUI smoke logs prove Auto Look Assist was not missing in the current x1 Quality runs. For `C:\temp\MLV\M16-1327.MLV`, the rebuilt release smoke reported `lookAssist.applied=true`, `scene=night`, `preset_exp=167`, `preset_contrast=14`, `preset_shadows=32`, `preset_highlights=-26`, `preset_vibrance=3`, `final_temp=6250`, and `final_tint=22`.
- The likely cause of the user's "less pretty" observation was not Look Assist being skipped; it was an experimental Shadows/Highlights curve-index mask path used during A/B work, plus earlier smoke artifacts that did not always prove the settled visual state before timing.
- `tools\profiling\run-release-gui-smoke.ps1` now clears known experimental playback/processing environment variables by default, including `MLVAPP_ENABLE_SH_CURVE_INDEX_MASK`, unless `-PreserveExperimentalEnvironment` is explicitly passed. Normal automated smoke runs therefore match the user's visual-quality defaults more closely.
- The smoke harness now records the cleared environment, requires x1 scale and Quality mode, requires Look Assist, and records the full `visualQuality` block before accepting a benchmark result.
- Implemented a GUI presentation copy-avoidance path for render-thread pre-scaled playback frames:
  - playback scaler helpers can write padded RGB888 rows with an explicit bytes-per-line;
  - `RenderFrameThread::ReadyFrame` carries `playbackScaledBytesPerLine`;
  - `MainWindow` wraps borrowed padded render-thread RGB8 buffers directly instead of copying them into owned GUI buffers.
- Focused regression coverage passed after these changes with explicit filters for the touched surfaces: `PlaybackScaling.FastScalerCanWritePaddedRows`, `PlaybackScaling.*`, `ProcessingFilters.*`, `DualIsoPipeline.TinyDualIsoFullFramesMatchGolden`, `DualIsoPipeline.HQ_FullBlendAvx2ByteIdentity`, and `DualIsoPipeline.HQ_AliasMapAvx2ByteIdentity`.
- Rebuilt the user-facing release executable at `platform\qt\build-release\release\MLVApp.exe`; timestamp `2026-05-29 13:45:03`, size `8346624`, SHA256 `A74F11DDA7542A782CC402CE8E2DC3F3455FB7E497EC79CA5D345BBBC0D9BD9F`.
- Guarded multi-clip GUI smoke results after the no-copy path:
  - `M16-1327_render_prescale_padded_borrow`: `presented_fps=4.793`, `owned_prepared_rgb8_frames=0`, `borrowed_prepared_rgb8_frames=48`, `avg_render_total_ms=199.688`, `avg_llrawproc_ms=85.812`, `avg_processing_shadows_highlights_prep_ms=53.417`.
  - `M16-1347_render_prescale_padded_borrow`: `presented_fps=5.091`, `owned_prepared_rgb8_frames=0`, `borrowed_prepared_rgb8_frames=51`, `avg_render_total_ms=187.980`, `avg_llrawproc_ms=74.529`, `avg_processing_shadows_highlights_prep_ms=52.510`.
  - `M16-1243_render_prescale_padded_borrow`: `presented_fps=4.694`, `owned_prepared_rgb8_frames=0`, `borrowed_prepared_rgb8_frames=47`, `avg_render_total_ms=202.723`, `avg_llrawproc_ms=81.043`, `avg_processing_shadows_highlights_prep_ms=56.383`.
- Rejected and reverted a Dual ISO paired chroma-smoothing input copy experiment. It passed focused byte/parity tests, but guarded GUI smokes showed a mixed result: `M16-1327` regressed from `4.793` to `4.596` fps, `M16-1347` regressed from `5.091` to `4.794` fps, and only `M16-1243` improved from `4.694` to `4.992` fps. The release was rebuilt after the revert and `M16-1327_after_dualiso_revert_confirm` validated `presented_fps=4.992`, `lookAssist.applied=true`, `scaleActiveLast=1`, `qualityModeLast=1`, and `experimentalEnvironmentCleared=true`.

### Cross-checked from prior analysis

- The S/H curve-index mask path remains default-off because it produced mixed timing and visible-output risk during A/B checks. Keeping it opt-in and cleared by default is the safer posture.
- The no-copy playback change does not alter tone, debayer, Dual ISO, or Look Assist math; it only avoids an RGB8 ownership copy when the render-thread buffer has a Qt-safe padded stride.
- FPS is still mostly bounded by CPU engine work rather than GUI drawing. The main remaining per-frame buckets in the guarded smokes are Dual ISO/llrawproc and Shadows/Highlights prep.

### Needs runtime profiling

- Continue rejecting any GUI smoke that does not show `validation.ok=true`, `visualQuality.lookAssist.applied=true`, `scaleActiveLast=1`, `qualityModeLast=1`, and `experimentalEnvironmentCleared=true`.
- If future manual runs look visually different from automated runs, compare the launch environment first, especially any `MLVAPP_*` experimental flags.

### Ranked next steps

1. High impact / low risk: continue targeting CPU-only savings in Shadows/Highlights prep and Dual ISO buckets while preserving byte/visual parity.
2. Medium impact / low risk: keep the no-copy borrow counters in smoke summaries so regressions in the presentation path are visible immediately.
3. Medium impact / medium effort: add finer Shadows/Highlights prep telemetry around recursive BF setup, scan passes, and output conversion before attempting another algorithmic change.

## 2026-05-30 - rejected RBF RGB3 copy unroll

### Verified locally

- Tried a narrow `RGB3` specialization in `src\processing\rbfilter\RBFilterPlain.cpp` that kept the recursive bilateral math unchanged but replaced the generic per-channel copy loops with hand-unrolled 3-channel copies in the left, right, vertical-down, and vertical-up passes.
- Rebuilt the user-facing release executable after the change: `platform\qt\build-release\release\MLVApp.exe`, timestamp `2026-05-30 00:04:59`, size `8791552`, SHA256 `617EBD75D5302EE79AC74235BF342A3D0378DEB76E2B61D9640F389BC1019A3F`.
- Re-ran the visible GUI smoke set with x1 Quality, settled Auto Look Assist, and the same launch-state validation. All three clips still passed the visual gate, but the FPS was worse than the earlier accepted chroma-copy baseline:
  - `M16-1327`: `presented_fps=5.00`, `avg_render_total_ms=189.38`, `avg_llrawproc_ms=76.70`, `avg_processing_shadows_highlights_prep_ms=58.15`, `avg_mix_chroma_ms=33.75`.
  - `M16-1347`: `presented_fps=4.99`, `avg_render_total_ms=190.93`, `avg_llrawproc_ms=78.83`, `avg_processing_shadows_highlights_prep_ms=56.40`, `avg_mix_chroma_ms=35.50`.
  - `M16-1446`: `presented_fps=5.75`, `avg_render_total_ms=162.13`, `avg_llrawproc_ms=39.35`, `avg_processing_shadows_highlights_prep_ms=62.72`, `avg_mix_chroma_ms=0.00`.
- The unroll was reverted back to the generic per-channel copy form because it did not improve the GUI-visible multi-clip baseline on this VM.

### Cross-checked from prior analysis

- The accepted row-parallel Dual ISO chroma-copy change still looks better than this RBF unroll on the clips that exercise chroma smoothing, so the RBF specialization is not the next best lever.

### Needs runtime profiling

- If we stay in the RBF bucket, the next candidate should be a structural reduction in the vertical recurrence or output phase, not another copy micro-tweak.

### Ranked next steps

1. High impact / low risk: leave the rejected RGB3 unroll out and keep looking for a real RBF vertical-pass reduction.
2. High impact / low risk: continue the current accepted dualiso row-copy win as the stable baseline.
3. Medium impact / low risk: keep using the visible GUI smoke set as the acceptance gate so every new probe is compared against the same user-facing launch path.

## 2026-05-29 - rejected 2x2 chroma lookup-hoist and post-revert visual validation

### Verified locally

- Added a scalar reference guard for the active Dual ISO 2x2 chroma smoother: `ProcessingFilters.ChromaSmooth2x2MatchesScalarReference` verifies `chroma_smooth(2, ...)` against an explicit reference implementation.
- The 2x2 chroma-smooth lookup-hoist prototype passed the focused reference test, but guarded release GUI smokes showed it was slower or noisy in the user-visible playback path:
  - `M16-1327_chroma2x2_lookup_hoist`: `presented_fps=4.693`, `avg_llrawproc_ms=85.55`, `avg_mix_chroma_ms=39.83`.
  - `M16-1347_chroma2x2_lookup_hoist`: `presented_fps=4.697`, `avg_llrawproc_ms=84.79`, `avg_mix_chroma_ms=42.06`.
  - `M16-1243_chroma2x2_lookup_hoist`: `presented_fps=4.393`, `avg_llrawproc_ms=95.27`, `avg_mix_chroma_ms=41.36`.
- The lookup-hoist source change was reverted; `src\mlv\llrawproc\chroma_smooth.c` is clean again. The reference guard remains because it is useful for future byte-identity work in this hotspot.
- Rebuilt the user-facing release executable after the revert: `platform\qt\build-release\release\MLVApp.exe`, timestamp `2026-05-29 14:02:15`, size `8346624`, SHA256 `A6CA1C9575248F4A35CAFFABA44951BE3795E1E0AB1CAC365BCE0EDFCBD1F496`.
- Post-revert guarded GUI smokes all validated the visual state (`validation.ok=true`, `lookAssist.applied=true`, `scaleActiveLast=1`, `qualityModeLast=1`, `experimentalEnvironmentCleared=true`):
  - `M16-1327_after_chroma2x2_revert_confirm`: `presented_fps=4.497`, `avg_render_total_ms=211.38`, `avg_llrawproc_ms=92.82`, `avg_processing_shadows_highlights_prep_ms=54.33`, `avg_mix_chroma_ms=37.80`.
  - `M16-1347_after_chroma2x2_revert_confirm`: `presented_fps=4.596`, `avg_render_total_ms=205.26`, `avg_llrawproc_ms=87.96`, `avg_processing_shadows_highlights_prep_ms=55.56`, `avg_mix_chroma_ms=40.96`.
  - `M16-1243_after_chroma2x2_revert_confirm`: `presented_fps=4.800`, `avg_render_total_ms=198.67`, `avg_llrawproc_ms=83.46`, `avg_processing_shadows_highlights_prep_ms=57.88`, `avg_mix_chroma_ms=38.88`.
- `ProcessingFilters.ChromaSmooth2x2MatchesScalarReference` passed, and `ProcessingFilters.*` passed (`8` tests, `15` assertions, `0` failures) after the revert/test rename.

### Cross-checked from prior analysis

- The user's "not pretty" observation aligns with launch-state drift, not an Auto Look Assist failure. Current accepted smokes clear experimental `MLVAPP_*` flags and prove the settled Look Assist tone state before playback.
- The strongest remaining CPU buckets are still Dual ISO mix/chroma and Shadows/Highlights prep. The reverted lookup-hoist shows that small scalar rewrites in chroma smoothing need GUI proof, not just byte parity.

### Needs runtime profiling

- Do not treat a single GUI FPS number as authoritative on this VM; compare repeated, visually validated multi-clip clusters.
- The standalone `DualIsoPipeline.ChromaSmoothScratchReusesFrameBufferAcrossFrames` check currently fails with a null scratch-buffer observation in its fixture path, so it remains a test-fixture gap to investigate before relying on it as coverage.

### Ranked next steps

1. High impact / low risk: pursue exact Shadows/Highlights prep savings first, especially sink-side exact mask work or RBF RGB3 specialization, because prep remains a stable `~54-58 ms/frame` bucket.
2. Medium impact / low risk: add finer Dual ISO `mix_chroma` timing before another chroma-smoothing math change.
3. Medium impact / low risk: audit diagnostic/telemetry allocation and uncapped OpenMP loops for CPU-bound playback overhead that does not affect pixels.

## 2026-05-29 - current visual-quality confirmation and rejected CPU probes

### Verified locally

- Rebuilt the user-facing release after reverting the OpenMP thread-cap probe: `platform\qt\build-release\release\MLVApp.exe`, timestamp `2026-05-29 14:20:57`, size `8346624`, SHA256 `67AC25DA60D594919D3768680B40A58154485449F60BDA48218C41B7F671A4E3`.
- Fresh guarded release GUI smoke on `C:\temp\MLV\M16-1327.MLV` with the harness defaults passed the full visual gate:
  - artifact: `.claude-state\profiling\20260529-gui-smoke\M16-1327_current_visual_quality_confirm.json`
  - launch env: only `MLVAPP_PLAYBACK_MAX_THREADS=6`; experimental `MLVAPP_*` flags cleared; no forced Qt offscreen platform.
  - validation: `validation.ok=true`, `lookAssist.applied=true`, `scaleRequestMatched=true`, `qualityModeMatched=true`, `systemCpuSettled=true`.
  - Auto Look Assist state: `scene=night`, `preset_exp=167`, `preset_contrast=14`, `preset_shadows=32`, `preset_highlights=-26`, `preset_vibrance=3`, `final_temp=6250`, `final_tint=22`.
  - playback: `presented_fps=4.617`, `avg_render_total_ms=203.189`, `avg_llrawproc_ms=85.081`, `avg_processing_shadows_highlights_prep_ms=53.973`, `avg_mix_chroma_ms=36.595`, `borrowed_prepared_rgb8_frames=37`, `owned_prepared_rgb8_frames=0`.
- The current evidence says the user's "less pretty" observation was caused by launch-state drift and insufficient early visual validation, not by Auto Look Assist being absent in the accepted current runs.
- Rejected an RBF boundary-zeroing skip prototype. It looked like a low-cost cleanup, but focused `ProcessingFilters.*` coverage caught output/reuse failures, so it was reverted before any GUI acceptance.
- Rejected the `src\mlv\video_mlv.c` OpenMP cap probe for raw unpack / float convert / 16-to-8 pack loops. Focused tests passed, but the guarded multi-clip GUI cluster worsened or failed to prove a win:
  - `M16-1327_video_mlv_omp_cap_rerun`: `presented_fps=4.496`, `avg_processing_shadows_highlights_prep_ms=60.22`, `avg_mix_chroma_ms=39.44`.
  - `M16-1347_video_mlv_omp_cap`: `presented_fps=4.193`, `avg_processing_shadows_highlights_prep_ms=62.69`, `avg_mix_chroma_ms=46.45`.
  - `M16-1243_video_mlv_omp_cap`: `presented_fps=4.187`, `avg_processing_shadows_highlights_prep_ms=62.79`, `avg_mix_chroma_ms=44.43`.
- The OpenMP cap probe was reverted; `src\mlv\video_mlv.c` has the same blob hash as `HEAD` after the revert (`f98af02a4543bdbdb3b870ba99191c826541a3e0`).

### Cross-checked from prior analysis

- The S/H curve-index mask remains default-off and explicitly cleared by the smoke harness because it is an optimization candidate with visual/timing risk, not a user-default playback path.
- Accepted GUI smoke evidence now requires both performance and visual-state proof. Older artifacts that lack `visualQuality` are useful for timing history only.

### Needs runtime profiling

- Next accepted optimization candidates should be benchmarked only with `validation.ok=true`, `visualQuality.lookAssist.applied=true`, `scaleActiveLast=1`, `qualityModeLast=1`, and experimental environment cleared.
- Add finer telemetry before the next risky math-adjacent change: split Shadows/Highlights prep and Dual ISO `mix_chroma` enough to isolate the copy/filter/core sub-buckets.

### Ranked next steps

1. High impact / low risk: add finer `mix_chroma` and Shadows/Highlights prep telemetry before changing pixel math again.
2. Medium impact / low risk: continue no-pixel-change CPU reductions first, especially allocation/telemetry/copy overhead in the GUI playback path.
3. Medium impact / medium risk: revisit RBF RGB3 specialization only with the current ProcessingFilters guard set and guarded multi-clip GUI smoke proof.

## 2026-05-29 - GUI visual-state gate added after quality mismatch report

### Verified locally

- Added `gui_smoke.visual_state` telemetry before visible GUI playback begins, so automated smokes now capture the full quality state that can make the image look different: Look Assist, sliders, raw levels, chroma smooth, stretch/aspect controls, Dual ISO controls, scopes/zebras, playback scale, and playback quality mode.
- Updated `tools\profiling\run-release-gui-smoke.ps1` to parse the visual-state line and fail validation if it is missing, if settled Look Assist is not enabled, or if x1/Quality no longer match the expected smoke state.
- Rebuilt the user-facing release executable: `platform\qt\build-release\release\MLVApp.exe`, timestamp `2026-05-29 14:42:20`, size `8358400`, SHA256 `1B736F6F5208EB7D0E139711AC3A6B44C3D6A080937562A88204EC1717A7902C`.
- Fresh visible GUI smokes with the new gate all passed `validation.ok=true`, settled Look Assist, x1 scale request, Quality mode, and cleared experimental environment:
  - `M16-1327_visual_state_gate`: `presented_fps=4.896`, `avg_render_total_ms=192.84`, `avg_llrawproc_ms=78.00`, `avg_sh_filter_ms=56.29`, `avg_mix_chroma_ms=36.84`.
  - `M16-1347_visual_state_gate`: `presented_fps=4.697`, `avg_render_total_ms=203.17`, `avg_llrawproc_ms=85.04`, `avg_sh_filter_ms=54.45`, `avg_mix_chroma_ms=38.66`.
  - `M16-1243_visual_state_gate`: `presented_fps=4.895`, `avg_render_total_ms=194.26`, `avg_llrawproc_ms=80.02`, `avg_sh_filter_ms=54.73`, `avg_mix_chroma_ms=37.86`.
- Current accepted state confirms Auto Look Assist is applying the expected night look and chroma smooth is active (`chroma_smooth=2`). The visual-state line also exposes the anamorphic/aspect state (`stretch_x=3.0`, `stretch_y=1.0`, vertical stretch index `3`), which was previously not validated by the benchmark harness.

### Cross-checked from prior analysis

- The quality mismatch was not caused by Auto Look Assist being absent in the latest accepted runs. The gap was that earlier automation only proved the tone look and x1/Quality state, not the full GUI visual state that can make a manual run look different.

### Needs runtime profiling

- Continue treating any GUI smoke without `gui_smoke.visual_state` as invalid for visual-quality comparisons.
- If a manual smoke still looks different, compare the new `visualQuality.visualState` block against the manual launch state first, especially stretch/aspect, chroma smooth, scopes, and receipt usage.

### Ranked next steps

1. High impact / low risk: use the new visual-state gate on every future optimization smoke before accepting a speed gain.
2. High impact / low risk: continue CPU work in the stable hot buckets now isolated by telemetry: Shadows/Highlights recursive filter and Dual ISO `mix_chroma`.
3. Medium impact / low risk: add deeper RBF internal timing next to locate which S/H recursive-filter pass is dominating the `~54-56 ms/frame` bucket.

## 2026-05-29 - RBF telemetry gated and rejected default/runtime CPU probes

### Verified locally

- Added optional detailed RBF timing behind `MLVAPP_PLAYBACK_RBF_DETAIL_TIMING`. Normal GUI smokes now clear the flag, so the extra timing barriers are absent unless explicitly requested.
- RBF detail timing on `M16-1327` showed the Shadows/Highlights recursive filter is dominated by vertical recurrence passes: `vertical_down=22.05 ms`, `vertical_up=22.48 ms`, with horizontal/output work around `26 ms` combined. This points away from small wrapper/copy changes as the next big win.
- Rejected and reverted the RBF RGB3 manual-unroll prototype. It passed focused processing tests, but guarded GUI A/B regressed the three-clip cluster by about `5.6%` FPS on average.
- Rejected changing the default playback scope interval from `150 ms` to `250 ms`. It reduced scope draw work, but same-build GUI A/B against forced `150 ms` lost about `8.4%` average FPS and added about `20 ms/frame` render time.
- Rejected `MLVAPP_EXPERIMENTAL_PROCESSED8_PREFETCH=1` for the current CPU-bound GUI path. Against the fresh default baseline, average FPS fell from `6.018` to `3.725`, average render time rose from `156.16 ms` to `256.52 ms`, and `processed8_prefetch_hits` stayed `0`.
- Rejected thread-cap changes away from the user's normal 6-thread launch:
  - `MLVAPP_PLAYBACK_MAX_THREADS=4`: average FPS `4.360` versus `6.018` at 6 threads, about `-27.6%`.
  - `MLVAPP_PLAYBACK_MAX_THREADS=8`: average FPS `4.960` versus `6.018` at 6 threads, about `-17.6%`.
- Fresh accepted baseline after the revert/rebuild uses the user-facing release executable at `platform\qt\build-release\release\MLVApp.exe`, timestamp `2026-05-29 15:36:04`, size `8368128`, SHA256 `A5D10D9CC4AF90033F9EABCA6AA0C0DAA43D94FF29FED72E693DC36613333536`.
- Fresh accepted x1 Quality GUI baseline with Auto Look Assist and visual-state validation:
  - `M16-1327_default150_final`: `presented_fps=6.081`, `avg_render_total_ms=155.46`, `avg_llrawproc_ms=62.39`, `avg_sh_filter_ms=41.97`, `avg_mix_chroma_ms=31.69`, `borrowed_prepared_rgb8_frames=61`, `owned_prepared_rgb8_frames=0`.
  - `M16-1347_default150_final`: `presented_fps=6.081`, `avg_render_total_ms=154.03`, `avg_llrawproc_ms=59.08`, `avg_sh_filter_ms=42.02`, `avg_mix_chroma_ms=31.15`, `borrowed_prepared_rgb8_frames=61`, `owned_prepared_rgb8_frames=0`.
  - `M16-1243_default150_final`: `presented_fps=5.891`, `avg_render_total_ms=158.98`, `avg_llrawproc_ms=62.64`, `avg_sh_filter_ms=43.66`, `avg_mix_chroma_ms=31.56`, `borrowed_prepared_rgb8_frames=59`, `owned_prepared_rgb8_frames=0`.

### Cross-checked from prior analysis

- The current automation now matches the user's important launch expectations: visible GUI release, no `QT_QPA_PLATFORM=offscreen`, x1 scale request, Quality mode, `MLVAPP_PLAYBACK_MAX_THREADS=6`, cleared experimental `MLVAPP_*` flags, system CPU settle before launch, and in-app CPU settle before playback.
- Auto Look Assist is applying in the accepted runs. The latest `visualQuality.visualState` blocks show `look_assist_enabled=1`, `look_assist_diagnostics_valid=1`, `scene=night`, active shadows/highlights/vibrance, `chroma_smooth=2`, and the expected stretch state.
- The strongest remaining CPU targets are still Dual ISO reconstruction and Shadows/Highlights recursive filtering. The accepted no-copy path has removed the GUI RGB8 ownership copy from the current baseline.

### Needs runtime profiling

- Do not enable processed8 prefetch by default unless a future implementation produces nonzero hits and improves multi-clip visible GUI playback.
- Keep RBF detail timing opt-in only; use it for focused probes, not normal playback acceptance runs.
- Re-run a fresh default baseline before accepting any small FPS win because this VM shows large load swings even after CPU-settle gating.

### Ranked next steps

1. High impact / low risk: pursue byte-identical S/H recursive-filter savings, especially vertical pass work, using RBF detail timing only during probes.
2. High impact / medium effort: continue Dual ISO `mix_chroma` work with GUI A/B proof; scalar byte-identity alone has not predicted playback speed.
3. Medium impact / low risk: keep launch-state/visual-state assertions in the smoke harness so future speedups cannot silently trade away the user's preferred look.

## 2026-05-29 - stale release binary explained the latest visual-quality concern

### Verified locally

- The current source tree no longer contained the rejected direct8 identity-curve experiment (`rg` found no `MLVAPP_DISABLE_DIRECT8_IDENTITY_CURVE_SKIP` / identity-curve remnants), but the user-facing release executable still contained the experiment marker before rebuild. That meant a visible smoke could run a rejected A/B binary even though the source had been reverted.
- Rebuilt the user-facing release executable from the reverted source: `platform\qt\build-release\release\MLVApp.exe`, timestamp `2026-05-29 16:22:51`, size `8368128`, SHA256 `5DC77036D59D5EF51479CCE36058FAD8975FAAEE3A0BAC0D30F9CD11C1227D0F`.
- Post-rebuild binary check found no `MLVAPP_DISABLE_DIRECT8_IDENTITY_CURVE_SKIP` marker in the release executable.
- Focused pipeline checks after the rebuild passed with the current source: `DualIsoPipeline.DirectProcessed8FastPath*`, `DualIsoPipeline.PhaseE7*`, `DualIsoPipeline.PhaseE8*`, `DualIsoPipeline.PhaseE9*`, `PlaybackScaling.*`, and `ProcessingFilters.*`.
- Fresh post-rebuild user-style GUI smoke (`M16-1327_post_rebuild_visual_threads4`) passed `validation.ok=true` with `MLVAPP_PLAYBACK_MAX_THREADS=4`, no receipt, x1 scale, Quality mode, settled Auto Look Assist, cleared experimental environment, and the expected stretch/aspect state:
  - Auto Look Assist: `scene=night`, `preset_exp=167`, `preset_contrast=14`, `preset_shadows=32`, `preset_highlights=-26`, `preset_vibrance=3`, `final_temp=6250`, `final_tint=22`.
  - Visual state: `chroma_smooth=2`, `stretch_x=3.0`, `stretch_y=1.0`, `quality_mode=1`, `scale_request=1`, `receipt_supplied=0`.
  - Playback: `presented_fps=4.796`, `avg_render_total_ms=198.708`, `avg_llrawproc_ms=75.437`, `avg_processing_shadows_highlights_prep_ms=55.604`, `avg_mix_chroma_ms=35.458`.

### Cross-checked from prior analysis

- Auto Look Assist was applying in the accepted gated runs, so the likely visual mismatch was not that Look Assist was missing. The new finding is that the release binary itself was stale from a rejected experiment after source revert, which made any visible visual comparison suspect until the release rebuild.

### Needs runtime profiling

- Treat GUI smoke results after a reverted visual/playback experiment as invalid until the user-facing release executable has been rebuilt and its timestamp/hash recorded after the revert.

### Ranked next steps

1. High impact / low risk: keep release rebuild/hash verification immediately after any rejected GUI experiment revert, before visible smoke comparisons.
2. High impact / low risk: continue CPU work only from post-rebuild, visual-gated smokes.
3. Medium impact / low risk: compare `MLVAPP_PLAYBACK_MAX_THREADS=4` and `6` as separate launch profiles; thread count affects speed, while visual acceptance must remain identical.

## 2026-05-29 - rejected raw-buffer reuse and S/H curve-index mask probes

### Verified locally

- Rejected and reverted a thread-local raw-frame buffer reuse probe in `src/mlv/video_mlv.c`. It reduced the raw-read sub-bucket in one 4-thread smoke, but visible GUI playback did not improve and repeat runs regressed the normal launch profile:
  - Baseline `M16-1327_pre_rawbuf_threads4`: `presented_fps=4.793`, `avg_render_total_ms=197.667`, `avg_raw_frame_ms=12.479`, `avg_llrawproc_ms=74.896`, `avg_processing_shadows_highlights_prep_ms=54.542`, `avg_mix_chroma_ms=36.437`.
  - Probe `M16-1327_rawbuf_threads4_repeat`: `presented_fps=4.595`, `avg_render_total_ms=206.413`, `avg_raw_frame_ms=10.978`, `avg_llrawproc_ms=82.196`, `avg_processing_shadows_highlights_prep_ms=57.935`, `avg_mix_chroma_ms=37.413`.
  - Baseline `M16-1327_pre_rawbuf_threads6`: `presented_fps=5.000`, `avg_render_total_ms=188.980`, `avg_raw_frame_ms=12.060`, `avg_llrawproc_ms=76.080`, `avg_processing_shadows_highlights_prep_ms=55.440`, `avg_mix_chroma_ms=34.820`.
  - Probe `M16-1327_rawbuf_threads6_repeat`: `presented_fps=4.491`, `avg_render_total_ms=212.311`, `avg_raw_frame_ms=10.956`, `avg_llrawproc_ms=87.933`, `avg_processing_shadows_highlights_prep_ms=59.000`, `avg_mix_chroma_ms=39.622`.
- Re-tested the opt-in Shadows/Highlights curve-index mask path using `MLVAPP_ENABLE_SH_CURVE_INDEX_MASK=1` and `-PreserveExperimentalEnvironment`. It remained visually valid but did not earn default-on status:
  - `M16-1327_sh_mask_threads4_probe`: `presented_fps=4.793`, `avg_render_total_ms=197.854`, `avg_raw_frame_ms=10.417`, `avg_llrawproc_ms=77.187`, `avg_processing_shadows_highlights_prep_ms=54.646`, `avg_mix_chroma_ms=38.000`.
  - `M16-1327_sh_mask_threads6_probe`: `presented_fps=4.799`, `avg_render_total_ms=197.729`, `avg_raw_frame_ms=11.729`, `avg_llrawproc_ms=82.167`, `avg_processing_shadows_highlights_prep_ms=54.125`, `avg_mix_chroma_ms=36.292`.
- Rebuilt the user-facing release executable after reverting the raw-buffer probe. Current release verification: `platform\qt\build-release\release\MLVApp.exe`, timestamp `2026-05-29 16:35:24`, size `8368128`, SHA256 `CC9A541265A22723CDCB6BB5F1ED19F586DA537BD2C1ADB2D6B04C0AA5811AE2`.

### Cross-checked from prior analysis

- Small allocator/read-path wins are not sufficient acceptance criteria on this VM. The GUI acceptance gate must improve presented FPS or average render time while preserving x1 Quality, Auto Look Assist, chroma smooth, stretch/aspect, and cleared experimental environment.
- The curve-index mask remains useful as a controlled probe because focused tests prove output parity for its covered path, but current visible GUI evidence is neutral to negative.

### Needs runtime profiling

- Re-run a fresh same-binary baseline before accepting any future sub-5% result; current VM noise is large enough to make one-off raw/llraw sub-bucket wins misleading.

### Ranked next steps

1. High impact / low risk: focus next on RBF scheduling/vertical-pass structure where detail timing shows the real S/H cost.
2. Medium impact / low risk: continue Dual ISO `mix_chroma` profiling, but only accept multi-clip GUI wins because clip-specific improvements have already reversed elsewhere.
3. Low impact / low risk: leave raw-frame buffer reuse and S/H curve-index mask off by default unless a later change turns them into visible GUI wins.

## 2026-05-29 - accepted RBF left-scan barrier reduction

### Verified locally

- Kept a narrow `RBFilterPlain` scheduling change: the independent horizontal left scan now uses `#pragma omp for nowait`, while `MLVAPP_PLAYBACK_RBF_DETAIL_TIMING=1` still inserts an explicit barrier before recording `left_ms`. This removes one normal-playback OpenMP barrier without changing filter math or timing correctness.
- Focused pipeline tests passed after the kept change and again after reverting the rejected output-loop probe:
  - `DualIsoPipeline.DirectProcessed8FastPath*`
  - `DualIsoPipeline.PhaseE7*`
  - `DualIsoPipeline.PhaseE8*`
  - `DualIsoPipeline.PhaseE9*`
  - `PlaybackScaling.*`
  - `ProcessingFilters.*`
- Same-binary visible GUI A/B on `C:\temp\MLV\M16-1327.MLV` with x1 Quality, Auto Look Assist, chroma smooth, cleared experimental environment, and `MLVAPP_PLAYBACK_MAX_THREADS=6` improved from baseline `presented_fps=4.595`, `avg_render_total_ms=204.674`, `avg_processing_shadows_highlights_prep_ms=59.652` to:
  - `M16-1327_nowait_threads6`: `presented_fps=4.895`, `avg_render_total_ms=192.265`, `avg_processing_shadows_highlights_prep_ms=55.857`.
  - `M16-1327_nowait_threads6_repeat`: `presented_fps=4.898`, `avg_render_total_ms=194.204`, `avg_processing_shadows_highlights_prep_ms=56.551`.
- The final rebuilt release after rejecting the output-loop probe also passed the primary visible GUI smoke:
  - `M16-1327_left_nowait_final_threads6`: `presented_fps=5.394`, `avg_render_total_ms=174.667`, `avg_processing_shadows_highlights_prep_ms=51.056`, `avg_llrawproc_ms=69.444`, `avg_mix_chroma_ms=34.185`.
- Multi-clip post-change smokes validated the same visual gate:
  - `M16-1347_nowait_threads6`: `presented_fps=5.195`, `avg_render_total_ms=181.038`, `avg_processing_shadows_highlights_prep_ms=50.596`, `avg_mix_chroma_ms=34.519`.
  - `M16-1243_nowait_threads6`: `presented_fps=5.198`, `avg_render_total_ms=180.269`, `avg_processing_shadows_highlights_prep_ms=51.173`, `avg_mix_chroma_ms=33.635`.
- Current user-facing release executable after the accepted/rejected split and the final formatting-only source touch: `platform\qt\build-release\release\MLVApp.exe`, timestamp `2026-05-29 17:02:00`, size `8368128`, SHA256 `E662938E64EE6C5A41442331E34E9CDD0D992E03A699F555C60E42231C0339D7`.
- Post-rebuild visual-gated smoke `M16-1327_left_nowait_post_rebuild_threads6` passed `validation.ok=true`, with x1 Quality, cleared experimental environment, settled Auto Look Assist, and the same appearance state:
  - Auto Look Assist: `scene=night`, `preset_exp=167`, `preset_contrast=14`, `preset_shadows=32`, `preset_highlights=-26`, `preset_vibrance=3`, `final_temp=6250`, `final_tint=22`.
  - Visual state: `chroma_smooth=2`, `stretch_x=3.0`, `stretch_y=1.0`, `quality_mode=1`, `scale_request=1`, `receipt_supplied=0`.
  - Playback: `presented_fps=4.895`, `avg_render_total_ms=195.837`, `avg_llrawproc_ms=81.265`, `avg_processing_shadows_highlights_prep_ms=56.469`, `avg_mix_chroma_ms=36.959`.

### Cross-checked from prior analysis

- The speed gain is modest but credible because the repeat same-binary primary run held the improvement before VM load improved. The later `5.394 fps` final run should not be treated as the whole code gain because the VM became quieter.
- The visual-state gate remained stable across all accepted smokes: Auto Look Assist applied, x1 scale request/active scale, Quality mode, `chroma_smooth=2`, and `stretch_x=3.0` / `stretch_y=1.0`.
- Rejected and reverted an additional final-output-loop `nowait` probe. It passed focused tests and one threads=6 smoke, but the user-style threads=4 profile fell back near baseline (`M16-1327_nowait_output_threads4`: `presented_fps=4.593`, `avg_render_total_ms=205.130`) versus the earlier left-scan-only run (`presented_fps=4.688`, `avg_render_total_ms=203.021`).

### Needs runtime profiling

- Continue treating RBF vertical down/up as the hard target. Opt-in detail timing after the accepted change still showed roughly `avg_vertical_down_ms=21.981` and `avg_vertical_up_ms=22.000`, with normal detail overhead disabled for acceptance runs.

### Ranked next steps

1. High impact / medium risk: investigate byte-identical ways to reduce the RBF vertical recurrence cost without parallelizing columns or changing the legacy flattened traversal.
2. High impact / medium risk: continue Dual ISO `mix_chroma` work; recent accepted smokes still spend about `33-34 ms/frame` there.
3. Medium impact / low risk: keep threads=4 and threads=6 in the smoke matrix when testing small changes, because the rejected output-loop probe behaved differently across those launch profiles.

## 2026-05-29 - rejected Dual ISO 2x2 chroma sample-gather merge

### Verified locally

- Tried a byte-identical `chroma_smooth_2x2` cleanup in `src\mlv\llrawproc\chroma_smooth.c`: the horizontal and vertical sample-gather passes were merged so shared red/blue and green EV table lookups could be reused inside each of the five 2x2 samples.
- Focused tests passed against the old scalar-reference oracle and Dual ISO chroma/golden coverage:
  - `ProcessingFilters.ChromaSmooth2x2MatchesScalarReference`
  - `DualIsoPipeline.*ChromaSmooth*`
  - `DualIsoPipeline.TinyDualIsoFullFramesMatchGolden`
  - `DualIsoPipeline.DirectProcessed8FastPath*`
  - `DualIsoPipeline.PhaseE9*`
- Visible GUI smoke with the probe stayed visually valid but did not improve playback:
  - `M16-1327_cs2_combined_sample_threads6`: `presented_fps=4.893`, `avg_render_total_ms=195.612`, `avg_llrawproc_ms=82.000`, `avg_mix_chroma_ms=37.102`, `avg_chroma_fullres_ms=16.592`, `avg_chroma_halfres_ms=14.694`.
  - Same-path pre-probe baseline `M16-1327_left_nowait_post_rebuild_threads6`: `presented_fps=4.895`, `avg_render_total_ms=195.837`, `avg_llrawproc_ms=81.265`, `avg_mix_chroma_ms=36.959`.
- Rejected and reverted the sample-gather merge because it did not produce a GUI playback gain.
- Rebuilt the user-facing release executable after revert: `platform\qt\build-release\release\MLVApp.exe`, timestamp `2026-05-29 17:12:26`, size `8368128`, SHA256 `421C6CF29AE6959C38A48F2D10D12DFEABA381AE3E67E92734C93AD1E645B439`.
- Post-revert visual-gated restore smoke passed:
  - `M16-1327_post_reject_restore_threads6`: `validation.ok=true`, `presented_fps=5.289`, `avg_render_total_ms=180.151`, `avg_llrawproc_ms=70.755`, `avg_processing_shadows_highlights_prep_ms=53.604`, `avg_mix_chroma_ms=33.830`.

### Cross-checked from prior analysis

- The faster post-revert smoke is not credited as a code gain because the reverted source matches the accepted path and this VM load swings materially between runs.
- The Dual ISO chroma hotspot remains real, but small scalar refactors inside 2x2 sample gathering are not the next high-confidence path.

### Needs runtime profiling

- Avoid carrying pixel-path cleanups unless repeated same-binary GUI A/B shows a real win. Passing parity tests alone is not enough for this CPU-bound playback goal.

### Ranked next steps

1. High impact / low risk: prefer no-pixel-change scheduling/copy/telemetry reductions over further chroma-smooth scalar reshuffling.
2. High impact / medium risk: continue RBF vertical-pass investigation, but avoid broad manual unrolls already shown to regress.
3. Medium impact / low risk: consider lightweight playback telemetry/allocation reductions that keep smoke summary fields intact.

## 2026-05-29 - accepted playback OpenMP cap, rejected copy/unroll probes

### Verified locally

- Rebuilt the user-facing release after the earlier rejected RBF horizontal-average unroll source was reverted. Verification for the cleaned pre-cap binary:
  - `platform\qt\build-release\release\MLVApp.exe`, timestamp `2026-05-29 17:34:21`, size `8368128`, SHA256 `3D45D0BCFF76C470691D261541F2D49F314D7D75227BF6655407FF6289C99E2D`.
  - Restore smoke `M16-1327_restore_after_rebuild_rbf_detail_threads6`: `validation.ok=true`, Auto Look Assist applied, x1 Quality, `presented_fps=4.595`, `avg_render_total_ms=208.804`, `avg_processing_shadows_highlights_prep_ms=56.848`, `avg_mix_chroma_ms=39.152`.
- Rejected and reverted the Dual ISO chroma selective-copy/output-init probe. It passed focused tests and reduced the raw chroma copy bucket slightly, but GUI playback regressed:
  - Probe `M16-1327_selective_cs2_copy_threads6`: `presented_fps=4.790`, `avg_render_total_ms=199.958`, `avg_mix_chroma_ms=37.542`, `avg_chroma_copy_ms=5.083`.
  - Restore after revert `M16-1327_post_selective_copy_reject_restore_threads6`: `presented_fps=5.395`, `avg_render_total_ms=174.148`, `avg_mix_chroma_ms=32.537`.
- Rejected and reverted the RBF RGB3 horizontal-average unroll. It passed focused tests but was slower in the GUI:
  - Baseline `M16-1327_restore_rbf_detail_threads6`: `presented_fps=5.096`, `avg_render_total_ms=186.549`, `avg_processing_shadows_highlights_prep_ms=52.412`, RBF `avg_horizontal_average_ms=7.157`.
  - Probe `M16-1327_havg_unroll_rbf_detail_threads6`: `presented_fps=4.496`, `avg_render_total_ms=208.267`, `avg_processing_shadows_highlights_prep_ms=58.622`, RBF `avg_horizontal_average_ms=8.733`.
- Found a no-pixel-change CPU win: the app's playback worker cap did not cap OpenMP teams used by RBF and llrawproc. A pure env A/B with no source changes showed the hidden oversubscription:
  - No manual OMP cap, pre-code restore: `presented_fps=4.595`, `avg_render_total_ms=208.804`, RBF total `56.848`, Dual ISO total `81.848`.
  - `OMP_NUM_THREADS=6`, same binary style: `presented_fps=5.982`, `avg_render_total_ms=158.867`, RBF total `49.900`, Dual ISO total `60.533`.
  - `OMP_NUM_THREADS=4`: `presented_fps=5.398`, `avg_render_total_ms=175.204`; 6 threads was better for this clip/VM.
- Implemented the accepted fix: `mlvappApplyPlaybackOpenMpThreadCount(workerThreads)` caps OpenMP teams to the playback worker count, while respecting a lower existing `OMP_NUM_THREADS`. The GUI smoke summary now logs `openmp_threads_last` and `openmp_thread_cap_active_last`; the harness also records inherited `OMP_NUM_THREADS`.
- Tests passed after the accepted OpenMP cap:
  - `console_tests.exe --gtest_filter="WorkerThreadCount.*"`.
  - `pipeline_tests.exe --gtest_filter="DualIsoPipeline.DirectProcessed8FastPath*:DualIsoPipeline.PhaseE7*:DualIsoPipeline.PhaseE8*:DualIsoPipeline.PhaseE9*:PlaybackScaling.*:ProcessingFilters.*"`.
- Rebuilt the user-facing release after the accepted cap:
  - `platform\qt\build-release\release\MLVApp.exe`, timestamp `2026-05-29 17:42:55`, size `8369664`, SHA256 `9FC29E6D459319112906E1818E091478B03018A805057E18CDAE714E50810034`.
- App-applied cap smokes with `OMP_NUM_THREADS=unset` stayed visually valid and showed the cap taking effect:
  - `M16-1327_app_openmp_cap_threads6_rbf_detail`: `validation.ok=true`, `openmp_threads_last=6`, `openmp_thread_cap_active_last=1`, `presented_fps=5.298`, `avg_render_total_ms=177.472`.
  - Repeat `M16-1327_app_openmp_cap_threads6_repeat_rbf_detail`: `validation.ok=true`, `openmp_threads_last=6`, `openmp_thread_cap_active_last=1`, `presented_fps=5.691`, `avg_render_total_ms=164.456`, RBF total `52.614`, Dual ISO total `61.263`, `avg_mix_chroma_ms=31.246`.

### Cross-checked from prior analysis

- The user-visible quality concern was caused by launch/build drift, not Auto Look Assist being off. Current visual-gated smokes show Look Assist active with `scene=night`, `exposure=167`, `contrast=14`, `shadows=32`, `highlights=-26`, `vibrance=3`, `temperature=6250`, `tint=22`, x1 Quality, `chroma_smooth=2`, and `stretch_x=3.0` / `stretch_y=1.0`.
- Manual pixel-path micro-optimizations remain high-risk on this VM: several parity-safe-looking probes passed tests but lost FPS. The OpenMP cap is safer because it changes scheduling/oversubscription, not image math.

### Needs runtime profiling

- Run the accepted OpenMP cap across the other user smoke clips before treating the full gain as universal. M16-1327 is the primary clip, but multi-clip behavior has diverged before.
- Continue with no-pixel-change CPU work first: thread scheduling, presentation/scopes overhead, raw prefetch timing, and telemetry allocation reductions.

### Ranked next steps

1. High impact / low risk: run the app-applied OpenMP cap on `M16-1347`, `M16-1243`, and any other user smoke clips with x1 Quality and Auto Look Assist required.
2. High impact / low risk: investigate whether scope drawing can be adaptively throttled during playback without changing the processed frame image.
3. Medium impact / low risk: profile raw prefetch priming and telemetry allocation reduction as opt-in probes before defaulting anything.

## 2026-05-29 - raw-prime and micro-optimization rejection pass

### Verified locally

- Added smoke-harness/log hardening for raw-prime A/B: `MLVAPP_DISABLE_PLAY_START_PREROLL` is now cleared by default in `tools\profiling\run-release-gui-smoke.ps1` and recorded in `playback_smoke.start`.
- Rejected and reverted the raw uint16 play-start prime. Same visible GUI path on `C:\temp\MLV\M16-1327.MLV`, x1 Quality, Auto Look Assist, `MLVAPP_PLAYBACK_MAX_THREADS=6`:
  - Prime enabled: `presented_fps=4.397`, `play_to_first_ms=395`, `avg_render_total_ms=214.545`.
  - Play-start preroll disabled: `presented_fps=4.600`, `play_to_first_ms=347`, `avg_render_total_ms=204.870`.
  - Source reverted: `presented_fps=4.794`, `play_to_first_ms=339`, `avg_render_total_ms=197.896`.
- Rejected the S/H curve-index mask as a default. Env-only probe `MLVAPP_ENABLE_SH_CURVE_INDEX_MASK=1` was visually valid but slower: `presented_fps=4.590`, `avg_render_total_ms=209.543`, `avg_processing_shadows_highlights_prep_ms=58.457`.
- Rejected and reverted the RBF vertical-loop flattening probe. It passed focused tests, but normal GUI smoke did not beat the restored baseline:
  - Flat vertical loop: `presented_fps=4.897`, `avg_render_total_ms=191.531`, `avg_processing_shadows_highlights_prep_ms=53.429`.
  - Restored no-detail benchmark in the same pass: `presented_fps=4.999`, `avg_render_total_ms=189.460`, `avg_processing_shadows_highlights_prep_ms=53.780`.
- Rejected and reverted a direct8 hot-loop branch-hoist probe. Isolated no-hoist baseline after revert was better or equal:
  - Hoist build multi-clip: `M16-1327=4.687 fps`, `M16-1446=5.693 fps`.
  - No-hoist rebuild: `M16-1327=5.100 fps`, `M16-1446=5.698 fps`.
- Rejected and reverted two Dual ISO chroma micro-probes:
  - Parallel `memcpy` sections reduced `avg_chroma_copy_ms` (`6.52 -> 4.35`) but did not improve GUI playback (`4.900 fps` versus `4.984 fps` on the immediately prior direct8-hoist build).
  - `__restrict` chroma-smooth parameters compiled, but `M16-1327` fell to `4.496 fps`; reverted.
- Current rebuilt user-facing release after removing the rejected probes:
  - `platform\qt\build-release\release\MLVApp.exe`
  - timestamp `2026-05-29T18:59:15.2621785-05:00`
  - size `8370688`
  - SHA256 `862C1B03692378A5F68FCF80C7B0BF063B3F7F2CB802A503224A69FD4061726A`

### Cross-checked from prior analysis

- Auto Look Assist is active in current automated smokes, not missing: `scene=night`, x1 Quality, `chroma_smooth=2`, `stretch_x=3.0`, `stretch_y=1.0`.
- Single-run FPS remains noisy on this VM. Treat only repeated or isolated A/B wins as keepers.

### Needs runtime profiling

- RBF detail timing on the current accepted path still points at vertical recursion: `avg_vertical_down_ms=23.128`, `avg_vertical_up_ms=21.830` on `M16-1327_baseline_rbf_detail_threads6`.
- Dual ISO mix/chroma remains a major hotspot on clips that use it, but scalar micro-refactors have not turned into GUI FPS gains.

### Ranked next steps

1. High impact / medium risk: investigate a more structural RBF vertical-pass optimization with a strict parity test before GUI benchmarking.
2. High impact / medium risk: look for a safe Dual ISO chroma algorithm/library-level improvement rather than scalar reshuffling.
3. Medium impact / low risk: revisit adaptive scope drawing only if it can be proven not to alter the processed frame image and not to fight the user's normal UI expectations.

## 2026-05-30 - accepted RBF vertical scheduling and Dual ISO mean23 LUT reuse

### Verified locally

- Kept a structural RBF scheduling change in `src\processing\rbfilter\RBFilterPlain.cpp`: the exact vertical down and vertical up recurrence bodies now run as two OpenMP sections after the horizontal phase, then the output phase runs in its own normal parallel region. Filter math/indexing stayed unchanged.
- RBF-focused pipeline tests passed inside the broader `pipeline_tests.exe` run:
  - `ProcessingFilters.RbfFilterReuseMatchesFreshResultAfterResize`
  - `ProcessingFilters.RbfFilterReuseStaysStableAfterStateChanges`
  - `ProcessingFilters.RbfFilterOutputLutMatchesSeparateLevelsPass`
  - `ProcessingFilters.RbfFilterCurveIndexOutputMatchesSeparateLevelsAndMatrixPass`
- Multi-clip visible GUI smokes on the RBF vertical split passed the full visual gate with x1 Quality, Auto Look Assist, cleared experimental environment, `stretch_x=3.0`, `stretch_y=1.0`, and `MLVAPP_PLAYBACK_MAX_THREADS=6`:
  - `M16-1327_vertical_sections_threads6_repeat`: `presented_fps=5.790`, `avg_render_total_ms=163.293`, `avg_sh_filter_ms=48.138`.
  - `M16-1347_vertical_sections_threads6`: `presented_fps=5.685`, `avg_render_total_ms=165.281`, `avg_sh_filter_ms=47.579`.
  - `M16-1446_vertical_sections_threads6`: `presented_fps=6.795`, `avg_render_total_ms=137.662`, `avg_sh_filter_ms=51.059`.
- Kept a Dual ISO mean23 interpolation change in `src\mlv\llrawproc\dualiso.c`: the HQ mean23 path now uses the per-worker `dualiso_full20bit_scratch_t` EV LUT storage when available, avoiding the shared static LUT and `ev2raw_mutex` on the normal scratch-backed playback path. The old static-LUT path remains as a fallback for callers without scratch storage.
- Focused `pipeline_tests.exe --gtest_filter=DualIsoPipeline.*` after the Dual ISO change kept the relevant golden/scratch coverage green, including:
  - `TinyDualIsoFullFramesMatchGolden`
  - `TinyDualIsoPreviewFramesMatchGoldenAndStayCloseToFull`
  - `HQ_FullBlendAvx2ByteIdentity`
  - `Full20Mean23OutputIgnoresPoisonedOuterScratch`
  - `DualIsoPlaybackForcesMean23WhenOverrideActive`
  - `PhaseE5_AliasMapKeptAtScale1`
- The same focused Dual ISO run still reports the known pre-existing four failures: `DirectProcessed8FastPath_AVX2IntrinByteIdentity`, `ProcessedFrame16CacheKeepsNearbyFramesWarm`, `ProcessedFrame8CacheKeepsNearbyFramesWarm`, and `ChromaSmoothScratchReusesFrameBufferAcrossFrames`.
- Visible GUI smokes after the Dual ISO scratch-LUT change improved all three user clips versus the RBF-vertical checkpoint:
  - `M16-1327`: `presented_fps 5.790 -> 6.093` (`+5.2%`), `avg_render_total_ms 163.293 -> 155.885`, Dual ISO total `62.207 -> 57.426 ms`, interpolation `7.431 -> 5.705 ms`.
  - `M16-1327` repeat after the change: `presented_fps=5.974`, `avg_render_total_ms=158.200`, Dual ISO total `59.333 ms`, interpolation `5.467 ms`.
  - `M16-1347`: `presented_fps 5.685 -> 5.897` (`+3.7%`), `avg_render_total_ms 165.281 -> 158.864`, Dual ISO total `64.632 -> 60.847 ms`, interpolation `7.807 -> 6.712 ms`.
  - `M16-1446`: `presented_fps 6.795 -> 7.192` (`+5.8%`), `avg_render_total_ms 137.662 -> 130.083`, Dual ISO total `31.382 -> 30.069 ms`, interpolation `7.632 -> 6.486 ms`.
- Current user-facing release executable after the accepted RBF/Dual ISO changes: `platform\qt\build-release\release\MLVApp.exe`, timestamp `2026-05-29 19:23:20`, size `8371712`, SHA256 `846C3377905840805623EAAD596B47E4C6161E2F02FB44DCD919913DDB18D976`.

### Cross-checked from prior analysis

- The gains are accepted because they hit the actual non-headless GUI playback smoke, not just a headless profile or an inner timing bucket. All accepted smokes preserved the user-visible quality state that previously drifted: Auto Look Assist on, x1 Quality, chroma smooth active, and the expected anamorphic stretch.
- The Dual ISO scratch-LUT change is lower risk than prior rejected chroma-smooth scalar rewrites because it reuses existing LUT generation through per-worker storage and keeps the interpolation loop math byte-identical.
- The VM still has run-to-run noise, but this batch improved three clips plus a repeat on `M16-1327`; the direction matched the intended timing bucket reduction.

### Needs runtime profiling

- The remaining dominant buckets after these changes are still `mix_images`/`mix_chroma` inside Dual ISO full20 and Shadows/Highlights recursive filtering. Current accepted GUI runs show about `30-33 ms/frame` in Dual ISO chroma and about `45-49 ms/frame` in S/H RBF prep.
- Any next RBF change needs focused parity coverage first; the vertical recurrence remains sensitive to traversal and scheduling changes.

### Ranked next steps

1. High impact / medium risk: inspect Dual ISO `mix_chroma` at a coarser algorithm boundary, especially whether fullres and halfres chroma smoothing can be safely staged or cached without duplicating math.
2. High impact / medium risk: continue RBF vertical-pass work only with exact output parity and multi-clip GUI proof; small manual unrolls have repeatedly regressed.
3. Medium impact / low risk: look for additional shared-state/lock or per-frame allocation costs in the full20 playback path, because the scratch-LUT reuse shows these can become real GUI FPS wins.

## 2026-05-30 - raw prefetch lookahead=1 accepted, chroma overlap rejected

### Verified locally

- Reverted the failed Dual ISO chroma-overlap attempt. Overlapping `hdr_chroma_smooth()` for fullres and halfres inside `mix_chroma` looked structurally safe in code review, but the visible GUI smoke regressed badly on `M16-1327` (`presented_fps=4.297`, `avg_render_total_ms=221.535`, `avg_mix_chroma_ms=70.605`) versus the restored sequential path.
- Kept the raw `uint16` prefetch lookahead lowered from `2` to `1` in `src\mlv\video_mlv.c`.
- The focused pipeline regression slice stayed green after the raw-prefetch edit:
  - `DualIsoPipeline.*`
  - `ProcessingFilters.RbfFilter*`
- Fresh visible GUI smoke on the rebuilt user-facing release stayed valid on all three clips with x1 Quality, Auto Look Assist, and the expected stretch/aspect state:
  - `M16-1327`: `presented_fps=4.898`, `avg_render_total_ms=191.898`, `avg_llrawproc_ms=79.592`, `avg_mix_chroma_ms=36.510`, `raw_prefetch_hits=15`.
  - `M16-1347`: `presented_fps=4.595`, `avg_render_total_ms=205.565`, `avg_llrawproc_ms=85.717`, `avg_mix_chroma_ms=41.043`, `raw_prefetch_hits=12`.
  - `M16-1446`: `presented_fps=5.892`, `avg_render_total_ms=161.288`, `avg_llrawproc_ms=42.322`, `avg_mix_chroma_ms=0.000`, `raw_prefetch_hits=23`.
- Compared with the earlier accepted same-clip baseline in the investigation notes, the new raw-prefetch setting is a net multi-clip win by average FPS while preserving the visual gate. The gain is concentrated on `M16-1446`; the other two clips stayed roughly flat rather than regressing.
- Current user-facing release executable after the raw-prefetch change: `platform\qt\build-release\release\MLVApp.exe`, timestamp `2026-05-29 21:58:43`, size `8792064`, SHA256 `101A8F33B7A51D6410168B4652F74AAF08CF0ABE537D00A29E1BF0C7F604C7B4`.

### Cross-checked from prior analysis

- The earlier raw-prefetch `lookahead=1` A/B already suggested this was the right direction on at least one clip, and the visible GUI gate now confirms it under the current rebuilt release and launch settings.
- The failed chroma-overlap probe is a useful negative result: `mix_chroma` wants its two passes run independently on this VM, not overlapped with another OpenMP region.

### Needs runtime profiling

- `mix_chroma` remains hot, but the safe win so far came from the raw read path, not from trying to overlap the chroma smoothing itself.
- The next candidate should target the remaining `mix_chroma` work at a lower algorithm boundary or find another clip-stable read-path reduction rather than another parallel overlap.

### Ranked next steps

1. High impact / low risk: keep the raw-prefetch lookahead=1 change if the next smoke batch confirms the same average win on the usual clip set.
2. High impact / medium risk: inspect a lower-level `mix_chroma` optimization that does not add a second OpenMP region.
3. Medium impact / low risk: keep the current release/launch validation harness as-is, because it caught both the visual-state drift and the failed chroma overlap before they could be mistaken for wins.

## 2026-05-30 - adaptive scope redraw throttling accepted, active SH copy-skip rejected

### Verified locally

- Kept the `imageChanged && !shadows_highlights_active` copy-skip probe in `src\processing\raw_processing.c` while measuring it against the real visible GUI smoke set. On the active Shadows/Highlights clips it did not move the needle because the current hot path was already showing `avg_sh_copy_ms=0.000` on the visible benchmark receipts.
- Added adaptive playback scope throttling in `platform\qt\MainWindow.cpp`: when playback is active, the scope redraw interval now grows with the measured render time instead of staying fixed at the static base interval. The new interval is bounded between the configured base and a capped multiple of the current frame time so the GUI can stop repainting scopes as aggressively when the frame is already expensive.
- Fresh visible GUI smoke on the rebuilt user-facing release stayed valid with x1 Quality, Auto Look Assist, and the expected stretch/aspect state:
  - `M16-1327`: `presented_fps=6.493`, `avg_render_total_ms=144.338`, `avg_draw_scopes_ms=2.954`, `scope_updates=33`, `scope_skips=32`.
  - `M16-1347`: `presented_fps=5.292`, `avg_render_total_ms=178.868`, `avg_draw_scopes_ms=2.717`, `scope_updates=27`, `scope_skips=26`.
  - `M16-1446`: `presented_fps=6.397`, `avg_render_total_ms=146.469`, `avg_draw_scopes_ms=2.813`, `scope_updates=32`, `scope_skips=32`.
- The same adaptive run stayed visually correct on repeated clips and kept the user-facing state intact, so this is a GUI-side playback win rather than just an inner-loop timing curiosity.
- Current user-facing release executable after the adaptive scope change: `platform\qt\build-release\release\MLVApp.exe`, timestamp `2026-05-29 22:23:11`, size `8791040`, SHA256 `101A8F33B7A51D6410168B4652F74AAF08CF0ABE537D00A29E1BF0C7F604C7B4`.

### Cross-checked from prior analysis

- Static scope throttling at a fixed `300 ms` was too blunt for the whole clip set. The adaptive version keeps the same idea but makes the throttle respond to the actual frame cost, which is why it improved the clips that were previously getting hammered by redraw work without forcing a blanket regression.
- The raw copy-skip probe is now a clean negative result on the active SH clips: it was worth checking, but it was not the lever this benchmark set needed.

### Needs runtime profiling

- The remaining big cost buckets are still the RBF vertical recurrence on the Shadows/Highlights path and the Dual ISO `mix_chroma` path on the clips that use it.
- The adaptive scope throttle is a GUI-side win, but it should still be watched against more varied user clips to make sure it remains a net positive outside the current smoke set.

### Ranked next steps

1. High impact / medium risk: keep the adaptive scope throttle if the next smoke batch preserves the multi-clip win and the visual gate.
2. High impact / medium risk: continue with a lower-level RBF vertical-pass optimization because that remains the dominant processing hotspot.
3. Medium impact / low risk: leave the raw copy-skip probe behind unless a new clip shows actual `avg_sh_copy_ms` cost worth reclaiming.

## 2026-05-30 - rejected RBF task-group fusion probe

### Verified locally

- Tried a task-based fusion of the RBF vertical passes and the output pass in `src\processing\rbfilter\RBFilterPlain.cpp` so the output stage could reuse the same thread team instead of starting a fresh parallel region.
- The refactor compiled, but the visible GUI smoke set regressed compared with the accepted adaptive-scope baseline:
  - `M16-1327`: `presented_fps=5.387`, `avg_render_total_ms=177.037`, `avg_llrawproc_ms=68.907`, `avg_processing_shadows_highlights_prep_ms=54.870`, `avg_draw_scopes_ms=2.463`.
  - `M16-1347`: `presented_fps=4.696`, `avg_render_total_ms=202.255`, `avg_llrawproc_ms=81.894`, `avg_processing_shadows_highlights_prep_ms=57.149`, `avg_draw_scopes_ms=3.149`.
  - `M16-1446`: `presented_fps=5.588`, `avg_render_total_ms=167.607`, `avg_llrawproc_ms=43.339`, `avg_processing_shadows_highlights_prep_ms=58.661`, `avg_draw_scopes_ms=3.500`.
- The change was reverted immediately after the benchmark because it did not outperform the simpler accepted path and it weakened the multi-clip visible GUI set.
- Current user-facing release executable after restoring the accepted path: `platform\qt\build-release\release\MLVApp.exe`, timestamp `2026-05-29 22:23:11`, size `8791040`, SHA256 `101A8F33B7A51D6410168B4652F74AAF08CF0ABE537D00A29E1BF0C7F604C7B4`.

### Cross-checked from prior analysis

- This is a useful negative result because it rules out one easy-looking way to remove an OpenMP fork/join, but it also shows the accepted vertical-pass split is already closer to the right balance than the fused task version on this VM.

### Needs runtime profiling

- If we revisit RBF again, it should be from a more algorithmic angle than team fusion.

### Ranked next steps

1. High impact / medium risk: continue looking for exact RBF vertical-pass savings that keep the current team structure or reduce the actual recurrence work.
2. High impact / medium risk: keep the adaptive scope throttle accepted unless a future clip shows it trading away more than it saves.
3. Medium impact / low risk: leave the failed task-group fusion out of further experiments unless we first find a way to preserve the full output throughput.

## 2026-05-30 - restored accepted path after RBF task-group revert

### Verified locally

- Rebuilt the user-facing release executable after reverting the task-group probe: `platform\qt\build-release\release\MLVApp.exe`, timestamp `2026-05-29 22:34:56`, size `8791040`, SHA256 `E49D3B73495D64287A407EC3D52EE75A917F9BB4DA820F0B73B1BA0F63AC09F1`.
- Fresh visible GUI smokes on the restored build stayed valid with x1 Quality, Auto Look Assist, and the expected stretch/aspect state:
  - `M16-1327`: `presented_fps=5.094`, `avg_render_total_ms=186.039`, `avg_llrawproc_ms=78.078`, `avg_processing_shadows_highlights_prep_ms=55.843`, `avg_draw_scopes_ms=2.510`.
  - `M16-1347`: `presented_fps=4.596`, `avg_render_total_ms=204.804`, `avg_llrawproc_ms=90.609`, `avg_processing_shadows_highlights_prep_ms=57.152`, `avg_draw_scopes_ms=2.761`.
  - `M16-1446`: `presented_fps=5.898`, `avg_render_total_ms=156.966`, `avg_llrawproc_ms=39.102`, `avg_processing_shadows_highlights_prep_ms=63.000`, `avg_draw_scopes_ms=2.627`.
- The restored build keeps the visual gate intact and shows the same CPU profile shape as the earlier accepted non-fused path: RBF prep and Dual ISO mix work remain the dominant buckets, while the task-group fusion itself is confirmed not worth keeping.

### Cross-checked from prior analysis

- The restored build is back to the accepted behavior class, even though the exact FPS numbers wobble with VM noise. The important thing is that the visible smoke still proves x1 Quality, settled Look Assist, and the expected stretch/aspect state.

### Needs runtime profiling

- Revisit only the remaining high-value CPU buckets: RBF vertical recurrence and Dual ISO `mix_chroma`.

### Ranked next steps

1. High impact / medium risk: return to the RBF vertical-pass algorithm itself rather than the thread-team shape if we want another gain there.
2. High impact / medium risk: continue chasing lower-level `mix_chroma` savings, but only if they survive the multi-clip GUI gate.
3. Medium impact / low risk: keep the smoke harness and launch-state validation exactly as-is because it is still catching regressions before they become misleading benchmark wins.

## 2026-05-30 - widened adaptive scope throttle accepted

### Verified locally

- Increased the adaptive scope redraw multiplier in `platform\qt\MainWindow.cpp` from `renderTotalMs * 1.2` to `renderTotalMs * 1.4` so playback repaints scopes less aggressively when the frame is already expensive.
- The rebuilt user-facing release executable is [platform\qt\build-release\release\MLVApp.exe](C:\!Layi%20Wkspc\MLV-App\platform\qt\build-release\release\MLVApp.exe), timestamp `2026-05-29 22:40:28`, size `8791040`, SHA256 `FFD88A65D0746DAA1AEBA622D4F210FC101FC54BFCF2F246DBF801086A87D0F3`.
- Fresh visible GUI smoke on the restored build stayed valid with x1 Quality, Auto Look Assist, and the expected stretch/aspect state, and it improved every clip in the current smoke set:
  - `M16-1327`: `presented_fps=6.694`, `avg_render_total_ms=139.507`, `avg_llrawproc_ms=52.537`, `avg_draw_scopes_ms=2.597`, `scope_updates=34`, `scope_skips=33`.
  - `M16-1347`: `presented_fps=6.400`, `avg_render_total_ms=146.016`, `avg_llrawproc_ms=55.922`, `avg_draw_scopes_ms=3.000`, `scope_updates=32`, `scope_skips=32`.
  - `M16-1446`: `presented_fps=7.178`, `avg_render_total_ms=126.806`, `avg_llrawproc_ms=27.056`, `avg_draw_scopes_ms=3.319`, `scope_updates=36`, `scope_skips=36`.
- The visual-state gate remained intact on all clips, so this is a GUI playback win rather than a quality regression hiding behind a timing gain.

### Cross-checked from prior analysis

- This change is materially better than the accepted `1.2` multiplier because it improved the full multi-clip set instead of helping one clip while leaving the others noisy.
- The rest of the accepted playback state remains unchanged: x1 Quality, settled Look Assist, cleared experimental environment, and the same launch-thread cap behavior.

### Needs runtime profiling

- Watch whether the wider interval still behaves well on more varied clips, but the current evidence is strong enough to keep it as the default adaptive scope policy.

### Ranked next steps

1. High impact / low risk: keep the widened adaptive scope throttle as the current accepted GUI-side win.
2. High impact / medium risk: return to the RBF vertical-pass algorithm itself rather than thread-team shape if we want another gain there.
3. Medium impact / low risk: leave the smoke harness and visual-state validation in place so future GUI gains are measured against the same launch behavior.

## 2026-05-30 - RBF zero-fill prep cleanup accepted

### Verified locally

- Replaced the `std::fill(..., 0.0f)` zeroing of the `RBFilterPlain` working buffers with `memset` byte-zero fills in `src\processing\rbfilter\RBFilterPlain.cpp`. This does not change the filter math; it only changes how the already-zeroed setup buffers are cleared each frame.
- Focused regression slice remained green after the prep-path change:
  - `DualIsoPipeline.*`
  - `ProcessingFilters.RbfFilter*`
- Fresh visible GUI smoke on the rebuilt release stayed valid with x1 Quality, Auto Look Assist, and the expected stretch/aspect state:
  - `M16-1327`: `presented_fps=5.094`, `avg_render_total_ms=184.784`, `avg_llrawproc_ms=68.176`, `avg_processing_shadows_highlights_prep_ms=60.196`.
  - `M16-1347`: `presented_fps=4.891`, `avg_render_total_ms=195.327`, `avg_llrawproc_ms=77.306`, `avg_processing_shadows_highlights_prep_ms=61.082`.
  - `M16-1446`: `presented_fps=6.491`, `avg_render_total_ms=144.954`, `avg_llrawproc_ms=30.354`, `avg_processing_shadows_highlights_prep_ms=59.338`.
- Compared with the immediately preceding release-state baseline in this investigation, the prep-path cleanup is a real multi-clip GUI win: `M16-1327` improved, `M16-1347` held steady enough to remain non-regressive, and `M16-1446` improved substantially.
- Current user-facing release executable after the RBF prep cleanup: `platform\qt\build-release\release\MLVApp.exe`, timestamp `2026-05-29 22:08:21`, size `8791040`, SHA256 `DFB00BAB2CD7B41CB12793915367F5969313D65B41D8D054E51F64F347C9808D`.

### Cross-checked from prior analysis

- The prep-path change is lower risk than the rejected chroma overlap because it does not alter traversal, scheduling, or pixel math. It only changes the zero-initialization mechanism for already-zero scratch buffers.
- The win lines up with the established hot buckets: RBF prep remains a substantial part of the frame cost on the clips that use it, so reducing setup work can show up in GUI playback.

### Needs runtime profiling

- `avg_processing_shadows_highlights_prep_ms` is still large enough that more structural work may be worth it, but the current cleanup proves the setup path still matters.
- `mix_chroma` remains a live hotspot on the Dual ISO clips that use it, so there is still room to keep pushing in parallel with the RBF path.

### Ranked next steps

1. High impact / low risk: continue to look for byte-identical RBF prep savings, especially around work-buffer setup and per-frame copies.
2. High impact / medium risk: revisit Dual ISO `mix_chroma` only with a lower-level structural idea, since the naive parallel overlap already proved unhelpful.
3. Medium impact / low risk: keep using the visible GUI smoke harness as the acceptance gate, because it continues to catch both visual drift and misleading inner-loop wins.

## 2026-05-30 - rejected adaptive scope throttle 1.6 probe

### Verified locally

- Widened the adaptive scope redraw multiplier in `platform\qt\MainWindow.cpp` from `renderTotalMs * 1.4` to `renderTotalMs * 1.6` and rebuilt the user-facing release tree to test whether an even lazier scope cadence would help GUI playback.
- The rebuilt user-facing release executable is `platform\qt\build-release\release\MLVApp.exe`, timestamp `2026-05-29 22:52:24`, size `8791040`, SHA256 `56DAA41F8AB9E7E20796AAA745879F514E16C51CA5A5619FD924354FBC7E40F8`.
- The visible x1 Quality smoke gate still passed, including settled Look Assist and the expected stretch/aspect state, but playback regressed on every clip relative to the accepted 1.4 result:
  - `M16-1327`: `presented_fps=4.593`, `avg_render_total_ms=205.043`, `avg_llrawproc_ms=84.326`, `avg_draw_scopes_ms=2.565`.
  - `M16-1347`: `presented_fps=4.393`, `avg_render_total_ms=214.409`, `avg_llrawproc_ms=90.205`, `avg_draw_scopes_ms=3.068`.
  - `M16-1446`: `presented_fps=5.585`, `avg_render_total_ms=169.268`, `avg_llrawproc_ms=41.571`, `avg_draw_scopes_ms=2.732`.
- The probe was reverted immediately back to `renderTotalMs * 1.4` after the benchmark, because the extra suppression did not buy back meaningful frame rate and it hurt the current multi-clip GUI baseline.

### Cross-checked from prior analysis

- This is a clean negative result for the throttle line: the 1.4 interval still looks like the best balance we have found so far on this VM.
- The clip-by-clip shape matters here. The 1.6 change did not just wobble on one clip; it moved the whole visible set in the wrong direction.

### Needs runtime profiling

- If we ever revisit the scope throttle again, it should be from a different angle than just making the interval larger.

### Ranked next steps

1. High impact / low risk: keep `renderTotalMs * 1.4` as the accepted adaptive scope policy.
2. High impact / medium risk: resume work on the remaining CPU hotspots, especially the RBF vertical recurrence and Dual ISO `mix_chroma`.
3. Medium impact / low risk: keep the smoke harness and launch-state validation untouched so future changes stay comparable to these clips.

# 2026-05-30 - rejected RBFilterPlain aliasing hints

## Verified locally

- Added `__restrict` aliasing hints to `src\processing\rbfilter\RBFilterPlain.cpp` / `.h` around the hot recursive bilateral filter buffers, then benchmarked the visible x1 Quality GUI smoke set with the usual settled Auto Look Assist gate.
- The user-facing release executable was rebuilt at `platform\qt\build-release\release\MLVApp.exe`, timestamp `2026-05-30 00:26:10`, size `8790528`, SHA256 `2667950E9C0468BCD312E5EF73A3D042258BC675D03CBC8F70E8D1DA59494BB0`.
- The smoke stayed visually valid, but it regressed hard versus the accepted baseline:
  - `M16-1327`: `presented_fps=4.618`, `avg_render_total_ms=206.432`, `avg_llrawproc_ms=85.622`, `avg_processing_shadows_highlights_prep_ms=66.378`, `avg_mix_chroma_ms=38.811`.
  - `M16-1347`: `presented_fps=4.499`, `avg_render_total_ms=209.639`, `avg_llrawproc_ms=85.111`, `avg_processing_shadows_highlights_prep_ms=62.250`, `avg_mix_chroma_ms=37.944`.
  - `M16-1446`: `presented_fps=5.488`, `avg_render_total_ms=172.205`, `avg_llrawproc_ms=46.727`, `avg_processing_shadows_highlights_prep_ms=65.386`, `avg_mix_chroma_ms=0.000`.
- The aliasing hints were reverted immediately; the source is back on the prior accepted shape.

## Cross-checked from prior analysis

- The RBF bucket remains sensitive to apparently harmless compiler-facing changes. This probe showed that `restrict` hints do not automatically translate into better GUI playback on this VM.
- The accepted row-parallel Dual ISO chroma-copy change remains the stronger kept improvement in the chroma bucket.

## Needs runtime profiling

- The next useful pass should stay in a different seam than this one: either the RBF vertical recurrence itself or a GUI-side presentation boundary that actually removes visible frame wait.

## Ranked next steps

1. High impact / medium risk: continue with the remaining RBF vertical recurrence only if the probe is structurally different from these aliasing or unroll attempts.
2. High impact / medium risk: inspect the GUI presentation boundary around `drawFrameReady()` and `timerFrameEvent()` for a safe split that keeps visible playback smooth.
3. Medium impact / low risk: keep the smoke harness and visual-state checks unchanged so every new probe stays comparable.

# 2026-05-30 - rejected RBFilterPlain reserveMemory calloc cleanup

## Verified locally

- Tried replacing the `reserveMemory(...)` zero-initialized allocations in `src\processing\rbfilter\RBFilterPlain.cpp` with `calloc(...)` and then benchmarked the visible GUI smoke path again. The change was byte-safe in intent, but it did not improve the actual x1 Quality playback loop on the user-facing release executable.
- Rebuilt the user-facing release executable at `platform\qt\build-release\release\MLVApp.exe`, timestamp `2026-05-30 00:12:54`, size `8790528`, SHA256 `0C87DBF07D1EFB338F2127B24F0C1DA3DEF4D19ABF75C64666107138568B45FE`.
- Re-ran the visible GUI smoke set with the same launch shape and validation gate. The build still passed the visual-state checks and settled Auto Look Assist, but the FPS was slightly worse than the immediately prior accepted baseline:
  - `M16-1327`: `presented_fps=4.868`, `avg_render_total_ms=193.385`, `avg_llrawproc_ms=76.385`, `avg_mix_chroma_ms=34.308`, `avg_chroma_copy_ms=4.333`.
  - `M16-1347`: `presented_fps=4.873`, `avg_render_total_ms=194.897`, `avg_llrawproc_ms=80.795`, `avg_mix_chroma_ms=36.103`, `avg_chroma_copy_ms=4.256`.
  - `M16-1446`: `presented_fps=5.620`, `avg_render_total_ms=166.733`, `avg_llrawproc_ms=47.956`, `avg_mix_chroma_ms=0.000`, `avg_chroma_copy_ms=0.000`.
- Because the numbers were down across the set, the `calloc` cleanup was reverted immediately back to the original `new[]` zero-initialized allocation form.

## Cross-checked from prior analysis

- The accepted row-parallel copy change in `src\mlv\llrawproc\dualiso.c` remains the best currently kept gain in this bucket.
- The existing `renderTotalMs * 1.4` adaptive scope throttle also remains a real GUI-side win; the `calloc` probe did not add to it.

## Needs runtime profiling

- If we keep pushing `RBFilterPlain`, the next useful probe should be inside the hot recurrence or output path itself, not the allocation wrapper around it.

## Ranked next steps

1. High impact / medium risk: keep the accepted dual-ISO row-parallel copy and look for a true `mix_chroma` reduction.
2. High impact / medium risk: return to the RBF vertical recurrence with a deeper algorithmic idea instead of a setup tweak.
3. Medium impact / low risk: keep the smoke harness unchanged so future comparisons remain apples-to-apples.

## 2026-05-30 - accepted row-parallel Dual ISO chroma copy

### Verified locally

- Parallelized the two scratch-buffer copies in `src\mlv\llrawproc\dualiso.c` so `fullres_smooth` and `halfres_smooth` are copied row-by-row under OpenMP before chroma smoothing runs. This stays byte-identical to the prior copy path and only changes how the temporary data movement is scheduled.
- Rebuilt the user-facing release executable at `platform\qt\build-release\release\MLVApp.exe`, timestamp `2026-05-29 23:23:57`, size `8791552`, SHA256 `9F36CDD20C45694DD50CB91BB86DD44D116F690480EF3A3D2C502B5CA9413DB4`.
- Re-ran the visible GUI smoke set with x1 Quality, settled Auto Look Assist, and the same launch-state validation. All three clips passed the visual gate:
  - `M16-1327`: `presented_fps=5.365`, `avg_render_total_ms=174.372`, `avg_llrawproc_ms=68.488`, `avg_mix_chroma_ms=30.419`, `avg_chroma_copy_ms=3.930`.
  - `M16-1347`: `presented_fps=5.365`, `avg_render_total_ms=174.558`, `avg_llrawproc_ms=69.884`, `avg_mix_chroma_ms=32.698`, `avg_chroma_copy_ms=3.140`.
  - `M16-1446`: `presented_fps=6.245`, `avg_render_total_ms=150.820`, `avg_llrawproc_ms=37.080`, `avg_mix_chroma_ms=0.000`, `avg_chroma_copy_ms=0.000` because chroma smoothing was not active on that clip.
- Compared with the immediately preceding reverted build, the copy-parallel change is a real GUI win on the clips that exercise chroma smoothing. The remaining hot work in that bucket is now the chroma smoother itself, not just the temporary copy setup.

### Cross-checked from prior analysis

- This change is intentionally narrow: it does not alter the chroma-smoothing math, the mix curve cache, or the final blend path.
- The visible smoke still shows the same stretch/aspect and Look Assist state, so the speedup is not hiding a quality regression.

### Needs runtime profiling

- The copy parallelism looks worth keeping, but `avg_chroma_fullres_ms` and `avg_chroma_halfres_ms` are still the dominant costs on the clips that hit `mix_chroma`, so that is the next bucket if we keep pushing this path.

### Ranked next steps

1. High impact / medium risk: keep the row-parallel chroma copy and look for one more reduction inside the smoother itself.
2. High impact / medium risk: return to the RBF vertical recurrence only if we want a second independent gain beyond the copy win.
3. Medium impact / low risk: keep the smoke harness and visual-state gate unchanged so future wins stay comparable to the same user-facing launch path.

## 2026-05-30 - rejected fused chroma-copy row walk

### Verified locally

- Tried fusing the two row-copy loops inside `src\mlv\llrawproc\dualiso.c` into a single OpenMP row walk. The change stayed byte-identical in intent, but the visible GUI smoke regressed badly on the chroma-heavy clips, so it was reverted immediately.
- Rebuilt the user-facing release executable after the revert. Current release exe: `platform\qt\build-release\release\MLVApp.exe`, timestamp `2026-05-29 23:33:33`, size `8791552`, SHA256 `AB94FEBA577BA773E76015B5068B504F81CBD0F9EFA40A85D02C60E2389A25FF`.
- Restored visible GUI smoke on the reverted build:
  - `M16-1327`: `presented_fps=4.993`, `avg_render_total_ms=187.050`, `avg_llrawproc_ms=77.300`, `avg_mix_chroma_ms=34.375`, `avg_chroma_copy_ms=3.950`.
  - `M16-1347`: `presented_fps=4.742`, `avg_render_total_ms=197.835`, `avg_llrawproc_ms=80.658`, `avg_mix_chroma_ms=34.525`, `avg_chroma_copy_ms=3.738`.
  - `M16-1446`: `presented_fps=5.620`, `avg_render_total_ms=165.578`, `avg_llrawproc_ms=45.356`, `avg_mix_chroma_ms=0.000`, `avg_chroma_copy_ms=0.000`.
- The restored numbers show the branch is back on the accepted row-parallel copy shape, but the fused walk was a regression and should stay out.

### Cross-checked from prior analysis

- The regression lined up with the earlier warning pattern in this repo: a small-looking scheduling simplification can still lose on the VM when it changes how the chroma smoothing work lands on cores and cache.

### Needs runtime profiling

- Keep the current row-parallel copy, and only revisit chroma smoothing with a stronger algorithmic idea rather than a tighter loop wrapper.

### Ranked next steps

1. High impact / medium risk: keep the accepted row-parallel copy and look for a real `mix_chroma` reduction inside the smoother itself.
2. High impact / medium risk: return to the RBF vertical recurrence if we want a second independent win.
3. Medium impact / low risk: leave the smoke harness unchanged so the next benchmark stays comparable to this exact launch path.

## 2026-05-30 - rejected chroma_smooth 2x2 row-offset hoist

### Verified locally

- Rolled the 2x2 `chroma_smooth` row-offset hoist back out of `src\mlv\llrawproc\chroma_smooth.c` after the visible GUI smoke set showed a regression on the chroma-heavy clips. The accepted row-parallel `dualiso.c` copy change remains in place.
- Rebuilt the user-facing release executable at `platform\qt\build-release\release\MLVApp.exe`, timestamp `2026-05-29 23:46:40`, size `8794112`, SHA256 `F23E331001C0073082E4EE116B1F0B91819F82F093AAE88A678F8C73E915F8AC`.
- Re-ran the visible GUI smoke set with x1 Quality, settled Auto Look Assist, and the same launch-state validation. The restored build still passed the visual gate, but the overall shape stayed below the earlier accepted chroma-copy baseline:
  - `M16-1327`: `presented_fps=4.619`, `avg_render_total_ms=202.973`, `avg_llrawproc_ms=81.514`, `avg_mix_chroma_ms=38.487`, `avg_chroma_copy_ms=4.676`.
  - `M16-1347`: `presented_fps=4.580`, `avg_render_total_ms=204.427`, `avg_llrawproc_ms=86.141`, `avg_mix_chroma_ms=35.459`, `avg_chroma_copy_ms=3.873`.
  - `M16-1446`: `presented_fps=5.621`, `avg_render_total_ms=168.844`, `avg_llrawproc_ms=48.756`, `avg_mix_chroma_ms=0.000`.
- The reverted file is now back to the original indexing style, so this probe should not stay in the tree.

### Cross-checked from prior analysis

- The rejected result is consistent with the earlier pattern: small arithmetic hoists inside the chroma smoother can still lose on this VM when they perturb how the hot loop lands in cache and on the worker threads.
- The accepted row-parallel scratch-copy win in `dualiso.c` remains the best known improvement in this bucket.

### Needs runtime profiling

- The next useful look is still the same one: either a true `mix_chroma` algorithmic reduction or the RBF vertical recurrence. The `chroma_smooth` row-offset hoist itself is not worth keeping.

### Ranked next steps

1. High impact / medium risk: keep the accepted row-parallel copy in `dualiso.c` and look for a real `mix_chroma` cut.
2. High impact / medium risk: inspect the RBF vertical passes for a safe reduction that does not change image output.
3. Medium impact / low risk: keep the smoke harness and launch-state validation exactly as they are so future numbers stay comparable.

## 2026-05-30 - rejected `__restrict` aliasing-hint probe

### Verified locally

- Tried adding `__restrict` hints to the Dual ISO chroma smoothing interfaces in `src\mlv\llrawproc\pixelproc.h`, `src\mlv\llrawproc\pixelproc.c`, `src\mlv\llrawproc\dualiso.c`, and `src\mlv\llrawproc\chroma_smooth.c`. The build remained healthy, but the visible GUI smoke set did not show a consistent win, so the hinting change was backed back out.
- The rebuilt user-facing release executable is current at `platform\qt\build-release\release\MLVApp.exe`, timestamp `2026-05-30 00:39:59`, size `8790528`, SHA256 `328C9136E1D0BA6DBD2F475BABEFC82813A5911D8F301A8C8BA4514A3EB4978A`.
- The latest benchmark receipts before the revert showed mixed results rather than a net gain:
  - `M16-1327`: `presented_fps=4.619`, `avg_render_total_ms=204.108`, `avg_llrawproc_ms=86.432`, `avg_mix_chroma_ms=40.324`.
  - `M16-1347`: `presented_fps=4.499`, `avg_render_total_ms=209.028`, `avg_llrawproc_ms=88.250`, `avg_mix_chroma_ms=38.083`.
  - `M16-1446`: `presented_fps=5.996`, `avg_render_total_ms=157.021`, `avg_llrawproc_ms=41.688`, `avg_mix_chroma_ms=0.000`.
- The clear takeaway is that this probe does not move the real hot buckets enough to justify keeping it. The earlier row-parallel scratch-copy win in `dualiso.c` stays the better change.

### Cross-checked from prior analysis

- `avg_draw_total_ms` on the same smoke receipts is only about `26-28 ms`, while `avg_llrawproc_ms` and `avg_mix_chroma_ms` remain much larger on the chroma-heavy clips. That is why presentation-scale tweaks keep disappointing here: the app is still spending most of its time upstream of the final draw.

### Needs runtime profiling

- If we keep pushing this branch, the next useful target is still an actual reduction inside the chroma smoothing work or the RBF vertical recurrence. A pure aliasing-hint pass is not enough on this VM.

### Ranked next steps

1. High impact / medium risk: keep the accepted row-parallel copy in `dualiso.c` and look for a real `mix_chroma` reduction.
2. High impact / medium risk: inspect the RBF vertical passes for a safe reduction.
3. Low impact / low risk: leave the smoke harness alone so future measurements remain comparable.

## 2026-05-30 - playback-preview split for GUI playback

### Verified locally

- Added an explicit playback-preview processing path so GUI playback can take a cheaper processing route without changing export fidelity. The preview signal now flows from `platform\qt\MainWindow.cpp` into `platform\qt\RenderFrameThread.cpp`, and the processed8 prefetch worker in `src\mlv\video_mlv.c` also marks itself as preview-mode before rendering.
- The hot-path playback gate now treats preview mode as a first-class signal instead of relying on scale factor alone. In `src\processing\raw_processing.c`, the preview path allows the lighter tone-adjustment handling while skipping the expensive shadows/highlights prep that export still keeps.
- Rebuilt the user-facing release executable after the header fix. Current release exe: `platform\qt\build-release\release\MLVApp.exe`, timestamp `2026-05-30 01:25:39`, size `8792576`, SHA256 `249C42F680AE27EAAC203CC9AC726F82688E04163770E3C3394E761BD888E116`.
- Re-ran the visible GUI smoke set on the non-headless release executable with x1 Quality and settled Auto Look Assist:
  - `M16-1327`: `presented_fps=6.610`, `avg_render_total_ms=141.491`, `avg_llrawproc_ms=83.566`, `avg_processing_shadows_highlights_prep_ms=0.000`, `avg_mix_chroma_ms=37.717`.
  - `M16-1347`: `presented_fps=6.364`, `avg_render_total_ms=146.118`, `avg_llrawproc_ms=86.431`, `avg_processing_shadows_highlights_prep_ms=0.000`, `avg_mix_chroma_ms=38.255`.
  - `M16-1446`: `presented_fps=8.978`, `avg_render_total_ms=102.500`, `avg_llrawproc_ms=41.014`, `avg_processing_shadows_highlights_prep_ms=0.000`, `avg_mix_chroma_ms=0.000`.
- The important result is that the architecture split moved work out of the playback path without touching export behavior. That is why playback now benefits in the way scale factor alone never could.

### Cross-checked from prior analysis

- The earlier rejected probes were mostly loop-shape or hinting changes. This one differs because it changes which processing policy the playback path actually asks for, so the measured win lines up with the design change instead of with a scheduling accident.

### Needs runtime profiling

- Keep export on the existing full-fidelity path and continue to treat playback preview as the place for CPU reduction experiments.

### Ranked next steps

1. High impact / medium risk: keep the preview split and see whether the remaining `mix_chroma` work can be reduced further without changing export output.
2. High impact / medium risk: revisit the RBF vertical recurrence with the same playback-vs-export boundary in place.
3. Medium impact / low risk: keep the smoke harness and launch settings unchanged so future comparisons stay meaningful.

## 2026-05-30 - rejected chroma_smooth aliasing-hint probe

### Verified locally

- Tried adding `restrict` qualifiers to the `chroma_smooth` generated helpers in `src\mlv\llrawproc\chroma_smooth.c`. The change compiled, but it did not produce a credible improvement over the already-accepted playback-preview split, so the hinting change was removed again.
- Rebuilt the user-facing release executable after the revert. Current release exe: `platform\qt\build-release\release\MLVApp.exe`, timestamp `2026-05-30 01:41:54`, size `8792576`, SHA256 `E03FA28946A32F14B168E892522859AF62BDEEEB39E1EE0AE6ABE95F0152A111`.
- The directional smoke receipts during the probe stayed in the same band as the accepted preview split rather than moving decisively higher, so this should not be treated as a real gain.

### Cross-checked from prior analysis

- The accepted architecture split already removed the expensive shadows/highlights prep from playback. That left `mix_chroma` as the real remaining chroma bucket, and a compiler hint inside the smoother was not enough to move it.

### Needs runtime profiling

- Keep the current playback-preview architecture split and continue looking for an actual reduction inside `mix_chroma` or the RBF vertical passes.

### Ranked next steps

1. High impact / medium risk: keep the preview split and look for a real `mix_chroma` reduction.
2. High impact / medium risk: revisit the RBF vertical recurrence with the same playback-vs-export boundary in place.
3. Low impact / low risk: leave the smoke harness unchanged so future measurements remain comparable.

## 2026-05-30 - live playback pink cast is preview-only

### Verified locally

- Exported the current frame from `M16-1446.MLV` through the built-in **Export Current Frame** dialog and saved it as `C:\!Layi Wkspc\MLV-App\.claude-state\profiling\20260530-export-dialog-check\out\m16-1446-preview-frame.png`. The exported PNG is clean: no pink band, no top-bar artifact, and the image content matches the clip frame as expected.
- Captured the live playback surface from the same clip while transport was running. That capture still shows a broad pink cast across the rendered video area, strongest near the top but visibly washing more than just a thin stripe.
- The playback smoke telemetry for the same sessions reports `zebras=false`, so the band is not the explicit zebra overlay.
- The clean export plus banded live playback proves the source frame is fine and the artifact is in the playback presentation path, not in export or decode.

### Cross-checked from prior analysis

- The preview path already differs from export: playback can present a pre-scaled or borrowed RGB8 buffer, while export uses `getMlvProcessedFrame8(...)` and a separate scaling path. That makes the preview handoff/presentation code the first place to inspect.

### Needs runtime profiling

- The next useful probe is to narrow whether the cast is coming from the borrowed playback-scaled buffer, the presentation cache, or the viewport upload/composition path.

### Ranked next steps

1. High impact / medium risk: inspect the playback presentation handoff around `playbackScaledImage8` and `preparedBorrowedImage` against the live-band case.
2. Medium impact / low risk: compare the viewport/presentation path with a forced owned-copy presentation of the same preview frame.
3. Medium impact / low risk: keep export as the control path when testing so we can distinguish source bugs from preview bugs quickly.

## 2026-05-30 - direct8 preview serial fallback clears the pink-band signature

### Verified locally

- Added a narrow preview-only serial fallback in `src\processing\raw_processing.c` so the direct8 preview renderer drops to one thread when playback preview is active and creative adjustments are present. Export remains on the existing full path.
- Rebuilt the user-facing release executable at `platform\qt\build-release\release\MLVApp.exe`, timestamp `2026-05-30 03:23:00`, size `8792064`, SHA256 `BD2060B4F565D8C2F67C425735C40A8882907E313AE86D094657549E556D8A0A`.
- Re-ran the visible GUI smoke set on `M16-1446.MLV` with x1 Quality, settled Auto Look Assist, and the same launch-state validation. The run stayed valid and the playback throughput remained healthy:
  - `presented_fps=11.103`
  - `avg_render_total_ms=155.708`
  - `avg_llrawproc_ms=42.978`
  - `avg_processed8_ms=84.876`
  - `avg_draw_total_ms=27.775`
- The earlier known-bad capture from `20260530-bandprobe` showed a strong top-of-frame pink signature in `playing_S5_processed8_f100`:
  - top rows average roughly `R 230, G 180, B 214`
  - mid-frame rows average roughly `R 63, G 59, B 49`
- The new captured direct8 frame from the serial-fallback build does not show that pink signature:
  - `playing_S5_processed8_f190` top rows average roughly `R 105, G 111, B 103`
  - mid-frame rows average roughly `R 46, G 37, B 24`
  - the top rows are still brighter than the mid-frame content, but they are no longer magenta-heavy
- That means the visible playback artifact is not present in the new captured preview buffer, and the serial fallback is a credible fix for the playback-only band on this clip.

### Cross-checked from prior analysis

- The earlier playback-only band was already proven not to exist in export. This new comparison narrows the remaining issue to the preview direct8 path rather than the source frame or export pipeline.
- The performance cost of the serial fallback is acceptable on this VM for the affected clip shape, because the smoke remains above the earlier 3-4 fps baseline and now avoids the obvious color defect.

### Needs runtime profiling

- Keep an eye on whether the serial fallback is needed for every preview-adjusted clip or only for the specific creative-adjustment shape that still triggers the artifact.

### Ranked next steps

1. High impact / medium risk: keep the serial fallback if the same visual check remains clean across the other smoke clips.
2. Medium impact / low risk: if needed, refine the fallback condition so it only applies to the exact preview adjustment shapes that still reproduce the band.
3. Low impact / low risk: keep export untouched and continue using it as the control path.

## 2026-05-30 - AVX2 aliasing hints on Dual ISO row kernels

### Verified locally

- Added `__restrict` aliasing hints to the AVX2 Dual ISO row kernels in [`src/mlv/llrawproc/dualiso_avx2.inc`](C:\!Layi%20Wkspc%5CMLV-App%5Csrc%5Cmlv%5Cllrawproc%5Cdualiso_avx2.inc) without changing the arithmetic or the call graph. The touched kernels are the row-level `overexposed_mark`, `overexposed_blur`, `fullres_reconstruction_bright_row`, `mix_images_row`, `final_blend_row`, and the AVX2 helper copies.
- Rebuilt the user-facing release executable at [`platform/qt/build-release/release/MLVApp.exe`](C:\!Layi%20Wkspc%5CMLV-App%5Cplatform%5Cqt%5Cbuild-release%5Crelease%5CMLVApp.exe), timestamp `2026-05-30 04:06:30`, SHA256 `1E83744A54D1793968FB0D7425B7A0EF79F1938FB52E612754BE4FEFE356856B`.
- Re-ran the visible GUI smoke set with x1 Quality, settled Auto Look Assist, and the same launch-state validation:
  - `M16-1327`: `presented_fps=9.238`, `avg_render_total_ms=196.459`, `avg_llrawproc_ms=66.365`, `avg_processed8_ms=104.541`, `avg_draw_total_ms=28.676`, `avg_mix_chroma_ms=33.635`, `avg_final_blend_ms=6.351`
  - `M16-1347`: `presented_fps=7.859`, `avg_render_total_ms=118.508`, `avg_llrawproc_ms=73.556`, `avg_processed8_ms=117.079`, `avg_draw_total_ms=27.095`, `avg_mix_chroma_ms=35.651`, `avg_final_blend_ms=7.619`
  - `M16-1446`: `presented_fps=10.621`, `avg_render_total_ms=79.318`, `avg_llrawproc_ms=37.024`, `avg_processed8_ms=77.376`, `avg_draw_total_ms=30.176`, `avg_mix_chroma_ms=0.000`, `avg_final_blend_ms=6.482`
- The change is output-preserving so far and the smoke gate still passes the visual-state checks. On the chroma-heavy clips, `mix_chroma` remains the real hot bucket; on `M16-1446`, the current load is in the broader Dual ISO mix/final-blend path rather than chroma smoothing.

### Cross-checked from prior analysis

- The recent playback-preview split remains the main architectural win. This pass did not alter export behavior or any of the preview-vs-export gates.
- The earlier `mix_chroma` and RBF trails are still useful context, but the latest smoke confirms that the best target shifts by clip shape. On this smoke set, the hottest work is still inside the Dual ISO full20 pipeline.

### Needs runtime profiling

- Keep watching whether the aliasing hints stay neutral on other receipts before we treat them as a durable optimization.
- If the next pass still targets Dual ISO, the next useful investigation seam is the mix/final-blend kernels themselves, not the preview plumbing.

### Ranked next steps

1. High impact / medium risk: keep the current preview split and continue trimming the Dual ISO mix/final-blend kernels if the next smoke still shows them as dominant.
2. Medium impact / low risk: watch for any regression in the chroma-heavy clips before promoting the aliasing hints as permanent.
3. Low impact / low risk: leave the GUI smoke harness and launch-state validation unchanged so the next comparison stays apples-to-apples.

## 2026-05-30 - rejected chroma_smooth row-offset hoist

### Verified locally

- Probed a row-offset hoist inside `src\mlv\llrawproc\chroma_smooth.c` to reuse adjacent sample work across the 2x2 chroma smoother. The change compiled, but the visible GUI smoke regressed on the chroma-heavy clips, so it was backed out.
- Rebuilt the user-facing release executable for the probe and benchmarked it through the standard visible GUI smoke set with x1 Quality, settled Auto Look Assist, and the same launch-state validation.
- Smoke results from the hoist probe:
  - `M16-1327`: `presented_fps=8.218`, `avg_render_total_ms=112.045`, `avg_llrawproc_ms=71.621`, `avg_mix_chroma_ms=34.621`, `avg_chroma_copy_ms=4.152`, `avg_chroma_fullres_ms=15.636`, `avg_chroma_halfres_ms=14.818`
  - `M16-1347`: `presented_fps=7.244`, `avg_render_total_ms=129.483`, `avg_llrawproc_ms=84.069`, `avg_mix_chroma_ms=38.603`, `avg_chroma_copy_ms=4.276`, `avg_chroma_fullres_ms=17.138`, `avg_chroma_halfres_ms=17.190`
  - `M16-1446`: `presented_fps=11.580`, `avg_render_total_ms=150.645`, `avg_llrawproc_ms=40.097`, `avg_mix_chroma_ms=0.000`, `avg_chroma_copy_ms=0.000`, `avg_chroma_fullres_ms=0.000`, `avg_chroma_halfres_ms=0.000`

### Cross-checked from prior analysis

- Compared with the accepted Dual ISO aliasing-hint baseline, the hoist probe lost meaningful playback throughput on the chroma-heavy clips and did not justify the extra loop shape complexity.
- The chroma smoother is not the right next seam for a low-risk playback win when the preview split already removes the bigger upstream costs.

### Needs runtime profiling

- Keep the chroma smoother unchanged and look for wins in the Dual ISO blend kernels instead.

### Ranked next steps

1. High impact / medium risk: keep trimming the Dual ISO mix/final-blend kernels on the playback preview path.
2. Medium impact / low risk: keep the smoke harness unchanged so future comparisons stay apples-to-apples.
3. Low impact / low risk: leave export untouched and continue using it as the control path.

## 2026-05-30 - rejected Dual ISO final_blend prefetch probes

### Verified locally

- Probed `__builtin_prefetch` inside `src\mlv\llrawproc\dualiso_avx2.inc` in the `final_blend_row_avx2` loop. I tried a broad version first, then narrowed it to only the smoother buffers and a shorter lead distance. Both builds compiled and both passed the GUI smoke visual gate, but neither beat the accepted aliasing-hint baseline.
- Broad prefetch smoke:
  - `M16-1327`: `presented_fps=8.333`, `avg_render_total_ms=109.567`, `avg_llrawproc_ms=66.851`, `avg_mix_chroma_ms=32.284`, `avg_chroma_copy_ms=3.537`, `avg_chroma_fullres_ms=14.821`, `avg_chroma_halfres_ms=13.925`
  - `M16-1347`: `presented_fps=8.485`, `avg_render_total_ms=107.794`, `avg_llrawproc_ms=67.441`, `avg_mix_chroma_ms=33.735`, `avg_chroma_copy_ms=3.456`, `avg_chroma_fullres_ms=15.912`, `avg_chroma_halfres_ms=14.368`
  - `M16-1446`: `presented_fps=12.715`, `avg_render_total_ms=134.686`, `avg_llrawproc_ms=33.873`, `avg_mix_chroma_ms=0.000`, `avg_chroma_copy_ms=0.000`, `avg_chroma_fullres_ms=0.000`, `avg_chroma_halfres_ms=0.000`
- Narrow prefetch smoke:
  - `M16-1327`: `presented_fps=8.117`, `avg_render_total_ms=112.815`, `avg_llrawproc_ms=71.323`, `avg_mix_chroma_ms=34.938`, `avg_chroma_copy_ms=4.108`, `avg_chroma_fullres_ms=16.523`, `avg_chroma_halfres_ms=14.308`
  - `M16-1347`: `presented_fps=7.470`, `avg_render_total_ms=123.650`, `avg_llrawproc_ms=78.817`, `avg_mix_chroma_ms=36.617`, `avg_chroma_copy_ms=3.850`, `avg_chroma_fullres_ms=16.500`, `avg_chroma_halfres_ms=16.250`
  - `M16-1446`: `presented_fps=10.110`, `avg_render_total_ms=86.296`, `avg_llrawproc_ms=40.889`, `avg_mix_chroma_ms=0.000`, `avg_chroma_copy_ms=0.000`, `avg_chroma_fullres_ms=0.000`, `avg_chroma_halfres_ms=0.000`

### Cross-checked from prior analysis

- The accepted aliasing-hint baseline was still stronger on the chroma-heavy clips overall. The prefetch ideas moved work around, but they did not clear the no-regression bar for multi-clip playback.

### Needs runtime profiling

- Keep the Dual ISO kernel shape stable for now and look for a different low-risk seam, rather than more prefetch tuning in `final_blend_row_avx2`.

### Ranked next steps

1. High impact / medium risk: step away from prefetch and revisit the actual `mix_chroma` blend math or a different cache-locality seam.
2. Medium impact / low risk: keep the current smoke harness and launch settings unchanged.
3. Low impact / low risk: leave export untouched and continue using it as the control path.

## 2026-05-30 - 2x2 chroma smoother row-pointer cleanup

### Verified locally

- Tightened the `chroma_smooth_2x2` inner loops in [`src/mlv/llrawproc/chroma_smooth.c`](C:\!Layi Wkspc\MLV-App\src\mlv\llrawproc\chroma_smooth.c) so the hot row math uses precomputed row pointers instead of recomputing `x + y*w` for every sample access. The 2x2 smoother is the one exercised by the current x1 Quality smoke path.
- Rebuilt the user-facing release executable at [`platform/qt/build-release/release/MLVApp.exe`](C:\!Layi Wkspc\MLV-App\platform\qt\build-release\release\MLVApp.exe), timestamp `2026-05-30 06:36:56`, SHA256 `7CCF2918B3C3C8362CC0948EB888A2D7E82D9E6CF539848F7A1C4F0422E5639C`.
- Re-ran the visible GUI smoke set with x1 Quality, settled Auto Look Assist, and the same launch-state validation. The visual gate still passed on all three clips:
  - `M16-1327`: `presented_fps=8.69`, `avg_render_total_ms=105.94`, `avg_llrawproc_ms=64.42`, `avg_mix_chroma_ms=30.78`, `avg_chroma_copy_ms=3.82`, `avg_chroma_fullres_ms=14.02`, `avg_chroma_halfres_ms=12.94`
  - `M16-1347`: `presented_fps=8.39`, `avg_render_total_ms=109.83`, `avg_llrawproc_ms=65.87`, `avg_mix_chroma_ms=30.59`, `avg_chroma_copy_ms=3.32`, `avg_chroma_fullres_ms=14.30`, `avg_chroma_halfres_ms=12.98`
  - `M16-1446`: `presented_fps=12.54`, `avg_render_total_ms=137.21`, `avg_llrawproc_ms=36.33`, `avg_mix_chroma_ms=0.00`, `avg_chroma_copy_ms=0.00`, `avg_chroma_fullres_ms=0.00`, `avg_chroma_halfres_ms=0.00`

### Cross-checked from prior analysis

- The playback preview split remains intact. This pass only changed the chroma smoother's row addressing and did not touch export behavior.
- The change moved the right bucket a little: `avg_mix_chroma_ms` came down compared with the previous accepted row path, while the visible smoke gate stayed clean.

### Needs runtime profiling

- The 2x2 chroma smoother is still the current hot bucket on the chroma-heavy clips. If we keep pushing here, the next likely seam is reducing repeated work across adjacent x positions rather than just the row address math.

### Ranked next steps

1. High impact / medium risk: look for a rolling-window or reused-neighborhood version of the 2x2 chroma smoother.
2. Medium impact / low risk: keep the current smoke harness and settle gate unchanged so future comparisons stay apples-to-apples.
3. Low impact / low risk: leave export untouched and continue using it as the control path.

## 2026-05-30 - raw prefetch keep value and rejected alias-map branch split

### Verified locally

- `src/mlv/video_mlv.c` is back to `MLV_RAW_UINT16_PREFETCH_LOOKAHEAD 2`. Rolling it down to `1` regressed the visible GUI smoke set, so `2` is the current keep value.
- The latest rebuilt user-facing release executable is [`platform/qt/build-release/release/MLVApp.exe`](C:\!Layi%20Wkspc%5CMLV-App%5Cplatform%5Cqt%5Cbuild-release%5Crelease%5CMLVApp.exe), `LastWriteTime=5/30/2026 7:17:07 AM`, `Length=8791552`, `SHA256=A4668FD355FBAC4E717FD691FB837ACB7430D9E197E49E512F8D981AED5FA559`.
- The restored visible smoke set with x1 Quality and settled Auto Look Assist stayed valid on the current source shape:
  - `M16-1327`: `presented_fps=7.869`, `avg_llrawproc_ms=75.270`, `avg_mix_chroma_ms=33.762`
  - `M16-1347`: `presented_fps=7.491`, `avg_llrawproc_ms=80.133`, `avg_mix_chroma_ms=33.667`
  - `M16-1446`: `presented_fps=8.863`, `avg_llrawproc_ms=56.803`, `avg_mix_chroma_ms=0.000`
- A separate alias-map branch-split probe in `src/mlv/llrawproc/dualiso_avx2.inc` was tried and rejected. It hoisted the `alias_map` null-check out of `final_blend_row_avx2`, but the benchmark regressed on the chroma-heavy clips and the file was restored to the original code.

### Cross-checked from prior analysis

- The earlier raw-prefetch `1` rollback was the wrong direction. The stronger visible-smoke path on this VM is the current `2` setting, not `1`.
- The alias-map split had one clip that improved, but the overall visible set regressed enough that it should not ship.

### Needs runtime profiling

- Keep the current prefetch lookahead stable unless a new probe can clear all three visible clips at once.

### Ranked next steps

1. High impact / medium risk: keep the current prefetch setting and look for a different low-risk seam inside the Dual ISO mix/final-blend path.
2. Medium impact / low risk: preserve the current visible smoke harness and launch defaults so future comparisons stay apples-to-apples.
3. Low impact / low risk: leave export untouched and continue using it as the control path.

## 2026-05-30 - accepted chroma_smooth aliasing hint probe

### Verified locally

- Added `restrict` aliasing hints to the `chroma_smooth_2x2` function signature in [`src/mlv/llrawproc/chroma_smooth.c`](C:\!Layi Wkspc\MLV-App\src\mlv\llrawproc\chroma_smooth.c), covering the input/output buffers and LUT pointers without changing the filter math.
- Rebuilt the user-facing release executable at [`platform/qt/build-release/release/MLVApp.exe`](C:\!Layi Wkspc\MLV-App\platform\qt\build-release\release\MLVApp.exe), `LastWriteTime=5/30/2026 7:23:34 AM`, `Length=8791552`, `SHA256=92866AEFB3D13567C5357C46DC1009868A8CA97405949F06433BA33B6A86D5D7`.
- Re-ran the visible GUI smoke set with x1 Quality, settled Auto Look Assist, and the same launch-state validation. All three clips passed the visual gate and improved versus the current keep baseline:
  - `M16-1327`: `presented_fps=7.985`, `avg_llrawproc_ms=72.672`, `avg_mix_chroma_ms=31.297`, `avg_chroma_copy_ms=3.953`, `avg_chroma_fullres_ms=13.781`, `avg_chroma_halfres_ms=13.563`
  - `M16-1347`: `presented_fps=8.355`, `avg_llrawproc_ms=67.433`, `avg_mix_chroma_ms=31.284`, `avg_chroma_copy_ms=3.209`, `avg_chroma_fullres_ms=14.627`, `avg_chroma_halfres_ms=13.448`
  - `M16-1446`: `presented_fps=12.235`, `avg_llrawproc_ms=39.204`, `avg_mix_chroma_ms=0.000`

### Cross-checked from prior analysis

- Compared with the restored `restrict`-free baseline, the aliasing hints reduced `avg_mix_chroma_ms` on the chroma-heavy clips and improved visible playback throughput across the full smoke set.
- The visual-state gate remained unchanged: x1 Quality, Auto Look Assist, stretch, scopes, and dual-ISO settings all matched the user-facing smoke policy.

### Needs runtime profiling

- Keep this hint probe only if the next pass stays stable on repeated smoke runs; the 1446 clip may still carry more noise in its timing because its chroma bucket is inactive.

### Ranked next steps

1. High impact / medium risk: keep the `restrict` hint if it repeats cleanly on another smoke pass.
2. Medium impact / low risk: continue using the current launch defaults and visible smoke harness for apples-to-apples comparisons.
3. Low impact / low risk: leave export untouched and continue treating it as the control path.

## 2026-05-30 - accepted branchless median rewrite in 2x2 chroma smoother

### Verified locally

- Reworked the `chroma_smooth_med5` helper in [`src/mlv/llrawproc/chroma_smooth.c`](C:\!Layi Wkspc\MLV-App\src\mlv\llrawproc\chroma_smooth.c) so the 2x2 chroma smoother uses the same comparison network but in a branchless `MIN`/`MAX` swap form. The surrounding sample math and preview-only scope stayed unchanged.
- Rebuilt the user-facing release executable at [`platform/qt/build-release/release/MLVApp.exe`](C:\!Layi Wkspc\MLV-App\platform\qt\build-release\release\MLVApp.exe), `LastWriteTime=5/30/2026 7:36:26 AM`, `Length=8791552`, `SHA256=140F741CFBD330B5492B93A144776CCF565A8EDBB612103E67A4774FC520CEDC`.
- Re-ran the visible GUI smoke set with x1 Quality, settled Auto Look Assist, and the same launch-state validation. The visual gate still passed on all three clips, and the results improved against the last restored baseline:
  - `M16-1327`: `presented_fps=8.352` vs `7.869`, `avg_mix_chroma_ms=25.134` vs `33.762`, `avg_chroma_copy_ms=3.746` vs `5.143`
  - `M16-1347`: `presented_fps=8.468` vs `7.491`, `avg_mix_chroma_ms=27.588` vs `33.667`, `avg_chroma_copy_ms=3.971` vs `3.933`
  - `M16-1446`: `presented_fps=10.213` vs `8.863`, with `avg_mix_chroma_ms=0.000`

### Cross-checked from prior analysis

- The earlier row-pointer cleanup in `chroma_smooth_2x2` remains in place. This branchless median rewrite builds on that same hot loop rather than changing the playback/export split.
- The improvement is consistent across the visible smoke set, which is the key reason this probe keeps while the earlier no-alias final-blend split did not.

### Needs runtime profiling

- The chroma-heavy clips are still dominated by `avg_mix_chroma_ms`, so the next gain likely needs a more structural reuse pass inside the 2x2 smoother rather than another tiny helper tweak.

### Ranked next steps

1. High impact / medium risk: look for rolling-neighborhood reuse in `chroma_smooth_2x2` so the median network does less repeated work.
2. Medium impact / low risk: keep the current visible smoke harness and launch defaults unchanged for apples-to-apples comparisons.
3. Low impact / low risk: leave export untouched and continue using it as the control path.

## 2026-05-30 - rejected scalar-local rewrite in 2x2 chroma smoother

### Verified locally

- Tried replacing the 2x2 smoother's small median arrays with scalar locals in [`src/mlv/llrawproc/chroma_smooth.c`](C:\!Layi Wkspc\MLV-App\src\mlv\llrawproc\chroma_smooth.c) to reduce stack traffic and make the branchless `chroma_smooth_med5` call sites more register-friendly.
- Rebuilt the user-facing release executable at [`platform/qt/build-release/release/MLVApp.exe`](C:\!Layi Wkspc\MLV-App\platform\qt\build-release\release\MLVApp.exe), `LastWriteTime=5/30/2026 7:45:37 AM`, `Length=8791552`, `SHA256=0826E151FC3A36BB57B291169B91BD12BA4F72F5836FA7BEDE44D13A42CBEF2A`.
- The revert-to-accepted smoke pass still satisfied the x1 Quality / Auto Look Assist gate, but the scalar-local rewrite itself did not outperform the accepted branchless-median baseline across the same visible playback route, so it was backed out.

### Cross-checked from prior analysis

- The current keep path remains the branchless median rewrite plus the earlier row-pointer cleanup in `chroma_smooth_2x2`.
- The failed scalar-local version is a dead end for now; it did not deliver a stable enough improvement to displace the accepted path.

### Needs runtime profiling

- The next promising seam is still structural reuse across neighboring x positions inside `chroma_smooth_2x2`; the remaining cost is not in the tiny median helper alone.

### Ranked next steps

1. High impact / medium risk: prototype a rolling-window or reused-neighborhood 2x2 smoother.
2. Medium impact / low risk: keep the current smoke harness and launch defaults fixed so future runs stay comparable.
3. Low impact / low risk: keep export untouched as the control path.

## 2026-05-30 - rejected no-alias fast path split in Dual ISO final blend

### Verified locally

- Split `final_blend_row_avx2` in [`src/mlv/llrawproc/dualiso_avx2.inc`](C:\!Layi Wkspc\MLV-App\src\mlv\llrawproc\dualiso_avx2.inc) into separate `alias_map == NULL` / `alias_map != NULL` loops so the common playback case could skip the per-iteration alias branch.
- Rebuilt the user-facing release executable at [`platform/qt/build-release/release/MLVApp.exe`](C:\!Layi Wkspc\MLV-App\platform\qt\build-release\release\MLVApp.exe), `LastWriteTime=5/30/2026 8:17:12 AM`, `Length=8791552`, `SHA256=82D78998E6E763F210F62EFAE383BFC32050E0BD12872DCBCFAC75FA1C6BA88D`.
- Re-ran the visible GUI smoke set with x1 Quality and settled Auto Look Assist. The visual gate stayed clean, but the split regressed all three user clips versus the accepted branchless-median chroma baseline:
  - `M16-1327`: `presented_fps=8.734`, `avg_mix_chroma_ms=27.300`
  - `M16-1347`: `presented_fps=7.860`, `avg_mix_chroma_ms=29.841`
  - `M16-1446`: `presented_fps=9.368`, `avg_mix_chroma_ms=0.000`

### Cross-checked from prior analysis

- The accepted branchless-median `chroma_smooth_2x2` path still outperforms this final-blend branch split on the same visible smoke set.
- The no-alias split was a plausible branch-shape simplification, but the real clips did not reward it.

### Needs runtime profiling

- Keep the accepted branchless-median chroma path as the baseline.
- The next useful seam remains structural reuse inside `chroma_smooth_2x2`, not more branching inside `final_blend_row_avx2`.

### Ranked next steps

1. High impact / medium risk: prototype a rolling-window or reused-neighborhood 2x2 smoother.
2. Medium impact / low risk: keep the current smoke harness and launch defaults fixed so future runs stay comparable.
3. Low impact / low risk: keep export untouched as the control path.

## 2026-05-30 - rejected restrict alias hint pass in 2x2 chroma smoother

### Verified locally

- Restored the `restrict` aliasing hints in [`src/mlv/llrawproc/chroma_smooth.c`](C:\!Layi Wkspc\MLV-App\src\mlv\llrawproc\chroma_smooth.c) around the `raw2ev` / `ev2raw` LUT pointers without changing the accepted branchless-median math or the row sampling layout.
- Rebuilt the user-facing release executable at [`platform/qt/build-release/release/MLVApp.exe`](C:\!Layi Wkspc\MLV-App\platform\qt\build-release\release\MLVApp.exe), `LastWriteTime=5/30/2026 8:04:40 AM`, `Length=8791552`, `SHA256=DDF3BF54685EE65E481EF7FDEC90B8047DE83F825A3156E54915A28B117F4FF5`.
- Re-ran the visible GUI smoke set with x1 Quality, settled Auto Look Assist, and the same launch-state validation. The gate stayed clean, but the repeat runs were not stable enough to displace the accepted branchless-median baseline:
  - `M16-1327`: `presented_fps=8.245`, `avg_mix_chroma_ms=24.348`
  - `M16-1347`: `presented_fps=7.493`, `avg_mix_chroma_ms=29.617`
  - `M16-1446`: `presented_fps=10.189`, `avg_mix_chroma_ms=0.000`

### Cross-checked from prior analysis

- The branchless median rewrite itself is still a good path; the alias hints did not produce a repeatable enough win to keep on top of that baseline.
- The current hot bucket remains `avg_mix_chroma_ms`, but this hint pass does not seem to be the lever that moves it reliably on the user clips.

### Needs runtime profiling

- Keep this as a rejected probe unless a later structural change around the 2x2 smoother makes the alias hints repeatable again.

### Ranked next steps

1. High impact / medium risk: look for a more structural reuse pass in `chroma_smooth_2x2` rather than tiny aliasing hints.
2. Medium impact / low risk: keep the same smoke harness and visual-state gate for the next pass.
3. Low impact / low risk: leave export untouched as the control path.

## 2026-05-30 - rejected restrict annotations across Dual ISO AVX2 row kernels

### Verified locally

- Added `restrict` qualifiers to the hot AVX2 row-kernel pointers in [`src/mlv/llrawproc/dualiso_avx2.inc`](C:\!Layi Wkspc\MLV-App\src\mlv\llrawproc\dualiso_avx2.inc) for `overexposed_mark_row_avx2`, `overexposed_blur_row_avx2`, `fullres_reconstruction_bright_row_avx2`, `convert_20_to_16bit_row_avx2`, `final_blend_row_avx2`, and `mix_images_row_avx2`, without changing the blend math or the row layout.
- Rebuilt the user-facing release executable at [`platform/qt/build-release/release/MLVApp.exe`](C:\!Layi Wkspc\MLV-App\platform\qt\build-release\release\MLVApp.exe), `LastWriteTime=5/30/2026 8:04:40 AM`, `Length=8791552`, `SHA256=C3DB1F223C7698A28A2FFDF59621F09EB077D3DAC9724BC94C9A6268A5DCCBD4`.
- Re-ran the visible GUI smoke set with x1 Quality, settled Auto Look Assist, and the same launch-state validation. The gate stayed clean, but the results were mixed and not stable enough to keep:
  - `M16-1327`: `presented_fps=9.871`, `avg_mix_chroma_ms=25.000`
  - `M16-1347`: `presented_fps=7.871`, `avg_mix_chroma_ms=25.921`
  - `M16-1446`: `presented_fps=11.368`, `avg_mix_chroma_ms=0.000`

### Cross-checked from prior analysis

- The hint pass did not produce a repeatable multi-clip win over the accepted branchless-median chroma baseline.
- The performance picture still points at the same chroma-heavy blend path, but the fix is not a simple `restrict` annotation across the AVX2 row kernels.

### Needs runtime profiling

- Keep this as a rejected probe unless a later, more structural change in the same code path makes the compiler hints repeatable.

### Ranked next steps

1. High impact / medium risk: look for a more structural reuse pass in `chroma_smooth_2x2` instead of more alias hints.
2. Medium impact / low risk: keep the same smoke harness and visual-state gate for the next pass.
3. Low impact / low risk: leave export untouched as the control path.

## 2026-05-30 - rejected rolling-window reuse in 2x2 chroma smoother

### Verified locally

- Prototyped a rolling five-sample neighborhood reuse scheme in [`src/mlv/llrawproc/chroma_smooth.c`](C:\!Layi Wkspc\MLV-App\src\mlv\llrawproc\chroma_smooth.c) so the 2x2 chroma smoother could reuse overlapping neighborhood work across `x` steps.
- The probe passed the x1 Quality / Auto Look Assist visual gate, but the visible multi-clip smoke set did not clear the no-regression bar:
  - `M16-1327`: `presented_fps=8.488`, `avg_mix_chroma_ms=22.544`
  - `M16-1347`: first pass `presented_fps=7.619`, repeat runs `8.218` and `7.995`
  - `M16-1446`: `presented_fps=10.621`
- The rollout did not produce a stable enough win over the accepted branchless-median baseline, so the rolling-window rewrite was reverted.
- After the revert, the restored source shape still passed the visual gate, but the current visible smoke on this working tree sits at:
  - `M16-1327`: `presented_fps=7.485`, `avg_mix_chroma_ms=36.800`
  - `M16-1347`: `presented_fps=6.240`, `avg_mix_chroma_ms=41.120`
  - `M16-1446`: `presented_fps=9.098`, `avg_mix_chroma_ms=0.000`
- Restoring the accepted branchless-median call shape in `chroma_smooth_2x2` brought the visible smoke back up on this pass:
  - `M16-1327`: `presented_fps=7.988`, `avg_mix_chroma_ms=28.250`, `avg_chroma_copy_ms=3.922`, `avg_chroma_fullres_ms=13.094`, `avg_chroma_halfres_ms=11.234`
  - `M16-1347`: `presented_fps=7.609`, `avg_mix_chroma_ms=27.066`, `avg_chroma_copy_ms=4.557`, `avg_chroma_fullres_ms=11.902`, `avg_chroma_halfres_ms=10.607`
  - `M16-1446`: `presented_fps=11.341`, with `avg_mix_chroma_ms=0.000`

### Cross-checked from prior analysis

- The accepted branchless-median 2x2 smoother remains the correct keep-path for now.
- The best nearby gain remains structural, but it needs to be repeatable on the same three-clip GUI smoke gate before we keep it.

### Needs runtime profiling

- If we revisit this area, the next candidate should prove repeatable across `M16-1327`, `M16-1347`, and `M16-1446` before we consider it safe.

### Ranked next steps

1. High impact / medium risk: revisit the 2x2 chroma path only if a more local reuse strategy can be made repeatable.
2. Medium impact / low risk: keep the current smoke harness and launch defaults unchanged for apples-to-apples comparisons.
3. Low impact / low risk: leave export untouched as the control path.

## 2026-05-30 - accepted row-pointer hoist in 2x2 chroma smoother

### Verified locally

- Hoisted the 2x2 chroma smoother's row-address arithmetic in [`src/mlv/llrawproc/chroma_smooth.c`](C:\!Layi%20Wkspc%5CMLV-App%5Csrc%5Cmlv%5Cllrawproc%5Cchroma_smooth.c) so the hot horizontal and vertical sample macros use precomputed row pointers instead of recomputing `inp + y * w` inside each sample.
- Rebuilt the user-facing release executable at [`platform/qt/build-release/release/MLVApp.exe`](C:\!Layi%20Wkspc%5CMLV-App%5Cplatform%5Cqt%5Cbuild-release%5Crelease%5CMLVApp.exe), `LastWriteTime=5/30/2026 8:45:27 AM`, `Length=8792064`, `SHA256=A3017A0BDA61627DA9A7B17C1ED77D016249AABFCD60EB84F67080F04E03C28D`.
- Re-ran the visible GUI smoke set with x1 Quality, settled Auto Look Assist, and the same launch-state validation. The gate stayed clean and the chroma-heavy clips improved versus the previous accepted baseline:
  - `M16-1327`: `presented_fps=8.574`, `avg_mix_chroma_ms=24.870`, `avg_chroma_copy_ms=4.377`, `avg_chroma_fullres_ms=10.087`, `avg_chroma_halfres_ms=10.406`
  - `M16-1347`: `presented_fps=8.738`, `avg_mix_chroma_ms=25.971`, `avg_chroma_copy_ms=3.843`, `avg_chroma_fullres_ms=11.729`, `avg_chroma_halfres_ms=10.400`
  - `M16-1446`: `presented_fps=11.115`, with `avg_mix_chroma_ms=0.000`

### Cross-checked from prior analysis

- The earlier branchless-median helper remains the right scalar baseline for `chroma_smooth_2x2`; this row-pointer hoist builds on that accepted shape rather than replacing it.
- The improvement is repeatable across both chroma-heavy clips, which makes it safer than the earlier rolling-window and alias-hint probes.

### Needs runtime profiling

- The current hot bucket is still `avg_mix_chroma_ms`, but the row-pointer hoist now looks like a real, keep-worthy reduction in that path.

### Ranked next steps

1. High impact / low risk: keep this row-pointer hoist as the new baseline and continue looking for the next structural reduction inside `mix_chroma`.
2. Medium impact / low risk: preserve the current smoke harness and visual-state gate for apples-to-apples comparisons.
3. Low impact / low risk: leave export untouched as the control path.

## 2026-05-30 - rejected overexposed blur row-pointer hoist

### Verified locally

- Prototyped a small address-hoist in [`src/mlv/llrawproc/dualiso_avx2.inc`](C:\!Layi%20Wkspc%5CMLV-App%5Csrc%5Cmlv%5Cllrawproc%5Cdualiso_avx2.inc) for `overexposed_blur_row_avx2`, replacing repeated `prev + x` / `curr + x` / `next + x` arithmetic with row-local pointer variables inside the 8-pixel AVX2 loop.
- Rebuilt the user-facing release executable at [`platform/qt/build-release/release/MLVApp.exe`](C:\!Layi%20Wkspc%5CMLV-App%5Cplatform%5Cqt%5Cbuild-release%5Crelease%5CMLVApp.exe), `LastWriteTime=5/30/2026 8:58:12 AM`, `Length=8792064`.
- Re-ran the visible GUI smoke set with x1 Quality, settled Auto Look Assist, and the same launch-state validation. The gate stayed clean, but the visible FPS regressed versus the accepted row-pointer chroma baseline:
  - `M16-1327`: `presented_fps=8.327`, `avg_mix_chroma_ms=24.925`, `avg_mix_overexposed_ms=4.388`, `avg_final_blend_ms=6.776`
  - `M16-1347`: `presented_fps=8.121`, `avg_mix_chroma_ms=25.492`, `avg_mix_overexposed_ms=3.923`, `avg_final_blend_ms=8.369`
  - `M16-1446`: `presented_fps=9.357`, `avg_mix_chroma_ms=0.000`, `avg_mix_overexposed_ms=5.973`, `avg_final_blend_ms=9.400`

### Cross-checked from prior analysis

- The overexposed pass is small enough that this kind of pointer hoist appears to be neutral-to-negative on the visible set, at least on this VM and clip mix.

### Needs runtime profiling

- Keep this as a rejected probe unless a later structural change in the same AVX2 row path makes the address hoist repeatable.

### Ranked next steps

1. High impact / medium risk: return to `dualiso.c` / `mix_chroma` or a deeper `final_blend` structural change rather than more row-address cleanup.
2. Medium impact / low risk: keep the current smoke harness and launch defaults unchanged for apples-to-apples comparisons.
3. Low impact / low risk: leave export untouched as the control path.

## 2026-05-30 - accepted alias-map 37-neighbor selection helper

### Verified locally

- Replaced the alias-map middle-pass `kth_smallest_int` call in [`src/mlv/llrawproc/dualiso.c`](C:\!Layi%20Wkspc%5CMLV-App%5Csrc%5Cmlv%5Cllrawproc%5Cdualiso.c) with a fixed-size helper that computes the 5th smallest of the 37-value neighborhood directly, avoiding the generic quickselect path and the temporary `neighbours[]` selection overhead.
- Rebuilt the user-facing release executable at [`platform/qt/build-release/release/MLVApp.exe`](C:\!Layi%20Wkspc%5CMLV-App%5Cplatform%5Cqt%5Cbuild-release%5Crelease%5CMLVApp.exe), `LastWriteTime=5/30/2026 9:05:03 AM`, `Length=8792064`, `SHA256=9C6D783F40F897B1D6F78D711C8DB07752FDE35972EE56AFAEE5624257759DB7`.
- Re-ran the visible GUI smoke set with x1 Quality, settled Auto Look Assist, and the same launch-state validation. The smoke gate stayed visually correct, and the chroma-heavy clips improved versus the previous accepted chroma baseline:
  - `M16-1327`: `presented_fps=9.348`, `avg_mix_chroma_ms=23.880`, `avg_chroma_copy_ms=4.147`, `avg_chroma_fullres_ms=10.653`, `avg_chroma_halfres_ms=9.080`
  - `M16-1347`: `presented_fps=9.345`, `avg_mix_chroma_ms=24.520`, `avg_chroma_copy_ms=3.547`, `avg_chroma_fullres_ms=10.920`, `avg_chroma_halfres_ms=10.053`
  - `M16-1446`: `presented_fps=11.240`, with `avg_mix_chroma_ms=0.000`

### Cross-checked from prior analysis

- The accepted row-pointer hoist in `chroma_smooth.c` remains the right baseline for the 2x2 smoother; this change trims the alias-map filter that still sat inside the same full20 pipeline.
- The win is repeatable across the two chroma-heavy clips, which makes it more credible than the earlier rejected alias-map branch-hoist and overexposed-hoist probes.

### Needs runtime profiling

- The remaining hot bucket on the chroma-heavy clips is still `avg_mix_chroma_ms`, but it is now lower than the previous accepted baseline rather than just noise.

### Ranked next steps

1. High impact / medium risk: keep the alias-map selection helper and continue looking for a true `mix_chroma` reduction.
2. Medium impact / low risk: preserve the current smoke harness and visual-state gate for apples-to-apples comparisons.
3. Low impact / low risk: leave export untouched as the control path.

## 2026-05-30 - accepted restrict aliasing hints in 2x2 chroma smoother

### Verified locally

- Added `__restrict` aliasing hints to the 2x2 chroma smoother in [`src/mlv/llrawproc/chroma_smooth.c`](C:\!Layi%20Wkspc%5CMLV-App%5Csrc%5Cmlv%5Cllrawproc%5Cchroma_smooth.c:46) and [`src/mlv/llrawproc/chroma_smooth.c`](C:\!Layi%20Wkspc%5CMLV-App%5Csrc%5Cmlv%5Cllrawproc%5Cchroma_smooth.c:164) for `inp`, `out`, `raw2ev`, and `ev2raw`.
- Rebuilt the user-facing release executable at [`platform/qt/build-release/release/MLVApp.exe`](C:\!Layi%20Wkspc%5CMLV-App%5Cplatform%5Cqt%5Cbuild-release%5Crelease%5CMLVApp.exe), `LastWriteTime=5/30/2026 9:17:08 AM`, `Length=8792064`, `SHA256=52CE0E91461EAC15545082853B195F92D4612AFAFD2170002EBB99DEF3D67ACB`.
- Re-ran the visible GUI smoke set with x1 Quality, settled Auto Look Assist, and the same launch-state validation. The gate stayed clean, and the chroma-heavy clips improved again versus the previous accepted baseline:
  - `M16-1327`: `presented_fps=9.469`, `avg_mix_chroma_ms=24.145`, `avg_chroma_copy_ms=3.566`, `avg_chroma_fullres_ms=10.263`, `avg_chroma_halfres_ms=10.316`
  - `M16-1347`: `presented_fps=9.496`, `avg_mix_chroma_ms=23.316`, `avg_chroma_copy_ms=3.461`, `avg_chroma_fullres_ms=9.671`, `avg_chroma_halfres_ms=10.184`
  - `M16-1446`: `presented_fps=12.210`, with `avg_mix_chroma_ms=0.000`

### Cross-checked from prior analysis

- The new aliasing hints are small, but they are now helping the same hot chroma path that was already improved by the row-pointer hoist.
- The improvement is repeatable on the two chroma-heavy clips and does not disturb the visual-state gate.

### Needs runtime profiling

- `M16-1446` does not exercise the chroma mix bucket, so it remains a control clip rather than proof of further headroom in `mix_chroma`.

### Ranked next steps

1. High impact / medium risk: keep the aliasing hints and continue looking for a deeper structural reduction inside `mix_chroma`.
2. Medium impact / low risk: preserve the current smoke harness and visual-state gate for apples-to-apples comparisons.
3. Low impact / low risk: leave export untouched as the control path.
