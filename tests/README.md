# Test Harness

This tree contains the regression-safety scaffold for MLV-App.

Current layers:
- `console/` lightweight non-GUI regression tests
- `alloc/` allocation-tracking smoke tests and allocator scaffolding
- `gui/` optional Qt Test smoke coverage
- `common/` shared helpers
- `fixtures/` checked-in test assets and placeholders
- `fuzz/` optional fuzz harnesses
- `perf/` benchmark harness and baselines
- `pipeline/` direct in-process engine goldens against the tiny Dual ISO clip

The initial goal is to make correctness checks runnable in CI without changing
the main app build or requiring large clip fixtures.

Current console harness behavior:
- `console_tests --hash-output <path>` writes the current seed artifact hashes
- `console_tests --check-golden` compares against `tests/fixtures/golden/hashes.json`
- `console_tests --check-golden <path>` compares against an explicit golden file
- `console_tests` also includes a local AVX parity check that builds
  `tests/console/avx_parity_helper.pro` twice (default + `MLVAPP_ENABLE_AVX=1`)
  and asserts both builds render identical frame hashes on the checked-in Dual
  ISO fixtures when Qt/qmake + a make tool are available

Current pipeline/perf behavior:
- `pipeline_tests --check-golden --hash-output <path>` compares direct frame hashes against `tests/fixtures/golden/pipeline_hashes.json`
- `pipeline_tests` also pins current processing-reuse behavior: Dual ISO exact-frame reuse after runtime solve, chroma-smooth scratch reuse, median/RBF reuse stability, Sobel scratch parity, and no-reset cache invalidation
- `pipeline_tests` also pins Dual ISO full20bit helper scratch reuse for the
  active autodetect, exposure-match, and AMaZE helper paths, including the
  persistent autodetect histogram scratch in
  `C:/!Layi Wkspc/MLV-App/.claude/worktrees/festive-boyd/src/mlv/llrawproc/dualiso.c`
- `pipeline_tests` now also guards two ownership/reuse contracts in llrawproc:
  repeated renders with unchanged pixel maps stop recopies on the steady path,
  and the new dark-frame worker snapshot reuses its copied payload across
  frames
- `perf_tests --iterations <n> --json-output <path>` measures tiny Dual ISO plus the checked-in `large_dual_iso` fixture and writes a JSON artifact with results plus gate status
- `perf_tests --stage-log <path>` appends the stage-timing stream to a file instead of relying on console capture
- `perf_tests --cold-8bit` clears processed preview caches before every 8-bit sample so `full8` and `preview8` stop reporting warmed exact-cache hits as their median path
- `perf_tests --raw-cache-mb <n> --cache-cpu-cores <n>` leaves the MLV raw/debayer cache enabled inside the harness so local profiling can measure cache-worker contention
- `perf_tests` now also includes a checked-in synthetic dark-frame scenario
  (`tiny_dual_iso_darkframe`) so the worker dark-frame snapshot split can be
  measured directly without relying on receipts that keep `darkFrame=0`
- `perf_tests` now also includes a checked-in synthetic forced-stripes scenario
  (`tiny_dual_iso_stripes`) so the stripes compute/publish split can be
  measured directly without depending on incidental receipt settings
- `perf_tests --update-baseline` refreshes the auto-selected local perf baseline profile in `tests/perf/baselines.json`
- `perf_tests --require-baseline` turns the local baseline profile from a warning into a hard gate
- the warmed local baseline profile continues to gate the stable 16-bit medians
  plus the warmed 8-bit medians; the `--cold-8bit` profile variant now gates
  the colder 8-bit medians specifically so the processed-frame exact-cache path
  and the true 16->8 render path are both covered without making 16-bit cold
  runs responsible for the extra `--cold-8bit` noise
- `tests/perf/run_runtime_profile.ps1` is the local no-intervention profiling wrapper; it discovers or builds `perf_tests.exe`, runs a thread-count matrix, and writes JSON + stage-log artifacts into `.claude/profiling/<timestamp>/`; pass `-Cold8bit` when you want a colder 8-bit processed-frame measurement and `-RawCacheMB/-CacheCpuCores` when you want the raw cache left on for contention profiling
- the checked-in perf receipts still run with `darkFrame=0`, so those warm
  cache-enabled profiling runs measure surrounding llrawproc behavior but do
  not directly benchmark the dark-frame snapshot split
- that runtime-profile wrapper now prefers the current
  `C:/!Layi Wkspc/MLV-App/.claude/worktrees/festive-boyd/tests/perf/debug/perf_tests.exe`
  before searching older build trees, and it keeps profiling artifacts even if
  perf assertions fail (useful when the point is diagnosis, not gating)
- Set `MLVAPP_STAGE_TIMING=1` to emit per-stage timing lines while rendering through the app, pipeline tests, or perf harness
- the main app also supports a headless Qt playback profiler via
  `MLVApp.exe --profile-playback --input <clip> --output <json>`; that mode
  steps frames through the real `MainWindow`/render-thread/`drawFrameReady()`
  path and can optionally apply a receipt, enable scopes/zebras, leave raw
  caching on, and capture a stage-timing log

Current GUI behavior:
- `gui_tests` now defaults `QT_QPA_PLATFORM` to `offscreen` when the variable is
  unset, and on Windows it suppresses native crash dialogs before constructing
  `QApplication`; this keeps direct local launches from surfacing modal
  fail-fast popups in this workspace
- `gui_tests` covers the current GPU presenter seam only: environment-gated install, CPU fallback visibility when the presenter is absent, fallback hide/show when a texture-backed presentation is active, and the new 16-bit RGB presenter handoff used by the experimental viewport
- `gui_tests` also covers the extracted MainWindow GPU preview decision matrix:
  when the 16-bit presenter path is allowed, when scopes force the app back to
  the 8-bit GPU image path, when shader-side zebra processing is allowed, and
  how sampler/zebra presentation options are derived from UI state
- `gui_tests` also covers pixel-exact presenter hashes for RGB888/RGB16 uploads,
  scope image regression hashes (histogram/vector scope/waveform), and
  zebra-processing parity seams on both the 8-bit and 16-bit presenter paths
- `gui_tests` now includes live `ScopesLabel::setScope()` regressions for raw
  histogram, waveform, parade, and vector scope dispatch. Histogram/vector
  scope remain exact widget-output goldens; waveform/parade use a coarsened
  widget signature to stay stable on the software-rendered Qt path in this
  workspace.
- the zebra parity check is intentionally skipped on the local `llvmpipe`
  software GL renderer, which does not produce stable shader-processed output in
  this workspace
- There is intentionally no GPU compute parity coverage yet because current OpenGL support is presentation-only, not image processing

Current local status:
- `console_tests --check-golden`: pass
- `alloc_tests`: pass
- `pipeline_tests --check-golden`: pass
- `pipeline_tests --check-golden` now also includes a forced-rerender llrawproc
  guard, `DualIsoPipeline.StablePixelMapsReuseWorkerCopiesAcrossForcedReprocess`,
  which invalidates processed preview caches between renders and verifies the
  worker pixel-map copies stay warm across the real rerender
- `gui_tests`: pass, with the zebra parity seam skipped on `llvmpipe`
- `perf_tests --iterations 10 --require-baseline`: pass when run in isolation
- cache-enabled profiling runs are intentionally measurement-first; they may
  preserve artifacts even when preview-speedup assertions fail
- `perf_tests --iterations 10 --cold-8bit --require-baseline`: pass
- `fuzz_receipt_loader tests/fixtures/receipts`: pass
- `fuzz_lj92 tests/fixtures/clips/tiny_dual_iso.mlv`: pass
- `fuzz_mlv_open tests/fixtures/clips/tiny_dual_iso.mlv`: pass

Playback-profile smoke coverage:
- `console_tests` now includes a real subprocess smoke test for
  `MLVApp.exe --profile-playback`
- that test injects the local Qt runtime via `QLibraryInfo` and forces the
  `windows` platform plugin; do not force `QT_QPA_PLATFORM=offscreen` for this
  seam in this workspace. If Linux CI is added later, this seam will need
  either an `xvfb`-backed run or explicit offscreen-plugin deployment there.

Current experimental GPU-present status:
- the environment-gated OpenGL viewport can now accept either 8-bit `QImage` frames or direct 16-bit RGB frame buffers
- when the viewport is installed and zebras/scopes are off, the main preview path can hand the processed 16-bit frame directly to the presenter and skip the CPU-side 16->8 reduction for that display path
- zebra overlays now stay in the fragment shader on both the 8-bit image
  presenter path and the 16-bit presenter path; the CPU still computes the
  lightweight under/over flags needed by scopes
- the experimental GPU preview-processing subset now has fixture-backed pipeline
  goldens in `tests/fixtures/golden/pipeline_hashes.json`; it is still
  display-side processing on already-debayered 16-bit RGB, but the subset is no
  longer tested only for determinism
- the backend-parametric pipeline shell now has a real offscreen GPU execution
  path for that same subset:
  - CPU backend always runs and records
    `tiny_dual_iso.preview_processing.cpu.frame0`
  - GPU backend now probes runtime availability and either executes with a
    CPU-vs-GPU tolerance check or skips on known unsupported/software GL
    conditions such as `QOffscreenSurface`/`QOpenGLContext` setup failure or
    `llvmpipe`
  - the pipeline golden runner now treats expected `.gpu.` keys as optional
    when the GPU backend is skipped at runtime, so one flat manifest can stay
    valid across both GPU-capable and software-only hosts
- that subset now tracks `exposure_stops` through the copied precomputed LUTs;
  the pipeline test records exact subset-output hashes plus config-signature
  drift when exposure changes
- there is still intentionally no claim of full CPU/GPU image-processing parity
  for that subset; it is a stable drift detector for the current display-side
  processing path, not a proof that the subset matches the full CPU pipeline
- playback-profile mode now also exposes
  `--gpu-preview-processing <auto|cpu|gpu>` alongside `--gpu-viewport` so the
  production preview-policy seam can be exercised without depending only on the
  environment variable
- playback-profile JSON now reports the selector and the real per-frame seam:
  - metadata:
    `gpu_preview_processing_backend_request`,
    `gpu_preview_processing_environment_requested`
  - per-frame:
    `gpu16_preview_active`,
    `gpu_preview_processing_active`
- `console_tests` now includes explicit playback-profile selector contracts for:
  - default `auto` metadata
  - `--gpu-preview-processing cpu`
  - invalid `--gpu-preview-processing` rejection
- `.github/workflows/tests.yml` now exists as the initial CI scaffold:
  - Windows-only for now
  - provisions Qt 6.10.2 + MinGW 13.1 via `aqtinstall`
  - runs `console_tests --check-golden` and `pipeline_tests --check-golden`
  - intentionally does not run `gui_tests` yet; the fresh offscreen GUI target
    was not stable enough on this host to promote into the first workflow cut
- GPU support is still limited to display-side processing; debayer, llrawproc,
  and the main color-processing pipeline remain CPU work today
- the current llrawproc ownership work has reached a practical decision point
  on the ordinary warm Dual ISO path: the direct perf JSON now includes
  `llrawproc_dualiso_refine_lock_ms` and `llrawproc_publish_lock_ms`, and the
  latest warm same-build reruns show those medians at `0.0 ms` on the standard
  warm Dual ISO scenarios. Future CPU work should focus on colder/contention
  seams rather than assuming more publish/refine slicing will keep paying off.
- the forced-rerender pipeline coverage now uses `resetMlvCachedFrame(...)`
  where real llrawproc re-entry matters:
  - Dual ISO-on raw debayered output is deterministic across all renders after
    the `diso_pattern` sign-fix in
    `src/mlv/llrawproc/dualiso.c`
  - Dual ISO-on processed full-output is also deterministic across all renders
    after that same fix
  - Dual ISO-off raw debayered output is deterministic on the first forced
    rerender
  - the pixel-map reuse guard now proves real worker-map reuse on later forced
    rerenders without claiming full-frame determinism for the combined Dual ISO
    + focus/bad-pixel path
- the latest current-tree focused verification run of
  `pipeline_tests --check-golden` passed with
  `33 tests / 432 assertions / 0 failures`

Current experimental GPU-debayer status:
- `pipeline_tests` now also includes a backend-parametric debayer shell:
  - CPU bilinear frame-0 goldens under
    `tiny_dual_iso.debayer.bilinear.cpu.frame0`
  - CPU AMaZE frame-0 goldens under
    `tiny_dual_iso.debayer.amaze.cpu.frame0`
  - a GPU-debayer skip tripwire that stays green on this host until a real
    raw-CFA GPU backend exists
- this is a regression shell for the current CPU debayer boundary, not evidence
  of a working GPU debayer backend yet
- the future production GPU debayer seam is still the raw-debayer boundary in
  `src/mlv/video_mlv.c` / `src/mlv/frame_caching.c`, not the already-landed
  post-debayer preview-processing shader path
- the latest current-tree focused verification run of the debayer shell via
  `tests/build-debayer-shell/release/pipeline_tests.exe --check-golden`
  passed with `42 tests / 485 assertions / 3 skips / 0 failures`

GPU debayer update (2026-04-22):
- the debayer shell now uses the existing production-side
  `platform/qt/GpuDebayer.{h,cpp}` backend rather than a duplicate
  test-only bilinear implementation
- bilinear GPU debayer now behaves like the preview-processing shell:
  - if a supported OpenGL backend is present, the test compares CPU vs GPU
    within tolerance
  - if the host is unsupported/software-only, the test skips with a known
    runtime reason
- AMaZE GPU debayer still intentionally skips; it remains the explicit next
  backend flip rather than silently turning green
- the latest current-tree focused verification run of the converged debayer
  shell via
  `tests/build-gpu-debayer/release/pipeline_tests.exe --check-golden`
  passed with `43 tests / 491 assertions / 4 skips / 0 failures`
- no `.gpu.` debayer golden is checked in yet; on a hardware-backed local host,
  the first successful bilinear GPU run should intentionally force a manifest
  update by recording `tiny_dual_iso.debayer.bilinear.gpu.frame0`

Experimental GPU bilinear debayer (2026-04-22):
- playback-profile mode now accepts:
  - `--gpu-bilinear-debayer auto`
  - `--gpu-bilinear-debayer cpu`
  - `--gpu-bilinear-debayer gpu`
- the selector is production-adjacent control for the existing experimental
  bilinear GPU debayer path only; AMaZE remains intentionally unsupported
- `"requested"` vs `"active"` in playback-profile output means:
  - requested:
    `metadata.gpu_bilinear_debayer_backend_request`
    and `metadata.gpu_bilinear_debayer_environment_requested`
  - active:
    per-frame `gpu_bilinear_debayer_active`
    plus optional `gpu_bilinear_debayer_renderer`
    / `gpu_bilinear_debayer_fallback_reason`
- playback-profile output also records a one-time probe:
  - `gpu_bilinear_debayer_probe_available`
  - `gpu_bilinear_debayer_probe_reason`
  - `gpu_bilinear_debayer_probe_renderer`
- expected behavior on `llvmpipe` or another software renderer:
  - the probe reports unavailable
  - the debayer path falls back to CPU
  - `gpu_bilinear_debayer_active` stays `false`
- local suites covering this feature now include:
  - app-backed `console_tests --check-golden`
  - `gui_tests`
  - `pipeline_tests --check-golden` debayer shell / tripwire coverage
- hardware-backed local validation is still required for:
  - real bilinear GPU parity confirmation
  - the first `.gpu.` debayer golden capture
  - meaningful GPU debayer performance numbers
- on Windows local GPU/profile entrypoints now prefer `QT_OPENGL=desktop`
  when the caller has not already set `QT_OPENGL`:
  - `MLVApp --profile-playback`
  - app-backed `console_tests`
  - `pipeline_tests`
  - `gui_tests`
- playback-profile metadata now records:
  - `qt_opengl_environment`
  - `qt_qpa_platform_environment`
  so host/VM runs can prove which Qt GL backend choice was actually exercised
- playback-profile metadata now also records the Dual ISO playback/runtime split:
  - `dual_iso_mode_selected`
  - `dual_iso_mode_effective`
  - `dual_iso_preview_runtime_active`
- playback-profile samples now also expose CPU stage telemetry for local diagnosis:
  - outer stages:
    `raw_uint16_ms`,
    `llrawproc_ms`,
    `debayered_frame_ms`,
    `processing_ms`,
    `processed16_total_ms`,
    `processed16_to_8bit_ms`
  - Dual ISO preview internals:
    `dual_iso_preview_histogram_ms`,
    `dual_iso_preview_regression_ms`,
    `dual_iso_preview_rowscale_ms`
  - exclusive debayer internals:
    `raw_float_convert_ms`,
    `debayer_exclusive_ms`,
    `debayer_kernel_ms`,
    `debayer_pipeline_other_ms`
- processing internals:
    `processing_core_ms`,
    `processing_core_color_ms`,
    `processing_highest_green_ms`
  - raw uint16 internals:
    `raw_uint16_disk_read_ms`,
    `raw_uint16_decompress_ms`,
    `raw_uint16_decompress_prepare_ms`,
    `raw_uint16_decompress_execute_ms`,
    `raw_uint16_unpack_ms`,
    `raw_uint16_copy_ms`,
    `raw_uint16_other_ms`
  - `dual_iso_preview_override_active`
- expected interpretation:
  - HQ receipt during playback:
    `selected=1`, `effective=2`, `runtime_active=true`,
    `override_active=true`
  - explicit preview receipt during playback:
    `selected=2`, `effective=2`, `runtime_active=true`,
    `override_active=false`
- playback-processing profile controls now support:
  - `--playback-processing auto`
  - `--playback-processing receipt`
  - `--playback-processing subset`
- current local semantics:
  - `auto` defaults to the receipt path
  - `subset` is an explicit opt-in
  - this is intentional because the subset path currently loses to the receipt
    path on the tested Dual ISO CPU-only VM workload
- local AVX2 builds are already supported for x86 hosts via:
  - `platform/qt/avx_optin.pri`
  - set `MLVAPP_ENABLE_AVX2=1` before running `qmake`
  - parity coverage for the helper build already exists in
    `tests/console/test_avx_golden.cpp`
- the processed-frame cache now has two layers:
  - exact-current 16-bit cache for repeated same-frame requests
  - small 16-bit revisit cache for nearby replays / short loops
  - existing multi-slot 8-bit cache for preview output
- current revisit-specific proof is functional, not benchmarked:
  - `DualIsoPipeline.ProcessedFrame16CacheKeepsNearbyFramesWarm`
  - if you want a wall-clock number for this seam, use a revisit-aware harness
    rather than the normal forward-only `--profile-playback` path
- current local Dual ISO preview thread matrix on the VM:
  - `--threads 4` is the clean fastest point measured (`111.84 ms` warm)
  - `--threads 8` regresses materially on this VM (`158.73 ms` warm)
  - the raw split shows the remaining bottleneck is compressed decode
    (`raw_uint16_decompress_ms`), not disk I/O or bit-unpack
- decode-ahead prefetch status:
  - the raw-`uint16` decode-ahead worker is **default-on**; it hides foreground
    decode time on hosts with spare CPU headroom and produces `raw_uint16_prefetch_hit=true`
    on warm samples for compressed raw
  - set `MLVAPP_DISABLE_RAW_UINT16_PREFETCH=1` (values `1`, `true`, `yes`, `on`) to
    disable the worker and fall back to foreground decode — use this if the host
    regresses on end-to-end playback latency
  - the older `MLVAPP_EXPERIMENTAL_RAW_UINT16_PREFETCH` env var is inert; setting
    it has no effect (removed from the enable gate)
  - new telemetry field `raw_uint16_prefetch_decode_failures` surfaces the count
    of background decode failures per clip (mutex-protected read of the worker
    counter); it should be `0` on healthy runs, and remains `0` when the worker
    is disabled
- play-start cache preroll status:
  - clicking play now primes the existing raw cache window with a small
    non-blocking 2-frame lookahead
  - this only applies when playback is already using cached AMaZE debayer
  - it is a play-start UX hint, not a measured sustained-FPS optimization
- playback-profile play-start metrics now include:
  - `play_start_preroll_active`
    actual preroll request issued for this run
  - `play_start_preroll_eligible`
    cached-playback mode made preroll possible for this run
  - `play_start_preroll_disabled_by_environment`
    local profiling env disabled the preroll request for this run
  - `play_to_first_frame_measured`
    whether a first requested render completed after the metric was armed
  - `play_to_first_frame_ms`
    time from arming to the first requested frame reaching `drawFrameReady`
- LJ92 profiling fields now distinguish "requested" from "applicable":
  - `raw_uint16_lj92_pred6_split_requested`
    the profiling env asked for the pred6 split
  - `raw_uint16_lj92_predictor`
    actual JPEG predictor used by the frame decode
  - `raw_uint16_lj92_pred6_split_active`
    true only when the frame really decoded through predictor 6
- generic predictor-path profiling fields are also available:
  - `raw_uint16_lj92_generic_split_requested`
  - `raw_uint16_lj92_generic_split_active`
  - `raw_uint16_lj92_generic_total_ms`
  - `raw_uint16_lj92_generic_bitstream_ms`
  - `raw_uint16_lj92_generic_predictor_ms`
  - `raw_uint16_lj92_generic_other_ms`
- important interpretation note for the generic LJ92 split:
  - it instruments inside the per-sample decode loop
  - that makes it useful for relative shape, but too intrusive for honest absolute decoder `ms`
  - use a split-disabled run for sustained playback benchmarking
- important interpretation note:
  - `play_to_first_frame_ms` is an app-level readiness metric
  - it is measured before guaranteed OS-window paint unless
    `wait_for_paint` is enabled
  - use it for A/B trend comparisons, not as a literal “button to visible pixel”

Current fuzz behavior:
- `tests/fuzz/fuzz_targets.pro` builds three opt-in local file-fed fuzz executables:
  `fuzz_receipt_loader`, `fuzz_lj92`, and `fuzz_mlv_open`
- fuzz targets are intentionally not part of the default test tree; they are
  for local/nightly parser hardening rather than every quick regression run
