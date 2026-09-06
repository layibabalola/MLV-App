# FIELDS for product-card-TEMPLATE.md — composed by the dispatcher; both files are in the ratified manifest
CARD_ID: PROD-UPSTREAM-SYNC-1
PRIORITY: 9
CLIP_OR_NONE: none
ALLOWED_PATHS: any tracked path touched by the merge of 877dea2cb9413bd0542abb622af517cf12db63d3 (the ratified upstream head; a moving origin/master never selects the subject) — conflicts resolved in place; no new features; no factory paths
BRANCH: sync/upstream-877dea2
NOTE: this overrides the template default product/<CARD_ID>.

DELIVERABLE:
`fork/master` is 32 commits behind `origin/master` (`ilia3101/MLV-App`, head `877dea2c`): Cineform export restore,
"Seek the In frame on clip load", the QProcess export-call change, sse3 build flags, build-fix commits. Merge
`877dea2cb9413bd0542abb622af517cf12db63d3` — the ratified upstream head, NEVER the moving `origin/master`; if the dispatcher's `originHeadSha` export is newer, still merge this
sha and name the newer head in the PR body — into a branch from `fork/master` with a merge commit (no rebase, no history rewrite of the ~2,985
fork commits). Expected conflict surface: `platform/qt/MainWindow.cpp` (1.39 MB here vs 446 KB upstream),
`src/mlv/video_mlv.c`, `platform/qt/MLVApp.pro`, the release ymls. Resolve preserving BOTH the fork's batch/GPU/test
work and upstream's fixes; where upstream removed a CineForm option the fork still ships, keep the fork's and note it.

ACCEPTANCE:
All hosted required checks green; `tests/fixtures/golden/` pipeline hashes unchanged (the golden test fails on any
drift — do not re-bless); the PR body lists every conflict file with a one-line resolution rationale, and the exact
`git rev-list --count {{BASE_SHA}}..877dea2cb9413bd0542abb622af517cf12db63d3` at the base (32) and `git rev-list --count HEAD..877dea2cb9413bd0542abb622af517cf12db63d3` on the branch (0).

VERIFY_FIRST:
git -C . cat-file -e 877dea2cb9413bd0542abb622af517cf12db63d3^{commit}   # exit 0 required: the ratified upstream head must be present (the dispatcher fetched it; the lane never fetches) — if this fails, DECLINE
git -C . rev-list --count {{BASE_SHA}}..877dea2cb9413bd0542abb622af517cf12db63d3        # 32 at DIAGNOSIS_BASE
git -C . merge-tree --write-tree {{BASE_SHA}} 877dea2cb9413bd0542abb622af517cf12db63d3 2>&1 | head -20   # conflict preview at the base (git >= 2.38), run in your worktree
git -C . rev-parse origin/master   # DIAGNOSTIC ONLY — report it; it never selects the merge subject (S90)
