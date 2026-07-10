---
name: session-handoff
description: |
  Gracefully hand off a bloated / freezing / context-pressured session: update
  durable memory, refresh the cross-account resume anchor
  (.claude-state/SESSION-HANDOFF.md), send Codex a STATUS_UPDATE over the agent
  bridge, cancel this session's automations (after confirming), and emit a
  copy-paste resume prompt for a fresh session. Knows MLV-App's real handoff
  surfaces (agent bridge, dual-lane ledgers, closeout, project-memory). This is
  the only session-handoff skill (the generic global copy was removed 2026-06-30,
  Layi's call, so the rich version always runs here with no name-collision risk).

  Trigger this skill BOTH when the user explicitly invokes /session-handoff
  (or /handoff) AND when the user says any of the following in natural language:
  - "Session Bloated"
  - "session is bloated"
  - "the session is bloated and freezing"
  - "session is freezing" / "session is lagging" / "this session is slow"
  - "hand off this session" / "hand off the session" / "let's hand off"
  - "wrap up and give me a resume prompt"
  - "give me a prompt to paste into a new session"
  - "save and hand off" / "save state and start fresh"
  - "prep a new session" / "prep a fresh session" / "I need to restart the session"
  - "cancel the automations here and resume them in a new session"
  - "update memory and handoff so we can resume elsewhere"

  This is a GRACEFUL SHUTDOWN, not a closeout/merge. Do NOT finalize or merge
  in-flight work to master just to hand off — preserve it on the feature branch
  and record what is uncommitted.
---

# /session-handoff — graceful session handoff under context pressure

The current session is bloated / freezing. Your job is to leave behind a clean,
complete handoff so a **fresh session with zero chat history** can resume
immediately, and to stop this session's automations so nothing keeps firing into
a dead session. The user's three asks map 1:1 to the phases below:

1. **Update durable memory and handoff documents**
2. **Cancel automations here, restart them there**
3. **Emit a paste-ready resume prompt**

Read this whole file first, then execute the phases **in order**. Keep your own
output terse — the session is already heavy. The deliverable is the resume
prompt at the end, not narration.

---

## Phase 0 — Triage (fast, read-only)

Gather just enough state to write an accurate handoff. Do NOT re-derive anything
already known this session.

- `git status -s` + `git branch --show-current` + `git log --oneline -6` — branch,
  HEAD, dirty paths. Classify dirty paths as **owned** (yours this session) vs
  **foreign** (pre-existing / other lanes — never touch these).
- Open todos / in-flight task: what was being worked on, what's the next concrete
  step, what's blocking.
- Enumerate live automations (Phase 2 cancels them):
  - `TaskList` → background Tasks, especially the **bridge Monitor**
    (`bridge_monitor_poll.py`) and any `/loop`, background Bash, or heartbeat Monitors.
  - `CronList` → session crons.
  - `mcp__…__list_triggers` → scheduled triggers / `send_later` wakeups.
  - `mcp__scheduled-tasks__list_scheduled_tasks` → cloud routines (leave cloud
    routines alone unless the user owns them for THIS work; note them, don't cancel).
  - Any `ScheduleWakeup` you set this session.
- Bridge state: your active session GUID, any unread inbox, any queued/unsent
  messages, open dual-lane ledger items.

Print a 4-6 line triage summary (branch/HEAD, owned-dirty count, next step, list
of automations found). Then proceed.

---

## Phase 1 — Update durable memory + handoff documents

### 1a. Durable memory (only what's non-obvious and not in git)
Per the project Memory Policy (CLAUDE.md): full CONTENT goes to
[`.claude-state/project-memory/<slug>.md`](../../../.claude-state/project-memory/),
indexed in its [`README.md`](../../../.claude-state/project-memory/README.md);
the machine store gets only a slim pointer.

- For each genuinely new finding/decision from this session: create or UPDATE the
  matching `.claude-state/project-memory/<slug>.md` (prefer updating an existing
  file over a near-duplicate), then add/refresh its one-line README pointer.
- Convert relative dates to absolute. Do NOT save anything the repo/git already
  records, or anything that only mattered to this conversation.
- If nothing durable is new, say so and skip — do not manufacture memories.

### 1b. Cross-account resume anchor — `.claude-state/SESSION-HANDOFF.md`
This file already exists and is the canonical cross-account anchor. **Update it
in place; do not clobber it.**
- Bump the top `**Written:**` line to now (Houston/Central + a UTC stamp) with a
  one-line reason (e.g. "prior session bloated/froze").
- Refresh the `⏩ RESUME HERE — CURRENT STATE` section to current truth: branch +
  HEAD + commit chain, priority, what just landed, what's next, blockers, and the
  pending decisions awaiting the user. Keep the older body below as history (if a
  section is now stale, move it down under a historical heading rather than
  deleting it).
- Make sure the Monitor restart command at the bottom of that section is current
  (it is the single source of truth the new session copies).

### 1c. Notify the peer (Codex) over the agent bridge
Per CLAUDE.md "Gap 2 — Proactive STATUS_UPDATE": before the context wall, send
Codex a `STATUS_UPDATE` containing your active session GUID, a summary of open
ledger items, any unsent/queued messages, and the note **"Claude context
approaching limits — bootstrap next session to resume."** Then record the
STATUS_UPDATE as sent in the dual-lane ledger
(`.claude-state/coordination/dual-lane/claude.md`) so it isn't double-sent.
If the bridge send fails, write the same note into the ledger anyway and flag it
in the resume prompt as "Codex not notified — re-handshake on resume."

---

## Phase 2 — Cancel this session's automations (confirm once before destructive)

The new session re-arms its own automations; the old ones must stop so they don't
fire into a dead session or fight the new owner.

1. **List, then confirm.** Present the exact automations found in Phase 0 that you
   intend to stop (Monitor task id(s), loops, crons, wakeups, triggers). Ask the
   user once: *"Cancel these N automations here? (the resume prompt restarts them
   in the new session)."* Wait for yes before stopping anything.
2. On confirmation, stop them:
   - `TaskStop` each background Task / Monitor / loop by id.
   - `CronDelete` each session cron by id.
   - `delete_trigger` / `update_trigger enabled=false` for triggers and
     `send_later` wakeups you own.
   - Do NOT cancel cloud routines or triggers the user owns for unrelated work —
     list them as "left running" in the resume prompt.
3. The bridge Monitor specifically: stopping it here is correct — the new
   session's `SessionStart` hook re-bootstraps the bridge and you restart the
   Monitor there with the new GUID. Note in the resume prompt that until the new
   Monitor is live, Claude is deaf to Codex.
4. **Preserve in-flight work, do NOT finalize/merge.** If there is OWNED dirty
   work, checkpoint it onto the current feature branch so nothing is lost
   (a plain commit on the branch, or the broker `checkpoint-owned-dirty` path) and
   record the commit sha in the handoff. Leave FOREIGN dirty paths untouched and
   list them in the handoff as "foreign WIP — do not stash." This is a handoff,
   not a closeout — do not run finalize/merge-to-master here.

If the user declines the cancellation, skip stopping them but STILL emit the
resume prompt, and note in it that the old session's automations are still live
(the new session's bootstrap will supersede the bridge Monitor anyway).

---

## Phase 3 — Emit the paste-ready resume prompt

Output a single fenced code block the user can copy whole into a new session.
Fill every placeholder from real state — no `<…>` left behind. Keep it tight but
complete; the new session has NO chat history, so anything not written here or in
the anchor/ledgers/memory is lost.

```text
Resume MLV-App work — fresh session, no prior chat history.

READ FIRST (in order):
1. .claude-state/SESSION-HANDOFF.md  ← the ⏩ RESUME HERE section is current truth
2. .claude-state/coordination/dual-lane/claude.md + codex.md  ← every step is a SEQ entry
3. .claude-state/project-memory/README.md  ← then the specific notes it points to:
   - <relevant memory slugs touched this handoff>

OBJECTIVE / PRIORITY:
<one or two lines — the current Layi priority, e.g. DNG export + CUDA/GPU fix-harden-polish>

GIT STATE:
- Branch: <branch>  HEAD: <sha>  (<pushed? local/unpushed>)
- Commit chain: <a -> b -> c -> HEAD with one-line each>
- Owned WIP checkpointed at: <sha or "none — tree clean">
- Foreign dirty (DO NOT touch/stash): <paths or "none">

LAST COMPLETED:
<the last thing that actually landed + how it was validated>

NEXT STEPS (ordered):
1. <concrete next action>
2. <…>

BLOCKERS / OPEN DECISIONS FOR LAYI:
- <blocker or pending product decision>

RESTART AUTOMATIONS:
- The SessionStart hook auto-bootstraps the agent bridge (drains messages,
  handshakes Codex, gives you a new session GUID). Surface any drained messages first.
- Then restart the bridge Monitor with the NEW GUID + state-dir from the bootstrap output:
  Monitor(persistent=True, command="<python> -u tools/agent-bridge/bridge_monitor_poll.py --state-dir <bridge-state-dir> --agent claude --session-id <new-guid> --project mlv-app --poll-interval-seconds 2")
- Re-restart these too (were cancelled at handoff): <any /loop, dual-lane heartbeat
  Monitor, scheduled wakeups — with their exact commands, or "none">
- Confirm the Monitor is active before saying "waiting for Codex."
- Sync the dual-lane cursor (LAST_READ_CODEX_SEQ) before continuing.

STANDING DIRECTIVES (Layi): Houston-time chat timestamps; 4K widgets w/ Copy/Download
buttons; images under project root; NEVER `git add -A` (use lane-commit/partial-stage);
don't touch foreign dirty WIP; 7-Zip Ultra .7z for packages; don't squeeze the VM CPU.
```

After printing it, stop. Do not start new work in this session — it's being
retired. If you cancelled automations, confirm in one line that they're stopped.

---

## Notes / guardrails
- **Order matters**: memory + anchor first (so the truth is durable), then notify
  Codex, then cancel automations, then emit the prompt. If you cancel the Monitor
  before writing the anchor and the session dies, the handoff is lost.
- **Never destroy work to hand off.** Owned dirty → checkpoint on the branch.
  Foreign dirty → leave it, list it.
- **Idempotent**: re-running this skill should re-refresh the anchor and re-emit
  the prompt without duplicating memory files or re-sending the STATUS_UPDATE if
  the ledger already records one this session.
- If the bridge/closeout surfaces named here have moved, degrade gracefully to the
  generic behavior: write/refresh a handoff doc, cancel discoverable automations,
  emit a resume prompt.
