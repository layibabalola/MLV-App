# Workspace Notes For Codex

## Sensitive Folders -- Write Policy
- **DO NOT write new files into `.claude/`**. It is treated as a sensitive folder in this repo. Anything committed there was curated and should stay stable.
- Write all *new* agent scratch/state to `.claude-state/` instead. This includes profiling runs, temporary JSON, smoke-test logs, stashed artifacts, summary scripts, etc. `.claude-state/` is `.gitignore`d.
- Durable cross-session findings (the kind you want committed alongside a code change) still belong under `.claude/analysis/<topic>.md` -- editing existing tracked notes there is fine, but do not create ad-hoc new files under `.claude/profiling/` or `.claude/` roots.
- Codex worktrees under `.claude/worktrees/` remain in place -- that subtree is load-bearing.

## Investigation Discipline
- Scratch profiling / ephemeral measurements: `.claude-state/profiling/<date>-<topic>/`.
- Curated, cross-session findings: update existing `.claude/analysis/<topic>.md` rather than scattering new files.
- Use `.claude/ANALYSIS_LOG.md` only as the append-only historical log for already-tracked major investigations (do not create parallel logs elsewhere).
- When a workflow or coordination gap is discovered, do not stop at a live correction. Add the smallest durable prevention mechanism that fits the failure mode in the same turn when feasible: a test, hook/check, ledger state, documented rule, or explicit roadmap item.
- Do not run broad recursive searches over `%USERPROFILE%`, `$env:LOCALAPPDATA\Packages`, or other whole user/app-package trees during agent investigations. These locations can contain unrelated mail attachments, downloads, and app caches; target the known Codex/OpenAI/bridge state paths instead, and exclude `microsoft.windowscommunicationsapps_*` when a package-level search is unavoidable.
- Separate claims into:
  - `Verified locally`
  - `Cross-checked from prior analysis`
  - `Needs runtime profiling`
- Prefer code references in `path:line` form.
- Keep next-step recommendations ranked by impact and effort.

## Active Investigation Notes
- `.claude/analysis/mlv-playback-investigation.md`
- `.claude/analysis/testing-strategy.md`
- `.claude/analysis/testing-scaffold-implementation.md`

## Implemented Test Scaffold
- Seed automated coverage now lives under `tests/`.
- CI entrypoint for that scaffold is `.github/workflows/tests.yml`.
- Keep the docs above synchronized with what is implemented now versus still planned next.

## Brokered Auto-Closeout
- The repo owns work-block closeout through `closeout.config.json`, `tools/repo_hygiene/brokered_closeout.py`, and the PowerShell adapters in `tools/closeout/`.
- At the start of a non-trivial work block, prefer:
  - `powershell -NoProfile -ExecutionPolicy Bypass -File tools\closeout\start-work-block.ps1 -RepoRoot .`
- Before any final response after non-trivial edits, always trigger closeout:
  - `powershell -NoProfile -ExecutionPolicy Bypass -File tools\closeout\work-block-complete.ps1 -RepoRoot . -Finalize`
- To audit whole-repo branch/worktree/stash cleanup, run:
  - `powershell -NoProfile -ExecutionPolicy Bypass -File tools\closeout\repo-sweep-closeout.ps1 -RepoRoot .`
- The trigger must run even when mutation will be blocked. The repo detector/auditor may retain or block, but final responses should not silently skip the closeout path.
- Do not stash, commit, delete, or reset dirty paths classified as `foreignDirty`; they are retained and audited for their owning session or for later attribution.
- High-impact mutation, including repo-sweep pruning, is allowed only after the exact closeout tuple passes review quorum: candidate id, action id, evidence hash, policy hash, and pinned refs.
- For eligible symbolic actions, the repo auto-quorum actor may generate Codex/self plus independent policy-review artifacts and continue without user intervention. Manual-only, dirty, locked, protected, stale, or ambiguous candidates must print recoverable unblock detail instead.
- Repo sweep retention is not complete at first classification. Non-protected retained candidates must get durable candidate investigation reports under `.claude-state/closeout/repo-sweep/candidate-reports/`; clean merge-required and clean checked-out branches may be auto-quorum clean-integrated, stale clean locked worktrees may be cleaned, redundant backup branches may be pruned, and dirty worktrees must include owned/unowned/foreign classification plus a recovery command.
- Split-required owned dirty work should not remain a passive blocker. When policy allows, the dirty-split actor plans exact dirty paths, obtains autonomous quorum for symbolic action `split`, preserves those paths on a broker-claimed `closeout/split/...` branch/worktree, removes only those exact paths from the original after preservation is proven, audits the outcome, then reruns repair/finalize.
- Retained blockers must enter the blocker auto-remediation queue before becoming terminal. Foreign-dirty integrated branches may switch/detach the worktree to the target and prune the completed branch only when dirty paths do not overlap the target delta. Dirty detached worktrees may be preserved to `closeout/recovery/detached/...` and removed only after exact-path preservation is committed. Patch-equivalent non-backup branches may be pruned. Merge failures must include conflict paths and an agent-resolution packet. Protected locked worktrees remain inspect-only unless `blockerAutoRemediation.explicitProtectedWorktreeActions` names the exact path, branch, lock reason, action, evidence hash, and recovery route.
- Stale review tuples are not terminal when `autoQuorum.allowStaleReviewRenewal=true` and the candidate is still eligible. The auto-quorum actor regenerates exact-tuple reviews against the current evidence and pinned refs, revalidates immediately before mutation, and blocks only when refs, dirty state, policy, or validation no longer satisfy the action.
- Before treating closeout blockers as authoritative, the worktree must pass the configured closeout tooling baseline check. Missing actors, config fields, contract checks, repair paths, or required tests must be reported as `closeout_tooling_stale`; the actor may update from the configured baseline only when doing so will not overwrite dirty or broker-owned paths.
- If publish/upstream/final-push repair is blocked only by missing metrics, handoff, session, or closeout evidence, the evidence repair actor must generate and claim only the configured evidence files, commit only those paths, retain unrelated dirty work, and rerun the safe publish repair before reporting a blocker.
- Clean integration and remediation actors must create temporary Git worktrees with `core.longpaths=true` on Windows so tracked long-path evidence/profiling files cannot block closeout before validation starts.

## Agent Bridge Startup
- On session open in this repository, initialize the agent bridge before normal relay work.
- For Codex-specific bridge heuristics, wake-loop behavior, and routing policy, also read `bridge_trigger_heuristics.md`.
- Before any substantive bridge-related response, run:
  - `powershell -NoProfile -ExecutionPolicy Bypass -File tools\agent-bridge\codex_pre_response.ps1 -RepoRoot .`
- Before any final response after bridge-related work, run:
  - `powershell -NoProfile -ExecutionPolicy Bypass -File tools\agent-bridge\codex_pre_final.ps1 -RepoRoot .`
- These pre-response/pre-final scripts are workflow reminders, not consumers. They must not inspect message bodies, mark messages read, or replace explicit inbox hygiene.
- These hooks pass `-SkipSessionWorktree` into the bridge reminder and must remain read-only with respect to managed session worktree lifecycle. They may refresh context, metrics, timestamps, and completion gates, but they must not create, reuse, refresh, or resurrect `.codex-worktrees/` or session worktree branches; explicit start/bootstrap commands own that lifecycle.
- Treat them as best-effort only in this Codex Desktop thread. If a trivial nudge or other minimal prompt bypasses them, do not claim they provided reliable wake behavior.
- For explicit parked bridge-watch tests only, use:
  - `powershell -NoProfile -ExecutionPolicy Bypass -File tools\agent-bridge\codex_bridge_watch_mode.ps1 -Action on`
  - `powershell -NoProfile -ExecutionPolicy Bypass -File tools\agent-bridge\codex_bridge_watch_mode.ps1 -Action off`
- Bridge-watch mode is reminder-only. It makes the hooks front-load a louder `wait_inbox` reminder, but it does not hard-enforce tool usage and it does not change the main chat default.
- Use:
  - `py -3 tools\agent-bridge\bootstrap_session.py --state-dir "$env:USERPROFILE\.agent-bridge\state" --agent codex --cwd . --watcher-config "$env:USERPROFILE\.agent-bridge\watcher-config.json"`
- Bootstrap does four things:
  - derives the canonical project/rendezvous identity,
  - activates this Codex session and supersedes any older same-agent session,
  - drains any previous same-agent unread messages once,
  - sends the bridge `HANDSHAKE` and refreshes `watcher-config.json` with the active private GUID plus the rendezvous/control-plane entry.
- After bootstrap:
  - surface any drained previous-session messages in the chat,
  - if a bridge message body is surfaced to Codex by `check_inbox`, `wait_inbox`, or an equivalent non-destructive read, treat that message as already read by Codex and mark it read in the bridge immediately, even if the follow-up work will happen later,
  - if a surfaced bridge message is an `ACTION_REQUEST`, do not stop at an inbox summary. In the same turn, either start/continue implementation and record execution progress, record and park/block/displace it in the pending-action ledger with a reason, or explicitly name the user decision that blocks it.
  - after replying to, acting on, parking, blocking, displacing, rejecting, or otherwise folding a substantive surfaced message into the active task, mark that bridge message handled with the matching disposition,
  - use the returned active session GUID for bridge traffic,
  - if Codex's MCP/bridge tools become available again after an interruption or Desktop restart, send Claude a `STATUS_UPDATE` in that same turn before other outbound bridge traffic; include the active session GUID, pair id if known, bridge state, and any queued/dropped/drained messages from the dark window,
  - if bridge consumption reports `SESSION_UPDATE: superseded`, stop bridge communication in this session.
  - do not start a persistent `wait_inbox` loop in the main working chat by default; only use it for an explicit short smoke test or parked bridge-watch session described in `bridge_trigger_heuristics.md`.

## Runtime Execution Rules (Windows)
- Before running any `MLVApp.exe` binary directly, always use a Qt runtime path that matches the binary and force it for that launch.
- Required shell pattern before launch:
  - set `QT_OPENGL=desktop`.
  - set `PATH` so the active Qt runtime comes first, then the active MinGW toolchain, then the exe folder:
    - `C:\Qt\6.10.2\mingw_64\bin`
    - `C:\Qt\Tools\mingw1310_64\bin`
    - `<directory containing MLVApp.exe>`
  - launch from the exe directory (or pass absolute paths).
- Do not mix `C:\Qt\6.10.2\mingw_64` runtime binaries with a different Qt runtime in the same launch session.
- For profile/test runs, prefer:
  - `Set-Location <build-root>\release`
  - `$env:QT_OPENGL='desktop'`
  - `$env:PATH='C:\Qt\6.10.2\mingw_64\bin;C:\Qt\Tools\mingw1310_64\bin;' + (Get-Location) + ';' + $env:PATH`
  - `.\MLVApp.exe ...`
- If the system reports missing `Qt6Core.dll` / `Qt6Network.dll` or entry-point lookup failures, rerun:
  - `C:\Qt\6.10.2\mingw_64\bin\windeployqt.exe <path-to-MLVApp.exe> --release --no-translations --no-compiler-runtime`
- After every release build intended for manual dogfood or Explorer double-click launch, make the release folder self-contained for the MinGW runtime. Copy these DLLs from `C:\Qt\Tools\mingw1310_64\bin` into the directory containing `MLVApp.exe`, then verify they exist there:
  - `libgcc_s_seh-1.dll`
  - `libstdc++-6.dll`
  - `libwinpthread-1.dll`
  - `libgomp-1.dll`
  - This is required even when command-line launches work, because Explorer does not inherit the Codex shell `PATH`.
- For a repeatable launch with less chance of error, use:
  - `powershell -NoProfile -ExecutionPolicy Bypass -File .claude-state\\scripts\\run-mlvapp.ps1 -ExePath <path-to-MLVApp.exe> -Arguments '--help'`
  - if you changed Qt paths, pass `-QtBin ...` and `-MingwBin ...`.

## Runtime helper
- Use `.claude-state\\scripts\\run-mlvapp.ps1` for deterministic launches:
  - prepends the correct Qt and toolchain bins,
  - sets `QT_OPENGL=desktop`,
  - optionally runs `windeployqt` in-place,
  - and launches `MLVApp.exe` with supplied arguments.
