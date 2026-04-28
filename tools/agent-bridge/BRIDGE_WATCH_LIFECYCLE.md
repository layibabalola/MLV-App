# Bridge Watch Lifecycle

This note defines the lifecycle for Codex-side bridge watch: how the
`wait_inbox` keepalive loop starts, stays alive, breaks, and resumes.

This is an operational spec for the current bridge design, not the future
hierarchical inbox redesign.

## Goal

Keep Codex in a near-continuous blocking `wait_inbox(...)` loop so bridge
messages arrive with zero idle-token cost while the loop is active.

## States

Use these two states consistently:

- `active-waiting`
  - the current live turn is blocked inside `wait_inbox(...)`
  - bridge traffic can wake the agent through the open tool call
- `idle-detached`
  - no live `wait_inbox(...)` call is open
  - the chat is not currently listening for bridge traffic
  - recovery requires a new live turn

## Start Conditions

Preferred start point:

- session start, immediately after bridge bootstrap succeeds

Rules:

- session start is the preferred time to enter bridge watch
- bridge watch is not active until Codex explicitly calls `wait_inbox(...)`
  in a live turn
- use:
  - `wait_inbox(agent="codex", session_ids=["mlv-app", "<active-guid>"], timeout_seconds=55, mark_read=false)`
- use `55` seconds to stay below the practical `60` second MCP host timeout

## Steady-State Loop

The intended loop is:

1. call `wait_inbox(..., timeout_seconds=55, mark_read=false)`
2. if a message arrives:
   - surface it
   - handle it
   - mark it read explicitly by id
   - call `wait_inbox(...)` again
3. if the call times out:
   - call `wait_inbox(...)` again immediately
4. if the call returns a transient error:
   - surface the error
   - call `wait_inbox(...)` again unless a stop condition applies

The gap between waits should be as small as possible.

## Stop Conditions

Bridge watch should stop when any of these are true:

- `SESSION_UPDATE: superseded` arrives
- the user explicitly ends or clears the session
- the chat is compacted and the prior live turn is no longer running
- a newer Codex session takes over and this session is no longer the active
  owner
- the current task is no longer bridge watch

## Interruptions

Any normal user message interrupts the active `wait_inbox` chain.

After responding to the user, re-enter the loop only if bridge watch remains
the current task.

Treat each of these as a loop-break event:

- normal user interruption
- compaction
- app restart
- thread clear/end
- session supersede
- unhandled turn termination

After any loop-break event, assume the chat is `idle-detached` until a new
live turn explicitly re-enters `wait_inbox(...)`.

## Consumption Rules

Observe first, consume second.

- use `wait_inbox(..., mark_read=false)` or `peek_inbox` for discovery and wake
  loops
- do not use `check_inbox(..., mark_read=true)` unless ready to surface and act
  on every returned message immediately
- if a test explicitly asks for `wait_inbox`, do not substitute `check_inbox`
- mark messages read explicitly by id after handling

## Recovery Model

Current recovery is only partially automatic.

Automatic while the loop is alive:

- timeouts
- new bridge messages
- transient tool errors, if the loop re-invokes correctly

Not fully automatic yet:

- starting the first loop without a user or startup turn
- restarting the loop after compaction, restart, clear, or other detached state
- waking a detached idle Codex chat from outside the thread

## Hooks

Do not confuse keepalive with post-turn recovery.

- keepalive loop:
  - the chat is still alive inside `wait_inbox(...)`
  - this is the primary wake mechanism
- post-turn recovery:
  - the turn has already ended
  - hooks such as `Stop` may run external recovery logic
  - this is not the same as guaranteed re-entry into the same live thread

Treat `Stop` hooks as recovery helpers only:

- useful for logging, reminders, or external orchestration
- not a substitute for maintaining the active `wait_inbox(...)` loop

Codex Hook v1 is reminder-only:

- may remind Codex to run inbox hygiene after a turn
- must not inspect message bodies
- must not mark messages read
- must not call `consume_inbox.py`
- may evolve to show receipt/status summaries only after non-destructive receipt
  tools exist
- the attempted Codex Desktop `notify` integration is tested and not active unless
  `codex-bridge-reminder.log` records an automatic invocation with
  `force=False noToast=False`
- the active Codex hook strategy is workflow-rule based: `AGENTS.md` requires
  `codex_pre_response.ps1` and `codex_pre_final.ps1` around bridge-related responses

## Hardening Checklist

Before calling bridge watch hardened, verify all of these:

- bootstrap succeeds and returns the active session GUID
- bridge watch enters `wait_inbox(...)` after bootstrap
- timeout path re-invokes immediately
- normal bridge message path surfaces, handles, marks by id, and re-enters
- transient MCP/tool error path re-invokes correctly
- user interruption path is documented and intentional
- compaction path is treated as detached and requires explicit re-entry
- restart path re-enters on the next live turn
- supersede path stops the old loop and does not re-enter
