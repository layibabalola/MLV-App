## Playback-Profile Sample Flakes (2026-04-22)

### Verified locally

- The hard `ClipGolden` failure around `playback_processing_effective` was not reproducible after a fresh rebuild of both the app and console test binary. The stale failure came from older binaries that predated the current `receipt` expectation in `tests/console/test_clip_golden.cpp`.
- Two real playback-profile sample emission races remained in `platform/qt/MainWindow.cpp` and are now patched:
  - `platform/qt/MainWindow.cpp:1393-1408`
    - If `gpu_bilinear_debayer_fallback_reason` is present and the renderer string has not been published yet, the sample now emits `gpu_bilinear_debayer_renderer = "unknown"` instead of omitting the field.
  - `platform/qt/MainWindow.cpp:1411-1419`
    - `engine_completion_ns`, `engine_latency_ms`, and `presentation_overhead_ms` are now emitted for every sample using `completionNs` as a fallback when the direct `engineCompletionNs` signal has not landed yet.
    - `engine_latency_direct_measured` records whether the direct engine completion timing was available.
- Fresh app-backed `ClipGolden` runs captured after the patch were green:
  - `.claude/profiling/20260422-clipgolden-flake-fix/run_4.txt`
  - `.claude/profiling/20260422-clipgolden-flake-fix/run_5.txt`
  - `.claude/profiling/20260422-clipgolden-flake-fix/run_6.txt`
  - `.claude/profiling/20260422-clipgolden-flake-fix/run_7.txt`
  - `.claude/profiling/20260422-clipgolden-flake-fix/run_8.txt`
- Each captured run above reports:
  - `tests=35 assertions=333 skipped=0 failed=0`

### Cross-checked from prior analysis

- Claude independently identified the same two flaky fields before the source patch:
  - `gpu_bilinear_debayer_renderer` could be missing while `gpu_bilinear_debayer_fallback_reason` was present.
  - `engine_latency_ms` could be missing when the direct timing signal lost a race against sample emission.
- Those findings match the current source locations and the patched fix points in `MainWindow.cpp:1393-1419`.

### Needs runtime profiling

- A full 10-run fresh-binary app-backed matrix is still worth doing when time budget allows, but it is no longer a blocker for treating the source fix as landed.
- If the flake recurs after this patch, the next thing to log is whether `m_pRenderThread->lastGpuBilinearRendererDescription()` and `engineCompletionNs` are arriving late on the same frame or on a previous frame boundary.

### Next steps ranked by impact / effort

1. Low effort / medium impact: rerun the full 10x app-backed `ClipGolden` matrix with fresh binaries and replace the prior retry-contract note with a cleaner post-fix result if it stays green.
2. Medium effort / medium impact: move the app-backed test launcher onto a clean rebuild helper so stale-binary contamination cannot masquerade as a product regression again.
3. Low effort / low impact: if future stages add the same runtime skip / fallback metadata pattern, factor the field-emission guard into a small helper instead of open-coding it in `MainWindow.cpp`.
