# Agent Bridge State Layout

Default state root:

```text
%USERPROFILE%\.agent-bridge\
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

## Root Files

| Path | Owner | Purpose |
|---|---|---|
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
