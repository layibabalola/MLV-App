# Closeout Adjudication Protocol

Draft portable judgment contract for repo-owned brokered closeout.

This protocol is drafted from `CLOSEOUT-HISTORICAL-INCIDENT-MAP.md` and
`CLOSEOUT-CROSS-MAP-COMPARISON.md`. It defines the judgment layer only. It is
not the final closeout standard, not the reference matrix, and not the install
prompt.

## Purpose

Closeout automation can collect evidence, classify state, validate refs, enforce
exact tuples, and perform safe repo-owned mutation. It cannot always decide
intent for ambiguous merge, prune, retain, dirty, conflict, or historical
cleanup cases.

This protocol makes those decisions portable:

- the repo produces evidence packets,
- a declared review surface produces adjudication reports,
- reviewers recommend symbolic actions only,
- repo-owned actors revalidate and mutate,
- every result is audited and recoverable.

## Framework Center

> Structured adjudication is mandatory. Subagents are optional transport. Repo-owned actors are the only mutation authority.

First normative rule:

> A mechanically safe action may proceed by deterministic review. An ambiguous action must have structured adjudication. In both cases, mutation remains repo-owned, symbolic, exact-tuple, validated, and audited.

## Mechanically Safe Versus Ambiguous Actions

A **mechanically safe action** is one whose eligibility can be proven entirely
from repo-owned facts and policy. Examples:

- target contains branch head by ancestry;
- tree equality or patch equivalence is proven and policy permits prune;
- dirty path was clean at broker baseline and is exactly owned;
- protected target is clean and repo-closed postcondition passes;
- target push failed with a known non-fast-forward race and policy permits a
  fetch/re-pin/rerun blocker.

An **ambiguous action** is any action where intent, ownership, historical value,
semantic conflict resolution, or preservation sufficiency is not mechanically
provable. Examples:

- merge conflicts;
- historical or non-ancestor branch deletion;
- dirty detached worktree removal;
- protected or stale locked worktree cleanup;
- broad sweep mutation override;
- retained candidate terminal classification;
- sensitive path preservation;
- remediation freeze removal.

Mechanically safe actions may use a deterministic reviewer. Ambiguous actions
must produce a candidate evidence packet and at least one adjudication report
before mutation.

## Declared Review Surfaces

Every adjudication report MUST name a declared review surface. Initial portable
values:

- `codex-primary-agent`
- `codex-subagent`
- `claude-primary-agent`
- `claude-subagent`
- `human-reviewer`
- `ci-policy-reviewer`
- `deterministic-reviewer`

Repos MAY add policy-specific surfaces if they define identity, scope,
capabilities, and audit behavior.

Subagents are useful but not required. A repo with no subagent transport can
still comply by producing the same evidence packet and adjudication report from
a primary agent, human reviewer, CI reviewer, or deterministic reviewer when the
action is mechanically safe.

## Candidate Evidence Packet

A candidate evidence packet is the durable input to adjudication. It SHOULD be
JSON or another machine-readable format. It MUST be stored under a repo-owned
generated state root.

Minimum fields:

- `schemaVersion`
- `candidateId`
- `scenarioKind`
- `repo`
- `workBlockId`, when applicable
- `createdAt`
- `createdBy`
- `declaredPolicyProfile`
- `policyHash`
- `evidenceHash`
- `refsAndPins`
- `targetRef`
- `candidateRef`, when applicable
- `mergeBase`, when applicable
- `dirtyBaseline`, when applicable
- `pathClaims`, when applicable
- `dirtyClassification`, when applicable
- `generatedOrExemptPaths`
- `conflictPaths`, when applicable
- `validationCommands`
- `sensitivePathFindings`
- `recoveryCommand`
- `allowedSymbolicActions`
- `manualOnlyReason`, when applicable
- `sourceArtifacts`

Scenario-specific packets MAY add fields, but mutation eligibility MUST be based
only on fields included in the exact mutation tuple or revalidated by repo-owned
actors immediately before mutation.

## Adjudication Report

An adjudication report records a reviewer decision. It MUST be durable and tied
to a candidate packet hash.

Minimum fields:

- `schemaVersion`
- `reportId`
- `candidateId`
- `candidatePacketHash`
- `reviewerIdentity`
- `declaredReviewSurface`
- `reviewedAt`
- `evidencePathsReviewed`
- `recommendedSymbolicAction`
- `confidence`
- `blockers`
- `blockersAbsent`
- `exactTupleFields`
- `independentReviewRequired`
- `independentReviewSatisfied`
- `surfaceUnavailableReason`, when applicable
- `insufficientReviewReason`, when applicable
- `auditArtifactPath`
- `recoveryCommand`

Valid report outcomes:

- `approve_symbolic_action`
- `request_more_evidence`
- `block_ambiguous`
- `block_policy`
- `block_surface_unavailable`
- `block_insufficient_review`
- `preserve_and_retain`
- `escalate_to_human`

`blockersAbsent` MUST be explicit when no blockers are found. Silent omission is
not approval.

## Independent Review Requirements

Repos MUST declare which action classes require independent review. At minimum,
independent review SHOULD be required for high-impact ambiguous actions:

- non-ancestor or historical branch deletion;
- dirty detached worktree removal;
- protected locked worktree cleanup;
- conflict resolution;
- broad mutation override;
- remediation freeze removal;
- sensitive path preservation.

Independent review can be satisfied by subagents, primary agents, humans, CI
policy reviewers, or deterministic reviewers when the action is mechanically
safe. If independent review is required and cannot be obtained, mutation MUST
block with an insufficient-review artifact.

## Surface Unavailable / Insufficient Review States

When a required review surface is not available, the repo MUST write a durable
status artifact instead of silently downgrading the requirement.

Required statuses:

- `review_surface_unavailable`
- `independent_review_unavailable`
- `insufficient_review_quorum`
- `review_tuple_stale`
- `review_blocked`
- `agent_remediation_surface_unavailable`

Each status artifact MUST include:

- `candidateId`
- `requiredReview`
- `availableSurfaces`
- `missingSurfaces`
- `attemptedSurface`
- `reason`
- `recoveryCommand`
- `packetPath`
- `candidatePacketHash`
- `policyHash`
- `evidenceHash`
- `currentRefs`
- `dirtyStateSummary`

Unavailable subagent dispatch does not make adjudication optional. It only means
the repo must route to another declared review surface or block.

## Symbolic Recommendation Boundary

Reviewers MAY recommend symbolic actions. Reviewers MUST NOT perform arbitrary
mutation as part of adjudication.

Initial symbolic action classes:

- `checkpoint-owned-dirty`
- `split-owned-dirty`
- `clean-integrate`
- `prune-integrated-ref`
- `prune-patch-equivalent-ref`
- `preserve-dirty-detached`
- `resolve-conflicts-with-agent`
- `retain-with-blocker`
- `protected-target-noop`
- `target-race-rerun`
- `freeze-remediation`
- `remove-remediation-freeze`
- `bulk-mutation-override`

Repos MAY add symbolic actions if they define policy, tuple fields, validators,
actors, audit events, and tests.

## Exact Mutation Tuple

Every mutation MUST be approved and revalidated against an exact tuple. Minimum
tuple fields:

- `candidateId`
- `actionId`
- `symbolicAction`
- `candidatePacketHash`
- `adjudicationReportHash`
- `policyHash`
- `evidenceHash`
- `targetRef`
- `targetHead`
- `candidateRef`, when applicable
- `candidateHead`, when applicable
- `mergeBase`, when applicable
- `dirtyClassificationHash`, when applicable
- `pathSetHash`, when applicable
- `recoveryArtifactHash`, when required
- `validationCommandHash`

If refs, dirty state, policy, evidence, recovery artifacts, or validation
requirements drift, the tuple is stale. Stale tuples MUST NOT mutate. They may be
renewed only through configured review renewal and immediate revalidation.

## Repo-Owned Mutation Authority

Only repo-owned actors may mutate source, refs, worktrees, stashes, generated
closeout state, or runtime lifecycle state.

Repo-owned actors MUST:

- run through bounded process boundaries where applicable;
- revalidate the exact tuple immediately before mutation;
- refuse stale refs or stale evidence;
- refuse foreign dirty mutation;
- honor protected branch and protected worktree policy;
- write audit artifacts for success, block, retain, partial recovery, and
  failure;
- emit recovery commands for blockers.

Agents, subagents, humans, dashboards, hooks, and CI jobs may request or approve
symbolic actions. They are not mutation authority unless the repo has declared
them as repo-owned actors with contract checks and tests.

## Audit And Recovery Artifacts

Every adjudicated outcome MUST preserve enough evidence to inspect or recover
the decision.

Minimum audit fields:

- `candidateId`
- `symbolicAction`
- `tuple`
- `packetPath`
- `adjudicationReportPaths`
- `actor`
- `startedAt`
- `completedAt`
- `status`
- `blockerKind`, when applicable
- `refsBefore`
- `refsAfter`, when applicable
- `pathsMutated`
- `validationResult`
- `recoveryCommand`
- `recoveryArtifactPath`, when applicable

Destructive cleanup requires recovery evidence before deletion when the candidate
is historical, non-ancestor, dirty, protected, or otherwise ambiguous.

## Broad Planning And Single-Candidate Mutation

Read-only planning may be broad. Mutation is single-candidate by default. Raw
broad mutation is forbidden by default.

A mutating sweep actor MUST require one of:

- an explicit `candidateId`;
- a promoted single-candidate remediation packet;
- an explicit bulk override tuple.

Bulk override MUST include configured permission, candidate list, symbolic
actions, policy hash, evidence hashes, reviewer approval, audit reason, and
recovery commands. Bulk override is high-impact and SHOULD require independent
review.

## Generated / Exempt Final Utility Artifacts

Final-response utilities, metrics writers, handoff writers, conflict packet
writers, and audit writers MUST either run before hard-clean verification or
write only to declared generated/exempt paths.

Generated/exempt artifacts MUST include provenance:

- producing actor;
- reason;
- owning work block or candidate, when applicable;
- generated path rule;
- content hash;
- whether the artifact is required for closeout evidence.

Generated artifacts must not be silently classified as source work or owned dirty
work.

## Relationship To Hard-Clean Final Gate

This protocol decides how closeout judgment becomes safe mutation. It does not
replace the hard-clean final gate.

After mutation, the repo MUST still prove the final postcondition:

`repo_closed_for_final_response`

That postcondition is satisfied only when repo-owned checks prove the selected
work block, target ref, dirty state, stash state, branch state, worktree state,
runtime state, queue state, audit artifacts, and cleanup state are inspectable
and policy-compliant.

Chat done is not repo closed.

## Historical Incident Examples

These example mappings come from the three repo incident maps:

| Incident Pattern | Protocol Requirement |
|---|---|
| Conflicting remote ref | Candidate packet plus adjudication report before repo-owned conflict remediation or retain-with-blocker. |
| Foreign dirty closeout | Deterministic dirty classification, retain/audit foreign paths, and never mutate them. |
| Dirty detached worktree | Preserve bytes and hashes before cleanup; adjudicate when ownership or sensitivity is ambiguous. |
| Target push race | Fetch, re-pin, rerun or block, and never force-push target. |
| Protected target closeout | No-op only when protected target is clean and repo-closed postcondition passes. |
| Response hook artifact | Write only generated/exempt state and never resurrect managed worktrees. |
| Retained candidate | Investigate or remediate one candidate at a time before terminal retention. |
| Raw broad sweep mutation | Require `candidateId`, promoted one-candidate remediation packet, or explicit audited bulk override. |
| Original closeout standard addendum | Map the rule to capability, profile, config, actor, test, audit, or explicit non-goal before finalizing the clean standard. |

## Non-Goals

This protocol does not:

- require subagents;
- require a specific implementation language;
- require PowerShell;
- require Git worktrees for every repo;
- define the final normative closeout standard;
- define install profiles;
- define the capability ledger schema;
- replace repo-specific policy;
- allow reviewers to bypass repo-owned mutation actors;
- treat documentation-only claims as implementation.

The protocol is portable because the packet/report/tuple/audit contract is
portable. The reviewer transport and repo actor implementation are pluggable.
