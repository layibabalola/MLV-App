# FIELDS for product-card-TEMPLATE.md — composed by the dispatcher; both files are in the ratified manifest
CARD_ID: PROD-DUALISO-GUARD-TEST
PRIORITY: 5
CLIP_OR_NONE: none
ALLOWED_PATHS: docs/lane-prompts/v2/fields-PROD-DUALISO-GUARD-TEST.md, platform/qt/DualIsoLevelSyncPolicy.h, platform/qt/MainWindow.cpp, tests/console/test_dual_iso_level_sync_policy.cpp, tests/console/console_tests.pro, tools/repo_hygiene/test_dual_iso_policy_wiring.py

DELIVERABLE:
A debug teardown once deleted the guard that syncs dual-ISO black/white levels immediately BEFORE the GPU-preview
config bakes its level/gamma LUTs (symbol `mlvSyncProcessingDualIsoBlackWhiteLevels`, called under
`mlvProcessingDualIsoBlackWhiteLevelsOutOfSync` in `MainWindow.cpp`; the measured effect, white_level stuck at 23832
instead of 62805 and a 2.5x brighter render, is recorded in the fleet spec `specs/mlv-app.md`, not in tracked source).
`MainWindow.cpp` is compiled by no test project, so the ordering cannot be tested where it lives. Extract the DECISION
(given `sourceReady`, `bakePending`, and `levelsOutOfSync`, must the sync run before the bake?) into header-only
`platform/qt/DualIsoLevelSyncPolicy.h` (`QT += core` only, the `RawAspectStretchPolicy.h` pattern) and make
`MainWindow.cpp` call it at the existing site. Preserve the loaded-object short-circuit before evaluating levels.
No dual-mode gate: the existing level comparison also covers ordinary RAW clips. No behaviour change.

ACCEPTANCE:
`tests/console/test_dual_iso_level_sync_policy.cpp` in `console_tests.pro`: covers the eight input combinations,
including synchronization for ordinary RAW when a loaded source has stale levels and a bake is pending. The predicate
test FAILS when inverted (prove it once). `tools/repo_hygiene/test_dual_iso_policy_wiring.py` binds the policy to the
existing config-bake block and asserts wait, sync, invalidation, then bake. An ordered decoy elsewhere must not hide
a missing or reordered call at this site. Preserve the existing surrounding preview/playback selection.

VERIFY_FIRST:
git -C . grep -n "mlvSyncProcessingDualIsoBlackWhiteLevels" {{BASE_SHA}} -- platform/qt/MainWindow.cpp
git -C . ls-tree {{BASE_SHA}} -- platform/qt/DualIsoLevelSyncPolicy.h     # empty today
git -C . grep -n "MainWindow.cpp" {{BASE_SHA}} -- 'tests/**/*.pro'          # empty: not compiled by tests
