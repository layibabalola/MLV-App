# Agent Bridge Architecture

**Status:** Implemented baseline with active hardening seams. Multi-layer
presence model and transport abstraction are in design; see
[BRIDGE_PRESENCE_SPEC.md](BRIDGE_PRESENCE_SPEC.md) and the Tier-2/Tier-3 specs
referenced below.

Agent Bridge is a local, file-backed message bus used by Claude and Codex to
handoff work through MCP tools. Its main invariant is simple: durable delivery to
an inbox is not the same thing as wake, read, or handled completion.

This document is the canonical reference for how the bridge works on a single
machine. Cross-machine (LAN) and multi-tenant cloud deployments share the same
protocol, message types, and presence model; the transport layer differs and
is described in `BRIDGE_LAN_TRANSPORT_SPEC.md` (Tier 3) and
`BRIDGE_AWS_MULTITENANT_DESIGN.md` (Tier 3).

## Glossary

| Term | Definition |
|---|---|
| **bootstrap** | The act of activating a fresh session for an agent in this project. `bootstrap_session.py` registers the session, drains previous-session unread, sends HANDSHAKE, refreshes watcher config, and writes the peer breadcrumb. |
| **bridge-d** | Forward-compat name for the per-machine daemon that owns presence detection, peer breadcrumb writes, transport coordination, and audit log writes. In v1 this role is filled by `watcher.py`; in v2/v3 it gains LAN sync and cloud transport responsibilities. |
| **bridge_root** | The on-disk root containing `bridge-root.json`, session state, watcher config, and the `state/` subdir. Resolved via `--bridge-root`, `AGENT_BRIDGE_ROOT`, or default `%USERPROFILE%\.agent-bridge`. |
| **handshake** | The HANDSHAKE control message exchange between agents on bootstrap. "Pairing" requires both peers to have observed each other's HANDSHAKE. |
| **MCP server** | A `server.py` subprocess hosting the FastMCP server for one MCP client. NOT singleton — each client (Codex Desktop, Claude Desktop, Claude Code, probes) spawns its own. |
| **pairing** | Same-project: both peers have ack'd HANDSHAKE. Cross-project (future per `CROSS_PROJECT_PAIR_TEST_MATRIX.md`): an active link in `read_and_advise` or higher tier. |
| **peer** | The other agent (Claude when speaking from Codex's perspective; Codex when speaking from Claude's). |
| **peer_breadcrumb** | `peer-<agent>.runtime.json` at the bridge root, written on bootstrap, containing the agent's current session_id, desktop_thread_id, and other identity fields. Read by the watcher at fire time per `PHASE_B_BREADCRUMB_DESIGN.md`. |
| **presence** | A multi-layer model of "is the peer there and reachable?" — see `BRIDGE_PRESENCE_SPEC.md`. Ten layers, each independently checkable. |
| **rendezvous** | The project-level inbox bucket (e.g. `mlv-app`) used for control-plane traffic and durable cross-session collaboration. |
| **session** | A unique GUID identifying one agent's running instance. Sessions are tracked in `session.json`; the most recent for an agent is the "active" peer. |
| **supersession** | When a newer session for the same agent registers, the older session is marked superseded; bridge auto-redirects sends to the new session. |
| **tenant** | (Multi-tenant cloud only — forward-compat in v1.) An identity scope; users in different tenants cannot see each other's traffic. Single-machine v1 uses `tenant_id="local-default"`. |
| **wake** | The mechanism by which a sender's message reaches the receiver's attention. On Claude side: Monitor (always-on inbox poll). On Codex side: `wake_codex.ps1` SendKeys nudge. Wake spawn is not equivalent to delivery. |

## Components

| Component | Responsibility |
|---|---|
| `agent_bridge.py` | Compatibility facade and current orchestration layer for routing, inboxes, sessions, receipts, and diagnostics. |
| `server.py` | Import-safe FastMCP server factory and stdio entrypoint. |
| `server_wrapper.py` | Resolver-aware Desktop MCP launcher; rejects moved roots, audits startup, then execs `server.py` without proxying stdio. |
| `core/storage.py` | JSON/JSONL helpers, atomic writes, schema-version helper, and quarantine of malformed JSONL rows. |
| `core/addressing.py` | Typed address/message/sender models used by contract tests and routing seams. |
| `core/routing.py` | Pure routing resolver for active/superseded/project/agent-level decisions. |
| `core/processes.py` | Process liveness, command hashing, role leases, heartbeats, and safe release. |
| `core/settings.py` | Constrained `%USERPROFILE%\.agent-bridge\settings.json` loader and validation. |
| `bootstrap_session.py` | Session activation, previous-session drain, handshake, watcher config refresh, and watcher start. |
| `watcher.py` | Singleton file watcher and wake dispatcher. It notifies only; it must not consume inbox messages. Helper-backed wake paths are receipt-verified before being recorded as seen. |
| `configure_watcher.py` | Transactional watcher config writer with parent-thread guardrails. |
| `wake_codex.ps1` | Codex Desktop wake helper. It is a wake trigger, not proof of delivery. |
| `compact.py` | Inbox retention, audit rotation, and stale MCP server marker reaping. |
| `recover_state.py` | Dry-run-first state validator and backup-before-repair tool. |

## Protocol Planes

| Plane | Bucket | Use | Backpressure |
|---|---|---|---|
| Session | active GUID | Hot-path turn-to-turn work. | Strict one unread work item. |
| Project | `mlv-app` or derived rendezvous | Restart coordination and promoted work. | Relaxed; normal collaboration bucket. |
| Agent | `agent:<name>` | Durable control/recovery only. | Never blocked by work backpressure. |

`default` is deprecated. New writes must use an explicit project bucket, session
GUID, or typed agent-control path. Compatibility shims remain only where older
callers still need them.

## Settings

Runtime settings are optional and live at
`%USERPROFILE%\.agent-bridge\settings.json`. If the file is absent, defaults from
`core/settings.py` apply. Unsupported keys are rejected so the settings surface
stays intentionally small; see `SETTINGS.md` for the canonical list.

## Receipt Lifecycle

Message state is derived from the original inbox row:

1. `queued` - row exists in the target inbox.
2. `seen` - `check_inbox` or `wait_inbox` observed the row.
3. `read` - receiver explicitly consumed or marked it read.
4. `handled` - receiver recorded semantic completion.
5. `failed` - receiver recorded a handled failure reason.

Explicit ACK messages are allowed for handshakes and smoke tests, but routine
delivery/read/handled status should be queried through receipt tools.

## Process Ownership

`watcher.py` is a strict singleton per state directory and owns
`state/locks/watcher.lock`. It heartbeats the lease and releases only its own
`pid + generation`.

`server.py` is never singleton. Claude, Codex, and probes can each spawn their
own MCP stdio server. Each process writes only a marker under `state/server-pids/`.
Existing MCP server processes do not hot-reload changed Python files; see
`RUNTIME_RELOAD.md` before treating client/probe disagreements as bridge defects.

Short-lived helpers do not own leases. They should be bounded, observable, and
safe to retry.

## Process Model

Three process roles, one machine:

| Role | Process | Singleton? | Responsibilities |
|---|---|---|---|
| **bridge-d** | `watcher.py` (today; will gain responsibilities) | Yes (per state-dir) | Inbox polling, wake dispatch, presence layer computation, peer breadcrumb consumption, audit log writes (compaction, supersession events), pending-queue management |
| **MCP server** | `server.py` subprocesses, one per MCP client | No (multiple OK) | Hosts the FastMCP tool surface (`check_inbox`, `send_to_peer`, etc.) for one specific MCP client. Each connection is stdio-only. |
| **Wake helper** | `wake_codex.ps1` (Codex direction); future `wake_claude.ps1` (Claude Desktop direction) | No (short-lived) | OS-side keystroke injection into the desktop app's chat; fire-and-forget per message |

In v2 (LAN), `bridge-d` adds a peer-sync responsibility: bidirectional
WebSocket / HTTP to peer-d on other machines, replicating inbox rows + receipt
updates with last-write-wins CRDT semantics on receipt fields.

In v3 (cloud), `bridge-d` adds a JWT-authenticated WebSocket to API Gateway,
puts to SQS / DynamoDB, subscribes to push notifications. Local FS storage
becomes a write-through cache; cloud is canonical.

The transport plug-in surface is described in
`BRIDGE_TRANSPORT_ABSTRACTION_SPEC.md`. Authentication is in
`BRIDGE_AUTH_SPEC.md`. Tenant scoping (forward-compat for cloud) is in
`BRIDGE_TENANT_SCOPING_SPEC.md`.

## Presence Model

"Is the peer there?" is not a single boolean. The bridge tracks ten
independent presence layers per agent (see `BRIDGE_PRESENCE_SPEC.md`):

1. **os_process** — agent executable running, owned by current user
2. **bridge_bootstrap** — agent has an active session in registry
3. **active_peer** — that session is the most recent (not superseded)
4. **bridge_d** — watcher process alive, lease heartbeat fresh
5. **mcp_server** — `server.py` subprocess alive
6. **peer_breadcrumb** — `peer-<agent>.runtime.json` fresh, valid
7. **project_scope** — bootstrapped project matches message's project
8. **tenant_scope** — JWT tenant_id matches (cloud only)
9. **active_pairing** — handshake ack'd; cross-project link not expired
10. **receptive** — recent inbox-check activity; not stuck in long task

Presence is computed by bridge-d before each wake fire and cached in
`presence-<agent>.runtime.json` with a 30s TTL. The watcher reads the cache,
computes a watcher exit code (0-10), and routes the wake decision based on
which layer is failing. See `BRIDGE_PRESENCE_SPEC.md` for the full
detection table, decision matrix, and exit code semantics.

## Wake Flow (end-to-end sequence)

```
sender                    bridge-d                      receiver
  │                          │                              │
  │  send_to_peer            │                              │
  ├─────────────────────────►│                              │
  │  (MCP server route)      │                              │
  │                          │                              │
  │                          │  read presence-<peer>.runtime.json
  │                          │  (compute if cache stale)
  │                          │
  │  (returns route id)      │  if overall=critical:
  │◄─────────────────────────┤    refuse, return reason
  │                          │
  │                          │  if overall=ok|degraded:
  │                          │    append to inbox-<peer>.jsonl
  │                          │    fire wake helper
  │                          │
  │                          │  (wake_codex.ps1)
  │                          ├─────────► SendKeys "check bridge inbox"
  │                          │              │
  │                          │              ▼
  │                          │           receiver agent
  │                          │              │
  │                          │              │  check_inbox MCP call
  │                          │              ◄────────┘
  │                          │
  │                          │  receiver agent surfaces
  │                          │  message + mark_read
  │                          │
  │                          │  audit: seen_at, read_at, [handled_at]
  │                          │
  │  message_status query    │
  ├─────────────────────────►│
  │  (returns receipt state) │
  │◄─────────────────────────┤
```

Wake spawn is not delivery. For Codex, `wake_codex.ps1` can exit
successfully even if the synthetic input never submits a turn. The watcher
therefore keeps helper-backed wake attempts in `watcher-state.json` as
pending until the target inbox row gains `seen_at` or `read_at`.

Codex wake is not equivalent to Claude Monitor. Claude Monitor is scoped to
the active Claude conversation. Codex wake is only thread-scoped when the
protected parent thread id is configured and the `codex://threads/<id>`
navigation succeeds; otherwise it is an active-window SendKeys helper and
can target the wrong Codex chat (the failure mode `BRIDGE_PRESENCE_SPEC.md`
exit code 6 is meant to catch).

If no receipt appears after the grace period, the watcher retries per
`WAKE_MAX_RETRIES`. After the retry limit, it writes a
`wake_delivery_failed` audit event, prints a terminal notification, and
suppresses further automatic retries for that message id so the failure is
visible instead of noisy.

## Failure Mode Tree

Cross-references the `BRIDGE_PRESENCE_SPEC.md` exit codes. Each row maps
"what's wrong" to "what does the watcher do" to "what does the user see":

| Failed presence layer | Watcher exit code | Watcher action | Sender UX |
|---|---|---|---|
| os_process missing | 4 | Defer; pending_peer_absent queue; backoff process poll | "Peer offline — Claude not running" |
| bridge_bootstrap missing | 5 | Defer; pending_bootstrap queue | "Peer present but bridge not started" |
| active_peer superseded | 6 | Auto-redirect to current peer; transparent | (no user-facing change) |
| project_scope wrong | 7 | Mark seen; audit `wake_skipped_wrong_project` | "Peer is on different project; refused" |
| tenant_scope wrong | 8 | Mark seen; audit `wake_skipped_auth_block` | "Tenant mismatch; refused" |
| active_pairing handshake_pending | 1 (transient) | Wait + retry handshake; bounded | "Handshake pending; retrying" |
| active_pairing expired/revoked | 9 | Mark seen; surface immediately | "Link expired/revoked; re-pair to send" |
| receptive busy | 10 | Defer; drain on next inbox-check | "Peer busy; deliver when free" |
| bridge_d missing | (n/a — local-only failure) | Operator dashboard surfaces | "Receiver's bridge daemon offline" |
| mcp_server missing | (n/a — local-only) | Wrapper Phase 2 supervisor restarts | "MCP connection lost; auto-reconnecting" |
| peer_breadcrumb stale | 1 (degraded) | Best-effort wake; trigger handshake refresh | "Pairing data outdated; reconciling" |

## Recovery Model

Use diagnostics in this order:

1. `message_status(id)` for a stuck message.
2. `list_pending_receipts(...)` for a bounded page of queued/seen/read but unhandled receipt summaries.
3. `bridge_process_status()` for watcher leases, stale locks, and server markers.
4. `recover_state.py --state-dir <state-dir>` for corruption checks.
5. `compact.py --state-dir <state-dir> --dry-run` for retention and stale marker cleanup.

Repair tools are intentionally conservative: they validate first, back up before
mutation, and preserve unread work unless explicitly told otherwise.
