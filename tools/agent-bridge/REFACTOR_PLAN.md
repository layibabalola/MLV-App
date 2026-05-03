# Agent Bridge Refactor — Canonical Plan v1.1

**Status:** Approved by Claude and Codex (2026-04-28). Baseline hardening has shipped across contracts, receipts/status, import-safe MCP, watcher leases, receipt-verified wake retry, explicit bucket tools, recovery diagnostics, Codex wake-storm prevention, provenance/wrong-chat defense, component supervision, WR1-WR3 wake recovery loops, session-routing remainder, cross-project pairing MVP, knowledge-sharing/policy-authority spec closures, initial Tier-2 local auth/transport/schema seams, remote-obedience classification, dynamic watcher active-session binding, Claude bootstrap Monitor guardrails, pairing-intent gating, explicit ephemeral relays, non-primary session caps, PREFLIGHT wake safety in `wake_codex.ps1`, local dashboard backend surfaces, `bridge_health_panel` MVP, health-panel recommended remediation actions, stale-unread watchdog/rearm, receipt-debt cleanup/migration, CR Wave 1 cross-process storage locks / unique temp files / JSONDecodeError guards, a localhost token/CSRF dashboard server, same-project guided-pairing backend v1, dashboard status surfaces for catch-up / contract reauth / policy-doc drift, a fail-closed diagnostic `wake_claude.ps1` boundary, Phase 13 configurable-root closure, bounded Hypothesis/property routing coverage, a concurrent project-send harness, a Phase 17 local-v1 security review snapshot, and a workflow guardrails spec that separates mandatory invariants from configurable workflow preferences. Deeper service extraction, cross-project file read/write tools, richer guided pairing/dashboard UI, catch-up preview/policy drift proposal UI, optional live Desktop targeted-wake dogfood, stronger stop-time enforcement for mandatory workflow guardrails, and a real thread-addressable Claude wake primitive remain follow-up work.

**Status checkpoint (2026-05-01 14:05 America/Chicago):**
- User explicitly confirmed the current Codex Desktop thread as the active pair
  target. The exact session ids, pair id, and Desktop thread id are runtime
  state and belong in bridge state/audit artifacts, not the durable roadmap.
- The health panel and stale-unread watchdog are now explicit roadmap items and shipped as code: `bridge_health_panel` reports old/stale unread debt, `stale_unread_watchdog` can detect and optionally rearm delivered-but-unread wake gaps, and both are exposed through the MCP server.
- Verification: focused health/watchdog tests passed, and the full Agent Bridge unit suite passed locally (`167 tests OK`). `psutil` is now in `requirements.txt` and installed in the local Python runtime used for live diagnostics.
- Live state after cleanup: stale server PID markers were compacted, the watcher was restarted for `signature_changed`, and bridge health improved from `broken` to `degraded`. Remaining degradation is real receipt/inbox debt, not stale process-marker noise.
- Subagent pairing-aggression finding: chat/audit logs showed side/spawned Codex threads were classified as `parent` and then promoted by `bootstrap_trusted_parent_drift_auto_superseded` because the previous trusted parent's bootstrap PID was dead. That predicate is invalid because `bootstrap_session.py` is intentionally short-lived; a dead bootstrap PID is normal and must not prove the parent thread is gone. Future fixes must refuse trusted-parent thread drift unless an explicit repair/pair command, valid rollover flow, or stronger parent proof exists. UI title text such as `spawned agent thread` can be an extra defensive heuristic, not an authority.
- Receipt-debt cleanup/migration shipped on 2026-05-01 15:06 America/Chicago: `receipt_debt_cleanup(...)` reports read-without-seen, old-unread, and stale-unread debt; dry-run is default; apply mode only backfills `seen_at` for already-read rows and optionally rearms stale wake ids. Verification: focused receipt/health tests passed, one full suite run hit a transient localhost dashboard connection abort, rerun passed (`169 tests OK`).
- Health panel productization shipped on 2026-05-01 15:14 America/Chicago: `bridge_health_panel` now returns `recommended_actions` and `recovery_hint`; health/dashboard markdown renders the next safe command; docs state mutating recovery recommendations are hints with rejected/no-op/partial failure paths. Verification: focused health/dashboard tests passed; full suite passed (`171 tests OK`).
- Claude Monitor self-healing slice AC79-84 shipped on 2026-05-02: `bridge_monitor_poll.py` writes `monitor-claude-<session>.runtime.json`, bootstrap classifies missing/stale/misbound Monitor evidence and emits the exact repair command, health/dashboard surfaces `CLAUDE_MONITOR_STALE` / `CLAUDE_UNREAD_WITHOUT_MONITOR` with stuck ids, the watcher escalates old unread Claude work without marking it read, and the watcher sends `MONITOR_RESTART_REQUIRED` control mail when the Monitor is stale/missing or bridge Monitor-related code changes. Verification: full Agent Bridge suite passed (`235 tests OK` for AC79-82; AC84 has dedicated regression tests).
- CR Wave 1 shipped on 2026-05-01 15:25 America/Chicago: shared storage helpers now use per-file cross-process directory locks, per-process/thread/uuid temp files, and corrupt-JSON-safe reads where appropriate; watcher hot paths use the locked storage helpers for inbox reads, watcher-state writes, wake audit, and wake breaker state; watcher refreshes/merges `seen_ids` from disk to preserve rearm/concurrent state changes. Verification: focused process-writer/watcher tests passed; one full suite run hit the known transient localhost dashboard abort, rerun passed (`175 tests OK`).
- DR Wave 1 shipped on 2026-05-01 15:34 America/Chicago: documentation contradictions DR-A1, DR-A2, and DR-A11 were resolved. `PREFLIGHT_DETECTION_SPEC.md` now treats window title text as diagnostic-only, separates watcher-layer seen/policy outcomes from pre-flight receipt rules, and tests stale-context via breadcrumb/session/thread identity. `ARCHITECTURE.md` and `USER_GUIDE.md` now state Claude Monitor is per-context, does not survive compaction, and must be restarted/confirmed after session rollover.
- MCP reconnect validation shipped on 2026-05-01 15:43 America/Chicago: process health now classifies MCP wrapper relaunches separately from inner `server.py` hot-reloads, reports whether post-launch tool activity suggests the host recovered, degrades health on `tool_access_risk` / `client_reconnect_likely_required`, and documents the stdio limitation that a detached wrapper cannot repair an already-broken host pipe. The full discovery suite exposed and fixed an adjacent wake-receipt invariant bug: successful wake retries remain pending until `seen_at` / `read_at` appears instead of being added to watcher `seen_ids`. Verification: wrapper validation tests passed; full Agent Bridge discovery passed (`214 tests OK`).
- App-native heartbeat wake smoke ran on 2026-05-01 21:30 UTC: Codex Desktop
  injected a visible `Sent via automation` turn into the paired thread, checked
  the old Codex private session plus `mlv-app`,
  found both empty, and the one-shot automation was deleted. This proves
  same-thread app-native heartbeat wake viability. The visible transcript is a
  product affordance for interactive pairing because the user can see wake,
  inbox check, and disposition in chat; remaining productization questions are
  cadence limits, cost, lifecycle cleanup, and how it coexists with
  targeted-sendkeys/app-server wake providers.
- Silent CLI failure remediation shipped on 2026-05-01 17:12 America/Chicago:
  the local `agent_bridge.py check-inbox` command now has a real argparse
  entrypoint, JSON output, and nonzero rejection path. `codex_pre_response.ps1`
  and `codex_pre_final.ps1` now act as canaries by probing
  `agent_bridge.py check-inbox --help` before printing reminder state, so a
  future no-op CLI regression fails loudly instead of being mistaken for an
  empty inbox. The same hardening pass replaced direct reminder-log
  `Add-Content` calls with bounded retry plus visible diagnostics, after the
  canary uncovered transient log-file contention. This incident also added the
  no-silent-success process rule to `bridge_trigger_heuristics.md`.
- Phase 18 final readiness passed 10/10 validation: Claude and two full-scope stranger-agent reviews scored the shipped workflow-guardrail hardening 10/10. Remaining items such as AgentBridge facade/service extraction, cross-project guided dashboard polish, catch-up preview/policy drift proposal UI, optional live Desktop targeted-wake dogfood for broad distribution, and a real Claude thread-addressable wake primitive are explicit follow-up roadmap work, not Phase 18 blockers.

**Status checkpoint (2026-05-03 America/Chicago):**
- Phase 18 final 10/10 validation loop complete: Claude 10/10, two full-scope stranger-agent 10/10 reviews (Stranger 2 initially 8/10; Unicode-safe CLI gap fixed in 58e5a5ef and re-scored 10/10). 250 tests OK. User sign-off pending.
- fork/master fast-forwarded to 58e5a5ef; remote integration branch deleted.
- Pending worktree cleanup: watcher-config `repo_root`, Codex wake script paths, and the running watcher runtime now point at the main checkout (`C:\!Layi Wkspc\MLV-App`). The former integration directory at `C:\!Layi Wkspc\MLV-App\.claude\worktrees\festive-boyd-integration` is no longer registered by `git worktree list` and is empty, but Windows refused removal because another process still holds a handle to that directory. Cleanup is now blocked on that handle being released, not on watcher migration.

**Follow-up roadmap items identified 2026-05-03:**
- `server_wrapper.py` self-healing shipped (Fix A + Fix B, 251 tests). Desktop restart required once to bootstrap onto new wrapper; after that restarts are no longer needed for code-change or crash scenarios.
- Wake script composer cleanup on failure shipped in the 2026-05-03 Codex follow-up pass: `wake_codex.ps1` now tracks unverified wake injection and uses a final cleanup pass to clear exact-match stranded wake text after unhandled failure paths, in addition to existing postflight/clipboard cleanup.
- Superseded session backpressure confusion shipped in the 2026-05-03 Codex follow-up pass: backpressure counting, health/dashboard surfacing, `send_to_peer`, and `nudge_peer` now exclude registered non-active/superseded session buckets from work backpressure. Superseded unread rows remain visible for receipt hygiene, but only active session/project buckets gate sends and wake rearm.
- Targeted SendKeys thread proof/restore follow-up: the stale archived `desktop_thread_id` pin and stranded composer text are fixed in the May 3 wake-routing patch, and the wake contract now carries an explicit `RestoreThreadId` slot. Codex Desktop still often exposes only the generic UIA root title `Codex`; generic titles are treated as unknown rather than false project mismatches.
- Stage 6 restore-thread UUID gap: the strict guard still fails closed with retryable exit 16 when `RestoreThreadId` is empty and Codex itself is the foreground app on a different or unprovable thread, but the production targeted watcher now opts into explicit delivery priority with `-AllowForegroundCodexThreadDisplacement`. In that mode a valid target thread may be opened without exact restore and logs `targeted_wake_delivery_priority_no_restore`, because near-realtime inbox delivery is preferred over non-stranding. Durable future work remains a real previous-thread identity primitive or a live-rendering app-server wake path.
- Phase 18 dashboard guardrail-debt visibility and initial stop-time enforcement shipped in the 2026-05-03 Codex follow-up pass: `dashboard_overview` reads `state\guardrail-debt.jsonl` without mutation and surfaces active debt by severity, guard id, enforcement tier, session, and remediation in JSON and markdown status surfaces. `codex_pre_final.ps1` / `codex_bridge_reminder.ps1` now include active guardrail debt in the final digest and emit a `FINAL-GUARD` before 10/10 closeout while scoped rows remain open. Broader automatic WGI debt emission remains follow-up work.
- Dashboard Launcher UX shipped in the 2026-05-03 Codex dashboard follow-up pass: `dashboard_launcher.py` and the Windows double-click launcher start/reuse a singleton local dashboard, prefer hidden/background launch, write `dashboard-launcher.runtime.json`, health-check and restart the HTTP server during the launcher lifetime, expose a dashboard Stop button via `/api/shutdown`, auto-refresh dashboard JSON every 5 seconds without full reload, and provide the `open_dashboard` MCP tool while returning only a token-free URL to chat.
- Dashboard operator UX follow-up: the live dashboard now preserves scroll/focus/details state across refreshes, supports pausing automatic refresh while keeping manual refresh active, uses in-dashboard modals instead of native prompts, pins the primary recovery command, collapses stable surfaces into readiness chips, and exposes `/api/recommended-action` only for allowlisted local remediations such as stale server marker cleanup and read-receipt backfill.

**Restart checkpoint (2026-04-28 16:00 America/Chicago):**
- Codex and Claude Desktop configs have already been backed up and updated to launch `tools\agent-bridge\server_wrapper.py --bridge-root C:\Users\obabalola\.agent-bridge`.
- Backups: `C:\Users\obabalola\.codex\config.toml.bak-20260428T160012` and `C:\Users\obabalola\AppData\Roaming\Claude\claude_desktop_config.json.bak-20260428T160012`.
- Active Codex private bucket is `fadda757-5bbe-4a6c-9def-f27a04d118f4`; Codex notify config was corrected from superseded `9111dce5-3d33-4d06-b7a7-87dbf259b0c6`.
- `recover_state.py --scan-historical` against `C:\Users\obabalola\.agent-bridge\state` is healthy; no stale-root or migration-history issues were reported.
- Project-bucket backlog was surfaced and marked read for `3a6f6a03-b07a-4f87-91c7-88aef54b5ac7`, `ffcd534c-70ed-4229-bf5d-f12b270ed47f`, `82c724ec-ce33-4a20-ad1e-5c0ac5a1f05a`, `b3207397-7671-4a88-8243-d7204748b526`, and `1429d520-c80c-406f-852f-5b03a9513b51`.
- Important correction: current `server_wrapper.py` resolves/rejects/audits and then `execv`s `server.py`; it does **not** monitor mtimes or auto-respawn on bridge code changes yet.
- Post-restart first checks: run `recover_state.py --scan-historical`, verify `bridge_process_status` shows new runtime breadcrumbs for MCP servers, then check Codex private bucket plus `mlv-app`.
- Historical note: Claude ACTION_REQUEST `82c724ec` originally asked for
  `wake_codex.ps1 -ExpectedTitleMarker` and matching watcher-config
  plumbing. User direction on 2026-04-28 removed the title-marker layer as
  a dead-end heuristic on this Windows host. Current follow-up priority is
  breadcrumb-driven peer identity plus fire-time watcher command-template
  resolution (see `AUTO_PAIR_SPEC.md` and `PHASE_B_BREADCRUMB_DESIGN.md`).

**Inputs synthesized:**
- Claude first draft: bridge message `fd2b5d8c`
- Codex first draft: bridge message `5161697e`
- Claude hybrid: `91327d57` (with Phase 7.X parent-only wake targeting)
- Codex independent hybrid: `4dc78d47`
- Claude final canonical: `0cf7467c`
- Codex final canonical: `37543812`
- Reconciled v1.1: `b7b3da65` → Codex approval `5a8ebc18`

---

## Prerequisites for Standalone Repo Extraction

Before agent-bridge is extracted into its own standalone repository/product,
the closeout hooks must be made portable. Currently three files hardcode
`"mlv-app"` as the project bucket:

- `tools/agent-bridge/codex_pre_response.ps1` (lines 42-43)
- `tools/agent-bridge/codex_pre_final.ps1` (lines 42-43)
- `tools/agent-bridge/codex_bridge_reminder.ps1` (line 4)

**Complete technical spec:** `.claude-state/closeout-config-driven-approach-v5.md`
- Rated 10/10 by two independent cold reviewers (7 review rounds, all issues resolved)
- Implementation-ready — no open blockers
- Adds `main()` CLI entrypoint to `project_identity.py`, updates all three PS1
  hooks to auto-derive the project bucket from the workspace git root, adds tests

**Gate:** This change must be implemented **before or as part of** the extraction.
Extracting with hardcoded `"mlv-app"` and then patching portability in the new
repo is more fragile than shipping portability here first. Claude and Codex both
have the spec; either agent can drive implementation when extraction is next on
the roadmap.

---

## North Star

The bridge should be a **small, local, durable message bus with explicit protocol invariants.** It must not depend on agents remembering hygiene rules, on raw UI focus tricks being interpreted as delivery, or on undocumented global state staying lucky.

A 10/10 implementation is:
- correct under concurrency
- recoverable after crashes and compaction
- observable without manual JSONL spelunking
- honest about what is delivered, seen, read, and handled
- safe around sub-agents and multiple Codex/Claude sessions
- documented only where behavior is actually shipped

---

## Canonical Completion Standard

This is the user-defined standard for calling any Agent Bridge implementation,
roadmap item, or hardening phase finished. It is stronger than "tests passed"
and stronger than one agent's confidence score.

1. Every roadmap acceptance item is either implemented or explicitly removed
   from scope by user + Codex + Claude agreement.
2. Codex iterates locally until its own `READINESS_ASSESSMENT` is 10/10 or it
   names concrete blockers.
3. Codex sends Claude the test matrix, failure-path coverage, score, changed
   files, remaining risks, and any explicit scope exclusions.
4. Claude independently reviews the same implementation, using its own
   stranger/cold-review or failure-path pass, until Claude also rates the work
   10/10 or names concrete blockers.
5. Two or more background agents acting as strangers independently review the
   implementation and must rate it 10/10, or name concrete blockers, before the
   work can be called final.
6. Any Codex/Claude/background-agent disagreement becomes `RISK_DELTA` and is
   resolved by code, tests, docs, or explicit user-approved scope change before
   final signoff.
7. The final hardening/readiness status is canonical only when Codex, Claude,
   two or more background stranger agents, and the user all agree.

This standard applies to every Agent Bridge implementation checkpoint, not just
the final roadmap closeout. Small changes may use a proportionate test matrix,
but they still require explicit peer notification, ledger state, and independent
review before being called complete.

Roadmap and todo/ledger iteration are part of the standard. After completing or
parking one item, Codex should immediately choose the next safe highest-impact
roadmap, todo, or pending-ledger item and continue working without waiting for
another user prompt, unless the next step requires a non-obvious product/security
decision, external credentials, destructive action, or explicit user choice.

When an implementation reaches a checkpoint, Codex notifies Claude for review
and iterates on Claude change requests until Claude would rate the work 10/10.
Codex also uses two or more background stranger reviewers and iterates on their
change requests until they would rate the work 10/10, or records the named
blockers in the ledger.

When Codex is waiting on background agents, "waiting" is an active state, not a
place to stop. Codex must either keep doing non-overlapping work or block on the
agent result. If a reviewer does not return at the first checkpoint, Codex asks
that agent for an ETA, records the ETA or lack of response, checks back at the
ETA, and requests a renewed ETA before yielding again.

Implementation must be tandem by default. For shared Agent Bridge work, Codex
sends Claude `IMPLEMENTATION_START` before material edits, then sends
`IMPLEMENTATION_UPDATE`, `REVIEW_REQUEST`, or `READINESS_ASSESSMENT` at each
meaningful checkpoint. Claude should be able to review while implementation is
still fresh, not only after a final closeout.

---

## Frozen Protocol Invariants

These are the immovable contracts. Every phase serves these.

1. No superseded, ended, or orphaned sender can send normal work traffic.
2. Normal work traffic targets only session or project inboxes — never agent-level recovery inboxes.
3. Agent-level inboxes are durable control/recovery paths and remain available even when session/project inboxes are full.
4. No unread work message may be marked read by watcher, bootstrap, end_session, or compaction unless surfaced, explicitly read by tool call, or quarantined with audit metadata.
5. `check_inbox(session_id=None)` has one explicit contract: scans all unread buckets for that agent (canonical: diagnostic mode). Never silently means `default`.
6. `default` is dead. No public destructive/write op may silently normalize to `default`.
7. **Wake spawn is not delivery.** Delivery/wake success is proven by receipts: `seen` / `read` / `handled` / `failed`.
8. Routine ACK messages are opt-in protocol traffic only; normal receipts attach to the original message metadata.
9. Singleton background daemons use leases with command-hash + generation; MCP stdio servers remain multi-instance.
10. Sub-agents cannot mutate parent/global Codex wake targets.

---

## Target Architecture

`AgentBridge` becomes a thin facade/orchestrator (~150 lines) composing focused services.

```
tools/agent-bridge/
├── core/
│   ├── storage.py          # JSON/JSONL IO, atomic writes, owned locks, corruption quarantine, schema versions, migrations
│   ├── schema.py           # typed records (TypedDict/dataclasses), validation, compatibility helpers
│   ├── registry.py         # project/session registry; active/superseded/ended/orphaned lifecycle; pairing; sender liveness
│   ├── addressing.py       # AgentInbox / ProjectInbox / SessionInbox / MessageKind / SenderContext
│   ├── routing.py          # pure RoutingResolver (no filesystem); table-tested
│   ├── inbox.py            # InboxStore: append/query/promote/mark_read/mark_seen/mark_handled/compact (no routing)
│   ├── control.py          # ControlPlane: typed control messages, scoped replacement (bucket+control_type+source)
│   ├── backpressure.py     # per-level limits + rate limiting scoped by (project, sender, target)
│   └── receipts.py         # message_status, mark_seen, mark_handled, list_pending_receipts
├── lifecycle/
│   ├── bootstrap.py        # activation + handshakes + watcher config (no silent consumption)
│   └── processes.py        # role leases, watcher start/reclaim, bridge_process_status
├── watcher/                # polling + hot-reload + notification only — NO consumption
├── wake/                   # Codex wake backends (clipboard-paste preferred), delivery markers, parent-thread targeting
├── transport/
│   ├── mcp_server.py       # FastMCP factory; create_bridge → create_mcp → main; no import side effects
│   └── bridge_result.py    # BridgeResult + BridgeStatus enum
├── diag/                   # safe probes, recover_state.py, compact.py + reaper, docs/test helpers
└── tests/
    ├── unit/
    ├── integration/
    └── property/           # Hypothesis (dev-only dependency)
```

---

## Phase Plan

### Phase 0 — Contract freeze (failing-then-passing tests)

Refactor depends on these. All 13 tests must fail before implementation begins; existing 18 tests must still pass.

1. `check_inbox(None)` scans all buckets or rejects explicitly; never `default`.
2. Superseded sender cannot send normal work to an active target bucket.
3. Agent-level bucket rejects normal work but accepts recovery/control.
4. `default` is rejected consistently by send/clear/reset/status where applicable.
5. `send_control_message(replace_existing_control=True)` only replaces same `(bucket, control_type, source)` tuple unless explicit `squash=True`.
6. `end_session` and bootstrap do not silently mark unread work read.
7. **Wake command failure does NOT add message id to watcher `seen_ids`** (live-demonstrated bug).
8. Concurrent parent + sub-agent `configure_watcher` preserves parent wake target.
9. **Sub-agent context cannot mutate `parent_thread_id` or global Codex wake target.**
10. Bootstrap watcher-config test does NOT leave a real daemon subprocess running.
11. `wait_inbox` rejects non-string `session_ids` cleanly at MCP boundary (`list[str]` typed).
12. `clear_bucket` clears associated dedupe / `seen_hash` state if that state remains a concept.
13. Probe tooling cannot mutate live bridge state unless explicitly run with `--mutate`.

### Phase 1 — Storage, locks, schema versions

Extract `core/storage.py` first because every later phase depends on trustworthy persistence.

- Owned file/lease lock abstraction; no stale rmdir without owner metadata
- Windows-safe atomic write with bounded retry and corruption quarantine
- `schema_version` field in state.json, session.json, inbox row headers
- Migration skeleton v1 → v2 (path exists; no migrators yet)
- Tests: corrupt JSON, partial writes, lock timeout, concurrent append/mark_read

### Phase 2 — Typed addresses and pure routing

Introduce the protocol model under the existing API.

- `Address` classes: `AgentInbox(agent)`, `ProjectInbox(project, agent)`, `SessionInbox(project, agent, session_id)`
- `MessageKind`: `work` | `control` | `recovery` | `receipt` (4 values)
- `SenderContext`: `from_agent` + `sender_session_id` + `project`
- `RoutingResolver` pure function: `(SenderContext, Address, MessageKind, registry) → Delivered | Rejected | Escalated`
- Routing matrix tests: active, superseded, ended, orphaned, unknown, project, agent-level, missing sender session

**Compatibility rule:** old `send_to_peer` MCP signature stays as a shim; internally normal work must prove sender liveness or reject (no guessing).

### Phase 3 — InboxStore + ControlPlane + BackpressurePolicy

Separate persistence from routing.

- **InboxStore** owns append/query/promote/mark_read/mark_seen/mark_handled
- **ControlPlane** owns HANDSHAKE / SESSION_UPDATE / ROUTE_REPAIR / RESTART_ACK creation + scoped replacement policy
- **BackpressurePolicy**: session strict 1, project configurable default 5, agent unlimited for control/recovery only
- Rate limiter scoped by `(project, sender, target)` — not only global agent pair

### Phase 4 — Receipts and message status

**Receipts become the source of truth for stuck-message diagnosis and wake success.**

- New row fields: `seen_at`, `seen_by_session`, `seen_via`, `triggered_by`, `read_at`, `handled_at`, `handled_by_session`, `handled_status`, `failure_reason`
- New tools: `message_status(id)`, `mark_seen(id, via)`, `mark_handled(id, status, reason?)`, `list_pending_receipts(filter)`
- `check_inbox` / `wait_inbox(mark_read=False)` records `seen_at` unless `record_seen=False`
- `peek_inbox` remains pure unless `record_seen=True`
- `mark_read` is consumption only; never implies `handled`
- `MESSAGE_RECEIPTS_SPEC.md` flips from "Proposed" → "Implemented"

### Phase 5 — Session lifecycle and promotion

Finish hierarchy semantics before deeper wake/process work.

- Explicit state machine: `active → superseded | ended | orphaned`
- Superseded session messages promote to project bucket WITHOUT `read_at`
- Orphan TTL → promotion path for crash-only disappearance
- Bootstrap surfaces promoted/drained messages but does NOT mark work read unless current turn actually surfaces them
- SESSION_UPDATE controls scoped; cannot delete unrelated controls

### Phase 6 — Process ownership and health

**Implements `PROCESS_OWNERSHIP_SPEC.md`** for daemons; MCP servers stay multi-instance.

- Role leases: `state/locks/watcher.lock`, optional `monitor-<agent>-<session>.lock`
- Lease fields: `pid`, `parent_pid`, `process_name`, `command_line_hash`, `state_dir`, `agent`, `project`, `session_id`, `started_at`, `heartbeat_at`, `generation`
- **Never kill by PID alone** — verify command_line_hash + heartbeat freshness + generation
- MCP server markers stay per-process `server-<pid>.pid`; never singleton
- New `bridge_process_status()` MCP tool — non-overlapping with existing `bridge_status`
- Graceful shutdown handlers (SIGINT/SIGTERM/atexit) clean up leases
- `PROCESS_OWNERSHIP_SPEC.md` flips from "Proposed" → "Implemented"

### Phase 7 — Watcher and wake hardening

Use receipts + leases to make wake honest.

- Transactional `configure_watcher` under config lock
- `on_message_command` stored as **argv array, not shell string**
- Watcher executes wake synchronously with bounded timeout, OR records attempt-id and awaits delivery marker / receipt
- Watcher only records seen/delivery after receipt or verified wake marker — **never on Popen-spawn** (the live-demonstrated bug)
- Failed wake leaves message retryable with backoff and visible status
- `wake_codex.ps1` returns nonzero exit codes for: no window / bad ThreadId / failed foreground / paste/send failure
- Automated Codex wake requires protected `parent_thread_id`; unsafe window-scoped mode is explicit/manual only
- **Clipboard-paste pattern** preferred over raw SendKeys typing (IME-safe, focus-race resistant) — but treat as wake backend, NOT delivery truth

**Phase 7.X — Parent-only wake targeting:**
- `parent_thread_id` is a typed, validated, protected config field
- Sub-agent contexts cannot mutate it from ambient `CODEX_THREAD_ID`
- Parent provenance is explicit (allowlist), not inferred from UUID shape
- Contract test simulates sub-agent update; proves parent target survives

### Phase 8 — MCP/API/CLI cleanup

Strict and import-safe public contracts.

- `server.py` factory split: `create_bridge(args)` → `create_mcp(bridge)` → `main()`
- Importing server module has NO `parse_args` / PID marker / side effects
- Strict schemas: `list[str]` not `list`, typed enums, explicit destructive params
- Accurate tool annotations (especially control replacement / destructive tools)
- Rename: `clear_inbox` → `clear_bucket`, `reset_session` → `reset_bucket`; explicit `bucket: Address` param required
- `probe_server.py` reworked: argparse + temp state by default; `--mutate` required for live sends
- Replace `Dict[str, Any]` in `BridgeResult.data` with TypedDicts per result type
- Codex config guidance version-gated (`tool_timeout_sec` / `startup_timeout_sec` broke Codex 0.111)

### Phase 9 — Tests and verification harness

Test pyramid around the new seams.

- Unit tests for storage, registry, routing, inbox, receipts, backpressure (≥85% line coverage on `core/*`)
- Integration tests against temp state and MCP tools
- Concurrency harness: 4 threads × 50 sends; assert no loss / duplicates / hop-count gaps
- Stress test: concurrent `configure_watcher` + `ensure_watcher` parent + sub-agent simultaneous bootstraps
- Optional Hypothesis property tests (dev-only dependency; doesn't gate Phase 0)
- PowerShell/wake tests with mocks where possible
- Docs contract tests: quoted Windows paths, `mlv-app` (not `mlvapp`), no `default` fallback, no unsupported Codex config keys

### Phase 10 — Docs consolidation

- One canonical `ARCHITECTURE.md` covering protocol + hierarchical inbox + wake paths + state layout + recovery
- `STATE_LAYOUT.md` for `~/.agent-bridge/` directory contract
- README portable (replace hardcoded paths with `%USERPROFILE%`)
- Move `BRIDGE_HARDENING.md` audit log → `docs/AUDIT_HISTORY.md`
- All specs labeled "Implemented" or moved to `archive/proposals/`
- Align `AGENTS.md` / `CLAUDE.md` / `bridge_trigger_heuristics.md` / protocol docs / hierarchy docs

### Phase 11 — Recovery and housekeeping

- `recover_state.py` validates and rebuilds state/session/inbox from audit trail; mandatory backup-before-repair; dry-run default
- Compaction extended: stale `server-pids/` reaper (24h TTL), `.quarantine.jsonl` size cap with rotation, old read-rows pruning beyond retention, receipt log rotation
- `message_status` and `bridge_process_status` are first-line diagnostics
- Structured logs for routing, wake attempts, leases, migrations, recovery

### Release hardening follow-up — startup, reconnect, and field resilience

This is a release hardening track for the current wrapper-plus-child architecture.
It does not change the architectural direction; it validates and hardens the
startup/reconnect path so routine bridge use should not require Claude Desktop
restarts in the field.

- Hammer cold-start and reconnect paths repeatedly until `initialize`,
  `tools/list`, and first real tool call are consistently prompt across many
  consecutive runs.
- Add a repeatable validation matrix for:
  - cold Claude start with healthy bridge
  - reconnect after prior MCP timeout/failure
  - wrapper alive while child `server.py` restarts
  - child crash during active session followed by wrapper-managed recovery
- Simulate child crash/restart during active sessions and verify:
  - wrapper stays stable
  - Claude-side tool surface remains usable after recovery
  - no unread/read/handled state corruption occurs during restart windows
- Validate on multiple cleaner Windows environments, not just the primary dev
  box, to catch PATH, Python, permissions, antivirus, and pipe/runtime quirks.
- Extend health signaling so diagnostics clearly distinguish:
  - `healthy`
  - `degraded_but_recoverable`
  - `client_reconnect_likely_required`
  - `broken`
- Ensure these states surface through `bridge_process_status`,
  `bridge_health_panel`, audit logs, and operator-facing runbooks.
- Productize HP-Phase 1 as the first user-facing status surface:
  - `bridge_health_panel(format="markdown", include_extended=True)` should be
    the default answer to "show bridge health" and "what is happening?"
  - stale-unread counts must be visible without JSON spelunking
  - users should see watcher status, active sessions, paired thread, wake
    history, breaker state, and receipt gaps in one snapshot
- Keep `stale_unread_watchdog` as the active companion to HP7:
  - diagnostic mode lists wake-delivered unread rows
  - rearm mode removes matching ids from watcher `seen_ids`
  - health status degrades when stale unread rows exceed the threshold
- Treat any issue that can force a Claude restart during normal steady-state use
  as release-blocking until either fixed or explicitly documented as a client
  limitation outside bridge control.
- **Trusted code-signing certificate for bridge PowerShell scripts** (field
  deployment prerequisite):
  - `wake_codex.ps1`, `codex_bridge_reminder.ps1`, `codex_pre_response.ps1`,
    `codex_pre_final.ps1`, and `wake_claude.ps1` each use APIs (UIA
    `SetFocus`, `AttachThreadInput`, `SetForegroundWindow`, clipboard
    set/restore, `SendKeys`) that trigger AV behavioral heuristics when
    unsigned. Signing with a trusted Authenticode certificate drops these flags
    to near-zero for all major AV/EDR vendors.
  - Procurement path: OV (Organization Validation) code-signing certificate
    from a CA trusted by Windows (DigiCert, Sectigo, GlobalSign). EV
    (Extended Validation) provides higher immediate SmartScreen reputation
    but requires a hardware token; OV is sufficient for AV suppression.
  - Signing must be applied to every release; add a `Sign-BridgeScripts.ps1`
    helper that wraps `signtool.exe /fd sha256 /tr <RFC3161-TSA>` for
    reproducible timestamped signing.
  - Until a cert is procured: field users with aggressive EDR (CrowdStrike
    Falcon strict, MDE Attack Surface Reduction) should add the bridge
    scripts directory to AV exclusions as a documented workaround. Log this
    gap in the field-deployment runbook.
  - Signing does NOT help for `BlockInput` — that API is removed from the
    bridge codebase (2026-05-01) in favor of clipboard-paste injection, which
    has no equivalent AV signature.

### Phase 12 — Dead-code decisions

**No zombies in tree.** Each ambiguous component gets explicit keep/wire/archive:
- `routing_rules.py` / `routing_policy.py` / `evaluate_routing` — wire into `send_to_peer` automatic-bridge OR archive as opt-in CLI tool
- Codex hooks (`codex_pre_response.ps1`, `codex_pre_final.ps1`, `codex_bridge_reminder.ps1`) — keep best-effort or delete
- `codex_bridge_watch_mode.ps1` + `bridge_watch_mode.flag` — keep opt-in or delete
- `consume_inbox.py` — already narrowed; verify still useful as CLI diagnostic
- Old probe scripts
- Legacy `default` compatibility shims

### Phase 13 - Configurable bridge root and migration

Make the bridge root a first-class, user-selected location instead of a
hardcoded `%USERPROFILE%\.agent-bridge` convention. The goal is one coherent
root that every MCP server, watcher, helper script, routing tool, and recovery
tool resolves the same way.

- Add a `BridgePaths` / root resolver used by all bridge code:
  - discovery order: explicit `--bridge-root`, `AGENT_BRIDGE_ROOT`, optional
    locator/redirect file, then `%USERPROFILE%\.agent-bridge`
  - `--state-dir` remains supported for backward compatibility but is treated as
    advanced/legacy; new docs prefer `--bridge-root`
  - derived paths include `state\`, `session.json`, `settings.json`,
    `watcher-config.json`, `routing-rules.json`, `bridge_watch_mode.flag`,
    `watcher.pid`, logs, leases, and server markers
- Add `bridge-root.json` manifest at the active root:
  - stable `root_id`
  - `schema_version`
  - `active_root`
  - created/migrated timestamps
  - migration history with source, target, tool version, and reason
- Add stale-root redirect manifests:
  - old roots keep a small `MOVED_TO.json` / redirect record
  - clients that start against a moved root fail loudly with the new path instead
    of silently creating split-brain state
- Add migration tooling:
  - dry-run default
  - validates source and target layout before copying
  - detects/pauses watcher and warns about live MCP server processes
  - backs up source before mutation
  - copies root files plus `state\`
  - rewrites watcher-config inbox paths
  - writes migration audit events
  - runs recovery/probe validation against the target root
  - prints exact Claude/Codex MCP config snippets for the new root
- Add tests for:
  - resolver precedence and path derivation
  - manifest creation and redirect detection
  - migration dry-run vs mutate behavior
  - watcher-config path rewrite
  - stale-root startup rejection
  - backward compatibility for `--state-dir`
  - routing-rules/settings/session registry all resolving from the same root

### Phase 14 - Knowledge sharing contracts

Implement `KNOWLEDGE_SHARING_CONTRACT_SPEC.md` so pairing consent is explicit,
scoped, expiring, and enforced before catch-up or future cross-project knowledge
sharing. This is a privacy/security feature that must land before the final
security signoff.

- Add contract state:
  - `state/knowledge-contracts/<contract_id>.json`
  - `state/knowledge-contracts/_index.json`
  - active lookup by scope, project(s), and agent pair
  - statuses: `active`, `expired`, `revoked`, `superseded`
- Add default policies:
  - same-project short offline catch-up allowed within the contract dormancy
    window
  - cross-project catch-up allowed only while the manual pair is active and
    unexpired
  - long dormancy returns metadata-only reauthorization prompts, never bodies
- Gate catch-up and knowledge-sharing paths:
  - `send_catchup_digest`
  - backpressure-clear digest generation
  - handshake-triggered digest generation
  - cross-project pairing and cross-project message flows
  - future file read/write tools
- Add reauthorization flow:
  - preview withheld count, date range, projects, and message types
  - renew with no history, metadata-only history, or bounded body-sharing window
  - revoke and block future catch-up
- Add MCP tools:
  - `list_knowledge_contracts`
  - `knowledge_contract_status`
  - `renew_knowledge_contract`
  - `revoke_knowledge_contract`
  - `preview_catchup`
- Add audit actions:
  - `knowledge_contract_created`
  - `knowledge_contract_renewed`
  - `knowledge_contract_revoked`
  - `knowledge_contract_expired`
  - `knowledge_contract_reauth_required`
  - `catchup_digest_policy_allowed`
  - `catchup_digest_policy_blocked`
- Add tests for:
  - recently offline same-project catch-up still works
  - dormant-too-long peer receives reauth-required metadata only
  - expired cross-project pair blocks catch-up bodies
  - revoked contract blocks catch-up and cross-project sends
  - bounded renewal filters implementation journal history
  - health/diagnostics surface reauth-required contracts

### Phase 15 - Policy authority and documentation drift

Implement `POLICY_AUTHORITY_SPEC.md` so markdown can explain or propose policy
but cannot override runtime enforcement. This closes the remote-doc-edit
escalation path before the final security review.

- Add runtime policy registry:
  - policy id, version, owner, severity, default/effective values
  - immutable vs locally configurable flag
  - allowed override source
  - generated markdown fragment hash
  - linked enforcement tests
- Classify policy docs:
  - `generated`
  - `enforced_reference`
  - `proposal`
  - `explanatory`
- Protect sensitive docs:
  - `AGENTS.md`
  - `CLAUDE.md`
  - `bridge_trigger_heuristics.md`
  - bridge protocol/security specs
  - knowledge-sharing and policy-authority specs
- Add protected-doc workflow:
  - remote-origin edits become proposals
  - local user sees diff preview
  - local confirmation token is required before applying authority-affecting
    changes
  - code/tests must change with docs when docs claim enforced behavior changed
- Add drift detection:
  - generated sections match runtime policy snapshot hashes
  - enforced-reference docs cannot claim broader permissions than runtime policy
  - dashboard/health panel surfaces drift
  - readiness/signoff blocks while drift remains unresolved
- Add obedience classifier for remote requests:
  - informational
  - proposal
  - contract action request
  - local confirmation required
  - forbidden remote authority
- Add MCP/CLI/dashboard support:
  - `list_policy_rules`
  - `policy_rule_status`
  - `validate_policy_docs`
  - `protected_doc_status`
  - protected-doc edit proposal/approval/rejection flow
- Add audit actions:
  - `policy_doc_drift_detected`
  - `policy_doc_drift_resolved`
  - `protected_doc_edit_proposed`
  - `protected_doc_edit_approved`
  - `protected_doc_edit_rejected`
  - `remote_authority_request_rejected`
  - `runtime_policy_changed`
  - `runtime_policy_snapshot_generated`
- Add tests for:
  - markdown contradictions do not change enforcement
  - generated policy docs fail validation after manual edits
  - remote protected-doc edits become proposals only
  - local-confirmed protected-doc edits are audited
  - remote authority escalation requests are rejected
  - dashboard reads runtime policy, not markdown

### Phase 16 - Guided pairing and local admin dashboard UX

Implement the use-case-facing UX layer described by:

- `BRIDGE_PAIRING_INTENT_SPEC.md`
- `BRIDGE_PAIRING_USER_FLOWS_SPEC.md`
- `BRIDGE_ADMIN_DASHBOARD_SPEC.md`
- `REMOTE_OBEDIENCE_USE_CASES_SPEC.md`

This phase makes the contract/policy model understandable to users before final
security signoff.

- Add guided pairing flow:
  - startup pairing-intent prompt before supersession
  - background/incognito/question-only chat mode
  - explicit "pair this chat" promotion and rollback for accidental pairing
  - scoped one-off peer relay from an unpaired same-project chat
  - same-project primary
  - same-project observer/advisor/auditor
  - simultaneous same-project primaries across different projects
  - cross-project advisor
  - cross-project write-with-confirmation
  - bidirectional advising modeled as two directed contracts
- Add pairing cardinality enforcement:
  - one primary per `(project, agent)`
  - configurable observer/advisor/auditor caps
  - configurable cross-project active-contract caps
  - dashboard-visible reasons when a pairing option is disabled
- Add user-facing naming model:
  - local friendly alias
  - project alias/id
  - peer agents and short session ids
  - trusted local alias vs untrusted peer-claimed label
  - bidirectional `<->` vs directed `->` relationship display
- Add local admin dashboard:
  - localhost-only authenticated web surface
  - pairings/contracts overview
  - contract duration and expiration countdown
  - revoke, renew, rename, preview catch-up
  - backpressure/catch-up status
  - policy/doc drift status
  - remote-authority rejection/proposal queue
  - audit timeline
- Add revocation flows:
  - dashboard revoke with confirmation
  - natural-language local revoke using the same backend path
  - peer-initiated disconnect as access-reducing action
  - revoked contract severs pairing and blocks future sends/wakes/body catch-up
- Add remote-obedience UX:
  - classify remote requests before action
  - reject forbidden remote authority with user-facing explanation
  - route protected-doc edits and authority-broadening requests to local
    proposals/confirmations
  - honor access-reducing requests safely
- Add tests for:
  - dashboard rendering and escaping
  - guided pairing option availability
  - duplicate-primary rejection
  - simultaneous multi-project primary pairings
  - revoke/renew/rename flows
  - natural-language revoke path
  - natural-language pair/do-not-pair/background/one-off relay paths
  - `default_pairing_intent` settings validation and bootstrap behavior
  - pending-pair timeout/default-background fallback
  - ephemeral relay orphaning, cap enforcement, and reply-only routing
  - duplicate-primary refusal UX and fourth observer cap rejection
  - remote-obedience request classes
  - dashboard uses runtime policy, not markdown

Progress note (2026-04-30 Codex pass): the shared backend now exposes
`dashboard_overview`, `list_pairings`, `list_contracts`,
`list_policy_dashboard`, `validate_policy_dashboard`,
`list_remote_authority_requests`, `audit_timeline`, confirmed
`revoke_contract`, confirmed `renew_contract`, `rename_local_alias`, and
`classify_remote_authority_request` as MCP/local API surfaces. These cover the
runtime-derived read model, tenant-filtered audit display, escaped markdown
rendering, remote-obedience classes, and the local-chat/dashboard shared
confirmation path for revoke/renew. A stdlib localhost dashboard server now
binds only local hosts, requires a bearer/X-Bridge token, exposes the dashboard
overview, and requires CSRF for revoke/renew/alias mutation routes. Remaining
Phase 16 work is richer guided-pairing activation UI, preview catch-up, and
full policy/doc drift proposal workflow.

Progress note (2026-05-01 Codex pass): same-project guided pairing backend v1
now exposes `pairing_details`, `start_guided_pairing`, and
`confirm_guided_pairing` through the shared bridge API and MCP server. Pending
or background same-project sessions can be promoted to active primary only after
an explicit confirmation step, or parked as background/observer/advisor/auditor
without superseding the active pair. Subagent-origin sessions expose the disabled
primary action with a reason, and active-promotion cleanup removes stale
pending/non-primary fields so dashboard state remains truthful. Remaining Phase
16 work is dashboard/UI integration, cross-project guided flows, catch-up
preview, and policy/doc drift proposal workflow.

Progress note (2026-05-01 Codex pass 2): AC-46's read-only dashboard status
surface is implemented in `dashboard_overview`. The JSON overview now includes
`status_surfaces.dashboard_reads`, `status_surfaces.backpressure`,
`status_surfaces.catchup`, `status_surfaces.contracts`, and
`status_surfaces.policy_drift`; markdown rendering includes a "Status Surfaces"
table for dashboard read degradation, blocked buckets,
project-scoped implementation-journal catch-up debt, contract
reauthorization/expiry/revocation, and protected-doc drift. The surface is
diagnostic-only: dashboard reads use non-mutating health-style readers for
session registry, pending actions, implementation journal, watcher state, and
audit JSONL, and regression tests assert corrupt dashboard inputs are not
renamed/quarantined by overview reads. Protected-doc drift validation now
detects missing protected docs and explicit contradictory policy claims such as
`remote_labels_trusted: true`; project-scoped catch-up reports `unknown` when a
degraded session registry prevents safe scoping. Verification: focused
dashboard tests and the full Agent Bridge suite passed (`240 tests OK`).
Remaining Phase 16 work is
richer dashboard/UI integration, cross-project guided flows, actual catch-up
preview actions, and policy/doc drift proposal workflow.

Progress note (2026-04-30 Codex wake/session remediation): private watcher
entries now opt into `session_id_source: "active_session"`, so `watcher.py`
resolves the current active GUID from `session.json` with an mtime/size cache
instead of trusting a stale static config GUID. `bootstrap_session.py` also
emits a Claude Monitor startup reminder for Claude sessions. This closes the
stale-pinning failure mode while Claude wake remains Monitor-owned.

Progress note (2026-05-01 Codex pass): `wake_claude.ps1` now exists as a
fail-closed diagnostic boundary, not as a production SendKeys helper. `-FindOnly`
reports candidate Claude Desktop windows, while normal invocation exits 20 with
`unsupported_thread_addressable_wake` because Agent Bridge has no verified
Claude Desktop thread id/deeplink contract. Watcher config records the disabled
reason for Claude entries and continues to rely on the in-context Claude Monitor.

Progress note (2026-05-01 Codex pass): Phase 13 configurable-root closure
patched the remaining helper seams. `configure_watcher.py` now writes explicit
root-derived `-StateDir` and `-LockFile` arguments into Codex SendKeys wake
templates; `wake_codex.ps1` and `wake_claude.ps1` honor `AGENT_BRIDGE_ROOT` when
defaults are needed; `codex_bridge_watch_mode.ps1` and
`codex_bridge_reminder.ps1` accept/use an explicit bridge root instead of
hardcoding `%USERPROFILE%\.agent-bridge`. User-facing docs now prefer
`--bridge-root` for bootstrap/configure examples.

Progress note (2026-05-01 Codex pass): concurrency/property hardening first
slice shipped with `hypothesis` added to `requirements.txt`, bounded property
tests for pure routing invariants, and a thread-level concurrent project-send
harness. The properties cover work-routing active-sender requirements,
session-target delivery/escalation, and agent-level work rejection; the
concurrency harness asserts five simultaneous project-bucket sends preserve
five unique rows.

Progress note (2026-05-01 Codex pass 2): AC-17 and AC-22 test-depth gaps were
closed for the current local harness. The concurrency harness now exercises
4 threads x 50 `send_to_peer` calls against one project bucket and asserts 200
successful send results, gap-free hop counts 1..200, and 200 unique rows/message
ids. Hypothesis now covers JSONL write/read round-trip for JSON objects plus
normalizer stability for rendezvous, session, and project identifiers. Focused
verification passed before the full-suite run.

Roadmap addition (2026-04-30 Codex): add `BRIDGE_PAIRING_INTENT_SPEC.md`.
Phase 16 must stop treating every new parent chat as an automatic active-pair
takeover. New sessions should start as `pending_pair`/`background` unless the
local user explicitly confirms pairing, while still supporting a scoped
one-off peer relay whose reply returns only to the requesting background chat.
Claude review changes incorporated: default `pending_pair` timeout is 120s,
ephemeral relay is explicit opt-in, project bucket is the default relay target,
outstanding relay cap defaults to 5 per background session, orphaned relay
replies stay in the project bucket with audit metadata, and all relay bodies
remain subject to knowledge-sharing contract body-sharing policy.
Follow-up user/Claude refinement: expose global `default_pairing_intent` in
`settings.json` with `ask_first` as safe default, `active_primary` as the
legacy auto-supersede fast path, and `background` for sidecar-heavy workflows.
CLI `--pairing-intent` remains the highest-precedence override.

### Phase 17 - Security review and threat model

Treat the bridge as local-only infrastructure, but still hostile-input exposed:
messages, config files, JSONL rows, environment variables, watcher commands,
and desktop wake helpers can all be influenced by a compromised peer, stale
state, stale knowledge contract, contradictory markdown, or accidental operator
input. The local admin dashboard is part of the reviewed surface.

`PREFLIGHT_DETECTION_SPEC.md` is now represented in `wake_codex.ps1`: composer
state is read non-intrusively, active typing defers without marking messages
seen/read, non-empty drafts are restored after the bridge wake, UIA-unavailable
fails closed unless a local debug override is explicitly supplied, and audit
records include hash plus safe length/line-count metadata rather than raw
composer text. Live Desktop dogfood remains part of final security validation.

Progress note (2026-05-01 Codex pass): Phase 17 local-v1 security review
snapshot is complete in `SECURITY_REVIEW.md`. The pass records no open P0s,
keeps legacy inline watcher commands and intrusive live targeted-wake dogfood
as explicit P1 residuals, records real Claude wake as a fail-closed P2
availability gap, and adds a regression hardening patch so the legacy Windows
balloon fallback also uses PowerShell `-EncodedCommand` instead of raw
`-Command`.

- Write `SECURITY_REVIEW.md` with:
  - trust boundaries: Claude, Codex, MCP clients, watcher, helper scripts,
    shared state files, settings, knowledge contracts, protected docs, policy
    registry, local dashboard, and desktop UI wake surface
  - threat model: command injection, path traversal, state tampering, message
    spoofing, replay/dedupe bypass, denial of service/backpressure wedging,
    stale-session takeover, stale-contract knowledge leakage, prompt/log
    exfiltration, markdown-policy drift escalation, and unsafe destructive tools
  - explicit assumptions: same-user local machine, no network listener, state
    files not secret, bridge messages may contain sensitive prompts and must not
    be silently copied elsewhere
- Audit every shell/process boundary:
  - `watcher.py` command execution
  - `wake_codex.ps1` SendKeys/deeplink behavior
  - `codex_*` hook scripts
  - bootstrap/configure/recovery/compact/probe CLIs
  - MCP tool inputs that can trigger filesystem writes or process spawning
- Add tests for security-sensitive contracts:
  - no shell-string construction for watcher commands after argv migration
  - message ids / toast tags / delivered bodies cannot inject PowerShell lines
  - helper paths are literal/quoted and do not traverse outside expected roots
  - destructive tools reject `default`, missing buckets, and ambiguous targets
  - `probe_server.py` cannot mutate live state without `--mutate`
  - settings reject unknown keys and invalid types
  - expired/revoked/stale knowledge contracts block body-sharing catch-up
  - contradictory markdown cannot broaden runtime permissions
  - dashboard auth, CSRF, escaping, and localhost binding are tested
  - guided pairing cannot activate disabled/forbidden relationship types
- Review state-file permissions and document the expected local-user security
  posture. If permissions cannot be enforced portably, report it as an accepted
  local-user trust assumption.
- Produce a final security signoff with:
  - findings ranked P0/P1/P2/P3
  - fixed findings and regression tests
  - accepted risks and why they are acceptable for a local bridge
  - explicit "not reviewed" exclusions, if any

### Phase 18 - Workflow guardrail enforcement

`WORKFLOW_GUARDRAILS_SPEC.md` classifies current agent workflow/reminder
behaviors as mandatory invariants versus configurable preferences. Phase 18
promotes the most dangerous Tier 2/Tier 3 mandatory items from reminders and
agent discipline into fail-visible stop/pre-final checks.

Status boundary: this section is an implementation plan, not a shipped guard
runtime. The WGI schemas and artifacts below are planned contracts for Phase 18.
Until Phase 18 lands, current enforcement remains the existing hooks, reminders,
ledger, receipt tools, health panel, and agent discipline described in the
enforcement tiers.

- Add a guardrail registry that records each mandatory behavior, enforcement
  tier, owner, failure mode, tests, and user-facing diagnostic.
  - Proposed code location: `core/guardrails.py`.
  - Proposed state artifact: `<bridge-root>\state\guardrail-debt.jsonl` for
    current debt events that must survive compaction.
  - Required registry fields: `id`, `name`, `category`, `mandatory`,
    `enforcement_tier`, `owner_agent`, `data_sources`, `hook_entry_points`,
    `diagnostic`, `blocking_default`, `tests`, and `roadmap_criterion`.
- Add stop/pre-final checks for surfaced substantive bridge messages with
  `read_at` but no `handled_at`.
  - Initial hook entry points: `codex_pre_final.ps1`,
    `codex_bridge_reminder.ps1 -HookPhase final`, and the future Claude
    Stop/Monitor guard.
  - Initial data sources: inbox rows for the active private/project buckets,
    `response-debt-state.json`, pending-action ledger, and `messages.jsonl`
    current-turn tool activity.
- Add stop/pre-final checks for surfaced `ACTION_REQUEST` messages lacking an
  explicit acting/parked/blocked/displaced/rejected disposition.
- Add outbound reply-debt checks that require a queued message id or a full
  pending-ledger body before closeout when peer send is blocked.
  - Data sources: failed/backpressured `send_to_peer` audit rows,
    `record_pending_bridge_action` rows, and original outbound body hash/body
    stored in the pending ledger.
- Add a Claude Monitor freshness guard before "waiting for Claude" or
  Codex-to-Claude notification reliability claims.
  - Data sources: Claude bootstrap output, Monitor heartbeat/state artifact,
    peer runtime breadcrumb, and latest Claude-side inbox activity.
  - A running process is not enough. The heartbeat must name the current Claude
    private session bucket and project bucket, the canonical
    `bridge_monitor_poll.py` helper path, and a recent heartbeat timestamp.
  - Stale generated scripts under `.claude-state\scripts\` are diagnostic
    evidence of a failed Monitor re-arm, not valid wake evidence.
- Add a log-first root-cause template or health-panel affordance that requires
  relevant audit/log references before a bridge defect is marked diagnosed.
- Add a reusable safe settings writer for UTF-8-no-BOM, atomic write, validate,
  backup-on-failure, and reload guidance; require settings mutations to use it.
- Add a both-scoped settings parity checker that reports every `both` key,
  consuming code paths per agent, focused tests, and any behavioral keys that
  are only documented/validated but not actually honored.
- Add docs/path hygiene checks for `.claude-state/` scratch placement and
  personal-path leakage in Agent Bridge docs.
- Add relay-debt checks for known shared bridge decisions, breakthroughs,
  remediations, roadmap/status/next-step changes, and completion reports that
  have not been sent to the peer.
  The relay content categories are defined in `bridge_trigger_heuristics.md`;
  the relay-debt check verifies no open row in
  `state\relay-candidates.jsonl` matches those mandatory categories without a
  `sent_message_id` or `pending_action_id`.
- Add dashboard visibility for guardrail debt so users can see whether an item
  is structurally enforced, reminder-backed, or norm-backed.

Phase 18 guard ids and deterministic test contracts:

| Guard id | Debt definition | Data sources | Initial default | Pass/fail tests |
|---|---|---|---|---|
| WGI-01 read-without-handled | A surfaced substantive bridge row has `read_at` or current-turn `seen_at` but lacks `handled_at`. | Active private/project inbox rows, `response-debt-state.json`, `messages.jsonl` tool activity. | Warn in general; block Agent Bridge implementation closeout. | Seed current-turn read row without handled -> debt; add `mark_handled` -> clean. |
| WGI-02 action-request-disposition | A surfaced row with `ACTION_REQUEST` / `ACTION_REQUESTED` lacks acted/parked/blocked/displaced/rejected/completed disposition. | Inbox body headers, `handled_status`, pending-action ledger. | Warn in general; block Agent Bridge implementation closeout. | Seed ACTION_REQUEST read row -> debt; record disposition -> clean. |
| WGI-03 outbound-reply-debt | A peer reply/send was attempted or required, but no queued id and no full pending body are durable. | `send_to_peer` audit rows, bridge response-debt state, pending-action ledger. | Warn; block when the unsent body is not recoverable after compaction. | Simulate backpressure with body absent -> debt; store full pending body -> clean. |
| WGI-04 claude-monitor-freshness | A registered waiting-on-Claude-visible-delivery claim has no fresh Claude Monitor heartbeat after latest compaction/session rollover. | `state\peer-wait-claims.jsonl`, future `monitor-claude-<session>.runtime.json` heartbeat from STATE_LAYOUT, Claude bootstrap output, peer runtime breadcrumb, Claude inbox activity. | Warn-only; cannot become blocking until the Monitor heartbeat writer/reader ships. | Register wait claim + missing/stale heartbeat -> warning; fresh heartbeat -> clean. |
| WGI-05 pending-ledger-drain | Top Codex-owned ledger item is actionable while execution is idle. | `state\pending-actions.json`, `state\execution-state.json`. | Existing final guard warning; block Agent Bridge implementation closeout. | Seed actionable ledger item + idle execution-state -> final guard; park/block/resolve or active task -> clean. |
| WGI-06 relay-debt | Shared bridge decision, defect, remediation, breakthrough, roadmap/status/next-step change, or completion report is known but not sent or stored pending. | `state\relay-candidates.jsonl`, `bridge_trigger_heuristics.md` mandatory relay categories, `send_to_peer` audit rows, pending-action ledger. | Warn; block 10/10 closeout. | Record relay candidate with no send/pending row -> debt; attach sent id or pending ledger id -> clean. |
| WGI-07 settings-parity-debt | A `both`-scoped setting lacks consuming code path, focused test, or peer ACK for either agent. | `state\settings-parity.jsonl`, `core/settings.py`, `SETTINGS.md`, focused tests. | Warn; block setting-complete status. | Add synthetic both key with one-sided parity row -> debt; add both consumers/tests/ACK ids -> clean. |
| WGI-08 log-first-debt | A registered bridge root-cause claim lacks a cited audit/log/message/health artifact. | `state\diagnostic-claims.jsonl`, pending ledger notes, health/report templates. | Warn; block defect-closed status for registered bridge defects. | Register diagnosis without artifact id/path -> debt; artifact citation -> clean. |
| WGI-09 review-closeout-debt | A peer review result was handled locally, but no amended closeout handoff was sent or durably parked. | `state\review-loop-state.jsonl`, `send_to_peer` audit rows, inbox review-result rows, pending-action ledger. | Codex final-hook warning now; block 10/10 closeout once dashboard debt visibility ships. | Send REVIEW_REQUEST -> peer AUDIT_RESULT -> mark_handled -> warning; send READINESS_ASSESSMENT/ACK with `IN_REPLY_TO` peer result -> clean; add WGI-09 parked pending action with exact peer result id and full closeout body -> clean. |
| WGI-10 active-task-interrupt-debt | A turn is about to close while an active execution task remains open and the latest user/peer interrupt has not been classified as resumed, completed, blocked, parked, or displaced. | `state\execution-state.json`, `state\pending-actions.json`, current-turn hook phase, `classify_execution_interrupt` artifacts. | Codex final-hook warning now; block Agent Bridge implementation closeout once shared guardrail debt visibility ships. | Seed active task -> final guard; `classify_execution_interrupt(disposition=resume)` -> classified resume artifact; `complete`/`blocked`/`parked`/`displaced` -> terminal classification + task closure; status/inbox interrupt without classification -> debt. |

For Phase 18, "surfaced" means the row was returned to the active agent by
`check_inbox`, `wait_inbox`, `peek_inbox(record_seen=true)`, or equivalent
tooling in the current turn, or has `seen_by_session` / `read_by_session`
matching the active session after `current_turn_started_at`. "Substantive" means
the message is not a pure closed ACK/control row and contains an action request,
review, design decision, implementation status, smoke/test prompt, user request,
or explicit reply/confirmation ask.

False-positive handling: every guard emits machine-readable debt first. A guard
can become blocking only after focused tests cover the clean path, debt path,
and explicit park/block/displace path, and after dashboard/health output names
the remediation.

Phase 18 day-1 state schemas:

- `relay-candidates.jsonl`: machine-readable candidates for mandatory peer
  relay. Required fields: `schema_version`, `candidate_id`, `owner_agent`,
  `category`, `summary`, `source_turn_id`, `source_message_id`, `body_hash`,
  `created_at`, `status`; completion fields: `sent_message_id` or
  `pending_action_id`.
- `diagnostic-claims.jsonl`: machine-readable bridge root-cause or defect-close
  claims. Required fields: `schema_version`, `claim_id`, `owner_agent`,
  `defect_id`, `summary`, `artifact_refs`, `created_at`, `status`.
- `peer-wait-claims.jsonl`: machine-readable claims that Codex or Claude is
  waiting on visible peer delivery. Required fields: `schema_version`,
  `claim_id`, `owner_agent`, `peer_agent`, `session_id`, `project`, `reason`,
  `monitor_required`, `monitor_evidence_id`, `created_at`, `status`.
- `settings-parity.jsonl`: machine-readable parity rows for every `both` scoped
  setting. Required fields: `schema_version`, `setting_key`, `scope`, `agent`,
  `consumer_path`, `focused_test`, `peer_ack_message_id`, `status`,
  `updated_at`.
- `review-loop-state.jsonl`: append-only review-loop state for WGI-09. Required
  fields: `schema_version`, `event_id`, `event_type`, `review_loop_id`,
  `request_message_id`, `owner_agent`, `peer_agent`, `created_at`, and
  `status`; scoping field: `owner_session_id`; transition fields:
  `peer_result_message_id` and
  `closeout_message_id`. Current event types are `review_requested`,
  `peer_replied`, `peer_result_handled`, and `closeout_sent`.
- `guardrail-debt.jsonl`: canonical debt rows emitted by WGI-01 through WGI-09.
  Required fields: `schema_version`, `debt_id`, `guard_id`, `severity`,
  `owner_agent`, `session_id`, `source_message_id`, `debt_status`,
  `detected_at`, `data_sources`, and `remediation`.
- `monitor-claude-<session>.runtime.json`: per-context Claude Monitor heartbeat
  with `schema_version`, `agent`, `session_id`, `project`, `monitor_pid`,
  `started_at`, `heartbeat_at`, `context_generation`, `compacted_after_start`,
  `script_path`, `argv`, `watched_buckets`, `helper_hash`,
  `preexisting_target_unread`, and `last_emit_at`.

Claude Monitor self-healing boundary:

- Truly self-healable:
  - canonical helper behavior, including emitting pre-existing targeted unread
    rows on startup instead of silently priming them away,
  - heartbeat/runtime breadcrumbs,
  - stale/misbound Monitor detection,
  - health/dashboard/watchdog escalation,
  - bootstrap surfacing of current active-session unread rows.
- Not truly self-healable without a future thread-addressable Claude wake
  primitive:
  - making an already-compacted or detached Claude chat read a message body,
  - proving Claude cognition from watcher toasts or process existence alone,
  - marking Claude-bound rows read/handled on Claude's behalf.
- Field behavior must therefore be fail-loud rather than silent:
  - preserve queued messages durably,
  - show `CLAUDE_MONITOR_STALE` / `CLAUDE_UNREAD_WITHOUT_MONITOR`,
  - include the exact repair command and stuck ids,
  - keep backpressure/relay debt visible until `read_at` or `handled_at`
    proves the peer consumed the work.

### Phase 19 - DOM/UI telemetry feasibility and reconciliation

`DOM_TELEMETRY_USE_CASE_CATALOG.md` captures the candidate use cases and
privacy/overhead assumptions for read-only Desktop UI telemetry. Phase 19 is a
feasibility phase, not a commitment to make DOM traversal mandatory.

**Concrete first deliverable (AC-67 — ships independently of the feasibility gate):**
`wake_codex.ps1` reads the Codex Desktop thread title via UIA `AutomationElement.Name`
after a successful deeplink navigation and emits an `AGENT_BRIDGE_WAKE_TELEMETRY`
JSON line. The watcher parses this from wake command output and writes
`desktop_thread_title` into `peer-codex.runtime.json`. The same wake pass records
warn-only title/project certification and wake postflight telemetry. This is
on-demand (no background polling), zero overhead outside wake events, and does
not require the full feasibility investigation before shipping.

Goal: determine whether read-only UIA/DOM/CDP telemetry can safely become a core
observer/reconciler for Agent Bridge orchestration without introducing visible
UI jank, privacy leakage, or brittle selector dependence.

Investigation deliverables:

- Build a read-only UIA snapshot probe for Codex Desktop and Claude Desktop.
  - Preferred scratch location: `.claude-state/bridge-ui/`.
  - The probe must support one-shot snapshots and bounded cadence runs.
  - Raw snapshots/screenshots remain scratch artifacts and are not checked in.
- Measure overhead at representative cadences:
  - event burst: 250-750ms for 3-5s after wake/send/read/handle,
  - active pairing: 2-5s,
  - idle paired session: 10-30s,
  - on-demand guard: single snapshot before write-side wake or closeout.
- Record p50/p95/p99 snapshot latency, CPU, memory, handle count, and visible
  UI jank.
- Validate parser stability across known UI states:
  - correct thread, wrong thread, wrong project,
  - idle, streaming, approval modal, error/rate-limit banner,
  - non-empty composer/draft, attachment present,
  - compaction/new-thread/session rollover,
  - minimized/hidden/multiple windows.
- Define compact fact schemas before promoting anything into bridge-root state:
  - `dom-telemetry/current-state.json`,
  - `dom-telemetry/ui-events.jsonl`,
  - `dom-telemetry/operation-results.jsonl`.
- Implement at least one table-driven truth-reconciliation prototype that
  combines bridge JSONL, receipts, and UI facts into confidence-scored state.
- Decide which uses are safe as core observers, which remain optional/debug, and
  which should be rejected as too invasive or brittle.

Initial promotion candidates:

1. Wrong-thread/wrong-project certification before UI write paths.
2. User draft/composer safety gate before `targeted_sendkeys`.
3. Wake actually rendered confirmation for helper-backed wake.
4. Agent busy/idle/blocked classifier to avoid wake storms.
5. No-silent-success operation contract for wake/send/read/handle.

Non-goals for Phase 19:

- Do not store raw transcript bodies in bridge-root state by default.
- Do not use UI telemetry as sole authorization for cross-project sharing.
- Do not make screenshot/OCR a primary dependency.
- Do not enable CDP probing unless the target app exposes it explicitly and the
  local security review accepts the mode.

---

## Critical-Path Execution Order

```
Phase 0 (failing tests)
     ↓
Phase 1 (storage)
     ↓
Phase 2 (routing model)
     ↓
Phase 4 (receipts)         ← skip 3; receipts before wake hardening
     ↓
Phase 7 (wake hardening)
     ↓
[Phases 3, 5, 6, 8, 9, 10, 11, 12 in dependency-allowed order]
```

**Why receipts before wake hardening:** receipts define what wake success means. Wake retry semantics depend on receipt state.

---

## Acceptance Criteria (phase-mapped)

| # | Criterion | Phase |
|---|---|---|
| 1 | Existing bridge behavior covered by failing-then-passing contract tests before refactor | 0 |
| 2 | `check_inbox(None)` behavior is explicit and tested | 0, 3 |
| 3 | No superseded/ended/orphaned sender can send normal work | 2 |
| 4 | Agent-level inbox is control/recovery only | 2, 3 |
| 5 | Agent-level recovery path remains available under lower-level backpressure | 3 |
| 6 | No unread work silently consumed by watcher/bootstrap/end/compaction | 5, 7 |
| 7 | Every message reports `queued`/`seen`/`read`/`handled`/`failed` and where stuck | 4 |
| 8 | Wake failures visible, retryable, not silently consumed | 7 |
| 9 | Parent Codex wake target cannot be overwritten by sub-agents | 7.X |
| 10 | Watcher/config updates atomic under concurrent bootstrap calls | 7 |
| 11 | Daemon singletons use leases + heartbeat + generation | 6 |
| 12 | MCP stdio servers strictly multi-instance | 6, 8 |
| 13 | Schema versioning + recovery exist before destructive migrations | 1, 11 |
| 14 | Probe tools safe by default; `--mutate` required for live state | 8 |
| 15 | MCP schemas reject malformed args at boundary | 8 |
| 16 | `recover_state.py` tested against deliberate corruption | 11 |
| 17 | Concurrency harness proves no lost/duplicated messages | 9 |
| 18 | Docs: no hardcoded personal paths, no stale `default` guidance, no unsupported config keys | 10 |
| 19 | `AgentBridge` is a facade (<200 lines) over service composition | 3, 7 |
| 20 | `bridge_status` and `bridge_process_status` have non-overlapping responsibilities | 6 |
| 21 | Wake path has Windows-focused regression coverage or mocked equivalents | 9 |
| 22 | Hypothesis property tests pass for normalizers and JSONL round-trip | 9 |
| 23 | Dead/ambiguous components have explicit keep/wire/archive decisions | 12 |
| 24 | All P1 reproduced bugs fixed with regression tests | 0 |
| 25 | One canonical `ARCHITECTURE.md`; specs marked Implemented or Archived (none Proposed) | 10 |
| 26 | Graceful shutdown leaves no stale locks / PIDs / leases | 6 |
| 27 | Bridge root is configurable through one resolver used by all tools | 13 |
| 28 | Root manifest and stale-root redirects prevent silent split-brain | 13 |
| 29 | Migration tool dry-runs, backs up, rewrites paths, validates target, and prints new config snippets | 13 |
| 30 | `--state-dir` compatibility remains tested while docs prefer `--bridge-root` | 13 |
| 31 | Pairings have explicit knowledge-sharing contracts with scope, expiry, and dormancy limits | 14 |
| 32 | Catch-up digests are gated by active contract policy before body sharing | 14 |
| 33 | Long-dormant peers receive reauthorization metadata, not historical bodies | 14 |
| 34 | Contract renewal supports no-history, metadata-only, and bounded body-sharing modes | 14 |
| 35 | Revoked/expired contracts block catch-up and cross-project sends | 14 |
| 36 | Runtime policy registry is authoritative over markdown/spec text | 15 |
| 37 | Protected policy docs require local authority path before authority-affecting edits apply | 15 |
| 38 | Remote-origin protected doc edits become proposals, not applied policy changes | 15 |
| 39 | Documentation drift is detected, audited, and blocks readiness/signoff while unresolved | 15 |
| 40 | Dashboard/effective policy views read runtime policy, not markdown text | 15 |
| 41 | Guided pairing explains allowed capabilities from runtime policy before activation | 16 |
| 42 | Same-project primary cardinality is one per `(project, agent)` while different projects can pair simultaneously | 16 |
| 43 | Dashboard lists pairings/contracts with friendly names, roles, short session ids, durations, and countdowns | 16 |
| 44 | Dashboard and natural-language revoke use the same confirmed backend path | 16 |
| 45 | Remote requests are classified before action and forbidden authority requests are rejected/audited | 16 |
| 46 | Dashboard surfaces backpressure, catch-up, contract reauth, and policy/doc drift status | 16 |
| 47 | Security threat model and trust boundaries documented | 17 |
| 48 | Shell/process/dashboard boundaries audited with injection/path/CSRF tests where applicable | 17 |
| 49 | Security signoff records fixed findings, accepted risks, and exclusions | 17 |
| 50 | Mandatory vs configurable workflow behavior is documented with enforcement tiers | 18 |
| 51 | Stop/pre-final guards surface unread/read/handled and ACTION_REQUEST disposition debt | 18 |
| 52 | Outbound reply-debt is durable across backpressure and compaction | 18 |
| 53 | Claude Monitor freshness is visible before waiting-on-peer claims | 18 |
| 54 | Root-cause claims for bridge defects cite durable logs/audit evidence | 18 |
| 55 | Settings mutations use one safe UTF-8-no-BOM atomic writer and validate after write | 18 |
| 56 | Scratch/doc path hygiene checks prevent tracked scratch and personal-path leaks | 18 |
| 57 | Guardrail debt is visible in dashboard/health output by enforcement tier | 18 |
| 58 | Both-scoped settings parity checker proves every shared key is honored by both agents or reports guardrail debt | 18 |
| 59 | Pending-ledger drain guard warns or blocks idle closeout with actionable Codex-owned work | 18 |
| 60 | Review-loop closeout guard tracks review requests/results and warns before final when local handled status lacks a peer closeout handoff | 18 |
| 68 | Active-task interrupt guard prevents inbox/status/process interrupts from becoming accidental closeout while implementation or review work remains open | 18 |
| 61 | DOM/UI telemetry use-case catalog is documented with privacy rules, overhead hypothesis, and source preference | 19 |
| 67 | `wake_codex.ps1` reads thread title via UIA after deeplink nav and emits it to stdout; watcher caches it in `peer-codex.runtime.json` as `desktop_thread_title`; watcher logs, toasts, health panel, and dashboard friendly-name fields display the cached title alongside session GUID tails | 19 |
| 62 | Read-only UIA snapshot probe measures Codex/Claude Desktop p50/p95/p99 latency, CPU, memory, handle count, and UI jank at candidate cadences | 19 |
| 63 | DOM telemetry parser fixtures cover wrong thread/project, busy/idle/blocked, draft/composer, error banner, and compaction/rollover states | 19 |
| 64 | Compact DOM telemetry fact schemas are defined before any bridge-root state promotion and exclude raw transcript bodies by default | 19 |
| 65 | Hybrid truth reconciler prototype combines bridge JSONL, receipt state, and UI facts into confidence-scored state with contradiction tests | 19 |
| 66 | Phase 19 closes with an explicit promote/optional/reject decision for each initial DOM telemetry candidate | 19 |
| 69 | bootstrap_session.py peeks the new active session bucket immediately after activation and surfaces any unread work rows in the bootstrap JSON output (M-1 backpressure mitigation) | 18 |
| 70 | bridge_health_panel and dashboard_overview surface a BACKPRESSURE_BLOCKED row when SESSION_BACKPRESSURE_LIMIT is saturated, naming the blocked sender agent/session and how long the block has been active (M-2 backpressure visibility) | 18 |
| 71 | Spec-only gate for a scoped same-pair IN_REPLY_TO update exemption from the backpressure gate; implementation requires separate protocol review before any bypass ships (M-3 backpressure protocol) | 18 |
| 72 | bootstrap_session.py Monitor reminder emits the exact correct script path (bridge_monitor_poll.py) with no ambiguity; CLAUDE.md startup sequence is updated to name the script explicitly and distinguish it from probe_server.py (M-4 operational clarity) | 18 |
| 73 | SESSION_BACKPRESSURE_LIMIT raised from 1 to 5 and PROJECT_BACKPRESSURE_LIMIT raised from 5 to 10 as hardcoded constants; soft-warn added at ceil(limit*0.6) unread (3 and 6 at defaults) before hard-reject; not user-configurable (a wrong value fails silently) | 18 |
| 74 | control/health messages (marker_variant="control") are excluded from SESSION_BACKPRESSURE_LIMIT and PROJECT_BACKPRESSURE_LIMIT counts; regression test verifies a SESSION_UPDATE or HANDSHAKE does not consume a work slot | 18 |
| 75 | stale-unread watchdog auto-rearms wake IDs without any operator call when peer Monitor heartbeat is absent for >N minutes; safe — rearm-only, never mutates read_at | 18 |
| 76 | health panel BACKPRESSURE_BLOCKED row exposes a ready-to-run recovery command (e.g. receipt_debt_cleanup call) not just a descriptive string; the command is validated as safe before being surfaced | 18 |
| 77 | each AC75 auto-rearm event writes a `stale_unread_watchdog_rearmed` audit row containing session_id, count of rearmed message ids, and timestamp; health panel and dashboard_overview surface STALE_UNREAD_WATCHDOG_REARMED rows so the rearm history is visible without log spelunking | 18 |
| 79 | Claude Monitor writes `monitor-claude-<session>.runtime.json` heartbeat rows with script path, argv, watched buckets, helper version/hash, preexisting unread count, last emit time, and heartbeat time; health/WGI-04 consumes this instead of trusting process existence | 18 |
| 80 | bootstrap_session.py detects stale/misbound Claude Monitor runtime evidence at startup, refuses to describe the Monitor as armed, and emits an exact repair command plus the current private/project buckets | 18 |
| 81 | bridge_health_panel/dashboard_overview surface `CLAUDE_MONITOR_STALE` and `CLAUDE_UNREAD_WITHOUT_MONITOR` rows when active Claude unread work exists but no fresh current-bucket Monitor heartbeat is present; include stuck message ids and remediation command | 18 |
| 82 | Watcher stale-unread watchdog escalates unread Claude work after a bounded threshold by writing a diagnostic/audit row and user-visible notification; it must not mark read or claim Claude cognition without `read_at`/`handled_at` | 18 |
| 83 | Reviewer wait discipline is ledger/guard enforced: background stranger waits write `reviewer-wait-state.jsonl`; final guard warns when no verdict, no future ETA/checkback, and no parked/blocked/cancelled status exists; CLI/MCP helpers expose start/update/status | 18 |
| 84 | Watcher sends `TYPE: CONTROL / marker_variant=control / SUBJECT: MONITOR_RESTART_REQUIRED` to Claude's active session inbox on two triggers: (a) Monitor heartbeat absent/stale ≥ the AC80 stale-classification threshold; (b) watcher detects a new commit that modifies any of bridge_monitor_poll.py, server.py, agent_bridge.py, or watcher.py (polled via git log since last watcher tick); CLAUDE.md documents that Claude must stop any stale Monitor task handle and start a fresh Monitor instance immediately on receipt of this control message; control messages are exempt from backpressure per AC74 | 18 |
| 78 | Targeted SendKeys wake treats user UI state as transactional but delivery-priority by default: if the foreground is Codex on a different or unprovable thread and no exact restore UUID is available, strict callers fail closed with retryable exit 16, while the production watcher explicitly passes `-AllowForegroundCodexThreadDisplacement` so a valid target thread still receives the nudge and the displacement risk is audited with `targeted_wake_delivery_priority_no_restore`; when a valid `RestoreThreadId` is supplied, Stage 6 invokes that deeplink and marks the restore proof as unverified until an exact visible-thread primitive exists | 19 |

## Final 10/10 Validation Loop

Smoke testing can raise operational confidence, but it does not replace roadmap
completion. The final 10/10 hardening call happens only after every roadmap
acceptance item above is either implemented or explicitly removed from scope by
Codex + Claude + user agreement.

After roadmap completion:

1. Codex runs an iterative local smoke and failure-path suite until its own
   `READINESS_ASSESSMENT` rates the bridge 10/10 or names concrete blockers.
2. Codex sends Claude the test matrix, results, score, and remaining risks as an
   `AUDIT_REQUEST` / `READINESS_ASSESSMENT`.
3. Claude independently runs its own iterative smoke and failure-path suite
   against the same implementation, including stranger/cold-review where useful,
   until Claude also rates the bridge 10/10 or names concrete blockers.
4. Two or more background agents acting as strangers independently review the
   same result and must rate it 10/10, or name concrete blockers.
5. Any divergence becomes a `RISK_DELTA` and must be resolved by code, docs, or
   explicit scope decision before either side calls the bridge 10/10.
6. The final hardening score is canonical only when Codex, Claude, two or more
   background stranger agents, and the user agree.

This preserves the two-axis distinction agreed during hardening review:

- operational confidence can improve through smoke coverage;
- full hardening confidence requires completing the roadmap and validating the
  failure paths that smoke alone cannot prove.

---

## Risk Register

| Risk | Mitigation |
|---|---|
| Module extraction breaks MCP surface | External MCP signatures stable; only internals refactor; full suite per phase |
| Synchronous watcher run blocks poll loop | 90s timeout cap; failed waits leave message unmarked for retry |
| Schema v2 breaks existing state | Phase 1 read-only assertion; v2 migrators only in Phase 11 with backup |
| Recovery tool corrupts more than it fixes | Mandatory backup-before-repair; dry-run default |
| Phase 0 failing tests block phase progression | Each phase explicitly notes which Phase 0 tests must turn green |
| Codex 0.111 config compatibility regression | Version-gated docs (Phase 10) |
| Sub-agent context pollutes parent state mid-phase | Phase 0 contract test catches it; Phase 7.X enforces provenance allowlist |
| Hypothesis dependency setup friction | Property tests are dev-only extra; Phase 0 doesn't depend on them |
| Root relocation creates split-brain state | Phase 13 adds a single resolver, root manifest, stale-root redirects, and startup rejection for moved roots |
| Migration loses or corrupts bridge history | Phase 13 migration is dry-run first, backup-before-mutate, and validates target with recovery/probe tools |
| Long-dormant peer receives stale sensitive context | Phase 14 gates catch-up on active knowledge contracts and returns reauthorization metadata after dormancy expiry |
| Remote or stale markdown claims broader permissions than code allows | Phase 15 makes runtime policy authoritative, detects doc drift, and gates protected-doc edits through local confirmation |
| User accidentally creates ambiguous multiple primaries | Phase 16 guided pairing enforces one primary per `(project, agent)` and makes non-primary roles explicit |
| Dashboard becomes a remote attack surface | Phase 16 keeps it localhost/auth-token/CSRF-only; Phase 17 audits dashboard boundaries |
| Remote peer manipulates user-facing labels or markdown | Phase 16 marks peer labels untrusted and escapes remote text; Phase 15 keeps runtime policy authoritative |
| Local command injection via watcher/helper boundaries | Phase 17 audits all process boundaries and adds injection/path tests |
| Sensitive prompt leakage through bridge state/logs/dashboard | Phase 17 documents local-user trust assumptions, retention, dashboard redaction, and accepted risks |
| Destructive MCP tool misuse | Phase 17 validates ambiguous/destructive inputs reject by default |

---

## What's NOT In Scope

- Multi-machine state replication
- Encryption of state files
- Multi-agent beyond `claude` / `codex`
- Replacing MCP transport
- Cross-platform watcher (Windows-only acceptable)
- Remote web UI or externally reachable dashboard
- Wire protocol redesign
- Resurrecting heartbeat automations
- Silent automatic bridge-root migration without explicit user approval
- Editing Claude/Codex desktop config files during migration unless an explicit
  future `--write-configs` mode is implemented and reviewed
- Formal third-party penetration test
- Cryptographic identity, signatures, or encrypted-at-rest bridge state

---

## Provenance

| Element | Source |
|---|---|
| North Star + 10 invariants | Codex (synthesized from both first drafts) |
| 11-service architecture | Both (independent convergence) |
| `SenderContext` + `MessageKind` (4 values) | Codex |
| "Receipts are truth" framing | Codex |
| Critical-path execution order (0→1→2→4→7) | Codex |
| Risk register | Claude |
| Scope exclusions | Claude |
| Dead-code as Phase 12 | Claude |
| Phase-mapped acceptance criteria | Claude |
| Concrete directory layout | Claude |
| Phase 7.X parent-only wake (provenance allowlist, not pattern) | Codex (live debugging) |
| Phase 0 test #7 (wake failure ≠ seen) | Live debugging this session |
| Phase 0 test #13 (probe `--mutate` gate) | Codex |
| ASCII-only PowerShell rule (registered in memory) | Live debugging |
| `bridge_status` vs `bridge_process_status` non-overlap | Codex pushback |
| Clipboard-paste as backend, not delivery guarantee | Codex pushback |
| Hypothesis as dev-only dependency | Codex pushback |
