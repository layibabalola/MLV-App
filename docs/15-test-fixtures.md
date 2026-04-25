# Test Fixtures

Migrated from `tests/fixtures/clips/README.md`, with supplementary content
from `tests/fixtures/README.md`. Originally written by the maintainers; edits
tracked via git history.

This document covers:

1. Committed clip fixtures and what they exercise.
2. Receipts under `tests/fixtures/receipts/`.
3. Golden manifests under `tests/fixtures/golden/`.
4. Policy on adding large fixtures to git.
5. How to regenerate fixtures from a local source MLV.

## Committed clip fixtures

The first clip-backed golden test expects:

- `tests/fixtures/clips/tiny_dual_iso.mlv`

Optional larger perf fixture convention:

- `tests/fixtures/clips/large_dual_iso.mlv`
- `tests/fixtures/receipts/large_dual_iso_hq.marxml`

### `tiny_dual_iso.mlv`

- 2 frames.
- 7,931,239 bytes (~7.9 MB).
- Derived from `C:\temp\MLV\Processed\M02-1344.MLV`.
- Source clip carries a `DISO` block and is treated as the local Dual ISO
  source fixture.

This is the canonical correctness fixture: the pipeline goldens, the
console-tests AVX parity, and the smallest perf scenarios all reference it.

### `large_dual_iso.mlv`

- 16 frames.
- 62,759,335 bytes (~62.8 MB).
- Trimmed from the same local source clip for broader playback/perf
  benchmarking.

This is the checked-in larger perf fixture. The `perf_tests` harness picks
it up automatically when its paired receipt is present under the
conventional path; otherwise pass `--extra-clip` to point at a local
fixture instead.

## Receipts

Sample `.marxml` receipts that pin processing parameters live under
`tests/fixtures/receipts/`. Currently committed:

- `tiny_dual_iso_hq.marxml` — high-quality receipt for `tiny_dual_iso.mlv`.
- `tiny_dual_iso_preview.marxml` — preview-mode receipt for
  `tiny_dual_iso.mlv`.
- `large_dual_iso_hq.marxml` — high-quality receipt for
  `large_dual_iso.mlv` (perf harness pairing).
- `large_dual_iso_preview.marxml` — preview-mode receipt for
  `large_dual_iso.mlv`.

The fuzz target `fuzz_receipt_loader` also accepts this directory directly:

```
fuzz_receipt_loader tests/fixtures/receipts
```

See [`docs/16-fuzz-testing.md`](16-fuzz-testing.md).

## Golden manifests

Golden outputs are stable SHA256 hashes captured against the committed
fixtures and pinned receipts. They live under `tests/fixtures/golden/`:

- `hashes.json` — `console_tests` seed artifact hashes.
- `pipeline_hashes.json` — direct in-process engine frame hashes,
  including the backend-parametric debayer and preview-processing shells.
- `gui_hashes.json` — Qt-test pixel-exact presenter and scope hashes.
- `tiny_dual_iso_hq_dng_hashes.json` — DNG export sequence hashes from the
  clip-backed `--batch` golden test.

The matching clip-backed golden test drives the app in `--batch` mode and
compares the exported DNG sequence hashes against the committed manifest.
For the matching test to run, all three of these must be present:

- Clip: `tests/fixtures/clips/tiny_dual_iso.mlv`
- Receipt: `tests/fixtures/receipts/tiny_dual_iso_hq.marxml`
- Manifest: `tests/fixtures/golden/tiny_dual_iso_hq_dng_hashes.json`

## Policy

- Checked-in fixtures are intentionally lightweight. The two committed Dual
  ISO clips are the only large binary assets in the test tree.
- Do **not** add additional large clips to git. Use `--extra-clip` to point
  the perf harness at a local file instead, and place anything ephemeral
  under `.claude-state/` (which is `.gitignore`d).
- Build artifacts under `tests/build-*/` are `.gitignore`d via the global
  `*build*` pattern in the root `.gitignore`.
- Existing repo receipts under `receipts/` are also reachable from the seed
  console tests as additional fixture sources.
- `golden/` is the single source of truth for stable reference hashes and
  rendered outputs.

## Regeneration path

To regenerate `tiny_dual_iso.mlv` (or trim a new fixture) from a source MLV:

1. Build `platform/qt/MLVApp.exe` (see
   [`docs/10-build-windows.md`](10-build-windows.md)).
2. Run:

   ```
   MLVApp.exe --trim-mlv --input <source.MLV> \
     --output tests/fixtures/clips/tiny_dual_iso.mlv \
     --cut-in 1 --cut-out 2
   ```

More ergonomic trim variants:

- Use `--frame-count <n>` instead of manually computing `--cut-out`.
- Use `--describe-input` to print clip metadata before trimming.

Examples:

```
MLVApp.exe --trim-mlv --describe-input --input <source.MLV>
```

```
MLVApp.exe --trim-mlv --input <source.MLV> \
  --output tests/fixtures/clips/large_dual_iso.mlv \
  --cut-in 1 --frame-count 16
```

The perf harness always benchmarks `tiny_dual_iso.mlv`. It will also
benchmark the checked-in `large_dual_iso.mlv` fixture automatically when the
paired receipt exists under the conventional path above. For local-only
benchmarking, you can still override it with `perf_tests --extra-clip ...`.

## Cross-references

- [`docs/10-build-windows.md`](10-build-windows.md) — how to build
  `MLVApp.exe` for the regeneration path.
- [`docs/13-testing-infrastructure.md`](13-testing-infrastructure.md) —
  `console_tests`, `pipeline_tests`, and `gui_tests` that consume these
  fixtures.
- [`docs/14-performance-benchmarking.md`](14-performance-benchmarking.md) —
  `perf_tests` and the conventional `large_dual_iso` paths.
- [`docs/16-fuzz-testing.md`](16-fuzz-testing.md) — fuzz targets that
  consume the receipts and clips here.
