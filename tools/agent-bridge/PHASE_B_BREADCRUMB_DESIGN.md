# Phase B - Peer Breadcrumb Detailed Design

**Status:** Partially implemented. Bootstrap breadcrumb writing, watcher
fire-time template resolution, and managed argv-template watcher config are
landed in the working tree; UUID-based foreground verification remains open.
**Authors:** Claude (proposal); Codex implementation in progress
**Parent spec:** `AUTO_PAIR_SPEC.md`, Migration Plan Phase B
**Motivation:** replace stale-config dependency with breadcrumb-driven peer
identity discovery; provide UUID-based identity proof to plug into the
watcher's exit-3 infrastructure left over from Phase A revert.

---

## Goal

Each agent's bootstrap writes a peer-identity breadcrumb to the bridge root.
The watcher reads the breadcrumb at fire time to resolve the active peer's
desktop_thread_id and, in a future stronger version, may verify it against the
foreground after deeplink navigation. Breadcrumb is the source of truth for "who is the active peer
right now"; watcher-config drops hardcoded session_ids and ThreadIds.

---

## Schema

**Path:** `<bridge-root>/peer-<agent>.runtime.json`

**Two files at most:** `peer-claude.runtime.json` and
`peer-codex.runtime.json`. Each agent owns its own file (no
read-modify-write race).

**Schema v1:**

```json
{
  "schema_version": 1,
  "agent": "codex",
  "session_id": "fadda757-5bbe-4a6c-9def-f27a04d118f4",
  "desktop_app": "codex-desktop",
  "desktop_thread_id": "019dd58e-4572-73e3-be9c-2377b1a8dd0d",
  "deeplink_template": "codex://threads/{thread_id}",
  "written_by_pid": 102756,
  "written_at": "2026-04-29T02:38:32+00:00",
  "bootstrap_command": ["py", "-3", "bootstrap_session.py", "..."],
  "bridge_root": "C:\\Users\\obabalola\\.agent-bridge",
  "manifest_root_id": "4d3fe5ae-d20e-4a75-a741-63eaf5a0ef68"
}
```

**Field semantics:**

| Field | Required | Notes |
|---|---|---|
| schema_version | yes | int; watcher accepts >= current_known_version |
| agent | yes | "claude" or "codex" |
| session_id | yes | UUID; current active session GUID |
| desktop_app | yes | "claude-desktop" or "codex-desktop"; could expand |
| desktop_thread_id | conditional | UUID; required when known, omitted when not (Claude Code in terminal has no thread id) |
| deeplink_template | conditional | required when desktop_thread_id is present; uses `{thread_id}` placeholder |
| written_by_pid | yes | int; for staleness detection |
| written_at | yes | ISO8601 UTC; for staleness detection |
| bootstrap_command | optional | argv that wrote this breadcrumb; useful for diagnosis |
| bridge_root | yes | string; absolute path to bridge root used by this session |
| manifest_root_id | optional | matches `bridge-root.json` `root_id`; cross-validates breadcrumb belongs to this root |

---

## Writers

**Claude side:** `tools/agent-bridge/bootstrap_session.py` writes
`peer-claude.runtime.json` on every bootstrap, after activation succeeds and
before HANDSHAKE is queued. Atomic write: write to `.tmp`, fsync, rename.
Claude's `desktop_thread_id` is NOT known from inside Claude Code (no
desktop API exposed to the in-terminal CLI agent). For Claude Code, write
the breadcrumb without `desktop_thread_id` and without `deeplink_template`.
For Claude Desktop separately invoking bootstrap (a different invocation
path, not in scope here), include the thread id.

**Codex side:** Codex's bootstrap (mirror of `bootstrap_session.py` for
codex agent) writes `peer-codex.runtime.json` similarly. Codex Desktop
supplies the thread id via `CODEX_THREAD_ID` env var or
`PARENT_THREAD_ID_KEY` config (already used by `configure_watcher.py`).

**Atomic write pattern (both sides):**
1. Build the breadcrumb dict
2. Write to `<bridge-root>/peer-<agent>.runtime.json.tmp`
3. fsync the file handle
4. `os.replace` to final path (atomic on Windows + POSIX)
5. Old breadcrumb is overwritten in one step; no torn-read window

**Cleanup:** on bootstrap supersession (older session is superseded), the
older session's breadcrumb gets overwritten by the new bootstrap. No
explicit cleanup needed for normal flow. On graceful shutdown (out of
scope for Phase B), old session can delete its own breadcrumb to signal
"I'm gone, no active peer" — but this is opportunistic, not required.

---

## Readers

**Watcher (`watcher.py`):** at `run_command_for_session` time, before
expanding `on_message_command_template`:

1. Read `<bridge-root>/peer-<peer-agent>.runtime.json` (e.g. for codex
   sessions, read `peer-codex.runtime.json`).
2. Validate schema_version >= 1; reject if newer than the watcher's
   `MAX_BREADCRUMB_SCHEMA = 1` and the breadcrumb adds required fields the
   watcher doesn't understand. If newer with only optional additions,
   accept and warn.
3. Validate `bridge_root` matches the watcher's own `bridge_root`. If not,
   the breadcrumb belongs to a different bridge instance; refuse to use it
   (likely a misconfigured cross-bridge poll).
4. Substitute `{desktop_thread_id}` (and other placeholders) into
   `on_message_command_template`.
5. Pass the resolved desktop_thread_id to `wake_codex.ps1` (or equivalent)
   as the ordinary `-ThreadId` wake target.

**Wake script (`wake_codex.ps1`, post-Phase-B):**

1. Receive `-ThreadId <UUID>` from watcher.
2. Open `codex://threads/<UUID>` deeplink as today.
3. After `SetForegroundWindow` succeeds, validate the foreground window
   actually corresponds to that thread. **How?** Two candidate mechanisms:
   - **A: Read the URL bar** — Codex Desktop's URL bar shows
     `codex://threads/<UUID>`. We can probably read it via UI automation
     (UIA accessibility tree). Risky: depends on app internals.
   - **B: Filesystem correlation** — Codex Desktop writes its current
     thread id to a known file when active (e.g.
     `~/AppData/Local/Codex/active-thread.json`). Wake script reads,
     validates UUID match, exits 3 on mismatch. Requires Codex Desktop to
     write that file; not in their codebase today.
   - **C: Just trust the deeplink** (current empty-marker mode) — accept
     that navigation either succeeded or didn't; exit 1 on
     SetForegroundWindow failure but don't re-verify after.
4. **Recommended Phase B v1: option C with the watcher's exit-3
   infrastructure preserved for option A or B in v2.** That is, Phase B
   ships the breadcrumb writers + watcher template-resolution + ordinary
   `-ThreadId` plumbing, but the wake script itself doesn't
   actually verify post-foreground (yet). Verification is plumbed in
   later when Codex Desktop or UIA-based reading is available.

This is the "smallest unit that closes the wrong-chat loop" — by NOT relying
on the title heuristic, we don't fail-closed on this host. By having
breadcrumb-driven config, we don't accumulate stale ThreadIds in
watcher-config.

---

## Watcher-config schema changes

**Current (post-Phase-A-revert):**

```json
{
  "agent": "codex",
  "session_id": "74e288cf-...",
  "kind": "private",
  "on_message_command_template": ["powershell", "...", "-ThreadId", "{desktop_thread_id}"]
}
```

**Phase B target:**

```json
{
  "agent": "codex",
  "kind": "private",
  "on_message_command_template": ["powershell", "...", "-ThreadId", "{desktop_thread_id}"]
}
```

`on_message_command_template` uses placeholders that the watcher resolves
from breadcrumb. Placeholders: `{desktop_thread_id}`, `{session_id}`,
`{deeplink_template}` (if needed in future), `{agent}`.

**Backward compat:** existing `on_message_command` (literal string) still
loads as a compatibility path, but it is now coerced to argv or rejected as a
config error instead of running through a shell. Bootstrap rewrites
watcher-config to template form.

---

## Edge cases

1. **Breadcrumb missing.** Watcher logs `peer_breadcrumb_missing` audit
   event; skips wake; marks message seen with reason
   `wake_skipped_no_peer`. No retry. User sees the message in inbox via
   manual check.
2. **Breadcrumb stale (peer process died but file remains).** Watcher
   doesn't detect this directly (can't verify written_by_pid is alive
   without extra OS calls per fire). Acceptable: stale breadcrumb +
   deeplink failure → exit 1 → standard retry up to MAX_RETRIES → permanent
   `wake_skipped_no_peer` after exhaustion. New peer bootstrap overwrites
   the breadcrumb and self-corrects.
3. **Two peers bootstrap simultaneously.** Each agent owns one file. No
   cross-write. Last-write-wins on each file independently. Bridge's
   active-session registry handles ownership.
4. **schema_version evolution.** Watcher accepts >= current_known. On a
   newer schema version, log warning, attempt to use known fields, ignore
   unknown fields. Per Phase 13 manifest semantics.
5. **Bridge root mismatch.** Breadcrumb's `bridge_root` differs from
   watcher's. Watcher refuses to use it (treats as missing). Ensures
   breadcrumbs from a different bridge instance can't pollute.
6. **Desktop thread id missing for Claude Code.** Watcher accepts
   breadcrumb without `desktop_thread_id`. Wake for codex-side won't fire
   (claude entries don't have `on_message_command_template`); for
   codex→claude direction, use cold-start fallback (no SendKeys; Monitor
   in claude code handles).
7. **Bootstrap supersession during a wake.** Old breadcrumb is replaced
   atomically; in-flight wake holds the old desktop_thread_id; SendKeys
   may land in old thread. Acceptable race because the old thread is
   typically still owned by the same user; the synthetic message is just
   a "check bridge inbox" prompt that any active Codex chat for that
   project would handle. Worst case: user sees the prompt in two chats.
8. **Claude Desktop deeplink unconfirmed (Open Question 1, RESOLVED).**
   Phase D fallback: title/foreground enumeration if no `claude://` URI
   handler. Not blocking Phase B.

---

## Tests required

**Unit:**

1. `test_peer_breadcrumb_write_atomic` — write under /tmp, verify file
   contents match schema, verify no torn-write window via concurrent
   reader stress.
2. `test_peer_breadcrumb_overwrites_on_rebootstrap` — write breadcrumb v1,
   re-bootstrap with different session_id, verify file now reflects v2.
3. `test_peer_breadcrumb_missing_yields_wake_skipped_no_peer` — watcher
   without breadcrumb on disk → emits audit event, marks seen, no retry.
4. `test_watcher_resolves_template_from_breadcrumb` — breadcrumb has
   thread_id X, template has `{desktop_thread_id}`, watcher fires
   command with X substituted.
5. `test_breadcrumb_schema_version_newer_warned_not_rejected` — file has
   `schema_version: 2` with optional new field; watcher logs warning,
   uses known fields.
6. `test_breadcrumb_bridge_root_mismatch_refused` — breadcrumb's
   `bridge_root` differs from watcher's; treated as missing.
7. `test_legacy_inline_command_still_works` — watcher honors old-form
   `on_message_command` only as a temporary compatibility path and coerces it
   to argv.

**Integration:**

8. End-to-end pair: Claude bootstraps → writes peer-claude breadcrumb;
   Codex bootstraps → writes peer-codex breadcrumb; Claude sends; watcher
   resolves codex breadcrumb's thread_id; wake fires with correct UUID;
   verify SendKeys happened in correct chat.
9. Stale-breadcrumb recovery: old peer crashed without re-bootstrap;
   breadcrumb is stale; new peer bootstraps; new breadcrumb overwrites;
   subsequent wake uses new thread_id.
10. Two bootstraps in <1s (rapid restart): file handle race; assert no
    corrupted breadcrumb on disk; subsequent reader sees one of the two
    cleanly.

---

## Acceptance criteria (Phase B)

- B1. `peer-<agent>.runtime.json` schema implemented; round-trip tests
  green.
- B2. `bootstrap_session.py` (Claude) + Codex bootstrap (mirror) write
  breadcrumbs atomically; tests assert.
- B3. Watcher resolves `on_message_command_template` from breadcrumb at
  fire time; `peer_breadcrumb_missing` audit event on missing file.
- B4. `wake_codex.ps1` accepts the breadcrumb-resolved `-ThreadId <UUID>` and
  uses it for direct thread navigation. Stronger post-foreground verification
  remains deferred to a future Phase B.2.
- B5. Watcher continues to honor legacy inline form only as a temporary
  compatibility path; it is coerced to argv or rejected as config error, and
  tests assert that behavior.
- B6. Two-side end-to-end test passes.

---

## Coordination model

Codex implements; Claude reviews. Phase B is "highest-value next
implementation" per AUTO_PAIR_SPEC and tonight's evidence (no nudge =
host-specific Phase A failure mode).

Audit profile: `tools/agent-bridge/audit-profiles/phase-b-breadcrumbs.md`.

[[handoff:codex]]
