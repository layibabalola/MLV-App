# Fuzz Testing

Migrated from `tests/fuzz/README.md`. Originally written by the maintainers;
edits tracked via git history.

This document covers:

1. The opt-in fuzz-target tree and its design.
2. Each target (`fuzz_receipt_loader`, `fuzz_lj92`, `fuzz_mlv_open`).
3. How to build them locally.
4. How to run them against the checked-in fixtures.
5. Why they are intentionally not part of the default CI path.

## Overview

The `tests/fuzz/` directory contains opt-in fuzzing entry points with a
small local file-fed driver. They are scaffolded for future nightly or local
fuzz runs and are **not** part of the default CI path.

Planned focus areas:

- Receipt XML parsing.
- LJ92 decode error handling.
- MLV open/header parsing.

## Targets

Each executable accepts one or more files or directories and feeds every
file's bytes into `LLVMFuzzerTestOneInput(...)`.

### `fuzz_receipt_loader`

- Source: `tests/fuzz/fuzz_receipt_loader.cpp`
- Entry point: receipt XML loader.
- Typical input: a directory of `.marxml` receipts (see
  [`docs/15-test-fixtures.md`](15-test-fixtures.md)).

### `fuzz_lj92`

- Source: `tests/fuzz/fuzz_lj92.cpp`
- Entry point: LJ92 (lossless JPEG) decoder used by the raw-`uint16`
  decompression path.
- Typical input: an `.mlv` clip whose frames decompress through the LJ92
  predictor.

### `fuzz_mlv_open`

- Source: `tests/fuzz/fuzz_mlv_open.cpp`
- Entry point: MLV open / header parsing.
- Typical input: an `.mlv` file (the parser reads the header chain from the
  raw bytes).

The shared local driver is in `tests/fuzz/fuzz_driver.cpp`. The
target-specific build files are:

- `tests/fuzz/fuzz_receipt_loader.pro`
- `tests/fuzz/fuzz_lj92.pro`
- `tests/fuzz/fuzz_mlv_open.pro`
- `tests/fuzz/fuzz_target_defaults.pri` (shared compile/link defaults)

## Building

Use `tests/fuzz/fuzz_targets.pro` to build all three opt-in local file-fed
fuzz executables at once: `fuzz_receipt_loader`, `fuzz_lj92`, and
`fuzz_mlv_open`.

The Windows toolchain prerequisites and `qmake` / `mingw32-make` invocation
are documented in [`docs/10-build-windows.md`](10-build-windows.md). The
condensed pattern:

```powershell
$env:PATH = "C:\Qt\6.10.2\mingw_64\bin;C:\Qt\Tools\mingw1310_64\bin;" + $env:PATH
New-Item -ItemType Directory -Force tests\build-fuzz | Out-Null
Push-Location tests\build-fuzz
& "C:\Qt\6.10.2\mingw_64\bin\qmake.exe" "..\fuzz\fuzz_targets.pro"
& "C:\Qt\Tools\mingw1310_64\bin\mingw32-make.exe" -j2
Pop-Location
```

The resulting executables are written into per-target subdirectories.

## Running locally

Each target reads bytes from one or more files or directories and feeds the
contents into `LLVMFuzzerTestOneInput(...)`. The committed fixtures provide
ready-made inputs.

Example local runs:

```
tests/fuzz/build/receipt/fuzz_receipt_loader tests/fixtures/receipts
```

```
tests/fuzz/build/mlv/fuzz_mlv_open tests/fixtures/clips/tiny_dual_iso.mlv
```

Current-tree healthy local status (from
[`docs/13-testing-infrastructure.md`](13-testing-infrastructure.md)):

- `fuzz_receipt_loader tests/fixtures/receipts`: pass.
- `fuzz_lj92 tests/fixtures/clips/tiny_dual_iso.mlv`: pass.
- `fuzz_mlv_open tests/fixtures/clips/tiny_dual_iso.mlv`: pass.

## Why these are not in CI

Fuzz targets are intentionally not part of the default test tree; they are
for local/nightly parser hardening rather than every quick regression run.
They sit outside `.github/workflows/tests.yml` for the following reasons:

- They are designed for unbounded byte-range exploration, which doesn't fit
  the deterministic per-PR CI budget.
- They are most useful when paired with corpus tooling (libFuzzer / AFL),
  which is not currently provisioned on the Windows-only CI runner.
- Running them against the same checked-in fixtures every PR would only
  re-confirm the existing healthy local status without exploring new
  inputs.

When a nightly fuzz job is added in the future, this is the surface it will
target.

## Cross-references

- [`docs/10-build-windows.md`](10-build-windows.md) — Windows toolchain and
  `qmake` invocation pattern.
- [`docs/13-testing-infrastructure.md`](13-testing-infrastructure.md) —
  current local status, including fuzz pass/fail summary.
- [`docs/15-test-fixtures.md`](15-test-fixtures.md) — committed receipts
  and clips that the fuzz targets accept as input.
