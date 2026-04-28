# Agent Bridge - Process Ownership And Lease Spec

**Status:** Implemented for current bridge-owned processes - watcher leases/heartbeats, MCP server per-process markers, and `bridge_process_status` landed. Monitor leases remain a future extension if Monitor startup becomes bridge-owned.
**Authors:** Codex + Claude review
**Motivation:** prevent duplicate background daemons without killing valid client-owned MCP servers

---

## Problem

The bridge previously treated process ownership too broadly. A shared `server.pid`
made every MCP stdio server look like a singleton, so a new Claude, Codex, or probe
server could kill another client's live transport.

The correct rule is role-specific ownership:

- Some bridge processes must be strict singletons.
- Some processes are allowed to have one instance per agent/session/bucket set.
- Some processes are client-owned and must allow multiple concurrent instances.
- Some helpers are short-lived and should be observable, not singleton-managed.

---

## Process Classes

| Process | Ownership Model | Key |
|---|---|---|
| `watcher.py` | Strict singleton | state dir |
| Monitor / notification tailer | Scoped singleton | agent + session + watched buckets |
| `server.py` MCP stdio server | Multi-instance | pid marker only |
| `wake_codex.ps1` | Short-lived helper (CLI-style) | no lease |
| `consume_inbox.py` | Short-lived helper (CLI diagnostic only — NOT wake path) | no lease |
| `bootstrap_session.py` | Short-lived helper | no lease |
| probes and diagnostics | Short-lived helper | no lease |

The guardrail: never use one shared `server.pid` for MCP stdio servers again.

---

## Directory Layout

```text
state/
  locks/
    watcher.lock
    monitor-claude-a16b6e4f.lock
    monitor-codex-9111dce5.lock
  server-pids/
    server-12345.pid
    server-67890.pid
```

`locks/` is for exclusive leases.

`server-pids/` is for observation-only markers. These files do not grant ownership
and must not be used to kill other live MCP server instances.

---

## Lease File Schema

Each lease file is JSON:

```json
{
  "role": "watcher",
  "pid": 12345,
  "parent_pid": 11111,
  "process_name": "python.exe",
  "command_line_hash": "sha256:...",
  "state_dir": "C:\\Users\\obabalola\\.agent-bridge\\state",
  "agent": "claude",
  "session_id": "a16b6e4f-d0bb-4f9e-8878-22ccbef0deeb",
  "bucket_set_hash": "sha256:...",
  "started_at": "2026-04-28T00:00:00Z",
  "heartbeat_at": "2026-04-28T00:00:10Z",
  "generation": "uuid"
}
```

`agent`, `session_id`, and `bucket_set_hash` are optional for process roles that do
not need that scope.

`parent_pid` helps prevent PID-reuse false matches. `generation` prevents a new
process from accidentally deleting a lease created by a newer owner after PID reuse
or delayed shutdown.

---

## Startup Rule

For strict or scoped singleton roles:

1. Acquire an OS-level lock before inspecting or writing the lease.
   - On Windows, use a named mutex or an exclusive file handle.
   - On POSIX, use `flock` or equivalent.
2. If no lease exists, write one and start.
3. If a lease exists, verify whether the PID is alive.
4. If the PID is alive, verify that the command line matches the expected role.
5. If PID, command line, and heartbeat are fresh, do not start another instance.
6. If the PID is dead or heartbeat is stale, reclaim the lease and start.
7. If the PID exists but command line does not match, do not kill it.
   - Mark the lease `corrupt` or move it to `locks/corrupt/`.
   - Create a new lease generation only after acquiring the role lock.

Freshness should be role-specific:

| Role | Suggested Stale Threshold |
|---|---|
| watcher | 3 poll intervals |
| monitor | no heartbeat; use pending receipts to detect a dead wake path |
| long-running future daemons | 2 heartbeat intervals |

---

## Shutdown Rule

Long-running singleton/scoped processes handle:

- `SIGINT`
- `SIGTERM`
- normal process exit
- `atexit`

On shutdown, remove only the lease owned by this exact `pid + generation`.

Never remove a lease only because the filename matches. That can delete a lease
created by a replacement process during restart races.

---

## MCP Server Markers

`server.py` is a client-owned stdio MCP server. Multiple instances are valid because
Claude Desktop, Codex Desktop, and direct probes may each spawn their own server.

Rules:

- Do not use a singleton lock for `server.py`.
- Write a per-process marker such as `server-pids/server-12345.pid`.
- Marker cleanup is opportunistic.
- A new server must not kill any other server just because a marker exists.
- Stale markers can be removed if the PID is dead or the command line clearly is not
  an agent-bridge MCP server.

---

## Helper Processes

Short-lived helpers do not take singleton leases:

- `wake_codex.ps1` (Codex wake — fires from watcher.on_message_command)
- `consume_inbox.py` (CLI diagnostic only — never wired into watcher)
- `bootstrap_session.py`
- direct MCP probes
- one-off diagnostics

They should be bounded by timeouts and logged in the audit trail when they mutate
bridge state.

---

## `bridge_process_status` Tool

Diagnostic MCP tool:

```python
bridge_process_status(state_dir=None) -> dict
```

Response shape:

```json
{
  "watcher": {
    "expected": true,
    "running": true,
    "pid": 77740,
    "heartbeat_at": "2026-04-28T00:00:10Z",
    "stale": false
  },
  "monitors": [
    {
      "agent": "claude",
      "session_id": "a16b6e4f-d0bb-4f9e-8878-22ccbef0deeb",
      "bucket_set": ["mlv-app", "a16b6e4f-d0bb-4f9e-8878-22ccbef0deeb"],
      "running": false,
      "stale": true
    }
  ],
  "mcp_servers": {
    "marker_count": 3,
    "live_count": 2,
    "stale_markers": ["server-12345.pid"]
  },
  "corrupt_locks": []
}
```

This should answer "is the bridge healthy?" without requiring Task Manager,
PowerShell process spelunking, or raw JSONL inspection.

---

## Implemented Behavior

1. `core/processes.py` owns lease acquisition, heartbeat, validation, and cleanup.
2. `watcher.py` owns `locks/watcher.lock` and heartbeats it while polling.
3. `bootstrap_session.ensure_watcher` uses the watcher lease before spawning.
4. `server.py` writes per-process markers under `server-pids/` and never claims a
   singleton lease.
5. `bridge_process_status` reports watcher lease status, lock files, and MCP server
   marker counts.
6. `compact.py` reaps stale `server-pids/` markers opportunistically.
7. Tests cover lease acquire/heartbeat/release and stale server marker reaping.

Future extension: add monitor-scoped leases if Monitor startup becomes automated by
the bridge itself. Today Monitor remains harness-owned on Claude's side.

---

## Non-Goals

- Do not make `server.py` singleton.
- Do not use process name alone as proof of ownership.
- Do not delete or kill processes solely because a PID file exists.
- Do not make watcher consume messages again as part of process recovery.

---

## Open Questions

1. Should stale lock repair be automatic on startup only, or also exposed as an
   explicit `repair_process_locks` tool?
2. What should the exact pending-receipts threshold be for declaring a Monitor wake
   path dead?
