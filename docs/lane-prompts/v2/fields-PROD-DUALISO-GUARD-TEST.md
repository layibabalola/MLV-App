# FIELDS for product-card-TEMPLATE.md — composed by the dispatcher; both files are in the ratified manifest
CARD_ID: PROD-DUALISO-GUARD-TEST
PRIORITY: 5
CLIP_OR_NONE: none
ALLOWED_PATHS: platform/qt/DualIsoLevelSyncPolicy.h, platform/qt/MainWindow.cpp, tests/console/test_dual_iso_level_sync_policy.cpp, tests/console/console_tests.pro, tools/repo_hygiene/test_dual_iso_policy_wiring.py

DELIVERABLE:
A debug teardown once deleted the guard that syncs dual-ISO black/white levels immediately BEFORE the GPU-preview
config bakes its level/gamma LUTs (symbol `mlvSyncProcessingDualIsoBlackWhiteLevels`, called under
`mlvProcessingDualIsoBlackWhiteLevelsOutOfSync` in `MainWindow.cpp`; the measured effect, white_level stuck at 23832
instead of 62805 and a 2.5x brighter render, is recorded in the fleet spec `specs/mlv-app.md`, not in tracked source).
`MainWindow.cpp` is compiled by no test project, so the ordering cannot be tested where it lives. Extract the DECISION
(given `outOfSync`, `dualMode`, and a `bakePending` flag, must the sync run before the bake?) into header-only
`platform/qt/DualIsoLevelSyncPolicy.h` (`QT += core` only, the `RawAspectStretchPolicy.h` pattern) and make
`MainWindow.cpp` call it at the existing site. No behaviour change.

ACCEPTANCE:
`tests/console/test_dual_iso_level_sync_policy.cpp` in `console_tests.pro`: asserts the policy orders SYNC before BAKE
whenever levels are out of sync and dual mode is active, and NOT when dual mode is off; the test FAILS when the policy's
ordering is inverted (prove it once). `tools/repo_hygiene/test_dual_iso_policy_wiring.py` (created by this card) asserts the `MainWindow.cpp` call site references the policy.

VERIFY_FIRST:
git -C . grep -n "mlvSyncProcessingDualIsoBlackWhiteLevels" {{BASE_SHA}} -- platform/qt/MainWindow.cpp
git -C . ls-tree {{BASE_SHA}} -- platform/qt/DualIsoLevelSyncPolicy.h     # empty today
git -C . grep -n "MainWindow.cpp" {{BASE_SHA}} -- 'tests/**/*.pro'          # empty: not compiled by tests
