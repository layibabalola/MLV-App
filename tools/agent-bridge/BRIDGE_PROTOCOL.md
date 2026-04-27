# Agent-Bridge Communication Protocol

Version: 1.5
Transport: agent-bridge MCP server
Applies to: any two agents sharing an agent-bridge instance

## Purpose

Define when agents send bridge messages, how messages are routed, and when
they should stay local.

The bridge replicates the user's manual copy/paste workflow with less
friction. It carries the full relevant response, review, report, or question
that the user would otherwise paste into the other agent.

## The One Rule

> If the user would normally copy this response to the other agent, send it.
> If the other agent does not need to act or know, keep it local.

Applied to natural language: translate user phrasing into bridge events
without waiting for the user to relay manually.

```text
"go ahead"        -> PHASE_APPROVED or ACTION_REQUEST
"looks good"      -> AUDIT_RESULT pass (if reviewing)
"3B is closed"    -> PHASE_PASSED + PHASE_APPROVED for next phase
"tell Codex..."   -> ACTION_REQUEST, verbatim payload
"don't use toast" -> USER_PREFERENCE
```

## Agents And Ownership

| Agent | Owns | Handles locally |
|---|---|---|
| Codex | Code, tests, git, builds, execution | Implementation decisions, local refactors, test retries |
| Claude | User conversation, memory, review/audit | Scope reasoning, memory updates, question answering |

## Runtime Wake Model

The bridge inbox is durable and symmetric: either agent can send to either peer.

Message consumption is not necessarily symmetric. Each client has its own wake
model:

- **Active polling**: a scheduled chat/task periodically calls `peek_inbox`.
- **External watcher**: a local daemon watches inbox files and triggers a consumer.
- **Push-capable client**: a client may expose an API/event hook to wake on message.
- **Manual fallback**: the user can explicitly ask an agent to check its inbox.

Protocol rules must not assume a specific wake model. They only define when a
message should be sent and how it should be shaped.

Workload distribution (who sends more) is project-specific and not fixed by
agent identity. A project where Claude does the implementation and Codex does
review would invert the volume pattern from MLV-App. The routing rules and type
tables are symmetric by design.

## Project Identity and Rendezvous Naming

**Convention over configuration.** The rendezvous channel name is derived automatically
from the project root folder name. No config file required.

```
rendezvous = normalize(project_root_folder_name)

normalize rules (apply in order):
  1. lowercase
  2. spaces → hyphens
  3. keep only [a-z0-9-_], strip everything else
```

Examples:

| Folder name | Rendezvous |
|---|---|
| `MLV-App` | `mlv-app` |
| `AdversarialLLM-ClaudeCode` | `adversarialllm-claudecode` |
| `My Cool Project` | `my-cool-project` |

Both agents work in the same repo → same folder name → same rendezvous. Zero
coordination required.

**Override (optional):** If a `.agent-bridge.json` file exists at the project root
with a `rendezvous` field, use that value instead. Reserved for cases where the
folder name is genuinely wrong (legacy name, shared folder, etc.). Most projects
will never need this file.

```json
{ "rendezvous": "my-override-name" }
```

**Multi-project:** Each project gets its own rendezvous and its own session state
slot. You can have multiple projects paired simultaneously without interference.
`session.json` is keyed by rendezvous name:

```json
{
  "sessions": {
    "mlv-app": { "claude_guid": "...", "codex_guid": "...", "status": "active" },
    "adversarialllm-claudecode": { "claude_guid": "...", "codex_guid": "...", "status": "active" }
  }
}
```

Supersede is project-scoped: a new `mlv-app` session only supersedes old `mlv-app`
sessions; other projects are untouched.

## Two-Channel Model

The bridge uses two inbox channels with different roles:

```text
Private GUID inbox    = normal work traffic (IMPLEMENTATION_SUMMARY, PHASE_DONE, AUDIT_RESULT, etc.)
<rendezvous> channel  = low-frequency control-plane only (HANDSHAKE, HANDSHAKE_ACK, SESSION_UPDATE)
```

Both agents watch **both** channels after pairing:

- **Private inbox**: file monitor (zero polling).
- **Rendezvous channel**: low-frequency cadence (~5 min or watcher daemon).

The separation keeps work traffic clean and the control plane always reachable without flooding it.

## Session Boot and Automatic Supersede

**Every new agent session always sends a HANDSHAKE.** There is no silent resume.

`session.json` is read on boot solely to find the peer's current GUID so the ACK can
be routed quickly — it never skips the HANDSHAKE. This is what enables automatic
supersede without user intervention: starting a new session is the signal.

Boot sequence on every new session:

```
1. Derive rendezvous from project root folder name (normalize rule above)
   or read .agent-bridge.json override if present
2. Read %USERPROFILE%\.agent-bridge\session.json (for peer GUID hint under this rendezvous only)
3. Generate a fresh own GUID
4. Start watching own private inbox file (file monitor, not polling)
5. Start watching <rendezvous> channel (low-frequency, for control-plane signals)
6. Send HANDSHAKE to <rendezvous> with new GUID and project=<rendezvous>
7. On HANDSHAKE_ACK: update session.json under this rendezvous key, begin normal bridging
```

The peer, on receiving a HANDSHAKE while a prior session is active:

```
1. Send HANDSHAKE_ACK to new GUID
2. Send SESSION_UPDATE: superseded to OLD GUID
3. Update session.json to new GUIDs
```

The old session, on receiving SESSION_UPDATE: superseded:

```
1. File monitor fires (new line in inbox file)
2. Call check_inbox, read the supersede signal
3. Stop file monitor, stop sending bridge messages
4. Surface note to user: "Newer session has taken over. Bridge closed here."
```

This means the user never needs to say anything. Opening a new session is enough.

| Scenario | Behavior |
|---|---|
| User opens new session while old one still active | New session HANDSHAKEs → peer supersedes old → old session self-closes bridge. Automatic. |
| User opens new session after archiving old one | Same — HANDSHAKE goes out, peer ACKs, session.json updated. |
| Only Claude restarts (crash/reload) | Same boot sequence — HANDSHAKE out, peer ACKs. |
| Only Codex restarts | New Codex HANDSHAKEs → Claude sees it via mlvapp watch → ACKs → Codex resumes. |
| Both restart | Each sends HANDSHAKE; newest ACK wins; session.json updated. |
| session.json missing | HANDSHAKE still goes out; peer GUID hint unavailable but mlvapp rendezvous works. |
| User says `bridge end` | Explicit teardown — drains inbox, sends TEARDOWN, marks ended. Use only when fully done. |

The watcher daemon watches both the private inbox file and the mlvapp file so supersede
signals and restart HANDSHAKEs wake the running session immediately.

Control messages carried on mlvapp: `HANDSHAKE`, `HANDSHAKE_ACK`, `SESSION_UPDATE`.
Normal work messages must never use the rendezvous channel after pairing.

## Session Lifecycle

**Invariant: only one active session per agent/project should own a given private GUID.**

If a new session pairs, it supersedes any prior session. The old session must either
be explicitly ended or receive a `SESSION_UPDATE: superseded` signal so it stops
sending and consuming.

Without a stop signal you can get ghost sessions — old chats still polling the same
inbox, still sending on stale GUIDs, racing with the new session.

### Lifecycle commands

```text
bridge status   -> report current session GUIDs, active/paused/ended state
bridge pause    -> stop polling temporarily; keep session state for resume
bridge resume   -> resume polling current GUID; check inbox once immediately
bridge end      -> permanently retire this session; notify peer to stop sending here
```

### `bridge status`

Reports: current Claude GUID, Codex GUID, pairing state (active / paused / ended),
last message time, and whether session.json is current.

### `bridge pause`

Use when stepping away temporarily but want to resume the same session later.

The receiving agent will:

1. Stop the polling loop.
2. Stop sending new bridge messages.
3. Mark `session.json` state as `paused`.
4. Keep GUIDs intact — inbox messages accumulate for later.

### `bridge resume`

Resumes a paused session.

The receiving agent will:

1. Reload GUIDs from `session.json`.
2. Mark state as `active`.
3. Check inbox once immediately, then resume normal polling cadence.

### `bridge end` / `end bridge`

Use before archiving when you want a true fresh start — different project, done for
the day, or explicitly retiring old GUIDs.

The receiving agent will:

1. Stop the polling loop.
2. Send `SESSION_UPDATE` (TEARDOWN) to the peer's private GUID:
   ```text
   TYPE: SESSION_UPDATE
   STATUS: info
   SUMMARY: Session ending; stop sending to this GUID.
   ACTION_REQUESTED: none
   ```
3. Mark `session.json` as ended/stale.
4. Stop sending bridge messages.

The peer, on receiving the TEARDOWN:

1. Stops sending to the old GUID.
2. Goes quiet until a new HANDSHAKE arrives.
3. Updates `session.json` to reflect the ended state.

Next new session sees stale `session.json` and does a full fresh HANDSHAKE.

```text
bridge end
end bridge
end bridge: done for today
end bridge: switching projects
```

### Supersede on new pairing

When a new session sends `HANDSHAKE` to mlvapp while an old session is still active,
the running peer should treat this as an implicit supersede of the old pairing:

1. Peer sends `HANDSHAKE_ACK` to the new GUID.
2. Peer sends `SESSION_UPDATE: superseded` to the **old** GUID so the old session
   knows to stop polling.
3. Both update `session.json` to the new GUIDs.

This ensures a clean handoff even if the user forgets to say `bridge end` first.

### Summary

| Intent | Action |
|---|---|
| Continue tomorrow / resume context | Just archive. New session resumes seamlessly. |
| Step away briefly | `bridge pause` |
| Come back after pause | `bridge resume` |
| True fresh start / stop bridging | `bridge end`, then archive. |
| Check what's active | `bridge status` |

## Self-Healing

Self-healing here means: detect when a session is probably stale and recover safely —
not a distributed-systems heartbeat, just enough to keep the bridge from getting stuck.

### Session states

Sessions carry a lifecycle state in `session.json`:

```json
{
  "sessions": {
    "<active-guid>": {
      "agent": "claude",
      "project": "mlvapp",
      "status": "active",
      "last_read_at": "2026-04-26T22:00:00Z"
    },
    "<old-guid>": {
      "agent": "claude",
      "project": "mlvapp",
      "status": "superseded",
      "superseded_by": "<active-guid>",
      "superseded_at": "2026-04-26T21:00:00Z"
    }
  }
}
```

Valid statuses:

```text
active       -> currently paired and polling
paused       -> polling stopped, state retained for resume
suspect      -> no read activity beyond timeout threshold; may be dead
superseded   -> replaced by a newer session for the same agent/project
ended        -> explicitly closed via bridge end
```

### Healing rules

**Rule 1 — Heartbeat timeout (`active` → `suspect`)**

If a session has no read activity for a configurable threshold (default: 24 hours),
mark it `suspect`. Do not stop delivery or delete anything; just flag it.
Threshold is a heuristic — burst workflows with long gaps may need a longer window.

**Rule 2 — New handshake for same agent/project (`old` → `superseded`)**

When a new HANDSHAKE arrives on mlvapp for the same agent/project role:
- New session → `active`
- Old session → `superseded` (record `superseded_by` and timestamp)
- Peer sends `SESSION_UPDATE: superseded` to old GUID so any running old session
  knows to stop

**Rule 3 — Unread messages on a non-active session**

```text
Unread + active session    -> keep; deliver normally
Unread + suspect session   -> keep; surface as warning on bridge status
Unread + superseded/ended  -> rehome or quarantine (see below)
```

Rehome: if there is exactly one active successor session for the same agent/project,
offer or perform rehome automatically. If ambiguous (multiple active sessions), do not
auto-rehome — quarantine instead.

**Rule 4 — `bridge end` drains before shutdown**

Before marking a session `ended` and sending TEARDOWN:
1. Read the inbox once and surface any unread messages to the user.
2. User acknowledges or they are quarantined.
3. Then send TEARDOWN and mark `ended`.

No unread message is silently dropped on teardown.

### Orphan quarantine

Unread messages on dead/superseded sessions that cannot be rehomed move to:

```text
%USERPROFILE%\.agent-bridge\state\orphaned-claude.jsonl
%USERPROFILE%\.agent-bridge\state\orphaned-codex.jsonl
```

Each entry carries metadata:

```json
{
  "original_session_id": "...",
  "orphaned_at": "...",
  "reason": "session superseded",
  "message": { ... }
}
```

Quarantined messages are retained for 30 days (configurable), then pruned — never
silently deleted while within the window. `bridge status` reports quarantine count.

### What is never done

- Silent deletion of unread messages.
- Auto-rehome when there is ambiguity about the correct successor.
- Marking a session `suspect` or `superseded` during active polling (only on timeout
  or explicit new HANDSHAKE).

## Session Pairing

Both agents run this flow on every new session.

Initiating agent (Claude by convention):

1. Generate a GUID as its private inbox for this session.
2. Poll that GUID (peek-first: read without marking, mark only on real message).
3. Send `HANDSHAKE` to the rendezvous channel (`mlvapp`) with its GUID.

Responding agent (Codex by convention):

1. Poll the rendezvous channel for `HANDSHAKE`.
2. Generate a GUID as its private inbox for this session.
3. Send `HANDSHAKE_ACK` to the initiator's GUID, including its own GUID.
4. Switch all outbound to the initiator's GUID.
5. Continue polling mlvapp at low frequency for future restart signals.

## Message Envelope

```text
TYPE: <TYPE_NAME>
STATUS: pass | fail | blocked | info
SUMMARY: <one short line>
ACTION_REQUESTED: audit | approve | implement | investigate | none

<full natural-language response -- same as you would paste manually>
```

Include a `SESSION:` field only when the payload references a specific GUID.
Do not truncate the body; include all reasoning, caveats, and recommendations.
If a commit hash is available, include it. If not committed, say so and list
changed files/tests instead.

## Trigger Rules

### Codex Sends

| Type | When to send |
|---|---|
| `IMPLEMENTATION_SUMMARY` | Meaningful code/tooling change completed, even before commit |
| `PHASE_DONE` | Phase milestone ready for Claude review; include commit hash if available, else list changed files |
| `DOGFOOD_REPORT` | Dogfood window closed; metrics, observations, hang confirmation |
| `TEST_RESULT` | Important test/PAR/stress result, pass or fail |
| `BLOCKER` | Work halted; needs user or Claude decision |
| `SCOPE_QUERY` | Architecture/scope clarification needed before proceeding |
| `AUDIT_REQUEST` | Asking Claude to review code, protocol, docs, or design |
| `SESSION_UPDATE` | Bridge/session/config/state-dir changed |

### Claude Sends

| Type | When to send |
|---|---|
| `PHASE_APPROVED` | User opens next gate or says "go ahead" |
| `PHASE_PASSED` | PASSED marker/dogfood gate complete |
| `AUDIT_RESULT` | Review verdict (pass/fail, items, recommendations) |
| `SCOPE_CHANGE` | User changes what is in or out of scope |
| `ARCH_DECISION` | Architecture or design decision resolved in conversation |
| `UNBLOCK` | A blocker Codex reported has been resolved |
| `ACTION_REQUEST` | User explicitly asks Claude to tell Codex something |
| `USER_PREFERENCE` | User states a durable workflow preference affecting Codex |

### Strong Non-Triggers (both sides)

Do not bridge:

- Empty polling / "no unread messages."
- "Starting work" or "beginning implementation."
- Intermediate errors that are fixable locally.
- Routine acknowledgements ("ok," "sounds good," "received") -- unless they
  also change state.
- Internal reasoning not yet resulting in a decision.
- Tool progress updates while still working.
- Repeated reminders of already-known session IDs or config.
- Local command output that does not affect the other agent.
- Status that will be superseded within minutes by a more complete result.

## Safety Rules

1. No secrets in bridge messages; they may be logged.
2. Full relevant prose is welcome; link to large files/logs instead of
   embedding raw multi-MB output.
3. If the bridge rejects because the peer has unread mail, retry once after
   the peer consumes the message; do not loop.
4. After pairing, do not send non-handshake messages to the rendezvous channel.
5. One unread message per target session is the transport constraint; respect it.

## Valid Types

```text
Codex -> Claude:
  IMPLEMENTATION_SUMMARY, PHASE_DONE, DOGFOOD_REPORT, TEST_RESULT,
  BLOCKER, SCOPE_QUERY, AUDIT_REQUEST, SESSION_UPDATE

Claude -> Codex:
  PHASE_APPROVED, PHASE_PASSED, AUDIT_RESULT, SCOPE_CHANGE,
  ARCH_DECISION, UNBLOCK, ACTION_REQUEST, USER_PREFERENCE

Either direction:
  SESSION_UPDATE, USER_PREFERENCE, AUDIT_REQUEST
```

## Bridge Feedback

The user can teach both agents what to send or stop sending. Three commands:

### `bridge learn`

Use this after manually pasting something that should have been bridged automatically.

```text
bridge learn
bridge learn: this should have been sent as AUDIT_REQUEST
```

The receiving agent will:
1. Identify the missed pattern from context (type is optional -- agent infers if not given).
2. Add a positive trigger rule to `%USERPROFILE%\.agent-bridge\routing-rules.json` under `learned_triggers`.
3. Send `USER_PREFERENCE` to the peer so both sides apply it.
4. Update `BRIDGE_PROTOCOL.md` trigger rules if the pattern is generally useful.

### `bridge suppress` / `stop bridging this`

Use this after an auto-send that was noisy or unnecessary.

```text
bridge suppress
bridge suppress: stop sending routine ACKs
stop bridging this
```

The receiving agent will:
1. Identify the noisy pattern from context.
2. Add a negative rule to `routing-rules.json` under `suppressed_triggers`.
3. Send `USER_PREFERENCE` to the peer.
4. Apply immediately for the rest of the session.

### `bridge rule status`

List the active custom learned and suppressed rules.

```text
bridge rule status
```

### Routing rules file

```text
%USERPROFILE%\.agent-bridge\routing-rules.json
```

Shape (see `routing-rules.example.json` in this directory):

```json
{
  "learned_triggers": [
    {
      "source": "codex",
      "direction": "codex->claude",
      "suggested_type": "AUDIT_REQUEST",
      "pattern": "When Codex changes bridge tooling and asks for review, send AUDIT_REQUEST.",
      "reason": "User manually pasted it; should auto-send next time.",
      "learned_from_session": "<session-guid>",
      "last_updated": "YYYY-MM-DD"
    }
  ],
  "suppressed_triggers": [
    {
      "source": "claude",
      "direction": "claude->codex",
      "pattern": "Routine acknowledgement with no state change.",
      "rule": "Do not auto-bridge routine acks unless they change state or unblock work.",
      "learned_from_session": "<session-guid>",
      "last_updated": "YYYY-MM-DD"
    }
  ],
  "updated_at": "YYYY-MM-DD"
}
```

Both agents load this file on session start and merge it with their built-in trigger heuristics.
Suppressed rules take precedence over learned triggers.

## Examples

### Phase Milestone Ready (Codex -> Claude)

```text
TYPE: PHASE_DONE
STATUS: pass
SUMMARY: Phase 3C ReconWorker committed; 107 tests pass, 0 failures
ACTION_REQUESTED: audit

Phase 3C ReconWorker is committed and ready for audit.

DATA:
- commit: aa8e9aa4
- phase: 3C
- tests_passed: 107
- assertions: 1345
- skips: 7

Full implementation summary:
...
```

### Uncommitted Milestone (Codex -> Claude)

```text
TYPE: IMPLEMENTATION_SUMMARY
STATUS: info
SUMMARY: Rate-limit replacement for hop counter -- uncommitted, ready for review
ACTION_REQUESTED: audit

Hop counter replaced with time-window rate limiting. Not yet committed.

Changed files:
- tools/agent-bridge/agent_bridge.py (core change)
- tests/test_bridge_ratelimit.py (new tests, all pass)

Behavior: rejects sends that exceed N messages per rolling T seconds,
not hop count. Echo detection via seen-sender tracking.

Awaiting your review before I commit.
```

### Dogfood Report (Codex -> Claude)

```text
TYPE: DOGFOOD_REPORT
STATUS: pass
SUMMARY: 3C dogfood complete; cadence p50 29ms, zero hangs
ACTION_REQUESTED: approve

3C dogfood completed without play/pause/scrub hangs.

DATA:
- phase: 3C
- cadence_p50_ms: 29
- cadence_p95_ms: 48
- recon_overlap_pct: 94
- zero_hang_play_pause_scrub: yes
- drop_count: 0

Full observations:
...
```

### Audit Result (Claude -> Codex)

```text
TYPE: AUDIT_RESULT
STATUS: pass
SUMMARY: 3C audit passed all 5 items; dogfood window open
ACTION_REQUESTED: none

Audit passed all 5 items.

DATA:
- phase: 3C
- items_passed: 5
- items_failed: 0
- notes: MLVApp.pro scope guard expected and correct

Full review:
...
```

### Blocker (Codex -> Claude)

```text
TYPE: BLOCKER
STATUS: blocked
SUMMARY: applyLLRawProcObjectWorker missing; prep commit required before 3C
ACTION_REQUESTED: approve

I am blocked before 3C can proceed.

DATA:
- missing: applyLLRawProcObjectWorker in llrawproc.c
- options: [land prep commit, descope recon worker]

Full blocker context:
...
```

### User Preference (either direction)

```text
TYPE: USER_PREFERENCE
STATUS: info
SUMMARY: User wants full payload, not terse; 60s polling cadence matters
ACTION_REQUESTED: none

User clarified their workflow: they paste responses between agents in
real-time, so 5-minute polling is too slow. 60s is the right cadence.
Full natural-language payloads, not stripped summaries. This applies to
all future bridge messages.
```
