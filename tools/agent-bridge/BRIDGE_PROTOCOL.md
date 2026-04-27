# Agent-Bridge Communication Protocol

Version: 1.3
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

After pairing, the rendezvous channel is silent until a new session starts.

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
