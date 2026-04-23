## Testing Scaffold Notes

### Safe Overlap Keep-Set + Expanded Playback Telemetry Assertions (2026-04-23)

#### Verified locally

- The playback-profile scaffold now covers the widened safe overlap split, not just the earlier direct-8-bit activation seam.
  - `tests/console/test_clip_golden.cpp` now asserts presence and numeric validity for:
    - `draw_frame_ready_scene_ms`
    - `draw_frame_ready_image_ms`
    - `draw_frame_ready_present_ms`
    - `draw_frame_ready_scopes_ms`
    - `draw_frame_ready_overlay_ms`
    - `processed8_prefetch_hit`
- The app-backed playback-profile checks stayed green after the render-thread exclusivity repair and the processed8 prefetch gate.
  - Fresh validation on the current keep-set:
    - plain `console_tests --check-golden`: `41 tests / 160 assertions / 17 skips / 0 failures`
    - app-backed `console_tests --check-golden` with `platform/qt/build-codex-current/release/MLVApp.exe`: `41 tests / 750 assertions / 1 skip / 0 failures`
    - `pipeline_tests --check-golden`: `46 tests / 526 assertions / 4 skips / 0 failures`
- The new safe-path profiling artifacts for the large Dual ISO preview receipt are:
  - `.claude/profiling/20260423-safe-overlap-fastscale/large_dual_iso_preview_t4_safe_run1.json`
  - `.claude/profiling/20260423-safe-overlap-fastscale/large_dual_iso_preview_t4_safe_run2.json`
  - `.claude/profiling/20260423-safe-overlap-fastscale/large_dual_iso_preview_t4_safe_run3.json`
  - `.claude/profiling/20260423-safe-overlap-fastscale/large_dual_iso_preview_t4_safe_run4.json`
- Those artifacts also confirm that the kept path does not rely on processed8 background rendering.
  - All warm samples in the current keep-set reruns reported `processed8_prefetch_hit = false`.

#### Cross-checked from prior analysis

- The processed8 prefetch worker is still useful as an experiment seam, but the scaffold should not assume it is part of the default path.
  - Default coverage should stay focused on the safe direct-8-bit path and the Qt-side cadence split until the prefetch path has a real raw/VIDF snapshot.

#### Needs runtime profiling

- After a real front/back render-buffer handoff lands, extend the app-backed scaffold again so it asserts the new overlap metadata on the completed-frame slot instead of the current shared-buffer seam.

### Cadence-Gap Telemetry + Activated Direct Processed8 Path (2026-04-23)

#### Verified locally

- The playback-profile scaffold now measures the full post-engine gap instead of treating it as one opaque cadence delta.
  - `platform/qt/RenderFrameThread.cpp:138-189` and `platform/qt/RenderFrameThread.cpp:600-602` now export:
    - `render_thread_queue_wait_ms`
    - `render_thread_work_ms`
    - `render_thread_total_ms`
  - `platform/qt/MainWindow.cpp:1442` and `platform/qt/MainWindow.cpp:10823` now export:
    - `draw_frame_ready_queue_ms`
    - `draw_frame_ready_total_ms`
- The new direct processed-8-bit path is now both behavior-covered and app-backed activation-covered.
  - `tests/pipeline/test_dual_iso_pipeline.cpp:230` now includes `DualIsoPipeline.DirectProcessed8FastPathMatchesShiftedProcessed16Reference`, which keeps the direct 8-bit math pinned to the shifted 16-bit reference on a known-supported subset.
  - `tests/console/test_clip_golden.cpp:530` now also asserts that the real app-backed preview receipt reports `processed8_direct_path_active = true` on every measured frame.
- The first narrow direct-8-bit gate turned out to be too optimistic about what “neutral preview receipt” meant on this branch.
  - The kept follow-up in `src/processing/raw_processing.c:876-928` and `src/processing/raw_processing.c:1668-1755` now preserves the preview receipt’s post-gamma curve steps and `exr_mode` skip-gamut behavior, which is what made the app-backed activation test turn green.
- Fresh current-tree validation after the updated telemetry and activated direct-8-bit path:
  - plain `console_tests --check-golden`: `41 tests / 160 assertions / 17 skips / 0 failures`
  - app-backed `console_tests --check-golden` with `platform/qt/build-codex-current/release/MLVApp.exe`: `41 tests / 726 assertions / 1 skip / 0 failures`
  - `pipeline_tests --check-golden`: `46 tests / 526 assertions / 4 skips / 0 failures`
- Fresh large-receipt playback-profile artifacts on the current keep point:
  - `.claude/profiling/20260423-direct8bit-playback-gap/large_dual_iso_preview_t4_direct8_run1.json`
  - `.claude/profiling/20260423-direct8bit-playback-gap/large_dual_iso_preview_t4_direct8_run2.json`
  - `.claude/profiling/20260423-direct8bit-playback-gap/large_dual_iso_preview_t4_direct8_run3.json`
  - Warm aggregate medians versus the prior kept bilinear/u16 baseline moved from:
    - `cadence_ms 75.439 -> 59.299`
    - `processed8_total_ms 54.000 -> 37.000`
    - `processed16_total_ms 48.000 -> 34.000`
    - `processed16_to_8bit_ms 2.000 -> 0.000`
    - `processing_ms 13.000 -> 7.000`
  - The activation seam is now explicit in the artifact family itself:
    - `processed8_direct_path_active` was `true` on every warm frame in the kept reruns

#### Cross-checked from prior analysis

- The scaffold change from “field presence only” to “field presence plus one app-backed activation assertion on the real preview receipt” was worth it here. The pipeline-only subset test would have missed the first non-activating implementation.

#### Needs runtime profiling

- The scaffold now proves the direct path activates and stays bit-matched on its supported receipt shape, but the remaining playback gap after this win is still a runtime diagnosis problem, not a scaffold problem. Re-profile host versus VM before locking in the next optimization order.

### Multithread Processing Telemetry + MCraw Test-Link Fix (2026-04-23)

#### Verified locally

- The old single-thread-only caveat on `processing_core_*` is now superseded on this branch.
  - `src/processing/raw_processing.h:323` adds `processing_core_timing_t`.
  - `src/processing/raw_processing.c:576-623` now records per-worker core timings on the `threads > 1` path and collapses them back with `max(...)`, so app-backed playback-profile JSON now reports meaningful `processing_core_levels_ms`, `processing_core_color_ms`, and `processing_core_output_ms` on the large Dual ISO `--threads 4` path.
- Fresh current-tree large-receipt playback-profile artifacts proving the t4 export:
  - `.claude/profiling/20260423-postdecode-t4-breakdown/large_dual_iso_preview_t4_breakdown.json`
  - `.claude/profiling/20260423-postdecode-t4-breakdown/large_dual_iso_preview_t4_breakdown_postcopyskip.json`
  - `.claude/profiling/20260423-postdecode-t4-breakdown/large_dual_iso_preview_t4_breakdown_postcopyskip_run2.json`
  - `.claude/profiling/20260423-postdecode-t4-breakdown/large_dual_iso_preview_t4_breakdown_postcopyskip_run3.json`
- The current kept post-decode follow-up on top of that telemetry is the smaller scalar cleanup of the common basic-matrix fast path in `src/processing/raw_processing.c:939-989`.
  - Kept artifacts:
    - `.claude/profiling/20260423-postdecode-t4-breakdown/large_dual_iso_preview_t4_breakdown_colorfast_run1.json`
    - `.claude/profiling/20260423-postdecode-t4-breakdown/large_dual_iso_preview_t4_breakdown_colorfast_run2.json`
    - `.claude/profiling/20260423-postdecode-t4-breakdown/large_dual_iso_preview_t4_breakdown_colorfast_run3.json`
  - Aggregate warm-sample medians versus the kept post-copy-skip baseline moved from:
    - `latency_ms 76.032 -> 74.294`
    - `processed16_total_ms 49.000 -> 47.000`
    - `processing_ms 13.000 -> 12.000`
    - `processing_core_color_ms 8.000 -> 8.000`
- A heavier LUT-backed version of the same color-path idea was measured and then reverted.
  - Rejected artifacts:
    - `.claude/profiling/20260423-postdecode-t4-breakdown/large_dual_iso_preview_t4_breakdown_colorlut_run1.json`
    - `.claude/profiling/20260423-postdecode-t4-breakdown/large_dual_iso_preview_t4_breakdown_colorlut_run2.json`
    - `.claude/profiling/20260423-postdecode-t4-breakdown/large_dual_iso_preview_t4_breakdown_colorlut_run3.json`
  - It did not outperform the smaller scalar rewrite on the same large Dual ISO `--threads 4` shape.
- Follow-up investigation on the current receipt showed that the hot preview path here is still bilinear, not grayscale `none`.
  - The kept code now adds direct-`uint16` preview helpers in `src/mlv/video_mlv.c`, `src/debayer/debayer.c`, and `src/mlv/frame_caching.c` so the `none` and bilinear preview paths can stop after `raw_uint16 + llrawproc` instead of round-tripping through `getMlvRawFrameFloat(...)`.
  - Kept artifacts for the current large Dual ISO bilinear path:
    - `.claude/profiling/20260423-postdecode-t4-breakdown/large_dual_iso_preview_t4_breakdown_bilinearu16_run1.json`
    - `.claude/profiling/20260423-postdecode-t4-breakdown/large_dual_iso_preview_t4_breakdown_bilinearu16_run2.json`
    - `.claude/profiling/20260423-postdecode-t4-breakdown/large_dual_iso_preview_t4_breakdown_bilinearu16_run3.json`
  - Aggregate warm-sample medians versus the kept post-copy-skip baseline moved from:
    - `processed16_total_ms 49.000 -> 48.000`
    - `debayered_frame_ms 30.000 -> 29.000`
    - `raw_float_convert_ms 1.000 -> 0.000`
    - `debayer_exclusive_ms 6.000 -> 4.000`
  - Safe read: keep it as a low-risk debayer-side cleanup, but it does not change the larger conclusion that `processing_ms` / `processing_core_color_ms` still dominate this receipt.
- Added a focused regression guard in `tests/pipeline/test_dual_iso_pipeline.cpp`:
  - `DualIsoPipeline.NoneDebayerMatchesScaledRawFloatReference` verifies that the new direct-`uint16` `none` preview path still matches the existing scaled raw-float reference exactly.
- The current integration tree also needed a test-build fix after the upstream MCraw parser sync:
  - `src/mlv/mcraw/cJSON.c` is now listed in `tests/common/pipeline_runtime.pri:18`, `tests/pipeline/pipeline_tests.pro:33`, and `tests/perf/perf_tests.pro:28`.
- Fresh current-tree validation after the telemetry update, qmake fix, kept scalar fast-path cleanup, and kept direct-`uint16` preview debayer cleanup:
  - plain `console_tests --check-golden`: `41 tests / 160 assertions / 17 skips / 0 failures`
  - app-backed `console_tests --check-golden` with `platform/qt/build-codex-current/release/MLVApp.exe`: `41 tests / 726 assertions / 1 skip / 0 failures`
  - `pipeline_tests --check-golden`: `46 tests / 526 assertions / 4 skips / 0 failures`

#### Cross-checked from prior analysis

- The earlier contract choice was still right: playback-profile coverage should assert field presence, numeric shape, and coarse parent/child bounds rather than exact additive equality. That stayed robust when the multithreaded `processing_core_*` values became nonzero.
- The rejected LUT experiment was a useful reminder that the scaffold is now strong enough to reject performance-shaped code that compiles and stays green but fails to produce a convincing measurement win.
- The added `NoneDebayerMatchesScaledRawFloatReference` guard is intentionally narrow: it gives the current direct-`uint16` preview cleanup a behavior tripwire without forcing new golden hashes or broadening the performance scaffold with another artifact family.

#### Needs runtime profiling

- The scaffold now exports honest multithreaded processing-core buckets, but the large-receipt performance interpretation is still measurement-first rather than gate-first. Use repeated host reruns before converting the new t4 values into hard performance claims.

### Playback-Profile Sample Flake Fix (2026-04-22)

#### Verified locally

- The app-backed `ClipGolden` playback-profile failures were narrowed to sample-emission ordering in `platform/qt/MainWindow.cpp:1393-1419`, not playback correctness.
- The stale-binary failure mode was eliminated by rebuilding the app and console test binary before rerunning.
- Source fix landed:
  - `gpu_bilinear_debayer_renderer` is now always present when a bilinear fallback reason is present.
  - `engine_latency_ms` is now always present, with fallback timing derived from `completionNs` when the direct engine signal is late.
- Five consecutive captured fresh-binary runs are green:
  - `.claude/profiling/20260422-clipgolden-flake-fix/run_4.txt`
  - `.claude/profiling/20260422-clipgolden-flake-fix/run_5.txt`
  - `.claude/profiling/20260422-clipgolden-flake-fix/run_6.txt`
  - `.claude/profiling/20260422-clipgolden-flake-fix/run_7.txt`
  - `.claude/profiling/20260422-clipgolden-flake-fix/run_8.txt`

#### Cross-checked from prior analysis

- Claude's pre-patch flake report matches the patched source locations exactly and should now be considered superseded by the source fix plus fresh-binary reruns.

#### Needs runtime profiling

- One longer fresh-binary soak run is still desirable, but the old "80% green with retry contract" wording should no longer be treated as current state for this branch.

### Dual ISO Preview Timing Telemetry (2026-04-22)

#### Verified locally

- Added playback-profile sample fields for:
  - `dual_iso_preview_histogram_ms`
  - `dual_iso_preview_regression_ms`
  - `dual_iso_preview_rowscale_ms`
- The values are ferried through `RenderFrameThread` so the UI-thread sample writer sees the render-thread timings.
- App-backed console coverage is green after the telemetry addition:
  - `35 tests / 358 assertions / 0 skips / 0 failures`

#### Cross-checked from prior analysis

- Claude's concern about requiring nonzero values on every frame was valid once cache hits and timer-resolution effects showed up. The scaffold now only requires the fields to exist and at least one preview sample to show nonzero rowscale work.

#### Needs runtime profiling

- If future playback runs use a different worker/cache shape, expect some samples to carry zero preview-stage timings when the frame reuses cached work instead of re-entering llrawproc.

### Outer Stage Timing Telemetry (2026-04-22)

#### Verified locally

- Added per-sample playback-profile fields for:
  - `raw_uint16_ms`
  - `llrawproc_ms`
  - `llrawproc_total_ms`
  - `llrawproc_dark_frame_ms`
  - `llrawproc_vertical_stripes_ms`
  - `llrawproc_focus_pixels_ms`
  - `llrawproc_bad_pixels_ms`
  - `llrawproc_pattern_noise_ms`
  - `llrawproc_dual_iso_ms`
  - `llrawproc_chroma_smooth_ms`
  - `llrawproc_other_ms`
  - `dual_iso_preview_total_ms`
  - `debayered_frame_ms`
  - `processing_ms`
  - `processed16_total_ms`
  - `processed16_for_8bit_ms`
  - `processed16_to_8bit_ms`
  - `processed8_total_ms`
- The original RenderFrameThread snapshot-harvest attempt was replaced with direct `video_mlv` getters after local verification showed the header-local `StageTiming.h` snapshot could not safely bridge translation units.
- App-backed console coverage is green with the expanded sample contract:
  - `35 tests / 432 assertions / 0 skips / 0 failures`

#### Cross-checked from prior analysis

- Claude's “sample-level existence + nonnegative sanity, not exact equality” guidance matched what was needed here. The new fields are asserted in the main `ClipGolden.TinyDualIsoHeadlessPlaybackProfileProducesJson` loop only.

#### Needs runtime profiling

- The scaffold now tells us where warm Dual ISO preview frames spend time on this VM, but the numbers should be re-collected on the hardware-backed host before being treated as representative beyond this environment.

### Processing Stage Timing Telemetry (2026-04-22)

#### Verified locally

- Added playback-profile sample fields for:
  - `processing_setup_ms`
  - `processing_shadows_highlights_prep_ms`
  - `processing_highest_green_ms`
  - `processing_core_ms`
  - `processing_denoise_ms`
  - `processing_rbf_ms`
  - `processing_ca_ms`
  - `processing_other_ms`
  - `processing_core_levels_ms`
  - `processing_core_color_ms`
  - `processing_core_creative_ms`
  - `processing_core_output_ms`
  - `processing_core_other_ms`
- Added a small console helper in [tests/console/test_clip_golden.cpp:153](</C:/!Layi%20Wkspc/MLV-App/.claude/worktrees/festive-boyd/tests/console/test_clip_golden.cpp:153>) so the app-backed playback-profile test asserts:
  - field exists
  - field is numeric
  - field is non-negative
  - only coarse parent/child bounds
- Fixed an instrumentation bug in [src/processing/raw_processing.c:9](</C:/!Layi%20Wkspc/MLV-App/.claude/worktrees/festive-boyd/src/processing/raw_processing.c:9>) by adding the missing `#include <omp.h>`. Before that fix, `omp_get_wtime()` compiled with an implicit declaration and produced garbage-sized substage values in playback-profile JSON.
- Fresh validation after the `omp.h` fix:
  - `console_tests --check-golden`: `35 tests / 524 assertions / 0 skips / 0 failures`
  - `pipeline_tests --check-golden`: `44 tests / 507 assertions / 4 skips / 0 failures`

#### Cross-checked from prior analysis

- Rawls' earlier guidance was correct: the stable contract here is presence/non-negative/coarse bounds, not exact additive equality across processing substeps.

#### Needs runtime profiling

- That original single-thread limitation applied to the 2026-04-22 implementation only. See the 2026-04-23 multithread follow-up above for the current branch state: the large-receipt `--threads 4` playback-profile path now exports meaningful inner `processing_core_*` buckets.

### Exclusive Debayer Telemetry + Processing Fast Path (2026-04-22)

#### Verified locally

- Added playback-profile sample fields for:
  - `raw_float_convert_ms`
  - `debayer_exclusive_ms`
  - `debayer_wb_prepare_ms`
  - `debayer_ca_ms`
  - `debayer_kernel_ms`
  - `debayer_wb_undo_ms`
  - `debayer_pipeline_other_ms`
- The exclusive debayer timings come from:
  - [src/mlv/video_mlv.c:34-44](</C:/!Layi%20Wkspc/MLV-App/.claude/worktrees/festive-boyd/src/mlv/video_mlv.c:34>)
  - [src/mlv/frame_caching.c:22-59](</C:/!Layi%20Wkspc/MLV-App/.claude/worktrees/festive-boyd/src/mlv/frame_caching.c:22>) and [frame_caching.c:614-664](</C:/!Layi%20Wkspc/MLV-App/.claude/worktrees/festive-boyd/src/mlv/frame_caching.c:614>)
  - [platform/qt/RenderFrameThread.cpp:235-258](</C:/!Layi%20Wkspc/MLV-App/.claude/worktrees/festive-boyd/platform/qt/RenderFrameThread.cpp:235>)
- The console-link regression was fixed by replacing direct `omp_get_wtime()` calls in `frame_caching.c` with the local `mlv_debayer_timing_now_seconds()` helper.
- App-backed playback-profile coverage stays green with the extra fields:
  - `console_tests --check-golden`: `35 tests / 558 assertions / 0 skips / 0 failures`
  - `pipeline_tests --check-golden`: `44 tests / 507 assertions / 4 skips / 0 failures`
- Landed a narrow `raw_processing.c` fast path for the common preview-playback receipt shape, plus a `highlight_reconstruction` gate around `analyse_frame_highest_green(...)`.

#### Cross-checked from prior analysis

- The old `debayered_frame_ms` ranking was inflated by inclusive timing. The new exclusive fields confirm that pure debayer is materially smaller than the old inclusive number suggested.
- Receipt inspection for `large_dual_iso_preview.marxml` matches the new fast-path gating: the expensive creative / gradient / highlight / AgX branches are off in this playback scenario.

#### Needs runtime profiling

- The current default-thread playback profile is now close enough that `raw_uint16_ms` needs its own sub-breakdown before another major optimization bet.

### Raw `raw_uint16` Split + Thread Matrix (2026-04-22)

#### Verified locally

- Added app-backed playback-profile assertions for:
  - `raw_uint16_disk_read_ms`
  - `raw_uint16_decompress_ms`
  - `raw_uint16_unpack_ms`
  - `raw_uint16_copy_ms`
  - `raw_uint16_other_ms`
- The contract is presence + nonnegative values + coarse parent/child bounds, not exact additive equality.
- Fresh green validation on the current telemetry-bearing branch:
  - `console_tests --check-golden`: `35 tests / 586 assertions / 0 skips / 0 failures`
  - `pipeline_tests --check-golden`: `44 tests / 507 assertions / 4 skips / 0 failures`
- Clean current VM thread matrix for the Dual ISO preview receipt:
  - `t1`: `152.16 ms`
  - `t2`: `121.70 ms`
  - `t4`: `111.84 ms`
  - `t8`: `158.73 ms`
- The split established that `raw_uint16_ms` is decode-heavy on this clip:
  - `disk_read` is only about `1.3-3.0 ms`
  - `decompress` is about `31-46 ms`
  - `unpack` / `copy` are `0` for this compressed path

#### Cross-checked from prior analysis

- Claude's recommendation to separate disk I/O from CPU work before planning SIMD or unpack changes was correct; the resulting telemetry shows the bottleneck is decode.
- Claude's thread-count concern was also justified. On the current VM, `4` threads is the best clean benchmark point and `8` regresses materially.

#### Needs runtime profiling

- Repeat the `1/2/4/8` matrix on the host before converting the VM-local `4`-thread sweet spot into any broader policy change.
- A deeper decode-path split is still needed before touching the decoder itself; today we only know the cost lands in the compressed decode bucket, not whether the best next move is decoder optimization vs prefetch/caching.

### Raw decode-ahead prototype follow-up (2026-04-22)

#### Verified locally

- Prototyped a raw-`uint16` decode-ahead ring and added `raw_uint16_prefetch_hit` telemetry.
- Measured result on the Dual ISO preview VM path:
  - foreground raw decode was hidden on warm frames (`prefetch_hit 7/7`, `raw_uint16_ms ~1.5 ms`)
  - but total warm latency regressed at `t1` / `t4`
- Practical resolution:
  - the prototype is now opt-in only via `MLVAPP_EXPERIMENTAL_RAW_UINT16_PREFETCH`
  - the default playback path stays on the direct decode path
- Added deeper compressed-decode telemetry:
  - `raw_uint16_decompress_prepare_ms`
  - `raw_uint16_decompress_execute_ms`
- On the current path, `prepare` is effectively `0` and `execute` accounts for essentially all decode time.
- One attempted LJ92 `parsePred6` branch-hoist micro-optimization was reverted in the same session after quick playback samples failed to show a trustworthy win. The branch keeps the deeper decode telemetry, not that speculative loop rewrite.

#### Cross-checked from prior analysis

- This matches the earlier recommendation to test pipelining quickly before investing in decoder internals: the quick prototype was worth building, but the measured VM result does not justify making it the default path.

#### Needs runtime profiling

- The next decode pass should instrument `liblj92` internals directly; the current outer split is enough to say that setup is not the problem.

### Play-start cache preroll (2026-04-22)

#### Verified locally

- Added a new cache-layer helper, [mlv_cache_request_playback_preroll(...)](</C:/!Layi%20Wkspc/MLV-App/.claude/worktrees/festive-boyd/src/mlv/frame_caching.c:302>), and wired it into [MainWindow::on_actionPlay_toggled(bool)](</C:/!Layi%20Wkspc/MLV-App/.claude/worktrees/festive-boyd/platform/qt/MainWindow.cpp:9850>) through [primePlaybackCacheOnPlayStart()](</C:/!Layi%20Wkspc/MLV-App/.claude/worktrees/festive-boyd/platform/qt/MainWindow.cpp:9857>).
- The helper keeps play-start preroll intentionally small and non-blocking:
  - 2-frame lookahead
  - only requests future uncached frames
  - only wakes existing cache workers when caching is already enabled
- Added cache-behavior regression coverage:
  - `CacheBehavior.PlaybackPrerollRequestsFirstFutureUncachedFrame`
  - `CacheBehavior.PlaybackPrerollSlidesWindowTowardLookahead`
- Fresh verification:
  - `console_tests --check-golden`: `37 tests / 160 assertions / 13 skips / 0 failures`
  - `pipeline_tests --check-golden`: `44 tests / 507 assertions / 4 skips / 0 failures`
  - `gui_tests`: `19 passed / 0 failed / 6 skipped`

#### Cross-checked from prior analysis

- This matches the earlier design constraint from Claude/Curie: do not block play-start on a full cache reload, and do not turn the experimental raw decode-ahead worker into default behavior.
- Test coverage stayed at the right level: cache logic is pinned in `test_cache_behavior.cpp`, while GUI smoke remains a broad integration sanity check rather than a fragile timing assertion.

#### Needs runtime profiling

- We still need a real play-start A/B on cached AMaZE playback if we want to quantify first-frame UX improvement. This pass was validated by code/tests, not by a new measured playback-start latency artifact.

### Play-start metric refinement + LJ92 local-hoist follow-up (2026-04-22, late)

- Refined the new playback-profile first-frame metric so it now waits for the first **requested** render to complete instead of whichever `drawFrameReady()` arrives next.
- Added `play_start_preroll_eligible` alongside the stricter `play_start_preroll_active` metadata so the cached-playback mode check and the actual preroll request are distinguishable in artifacts.
- Strengthened the app-backed console contract accordingly; current green result:
  - `console_tests --check-golden`: `37 tests / 604 assertions / 1 skip / 0 failures`
- Revalidated the pipeline and GUI suites after the `MainWindow` telemetry refinement:
  - `pipeline_tests --check-golden`: `44 tests / 507 assertions / 4 skips / 0 failures`
  - `gui_tests`: `19 passed / 0 failed / 6 skipped`
- Also landed a first LJ92 local-state hoist in `parsePred6()` and hardened the dormant `SLOW_HUFF` branch to stay compatible with the new helper-local state.
- Important nuance:
  - the new metric is now trustworthy enough for future A/B work
  - but the cached/non-cached profile runs performed here validate instrumentation, not the UX delta of preroll itself
  - and the LJ92 hoist is verified-in-source plus green-in-tests, not yet a decisive measured decoder-speed win

### Same-mode preroll gate + honest LJ92 predictor contract (2026-04-22, near midnight)

- Added an app-backed cached-AMaZE preroll env-gate test:
  - `ClipGolden.TinyDualIsoHeadlessPlaybackProfileAmazeCachedCanDisablePlayStartPrerollViaEnvironment`
- Added an app-backed LJ92 profiling contract that no longer assumes all fixtures use predictor 6:
  - `ClipGolden.TinyDualIsoHeadlessPlaybackProfileCanEnableLj92Pred6SplitViaEnvironment`
- The playback-profile scaffold now exports two more frame-level fields:
  - `raw_uint16_lj92_pred6_split_requested`
  - `raw_uint16_lj92_predictor`
- Fresh green local result after those additions:
  - `console_tests --check-golden`: `39 tests / 644 assertions / 1 skip / 0 failures`
  - `pipeline_tests --check-golden`: `44 tests / 507 assertions / 4 skips / 0 failures`
- Runtime takeaway attached to the scaffold:
  - both current Dual ISO fixtures report `raw_uint16_lj92_predictor = 1`
  - so `raw_uint16_lj92_pred6_split_active = false` is the honest expected outcome even when the profiling env flag is enabled

### Predictor-1 LJ92 profiling follow-up (2026-04-23)

- Added a second app-backed LJ92 profiling contract for the generic non-pred6 path:
  - `ClipGolden.TinyDualIsoHeadlessPlaybackProfileCanEnableLj92GenericSplitViaEnvironment`
- Hardened both env-gated LJ92 tests so they inspect the first frame with real raw-decode telemetry instead of assuming `frames[0]` is always the measured one.
- Fresh local state after that hardening:
  - `console_tests --check-golden`: `40 tests / 676 assertions / 0 skips / 0 failures`
  - `pipeline_tests --check-golden`: `44 tests / 507 assertions / 4 skips / 0 failures`
- The new runtime fields are now part of the scaffold contract:
  - `raw_uint16_lj92_generic_split_requested`
  - `raw_uint16_lj92_generic_split_active`
  - `raw_uint16_lj92_generic_total_ms`
  - `raw_uint16_lj92_generic_bitstream_ms`
  - `raw_uint16_lj92_generic_predictor_ms`
  - `raw_uint16_lj92_generic_other_ms`
- Important implementation note:
  - the generic split is useful for relative shape only
  - because it times inside the per-sample decode loop, it perturbs absolute decoder `ms`
  - do not use the split-enabled artifact itself as a sustained-FPS benchmark

### Overlap keep revalidation (2026-04-23, current)

- Revalidated the current kept playback state after the front/back overlap handoff and final fast-scaler keep decision.
- Fresh green local result on the kept state:
  - `console_tests --check-golden`: `41 tests / 160 assertions / 17 skips / 0 failures`
  - `pipeline_tests --check-golden`: `46 tests / 526 assertions / 4 skips / 0 failures`
  - app-backed `console_tests --check-golden`: `41 tests / 750 assertions / 1 skip / 0 failures`
- Practical scaffold takeaway:
  - the playback overlap work is now covered by the same console + pipeline + app-backed profile contracts that already pinned the direct-8-bit path
  - follow-up playback experiments should keep using those three checks before claiming any new cadence win
