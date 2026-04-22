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
- writes per-iteration samples, trimmed averages, and coefficient-of-variation
  stats into the JSON artifact so noisy paths can be diagnosed after the run
- can append per-stage timing output to a log file with
  `--stage-log <path>`; this also enables `MLVAPP_STAGE_TIMING=1` for the run
- can invalidate processed preview caches before every 8-bit sample with
  `--cold-8bit` so `full8`/`preview8` measure a colder processed-frame path
  instead of exact-cache hits
- can leave the MLV raw/debayer cache enabled with
  `--raw-cache-mb <n> --cache-cpu-cores <n>` so local profiling can measure
  cache-worker contention instead of the cache-disabled lower bound
- always applies portable relative checks from `tests/perf/baselines.json`
  - `tiny_dual_iso.preview16_speedup_vs_full16.min` remains the portable
    cross-machine "preview must stay faster" floor
  - `large_dual_iso.preview16_speedup_vs_full16.min` is ready for the optional
    larger fixture when that fixture exists locally or is checked in later
- `preview8_speedup_vs_full8` is still reported in output/JSON, but it is not
  treated as a portable hard gate because it was too noisy across local runs
- `full8` medians without `--cold-8bit` should be read as a warmed-cache metric;
  use the cold mode when you want to profile the actual 16-bit-to-8-bit render
  path instead of the exact processed-frame cache
- applies explicit local absolute gates when a matching baseline profile exists
  in `tests/perf/baselines.json`
  - local profile checks currently gate `median_ms` for the stable 16-bit paths
    (`full16` and `preview16`) on each fixture
  - 8-bit paths are still reported and stored in the local profile, but they are
    guarded by the absolute watchdog ceilings and relative speedup floors rather
    than the local median gate because their medians were too bursty across runs
  - the separate `--cold-8bit` profile variant now gates the colder `full8` and
    `preview8` medians directly, so the warmed exact-cache path and the colder
    tone-map/render path are both pinned without making the cold run also own
    the 16-bit median gate
  - `average_ms` is still reported and stored for context, but not used as the
    hard local gate because it is more sensitive to one-off outliers
  - the selected profile defaults to an auto-generated local machine key
- supports refreshing the selected local baseline profile with
  `--update-baseline`
  - update mode still prints any failing checks, but it exits successfully after
    writing the refreshed profile so the baseline can be intentionally advanced
- supports stricter local enforcement with `--require-baseline`
- includes a direct synthetic dark-frame case keyed as
  `tiny_dual_iso_darkframe`; that fixture reuses the tiny checked-in clip and
  injects synthetic dark-frame payload into shared llrawproc state so the
  worker dark-frame snapshot split can be measured on a warm steady path
- respects `MLVAPP_STAGE_TIMING=1` (or `--stage-timing`) to emit per-stage
  timing lines during the benchmark run
- ships a local no-intervention profiling wrapper at
  `tests/perf/run_runtime_profile.ps1` that:
  - prefers the current `tests/perf/debug/perf_tests.exe` before scanning older build trees
  - locates or builds `perf_tests.exe` if that debug binary does not exist
  - runs a small thread-count matrix
  - writes JSON + stage-log artifacts into `.claude/profiling/<timestamp>/`
  - keeps those artifacts even if perf assertions fail, so profiling runs stay useful for diagnosis

Typical local run:

```powershell
$env:PATH='C:\Qt\Tools\mingw1310_64\bin;C:\Qt\6.10.2\mingw_64\bin;' + $env:PATH
.\tests\build-all\perf\release\perf_tests.exe --iterations 10 --json-output .\tests\build-all\perf\perf-results.json
```

Capture a local profiling bundle with stage timing included:

```powershell
.\tests\perf\run_runtime_profile.ps1 -Iterations 10
```

Capture the same bundle while forcing cold 8-bit samples:

```powershell
.\tests\perf\run_runtime_profile.ps1 -Iterations 10 -Cold8bit
```

Capture a contention-oriented bundle with raw caching left enabled:

```powershell
.\tests\perf\run_runtime_profile.ps1 -Iterations 10 -RawCacheMB 128 -CacheCpuCores 4
```

Capture the real Qt playback path instead of the engine-only perf harness:

```powershell
$env:PATH='C:\Qt\Tools\mingw1310_64\bin;C:\Qt\6.10.2\mingw_64\bin;' + $env:PATH
$env:QT_OPENGL='desktop'
.\platform\qt\build-headless\release\MLVApp.exe `
  --profile-playback `
  --input tests/fixtures/clips/large_dual_iso.mlv `
  --receipt tests/fixtures/receipts/large_dual_iso_hq.marxml `
  --frames 8 `
  --output .claude/profiling/playback-ui/large-step-cache.json `
  --stage-log .claude/profiling/playback-ui/large-step-cache-stage.log `
  --threads 1 `
  --raw-cache-mb 128 `
  --cache-cpu-cores 4
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
- `--stage-log <path>` appends stage-timing output directly to a file for
  later analysis
- the JSON output now includes per-scenario residual aggregates when available;
  the currently stable field is `residuals.llrawproc_shared_lock_ms`, which is
  measured inside the render thread and does not rely on downstream stage-log
  pairing
- newer llrawproc ownership slices also emit:
  - `residuals.llrawproc_dualiso_refine_lock_ms`
  - `residuals.llrawproc_publish_lock_ms`
  These are useful when judging whether additional Dual ISO ownership work is
  still paying off, and they should be compared warm-vs-warm in the same build
  session.
- `--cold-8bit` clears processed preview caches before each 8-bit sample so
  cache hits do not dominate the reported `full8` median
- `--raw-cache-mb <n>` enables the MLV raw cache inside the harness for local
  contention profiling
- `--cache-cpu-cores <n>` chooses how many cache-worker cores the harness uses
  when raw caching is enabled
- the built-in tiny correctness fixtures now include:
  - `tiny_dual_iso_darkframe` for the worker dark-frame snapshot seam
  - `tiny_dual_iso_stripes` for the forced stripes compute/publish seam
  - `tiny_dual_iso_stripes` is also the direct regression seam for the
    claim-before-unlock `compute_stripes` flow, so inspect its JSON residuals
    even if the overall perf harness exits nonzero on an unrelated threshold
- `--help` prints the local harness usage
