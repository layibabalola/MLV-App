# Agent Bridge - Health Panel Spec

**Status:** MVP implemented (2026-04-30)
**Authors:** Claude (proposal); Codex review pending
**Tier:** Tier 0 - in-chat surface; no install required, no new processes
**Motivation:** Field-deployed users need a single, friction-free way to ask "is the bridge healthy?" and get a structured, actionable answer. The current path is: read multiple state files manually, run `Get-Process`, tail an audit log, decide. That's expert-only. A single MCP tool that returns a structured health snapshot, renderable as a markdown table in chat, makes self-service diagnosis available to any user with bridge access. This is the smallest possible diagnostic UI: the chat *is* the panel.

**Implemented v1:** `bridge_health_panel` is exposed from the MCP server and
returns JSON or markdown. It is a read-only on-demand aggregator over
`state.json`, `session.json`, watcher/server breadcrumbs, wake processes when
`watcher-state.json`, inbox rows, stale-unread wake gaps, pending-action
ledger, wake breaker state, audit-derived failures/wakes, schema versions,
provenance, and cross-project links. Missing or malformed slices degrade
independently; the tool does not repair or quarantine state while reading.
Ordinary health/dashboard calls avoid expensive Windows process command-line
scans; in-flight wake status is derived from watcher pending-wake state.
Implemented v1.1 adds `recommended_actions` and `recovery_hint` to the health
snapshot and renders those actions in health/dashboard markdown, so field users
see the next safe command instead of only raw counters.
Recommended actions are diagnostic guidance, not proof that a recovery command
will succeed. Each mutating recommendation must document its failure path in the
tool result or surrounding docs: rejected preconditions, no-op/already-clean,
partial cleanup, and retry/next diagnostic command.

---

## Goal

One MCP tool, `bridge_health_panel`, that returns a structured snapshot of bridge health. The snapshot is computed from existing state files and process introspection - **no new state files, no new daemons, no new background work**. The tool is read-only and side-effect-free; calling it never mutates bridge state.

The agent renders the snapshot as a markdown table in chat. Users invoke it by asking ("show bridge health", "is the bridge OK?") or via a slash command (`/bridge-health` if a host supports them).

## Non-Goals

- No live updating UI (snapshot per call; user re-asks for fresh data).
- No new persistent state. The snapshot is computed on-demand and not cached to disk.
- No mutation tools in this spec. Self-healing actions (kill stuck wakes, reset breaker, force-resume) are separate MCP tools tracked in their own specs (`WAKE_HARDENING_SPEC.md` D2, `BRIDGE_HARDENING.md` reset paths). This spec is the *visibility* layer.
- No GUI app, tray icon, web dashboard, or Electron shell. Those are Tier 1+ surfaces and out of scope here.
- No cross-machine aggregation. v1 surfaces only the local bridge root the caller is bootstrapped against. Multi-tenant aggregation is forward-compat (HP-FC1 below).

---

## Architecture

```
                          +-----------------------------+
   chat invocation        |    bridge_health_panel      |
   ("show bridge health") |    (MCP tool, in server.py) |
                          +--------------+--------------+
                                         |
                  +----------------------+----------------------+
                  | reads (no writes):                          |
                  |  - state.json          (paused flag)        |
                  |  - session.json        (active sessions,    |
                  |                         provenance)         |
                  |  - state/locks/        (watcher.lock, etc)  |
                  |  - state/server-pids/  (server breadcrumbs) |
                  |  - peer-*.runtime.json (peer breadcrumbs)   |
                  |  - inbox-*.jsonl       (unread/handled cnt) |
                  |  - audit.jsonl         (recent failures)    |
                  |  - pending-actions/*   (ledger, if present) |
                  |  - presence-*.json     (presence cache, if  |
                  |                         BRIDGE_PRESENCE     |
                  |                         shipped)            |
                  |  - psutil.process_iter (wake PIDs, ages)    |
                  +----------------------+----------------------+
                                         |
                                  Snapshot (JSON)
                                         |
                                         v
                              Agent renders to markdown
                                  in chat response
```

The tool is a pure read aggregator. Each metric is computed by an isolated collector function (`core/health/collectors/<metric>.py`); a top-level `assemble_snapshot()` orchestrates them. A collector that fails (e.g. corrupted state file) yields `{"status": "error", "error": "..."}` for its slice instead of failing the entire snapshot. **Never let one bad metric mask the rest.**

---

## API

### MCP tool signature

```
name: bridge_health_panel
args:
  agent: str                # "claude" | "codex" - the calling agent
  session_id: str | None    # active session GUID; defaults to caller's bootstrapped session
  include_extended: bool    # default False; True returns extended metrics (HP8-HP14)
  format: "json" | "markdown"  # default "json"
  stuck_wake_threshold_seconds: int  # default 30; ages a wake PID into "stuck" beyond this
returns: SnapshotV1 (json) | str (pre-rendered markdown table)
```

### SnapshotV1 schema

```json
{
  "schema_version": 1,
  "snapshot_ts": "2026-04-29T13:21:00.000Z",
  "snapshot_duration_ms": 47,
  "bridge_root": "C:/Users/.../.agent-bridge",
  "tenant_id": "local-default",
  "machine_id": "8a6e1c-machine-uuid",
  "overall_status": "healthy" | "degraded" | "paused" | "broken",
  "core": {
    "bridge_state":     { ... HP1 ... },
    "active_sessions":  { ... HP2 ... },
    "watcher":          { ... HP3 ... },
    "server":           { ... HP4 ... },
    "in_flight_wakes":  { ... HP5 ... },
    "stuck_wakes":      { ... HP6 ... },
    "inboxes":          { ... HP7 ... }
  },
  "extended": {
    "pending_actions":  { ... HP8 ...  } | null,
    "recent_failures":  { ... HP9 ...  } | null,
    "provenance":       { ... HP10 ... } | null,
    "wake_breaker":     { ... HP11 ... } | null,
    "last_wake_per_peer": { ... HP12 ... } | null,
    "schema_versions":  { ... HP13 ... } | null,
    "cross_project":    { ... HP14 ... } | null
  },
  "errors": [
    {"metric": "in_flight_wakes", "error": "psutil unavailable: ..."}
  ]
}
```

`overall_status` derivation:

- **`broken`** if any of: watcher dead AND not gracefully paused; server PID listed but actually dead; >0 stuck wakes; bridge state corrupted/unreadable.
- **`paused`** if `bridge_state.paused == True` and watcher is intentionally idle.
- **`degraded`** if any of: server breadcrumb stale (>300s); ≥1 inbox has handled-not-seen messages; recent_failures (HP9) shows >0 in last 5min; oldest unread >watcher poll * 10.
- **`healthy`** otherwise.

---

## Acceptance Criteria

### Core metrics (must ship in v1)

#### HP1 - Bridge state

- [ ] Reads `<bridge-root>/state/state.json`; surfaces `paused` flag, `paused_reason`, `paused_since`.
- [ ] If file missing/corrupt: `{ "status": "error", "error": "..." }`; does not abort snapshot.
- [ ] Tests: paused=true, paused=false, file missing, file corrupted.

#### HP2 - Active sessions

- [ ] Reads `<bridge-root>/session.json`; lists `claude` and `codex` active sessions with: `session_id`, `started_at`, `bootstrap_origin` (parent/subagent/unknown - per `BRIDGE_BOOTSTRAP_PROVENANCE_SPEC.md`).
- [ ] Includes any superseded session GUIDs from the last 1 hour for context (so the user can see "session was rotated 12 min ago").
- [ ] Tests: parent provenance, subagent provenance, unknown provenance, no active session, recently superseded session visible.

#### HP3 - Watcher status

- [ ] Stats `<bridge-root>/state/locks/watcher.lock`; reads recorded PID.
- [ ] Verifies PID via `psutil.pid_exists(pid)`; reads `heartbeat_at` from lock.
- [ ] Reports: `alive` (bool), `pid`, `heartbeat_age_seconds`, `last_poll_age_seconds` (from `state/last_poll.json` if exists).
- [ ] Distinguishes "watcher dead" from "watcher gracefully paused" via `bridge_state.paused`.
- [ ] Tests: alive+heartbeat fresh; alive+heartbeat stale; PID not running; lock missing; paused state.

#### HP4 - Server status (wrapper + inner server, separately)

MCP server health is two-layer: the **wrapper process** (parent; holds the stdio pipe the MCP host connected to) and the **inner server process** (child; the actual FastMCP server). These must be reported separately because their failure modes differ.

| Event | Audit action | User impact |
|---|---|---|
| Bridge code changed under wrapper | `mcp_server_refresh_required` | Inner `server.py` was restarted under the live wrapper; ordinary tool calls should continue, but tool-list/schema changes may still require host reconnect/reload. |
| Wrapper exit + trampoline relaunch | `mcp_server_wrapper_self_restart_requested` + `mcp_server_wrapper_launch` | `server_wrapper_trampoline.py` keeps stdio open while the wrapper relaunches after exit 77. |

- [ ] Globs `<bridge-root>/state/server-pids/server-*.json`; for each: `pid`, `agent`, `started_at`, breadcrumb `mtime_age_seconds`.
- [ ] Verifies each PID alive and, on Windows, verifies the runtime breadcrumb
  timestamp matches the OS process creation time. Entries with dead PIDs or
  PID-reuse identity mismatches are flagged `stale_breadcrumb`.
- [ ] Reads audit log for most recent `mcp_server_wrapper_launch` per parent PID: `pid`, `timestamp`, `changed_files`, `elapsed_ms` (if self-restart).
- [ ] Reads audit log for most recent `mcp_server_refresh_required` per wrapper PID: `child_pid`, `timestamp`, `changed_files`, `reason`.
- [ ] Surfaces:
  - `wrapper_pid` — current live wrapper PID (null if none alive)
  - `wrapper_started_at` — timestamp of last `mcp_server_wrapper_launch`
  - `inner_pid` — current inner child PID (from most recent self-restart or initial launch)
  - `inner_last_restart_at` — legacy timestamp of last `mcp_server_self_restarted` (null if never)
  - `inner_restart_count_today` — count of self-restart events since midnight UTC
  - `mcp_host_likely_reconnected` — bool: `True` if any `check_inbox` or `mark_seen` audit event for any agent has `timestamp > wrapper_started_at`. Heuristic: if the agent used bridge tools after the wrapper launched, the MCP host reconnected successfully.
  - `impact_class` — `"tool_access_risk"` if a wrapper launch or bridge-code refresh happened and `mcp_host_likely_reconnected == False`; legacy `"benign_hot_reload"` applies only to historical `mcp_server_self_restarted` audit rows followed by proven tool activity.
- [ ] If `impact_class == "tool_access_risk"`: contributes to `overall_status = degraded`.
- [ ] Tests: live server only; live + stale; no servers; corrupted breadcrumb; wrapper recently relaunched with no post-launch audit activity (tool_access_risk); wrapper relaunched with post-launch audit activity (benign); multiple self-restarts in burst (inner_restart_count_today correct).

**Implemented v1.2 (2026-05-01):** `bridge_process_status` now includes
`mcp_reconnect` with wrapper launch count, current wrapper PID/running state,
inner restart count, latest inner child PID, last post-launch tool activity,
`mcp_host_likely_reconnected`, and an `impact_class`:

- `connected_or_idle` - wrapper launch exists and no recent reconnect risk is
  visible.
- `benign_hot_reload` - inner `server.py` restarted under a still-live wrapper;
  stdio stayed attached.
- `tool_access_risk` - wrapper relaunched recently and no post-launch bridge
  tool activity has proven the MCP host recovered.
- `client_reconnect_likely_required` - latest wrapper PID is dead; tool access
  likely requires host/client reconnect.
- `unknown_no_wrapper_audit` - no wrapper launch audit exists.

`bridge_health_panel` degrades when `impact_class` is `tool_access_risk` or
`client_reconnect_likely_required` and recommends reconnecting/restarting the
MCP host before assuming bridge tools are healthy. The heuristic is intentionally
conservative: inbox JSONL durability still holds, but tool access may be gone
for the current turn.

#### HP5 - In-flight wakes

- [ ] Iterates processes via `psutil.process_iter(['pid','name','cmdline','create_time'])`; matches `name in {pwsh.exe, powershell.exe, python.exe}` AND cmdline contains `wake_codex.ps1` or known wake entrypoint.
- [ ] For each match: `pid`, `target_agent` (parsed from cmdline), `target_session` (parsed if available), `started_at`, `age_seconds`.
- [ ] Tests: zero wakes; one wake; multiple wakes; cmdline parse failure (degrades to unknown target, not error).

#### HP6 - Stuck wakes

- [ ] From HP5, filters wakes with `age_seconds > stuck_wake_threshold_seconds` (default 30).
- [ ] Each entry: `pid`, `age_seconds`, `stuck_threshold_used`.
- [ ] If any present: `overall_status` becomes `broken`.
- [ ] Tests: threshold honored at boundary; multiple stuck; threshold override accepted.

#### HP7 - Inbox summary

- [ ] For each known inbox (active session inbox + project rendezvous inbox + any superseded sessions visible in HP2):
  - `agent`, `session_id`, `unread_count`, `oldest_unread_age_seconds` (null if none), `old_unread_over_threshold_count`, `stale_unread_count`, `stale_unread_oldest_age_seconds`, `handled_not_seen_count`, `handled_not_seen_oldest_age_seconds`.
- [ ] `old_unread_over_threshold_count` increments when an unread row is older
  than watcher poll interval * 10. Any nonzero value degrades the health
  snapshot because delivery is no longer boringly fresh.
- [ ] `stale_unread_count` is the wake-success-without-consumption metric:
  unread rows whose message id is already in watcher `seen_ids` past the stale
  threshold. This catches "wake command exited 0, but the receiving agent never
  actually read the inbox" failures.
- [ ] `handled_not_seen_count` is the discipline-failure metric: messages where `read_at` is set but `seen_at` was never stamped (the case behind the second toast flood incident; see `bridge_trigger_heuristics.md`).
- [ ] Tests: empty inbox; unread present; stale unread present; handled-not-seen present; mixed.

### Extended metrics (gated on subsystem readiness; `include_extended=True` only)

#### HP8 - Pending action ledger

- [ ] Depends on Codex commit `83fbb6c5` (`record_pending_bridge_action` MCP tool family).
- [ ] Reads `<bridge-root>/pending-actions/<agent>/*.json`; aggregates by status (`pending`, `working`, `parked`, `resolved`, `expired`).
- [ ] Surfaces top 5 oldest unresolved with `id`, `requested_by`, `requested_at`, `status`, `eta_at`.
- [ ] Tests: directory missing (return null gracefully); empty; aggregate counts; oldest-5 selection.

#### HP9 - Recent failures

- [ ] Tails `<bridge-root>/audit.jsonl` for last 50 entries; filters `event_class == "failure"` from last 5 min.
- [ ] For each: `event_type`, `event_ts`, `agent`, `session_id`, brief `summary` (first 100 chars of `details`).
- [ ] Tests: no failures; failures present; ring-buffer wraparound (audit.jsonl rotated mid-read).

#### HP10 - Bootstrap provenance summary

- [ ] Aggregates HP2 by `bootstrap_origin`; counts of parent / subagent / unknown.
- [ ] If any subagent session is the *active* session for an agent: flag `subagent_owns_active`. (The "Confucius incident" symptom.)
- [ ] Tests: all-parent state; subagent-owns-active flagged; unknown-provenance counted.

#### HP11 - Wake circuit breaker

- [ ] Depends on `WAKE_HARDENING_SPEC.md` D2 (per-session breaker; not yet shipped).
- [ ] Reads `<bridge-root>/state/wake-failure-windows.json` (or equivalent).
- [ ] Per session: `breaker_state` (closed/open/half-open), `consecutive_failures`, `last_failure_at`, `next_retry_at` if open.
- [ ] Tests: closed; tripped (open); half-open recovery; file missing → returns null.

#### HP12 - Last successful wake per peer

- [ ] Scans audit log for last `wake_succeeded` event per `(agent, session_id)`.
- [ ] Surfaces ISO timestamp; null if never.
- [ ] Tests: never woken; recently woken; multiple peers.

#### HP13 - Schema version compliance

- [ ] Depends on `BRIDGE_SCHEMA_EVOLUTION_SPEC.md` SE1+ (schema_version on persistent files).
- [ ] Walks bridge root; for each known persistent file (registry below): reports `schema_version` field if present, else `legacy`.
- [ ] Tests: all current; mixed; legacy-only.

#### HP14 - Cross-project pair state

- [ ] Depends on cross-project pairing impl.
- [ ] Reads `<bridge-root>/state/cross-project-pairs/*.json`; lists active links: `link_id`, `tier`, `expires_at`, `peer_project`.
- [ ] Tests: no links; active link; expired link.

---

## Recovery Actions (companion to health panel; NOT part of the read-only panel)

The health panel is read-only. When HP4 surfaces `impact_class == "tool_access_risk"`, the panel also returns a `recovery_hint` field pointing the user to the appropriate recovery action. The actions themselves are separate MCP tools (or CLI commands) so the read-only invariant on the panel is never broken.

### HP-R1 - MCP wrapper reconnect/reload

**Purpose:** gives field users a safe, one-step recovery path when the MCP host lost the wrapper connection, without requiring PID spelunking or manual process management.

**Mechanism:** `bridge_reconnect_mcp` MCP tool (or `python -m agent_bridge reconnect-mcp` CLI equivalent):

1. Locates the wrapper command from the most recent `mcp_server_wrapper_launch` audit entry.
2. Verifies whether the wrapper PID is alive (guards against diagnosing a
   healthy wrapper as dead).
3. Surfaces the correct operator action. For stdio MCP, a bridge subprocess
   cannot reconnect itself to a Desktop host after the wrapper process exits;
   the host must relaunch/reconnect the MCP server. The recovery action should
   therefore be an MCP-host reconnect/reload command when the host exposes one,
   or an explicit user instruction to restart/reload the MCP client.
4. Writes a `mcp_server_reconnect_requested` audit event with `requested_by`
   and the recommended operator action.
5. Returns a structured result explaining whether reconnect is needed, refused
   because the wrapper is alive, or blocked because no host reconnect mechanism
   is available.

**Guard:** if the wrapper PID is alive, refuse and return `{ "ok": false, "reason": "wrapper_alive", "pid": <pid> }`. The user should call `bridge_health_panel` first to confirm the wrapper is actually dead before calling reconnect.

**Field-user docs note:** JSONL inbox messages are durable through any number of MCP restarts — messages queued while the MCP server is down are not lost. Only bridge tool *calls* fail during the outage. Once the MCP host reconnects, the agent's next `check_inbox` call will surface any messages that arrived during the gap.

**Acceptance criteria:**

- [ ] `bridge_reconnect_mcp` refuses if wrapper PID is alive.
- [ ] `bridge_reconnect_mcp` returns host-specific reconnect guidance; it must
  not pretend a detached stdio wrapper can repair an already-broken host pipe.
- [ ] `mcp_server_reconnect_requested` audit event written before exec.
- [ ] HP4 `mcp_host_likely_reconnected` heuristic picks up the new wrapper within one poll cycle.
- [ ] Tests: wrapper dead → reconnect succeeds; wrapper alive → refused; no audit history → error (can't determine args safely).

### HP-R2 - Stale-unread watchdog/rearm

**Purpose:** closes the gap where a wake command succeeds and the watcher stops
refiring, but the target agent never consumes the message.

**Mechanism:** `stale_unread_watchdog(agent=None, stale_after_seconds=300,
rearm=False, limit=50)` scans inbox rows for unread messages whose ids are in
watcher `seen_ids` and older than the threshold.

- With `rearm=false`, it is diagnostic-only and returns the stale rows.
- With `rearm=true`, it removes those ids from watcher `seen_ids` so the next
  watcher poll can fire the normal nudge path again.
- It writes a `stale_unread_watchdog` audit event with the stale and rearmed
  counts.

**Acceptance criteria:**

- [ ] Diagnostic mode reports stale unread messages without mutating
      watcher-state.
- [ ] Rearm mode removes only matching stale ids from `seen_ids`; unrelated ids
      remain.
- [ ] Health panel degrades when stale unread rows are present.
- [ ] Tests cover old unread, fresh unread, read rows, unrelated `seen_ids`, and
      rearm behavior.

### HP-R3 - Receipt-debt cleanup/migration

**Purpose:** gives users a safe way to move bridge health from degraded back to
healthy without guessing which receipt rows are safe to mutate.

**Mechanism:** `receipt_debt_cleanup(agent=None, old_after_seconds=None,
stale_after_seconds=300, apply=false, rearm_stale_unread=false, limit=50)`:

- Reports three categories: read-without-seen, old-unread, and stale-unread.
- Dry-run mode is the default and does not mutate inbox or watcher state.
- Apply mode backfills `seen_at` only for rows that already have `read_at`.
- `rearm_stale_unread=true` removes stale unread ids from watcher `seen_ids` so
  the normal watcher wake path can nudge them again.
- It never marks old unread rows read; those still require the receiver to
  actually inspect the message body.

**Acceptance criteria:**

- [ ] Dry-run reports all three debt classes without mutating inbox rows or
      watcher state.
- [ ] Apply mode backfills `seen_at` for already-read rows and leaves old unread
      rows unread.
- [ ] Optional rearm removes only matching stale unread ids from watcher
      `seen_ids`; unrelated ids remain.
- [ ] The tool writes an auditable `receipt_debt_cleanup` event with totals.

---

## Field Deployment Notes

These are invariants that production documentation must state explicitly:

1. **Message durability:** JSONL inbox messages survive any MCP server restart, wrapper crash, or machine reboot. The only way to lose a queued message is explicit `clear_bucket` or manual JSONL deletion. Field users should not fear data loss during MCP instability.
2. **Tool access during outage:** Bridge MCP tool calls (`check_inbox`, `send_to_peer`, etc.) will fail while the wrapper is down. This is a *tool access* failure, not a bridge protocol failure. The watcher continues to fire toasts and wake helpers independently of the MCP server.
3. **Recovery path:** HP4 `impact_class == "tool_access_risk"` + `recovery_hint` → `bridge_reconnect_mcp`. That is the complete self-service recovery flow.
4. **Transparent process self-heal, not tool-list hot-reload:** file-watch changes restart the affected bridge process layer, and the trampoline can relaunch `server_wrapper.py` after exit 77 without closing host stdio. Field users should still reconnect/reload the MCP host before expecting newly added, removed, or renamed tools to appear in the host's cached tool list.

---

### Tool-level behavior

#### HP-T1 - Read-only invariant

- [ ] No collector writes to bridge state files. Lint check (grep) verifies no `open(..., "w")` or equivalent in `core/health/`.
- [ ] Tests: snapshot called 100 times; bridge state file mtimes unchanged.

#### HP-T2 - Snapshot under 500ms (typical)

- [ ] On a healthy local single-machine bridge with <100 inbox entries and <10 sessions in history, `snapshot_duration_ms < 500`.
- [ ] Tests: synthetic state of representative size; assert ms ceiling.

#### HP-T3 - Partial failure resilience

- [ ] Any single collector raising returns `{"status": "error", ...}` for that slice; remaining metrics still computed; top-level `errors[]` lists the failures.
- [ ] Tests: each collector mocked to raise; snapshot still returns valid SnapshotV1 with partial data.

#### HP-T4 - Markdown rendering

- [ ] When `format="markdown"`, returns a single rendered string with sectioned tables: Overview, Sessions, Wakes, Inboxes, (Extended sections if requested).
- [ ] Color/icons via Unicode markers: `OK`, `!`, `X`, `||` (paused) - no emoji unless host requests.
- [ ] Tests: golden-string match for representative healthy/degraded/broken/paused snapshots.

#### HP-T5 - Symmetric across agents

- [ ] Same tool exposed to both Claude and Codex MCP clients with identical schema.
- [ ] Caller `agent` field used only to set the "self" session highlight in markdown rendering; does not change the data returned.
- [ ] Tests: invoking from claude vs codex returns the same snapshot for the same bridge state.

#### HP-FC1 - Forward-compat: tenant + machine scoping

- [ ] Snapshot includes `tenant_id` and `machine_id` fields (default `local-default` / local UUID for v1 single-machine).
- [ ] When tenant scoping (Tier-2 `BRIDGE_TENANT_SCOPING_SPEC.md`) ships, snapshot already filtered to `identity.tenant_id`; no schema change.
- [ ] Tests: v1 returns local-default; cloud-mode fixture returns scoped.

---

## Failure Modes

| Failure | Detection | Tool behavior |
|---|---|---|
| `state.json` missing | `FileNotFoundError` on read | HP1 = `error`; `overall_status = broken`; other metrics still attempted |
| `session.json` corrupted JSON | `JSONDecodeError` | HP2 = `error`; `overall_status = broken`; HP3-7 still computed |
| psutil import fails (rare on Windows; typical container) | `ImportError` | HP3, HP4, HP5, HP6 = `error`; HP1, HP2, HP7 still computed |
| Watcher lock present but PID dead | `pid_exists(pid) == False` | HP3 = `{"alive": false, "stale_lock": true}` |
| Wake cmdline unparseable | regex miss | wake entry has `target_agent: "unknown"`; not an error |
| audit.jsonl mid-rotation (concurrent write) | partial last line | tail skips partial line; HP9 returns what parsed |
| Snapshot exceeds 500ms | wall-clock | logged at WARN; `snapshot_duration_ms` truthful |

---

## Tests Required

**Unit (per collector):** see HP1-HP14 individual checklists above; ~3-5 cases each = ~50 unit tests total.

**Integration:**

- T-INT-1: snapshot of fully healthy bridge → `overall_status == healthy`; all core slices populated; no errors.
- T-INT-2: paused bridge → `overall_status == paused`; `bridge_state.paused == True`; watcher reported alive but idle.
- T-INT-3: stuck wake present → `overall_status == broken`; HP6 lists the wake; markdown render shows it under "Stuck Wakes".
- T-INT-4: corrupted `session.json` → `overall_status == broken`; HP2 in errors[]; HP3-7 still populated.
- T-INT-5: snapshot called from Claude session vs Codex session → identical snapshot bytes (modulo `snapshot_ts`).
- T-INT-6: 100 calls in succession → no state file mtimes changed.
- T-INT-7: extended-on with all subsystems shipped → HP8-HP14 populated.
- T-INT-8: extended-on with subsystems not yet shipped → HP8/HP11/HP13/HP14 = null gracefully.

**Performance:**

- T-PERF-1: 1000-message inbox, 50-session history → `snapshot_duration_ms < 1500`.
- T-PERF-2: 100-message inbox, 5-session history → `snapshot_duration_ms < 500`.

---

## Phased Rollout

**HP-Phase 1 (v1, ships first):** HP1, HP2, HP3, HP4 (extended wrapper metrics included), HP5, HP6, HP7 + HP-T1..T5. Markdown rendering. Read-only contract. This is the deliverable.

**HP-Phase 1b:** HP-R1 (`bridge_reconnect_mcp` companion tool). Ships alongside or immediately after HP-Phase 1; depends on HP4 audit-log reading being in place. Considered part of the same user story as HP4 — surfacing `impact_class == "tool_access_risk"` without a recovery action is incomplete for field users.

**HP-Phase 2:** HP9 (recent failures), HP10 (provenance summary), HP12 (last wake) - depend only on existing audit log; ship as soon as HP-Phase 1 is in.

**HP-Phase 3:** HP8 (pending actions; depends on Codex `83fbb6c5`), HP11 (D2 breaker; gated on `WAKE_HARDENING_SPEC.md` D2 ship).

**HP-Phase 4:** HP13 (schema versions; depends on `BRIDGE_SCHEMA_EVOLUTION_SPEC.md` SE1+), HP14 (cross-project; depends on cross-project pairing impl).

Each phase lands in its own commit, audited via the matching audit profile.

---

## Coordination Model

Codex implements; Claude reviews. Audit profile: `tools/agent-bridge/audit-profiles/health-panel.md` (added in same commit as this spec).

This spec is independent of Tier-2 forward-compat work. HP-Phase 1 ships in single-machine v1 with no schema changes elsewhere. Forward-compat fields (`tenant_id`, `machine_id`) are present from v1 with safe defaults; no follow-on bump needed when tenant scoping ships.

**Composes with:**

- `BRIDGE_PROTOCOL.md` exit codes - HP3 (watcher status) surfaces the last exit code if available.
- `BRIDGE_PRESENCE_SPEC.md` - if presence-cache file exists, HP-Extended can include it (future HP-Ext); not required for HP-Phase 1.
- `BRIDGE_BOOTSTRAP_PROVENANCE_SPEC.md` - HP2/HP10 surface the three-state provenance.
- `WAKE_HARDENING_SPEC.md` - HP11 surfaces D2 breaker state when shipped.
- `BRIDGE_TENANT_SCOPING_SPEC.md` - HP-FC1 forward-compat scoping.

**Does NOT compose with (deliberately):**

- Mutation tools. This spec is visibility only. Self-heal actions are separate proposals; the panel surfaces problems but does not fix them.

[[handoff:codex]]
