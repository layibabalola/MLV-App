# Agent-Bridge Hardening Plan

Merged from Claude + Codex edge-case analyses (2026-04-26/27).
Target: "boring and dependable" — not theoretically perfect.

---

## Guiding Invariants

Every implementation decision should be checked against these:

1. One project has one rendezvous name.
2. One agent/project has one active bridge-owning session at a time.
3. New session startup automatically claims ownership and supersedes the older sibling.
4. Unread messages are never silently deleted.
5. Same-machine local-file bridge is the supported mode for v1.
6. Protocol decisions come from shared bridge code, not duplicated client logic.

---

## Known Limits (document; do not solve in v1)

- Two parallel Claude sessions on the same project are not supported as co-equals; newest wins.
- Multi-machine use is not supported by the local-file bridge.
- Monorepos share one rendezvous by default unless overridden via `.agent-bridge.json`.
- Submodules resolve to their own git root and therefore their own rendezvous.
- Folder rename means fresh rendezvous; old control-plane traffic may orphan.

---

## Phase 1 — Project Identity and Rendezvous Hardening

*"Make sure both sides are talking about the same project."*
If identity diverges, everything else becomes noise. Do this first.

### H-01 Shared project identity utility

Single implementation in `tools/agent-bridge/project_identity.py` (or inline in
`agent_bridge.py`). Never re-implemented per client.

Precedence (apply in order):
1. `.agent-bridge.json` at git root → use `rendezvous` field
2. `git rev-parse --show-toplevel` succeeds → normalize its basename
3. Not a git repo → normalize CWD basename + **warn user**
4. Normalization produces empty string → use first 8 chars of SHA1(original) + warn
5. Submodule detected → warn that submodule root is used, not parent repo root

Normalize rules:
```
lowercase → spaces to hyphens → keep [a-z0-9-_] → strip rest
```

**Deliverables:**
- `project_identity.py` (or equivalent)
- Tests: normal repo, non-git, unicode name, punctuation-heavy name, monorepo override

### H-02 Normalization as shared utility

`normalize_rendezvous(name: str) -> str` is a single function imported by all clients.
Never re-implemented inline. Any divergence silently breaks pairing.

**Deliverables:** function in shared module, both clients import it.

---

## Phase 2 — Session Ownership and Supersede Correctness

*"Make the open-new-session-without-archiving workflow actually safe."*
Most important behavioral layer for the user's workflow.

### H-03 Always-HANDSHAKE boot with activate_session

Every new session startup must:
1. Derive project identity (Phase 1)
2. Generate fresh GUID
3. Call `activate_session(agent, session_id, project)` in the bridge core
4. Send HANDSHAKE on `<rendezvous>` channel

`activate_session` must:
- Supersede any older same-agent session for this project
- Update session registry atomically (atomic temp-file replace)
- Queue high-priority SESSION_UPDATE: superseded to old session's private GUID
- Return active peer GUID if one exists (for HANDSHAKE routing)
- Store `activated_at` timestamp for tie-breaking

**Deliverables:** startup integration for Claude and Codex clients.

### H-04 Reject sends from superseded sessions

Before every `send_to_peer`, check session registry. If sender's session_id is not
the current `active` session for that agent/project, refuse the send:

> "This session has been superseded. The newer session owns the bridge. Send from there."

**Deliverables:** gate in `server.py` send_to_peer, session registry lookup.

### H-05 Old session auto-halt on supersede

When `check_inbox` returns `SESSION_UPDATE: superseded`, the consumer must:
1. Stop file monitor
2. Stop sending bridge messages
3. Surface note to user: "A newer session has taken over. Bridge closed here."

Must be wired in both Claude's and Codex's consumption path.

**Deliverables:** consumer path wiring in both clients.

### H-06 Drain old GUID inbox before switching

On new session boot, after reading `session.json` for the peer GUID hint:
1. Check old own-GUID inbox once for in-flight messages
2. Surface any unread to the user
3. Then generate new GUID and HANDSHAKE

Prevents messages Codex sent to the old GUID during the gap from being silently stranded.

**Deliverables:** boot sequence addition, single `check_inbox` call pre-HANDSHAKE.

### H-07 Deterministic tie-breaking for racing sessions

Two new Claude sessions start nearly simultaneously. Both send HANDSHAKE. Both try to
claim `active`. `activated_at` timestamp in session registry determines the winner.
Loser receives SESSION_UPDATE: superseded.

**Deliverables:** `activated_at` field in `activate_session`, conflict resolution in registry.

**Tests for Phase 2:**
- Old Claude → new Claude takeover
- Old Codex → new Codex takeover
- Superseded session trying to send (must be rejected)
- Message in flight during supersede
- Two simultaneous HANDSHAKEs (tie-breaking)

---

## Phase 3 — Control-Plane Robustness

*"Make restart, teardown, and control messages harder to lose."*
Do alongside Phase 2 — touches same startup/session-control surface.

### H-08 Replaceable control message slots

Control messages (`HANDSHAKE`, `HANDSHAKE_ACK`, `SESSION_UPDATE`) must not be blocked
by the "one unread" constraint that applies to work traffic.

A newer control message for the same project/slot overwrites the prior unread one
rather than being rejected. Implement a separate replaceable control slot per
rendezvous channel, distinct from the work inbox queue.

**Deliverables:** control slot in `server.py` inbox logic; `agent_bridge.py` routing.

### H-09 TEARDOWN targets private GUID, not rendezvous

TEARDOWN and SESSION_UPDATE: superseded must always go to the peer's **private GUID**,
not to mlvapp/rendezvous. This guarantees ordering: TEARDOWN arrives after any queued
work messages, not interleaved with unrelated project HANDSHAKEs.

**Deliverables:** routing fix in TEARDOWN path.

### H-10 HANDSHAKE retry on boot

If mlvapp channel unavailable or MCP server not running when HANDSHAKE is sent:
retry up to 3 times with exponential backoff (2s, 4s, 8s). Surface clear failure if
all retries fail:

> "Bridge HANDSHAKE failed after 3 attempts. Is the MCP server running?"

**Deliverables:** retry loop in boot sequence.

**Tests for Phase 3:**
- Stale control message replacement (new HANDSHAKE replaces pending old one)
- TEARDOWN ordering (arrives after work messages, not before)
- Retry-then-fail behavior

---

## Phase 4 — File and State Resilience

*"Don't wedge on bad files."*

### H-11 Atomic writes everywhere

All writes to `session.json`, `routing-rules.json`, and inbox JSONL files use atomic
temp-file replace (write to `.tmp`, then rename). Already the pattern in
`routing_rules.py` — apply consistently to all state files.

### H-12 Corrupt session.json recovery

On read failure:
1. Rename corrupt file to `session.corrupt.<timestamp>.json`
2. Log warning: "session.json unreadable; renamed aside, starting fresh"
3. Continue as if session.json is missing (full HANDSHAKE)

Never crash on a bad session.json — always recover to a known state.

### H-13 Malformed JSONL quarantine

JSONL readers must skip malformed lines and move them to a `malformed-<inbox>.jsonl`
sidecar rather than failing the whole read. One bad line must not block the inbox.

### H-14 File monitor survives inbox deletion/recreation

`tail -n 0 -f inbox-claude.jsonl` may stop tracking after delete + recreate on Windows.
Monitor command should detect the file disappearing and restart the tail. Or use
a platform-appropriate watcher (PowerShell FileSystemWatcher on Windows).

### H-15 State-dir errors fail loudly

Missing state directory, permissions error, disk full — fail fast with an actionable
error message, not a silent NOP or cryptic exception.

### H-16 session.json bounded growth

On every write, prune entries with `status: ended` or `status: superseded` older than
30 days. Orphan quarantine files use the same 30-day window.

**Deliverables:** corrupt-state recovery, JSONL quarantine, watcher recreation handling,
session pruning. Tests for all four.

---

## Phase 5 — Message Hygiene and Limits

*"Keep the bridge from bloating or becoming noisy."*

### H-17 Soft message size cap

64 KB per message, enforced at `send_to_peer`. Rejection message:

> "Message too large (Xkb > 64kb). Link to the file instead of embedding raw output."

### H-18 Orphaned unread visibility in bridge status

`bridge status` full health report:
```
project:     mlv-app
rendezvous:  mlv-app (derived from git root: C:\!Layi Wkspc\MLV-App)
claude:      84b53694  active   last-read: 2m ago
codex:       2bcb0bb1  active   last-seen: 5m ago
inbox:       0 unread
orphans:     2 messages (oldest: 3 days ago)
rules:       3 learned, 1 suppressed
session.json: healthy
```

**Deliverables:** size check in send_to_peer, health fields in status output.

---

## Phase 6 — Routing Rule Reader and Application

*"Make auto-bridging behavior actually shaped by learned rules."*
We have the writer (`routing_rules.py`). This phase adds the reader side.

### H-19 Load routing-rules.json on session start

On boot, load `%USERPROFILE%\.agent-bridge\routing-rules.json` and merge with
built-in trigger heuristics. Suppressed rules override learned triggers.

### H-20 Apply rules in send decision

Before deciding whether to auto-bridge a message, check:
1. Does it match a `suppressed_triggers` pattern? → don't send
2. Does it match a `learned_triggers` pattern? → send with suggested type
3. Apply built-in heuristics otherwise

### H-21 Low-confidence NL feedback handling

If type inference from `bridge learn` is weak (no clear TYPE keyword or known pattern),
record the pattern but leave `suggested_type` unset. Optionally ask user:

> "Pattern recorded. Which type should this trigger? (optional — press enter to skip)"

### H-22 Routing rule pruning

Add `bridge rule prune` command. Remove entries not updated in 90 days.
Future: track `last_matched_at` for activity-based pruning.

**Deliverables:** rule reader, merge/apply logic, low-confidence path, prune command.
Tests: learned triggers, suppress wins, dedup, ambiguous feedback.

---

## Phase 7 — Watcher-Driven Wakeup

*"Finally kill token-drip polling."*
Biggest UX and cost win, but depends on client capabilities we don't fully control.

### H-23 Proven wake trigger per client

Codex side: investigate `on_message_command` hook options:
- Codex Desktop CLI "run now"
- Local API / port
- File-drop trigger / named pipe
- Narrow UI automation fallback if none exist

Claude side: file monitor (`tail -n 0 -f inbox-claude.jsonl`) is already live.
Remaining gap: auto-recognizing bridge messages from Monitor notifications and
responding without manual user prompt.

### H-24 Disable empty polling once wakeup is real

Once both clients have a real trigger path:
- Remove all ScheduleWakeup-based polling
- Watcher is primary wake signal; no fallback polling
- Document: degraded mode (no watcher) = manual `bridge check` only

**Deliverables:** proven trigger for each client, rollout docs, degraded-mode docs.

---

## Implementation Order

Phases 1–3 first (touch the same startup/session-control surface, highest real-world impact):

```
Phase 1: Identity  →  Phase 2: Ownership  →  Phase 3: Control-plane
Phase 4: File resilience
Phase 5: Hygiene
Phase 6: Routing rule reader
Phase 7: Watcher wakeup
```

Within Phases 1–3, start with **H-01/H-02** (shared identity utility) then **H-03/H-04**
(activate_session + reject superseded sends) — these unblock everything else.

---

## Definition of Done (v1 hardening complete)

- [ ] New session automatically takes over without user intervention
- [ ] Old same-agent session is explicitly superseded and halts
- [ ] Stale session cannot keep sending (send rejected at bridge)
- [ ] Rendezvous derived consistently from canonical git root on both clients
- [ ] Bad state files don't wedge the bridge (corrupt recovery works)
- [ ] Unread messages are never silently lost (quarantine + visibility)
- [ ] `bridge status` explains what is active, stale, orphaned, or superseded

---

## Owner Summary

| Phase | Owner | Status |
|---|---|---|
| 1 — Identity | Codex implements; Claude audits | Pending |
| 2 — Ownership | Codex implements; Claude audits | Pending |
| 3 — Control-plane | Codex implements; Claude audits | Pending |
| 4 — File resilience | Codex implements; Claude audits | Pending |
| 5 — Hygiene | Codex implements; Claude audits | Pending |
| 6 — Routing rules | Codex implements; Claude audits | Pending |
| 7 — Watcher wakeup | Both; Codex investigates trigger | Pending |
| Known limits | Claude documents | Pending |
