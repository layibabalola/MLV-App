# Agent Bridge State Layout

Default state root:

```text
%USERPROFILE%\.agent-bridge\
  bridge-root.json
  MOVED_TO.json          (only after this root is relocated)
  session.json
  settings.json
  watcher-config.json
  routing-rules.json
  watcher.pid
  state\
    state.json
    messages.jsonl
    inbox-claude.jsonl
    inbox-codex.jsonl
    orphaned-claude.jsonl
    orphaned-codex.jsonl
    locks\
      watcher.lock
    server-pids\
      server-<pid>.pid
    backups\
      recovery-<timestamp>\
```

The root above is the default when neither `--bridge-root` nor
`AGENT_BRIDGE_ROOT` is provided. New bridge CLIs should prefer `--bridge-root`;
`--state-dir` remains available as a legacy/advanced compatibility flag.

Phase 13 root resolution is landing incrementally. The current resolver derives
all root files from one `BridgePaths` object, rejects stale roots with
`MOVED_TO.json`, and follows bounded redirect chains with cycle detection.
Full migration tooling is still tracked in `REFACTOR_PLAN.md`.

## Root Files

| Path | Owner | Purpose |
|---|---|---|
| `bridge-root.json` | bridge root resolver | Active root manifest with stable `root_id`, schema version, and migration history. |
| `MOVED_TO.json` | migration tooling | Stale-root redirect. Startup against a moved root fails loudly with the active root path. |
| `session.json` | bridge session lifecycle | Active and historical Claude/Codex sessions per project. |
| `settings.json` | user/runtime config | Optional supported tuning surface documented in `SETTINGS.md`. |
| `watcher-config.json` | bootstrap/configure_watcher | Static watcher entries for private and project buckets. |
| `routing-rules.json` | routing rules CLI/MCP | Optional learned/suppressed routing policy. |
| `watcher.pid` | watcher/bootstrap compatibility | Legacy marker for the current watcher process. The lease is authoritative. |

## `state\` Files

| Path | Purpose |
|---|---|
| `state.json` | Pause flag, per-bucket dedupe/rate-limit/session metadata. |
| `messages.jsonl` | Audit log for sends, reads, receipts, sessions, compaction, and recovery actions. |
| `inbox-<agent>.jsonl` | Durable inbox rows for Claude and Codex. |
| `orphaned-<agent>.jsonl` | Future orphan handling path; absent when unused. |
| `*.quarantine.jsonl` | Malformed JSONL rows preserved by storage/recovery tools. |

## Process State

`locks\` contains singleton/scoped daemon leases. `watcher.lock` is JSON and
contains `pid`, `command_line_hash`, `state_dir`, `started_at`, `heartbeat_at`,
and `generation`.

`server-pids\` contains observation-only MCP server markers. Multiple markers are
valid because each MCP client or probe may spawn its own `server.py`.

## Maintenance

Use `recover_state.py` to validate state and optionally repair corruption after a
backup. Use `compact.py` to prune old read rows, rotate oversized audit logs, and
reap stale `server-pids\server-<pid>.pid` markers.

Use `recover_state.py --scan-historical` when clients appear split across bridge
roots or repeated wake nudges are ignored. The scan is read-only and reports
stale-root redirects, unreadable or mismatched `bridge-root.json` manifests, and
partial migrations where the target manifest names a source root that lacks a
matching `MOVED_TO.json`.

Use `migrate_root.py` for bridge-root relocation. It is dry-run by default,
refuses live watcher/MCP server markers unless `--force-while-running` is
provided, and holds a `state\locks\migration.lock` singleton lease while
applying. By default it refuses symlink/reparse targets and requires the old
root to be writable so it can write `MOVED_TO.json`; `--allow-reparse-target`
and `--skip-redirect` are explicit risk-acceptance flags. Apply mode copies the
source root to the target, rewrites watcher inbox paths, and writes
`MOVED_TO.json` at the old root so stale clients fail with the new active root
path.
