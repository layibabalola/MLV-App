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
  monitor-claude-<session>.runtime.json (Claude Monitor heartbeat)
  presence-claude.runtime.json        (future multi-layer presence; not emitted in v1)
  presence-codex.runtime.json         (future multi-layer presence; not emitted in v1)
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
    pending-actions.json              (durable deferred bridge work ledger)
    execution-state.json              (active Codex-owned task state)
    response-debt-state.json          (Codex hook turn marker for response-debt checks)
    review-loop-state.jsonl           (review request/result/closeout guard state)
    reviewer-wait-state.jsonl         (background reviewer ETA/checkback guard state)
    peer-wait-claims.jsonl            (future Phase 18 waiting-on-peer claims)
    settings-parity.jsonl             (future Phase 18 both-scoped settings parity)
    relay-candidates.jsonl            (future Phase 18 mandatory relay candidates)
    diagnostic-claims.jsonl           (future Phase 18 root-cause claim evidence)
    guardrail-debt.jsonl              (future Phase 18 guardrail-debt events)
    implementation-journal.json       (durable shipped-progress catch-up log)
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
    dom-telemetry\                     (future promoted compact UI facts only)
      current-state.json
      ui-events.jsonl
      operation-results.jsonl
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
| `watcher-config.json` | bootstrap/configure_watcher | Watcher entries for private and project buckets. Private entries may set `session_id_source: "active_session"` so `session.json` remains the source of truth and the stored `session_id` is only a fallback snapshot. |
| `routing-rules.json` | routing rules CLI/MCP | Optional learned/suppressed routing policy. |
| `watcher.pid` | watcher/bootstrap compatibility | Legacy marker for the current watcher process. The lease is authoritative. |
| `watcher.runtime.json` | watcher | Runtime breadcrumb (role, PID, command, bridge_root, manifest identity); read by `bridge_process_status` and the `bridge_d` presence layer. |
| `peer-<agent>.runtime.json` | bootstrap | Peer identity breadcrumb per Phase B (`PHASE_B_BREADCRUMB_DESIGN.md`): session_id, desktop_thread_id, deeplink_template, written_by_pid. Source of truth for the `peer_breadcrumb` presence layer. |
| `monitor-claude-<session>.runtime.json` | Claude Monitor | Per-context Monitor heartbeat written by `bridge_monitor_poll.py`. Schema includes `{schema_version, agent, session_id, project, monitor_pid, started_at, heartbeat_at, context_generation, compacted_after_start, script_path, argv, watched_buckets, helper_hash, poll_interval_seconds, preexisting_target_unread, last_emit_at}`. Freshness TTL defaults to 30s or `3 * poll_interval_seconds`, whichever is larger. Health/dashboard/bootstrap use this evidence before claiming the Monitor is armed. |
| `presence-<agent>.runtime.json` | future bridge-d | Planned multi-layer presence record per `BRIDGE_PRESENCE_SPEC.md`: 10 layer states + overall verdict, recomputed every 30s. Not emitted by the current v1 watcher. |
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
| `pending-actions.json` | Durable deferred bridge work ledger. Stores Codex/Claude-owned follow-up items that must survive long turns and compaction. |
| `execution-state.json` | Active task state used by Codex reminders and pending-ledger drain checks. Schema v1: `{schema_version, agents: {<agent>: {active_task, updated_at}}}`; `active_task` includes id, summary, source, status, proof_status, and interrupt_mode. |
| `response-debt-state.json` | Codex hook turn marker used by `codex_bridge_reminder.ps1`. Current schema v1: `{schema_version, owner_agent, project, private_session, current_turn_started_at, updated_at}`. It lets final guards distinguish current-turn reads from old receipt history. |
| `review-loop-state.jsonl` | Append-only review-loop state used by WGI-09. Current schema v1 rows include `{schema_version, event_id, event_type, review_loop_id, request_message_id, peer_result_message_id, closeout_message_id, owner_agent, owner_session_id, peer_agent, session_id, subject, status, created_at}`. `event_type` is one of `review_requested`, `peer_replied`, `peer_result_handled`, or `closeout_sent`. The Codex final hook scopes to Codex-owned current-session loops and warns when a peer review result has been handled locally but no matching closeout row or WGI-09 pending action exists. |
| `reviewer-wait-state.jsonl` | Append-only background reviewer wait state used by WGI-11. Rows include `{schema_version, event_id, event_type, wait_id, owner_agent, owner_session_id, reviewer_id, request_id, subject, eta_at, checkback_due_at, result, status, note, created_at}`. The Codex final hook warns when a reviewer wait is active without an ETA/checkback, or when the scheduled checkback is due. Terminal statuses are `verdict_received`, `parked`, `blocked`, and `cancelled`. |
| `peer-wait-claims.jsonl` | Future Phase 18 waiting-on-peer claim stream. Planned rows include `{schema_version, claim_id, owner_agent, peer_agent, session_id, project, reason, created_at, monitor_required, monitor_evidence_id, status}`. WGI-04 reads this instead of inferring wait claims from prose. |
| `settings-parity.jsonl` | Future Phase 18 both-scoped settings parity stream. Planned rows include `{schema_version, setting_key, scope, agent, consumer_path, focused_test, peer_ack_message_id, status, updated_at}`. WGI-07 reads this instead of relying on free-form ACK text. |
| `relay-candidates.jsonl` | Future Phase 18 relay-candidate stream. Planned rows include `{schema_version, candidate_id, owner_agent, category, summary, source_turn_id, source_message_id, body_hash, created_at, status, sent_message_id, pending_action_id}`. WGI-06 reads this instead of inferring unsent decisions from prose alone. |
| `diagnostic-claims.jsonl` | Future Phase 18 root-cause evidence stream. Planned rows include `{schema_version, claim_id, owner_agent, defect_id, summary, artifact_refs, created_at, status}`. WGI-08 applies to registered bridge defect/root-cause claims, not arbitrary free-form chat. |
| `guardrail-debt.jsonl` | Future Phase 18 machine-readable guardrail debt stream. Planned rows include `{schema_version, debt_id, guard_id, severity, owner_agent, session_id, source_message_id, debt_status, detected_at, data_sources, remediation}`. |
| `implementation-journal.json` | Durable implementation progress journal used to generate coalesced `CATCHUP_DIGEST` control messages when a peer reconnects or clears backpressure. |
| `wake-failure-windows.json` | Per-session circuit-breaker state per `WAKE_HARDENING_SPEC.md` D2: rolling failure windows, breaker state, exit-code distribution metadata. |
| `cross-project-pairs/_pending.json` | Manual nonce observations for cross-project pairing plus hashed used-nonce replay cache. |
| `cross-project-pairs/<link_id>.json` | Cross-project pairing state per `CROSS_PROJECT_PAIRING_SPEC.md`: tier, expiration, advisor/executor, policy, and audit references. |
| `dom-telemetry/current-state.json` | Future compact reconciled UI/bridge state after DOM telemetry overhead and privacy gates pass. Planned fields include confidence, active thread evidence, wake observation, blocked reason, and evidence ids. |
| `dom-telemetry/ui-events.jsonl` | Future append-only compact UI fact stream. Intended for facts such as `thread_title_changed`, `wake_visible`, `composer_nonempty`, `agent_busy`, and `tool_error_seen`; raw transcript bodies are out of scope by default. |
| `dom-telemetry/operation-results.jsonl` | Future no-silent-success operation ledger keyed by `operation_id`, with process result plus independent artifact/receipt/UI evidence. |
| `*.quarantine.jsonl` | Malformed JSONL rows preserved by storage/recovery tools. |

DOM/UI telemetry profiling artifacts, raw UIA snapshots, screenshots, and OCR
outputs belong under `.claude-state/bridge-ui/` until the specific compact fact
schema passes the privacy and overhead gates in `DOM_TELEMETRY_USE_CASE_CATALOG.md`.

## Process State

`locks\` contains singleton/scoped daemon leases. `watcher.lock` is JSON and
contains `pid`, `command_line_hash`, `state_dir`, `started_at`, `heartbeat_at`,
and `generation`.

`watcher.pid` and `server-pids\server-<pid>.pid` remain compatibility PID
markers. `watcher.runtime.json` and `server-pids\server-<pid>.json` are
best-effort runtime breadcrumbs that record the process role, PID, resolved
bridge root, state directory, command, and manifest identity when available.
`bridge_process_status` reports these breadcrumbs and flags root mismatches as
attention-worthy even when the process is still alive. On Windows, MCP server
markers are also checked against the OS process creation time so stale markers
do not survive forever when Windows reuses an old server PID for an unrelated
process.

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
