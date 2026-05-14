# Closeout Requirements Trace

Draft Round 4 trace from the original closeout standard and addenda into the portable framework.

This document is derived from:

- `AGENTS.md`
- `CLAUDE.md`
- `closeout.config.json`
- `tools/repo_hygiene/brokered_closeout.py`
- `tools/repo_hygiene/test_brokered_closeout.py`
- `CLOSEOUT-HISTORICAL-INCIDENT-MAP.md`
- `CLOSEOUT-CROSS-MAP-COMPARISON.md`
- `CLOSEOUT-ADJUDICATION-PROTOCOL.md`
- `CLOSEOUT-REFERENCE-MATRIX.md`
- `CLOSEOUT-FRAMEWORK-PROFILES.md`
- `CLOSEOUT-CAPABILITY-LEDGER.schema.json`

It is not the clean standard. Its job is to prove that the accumulated LLM Automatic Work Block Closeout Standard and addenda were mined into capability rows, profile decisions, ledger evidence expectations, or explicit non-goals before `CLOSEOUT-STANDARD.md` is written.

## Trace Boundary

The original closeout standard is no longer treated as a single normative blob. In this repo it is represented by durable policy, config, actors, tests, and Round artifacts.

Evidence classes:

- `Verified locally`: source exists in this workspace and is referenced by file path.
- `Cross-checked from prior analysis`: source is represented in the cross-repo Round docs and reports.
- `Needs ledger proof`: the rule is accepted, but each repo must later prove exact local implementation in `CLOSEOUT-CAPABILITY-LEDGER.json`.

Rules:

- A source rule that prevents universal data loss or false completion becomes `Core MUST`.
- A source rule that automates common multi-agent or recurring closeout becomes `Standard SHOULD`.
- A source rule that handles heavy scar tissue becomes `Max SHOULD` or `Conditional SHOULD`.
- A transport-specific source rule becomes `Surface Plugin`.
- A source rule that should not be portable becomes an explicit non-goal or repo policy item.
- Documentation alone is never enough for `YES` in the capability ledger.

## Requirements Trace Table

| Trace ID | Source Rule / Addendum Cluster | Source Evidence | Capability Rows | Profile Decision | Ledger Expectation | Clean Standard Disposition |
|---|---|---|---|---|---|---|
| `REQ-001` | Incoming closeout addenda are repo-wide framework changes, not chat-only notes. | Verified locally: `AGENTS.md:35`, `closeout.config.json:353`, `tools/repo_hygiene/test_brokered_closeout.py:493`. | `requirements-trace-to-original-standard`; `historical-incident-traceability`; `tooling-drift-detection`; `capability-ledger-schema`. | Core MUST | Ledger row must cite policy/config/test evidence or record a blocker. | Include as governance rule: incoming closeout rules must become durable policy, config, test, blocker, or explicit non-goal. |
| `REQ-002` | Historical incidents are source evidence for framework capabilities. | Verified locally: `CLOSEOUT-HISTORICAL-INCIDENT-MAP.md`; `CLOSEOUT-REFERENCE-MATRIX.md`. | `historical-incident-traceability`; `requirements-trace-to-original-standard`. | Core MUST | Ledger must cite the incident map or local equivalent. | Include as adoption prerequisite for framework work. |
| `REQ-003` | Ambiguous closeout decisions require structured adjudication before mutation. | Verified locally: `CLOSEOUT-ADJUDICATION-PROTOCOL.md`; cross-checked in `CLOSEOUT-CROSS-MAP-COMPARISON.md`. | `structured-adjudication-protocol`; `candidate-evidence-packet`; `adjudication-report`. | Core MUST | Ledger must cite packet/report schema, artifact roots, tests, or a scoped exception. | Include as first normative judgment rule. |
| `REQ-004` | Review surfaces are declared and pluggable; subagents are optional transport. | Verified locally: `CLOSEOUT-ADJUDICATION-PROTOCOL.md`; `CLOSEOUT-FRAMEWORK-PROFILES.md`. | `declared-review-surface`; `surface-unavailable-or-insufficient-reviewer-block`; `automated-subagent-dispatch`. | Core MUST for declared surfaces; Surface Plugin / Standard SHOULD for subagents | Ledger must distinguish protocol compliance from subagent adapter support. | Include portability boundary: structured adjudication is Core, subagent dispatch is not Core. |
| `REQ-005` | Reviewers recommend symbolic actions only; repo-owned actors mutate after revalidation. | Verified locally: `CLOSEOUT-ADJUDICATION-PROTOCOL.md`; repo actors in `tools/repo_hygiene/brokered_closeout.py`. | `adjudication-to-symbolic-action-boundary`; `repo-owned-mutation-after-adjudication`; `exact-mutation-tuple`. | Core MUST | Ledger must cite actor paths, tuple validation, audit artifacts, and tests. | Include as mutation authority rule. |
| `REQ-006` | High-impact mutation requires exact tuple, review quorum, stale tuple rejection, and immediate revalidation. | Verified locally: `closeout.config.json:169`, `tools/repo_hygiene/test_brokered_closeout.py:426`, `CLOSEOUT-ADJUDICATION-PROTOCOL.md`. | `exact-mutation-tuple`; `repo-owned-mutation-after-adjudication`; `adjudication-report`. | Core MUST | Ledger `YES` must include executable behavior, test, contract, or audit evidence. | Include tuple fields and staleness semantics; leave repo-specific quorum counts to policy. |
| `REQ-007` | Broker manifest and dirty baseline define work ownership and deterministic closeout selection. | Verified locally: `tools/repo_hygiene/test_brokered_closeout.py:1081`; `CLOSEOUT-REFERENCE-MATRIX.md`. | `broker-manifest-dirty-baseline`; `deterministic-work-block-selection`. | Core MUST | Ledger must cite manifest fields, dirty baseline capture, deterministic selection, and blocker behavior. | Include as work-block ownership rule. |
| `REQ-008` | Dirty state must be classified before repair, integration, cleanup, or final response. | Verified locally: `tools/repo_hygiene/brokered_closeout.py:4470`; `CLOSEOUT-REFERENCE-MATRIX.md`. | `dirty-classification`; `foreign-dirty-preservation`; `baseline-dirty-mixed-path-protection`. | Core MUST | Ledger must cite classifier, policy, tests, and audit evidence. | Include class names and fail-closed handling. |
| `REQ-009` | Foreign dirty work is retained and audited; it must not be staged, stashed, reset, or deleted by another work block. | Verified locally: `tools/repo_hygiene/brokered_closeout.py:6815`; cross-checked in Round docs. | `foreign-dirty-preservation`; `dirty-classification`; `hard-clean-final-gate`. | Core MUST | Ledger must cite target-overlap proof and retained-foreign audit behavior. | Include as non-negotiable data-preservation rule. |
| `REQ-010` | Clean-at-start paths may be auto-claimed only when the broker baseline proves they were clean or absent. | Verified locally: `tools/repo_hygiene/test_brokered_closeout.py:1229`; `CLOSEOUT-FRAMEWORK-PROFILES.md`. | `broker-manifest-dirty-baseline`; `dirty-classification`; `exact-mutation-tuple`. | Core MUST | Ledger must cite baseline proof and exact-path checkpoint behavior. | Include as safe auto-claim rule. |
| `REQ-011` | Baseline-dirty overlaps are mixed or unowned unless split or stronger ownership proof exists. | Verified locally: `tools/repo_hygiene/test_brokered_closeout.py:1266`; `CLOSEOUT-CROSS-MAP-COMPARISON.md`. | `baseline-dirty-mixed-path-protection`; `dirty-classification`; `dirty-split-automation`. | Core MUST; Max SHOULD for split automation | Ledger must cite blocker behavior and optional split actor evidence. | Include blocker rule; mark hunk-level ownership as optional/future unless implemented. |
| `REQ-012` | Final utility artifacts must run before hard-clean or write only generated/exempt paths with provenance. | Verified locally: `closeout.config.json:26`, `closeout.config.json:210`, `closeout.config.json:214`; `CLOSEOUT-ADJUDICATION-PROTOCOL.md`. | `final-utility-generated-or-preclean`; `dirty-classification`; `repo-closed-for-final-response`. | Core MUST | Ledger must cite generated path config, tests, and audit/provenance artifacts. | Include as final utility discipline. |
| `REQ-013` | Response and final hooks are advisory/status writers and must not resurrect managed worktrees. | Verified locally: `tools/repo_hygiene/test_brokered_closeout.py:1081`; cross-checked in DNG reports. | `advisory-hooks-non-authoritative`; `response-hook-no-worktree-resurrection`; `final-utility-generated-or-preclean`. | Core MUST | Ledger must cite hook path, no-worktree test, and status consumption behavior. | Include as hook authority boundary; adapter details belong to Surface Plugins. |
| `REQ-014` | Tooling drift must be detected before closeout blockers are treated as authoritative. | Verified locally: `closeout.config.json:169`, `closeout.config.json:320`, `tools/repo_hygiene/test_brokered_closeout.py:426`. | `tooling-drift-detection`; `bounded-wrapper-authority`; `capability-ledger-schema`. | Core MUST | Ledger `YES` must include drift-check path or scoped exception. | Include fail-closed stale tooling rule. |
| `REQ-015` | Closeout subprocesses must be bounded and fail closed on timeout, output cap, process-tree leftovers, or known failure text. | Verified locally: `AGENTS.md:59`, `tools/repo_hygiene/test_brokered_closeout.py:585`, `tools/repo_hygiene/test_brokered_closeout.py:514`. | `bounded-wrapper-authority`; `hard-clean-final-gate`; `repo-owned-mutation-after-adjudication`. | Core MUST | Ledger must cite bounded runner actor, tests, and audit events. | Include as process-boundary authority rule. |
| `REQ-016` | User-visible completion requires `repo_closed_for_final_response`, not just merge or validation success. | Verified locally: `AGENTS.md:60`, `tools/repo_hygiene/test_brokered_closeout.py:787`; closeout runs for prior Round artifacts. | `hard-clean-final-gate`; `repo-closed-for-final-response`; `final-utility-generated-or-preclean`. | Core MUST | Ledger must cite hard-clean checker, tests, and closeout audit artifacts. | Include as final completion postcondition. |
| `REQ-017` | Protected target closeout is a no-op only when the protected target is clean and repo-closed passes. | Verified locally: `AGENTS.md:61`, `closeout.config.json:76`, `tools/repo_hygiene/test_brokered_closeout.py:845`. | `protected-target-noop`; `hard-clean-final-gate`; `repo-closed-for-final-response`. | Standard SHOULD; Core candidate for target-triggered surfaces | Ledger must cite no-op actor/test and dirty protected blocker behavior. | Include in Standard; mention Core candidate in profile notes. |
| `REQ-018` | Runtime services executing repo code must stop before promotion and restart only after clean promotion and repo-closed verification. | Verified locally: `AGENTS.md:62`, `closeout.config.json:85`; `CLOSEOUT-HISTORICAL-INCIDENT-MAP.md`. | `runtime-service-lifecycle`; `repo-closed-for-final-response`; `bounded-wrapper-authority`. | Max / Conditional SHOULD | Ledger must say whether repo-owned runtime services exist and cite service lifecycle tests if enabled. | Include as conditional requirement, not universal Core. |
| `REQ-019` | Target push non-fast-forward is a recoverable race, never force-push permission. | Verified locally: `AGENTS.md:64`, `closeout.config.json:363`; cross-checked in Round docs. | `no-force-push-target-recovery`; `target-push-race-recovery`; `exact-mutation-tuple`. | Core MUST for no force-push; Standard SHOULD for full retry | Ledger must cite blocker or retry evidence and confirm no automatic force-push path. | Include no-force-push in Core and retry loop in Standard. |
| `REQ-020` | Closeout completion is a fixed point after finalize, hooks, repair, sweep, and remote cleanup. | Verified locally: `AGENTS.md:65`; closeout audit from prior Round artifacts. | `hard-clean-final-gate`; `repo-closed-for-final-response`; `repo-sweep-read-only-planning`; `final-utility-generated-or-preclean`. | Core MUST | Ledger must cite rerun/fixed-point behavior or blocker. | Include as completion model. |
| `REQ-021` | Remote feature branches are temporary unless policy explicitly retains them with audit. | Verified locally: `AGENTS.md:66`; `CLOSEOUT-REFERENCE-MATRIX.md`. | `remote-feature-clean-integration`; `retained-candidate-remediation`; `evidence-preserving-prune`. | Max SHOULD | Ledger must cite remote branch policy, retained audit, and cleanup actor if supported. | Include as Max/reference behavior. |
| `REQ-022` | Retained remediation is a dedicated actor, not a chat follow-up. | Verified locally: `AGENTS.md:67`, `tools/repo_hygiene/test_brokered_closeout.py:1995`. | `retained-candidate-remediation`; `repo-sweep-single-candidate-mutation`; `repo-owned-mutation-after-adjudication`. | Standard SHOULD | Ledger must cite one-candidate actor and terminal retain evidence. | Include in Standard. |
| `REQ-023` | Read-only repo sweep planning may be broad, but mutation is single-candidate by default. | Verified locally: `tools/repo_hygiene/test_brokered_closeout.py:1884`; `CLOSEOUT-ADJUDICATION-PROTOCOL.md`. | `repo-sweep-read-only-planning`; `repo-sweep-single-candidate-mutation`; `audited-bulk-override`. | Core MUST | Ledger must cite candidate-id gating or audited bulk override; known gaps remain `PARTIAL`. | Include Core default and require explicit audited bulk exception. |
| `REQ-024` | Bulk mutation requires explicit override, configured permission, reviewer approval, per-candidate tuples, audit reason, and recovery commands. | Verified locally in protocol/schema; implementation enforcement remains a matrix gap. | `audited-bulk-override`; `repo-sweep-single-candidate-mutation`; `adjudication-report`. | Core MUST | Ledger should mark `PARTIAL` until config/test enforcement exists. | Include rule now; implementation proof belongs to ledger and standard acceptance. |
| `REQ-025` | Merge-failed retained candidates should enter agent remediation before terminal block when policy/surface supports it. | Verified locally: `AGENTS.md:69`, `closeout.config.json:382`, `tools/repo_hygiene/test_brokered_closeout.py:2477`. | `agent-remediation-queue`; `surface-unavailable-or-insufficient-reviewer-block`; `automated-subagent-dispatch`. | Surface Plugin / Standard SHOULD | Ledger must distinguish queue protocol from subagent transport. | Include adapter boundary: queue is optional automation around Core adjudication. |
| `REQ-026` | Missing required review or subagent surface writes durable unavailable/insufficient-review evidence. | Verified locally in `CLOSEOUT-ADJUDICATION-PROTOCOL.md`; `closeout.config.json:382`. | `surface-unavailable-or-insufficient-reviewer-block`; `declared-review-surface`; `agent-remediation-queue`. | Standard SHOULD; Core when review is required | Ledger must cite status names, artifact roots, and recovery command behavior. | Include status vocabulary; keep dispatch optional. |
| `REQ-027` | Evidence-preserving transaction prune must preserve recovery evidence before destructive deletion. | Verified locally: `AGENTS.md:70`, `closeout.config.json:474`, `tools/repo_hygiene/test_brokered_closeout.py:2128`. | `evidence-preserving-prune`; `retained-candidate-remediation`; `repo-owned-mutation-after-adjudication`. | Max SHOULD; Core invariant for ambiguous deletion | Ledger must cite recovery root, byte/hash/bundle proof, reviewer verdicts, and deletion order. | Include Core invariant: no ambiguous destructive cleanup without recovery/adjudication; full machinery is Max. |
| `REQ-028` | Remediation freeze prevents lifecycle automation from worsening dirty-state confusion. | Verified locally: `AGENTS.md:55`, `closeout.config.json:125`, `tools/repo_hygiene/test_brokered_closeout.py:1100`. | `remediation-freeze`; `dirty-classification`; `hard-clean-final-gate`. | Max SHOULD | Ledger must cite marker/env, hook blocking, audit packet, and removal quorum behavior. | Include as Max safety brake; unsupported repos must block or report unavailable when needed. |
| `REQ-029` | Dirty split automation preserves exact owned dirty clusters before removing them from the original context. | Verified locally in config/test corpus; cross-checked in Round docs. | `dirty-split-automation`; `baseline-dirty-mixed-path-protection`; `exact-mutation-tuple`. | Max SHOULD | Ledger must cite split actor, exact path tuple, recovery branch/artifact, and tests. | Include as Max automation; Core only requires mixed dirty to fail closed. |
| `REQ-030` | Checked-out, locked, active, dirty, or protected worktrees require evidence before cleanup. | Verified locally in incident map and test corpus; cross-checked in matrix. | `checked-out-and-locked-worktree-handling`; `evidence-preserving-prune`; `dirty-classification`. | Standard SHOULD | Ledger must cite lock/protected/dirty evidence and blocker behavior. | Include in Standard; Core invariant is that ambiguous worktree cleanup blocks. |
| `REQ-031` | The capability ledger must make compliance machine-readable and block documentation-only `YES` claims. | Verified locally: `CLOSEOUT-CAPABILITY-LEDGER.schema.json`. | `capability-ledger-schema`; `requirements-trace-to-original-standard`; `tooling-drift-detection`. | Core MUST | Ledger schema requires status, profile, decision, evidence paths, blockers, next step, and YES guardrails. | Include as evidence contract for adoption. |
| `REQ-032` | The clean standard must not smuggle Max-profile machinery into Core. | Verified locally: `CLOSEOUT-FRAMEWORK-PROFILES.md`. | `capability-ledger-schema`; `runtime-service-lifecycle`; `remediation-freeze`; `agent-remediation-queue`; `automated-subagent-dispatch`. | Core governance rule; Max/Surface rows stay optional unless risk is present | Ledger must allow honest `UNAVAILABLE`, `NO`, or `PARTIAL` for unsupported non-Core rows. | Include non-smuggling rule near profile section of clean standard. |
| `REQ-033` | The canonical dashboard spec and tracked round-delta note must stay visibly fresh by regenerating the comparison artifacts in the same work block whenever either anchor changes. | Verified locally: `docs/19-closeout-dashboard-spec.md`; `CLOSEOUT-CROSS-MAP-COMPARISON.md`; `CLOSEOUT-REFERENCE-MATRIX.md`. | `docs-freshness-regeneration`; `historical-incident-traceability`; `requirements-trace-to-original-standard`. | Core MUST | The ledger and docs should show same-block regeneration evidence or a stale-artifact blocker whenever the freshness anchors move. | Include as a docs-freshness rule in the clean standard. |

## Explicit Non-Goals And Repo Policy Items

These source rules should not become universal Core requirements:

- Subagent dispatch is not Core. The packet/report/tuple/audit contract is Core; Codex, Claude, CI, deterministic, and manual adapters are Surface Plugins.
- PowerShell is not required by the portable framework. MLV-App uses PowerShell adapters, but the standard should describe repo-owned actors and bounded process behavior.
- Git worktrees are not required for every repo. They are an implementation surface where a repo selects them.
- Runtime service lifecycle is conditional. It is required only when repo-owned services execute repo code.
- Remediation freeze, evidence-preserving prune, remote feature clean integration, and dirty split automation are Max unless the repo selects that profile or owns the relevant risk.
- Hunk-level ownership is not Core. Baseline-dirty overlap must fail closed unless stronger ownership proof exists.
- Documentation-only compliance is never a `YES` capability ledger status.

## Inputs To `CLOSEOUT-STANDARD.md`

The clean standard should carry forward:

1. The first normative rule from the adjudication protocol.
2. Core profile invariants from `CLOSEOUT-FRAMEWORK-PROFILES.md`.
3. Ledger-backed evidence requirements from `CLOSEOUT-CAPABILITY-LEDGER.schema.json`.
4. The non-smuggling rule.
5. The distinction between deterministic review for mechanically provable actions and structured adjudication for ambiguity.
6. The hard-clean final response postcondition.
7. The repo-owned mutation boundary.
8. The single-candidate mutation default and audited bulk override rule.
9. The no-force-push target recovery invariant.
10. The generated/exempt final utility discipline.
11. The same-work-block docs-freshness rule for the canonical dashboard spec and the tracked round-delta note.

The clean standard should avoid:

- reprinting every incident from the historical map
- requiring subagents
- requiring MLV-App-specific paths or PowerShell names
- turning Max scar tissue into a minimum install burden
- treating a prose artifact as implementation proof

## Open Items For Ledger Population

When `CLOSEOUT-CAPABILITY-LEDGER.json` is later populated, these rows need special attention:

- `repo-sweep-single-candidate-mutation`: MLV-App has one-candidate retained remediation, but raw broad apply enforcement remains `PARTIAL`.
- `audited-bulk-override`: accepted as Core, but implementation enforcement still needs config/test proof.
- `surface-unavailable-or-insufficient-reviewer-block`: protocol/statuses exist; each adapter must prove durable artifacts.
- `requirements-trace-to-original-standard`: this document satisfies the artifact requirement, but each repo must cite its local source corpus.
- `capability-ledger-schema`: now drafted, but actual ledgers still need population and validation.

## Next Artifact Input

This trace should feed `CLOSEOUT-STANDARD.md`.

The standard can now be written from:

- source evidence: historical map and this requirements trace
- judgment contract: adjudication protocol
- capability decisions: reference matrix and profiles
- machine-readable proof model: capability ledger schema

Principle:

> Requirements traced, standard next, implementation prompt later.
