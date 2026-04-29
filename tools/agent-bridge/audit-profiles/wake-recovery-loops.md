# Audit Profile - Wake Recovery Loops

**Trigger:** commit implementing any of WR1/WR2/WR3 (or WR1a) in `BRIDGE_WAKE_RECOVERY_LOOPS_SPEC.md`
**Reference spec:** `BRIDGE_WAKE_RECOVERY_LOOPS_SPEC.md` WR1-WR3 + optional WR1a

---

## Files this profile covers

- `tools/agent-bridge/agent_bridge.py` (MCP tool registration: `nudge_peer`; send_to_peer error path; mark_read enhancement)
- `tools/agent-bridge/server.py` (tool dispatch)
- `tools/agent-bridge/watcher.py` (Layer 4 auto-close transition for WR3; bypass_grant decrement on fire)
- `tools/agent-bridge/wake-failure-windows.json` schema (adds `bypass_grant` field)
- `tools/agent-bridge/test_wake_recovery_loops.py` (new test file)

---

## WR-by-WR audit checklist

### WR1 - Backpressure rejection auto-nudge

- [ ] `send_to_peer` rejection with `error_kind="backpressure_unread_work"` triggers a wake_<peer> attempt
- [ ] Wake honors Layer 3 rate limit (skipped if recent fire)
- [ ] Wake honors Layer 4 breaker (skipped if open)
- [ ] Audit events emitted per path:
  - `backpressure_rejected_with_nudge`
  - `backpressure_rejected_no_nudge_breaker_open`
  - `backpressure_rejected_no_nudge_rate_limited`
- [ ] Rejection error response includes nudge status (`data.nudge_status` or equivalent)
- [ ] Tests:
  - `test_wr1_backpressure_rejection_fires_nudge_when_breaker_closed`
  - `test_wr1_backpressure_rejection_skips_nudge_when_breaker_open`
  - `test_wr1_backpressure_rejection_skips_nudge_when_rate_limited`
  - `test_wr1_backpressure_rejection_emits_correct_audit_event_per_path`
  - `test_wr1_nudge_honors_layer3_rate_limit_under_rapid_backpressure`

**Critical pushback:** if WR1's nudge bypasses Layer 4 breaker, STATUS=fail. WR1 is the "respects breaker" path; bypass belongs only to WR2.

### WR1a - Held-send retry (optional, feature-flagged)

- [ ] `WR1A_HELD_SEND_ENABLED` flag introduced; default off
- [ ] When enabled: server holds rejected send for up to `WR1_HOLD_SECONDS` (default 30); writes after receiver drains
- [ ] When disabled: behavior identical to WR1-only (no hold, immediate error)
- [ ] Tests:
  - `test_wr1a_held_send_writes_after_receiver_drains`
  - `test_wr1a_held_send_times_out_returns_error`
  - `test_wr1a_disabled_by_default_returns_immediate_error`

### WR2 - User-action breaker bypass

- [ ] New MCP tool `nudge_peer(agent, session_id=None)` registered in agent_bridge.py + server.py
- [ ] Bypasses open breaker on call (one-shot per call)
- [ ] Rate-limited per `(agent, session_id)` per `BREAKER_BYPASS_COOLDOWN_S` (default 60s)
- [ ] On bypass success: breaker fully closes (failure window cleared)
- [ ] On bypass failure: breaker stays open; failure recorded normally; bypass NOT re-granted
- [ ] `resume_bridge` grants one bypass per open breaker
- [ ] `mark_read` of a message older than `BREAKER_BYPASS_STALE_MIN` (default 30 min) when breaker is open grants one bypass
- [ ] `wake-failure-windows.json` schema gets `bypass_grant` field; watcher decrements on use
- [ ] Audit events:
  - `nudge_peer_user_initiated`
  - `nudge_peer_user_initiated_rate_limited`
  - `resume_bridge_breaker_bypass_grant`
  - `mark_read_stale_breaker_bypass_grant`
- [ ] Tests:
  - `test_wr2_nudge_peer_fires_when_breaker_closed`
  - `test_wr2_nudge_peer_bypasses_open_breaker_one_shot`
  - `test_wr2_nudge_peer_bypass_failure_keeps_breaker_open`
  - `test_wr2_nudge_peer_rate_limit_blocks_rapid_calls`
  - `test_wr2_resume_bridge_grants_bypass_per_open_breaker`
  - `test_wr2_resume_bridge_no_grant_when_no_open_breaker`
  - `test_wr2_mark_read_stale_message_grants_bypass`
  - `test_wr2_mark_read_fresh_message_does_not_grant_bypass`
  - `test_wr2_bypass_success_closes_breaker_fully`
  - `test_wr2_bypass_failure_does_not_extend_breaker_window`

**Critical pushback if seen during audit:**
- Bypass cascade abuse: ANY code path that lets bypasses accumulate / chain → STATUS=fail. One user action → one bypass.
- Agent-driven bypass: if a non-user-surface caller (internal poller, automation) is allowed to call `nudge_peer` without rate limit holding → STATUS=fail.
- Bypass without rate limit: STATUS=fail.
- Bypass granted by tools NOT in the WR2 fixed list (e.g. `mark_seen`, `pause_bridge`) → STATUS=fail.

### WR3 - Breaker auto-close drains backlog

- [ ] On Layer 4 breaker open → closed via auto-close path: server checks for unread messages
- [ ] If unread present: fires ONE wake_<peer> invocation
- [ ] If no unread: does nothing
- [ ] Subject to Layer 3 rate limit (may be deferred 10s)
- [ ] Failure of retry counts as Layer 4 first failure (window restarts)
- [ ] Audit event `wake_breaker_autoclose_retry` per fire
- [ ] One wake per session regardless of backlog size
- [ ] Triggered ONLY by auto-close path, NOT by WR2 user-bypass-success
- [ ] Tests:
  - `test_wr3_autoclose_with_unread_fires_one_wake`
  - `test_wr3_autoclose_with_no_unread_does_nothing`
  - `test_wr3_autoclose_retry_failure_starts_fresh_window`
  - `test_wr3_autoclose_retry_respects_layer3_rate_limit`
  - `test_wr3_user_bypass_close_does_NOT_trigger_autoclose_retry`

**Critical pushback if seen:** WR3 firing more than once per auto-close transition → STATUS=fail. The whole point is "one wake per cycle."

---

## Required test pass

```bash
cd tools/agent-bridge
py -3 -m unittest test_agent_bridge test_wake_recovery_loops test_phase0_contract
```

---

## Integration test must-pass

Codex's commit MUST include `test_int_wr_recovery_loop_2026-04-29_regression` - it replays the actual 2026-04-29 13:53 UTC scenario:

1. 5 wake failures open the breaker
2. Send hits backpressure
3. Verify WR1 attempts nudge → audit shows `backpressure_rejected_no_nudge_breaker_open`
4. User calls `nudge_peer` → WR2 bypass fires → success path closes breaker
5. (alternate flow) 15min idle elapses → WR3 fires one retry wake

Without this regression test, the audit STATUS=fail. The whole point of the spec is "today's incident wouldn't happen the same way again."

---

## AUDIT_RESULT template

```
TYPE: AUDIT_RESULT
NONCE: audit-wake-recovery-<wr-phase>-<sha-short>
TIMESTAMP: <iso-utc>
SCOPE: project-only
STATUS: pass | pass-with-followup | fail
SUMMARY: <SHA> Wake recovery loops <wr-phase>
ACTION_REQUESTED: none | implement-deferred-followups

Reviewed <sha>. Coverage:

| WR# | Status |
|---|---|
| WR1 | DONE / PARTIAL / DEFERRED / MISSING |
| WR1a | DONE / DEFERRED / MISSING (optional) |
| WR2 | ... |
| WR3 | ... |

Phase status:
- WR-Phase 1 (WR3): <status>
- WR-Phase 2 (WR1): <status>
- WR-Phase 3 (WR2): <status>
- WR-Phase 4 (WR1a): <status>

Regression integration test (test_int_wr_recovery_loop_2026-04-29_regression):
<pass/fail/missing>

Critical pushback checks:
- WR1 nudge does NOT bypass breaker: <verified/violated>
- WR2 bypass cascade prevented (one action -> one bypass): <verified/violated>
- WR2 rate limit holds (60s cooldown): <verified/violated>
- WR2 bypass granted only by fixed tool list: <verified/violated>
- WR3 fires once per auto-close transition: <verified/violated>

Tests at HEAD: <N>

[[handoff:codex]]
```

---

## Coordination

WR-Phase 1 (WR3) is the smallest, highest-leverage commit. Audit Phase 1 first; Phase 2/3 follow as separate commits.

The 2026-04-29 regression integration test is the canary - if Codex ships any WR phase without that test, audit STATUS=fail regardless of other coverage. The whole point of this spec is "the system self-heals from the kind of state we just observed."
