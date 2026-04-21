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
- `perf_tests --iterations <n> --json-output <path>` measures tiny Dual ISO plus the checked-in `large_dual_iso` fixture and writes a JSON artifact with results plus gate status
- `perf_tests --update-baseline` refreshes the auto-selected local perf baseline profile in `tests/perf/baselines.json`
- `perf_tests --require-baseline` turns the local baseline profile from a warning into a hard gate
- Set `MLVAPP_STAGE_TIMING=1` to emit per-stage timing lines while rendering through the app, pipeline tests, or perf harness

Current GUI behavior:
- `gui_tests` covers the current GPU presenter seam only: environment-gated install, CPU fallback visibility when the presenter is absent, fallback hide/show when a texture-backed presentation is active, and the new 16-bit RGB presenter handoff used by the experimental viewport
- `gui_tests` also covers pixel-exact presenter hashes for RGB888/RGB16 uploads,
  scope image regression hashes (histogram/vector scope/waveform), and a
  zebra-processing parity seam on the 16-bit presenter path
- the zebra parity check is intentionally skipped on the local `llvmpipe`
  software GL renderer, which does not produce stable shader-processed output in
  this workspace
- There is intentionally no GPU compute parity coverage yet because current OpenGL support is presentation-only, not image processing

Current local status:
- `console_tests --check-golden`: pass
- `alloc_tests`: pass
- `pipeline_tests --check-golden`: pass
- `gui_tests`: pass, with the zebra parity seam skipped on `llvmpipe`
- `perf_tests --iterations 10 --require-baseline`: pass
- `fuzz_receipt_loader tests/fixtures/receipts`: pass
- `fuzz_lj92 tests/fixtures/clips/tiny_dual_iso.mlv`: pass
- `fuzz_mlv_open tests/fixtures/clips/tiny_dual_iso.mlv`: pass

Current experimental GPU-present status:
- the environment-gated OpenGL viewport can now accept either 8-bit `QImage` frames or direct 16-bit RGB frame buffers
- when the viewport is installed and zebras/scopes are off, the main preview path can hand the processed 16-bit frame directly to the presenter and skip the CPU-side 16->8 reduction for that display path
- zebra overlays are now allowed to stay on the GPU 16-bit presenter path and
  are applied in the fragment shader when the driver path supports stable parity
- GPU support is still presentation-only; debayer, llrawproc, and color processing remain CPU work today

Current fuzz behavior:
- `tests/fuzz/fuzz_targets.pro` builds three opt-in local file-fed fuzz executables:
  `fuzz_receipt_loader`, `fuzz_lj92`, and `fuzz_mlv_open`
- fuzz targets are intentionally not part of the default test tree; they are
  for local/nightly parser hardening rather than every quick regression run
