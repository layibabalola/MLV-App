# Agent Bridge — Hierarchical Inbox Design Spec (v2)

**Status:** Approved for implementation (not yet implemented)
**Authors:** Claude (`a16b6e4f`) + Codex (`9111dce5`), converged 2026-04-28
**Supersedes:** flat `session_id` routing in `agent_bridge.py` v1

---

## Motivation

Today's retire-and-drain model has two failure modes:

1. **Message dies with the session.** A message sent to a superseded session gets
   `superseded_at` stamped and becomes invisible to `check_inbox`, even if it arrived
   just before the session was superseded. The drain fix (`b690e5fa`) mitigates this
   but requires the drain to happen atomically at `activate_session` time — a narrow
   window that still fails if the sender races the retirement.

2. **`default` bucket is an accidental root inbox.** No defined semantics, acts as a
   shared chokepoint, routinely wedges all sends when a single message sits unread.

The v2 model replaces ad-hoc session fallback with an explicit three-level hierarchy
and receiver-side promotion, so message loss on session death becomes structurally
impossible rather than mitigated by a drain race fix.

---

## Three-Level Hierarchy

```
agent (permanent, recovery/control only)
  └── project (durable, normal fallback coordination)
        └── session (ephemeral, preferred for active turn traffic)
```

### Level 1 — Agent Inbox

| Property | Value |
|---|---|
| Identity | Agent name: `claude`, `codex` |
| Lifetime | Permanent — never expires, never superseded |
| Purpose | Recovery, routing corrections, bootstrap repair, wake/fallback control |
| Backpressure | **None** — control messages must always land |
| Routine work | **Forbidden** — never use for normal work traffic |

Acceptable message types: `ROUTE_REPAIR`, `SESSION_REHOME`, `RESTART_ACK`,
`HANDSHAKE` (when no project is known), `SESSION_UPDATE` (supersede/orphan notices).

### Level 2 — Project Inbox

| Property | Value |
|---|---|
| Identity | Project rendezvous name: `mlv-app`, etc. |
| Lifetime | Durable — exists as long as the project is active |
| Purpose | Normal shared work when the exact session is less important; promoted session messages |
| Backpressure | Relaxed — ~5 unread max before new sends are rejected |
| Routine work | Allowed for coordination traffic |

This is the natural fallback when a session is superseded. Promoted messages land here,
not at the agent level, unless the project itself is unknown.

### Level 3 — Session Inbox

| Property | Value |
|---|---|
| Identity | Session GUID, e.g. `a16b6e4f-d0bb-4f9e-8878-22ccbef0deeb` |
| Lifetime | Ephemeral — lives while the session is active |
| Purpose | Active turn traffic, fine-grained handoffs |
| Backpressure | Strict — 1 unread max |
| Routine work | Preferred for all active work |

---

## Receiver-Side Promotion (replaces retire-and-stamp)

When `activate_session` supersedes an old session, instead of stamping `superseded_at`
on unread messages (which buries them), the bridge **promotes** them to the parent
project bucket.

```
Old session a5541439 superseded by a16b6e4f
  → unread messages in a5541439 get session_id rewritten to "mlv-app"
  → inbox_level rewritten from "session" to "project"
  → promoted_from field set to "a5541439" for audit trail
  → original session entry retained for audit (status: superseded)
```

The new session finds promoted messages at the project level on its first
`check_inbox(session_id="mlv-app")` or bootstrap drain. No special drain logic needed.
No TOCTOU window. The `b690e5fa` drain fix becomes redundant and can be simplified.

**Sender transparency:** Senders never need to know the hierarchy. They address the
deepest level they know. Promotion is entirely receiver-side.

---

## Orphaned vs. Superseded

| State | Cause | Handling |
|---|---|---|
| `superseded` | Session ended cleanly; newer session registered | Promote unread to project immediately |
| `orphaned` | Session vanished without supersede notice (crash, compaction without bootstrap, Desktop killed) | Apply longer TTL before promotion; flag messages with `orphaned_at` |

Orphaned detection: if a session has not had `activate_session` called for it within
`ORPHAN_TTL` (suggested: 30 minutes) and no heartbeat has been recorded, mark it
orphaned and promote its unread messages to project level.

---

## Escalation Rules (Send Path)

Senders address messages at the deepest level they know. The bridge resolves the
correct bucket at send time using this ladder:

```
1. Is the addressed session GUID active?
   YES → deliver to session bucket
   NO (superseded/orphaned/unknown) → escalate to project bucket

2. Is the project known and active?
   YES → deliver to project bucket
   NO → escalate to agent inbox

3. Deliver to agent inbox (always succeeds — no backpressure)
```

Escalation is logged with `escalated_from` and `escalation_reason` fields for audit.

---

## check_inbox Behavior

Hierarchical reads are **explicit and non-destructive by default.**

```python
check_inbox(agent, session_id, include_parents=False, mark_read=False)
```

| Parameter | Default | Meaning |
|---|---|---|
| `session_id` | required | The bucket to read |
| `include_parents` | `False` | Also read parent project and agent buckets |
| `mark_read` | `False` | Whether to consume (changed from current True default) |

**Why `include_parents=False` by default:** Automatic ancestor walking on a destructive
`mark_read=True` call could silently consume messages from all three levels at once.
Callers must opt in to hierarchical reads. This preserves observe-first-consume-second
discipline at the routing level.

**Recommended wake-loop pattern (Codex):**
```python
# Non-destructive — returns immediately if anything is unread at session or project level
result = wait_inbox(
    agent="codex",
    session_ids=["mlv-app", "<active-guid>"],
    timeout_seconds=55,
    mark_read=False
)
# After handling, mark read explicitly by id
mark_read(agent="codex", message_id=msg["id"], session_id=msg["session_id"])
```

---

## Backpressure Table

| Level | Limit | Rationale |
|---|---|---|
| Session | 1 unread | Strict — transient slot, prevents flooding |
| Project | 5 unread | Relaxed — coordination traffic, multiple senders |
| Agent | **None** | Recovery path must never be blockable |

A full project inbox never blocks delivery to the agent inbox. The levels are
independent backpressure domains.

---

## Schema Changes

### Inbox entry additions (no file restructuring — flat JSONL stays flat)

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

New fields are optional/nullable — existing entries remain valid (treated as
`inbox_level: "session"` for backward compatibility during migration).

### Session registry additions

```json
{
  "status": "active | superseded | orphaned",
  "orphaned_at": null,
  "promoted_message_count": 0
}
```

---

## `default` Deprecation

Once this design is implemented:
- Any send addressed to `session_id="default"` is rejected with:
  `"routing error: 'default' is deprecated; use a named project bucket or the agent inbox"`
- A migration period: during the first release, `default` sends are accepted but
  logged as deprecated warnings with the caller's stack context.
- After one release cycle: hard reject.

`default` had no defined semantics and acted as an accidental shared root inbox.
This design replaces it with explicit, semantically defined levels.

---

## Migration Steps (when implementing)

1. Add `inbox_level`, `parent_project`, `promoted_from`, `promoted_at` fields to
   `send_to_peer` and `send_control_message` write paths. Derive `inbox_level` from
   `session_id` shape (GUID → session, known-project-name → project, agent-name → agent).

2. Replace `_drain_and_retire_superseded_inbox` with `_promote_superseded_inbox`:
   rewrite `session_id` → project name, set `inbox_level = "project"`, set
   `promoted_from`, remove `superseded_at` stamp (no longer needed).

3. Update `check_inbox` signature to add `include_parents` and change `mark_read`
   default to `False`.

4. Update `_unread_for` to respect `inbox_level` in backpressure checks (session
   backpressure does not count project-level messages and vice versa).

5. Add orphan detection: background scan or on-activate check for sessions with no
   activity beyond `ORPHAN_TTL`.

6. Add `default` deprecation warning, then hard reject in the following release.

7. Update `CLAUDE.md`, `AGENTS.md`, and both `bridge_trigger_heuristics.md` files
   to document the new addressing model.

---

## What This Does Not Change

- The flat JSONL file format — no restructuring
- The MCP tool surface (`send_to_peer`, `check_inbox`, `wait_inbox`, etc.)
- The session registry structure (additions only, no removals)
- The watcher, bootstrap, or configure_watcher scripts (minor updates only)

---

## Open Questions (pre-implementation)

1. **Orphan TTL value:** 30 minutes suggested. Is that too short for a user who steps
   away and comes back? Consider making it configurable.

2. **Project-level backpressure limit:** 5 suggested. Should this be per-sender or
   total? Per-sender is safer but more complex.

3. **Agent-level message TTL:** Without backpressure, the agent inbox could accumulate
   stale control messages. Suggest a TTL of 24 hours with auto-archive to a separate
   `inbox-claude-archive.jsonl`.

4. **Multi-project:** A single Desktop session may work across multiple projects.
   Should a session be parented to exactly one project, or can it span projects?
   Current model assumes one-project-per-session.
