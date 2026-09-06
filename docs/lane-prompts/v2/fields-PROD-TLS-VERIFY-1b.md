# FIELDS for product-card-TEMPLATE.md — composed by the dispatcher; both files are in the ratified manifest
CARD_ID: PROD-TLS-VERIFY-1b
PRIORITY: 1 (dispatched after PROD-TLS-VERIFY-1 lands)
CLIP_OR_NONE: none
ALLOWED_PATHS: platform/qt/SyncDownloadWaiter.h, platform/qt/FocusPixelMapManager.cpp, tests/console/test_sync_download_waiter.cpp, tests/console/console_tests.pro, tools/repo_hygiene/test_fpm_no_process_events.py

DELIVERABLE:
Split from PROD-TLS-VERIFY-1 because its behaviour change had no hosted test. `FocusPixelMapManager.cpp` waits for
each download with `while (!manager->isDownloadReady()) qApp->processEvents();` (4 sites), which re-enters the GUI
event loop mid-download (an export or clip-open can start while a map write is in flight). Replace with a header-only
`platform/qt/SyncDownloadWaiter.h` (`QT += core`): a `QEventLoop` quit by a finished signal, with a timeout, exposing
`Result { finished, timedOut }`. The four call sites use it. No re-entrancy: no `processEvents` remains in that file.

ACCEPTANCE:
`tests/console/test_sync_download_waiter.cpp` in `console_tests.pro`, driven by `QTimer` (no network): a signal fired
after 10 ms yields `finished=true`; no signal within a 50 ms timeout yields `timedOut=true`; the waiter returns
exactly once. `tools/repo_hygiene/test_fpm_no_process_events.py` (created by this card) asserts `FocusPixelMapManager.cpp` contains no `processEvents`. Prove the timeout
test can fail by setting the timeout to 0 once.

VERIFY_FIRST:
git -C . grep -c processEvents {{BASE_SHA}} -- platform/qt/FocusPixelMapManager.cpp     # 4 today
git -C . ls-tree {{BASE_SHA}} -- platform/qt/SyncDownloadWaiter.h                        # empty today
