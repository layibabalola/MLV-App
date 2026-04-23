# Next-Session Handoff

## 2026-04-23 final predictor-1 keep

### Verified locally

- Final kept source state on this branch is candidate 1 plus candidate 6.
  - Candidate 1 remains kept in [src/mlv/liblj92/lj92.c:374](</C:/!Layi%20Wkspc/MLV-App/.claude/worktrees/festive-boyd/src/mlv/liblj92/lj92.c:374>), [lj92.c:568](</C:/!Layi%20Wkspc/MLV-App/.claude/worktrees/festive-boyd/src/mlv/liblj92/lj92.c:568>), [lj92.c:646](</C:/!Layi%20Wkspc/MLV-App/.claude/worktrees/festive-boyd/src/mlv/liblj92/lj92.c:646>), and [lj92.c:903](</C:/!Layi%20Wkspc/MLV-App/.claude/worktrees/festive-boyd/src/mlv/liblj92/lj92.c:903>).
  - Candidate 6 is also kept in [src/mlv/liblj92/lj92.c:97](</C:/!Layi%20Wkspc/MLV-App/.claude/worktrees/festive-boyd/src/mlv/liblj92/lj92.c:97>), [lj92.c:123](</C:/!Layi%20Wkspc/MLV-App/.claude/worktrees/festive-boyd/src/mlv/liblj92/lj92.c:123>), [lj92.c:142](</C:/!Layi%20Wkspc/MLV-App/.claude/worktrees/festive-boyd/src/mlv/liblj92/lj92.c:142>), [lj92.c:319](</C:/!Layi%20Wkspc/MLV-App/.claude/worktrees/festive-boyd/src/mlv/liblj92/lj92.c:319>), [lj92.c:322](</C:/!Layi%20Wkspc/MLV-App/.claude/worktrees/festive-boyd/src/mlv/liblj92/lj92.c:322>), and [lj92.c:449](</C:/!Layi%20Wkspc/MLV-App/.claude/worktrees/festive-boyd/src/mlv/liblj92/lj92.c:449>).
- Candidates 2 through 5 were all measured and reverted; only their profiling artifacts remain.
- Final candidate 6 medians from [candidate6_metrics.txt](</C:/!Layi%20Wkspc/MLV-App/.claude/worktrees/festive-boyd/.claude/profiling/20260423-pred1-fastpath-candidate6-huffman-direct-lut/candidate6_metrics.txt:1>):
  - `threads=1`: `raw_uint16_ms 18.0`
  - `threads=4`: `raw_uint16_ms 18.0`
- Final pivot artifact on the accepted build is [large_dual_iso_preview_t4_final_pivot.json](</C:/!Layi%20Wkspc/MLV-App/.claude/worktrees/festive-boyd/.claude/profiling/20260423-pred1-fastpath-final-pivot/large_dual_iso_preview_t4_final_pivot.json:1>).
  - warm medians from that `--threads 4` run:
    - `latency_ms 77.952`
    - `processed16_total_ms 53.000`
    - `debayered_frame_ms 29.000`
    - `processing_ms 18.000`
    - `raw_uint16_ms 18.000`
    - `raw_uint16_lj92_pred1_fast_path_total_ms 16.000`
- Final green validation on the kept build:
  - `console_tests --check-golden`: `41 tests / 160 assertions / 17 skips / 0 failures`
  - app-backed `console_tests --check-golden` with fresh [MLVApp.exe](</C:/!Layi%20Wkspc/MLV-App/.claude/worktrees/festive-boyd/platform/qt/build/Desktop_Qt_6_10_2_MinGW_64_bit-Release/release/MLVApp.exe:1>): `41 tests / 695 assertions / 1 skip / 0 failures`
  - `pipeline_tests --check-golden`: `44 tests / 507 assertions / 4 skips / 0 failures`

### Cross-checked from prior analysis

- Candidate 1 is the foundational accepted optimization and candidate 6 is the final Phase C addition on top of it.
- The final pivot confirms the accepted decoder work moved the next hottest seam out of LJ92 and into later processed16 / debayered-frame work on this VM.

### Next seam

- Phase C is complete on this branch. Do not reopen the predictor-1 candidate queue from this handoff.
- If work continues, pivot to the post-decode `processed16_total_ms` / `debayered_frame_ms` path rather than more predictor-1 LJ92 tuning.
- If `lj92.c` is touched again, keep it scoped to correctness hardening and validate it separately from the accepted perf stack.

## 2026-04-23 candidate 1 kept (historical)

### Verified locally

- Candidate 1 is kept in [src/mlv/liblj92/lj92.c:374](</C:/!Layi%20Wkspc/MLV-App/.claude/worktrees/festive-boyd/src/mlv/liblj92/lj92.c:374>), [lj92.c:568](</C:/!Layi%20Wkspc/MLV-App/.claude/worktrees/festive-boyd/src/mlv/liblj92/lj92.c:568>), [lj92.c:646](</C:/!Layi%20Wkspc/MLV-App/.claude/worktrees/festive-boyd/src/mlv/liblj92/lj92.c:646>), and [lj92.c:903](</C:/!Layi%20Wkspc/MLV-App/.claude/worktrees/festive-boyd/src/mlv/liblj92/lj92.c:903>).
  - Combined contents:
    - hot/cold split of the active predictor-1 fast path
    - `LJ92_ALWAYS_INLINE` on `nextdiff_fast(...)`
    - cached `data` pointer use in the second refill loop
  - Treat the measured win as a combined candidate result, not as proof that the inline attribute alone matters.
- The predictor-1 activation blocker remains resolved.
  - Root cause: the shipped Dual ISO benchmark receipts decode as contiguous predictor-1 with `2` LJ92 components, not the earlier assumed mono shape.
  - Final fast-path gate lives in [src/mlv/liblj92/lj92.c:554](</C:/!Layi%20Wkspc/MLV-App/.claude/worktrees/festive-boyd/src/mlv/liblj92/lj92.c:554>) and dispatches through [lj92.c:568](</C:/!Layi%20Wkspc/MLV-App/.claude/worktrees/festive-boyd/src/mlv/liblj92/lj92.c:568>) / [lj92.c:903](</C:/!Layi%20Wkspc/MLV-App/.claude/worktrees/festive-boyd/src/mlv/liblj92/lj92.c:903>).
- Important implementation note for future decoder work:
  - A first direct-output multi-component rewrite regressed pipeline goldens.
  - The final `parseScanPred1Fast(...)` preserves the generic row-buffer behavior and is green.
- The earlier `baseline_metrics.txt` text summary was inconsistent. The corrected source of truth is the recomputed raw-JSON baseline summary saved in [baseline_metrics.txt](</C:/!Layi%20Wkspc/MLV-App/.claude/worktrees/festive-boyd/.claude/profiling/20260423-pred1-fastpath-baseline/baseline_metrics.txt:1>).
- Phase B corrected baseline on the fixed code:
  - artifacts: [20260423-pred1-fastpath-baseline](</C:/!Layi%20Wkspc/MLV-App/.claude/worktrees/festive-boyd/.claude/profiling/20260423-pred1-fastpath-baseline>)
  - summary: [baseline_metrics.txt](</C:/!Layi%20Wkspc/MLV-App/.claude/worktrees/festive-boyd/.claude/profiling/20260423-pred1-fastpath-baseline/baseline_metrics.txt:1>)
  - fixed run shape:
    - `3x --threads 1`
    - `3x --threads 4`
    - `--frames 16`
    - discard first `5`
    - warm medians only over remaining samples with `raw_uint16_ms > 0`
  - median-of-run-medians:
    - `threads=1`: `raw_uint16_ms 32.5`, `decompress_execute_ms 31.0`, `fast_path_total_ms 31.0`, `bitstream_ms 29.5`, `predictor_ms 4.0`
    - `threads=4`: `raw_uint16_ms 39.0`, `decompress_execute_ms 38.0`, `fast_path_total_ms 38.0`, `bitstream_ms 28.0`, `predictor_ms 4.0`
  - all six runs kept the fast path active on every decode-active warm sample.
- Candidate 1 profiling is saved in [20260423-pred1-fastpath-candidate1-split-hot-cold](</C:/!Layi%20Wkspc/MLV-App/.claude/worktrees/festive-boyd/.claude/profiling/20260423-pred1-fastpath-candidate1-split-hot-cold>) with summary in [candidate1_metrics.txt](</C:/!Layi%20Wkspc/MLV-App/.claude/worktrees/festive-boyd/.claude/profiling/20260423-pred1-fastpath-candidate1-split-hot-cold/candidate1_metrics.txt:1>).
  - `threads=1`: `raw_uint16_ms 29.0` (`10.770%` faster than corrected baseline)
  - `threads=4`: `raw_uint16_ms 29.0` (`25.641%` faster than corrected baseline)
- Later queue results:
  - candidate 2 refill split was reverted: [candidate2_metrics.txt](</C:/!Layi%20Wkspc/MLV-App/.claude/worktrees/festive-boyd/.claude/profiling/20260423-pred1-fastpath-candidate2-refill-split/candidate2_metrics.txt:1>)
  - candidate 3 pointer-walk loops were reverted: [candidate3_metrics.txt](</C:/!Layi%20Wkspc/MLV-App/.claude/worktrees/festive-boyd/.claude/profiling/20260423-pred1-fastpath-candidate3-pointer-walk/candidate3_metrics.txt:1>)
  - candidate 4 branch trimming was reverted: [candidate4_metrics.txt](</C:/!Layi%20Wkspc/MLV-App/.claude/worktrees/festive-boyd/.claude/profiling/20260423-pred1-fastpath-candidate4-branch-trim/candidate4_metrics.txt:1>)
  - practical conclusion: candidate 1 remains the only accepted Phase C optimization on this branch
- Final validation on the kept candidate:
  - `console_tests --check-golden`: `41 tests / 160 assertions / 17 skips / 0 failures`
  - app-backed `console_tests --check-golden` with fresh [MLVApp.exe](</C:/!Layi%20Wkspc/MLV-App/.claude/worktrees/festive-boyd/platform/qt/build/Desktop_Qt_6_10_2_MinGW_64_bit-Release/release/MLVApp.exe:1>): `41 tests / 695 assertions / 1 skip / 0 failures`
  - `pipeline_tests --check-golden`: `44 tests / 507 assertions / 4 skips / 0 failures`

### Cross-checked from prior analysis

- The old activation investigation is done. Do not spend the next session rediscovering why the fast path was inactive; it was the mono-only gate, not the predictor readback.
- The next honest comparison point is the corrected Phase B raw-JSON baseline above, not the earlier blocked smoke artifacts or the superseded text-only summary.
- Candidate 1 is already accepted. Candidates 2-4 were tried and reverted, so the only untested queued decoder experiment left is the explicit Huffman / refill work.

### Needs runtime profiling

- Phase C should now proceed exactly in the agreed queue order using the fixed baseline rule:
  - Huffman / refill work last
- Reuse the same measurement rule for every candidate:
  - `--frames 16`
  - discard first `5`
  - medians over remaining warm samples with `raw_uint16_ms > 0`

### Ranked next steps

1. High impact / high effort: if you continue Phase C, the only queued decoder experiment left is the explicit Huffman / refill work on top of the kept candidate-1 state.
2. High impact / medium effort: judge any final decoder candidate against both the corrected raw-JSON baseline and the current kept candidate-1 state; candidates 2-4 showed that “still above baseline” is not enough to justify stacking a slower intermediate.
3. Medium impact / low effort: if you stop here, the next honest step is the final pivot profile and closeout docs, not more near-threshold branch churn.

## 2026-04-23 implementation update

### Verified locally

- Phase A is now landed:
  - env gate: `MLVAPP_PRED1_FASTPATH_MEASUREMENT=1`
  - decoder seam: [src/mlv/liblj92/lj92.c:471](</C:/!Layi%20Wkspc/MLV-App/.claude/worktrees/festive-boyd/src/mlv/liblj92/lj92.c:471>), [lj92.c:548](</C:/!Layi%20Wkspc/MLV-App/.claude/worktrees/festive-boyd/src/mlv/liblj92/lj92.c:548>), [lj92.c:860](</C:/!Layi%20Wkspc/MLV-App/.claude/worktrees/festive-boyd/src/mlv/liblj92/lj92.c:860>)
  - export path: [src/mlv/video_mlv.c:1365](</C:/!Layi%20Wkspc/MLV-App/.claude/worktrees/festive-boyd/src/mlv/video_mlv.c:1365>) and [platform/qt/RenderFrameThread.cpp:413](</C:/!Layi%20Wkspc/MLV-App/.claude/worktrees/festive-boyd/platform/qt/RenderFrameThread.cpp:413>)
  - app-backed contract: [tests/console/test_clip_golden.cpp:1091](</C:/!Layi%20Wkspc/MLV-App/.claude/worktrees/festive-boyd/tests/console/test_clip_golden.cpp:1091>)
- New exported playback-profile fields:
  - `raw_uint16_lj92_pred1_fast_path_active`
  - `raw_uint16_lj92_pred1_fast_path_measurement_requested`
  - `raw_uint16_lj92_pred1_fast_path_measurement_active`
  - `raw_uint16_lj92_pred1_fast_path_total_ms`
  - `raw_uint16_lj92_pred1_fast_path_bitstream_ms`
  - `raw_uint16_lj92_pred1_fast_path_predictor_ms`
  - `raw_uint16_lj92_pred1_fast_path_other_ms`
- Fresh validation on the rebuilt app/binaries:
  - `console_tests --check-golden`: `41 tests / 160 assertions / 17 skips / 0 failures`
  - app-backed `console_tests --check-golden` with `MLVAPP_PROFILE_EXE=[MLVApp.exe](</C:/!Layi%20Wkspc/MLV-App/.claude/worktrees/festive-boyd/platform/qt/build/Desktop_Qt_6_10_2_MinGW_64_bit-Release/release/MLVApp.exe:1>)`: `41 tests / 692 assertions / 1 skip / 0 failures`
  - `pipeline_tests --check-golden`: `44 tests / 507 assertions / 4 skips / 0 failures`
- Fresh smoke artifacts:
  - [tiny_dual_iso_preview_t1_pred1_fastpath_measurement_smoke.json](</C:/!Layi%20Wkspc/MLV-App/.claude/worktrees/festive-boyd/.claude/profiling/20260423-pred1-fastpath-measurement/tiny_dual_iso_preview_t1_pred1_fastpath_measurement_smoke.json:1>)
  - [large_dual_iso_preview_t1_pred1_fastpath_measurement_smoke.json](</C:/!Layi%20Wkspc/MLV-App/.claude/worktrees/festive-boyd/.claude/profiling/20260423-pred1-fastpath-measurement/large_dual_iso_preview_t1_pred1_fastpath_measurement_smoke.json:1>)
- Both smoke runs currently show the same blocker:
  - predictor readback is still `1`
  - measurement is requested
  - `raw_uint16_lj92_pred1_fast_path_active = false`
  - `raw_uint16_lj92_pred1_fast_path_measurement_active = false`

### Cross-checked from prior analysis

- The coarse fast-path measurement pass is implemented exactly as planned, but the runtime receipts now say the benchmark fixtures are not actually taking the landed mono fast path.

### Needs runtime profiling

- Phase B baseline is blocked until the active-path eligibility mismatch is explained; otherwise the new clean metric remains zero and cannot be used for the planned keep/revert thresholds.

### Ranked next steps

1. High impact / low-medium effort: probe the fast-path eligibility contract in [src/mlv/liblj92/lj92.c:536](</C:/!Layi%20Wkspc/MLV-App/.claude/worktrees/festive-boyd/src/mlv/liblj92/lj92.c:536>) and identify why predictor-1 benchmark fixtures still miss it.
2. High impact / medium effort: once `large_dual_iso` actually reports the fast path as active, resume the Phase B repeated baseline plan unchanged.
3. High impact / medium effort: only then continue down the queued decoder candidates.

## Current state (2026-04-23)

### Verified locally

- Current local test state:
  - `console_tests --check-golden`: `40 tests / 676 assertions / 0 skips / 0 failures`
  - `pipeline_tests --check-golden`: `44 tests / 507 assertions / 4 skips / 0 failures`
- The playback-performance investigation has now moved past `diso_get_preview()` and the first processing fast path:
  - Dual ISO preview path is no longer the dominant warm cost on this VM.
  - The sustained CPU playback question is now centered on shared raw decode / processing hot paths.
- The current Dual ISO fixtures still decode through JPEG predictor `1`, not predictor `6`.
  - Honest predictor readback is exported via `raw_uint16_lj92_predictor`.
  - The pred6 split is therefore requested-but-inactive on the current fixtures.
- Generic predictor-path LJ92 profiling now exists and is exported via:
  - `raw_uint16_lj92_generic_split_requested`
  - `raw_uint16_lj92_generic_split_active`
  - `raw_uint16_lj92_generic_total_ms`
  - `raw_uint16_lj92_generic_bitstream_ms`
  - `raw_uint16_lj92_generic_predictor_ms`
  - `raw_uint16_lj92_generic_other_ms`
- The app-backed playback-profile tests were hardened so env-gated LJ92 checks use the first frame with real raw-decode telemetry instead of assuming `frames[0]` always carries it.
- A first generic-path local-state hoist is landed in `parseScan()`:
  - [src/mlv/liblj92/lj92.c:740](</C:/!Layi%20Wkspc/MLV-App/.claude/worktrees/festive-boyd/src/mlv/liblj92/lj92.c:740>)
  - It switches the non-pred6 path to local `b/cnt/ix` state with `nextdiff_fast(...)` and writes state back once on exit.
- A narrower predictor-1 mono fast path is now landed:
  - [src/mlv/liblj92/lj92.c:497](</C:/!Layi%20Wkspc/MLV-App/.claude/worktrees/festive-boyd/src/mlv/liblj92/lj92.c:497>)
  - [src/mlv/liblj92/lj92.c:509](</C:/!Layi%20Wkspc/MLV-App/.claude/worktrees/festive-boyd/src/mlv/liblj92/lj92.c:509>)
  - [src/mlv/liblj92/lj92.c:740](</C:/!Layi%20Wkspc/MLV-App/.claude/worktrees/festive-boyd/src/mlv/liblj92/lj92.c:740>)
  - It only activates for the current hot decode shape: predictor `1`, single component, no linearize table, `skiplen == 0`, contiguous output, and generic split profiling disabled.
- Fresh green validation after that pass:
  - `console_tests --check-golden`: `40 tests / 676 assertions / 0 skips / 0 failures`
  - `pipeline_tests --check-golden`: `44 tests / 507 assertions / 4 skips / 0 failures`
- Fresh smoke artifact:
  - [large_dual_iso_preview_t1_pred1_fast_path.json](</C:/!Layi%20Wkspc/MLV-App/.claude/worktrees/festive-boyd/.claude/profiling/20260423-pred1-fast-path/large_dual_iso_preview_t1_pred1_fast_path.json:1>)

### Cross-checked from prior analysis

- The current VM still benefits from the same Dual ISO preview-mode UI change and the processing fast path already on disk.
- The play-start preroll helper is useful as a UX improvement, but it is still a separate question from sustained playback FPS.
- The decode-ahead prototype remains experimental-only and should stay off by default.

### Needs runtime profiling

- The generic predictor-1 LJ92 split is informative but intrusive:
  - it times inside the per-sample decode loop
  - use it for relative-shape inspection only
  - do not use split-enabled artifacts as absolute decoder `ms` benchmarks
- The generic-path local-state hoist is green and plausibly helpful, but not yet a decisive measured win on this noisy VM:
  - older warm reference: [large_dual_iso_preview_t1_refined.json](</C:/!Layi%20Wkspc/MLV-App/.claude/worktrees/festive-boyd/.claude/profiling/20260422-play-to-first-frame-and-lj92/large_dual_iso_preview_t1_refined.json:1>) shows warm `raw_uint16_decompress_execute_ms ~37 ms`
  - post-hoist reruns currently span roughly `32.5 ms` to `60.0 ms`
  - treat it as a plausible small win pending tighter repeat profiling
- The new predictor-1 fast-path smoke run landed at roughly:
  - `raw_uint16_decompress_execute_ms ~47.78 ms`
  - `raw_uint16_ms ~50.11 ms`
  - That is still inside the existing noisy VM band, so treat it as a no-regression smoke result rather than proof of a decoder breakthrough.

## Ranked next steps

1. High impact / medium effort: get a lower-overhead predictor-1 decoder read around the new fast path before more decoder surgery.
   - Goal: answer whether bitstream/Huffman or predictor/writeback is the next real sustained CPU lever without per-sample timer distortion.
   - Best seams now are [src/mlv/liblj92/lj92.c:509](</C:/!Layi%20Wkspc/MLV-App/.claude/worktrees/festive-boyd/src/mlv/liblj92/lj92.c:509>) and [lj92.c:740](</C:/!Layi%20Wkspc/MLV-App/.claude/worktrees/festive-boyd/src/mlv/liblj92/lj92.c:740>).

2. High impact / medium effort: if another optimization pass is preferred over more profiling, keep it on the predictor-1 path.
   - First candidates:
     - forced inlining / local-state cleanup around `nextdiff_fast(...)`
     - carefully measured bitstream/Huffman-side experimentation
     - only after the measurement-overhead story is cleaner
     - keep the current mono/no-linearize/contiguous fast-path contract unless a broader caller proves worth specializing

3. Medium impact / low effort: keep preroll as a measured play-start UX improvement, not a sustained-FPS claim.
   - Same-mode cached-AMaZE A/B already showed about `37 ms` better `play_to_first_frame_ms` on this VM.

## Historical Implementation Approach

### Verified locally

- The next session should target the active predictor-1 fast path in [src/mlv/liblj92/lj92.c:509](</C:/!Layi%20Wkspc/MLV-App/.claude/worktrees/festive-boyd/src/mlv/liblj92/lj92.c:509>) via the dispatch in [lj92.c:740](</C:/!Layi%20Wkspc/MLV-App/.claude/worktrees/festive-boyd/src/mlv/liblj92/lj92.c:740>), not only the generic fallback path.
- The existing generic split remains useful for relative-shape inspection, but the next measurement pass should add separate coarse fast-path telemetry instead of changing the meaning of the old intrusive split fields.
- On this machine, `qmake` / `mingw32-make` are not on `PATH` by default. For rebuilds, prepend:
  - `C:\Qt\Tools\mingw1310_64\bin`
  - `C:\Qt\6.10.2\mingw_64\bin`
- The app-backed validation gate should keep using the freshly rebuilt [MLVApp.exe](</C:/!Layi%20Wkspc/MLV-App/.claude/worktrees/festive-boyd/platform/qt/build/Desktop_Qt_6_10_2_MinGW_64_bit-Release/release/MLVApp.exe:1>) through `MLVAPP_PROFILE_EXE`.

### Cross-checked from prior analysis

- Claude's refinement is directionally correct: the `3%` improvement floor only becomes meaningful on this VM when it is backed by long repeated runs, not short smoke checks.
- The strongest autonomous overnight flow is:
  - lower-overhead fast-path measurement first
  - repeated large-clip baseline second
  - one-candidate-at-a-time iteration with explicit keep/revert rules
  - final pivot decision after accepted candidates are stacked

### Needs runtime profiling

- Use the large `large_dual_iso` fixture as the primary overnight benchmark clip.
- For every baseline and candidate comparison:
  - run `3` repeats at `--threads 1`
  - run `3` repeats at `--threads 4`
  - target `100+` frames if practical
  - if `100+` is too slow, choose the longest stable frame count you can afford, record it once, and then reuse the exact same frame count for every later run in the pass
- Warm-window rule:
  - if total frames are `>= 100`, discard the first `20` frames and compute medians from the remaining warm frames
  - otherwise discard `max(5, ceil(20% of total frames))` and compute medians from the remaining warm frames
  - once chosen, keep both total-frame count and discard count fixed across baseline and candidates
- Decision thresholds:
  - `<3%` improvement: treat as noise, revert, move on
  - `3-5%` improvement: keep only if all three repeats improve with no regression outlier
  - `>=5%` improvement: keep if `--threads 4` `raw_uint16_ms` does not regress beyond about `2%`
  - if only the intrusive split looks faster while the clean fast-path metric is flat, revert and move on

### Execution phases

1. Phase A: add separate coarse predictor-1 fast-path measurement.
   - Add `MLVAPP_PRED1_FASTPATH_MEASUREMENT=1`.
   - Keep old intrusive generic split fields unchanged.
   - Add new fast-path telemetry rooted in the active predictor-1 path.

2. Phase B: capture rigorous baseline.
   - Save artifacts under `.claude/profiling/20260423-pred1-baseline/`.
   - Record `threads=1` fast-path medians and `threads=4` `raw_uint16_ms` medians in `.claude/ANALYSIS_LOG.md`.

3. Phase C: iterate the candidate queue in order.
   - Candidate order:
     - forced inlining / tighter helper boundaries around `nextdiff_fast(...)`
     - split bit-buffer refill from symbol decode
     - pointer-walk loops instead of indexed addressing
     - branch / reload trimming in first-row vs later-row decode
     - wider Huffman lookup or refill only if earlier candidates moved the median
   - Revert immediately on failed thresholds.
   - If two consecutive candidates land in the `3-5%` band but fail the consistency rule, stop the queue and pivot to final validation.

4. Phase D: final pivot decision.
   - Run one more `--threads 4` playback profile on the accepted binary.
   - If decoder remains `>50%` of playback time, keep the next seam on the decoder.
   - If decoder falls below roughly `40%`, document the new hottest stage and pivot the next session there.

5. Phase E: cleanup state.
   - Re-run final `console_tests --check-golden` and `pipeline_tests --check-golden`.
   - Do not commit.
   - Leave the worktree containing only intentional source, doc, and artifact changes from the pass.

## Historical resume prompt

Use this in the next session:

`Read .claude/analysis/mlv-playback-investigation.md and .claude/analysis/cross-agent-handoffs.md, then execute the predictor-1 LJ92 overnight pass from the current green state. Target the active predictor-1 fast path in src/mlv/liblj92/lj92.c:509 via the dispatch at lj92.c:740, not only the generic fallback. Add separate coarse fast-path telemetry behind MLVAPP_PRED1_FASTPATH_MEASUREMENT=1 while keeping the intrusive generic split unchanged. Rebuild using the Qt/MinGW toolchain on this machine, validate with console_tests --check-golden, app-backed console_tests with MLVAPP_PROFILE_EXE pointing at the fresh MLVApp.exe, and pipeline_tests --check-golden. Establish a repeated large_dual_iso baseline with 3 runs at --threads 1 and 3 runs at --threads 4, long enough to keep a substantial warm window; once you choose the total frames and warmup discard count, keep them fixed across all baseline and candidate runs. Use warm medians only. Apply these rules without improvisation: <3% improvement is noise and must be reverted, 3-5% improvement only counts if all 3 repeats improve with no regression outlier, >=5% improvement can be kept if threads=4 raw_uint16_ms does not regress beyond about 2%. Work the candidate queue in order: forced inlining/helper-boundary cleanup, bit-buffer refill split, pointer-walk loops, branch trimming, then Huffman/refill work last. Update ANALYSIS_LOG.md, mlv-playback-investigation.md, and cross-agent-handoffs.md with baseline numbers, accepted/rejected candidates, thresholds applied, and the next seam. Do not commit; leave only intentional source/doc/artifact changes in the worktree for review next session.`

## Useful artifacts and commands

- Green local gates:
  - `C:\!Layi Wkspc\MLV-App\.claude\worktrees\festive-boyd\tests\build\console\release\console_tests.exe --check-golden`
  - `C:\!Layi Wkspc\MLV-App\.claude\worktrees\festive-boyd\tests\build\pipeline\release\pipeline_tests.exe --check-golden`
- Current app binary:
  - [MLVApp.exe](</C:/!Layi%20Wkspc/MLV-App/.claude/worktrees/festive-boyd/platform/qt/build/Desktop_Qt_6_10_2_MinGW_64_bit-Release/release/MLVApp.exe:1>)
- Predictor-1 profiling artifacts:
  - [large_dual_iso_preview_t1_generic_split_refined.json](</C:/!Layi%20Wkspc/MLV-App/.claude/worktrees/festive-boyd/.claude/profiling/20260423-lj92-generic-split-refined/large_dual_iso_preview_t1_generic_split_refined.json:1>)
  - [tiny_dual_iso_t1_generic_split_refined.json](</C:/!Layi%20Wkspc/MLV-App/.claude/worktrees/festive-boyd/.claude/profiling/20260423-lj92-generic-split-refined/tiny_dual_iso_t1_generic_split_refined.json:1>)
- Post-hoist replay artifacts:
  - [large_dual_iso_preview_t1_after_generic_hoist.json](</C:/!Layi%20Wkspc/MLV-App/.claude/worktrees/festive-boyd/.claude/profiling/20260423-lj92-generic-split-refined/large_dual_iso_preview_t1_after_generic_hoist.json:1>)
  - [large_dual_iso_preview_t1_after_generic_hoist_rerun1.json](</C:/!Layi%20Wkspc/MLV-App/.claude/worktrees/festive-boyd/.claude/profiling/20260423-lj92-generic-split-refined/large_dual_iso_preview_t1_after_generic_hoist_rerun1.json:1>)
  - [large_dual_iso_preview_t1_after_generic_hoist_rerun2.json](</C:/!Layi%20Wkspc/MLV-App/.claude/worktrees/festive-boyd/.claude/profiling/20260423-lj92-generic-split-refined/large_dual_iso_preview_t1_after_generic_hoist_rerun2.json:1>)
