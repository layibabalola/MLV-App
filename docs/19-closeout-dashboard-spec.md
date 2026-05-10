# Closeout Dashboard Spec

## Purpose

The closeout dashboard is a read-only operator view over repo-owned evidence. It
must not scrape ad hoc Git commands for authority and must not mutate repo state
directly. Its sticky local URL is `http://127.0.0.1:8765/closeout`.

## Data Contract

The primary feed is `.claude-state/closeout/repo-state/latest.json`, emitted by
`tools\repo_hygiene\work_block_cli.py repo-state --write`. The feed declares
`repo-state-snapshot.v1` and includes branch/tracking, dirty entries, local
branches, worktrees, stashes, latest `closeoutCleanTruth`, audit pointers,
bounded `closeout-history-index.v1`, dashboard settings, and
`rollback-readiness.v1`.

Live refresh uses
`pwsh.exe -NoLogo -NoProfile -NonInteractive -ExecutionPolicy Bypass -File tools\closeout\write-repo-state.ps1 -RepoRoot . -Write -LatestOnly`. That
command updates only the stable latest feed so a dashboard polling every few
seconds does not create synthetic closeout history or audit noise. Full closeout
and explicit audit captures should continue to use the normal history-writing
mode.

`latest.json` is a mutable display feed. It must not be used as rollback
evidence. Rollback panels may show readiness only from the fail-closed
`rollback-readiness.v1` payload and must require immutable history/source
snapshot evidence before presenting any action as executable.

Historical browsing comes from the repo-state history directory and the durable
audit log. The UI may summarize those artifacts, but the artifacts remain the
source of truth.

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
- `/api/closeout/events`: SSE refresh stream for clients that prefer events,
  with polling remaining as the fallback path

## UX Contract

The dashboard should auto-refresh through SSE with a polling fallback using
`webDashboardSpec.autoRefreshMs`. Refreshes preserve scroll position, focused
controls, selected work block, expanded detail rows, and active history filters.
The snapshot exposes these as `preservedClientStateKeys` so clients do not have
to infer state names.

Primary panels:

- repo-map: branches, worktrees, stashes, dirty files, and target/upstream state
- workflow-lane: closeout stages, current blocker, retries, and final authority
- blocker-queue: retained candidates, owner/classification, and recovery command
- audit-timeline: current and historical closeout events with audit hashes
- rollback-readiness: feasible strategies, required approvals, and evidence roots

## Mutation Boundary

The dashboard mutation model is `symbolic-action-request-only`. Buttons may draft
requests such as rollback, retained remediation, or repo-state refresh, but a
repo-owned actor must revalidate the exact tuple before anything changes.

Rollback defaults to a new work block, user approval, a pre-mutation repo-state
snapshot, immutable source snapshot evidence, a
`closeout-rollback-manifest.v1`, an explicit rollback plan, and recovery
commands in every mutating audit. `reset --hard` and force push are never
default actions. Until a repo-owned rollback actor validates that manifest,
actionability remains `read-only-no-actor`.
