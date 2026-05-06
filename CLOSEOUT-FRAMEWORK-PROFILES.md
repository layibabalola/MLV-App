# Closeout Framework Profiles

Draft install profiles for the portable, repo-owned brokered closeout
framework.

Inputs:

- `CLOSEOUT-HISTORICAL-INCIDENT-MAP.md`
- `CLOSEOUT-CROSS-MAP-COMPARISON.md`
- `CLOSEOUT-ADJUDICATION-PROTOCOL.md`
- `CLOSEOUT-REFERENCE-MATRIX.md`

This document separates universal safety invariants from heavier automation and
surface-specific adapters. It is not the final normative standard and not the
machine-readable capability ledger.

## Profile Model

The framework has four layers:

| Profile | Purpose |
|---|---|
| Core | Portable safety invariants every repo can implement. |
| Standard | High-value automation for serious multi-agent or recurring closeout workflows. |
| Max / Conditional | Scar-tissue machinery for repos with many worktrees, retained candidates, runtime services, stale branches, or complex dirty-state remediation. |
| Surface Plugins | Codex, Claude, CI, deterministic-reviewer, and manual bridge adapters. |

The protocol is portable because the packet/report/tuple/audit contract is
portable. The implementation language, shell, agent surface, and repo actor
layout are repo-specific.

## Non-Smuggling Rule

Max-profile machinery MUST NOT be smuggled into Core merely because it was
useful in one repo.

Core is for universal safety invariants:

- evidence before ambiguous judgment;
- declared review surface;
- repo-owned mutation;
- dirty-state preservation;
- exact tuple;
- hard-clean final postcondition;
- no broad raw mutation by default.

Automation depth belongs in Standard, Max, or Surface Plugins unless the absence
of the capability creates a universal data-loss or false-completion risk.

## Core Profile

Core is the minimum portable install profile.

Core answers:

> Can this repo avoid unsafe mutation, preserve foreign work, require structured judgment for ambiguity, and avoid claiming done before repo-closed evidence exists?

### Core MUST Capabilities

| Capability | Core Requirement |
|---|---|
| `structured-adjudication-protocol` | Ambiguous actions require durable candidate evidence and adjudication before mutation. |
| `declared-review-surface` | Every adjudication names the review surface; missing required review blocks durably. |
| `candidate-evidence-packet` | Ambiguous or high-impact candidates have durable evidence packets. |
| `adjudication-report` | Judgment is recorded as a report tied to the packet hash. |
| `adjudication-to-symbolic-action-boundary` | Reviewers recommend symbolic actions only. |
| `repo-owned-mutation-after-adjudication` | Only repo-owned actors mutate after exact validation. |
| `exact-mutation-tuple` | High-impact mutation uses exact tuple validation and stale tuple rejection. |
| `bounded-wrapper-authority` | Closeout subprocesses are bounded and fail closed on timeout, output cap, or failure normalization. |
| `broker-manifest-dirty-baseline` | Work ownership uses a repo-owned manifest and dirty baseline. |
| `deterministic-work-block-selection` | Finalization must select a unique work block or block. |
| `dirty-classification` | Dirty state is classified before repair, mutation, or final response. |
| `foreign-dirty-preservation` | Foreign dirty work is never committed, stashed, reset, deleted, or silently absorbed. |
| `baseline-dirty-mixed-path-protection` | Paths dirty at broker start cannot be whole-file auto-claimed by the current work block. |
| `hard-clean-final-gate` | User-visible completion requires the hard-clean final gate. |
| `repo-closed-for-final-response` | `repo_closed_for_final_response` is the named completion postcondition. |
| `advisory-hooks-non-authoritative` | Exit-0 or advisory hooks may write status only; a later blocking gate must consume status. |
| `response-hook-no-worktree-resurrection` | Response/final hooks must not create, reuse, or resurrect managed worktrees. |
| `final-utility-generated-or-preclean` | Final utilities run before hard-clean or write only generated/exempt paths with provenance. |
| `tooling-drift-detection` | Stale closeout tooling reports non-authoritative drift, not false blockers. |
| `repo-sweep-read-only-planning` | Broad planning is allowed. |
| `repo-sweep-single-candidate-mutation` | Mutation is single-candidate by default. |
| `audited-bulk-override` | Bulk mutation requires explicit override, configured permission, per-candidate tuples, reviewer approval, audit reason, and recovery commands. |
| `no-force-push-target-recovery` | Target push races never authorize automatic force-push. |
| `historical-incident-traceability` | Repo adoption includes historical incident mapping. |
| `requirements-trace-to-original-standard` | Original standard/addenda map to capabilities, profiles, tests, or explicit non-goals. |
| `capability-ledger-schema` | Compliance is machine-readable; prose alone is insufficient. |

### Core Minimum Files

A Core install SHOULD include:

- `CLOSEOUT-HISTORICAL-INCIDENT-MAP.md`
- `CLOSEOUT-ADJUDICATION-PROTOCOL.md`
- `CLOSEOUT-FRAMEWORK-PROFILES.md`
- `CLOSEOUT-CAPABILITY-LEDGER.json`
- `CLOSEOUT-CAPABILITY-LEDGER.schema.json`
- repo-owned closeout config;
- repo-owned closeout actors or adapters;
- contract/drift verifier;
- behavioral tests for Core safety claims.

When doing cross-repo design, include:

- `CLOSEOUT-CROSS-MAP-COMPARISON.md`
- `CLOSEOUT-REFERENCE-MATRIX.md`

For a single repo adopting the framework without cross-repo comparison, the
cross-map and reference matrix may be omitted, but the capability ledger MUST
still expose local status and evidence.

### Core Minimum Commands

Command names are repo-specific, but Core needs commands or adapters for:

- start/register work block;
- read work block manifest and dirty baseline;
- classify dirty state;
- create candidate evidence packet;
- create or ingest adjudication report;
- validate exact mutation tuple;
- perform repo-owned symbolic mutation;
- verify tooling drift;
- verify hard-clean final postcondition.

### Core Minimum Tests

Core tests SHOULD prove:

- ambiguous mutation blocks without adjudication;
- reviewer output cannot mutate directly;
- stale tuple blocks;
- foreign dirty is preserved;
- baseline-dirty overlap blocks whole-file checkpoint;
- generated/exempt final utility writes do not become source work;
- advisory hooks are non-authoritative;
- response/final hooks do not create managed worktrees;
- hard-clean gate blocks dirty, stash, worktree, branch, queue, and runtime state according to policy;
- stale tooling emits drift, not authoritative blockers;
- broad read-only planning does not imply broad mutation;
- raw broad mutation requires explicit audited override.

## Standard Profile

Standard is for repos with recurring closeout, multiple agents, retained
candidates, or frequent branch/worktree cleanup.

Standard answers:

> Can this repo automate common closeout remediation safely without requiring a human for every retained or high-impact case?

### Standard SHOULD Capabilities

| Capability | Standard Requirement |
|---|---|
| `target-push-race-recovery` | Fetch, re-pin, rebuild/retry target races or emit durable rerun blocker; never force-push. |
| `protected-target-noop` | Clean protected target can close as no-op without synthetic work block; dirty target blocks. |
| `retained-candidate-remediation` | Retained candidates are actively investigated/remediated before terminal retain. |
| `surface-unavailable-or-insufficient-reviewer-block` | Missing required review writes durable blocker and recovery path. |
| `checked-out-and-locked-worktree-handling` | Clean stale worktrees may be planned safely; active, dirty, or protected ambiguity blocks. |
| `independent-review-quorum` | High-impact ambiguity receives independent review or blocks/escalates. |
| `git-hook-gates` | Git hooks protect commits/pushes and feed authoritative closeout gates. |

### Standard Minimum Tests

Standard tests SHOULD prove:

- protected target no-op succeeds only when repo-closed passes;
- target push race never force-pushes and either retries safely or blocks durably;
- retained remediation processes one candidate per mutating run;
- unavailable review surface blocks with durable artifact;
- high-impact reviewer disagreement blocks, preserves, or escalates;
- checked-out clean stale worktree cleanup revalidates immediately before mutation;
- dirty, active, or protected worktree cleanup blocks with exact evidence.

## Max / Conditional Profile

Max is for repos with heavy closeout scar tissue: many worktrees, stale branches,
runtime services, agent queues, dirty split workflows, or historical prune
needs.

Max answers:

> Can this repo autonomously clean complicated historical, retained, runtime, and dirty-state messes while preserving evidence and recovery paths?

### Max SHOULD Capabilities

| Capability | Max Requirement |
|---|---|
| `automated-subagent-dispatch` | Dispatch reviewers where the surface supports it. |
| `agent-remediation-queue` | Queue, shard, dispatch, collect, and validate agent remediation packets. |
| `evidence-preserving-prune` | Preserve recovery evidence before historical, non-ancestor, or dirty destructive cleanup. |
| `remediation-freeze` | Durable freeze blocks risky remediation and records scope/recovery. |
| `dirty-split-automation` | Split or preserve dirty work through exact path tuples and recovery branches/artifacts. |
| `runtime-service-lifecycle` | Stop/restart repo-owned runtime services around promotion and repo-closed verification. |
| `remote-feature-clean-integration` | Clean-integrate or remediate remote feature refs with validation and pinned refs. |

### Conditional Requirements

Some Max rows become effectively required when the repo owns the relevant risk:

- If repo-owned runtime services exist, `runtime-service-lifecycle` SHOULD be installed.
- If destructive historical prune is allowed, `evidence-preserving-prune` SHOULD be installed.
- If automated remediation can make state worse, `remediation-freeze` SHOULD be installed.
- If remote feature cleanup is part of closeout, `remote-feature-clean-integration` SHOULD be installed.

### Max Minimum Tests

Max tests SHOULD prove:

- evidence-preserving prune writes recovery evidence before deletion;
- dirty detached worktree cleanup preserves exact dirty bytes or blocks;
- remediation freeze prevents mutation and emits recovery/unfreeze path;
- agent queues reject out-of-scope writes;
- subagent unavailability emits durable status rather than skipping adjudication;
- runtime services stop before promotion and restart only after clean promotion and repo-closed verification;
- remote feature clean integration validates, pushes only after re-pin, and deletes remote refs only after target update succeeds.

## Surface Plugin Profile

Surface Plugins adapt the Core adjudication protocol to a runtime surface.

Surface Plugins answer:

> How does this environment produce or consume the same packets, reports, statuses, and audits?

### Initial Surface Plugins

| Surface | Role |
|---|---|
| Codex Desktop | Primary agent, optional subagents, local tool execution, thread-aware reporting. |
| Codex CLI | Primary agent, shell workflow, broker/closeout actor execution. |
| Claude Code | Primary agent, optional subagents, project hook integration. |
| Claude Desktop | Command/manual bridge; must not assume Stop-hook magic. |
| CI policy reviewer | Deterministic or policy review of packet/report/tuple evidence. |
| Human/manual bridge | Human review writes or approves the same adjudication report format. |
| Deterministic reviewer | Mechanical proof for machine-verifiable actions. |

### Surface Plugin Rules

Surface Plugins MUST NOT weaken Core.

They MAY:

- spawn subagents;
- dispatch review packets;
- collect reviewer results;
- provide UI/reporting;
- bridge human/manual decisions;
- run deterministic proof checks.

They MUST:

- preserve packet/report hashes;
- record declared review surface;
- avoid direct mutation unless declared as repo-owned actor;
- emit unavailable/insufficient-review artifacts when required review cannot run;
- feed authoritative repo-owned gates instead of replacing them.

## Profile Selection Guidance

Choose Core when:

- the repo needs portable safety and honest completion;
- closeout is mostly local and low-volume;
- human or primary-agent adjudication is acceptable for ambiguity.

Choose Standard when:

- multiple agents or recurring closeout are common;
- retained candidates and target races are expected;
- protected target runs, worktrees, or hooks are common enough to automate.

Choose Max when:

- many stale branches, worktrees, or remote refs exist;
- runtime services execute repo code;
- dirty split, evidence-preserving prune, remediation freeze, or agent queues are needed;
- autonomous cleanup is expected to handle high-volume scar tissue.

Add Surface Plugins when:

- Codex, Claude, CI, deterministic tools, or manual workflows need adapters;
- the repo wants subagent acceleration without making subagents a Core dependency.

## Adoption Rule

A repo may adopt any profile honestly, but it MUST report unsupported rows as
`PARTIAL`, `NO`, or `UNAVAILABLE` in the capability ledger.

Unsupported Max features are acceptable in Core or Standard installs.
Unsupported Core safety invariants are blockers, not preferences.

## Next Artifact Input

This profile split should feed `CLOSEOUT-CAPABILITY-LEDGER.schema.json`.

The ledger schema should encode:

- `capabilityId`
- `status`
- `profile`
- `decision`
- `evidencePaths`
- `testPaths`
- `blockers`
- `smallestNextStep`
- `repoSpecificNotes`
- whether `YES` requires executable behavior, tests, or an explicit scoped exception.
