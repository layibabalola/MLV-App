# Wake Hardening Spec - Pause Gating, Circuit Breaker, Title-Marker Lessons

**Status:** Partially implemented. D1 pause gating is now landed in the
working tree; D2 circuit-breaker follow-up remains open.
**Authors:** Claude
**Motivation:** capture three wake-loop-suppression concerns that Codex's
2026-04-28 SPEC_REVIEW_RESULT explicitly kept OUT of `AUTO_PAIR_SPEC`:
(1) `pause_bridge` does not gate watcher's `on_message_command`,
(2) circuit breaker for repeatedly-failing wake is not implemented,
(3) the title-marker layer (Phase A) was a dead-end that taught us
title heuristics are unreliable on Windows hosts.

---

## Background

The 2026-04-28 incident timeline (concrete data to anchor this spec):

1. 9 AUDIT_RESULTs / SPEC_PROPOSALs queued for Codex in rapid succession
2. Codex's wake mechanism on Claude side fired wake_codex.ps1 per message
3. Watcher's running code was pre-Phase-A; treated all non-zero exits as
   retryable; with N queued messages × 3 retries × 90s = 6+ minutes of
   wake activity
4. User invoked `pause_bridge` MCP tool → bridge accepted "paused" status
5. **Watcher did NOT honor pause** — it kept polling inbox-codex.jsonl
   and firing wake_codex.ps1
6. User had to manually edit `watcher-config.json` to remove
   `on_message_command` to actually stop the wake firings
7. Phase A landed with title-marker check; on this host every wake fired
   exit 3 (title doesn't contain marker) → no nudge at all
8. User directed title-marker removal; Phase A revert in flight

Three independent defects revealed:

- **D1.** `pause_bridge` MCP call does not propagate to the watcher's
  autonomous polling. The "stop button" doesn't stop.
- **D2.** No circuit breaker exists for "wake keeps failing the same way";
  retry storms can run for the full `N × MAX_RETRIES` budget.
- **D3.** Title-heuristic verification (Phase A) was the wrong intermediate
  step on Windows hosts where `MainWindowTitle` is unreliable. Real
  identity proof needs Phase B breadcrumbs.

This spec covers D1 + D2; D3 is closing via Phase A revert + Phase B.

---

## D1 - pause_bridge gating watcher's on_message_command

### Current state

The branch now stores pause state in bridge `state.json` (`paused: true`),
and the watcher checks that state before firing wake commands. While paused,
toast/log notifications still surface, but autonomous `on_message_command`
invocation is skipped and audited as `wake_skipped_paused`. The remaining
open items in D1 are mostly contract clarity questions, not the original
"stop button doesn't stop" bug.

### Implemented in this branch

1. `pause_bridge` and `resume_bridge` continue to write the canonical bridge
   paused flag in `state.json`.
2. Watcher checks paused state per poll cycle before firing wake commands.
3. While paused:
   - watcher still emits toast/log notification
   - watcher skips `on_message_command`
   - watcher appends `wake_skipped_paused`
   - watcher does not mark the message seen just because wake was suppressed
4. After resume:
   - paused wake-suppressed messages become eligible for normal wake handling
   - the watcher resumes normal receipt-driven behavior

### Acceptance criteria

- W1. `pause_bridge` writes canonical paused state and watcher detects it
  within one poll cycle.
- W2. While paused, new inbox messages do NOT fire `on_message_command`;
  audit log contains `wake_skipped_paused` events with reason.
- W3. While paused, `on_message: "toast"` STILL fires (user sees new
  messages).
- W4. `resume_bridge` clears the paused state; first message after resume fires
  wake normally.
- W5. If ownership semantics for pause are later introduced, they must be
  explicit. They are not part of the current shipped slice.

### Tests

- `test_watcher_skips_wake_command_when_paused`
- `test_watcher_still_emits_toast_when_paused`
- `test_resume_bridge_removes_pause_state_and_unblocks_watcher`

---

## D2 - Circuit breaker for repeatedly-failing wake

### Current state

`watcher.py` retries failing wake invocations up to `WAKE_MAX_RETRIES = 3`
per message, with `WAKE_ACK_GRACE_PERIOD_S = 30` between retries. Phase A
introduced `WAKE_PERMANENT_EXIT_CODES = {3}` for "no retry" exits, but
that's per-exit-code, not per-target.

If wake is failing for a session-wide reason (e.g. dead Codex Desktop,
OS misconfiguration), each new message triggers its own up-to-3-retries.
With N queued messages: `N × MAX_RETRIES × GRACE_PERIOD ≈ N × 90s` of
retry storm.

### Proposed fix

Per-session circuit breaker, keyed by `session_id` (per Codex's resolved
answer to AUTO_PAIR_SPEC Q5).

1. Watcher tracks a `wake_failure_window` per `session_id`:
   ```
   { session_id: deque<(timestamp, exit_code)> }
   ```
2. On wake invocation result:
   - If exit code is in `WAKE_PERMANENT_EXIT_CODES`: count toward breaker
   - If exit code is non-zero (transient): count toward breaker
   - If exit code is 0: clear the breaker for this session_id (success
     resets)
3. If `wake_failure_window[session_id]` contains ≥ 5 failures within the
   last 5 minutes: **open the breaker** for that session_id.
4. While breaker is open for a session_id:
   - Skip wake invocation
   - Log `wake_breaker_open` audit event (once per breaker-open; not per
     suppressed message)
   - Mark message seen with reason `wake_skipped_breaker_open` (to
     prevent retries piling up)
   - User-facing notification: terminal toast saying "Codex wake is
     suppressed; X consecutive failures; investigate or run resume_wake"
5. Breaker auto-closes after 15 minutes of inactivity (no new wake
   attempts) OR explicit `resume_wake_for_session` MCP tool call.
6. Exit code distribution kept as metadata for diagnostics:
   ```json
   {
     "session_id": "<guid>",
     "breaker_state": "open",
     "consecutive_failures": 7,
     "exit_code_distribution": {"3": 5, "1": 2, "timeout": 0},
     "opened_at": "2026-04-29T04:00:00+00:00",
     "auto_close_at": "2026-04-29T04:15:00+00:00"
   }
   ```

### Why per-session not per-(session, exit_code)

Per Codex's resolved answer (AUTO_PAIR_SPEC Q5): if breaker keyed by
`(session_id, exit_code)`, alternating exit classes (1 → 3 → 1 → 3) against
the same dead target would bypass the breaker — each pair would have its
own counter. Per-session-only key prevents this.

### Acceptance criteria

- C1. Breaker counts failures per session_id; rolling 5-minute window.
- C2. Threshold of 5 failures opens the breaker; subsequent wakes are
  skipped with `wake_skipped_breaker_open`.
- C3. One success (exit code 0) clears the breaker.
- C4. Breaker auto-closes after 15 minutes of inactivity OR explicit
  resume.
- C5. Exit-code distribution logged in breaker state for diagnostics.
- C6. User-facing terminal notification fires once per breaker-open.

### Tests

- `test_breaker_counts_consecutive_failures`
- `test_breaker_opens_at_threshold`
- `test_breaker_clears_on_success`
- `test_breaker_auto_closes_after_idle`
- `test_breaker_alternating_exit_codes_still_trips_threshold`
- `test_breaker_emits_user_notification_once`
- `test_resume_wake_for_session_clears_breaker`

---

## D3 - Title-marker lessons learned (closure)

### Already in flight

- Phase A title-check producer being reverted (per ACTION_REQUEST
  `8efd4010` to Codex)
- Watcher exit-3 infrastructure preserved for future Phase B reuse
- AUTO_PAIR_SPEC.md updated with full historical context

### Lessons codified

1. **OS-level window titles are unreliable on Windows hosts** for
   identity proof. `MainWindowTitle` returns blank or non-predictable
   strings for desktop Electron apps. Don't depend on title heuristics
   for security-critical decisions.
2. **Active-wake nudge mechanism (SendKeys) is fragile** — the deeplink
   is the GUID-bearing channel; everything post-deeplink is best-effort.
   Don't add layers that fail-closed without giving up the nudge entirely.
3. **The smallest unit that closes a loop is the unit that USES the
   GUID, not a layer that pretends to verify it.** Phase B's
   breadcrumb-driven UUID check is the smallest meaningful identity
   layer. Title-marker was overhead.

These lessons go into `AUTO_PAIR_SPEC.md` Layer 2 section (already done
in the 2026-04-28 update).

---

## Migration plan

**Phase W1 - pause_bridge gating** (D1)
- Use canonical paused state in bridge `state.json`
- Watcher reads paused state each poll; skip `on_message_command` when
  paused; toast still fires
- Tests W1-W5

**Phase W2 - circuit breaker** (D2)
- Add per-session failure window tracking in watcher
- Open breaker at 5 failures / 5 min; auto-close at 15 min idle
- `resume_wake_for_session` MCP tool
- Tests C1-C7

These phases are independent. W1 is now effectively shipped on this branch and
addresses the user-perception bug ("the stop button doesn't stop"). W2 remains
the substantive open wake-loop follow-up.

---

## Coordination model

Codex implements; Claude reviews. This spec is a doc proposal, not a
commitment. Codex can disagree with the design (e.g. prefer different
threshold values) and reply via SPEC_REVIEW_RESULT.

Audit profile: `tools/agent-bridge/audit-profiles/wake-hardening.md`
(stub).

[[handoff:codex]]
