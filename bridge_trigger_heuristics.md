# Codex Bridge Trigger Heuristics

This file is Codex-specific bridge policy. It complements `AGENTS.md`.

`AGENTS.md` keeps the always-on startup contract short.
This file holds the evolving heuristics, wake-loop behavior, and message-handling discipline for Codex.
For the operational lifecycle spec, see `tools/agent-bridge/BRIDGE_WATCH_LIFECYCLE.md`.

## Session Startup

After bridge bootstrap succeeds, start Codex-side inbox monitoring for the active session.
Session start is the preferred moment to enter the loop, but the loop is not considered active until Codex explicitly calls `wait_inbox(...)` in a live turn.

- Preferred loop:
  - call `wait_inbox(agent="codex", session_ids=["mlv-app", "<active-guid>"], timeout_seconds=55, mark_read=false)`
- Use `55` seconds so the call returns before the MCP host's default `60` second timeout.
- Re-invoke immediately on timeout. Do not pause and do not wait for user input.
- The goal is a near-continuous blocking loop:
  - `wait_inbox` returns
  - handle or discard
  - `wait_inbox` again
- Keep the gap between waits as small as possible.
- If a message arrives:
  - surface it in chat,
  - handle it,
  - mark it read explicitly by id,
  - then re-enter `wait_inbox`.
- If the tool returns a transient error or unhandled exception:
  - surface the error,
  - then re-invoke `wait_inbox` unless a stop condition below applies.
- Stop conditions:
  - `SESSION_UPDATE: superseded` arrives
  - the user explicitly ends or clears the session
  - the chat is compacted and the prior live turn is no longer running
  - a newer Codex session takes over and this session is no longer the active owner
- Any normal user message interrupts the active `wait_inbox` chain; after responding, re-enter the loop only if bridge-watch remains the current task.

## Consumption Safety

Observe first, consume second.

- Use `wait_inbox(..., mark_read=false)` or `peek_inbox` for discovery, demos, and wake loops.
- Do not call `check_inbox(..., mark_read=true)` unless you are ready to surface and act on every returned message immediately.
- If a test explicitly asks for `wait_inbox`, do not substitute `check_inbox`.
- After handling a message seen with `mark_read=false`, mark it read explicitly by id.

Inbox hygiene for bridge-related work:

- At the start of any bridge-related coding, design, audit, or protocol turn, check Codex's private GUID bucket and the project bucket non-destructively.
- At the end of that turn, check the same buckets again before the final response.
- Surface and handle any relevant messages, then mark each handled message read by id.
- Enter the persistent `wait_inbox` loop only when bridge-watch itself is the active task; for normal coding turns, use start/end inbox checks instead.
- Continuous monitoring is only active while a live turn is blocked inside `wait_inbox`. If Codex is answering the user normally, Codex is not continuously monitoring.
- Workflow hooks can remind Codex to check or enter `wait_inbox`, but they cannot resume an already-ended turn or create continuous monitoring by themselves.
- If the user asks for continuous bridge monitoring, stop normal response work and enter the `wait_inbox` loop as the active task.
- Hook v1 is reminder-only: it may remind Codex to run inbox hygiene, but it must not inspect message bodies, mark messages read, or call `consume_inbox.py`.
- Current Codex Desktop `notify` hook status: tested and not firing in this Desktop thread. Do not rely on it unless `codex-bridge-reminder.log` shows an automatic entry with `force=False noToast=False`.
- Active hook strategy is workflow-rule based: `AGENTS.md` requires `codex_pre_response.ps1` and `codex_pre_final.ps1` around bridge-related responses.
- A hook may evolve to show non-destructive receipt/status summaries only after the receipt tools exist; it must never silently consume bridge messages.
- If Claude or the user reports that Codex's bucket is blocking sends, immediately check Codex's private GUID bucket and project bucket non-destructively.
- If unread messages are present, surface them, handle them, and mark them read by id before doing more bridge work.
- If both buckets are already empty, send a `BACKPRESSURE_STATUS` or `ROUTE_REPAIR` update to Claude with the checked buckets and ask them to retry from fresh state.

## Routing Heuristics

Bridge messages automatically when the user would otherwise need to paste them manually.

High-value auto-send categories:

- `ROOT_CAUSE`
  - a genuine diagnosis that changes what we think is broken
- `PROTOCOL_SYNC`
  - shared operating rules, consumption rules, or message-shape changes
- `HEURISTIC_SYNC`
  - changes to bridge-trigger behavior or what should auto-send
- `SPEC_REVIEW_REQUEST`
  - new or materially changed bridge specs, protocol docs, lifecycle docs, or design notes that need peer review before implementation
- `IMPLEMENTATION_START`
  - starting code changes that implement a shared bridge design, protocol behavior, or cross-agent workflow
- `IMPLEMENTATION_UPDATE`
  - finishing or committing code changes that implement shared bridge behavior, especially when the peer agent was expected to review the design
- `RESTART_ACK`
  - confirmation that a restart fixed a previously broken bridge path
- `AUDIT_RESULT`
  - verification of another agent's patch, diagnosis, or test result
- `ACTION_REQUEST`
  - a concrete next step the other agent needs to do now
- `PHASE_DONE`
  - a meaningful milestone ready for peer review

Bridge spec discipline:

- When drafting or materially changing a bridge design spec, protocol spec, lifecycle doc, or trigger heuristic, send `SPEC_REVIEW_REQUEST` to Claude automatically.
- When beginning implementation of a shared bridge design, send `IMPLEMENTATION_START` before editing.
- After committing shared bridge behavior, send `IMPLEMENTATION_UPDATE` with the commit hash, verification, and known follow-up gaps.
- When changing this heuristics file, send `HEURISTIC_SYNC` to Claude and ask whether the same rule is useful on Claude's side.
- If Codex realizes after the fact that a message should have been bridged, send the missed bridge message immediately, then update this heuristics file in the same turn so the miss becomes an explicit future trigger.

Do not auto-send:

- routine empty-inbox updates
- low-signal acknowledgements with no state change
- repeated restatements of already-known session ids or config

## Routing Discipline

- Prefer project or active GUID session buckets over `default` whenever known.
- Treat `default` as fallback-only, since it is a shared chokepoint.
- If a send is blocked by unread mail, inspect which bucket is blocked before assuming transport failure.

## Known Limits

- Codex does not have a proven external wake path into an idle active chat yet.
- Practical Codex wake today is:
  - a running `wait_inbox` loop in-session, or
  - the user nudging Codex to check/read after Claude sends.
- Until a true harness-level trigger exists, do not overclaim "auto-wake" beyond the `wait_inbox` loop pattern.
- Distinguish two mechanisms clearly:
  - keepalive loop:
    - the chat is still alive inside `wait_inbox(...)`
    - this is the primary wake mechanism
    - chained `wait_inbox` calls keep the thread near-continuously listening
  - post-turn recovery:
    - the turn has already ended and the chat is detached
    - hooks such as `Stop` may run external recovery logic
    - that is not the same as guaranteed re-entry into this same live thread
- Treat `Stop` hooks or similar post-turn hooks as recovery helpers only:
  - useful for logging, reminders, or external orchestration
  - not a substitute for maintaining the active `wait_inbox` loop
