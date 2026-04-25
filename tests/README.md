# Test Tree Index

This is the regression-safety scaffold for MLV App. Detailed reference
material lives under [`docs/`](../docs/) — this page is just a directory
map.

## Subdirectories at a glance

- `console/` — lightweight non-GUI regression tests.
- `alloc/` — allocation-tracking smoke tests and allocator scaffolding.
- `gui/` — optional Qt Test smoke coverage.
- `common/` — shared helpers.
- `fixtures/` — checked-in test assets, receipts, and golden manifests.
- `fuzz/` — opt-in fuzz harnesses (not in CI).
- `perf/` — `perf_tests` benchmark harness, baselines, and the
  `run_runtime_profile.ps1` wrapper.
- `pipeline/` — direct in-process engine goldens against the tiny Dual ISO
  clip.

## Where to read

- Main test harness, golden-hash contract, backend-parametric shells, GPU
  gating, and current local status:
  [`docs/13-testing-infrastructure.md`](../docs/13-testing-infrastructure.md).
- `perf_tests` reference, `run_runtime_profile.ps1`, the headless
  `MLVApp.exe --profile-playback` mode, telemetry key list, and baselines:
  [`docs/14-performance-benchmarking.md`](../docs/14-performance-benchmarking.md).
- Committed clips, receipts, and golden manifests under `fixtures/`:
  [`docs/15-test-fixtures.md`](../docs/15-test-fixtures.md).
- Fuzz targets (`fuzz_receipt_loader`, `fuzz_lj92`, `fuzz_mlv_open`):
  [`docs/16-fuzz-testing.md`](../docs/16-fuzz-testing.md).
