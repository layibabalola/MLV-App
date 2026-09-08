# PRODUCT CARD: PROD-TLS-VERIFY-1  (priority 1)

You are the `sonnet` implementer lane of the MLV-App board. Shell + write access to ONE worktree. Everything you need
is here; do not read the queue, the pens, or the doctrine bus.

## Where you are
- Worktree: `{{WORKDIR}}` (from `fork/master` at `{{BASE_SHA}}`). Work ONLY here. Run dir `{{RUNDIR}}` is readable.
- Branch: `git switch -c product/PROD-TLS-VERIFY-1`. Remote `fork` = `https://github.com/layibabalola/MLV-App.git`.
- All repo claims at `{{BASE_SHA}}` (the dispatch-time base recorded in your receipt); anchor by symbol, never line number.
- CLIP_OR_NONE: none
ALLOWED_PATHS: platform/qt/DownloadManager.cpp, platform/qt/DownloadManager.h, platform/qt/FpmNameValidator.h, platform/qt/AtomicFileReplace.h, tests/console/test_fpm_name_validator.cpp, tests/console/test_atomic_file_replace.cpp, tests/console/console_tests.pro, tools/repo_hygiene/test_no_tls_verify_none.py

## Deliverable (three items, all in `DownloadManager`; the busy-waits in `FocusPixelMapManager.cpp` belong to card 1b — do not touch them)
Pixel-map (`.fpm`) downloads are applied to RAW frames. Today `DownloadManager` fetches with TLS verification OFF,
chooses the on-disk name from the URL in `DownloadManager::saveFileName`, and writes the reply straight to the final
path in `DownloadManager::saveToDisk` (`QFile file(filename); ... file.write(data->readAll())`).
1. `platform/qt/DownloadManager.cpp`: delete the `QSslConfiguration` block that sets `QSslSocket::VerifyNone` and applies
   it to the request; default verification stays in force.
2. New header-only `platform/qt/FpmNameValidator.h` (`QT += core` only): `bool isValidFpmName(const QString&)` matching
   `^[0-9a-f]+_[0-9]+x[0-9]+\.fpm$`. `saveFileName` returns an empty string, and `downloadFinished` skips the write and
   logs, when the URL's basename fails validation.
3. New header-only `platform/qt/AtomicFileReplace.h` (`QT += core` only): `bool writeAtomically(const QString& finalPath,
   const QByteArray& bytes)` — temp file in the same directory, then `QFile::rename` into place; never a partial file at
   the final path. `saveToDisk` writes through it. `DownloadManager.h` changes only as needed for these two call sites.

## Acceptance (hosted CI)
- `tools/repo_hygiene/test_no_tls_verify_none.py` (created by this card; the repo-hygiene job's `unittest discover`
  collects it): fails if `VerifyNone` appears under `platform/` or `src/`.
- `tests/console/test_fpm_name_validator.cpp` and `tests/console/test_atomic_file_replace.cpp` added to
  `tests/console/console_tests.pro` (SOURCES; both headers in HEADERS): accepts `80000331_1808x1190.fpm`; rejects
  `../evil.fpm`, `map.exe`, `80000331_1808x1190.fpm.bak`, `""`; atomic replace leaves no temp file, the final bytes equal
  the input, and a simulated failure before rename leaves the previous final file intact.
- A hygiene grep test asserts `DownloadManager.cpp` contains `isValidFpmName(` and `writeAtomically(` (the call sites
  are in a file no test project compiles; the grep binds the wiring, `Batch Compile` compiles it once 0.4a lands).

## Verify FIRST
```
git -C . grep -n VerifyNone {{BASE_SHA}} -- platform/qt/DownloadManager.cpp
git -C . grep -n -E "saveFileName|saveToDisk|file.write" {{BASE_SHA}} -- platform/qt/DownloadManager.cpp
git -C . ls-tree {{BASE_SHA}} -- platform/qt/FpmNameValidator.h platform/qt/AtomicFileReplace.h   # empty today
git -C . show {{BASE_SHA}}:tests/console/console_tests.pro | grep -n "^QT"                          # QT += core
git -C . ls-tree {{BASE_SHA}} -- tools/repo_hygiene/test_no_tls_verify_none.py                 # empty today: this card CREATES it
```
If the first prints nothing, print `ALREADY-SHIPPED: <evidence>` and stop.

## Build and test
```
cd platform/qt && qmake && mingw32-make -j8
python -m unittest discover -s tools/repo_hygiene -p "test_*.py" -t . -v
```
Console suite runner: see `tests/README.md`; build `tests/console/console_tests.pro` and run the new cases.

## Procedure
1. `git switch -c product/PROD-TLS-VERIFY-1`; smallest diff; show full diffs of `DownloadManager.cpp` and `DownloadManager.h`.
2. Prove the hygiene test can FAIL: temporarily re-add a `VerifyNone` line, run the test (red), restore (green). Paste both.
3. `git add platform/qt/DownloadManager.cpp platform/qt/DownloadManager.h platform/qt/FpmNameValidator.h platform/qt/AtomicFileReplace.h tests/console/test_fpm_name_validator.cpp tests/console/test_atomic_file_replace.cpp tests/console/console_tests.pro tools/repo_hygiene/test_no_tls_verify_none.py`;
   commit "security: verify TLS on pixel-map downloads; validate map names; write atomically"; body ends
   `Co-Authored-By: Claude Sonnet <noreply@anthropic.com>`.
4. `git push fork product/PROD-TLS-VERIFY-1`.
5. {{PR_STEP}}

## STOP conditions (last line `STOP: <reason>`)
Build red after two attempts; a test cannot fail-then-pass; a change needed outside the eight paths above; any edit to
`FocusPixelMapManager.cpp`.

## NEVER-AUTHORIZED (docs/never-authorized.json NA-1..NA-10)
NA-10: never write `.claude/settings.json`, `.claude/settings.local.json` or `tools/hooks/mlv-never-authorized.py` under any root — a lane never edits its own gate (O129).
force-push/history rewrite; deleting/moving/truncating ledger or evidence; credentials, `claude auth login|logout`/`codex login`,
`ANTHROPIC_*`/`OPENAI_*`/`CLAUDE_CODE_*` env vars; opening any clip (none); spend beyond this run; weakening/skipping/
deleting a test; writing outside the worktree, `.factory/`, other projects; owner machine state; branch protection.
No `.github/workflows/*` edits.
