# Agent Bridge - Session Routing Hardening Spec

**Status:** Proposed
**Authors:** Claude (proposal); Codex review pending
**Tier:** Tier 1 - ASAP single-machine fix; the fourth toast storm of 2026-04-29 was triggered by this footgun
**Motivation:** On 2026-04-29 ~13:41 UTC, Claude called `send_to_peer(to_agent="codex", session_id="<claude-session-guid>", ...)`. The message was queued into `inbox-codex.jsonl` keyed by a Claude session GUID. The Codex watcher only polls (a) Codex's active session bucket and (b) the project rendezvous bucket - it does NOT poll buckets keyed by Claude GUIDs. Result: the AUDIT_RESULT was orphaned in a bucket nobody watches. No toast, no wake, no delivery. When I corrected the route to `session_id="<codex-session-guid>"`, the new (correct-bucket) message immediately triggered the fourth wake-storm of the day - because the running watcher was pre-d97eaf9c and lacked Layers 1-4. This spec hardens the path so the misroute and the storm-from-misroute can't both happen.

The misroute is one symptom of a broader weakness: **session identity at the row level is partial and ad-hoc**. The bucket key (`session_id` field on a row) doubles as routing target, but there's no first-class record of WHICH agent session sent the message or WHICH session it was meant for. Different message types embed `FROM_SESSION:`/`TO_SESSION:` in the body via convention; some types omit them. There's no schema enforcement, no server-side validation, and no recovery tool for orphaned buckets.

---

## Goal

Make session routing safe by default. Specifically:

1. Prevent the wrong-agent bucket key footgun at the API layer (refuse misrouted sends).
2. Make sender-session and intended-receiver-session first-class row fields, not body conventions.
3. Provide a recovery tool for orphaned messages already in stale buckets.

## Non-Goals

- No multi-tenant cloud routing changes - this is single-machine forward-compat aware.
- No new authentication or permission layer - that's `BRIDGE_AUTH_SPEC.md`.
- No transport-layer changes - the bucket file layout stays the same.
- No deprecation of the body-level `FROM_SESSION:`/`TO_SESSION:` convention. SR1 makes them redundant; agents may continue to write them for human-readable audit until a future cleanup.

---

## Problem

```
┌─────────────────────────────────────────────────────────────────────┐
│ send_to_peer(from_agent="claude", to_agent="codex",                 │
│              session_id="<claude-guid>", message="...")             │
└────────────────────────┬────────────────────────────────────────────┘
                         │
                         v
              writes row to inbox-codex.jsonl with
              session_id = "<claude-guid>"   ← bucket key
                         │
                         v
        ┌────────────────┴─────────────────┐
        │ Codex watcher polls buckets:     │
        │  - "<codex-active-guid>"  ✓      │
        │  - "mlv-app"              ✓      │
        │  - "<claude-guid>"        ✗ skip │
        └──────────────────────────────────┘
                         │
                         v
              Message orphaned. No toast, no wake, no delivery.
```

The footgun is that `session_id` parameter on `send_to_peer` is overloaded:

- **Conceptually** it should be "which receiver session bucket should this land in"
- **Practically** there's no validation that the value belongs to the receiving agent
- **Default behavior** when `session_id` is null is to resolve to receiver's active session (correct), but explicit values bypass that resolution

---

## Schema changes

### SR-Schema-1: row-level session fields

Add to all message rows (in `inbox-claude.jsonl`, `inbox-codex.jsonl`):

| Field | Type | Required | Notes |
|---|---|---|---|
| `from_session_id` | string (GUID) | yes (v2+) | Sender's active session at queue time |
| `to_session_id` | string (GUID) or "mlv-app" or null | yes (v2+) | Intended recipient session bucket; null = "any active session for to_agent" |
| `from_session_id_kind` | enum | yes (v2+) | `parent` / `subagent` / `unknown` (mirrors bootstrap_origin) |

The existing `session_id` field stays as the **bucket key** for backward compat. The new fields are the **routing intent**.

Schema bumps `inbox-claude.jsonl` and `inbox-codex.jsonl` row schema from v1 → v2 per `BRIDGE_SCHEMA_EVOLUTION_SPEC.md` SE1-SE10:

- v1 readers default `from_session_id`/`to_session_id` to null when reading v2 rows (forward-compat per SE3)
- v2 readers default both fields to null when reading v1 rows (back-compat per SE2)
- Compact-on-touch promotes v1 → v2 per SE8 (no mass migration needed)

### SR-Schema-2: validation

Server-side at write time, after schema-version check:

- If `from_session_id` is set, must be the sender's currently-active session for `from` agent (per `session.json`).
- If `to_session_id` is set AND not equal to `"mlv-app"`, must be a known active or recent-superseded session for `to` agent.
- If `to_session_id` is mismatched-agent (e.g. claude GUID with `to=codex`): **refuse** with structured error.

---

## Acceptance Criteria

### SR1 - Row-level session fields shipped

- [ ] Inbox row schema bumps from v1 to v2 with `from_session_id`, `to_session_id`, `from_session_id_kind`
- [ ] All current writers populate the new fields
- [ ] v1 readers gracefully ignore the new fields (per SE3)
- [ ] v2 readers gracefully default null when reading v1 rows (per SE2)
- [ ] compact-on-touch upgrades v1 → v2 (per SE8)
- [ ] Per-bump documentation requirement (SE10): this spec is the bump rationale doc

### SR2 - Server validates session ownership

- [ ] `send_to_peer` extracts `to_agent` from arg
- [ ] If `session_id` is supplied AND not `null`/`mlv-app`/`<to_agent>'s active or recent-superseded GUID>`: **refuse** with `error_kind="session_routing_mismatch"`, structured error giving received vs expected agent
- [ ] Error message tells the caller (a) the value they passed, (b) which agent it actually belongs to, (c) the recommended fix (omit `session_id` and let the server resolve)
- [ ] Same validation runs on any internal write path that takes a session_id (not just send_to_peer)

### SR3 - Default-resolve when `session_id` is null

- [ ] If `session_id=None`: server resolves to receiver's active session GUID (per `session.json`)
- [ ] If receiver has no active session: server falls back to project rendezvous bucket (`mlv-app` for this project) and emits a `routing_fallback_to_rendezvous` audit event
- [ ] If both fail: refuse with `error_kind="no_active_receiver"` (existing behavior; just formalize)

### SR4 - `truedup_session_routing` MCP tool

A new read-write tool for self-healing orphaned buckets.

- [ ] Tool signature: `truedup_session_routing(agent: str, dry_run: bool = True, mode: "rekey"|"quarantine" = "rekey")`
- [ ] Scans `inbox-<agent>.jsonl` for rows whose `session_id` doesn't match any active or recent-superseded session for that agent
- [ ] Mode `rekey`: rewrites those rows with `session_id` set to receiver's active session GUID (or `mlv-app` if no active session); audits `session_truedup_rekeyed` per row
- [ ] Mode `quarantine`: moves those rows to `<inbox>.orphan.jsonl`; audits `session_truedup_quarantined` per row
- [ ] `dry_run=True` (default): returns the list of rows that WOULD be touched, without modifying anything
- [ ] Tool is read-only when dry_run; not idempotent when applied (each application removes the orphans it found, so a second run on the same data returns empty)
- [ ] Tests: synthetic inbox with mix of valid + orphaned rows, dry_run lists orphans, apply rekeys, second run is empty

### SR5 - send_to_peer parameter rename (optional, backwards-compat)

- [ ] Add new param `target_session_id` to `send_to_peer`
- [ ] Old `session_id` kept as deprecated alias for one release (writes audit `deprecated_param_used`)
- [ ] Deprecation period: until next schema bump or 2 weeks, whichever is longer
- [ ] Tests: both names work; deprecated name warns; conflicting values error

### SR6 - Body-level conventions become advisory

- [ ] `FROM_SESSION:` / `TO_SESSION:` body lines no longer required (SR1 row fields are the source of truth)
- [ ] Existing readers tolerate missing body lines without error
- [ ] Documentation updated in `BRIDGE_PROTOCOL.md` to mark body lines as informational/optional

### SR7 - Bootstrap session-rotation safety

- [ ] When `bootstrap_session.py` rotates sessions (old → new), it scans inbox-<agent>.jsonl for unread rows in the old session bucket
- [ ] Either re-keys them to the new active session (default), OR leaves them with a `superseded_bucket_at` marker; configurable
- [ ] Sends an internal audit event `bootstrap_rotation_routed_messages` with count
- [ ] Tests: synthetic inbox with unread rows in old bucket; after rotation, rows are reachable from new active session

### SR9 - User-action breaker bypass (one-shot recovery)

**Motivation:** Layers 1-4 (post-d97eaf9c) protect against wake storms by tripping the D2 breaker after N consecutive wake failures. Trade-off: when the receiver is genuinely in a bad state at the moment the breaker trips, there's no auto-recovery path. Observed 2026-04-29 13:53 UTC: 5 wake failures opened the breaker; subsequent messages emitted `wake_skipped_breaker_open`; user had to manually paste "check bridge inbox" two hours later. Layer 4's auto-close (15min idle) eventually clears it, but only if the user waits and the receiver state has changed in the interim.

The fix is a recovery primitive, NOT a breaker disable: when the user takes an explicit recovery action ("I want this delivered now"), grant ONE bypass attempt. If it succeeds, reset the failure window. If it fails, the breaker stays open and a new failure is recorded; the next user action gets another bypass.

**Definition of "user action":** an explicit MCP tool call from the user's surface (Claude Desktop, Codex Desktop, or CLI) that signals delivery intent. The server cannot perfectly distinguish user vs agent calls, so the bypass is granted on a fixed list of MCP tools that imply delivery intent:

- `resume_bridge` - if any breaker is open at resume time, each open breaker is granted one bypass on its next wake fire
- `nudge_peer(agent, [session_id])` - new MCP tool for explicit one-shot wake; bypasses an open breaker if applicable
- `mark_read` of a message older than `BREAKER_BYPASS_STALE_MIN` (default 30 minutes) - implies user is cleaning up a stuck message

The bypass is **rate-limited** independently from Layer 3: at most one bypass per session per `BREAKER_BYPASS_COOLDOWN_S` (default 60s). This prevents users (or agents) from chaining bypass calls to defeat the breaker.

**Acceptance Criteria:**

- [ ] New MCP tool `nudge_peer(agent: str, session_id: str | None = None)`:
  - Fires one wake_<peer>.ps1 invocation immediately
  - If breaker is open for that session: bypasses, executes, on success closes the breaker; on failure records the failure as if a normal wake had failed
  - If breaker is closed: same as a normal wake fire
  - Rate-limited: max one call per `(agent, session_id)` per `BREAKER_BYPASS_COOLDOWN_S`
  - Audit event `nudge_peer_user_initiated`
- [ ] `resume_bridge` enhancement: when called with any open breakers, grants each open session one bypass on the next wake fire (writes `breaker_bypass_grant` to `wake-failure-windows.json`); audit event `resume_bridge_breaker_bypass_grant` per granted bypass
- [ ] `mark_read` enhancement: if the message's `created_at` is older than `BREAKER_BYPASS_STALE_MIN` AND a breaker is open for that session, grants one bypass on the next wake fire (same mechanism as resume_bridge); audit event `mark_read_stale_breaker_bypass_grant`
- [ ] Bypass mechanism: `wake-failure-windows.json` schema gets `bypass_grant: int` field (count of pending bypasses for the session); watcher decrements on use, fires the wake even if breaker_state == open
- [ ] On bypass success (exit 0): breaker fully closes (failure window cleared per Layer 4 success-clears semantic) AND `bypass_grant` decrements
- [ ] On bypass failure: breaker stays open; failure recorded normally; `bypass_grant` decrements without re-grant
- [ ] Bypass is one-shot — does NOT persist across multiple wake fires; once used (success or fail), gone until next user action
- [ ] Tests:
  - `test_sr9_nudge_peer_fires_when_breaker_closed`
  - `test_sr9_nudge_peer_bypasses_open_breaker_one_shot`
  - `test_sr9_nudge_peer_bypass_failure_keeps_breaker_open`
  - `test_sr9_nudge_peer_rate_limit_blocks_rapid_calls`
  - `test_sr9_resume_bridge_grants_bypass_per_open_breaker`
  - `test_sr9_resume_bridge_no_grant_when_no_open_breaker`
  - `test_sr9_mark_read_stale_message_grants_bypass`
  - `test_sr9_mark_read_fresh_message_does_not_grant_bypass`
  - `test_sr9_bypass_success_closes_breaker_fully`
  - `test_sr9_bypass_failure_does_not_extend_breaker_window`

**Composes with SR8:** SR8's auto-nudge respects the breaker (does not bypass). SR9 is the path that *can* bypass the breaker, but only on explicit user intent. Together: backpressure rejections fire nudges (SR8) when safe; the user has a recovery primitive (SR9) when the breaker has tripped. Two distinct trigger sources, one consistent breaker semantic.

**Composes with `BRIDGE_HEALTH_PANEL_SPEC.md` HP11:** the panel surfaces breaker state. Once HP11 ships, the user sees "breaker open for codex" in the panel and knows to call `nudge_peer` (or otherwise take a recovery action). The recovery loop becomes: panel shows problem -> user nudges -> bypass fires -> if success, breaker clears.

**Important pushback if seen during audit:**
- ANY automatic / agent-driven path (e.g. polling agent calls `nudge_peer` in a loop) constitutes a breaker bypass abuse; rate limit must hold
- The bypass MUST NOT cascade — one user action grants ONE bypass, period. No "save up bypasses" semantics.
- Default `BREAKER_BYPASS_STALE_MIN` (30 min) and `BREAKER_BYPASS_COOLDOWN_S` (60s) are conservative starting points; tune in follow-on commits with usage data, not by guess.

---

### SR8 - Backpressure rejection auto-nudges receiver

**Motivation:** when `send_to_peer` is rejected because the receiver already has unread work, the current behavior is "fail and tell sender." The receiver is not nudged to drain. This produces a deadlock-like state where the sender knows there's an issue but has no path to escalate, and the receiver may be idle and would happily drain if poked. Observed 2026-04-29 ~13:53 UTC: send refused due to abe057cc unread; no nudge fired; user had to paste "check bridge inbox" manually two hours later.

- [ ] When `send_to_peer` returns `error_kind="backpressure_unread_work"`, server SHALL ALSO attempt to fire a wake nudge to the receiver before returning the error
- [ ] Wake nudge honors all wake-storm protections (Layer 3 rate limit, Layer 4 D2 breaker) - it does NOT bypass them, so a receiver in a known-bad state still doesn't get spammed
- [ ] Audit event `backpressure_rejected_with_nudge` per occurrence (or `backpressure_rejected_no_nudge_breaker_open` if breaker prevented the nudge)
- [ ] The rejection error message tells the caller whether the nudge fired or was suppressed
- [ ] Optional SR8a: server holds the queued send for up to N seconds (default 30) after the receiver marks_read, then writes it; sender's MCP call awaits up to that ceiling. Behind a flag for v1; can ship in a follow-on commit
- [ ] Tests:
  - `test_sr8_backpressure_rejection_fires_nudge_when_breaker_closed`
  - `test_sr8_backpressure_rejection_skips_nudge_when_breaker_open`
  - `test_sr8_backpressure_rejection_emits_correct_audit_event_per_path`
  - `test_sr8_nudge_honors_rate_limit` (rapid backpressures don't escape Layer 3)
  - (SR8a) `test_sr8a_held_send_writes_after_receiver_drains`
  - (SR8a) `test_sr8a_held_send_times_out_returns_error`

---

## Failure Modes

| Failure | Detection | Prevention |
|---|---|---|
| Wrong-agent GUID passed (the 2026-04-29 incident) | SR2 validation | Refused at write time; caller gets structured error |
| Caller's local session.json out of sync | server reads its own copy | Server uses canonical `session.json` for validation |
| Recipient has multiple active sessions (subagent + parent) | active is unique by agent in v1 | If schema bump introduces multi-active later, validation expands |
| Row in old bucket from before spec ships | SR4 truedup tool | Scan + rekey/quarantine on demand |
| Cross-tenant routing (Tier-3 future) | tenant_id mismatch detected separately per `BRIDGE_TENANT_SCOPING_SPEC.md` | Out of scope here; SR2 layers cleanly |

---

## Tests Required

**Unit:**

1. `test_sr_schema_v2_round_trip`
2. `test_sr_v1_reader_ignores_v2_fields`
3. `test_sr_v2_reader_handles_v1_row_default_null`
4. `test_sr_compact_on_touch_promotes_v1_to_v2`
5. `test_sr_send_to_peer_refuses_wrong_agent_session_id`
6. `test_sr_send_to_peer_accepts_correct_session_id`
7. `test_sr_send_to_peer_accepts_null_resolves_to_active`
8. `test_sr_send_to_peer_falls_back_to_rendezvous_when_no_active`
9. `test_sr_send_to_peer_refuses_when_no_active_and_no_fallback`
10. `test_sr_send_to_peer_accepts_recent_superseded_session_id`  (allows replay/recovery sends)
11. `test_truedup_dry_run_lists_orphans`
12. `test_truedup_rekey_mode_rewrites_session_id`
13. `test_truedup_quarantine_mode_moves_to_orphan_file`
14. `test_truedup_idempotent_after_apply`
15. `test_target_session_id_alias_works`
16. `test_session_id_param_warns_deprecated`
17. `test_conflicting_session_id_and_target_session_id_errors`
18. `test_bootstrap_rotation_rekeys_old_bucket_unread`

**Integration:**

19. `test_int_sr_round_trip_send_through_correct_bucket`
20. `test_int_sr_misroute_caught_before_disk_write`
21. `test_int_sr_truedup_recovers_2026-04-29_orphan` (regression for the actual b3fccc3d incident shape)
22. `test_int_sr_session_rotation_does_not_lose_unread`

---

## Phased Rollout

**SR-Phase 1 (ships first - high leverage, low risk):** SR2 + SR3. Server-side validation + default-resolve. No schema change. Prevents the misroute footgun outright. Two unit tests, one integration test. Smallest possible PR that closes the bug.

**SR-Phase 2:** SR1 + SE-aware compact. Schema bump for new row fields. Requires `BRIDGE_SCHEMA_EVOLUTION_SPEC.md` SE1 to have shipped (or ship together).

**SR-Phase 3:** SR4 truedup tool. Recovers existing orphans. Useful retroactively (cleans `b3fccc3d` from this incident).

**SR-Phase 4 (optional):** SR5 param rename. Backwards-compat deprecation cycle.

**SR-Phase 5:** SR6 + SR7 + SR8 + SR9. Documentation cleanup + bootstrap-rotation safety + backpressure auto-nudge + user-action breaker bypass. SR8 and SR9 sit in Phase 5 (not earlier) because they depend on Layers 1-4 wake protections being live, which they now are post-d97eaf9c.

Phases 1-3 are the high-value work. 4-5 are polish + recovery loops.

---

## Coordination Model

Codex implements; Claude reviews. Audit profile: `tools/agent-bridge/audit-profiles/session-routing-hardening.md` (added in same commit as this spec).

**Composes with:**

- `BRIDGE_SCHEMA_EVOLUTION_SPEC.md` SE1-SE10 - SR1 is one of the schema bumps that uses the SE policy.
- `BRIDGE_BOOTSTRAP_PROVENANCE_SPEC.md` - SR1's `from_session_id_kind` reuses the parent/subagent/unknown three-state from BP.
- `BRIDGE_PROTOCOL.md` - SR6 amends the body-convention documentation; protocol version bump implied.
- `BRIDGE_HEALTH_PANEL_SPEC.md` HP-Phase 2 - HP9 (recent failures) will surface `session_routing_mismatch` errors when they happen.

**Does NOT compose with:**

- Mutation tools beyond SR4 truedup (intentionally narrow).
- Cross-project pairing (separate channel; tenant_id covers cross-tenant routing).

[[handoff:codex]]
