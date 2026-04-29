# Audit Profile - Phase A Title-Marker Revert

**Trigger:** ACTION_REQUEST `8efd4010` (2026-04-28) directs Codex to revert
the title-check producer pieces of commit `64fec9b8` while preserving
the watcher exit-3 infrastructure for future Phase B reuse.

**Audit timing:** when Codex sends `IMPLEMENTATION_UPDATE` with a commit
SHA for the revert.

---

## Pre-audit setup

```bash
cd tools/agent-bridge
git log --oneline -5  # confirm new SHA on top of 64fec9b8
git show <new-sha> --stat
```

Expected scope: `wake_codex.ps1`, `configure_watcher.py`,
`test_phase0_contract.py`, `test_agent_bridge.py`. NOT `watcher.py`
(infrastructure preserved).

---

## Required removals

### `wake_codex.ps1`

- [ ] `$ExpectedTitleMarker = ""` param REMOVED from `param(...)` block
- [ ] `Test-ExpectedTitleMarker` function REMOVED
- [ ] Title-check call site REMOVED (the lines that read window title
  after `SetForegroundWindow` and exit 3 on mismatch)
- [ ] `exit 1` on `SetForegroundWindow` failure RETAINED (not part of
  Phase A; pre-existing)

### `configure_watcher.py`

- [ ] `"-ExpectedTitleMarker", project_name,` line REMOVED from the
  wake_codex.ps1 args list (the +1 line in 64fec9b8 diff)
- [ ] Other args (`-IdleThresholdSeconds`, `-MaxWaitSeconds`, `-ThreadId`)
  RETAINED unchanged

### Tests

- [ ] `test_07c_wake_exit_3_marks_seen_without_retry` SHOULD STAY but its
  setup MUST stop using `-ExpectedTitleMarker` to produce exit 3.
  Acceptable substitutes: a stub script that always returns 3, or a
  comment that this test exercises generic exit-3 infrastructure for
  future Phase B. The test's PURPOSE (watcher treats exit-3 as
  permanent) is independent of how exit-3 is produced.
- [ ] `test_configure_watcher_replaces_same_agent_entries` SHOULD be
  updated: assert that emitted `on_message_command` does NOT contain
  `-ExpectedTitleMarker`.
- [ ] Any other test that explicitly tested title-marker behavior SHOULD
  be removed or updated.

---

## Required preservation (regression risk if removed)

### `watcher.py`

- [ ] `WAKE_PERMANENT_EXIT_CODES = {3}` STAYS
- [ ] `_permanent_wake_event` STAYS
- [ ] `_mark_permanent_wake_failure` STAYS
- [ ] `run_command_for_session` dict-return refactor STAYS (not bool)
- [ ] Both call sites in `_process_pending_wake_verifications` and
  `process_session_once` STAY

These are needed by future Phase B UUID-based check. Removing them would
make Phase B harder.

A code comment SHOULD be added near `WAKE_PERMANENT_EXIT_CODES` saying
something like:
> "Currently no producer; reserved for future Phase B UUID-based check
> in wake script."

---

## Required test pass

```bash
py -3 -m unittest test_agent_bridge test_phase0_contract
```

Expected:
- All tests pass
- Test count should be 75 minus removed tests (probably 73-74), unless
  test_07c was repurposed (which keeps it at 75)
- No new failures elsewhere

If pass count drops below 73, ask Codex to explain.

---

## AUDIT_RESULT template

```
TYPE: AUDIT_RESULT
STATUS: pass | fail | pass-with-followup
SUMMARY: <SHA> Phase A title-marker revert per ACTION_REQUEST 8efd4010
ACTION_REQUESTED: none
NONCE: audit-title-revert-<sha-short>
SCOPE: project-only

Reviewed <sha> (`<commit message>`).

Required removals:
- wake_codex.ps1 ExpectedTitleMarker + Test-ExpectedTitleMarker:
  CHECK | MISSING
- configure_watcher.py -ExpectedTitleMarker arg: CHECK | MISSING
- Test updates: CHECK | MISSING

Required preservation:
- watcher.py WAKE_PERMANENT_EXIT_CODES + dict-return: CHECK | MISSING
- _mark_permanent_wake_failure: CHECK | MISSING

Tests at HEAD: <N> pass.

[Push back any deviations from spec; otherwise PASS]

[[handoff:codex]]
```

---

## Coordination

If Codex's revert deviates from this profile (e.g., they remove watcher.py
infrastructure too, or change behavior in a different way than spec'd),
push back via AUDIT_RESULT with concrete observation. Don't let an over-
revert ship by silence.
