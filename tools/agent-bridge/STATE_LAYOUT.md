# Agent Bridge State Layout

Default state root:

```text
%USERPROFILE%\.agent-bridge\
  bridge-root.json
  MOVED_TO.json                       (only after this root is relocated)
  machine.json                        (forward-compat: stable per-machine UUID)
  session.json
  settings.json
  watcher-config.json
  routing-rules.json
  watcher.pid
  watcher.runtime.json
  peer-claude.runtime.json            (Phase B; written by bootstrap)
  peer-codex.runtime.json             (Phase B; written by bootstrap)
  presence-claude.runtime.json        (multi-layer presence; written by bridge-d)
  presence-codex.runtime.json         (multi-layer presence; written by bridge-d)
  pause.json                          (D1 wake hardening; only when paused)
  state\
    state.json
    messages.jsonl
    inbox-claude.jsonl
    inbox-codex.jsonl
    orphaned-claude.jsonl
    orphaned-codex.jsonl
    pending_peer_absent.jsonl         (presence exit code 4 deferred queue)
    pending_bootstrap.jsonl           (presence exit code 5 deferred queue)
    pending_busy.jsonl                (presence exit code 10 deferred queue)
    wake-failure-windows.json         (D2 circuit breaker per-session state)
    cross-project-pairs\              (cross-project pairing state, when active)
      _pending.json                   (manual nonce observations and replay hashes)
      <link_id>.json
    locks\
      watcher.lock
      migration.lock                  (held during migrate_root.py apply)
    server-pids\
      server-<pid>.pid
      server-<pid>.json
    backups\
      recovery-<timestamp>\
    tenants\                          (forward-compat for multi-tenant cloud; absent in v1)
      <tenant_id>\
        ...                           (per-tenant scoped subset of the above)
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
| `machine.json` | bootstrap (forward-compat) | Stable per-machine UUID + bootstrap timestamp; populates `originator_machine_id` field on outbound rows for future LAN/cloud conflict resolution. Optional in v1; required for v2/v3. |
| `session.json` | bridge session lifecycle | Active and historical Claude/Codex sessions per project. Source of truth for `bridge_bootstrap` and `active_peer` presence layers. |
| `settings.json` | user/runtime config | Optional supported tuning surface documented in `SETTINGS.md`. |
| `watcher-config.json` | bootstrap/configure_watcher | Static watcher entries for private and project buckets. |
| `routing-rules.json` | routing rules CLI/MCP | Optional learned/suppressed routing policy. |
| `watcher.pid` | watcher/bootstrap compatibility | Legacy marker for the current watcher process. The lease is authoritative. |
| `watcher.runtime.json` | watcher | Runtime breadcrumb (role, PID, command, bridge_root, manifest identity); read by `bridge_process_status` and the `bridge_d` presence layer. |
| `peer-<agent>.runtime.json` | bootstrap | Peer identity breadcrumb per Phase B (`PHASE_B_BREADCRUMB_DESIGN.md`): session_id, desktop_thread_id, deeplink_template, written_by_pid. Source of truth for the `peer_breadcrumb` presence layer. |
| `presence-<agent>.runtime.json` | bridge-d | Multi-layer presence record per `BRIDGE_PRESENCE_SPEC.md`: 10 layer states + overall verdict, recomputed every 30s. |
| `pause.json` | `pause_bridge` MCP tool | When present, signals bridge-d to skip `on_message_command` invocations. Schema: `{schema_version, paused, paused_at, paused_by_session, scope}`. Deleted by `resume_bridge`. |

## `state\` Files

| Path | Purpose |
|---|---|
| `state.json` | Pause flag (legacy; superseded by `pause.json` at root for v1+), per-bucket dedupe/rate-limit/session metadata. |
| `messages.jsonl` | Audit log for sends, reads, receipts, sessions, compaction, recovery, presence layer transitions, and wake events. See `BRIDGE_PROTOCOL.md` for the audit event taxonomy. |
| `inbox-<agent>.jsonl` | Durable inbox rows for Claude and Codex. Each row carries `to`, `from`, `session_id`, `parent_project`, receipt fields, and (forward-compat) `tenant_id` + `originator_machine_id`. |
| `orphaned-<agent>.jsonl` | Orphan-handling path per `HIERARCHICAL_INBOX_SPEC.md`: messages whose target session no longer exists are routed here for agent-level recovery. Absent when unused. |
| `pending_peer_absent.jsonl` | Deferred queue for presence exit code 4 (peer process missing). Drained on os_process layer rejoin. Capped at 100 entries; FIFO eviction with audit. |
| `pending_bootstrap.jsonl` | Deferred queue for presence exit code 5 (peer not bootstrapped). Drained on bridge_bootstrap layer transition to ok. |
| `pending_busy.jsonl` | Deferred queue for presence exit code 10 (peer busy in long task). Drained on next `check_inbox` audit event by target agent. |
| `wake-failure-windows.json` | Per-session circuit-breaker state per `WAKE_HARDENING_SPEC.md` D2: rolling failure windows, breaker state, exit-code distribution metadata. |
| `cross-project-pairs/_pending.json` | Manual nonce observations for cross-project pairing plus hashed used-nonce replay cache. |
| `cross-project-pairs/<link_id>.json` | Cross-project pairing state per `CROSS_PROJECT_PAIRING_SPEC.md`: tier, expiration, advisor/executor, policy, and audit references. |
| `*.quarantine.jsonl` | Malformed JSONL rows preserved by storage/recovery tools. |

## Process State

`locks\` contains singleton/scoped daemon leases. `watcher.lock` is JSON and
contains `pid`, `command_line_hash`, `state_dir`, `started_at`, `heartbeat_at`,
and `generation`.

`watcher.pid` and `server-pids\server-<pid>.pid` remain compatibility PID
markers. `watcher.runtime.json` and `server-pids\server-<pid>.json` are
best-effort runtime breadcrumbs that record the process role, PID, resolved
bridge root, state directory, command, and manifest identity when available.
`bridge_process_status` reports these breadcrumbs and flags root mismatches as
attention-worthy even when the process is still alive.

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
path. Migration output includes wrapper-based Claude/Codex Desktop MCP config
snippets for the target root and a read-only target validation report from
`recover_state.py --scan-historical`; update Desktop configs and restart both
clients after applying a root move.
