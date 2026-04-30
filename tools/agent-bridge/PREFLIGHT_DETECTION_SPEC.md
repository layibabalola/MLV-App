# Pre-flight Composer Detection Spec

**Status:** Proposed
**Owner:** Claude implements (wake_codex.ps1 path), Codex reviews
**Scope:** Wake-time gate that reads target composer state non-intrusively
before any intrusive write path, defers when user is actively typing, and
preserves stable drafts through full clipboard save/restore.

## Motivation

Today `wake_codex.ps1` fires the intrusive write path (Ctrl+A / Delete /
SendKeys / Ctrl+Enter) on every inbox event. The system-wide idle gate at
`Get-IdleSeconds` only detects keyboard inactivity at the OS layer; it does
not see *where* the user is typing or *what is already in the composer*.
The 2026-04-30 typing-collision incident demonstrated this: the wake fired
into an actively-typed composer in the wrong window because OS-level idle
was satisfied while the user was mid-keystroke in a non-Codex foreground.
The interim band-aid (`exit 16` instead of force-inject on MaxWaitSeconds
expiry) prevents the worst case but does not preserve drafts and does not
read composer state.

Pre-flight detection is the durable replacement for that band-aid: a
non-intrusive UIA-based state read that gates the intrusive write path,
defers cleanly when the composer is actively changing, and preserves
unsent drafts when the wake must proceed.

## Goals

- Read Codex Desktop composer state without changing foreground or focus.
- Classify composer state as `idle-empty`, `idle-with-draft`, or
  `actively-typing` and act accordingly.
- Preserve unsent drafts via full IDataObject clipboard save/restore around
  the intrusive write path.
- Compose cleanly with existing wake primitives (rate limit, breaker, ack
  loop) without double-counting deferrals as failures.
- Make policy values defaults, not invariants - allow per-priority and
  per-session overrides.

## Non-Goals

- Replacing the intrusive write path itself. UIA SetFocus and SendKeys
  remain the actual delivery mechanism; pre-flight is a gate, not a
  substitute.
- Cross-application composer detection. Scope is Codex Desktop ProseMirror
  only. (Symmetric Claude-side wake is out of scope; if added later it
  inherits this spec's structure.)
- Cryptographic clipboard integrity. Restore is best-effort + Win+V history
  as user-facing fallback.
- Replacing wake breaker / rate limit semantics for the actual write path.

## Authority and Composition

Pre-flight sits between the watcher's "fire wake" decision and the existing
wake_codex.ps1 stages 4-5 (foreground activation + SendKeys). It is a
**pre-fire gate**, not a delivery path:

1. Watcher receives unread message, decides to fire wake.
2. Wake script enters pre-flight: non-intrusive UIA composer read.
3. Pre-flight classifies state and either fires the intrusive write path
   immediately, defers and re-polls, or aborts cleanly.
4. The intrusive write path (stages 4-5) still goes through:
   - Layer 3 rate limit (existing wake rate limiting)
   - Layer 4 breaker (existing wake circuit breaker)
   - Receipt verification (`seen_at` / `read_at` ack loop)

**Pre-flight events MUST NOT:**
- Count as wake failures.
- Increment the wake breaker (`WAKE_BREAKER_THRESHOLD`).
- Mark messages `seen_at` or `read_at`.
- Consume wake rate limit budget (`WAKE_PREFIRE_LIMIT`).

**Pre-flight events MUST defer cleanly when:**
- The wake breaker opens during the pre-flight wait.
- `pause_bridge` is set during the pre-flight wait.
- The owning session is superseded during the pre-flight wait.

Receipt semantics: pre-flight does not mutate `seen_at` or `read_at`. Only
the normal wake / receipt path proves delivery.

## State Machine

Three primary states, plus four error/abort states.

### Primary states

**`idle-empty`** - composer has no user content. Fire intrusive write path
immediately, no draft preservation needed.

Detected when composer text matches one of:
- Empty string or whitespace only.
- Captured placeholder fingerprint (auto-detected on first run; see below).

**`idle-with-draft`** - composer has stable non-empty content for at least
the priority-specific idle-stability window. Fire intrusive write path
**with full clipboard save/restore** around the SendKeys sequence.

**`actively-typing`** - composer content changed within the polling window.
Defer; re-poll after the polling cadence interval.

### Error/abort states

**`uia-unavailable`** - UIA read fails (UIAutomationClient assembly load
fails, AutomationElement.FromHandle returns null, or the ProseMirror element
is missing). Audit and proceed to the legacy MaxWait/idle path as
fall-through, since pre-flight cannot make a determination.

**`window-not-ready`** - Codex window minimized, off-screen, or no
MainWindowHandle. Audit and defer; this is a watcher / window-presence
problem, not a typing problem.

**`stale-context`** - between pre-flight start and the moment of paste, the
target thread / session / project no longer matches. Abort the wake; the
inbox message remains unread for the next wake cycle. Audited.

**`policy-aborted`** - bridge paused, wake breaker opens, session
superseded, or `pause_bridge` toggled during pre-flight wait. Defer cleanly.

## Polling Mechanics

- **Read primitive:** `[AutomationElement]::FromHandle` + `FindAll` for
  ProseMirror, then `TextPattern.DocumentRange.GetText(-1)`. Read-only.
  No `SetFocus`, no `Select`. UIA read is non-intrusive (`uia_setfocus_intrusive`
  memory empirically confirms read works in background; only write paths
  require activation).
- **Cadence:** poll every 5 seconds.
- **Stability requirement:** content must be byte-for-byte equal across at
  least 2 consecutive polls before transitioning out of `actively-typing`.
- **Idle-stability windows (defaults, not invariants):**

  | Priority | Window | Notes |
  |---|---|---|
  | urgent | 3 sec | Only valid when paired with 2 consecutive stable reads. Otherwise fall back to `normal`. |
  | normal | 8 sec | Default for typical bridge messages. |
  | low | 15 sec | Audit/digest/non-blocking traffic. |

- **Per-session override:** `preflight_idle_stability_seconds` may be set
  in the session config to override the priority default.

## Hard Cap

Time-boxed safety net: pre-flight cannot wait forever. Cap is
**priority-tiered** (defaults, not invariants):

| Priority | Cap | After cap |
|---|---|---|
| urgent | 30-60 sec | Force-fire with full clipboard save/restore + audit `preflight_forced_after_cap`. |
| normal | 2 min | Same. |
| low | 5 min | Same. |

After the cap fires:
- The intrusive write path runs **only with full draft preservation** (full
  IDataObject save before, restore after, in try/finally).
- An audit event records that the cap forced progress despite continued
  typing detection.
- The wake breaker is **not** incremented (this is policy, not failure).

## Clipboard Discipline

Mandatory whenever the composer state is `idle-with-draft` or the cap
forces progress through `actively-typing`:

1. **Save:** `[System.Windows.Forms.Clipboard]::GetDataObject()` returns
   full `IDataObject` (preserves text, RTF, HTML, image, file-list - not
   just plain text).
2. **Inject:** `SetDataObject(payload, copy=true)`.
3. **Paste:** existing SendKeys path (Ctrl+A / Delete / Ctrl+V or typed
   message / Ctrl+Enter).
4. **Restore:** `SetDataObject(saved, copy=true)` in `finally` block so
   restore runs even on paste exception.
5. **Restore-failure recovery:** if restore throws or returns non-success,
   audit `preflight_clipboard_restore_failed` and emit a one-line
   user-facing console warning: "Original clipboard could not be restored.
   Use Win+V to recover from clipboard history."

The save/restore round-trip is conditional on a non-trivial save state. If
`GetDataObject` returns null or contains zero formats, skip the
save/restore dance (nothing to lose).

## Stale-Context Check

Immediately before paste (after pre-flight stability achieved or cap
forced), re-verify:
- Target Codex window handle still exists.
- Window title still matches the expected project / thread marker.
- Owning bridge session still active (not superseded).
- Bridge not paused.

If any check fails, abort the paste and audit `preflight_aborted_policy_state`.
The inbox message remains unread; the next wake cycle will re-evaluate.

## Placeholder Fingerprinting

Codex Desktop's empty composer renders placeholder text (currently
`\nAsk for follow-up changes` per the prototype). The fingerprint changes
across Codex Desktop builds, so it cannot be hard-coded.

**Strategy:** auto-detect on first run, allow override.

- **First-run probe:** when `placeholder_fingerprint` is unset for a
  session, capture the current composer text immediately after a known-empty
  state (e.g., right after Ctrl+Enter delivery) and persist it as the
  fingerprint.
- **Config override:** `preflight_placeholder_fingerprint` in session
  config overrides the auto-detected value.
- **Fail-safe default:** if no fingerprint is captured AND the composer
  text is non-empty, treat as `idle-with-draft` (preserve drafts) rather
  than `idle-empty` (proceed without preservation). Bias toward draft
  safety.

Per `convention_over_configuration` memory: auto-detect is the default;
explicit override is allowed for users with known-stable Codex builds.

## Audit Events

Required audit events (written to `messages.jsonl` via existing breadcrumb
infrastructure):

| Event | Fields |
|---|---|
| `preflight_state_detected` | message_id, state, composer_text_hash, priority, idle_seconds_observed |
| `preflight_deferred_active_typing` | message_id, deferred_for_seconds, current_state, retry_count |
| `preflight_forced_after_cap` | message_id, priority, cap_seconds, draft_preserved (bool) |
| `preflight_draft_preserved` | message_id, save_format_count, restore_succeeded |
| `preflight_clipboard_restore_failed` | message_id, save_format_count, exception_text |
| `preflight_aborted_policy_state` | message_id, abort_reason (breaker_open / paused / superseded / stale_context / window_not_ready / uia_unavailable) |

`composer_text_hash` is SHA-256 of the read text; the raw text is **not**
audited (privacy: composer content is user data, not bridge protocol).

## Receipt Semantics (re-iterated)

Pre-flight events **never** mutate `seen_at` or `read_at` on the inbox
message. Only the normal post-wake receipt path (composer ack visible to
watcher poller) proves delivery. A pre-flight that defers all the way to
hard-cap-then-paste still relies on the existing receipt loop to mark the
message handled.

## Test Requirements

Required tests (PowerShell + python-side):

- **idle-empty fast-path:** composer empty / placeholder match → fire
  immediately, no save/restore, exit 0.
- **idle-with-draft preservation:** composer has 100-byte stable text → fire
  with save/restore, verify clipboard restored to original after paste.
- **actively-typing defer:** composer text changes between polls → defer up
  to cap, audit `preflight_deferred_active_typing` per poll.
- **cap fires under sustained typing:** continuous typing through full cap
  window → `preflight_forced_after_cap` audit, draft preserved.
- **2-consecutive-stable for urgent:** urgent priority + single-poll
  stability does NOT transition; 2-poll stability does.
- **stale-context abort:** between pre-flight start and paste, change the
  Codex window title → abort, audit, message stays unread.
- **breaker-opens defer:** breaker opens during pre-flight wait → defer
  cleanly, no force-paste.
- **clipboard-unavailable:** mock `GetDataObject` to throw → fall through
  to no-save path, audit, fire without restore (no preservation possible).
- **clipboard-restore-fails:** mock `SetDataObject` to throw on restore →
  audit + console warning, paste still completed.
- **placeholder auto-detect:** first run with empty fingerprint → capture
  after first delivery, persist; subsequent runs use captured value.
- **UIA unavailable:** simulate UIAutomationClient assembly load failure →
  fall through to legacy MaxWait/idle path, audit `uia_unavailable`.
- **deferral does not increment breaker:** N consecutive deferrals → wake
  breaker count unchanged.
- **deferral does not consume rate limit:** N consecutive deferrals →
  `WAKE_PREFIRE_LIMIT` budget unchanged.
- **deferral does not mark seen:** message remains in inbox after
  deferrals.

## Acceptance Criteria

- Wake fired when composer is `idle-empty` does not preserve clipboard
  state (no-op fast path).
- Wake fired when composer is `idle-with-draft` preserves the user's
  unsent draft via clipboard round-trip; user verifies original clipboard
  intact after delivery.
- Wake deferred when composer is `actively-typing` for less than the cap;
  user observes no keystroke collision and no foreground theft during
  deferral.
- Wake forced after cap fires the intrusive write path with draft
  preservation and emits `preflight_forced_after_cap` audit.
- Pre-flight deferrals do not trip the wake breaker, do not consume rate
  limit, do not mark messages seen.
- Stale-context check correctly aborts when target thread / session
  changes during pre-flight wait.
- All audit events listed appear in `messages.jsonl` for their respective
  scenarios.
- Placeholder fingerprint auto-captures on first run; override config
  honored when set.
- Existing `wake_codex.ps1 -DryRun`, `-FindOnly`, `-RunInnerWake`,
  `-PrintInnerCommand` paths remain functional after pre-flight integration.
- The interim `exit 16` band-aid is replaced by structured pre-flight
  states once the spec is implemented; `WAKE_DEFERRED_EXIT_CODE` becomes
  one of several pre-flight defer reasons rather than the single fall-through.

## Migration From Band-Aid

Current state (post-2026-04-30 band-aid commit `6eae4c21`):
- `wake_codex.ps1:380-384` exits 16 on `MaxWaitSeconds` expiry without idle.
- `watcher.py:55-56` exempts exit 16 from the wake breaker.
- The intrusive write path still uses raw SendKeys with no draft
  preservation.

Implementation plan (post-spec):
1. Land pre-flight UIA detection layer ahead of stage 3 (the existing
   idle wait).
2. Replace stage 5 SendKeys block with a clipboard-paste path guarded by
   save/restore.
3. Keep `exit 16` and the breaker-exempt set as fallback for pre-flight
   error states (`uia-unavailable`, `policy-aborted`).
4. Migrate `MaxWaitSeconds` semantics: the legacy single-window cap
   becomes one of three priority-tiered caps in the new state machine.
5. Update tests to cover all 11 scenarios listed in Test Requirements.

## Security Notes

- UIA composer read is **read-only**; the spec deliberately avoids
  `SetFocus`, `Select`, and any pattern with side effects. Per
  `uia_setfocus_intrusive` memory, all composer-write paths require window
  activation; pre-flight is the read-only counterpart.
- Composer text content is **not** stored in audit logs - only its
  SHA-256 hash. Composer content is user data and outside the bridge's
  audit scope.
- Stale-context check is the defense against TOCTOU between pre-flight
  decision and paste execution. Without it, a delayed paste could land in
  the wrong thread after the user navigated.
- The `idle-with-draft` state with cap-forced-progress is the residual
  hostile-typing case where user typing continues for the full cap
  window. Draft preservation via clipboard round-trip is the user
  contract: "we will not lose your text even if we have to interrupt."
- Win+V history is the second-line recovery if clipboard restore fails.
  The user-facing warning makes this discoverable rather than silent.

## Open Questions for Reviewer

1. Should the `urgent` priority cap default to 30 sec or 60 sec? Codex's
   pass_with_changes suggested "30-60" without committing; I default to
   45 sec in this spec but call it tunable.
2. Should `idle-with-draft` always preserve clipboard, or only when the
   draft exceeds N characters (cheap drafts may not be worth round-trip
   risk)? Current spec says "always preserve when non-empty"; revisit if
   restore-failure rate proves nontrivial.
3. Should the spec mandate a config knob to disable pre-flight entirely
   (legacy mode) for debugging? Current spec assumes always-on; happy to
   add a `preflight_enabled` config field if useful.
