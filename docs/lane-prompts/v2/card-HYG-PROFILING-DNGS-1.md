# PRODUCT CARD: HYG-PROFILING-MEDIA-1  (priority 3)   [file name kept from rev 2; card id is HYG-PROFILING-MEDIA-1]

You are the `sonnet` implementer lane of the MLV-App board. Shell + write access to ONE worktree. Everything you need
is here; do not read the queue, the pens, or the doctrine bus.

## Where you are
- Worktree: `{{WORKDIR}}` (from `fork/master` at `{{BASE_SHA}}`). Work ONLY here. Run dir `{{RUNDIR}}` is readable.
- Branch: `git switch -c product/HYG-PROFILING-MEDIA-1`. Remote `fork` = `https://github.com/layibabalola/MLV-App.git`.
- All repo claims at `{{BASE_SHA}}` (the dispatch-time base recorded in your receipt).
- CLIP_OR_NONE: none
ALLOWED_PATHS: .gitignore, .claude/profiling/**/*.dng (index removal only; working files stay), tools/repo_hygiene/test_no_tracked_scratch_media.py

## Why this card exists, precisely
`.claude/profiling/` holds 625 tracked files (177.0 MB at BASE). **Only 20 are `.dng`, and those 20 carry 164.0 MB; the 605 metadata files hold 13.0 MB** — many are
byte-identical copies of `tiny_dual_iso_000000.dng`. The other 605 (319 `.log`, 240 `.json`, 42 `.txt`, 2 `.md`,
1 `.stagelog`, 1 `.jsonl`) are small metadata and evidence and STAY TRACKED pending a separately ratified archive.
`.claude/ANALYSIS_LOG.md` (55,595 bytes) is an append-only historical log: **do not truncate, replace, or reformat it.**
Goldens are hashes under `tests/fixtures/golden/`; the 48 GB of live profiling evidence is untracked under
`.claude-state/profiling/` at the board root and is not touched.

## Deliverable
1. `.gitignore`: add `.claude/**/*.dng` (keep `tests/fixtures/` fully tracked; do not touch the existing `.claude/` rules).
2. `git rm --cached` exactly the 20 tracked `.dng` files under `.claude/profiling/` (list them first with
   `git ls-files -- '.claude/profiling/**/*.dng'`; the working files stay on disk; no history rewrite).
3. `tools/repo_hygiene/test_no_tracked_scratch_media.py`: fails if any tracked path ends in `.dng` outside
   `tests/fixtures/`. Collected by `unittest discover`.
4. Nothing else. No pointer file; the PR body says where the evidence lives.

## Acceptance (hosted CI)
```
python -m unittest discover -s tools/repo_hygiene -p "test_*.py" -t . -v
git ls-files -- '.claude/**/*.dng' | wc -l     # expect 0 on the branch
git ls-files -- .claude/profiling | wc -l       # expect 605 on the branch (625 - 20)
```

## Verify FIRST
```
git -C . ls-tree -r --name-only {{BASE_SHA}} -- .claude/profiling | grep -c '\.dng$'   # 20 on 2026-09-06
git -C . cat-file -s {{BASE_SHA}}:.claude/ANALYSIS_LOG.md                               # 55595
```
If the first prints 0, print `ALREADY-SHIPPED: <evidence>` and stop.

## Procedure
1. `git switch -c product/HYG-PROFILING-MEDIA-1`; steps 1-3; show the `.gitignore` diff and the test in full.
2. Prove the test can FAIL: `git add -f` one `.dng` back, run the test (red), `git rm --cached` it (green). Paste both.
3. `git add .gitignore tools/repo_hygiene/test_no_tracked_scratch_media.py` plus the staged removals; commit
   "hygiene: untrack 164 MB of profiling scratch DNGs (20 files)"; body ends `Co-Authored-By: Claude Sonnet <noreply@anthropic.com>`.
4. `git push fork product/HYG-PROFILING-MEDIA-1`.
5. {{PR_STEP}}  (the PR body must state: 20 files, no history rewrite, working files kept, 605 metadata files untouched, ANALYSIS_LOG.md untouched)

## STOP conditions (last line `STOP: <reason>`)
Any path under `tests/fixtures/` would change; the hygiene suite goes red for an unrelated reason; more than 20
removals are staged; any change to `.claude/ANALYSIS_LOG.md` or to any non-`.dng` file under `.claude/`.

## NEVER-AUTHORIZED (docs/never-authorized.json NA-1..NA-10)
NA-1 history rewrite of any kind (objects stay in history; accepted). NA-2 deleting working files under `.claude/` or
anything under `.claude-state/`; truncating `ANALYSIS_LOG.md`. NA-3 credentials or token env vars. NA-4 opening any
clip (none needed). NA-5 spend beyond this run. NA-6 weakening/deleting a test. NA-7 writing outside the worktree.
NA-8 owner machine state. NA-9 branch protection. NA-10 writing `.claude/settings.json`, `.claude/settings.local.json` or
`tools/hooks/mlv-never-authorized.py` under ANY root — a lane never edits its own gate. No `.github/workflows/*` edits.
