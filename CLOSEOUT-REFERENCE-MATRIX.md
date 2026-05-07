# Closeout Reference Matrix

Draft Round 1 comparison matrix for the portable closeout framework.

This matrix is derived from:

- `CLOSEOUT-HISTORICAL-INCIDENT-MAP.md`
- `CLOSEOUT-CROSS-MAP-COMPARISON.md`
- `CLOSEOUT-ADJUDICATION-PROTOCOL.md`
- shared Round -1 / Round -0.5 / Round 0 reports from DngAutoProcessor and AdversarialLLM
- shared 2026-05-07 implementation addenda from MLV-App, DngAutoProcessor,
  and AdversarialLLM

It is not the final standard and not the capability ledger. It converts the
incident map, cross-map worksheet, and adjudication protocol into capability
rows so the next profile and ledger artifacts can make explicit decisions.

Evidence boundary:

- MLV-App status is based on local files, local tests referenced by the incident
  map, and repo closeout runs in this workspace.
- DngAutoProcessor and AdversarialLLM status is based on their shared reports in
  the cross-repo planning thread, not direct inspection from this workspace.
- `PARTIAL` is expected and not a failure. It means at least one durable layer,
  behavioral test, actor path, adapter path, or edge case is still missing.

Status legend:

- `YES`: reported or locally verified implementation/evidence is strong enough
  for the current planning round.
- `PARTIAL`: capability exists in docs/config/actors/tests but has known gaps, or
  evidence is not yet complete across enforcement layers.
- `NO`: reported missing or not implemented.
- `UNKNOWN`: not enough evidence from the repo reports.

## 2026-05-07 Sync Notes

- MLV-App has since promoted `surface-unavailable-or-insufficient-reviewer-block`
  to `YES` in the local capability ledger. Local evidence includes declared
  review surfaces, tuple-bound `review_surface_unavailable` reports, CLI/wrapper
  support, and the table-driven regression for every declared surface.
- DngAutoProcessor reported commit `2a1e0494` on `master`, adding integrated
  branch detection (`ancestorOfTarget`, `treeEquivalentToTarget`,
  `patchEquivalentToTarget`, `alreadyIntegrated`,
  `goneUpstreamAfterIntegration`), routing already-integrated dirty branches
  into remediation freeze, and terminating repeated finalize blocker/evidence
  tuples. Its remediation-freeze row remains `PARTIAL` until unfreeze quorum,
  exact allowlists, and all hook surfaces have table-driven proof.
- DngAutoProcessor later reported commit `f8f507bc` on `master`, adding
  tuple-bound `review_surface_unavailable` reports, declared review surfaces,
  quorum output for those surfaces, fail-closed unknown surface ids, focused
  test selection, and ledger promotion of
  `surface-unavailable-or-insufficient-reviewer-block` to `YES`
  (`33 YES / 7 PARTIAL`).
- AdversarialLLM reported pushed commit `04d7cdd` on
  `codex/closeout-cross-repo-improvements`, adding focused local-only and
  ahead-only dirty repair regressions. Its `local-only-repo-closeout` row remains
  `PARTIAL` because the broader `CompleteRepairsMissingUpstreamThenFinalizes`
  suite path still hangs.
- AdversarialLLM later reported an uncommitted draft matrix/profile/standard
  reconciliation, but publish/closeout was blocked by an exclusive ledger path
  claim and hygiene review classification. Those draft rows remain planning
  input here, not landed compliance evidence.

## Matrix

| Capability | MLV-App | DngAutoProcessor | AdversarialLLM | Framework Decision | Profile | Strongest Reference | Smallest Next Step |
|---|---|---|---|---|---|---|---|
| `structured-adjudication-protocol` | YES: protocol committed; queue/conflict/prune rows exist. | YES: incident map and protocol draft cover ambiguous retained/conflict/dirty cases. | YES: strongly argues script-only cleanup is unsafe. | Ambiguous closeout requires durable adjudication. | Core MUST | Shared | Use this protocol as source for schema and tests. |
| `declared-review-surface` | YES: protocol declares Codex, Claude, human, CI, deterministic reviewers. | YES: same surfaces reported. | YES: same surfaces, with transport/protocol split. | Reviewer transport is pluggable; evidence contract is portable. | Core MUST | Shared | Encode allowed initial values in ledger schema. |
| `candidate-evidence-packet` | YES: protocol fields plus MLV queue/prune/dirty incident evidence. | PARTIAL/YES: reports include refs, pins, dirty baseline, recovery command. | PARTIAL/YES: fields listed; some packet schemas still gaps. | Ambiguous candidates need durable packet evidence. | Core MUST | MLV-App | Turn minimum fields into schema row. |
| `adjudication-report` | YES: protocol includes report fields and valid outcomes. | YES/PARTIAL: protocol covers reports and outcomes; behavioral artifact parity still needs ledger proof. | PARTIAL: report need is explicit; `agent-resolution-packet.v1` gap noted. | Judgment must become durable report, not chat memory. | Core MUST | MLV-App | Define report schema and artifact paths. |
| `adjudication-to-symbolic-action-boundary` | YES: protocol and queue tests keep reviewers advisory. | YES: DNG distinguishes review intent from actors. | YES: automation proposes; repo mutates. | Reviewers recommend symbolic actions only. | Core MUST | Shared | Later standard should keep action catalog profile-specific. |
| `repo-owned-mutation-after-adjudication` | YES: closeout, sweep, queue, prune, split actors exist. | YES: closeout/finalize/sweep/remediation actors reported. | YES: strong repo-owned actor framing. | Only repo-owned actors mutate after revalidation. | Core MUST | MLV-App | Ledger must require actor, audit, and behavior-test evidence. |
| `exact-mutation-tuple` | YES: exact tuple/quorum tests referenced. | PARTIAL/YES: tuple concept and repeated blocker/evidence tuple terminal handling reported; per-action ledger parity remains to prove. | YES: candidate/action/evidence/policy/pins tuple evidence. | Mutation requires exact tuple and stale tuple rejection. | Core MUST | AdversarialLLM / MLV-App | Normalize tuple fields across repos. |
| `bounded-wrapper-authority` | YES: bounded runner tests and wrapper evidence. | PARTIAL: earlier gap around authoritative wrapper wiring. | YES: timeout/output cap fail-closed evidence. | Closeout child actors must be bounded and fail closed. | Core MUST | MLV-App / AdversarialLLM | DNG: wire bounded runner into authoritative wrapper if not complete. |
| `broker-manifest-dirty-baseline` | YES: broker manifest, claims, dirty baseline tests. | YES: broker manifest, dirty baseline, path claims, and committed delta references reported. | YES: manifest schema, lease, claims, dirty baseline reported. | Broker manifest and dirty baseline are core safety primitives. | Core MUST | AdversarialLLM / MLV-App | Normalize required manifest fields. |
| `deterministic-work-block-selection` | YES: local closeout selection evidence and tests. | PARTIAL: deterministic selection was a known DNG fix target. | PARTIAL: branch-only fallback still reported. | Finalize must select deterministically or block. | Core MUST | MLV-App | Ban branch-only finalize unless unique tuple is proven. |
| `dirty-classification` | YES: owned/mixed/unowned/foreign/generated coverage. | YES: owned/foreign/unowned/mixed/generated reported. | YES: owned/mixed/unowned/generated tests reported. | Dirty state must be classified before mutation. | Core MUST | Shared | Align class names and packet fields. |
| `foreign-dirty-preservation` | YES: independent closeout and target-overlap blocker tests. | YES: retained/audited, not staged or stashed. | YES: clean integration can proceed while preserving foreign dirty. | Never mutate another work block's dirty state. | Core MUST | Shared | Define target-delta overlap evidence. |
| `baseline-dirty-mixed-path-protection` | YES: baseline-dirty overlap blocks whole-file checkpoint. | YES: `baseline-dirty-overlaps-candidate` reported. | YES: exact owned checkpoint and mixed baseline tests. | Pre-existing dirty bytes cannot be auto-claimed. | Core MUST | Shared | Hunk-level ownership remains optional/future unless implemented. |
| `hard-clean-final-gate` | YES: repo-closed postcondition passed locally. | YES: `RequireRepoClosed` blocks WIP docs. | YES: hard-clean final gate tests reported. | User-visible done requires repo-closed postcondition. | Core MUST | Shared | Standardize blocker names and exempt path rules. |
| `repo-closed-for-final-response` | YES: fixed-point closeout passed on `master`. | YES: final gate blocks WIP accurately and reports clean worktree status. | YES: chat done vs repo closed is explicit. | Completion is a repo state, not a chat claim. | Core MUST | MLV-App | Define WIP-reporting versus completion-reporting language. |
| `advisory-hooks-non-authoritative` | YES: bridge/final hooks are reminders/status, not mutation authority. | YES: response/final hook discipline is strong. | YES: exit-0 hooks must feed later blocking gate. | Advisory hooks may write status; later gates decide. | Core MUST | DngAutoProcessor | Formalize hook status consumption in standard. |
| `response-hook-no-worktree-resurrection` | YES: lightweight manifest without session worktree lifecycle. | YES: `SkipSessionWorktree` scar tissue and tests reported. | YES: bootstrap no-worktree test reported. | Response/final hooks must not resurrect managed worktrees. | Core MUST | DngAutoProcessor | Add adapter obligations per surface. |
| `final-utility-generated-or-preclean` | YES: generated/exempt artifact policy and tests. | YES: metrics/handoff/timestamps classified generated or pre-clean. | YES: generated packets not source work. | Final utility writes are pre-clean or generated/exempt. | Core MUST | Shared | Require generated/exempt declarations in ledger/config. |
| `tooling-drift-detection` | YES: contract/tooling drift checks referenced. | YES: `closeout_tooling_stale` reported. | YES: tooling baseline and contract verifier reported. | Stale tooling is non-authoritative. | Core MUST | Shared | Ledger `YES` requires drift-check evidence. |
| `repo-sweep-read-only-planning` | YES: broad planning supported. | YES: broad planning supported. | YES: plan mode first-class. | Broad read-only planning is allowed. | Core MUST | Shared | Keep separate from mutation rules. |
| `repo-sweep-single-candidate-mutation` | YES: retained actor and raw sweep apply now default to one candidate or block without exact audited override. | PARTIAL: retained actor preferred; raw sweep apply gap remains reported. | PARTIAL: broad mutation risk called out. | Mutation is single-candidate by default. | Core MUST | MLV-App | Keep every raw broad apply path gated by `candidateId` or audited bulk override. |
| `audited-bulk-override` | YES: policy, CLI, actor, and regression tests require exact candidate set, per-candidate tuples, audit reason, recovery command, and reviewer approval. | PARTIAL: needs explicit audited override. | PARTIAL: requested for broad mutation. | Bulk mutation requires explicit override, permission, audit reason. | Core MUST | MLV-App | Port policy/config/test row to other implementations. |
| `no-force-push-target-recovery` | YES: no-force-push target race blocker path. | YES: no-force-push marked Core. | YES: strongest retry/no-force-push evidence. | Target race is never force-push permission. | Core MUST | AdversarialLLM | Standardize minimum blocker fields. |
| `local-only-repo-closeout` | YES: local-only closeout is represented in the capability ledger and covered by local closeout behavior. | YES/PARTIAL: no remote was configured for the reported DNG checkout and the local worktree was clean; broader local-only suite proof was not claimed. | PARTIAL: focused local-only pass exists, but the broader `CompleteRepairsMissingUpstreamThenFinalizes` path still hangs. | Repos without a usable upstream still need repo-closed evidence or a durable blocker. | Core MUST | MLV-App / AdversarialLLM | Fix the AdversarialLLM full-suite hang and encode the local-only blocker/pass fields portably. |
| `target-push-race-recovery` | PARTIAL: fetch/update/rerun blocker; full retry is future. | PARTIAL: contract/tests, runtime completeness to verify. | YES: strongest fetch/re-pin/retry ledger. | Full retry/reintegrate is high-value automation. | Standard SHOULD | AdversarialLLM | Decide Core blocker versus Standard retry loop. |
| `protected-target-noop` | YES: clean protected target no-op and dirty blocker tests. | YES: no-op and dirty blocker reported. | NO: reported gap. | Clean protected target may no-op only when repo-closed passes. | Standard SHOULD; Core candidate | MLV-App | AdversarialLLM: implement clean protected-target no-op. |
| `retained-candidate-remediation` | YES: one-candidate actor and retained terminal evidence tests. | YES/PARTIAL: retained actor and queue reported; docs WIP. | YES/PARTIAL: strong remote remediation; broader local parity gap. | Retained candidates are a remediation queue, not passive retain. | Standard SHOULD | MLV-App | Define uniform retained blocker packet/recovery schema. |
| `surface-unavailable-or-insufficient-reviewer-block` | YES: declared surfaces, exact-tuple unavailable reports, CLI/wrapper support, and table-driven regression proof are in the local ledger. | YES: commit `f8f507bc` reports tuple-bound unavailable reports, declared surfaces, fail-closed unknown ids, focused tests, and ledger promotion to YES. | NO/PARTIAL: unavailable artifact gap remains; later draft reconciliation was blocked before commit. | Missing review surface must block durably. | Standard SHOULD; Core when review required | MLV-App / DngAutoProcessor | AdversarialLLM: land durable unavailable reports or preserve a scoped unavailable blocker before promoting. |
| `automated-subagent-dispatch` | PARTIAL: queue/dispatch tests exist; transport still surface-specific. | PARTIAL: instructions support subagents; transport dependent. | PARTIAL/NO: optional transport, unavailable artifact gap. | Subagents accelerate adjudication but are not required. | Surface Plugin / Standard SHOULD | MLV-App | Later adapter docs for Codex Desktop, Codex CLI, Claude, CI, manual. |
| `agent-remediation-queue` | YES/PARTIAL: queue packets, dispatch, collection, scope checks. | YES/PARTIAL: queue packet/status/result modes and unavailable handling reported. | NO/PARTIAL: gap for durable unavailable artifact. | Agent queues are adapters around the core adjudication protocol. | Surface Plugin / Standard SHOULD | MLV-App / DngAutoProcessor | Split queue protocol from subagent execution in profiles. |
| `checked-out-and-locked-worktree-handling` | YES: stale locked clean cleanup and protected inspect-only tests. | PARTIAL: checked-out/locked handling reported. | PARTIAL: protected locked policy needs fixture tests. | Active/protected/dirty worktrees require evidence before cleanup. | Standard SHOULD | MLV-App | Keep Core invariant: ambiguous worktree mutation blocks. |
| `evidence-preserving-prune` | YES: dirty detached and prune recovery tests. | PARTIAL: policy/contract, exact-byte implementation to verify. | PARTIAL: dirty detached preservation actor/test gap. | Historical/non-ancestor/dirty deletion needs recovery evidence first. | Max SHOULD | MLV-App | Define minimum recovery bundle and byte-preservation requirements. |
| `remediation-freeze` | YES: marker/env, hook blocking, audit packets, removal quorum tests. | PARTIAL: already-integrated dirty branches now enter freeze and finalize/work-block completion block while frozen; unfreeze quorum, exact allowlist, and all hook-surface proof remain. | NO: no executable freeze policy. | Freeze is an advanced safety brake for lifecycle confusion. | Max SHOULD | MLV-App | DNG: prove unfreeze quorum, exact allowlist, and every hook surface before promoting to YES. |
| `dirty-split-automation` | YES/PARTIAL: split actors/tests exist, but profile-specific. | PARTIAL: dirty split reported as planned/advanced. | PARTIAL: optional dirty split automation. | Split automation is useful for owned dirty preservation. | Max SHOULD | MLV-App | Keep exact-path tuple and recovery requirements. |
| `runtime-service-lifecycle` | YES: stop/restart around clean promotion and repo-closed. | CONDITIONAL/PARTIAL: service lifecycle when services exist. | YES: runtime lifecycle tests reported. | Required only for repos with repo-owned runtime services. | Max / Conditional SHOULD | MLV-App / AdversarialLLM | Profiles should say declare services if present. |
| `remote-feature-clean-integration` | YES: remote/retained remediation support in reference behavior. | PARTIAL/YES: retained remote feature remediation actor supports clean integration/prune decisions. | YES/PARTIAL: strong remote refs, local parity gap. | Remote feature cleanup is powerful but not minimal core. | Max SHOULD | MLV-App / AdversarialLLM | Keep as Max/reference until portability is proven. |
| `historical-incident-traceability` | YES: Round -1 map committed locally. | YES: Round -1 map exists in DNG planning evidence. | YES: incident map report complete. | Framework capabilities must trace to historical scenarios. | Core MUST | Shared | Require this artifact in every repo adoption. |
| `requirements-trace-to-original-standard` | YES: `CLOSEOUT-REQUIREMENTS-TRACE.md` is present and mapped into the local ledger. | PARTIAL: recognized as Core, trace not final. | PARTIAL: trace explicitly requested before standard. | Old standard/addenda must map to capabilities or non-goals. | Core MUST | MLV-App | Port requirements trace artifacts before claiming local ledger completeness. |
| `capability-ledger-schema` | YES: local schema and ledger exist; ledger validates at `38 YES / 2 PARTIAL`. | PARTIAL: a populated ledger is reported at `33 YES / 7 PARTIAL`, but schema-validation parity was not reported. | PARTIAL: draft/local ledger-schema claims were reported, but the reconciliation patch was blocked before commit. | Compliance must be machine-readable. | Core MUST | MLV-App | Other repos: close the schema/ledger rows through committed, repo-owned validation evidence. |

## Draft Core MUST Rows

These rows appear Core across all three repos:

- `structured-adjudication-protocol`
- `declared-review-surface`
- `candidate-evidence-packet`
- `adjudication-report`
- `adjudication-to-symbolic-action-boundary`
- `repo-owned-mutation-after-adjudication`
- `exact-mutation-tuple`
- `bounded-wrapper-authority`
- `broker-manifest-dirty-baseline`
- `deterministic-work-block-selection`
- `dirty-classification`
- `foreign-dirty-preservation`
- `baseline-dirty-mixed-path-protection`
- `hard-clean-final-gate`
- `repo-closed-for-final-response`
- `advisory-hooks-non-authoritative`
- `response-hook-no-worktree-resurrection`
- `final-utility-generated-or-preclean`
- `tooling-drift-detection`
- `repo-sweep-read-only-planning`
- `repo-sweep-single-candidate-mutation`
- `audited-bulk-override`
- `no-force-push-target-recovery`
- `local-only-repo-closeout`
- `historical-incident-traceability`
- `requirements-trace-to-original-standard`
- `capability-ledger-schema`

## Draft Standard SHOULD Rows

- `target-push-race-recovery`
- `protected-target-noop`
- `retained-candidate-remediation`
- `surface-unavailable-or-insufficient-reviewer-block`
- `checked-out-and-locked-worktree-handling`
- independent review/quorum for high-impact ambiguity
- Git hook gates that feed authoritative blockers

## Draft Max / Conditional SHOULD Rows

- `automated-subagent-dispatch`
- `agent-remediation-queue`
- `evidence-preserving-prune`
- `remediation-freeze`
- `dirty-split-automation`
- `runtime-service-lifecycle`
- `remote-feature-clean-integration`

## Next Artifact Input

This matrix has fed `CLOSEOUT-FRAMEWORK-PROFILES.md`,
`CLOSEOUT-REQUIREMENTS-TRACE.md`, `CLOSEOUT-STANDARD.md`, and the local
capability ledger. It should remain the cross-repo comparison artifact as DNG
and AdversarialLLM emit their own ledgers.

Profiles should separate:

- portable Core invariants;
- Standard automation for serious multi-agent repos;
- Max/profile-specific scar tissue;
- Surface Plugins for Codex, Claude, CI, and manual review.

The matrix should be revisited after every repo emits or updates its capability
ledger. At that point, `YES`, `PARTIAL`, `NO`, and `UNKNOWN` should be backed by
exact config, loader, actor, adapter, contract, drift, test, and audit evidence.
