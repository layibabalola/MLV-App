# Repo Hygiene Policy

This policy is repo-local. It does not copy another repository's paths or
branch rules. It exposes the portable safety behavior used by
`tools/repo_hygiene` so config, docs, tests, and implementation cannot drift.

## Sensitive Roots

- `.claude/` is curated durable agent context. Do not create scratch files there.
- `.claude/worktrees/` is load-bearing. Unregistered is not enough evidence for
  deletion.
- `.claude-state/repo-hygiene/` is the hygiene state root for generated
  inventories, run artifacts, mutexes, quarantine manifests, and stash
  promotion worktrees.

The policy config also carries a root registry for sensitive and special roots:
`.claude/`, `.claude-state/`, `.claude/worktrees/`, `tools/agent-bridge/`,
`tools/repo_hygiene/`, `tools/repo-hygiene/`, `docs/`, `.github/workflows/`,
`tests/`, vendored or external library roots, and ignored build outputs. The
verifier fails if the core hygiene roots disappear from that registry.

The verifier also checks tracked files that match `.gitignore`. Existing legacy
exceptions are narrow and explicit: `osx_installer/BuildInstaller.sh` and
`platform/mlv_blender/build.sh`. New tracked ignored files must either be
removed from tracking or deliberately added to the allowlist with review.

The verifier also uses positive and negative ignore samples. Runtime locations
such as `.claude-state/`, `.claude/worktrees/`, `.hypothesis/`, Python
`__pycache__`, root runtime breadcrumbs, and the extracted local FFmpeg binary
must remain ignored. Source, policy, workflow, and test files must not be hidden
by ignore rules.

## Risk Tiers

- `R0`: report-only facts and retained decisions.
- `R1`: generated hygiene state under the configured state root.
- `R2`: Git-safe reversible local cleanup, such as archived local branch
  deletion or clean registered worktree removal.
- `R3`: filesystem quarantine of a direct, eligible orphan directory candidate.
- `R4`: manual-only ambiguous state, including dirty files and stashes.
- `R5`: never allowed by hygiene automation, including forced resets, broad
  cleanups, remote deletions, dirty/locked worktree deletion, and protected
  branch mutation.

Every candidate emits a risk tier, allowed actions, explicit approval
requirement, preflight requirements, recovery path, and a never allowed reason
when blocked.

## Candidate ID Rules

Every generated report, dirty-file triage group, branch, worktree, orphan
directory, and stash gets a stable candidate ID. Apply commands accept candidate
IDs only. Dashboard controls use symbolic action IDs only and never execute
generated shell strings.

## Structured Artifacts

Each scan or apply writes `facts.json`, `plan.json`, `result.json`, and
`summary.md` under `.claude-state/repo-hygiene/runs/<run-id>/`. Artifacts include
schema version, policy hash, evidence hash, candidate IDs, decisions, preflight
results, commands invoked, outcomes, skipped/retained rationale, and recovery
hints.

Closeout transactions write their own audit trail under
`.claude-state/repo-hygiene/transactions/<tx-id>/`: `decision-packet.json`,
`codex-closeout-recommendation.json`, `agent-review-*.json`, `approval.json`,
`approval-anchor.json`, `apply-validation.json`, `state.json`, `events.jsonl`,
and a public nonce-hash receipt. The plaintext trusted approval nonce is returned once to the trusted
caller and is not stored in transaction artifacts. Signed provenance uses
role-specific plaintext provenance keys returned once to the trusted caller;
transaction artifacts store only role-specific provenance key hashes and HMAC
signatures, so a recommendation key cannot sign an approval artifact. The final
approval event anchor is signed with the approval key and rechecked by
`validate-apply`. CLI
adapters must pass approval/provenance secrets through environment variables,
not argv. Events are
hash-chained through `previous_event_hash` and `event_hash`. The decision packet
uses closeout candidate kinds `closeout-transaction`, `commit-unit`,
`merge-readiness`, `publish-target`, and `prune-after-publish`.

## Dirty-File Triage

Dirty-file triage is recommendation-backed. The scanner groups dirty paths and
recommends `commit`, `split`, `stash`, `ignore/generated`, or `ask`, with
confidence and evidence. Evidence may include current work paths, generated-path
patterns, source/test/config/doc classification, recent commit path overlap, and
branch keyword overlap. Retaining or ignoring for now is a completed decision
when the evidence supports it.

## Symbolic Actions

`repo_hygiene_prune_old_runs` is the only dashboard-safe direct action in the
initial policy. `branch_archive_delete`, `worktree_remove`, `orphan_quarantine`,
and `stash_promote` are explicit CLI candidate actions and require candidate IDs.
Stashes are never dropped by this policy.

Closeout action IDs are symbolic too: `commit_unit_commit`, `publish_pr`,
`publish_direct_branch`, `local_merge`, and `prune_after_publish`. A Codex review
may recommend those actions, but it must not emit command, shell, script, or raw
path instructions. Dashboard controls and trusted adapters pass symbolic IDs and
candidate IDs only.

## Closeout Transactions

A closeout transaction is the repo-hygiene workflow for treating a branch as a
work-block transaction. It starts with a read-only decision packet, then requires
a data-only Codex review, two read-only stranger agent reviews, and trusted
approval before any apply executor is allowed to run. The trusted approval comes
from Codex Desktop, a dashboard trusted adapter, or a local interactive CLI
adapter and must echo the transaction nonce. Review waivers are disabled by this
repo policy; two distinct reviews for the current recommendation hash are
required. Review artifacts must declare an allowed review source such as
`codex_background_agent`; approval artifacts must declare a trusted approval
source such as `codex_desktop`, `dashboard_trusted_adapter`, or
`local_interactive_cli`.

Publish safety is enforced from config, not only documented. `pr_only` and
`direct_push_branch` require an allowed publish remote, `direct_push_branch`
also requires a configured branch pattern such as `codex/*`, and no publish mode
may run from a protected branch. `no_publish` accepts no publish remote.

Pre-apply validation re-reads the repo immediately before mutation. It blocks if
the policy hash, decision packet hash, recommendation hash, approval hash, HEAD,
current branch, integration base, dirty status, registered worktree state, stash
state, dirty-file hashes, or recommended hygiene candidate evidence changed.

The auto trigger is read-only except for opening transaction state under
`.claude-state/`. It can fire when configured signals cross the threshold:
`dirty_current_work`, `dirty_generated_only`,
`clean_feature_branch_ready_to_publish`, or `hygiene_cleanup_recommendations`.
Opening a transaction pulls Codex into the workflow; it does not commit, merge,
push, delete a branch, remove a worktree, quarantine a directory, or drop a
stash.

## Portability Verifier

`hygiene.py verify-policy` fails if implemented risk tiers, candidate kinds,
action IDs, dashboard action IDs, closeout transaction kinds, closeout action
IDs, auto trigger signal IDs, docs, or tests are not represented by portable
config and the machine-readable `closeout.contract.json`. The contract records
artifact names, CLI subcommands, states, publish modes, action IDs, trigger
signals, review sources, approval sources, waiver policy, and the executor
boundary. Local hardening must update the config, shared loader, verifier, docs,
contract, and tests in the same work block.
