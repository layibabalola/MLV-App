# Closeout Cross-Map Comparison

Round -0.5 evidence worksheet for the portable closeout framework.

This worksheet is not the final reference matrix and not normative law. Its job is
to distill the DngAutoProcessor, MLV-App, and AdversarialLLM historical incident
maps into shared safety patterns, profile candidates, and open gaps before
drafting `CLOSEOUT-ADJUDICATION-PROTOCOL.md`.

Framework center:

> Structured adjudication is mandatory. Subagents are optional transport. Repo-owned actors are the only mutation authority.

Principle:

> Scar tissue first, comparison second, protocol third, law later.

## Round Delta Note

This round-delta note converted the cross-repo comparison idea into a durable contract and keeps the current round's delta visible beside the comparison rules:

- `workflow-comparison` is now treated as a baseline-checked dashboard surface, not just a descriptive label.
- The canonical dashboard spec at `docs/19-closeout-dashboard-spec.md` is now part of the machine-checked baseline.
- Docs freshness is operational: when either the canonical dashboard spec or this tracked round-delta note changes, the comparison artifacts in this work block must be regenerated before the block is closed.
- `webDashboardSpec` now carries explicit baseline keys for `readOnlyByDefault`, `preserveClientStateAcrossRefresh`, and `rollbackForbiddenActions`.
- The rollback ban is regression-checked for both `delete-evidence` and `force-push`.
- Freshness is visible in the artifact itself, not aspirational in chat: the comparison docs must show the regeneration rule and the latest comparison state instead of merely promising future sync.

The intent is that later repos can compare workflow changes from this tracked note instead of reconstructing the delta from chat history.

## Shared Report Envelope

The next comparison hardening step is report symmetry: every repo should use
the same closeout-report headings and freshness marker so "report in detail"
means the same thing everywhere. The canonical compare envelope is:

- objective
- last completed work
- next steps
- blockers
- freshness marker or timestamp
- compare findings

When the report shape is aligned, all repos can publish their closeout
workflow in one pass and the results can be compared mechanically instead of
manually translated. The compare note, dashboard spec, and repo-local closeout
docs should all carry the same envelope so the group can diff them without
renaming sections.

## Mechanical Compare Loop

The compare loop for future rounds should be:

1. Regenerate the canonical dashboard spec and this round-delta note in the
   same work block.
2. Publish each repo's closeout workflow using the shared report envelope.
3. Compare matching headings across repos, not free-form summaries.
4. Record the freshness marker or timestamp beside the comparison result.
5. Block closeout if the freshness marker, snapshot pointer, or compare
   artifact is missing or stale.

How this round changed from last round:

- Last round hardened the compare artifacts themselves.
- This round hardens the shared reporting envelope so the compare artifacts can
  be regenerated and read in the same shape across repos.
- The practical next step is to treat the shared report headings as the
  canonical closeout resume format, not just a note-taking convention.
- A visible freshness marker should be stable enough to copy verbatim, for
  example `Last updated: YYYY-MM-DD HH:MM TZ` or `Round delta snapshot: <id>`.
- The portable dashboard contract is the same sticky URL, SSE/polling refresh,
  history browsing, and `Inspect -> Preview -> Request -> Apply` flow already
  captured in the canonical dashboard spec.

## Cross-Map Worksheet

| Capability / Incident Pattern | DNG Evidence | MLV-App Evidence | AdversarialLLM Evidence | Common Rule Emerging | Profile Candidate | Open Gap |
|---|---|---|---|---|---|---|
| `structured-adjudication-protocol` | Conflicting remote refs, retained candidates, dirty preservation, and historical prune needed judgment beyond scripts. | Merge-required branches, backup branches, dirty detached worktrees, conflict queue, and broad sweep override need judgment packets. | Retained refs, merge-failed candidates, dirty detached worktrees, protected locks, and requirements distillation need adjudication. | Ambiguous closeout decisions require durable structured judgment before mutation. | Core MUST | Define the shared packet/report contract. |
| `declared-review-surface` | DNG supports Codex, Claude, subagents, humans, CI, and deterministic review as declared surfaces. | MLV-App uses deterministic review for exact checks and agents/humans/subagents for ambiguity. | AdversarialLLM separates required adjudication protocol from optional subagent transport. | Reviewer transport is pluggable; the evidence protocol is mandatory. | Core MUST | Standardize allowed review surface values and unavailable-state semantics. |
| `candidate-evidence-packet` | Remote refs, conflicts, dirty state, target pins, and recovery commands are captured per candidate. | Branch/worktree candidates require refs, dirty baseline, claims, merge base, validation, and recovery evidence. | Candidate packets require refs, pins, dirty baseline, policy hash, evidence hash, and recovery command. | Every ambiguous or high-impact candidate needs a durable evidence packet. | Core MUST | Lock minimum fields and hash requirements by scenario kind. |
| `adjudication-report` | Conflict decisions, broad sweep override, retained candidates, and standard distillation map to review reports. | Agent queue results, backup prune, dirty preservation, and freeze removal need review reports. | Merge-failed and protected/dirty cases need durable reports, not chat memory. | Judgment must become a durable report tied to packet hash. | Core MUST | Define report fields, blocker shape, and confidence semantics. |
| `adjudication-to-symbolic-action-boundary` | DNG states agents decide intent while repo-owned actors mutate. | MLV-App reinforces symbolic recommendations only and repo-owned mutation afterward. | AdversarialLLM says automation may propose, but mutation remains exact-tuple and repo-owned. | Reviewers recommend symbolic actions; they do not emit arbitrary mutation commands. | Core MUST | Enumerate initial symbolic actions and allow repo policy extension. |
| `repo-owned-mutation-after-adjudication` | DNG mutators include finalize, sweep, dirty split, and retained-remediation actors. | MLV-App uses repo actors for sweep, finalize, dirty split, queue collection, prune, and closeout. | AdversarialLLM separates reviewer decision from repo actor mutation. | Mutation authority belongs to repo-owned symbolic actors after revalidation. | Core MUST | Ledger must prove actors, audit events, tests, and contract checks exist. |
| `repo-sweep-single-candidate-mutation` | DNG identifies raw broad sweep apply as a remaining gap and prefers one-candidate retained remediation. | MLV-App reports raw `repo_sweep(..., apply=True)` can still process more than one candidate in some paths. | AdversarialLLM flags broad sweep mutation risk and calls for `CandidateId` or audited bulk override. | Read-only planning may be broad; mutation is single-candidate by default. | Core MUST | Require `candidateId` or explicit audited bulk override for mutating sweep entrypoints. |
| `dirty-classification` | Owned, mixed, unowned, foreign, and generated states shape closeout safety. | Broker baseline, active claims, generated/sensitive rules, and exact paths drive classification. | Dirty baseline and owned/mixed/unowned/generated classification have behavioral tests. | Dirty state must be classified before repair, checkpoint, prune, or final response. | Core MUST | Normalize dirty class names and packet fields. |
| `foreign-dirty-preservation` | Foreign dirty is retained and audited, not staged, stashed, reset, or deleted. | Foreign dirty can coexist with independent closeout if no target-delta overlap exists. | Foreign dirty can be retained while clean integration proceeds. | Foreign dirty work must never be auto-mutated by another work block. | Core MUST | Define target-delta overlap proof and retained audit semantics. |
| `baseline-dirty-mixed-path-protection` | Baseline overlap blocks whole-file checkpoint and requires split or proof. | Baseline-dirty claimed paths become mixed or unowned, not auto-owned. | Clean-at-start checkpointing is allowed only for proven clean baseline paths. | Pre-existing dirty bytes cannot be captured as current work by claim alone. | Core MUST | Decide whether hunk-level ownership is future extension or explicit non-goal. |
| `hard-clean-final-gate` | Final response requires repo-closed postcondition, not just merge success. | `repo_closed_for_final_response` checks dirty, stash, branch, worktree, queue, and runtime state. | Hard-clean separates chat done from repo closed. | User-visible done requires authoritative repo-closed postcondition. | Core MUST | Standardize blocker names and exempt/generated path rules. |
| `repo-closed-for-final-response` | DNG final hooks re-check postcondition and block on uncommitted WIP. | MLV-App closed its incident-map work on `master` with a clean worktree. | AdversarialLLM says repo-closed audit is the only safe done state. | A final answer is not a closeout event. | Core MUST | Define allowable WIP reporting versus completion reporting. |
| `response-hook-no-worktree-resurrection` | DNG response/final hooks pass `SkipSessionWorktree`; tests prevent managed worktree creation. | MLV-App allows lightweight manifest refresh but forbids session worktree lifecycle mutation. | AdversarialLLM tests response bootstrap does not create worktrees. | Advisory/final hooks may write status only; lifecycle mutation is forbidden there. | Core MUST | Define hook authority and later blocking-gate consumption. |
| `final-utility-generated-or-preclean` | Metrics, timestamp, handoff, and audit artifacts must be generated/exempt or pre-clean writes. | Generated closeout artifacts are classified as exempt evidence. | Generated audit/conflict/runtime packets are not owned source work. | Final utility writes must happen before hard-clean or only to declared generated/exempt paths. | Core MUST | Require generated path declarations in ledger and config. |
| `target-push-race-recovery` | DNG has target-race contract and no-force-push rule. | MLV-App has durable fetch/update/rerun blocker behavior; full retry is a possible refinement. | AdversarialLLM has the strongest retry, re-pin, and no-force-push evidence. | Target non-fast-forward is a recoverable race, never force-push permission. | Core MUST for no force-push; Standard SHOULD for full retry | Decide bootstrap allowance for partial retry with durable blocker. |
| `protected-target-noop` | DNG has protected target no-op behavior and dirty blocker tests. | MLV-App handles clean protected target no-op without synthetic work block. | AdversarialLLM reports this as a gap. | Clean protected targets should close successfully without synthetic work blocks. | Standard SHOULD; Core candidate | Decide if this becomes Core for trigger surfaces that run on target branches. |
| `retained-candidate-remediation` | Retained candidates are a remediation queue; terminal retain needs candidate-specific evidence. | MLV-App has retained-candidate remediation and one-candidate actors. | AdversarialLLM has strong remote retained remediation and broader local sweep gaps. | Retained candidates require candidate-specific investigation or remediation. | Standard SHOULD; Core for non-manual investigation evidence | Need uniform retained blocker packet and recovery command schema. |
| `tooling-drift-detection` | Stale tooling is classified as `closeout_tooling_stale` before blockers become authoritative. | MLV-App contract/tooling drift checks are part of reference behavior. | AdversarialLLM uses tooling baseline checks to prevent false authoritative blockers. | Stale closeout tooling must report drift, not pretend to be authoritative closeout. | Core MUST | Ledger must require drift evidence for `YES`. |
| `surface-unavailable-or-insufficient-reviewer-block` | DNG has agent queue modes including `surface-unavailable`. | MLV-App separates Core adjudication from subagent dispatch and validates queue result scope. | AdversarialLLM identifies unavailable-surface artifacts as a gap. | If required review cannot be provided, block durably with unavailable/insufficient-review evidence. | Standard SHOULD; Core when review is required | Define exact unavailable status values and recovery commands. |
| `automated-subagent-dispatch` | DNG supports eligible shards but treats dispatch as surface-dependent. | MLV-App has rich queue, dispatch, collection, and out-of-scope rejection behavior. | AdversarialLLM treats subagent dispatch as optional transport. | Subagents are an accelerator for adjudication, not a compliance prerequisite. | Surface Plugin / Standard SHOULD | Define adapter-specific docs for Codex, Claude, CI, and manual review. |
| `remediation-freeze` | DNG has config, docs, and freeze actor references; treats it as Max/optional for small repos. | MLV-App has freeze marker/env, hook blocking, audit packets, and removal quorum tests. | AdversarialLLM reports this as a gap. | Freeze is a safety brake when lifecycle automation is making state worse. | Max SHOULD | Define portable blocker semantics when unsupported. |
| `evidence-preserving-prune` | DNG policy covers dirty detached and historical prune preservation, with exact byte preservation to verify. | MLV-App has stronger evidence-preserving prune behavior. | AdversarialLLM marks dirty detached preservation as partial. | Historical, non-ancestor, or dirty deletion needs preservation evidence and recovery path. | Max SHOULD | Establish minimum recovery bundle and byte preservation requirements. |
| `runtime-service-lifecycle` | DNG treats runtime lifecycle as conditional when services exist. | MLV-App stops/restarts configured runtime services around clean promotion and repo-closed. | AdversarialLLM tests runtime stop/restart behavior. | Runtime services executing repo code must be managed when configured. | Max / Conditional SHOULD | Only required for repos that own runtime services. |
| `requirements-trace-to-original-standard` | DNG maps the original standard/addenda to capability rows and says trace is Core. | MLV-App treats the old standard as source corpus, not final spec. | AdversarialLLM says addenda were instrumental and trace must precede the clean standard. | The old standard/addenda must be mined into capability rows, profile decisions, tests, or explicit non-goals. | Core MUST | Create `CLOSEOUT-REQUIREMENTS-TRACE.md` before `CLOSEOUT-STANDARD.md`. |
| `docs-freshness-regeneration` | The canonical dashboard spec and tracked round-delta note are paired freshness anchors. | Comparison docs must regenerate in the same work block when either anchor changes. | Freshness must be visible in the artifact, not deferred to future chat. | Comparison freshness is operational, not aspirational: regenerate the comparison artifacts before closing the work block. | Core MUST | Add a stale-artifact blocker and regeneration evidence path whenever the anchors move. |

## Common Rules Emerging

- A mechanically safe action may proceed by deterministic review.
- An ambiguous action must have structured adjudication.
- Review surfaces are declared and pluggable.
- Subagents are optional transport, not a requirement.
- Reviewers produce evidence-backed symbolic recommendations only.
- Repo-owned actors are the only mutation authority.
- Mutation must be exact-tuple, revalidated, bounded, audited, and recoverable.
- Broad read-only planning is allowed.
- Mutation is single-candidate by default.
- Bulk mutation requires explicit override, configured permission, and audit reason.
- Comparison freshness is operational: the canonical dashboard spec and tracked round-delta note are freshness anchors, and comparison artifacts must be regenerated in the same work block when either changes.
- The original LLM Automatic Work Block Closeout Standard and addenda remain source evidence.

## Inputs For `CLOSEOUT-ADJUDICATION-PROTOCOL.md`

The protocol should define:

1. Declared review surfaces.
2. Candidate evidence packet fields.
3. Adjudication report fields.
4. Independent review requirements.
5. Surface-unavailable and insufficient-review blocker states.
6. Symbolic recommendation boundary.
7. Exact mutation tuple.
8. Repo-owned mutation authority.
9. Audit and recovery artifact requirements.
10. Broad planning and single-candidate mutation boundaries.

## Principle

> Scar tissue first, comparison second, protocol third, law later.
