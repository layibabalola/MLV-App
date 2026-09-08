# FIELDS for product-card-TEMPLATE.md — composed by the dispatcher; both files are in the ratified manifest
CARD_ID: PROD-TLS-VERIFY-1b
PRIORITY: 1 (dispatched after PROD-TLS-VERIFY-1 lands)
CLIP_OR_NONE: none
ALLOWED_PATHS: platform/qt/SyncDownloadWaiter.h, platform/qt/DownloadManager.h, platform/qt/DownloadManager.cpp, platform/qt/FocusPixelMapManager.h, platform/qt/FocusPixelMapManager.cpp, tests/console/test_sync_download_waiter.cpp, tests/console/test_download_manager.cpp, tests/console/console_tests.pro, tools/repo_hygiene/test_fpm_no_process_events.py

DELIVERABLE:
Split from PROD-TLS-VERIFY-1 because its behaviour change had no hosted test. `FocusPixelMapManager.cpp` waited for
each download with `while (!manager->isDownloadReady()) qApp->processEvents();` (4 sites), which pumps arbitrary Qt
events -- including queued callbacks and timers -- for as long as the download takes, with no deadline and no
ordered cancellation. Replaced with a header-only `platform/qt/SyncDownloadWaiter.h` (QtCore-only): a local
`QEventLoop`, bounded by a `QTimer` deadline (production default 30000ms, injectable for tests), exposing
`Result { finished, timedOut }`. The four call sites (plus the shared `getMapList()` helper they use) now go through
it, driven by a new `DownloadManager::downloadsFinished(bool)` completion signal.

CORRECTED CLAIMS (the original card asserted two things that do not hold; do not restate them):
- "No re-entrancy": FALSE. The waiter's `QEventLoop::exec()` runs with `QEventLoop::ExcludeUserInputEvents`, which
  excludes only literal mouse/keyboard input -- queued signals, timers, and other callbacks can still run
  reentrantly against shared state while the loop spins. `FocusPixelMapManager` now guards its four public entry
  points with an instance-level, non-static `m_operationInProgress` flag via `SyncDownloadWaiter::ScopedOperationGuard`,
  so a reentrant call to one of the four during another's wait returns the function's existing failure-convention
  default (false / 0) instead of doing work -- that is what is actually proven, not "no re-entrancy" in general.
- "A zero timeout falsifies the timeout test": FALSE. A `deadlineMs` of 0 is a legitimate, immediate-fire deadline,
  not a way to break the test -- it exercises the timeout path deterministically and quickly. The mutation used for
  this card's required red/green falsifier proof is neutering production behavior directly (e.g. commenting out the
  abort-before-processing guard in `DownloadManager::downloadFinished`, or the guard acquisition in one
  `FocusPixelMapManager` entry point), not manipulating the timeout value.

ACCEPTANCE:
`tests/console/test_sync_download_waiter.cpp` in `console_tests.pro`, using QtCore-only fakes (plain `QObject` +
`QTimer`, no `QSignalSpy`, no network): delayed success before the deadline yields `finished=true`; an
already-ready completion at connect time yields `finished=true` without entering the wait; a timeout invokes the
supplied cancellation callback and yields `timedOut=true`; the result is set exactly once even if the underlying
signal fires twice; a synchronous "success" notification fired from inside the timeout's cancellation callback does
NOT overwrite the already-latched `timedOut=true` result; destroying the sender mid-wait resolves safely without
invoking callbacks on the dead object; `ScopedOperationGuard` acquire/reentrancy-rejection/release is covered
directly.

`tests/console/test_download_manager.cpp`, using a fake `QNetworkAccessManager`/`QNetworkReply` pair injected via
`DownloadManager`'s new optional `QNetworkAccessManager*` constructor parameter (default `nullptr` preserves the
historic owned-internal-manager behaviour): normal delayed success writes the file via the real
`saveToDisk`/`writeAtomically` path (proven with a `QTemporaryDir` + scoped current-directory switch, not the real
map directory); an already-finished reply at registration time is handled once, not double-processed; the
timeout/abort path marks the operation failed and emits `downloadsFinished(false)` exactly once; a synchronous
finish fired from inside a fake reply's `abort()` override does not flip the result back to success; a late
`finished()` arriving after abort/timeout does not write a file; a rejected/disallowed basename propagates failure
without writing; a multi-request operation where one request fails and another later succeeds stays aggregate-failed.

`tools/repo_hygiene/test_fpm_no_process_events.py` (created by this card) is a supplement to the C++ tests, not a
replacement: it asserts `FocusPixelMapManager.cpp` contains no `processEvents`/`QEventLoop` text, that all four call
sites construct a `ScopedOperationGuard` over `m_operationInProgress` as the first statement of their body and check
`acquired()` before doing work, and that the file actually uses `SyncDownloadWaiter`.

VERIFY_FIRST:
git -C . grep -c processEvents {{BASE_SHA}} -- platform/qt/FocusPixelMapManager.cpp     # 4 today
git -C . ls-tree {{BASE_SHA}} -- platform/qt/SyncDownloadWaiter.h                        # empty today
