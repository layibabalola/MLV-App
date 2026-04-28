# Bridge Watch Lifecycle

This note defines the lifecycle for Codex-side bridge watch: how the
`wait_inbox` keepalive loop starts, stays alive, breaks, and resumes.

This is an operational spec for the current bridge design, not the future
hierarchical inbox redesign.

## Goal

Define the limits of Codex-side `wait_inbox(...)` loops. A blocking loop can
wake on bridge messages while active, but it captures the main working chat and
should not be the default operating mode.

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

- only during an explicit short smoke test or a deliberately parked bridge-watch
  session

Rules:

- session start should perform inbox hygiene, not automatically start a blocking
  loop in the main working chat
- bridge watch is not active until Codex explicitly calls `wait_inbox(...)`
  in a live turn
- use:
  - `wait_inbox(agent="codex", session_ids=["mlv-app", "<active-guid>"], timeout_seconds=55, mark_read=false)`
- use `55` seconds to stay below the practical `60` second MCP host timeout

## Explicit Test Loop

When a blocking loop is explicitly requested, the intended loop is:

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
- bridge-watch mode is explicitly exited
- the user steers or interrupts the conversation in the main working chat

## Interruptions

Any normal user message interrupts the active `wait_inbox` chain.

In the main working chat, do not auto-reenter `wait_inbox` after responding to
an interrupted user message. The UI/harness can leave the user unable to
interact normally while a shell command is running.

Re-enter only if the user explicitly asks for another short wait-loop test.

Treat each of these as a loop-break event:

- normal user interruption
- steered interruption
- compaction
- app restart
- thread clear/end
- session supersede
- unhandled turn termination

After any loop-break event, assume the chat is `idle-detached` until a new
live turn explicitly re-enters `wait_inbox(...)`.

## Consumption Rules

Observe first, consume second.

State meaning:

- `read`: the bridge message body has already been surfaced to Codex in this chat turn
- `actioned`: the requested follow-up work has been completed

Do not conflate these states. A message can be `read` and still pending action.

- use `wait_inbox(..., mark_read=false)` or `peek_inbox` for discovery and wake
  loops
- do not use `check_inbox(..., mark_read=true)` unless ready to surface and act
  on every returned message immediately
- if a test explicitly asks for `wait_inbox`, do not substitute `check_inbox`
- mark messages read explicitly by id after handling
- once a non-destructive read has surfaced a message body to Codex, mark it read
  in the bridge immediately even if the requested action is deferred to a later
  turn

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
- continuous monitoring:
  - active only while a live turn is blocked inside `wait_inbox(...)`
  - not active while Codex is answering normal user messages
- post-turn recovery:
  - the turn has already ended
  - hooks such as `Stop` may run external recovery logic
  - this is not the same as guaranteed re-entry into the same live thread

Treat `Stop` hooks as recovery helpers only:

- useful for logging, reminders, or external orchestration
- not a substitute for maintaining the active `wait_inbox(...)` loop
- unable to make an already-ended turn call `wait_inbox(...)`

Codex Hook v1 is reminder-only:

- may remind Codex to run inbox hygiene after a turn
- may front-load a louder reminder when `bridge_watch_mode.flag` is present
- must not inspect message bodies
- must not mark messages read
- must not call `consume_inbox.py`
- must not claim hard enforcement of `wait_inbox` re-entry
- is not a proven wake mechanism for trivial nudges in this Codex Desktop thread unless the reminder log shows it actually fired for that turn
- may evolve to show receipt/status summaries only after non-destructive receipt
  tools exist
- the attempted Codex Desktop `notify` integration is tested and not active unless
  `codex-bridge-reminder.log` records an automatic invocation with
  `force=False noToast=False`
- the active Codex hook strategy is workflow-rule based: `AGENTS.md` requires
  `codex_pre_response.ps1` and `codex_pre_final.ps1` around bridge-related responses

Bridge-watch mode helper:

- `tools\agent-bridge\codex_bridge_watch_mode.ps1 -Action on`
  enables a high-salience reminder mode for short explicit bridge-watch tests
- `tools\agent-bridge\codex_bridge_watch_mode.ps1 -Action off`
  returns the hook behavior to normal reminder mode
- this helper does not create automation and does not make the main working chat
  safe for indefinite blocking waits

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
