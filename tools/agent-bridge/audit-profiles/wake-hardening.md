# Audit Profile - Wake Hardening (pause gating + circuit breaker)

**Trigger:** `WAKE_HARDENING_SPEC.md` (Codex review pending or in flight).

**Audit timing:** per phase (W1 pause gating, W2 circuit breaker).

---

## W1 - pause_bridge gating

### Required behavior

- [ ] `pause_bridge` MCP tool writes `<bridge-root>/state/pause.json`
  atomically
- [ ] Schema: schema_version, paused, paused_at, paused_by_session, scope
- [ ] Watcher reads pause.json each poll (mtime-cached); detects changes
  within one poll cycle
- [ ] When paused: `on_message_command` SKIPPED;
  `on_message: "toast"` STILL fires
- [ ] Audit event `wake_skipped_paused` per skipped message
- [ ] Skipped messages NOT marked seen (so they re-deliver on resume)
- [ ] `resume_bridge` deletes pause.json; next poll resumes wake firings
- [ ] Double-pause from different session_id: refused

### Tests

- [ ] `test_pause_bridge_writes_pause_marker`
- [ ] `test_watcher_skips_wake_command_when_paused`
- [ ] `test_watcher_still_emits_toast_when_paused`
- [ ] `test_resume_bridge_removes_pause_marker_and_unblocks_watcher`
- [ ] `test_pause_bridge_refuses_double_pause_from_different_owner`

---

## W2 - Circuit breaker

### Required behavior

- [ ] Per-session_id failure window (rolling 5 min, deque<(ts, exit_code)>)
- [ ] Threshold: 5 failures opens breaker
- [ ] Success (exit 0) clears the breaker
- [ ] Open breaker: skip wake, audit `wake_breaker_open` once,
  mark seen with `wake_skipped_breaker_open`, terminal toast once
- [ ] Auto-close: 15 min of inactivity OR explicit `resume_wake_for_session`
- [ ] Exit-code distribution kept as metadata (not in dedup key) per
  AUTO_PAIR_SPEC Q5 resolution

### Tests

- [ ] `test_breaker_counts_consecutive_failures`
- [ ] `test_breaker_opens_at_threshold`
- [ ] `test_breaker_clears_on_success`
- [ ] `test_breaker_auto_closes_after_idle`
- [ ] `test_breaker_alternating_exit_codes_still_trips_threshold`
  (validates per-session, not per-(session, exit_code))
- [ ] `test_breaker_emits_user_notification_once`
- [ ] `test_resume_wake_for_session_clears_breaker`

---

## Live verification

After W1 ships, user can verify:

1. Run `mcp__agent-bridge__pause_bridge`
2. Send a bridge message (or trigger one)
3. Observe: toast appears, but NO wake_codex.ps1 process spawned
4. Run `mcp__agent-bridge__resume_bridge`
5. Send another message
6. Observe: wake_codex.ps1 process spawned, SendKeys nudge happens

After W2 ships, simulate 5 consecutive failures (e.g. close Codex
Desktop, send 5 messages); verify:

1. After 5th failure: breaker opens
2. 6th message: NO wake attempt (audit log shows
   `wake_skipped_breaker_open`)
3. Terminal notification appears once
4. After 15 min idle, breaker auto-closes
5. Next wake fires normally

---

## AUDIT_RESULT templates

### W1 (pause gating)

```
TYPE: AUDIT_RESULT
STATUS: pass | fail
SUMMARY: <SHA> Wake hardening W1 - pause_bridge gates watcher
ACTION_REQUESTED: none
NONCE: audit-wake-w1-<sha-short>
SCOPE: project-only

Reviewed <sha>. Coverage:
- pause.json schema + atomic write: CHECK | MISSING
- Watcher reads pause.json + detects within poll cycle: CHECK | MISSING
- Pause skips on_message_command: CHECK | MISSING
- Pause preserves on_message toast: CHECK | MISSING
- Audit event wake_skipped_paused: CHECK | MISSING
- Skipped messages not marked seen: CHECK | MISSING
- resume_bridge unblocks: CHECK | MISSING
- Double-pause refused: CHECK | MISSING

Tests at HEAD: <N> pass.

[Push back deviations; otherwise PASS]

[[handoff:codex]]
```

### W2 (circuit breaker)

```
TYPE: AUDIT_RESULT
STATUS: pass | fail
SUMMARY: <SHA> Wake hardening W2 - per-session circuit breaker
ACTION_REQUESTED: none
NONCE: audit-wake-w2-<sha-short>
SCOPE: project-only

Reviewed <sha>. Coverage:
- Per-session failure window tracking: CHECK | MISSING
- Threshold 5 failures opens: CHECK | MISSING
- Success clears: CHECK | MISSING
- Audit + toast on open (once): CHECK | MISSING
- Auto-close at 15 min idle: CHECK | MISSING
- Resume tool: CHECK | MISSING
- Per-session key (alternating exit codes still trip): CHECK | MISSING

Tests at HEAD: <N> pass.

[Push back deviations; otherwise PASS]

[[handoff:codex]]
```
