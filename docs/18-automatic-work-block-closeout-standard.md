# Automatic Work Block Closeout Standard

This standard defines repo-owned brokered auto-closeout for Codex Desktop,
Claude Desktop, humans, hooks, dashboards, and future tools. The repo owns the
workflow. External tools are trigger and agent surfaces only; safety logic,
policy, validation, audit, and mutation live in versioned repo files.

## Goal

Every completed work block must be either promoted into the configured target
branch or retained with exact proof that autonomous promotion is unsafe. Branches
are transactions, not permanent storage.

Canonical path:

```text
work starts -> broker owns it -> files are claimed -> work completes ->
detector -> safe repair -> autonomous quorum -> pinned validation ->
clean integration -> audit -> cleanup/prune -> repo sweep fixed point
```

Closeout always runs. Mutation happens only when eligibility, repair,
autonomous review quorum, validation, and pinned-ref checks pass.

## Non-Negotiable Principles

- Closeout must not silently stop at `review_quorum_missing`.
- For eligible symbolic actions, the repo automatically convenes exact-tuple
  quorum: primary self-review plus independent stranger reviews.
- Human intervention is required only for real blockers: reviewer rejection,
  ambiguous evidence, ref drift, validation failure, unproven ownership, unowned
  or mixed dirty state, active locks, manual-only policy, or protected override.
- Merge conflicts are not terminal by default. They become
  `resolve_conflicts_with_agent` dispatch candidates and enter a bounded
  agent-remediation queue.
- Foreign dirty work is retained and audited, never committed, stashed, deleted,
  or reset by another work block.

## Required Repo Infrastructure

Each repo must persist:

- durable agent instructions: `AGENTS.md`, `CLAUDE.md` or equivalent, and hook
  settings only where that surface actually supports hooks
- durable policy: `closeout.config.json` plus contract/schema checks
- durable actors: start, complete, detector, repair, evidence repair,
  auto-quorum, finalize, repo sweep, retained-remediation, orphan quarantine,
  audit, and contract verification
- durable trigger adapters: Codex instructions, Claude Desktop command/manual
  bridge instructions, optional Claude Code Stop hooks, and git hook installers
- durable tests proving scripts/config/symbols are present and current

A fresh session must discover the workflow from repo files alone.

## Broker Model

The work block broker records:

- `workBlockId`
- branch and worktree ownership
- path claims
- dirty baseline
- manifest and event ledger
- locks and leases
- completion state
- audit trail

Completion/finalize must prefer an explicit `workBlockId`. Without one, the
broker must select deterministically and report the selected block, branch,
worktree, state, timestamps, and selection reason before mutation.

## Policy File

`closeout.config.json` must define:

- target branch and remote
- protected branches and local-only mode
- validation commands
- generated and sensitive paths
- stash, cleanup, dirty split, and evidence policies
- quorum policy and autonomous/manual action classes
- retained-candidate remediation policy
- agent-remediation queue policy
- finalization retry limits
- tooling baseline requirements

## Dirty Work Classification

Dirty files are classified against the completed branch delta and broker
baseline:

- `ownedDirty`: belongs to this work block and may be checkpointed only through
  exact-tuple quorum.
- `mixedDirty`: overlaps baseline dirty state and must not be whole-file
  checkpointed.
- `unownedDirty`: cannot be attributed and blocks that candidate.
- `foreignDirty`: outside this candidate and retained without blocking
  independent closeout unless it overlaps the target delta.

Clean-at-start files may be auto-claimed only when the broker proves they were
clean or absent at the dirty baseline and no other active block claims them.

## Autonomous Review Quorum

High-impact actions require exact tuple approval:

```text
candidate id + action id + evidence hash + policy hash + pinned refs
```

The repo must generate:

- primary self-review at the configured score
- at least two independent stranger/subagent reviews at the configured score
- accepted review manifest
- approval/decision artifact

Tuple drift makes reviews stale. Stale reviews regenerate only when policy
allows and the candidate is still eligible.

## Symbolic Actions

Reviews and dashboards request only symbolic actions, for example:

- `checkpoint-owned-dirty`
- `publish-feature-branch`
- `clean-integrate`
- `delete-local-branch`
- `delete-remote-branch`
- `delete-worktree`
- `dirty-split`
- `quarantine-orphan`
- `prune-snapshot`
- `resolve-conflicts-with-agent`

Generated shell is not an approval surface.

## Clean Integration

Final integration runs from a clean temporary target worktree. The actor must:

- fetch before evidence when configured
- pin feature, local target, remote-tracking target, and advertised remote head
- create a temp worktree from the pinned target
- merge/cherry-pick the candidate
- sanitize configured generated state before diff check
- write fresh evidence heartbeat when required by push gates
- run `git diff --check`
- run configured validation
- prove target update is fast-forward safe
- push or update target
- update local target safely
- prune only after success
- preserve recovery artifacts on failure

Surface adapters that launch the bounded closeout child should emit a configurable
stderr heartbeat during long quiet runs. The heartbeat is liveness evidence only:
it must not alter stdout JSON, bypass timeouts or CPU-stall detection, or weaken
the repo-closed postcondition.

Target push non-fast-forward is a recoverable race: fetch, re-pin, rebuild the
integration candidate, regenerate quorum if the tuple changed, and retry within
the configured bounded limit. Never force-push the target automatically.

## Repo Sweep

Repo sweep is separate from current work-block closeout. It plans all branches,
worktrees, stashes, snapshots, and orphaned artifacts read-only, then mutates
one candidate at a time.

Expected behavior:

- integrated branch: auto-quorum prune
- clean merge-required branch: auto-quorum clean-integrate, validate, prune
- checked-out clean integrated worktree: remove worktree, then delete branch
- checked-out clean merge-required worktree: integrate from temp target worktree
- dirty branch: classify owned/foreign/unowned/mixed
- stale clean locked worktree: inspect, auto-quorum cleanup if safe
- active/protected locked worktree: retain with owner/lock evidence
- backup branch: compare by tree, ancestry, patch-id, cherry, and subject
- remote feature ref: compare to target, integrate or delete when safe
- stash: promote/drop only through a separate approved action

Retain is valid only after active investigation proves autonomous mutation is
unsafe and writes a candidate-specific report.

## Agent Remediation Queue

Merge-required branches must not be retained merely because they conflict.

When clean integration fails with conflicts and policy allows agent remediation,
repo sweep must:

1. Write a candidate report with merge base, branch head, target head, changed
   paths, conflict paths, validation requirements, and recovery command.
2. Promote the candidate to symbolic action `resolve_conflicts_with_agent`.
3. Generate exact-tuple quorum for the dispatch action.
4. Write a queue packet under the configured agent-remediation root.
5. Split large conflict sets into shards by subsystem/path group.
6. Instruct Codex/Claude surface adapters to spawn one background agent per
   shard where available.
7. Require agents to resolve only assigned paths in temp integration worktrees.
8. Require the coordinator to apply resolved shard patches one at a time in a
   clean target worktree, then run diff-check, validation, finalize, audit, and
   prune.

Repo scripts do not rely on chat memory. If no surface can spawn agents, the
queue packet is the exact manual recovery brief and the candidate remains
retained with proof.

## Retained-Remediation Actor

Each repo must provide a durable actor such as:

```text
tools/closeout/remediate-retained-closeout.ps1 -RepoRoot . -Apply
```

It wraps repo sweep and processes exactly one promoted candidate per run unless
an explicit candidate id is supplied. It never batch-mutates unrelated retained
candidates.

Allowed outcomes:

- `dirty-split`
- `clean-integrate`
- `prune`
- `quarantine`
- `resolve-conflicts-with-agent`
- `retain-with-proven-blocker`

## Surface Adapters

Codex Desktop:

- starts work blocks through repo entrypoints for non-trivial work
- runs completion/finalize before final response after non-trivial edits
- reads agent-remediation queue packets and spawns background agents when asked
- leaves mutation to repo-owned actors

Claude Desktop:

- is a command/manual bridge unless a real local command/MCP bridge exists
- must not assume Claude Code Stop hooks exist
- documents exact manual command when command execution is unavailable

Hooks:

- protect commits and pushes
- verify broker/path-claim rules
- do not replace chat/workblock completion triggers

Response/final hooks may refresh context, metrics, timestamps, and lightweight
broker manifests, but must not create or resurrect managed session worktrees.

## Audit Requirements

Every outcome writes durable JSON:

- success
- blocked repair
- stale refs or stale review
- validation failure
- target race retry
- retained foreign dirty work
- dirty split/checkpoint
- branch, worktree, remote branch, stash, and snapshot cleanup
- orphan quarantine
- agent-remediation dispatch
- retained blocker with exact evidence

## Required Tests

Each repo must test:

- contract parity and tooling drift detection
- stale refs, stale reviews, tuple mismatch, and validation failure
- local-only closeout
- foreign dirty independent closeout
- owned dirty checkpoint and mixed baseline protection
- repo sweep planning and one-candidate apply
- autonomous quorum generation
- integrated branch and checked-out worktree pruning
- stale locked cleanup and active/protected locked retention
- backup branch redundancy analysis
- dirty detached preservation
- missing evidence repair
- target push race recovery
- generated closeout state exclusion from owned dirty classification
- merge-failed candidate promotion to `resolve_conflicts_with_agent`
- agent-remediation queue packet creation and sharding
- surface adapters documented as trigger/agent surfaces, not safety systems

## Definition Of Done

A work block is done only when:

- owned changes are committed/published or intentionally retained with audit
- detector and safe repair ran
- autonomous quorum ran where eligible
- validation passed
- clean integration completed or blocked with exact reason
- cleanup/prune ran or was retained with audit
- agent remediation was dispatched for eligible conflicts
- remaining dirty state is classified
- final state is inspectable through durable logs/artifacts
