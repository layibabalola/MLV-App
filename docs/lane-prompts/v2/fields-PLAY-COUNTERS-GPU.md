# FIELDS for product-card-TEMPLATE.md — composed by the dispatcher; both files are in the ratified manifest
CARD_ID: PLAY-COUNTERS-GPU
PRIORITY: 7
CLIP_OR_NONE: none
ALLOWED_PATHS: platform/qt/AsyncH2dCounterContract.h, tests/console/test_async_h2d_counter_contract.cpp, tests/console/console_tests.pro, tools/profiling/bachelor/async-h2d-counter-job.ps1
STATE: EXTERNAL_CAPABILITY_UNAVAILABLE(gpu-runner) for the real-CUDA leg only; the hosted leg is ACTIVE
DEPENDS_ON: none for the hosted leg; the bachelor leg needs `Test-Connection -TargetName bachelor -Count 1 -Quiet` true at dispatch

DELIVERABLE:
Two legs. (1) HOSTED: a header-only `platform/qt/AsyncH2dCounterContract.h` (`QT += core`) defining the counter record
the async host-to-device path must emit (fires, bytes, frame ids, build identity) and a validator over it, tested with
an injectable FAKE backend that produces records; the contract is what PLAY-C2-SUBMIT-2-ACCEPT consumes.
(2) BACHELOR: `tools/profiling/bachelor/async-h2d-counter-job.ps1` that submits the real-CUDA counter capture through
the existing `\\bachelor\mlv-agent` job framework, binds the result to source + DLL identity (PR #67's provenance
rule), and writes the sealed receipt to `{{RUNDIR}}\PLAY-COUNTERS-GPU-{{TS}}.json` (inside the run directory — the lane never
writes under `$D`). After validating it, the DISPATCHER copies it to the canonical path `$D\receipts\PLAY-COUNTERS-GPU-{{TS}}.json`
and exports that path to dependents as `playCountersGpuReceipt` (S71). It is dispatched by the loop ONLY when bachelor answers and never blocks anything.

ACCEPTANCE:
- Hosted leg: `test_async_h2d_counter_contract.cpp` in `console_tests.pro` (header in HEADERS) — the validator accepts
  a well-formed fake record set, rejects fires=0 with bytes>0, rejects a build-identity mismatch; fails when the
  validator is stubbed to `true` (prove once).
- Bachelor leg: acceptance is the sealed receipt, recorded against the card when it exists; its absence is
  `EXTERNAL_CAPABILITY_UNAVAILABLE(gpu-runner)`, not a failure.

VERIFY_FIRST:
git -C . grep -n "runs-on" {{BASE_SHA}} -- .github/workflows | sort -u     # all hosted
git -C . grep -n -i "async.*h2d" {{BASE_SHA}} -- platform/qt src | head -5   # existing telemetry (C2-TELEM-2, PR #64)
git -C . ls-tree {{BASE_SHA}} -- platform/qt/AsyncH2dCounterContract.h     # empty today
pwsh -NoProfile -Command "Test-Connection -TargetName bachelor -Count 1 -Quiet"                     # live dependency check, not a source claim
