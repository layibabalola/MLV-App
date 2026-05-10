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
- When the user asks for "repo status" or an equivalent overall repository state, report the current branch/tracking and dirty state, plus local branches, registered worktrees, and stashes. If you intentionally omit any of those, say why.

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
- Incoming closeout addenda are repo-wide closeout framework changes, not chat-only notes. When a user or bridge message labels an item as an incoming/addendum/closeout rule, persist the smallest durable same-turn update whenever feasible: update `AGENTS.md`/`CLAUDE.md` for agent policy, `closeout.config.json`/`DEFAULT_CLOSEOUT_CONFIG` for broker policy, and `tools/repo_hygiene/test_brokered_closeout.py` or tooling-baseline required symbols/tests for executable coverage. If implementation is not feasible, record an explicit closeout blocker or roadmap item before final response.
- At the start of a non-trivial work block, prefer:
  - `powershell -NoProfile -ExecutionPolicy Bypass -File tools\closeout\start-work-block.ps1 -RepoRoot .`
- If `start-work-block` begins on a clean protected target branch and `workBlockBootstrap.autoBranchFromProtectedTarget=true`, the broker creates an allowed `workBlockBootstrap.branchPrefix/<workBlockId>` branch, records `protectedBranchBootstrap` in the manifest, and continues there. Dirty protected targets must block before branch creation with the dirty paths listed.
- Before any final response after non-trivial edits, always trigger closeout:
  - `powershell -NoProfile -ExecutionPolicy Bypass -File tools\closeout\work-block-complete.ps1 -RepoRoot . -Finalize`
- To audit whole-repo branch/worktree/stash cleanup, run:
  - `powershell -NoProfile -ExecutionPolicy Bypass -File tools\closeout\repo-sweep-closeout.ps1 -RepoRoot .`
- To advance the retained-candidate remediation queue one safe candidate at a time, run:
  - `powershell -NoProfile -ExecutionPolicy Bypass -File tools\closeout\remediate-retained-closeout.ps1 -RepoRoot . -Apply`
- The trigger must run even when mutation will be blocked. The repo detector/auditor may retain or block, but final responses should not silently skip the closeout path.
- Do not stash, commit, delete, or reset dirty paths classified as `foreignDirty`; they are retained and audited for their owning session or for later attribution.
- High-impact mutation, including repo-sweep pruning, is allowed only after the exact closeout tuple passes review quorum: candidate id, action id, evidence hash, policy hash, and pinned refs.
- For eligible symbolic actions, the repo auto-quorum actor may generate Codex/self plus independent policy-review artifacts and continue without user intervention. Manual-only, dirty, locked, protected, stale, or ambiguous candidates must print recoverable unblock detail instead.
- Declared review surfaces that cannot run must write a durable `review_surface_unavailable` report for the exact tuple, with surface id, candidate/action ids, evidence hash, policy hash, pinned refs, blocker, and recovery command. Missing or insufficient quorum remains `insufficient_review_quorum`/`review_quorum_missing`, not mutation authority.
- Repo sweep retention is not complete at first classification. Non-protected retained candidates must get durable candidate investigation reports under `.claude-state/closeout/repo-sweep/candidate-reports/`; clean merge-required and clean checked-out branches may be auto-quorum clean-integrated, stale clean locked worktrees may be cleaned, redundant backup branches may be pruned, and dirty worktrees must include owned/unowned/foreign classification plus a recovery command.
- Split-required owned dirty work should not remain a passive blocker. When policy allows, the dirty-split actor plans exact dirty paths, obtains autonomous quorum for symbolic action `split`, preserves those paths on a broker-claimed `closeout/split/...` branch/worktree, removes only those exact paths from the original after preservation is proven, audits the outcome, then reruns repair/finalize.
- Retained blockers must enter the blocker auto-remediation queue before becoming terminal. Foreign-dirty integrated branches may switch/detach the worktree to the target and prune the completed branch only when dirty paths do not overlap the target delta. Dirty detached worktrees may be preserved to `closeout/recovery/detached/...` and removed only after exact-path preservation is committed. Patch-equivalent non-backup branches may be pruned. Merge failures must include conflict paths and an agent-resolution packet. Protected locked worktrees remain inspect-only unless `blockerAutoRemediation.explicitProtectedWorktreeActions` names the exact path, branch, lock reason, action, evidence hash, and recovery route.
- Stale review tuples are not terminal when `autoQuorum.allowStaleReviewRenewal=true` and the candidate is still eligible. The auto-quorum actor regenerates exact-tuple reviews against the current evidence and pinned refs, revalidates immediately before mutation, and blocks only when refs, dirty state, policy, or validation no longer satisfy the action.
- Response/final hooks must not create or resurrect managed session worktrees, but the pre-response hook may create or refresh the lightweight broker manifest for the current response work block. That manifest records `workBlockId`, branch, worktree path, start head, dirty baseline, lease, and path claims when available; completion/finalize without an explicit id must report the deterministic selection reason.
- Clean-at-start dirty paths may be auto-claimed only when the broker manifest proves the path was clean or absent at the dirty baseline, the path is not generated-only, and no other active work block claims it. Eligible `ownedDirty` paths are checkpointed through exact-tuple autonomous quorum with symbolic action `checkpoint-owned-dirty`, staging only those exact paths before commit and then rerunning detection/finalize.
- Paths already dirty in the broker baseline are never whole-file checkpointed as owned just because the current block later claims or edits them. Claimed or delta paths that overlap the dirty baseline are `mixedDirty`/`unownedDirty`, emit blocker `baseline-dirty-overlaps-candidate` with exact paths and recovery command, and can proceed only after pre-existing content is split/checkpointed separately or ownership is otherwise proven.
- **Closeout Remediation Freeze:** If a checked-out branch is already integrated, tree-equivalent, patch-equivalent, or has a pruned upstream after integration and closeout is blocked by dirty baseline overlap, stale broker manifests/path claims, missing upstream, or response-created/spurious work blocks, stop treating it as publishable feature work and enter dirty-state remediation. The per-worktree marker is `remediationFreeze.markerPath` and the process-tree override is `remediationFreeze.envVar`; while either is active, hooks, broker bootstrap, start-work-block, ensure-feature-branch, publish, finalize, auto-closeout, pre-commit, and pre-push paths must not mutate lifecycle state. Only generated-exempt content-addressed freeze audit packets may be written. Any dirty preservation/removal must start from a pinned target worktree, preserve one exact allowlisted cluster at a time with byte hashes, modes, git object ids, remote-advertised pins, hook-guard proof, process quiescence evidence, recovery commands, and exact-tuple quorum by Codex/self plus two independent 10/10 reviewers. Freeze removal also requires coordinator lock, exact-tuple quorum, and immediate pre-removal revalidation; hard-clean completion is blocked while any freeze marker, stale claim, unpreserved dirty source byte, stale transaction branch/worktree, or unclassified generated effect remains.
- Before treating closeout blockers as authoritative, the worktree must pass the configured closeout tooling baseline check. Missing actors, config fields, contract checks, repair paths, or required tests must be reported as `closeout_tooling_stale`; the actor may update from the configured baseline only when doing so will not overwrite dirty or broker-owned paths.
- If publish/upstream/final-push repair is blocked only by missing metrics, handoff, session, or closeout evidence, the evidence repair actor must generate and claim only the configured evidence files, commit only those paths, retain unrelated dirty work, and rerun the safe publish repair before reporting a blocker.
- The finalize loop must be bounded and auditable. Each retry must write the selected `workBlockId`, blocker kind, symbolic repair attempted, evidence hash before repair, evidence hash after repair, pinned refs before retry, retry number, and terminal reason when retry stops. The same blocker/evidence tuple must not be retried more than once unless policy explicitly permits renewal.
- Closeout actors must be bounded at the process boundary. Detector, repair, review-unblock, finalize, cleanup, and repo-sweep subprocesses must run through the config-driven bounded runner with BelowNormal child priority, optional validation/test affinity caps, wall-clock timeout, process-tree termination, stdout/stderr output caps, CPU-stall watchdog termination for hot children with no output/progress, fail-closed status normalization, and durable audit for timeout, output-cap breach, CPU stall, killed process tree, and known failure text. Bounded-runner infrastructure failures use the shared `boundedRunnerExitCodes` taxonomy: timeout=124, output cap=125, and CPU stall=126. PowerShell closeout adapters must emit a configurable stderr heartbeat while their bounded child is still running, without polluting machine-readable stdout. Interactive closeout should run a short smoke validation by default; full closeout validation suites must be opt-in through the configured full-suite switch. A closeout result is authoritative only after the child exits, descendants are gone or intentionally retained with audit, exit/status and failure text agree, and expected success or blocker artifacts exist.
- Finalize and `complete --finalize` have semantic success authority only when the child exits `0`, emits machine-readable `status: success`, and expected success artifacts exist. In that case validation stdout/stderr inside the success payload are evidence, so configured known-failure vocabulary is recorded as ignored text rather than promoted to a blocker; children without trusted success JSON still fail closed on known-failure text.
- Hard-clean final responses are blocked unless the repo-closed postcondition passes after finalize. The postcondition must prove from repo-owned artifacts that the selected work block, target ref, dirty state, stash state, branch state, worktree state, and cleanup audit are inspectable, and that no non-exempt dirty/untracked files, disallowed stashes, stale transaction branches, stale managed worktrees, or orphaned closeout/runtime artifacts remain. Failures report `repo_closed_postcondition_failed`, not success with cleanup deferred.
- Closeout summaries, handoffs, metrics, and cleanup status must derive their final clean/blocked wording from `repoClosedPostcondition.closeoutCleanTruth`. That report exposes raw Git status, policy-clean status, and cleanup-clean status side by side so generated/exempt dirty state cannot create contradictory final claims.
- Repo state for future UI/dashboards is logged through `repoStateLedger`: `tools\repo_hygiene\work_block_cli.py repo-state --write` writes `.claude-state/closeout/repo-state/latest.json`, a timestamped history snapshot, and a `repo_state_snapshot` audit. The snapshot uses `repo-state-snapshot.v1` and must include branch/tracking, dirty entries, local branches, worktrees, stashes, latest closeout audit/truth pointers, a bounded closeout-history index, and `rollbackPolicy` so `webDashboardSpec` can auto-refresh `http://127.0.0.1:8765/closeout` without inventing another state authority.
- `webDashboardSpec` is read-only by default and `symbolic-action-request-only`: sticky `/closeout`, SSE with polling fallback, preserved scroll/focus/detail state across refresh, repo-map/workflow/blocker/audit/rollback panels, and historical closeout browsing all read from repo-owned ledger/audit artifacts.
- Rollback is feasible only through repo-owned evidence and Git-safe actions. `rollbackPolicy` prefers revert/recovery-branch restoration, path restore from snapshot, and preservation-ref promotion; requires a new work block, user approval, state snapshots before mutation, rollback plans, and recovery commands in mutating audits; and forbids `reset --hard`/force push unless the user explicitly requests it. Committed changes are usually highly recoverable; branch/worktree cleanup depends on preserved evidence; uncommitted foreign dirty paths remain manual.
- Protected target closeout is a no-op only when `hardClean.protectedTargetNoopCloseout.enabled=true`, the current branch is protected, no explicit workBlockId was supplied, and the hard-clean repo-closed postcondition passes. It writes `protected-target-noop-closeout`; a dirty protected target, unresolved stash, stale branch/worktree, runtime blocker, or missing remediation result still blocks as `repo_closed_postcondition_failed` and must not create a synthetic work block.
- Runtime services that execute repo code must follow configured lifecycle actors. If `runtimeServices.<service>.stopBeforePromotion=true`, closeout stops and verifies the service before promotion/finalize; if `restartAfterCleanPromotion=true`, it restarts only after promoted target validation, repo-closed verification, and cleanup/prune succeed. Stop/start/status must come from configured commands, and any failed stop, verification, or restart blocks or retains with exact process evidence and a recovery command.
- Clean integration and remediation actors must create temporary Git worktrees with `core.longpaths=true` on Windows so tracked long-path evidence/profiling files cannot block closeout before validation starts.
- A target push non-fast-forward is a recoverable target race, not permission to force-push. Fetch the target (`git fetch fork master` for this repo), update the local target only by fast-forward/descendant proof, then rerun `tools\closeout\work-block-complete.ps1 -RepoRoot . -Finalize`. If the fetched target already contains the attempted closeout head, finalize may continue; otherwise it must block as `target_push_rerun_required` with attempted head, fetched target head, local target head, and recovery commands. If another automation keeps moving the target, wait for that closeout to finish, fetch again, and rerun.
- Closeout completion is a fixed point, not the first successful merge. After finalize, response/final hooks, evidence repair, tooling-baseline repair, repo sweep, or remote cleanup, rerun detector/sweep until the current worktree is on the target branch, current dirty state is classified, no local feature branch for the completed work block remains, no clean tool-owned detached integration worktree remains, no post-final evidence commit is stranded on a feature ref, and any retained remote feature branch has an explicit policy-retention audit. If a hook or repair actor creates new evidence, commits, worktrees, or refs after a reported success, closeout must continue.
- Remote feature branches are temporary unless policy explicitly retains them. After target integration, compare every completed feature upstream against the target using ancestry first and patch-id/cherry equivalence second. Delete integrated or patch-equivalent remote feature refs through an audited symbolic action, or retain them with a durable reason such as unique work, manual-only policy, protected branch policy, or ambiguous evidence. Evidence-only commits stranded on a remote feature ref must be integrated into the target or retained with exact blocker evidence before the remote ref is deleted.
- Retained remediation is a dedicated actor, not a chat follow-up. `remediate-retained-closeout.ps1 -Apply` wraps repo sweep and selects exactly one promoted candidate per run unless `-CandidateId` is supplied. Remote feature refs configured by `repoSweep.remoteFeaturePatterns` are planned alongside local branches and can be exact-tuple pruned or clean-integrated from the remote ref, then deleted after the target is updated.
- Remaining-candidate cleanup is a bounded remediation queue. Older `fork/codex/*` branches must undergo redundancy analysis against the target and known related branches before retention. Clean locked worktrees may be cleaned only when stale, unprotected, and exact ownership/lock evidence proves safety; protected locked worktrees require an exact protected-worktree policy tuple. Clean merge-conflicting worktrees require a candidate-specific conflict remediation packet with files, hunks when available, validation commands, and a recovery command. Dirty detached worktrees with sensitive paths must be preserved by exact path to a recovery branch only when policy proves the sensitive paths are safe to copy; otherwise retain with the sensitive path list, owner/age evidence, and a manual recovery command.
- Merge-failed retained candidates are not terminal by default. Repo sweep must promote policy-eligible conflicts to symbolic action `resolve_conflicts_with_agent`, write an exact-tuple agent remediation queue packet under `agentRemediationQueue.queueRoots`, and let Codex/Claude surface adapters run `tools\closeout\agent-remediation-queue.ps1 -RepoRoot .` before declaring the candidate blocked. Codex Desktop should spawn one bounded background agent per eligible shard up to `agentRemediationQueue.maxParallelAgents`, require each agent to stay within its packet read/write scope, and write durable result packets under `agentRemediationQueue.resultRoot`; if the current surface cannot spawn agents, run the consumer with `-MarkUnavailable` or report `agent_remediation_surface_unavailable` with the queue path and recovery command. After result packets exist, run `tools\closeout\agent-remediation-queue.ps1 -RepoRoot . -CollectResults`; out-of-scope changed paths or stale tuples block. Source mutation still happens only through repo-owned clean integration/finalize after the coordinator revalidates the exact tuple and consumes resolved results or blockers.
- **Evidence-preserving transaction prune:** Branches are transactions, not archives. Before pruning a stale, redundant, patch-equivalent, historical-only, or non-ancestor branch/worktree, repo sweep must write recovery evidence under `repoSweep.evidencePreservingPrune.recoveryRoot` such as `.claude-state/closeout/manual-prune/`. Non-ancestor or historical branch deletion requires bundle-backed recovery evidence plus reviewer prune-readiness verdicts in the exact tuple; dirty detached worktree removal requires tracked binary diff, untracked byte copies, file modes, HEAD/target heads, SHA256 hashes, preservation ref, reviewer verdicts, and recovery commands. Deletion order is preserve evidence, remove worktree only after preservation proof, delete local branch, delete remote feature branch, fetch/prune, rerun sweep, then rerun final closeout. Missing, stale, hash-mismatched, or out-of-root recovery artifacts block prune.

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
