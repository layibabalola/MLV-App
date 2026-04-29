# Agent Bridge - Auto-Pair And Wrong-Chat Defense Spec

**Status:** Codex-side wrong-chat defense is implemented through
peer-runtime breadcrumbs, fire-time watcher command-template resolution,
parent-only provenance, subagent retargeting, and trusted-parent drift refusal.
Title-marker Phase A shipped historically, then was intentionally retired on
2026-04-28. Cross-project pairing, symmetric `wake_claude.ps1`, and optional
register fallback remain separate roadmap work.
**Authors:** Claude (proposal); Codex review and Phase A implementation
**Motivation:** eliminate the hardcoded `session_id` and `desktop_thread_id`
in `watcher-config.json` that go stale when either side rotates threads, and
provide defense-in-depth against the wrong-chat-injection failure mode that
caused an observed retry loop on 2026-04-28.

**Review status:** Codex review returned PASS-with-scope-edits on
2026-04-28. Open questions resolved inline in the
[Open Questions section](#open-questions-for-codex). Two scope edits
adopted: (1) Phase A is documented as a fail-closed OS-title heuristic, not
identity proof; (2) `pause_bridge`-gating-watcher is tracked separately
under Out Of Scope, not folded into this spec.

---

## Problem

Today's wake mechanism encodes peer identity statically into
`watcher-config.json`:

```json
{
  "agent": "codex",
  "kind": "rendezvous",
  "session_id": "9a81554a-0160-4810-95de-44611d9955c6",
  "on_message_command": "powershell ... wake_codex.ps1 ... -ThreadId 019dd71b-..."
}
```

`bootstrap_session.py` writes these values from the registered peer hint at
the moment of bootstrap. The next time the peer rotates its session GUID or
the user starts a new desktop chat thread, both values go stale. Three
failure modes follow:

1. **Stale `session_id`:** the rendezvous bucket still works (it's keyed by
   project, not session GUID), so messages still deliver. But the watcher's
   per-session dedupe keys grow stale, which can cause edge-case re-fires.
2. **Stale `desktop_thread_id`:** `wake_codex.ps1` opens
   `codex://threads/<stale-id>` which navigates to a non-existent or
   unfocused thread; `Get-ForegroundCodexWindow` falls back to whichever
   Codex window is foreground; SendKeys lands in the wrong chat.
3. **Stuck loop on stale ThreadId:** wake_codex.ps1 returns a non-zero exit
   code on some failure paths (e.g. `exit 1` on "no Codex window after
   deeplink"); the watcher treats this as retryable; with N queued messages
   the watcher fires up to `N * (1 + WAKE_MAX_RETRIES) * (60 + 30)s` of
   wake activity even when every wake is failing the same way.

The 2026-04-28 incident: 5 messages queued for codex; ThreadId
`019dd71b-...` was stale (Codex's new chat had a different id); 5 wakes
fired sequentially, each landing in the wrong foreground chat or failing
silently; pause_bridge did not stop the watcher's autonomous polling
(separate defect, see Open Questions); user observed ~5 minutes of
PowerShell toast chatter before manual intervention edited
`watcher-config.json` to remove `on_message_command`.

---

## Design Principle

**Pairing data is dynamic; peer identity is discovered at fire time, not
baked into static config.** Each side declares "I am the active peer for
this project; my desktop thread is X" by writing a runtime breadcrumb on
bootstrap. The watcher reads the breadcrumb at the moment it fires
on_message_command, so wake invocations always target the most recently
declared peer.

Defense in depth: a SendKeys-based wake is fundamentally lossy because the
target window can change between read and write. A second mechanism
(title verification) must catch wrong-chat injections at the keystroke
boundary.

---

## Three Layered Mechanisms

### Layer 1 - Breadcrumb-Driven Pairing (core)

Each agent's bootstrap writes a peer-identity breadcrumb to the bridge
root.

**Schema** (`<bridge-root>/peer-<agent>.runtime.json`):

```json
{
  "schema_version": 1,
  "agent": "codex",
  "session_id": "fadda757-5bbe-4a6c-9def-f27a04d118f4",
  "desktop_app": "codex-desktop",
  "desktop_thread_id": "019dd58e-4572-73e3-be9c-2377b1a8dd0d",
  "deeplink_template": "codex://threads/{thread_id}",
  "window_title_pattern": "^Codex.* - .*",
  "written_by_pid": 102756,
  "written_at": "2026-04-29T02:38:32+00:00",
  "bootstrap_command": ["py", "-3", "bootstrap_session.py", "..."]
}
```

**Where it lives:** the bridge root, alongside `bridge-root.json`. The
filename `peer-<agent>.runtime.json` is symmetric with the existing
`<state>/server-pids/server-<pid>.json` runtime breadcrumbs added in
8c69ea20.

**Who writes it:** `bootstrap_session.py` (Claude side) and Codex's
equivalent bootstrap (`tools/agent-bridge/AGENTS.md` side). On bootstrap,
each agent overwrites its own peer breadcrumb. There is no read-modify-write
race because each agent owns one file (`peer-claude.runtime.json` or
`peer-codex.runtime.json`).

**Who reads it:** the watcher, when expanding `on_message_command_template`
into a concrete command at fire time. Also any tool that needs to resolve
the active peer (e.g. a future `bridge_active_peer` query).

**Schema for `watcher-config.json`** changes from:

```json
{
  "session_id": "9a81554a-...",
  "on_message_command": "powershell ... -ThreadId 019dd71b-..."
}
```

to:

```json
{
  "session_id_source": "peer_breadcrumb",
  "on_message_command_template": "powershell ... -ThreadId {desktop_thread_id}"
}
```

Watcher resolves `{desktop_thread_id}` from `peer-codex.runtime.json` at
fire time. If the breadcrumb is missing, watcher logs
`peer_breadcrumb_missing` event, skips the wake, and marks the message
seen with a `wake_skipped_no_peer` failure reason. No retry.

**Bootstrap chain when both apps are running:**

1. Claude bootstrap writes `peer-claude.runtime.json`
2. Codex bootstrap writes `peer-codex.runtime.json`
3. Either side's bootstrap can read the other's breadcrumb to learn the
   peer's session GUID for HANDSHAKE
4. Order-independent: whoever bootstraps later sees the earlier one's
   breadcrumb on disk

### Layer 2 - Reserved For Future UUID-Based Check

> **Update 2026-04-28: title-marker producer removed per user direction.**
> Phase A originally shipped (commit `64fec9b8`) with an OS-title heuristic:
> the wake script read the foreground window title after `SetForegroundWindow`
> and exited 3 if it did not contain the project marker. Operational evidence
> on the maintainer's Windows host showed Codex Desktop window titles do not
> reliably contain any predictable string (often blank from the process API),
> so every wake fired exit 3 and no SendKeys nudges were delivered. User
> directed removal of the title-marker layer because (a) navigation already
> uses `codex://threads/<UUID>` deeplink, which IS GUID-based, and (b) the
> real identity proof belongs in Phase B breadcrumbs. Title heuristic was
> a dead-end intermediate.
>
> The watcher's `WAKE_PERMANENT_EXIT_CODES = {3}` infrastructure is
> **preserved** in `watcher.py` for the future Phase B UUID-based check to
> use. Removing the title-check producer does not require removing the
> watcher's permanent-exit-code handling. Phase B will reuse it.

**Original design (kept here for historical context):** Even with
breadcrumbs, race conditions can exist - user closes the bridge chat
between breadcrumb write and wake fire; user reopens Codex on a different
thread without re-bootstrapping; deeplink fails to navigate due to OS
permissions or app state. The wake script wanted to validate that the
foreground window after `SetForegroundWindow` actually matches the
registered bridge chat before keystroking. The chosen mechanism (substring
match on `MainWindowTitle`) turned out to be too unreliable on Windows
hosts to be useful as a safety net.

**Forward path:** Phase B replaces title-heuristic verification with a
GUID-based check using the peer breadcrumb. The wake script reads
`peer-<agent>.runtime.json` at fire time and verifies the
`desktop_thread_id` matches a value the deeplink navigated to (mechanism
TBD - possibly via the bridge MCP server querying the desktop app's
state, or via filesystem-based correlation if the desktop app writes its
active thread to a known file). Until Phase B lands, the empty-marker
configuration trusts the deeplink navigation 100%.

**`wake_codex.ps1` modification:**

After `SetForegroundWindow`, before `SendKeys`:

```powershell
$nowFg = [Win32Wake]::GetForegroundWindow()
if ($nowFg -ne $codexHwnd) {
    Write-Host "[wake_codex] WARNING: failed to bring Codex to foreground."
    exit 1   # transient, retryable
}

$nowTitle = Get-WindowTitle -hWnd $nowFg
$expectedPattern = $WindowTitlePattern   # passed via param, sourced from breadcrumb
if (-not [string]::IsNullOrEmpty($expectedPattern) -and $nowTitle -notmatch $expectedPattern) {
    Write-Host "[wake_codex] WARNING: foreground window title '$nowTitle' does not match bridge thread pattern '$expectedPattern'. Aborting to avoid wrong-chat injection."
    exit 3   # permanent: wrong chat, do not retry
}
```

**New exit code semantics:**

| Code | Meaning | Watcher action |
|------|---------|----------------|
| 0 | Wake delivered (SendKeys completed) | Mark seen, expect Codex `seen_at` receipt |
| 1 | Transient failure (window not foreground, idle wait expired but injected anyway) | Retry per `WAKE_MAX_RETRIES` |
| 2 | Configuration error (bad ThreadId UUID, no Codex window at all) | Log, mark seen, surface `wake_skipped_config_error` |
| 3 | Wrong chat detected (foreground title does not match bridge pattern) | Log, mark seen, surface `wake_skipped_wrong_chat`. **No retry.** |

The watcher's existing retry logic in `_process_pending_wake_verifications`
must be updated to honor these codes. Currently any non-zero is "retryable";
Layer 2 introduces a "do not retry" class.

**`window_title_pattern`** comes from `peer-codex.runtime.json` and is
substituted into the watcher's command template. Suggested defaults:

- Codex: `^Codex.*`
- Claude Desktop: `^Claude.*` (refine once Claude Desktop title format is confirmed)

Patterns tighten as we learn more about each app's window title format.

### Layer 3 - Optional SendKeys-Based "Register" Fallback

For the case where the user wants to switch to a different bridge chat
without re-running bootstrap (e.g. they accidentally archived the bridge
chat and made a new one):

1. User invokes `tools/agent-bridge/register_as_bridge_peer.ps1` (new
   helper) which fires SendKeys with payload "register as bridge peer for
   project mlv-app" into the foreground Codex window.
2. Codex's prompt instructions include a rule: "if you receive
   'register as bridge peer for project <X>', call
   `mcp__agent-bridge__register_active_peer` with the current
   `desktop_thread_id`."
3. The MCP tool overwrites `peer-codex.runtime.json` with the new
   thread_id.
4. If the synthetic message lands in the wrong chat, the agent there does
   not match the rule (different system prompt or different project
   context) and the message becomes a benign no-op visible to the user.

This is opt-in: not part of normal operation, only a manual recovery path.

**Tradeoff acknowledged:** the visible "register as bridge peer" line in
the wrong chat is less ergonomic than wrong-chat "check bridge inbox"
because it appears unprompted. We accept this for the manual-recovery use
case where the user is actively trying to fix the pairing.

---

## Symmetric Codex -> Claude Direction

Today's architecture is asymmetric:

- Claude -> Codex: watcher fires `wake_codex.ps1` (SendKeys path)
- Codex -> Claude: watcher fires nothing; Claude Code's Monitor
  catches new inbox lines in-process

The asymmetry is intentional. Claude Code is a CLI agent inside
`claude.exe`; once Claude Code is in an active session with Monitor
armed, the Monitor sees new bridge messages immediately. SendKeys is
unnecessary.

**For the cold-start case** (Claude Desktop is open but no Claude Code
session is running), introduce `wake_claude.ps1`:

1. Watcher detects new codex -> claude message in `inbox-claude.jsonl`.
2. Fires `wake_claude.ps1` with payload "check bridge inbox".
3. Script opens Claude Desktop deeplink (TBD - does Claude Desktop expose
   `claude://threads/<id>`? See Open Questions), foregrounds the bridge
   chat, SendKeys "check bridge inbox".
4. Title-verifies against `peer-claude.runtime.json` `window_title_pattern`.
5. User in bridge chat sees the synthetic prompt; this either spawns a
   new Claude Code session or steers an existing one toward
   `check_inbox`.

**Distinguish two roles cleanly:**

- Breadcrumbs answer "who is the active peer right now?"
- SendKeys answers "are they paying attention right now?" (i.e. is the
  agent in-session and able to receive a prompt)

Both directions need both, but Codex direction also needs the asymmetric
Monitor-handles-it case.

---

## Failure Modes Covered

| Failure | Without spec | With spec |
|---------|--------------|-----------|
| Peer rotates desktop thread | Watcher fires SendKeys to stale ThreadId, may inject in wrong chat, retries up to N times | Breadcrumb refreshed on next bootstrap; until then, title-verify catches and exits 3 (no retry) |
| Peer closes desktop chat without graceful shutdown | Same as above | Same as above + breadcrumb stays stale until peer re-bootstraps; Layer 3 manual register if needed |
| User has multiple Codex windows open | Watcher targets first by `Get-CodexWindow`; could be wrong | Title-verify catches if foreground window title doesn't match registered pattern |
| Both apps in cold start, neither bootstrapped | No wake fires; messages accumulate; user has to manually invoke check_inbox | Same; breadcrumb-based pairing requires at least one side to bootstrap |
| Watcher fires repeatedly while peer is asleep | `WAKE_MAX_RETRIES=3` per message but N queued -> `N*3` wakes | Same; circuit breaker (Open Questions) would cap |
| pause_bridge called during a loop | Loop continues (separate defect) | Loop continues (out of scope; track separately) |

---

## Watcher Infrastructure Changes

1. **`watcher-config.json` schema:** drop hardcoded `session_id` and inline
   `on_message_command`; replace with `on_message_command_template` and
   resolved-at-fire-time substitution. The migration adds new optional
   keys; existing inline `on_message_command` continues to work for one
   release as a deprecation buffer.

2. **`run_command_for_session`:** when expanding the template, read the
   peer breadcrumb at the bridge root, substitute placeholders
   (`{desktop_thread_id}`, `{window_title_pattern}`, `{deeplink_uri}`).
   If the breadcrumb is missing or unreadable, log
   `peer_breadcrumb_missing`, mark the message seen with
   `wake_skipped_no_peer` reason, do not retry.

3. **Exit code interpretation:** update
   `_process_pending_wake_verifications` to treat exit code 3
   ("wrong chat") as permanent. Mark the message seen, append a
   `wake_skipped_wrong_chat` audit event with the offending window title,
   skip retries. Add the same treatment for code 2
   ("config error") with `wake_skipped_config_error`.

4. **No change to `WAKE_ACK_GRACE_PERIOD_S` or `WAKE_MAX_RETRIES`.** These
   continue to apply to transient (exit code 1) failures. Per
   `settings_design_gate.md` they remain hardcoded.

5. **Hot-reload semantics:** the watcher already reloads
   `watcher-config.json` on mtime change (lines 683-690). Peer breadcrumbs
   should be read fresh on each fire (no caching), so no hot-reload logic
   is needed for them.

---

## Migration Plan

**Phase A - Title verification — SHIPPED then PARTIALLY REVERTED on
2026-04-28:**
- Initial implementation in commit `64fec9b8` (`Guard Codex wake against
  wrong-chat retries`):
  - `configure_watcher.py` emitted `wake_codex.ps1 -ExpectedTitleMarker <project>`
  - `wake_codex.ps1` verified foreground Codex window title; exited 3 on
    marker mismatch.
  - `watcher.py` treated exit code 3 as permanent: writes
    `wake_skipped_wrong_chat` audit event, marks message seen, suppresses
    retries.
  - Regression coverage; suite at the time: 75 tests pass.
- Operational evidence (2026-04-28): three `wake_skipped_wrong_chat rc=3`
  events at 03:42, 03:43, 03:49 UTC on maintainer's Windows host. Codex
  Desktop `MainWindowTitle` did not contain `mlv-app` (or any predictable
  marker), so every wake fired exit 3 and no SendKeys nudge was delivered
  to a quiet chat. Codex still received messages via its
  `codex_pre_response.ps1` hook (per-turn inbox check), so active
  conversations worked, but the active-wake nudge mechanism was fully
  suppressed.
- User directive: remove title-marker layer; it is a dead-end intermediate
  step. `codex://threads/<UUID>` deeplink already provides the GUID-based
  navigation; real identity proof belongs in Phase B breadcrumbs.
- Revert scope (revert commit pending from Codex):
  1. `wake_codex.ps1` - remove `$ExpectedTitleMarker` param,
     `Test-ExpectedTitleMarker` function, and the post-foreground title
     check.
  2. `configure_watcher.py` - remove `-ExpectedTitleMarker` arg emission.
  3. `watcher.py` - **keep** `WAKE_PERMANENT_EXIT_CODES`, the dict-return
     refactor of `run_command_for_session`, `_permanent_wake_event`, and
     `_mark_permanent_wake_failure`. Add code comment that exit 3 is
     reserved for future Phase B UUID-based check. This way Phase B does
     not have to rebuild the watcher exit-code handling.
  4. Tests - drop the title-mismatch tests; preserve generic exit-3
     watcher infrastructure tests.
- Lesson: window-title heuristics are unreliable on Windows hosts where
  the process API returns blank or non-predictable titles. The smallest
  unit that actually closes wrong-chat injection is Phase B's
  breadcrumb-driven UUID check, not a title heuristic.

**Phase B - Peer breadcrumb writers:**
- `bootstrap_session.py` writes `peer-claude.runtime.json` on every
  bootstrap.
- Codex bootstrap (its equivalent) writes `peer-codex.runtime.json`.
- Both write to the same bridge root resolved by `core.paths`.
- Add tests: breadcrumb format, breadcrumb overwrite on re-bootstrap,
  schema_version evolution.

**Phase C - Watcher template support:**
- Add `on_message_command_template` to watcher-config schema.
- `run_command_for_session` resolves placeholders at fire time.
- Bootstrap rewrites watcher-config to use template form. Old inline
  form continues only as a temporary compatibility path and is now
  coerced to argv or rejected as config error rather than run through a shell.
- Add tests: template resolution, missing breadcrumb path, hot-reload.

**Phase D - Symmetric `wake_claude.ps1`:**
- Deferred until Claude Desktop exposes a verified thread-addressable wake
  surface. Do not ship a best-guess SendKeys-only implementation; BP11 documents
  this as an explicit v1 scope boundary.
- When the surface exists, watcher can fire it on codex -> claude messages with
  tests parallel to `wake_codex.ps1`.

**Phase E (optional) - SendKeys "register" payload + agent-side handler:**
- Add `register_as_bridge_peer.ps1` helper.
- Add prompt-instruction mention in CLAUDE.md / AGENTS.md so each agent
  knows to handle the synthetic message.
- Add MCP tool `register_active_peer` that overwrites the peer breadcrumb
  with current desktop_thread_id (sourced from how? - see Open Questions).

Phases A and B are the highest-value and lowest-risk. Phase C is the bulk
of the refactor. Phase D extends symmetry. Phase E is opt-in recovery.

---

## Configurability Gate (per `settings_design_gate.md`)

**`bridge_auto_pair_enabled = True`** - hardcoded. Fail-visible: if the
breadcrumb is missing, wake is skipped and surfaced as
`wake_skipped_no_peer`. Escape hatch: revert `watcher-config.json` to
inline `session_id` and `on_message_command` (one-release deprecation
buffer keeps that working).

No new user-facing settings. The mechanism is internal infrastructure.

---

## Tests Required

**Unit:**

1. `test_peer_breadcrumb_write_and_read` - bootstrap writes breadcrumb,
   read back matches schema.
2. `test_peer_breadcrumb_overwrites_on_rebootstrap` - second bootstrap
   replaces first.
3. `test_peer_breadcrumb_missing_yields_wake_skipped_no_peer` - watcher
   handles missing file without crashing.
4. `test_watcher_command_template_substitution` - placeholders resolved
   from breadcrumb.
5. `test_watcher_treats_missing_breadcrumb_as_permanent_no_retry` - no retry,
   message marked seen with `wake_skipped_no_peer`.
6. `test_watcher_treats_exit_3_as_permanent` - preserve generic no-retry
   infrastructure for a future UUID-based wrong-chat verifier.
7. `test_watcher_legacy_inline_command_still_works` - one-release
   deprecation buffer.

**Integration:**

8. End-to-end: Claude bootstraps, Codex bootstraps, Claude sends, watcher
   resolves breadcrumb, wake fires, deeplink succeeds, Codex handles the
   message.
9. End-to-end with stale breadcrumb: Codex bootstraps with thread A, user
   switches to thread B without re-bootstrapping; wake fires against stale
   thread metadata and the follow-up UUID-aware verifier path is expected to
   own rejection once implemented.
10. End-to-end Codex -> Claude with `wake_claude.ps1`.

---

## Open Questions for Codex

All six resolved during Codex's SPEC_REVIEW_RESULT (2026-04-28). Resolutions
recorded inline below.

1. **Does Claude Desktop expose a `claude://threads/<id>` deeplink scheme?**
   **RESOLVED 2026-04-28: unverified, do not assume.** Codex did not find a
   shipped `claude://threads/` path in this tree. Phase D must be designed
   deeplink-optional with title/foreground enumeration as the canonical
   mechanism; deeplink only when verified empirically.

2. **Breadcrumb location:** `<bridge-root>/peer-<agent>.runtime.json` or a
   subdirectory? **RESOLVED 2026-04-28: bridge root.** Matches existing
   root-level manifest/debug story (`bridge-root.json`,
   `MOVED_TO.json`); keeps manual diagnosis simple; two files at most.

3. **`register_active_peer` MCP tool — how does an agent source its
   `desktop_thread_id` when called from inside its desktop chat?**
   **RESOLVED 2026-04-28: defer Phase E.** Do not block Phases A-C on it.
   When implemented, the safest first shape is an explicit tool argument
   carrying the candidate thread id, with provenance rules similar to the
   protected parent-thread path. Avoid scraping clipboard or title text in
   the first cut.

4. **`pause_bridge` gating in this spec or tracked separately?**
   **RESOLVED 2026-04-28: separate.** Real defect, contributed to incident
   severity, but orthogonal to stale-thread pairing. Tracked as wake-loop
   suppression / watcher gating, not folded into AUTO_PAIR.

5. **Circuit breaker key — per-(session_id, exit_code) or per-session_id
   with exit code as metadata?** **RESOLVED 2026-04-28: per-session.**
   Per-session key with exit code captured as metadata only.
   Reasoning (Codex): per-(session_id, exit_code) risks alternating exit
   classes still spamming the same dead target; suppress repeated wake
   attempts against the same target session regardless of which failure
   code happened first. Exit-code distribution still logged for diagnosis.

6. **schema_version evolution — align with Phase 13 semantics?**
   **RESOLVED 2026-04-28: yes, aligned.** Accept `>= current_known_version`
   with forward-compatible unknown-field tolerance. Warn on newer schema.
   Fail closed on malformed or older-than-supported content only when the
   watcher cannot safely interpret required fields.

---

## Out Of Scope For This Spec

- **`pause_bridge` gating watcher's `on_message_command`** — separate
  defect (per Codex review 2026-04-28). Real, contributed to the
  2026-04-28 incident severity, but architecturally orthogonal to
  stale-thread pairing. Tracked as wake-loop suppression / watcher
  gating, not folded here. If we ever want this spec to become an
  umbrella wake-hardening spec, it can be promoted in scope, but the
  current decision is to keep them separate.
- **Circuit breaker for repeatedly-failing wake** — separate
  infrastructure spec, acknowledged here as the protective layer that
  makes Phase E optional. Resolved key shape: **per-session, exit code
  as metadata only**. Suppress repeated wake attempts against the same
  target session regardless of which failure code happened first;
  alternating exit classes (e.g. 1 → 3 → 1) against a dead target must
  not bypass the breaker. Exit-code distribution logged for diagnosis.
- Replacement of SendKeys with a non-keystroke IPC (Codex Desktop has no
  documented IPC API; wake-by-keystroke is the only mechanism today)
- Multiple simultaneous Codex Desktop instances (the breadcrumb model
  assumes one active peer per project; multi-instance is a separate
  problem)

---

## Acceptance Criteria

- A1. Bootstrap on either side writes a valid `peer-<agent>.runtime.json`
  matching the schema; tests assert format and overwrite semantics.
- A2. Watcher reads the breadcrumb at fire time and substitutes
  placeholders into `on_message_command_template`.
- A3. Managed Codex watcher config is template-based and no longer emits an
  `ExpectedTitleMarker` argument; the active peer breadcrumb is the source
  of thread-targeting data.
- A4. Missing peer breadcrumb is treated as a permanent no-retry wake skip
  (`wake_skipped_no_peer`). Exit-code-3 no-retry handling remains reserved
  infrastructure for a future UUID-aware wrong-chat verifier.
- A5. Watcher continues to honor legacy inline `on_message_command` only as a
  temporary compatibility path; legacy strings are coerced to argv or rejected
  as config error, and tests assert that behavior.
- A6. Symmetric `wake_claude.ps1` remains deferred; when it ships it should
  use the same template/breadcrumb contract.
- A7. Settings gate: hardcoded enabled, escape hatch via legacy inline
  config documented in README.

---

## Why This Is Worth Doing

The 2026-04-28 incident is concrete evidence that hardcoded thread ids in
config are unsafe. Even if a user only switches threads once, the cost is
~5 minutes of toast/wake spam plus potential SendKeys injections into
unrelated chats. Both consequences are user-visible and erode trust in
the bridge.

Breadcrumb-driven pairing is also a prerequisite for any future feature
that needs to know "which desktop chat is the active peer right now"
(e.g. a status query, a routing decision, an audit trail of chat
switches). Making it the canonical source of truth removes the
hardcoded-config tech debt at the same time as fixing the immediate
defect.

Breadcrumb-driven pairing is still worth doing even after the title-marker
rollback because it removes hardcoded thread ids from static watcher config
and makes the active peer inspectable. Phase B/C are the minimum useful
implementation slice. Phases D
through E can land as follow-ups.

[[handoff:codex]]
