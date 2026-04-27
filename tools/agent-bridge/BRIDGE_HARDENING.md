# Agent-Bridge Hardening Checklist

Merged from Claude + Codex edge-case analysis sessions (2026-04-26/27).
Organized into four phases: correctness, reliability, polish, known limits.

---

## Phase 1 — Correctness (must-have; breaks things if missing)

### H-01 Reject sends from superseded sessions
**Problem:** Old Claude/Codex window stays open after supersede. User types in it.
Bridge happily sends from a dead session — Codex receives messages from a ghost.
**Fix:** Before every `send_to_peer`, check session registry. If sender's session_id
is not the current `active` session for that agent/project, refuse the send and
surface: *"This session has been superseded. Newer session owns the bridge."*
**Files:** `server.py` (send_to_peer gate), session registry lookup.

### H-02 Control-plane starvation
**Problem:** `mlvapp` / rendezvous channel uses the same "one unread only" transport
constraint as work traffic. A single unread control message blocks all subsequent
HANDSHAKEs and TEARDOWNs until consumed.
**Fix:** Control messages (`HANDSHAKE`, `HANDSHAKE_ACK`, `SESSION_UPDATE`) must be
**replaceable** — a newer control message for the same project overwrites the prior
unread one rather than being rejected. Implement a separate control slot per
rendezvous channel, distinct from the work inbox queue.
**Files:** `server.py` (inbox logic), `agent_bridge.py`.

### H-03 Normalization as shared utility
**Problem:** Claude and Codex each implement `normalize(folder_name)` independently.
Any divergence → different rendezvous strings → silent pairing failure.
**Fix:** Single `normalize_rendezvous(name: str) -> str` function in `agent_bridge.py`
(or a shared `utils.py`). Both clients import it. Never re-implement inline.
Rules: lowercase → spaces to hyphens → keep `[a-z0-9-_]` → strip rest.
**Files:** `agent_bridge.py` or new `utils.py`.

### H-04 Fail loudly on bad project detection
**Problem:** `git rev-parse --show-toplevel` fails (no git repo, permission error,
or nested submodule surprises). Silent fallback to CWD basename could produce
nonsense rendezvous names and pair with the wrong project.
**Fix:** Explicit precedence with loud failure on ambiguity:
1. `.agent-bridge.json` at git root → use `rendezvous` field
2. `git rev-parse --show-toplevel` succeeds → normalize basename
3. Not a git repo → normalize CWD basename + **warn user**
4. Normalization produces empty string (unicode stripping, etc.) → **hard fail**
   with message: "Could not derive project rendezvous. Set it in .agent-bridge.json."
5. Submodule detected (`git rev-parse --show-superproject-working-tree` non-empty)
   → warn user that submodule root is being used, not parent repo root.
**Files:** new `project_identity.py` (or inline in `server.py` init).

### H-05 Session registry corruption recovery
**Problem:** `session.json` partially written (crash mid-write, disk full, manual edit).
Bridge wedges — can't read state, can't pair, mysterious failures.
**Fix:**
- All writes to `session.json` use atomic temp-file replace (write `.session.tmp`,
  rename). Already the pattern in `routing_rules.py` — apply consistently.
- On read failure: rename corrupt file to `session.corrupt.<timestamp>.json`,
  start fresh. Log the rename.
- Never crash on a bad `session.json` — always recover to a known state.
**Files:** `server.py` (session read/write), `agent_bridge.py`.

---

## Phase 2 — Reliability (should-have; degrades gracefully without these)

### H-06 HANDSHAKE retry on boot
**Problem:** MCP server not running, or mlvapp channel temporarily unavailable when
new session sends HANDSHAKE. Pairing fails silently.
**Fix:** Retry HANDSHAKE up to 3 times with exponential backoff (2s, 4s, 8s) before
reporting failure. Surface clear error if all retries fail.
**Files:** client boot sequence.

### H-07 Drain old GUID inbox before switching on supersede
**Problem:** Codex queued a message to old Claude GUID just before Claude's new session
superseded. New session starts with new GUID — old message sits unread forever.
**Fix:** On new session boot, after reading `session.json`, check old GUID inbox once
before generating new GUID and HANDSHAKEing. Surface any unread messages. Then proceed.
**Files:** client boot sequence, `check_inbox` call.

### H-08 Deterministic tie-breaking for racing sessions
**Problem:** Two new Claude sessions start nearly simultaneously (e.g., two windows
opened in quick succession). Both send HANDSHAKE, both get ACK, both try to claim
`active`. Race condition in session registry.
**Fix:** `activate_session` stores `activated_at` timestamp. On conflict (two active
sessions for same agent/project), newest `activated_at` wins. Loser receives
`SESSION_UPDATE: superseded`.
**Files:** `server.py` (`activate_session`), session registry.

### H-09 File monitor survives inbox file deletion/recreation
**Problem:** `tail -n 0 -f inbox-claude.jsonl` — if the file is deleted (e.g., by
a cleanup script) and recreated, `tail -f` may stop tracking the new file on Windows.
**Fix:** Monitor command should use a wrapper that restarts `tail` if the file
disappears and reappears. Or use a polling fallback (`Get-Content -Wait`) on Windows
when inotify-style tail isn't reliable.
**Files:** client monitor setup, watcher.py.

### H-10 Old session auto-halt on supersede (consumer path)
**Problem:** `SESSION_UPDATE: superseded` is correctly queued and delivered by the
bridge. But the old running session doesn't automatically check its inbox and halt —
it needs the file monitor to fire AND the consumer to recognize the supersede signal.
**Fix:** When `check_inbox` returns a `SESSION_UPDATE: superseded` message, the
consuming agent must: stop file monitor, stop sending, surface note to user.
This must be wired in both Claude's and Codex's session consumption path.
**Files:** both agent session-boot/consume paths.

### H-11 Orphaned unread visibility in `bridge status`
**Problem:** Orphaned messages silently accumulate in `orphaned-claude.jsonl` /
`orphaned-codex.jsonl` with no user visibility.
**Fix:** `bridge status` must report:
- current session GUIDs and state
- count of unread messages in current inbox
- count of orphaned messages in quarantine files
- oldest orphaned message timestamp
**Files:** `bridge_status` command handler, `server.py`.

### H-12 Rate limiter tuned for legitimate burst traffic
**Problem:** Phase transitions generate several bridge messages in quick succession.
A tight rate limiter could reject legitimate PHASE_DONE → AUDIT_RESULT → PHASE_APPROVED
sequences.
**Fix:** Rate limit window should be per-sender and wide enough for normal phase work
(e.g., max 10 messages per 60-second window). Test against real phase-transition
scenarios before shipping.
**Files:** `agent_bridge.py` (rate limit implementation).

---

## Phase 3 — Polish (nice-to-have; maintenance and UX)

### H-13 `session.json` bounded growth
**Problem:** Accumulated sessions (ended, superseded) across many projects grow
`session.json` unboundedly.
**Fix:** On every write, prune entries with `status: ended` or `status: superseded`
older than 30 days.
**Files:** `server.py` (session write path).

### H-14 Soft message size cap
**Problem:** No enforced size limit. A 10MB bridge message bloats inbox JSONL files.
**Fix:** Soft cap of 64KB per message, enforced at `send_to_peer`. Messages exceeding
the cap are rejected with: "Message too large. Link to the file instead of embedding."
**Files:** `server.py` (`send_to_peer`).

### H-15 Feedback rule pruning
**Problem:** `routing-rules.json` grows messy as `bridge learn` / `bridge suppress`
accumulates entries over months.
**Fix:** Add `bridge rule prune` command that removes entries not matched in the last
90 days (requires adding `last_matched_at` tracking to rule application). For now,
entries already have `last_updated`; a simple age-based prune is a useful first step.
**Files:** `routing_rules.py`, `bridge_rule_prune` handler.

### H-16 Ambiguous NL bridge feedback
**Problem:** "Send this automatically next time" could infer wrong pattern or wrong
type. Silently storing a bad learned rule pollutes routing.
**Fix:** If type inference confidence is weak (no clear TYPE keyword or known pattern),
record the pattern but leave `suggested_type` unset rather than guessing. Ask user
to confirm: "Learned pattern recorded. Which type should this trigger? (optional)"
**Files:** `bridge_learn` handler, `routing_rules.py`.

### H-17 `.agent-bridge.json` as cross-clone / cross-machine override
**Problem:** Same logical project cloned into differently named folders (`MLV-App`
vs `mlvapp-main`) on two machines → different rendezvous → never pair.
**Fix:** This is the documented purpose of `.agent-bridge.json`. When a cross-machine
or cross-clone setup is needed, commit `.agent-bridge.json` to the repo with an
explicit `rendezvous` field. Both clones read the same committed file.
**Files:** documentation, project setup guide.

### H-18 `bridge status` full health report
**Problem:** No single command shows the complete bridge health picture.
**Fix:** `bridge status` output:
```
project:     mlv-app
rendezvous:  mlv-app (derived from git root)
claude:      84b53694 active  last-read: 2m ago
codex:       2bcb0bb1 active  last-seen: 5m ago
inbox:       0 unread
orphans:     2 messages (oldest: 3 days ago)
rules:       3 learned, 1 suppressed
session.json: healthy
```
**Files:** `bridge_status` command handler.

---

## Phase 4 — Known Limits (document; no immediate fix planned)

### KL-01 Multi-machine not supported
Both agents must share the same `%USERPROFILE%\.agent-bridge\state\` directory.
Cross-machine operation requires a hosted/shared backend (out of scope for v1).
Document this limitation explicitly.

### KL-02 Two parallel same-agent sessions intentionally
By design, a new session supersedes the old one. If you genuinely need two Claude
sessions bridging to the same Codex simultaneously, the current model doesn't
support it. Future: `bridge share` mode. Document as known gap.

### KL-03 Monorepo / multiple projects same root
All packages in a monorepo share one rendezvous. If you need per-package bridging,
use `.agent-bridge.json` overrides per package subdirectory (place the file in the
package root and have agents read it from CWD rather than git root for this case).
Document the workaround.

### KL-04 Concurrent inbox appends without MCP server
All writes must go through the MCP server, which serializes them. Direct file
manipulation outside the server is unsafe. Document: never write to inbox files
directly.

---

## Implementation Order Summary

| Phase | Items | Owner | Blocks |
|---|---|---|---|
| 1 — Correctness | H-01 through H-05 | Codex | Everything else |
| 2 — Reliability | H-06 through H-12 | Codex | Full automation |
| 3 — Polish | H-13 through H-18 | Codex | Maintenance only |
| 4 — Known limits | KL-01 through KL-04 | Claude (docs) | Nothing |

**Codex starts with:** H-01 (reject superseded sends) and H-02 (control-plane
starvation) — highest safety value, smallest scope.

**Claude handles:** KL-01 through KL-04 documentation, H-18 bridge status spec,
audit of each Phase 1 item when Codex completes it.
