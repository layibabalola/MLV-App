# Audit Profile - Bridge Health Panel

**Trigger:** commit adding `bridge_health_panel` MCP tool, OR commit adding/modifying any collector under `core/health/collectors/`
**Reference spec:** `BRIDGE_HEALTH_PANEL_SPEC.md` HP1-HP14 + HP-T1..T5 + HP-FC1

---

## Files this profile covers

- `tools/agent-bridge/agent_bridge.py` (MCP tool registration: `bridge_health_panel`)
- `tools/agent-bridge/server.py` (tool dispatch, if separate from registration)
- `tools/agent-bridge/core/health/__init__.py` (snapshot orchestrator)
- `tools/agent-bridge/core/health/collectors/*.py` (per-metric collectors)
- `tools/agent-bridge/core/health/render_markdown.py` (markdown formatter)
- `tools/agent-bridge/test_health_panel.py` (test surface)

---

## HP-by-HP audit checklist

### HP-T1 - Read-only invariant (CRITICAL - audit first, fail-fast)

- [ ] Grep `core/health/` for `open(.*"w"` / `open(.*"a"` / `os.replace` / `Path.write_*` - must return zero hits
- [ ] Grep for any `mark_read`/`mark_seen`/`mark_handled`/`record_pending_*`/`resolve_pending_*` invocation - must return zero hits
- [ ] Test `T-INT-6` exists and asserts mtimes unchanged across 100 snapshot calls

### HP1 - Bridge state

- [ ] Reads `state.json`; surfaces `paused`, `paused_reason`, `paused_since`
- [ ] File missing/corrupt → returns slice with `status=error`; does not raise
- [ ] Tests: paused=true, paused=false, file missing, file corrupted

### HP2 - Active sessions

- [ ] Reads `session.json`; lists active sessions per agent
- [ ] Each entry has: `session_id`, `started_at`, `bootstrap_origin`
- [ ] Includes superseded sessions from last 1h
- [ ] Tests: parent / subagent / unknown / no-session / superseded-visible

### HP3 - Watcher status

- [ ] Stats `state/locks/watcher.lock`; verifies recorded PID via `psutil.pid_exists`
- [ ] Reports `alive`, `pid`, `heartbeat_age_seconds`, `last_poll_age_seconds`
- [ ] Distinguishes dead-watcher from gracefully-paused (cross-checks HP1)
- [ ] Tests: alive+fresh / alive+stale / pid-dead / lock-missing / paused

### HP4 - Server status

- [ ] Globs `state/server-pids/server-*.json`; per entry: `pid`, `agent`, `started_at`, `mtime_age_seconds`
- [ ] Dead PIDs flagged `stale_breadcrumb`
- [ ] Tests: live-only / live+stale / no-servers / corrupted-breadcrumb

### HP5 - In-flight wakes

- [ ] Iterates `psutil.process_iter(['pid','name','cmdline','create_time'])`
- [ ] Matches name in `{pwsh.exe, powershell.exe, python.exe}` AND cmdline contains wake entrypoint
- [ ] Per match: `pid`, `target_agent`, `target_session`, `started_at`, `age_seconds`
- [ ] Cmdline parse failure → `target_agent="unknown"` (NOT an error)
- [ ] Tests: zero / one / multiple / unparseable-cmdline

### HP6 - Stuck wakes

- [ ] Filters HP5 by `age_seconds > stuck_wake_threshold_seconds` (default 30)
- [ ] If any present: `overall_status = broken`
- [ ] `stuck_wake_threshold_seconds` arg honored
- [ ] Tests: boundary / multiple-stuck / threshold-override

### HP7 - Inbox summary

- [ ] Per known inbox: `unread_count`, `oldest_unread_age_seconds`, `handled_not_seen_count`, `handled_not_seen_oldest_age_seconds`
- [ ] `handled_not_seen_count` correctly identifies `read_at` set & `seen_at` missing rows
- [ ] Tests: empty / unread-present / handled-not-seen-present / mixed

### HP8 - Pending actions (gated on `83fbb6c5`)

- [ ] Reads `pending-actions/<agent>/*.json`
- [ ] Aggregates count by status; surfaces top 5 oldest unresolved
- [ ] Directory missing → returns null gracefully
- [ ] Tests: missing / empty / aggregate-counts / oldest-5

### HP9 - Recent failures

- [ ] Tails `audit.jsonl` last 50 entries
- [ ] Filters `event_class == "failure"` from last 5 min
- [ ] Each entry: `event_type`, `event_ts`, `agent`, `session_id`, `summary[:100]`
- [ ] Tests: no-failures / failures-present / mid-rotation-tolerant

### HP10 - Provenance summary

- [ ] Aggregates HP2 by `bootstrap_origin`; counts parent/subagent/unknown
- [ ] Flags `subagent_owns_active` if subagent is the active session for an agent
- [ ] Tests: all-parent / subagent-owns-active / unknown-counted

### HP11 - Wake circuit breaker (gated on D2 ship)

- [ ] Reads `state/wake-failure-windows.json` (or equivalent)
- [ ] Per session: `breaker_state` (closed/open/half-open), `consecutive_failures`, `last_failure_at`, `next_retry_at`
- [ ] File missing → null
- [ ] Tests: closed / open / half-open / file-missing

### HP12 - Last successful wake per peer

- [ ] Scans audit log; last `wake_succeeded` per `(agent, session_id)`
- [ ] Tests: never-woken / recently-woken / multiple-peers

### HP13 - Schema versions (gated on SE1+)

- [ ] Walks bridge root; per known persistent file: reports `schema_version` or `legacy`
- [ ] Tests: all-current / mixed / legacy-only

### HP14 - Cross-project (gated on cross-project pairing impl)

- [ ] Reads `state/cross-project-pairs/*.json`
- [ ] Per active link: `link_id`, `tier`, `expires_at`, `peer_project`
- [ ] Tests: no-links / active / expired

### HP-T2 - Performance

- [ ] T-PERF-2 asserts <500ms on representative state
- [ ] T-PERF-1 asserts <1500ms on large state
- [ ] `snapshot_duration_ms` field truthfully populated

### HP-T3 - Partial failure resilience

- [ ] Each collector mocked-to-raise; snapshot still returns valid SnapshotV1
- [ ] `errors[]` correctly populated
- [ ] No metric raises bubble out to MCP layer

### HP-T4 - Markdown rendering

- [ ] `format="markdown"` returns string with sectioned tables
- [ ] No emoji unless host requests; ASCII markers only (`OK`, `!`, `X`, `||`)
- [ ] Golden-string tests for healthy / degraded / broken / paused

### HP-T5 - Symmetric

- [ ] Tool registered for both Claude and Codex MCP clients
- [ ] Caller `agent` arg only affects "self" highlighting in markdown
- [ ] Identical bytes returned for same bridge state regardless of caller

### HP-FC1 - Forward-compat scoping

- [ ] Snapshot includes `tenant_id` and `machine_id` fields with v1 defaults (`local-default`, machine UUID)
- [ ] Tests: v1 default / cloud-mode-stub-fixture-scoped

---

## Required test pass

```bash
cd tools/agent-bridge
py -3 -m unittest test_agent_bridge test_health_panel
```

Plus performance suite (separate; may be opt-in via env flag):

```bash
HEALTH_PANEL_PERF=1 py -3 -m unittest test_health_panel.PerfSuite
```

---

## AUDIT_RESULT template

```
TYPE: AUDIT_RESULT
STATUS: pass | pass-with-followup | fail
SUMMARY: <SHA> Bridge health panel <subscope>
ACTION_REQUESTED: none | implement-deferred-followups | fix-readonly-violation
NONCE: audit-health-panel-<sha-short>
SCOPE: project-only

Reviewed <sha>. Coverage vs HP-Phase 1 (HP1-HP7 + HP-T1..T5 + HP-FC1):

| HP# | Status |
|---|---|
| HP-T1 | DONE / PARTIAL / FAILED |  (read-only - audit first)
| HP1 | DONE / PARTIAL / DEFERRED / MISSING |
| HP2 | ... |
| HP3 | ... |
| HP4 | ... |
| HP5 | ... |
| HP6 | ... |
| HP7 | ... |
| HP-T2 | ... |
| HP-T3 | ... |
| HP-T4 | ... |
| HP-T5 | ... |
| HP-FC1 | ... |

Extended (HP-Phase 2+; only if `include_extended` shipped):

| HP# | Status |
|---|---|
| HP8 | DONE / DEFERRED |
| HP9 | ... |
| HP10 | ... |
| HP11 | ... |
| HP12 | ... |
| HP13 | ... |
| HP14 | ... |

Read-only invariant verified: yes / no
Snapshot performance (HP-T2): <ms> on <state-size>
Tests at HEAD: <N>

[[handoff:codex]]
```

---

## Coordination

HP-Phase 1 is the "ship it" milestone for the Tier-0 diagnostic surface and is the priority audit. HP-Phase 2 (HP9, HP10, HP12) ships next - all depend only on existing audit log infrastructure.

HP-Phase 3+ depends on subsystems still in flight. Treat HP8/HP11/HP13/HP14 as "wire to null gracefully when subsystem absent" - the spec is explicit that these collectors return null, not error, when their backing state is missing. This lets the panel ship before all dependencies land.

**Critical fail-fast:** if HP-T1 (read-only invariant) is violated by any commit in this scope, audit STATUS must be `fail` regardless of other coverage. The whole point of the panel is to be safe to invoke at any moment, including from a paused/broken bridge - any mutation breaks that contract.
