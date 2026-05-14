# Closeout Standard

Portable standard for repo-owned brokered closeout.

This document is distilled from:

- `CLOSEOUT-HISTORICAL-INCIDENT-MAP.md`
- `CLOSEOUT-CROSS-MAP-COMPARISON.md`
- `CLOSEOUT-ADJUDICATION-PROTOCOL.md`
- `CLOSEOUT-REFERENCE-MATRIX.md`
- `CLOSEOUT-FRAMEWORK-PROFILES.md`
- `CLOSEOUT-CAPABILITY-LEDGER.schema.json`
- `CLOSEOUT-REQUIREMENTS-TRACE.md`

This is the clean normative standard. It does not reprint every incident or prescribe one repo's implementation language, shell, tool layout, agent runtime, or worktree strategy.

## Normative Language

`MUST`, `MUST NOT`, `SHOULD`, `SHOULD NOT`, and `MAY` have their ordinary standards meaning:

- `MUST` is required for the selected profile.
- `SHOULD` is expected unless the repo records a reason, blocker, scoped exception, or lower profile selection.
- `MAY` is optional and policy-dependent.

Implementation is proven by repo-owned artifacts and the capability ledger. Prose alone is not implementation proof.

## First Rule

> A mechanically safe action may proceed by deterministic review. An ambiguous action must have structured adjudication. In both cases, mutation remains repo-owned, symbolic, exact-tuple, validated, and audited.

## Scope

This standard governs closeout of work blocks, retained candidates, branch/worktree cleanup, dirty-state remediation, final response readiness, and related repo-owned automation.

It covers:

- evidence collection
- work ownership and dirty baseline handling
- deterministic versus adjudicated decisions
- review surfaces
- packet, report, tuple, audit, and recovery contracts
- repo-owned mutation authority
- final completion postconditions
- profile selection and ledger-backed compliance

It does not require:

- subagents
- PowerShell
- Git worktrees
- a specific CI system
- a specific shell or programming language
- Max-profile remediation machinery in every repo

## Profile Model

Repos adopt one or more framework layers:

| Profile | Purpose |
|---|---|
| Core | Portable safety invariants every repo can implement. |
| Standard | High-value automation for serious multi-agent or recurring closeout workflows. |
| Max / Conditional | Heavy machinery for worktrees, runtime services, retained candidates, historical prune, dirty split, remediation freeze, and remote feature cleanup. |
| Surface Plugins | Adapters for Codex, Claude, CI, deterministic review, and manual review. |

Max-profile machinery MUST NOT be smuggled into Core merely because it was useful in one repo. Core is for universal data-loss and false-completion prevention.

## Core Requirements

### Governance And Source Evidence

A repo adopting the framework MUST preserve source evidence for closeout rules.

Core repos MUST provide:

- historical incident traceability, or a local equivalent for new adoptions
- requirements trace from prior standards/addenda into capabilities, profiles, tests, or non-goals
- machine-readable capability ledger schema
- capability ledger entries for local claims

Incoming closeout addenda or closeout rules MUST become durable policy, config, test, blocker, roadmap item, or explicit non-goal when feasible. They MUST NOT remain chat-only instructions when they alter repo-wide closeout behavior. Repos SHOULD keep their auto-closeout docs, canonical dashboard spec, and a short round-delta note synchronized in the same work block so cross-repo comparison stays reproducible. The round-delta note SHOULD live in a tracked durable note such as `CLOSEOUT-CROSS-MAP-COMPARISON.md`, and the canonical dashboard spec SHOULD be machine-checked rather than merely referenced. Documentation freshness is operational: the canonical spec and tracked round-delta note SHOULD be regenerated together after workflow changes, and the durable note SHOULD carry a visible freshness marker or timestamp so stale comparison docs are obvious. Stale review is not terminal when policy permits renewal; if the tuple remains eligible, finalize SHOULD renew the exact review tuple and rerun rather than treating stale review as a hard terminal blocker.

### Capability Ledger

Compliance MUST be machine-readable.

The capability ledger MUST record, for each capability:

- capability id
- title
- status
- profile
- framework decision
- verification basis
- evidence paths
- test, actor, adapter, config, contract, drift-check, and audit paths where applicable
- blockers
- smallest next step
- repo-specific notes
- last verification time

Allowed status values are:

- `YES`
- `PARTIAL`
- `NO`
- `UNAVAILABLE`
- `UNKNOWN`

A `YES` row MUST NOT be based on documentation-only, reported-only, not-implemented, or unknown evidence. A `YES` row MUST cite executable behavior, behavioral tests, contract or drift checks, audit artifacts, or an explicit scoped exception.

`PARTIAL`, `NO`, `UNAVAILABLE`, and `UNKNOWN` rows MUST include blockers and a smallest next step. `UNAVAILABLE` rows MUST include an unavailable reason.

### Work Block Ownership

Closeout MUST use repo-owned facts to identify the work being closed.

Core repos MUST support:

- work block identity
- target branch or target ref
- start head
- dirty baseline
- path claims, when available
- deterministic work-block selection or a durable blocker

Finalize MUST select a unique work block or block. Branch-only inference is not sufficient unless the repo proves the branch uniquely identifies the work block and tuple.

### Dirty State Classification

Dirty state MUST be classified before repair, checkpoint, integration, prune, cleanup, or final response.

Core dirty classes MUST distinguish at least:

- owned dirty
- foreign dirty
- unowned dirty
- mixed dirty
- generated or exempt dirty

Foreign dirty work MUST NOT be committed, stashed, reset, deleted, or silently absorbed by another work block. It may be retained and audited when policy allows.

Paths dirty at the broker baseline MUST NOT be whole-file auto-claimed by the current work block merely because the current work later claims or edits them. Baseline-dirty overlaps MUST block, split, or require stronger ownership proof.

Generated or exempt artifacts MUST NOT be misclassified as owned source work.

### Mechanically Safe Actions

A mechanically safe action MAY proceed by deterministic review when all required facts are machine-verifiable, current, and policy-authorized.

Examples include:

- exact tuple equality
- stale tuple rejection
- clean repo-closed status check
- stale tooling detection
- generated/exempt path classification from declared config
- no-force-push target race classification
- already-ancestor or tree-equal proof when no dirty, protected, locked, or manual-only state is involved

The deterministic reviewer MUST use repo-owned facts and MUST produce durable status or audit evidence when the action is high-impact.

### Ambiguous Actions

An ambiguous action MUST have structured adjudication before mutation or terminal retain.

Ambiguous actions include:

- conflict resolution
- historical or non-ancestor branch deletion
- dirty detached worktree cleanup
- retained candidate terminal classification
- broad mutation override
- protected, locked, active, or dirty worktree cleanup
- mixed dirty ownership
- deciding whether a prior rule becomes Core, Standard, Max, repo policy, or non-goal

Structured adjudication requires a candidate evidence packet and adjudication report.

### Declared Review Surfaces

Every adjudication report MUST name its declared review surface.

Initial portable review surfaces are:

- `codex-primary-agent`
- `codex-subagent`
- `claude-primary-agent`
- `claude-subagent`
- `human-reviewer`
- `ci-policy-reviewer`
- `deterministic-reviewer`

Repos MAY add surfaces by policy. Added surfaces MUST define identity, authority, scope, whether they can satisfy independent review, and whether they can adjudicate ambiguity.

Subagents are optional transport. If a required review surface is unavailable and no alternate declared surface can satisfy the requirement, closeout MUST write durable unavailable or insufficient-review evidence and block.

### Candidate Evidence Packets

Every ambiguous or high-impact candidate MUST have a durable evidence packet.

Minimum packet fields SHOULD include:

- schema version
- candidate id
- scenario kind
- repo
- work block id when applicable
- created time and creator
- declared policy profile
- refs and pins
- target and candidate refs when applicable
- merge base when applicable
- dirty baseline when applicable
- path claims when applicable
- dirty classification when applicable
- generated or exempt paths
- conflict paths when applicable
- validation commands
- policy hash
- evidence hash
- allowed symbolic actions
- manual-only reason when applicable
- recovery command
- source artifacts

Scenario-specific packets SHOULD add hashes, modes, object ids, lock evidence, sensitive path findings, queue shard scope, target-race pins, or recovery bundle references when relevant.

### Adjudication Reports

Every ambiguous candidate MUST have a durable adjudication report before mutation or terminal retain.

Minimum report fields SHOULD include:

- schema version
- report id
- candidate id
- candidate packet hash
- reviewer identity
- declared review surface
- reviewed time
- evidence paths reviewed
- recommended symbolic action
- confidence or score
- blockers or explicit absence of blockers
- exact tuple fields
- independent review requirement and satisfaction
- unavailable or insufficient-review reason when applicable
- audit artifact path
- recovery command

Valid report outcomes SHOULD include:

- `approve_symbolic_action`
- `request_more_evidence`
- `block_ambiguous`
- `block_policy`
- `block_surface_unavailable`
- `block_insufficient_review`
- `preserve_and_retain`
- `escalate_to_human`

Omitting blockers is not approval. If no blockers exist, the report MUST say so explicitly.

### Symbolic Recommendation Boundary

Reviewers MAY recommend symbolic actions. Reviewers MUST NOT provide arbitrary shell mutation as the authority for mutation.

Symbolic action catalogs are repo policy. The portable requirement is that each implemented symbolic action maps to:

- policy entry
- exact tuple fields
- repo-owned actor
- pre-apply revalidation
- audit event
- recovery behavior when relevant
- behavioral test or scoped exception

### Exact Mutation Tuple

Before high-impact mutation, the repo MUST construct and validate an exact tuple.

Minimum tuple fields SHOULD include:

- candidate id
- action id or symbolic action
- candidate packet hash
- adjudication report hash when required
- evidence hash
- policy hash
- pinned refs
- dirty state hash when relevant
- path set hash when relevant
- manifest hash when relevant
- review manifest hash when independent review is required
- recovery artifact hash when destructive cleanup or preservation is relevant
- validation command hash

If refs, policy, evidence, dirty state, path scope, recovery artifacts, or validation requirements drift, the tuple is stale. Stale tuples MUST NOT mutate.

### Repo-Owned Mutation Authority

Only repo-owned actors MAY mutate source, refs, worktrees, stashes, generated closeout state, or runtime lifecycle state.

Repo-owned mutating actors MUST:

- load repo policy/config
- validate packet and report requirements
- validate tuple freshness immediately before mutation
- refuse stale refs or stale evidence
- refuse foreign dirty mutation
- honor protected branch and protected worktree policy
- run required validation
- write success, blocked, retained, stale, or failure audit artifacts
- preserve recovery evidence before destructive cleanup when required

Agents, subagents, humans, dashboards, hooks, and CI may request or approve symbolic actions. They are not mutation authority unless the repo declares them as repo-owned actors with contract checks and tests.

### Bounded Process Authority

Closeout child actors MUST run through bounded process boundaries when they execute subprocesses.

The bounded runner MUST account for:

- wall-clock timeout
- output caps
- process-tree termination
- failure text normalization
- descendant process handling
- expected success or blocker artifacts
- durable audit for timeout, output cap, killed process tree, and normalized failure

Bounded-runner infrastructure failures MUST use the shared exit-code taxonomy:
timeout=124, output cap=125, and CPU stall=126. These codes describe the
runner boundary failure, not the child tool's own domain failure.

A closeout result is authoritative only after the child process exits, descendants are gone or intentionally retained with audit, status and failure text agree, and expected artifacts exist.

Closeout surface adapters that launch a bounded child SHOULD emit a configurable liveness heartbeat to stderr while the child is still running. Heartbeats MUST NOT be written to machine-readable stdout and MUST NOT replace bounded-runner timeout, CPU-stall, output-cap, or postcondition authority.

For finalizer children, `exit 0` plus machine-readable `status: success` and expected success artifacts is semantic success authority. Validation stdout/stderr embedded in that success payload are evidence, not blocker authority; configured known-failure vocabulary in that evidence must be recorded as ignored text rather than promoted to a failure. Children that do not return trusted success JSON still fail closed on known-failure text.

### Tooling Drift

Before treating closeout blockers or success as authoritative, the repo MUST verify that required closeout tooling is current enough for the selected profile.

Missing actors, config fields, contract checks, repair paths, adapter paths, required tests, or baseline symbols MUST be reported as tooling drift rather than as authoritative closeout blockers or success.

### Hard-Clean Final Gate

Integration success is not final completion.

The user-visible completion postcondition is:

`repo_closed_for_final_response`

The hard-clean final gate MUST run after repo-modifying work and MUST be authoritative over:

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

Repo closeout reporting MUST derive its final clean/blocked wording from the same postcondition. The canonical summary field is `repoClosedPostcondition.closeoutCleanTruth`, which MUST show raw Git status, policy-clean status, and cleanup-clean status together when those signals differ.

The repo-closed postcondition MUST inspect linked Git worktrees from repo-owned evidence such as `git worktree list --porcelain`. A failed or empty inspection, an inspection that omits the current repo root, or an ordinary linked sibling worktree MUST block hard-clean closeout. Protected load-bearing worktrees MAY be inspect-only when policy names their roots, but they still MUST be visible in repo-state evidence.

If the repo is not closed, the agent may report WIP or a blocker, but MUST NOT claim final closeout completion. The human-facing completion wrapper finalizes by default, so ordinary substantive replies should flow through `work-block-complete.ps1 -Finalize` unless a deliberate blocker or dry-run is recorded.

### Repo State, Dashboard, And Rollback

Repos SHOULD expose a dashboard-ready repo state feed through `repoStateLedger`. The feed MUST live under generated state such as `.claude-state/closeout/repo-state/`, SHOULD have a stable latest snapshot plus timestamped history, SHOULD declare a schema such as `repo-state-snapshot.v1`, and SHOULD include branch/tracking, dirty entries, local branches, worktrees, `worktree-inspection.v1`, stashes, closeout audit pointers, latest `closeoutCleanTruth`, a bounded `closeout-history-index.v1`, and rollback policy/readiness. Live dashboard refresh SHOULD have a repo-owned latest-only command that updates the stable feed without appending history or audit rows on every poll. Unsupported configured refresh commands MUST fail closed rather than becoming dashboard-exposed command surfaces.

Future web dashboards SHOULD follow `webDashboardSpec`: sticky local URL, SSE with polling fallback, read-only by default, preserved client scroll/focus/selection/expanded/history-filter state across refreshes, historical closeout browsing, repo-map/workflow/blocker/action-preview/action-request-history/audit/rollback views, and no separate mutation authority. The dashboard flow SHOULD be read-first: `Inspect` evidence, `Preview` consequences, `Request` symbolic intent, and `Apply` only through repo-owned actors outside the dashboard authority. Read-only preview and dry-run explanations are allowed when they are derived from repo-owned truth and do not become a second mutation surface. `latest.json` is a display feed only, not rollback evidence. Dashboard actions remain symbolic requests until repo-owned actors revalidate exact tuples. See `docs/19-closeout-dashboard-spec.md` for the canonical phase matrix and endpoint details. A local helper such as `tools/closeout/start-closeout-dashboard.ps1` SHOULD reuse healthy same-repo servers, fail closed for foreign or unknown port owners, and expose `/api/closeout/actions` with `serverProcessId`, repo ownership, symbolic actions, command policy, exact-tuple requirements, and rollback non-actionability/reason, plus `/api/closeout/actions/preview` for non-mutating cleanup or rollback explanations. Data endpoints SHOULD include `/api/closeout/repo-state/latest`, `/api/closeout/repo-state/history-index`, `/api/closeout/repo-state/history/{snapshotId}`, and `/api/closeout/actions/requests`.

Dashboard symbolic action intent MAY be recorded as generated request packets under a path such as `.claude-state/closeout/dashboard-action-requests/`, for example through `/api/closeout/actions/request`. Such packets are evidence, not authority: they MUST preserve helper freshness, repo-state hash, actionability/read-only reason, and non-empty exact tuple fields; MUST reject missing/stale/future helper timestamps, mismatched helper process ids, and request roots that resolve outside generated state; and MUST NOT directly mutate source, refs, worktrees, or stashes.

Repos SHOULD publish `rollbackPolicy`. Rollback SHOULD prefer `git revert`, recovery branches, path restore from snapshots, preserved prune bundles, dirty-split preservation refs, and audited recovery commands. Rollback readiness SHOULD declare `rollback-readiness.v1`, stay fail-closed/read-only until a repo-owned actor validates immutable source snapshot evidence and a `closeout-rollback-manifest.v1`, and run mutations in a new work block with explicit user approval, a plan, and a pre-mutation state snapshot. A read-only manifest validator such as `tools/closeout/validate-rollback-manifest.ps1` SHOULD return `closeout-rollback-manifest-validation.v1`, reject display feeds such as `latest.json`/`current.json`, require manifests under generated rollback state, bind `sourceSnapshotHash` to the documented repo-state snapshot hash scope, require explicit `sourceSnapshotAuditHash` and `repoClosedAuditHash`, integrity-check audit hashes and sidecar JSON, require matching `repo_state_snapshot` audit evidence, and require the source snapshot and repo-closed audit to share `workBlockId`. Dashboard rollback exact tuples SHOULD include every manifest-binding field, including `sourceSnapshotPath` and `recoveryCommand`. Rollback itself remains a mutating action once a repo-owned actor is available and the user has approved the plan. Blind destructive undo such as `reset --hard` or force push MUST require explicit user request and must not be the default rollback strategy.

### Hooks And Final Utilities

Response, final, advisory, and bridge hooks MUST NOT replace authoritative repo gates.

They MAY write status, reminders, metrics, handoff files, audit packets, or other generated/exempt evidence when policy allows.

They MUST NOT:

- create or resurrect managed worktrees unless explicitly acting as a repo-owned lifecycle actor
- bypass hard-clean verification
- mutate source or refs without repo-owned actor authority
- turn generated closeout artifacts into owned source work

Final utility artifacts MUST either be written before hard-clean verification or only to declared generated/exempt paths with provenance.

### Broad Planning And Single-Candidate Mutation

Read-only planning MAY inspect many candidates.

Mutation is single-candidate by default.

Mutating sweep actors MUST require one of:

- explicit candidate id
- promoted single-candidate remediation packet
- explicit audited bulk override

Bulk mutation is high-impact. It MUST include:

- explicit user or policy-enabled override
- configured permission
- candidate list
- reviewer approval when required
- audit reason
- per-candidate exact tuple
- serialized mutation
- recovery evidence

Raw broad mutation without candidate id or audited bulk override is forbidden.

### Target Push Races

A target push non-fast-forward is a recoverable race, not permission to force-push.

Core repos MUST NOT automatically force-push the target as a target-race recovery mechanism.

Standard repos SHOULD fetch, re-pin, rebuild or retry when policy allows, or emit a durable rerun blocker with attempted head, fetched target head, local target head, and recovery commands.

### Audit And Recovery

Every high-impact closeout outcome MUST be inspectable from repo-owned artifacts.

Required artifact classes include:

- candidate evidence packet
- adjudication report when required
- unavailable or insufficient-review blocker when required
- exact tuple decision
- mutation audit
- stale tuple audit
- validation failure audit
- recovery command

Destructive cleanup MUST preserve recovery evidence before deletion when the candidate is historical, non-ancestor, dirty, protected, ambiguous, or policy requires preservation.

## Standard Profile Requirements

Standard repos SHOULD implement automation for recurring or multi-agent closeout.

Standard capabilities include:

- target push race retry or durable rerun blocker
- clean protected target no-op
- retained candidate remediation
- unavailable or insufficient-review blocker artifacts
- checked-out and locked worktree handling
- independent review or quorum for high-impact ambiguity
- Git hook gates that feed authoritative blockers

Standard automation MUST still obey Core. It cannot weaken adjudication, tuple validation, dirty preservation, repo-owned mutation, or final gate requirements.

## Max And Conditional Requirements

Max capabilities are for repos with heavy closeout scar tissue.

Max capabilities include:

- automated subagent dispatch
- agent remediation queues
- evidence-preserving prune
- remediation freeze
- dirty split automation
- runtime service lifecycle
- remote feature clean integration

Some Max capabilities become effectively required when the repo owns the corresponding risk:

- If repo-owned runtime services execute repo code, runtime lifecycle SHOULD be installed.
- If destructive historical prune is allowed, evidence-preserving prune SHOULD be installed.
- If automated remediation can worsen state, remediation freeze SHOULD be installed.
- If remote feature cleanup is part of closeout, remote feature clean integration SHOULD be installed.

Unsupported Max capabilities are acceptable in Core or Standard installs when the repo does not own the risk. Unsupported Core safety invariants are blockers.

## Surface Plugins

Surface Plugins adapt the Core adjudication contract to a runtime surface.

Surface Plugins MAY:

- spawn subagents
- dispatch review packets
- collect reviewer results
- provide UI or reporting
- bridge human/manual decisions
- run deterministic proof checks

Surface Plugins MUST:

- preserve packet/report hashes
- record declared review surface
- avoid direct mutation unless declared as repo-owned actor
- emit unavailable or insufficient-review artifacts when required review cannot run
- feed authoritative repo-owned gates instead of replacing them
- avoid weakening Core

## Non-Goals

This standard does not:

- require subagents
- require PowerShell
- require Git worktrees
- require a specific CI provider
- require Max-profile machinery for minimal Core adoption
- define every repo's symbolic action catalog
- treat documentation-only claims as compliance
- authorize reviewers, dashboards, hooks, or chat responses to bypass repo-owned mutation actors
- replace repo-specific policy where the policy is stricter

## Adoption And Conformance

A repo may adopt Core, Standard, Max, Conditional, and Surface Plugin layers honestly.

To claim Core conformance, a repo MUST:

- implement or explicitly block all Core safety invariants
- publish a capability ledger compatible with the schema
- provide evidence paths for `YES` claims
- report unsupported or incomplete rows as `PARTIAL`, `NO`, `UNAVAILABLE`, or `UNKNOWN`
- preserve foreign dirty work
- require structured adjudication for ambiguity
- restrict mutation to repo-owned actors
- enforce exact tuple validation for high-impact mutation
- prove or block `repo_closed_for_final_response`

To claim Standard or Max conformance, a repo MUST keep Core conformance and populate the corresponding ledger rows with local evidence, blockers, or scoped exceptions.

The capability ledger, not this prose file, is the authoritative machine-readable record of what a specific repo actually supports.

## Next Artifact Input

This standard should feed:

`CLOSEOUT-IMPLEMENTATION-PROMPT.md`

The implementation prompt should translate this standard into a practical adoption checklist for a target repo without weakening the profile boundaries or evidence requirements.
