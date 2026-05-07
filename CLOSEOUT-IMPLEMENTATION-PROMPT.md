# Closeout Implementation Prompt

Reusable prompt for implementing the portable, repo-owned brokered closeout framework in a target repository.

Source standard:

- `CLOSEOUT-STANDARD.md`
- `CLOSEOUT-ADJUDICATION-PROTOCOL.md`
- `CLOSEOUT-FRAMEWORK-PROFILES.md`
- `CLOSEOUT-CAPABILITY-LEDGER.schema.json`
- `CLOSEOUT-REQUIREMENTS-TRACE.md`

This prompt is for future implementation work. It does not replace the standard, the capability ledger, or repo-specific policy.

## Prompt

You are implementing the portable, repo-owned brokered closeout framework in this repository.

Your job is to make closeout safe, inspectable, and honest for the selected profile. Do not treat prose as implementation. A capability is implemented only when repo-owned config, loader, actor, adapter, contract, drift check, behavioral test, audit artifact, or explicit scoped exception proves it.

First rule:

> A mechanically safe action may proceed by deterministic review. An ambiguous action must have structured adjudication. In both cases, mutation remains repo-owned, symbolic, exact-tuple, validated, and audited.

## Operating Rules

- Preserve user and foreign work. Never stash, reset, delete, commit, or silently absorb foreign dirty state.
- Do not force-push a target as automatic target-race recovery.
- Do not allow reviewers, dashboards, hooks, chat responses, or generated shell to bypass repo-owned mutation actors.
- Do not make subagents a Core dependency.
- Do not smuggle Max-profile machinery into Core.
- Do not claim final completion unless the repo proves `repo_closed_for_final_response`.
- When implementation is unsafe or unavailable, write a durable blocker or ledger entry instead of pretending success.

## Phase 0: Inspect And Select Profile

Read the repo before editing.

Inspect:

- existing closeout, publish, cleanup, and branch/worktree scripts
- agent instructions such as `AGENTS.md`, `CLAUDE.md`, or local equivalents
- repo config, hooks, CI workflows, test entrypoints, and runtime service definitions
- dirty-state, stash, branch, worktree, and remote-ref assumptions
- existing generated/exempt artifact policy
- existing historical notes or incident records

Decide the adoption profile:

- Choose Core when the repo needs portable safety and honest completion.
- Choose Standard when recurring closeout, multiple agents, retained candidates, protected target runs, target races, hooks, branches, or worktrees are common.
- Choose Max / Conditional when the repo owns heavy risks: runtime services, historical prune, dirty split, remediation freeze, agent queues, retained remote refs, or complex worktree hygiene.
- Add Surface Plugins only for actual Codex, Claude, CI, deterministic-reviewer, or manual bridge adapters.

Record unsupported rows honestly as `PARTIAL`, `NO`, `UNAVAILABLE`, or `UNKNOWN`.

## Phase 1: Create Or Update Framework Artifacts

Create or update these artifacts as appropriate for the repo:

- `CLOSEOUT-HISTORICAL-INCIDENT-MAP.md` or local adoption rationale
- `CLOSEOUT-ADJUDICATION-PROTOCOL.md`
- `CLOSEOUT-FRAMEWORK-PROFILES.md`
- `CLOSEOUT-CAPABILITY-LEDGER.schema.json`
- `CLOSEOUT-REQUIREMENTS-TRACE.md`
- `CLOSEOUT-STANDARD.md`
- `CLOSEOUT-CAPABILITY-LEDGER.json`

For cross-repo design work, also maintain:

- `CLOSEOUT-CROSS-MAP-COMPARISON.md`
- `CLOSEOUT-REFERENCE-MATRIX.md`

When a repo already has equivalent files, update them instead of creating duplicates.

## Phase 2: Implement Core

Core implementation MUST provide or explicitly block these safety invariants.

### Work Block Ownership

Implement repo-owned state for:

- work block id
- target branch or target ref
- start head
- dirty baseline
- path claims when available
- deterministic work-block selection

Finalize must select a unique work block or block durably.

### Dirty Classification

Implement dirty classification before repair, mutation, cleanup, or final response.

Required classes:

- owned dirty
- foreign dirty
- unowned dirty
- mixed dirty
- generated or exempt dirty

Foreign dirty must be preserved. Baseline-dirty overlap must block, split, or require stronger ownership proof. Generated/exempt classification must come from policy.

### Adjudication Contract

Implement or declare durable formats for:

- candidate evidence packet
- adjudication report
- unavailable or insufficient-review blocker
- exact mutation tuple
- mutation audit
- recovery command

Mechanically safe actions may use deterministic review. Ambiguous actions must use structured adjudication.

### Repo-Owned Mutation

Implement repo-owned actors for selected symbolic actions.

Each mutating actor must:

- load policy/config
- validate packet/report requirements
- validate exact tuple freshness immediately before mutation
- verify refs, dirty state, path scope, generated/exempt classification, and recovery evidence
- run required validation
- write success, stale, blocked, retained, or failure audit artifacts

Reviewers may recommend symbolic actions only.

### Bounded Processes And Drift

If closeout invokes child processes, implement bounded execution:

- timeout
- output cap
- process-tree termination
- failure text normalization
- durable audit

Implement tooling drift detection so stale closeout tooling reports drift instead of false success or false blockers.

### Hard-Clean Final Gate

Implement `repo_closed_for_final_response`.

The final gate must account for:

- selected work block
- target ref
- dirty state
- stash state
- branch state
- worktree state when relevant
- queue state when relevant
- runtime state when relevant
- generated/exempt artifacts
- retained blockers
- cleanup audit

If the gate fails, report WIP or blocked closeout. Do not claim final completion.

### Core Tests

Add behavioral or contract tests proving:

- ambiguous mutation blocks without adjudication
- reviewer output cannot mutate directly
- stale tuple blocks
- foreign dirty is preserved
- baseline-dirty overlap blocks whole-file checkpoint
- generated/exempt final utility writes do not become source work
- advisory hooks are non-authoritative
- response/final hooks do not create managed worktrees when that lifecycle exists
- hard-clean gate blocks dirty, stash, worktree, branch, queue, or runtime state according to policy
- stale tooling emits drift
- broad read-only planning does not imply broad mutation
- raw broad mutation requires candidate id or audited bulk override

## Phase 3: Implement Standard If Selected

Standard capabilities SHOULD include:

- target push race fetch/re-pin/retry or durable rerun blocker
- clean protected target no-op that still requires repo-closed verification
- retained candidate remediation before terminal retain
- unavailable or insufficient-review blocker artifacts
- checked-out and locked worktree handling
- independent review/quorum for high-impact ambiguity
- Git hook gates that feed authoritative blockers

Standard automation must not weaken Core. When unsure, block, preserve, or escalate.

## Phase 4: Implement Max / Conditional If Selected

Install Max capabilities only when the repo selects them or owns the risk.

Max capabilities include:

- automated subagent dispatch
- agent remediation queues
- evidence-preserving prune
- remediation freeze
- dirty split automation
- runtime service lifecycle
- remote feature clean integration

Conditional triggers:

- If repo-owned runtime services execute repo code, install runtime lifecycle handling or record `UNAVAILABLE`.
- If destructive historical prune is allowed, install evidence-preserving prune or record a blocker.
- If automated remediation can worsen state, install remediation freeze or record why unsupported.
- If remote feature cleanup is part of closeout, install remote feature clean integration or record a blocker.

## Phase 5: Surface Plugins

Implement Surface Plugins only for actual surfaces used by the repo.

Surface Plugins may:

- spawn subagents
- dispatch review packets
- collect reviewer results
- provide UI/reporting
- bridge manual decisions
- run deterministic proof checks

Surface Plugins must:

- preserve packet/report hashes
- record declared review surface
- avoid direct mutation unless declared as repo-owned actor
- emit unavailable/insufficient-review artifacts when required review cannot run
- feed authoritative repo-owned gates
- avoid weakening Core

## Phase 6: Populate Capability Ledger

Create or update `CLOSEOUT-CAPABILITY-LEDGER.json`.

Validate it against `CLOSEOUT-CAPABILITY-LEDGER.schema.json`.

For every capability row:

- set status to `YES`, `PARTIAL`, `NO`, `UNAVAILABLE`, or `UNKNOWN`
- set profile and decision
- record verification basis
- cite evidence paths
- cite test, actor, adapter, config, contract, drift-check, and audit paths where applicable
- record blockers
- record smallest next step
- record repo-specific notes
- record last verification time

Rules:

- `YES` must not be documentation-only, reported-only, not-implemented, or unknown.
- `YES` must cite executable behavior, behavioral test, contract or drift check, audit artifact, or scoped exception.
- `YES` must have no blockers.
- `PARTIAL`, `NO`, `UNAVAILABLE`, and `UNKNOWN` must include blockers and smallest next step.
- `UNAVAILABLE` must include unavailable reason.

## Phase 7: Verify And Close

Before reporting completion:

- run schema validation for JSON artifacts
- run relevant unit, contract, drift, and behavioral tests
- run closeout/postcondition tooling if the repo has it
- verify no foreign dirty work was mutated
- verify no target force-push occurred
- verify generated/exempt artifacts are accounted for
- verify retained candidates have durable blockers, remediation, or audits
- verify `repo_closed_for_final_response`

If closeout cannot complete, report the exact blocker and recovery command. Do not bury the blocker under a success summary.

## Stop Conditions

Stop and write a blocker when:

- required review cannot run
- tuple facts are stale
- dirty classification is ambiguous
- foreign dirty overlaps unsafe target mutation
- baseline dirty overlaps current claims without split or proof
- tooling drift makes blockers non-authoritative
- generated/exempt classification is ambiguous
- destructive cleanup lacks recovery evidence
- repo-owned actor for a symbolic action is missing
- target race would require force-push
- hard-clean final gate fails

## Final Response Format

When implementation work is complete, report:

- `Verified locally`: files changed, tests run, closeout/postcondition result
- `Cross-checked from prior analysis`: any cross-repo evidence used
- `Needs runtime profiling`: any behavior not exercised
- `Capability ledger`: path and validation result
- `Repo closed`: whether `repo_closed_for_final_response` passed

If the repo is not closed, say so plainly and include blocker kind, dirty state, retained candidates, stale refs, unavailable surfaces, or recovery commands as applicable.

## Expected Deliverables

At the end of a successful adoption, the repo should have:

- durable framework artifacts or local equivalents
- repo-owned closeout config
- repo-owned closeout actors/adapters
- contract or drift verifier
- behavioral tests for selected profile
- generated/exempt path policy
- capability ledger schema
- populated capability ledger
- hard-clean final response gate
- audit and recovery artifacts for closeout actions

## Non-Goals

Do not:

- require subagents for Core
- require PowerShell
- require Git worktrees
- require Max machinery in a minimal repo
- claim implementation from prose alone
- treat reported cross-repo evidence as local `YES`
- let reviewers mutate directly
- skip dirty classification
- skip hard-clean final gate

Principle:

> Implement the smallest honest profile, prove every `YES`, and block rather than improvise unsafe closeout.
