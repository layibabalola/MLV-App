# Agent Bridge - Session Routing Hardening Spec

**Status:** Implemented for SR1-SR7 v1. Optional long-tail cleanup remains only
where explicitly called out, such as eventual removal of the legacy
`session_id` alias after a compatibility window.
**Authors:** Claude (proposal); Codex implementation/review
**Tier:** Tier 1 - ASAP single-machine fix; the fourth toast storm of 2026-04-29 was triggered by this footgun
**Motivation:** On 2026-04-29 ~13:41 UTC, Claude called `send_to_peer(to_agent="codex", session_id="<claude-session-guid>", ...)`. The message was queued into `inbox-codex.jsonl` keyed by a Claude session GUID. The Codex watcher only polls (a) Codex's active session bucket and (b) the project rendezvous bucket - it does NOT poll buckets keyed by Claude GUIDs. Result: the AUDIT_RESULT was orphaned in a bucket nobody watches. No toast, no wake, no delivery. When I corrected the route to `session_id="<codex-session-guid>"`, the new (correct-bucket) message immediately triggered the fourth wake-storm of the day - because the running watcher was pre-d97eaf9c and lacked Layers 1-4. This spec hardens the path so the misroute and the storm-from-misroute can't both happen.

The misroute is one symptom of a broader weakness: **session identity at the row level is partial and ad-hoc**. The bucket key (`session_id` field on a row) doubles as routing target, but there's no first-class record of WHICH agent session sent the message or WHICH session it was meant for. Different message types embed `FROM_SESSION:`/`TO_SESSION:` in the body via convention; some types omit them. There's no schema enforcement, no server-side validation, and no recovery tool for orphaned buckets.

---

## Implemented Status

Shipped behavior rejects `send_to_peer` calls where `session_id` names the
sender's session instead of the receiver bucket, writes v2 row-level routing
metadata, exposes `target_session_id` as the preferred receiver-bucket name,
provides `truedup_session_routing` for orphan recovery, and audits bootstrap
rotation promotion of unread old-session rows. Body-level `FROM_SESSION:` /
`TO_SESSION:` lines are now advisory; row fields are the routing source of truth.

Follow-up hardening after the 2026-04-30 wrong-thread wake incident disables
implicit project-bucket fallback for work traffic. `send_to_peer` must now name
`target_session_id` / `session_id`, `pair_id`, or `sender_session_id` that
resolves to exactly one active pair. `session.json` schema v3 records active
pair identities under `projects.<project>.pairs`, and delivered work rows carry
`pair_id` when resolvable.

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
| `pair_id` | string or null | yes (v3+) | Active pair identity when resolvable |
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

### SR8 - Pair-scoped work routing

- [x] `session.json` schema v3 records active pair rows under `projects.<project>.pairs`
- [x] `send_to_peer(..., pair_id=...)` routes to the exact peer session in that pair
- [x] `send_to_peer(..., sender_session_id=...)` routes only when that sender maps to exactly one active pair
- [x] Sessionless work sends are rejected instead of falling back to the project bucket
- [x] Inbox rows and result data include `pair_id` when the route resolves through a pair

**Note:** Wake-recovery loops (formerly drafted as SR8/SR9 here) have been split into a dedicated spec: see `BRIDGE_WAKE_RECOVERY_LOOPS_SPEC.md` (WR1 backpressure auto-nudge, WR2 user-action breaker bypass, WR3 breaker auto-close drains backlog). Recovery loops are a wake-mechanism concern, not a routing concern; separating keeps each spec audit-able as one cohesive piece.

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

**SR-Phase 5:** SR6 + SR7. Documentation cleanup + bootstrap-rotation safety.

Phases 1-3 are the high-value work; Phase 4-5 are polish. Wake recovery loops (formerly SR8/SR9 in this spec) are now in `BRIDGE_WAKE_RECOVERY_LOOPS_SPEC.md`.

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
