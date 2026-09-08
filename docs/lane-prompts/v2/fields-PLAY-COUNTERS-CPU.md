# FIELDS for product-card-TEMPLATE.md — composed by the dispatcher; both files are in the ratified manifest
CARD_ID: PLAY-COUNTERS-CPU
PRIORITY: 7
CLIP_OR_NONE: none
ALLOWED_PATHS: platform/qt/PlaybackGatePolicy.h, platform/qt/MainWindow.cpp, tests/console/test_playback_gate_policy.cpp, tests/console/console_tests.pro, tools/repo_hygiene/test_playback_gate_wiring.py, docs/playback-improvement-plan-round2.md

DELIVERABLE:
INVENTORY FIRST, then extract only what is missing. Presented-frame gating, decode-request counters and parity machinery
ALREADY EXIST in `platform/qt/MainWindow.cpp` (symbols `m_playbackSmokePresentedFrames`,
`render_thread_decode_request_count_at_request`, `parity_match`, `gpu_playback_recon_gl_probe_parity_match`). They live in a
file no test project compiles. The deliverable is the playback ACCEPTANCE GATE as a header-only
`platform/qt/PlaybackGatePolicy.h` (`QT += core`) taking the counters as inputs (frames presented, decode requests, per-frame
hash-parity count, frames expected) and returning PASS/FAIL deterministically; wall-clock fps is an input the policy may
report but may never gate on. `MainWindow.cpp`'s existing smoke path calls the policy ONCE, passing the LIVE counter
variables by their existing names. Update the round-2 plan doc's "Common Rules" to name the policy as the gate.

ACCEPTANCE:
- `tests/console/test_playback_gate_policy.cpp` in `console_tests.pro` (header in HEADERS): PASS on a full-parity,
  all-presented vector; FAIL when presented < expected, when parity < presented, and — the L-CHEAT case — when a counter
  input is replaced by a constant equal to the expected value the test must still FAIL because the parity vector
  disagrees. Prove the test can fail by inverting one comparison once.
- `tools/repo_hygiene/test_playback_gate_wiring.py` (source-contract test, collected by discovery): parses the unique
  `PlaybackGatePolicy` call in `MainWindow.cpp` and asserts its argument list contains the live symbols
  `m_playbackSmokePresentedFrames` and `render_thread_decode_request_count_at_request` and a parity symbol — not literals.
  Fails when any argument is a numeric constant. `Batch Compile` (Phase 0.4a) compiles the call site.

VERIFY_FIRST:
git -C . grep -n -E "m_playbackSmokePresentedFrames|render_thread_decode_request_count_at_request|parity_match" {{BASE_SHA}} -- platform/qt/MainWindow.cpp | head -5
git -C . ls-tree {{BASE_SHA}} -- platform/qt/PlaybackGatePolicy.h     # empty today
git -C . grep -n -i "GatePolicy" {{BASE_SHA}} -- platform/qt src         # empty today; if a gate policy header exists, STOP with ALREADY-SHIPPED and name it
