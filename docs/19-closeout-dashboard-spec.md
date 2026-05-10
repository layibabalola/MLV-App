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
bounded closeout history, dashboard settings, and rollback readiness.

Historical browsing comes from the repo-state history directory and the durable
audit log. The UI may summarize those artifacts, but the artifacts remain the
source of truth.

## UX Contract

The dashboard should auto-refresh through SSE with a polling fallback using
`webDashboardSpec.autoRefreshMs`. Refreshes preserve scroll position, focused
controls, selected work block, expanded detail rows, and active history filters.

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
snapshot, an explicit rollback plan, and recovery commands in every mutating
audit. `reset --hard` and force push are never default actions.
