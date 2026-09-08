# FIELDS for product-card-TEMPLATE.md — composed by the dispatcher; both files are in the ratified manifest
CARD_ID: PLAY-C2-SUBMIT-2-ACCEPT
PRIORITY: 8
CLIP_OR_NONE: none
ALLOWED_PATHS: src/processing/**, platform/qt/GpuPreviewProcessing.cpp, platform/qt/GpuDisplayViewport.cpp, tests/pipeline/**, docs/playback-improvement-results.md
DEPENDS_ON: PLAY-COUNTERS-GPU sealed receipt; PR #72 merged (async-H2D frame id passed explicitly) — both states are exported by the dispatcher into {{RUNDIR}}\dependencies.json
STATE: inherits EXTERNAL_CAPABILITY_UNAVAILABLE(gpu-runner) until the PLAY-COUNTERS-GPU bachelor receipt exists

DELIVERABLE:
Queue card `C2-SUBMIT-2` ("make the H2D upload complete DURING the prior kernel: dedicated stream + pinned staging") is
`changes-requested-awaiting-acceptance-evidence`. The prior finding was that async-H2D NEVER fired (0 of 826) plus a
byte-mismatch fault. Using the counter contract and the real-CUDA receipt, either ACCEPT (fires > 0 of N on the named clip,
zero byte mismatches, frame hashes identical to the synchronous path) or REVERT the redesign on `fork/master` with the receipt
as the reason. Either outcome is a PR; "inconclusive" is not an outcome — if the receipt cannot be produced, the card stays
parked with `EXTERNAL_CAPABILITY_UNAVAILABLE(gpu-runner)` and no PR is opened.

ACCEPTANCE:
ACCEPT path: the bachelor receipt shows fires > 0, mismatches = 0, hash parity = N/N; hosted pipeline tests green.
REVERT path: hosted pipeline tests green on the reverted tree and fixture hashes identical to pre-redesign goldens.

VERIFY_FIRST:
type {{RUNDIR}}\dependencies.json        # dispatcher-written: {"pr72": {state, mergedAt}, "C2-SUBMIT-2": {state}, "playCountersGpuReceipt": <path or null>}
git -C . grep -n -i "async.*h2d" {{BASE_SHA}} -- platform/qt src | head -5     # the redesign's current shape at the base
If dependencies.json shows pr72 not merged or playCountersGpuReceipt null, print `DECLINE: <which dependency>` and exit without editing.
