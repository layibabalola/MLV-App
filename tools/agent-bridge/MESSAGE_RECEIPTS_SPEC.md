# Agent Bridge - Message Receipts And Status Spec

**Status:** Partially implemented - lifecycle fields and MCP tools landed; automatic `record_seen` integration remains planned
**Authors:** Codex + Claude review
**Motivation:** distinguish "delivered to inbox" from "seen, read, and handled" without creating ACK-message loops

---

## Problem

`send_to_peer` and `send_control_message` currently return `queued` when the bridge
writes a row into the target inbox. That proves durable delivery to disk, but it does
not answer the operational questions that matter during collaboration:

- Did the receiver's watcher or wait loop notice the message?
- Did the receiver consume or mark it read?
- Did the receiver act on it?
- Is the message stuck because the target session is not watching the right bucket?

When a receiver appears silent, the sender cannot tell whether the send failed, the
receiver is idle-detached, the wrong bucket was addressed, or the receiver consumed
the message without surfacing it.

---

## Design Principle

Receipts should attach to the original message. They should not be ordinary bridge
messages by default.

ACK-as-message is useful for explicit protocol milestones, but making every message
generate another inbox message risks loops, backpressure, and low-signal clutter.
The default receipt path should be metadata plus query tools.

---

## Message Lifecycle

| State | Meaning | Source Of Truth |
|---|---|---|
| `queued` | Message row was written to the target inbox | Inbox row exists |
| `seen` | Receiver-side watcher, wait loop, or explicit read observed the row | `seen_at` |
| `read` | Receiver marked the row read or consumed it with `mark_read=true` | `read_at` |
| `handled` | Receiver finished acting on the message | `handled_at` |
| `failed` | Receiver could not act on it and recorded why | `failed_at`, `failure_reason` |

`queued` is transport delivery. `seen` is wake-path confirmation. `read` is consumption.
`handled` is semantic completion.

---

## Schema Additions

Inbox rows should gain optional receipt fields:

```json
{
  "seen_at": null,
  "seen_by_session": null,
  "seen_via": null,
  "triggered_by": null,
  "handled_at": null,
  "handled_by_session": null,
  "handled_status": null,
  "failure_reason": null,
  "ack_requested": false
}
```

Accepted `seen_via` values:

- `check_inbox`
- `peek_inbox`
- `wait_inbox`
- `watcher`
- `manual_probe`

Accepted `triggered_by` values:

- `monitor`
- `wait_inbox_loop`
- `explicit_call`
- `watcher_toast`
- `manual_probe`

`seen_via` records the bridge tool that observed the row. `triggered_by` records the
wake path that caused the receiver to inspect the inbox.

Accepted `handled_status` values:

- `handled`
- `ignored`
- `failed`
- `superseded`

All fields are optional for backward compatibility.

---

## Tools

### `message_status(message_id)`

Return the current lifecycle state for a message id across audit and inbox files.

Response fields:

```json
{
  "id": "92dfd3f2-...",
  "exists": true,
  "from": "codex",
  "to": "claude",
  "session_id": "a16b6e4f-...",
  "inbox_level": "session",
  "state": "read",
  "queued_at": "2026-04-28T00:48:47Z",
  "seen_at": "2026-04-28T00:50:00Z",
  "read_at": "2026-04-28T00:50:03Z",
  "handled_at": null,
  "handled_status": null,
  "failure_reason": null
}
```

State derivation:

1. `handled_at` set and `handled_status == "failed"` -> `failed`
2. `handled_at` set -> `handled`
3. `read_at` set -> `read`
4. `seen_at` set -> `seen`
5. inbox row exists -> `queued`
6. no row and no audit match -> `unknown`

### `mark_seen(agent, message_id, session_id=None, seen_via="check_inbox")`

Set `seen_at` without consuming the message. If `session_id` is omitted, search all
buckets for the agent.

This is useful when `wait_inbox(mark_read=false)` returns a message and the receiver
wants to record wake-path success before deciding whether to act.

### `mark_handled(agent, message_id, status="handled", failure_reason=None)`

Record semantic completion after the receiver has acted.

This should not change `read_at`. Consumption and handling are separate facts.

### `list_pending_receipts(agent=None, older_than_seconds=60)`

Return queued messages that have not progressed past `queued` or `seen` after a
threshold. This is the diagnostic view for stuck collaboration.

---

## Read Tool Behavior

Read tools should optionally update `seen_at`:

- `wait_inbox(..., mark_read=false)` should set `seen_at` on returned messages.
- `check_inbox(..., mark_read=false)` should set `seen_at` unless `record_seen=false`.
- `peek_inbox` should remain pure read-only by default. If we want seen tracking from
  peeks, add `record_seen=true` explicitly rather than changing the default.

When a tool call uses `mark_read=true`, it should set both `seen_at` and `read_at`.

`mark_read` must not imply any `handled_status`. Consumption and semantic handling
are separate facts.

---

## Explicit ACK Messages

Explicit ACK messages are still allowed for protocol handshakes and tests, but they
must be opt-in.

Use an explicit ACK message when:

- a smoke test asks for `SMOKE_ACK`
- a protocol step requires human-visible confirmation
- the sender set `ack_requested=true`
- the receiver needs to return substantive state, not just a receipt

Do not send automatic ACK messages for every bridge message.

---

## Backpressure Interaction

Receipts must not count as unread inbox traffic.

If implemented as metadata fields, they naturally avoid backpressure. If a future
transport emits receipt events, they should go to the audit log or a separate receipt
log, not to the receiver's work inbox.

---

## Diagnostics This Enables

For a message like `92dfd3f2`, `message_status` should be able to say:

- `queued`: bridge delivered it, receiver has not noticed it
- `seen`: receiver wake path noticed it, but it was not consumed
- `read`: receiver consumed it, but no semantic completion was recorded
- `handled`: receiver acted on it
- `failed`: receiver saw it but recorded a failure reason

That would have made the recent Claude silence clear immediately: messages were
`queued` and unread in Claude's private bucket, so the issue was the Claude wake/check
path, not Codex send delivery.

---

## Implementation Plan

1. Add optional receipt fields to new inbox rows.
2. Add `message_status(message_id)` to `AgentBridge` and `server.py`.
3. Add `mark_seen(...)` and call it from `wait_inbox`/non-destructive `check_inbox`
   with opt-out via `record_seen=false`.
4. Add `mark_handled(...)` for explicit semantic completion.
5. Add `list_pending_receipts(...)` for stuck-message diagnostics.
6. Keep explicit ACK messages as opt-in protocol traffic only.

---

## Open Questions

1. Should `message_status` search archived inbox files once archive/TTL lands?
2. Should receipt transitions also append to a separate `receipt-events.jsonl` log?
