# Wake Hardening Spec - Pause Gating, Circuit Breaker, Title-Marker Lessons

**Status:** Implemented for D1/D2/D4/D5. D3 title-marker lessons are closed by
retiring the title heuristic and moving identity proof to breadcrumb/provenance
work in `AUTO_PAIR_SPEC.md` and `BRIDGE_BOOTSTRAP_PROVENANCE_SPEC.md`.
**Authors:** Claude
**Motivation:** capture three wake-loop-suppression concerns that Codex's
2026-04-28 SPEC_REVIEW_RESULT explicitly kept OUT of `AUTO_PAIR_SPEC`:
(1) `pause_bridge` did not gate watcher's `on_message_command`,
(2) circuit breaker for repeatedly-failing wake was not implemented,
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

Updated implementation note: `watcher.py` now records per-session wake failure
windows in `wake-failure-windows.json`. Repeated failures open a breaker for
that session, which suppresses further wake attempts until explicit reset,
one-shot bypass, or the idle auto-close path. Permanent-exit handling remains
per-message, while the breaker covers session-wide failure modes.

`watcher.py` retries failing wake invocations up to `WAKE_MAX_RETRIES = 3`
per message, with `WAKE_ACK_GRACE_PERIOD_S = 30` between retries. Phase A
introduced `WAKE_PERMANENT_EXIT_CODES = {3}` for "no retry" exits, but
that's per-exit-code, not per-target.

If wake is failing for a session-wide reason (e.g. dead Codex Desktop,
OS misconfiguration), each new message triggers its own up-to-3-retries.
With N queued messages: `N × MAX_RETRIES × GRACE_PERIOD ≈ N × 90s` of
retry storm.

### Implemented fix

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

---

## D4 - Wake re-fire on successful delivery (seen_ids guard)

**Status:** Implemented in `watcher.py` (commit `076bd500`, 2026-05-01).

### Problem

`_queue_pending_wake_verifications` checked `existing_ids` (messages currently
in the pending-wake list) to avoid duplicate enqueues, but did NOT check
`seen_ids` (messages already delivered). After successful delivery:

1. Message is removed from the pending list (`existing_ids`).
2. Message is added to `seen_ids`.
3. On the next poll, the message was absent from `existing_ids` and no wake
   receipt existed yet → re-enqueued → second wake fired for the same message.

Same gap existed in `_queue_paused_wake_messages`.

### Fix

Added `or message_id in seen_ids` guard to both enqueue functions:

```python
if not message_id or message_id in existing_ids or message_id in seen_ids:
    continue
```

### Acceptance criteria

- D4.1. A successfully delivered message does not re-enqueue after delivery.
- D4.2. A paused-then-resumed message fires exactly once after resume.
- D4.3. A genuinely undelivered message (no receipt) still retries normally.

---

## D5 - UIA SetFocus as primary foreground acquisition strategy

**Status:** Implemented in `wake_codex.ps1` Stage 4b (commit `91c479ef`, 2026-05-01).

### Problem

Win32 `SetForegroundWindow` nearly always fails from a background process due to
`ForegroundLockTimeout`. The API returns success but the window does not come to
the foreground; the taskbar button flashes orange instead. This was causing
reliable focus failures on every wake.

### Fix

Promoted UIA `[AutomationElement].SetFocus()` on the cached ProseMirror composer
element to the primary focus acquisition path (Stage 4b). Empirically:
- UIA `SetFocus()` acquires foreground, confirmed against a Notepad force-foregrounded
  baseline (see `uia_setfocus_intrusive` memory).
- Win32 chain (`SetForegroundWindow`, ALT-tap, SPI nuke, `SwitchToThisWindow`)
  retained as fallback if UIA path fails.

Stage 4b logic:
1. Try UIA `SetFocus()` on cached composer element.
2. Wait 50ms; check `GetForegroundWindow()`.
3. If foreground confirmed → proceed.
4. If not → fall through to Win32 fallback chain.

Orange taskbar flash eliminated after this change. Note: UIA `SetFocus()` IS
intrusive — it does change foreground. There is no non-intrusive write path;
all wake delivery requires window activation.

---

## D6 - User UI State Restoration Boundary

**Status:** Guard implemented in `wake_codex.ps1` Stage 4.

### Problem

Restoring the previous foreground HWND is sufficient only when the user was in
another app. If the previous foreground HWND is already Codex Desktop, opening
`codex://threads/<target>` can switch the user's visible Codex thread and leave
them displaced even after foreground restoration.

### Guard

Targeted SendKeys wake treats user UI state as transactional:

- If the previous foreground app is not Codex, targeted wake may navigate to the
  protected bridge thread and then restore the previous foreground HWND.
- If the previous foreground app is Codex and the current visible title matches
  the cached target thread title, wake skips deeplink navigation and types into
  the already-visible target.
- If the previous foreground app is Codex but the current visible thread is a
  different or unprovable thread, wake defers instead of navigating, unless a
  exact `RestoreThreadId` is available and valid.

Title matching is only a practical restoration guard, not a stable identity
proof. Generic Desktop titles such as `Codex` are recorded as unknown, not as
project mismatches. DOM/app-server telemetry may later replace title checks with
exact visible thread-id detection.

Failure mode: emit `targeted_wake_refused` with
`foreground_codex_restore_thread_unavailable` and exit with retryable code `16`.

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

These phases are independent and both are shipped. Follow-on recovery behavior
for breaker-open backlogs is implemented in `BRIDGE_WAKE_RECOVERY_LOOPS_SPEC.md`;
it layers on top of D2 without changing the storm-prevention threshold.

---

## Coordination model

Codex implements; Claude reviews. This spec is a doc proposal, not a
commitment. Codex can disagree with the design (e.g. prefer different
threshold values) and reply via SPEC_REVIEW_RESULT.

Audit profile: `tools/agent-bridge/audit-profiles/wake-hardening.md`
(stub).

[[handoff:codex]]
