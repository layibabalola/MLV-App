# Canonical Closeout Contract

## Purpose
This is the shared closeout rule block. It must be byte-for-byte identical
across repos. Repo-local paths, branch names, command wrappers, and
implementation notes may appear only below this block and must not weaken it.

## Priority
- This contract is the source of truth for closeout behavior.
- Any repo-local rule that conflicts with it is a blocker.
- Shared closeout artifacts fail closed when stale, missing, mismatched,
  unverifiable, or contradicted by live state.
- Compare `CLOSEOUT-IMPLEMENTATION-PROMPT.md` itself, not just summary prose,
  in the same work block whenever the workflow contract changes.
- Refresh the docs-sync bundle in the same work block as any workflow-contract
  change.
- `repo_closed_for_final_response` plus the repo-closed postcondition are
  required before any final completion claim.
- A repo is not closed unless the current closeout contract, compare artifact,
  freshness proof, repo-state snapshot, capability ledger, closeout clean
  truth, and repo-closed postcondition all agree.

## Shared Contract
- A mechanically safe action may proceed by deterministic review. An
  ambiguous action must have structured adjudication.
- Review surfaces, subagents, CI, and manual reviewers are transport only;
  repo-owned actors are the only mutation authority.
- Read-only planning may be broad, but mutation is single-candidate by default.
- Bulk mutation requires an explicit audited override, configured permission,
  reviewer approval when required, per-candidate exact tuples, recovery
  evidence, and serialized execution.
- Required shared artifacts are `CLOSEOUT-IMPLEMENTATION-PROMPT.md`,
  `CLOSEOUT-STANDARD.md`, `CLOSEOUT-CROSS-MAP-COMPARISON.md`,
  `CLOSEOUT-REQUIREMENTS-TRACE.md`, `closeout-compare-result.v1`, the
  docs-sync bundle, repo-state snapshot, capability ledger, closeout history
  index, rollback readiness evidence, and repo-closed postcondition evidence.
- Compare the real implementation prompt, not just summary prose.
- Keep `closeout-compare-result.v1` schema-stable, versioned, committed when
  applicable, machine-validated, and tied to a visible freshness marker or
  timestamp.
- Keep the compare artifact as a live machine object, not a prose summary.
- Keep the dashboard read-first and symbolic-action-only.
- Keep rollback separate, approval-gated, evidence-gated, and non-destructive
  by default.
- Treat stale docs, stale compare artifacts, snapshot drift, missing
  repo-closed evidence, retained refs, extra worktrees, stashes, dirty state,
  stale runtime services, stale served dashboard paths, and orphan runtime
  artifacts as blockers.
- Clean protected target branches may close as a no-op only when the protected
  target is clean and `repo_closed_for_final_response` passes.
- If finalize reaches a dirty protected target branch without an explicit work
  block, preserve the exact dirty state onto an allowed work-block branch,
  materialize a manifest there, and report the remaining blocker from the
  preserved branch.
- Must satisfy hard-clean final state: merged-and-pristine.
- Must prove no dirty working tree, no retained local feature branches, no
  unmerged local branches, no retained remote feature or snapshot refs, no
  non-current worktrees, no stashes, no stale transaction branches, no stale
  runtime services, and no orphan runtime artifacts.
- Must auto-resume interrupted closeout when target-branch residue proves
  cleanup did not finish.
- Must write durable audit evidence for cleanup decisions, including local
  feature branch cleanup, remote ref cleanup, post-closeout orphan quarantine,
  runtime lifecycle, retained remediation, dirty-detached preservation, and
  archive or prune outcomes.
- Must use exact-tuple review quorum for mutation-sensitive actions.
- Must process retained candidates one at a time, with immediate pre-apply
  revalidation.

## Shared Must Not
- Do not give the dashboard direct Git mutation authority.
- Do not let the dashboard branch, reset, stash, pull, merge, push, delete
  refs, or clean worktrees.
- Do not let compare refresh implicitly trigger rollback, auto-close, or
  auto-approve.
- Do not treat prose alignment as a substitute for schema, freshness, or
  ledger validation.
- Do not let stale docs or stale compare artifacts pass as current.
- Do not collapse rollback into normal closeout cleanup.
- Do not mark a repo closed unless the final gate is satisfied.
- Do not treat a partial repo-closed check as final.
- Do not force-push as part of closeout recovery.
- Do not allow response, metrics, timestamp, or final-completion hooks to
  create, refresh, or resurrect managed session worktrees.
- Do not treat a helper that answers `/health` as valid if it is serving stale
  worktree content.

## Shared Verify
- Verify the docs-sync bundle was refreshed in the same work block as the
  contract change.
- Verify the prompt comparison checks the real implementation prompt, not just
  summary docs.
- Verify `closeout-compare-result.v1` still matches the agreed schema and
  version.
- Verify freshness is visible and machine-checkable.
- Verify dashboard actions remain evidence-only, preview-only, request-only,
  or symbolic-action-only.
- Verify rollback still has its own approval and evidence path.
- Verify the current closeout docs and compare artifact tell the same story in
  every repo.
- Verify `repo_closed_for_final_response` before treating the repo as done.
- Verify local and remote feature or snapshot refs are integrated, deleted,
  archived, or retained only with exact blocker evidence.
- Verify worktree enumeration succeeds and reports every non-current worktree.
- Verify interrupted closeout resume is installed and covered by tests.
- Verify review quorum packets include candidate id, evidence hash, policy
  hash, allowed actions, and current branch or target pins.
- Verify finalize retry ledger records every retry and terminal retry stop.
- Verify post-closeout orphan quarantine is installed and covers integration
  branches and worktrees.
- Verify dirty-detached sibling cleanup archives recovery branches before
  pruning.
- Verify runtime service lifecycle stops, status-checks, and restarts
  repo-owned services from promoted target code.
- Verify launcher and helper processes cannot remain orphaned after tests or
  unattended startup.
- Verify final responses include `repoClosedAuditPath`, `repoStateSnapshotPath`,
  final HEAD, target branch, clean state, worktree count, and branch prune or
  retain outcome.

## Shared Report Envelope
- Objective
- Last completed work
- Next steps
- Blockers
- Freshness marker or timestamp
- Compare findings
- Repo-closed postcondition
- Branch/worktree/stash/ref cleanup state
- Runtime service lifecycle state
- Retained remediation state
- Final HEAD and target branch
- Repo-state snapshot path
- Repo-closed audit path

## Definitions
- `docs-sync bundle`: the exact set of closeout docs and state files that must
  be refreshed together whenever the workflow contract changes.
- `freshness marker`: a machine-checkable signal such as a timestamp,
  source-artifact hash, verified-at field, work-block id, or snapshot pointer
  that proves the bundle is current.
- `compare artifact`: the live machine object `closeout-compare-result.v1`,
  not a prose summary or dashboard rendering.
- `work block`: the single unit of closeout change where the prompt, docs,
  compare artifact, implementation, tests, and freshness proof are updated
  together.
- `repo_closed_for_final_response`: the final hard gate proving the repo is
  actually closed enough to answer as done.
- `repo-closed postcondition`: the machine-verified final state showing the
  repo is merged-and-pristine and has no leftover local, repo-global, or
  operational hygiene issues.
- `closeoutCleanTruth`: the unified final truth record separating raw Git
  cleanliness, policy cleanliness, and cleanup cleanliness.
- `merged-and-pristine`: target branch contains the work and the repo has no
  dirty files, no retained local feature branches, no unmerged local branches,
  no retained remote feature or snapshot refs, no non-current worktrees, no
  stashes, no stale transaction branches, no stale runtime services, and no
  orphan runtime artifacts.
- `exact tuple`: the full candidate, action, evidence, and policy tuple used
  for cleanup or mutation, including branch or ref identity, current head,
  expected target pins, allowed action, evidence hash, policy hash, and
  reviewer decision where required.
- `retained remediation queue`: the ordered set of retained candidates
  processed by bounded automation one candidate at a time.
- `dirty-detached preservation`: the path that commits exact dirty paths from a
  detached sibling worktree to a recovery branch, archives that branch to a
  verified archive ref and bundle or patch summary, then prunes only after
  proof.
- `runtime service lifecycle`: config-driven stop, status, and start handling
  for repo-owned services that must survive or be restored around promotion and
  closeout.
- `orphan runtime artifact`: a leftover process, listener, helper, launcher
  wrapper, stale served path, generated worktree, unmanaged service artifact,
  or closeout-lock residue that can make the repo appear closed while
  operational residue remains.
- `repo-bound-launcher`: a tracked process whose command line references the
  repository root or a repo-owned script path.
- `generic-tracked-shell`: a tracked process that does not reference the
  repository root and is safe to ignore unless other evidence says otherwise.

## Repo-Local Notes
- Keep local scripts, paths, branch prefixes, target branch names, and
  implementation details repo-specific.
- Keep the shared safety language identical across repos.
- Treat any mismatch in the shared contract as a blocker, not a note.
- Repo-local automation may self-heal only through bounded, config-driven,
  auditable actors.
- Repo-local dashboards may expose requests or symbolic actions, but not raw
  Git mutation.
