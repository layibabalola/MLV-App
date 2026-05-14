# Closeout Dashboard Spec

## Purpose

The closeout dashboard is a read-only-by-default, read-first operator view over
repo-owned evidence. It must not scrape ad hoc Git commands for authority and
must not mutate repo state directly. Read-only preview and dry-run explanations
are allowed when they are derived from repo-owned truth and do not become a
second mutation authority. Its sticky local URL is
`http://127.0.0.1:8765/closeout`.

## Operator Phases

The dashboard should present the closeout flow as four explicit phases:

- Inspect: evidence only
- Preview: explain consequences and blockers
- Request: record durable symbolic intent
- Apply: repo-owned actor only, behind gates, outside dashboard authority

## Data Contract

The primary feed is `.claude-state/closeout/repo-state/latest.json`, emitted by
`tools\repo_hygiene\work_block_cli.py repo-state --write`. The feed declares
`repo-state-snapshot.v1` and includes branch/tracking, dirty entries, local
branches, worktrees, stashes, latest `closeoutCleanTruth`, audit pointers,
bounded `closeout-history-index.v1`, dashboard settings, and
`rollback-readiness.v1`.
It also includes `worktreeInspection` with `worktree-inspection.v1`: the raw
linked-worktree inventory, whether the current repo root was present, ordinary
linked sibling counts, protected linked worktree counts, and fail-closed
inspection errors. The dashboard may visualize protected worktrees, but ordinary
linked siblings are closeout blockers until merged/pruned or explicitly retained
by repo-owned evidence.

Live refresh uses
`pwsh.exe -NoLogo -NoProfile -NonInteractive -ExecutionPolicy Bypass -File tools\closeout\write-repo-state.ps1 -RepoRoot . -Write -LatestOnly`. That
command updates only the stable latest feed so a dashboard polling every few
seconds does not create synthetic closeout history or audit noise. Full closeout
and explicit audit captures should continue to use the normal history-writing
mode.

The configured refresh command policy is
`repo-owned-write-repo-state-latest-only`. Any configured refresh command that
does not resolve to `tools\closeout\write-repo-state.ps1 -Write -LatestOnly`
must fail closed rather than being surfaced through dashboard metadata.

`latest.json` is a mutable display feed. It must not be used as rollback
evidence. Rollback panels may show readiness only from the fail-closed
`rollback-readiness.v1` payload, and cleanup or rollback preview panels may use
read-only repo-owned truth, but executable actions still require immutable
history/source snapshot evidence plus repo-owned actor revalidation.

Historical browsing comes from the repo-state history directory and the durable
audit log. The UI may summarize those artifacts, but the artifacts remain the
source of truth. When available, the dashboard should also surface a short
round-delta note so multiple repos can compare closeout workflow changes side
by side and keep cross-repo comparison meaningful without reconstructing them
from chat. This canonical spec is itself part of the machine-checked baseline;
the tracked round-delta note should live in `CLOSEOUT-CROSS-MAP-COMPARISON.md`
or an equivalent durable repo note.

## Local Helper

Start or reuse the local helper with
`pwsh.exe -NoLogo -NoProfile -NonInteractive -ExecutionPolicy Bypass -File tools\closeout\start-closeout-dashboard.ps1 -RepoRoot .`. The helper is
localhost-only, binds the sticky dashboard at `http://127.0.0.1:8765/closeout`,
and refuses to reuse the port when `/api/closeout/actions` reports a different
`repoRoot`. A healthy same-repo helper is reused rather than launched twice.

Required endpoints:

- `/api/closeout/repo-state/latest`: refreshes and returns the latest-only
  generated feed
- `/api/closeout/repo-state/history-index`: returns the bounded
  `closeout-history-index.v1`
- `/api/closeout/repo-state/history/{snapshotId}`: returns one immutable history
  snapshot by file id after path-safety checks
- `/api/closeout/actions`: returns `closeout-dashboard-actions.v1` with
  `serverProcessId`, repo ownership, endpoint metadata, symbolic actions, and
  fail-closed rollback actionability
- `/api/closeout/actions/preview`: returns a non-mutating explanation or dry-run
  summary for a symbolic action such as retained remediation or rollback; it
  may summarize `repoClosedPostcondition.closeoutCleanTruth`, retained-candidate
  reports, rollback readiness, and request-template fields, but it must not
  write repo-closed artifacts or mutate refs/worktrees/stashes/source
- `/api/closeout/actions/requests`: returns `closeout-dashboard-action-request-history.v1` and
  exposes immutable symbolic request records written under `.claude-state/closeout/dashboard-action-requests/`;
  it must report top-level `status` as `empty`, `ready`, or `partial`, include
  `displayedRequestCount`, `totalRequestCount`, `malformedCount`, and
  `truncated`, and preserve malformed rows instead of hiding them
- `/api/closeout/actions/request`: records symbolic action intent as generated
  packets under `.claude-state/closeout/dashboard-action-requests/` after helper
  freshness, action id, preview-token/repo-state-hash binding, and non-empty
  exact tuple validation; it rejects missing/stale/future helper timestamps,
  mismatched helper process ids, stale preview bindings, and request roots that
  resolve outside generated state; it does not mutate repo refs, worktrees,
  stashes, or source files
- `/api/closeout/events`: SSE refresh stream for clients that prefer events,
  with polling remaining as the fallback path

## UX Contract

The dashboard should auto-refresh through SSE with a polling fallback using
`webDashboardSpec.autoRefreshMs`. Refreshes preserve scroll position, focused
controls, selected action/work block, expanded detail rows, and active history
filters. The snapshot exposes these as `preservedClientStateKeys` so clients do
not have to infer state names. The first-party page persists each configured key
in browser storage before refresh and restores it after the feed updates. The
current action selection is mirrored into the URL as `?actionId=<id>` for sticky
deep-linking across refreshes.

Primary panels:

- repo-map: branches, worktrees, stashes, dirty files, and target/upstream state
- workflow-lane: closeout stages, current blocker, retries, and final authority
- workflow-comparison: round-delta notes and side-by-side closeout workflow deltas across histories
- blocker-queue: retained candidates, owner/classification, and recovery command
- action-preview: read-only explanation of cleanup/rollback consequences and, when exact-tuple requirements are known, an inline queue action that writes immutable symbolic request packets for operator approval workflows. It must surface safeguards and exact-tuple inputs before a request is queued.
- action-request-history: immutable request ledger rows (`createdAt`, `actionId`,
  `requestId`, `status`, `requestPath`, `requestHash`) plus a visible summary
  of readiness/truncation/malformed-row counts from
  `.claude-state/closeout/dashboard-action-requests/` for auditability and handoff
- audit-timeline: current and historical closeout events with audit hashes
- rollback-readiness: feasible strategies, required approvals, and evidence roots

## Mutation Boundary

The dashboard mutation model is `symbolic-action-request-only`. Buttons may
preview or explain actions such as rollback, retained remediation, or repo-state
refresh, and they may draft symbolic requests, but a repo-owned actor must
revalidate the exact tuple before anything changes. `/api/closeout/actions`
must include the command policy, actionability reason, and exact-tuple
requirements so the UI can explain why an action is read-only instead of hiding
the control. `/api/closeout/actions/preview` should explain likely cleanup or
rollback consequences before any request packet is recorded. Request packets
must echo the preview token and preview repo-state hash they were reviewed
against so the server can reject queue attempts after state drift.

If finalize discovers repo-owned dirty state on the protected target branch
without an explicit work block, the repo-owned closeout path should first
preserve that exact dirty state onto an allowed work-block branch and
materialize a manifest there before it reports the remaining blocker. The
dashboard should treat that recovery as repo-owned lifecycle behavior, not as a
direct UI mutation.

Rollback defaults to a new work block, user approval, a pre-mutation repo-state
snapshot, immutable source snapshot evidence, a
`closeout-rollback-manifest.v1`, an explicit rollback plan, and recovery
commands in every mutating audit. `reset --hard` and force push are never
default actions. Until a repo-owned rollback actor validates that manifest,
actionability remains `read-only-no-actor`. Rollback itself is still a mutating
action once that actor exists and the user approves the plan.
The dashboard may expose the read-only validator command
`tools\closeout\validate-rollback-manifest.ps1`, but it must not execute a
rollback actor. Validator results use
`closeout-rollback-manifest-validation.v1`; manifests live under
`.claude-state/closeout/rollback`, reject `latest.json` and `current.json`,
bind `sourceSnapshotHash` to the repo-state snapshot hash scope, require
explicit `sourceSnapshotAuditHash` and `repoClosedAuditHash`, require matching
`repo_state_snapshot` audit evidence, integrity-check audit hashes and sidecar
JSON, require the source snapshot and repo-closed audit to share `workBlockId`,
and reject forbidden recovery commands. Rollback symbolic request packets must
include the full manifest-binding tuple, including `sourceSnapshotPath` and
`recoveryCommand`.
