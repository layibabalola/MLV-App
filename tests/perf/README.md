# Performance Benchmarks

`perf_tests` is a lightweight benchmark harness built on the same tiny Dual ISO
fixture used by the direct pipeline goldens. It also benchmarks the checked-in
`large_dual_iso` fixture in the same run, and can be pointed at another local
fixture when you want to override that default.

Current behavior:
- measures `getMlvProcessedFrame16()` and `getMlvProcessedFrame8()` for both
  full Dual ISO and preview Dual ISO
- always benchmarks the checked-in `tiny_dual_iso` fixture
- benchmarks the checked-in `large_dual_iso` fixture when its paired receipt is
  present under the conventional path
- can optionally benchmark one extra fixture from:
  - `--extra-clip`, `--extra-receipt`, and `--extra-label`
  - or `MLVAPP_PERF_EXTRA_CLIP`, `MLVAPP_PERF_EXTRA_RECEIPT`,
    `MLVAPP_PERF_EXTRA_LABEL`, and `MLVAPP_PERF_EXTRA_SAMPLE_FRAMES`
  - or the conventional checked-in names
    `tests/fixtures/clips/large_dual_iso.mlv` and
    `tests/fixtures/receipts/large_dual_iso_hq.marxml`
- reports `average_ms`, `median_ms`, `min_ms`, `max_ms`, and derived FPS for
  each measured fixture/mode pair
- writes a JSON artifact with metadata, results, and check status when
  `--json-output <path>` is provided
- always applies portable relative checks from `tests/perf/baselines.json`
  - `tiny_dual_iso.preview16_speedup_vs_full16.min` remains the portable
    cross-machine "preview must stay faster" floor
  - `large_dual_iso.preview16_speedup_vs_full16.min` is ready for the optional
    larger fixture when that fixture exists locally or is checked in later
  - `preview8_speedup_vs_full8` is still reported in output/JSON, but it is not
    treated as a portable hard gate because it was too noisy across local runs
- applies explicit local absolute gates when a matching baseline profile exists
  in `tests/perf/baselines.json`
  - local profile checks currently gate `median_ms` for the stable 16-bit paths
    (`full16` and `preview16`) on each fixture
  - 8-bit paths are still reported and stored in the local profile, but they are
    guarded by the absolute watchdog ceilings and relative speedup floors rather
    than the local median gate because their medians were too bursty across runs
  - `average_ms` is still reported and stored for context, but not used as the
    hard local gate because it is more sensitive to one-off outliers
  - the selected profile defaults to an auto-generated local machine key
- supports refreshing the selected local baseline profile with
  `--update-baseline`
  - update mode still prints any failing checks, but it exits successfully after
    writing the refreshed profile so the baseline can be intentionally advanced
- supports stricter local enforcement with `--require-baseline`
- respects `MLVAPP_STAGE_TIMING=1` (or `--stage-timing`) to emit per-stage
  timing lines during the benchmark run

Typical local run:

```powershell
$env:PATH='C:\Qt\Tools\mingw1310_64\bin;C:\Qt\6.10.2\mingw_64\bin;' + $env:PATH
.\tests\build-all\perf\release\perf_tests.exe --iterations 10 --json-output .\tests\build-all\perf\perf-results.json
```

Benchmark tiny plus one larger local fixture without checking the larger clip
into the repo:

```powershell
$env:PATH='C:\Qt\Tools\mingw1310_64\bin;C:\Qt\6.10.2\mingw_64\bin;' + $env:PATH
.\tests\build-all\perf\release\perf_tests.exe `
  --iterations 10 `
  --extra-clip C:\bench\large_dual_iso.mlv `
  --extra-receipt tests/fixtures/receipts/tiny_dual_iso_hq.marxml `
  --extra-label large_dual_iso `
  --extra-sample-frames 8
```

The extra fixture is intended for broader playback/perf coverage, so it cycles
across more distinct frames than the tiny correctness fixture by default.

Seed or refresh the current machine baseline profile:

```powershell
$env:PATH='C:\Qt\Tools\mingw1310_64\bin;C:\Qt\6.10.2\mingw_64\bin;' + $env:PATH
.\tests\build-all\perf\release\perf_tests.exe --iterations 10 --update-baseline
```

Require a matching local profile and fail if it is missing or stale:

```powershell
$env:PATH='C:\Qt\Tools\mingw1310_64\bin;C:\Qt\6.10.2\mingw_64\bin;' + $env:PATH
.\tests\build-all\perf\release\perf_tests.exe --iterations 10 --require-baseline
```

Useful options:
- `--baseline <path>` overrides the baseline file location
- `--baseline-profile <id>` lets you pin a named profile instead of the auto
  local key
- `--extra-clip <path>` overrides the conventional larger fixture with a
  different local clip
- `--extra-receipt <path>` points the extra fixture at a receipt
- `--extra-label <id>` controls the result/baseline key prefix for the extra
  fixture
- `--extra-sample-frames <n>` controls how many distinct frames the extra
  fixture cycles through during measurement
- `--regression-pct <pct>` overrides the allowed slowdown versus the selected
  local baseline
- `--help` prints the local harness usage
