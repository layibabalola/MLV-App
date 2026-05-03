# Wake Codex Tuning Reference

**Status:** Active  
**Owned by:** Both agents (read before editing `wake_codex.ps1` or watcher-config.json)

This document is the authoritative reference for `wake_codex.ps1` parameters and
their tuned values. **When you add a param or change a default, update this doc
in the same commit.** The watcher-config.json and wake_codex.ps1 both point here.

---

## How the wake pipeline works

```
watcher.py polls inbox-codex.jsonl every 0.5s
  → new unread message detected
  → fires on_message_command_template (PowerShell wake_codex.ps1)
  → ps1 opens deeplink (codex://threads/<ThreadId>)
  → waits for UIA composer to be available (preflight)
  → checks composer state (empty = fast path; has draft = stability wait)
  → acquires foreground (UIA SetFocus primary, Win32 fallback)
  → injects message text via SendKeys
  → Codex reads "Watcher says check bridge inbox" → calls check_inbox MCP
```

---

## Tuned parameters (current production values)

These parameters were empirically tuned to achieve ~2.5s end-to-end wake latency
(down from 15s baseline). **Do not drop these from watcher-config.json templates.**

| Parameter | Production value | Default in script | Notes |
|---|---|---|---|
| `-Message` | `"Watcher says check bridge inbox"` | `"check bridge inbox"` | Hardcoded in `configure_watcher.py` template (was previously omitted, causing initiator to silently disappear each session restart). Approved list in `$script:ApprovedWakeMessages`. |
| `-IdleThresholdSeconds` | `0` | `5` | Fire even while user is typing elsewhere. Bridge messages are urgent. |
| `-FastPathIdleSeconds` | `0` | `1` | Fire immediately if composer is empty. Was 1; dropped to 0 for latency. |
| `-DraftStabilitySeconds` | `5` | `5` | If composer has draft, wait 5s stable before firing. Protects in-progress Codex response. Do not lower. |
| `-ActiveTypingMaxWaitSeconds` | `90` | `90` | If user has keyboard focus in the composer (actively typing), extend the preflight cap to this many seconds. Within the cap, the 5s inactivity check still applies — fires as soon as they pause for 5s or cap elapses. |
| `-DeeplinkSleepMilliseconds` | `150` | `500` | Sleep after deeplink nav before UIA lookup. UIA retry (3x at 200ms) absorbs remaining warmup. |
| `-Priority` | `urgent` | `normal` | Bridge wake is always urgent. |
| `-MaxWaitSeconds` | `60` | `60` | Total wait ceiling before deferred exit. |

## Composer preflight states

The preflight loop classifies the composer into one of three states before firing:

| State | Condition | Behavior |
|---|---|---|
| `idle-empty` | Composer is empty or shows placeholder text | Fire after `FastPathIdleSeconds` (0s — immediately) |
| `actively-typing` | Composer has text AND user has keyboard focus here | Wait up to `ActiveTypingMaxWaitSeconds` (90s); fire as soon as `DraftStabilitySeconds` (5s) of no typing change. Draft is restored after injection. |
| `idle-with-draft` | Composer has text AND user is NOT focused here | Fire after `DraftStabilitySeconds` (5s) of stability |

The distinction between `actively-typing` and `idle-with-draft` uses `HasKeyboardFocus` on the UIA composer element. This means the wake is patient while the user is composing, but still fires if they abandon the draft.

---

## Hardening parameters (added 2026-05-01 by Codex)

These were added for injection safety. Keep them — they prevent wrong-chat injection.

| Parameter | Value | Purpose |
|---|---|---|
| `-RequireThreadId` | (switch) | Abort if no ThreadId — prevents wrong-chat fallback |
| `-RequireConstantMessage` | (switch) | Message must be in approved list |
| `-VerifyTargetTwice` | (switch) | Double-check target before SendKeys |
| `-VerifyTargetGapMilliseconds` | `50` | Gap between the two target verifications |
| `-MaxPreSendRaceMilliseconds` | `500` | Abort if > 500ms between verify and send (race guard) |
| `-PostTypingVerify` | (switch) | Verify window is still Codex after typing |
| `-ProtectForegroundCodexThread` | (switch) | If Codex is already foreground, do not navigate away unless the target is already proven visible or an exact restore id is available |
| `-RestoreThreadId` | `{restore_thread_id}` | Exact previous-thread restore slot. Empty is allowed, but foreground-Codex protection then fails closed instead of displacing the user |

---

## Approved message list (`$script:ApprovedWakeMessages`)

The `-RequireConstantMessage` flag requires the injected text to match one of these:

```
"check bridge inbox"
"Watcher says check bridge inbox"
"Codex says check bridge inbox"
"Claude says check bridge inbox"
"User says check bridge inbox"
```

Use `"Watcher says check bridge inbox"` (the production value) so the Codex chat
shows who triggered the wake. Other values are available for debug/testing.

---

## Clipboard handling

Both SendKeys and PostMessage delivery paths temporarily use the Windows
clipboard for atomic paste. The wake script must save the original clipboard
state before setting wake text and restore it in `finally`, even when the
original clipboard was empty. An empty original clipboard should be restored by
clearing the clipboard, not by skipping restore. Clipboard save/set/restore calls
also use short retries because Windows may briefly lock the clipboard.

---

## Latency breakdown (at tuned settings, ~2.5s total)

- ~250ms average poll wait (0.5s interval)
- ~150ms deeplink sleep
- ~200-400ms UIA warmup retries (post-deeplink)
- ~100ms focus (UIA SetFocus primary, Win32 fallback)
- ~220ms SendKeys (Ctrl+A + Del + type + Ctrl+Enter)

Safe fallback values (revert if issues): `FastPathIdleSeconds=1`, `DeeplinkSleepMilliseconds=300`, `IdleThresholdSeconds=5`, `poll_interval_seconds=1`.

---

## watcher-config.json template structure

The Codex session entries in `~/.agent-bridge/watcher-config.json` must include
**all tuned params + all hardening params**. Missing any silently reverts to the
slower/unsafe default.

Canonical template (both `kind: private` and `kind: rendezvous` Codex sessions):

```json
"on_message_command_template": [
  "powershell", "-NoProfile", "-ExecutionPolicy", "Bypass",
  "-File", "<path>/wake_codex.ps1",
  "-RunInnerWake",
  "-IdleThresholdSeconds", "0",
  "-MaxWaitSeconds", "60",
  "-Priority", "urgent",
  "-FastPathIdleSeconds", "0",
  "-DraftStabilitySeconds", "5",
  "-DeeplinkSleepMilliseconds", "150",
  "-ThreadId", "{desktop_thread_id}",
  "-RestoreThreadId", "{restore_thread_id}",
  "-Message", "Watcher says check bridge inbox",
  "-RequireThreadId",
  "-RequireConstantMessage",
  "-VerifyTargetTwice",
  "-VerifyTargetGapMilliseconds", "50",
  "-MaxPreSendRaceMilliseconds", "500",
  "-PostTypingVerify",
  "-ProtectForegroundCodexThread"
]
```

---

## Workflow rule

> **Before editing `wake_codex.ps1` or `watcher-config.json`: read this file.**  
> **After adding a parameter or changing a default: update the table above in the same commit.**

This prevents regressions like the 2026-05-01 incident where Codex rewrote the
command template to add hardening flags but silently dropped `-Message` and all
tuning params, reverting wake latency from 2.5s to ~15s and removing sender labels.
