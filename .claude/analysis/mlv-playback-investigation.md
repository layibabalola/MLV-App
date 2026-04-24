## Direct-8 Loop Profiling (2026-04-24)

### Verified locally

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
- `platform/qt/build-codex-current/release/MLVApp.exe` in this environment is older than the current tree, so a fresh paired run could not be completed in this session.
- The earlier same-session artifacts at `.claude-state/profiling/20260424-m2-hotloop-rank*` still show large split distortion under sub-loop probe mode (`~200-300ms` overhead), so direct throughput deltas from those files are not trusted for absolute gains.

### Cross-checked from prior analysis

- This matches the static hypothesis ordering: matrix/tonemap math is the highest-confidence target before wider SIMD work, and creative curves are effectively inactive on the current Dual ISO preview receipt (`processing_direct8_curves_ms` is expected to remain zero in this shape).
- The direct-8 split hooks were added with the existing keep-set in place (no behavior change to pause/export pathways), and this aligns with the M1 milestone constraint.

### Needs runtime profiling

- Do a fresh same-session 4x baseline + 4x sub-loop rerun after rebuilding the exact working tree:
  - `--input tests/fixtures/clips/large_dual_iso.mlv`
  - `--receipt tests/fixtures/receipts/large_dual_iso_hq.marxml`
  - `--frames 16 --threads 1 --raw-cache-mb 0`
  - warm filter: `sample_index >= 4`
- Compare warm medians for at least: `latency_ms`, `cadence_ms`, `processed8_total_ms`, `processing_core_color_ms`, `processing_direct8_matrix_ms`, `processing_direct8_gamma_ms`.
- Accept a micro-pass only if the end-to-end `cadence_ms` delta is at least 5%.

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




