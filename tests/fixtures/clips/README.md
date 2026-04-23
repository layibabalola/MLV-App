# Clip Fixtures

The first clip-backed golden test expects this file:

- `tests/fixtures/clips/tiny_dual_iso.mlv`

Optional larger perf fixture convention:

- `tests/fixtures/clips/large_dual_iso.mlv`
- `tests/fixtures/receipts/large_dual_iso_hq.marxml`

Current fixture:
- `tiny_dual_iso.mlv`
- 2 frames
- 7,931,239 bytes
- derived from `C:\temp\MLV\Processed\M02-1344.MLV`
- source clip carries a `DISO` block and is treated as the local Dual ISO source fixture

Checked-in larger perf fixture:
- `large_dual_iso.mlv`
- 16 frames
- 62,759,335 bytes
- trimmed from the same local source clip for broader playback/perf benchmarking

Generation path:
- build `platform/qt/MLVApp.exe`
- run:
  `MLVApp.exe --trim-mlv --input <source.MLV> --output tests/fixtures/clips/tiny_dual_iso.mlv --cut-in 1 --cut-out 2`

More ergonomic trim variants:
- use `--frame-count <n>` instead of manually computing `--cut-out`
- use `--describe-input` to print clip metadata before trimming

Example:

`MLVApp.exe --trim-mlv --describe-input --input <source.MLV>`

`MLVApp.exe --trim-mlv --input <source.MLV> --output tests/fixtures/clips/large_dual_iso.mlv --cut-in 1 --frame-count 16`

The matching golden test also expects:
- receipt: `tests/fixtures/receipts/tiny_dual_iso_hq.marxml`
- manifest: `tests/fixtures/golden/tiny_dual_iso_hq_dng_hashes.json`

The matching clip-backed golden test drives the app in `--batch` mode and
compares the exported DNG sequence hashes against the committed manifest.

The perf harness always benchmarks `tiny_dual_iso.mlv`. It will also benchmark
the checked-in `large_dual_iso.mlv` fixture automatically when the paired
receipt exists under the conventional path above. For local-only benchmarking,
you can still override it with `perf_tests --extra-clip ...`.
