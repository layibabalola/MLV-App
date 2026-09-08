# FIELDS for product-card-TEMPLATE.md — composed by the dispatcher; both files are in the ratified manifest
CARD_ID: PROD-ENVFLAG-1
PRIORITY: 4
CLIP_OR_NONE: none
ALLOWED_PATHS: src/batch/EnvFlags.h, src/batch/WorkerThreadCount.h, platform/qt/main.cpp, tests/console/test_env_flags.cpp, tests/console/console_tests.pro, tools/repo_hygiene/test_env_flag_single_definition.py

DELIVERABLE:
Two helpers disagree on what an EMPTY environment value means: `mlvappEnvFlagEnabled` in `src/batch/WorkerThreadCount.h`
returns TRUE for `""`; `envFlagEnabled` in `platform/qt/main.cpp` returns FALSE. Seven env-flag helpers exist at `{{BASE_SHA}}` in four families — the two named above and five more
(derive: `git -C . grep -n -E 'bool (mlvappEnvFlagEnabled|envFlagEnabled)\(' {{BASE_SHA}} -- platform src`); this card consolidates ONLY the two
named above, and the four `platform/qt` copies plus `src/processing/rbfilter/rbf_wrapper.cpp` are deliberately out of scope (O117/S100). A set-but-empty `MLVAPP_*` variable
therefore caps threads in one path and not another. Create header-only `src/batch/EnvFlags.h` (`QT += core` only)
with `bool mlvappEnvFlagEnabled(const QByteArray& raw)`: empty → false; `1|true|yes|on` (case-insensitive) → true;
everything else → false. Make BOTH call sites delegate to it. This NARROWS the truthy set at both sites — under the blocklists at BASE, `2`,
`y` and `enable` are true at both and `no` is true at the `WorkerThreadCount.h` site; under the new header all four are false. That narrowing
and the empty-value unification are the only behaviour changes (O144).

ACCEPTANCE:
`tests/console/test_env_flags.cpp` added to `tests/console/console_tests.pro` (HEADERS += the new header): asserts
`""`→false, `"0"`→false, `"false"`→false, `"1"`→true, `"TRUE"`→true, `"on"`→true, `" 1 "`→true (trimmed). Also asserts `"2"`→false, `"y"`→false, `"no"`→false (O144).
The `main.cpp` call site is not compiled by any test project; the header is the tested unit, and
`tools/repo_hygiene/test_env_flag_single_definition.py` (created by this card) asserts `main.cpp` and `WorkerThreadCount.h` contain no second definition of empty-value semantics (its scope is those two files only, despite the filename — it is not a repo-wide single-definition assertion — O117)
(`isEmpty()` must not appear in either flag helper after the change).

VERIFY_FIRST:
git -C . grep -n "isEmpty()" {{BASE_SHA}} -- src/batch/WorkerThreadCount.h
git -C . grep -n "envFlagEnabled" {{BASE_SHA}} -- platform/qt/main.cpp
git -C . ls-tree {{BASE_SHA}} -- src/batch/EnvFlags.h        # empty today
