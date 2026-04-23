## Git Worktree Recovery (2026-04-23)

### Recovery outcome

- Recovery completed by creating a brand-new registered worktree at `C:/!Layi Wkspc/MLV-App/.claude/worktrees/festive-boyd-recovered` on branch `festive-boyd`, based on commit `c1d23e601a622445d64147d769a153d9888fbf35`.
- The original orphaned tree at `C:/!Layi Wkspc/MLV-App/.claude/worktrees/festive-boyd` was left untouched as the reference copy.
- A full-tree transplant from the orphan into the recovered worktree was done with `robocopy`, excluding only `.git`.
- Post-copy no-op verification via `robocopy /L` reported `0` files to copy, which confirms the recovered tree matches the orphaned tree contents apart from Git metadata.
- Spot-check hashes matched between orphan and recovered for:
  - `.claude/analysis/mlv-playback-investigation.md`
  - `.claude/analysis/git-worktree-recovery.md`
  - `.claude/profiling/20260423-pred1-fastpath-final-pivot/large_dual_iso_preview_t4_final_pivot.json`
  - `src/mlv/liblj92/lj92.c`
- File-count spot checks also matched:
  - `.claude/profiling/`: `470` files in both trees
  - `.claude/analysis/`: `5` files in both trees
- `git worktree list --porcelain` now reports:
  - main worktree on `master`
  - `festive-boyd-before-compare` detached at `c1d23e60`
  - recovered worktree at `festive-boyd-recovered` on branch `festive-boyd`
- Validation status:
  - `console_tests --check-golden` passed in the recovered worktree once the Qt/MinGW runtime directory was added to `PATH`
  - `pipeline_tests --check-golden` could not be completed in the same shell because the executable still exited with `-1073741515` (`STATUS_DLL_NOT_FOUND`), so that part of verification remains environment-blocked rather than source-blocked

### Verified locally

- The current working directory is an orphaned linked-worktree checkout at `C:/!Layi Wkspc/MLV-App/.claude/worktrees/festive-boyd`, and its `.git` file still points to `C:/!Layi Wkspc/MLV-App/.git/worktrees/festive-boyd` at [./.git:1](</C:/!Layi%20Wkspc/MLV-App/.claude/worktrees/festive-boyd/.git:1>).
- The administrative directory `C:/!Layi Wkspc/MLV-App/.git/worktrees/festive-boyd` does not exist, while `festive-boyd-before-compare` is still registered under `.git/worktrees/`.
- `git -C C:/!Layi Wkspc/MLV-App worktree list --porcelain` reports only:
  - the main worktree on `master`
  - `festive-boyd-before-compare` detached at `c1d23e601a622445d64147d769a153d9888fbf35`
- There is no existing `festive-boyd` branch in the main repo.
- The orphaned tree is materially larger than a “copy four files” recovery:
  - `41` tracked files differ from `master`
  - more than `6000` untracked files exist in the orphaned tree, primarily under `.claude/profiling/` and `.claude/analysis/`
- Local sandbox testing against a disposable temp repo showed:
  - `git worktree repair <path>` does **not** recover a worktree whose admin directory is fully missing
  - manual recreation of `.git/worktrees/<name>/` is fragile and quickly leads to inconsistent admin state (bad index / wrong worktree path reporting) unless rebuilt very carefully

### Cross-checked from prior analysis

- The existing playback investigation artifacts and notes under `.claude/analysis/` and `.claude/profiling/` are valuable enough that recovery should preserve the full tree, not just a narrow code diff.
- The sibling `festive-boyd-before-compare` worktree appears to be a separate detached comparison point, not a replacement registration for the orphaned `festive-boyd` tree.

### Needs runtime profiling

- None for the Git recovery question. The next uncertainty is recovery mechanics, not runtime behavior.

### Ranked next steps

1. High impact / low effort: make a file-level safety copy of the orphaned `festive-boyd` directory before any Git repair attempt or path reuse.
2. High impact / low-medium effort: recover by creating a brand-new registered worktree from `master` at commit `37ff9f38d943fe3cf6cb9cb30db32fb48ed498c4`, then transplant the orphaned tree contents into it while excluding `.git`.
3. Medium impact / low effort: only after verifying `git status` in the recovered worktree, decide whether to keep the new `festive-boyd-recovered` path or rename it back to `festive-boyd`.
4. Medium impact / medium effort: avoid manual `.git/worktrees/<name>/` reconstruction unless there is a specific need to preserve the exact old path and branch metadata. Local sandbox testing suggests it is the riskiest path.

## Corruption Cause Investigation (2026-04-23)

### Verified locally

- The orphaned checkout directory and its `.git` pointer file were created together on `2026-02-27 15:28:58 -06:00`, and the pointer still says `gitdir: C:/!Layi Wkspc/MLV-App/.git/worktrees/festive-boyd` at [/.git:1](</C:/!Layi%20Wkspc/MLV-App/.claude/worktrees/festive-boyd/.git:1>).
- The checkout directory's last-write time is `2026-04-22 04:07:36 -05:00`, but the referenced admin directory `C:/!Layi Wkspc/MLV-App/.git/worktrees/festive-boyd` is gone. That combination means the checkout survived while Git's linked-worktree admin metadata did not.
- `festive-boyd-before-compare` and its admin directory were both created on `2026-04-22 07:43:50 -05:00`, several hours after the orphaned checkout's last write. That timing argues `before-compare` was a later comparison checkout, not the original identity of `festive-boyd`.
- Codex session metadata shows the orphan path in active use as early as `2026-04-20T22:21:17.971Z` in [rollout-2026-04-20T17-47-26-019dad13-c21e-7321-b96e-8e73e12050f2.jsonl:2](</C:/Users/obabalola/.codex/sessions/2026/04/20/rollout-2026-04-20T17-47-26-019dad13-c21e-7321-b96e-8e73e12050f2.jsonl:2>). This proves the checkout directory existed and was being used by April 20, but it does not by itself prove Git validity then.
- The earliest logged Git failure I found against `festive-boyd` is `2026-04-23T00:57:39.668Z` from `git diff -- ...` in [rollout-2026-04-21T19-58-33-019db2b2-26e2-76e1-9e97-b3f0bbba541e.jsonl:1650](</C:/Users/obabalola/.codex/sessions/2026/04/21/rollout-2026-04-21T19-58-33-019db2b2-26e2-76e1-9e97-b3f0bbba541e.jsonl:1650>), following a turn rooted in the orphan path at [rollout-2026-04-21T19-58-33-019db2b2-26e2-76e1-9e97-b3f0bbba541e.jsonl:1639](</C:/Users/obabalola/.codex/sessions/2026/04/21/rollout-2026-04-21T19-58-33-019db2b2-26e2-76e1-9e97-b3f0bbba541e.jsonl:1639>).
- A second direct probe of `.git/worktrees/festive-boyd` failed at `2026-04-23T02:43:06.037Z` in [rollout-2026-04-21T19-58-33-019db2b2-26e2-76e1-9e97-b3f0bbba541e.jsonl:1878](</C:/Users/obabalola/.codex/sessions/2026/04/21/rollout-2026-04-21T19-58-33-019db2b2-26e2-76e1-9e97-b3f0bbba541e.jsonl:1878>), and listing `.git/worktrees` showed only `festive-boyd-before-compare` at [rollout-2026-04-21T19-58-33-019db2b2-26e2-76e1-9e97-b3f0bbba541e.jsonl:1887](</C:/Users/obabalola/.codex/sessions/2026/04/21/rollout-2026-04-21T19-58-33-019db2b2-26e2-76e1-9e97-b3f0bbba541e.jsonl:1887>) and [rollout-2026-04-21T19-58-33-019db2b2-26e2-76e1-9e97-b3f0bbba541e.jsonl:1888](</C:/Users/obabalola/.codex/sessions/2026/04/21/rollout-2026-04-21T19-58-33-019db2b2-26e2-76e1-9e97-b3f0bbba541e.jsonl:1888>). This confirms the admin dir was already missing during active work, not just during later recovery.
- A user request explicitly targeting the comparison worktree appears at [rollout-2026-04-22T07-52-36-019db53f-e2db-7f93-8f84-0cf6a6236a5f.jsonl:7](</C:/Users/obabalola/.codex/sessions/2026/04/22/rollout-2026-04-22T07-52-36-019db53f-e2db-7f93-8f84-0cf6a6236a5f.jsonl:7>), which shows `festive-boyd-before-compare` was intentionally used as a separate compare tree rather than an automatic repair artifact.
- Searches of `C:/Users/obabalola/AppData/Roaming/Microsoft/Windows/PowerShell/PSReadLine/ConsoleHost_history.txt`, `C:/Users/obabalola/.bash_history`, and `C:/Users/obabalola/.codex/history.jsonl` found no `git worktree add/remove/prune/move`, `festive-boyd`, `before-compare`, `robocopy`, `Rename-Item`, or `Remove-Item` command matching the corruption window. The causative delete was therefore not captured in the local shell histories I could inspect.

### Cross-checked from prior analysis

- The disposable-repo tests in this note already showed that deleting a linked worktree admin directory reproduces the same failure signature (`fatal: not a git repository: .../.git/worktrees/<name>`) and that `git worktree repair` does not recover it. That matches the state observed here.
- The full-tree transplant recovery succeeded without any source-content repair, which shows the checkout contents were intact. The corruption was loss of Git admin metadata, not loss of the worktree files themselves.

### Needs runtime profiling

- None for the cause investigation. This is a filesystem and Git bookkeeping problem, not a runtime behavior problem.

### Ranked next steps

1. High impact / low effort: treat the root cause as admin-directory loss outside normal Git bookkeeping, not as source-file corruption inside the checkout.
2. High impact / medium effort: if exact attribution matters, inspect external deletion sources around the April 22 window such as Recycle Bin history, backup/sync tooling, antivirus quarantine, or the NTFS USN journal if available.
3. Medium impact / low effort: avoid moving, copying, or deleting linked worktree directories outside `git worktree move` and `git worktree remove`, and never manually clean `.git/worktrees/*` unless `git worktree list --porcelain` says the entry is stale.
4. Medium impact / low effort: add a small local cleanup checklist or script that records `git worktree list --porcelain` before any worktree maintenance.

### Likely Cause Assessment

- Moderate confidence: `C:/!Layi Wkspc/MLV-App/.git/worktrees/festive-boyd` was manually deleted or cleaned up outside Git while `C:/!Layi Wkspc/MLV-App/.claude/worktrees/festive-boyd` was left in place.
- Moderate-low confidence: the checkout directory was moved or copied outside `git worktree move`, then some later stale-entry cleanup removed the admin directory and left the old checkout behind.
- Low confidence: creating `festive-boyd-before-compare` directly corrupted `festive-boyd`. I found no command evidence for that, and the later creation time cuts against it.
