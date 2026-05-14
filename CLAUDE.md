# MLV-App Batch CLI Implementation Spec (CLAUDE.md)

## Purpose
This file guides Claude Code through a surgical modification of MLV-App to add
headless batch CLI mode for Cinema DNG sequence export. Place this file in the
repository root — Claude Code reads it automatically every session.

---

## Agent Bridge — Session Startup (Hook-Driven)

This repo uses an agent-bridge to coordinate with a peer Codex session. The
`SessionStart` hook in `.claude/settings.local.json` runs
`tools/agent-bridge/bootstrap_session.py` automatically at the start of every
session — its stdout is injected into your context. The bootstrap registers
this session as the active Claude bridge owner (superseding any older Claude
session), drains messages from the previous session, sends a HANDSHAKE to
Codex, and updates the watcher config.

After the hook fires, do these in order:
1. **Read `drained_previous_messages`** in the hook output — surface any unread
   messages from the previous session to the user before proceeding.
2. **Use the returned `session_id`** as your active Claude bridge GUID for this session.
3. **If `check_inbox` returns a `SESSION_UPDATE: superseded` control message at any
   point**, stop all bridge sends immediately — a newer Claude session has taken over.
4. **Read `active_session_unread`** in the hook output — these are unread rows
   already sitting in the new active session bucket. Surface them, then mark each
   read by id after handling.
5. **Start the bridge Monitor** — the Monitor is Claude's inbox wake mechanism and does
   NOT survive context compaction. Start it every session, no exceptions:
   ```
   Monitor(persistent=True, command="<python> -u tools/agent-bridge/bridge_monitor_poll.py --state-dir <bridge-state-dir> --agent claude --session-id <active-guid> --project mlv-app --poll-interval-seconds 2")
   ```
   Use `bridge_monitor_poll.py` for the Monitor. Do not substitute
   `probe_server.py`; probes are diagnostics and will not keep Claude's inbox
   wake path armed. Before saying "waiting for Codex," verify the Monitor task
   is active. If no Monitor is running, start one before waiting.
   If a bridge message arrives with `TYPE: CONTROL` and
   `SUBJECT: MONITOR_RESTART_REQUIRED`, stop any stale Monitor task handle and
   immediately start a fresh `bridge_monitor_poll.py` Monitor with the command
   shown in the message. The watcher sends this control when it detects a stale
   or missing Monitor heartbeat, or when bridge Monitor-related code changes.

When a Monitor notification fires, call `mcp__agent-bridge__check_inbox` with
`agent=claude`, `session_id=<active-guid-or-mlv-app>`, `mark_read=False`, then mark
each message read explicitly by id.

If the hook output is missing from your session-start context (broken JSON,
deleted file, hook failure), fall back to running `bootstrap_session.py`
manually with the command stored in `.claude/settings.local.json`.

Bridge protocol details: `tools/agent-bridge/BRIDGE_PROTOCOL.md`
Hardening plan and audit log: `tools/agent-bridge/BRIDGE_HARDENING.md`

---

## Agent Bridge — Session Closeout

### Repo-Owned Work Block Closeout

This repo now owns the work-block completion transaction. Claude Code,
Claude Desktop, Codex, humans, hooks, and future adapters should all call the
same repo-local scripts instead of inventing surface-specific closeout logic.
Repo-owned closeout/dashboard PowerShell commands must prefer PowerShell 7+
through `pwsh.exe -NoLogo -NoProfile -NonInteractive -ExecutionPolicy Bypass`.
The `powerShell` policy in `closeout.config.json`/`DEFAULT_CLOSEOUT_CONFIG`
records the justification: PowerShell 7+ starts materially faster for repeated
script launches, while `-NoProfile` avoids profile load and profile side
effects. Use Windows PowerShell only when `pwsh.exe` is unavailable or a surface
is explicitly PS 5.1-only, such as bridge WinRT/wake/toast/balloon behavior.
Bridge process metadata probes are not part of that exception and should use the
shared `powershell_runtime.powershell_cim_command()` policy helper.

For non-trivial work, start a brokered block with:

```powershell
pwsh.exe -NoLogo -NoProfile -NonInteractive -ExecutionPolicy Bypass -File tools\closeout\start-work-block.ps1 -RepoRoot .
```

If the start command begins on a clean protected target branch and
`workBlockBootstrap.autoBranchFromProtectedTarget=true`, the broker creates an
allowed `workBlockBootstrap.branchPrefix/<workBlockId>` branch, records
`protectedBranchBootstrap` in the manifest, and continues there. Dirty protected
targets block before branch creation and must list the dirty paths.

Before declaring the work complete, always run:

```powershell
pwsh.exe -NoLogo -NoProfile -NonInteractive -ExecutionPolicy Bypass -File tools\closeout\work-block-complete.ps1 -RepoRoot . -Finalize
```

The completion wrapper finalizes by default, so ordinary substantive replies should be treated as closeout events rather than deferred checkpoints.

To audit cross-branch cleanup, run:

```powershell
pwsh.exe -NoLogo -NoProfile -NonInteractive -ExecutionPolicy Bypass -File tools\closeout\repo-sweep-closeout.ps1 -RepoRoot .
```

To advance retained cleanup one safe candidate at a time, run:

```powershell
pwsh.exe -NoLogo -NoProfile -NonInteractive -ExecutionPolicy Bypass -File tools\closeout\remediate-retained-closeout.ps1 -RepoRoot . -Apply
```

The trigger must run even when mutation is not eligible. The detector classifies
dirty paths as `ownedDirty`, `unownedDirty`, or `foreignDirty`; foreign dirty
work is retained and audited, never stashed or deleted by this workflow.
Incoming closeout addenda are repo-wide closeout framework changes, not
chat-only notes. When a user or bridge message labels an item as an
incoming/addendum/closeout rule, persist the smallest durable same-turn update
whenever feasible: update `AGENTS.md`/`CLAUDE.md` for agent policy,
`closeout.config.json`/`DEFAULT_CLOSEOUT_CONFIG` for broker policy, and
`tools/repo_hygiene/test_brokered_closeout.py` or tooling-baseline required
symbols/tests for executable coverage. If implementation is not feasible,
record an explicit closeout blocker or roadmap item before final response.
High-impact mutation, including repo-sweep pruning, requires the exact review
tuple recorded by the broker: candidate id, action id, evidence hash, policy
hash, and pinned refs.
For eligible symbolic actions, the repo auto-quorum actor may write Codex/self
plus independent policy-review artifacts and continue without user intervention.
Manual-only, dirty, locked, protected, stale, or ambiguous candidates must print
recoverable unblock detail instead of silently stopping.
Declared review surfaces that cannot run must write a durable
`review_surface_unavailable` report for the exact tuple, including surface id,
candidate/action ids, evidence hash, policy hash, pinned refs, blocker, and
recovery command. Missing or insufficient quorum remains
`insufficient_review_quorum`/`review_quorum_missing`, not mutation authority.
Response/final hooks remain read-only for managed session worktrees: they must
not checkout, create worktrees, pull, reset, stash, or clean. The pre-response
hook may create or refresh the lightweight broker manifest for the current
response work block, recording the work block id, branch, worktree path, start
head, dirty baseline, lease, and path claims when available.
Dirty paths created after that broker baseline can be auto-claimed only when the
manifest proves they were clean or absent at start, they are not generated-only,
and no other active work block claims them. Eligible `ownedDirty` paths are
checkpointed through exact-tuple autonomous quorum using symbolic action
`checkpoint-owned-dirty`; the checkpoint stages only the exact owned paths,
commits through the broker, then reruns detection and finalize. Paths already
dirty at the broker baseline are `mixedDirty`/`unownedDirty` when they overlap a
claim or candidate delta and must block with
`baseline-dirty-overlaps-candidate` plus exact recovery detail rather than being
whole-file checkpointed. The narrow exception is
`protected_branch_dirty_recovery`: when finalize preserves exact dirty paths
from a protected target onto a broker-created work-block branch, those exact
preserved paths and claims are explicit ownership proof and may proceed through
`ownedDirty`.
Repo sweep retention is an investigated outcome, not a first-pass label. For
non-protected retained candidates, the sweep actor writes durable candidate
investigation reports under `.claude-state/closeout/repo-sweep/candidate-reports/`.
Clean merge-required branches and clean checked-out branches can be promoted to
auto-quorum clean integration, stale clean locked worktrees can be cleaned,
redundant backup branches can be pruned, and dirty worktrees must carry
owned/unowned/foreign classification plus a recovery command.
Split-required owned dirty work should not stay a passive blocker. When policy
allows, the dirty-split actor plans exact dirty paths, obtains autonomous quorum
for symbolic action `split`, preserves those paths on a broker-claimed
`closeout/split/...` branch/worktree, removes only those exact paths from the
original after preservation is proven, audits the outcome, and then repair or
finalize can rerun.
Retained blockers enter the blocker auto-remediation queue before becoming
terminal. Foreign-dirty integrated branches can switch or detach their worktree
to the target and prune the completed branch only when dirty paths do not
overlap the target delta. Dirty detached worktrees can be preserved to
`closeout/recovery/detached/...` and removed only after exact-path preservation
is committed. Patch-equivalent non-backup branches can be pruned. Merge failures
must include conflict paths and an agent-resolution packet. Protected locked
worktrees stay inspect-only unless `blockerAutoRemediation.explicitProtectedWorktreeActions`
names the exact path, branch, lock reason, action, evidence hash, and recovery
route.
Stale review tuples are not terminal when `autoQuorum.allowStaleReviewRenewal=true`
and the candidate is still eligible. The auto-quorum actor regenerates exact-tuple
reviews against current evidence and pinned refs, revalidates immediately before
mutation, and blocks only when refs, dirty state, policy, or validation no longer
satisfy the action.
A target push non-fast-forward means the target moved during closeout. Treat it
as a recoverable race, never as permission to force-push: fetch the target
(`git fetch fork master` in this repo), fast-forward/update the local target
only when the fetched ref proves it is a descendant, then rerun
`pwsh.exe -NoLogo -NoProfile -NonInteractive -ExecutionPolicy Bypass -File tools\closeout\work-block-complete.ps1 -RepoRoot . -Finalize`. If the fetched
target already contains the attempted closeout head, finalize may continue. If
the target moved to different work, block as `target_push_rerun_required` with
the attempted head, fetched target head, local target head, and recovery
commands. If another automation keeps updating `master`, wait for that closeout
to finish, fetch again, and rerun.
Before reporting a closeout blocker as authoritative, run the configured
closeout tooling baseline check. Missing actors, policy fields, contract checks,
repair paths, or required tests are `closeout_tooling_stale`; stale tooling
output is not a final hygiene blocker. Auto-update from the configured baseline
is allowed only when it does not overwrite dirty or broker-owned paths.
When publish/upstream/final-push repair is blocked only by missing metrics,
handoff, session, or closeout evidence, generate the configured evidence bundle,
claim and commit only those evidence files, retain unrelated dirty work, and
rerun safe publish repair before stopping.
The finalize loop must be bounded and auditable. Each retry must write the
selected `workBlockId`, blocker kind, symbolic repair attempted, evidence hash
before repair, evidence hash after repair, pinned refs before retry, retry
number, and terminal reason when retry stops. The same blocker/evidence tuple
must not be retried more than once unless policy explicitly permits renewal.
Closeout actors must be bounded at the process boundary. Detector, repair,
review-unblock, finalize, cleanup, and repo-sweep subprocesses must run through
the config-driven bounded runner with BelowNormal child priority, optional
validation/test affinity caps, wall-clock timeout, process-tree termination,
stdout/stderr output caps, CPU-stall watchdog termination for hot children with
no output/progress, fail-closed status normalization, and durable audit for
timeout, output-cap breach, CPU stall, killed process tree, and known failure
text. Bounded-runner infrastructure failures use the shared
`boundedRunnerExitCodes` taxonomy: timeout=124, output cap=125, and CPU
stall=126. PowerShell closeout adapters must emit a configurable stderr
heartbeat while their bounded child is still running, without polluting
machine-readable stdout. Interactive closeout should run a short smoke validation
by default; full closeout validation suites must be opt-in through the configured
full-suite switch. A closeout result is authoritative only after the child exits,
descendants are gone or intentionally retained with audit, exit/status and
failure text agree, and expected success or blocker artifacts exist.
Finalize and `complete --finalize` have semantic success authority only when the
child exits `0`, emits machine-readable `status: success`, and expected success
artifacts exist. Validation stdout/stderr inside that success payload are
evidence, so configured known-failure vocabulary is recorded as ignored text
rather than promoted to a blocker; children without trusted success JSON still
fail closed on known-failure text.
Hard-clean final responses are blocked unless the repo-closed postcondition passes after finalize.
The postcondition must prove from repo-owned artifacts that the selected work
block, target ref, dirty state, stash state, branch state, worktree state, and
cleanup audit are inspectable, with no non-exempt dirty/untracked files,
disallowed stashes, stale transaction branches, stale managed worktrees, or
orphaned closeout/runtime artifacts left behind. Failures report
`repo_closed_postcondition_failed`, not success with deferred cleanup.
Hard-clean must also inspect `git worktree list --porcelain`. A failed or empty
listing, a listing that omits the current repo root, or any ordinary linked
sibling worktree blocks closeout; protected load-bearing worktrees such as
`.claude/worktrees/**` remain inspect-only under policy and still appear in
repo-state evidence.
Closeout summaries, handoffs, metrics, and cleanup status derive final
clean/blocked wording from `repoClosedPostcondition.closeoutCleanTruth`. That
report keeps raw Git status, policy-clean status, and cleanup-clean status
beside each other so generated/exempt dirty state does not create contradictory
final claims.
Repo state for dashboard and audit consumers is recorded through
`repoStateLedger`. Running `tools\repo_hygiene\work_block_cli.py repo-state
--write` writes `.claude-state/closeout/repo-state/latest.json`, a timestamped
history snapshot, and a `repo_state_snapshot` audit. The snapshot uses
`repo-state-snapshot.v1` and contains branch/tracking, dirty entries, local
branches, worktrees, stashes, latest closeout audit/truth pointers, a bounded
`closeout-history-index.v1`, `worktree-inspection.v1`, and `rollbackPolicy`
plus `rollback-readiness.v1`. Treat [`docs/19-closeout-dashboard-spec.md`](docs/19-closeout-dashboard-spec.md)
as the canonical dashboard contract and keep this summary aligned with it.
The `webDashboardSpec` surface should auto-refresh
`http://127.0.0.1:8765/closeout` from that feed and the closeout audits instead
of creating a separate state authority.
For live dashboard polling, use the latest-only repo-state adapter:
`pwsh.exe -NoLogo -NoProfile -NonInteractive -ExecutionPolicy Bypass -File tools\closeout\write-repo-state.ps1 -RepoRoot . -Write -LatestOnly`. It
refreshes `latest.json` without appending history or `repo_state_snapshot` audit
rows on every poll. Configured refresh commands must fail closed unless they
resolve to this repo-owned latest-only writer; arbitrary commands must not be
surfaced through dashboard metadata.
`webDashboardSpec` is read-only by default and
`symbolic-action-request-only`: sticky `/closeout`, SSE with polling fallback,
preserved scroll/focus/selection/expanded/history-filter state across refresh,
and repo-map, workflow, blocker, action-preview, action-request-history, audit, rollback, and
historical closeout views. The flow is read-first: `Inspect` evidence,
`Preview` consequences, `Request` symbolic intent, and leave `Apply` to
repo-owned actors outside dashboard authority. Read-only preview and dry-run
explanations are allowed when they are derived from repo-owned truth and do not
create a second mutation authority. `latest.json` is a mutable display feed,
not rollback evidence.
The local helper is `tools\closeout\start-closeout-dashboard.ps1`. It serves
`http://127.0.0.1:8765/closeout`, reuses a healthy same-repo process, and fails
closed if the port belongs to another repo. Required endpoints are
`/api/closeout/repo-state/latest`,
`/api/closeout/repo-state/history-index`,
`/api/closeout/repo-state/history/{snapshotId}`,
`/api/closeout/actions`, `/api/closeout/actions/requests`,
`/api/closeout/actions/preview`, and `/api/closeout/actions/request`;
`/api/closeout/actions` reports `serverProcessId`, repo ownership, symbolic
actions, exact-tuple requirements, command policy, and rollback
non-actionability plus the readiness reason, while
`/api/closeout/actions/preview` explains cleanup or rollback consequences
without mutating the repo.
`/api/closeout/actions/requests` exposes immutable request history rows for
audit handoff and post-action traceability. The feed should distinguish
`empty`, `ready`, and `partial` history states and surface malformed/truncated
row counts instead of silently hiding them.
Dashboard symbolic action intent is durable but non-mutating:
`/api/closeout/actions/request` writes generated request packets under
`.claude-state/closeout/dashboard-action-requests/` with helper freshness,
repo-state hash, actionability reason, preview-token binding, and exact tuple
fields. It rejects missing/stale/future helper timestamps, mismatched helper
process ids, stale preview bindings, empty exact-tuple values, and request
roots that resolve outside `.claude-state/`;
repo-owned actors must still revalidate before mutation.
Rollback is handled by repo-owned evidence, not by blind cleanup. `rollbackPolicy`
prefers Git revert, recovery-branch restoration, path restore from snapshot, or
preservation-ref promotion; requires a new work block, user approval, a
repo-state snapshot before mutation, immutable source snapshot evidence, a
`closeout-rollback-manifest.v1`, a rollback plan, and recovery commands in
mutating audits; and forbids `reset --hard` or force push unless the user
explicitly asks for it. Until a rollback actor validates the manifest, readiness
must remain `read-only-no-actor`; rollback itself is still a mutating action
once that actor exists and the user approves it. `tools\closeout\validate-rollback-manifest.ps1`
is a read-only `closeout-rollback-manifest-validation.v1` validator: manifests
must stay under `.claude-state/closeout/rollback`, reject `latest.json` and
`current.json`, bind `sourceSnapshotHash` to the repo-state snapshot hash scope,
require explicit `sourceSnapshotAuditHash` and `repoClosedAuditHash`,
integrity-check audit hashes/sidecars, require the source snapshot and
repo-closed audit to share `workBlockId`, and reject forbidden recovery
commands. Committed Git changes are usually highly recoverable; branch/worktree
cleanup depends on preserved evidence; uncommitted foreign dirty paths are
manual.
Protected target closeout is a no-op only when
`hardClean.protectedTargetNoopCloseout.enabled=true`, the current branch is
protected, no explicit workBlockId was supplied, and hard-clean passes. It writes
`protected-target-noop-closeout`. Dirty protected targets must not be left
stranded on the protected branch: finalize should preserve the exact dirty state
onto an allowed `workBlockBootstrap.branchPrefix/<workBlockId>` branch,
materialize a work-block manifest there, and write
`protected-target-dirty-recovery` before reporting any remaining blocker.
Unresolved stashes, stale branches/worktrees, runtime blockers, or missing
remediation results still block closeout.
Runtime services that execute repo code follow configured lifecycle actors. When
`runtimeServices.<service>.stopBeforePromotion=true`, closeout stops and verifies
the service before promotion/finalize; when `restartAfterCleanPromotion=true`,
restart occurs only after target promotion, validation, repo-closed verification,
and cleanup/prune succeed. Stop/start/status must come from configured commands,
and failed stop, verification, or restart blocks or retains with exact process
evidence and a recovery command.
Response, metrics, timestamp, and final-completion hooks are read-only with
respect to managed session worktree lifecycle. Codex response/final adapters pass
`-SkipSessionWorktree` and record `session_worktree_bootstrap=skipped`; such
hooks may refresh context and completion evidence, but they must not create,
reuse, refresh, or resurrect `.codex-worktrees/` or session worktree branches.
Managed session worktrees are created only by explicit start/bootstrap commands,
so repo sweep cleanup remains final.
Closeout Remediation Freeze applies when a checked-out branch is already
integrated, tree-equivalent, patch-equivalent, or has a pruned upstream after
integration and closeout is blocked by dirty baseline overlap, stale manifests or
path claims, missing upstream, or response-created/spurious work blocks. The
freeze marker comes from `remediationFreeze.markerPath`; the process-tree
override comes from `remediationFreeze.envVar`. While frozen, response/final
hooks, broker bootstrap, start-work-block, ensure-feature-branch, publish,
finalize, auto-closeout, pre-commit, and pre-push paths must not mutate lifecycle
state, and only generated-exempt content-addressed freeze audit packets may be
written. Dirty preservation/removal must use fresh target-pinned worktrees, exact
allowlisted clusters, byte hashes, file modes, git object ids, remote-advertised
pins, hook-guard proof, process quiescence evidence, recovery commands, and
exact-tuple quorum from Codex/self plus two independent 10/10 reviewers. Freeze
removal requires a coordinator lock, exact-tuple quorum, and immediate
revalidation; hard-clean completion is blocked while a freeze marker, stale
claim, unpreserved dirty byte, stale transaction branch/worktree, or unclassified
generated effect remains.
Clean integration and remediation actors must create temporary Git worktrees with
`core.longpaths=true` on Windows so tracked long-path evidence/profiling files
cannot block closeout before validation starts.
Closeout completion is a fixed point, not the first successful merge. After
finalize, response/final hooks, evidence repair, tooling-baseline repair, repo
sweep, or remote cleanup, rerun detector/sweep until the current worktree is on
the target branch, current dirty state is classified, no local feature branch for
the completed work block remains, no clean tool-owned detached integration
worktree remains, no post-final evidence commit is stranded on a feature ref,
and any retained remote feature branch has an explicit policy-retention audit. If
a hook or repair actor creates new evidence, commits, worktrees, or refs after a
reported success, closeout must continue.
Remote feature branches are temporary unless policy explicitly retains them.
After target integration, compare every completed feature upstream against the
target using ancestry first and patch-id/cherry equivalence second. Delete
integrated or patch-equivalent remote feature refs through an audited symbolic
action, or retain them with a durable reason such as unique work, manual-only
policy, protected branch policy, or ambiguous evidence. Evidence-only commits
stranded on a remote feature ref must be integrated into the target or retained
with exact blocker evidence before the remote ref is deleted.
Retained remediation is a dedicated repo actor, not a chat follow-up.
`remediate-retained-closeout.ps1 -Apply` wraps repo sweep and selects exactly
one promoted candidate per run unless `-CandidateId` is supplied. Remote feature
refs configured by `repoSweep.remoteFeaturePatterns` are planned alongside local
branches and can be exact-tuple pruned or clean-integrated from the remote ref,
then deleted after the target is updated.
Remaining-candidate cleanup is a bounded remediation queue. Older `fork/codex/*`
branches must undergo redundancy analysis against the target and known related
branches before retention. Clean locked worktrees may be cleaned only when stale,
unprotected, and exact ownership/lock evidence proves safety; protected locked
worktrees require an exact protected-worktree policy tuple. Clean merge-conflicting
worktrees require a candidate-specific conflict remediation packet with files,
hunks when available, validation commands, and a recovery command. Dirty detached
worktrees with sensitive paths must be preserved by exact path to a recovery
branch only when policy proves the sensitive paths are safe to copy; otherwise
retain with the sensitive path list, owner/age evidence, and a manual recovery
command.
Merge-failed retained candidates are not terminal by default. Repo sweep promotes
policy-eligible conflicts to symbolic action `resolve_conflicts_with_agent`,
writes an exact-tuple agent remediation queue packet under
`agentRemediationQueue.queueRoots`, and expects Codex/Claude surface adapters to
run `tools\closeout\agent-remediation-queue.ps1 -RepoRoot .` before declaring
the candidate blocked. Codex Desktop should spawn one bounded background agent
per eligible shard up to `agentRemediationQueue.maxParallelAgents`, require each
agent to stay within its packet read/write scope, and write durable result
packets under `agentRemediationQueue.resultRoot`; if the current surface cannot
spawn agents, run the consumer with `-MarkUnavailable` or report
`agent_remediation_surface_unavailable` with the queue path and recovery command.
After result packets exist, run
`tools\closeout\agent-remediation-queue.ps1 -RepoRoot . -CollectResults`;
out-of-scope changed paths or stale tuples block. Source mutation still happens
only through repo-owned clean integration/finalize after the coordinator
revalidates the exact tuple and consumes resolved results or blockers.

Evidence-preserving transaction prune: branches are transactions, not archives.
Before pruning a stale, redundant, patch-equivalent, historical-only, or
non-ancestor branch/worktree, repo sweep must write recovery evidence under
`repoSweep.evidencePreservingPrune.recoveryRoot`, for example
`.claude-state/closeout/manual-prune/`. Non-ancestor or historical branch
deletion requires bundle-backed recovery evidence plus reviewer prune-readiness
verdicts in the exact tuple. Dirty detached worktree removal requires tracked
binary diff, untracked byte copies, file modes, HEAD/target heads, SHA256
hashes, preservation ref, reviewer verdicts, and recovery commands. Missing,
stale, hash-mismatched, or out-of-root recovery artifacts block prune.

### Gap 1 — Workflow Debt Gate (agent-side, fires regardless of hook)

The pre-final hook (`codex_pre_final.ps1`) is advisory and may not fire on
every turn. Before any response that claims bridge-related work is complete
— any sentence containing "done", "complete", "finished", "all set", "that's
all", or equivalent — explicitly verify all three:

1. `next_pending_bridge_action` — top item is non-actionable or already
   dispositioned (no open actionable Claude-owned items remaining)
2. Inbox is drained — `check_inbox` returned empty or every surfaced message
   is marked handled
3. Every message surfaced in this turn has a disposition code
   (`acting` / `parked` / `blocked` / `displaced` / `rejected`)

If any of the three fails, address the debt before delivering the response.

### Gap 2 — Context Pressure: Proactive STATUS_UPDATE

User stops are undetectable — accept as a known gap with no mitigation.
For context compaction (the only closeable case):

**Proxy signals for approaching session limits:**
- A prior compaction message is visible in context
- Substantial bridge traffic has occurred in this session (many send/receive cycles)
- System context contains a compaction notice

When any proxy signal is present, before concluding substantial bridge work:
1. Send Codex a `STATUS_UPDATE` containing: active session GUID, summary of
   open ledger items, any unsent/queued messages, and the note
   "Claude context approaching limits — bootstrap next session to resume"
2. Record the STATUS_UPDATE as sent in the ledger to prevent duplicate sends

Do NOT wait for the hard context wall. Send proactively when the first
proxy signal appears — treat it as a voluntary graceful shutdown.

### Gap 3 — Monitor Liveness Before "Waiting for Codex"

Before any response that says Claude is waiting for Codex's reply, follow
the full recovery chain — do not skip steps:

1. Monitor task active in-context with correct session id → proceed
2. Monitor missing or stale → attempt restart with known session id and params
3. Session id unknown (compaction wiped it, bootstrap never ran) → run
   `bootstrap_session.py` first to re-register and get a valid GUID,
   then start Monitor
4. Only after Monitor is confirmed live → deliver the response

Skipping to step 2 without a valid session id produces a broken Monitor
that silently fails — still sitting doing nothing, just with an
active-looking task. The goal is a **confirmed-live** Monitor, not just
a started one.

Also check at the start of any turn resuming after a long idle.

---

## Architecture (Locked — Do Not Deviate)

- **Fork of MLV-App** — not a rewrite, not a new tool
- **Process-per-batch isolation**: .NET orchestrator launches 10 independent
  MLVApp.exe --batch processes. NO internal multi-threading for v1.
- **Use QApplication even in batch mode** (NOT QCoreApplication) — the export
  code path touches widgets, fonts, signal/slot machinery internally
- **Surgical extraction**: refactor the existing GUI CDNG export handler into a
  callable helper. Do NOT invent new exporter APIs or DNG writing code.
- **CDNG likely does not use FFmpeg** — DNG export probably writes TIFF/DNG
  frame files directly rather than using an FFmpeg pipeline. However, do NOT
  treat this as fact. Phase 0 must PROVE it by quoting actual include
  statements and function calls from the codebase.

## Target Export Format
**Cinema DNG sequences** (.dng files, one per frame)
- This is NOT a single-container format like ProRes or H.264
- Output layout: `<outRoot>/<clipBaseName>/clipBaseName_000001.dng`
- Error handling is per-frame (a corrupt frame can be skipped without losing
  the entire clip)
- DNG sequences are large — disk I/O and output volume matter

## Settings / Receipt Strategy (PHASED — Critical Design Decision)
- **v1 (Phases 0-5)**: Use MLV-App's DEFAULT processing settings when opening
  a file. Do NOT attempt .marxml receipt parsing yet. Get the export loop
  working first with whatever defaults the app applies on file open.
- **v1.1 (Phase 6)**: Add --receipt flag for .marxml loading. Extract receipt
  parsing from MainWindow into a standalone loader. Apply to mlvObject_t.
- **Rationale**: Receipts add complexity. Layering them onto a working export
  pipeline is safer than building both simultaneously. The user needs receipts
  eventually but the export loop must work first.

## Build Environment
- Windows 10
- Qt Creator with Qt 5.15 LTS
- MinGW toolchain
- FFmpeg dev libraries in platform/qt/FFmpeg/ (needed for GUI ProRes/H264,
  but likely NOT used by CDNG export path — confirm in Phase 0)

## Key Technical Constraints
- Always `QApplication`, never `QCoreApplication`
- `app.setQuitOnLastWindowClosed(false)` in batch mode
- All new files go in `src/batch/`
- Modified files: `platform/qt/main.cpp`, `platform/qt/MLVApp.pro`, and
  targeted patches in the CDNG export path
- Circular includes between MainWindow.h and batch headers are FORBIDDEN —
  use BatchTypes.h as the shared type header
- Use BatchPrompts helper class for dialog replacement — no inline if/else

---

## File Structure

### New Files (create these)
```
src/batch/
  BatchTypes.h        — Shared structs (ProcessingProfile, ProcessResult)
                        NOTE: ProcessingProfile fields are TBD until Phase 0
                        discovers the real internal setting names/types.
                        Do NOT pre-specify fields like "debayerAlgorithm = 4"
                        or "whiteBalanceKelvin" — these are guesses that may
                        not match MLV-App's actual API.
  BatchContext.h       — Static singleton for batch mode flags
  BatchContext.cpp     — Static member definitions
  BatchPrompts.h      — Helper class for dialog replacement in batch mode
  BatchPrompts.cpp    — shouldSkipFrame(), shouldContinue() implementations
  BatchRunner.h       — CLI batch orchestration class
  BatchRunner.cpp     — Enumerate MLVs, call export helper, log results
```

### Modified Files (surgical patches only)
```
platform/qt/main.cpp       — CLI/GUI branching before MainWindow creation
platform/qt/MLVApp.pro     — Add new HEADERS and SOURCES entries
platform/qt/MainWindow.cpp — Extract CDNG export into callable helper
                            — Replace QMessageBox calls with BatchPrompts
platform/qt/MainWindow.h   — Declare new static/public export helper method
```

---

## Implementation Phases (Execute In Order — Do Not Skip Ahead)

### Phase 0: Recon — Map the Real Code
Before writing ANY new code:
1. Find the CDNG export QAction handler in MainWindow.cpp
2. Trace the FULL call chain down to DNG file writing
3. **Confirm whether this path uses FFmpeg or direct TIFF/DNG writing**
4. Find every QMessageBox, QProgressDialog, QFileDialog, and ui-> reference
5. Find how output folder and frame filenames are determined
6. Identify the per-frame export loop structure
7. Report: file paths, function names, line numbers, call graph

### Phase 1: Foundation Files (No Export Logic Yet)
Create BatchTypes.h, BatchContext.h/.cpp, BatchPrompts.h/.cpp (stubs).
Update MLVApp.pro.
Verify: compiles clean, GUI still launches normally.

### Phase 2: CLI Entry Point
Modify main.cpp:
- Early --batch detection via raw argv scan
- QCommandLineParser for: --input, --output, --skip-errors, --log, --verbose
- Note: --receipt is NOT included yet (deferred to Phase 6)
- BatchContext flags set before BatchRunner call
- Stub BatchRunner that prints args and exits
Verify: `MLVApp --batch --help` shows usage, no GUI window appears.

### Phase 3: CDNG Export Helper Extraction (THE HARD PART)
Refactor the GUI CDNG export handler into two layers:
1. Original UI handler remains (calls helper internally)
2. New helper callable from batch mode:
   - Opens MLV with default processing settings (no receipt yet)
   - Creates `<outDir>/<clipBaseName>/` subfolder automatically
   - Writes frame sequence: clipBaseName_NNNNNN.dng
   - Uses the EXACT same DNG writing code as GUI
   - No dialogs, no progress UI
   - Returns ProcessResult with frames exported/skipped/errors
Verify: single MLV file exports to DNG sequence from CLI with defaults.

### Phase 4: Dialog/Prompt Patching via BatchPrompts
Create BatchPrompts utility class:
```cpp
class BatchPrompts {
public:
    // Returns true = skip and continue, false = abort
    static bool shouldSkipFrame(const QString& clipName, int frameIndex,
                                const QString& errorDetail);
    // Returns true = continue processing, false = abort
    static bool shouldContinue(const QString& context,
                               const QString& message);
};
```
Implementation logic:
- BatchContext::isBatchMode() && skipErrors → log warning, return true
- BatchContext::isBatchMode() && !skipErrors → log error, return false
- !BatchContext::isBatchMode() → show original QMessageBox, return user choice

Replace ONLY the QMessageBox calls in the CDNG export path.
Replace QProgressDialog with stdout logging in batch mode.
Do NOT globally disable all message boxes.
Verify: corrupt frame is skipped or causes exit based on --skip-errors flag.

### Phase 5: BatchRunner + Logging + Exit Codes
Complete BatchRunner:
1. Enumerate *.mlv files in input path (single file or folder)
2. For each file: open, export CDNG with defaults, log result
3. Structured stdout logging (parseable by .NET orchestrator):
   [BATCH] START input=<path> output=<path>
   [BATCH] FILE <filename> frames=<N>
   [BATCH] SKIP <filename> frame=<N> error=<description>
   [BATCH] DONE <filename> exported=<N> skipped=<N> elapsed=<seconds>
   [BATCH] COMPLETE files=<N> succeeded=<N> failed=<N> total_elapsed=<seconds>
4. Optional --log <file> mirrors stdout to file
5. Exit codes (see table below)
Verify: full batch run, parseable log, correct exit codes, .dng files exist.

### Phase 6: Receipt Loading (v1.1 — AFTER export loop is proven stable)
NOW add --receipt flag, in two sub-steps with separate gates:

**6A — Parse receipt headlessly:**
1. Find the .marxml parsing code in MainWindow
2. Extract into standalone ReceiptLoader function (no MainWindow dependency)
3. Add --receipt (-r) to QCommandLineParser
4. In BatchRunner, load receipt and PRINT parsed settings to stdout
5. Do NOT apply settings to export yet — just verify parsing works
Gate 6A: receipt loads, settings print correctly, bad XML returns error

**6B — Apply parsed settings to export:**
1. Apply loaded settings to mlvObject_t / processingObject_t before export
2. Use the EXACT same setter functions the GUI uses (discovered in Phase 0)
3. If --receipt not provided: use defaults (current v1 behavior preserved)
4. If --receipt provided: load, apply, then export
Gate 6B: export WITH receipt produces visibly different .dng output than
WITHOUT receipt (compare file sizes, visual appearance, or metadata).
Additionally, log a "settings fingerprint" after applying receipt — read back
actual processing state values (exposure, WB, dual ISO, debayer) from the
mlvObject_t/processingObject_t and print them. This proves settings reached
the pipeline, not just the parser.
This split prevents the classic trap of "receipt parsed but never applied."

---

## Exit Code Reference
| Code | Meaning                                        | .NET Orchestrator Action    |
|------|------------------------------------------------|-----------------------------|
| 0    | All files exported successfully                 | Mark batch as complete      |
| 1    | Some failures occurred (with --skip-errors)     | Log warnings, review output |
| 2    | Bad arguments / usage error                     | Fix command and retry       |
| 3    | Cannot open input file or folder                | Check paths, retry          |
| 4    | Export failure (without --skip-errors, fatal)   | Investigate, manual retry   |
| 5    | Receipt file not found or invalid (Phase 6+)    | Check receipt path/format   |

---

## Behavioral Rules for Claude Code

1. **No speculation** — search the repo and quote exact code before writing
2. **No new exporter APIs** — reuse the existing CDNG export code path
3. **Smallest diff possible** — surgical changes only
4. **Show full diffs** before applying to any existing file
5. **One phase per response** — do not jump ahead
6. **Compile after every change** — `cd platform/qt && qmake && mingw32-make -j8`
7. **Never use QCoreApplication** — always QApplication
8. **Never create circular includes** — BatchTypes.h is the shared type header
9. **Treat CDNG as frame-sequence** — per-frame error handling, subfolder output
10. **CDNG likely does NOT use FFmpeg** — Phase 0 must prove this with evidence
11. **No receipt parsing until Phase 6** — use defaults for Phases 0-5
12. **Use BatchPrompts helper class** — no inline if/else for dialog replacement
13. **Patch only CDNG export path dialogs** — do NOT globally disable message boxes

---

## CLI Usage (Target)

```bash
# v1: Single file with defaults (Phases 0-5)
MLVApp.exe --batch --input "C:/footage/clip.mlv" --output "C:/exports" --skip-errors

# v1: Folder of MLVs with defaults
MLVApp.exe --batch --input "C:/temp/batch_01/" --output "C:/exports" --skip-errors --log "batch_01.log"

# v1: Verbose logging
MLVApp.exe --batch --input "C:/footage/" --output "C:/exports" --skip-errors --verbose

# v1.1: With receipt (Phase 6, after export loop is stable)
MLVApp.exe --batch --input "C:/footage/clip.mlv" --output "C:/exports" --receipt "settings.marxml" --skip-errors
```

## .NET Orchestrator Integration (Later — Not Claude Code's Job)
- Hardlink .mlv files to temp batch folders (same NTFS volume required)
- Launch N processes with ProcessStartInfo + CreateNoWindow
- Parse [BATCH] log lines from stdout for progress monitoring
- Read exit codes to determine per-batch success/failure
- Track exact batch folders created in List<string>, clean up only those
