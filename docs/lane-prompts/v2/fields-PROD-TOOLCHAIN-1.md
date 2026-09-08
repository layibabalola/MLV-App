# FIELDS for product-card-TEMPLATE.md — composed by the dispatcher; both files are in the ratified manifest
CARD_ID: PROD-TOOLCHAIN-1
PRIORITY: 6
CLIP_OR_NONE: none
ALLOWED_PATHS: .github/workflows/Windows.yml, .github/workflows/Linux.yml, docs/10-build-windows.md, docs/11-build-macos-linux.md, tools/repo_hygiene/test_repo_hygiene.py
NOTE: this card edits .github/workflows/* by design; the template's default prohibition is lifted for exactly these two files.

DELIVERABLE:
Every required product oracle runs Qt 6.10.2 / MinGW 13.1 (see `tests.yml`, installed via aqtinstall), but the release
workflows build something else: `Windows.yml` pins `C:\Qt\5.15.2\mingw81_64` and `choco install mingw --version=8.1.0`;
`Linux.yml` installs `qt5-qmake`/`qtbase5-dev` on ubuntu-22.04. The published binaries are never the tested binaries.
Per platform: Windows → Qt 6.10.2 + MinGW 13.1 via the same aqtinstall steps `tests.yml` uses; Linux → Qt 6.10.2 via
aqtinstall with the runner's pinned native GCC (record the exact `g++ --version` in the log; do not use MinGW on Linux).
Both stay `workflow_dispatch`; the card's PR body says how they are triggered and where the artefact receipt lands.
The REQUIRED repo-hygiene suite pins the OLD toolchain — at BASE `tools/repo_hygiene/test_repo_hygiene.py` asserts `Install MinGW 8.1`,
`choco install mingw --version=8.1.0`, `qt5-default --version=5.15.2...`, `$compilerVersion -ne "8.1.0"` and `$qtVersion -ne "5.15.2"` (lines
1951-2023) and carries a falsifier at 2156 — so update `assert_windows_policy`/`assert_linux_policy` and their falsifiers to pin the NEW Qt 6.10.2
and MinGW 13.1 installation steps, executable paths and version checks, then run the COMPLETE repo-hygiene suite on a git-live checkout
(`python -m unittest discover -s tools/repo_hygiene -p "test_*.py" -t .`) and fix every failure it identifies; the required CI cannot go green
inside the previous ALLOWED_PATHS (S109).

ACCEPTANCE:
Each workflow gains a step `Print toolchain` that prints `qmake -query QT_VERSION` and the compiler version, ASSERTS
`6.10.2` and (Windows) `13.1`, fails the job otherwise, and uploads the log as `toolchain-receipt-<run_id>`. Acceptance
evidence = one `workflow_dispatch` run per workflow on the card branch with a green `Print toolchain` step and the
uploaded receipt (run URLs in the PR body). No runtime `--version` flag is added: `main.cpp` never calls
`addVersionOption`, and `setApplicationVersion` carries the app version, not Qt's.
EVIDENCE ACTOR (S111): the lane has no `gh` and never calls it. After the branch and PR exist, the HUB/DISPATCHER triggers `Windows.yml` and
`Linux.yml` on the exact branch head (`gh workflow run <file> -R layibabalola/MLV-App --ref <branch>`), waits for terminal success, exports `{headSha, workflow, runUrl,
artifactName, conclusion}` for both runs to `{{RUNDIR}}\toolchain-workflow-dispatch.json`, verifies `headSha` equals the reviewed PR head, and
updates the PR body with both run URLs. The review binds to that head; a run on any other sha is not this card's evidence.

VERIFY_FIRST:
git -C . grep -n -E "5\.15\.2|8\.1\.0" {{BASE_SHA}} -- .github/workflows/Windows.yml
git -C . grep -n -E "qt5-qmake|qtbase5" {{BASE_SHA}} -- .github/workflows/Linux.yml
git -C . grep -n -E "6\.10\.2|aqt" {{BASE_SHA}} -- .github/workflows/tests.yml | head -3
