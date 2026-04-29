# Agent Bridge - Multi-Layer Presence Spec

**Status:** Proposed
**Authors:** Claude (proposal); Codex review pending
**Replaces:** the previously-proposed `PROCESS_PRESENCE_SPEC.md` (never written; superseded by this richer model)
**Motivation:** "is the peer there?" is not a single boolean. The 2026-04-28 incidents (no-nudge from Phase A title-marker fail-closed; messages marked seen when Codex Desktop wasn't running and never re-delivered when it returned; pause_bridge not gating wake) all stem from collapsing many distinct presence axes into one signal. This spec models presence as a stack of independently-checkable layers, defines the schema and detection mechanism for each, and specifies how the watcher routes wake decisions based on which layer is failing.

---

## Problem

Today's wake mechanism conflates several signals into one yes/no decision:

1. `wake_codex.ps1` line 121-124: if `Get-CodexWindow` returns null, the script `exit 0` (success). Watcher marks the message seen. When the user reopens Codex hours later, the queue is silently empty.
2. The watcher has no view of "is the agent bootstrapped?" or "is the bridge daemon running?" — only "did the wake script return zero?"
3. Cross-project pairing (in flight) introduces yet another presence axis ("is this link active?") that's invisible to today's wake path.
4. Multi-tenant cloud (future) adds tenant-scope as a presence concern.

The unified presence model below treats each as an independent layer with its own detection method, failure semantics, and recovery path. The watcher computes presence before firing wake; sender's UX reports the precise failed layer instead of a generic "delivery failed."

---

## Layer Catalog

| # | Layer | Owner | What "ok" means |
|---|---|---|---|
| 1 | `os_process` | OS | `claude.exe` / `codex.exe` is running, owned by current user (SID match) |
| 2 | `bridge_bootstrap` | bridge registry | Agent has an active session in `session.json` |
| 3 | `active_peer` | bridge registry | Our session is the most recent for this agent (not superseded) |
| 4 | `bridge_d` | watcher process | Watcher PID alive, lease heartbeat fresh (<60s) |
| 5 | `mcp_server` | server.py subprocess | `server-<pid>.json` runtime breadcrumb mtime fresh, PID alive |
| 6 | `peer_breadcrumb` | bootstrap_session.py | `peer-<agent>.runtime.json` `written_at` fresh, `written_by_pid` alive, `bridge_root` matches |
| 7 | `project_scope` | configure_watcher | Bootstrapped project rendezvous matches the message's `parent_project` |
| 8 | `tenant_scope` | auth (cloud) | JWT `tenant_id` claim matches the message's `tenant_id` (forward-compat: local-default for single-tenant) |
| 9 | `active_pairing` | handshake / cross-project links | Same-project: peer ack'd HANDSHAKE; cross-project: link in active tier, not expired/revoked |
| 10 | `receptive` | inbox-check audit log | Last `check_inbox` audit event <2min ago; not stuck in a long tool call |

**Required vs Supportive layers:**

- **Required (critical):** os_process, bridge_bootstrap, active_peer, project_scope, tenant_scope, active_pairing — failure means delivery is impossible or unauthorized
- **Supportive (degraded):** bridge_d, mcp_server, peer_breadcrumb, receptive — failure means delivery is degraded but not impossible

---

## Detection Methods

| Layer | Mechanism | Cost | Failure mode |
|---|---|---|---|
| os_process | `Get-Process` (PowerShell) or `psutil.process_iter()` (Python); filter by name + owner SID | ~10ms | Process not in list, OR process owner SID differs from current user |
| bridge_bootstrap | Read `session.json`; check `agents.<agent>.active_session` is set | ~5ms (cached read) | Field missing, null, or expired beyond `BOOTSTRAP_TTL_SECONDS` (default 24h) |
| active_peer | Read `session.json`; check our self-known session_id == `agents.<agent>.active_session` | ~5ms | Mismatch → superseded |
| bridge_d | Stat `state/locks/watcher.lock`; check pid alive + `heartbeat_at` < 60s old | ~10ms | Lock missing, PID dead, or stale heartbeat |
| mcp_server | Glob `state/server-pids/server-*.json`; for each, check PID alive + breadcrumb mtime <300s | ~10ms (per check) | No live server marker for our agent's MCP client |
| peer_breadcrumb | Read `peer-<agent>.runtime.json`; validate schema, check `written_at` <60s, `written_by_pid` alive, `bridge_root` matches caller's bridge_root | ~10ms | File missing, stale write, dead writer PID, or root mismatch |
| project_scope | Read active session's recorded project; compare to message `parent_project` | ~5ms | Mismatch |
| tenant_scope | (cloud only) Validate JWT `tenant_id` claim against message's `tenant_id` | ~5ms | Invalid signature, expired, or claim mismatch |
| active_pairing | (same-project) `session.json` shows both peers' handshakes ack'd; (cross-project) read `state/cross-project-pairs/<link_id>.json` for tier + expiration | ~5ms | Handshake never ack'd, link expired, link revoked, or tier insufficient for the requested op |
| receptive | Tail `messages.jsonl` (last N entries); look for recent `check_inbox` or `wait_inbox` event by this agent | ~5ms | No event found in last 120s window |

**Total presence computation:** ~60ms full check. Cache in `presence-<agent>.runtime.json` with 30s TTL; recompute on any send, on any layer-failure event, or on TTL expiry.

---

## Schema

**Path:** `<bridge-root>/presence-<agent>.runtime.json`

**Schema v1:**

```json
{
  "schema_version": 1,
  "tenant_id": "local-default",
  "machine_id": "8a6e1c-machine-uuid",
  "agent": "codex",
  "checked_at": "2026-04-29T05:00:00+00:00",
  "next_recompute_at": "2026-04-29T05:00:30+00:00",
  "layers": {
    "os_process": {
      "state": "ok",
      "pid": 14328,
      "process_owner_sid": "S-1-5-21-..."
    },
    "bridge_bootstrap": {
      "state": "ok",
      "session_id": "7ec4a663-a027-4922-9c68-50067da1994c",
      "bootstrapped_at": "2026-04-29T04:34:00+00:00"
    },
    "active_peer": {
      "state": "ok"
    },
    "bridge_d": {
      "state": "ok",
      "pid": 100068,
      "last_heartbeat": "2026-04-29T05:00:28+00:00"
    },
    "mcp_server": {
      "state": "ok",
      "pid": 102756,
      "breadcrumb_mtime": "2026-04-29T04:59:50+00:00"
    },
    "peer_breadcrumb": {
      "state": "ok",
      "written_at": "2026-04-29T04:34:00+00:00",
      "written_by_pid": 14328,
      "pid_alive": true
    },
    "project_scope": {
      "state": "ok",
      "active_project": "mlv-app"
    },
    "tenant_scope": {
      "state": "ok",
      "active_tenant": "local-default"
    },
    "active_pairing": {
      "state": "ok",
      "handshake_ack_at": "2026-04-29T04:34:30+00:00",
      "link_id": null,
      "link_tier": null,
      "link_expires_at": null
    },
    "receptive": {
      "state": "ok",
      "last_inbox_check": "2026-04-29T04:59:45+00:00"
    }
  },
  "overall": "ok"
}
```

**State enum per layer:** `ok` | `degraded` | `missing` | `error`. Layer-specific sub-states (e.g. `superseded`, `expired`, `revoked`, `wrong_project`, `wrong_tenant`, `busy_long_task`, `busy_pre_response_hook`) are encoded in additional fields when relevant.

**`overall` computation:**

- Any required layer in `missing` or `error` → `critical`
- Any required layer in `degraded`, OR any supportive layer in `missing`/`error` → `degraded`
- All required `ok` and all supportive `ok` → `ok`

---

## Decision Matrix

The watcher checks the receiver's presence record before firing wake. Sender's UX uses the same record for "can I send right now?"

| Failed layer | `overall` | Watcher action | Wake exit code | Sender UX |
|---|---|---|---|---|
| os_process | critical | Defer; pending_peer_absent queue; 30s+backoff process poll | 4 | "Peer offline — Claude not running" |
| bridge_bootstrap | critical | Defer; pending_bootstrap queue; suggest user runs bootstrap | 5 | "Peer present but bridge not started" |
| active_peer (superseded) | critical | Auto-redirect to current peer's session; transparent | 6 | (no user-facing change) |
| project_scope | critical | Mark seen; audit `wrong_project_block` | 7 | "Peer is on different project; refused" |
| tenant_scope | critical | Mark seen; audit `auth_block` | 8 | "Tenant mismatch; refused" |
| active_pairing (handshake_pending) | degraded | Wait + retry handshake; bounded | 1 | "Handshake pending; retrying" |
| active_pairing (expired) | critical | Mark seen; surface to sender; require re-pair | 9 | "Link expired; re-pair to send" |
| active_pairing (revoked) | critical | Mark seen; surface immediately | 9 | "Link revoked" |
| receptive (busy) | degraded | Defer; drain on next inbox-check; bounded retry | 10 | "Peer busy; deliver when free" |
| bridge_d (missing) | degraded | Operator dashboard surfaces; messages still queue | (n/a — local-only) | "Receiver's bridge daemon offline" |
| mcp_server (missing) | degraded | Wrapper Phase 2 supervisor restart; messages buffer | (n/a — local-only) | "MCP connection lost; auto-reconnecting" |
| peer_breadcrumb (stale) | degraded | Best-effort wake; trigger handshake refresh | 1 (transient) | "Pairing data outdated; reconciling" |

---

## Watcher Exit Code Space (Authoritative)

This table replaces and extends what was in `WAKE_HARDENING_SPEC.md` and `AUTO_PAIR_SPEC.md`. All other docs cross-reference this section.

| Code | Meaning | Watcher behavior | Mark seen? | Retry? |
|---|---|---|---|---|
| 0 | Wake delivered (SendKeys completed; deeplink succeeded) | Standard receipt-verification path | On `seen_at` ack | On grace-period expiry, up to `WAKE_MAX_RETRIES` |
| 1 | Transient (foreground race, idle wait expired, peer_breadcrumb stale, handshake pending) | Standard retry per `WAKE_MAX_RETRIES` | After max retries | Yes |
| 2 | Config error (bad ThreadId UUID, missing required arg) | Mark seen; audit `wake_skipped_config_error` | Yes | No |
| 3 | (reserved for Phase B UUID-mismatch check; currently unused after Phase A title-revert) | Mark seen; audit `wake_skipped_wrong_chat`; permanent | Yes | No |
| 4 | Peer process missing (os_process layer down) | Defer; `pending_peer_absent` queue; 30s/1m/2m/5m backoff process poll | No (defer) | On peer rejoin (drain queue) |
| 5 | Peer present but not bootstrapped (bridge_bootstrap layer down) | Defer; `pending_bootstrap` queue; suggest user runs bootstrap | No (defer) | On bootstrap event |
| 6 | Active peer superseded (active_peer layer down) | Auto-redirect via registry to current peer; refire under new session | No (redirected) | Implicit redirect |
| 7 | Wrong project (project_scope layer down) | Mark seen; audit `wake_skipped_wrong_project` | Yes | No |
| 8 | Tenant mismatch (tenant_scope layer down; cloud only) | Mark seen; audit `wake_skipped_auth_block` | Yes | No |
| 9 | Active pairing expired or revoked | Mark seen; audit `wake_skipped_pairing_invalid` with sub-reason; surface to sender | Yes | No (require re-pair) |
| 10 | Peer busy (receptive layer down) | Defer; drain on next inbox-check by peer; bounded retry (3 tries, 60s apart) | No (defer) | On natural inbox-check or retry exhaustion |

Watcher reads presence from `presence-<agent>.runtime.json` BEFORE firing wake. If overall=critical, watcher emits the exit code directly without invoking the wake script (saves the script launch cost). If overall=degraded or ok, watcher invokes the wake script; the script can still emit additional codes (1, 2, or its own success) based on what it observes during execution.

---

## Pending Queues

The watcher maintains separate queues for messages deferred by exit code:

- `state/pending_peer_absent.jsonl` — exit 4
- `state/pending_bootstrap.jsonl` — exit 5
- `state/pending_busy.jsonl` — exit 10

Each entry: `{message_id, agent, session_id, deferred_at, deferred_reason, attempt_count}`.

**Drain triggers:**
- `pending_peer_absent` → on os_process layer rejoin (process poll detects PID re-appearance)
- `pending_bootstrap` → on bridge_bootstrap layer transition to ok (new session_id appears)
- `pending_busy` → on next `check_inbox` audit event by the target agent

**Capacity:** 100 messages per queue, FIFO eviction beyond cap. Evicted messages get `wake_dropped_*_overflow` audit events; user is notified once via terminal toast.

**TTL:** 24 hours per entry. Entries older than TTL are evicted with `wake_dropped_*_ttl` audit events.

---

## Forward Compatibility for Multi-Tenant Cloud

In v1 (single-machine local), `presence-<agent>.runtime.json` lives on local disk and is computed by the local watcher.

In v2/v3 (LAN/cloud), the same schema is replicated:

- **LAN:** each machine's bridge-d publishes its local presence record over LAN sync; receiver's view of sender's presence is the most recent replicated record.
- **Cloud:** presence rows live in DynamoDB:
  - PK: `<tenant_id>#<machine_id>#<agent>`
  - TTL: 90s (auto-deleted on stale heartbeat — built-in DynamoDB TTL)
  - Sender queries DynamoDB before send; receiver bridge-d heartbeats on 30s cadence

The schema does not change between v1, v2, and v3. Only the storage backend (filesystem → LAN replica → DynamoDB) varies. This is the canonical use case for the `BridgeTransport` abstraction in `BRIDGE_TRANSPORT_ABSTRACTION_SPEC.md`.

---

## Acceptance Criteria

- P1. `presence-<agent>.runtime.json` schema implemented; schema_version 1; round-trip tests green
- P2. Watcher computes presence on each poll cycle and writes to file atomically (`.tmp` → `os.replace`)
- P3. Watcher emits the correct exit code (0-10) based on layer states; tests assert each code path
- P4. Pending queues created and drained per their trigger conditions; tests assert
- P5. Sender (`send_to_peer` MCP tool) optionally reads receiver presence and surfaces failed-layer info via `MessageRouteResult.presence_warning` field; tests assert
- P6. Cache TTL (30s) honored; recomputation triggered by send, layer-failure event, or TTL expiry; tests assert
- P7. Each layer detector implemented as a standalone function in `core/presence.py`; unit tests per detector
- P8. Forward-compat: schema v1 includes `tenant_id` and `machine_id` fields even when local-default; schema does not change for cloud transport

## Tests Required

**Unit (per layer):**

1. `test_os_process_layer_detects_running_process_owned_by_user`
2. `test_os_process_layer_detects_missing_process`
3. `test_os_process_layer_detects_other_user_process`
4. `test_bridge_bootstrap_layer_detects_active_session`
5. `test_bridge_bootstrap_layer_detects_no_session`
6. `test_active_peer_layer_detects_superseded_session`
7. `test_bridge_d_layer_detects_dead_lease`
8. `test_mcp_server_layer_detects_stale_breadcrumb`
9. `test_peer_breadcrumb_layer_validates_pid_alive`
10. `test_peer_breadcrumb_layer_rejects_root_mismatch`
11. `test_project_scope_layer_detects_wrong_project`
12. `test_tenant_scope_layer_local_default_passes`
13. `test_active_pairing_layer_handshake_pending`
14. `test_active_pairing_layer_link_expired`
15. `test_receptive_layer_detects_busy`

**Integration:**

16. `test_watcher_emits_exit_4_on_os_process_missing`
17. `test_watcher_drains_pending_peer_absent_on_rejoin`
18. `test_watcher_emits_exit_5_on_not_bootstrapped`
19. `test_watcher_emits_exit_6_redirects_on_superseded`
20. `test_watcher_emits_exit_7_on_wrong_project`
21. `test_pending_queue_capacity_enforced_with_fifo_eviction`
22. `test_pending_queue_ttl_eviction`
23. `test_presence_cache_30s_ttl_honored`
24. `test_presence_recomputed_on_layer_failure_event`
25. `test_send_to_peer_returns_presence_warning_when_critical`

---

## Coordination Model

Codex implements; Claude reviews. Audit profile:
`tools/agent-bridge/audit-profiles/bridge-presence.md` (to be added; or fold into existing `wake-hardening.md` and `phase-b-breadcrumbs.md`).

This spec subsumes the prior `PROCESS_PRESENCE_SPEC.md` proposal and extends parts of `WAKE_HARDENING_SPEC.md` (D2 circuit breaker is unchanged; D1 pause gating now feeds into the `bridge_d` layer; D3 title-marker lessons are referenced from this spec's exit code table).

[[handoff:codex]]
