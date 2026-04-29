# Agent Bridge - Hierarchical Inbox Design Spec (v2)

**Status:** Implemented for the supersession path; orphan handling rules
specified below 2026-04-29 (open for Codex implementation as a follow-up
commit). Agent-level recovery rules also specified below; implementation
follows after orphan handling lands.
**Authors:** Claude (`a16b6e4f`) + Codex (`9111dce5`), converged 2026-04-28;
orphan/agent-level rules added 2026-04-29 by Claude.
**Supersedes:** flat `session_id` routing in `agent_bridge.py` v1

---

## Motivation

The original retire-and-drain model had two failure modes:

1. **Message dies with the session.** A message sent to a superseded session gets
   `superseded_at` stamped and becomes invisible to `check_inbox`, even if it arrived
   just before the session was superseded. The drain fix (`b690e5fa`) narrowed that
   race but still depended on exact timing at `activate_session`.

2. **`default` bucket is an accidental root inbox.** It had no defined semantics,
   acted as a shared chokepoint, and routinely wedged unrelated traffic.

The v2 model replaces ad hoc session fallback with an explicit three-level hierarchy
and receiver-side promotion, so session turnover no longer buries useful unread mail.

---

## Three-Level Hierarchy

```text
agent (permanent, recovery/control only)
  -> project (durable, normal fallback coordination)
       -> session (ephemeral, preferred for active turn traffic)
```

### Level 1 - Agent Inbox

| Property | Value |
|---|---|
| Identity | Agent name: `claude`, `codex` |
| Lifetime | Permanent - never expires, never superseded |
| Purpose | Recovery, routing corrections, bootstrap repair, wake/fallback control |
| Backpressure | **None** - control messages must always land |
| Routine work | **Forbidden** - never use for normal work traffic |

Acceptable message types: `ROUTE_REPAIR`, `SESSION_REHOME`, `RESTART_ACK`,
`HANDSHAKE` (when no project is known), `SESSION_UPDATE` (supersede/orphan notices).

### Level 2 - Project Inbox

| Property | Value |
|---|---|
| Identity | Project rendezvous name: `mlv-app`, etc. |
| Lifetime | Durable - exists as long as the project is active |
| Purpose | Normal shared work when the exact session is less important; promoted session messages |
| Backpressure | Relaxed - 5 unread max before new sends are rejected |
| Routine work | Allowed for coordination traffic |

This is the natural fallback when a session is superseded. Promoted messages land here,
not at the agent level, unless the project itself is unknown.

### Level 3 - Session Inbox

| Property | Value |
|---|---|
| Identity | Session GUID, e.g. `a16b6e4f-d0bb-4f9e-8878-22ccbef0deeb` |
| Lifetime | Ephemeral - lives while the session is active |
| Purpose | Active turn traffic, fine-grained handoffs |
| Backpressure | Strict - 1 unread max |
| Routine work | Preferred for all active work |

---

## Receiver-Side Promotion

When `activate_session` supersedes an old session, the bridge promotes unread
messages to the parent project bucket instead of burying them in place.

```text
Old session a5541439 superseded by a16b6e4f
  -> unread messages in a5541439 get session_id rewritten to "mlv-app"
  -> inbox_level rewritten from "session" to "project"
  -> promoted_from field set to "a5541439" for audit trail
  -> escalation_reason set to "session_superseded"
```

The new session finds promoted messages at the project level on its first
`check_inbox(session_id="mlv-app")` or parent-aware session read. Senders do not
need to know the hierarchy; promotion is receiver-side.

---

## Orphaned vs. Superseded

| State | Cause | Handling |
|---|---|---|
| `superseded` | Session ended cleanly; newer session registered | Promote unread to project immediately |
| `orphaned` | Session vanished without supersede notice (crash, compaction without bootstrap, Desktop killed) | Apply orphan-detection TTL before promotion; flag messages with `orphaned_at` |

### Orphan Handling Specification (2026-04-29)

A session is **orphaned** (vs cleanly superseded) when ALL of the following
hold:

1. The session is registered as active in `session.json` for some agent.
2. The session's last heartbeat in `session.json` is older than the orphan
   detection threshold (`ORPHAN_TTL_SECONDS`, default 300s = 5 min).
3. There is no newer session for the same agent in the same project (which
   would otherwise trigger normal supersession).
4. The session's MCP server marker (if any) is missing or stale per the
   `mcp_server` presence layer in `BRIDGE_PRESENCE_SPEC.md`.

**Heartbeat source:** `session.json` records `last_heartbeat_at` per session,
updated on each MCP tool call from that session. Bridge-d (the watcher's
forward-compat role per `ARCHITECTURE.md`) is responsible for stamping this
field on every inbound MCP request.

**Orphan detection cadence:** the watcher checks for orphaned sessions on
each poll cycle (every 2s) but only acts when the heartbeat-staleness
threshold is exceeded. Costs ~5ms per check; cheap.

**Orphan promotion path:**

1. When a session is detected as orphaned, the bridge sets
   `agents.<agent>.<session_id>.status = "orphaned"` in `session.json`
   and stamps `orphaned_at` (current UTC).
2. All unread messages in that session's bucket are scanned and rewritten:
   - `session_id` rewritten to project rendezvous (e.g. `mlv-app`)
   - `inbox_level` rewritten from `"session"` to `"project"`
   - `escalation_reason` set to `"session_orphaned"`
   - `promoted_from` set to the orphaned session GUID
   - `orphaned_at` set on each message (so audit shows when promotion
     occurred relative to orphan detection)
3. Receiver finds promoted messages at the project bucket on next
   `check_inbox(session_id="<project>")`.

**Why a different threshold than supersession:** clean supersession
promotes immediately because we know the session is dead-and-replaced.
Orphan detection waits 5 minutes because:
- A transient process pause (debugger, OS suspend) shouldn't trigger
  premature promotion
- The 5-minute threshold balances "user notices their messages got
  promoted while their session was just slow to respond" against
  "messages stuck forever in a dead session bucket"
- Tunable via `settings.json`'s `orphan_ttl_seconds` (forward-compat
  per `BRIDGE_SCHEMA_EVOLUTION_SPEC.md`)

**Recovery from orphan:** if a session's heartbeat resumes (process was
just paused, not dead), bridge-d:
1. Detects fresh heartbeat in `session.json`
2. Transitions session status from `orphaned` back to `active`
3. New messages can again target the session bucket
4. Already-promoted messages STAY at project level (no demote-back); the
   sender's intent was satisfied by promotion. The session can read its
   own messages from the project bucket on next `check_inbox` with
   `include_parents=True` or `session_id="<project>"`.

**Edge case — both peers orphan simultaneously:** if both Claude and Codex
sessions are orphaned at the same time (e.g. machine sleep), promotions
happen independently per agent. On wake, both sessions either re-register
(supersession path) or recover (orphan→active transition). Either path is
correct.

### Agent-Level Recovery Specification (2026-04-29)

Agent-level inbox is the deepest fallback when neither session nor
project routing applies. Specification:

**When the agent inbox accepts a message:**

The agent-level bucket (key: `agent:<name>`) accepts incoming messages
ONLY when ALL of the following hold:

1. The message's `to` field names a known agent (`claude` or `codex`)
2. The message has NO `parent_project` field set (i.e., not project-scoped)
3. The control-type field is in the agent-level allowlist:
   `ROUTE_REPAIR`, `SESSION_REHOME`, `RESTART_ACK`, `HANDSHAKE`
   (when project not yet known), `SESSION_UPDATE` (supersede/orphan
   notices), `BRIDGE_BOOTSTRAP_REPAIR`

**Sender side:** sender explicitly addresses agent inbox via
`session_id="agent:<name>"` parameter. Default routing never escalates
to agent level automatically — preserving the principle that work
traffic must not flow there.

**Reader side:** agent-level inbox is consumed by the
`bootstrap_session.py` activation path on session start (drains repair
messages first), and by explicit operator-tool reads via
`recover_state.py`. The watcher does NOT fire wake on agent-inbox
messages — they're recovery-only and don't need active-wake delivery.

**Backpressure:** none. Per `Backpressure Table` above, agent inbox is
the recovery path and must never be blocked.

**Retention:** agent-level messages have a 30-day retention. After 30
days, `compact.py` archives to `messages.jsonl` and removes from the
inbox to prevent unbounded growth. Compaction-archived messages remain
queryable via `recover_state.py --scan-historical`.

**Supersession does NOT promote to agent level.** Promotion goes
session → project (per Receiver-Side Promotion above). Agent-level is
exclusively for control/recovery traffic that was originally addressed
there. Promoting work traffic to agent-level would violate the "routine
work forbidden" invariant.

### Acceptance Criteria

- H1. Orphan detection: session with stale heartbeat (>5min default)
  and no superseding session triggers `orphaned_at` stamp; tests assert
  the threshold and the lack of double-promotion when supersession also
  applies.
- H2. Orphan promotion: messages in orphaned session bucket are
  rewritten with `escalation_reason = "session_orphaned"`,
  `promoted_from = "<old_session_id>"`, `orphaned_at` timestamp; tests
  assert.
- H3. Orphan recovery: session heartbeat resumption transitions status
  back to `active`; already-promoted messages stay at project level;
  tests assert.
- H4. Agent-level inbox accepts only allowlisted control types; rejects
  work traffic with `escalation_reason = "agent_inbox_rejects_work"`;
  tests assert.
- H5. Agent-level retention: messages older than 30 days are archived by
  `compact.py`; tests assert with mocked clock.
- H6. Both peers orphaned simultaneously: promotion and recovery are
  agent-independent; tests assert no cross-agent interference.

---

## Escalation Rules (Send Path)

Senders address messages at the deepest level they know. The bridge resolves the
delivery bucket using this ladder:

```text
1. Is the addressed session GUID active?
   YES -> deliver to session bucket
   NO (superseded/orphaned/known project session) -> escalate to project bucket

2. Is the project known and active?
   YES -> deliver to project bucket
   NO -> preserve the explicit session bucket when the sender supplied one we cannot classify yet

3. Deliver to agent inbox only for explicit agent-level control traffic
```

Escalation is logged with `escalated_from` and `escalation_reason`.

Important nuance from the shipped code: an unknown explicit `session_id` is preserved
as a session-level bucket unless the registry already knows it belongs to a specific
project. We do not silently escalate unknown names to the agent inbox.

---

## `check_inbox` Behavior

Hierarchical reads are explicit and non-destructive by default.

```python
check_inbox(agent, session_id=None, include_parents=False, mark_read=False)
```

| Parameter | Default | Meaning |
|---|---|---|
| `session_id` | `None` | Bucket to read; omit to scan all buckets for the agent |
| `include_parents` | `False` | Also read the parent project bucket when the target bucket is a session |
| `mark_read` | `False` | Whether to consume |

Why `include_parents=False` by default: an automatic ancestor walk paired with
`mark_read=True` could silently consume multiple levels at once. Callers must opt in.

Recommended wake-loop pattern for Codex:

```python
result = wait_inbox(
    agent="codex",
    session_ids=["mlv-app", "<active-guid>"],
    timeout_seconds=55,
    mark_read=False,
)

mark_read(agent="codex", message_id=msg["id"], session_id=msg["session_id"])
```

---

## Backpressure Table

| Level | Limit | Rationale |
|---|---|---|
| Session | 1 unread | Strict - transient slot, prevents flooding |
| Project | 5 unread | Relaxed - coordination traffic |
| Agent | **None** | Recovery path must never be blockable |

The levels are independent backpressure domains. A full project inbox must not block
agent-level repair traffic.

---

## Schema Additions

Inbox entries stay in the same flat JSONL file. New fields:

```json
{
  "inbox_level": "session | project | agent",
  "parent_project": "mlv-app",
  "promoted_from": "a5541439-...",
  "promoted_at": "2026-04-28T00:00:00Z",
  "orphaned_at": null,
  "escalated_from": null,
  "escalation_reason": null
}
```

New fields are optional/nullable, so existing entries remain valid.

Session registry additions:

```json
{
  "status": "active | superseded | orphaned",
  "orphaned_at": null,
  "promoted_message_count": 0
}
```

---

## `default` Deprecation

This is already hard-rejected in the current implementation:

- Any send addressed to `session_id="default"` is rejected with:
  `"routing error: 'default' is deprecated; use a named project bucket or the agent inbox"`

`default` had no stable semantics. The hierarchy replaces it with explicit levels.

---

## Implemented In This Slice

1. Added `inbox_level`, `parent_project`, `promoted_from`, `promoted_at`,
   `orphaned_at`, `escalated_from`, and `escalation_reason` to message rows.
2. Replaced superseded-session drain/retire with project-level promotion.
3. Added `check_inbox(..., include_parents=False, mark_read=False)`.
4. Added level-aware backpressure:
   - session: 1 unread
   - project: 5 unread
   - agent: no backpressure gate
5. Hard-rejected explicit `default` sends.
6. Added parent-aware inbox reads without destructive ancestor walking by default.

---

## Follow-Up Work

1. Add orphan detection and promotion for crash-only disappearance.
2. Decide whether project reads should optionally walk up to the agent inbox too.
3. Document the new addressing model consistently in the remaining Claude-side docs.

---

## What This Does Not Change

- The flat JSONL file format
- The MCP tool surface (`send_to_peer`, `check_inbox`, `wait_inbox`, etc.)
- The session registry structure (additions only, no removals)
- The watcher, bootstrap, or configure_watcher scripts beyond minor parameter updates

---

## Open Questions

1. **Orphan TTL value:** 30 minutes is a plausible default. It may need to be configurable.
2. **Project-level backpressure limit:** 5 works as a simple start. Per-sender limits would be safer but more complex.
3. **Agent-level message TTL:** Without backpressure, the agent inbox could accumulate stale control traffic.
4. **Multi-project sessions:** The current model assumes one project parent per session.
