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

## Two-Channel Model

The bridge uses two inbox channels with different roles:

```text
Private GUID inbox  = normal work traffic (IMPLEMENTATION_SUMMARY, PHASE_DONE, AUDIT_RESULT, etc.)
mlvapp rendezvous   = low-frequency control-plane traffic only (HANDSHAKE, HANDSHAKE_ACK, SESSION_UPDATE)
```

Both agents poll/watch **both** channels after pairing:

- **Private inbox**: active cadence (60s polling or watcher daemon).
- **mlvapp**: low-frequency cadence (~5 min polling or watcher daemon).

The separation keeps work traffic clean and the control plane always reachable without flooding it.

## Session Resume and Partial Restart Handling

On every new agent session, run this boot sequence:

```
1. Read %USERPROFILE%\.agent-bridge\session.json
2. If it contains own GUID and peer GUID -> resume (skip full handshake)
3. Start polling own private GUID inbox (active cadence)
4. Start polling mlvapp (low-frequency, ~5 min)
5. If session.json is missing or stale -> full HANDSHAKE via mlvapp rendezvous
```

After pairing, **both agents keep polling mlvapp** at low frequency. This is what enables
partial-restart detection mid-session:

| Scenario | Behavior |
|---|---|
| Only Claude restarts | New Claude sends HANDSHAKE to mlvapp with new GUID. Codex sees it, sends HANDSHAKE_ACK to new GUID. Both update session.json. |
| Only Codex restarts | New Codex sends HANDSHAKE to mlvapp with new GUID. Claude sees it, sends HANDSHAKE_ACK to new GUID. Both update session.json. |
| Both restart | Each reads session.json first. If stale or conflicting, newest HANDSHAKE wins. |
| session.json missing | Full handshake via mlvapp. |

The watcher daemon watches both the private inbox JSONL and the mlvapp JSONL so that a
restart HANDSHAKE wakes the running peer immediately rather than waiting for the next
poll cycle.

Control messages carried on mlvapp after pairing: `HANDSHAKE`, `HANDSHAKE_ACK`,
`SESSION_UPDATE`. Normal work messages must not use the rendezvous channel after pairing.

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
