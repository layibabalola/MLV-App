# Agent Bridge Architecture

**Status:** Implemented baseline with active hardening seams.

Agent Bridge is a local, file-backed message bus used by Claude and Codex to
handoff work through MCP tools. Its main invariant is simple: durable delivery to
an inbox is not the same thing as wake, read, or handled completion.

## Components

| Component | Responsibility |
|---|---|
| `agent_bridge.py` | Compatibility facade and current orchestration layer for routing, inboxes, sessions, receipts, and diagnostics. |
| `server.py` | Import-safe FastMCP server factory and stdio entrypoint. |
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

## Wake Verification

Wake spawn is not delivery. For Codex, `wake_codex.ps1` can exit successfully
even if the synthetic input never submits a turn. The watcher therefore keeps
helper-backed wake attempts in `watcher-state.json` as pending until the target
inbox row gains `seen_at` or `read_at`.

Codex wake is not equivalent to Claude Monitor. Claude Monitor is scoped to the
active Claude conversation. Codex wake is only thread-scoped when the protected
parent thread id is configured and the `codex://threads/<id>` navigation
succeeds; otherwise it is an active-window SendKeys helper and can target the
wrong Codex chat.

If no receipt appears after the grace period, the watcher retries the wake
command. After the retry limit, it writes a `wake_delivery_failed` audit event,
prints a terminal notification, and suppresses further automatic retries for
that message id so the failure is visible instead of noisy.

## Recovery Model

Use diagnostics in this order:

1. `message_status(id)` for a stuck message.
2. `list_pending_receipts(...)` for a bounded page of queued/seen/read but unhandled receipt summaries.
3. `bridge_process_status()` for watcher leases, stale locks, and server markers.
4. `recover_state.py --state-dir <state-dir>` for corruption checks.
5. `compact.py --state-dir <state-dir> --dry-run` for retention and stale marker cleanup.

Repair tools are intentionally conservative: they validate first, back up before
mutation, and preserve unread work unless explicitly told otherwise.
