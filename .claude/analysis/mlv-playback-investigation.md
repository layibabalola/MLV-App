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
