# Fuzz Harnesses

This directory contains opt-in fuzzing entry points with a small local file-fed
driver.

They are scaffolded for future nightly or local fuzz runs and are not part of
the default CI path yet.

Planned focus areas:
- receipt XML parsing
- LJ92 decode error handling
- MLV open/header parsing

Targets:
- `fuzz_receipt_loader`
- `fuzz_lj92`
- `fuzz_mlv_open`

Each executable accepts one or more files or directories and feeds every file's
bytes into `LLVMFuzzerTestOneInput(...)`.

Example local runs:
- `tests/fuzz/build/receipt/fuzz_receipt_loader tests/fixtures/receipts`
- `tests/fuzz/build/mlv/fuzz_mlv_open tests/fixtures/clips/tiny_dual_iso.mlv`
