# Agent Bridge User Guide

Agent Bridge lets Claude and Codex pass work to each other through a local MCP
message bus. The normal interactive goal is visible bidirectional collaboration:

1. Claude sends Codex a bridge message.
2. The watcher notices the unread Codex inbox row.
3. Codex Desktop wakes in the paired thread.
4. Codex runs `check bridge inbox`, responds in chat, and marks the message
   read/handled.

If the user cannot see Codex react in the Desktop chat, the bridge may be
technically delivering messages, but it is not providing the intended
interactive UX.

## Quick Start

Install the local Python dependencies:

```powershell
py -3 -m pip install -r tools\agent-bridge\requirements.txt
```

Configure the MCP server in both Codex and Claude with the same bridge root.
Use an absolute bridge root such as `C:\Users\<you>\.agent-bridge`; do not leave
`%USERPROFILE%` unexpanded inside Desktop config files.

Codex `%USERPROFILE%\.codex\config.toml`:

```toml
[mcp_servers.agent_bridge]
command = "py"
args = [
  "-3",
  "<repo>\\tools\\agent-bridge\\server_wrapper.py",
  "--bridge-root",
  "<bridge-root>"
]
```

Claude `claude_desktop_config.json`:

```json
{
  "mcpServers": {
    "agent-bridge": {
      "command": "py",
      "args": [
        "-3",
        "<repo>\\tools\\agent-bridge\\server_wrapper.py",
        "--bridge-root",
        "<bridge-root>"
      ]
    }
  }
}
```

Start or refresh a session from the project checkout:

```powershell
py -3 tools\agent-bridge\bootstrap_session.py --bridge-root "<bridge-root>" --agent codex --cwd .
```

Run the equivalent bootstrap for Claude with `--agent claude` when the Claude
side of the pair starts.

For Claude, also confirm the bridge Monitor is running at session start. The
Monitor is scoped to the current Claude context window; it does not persist
across context compaction or a fresh Claude session. If Claude compacts, restart
or confirm the Monitor before relying on Codex-to-Claude notifications.
`wake_claude.ps1` is diagnostic-only today: it refuses SendKeys because Agent
Bridge does not yet have a verified Claude Desktop thread id/deeplink target.

## Daily Use

For normal paired work, send bridge messages with the MCP bridge tools or with
the documented handoff marker:

```text
[[handoff:codex]]
Please review this change and report blockers.
```

Codex should visibly wake and type only the fixed command:

```text
check bridge inbox
```

The bridge never types arbitrary bridge message bodies into Codex Desktop. The
actual message body is read through the MCP inbox tool after Codex wakes.

When a bridge message is surfaced, mark it read immediately. If the work cannot
be completed in the same turn, reply with a disposition such as acting, parked,
blocked, displaced, or rejected, and record/mark it handled through the bridge
tools.

For local diagnostics, `py -3 tools\agent-bridge\agent_bridge.py check-inbox`
reads only the current machine's bridge state files. Use it for your own local
inbox hygiene, not as a remote peer inspector. In a future multi-machine setup,
the CLI cannot reach another machine's inbox; use the MCP bridge tools and
transport layer for cross-machine access.

## Completion Standard

All Agent Bridge implementation, roadmap, and hardening work uses this
completion standard. Work is not considered final just because one side says
"done" or because a smoke test passed:

1. Every roadmap acceptance item is implemented or explicitly scoped out by the
   user, Codex, and Claude.
2. Codex iterates until it can rate the implementation 10/10 or name concrete
   blockers.
3. Codex sends Claude the changed files, test matrix, failure-path results,
   readiness score, remaining risks, and any explicit scope exclusions.
4. Claude independently reviews the same work, including stranger/cold-review
   passes where useful, until Claude also rates it 10/10 or names blockers.
5. Two or more background agents acting as strangers independently review the
   implementation and must rate it 10/10 or name concrete blockers.
6. Any disagreement is tracked as `RISK_DELTA` and resolved by code, tests,
   docs, or explicit user-approved scope change.
7. Final status is canonical only when Codex, Claude, two or more background
   stranger agents, and the user agree.

During shared bridge implementation, Codex and Claude should work in tandem:
send `IMPLEMENTATION_START` before material edits, send checkpoint updates while
work is in progress, and request peer review before calling a phase complete.
The user should not have to manually relay roadmap status between the agents.
Background agents should assist where they improve speed, decomposition,
coverage, failure-path review, or independent stranger scoring.
Waiting on those background agents is not a stopping point. Codex should keep
working on safe non-overlapping tasks while they run. If a reviewer result is
needed next and is not ready at the first checkpoint, Codex asks for an ETA,
checks back at that ETA, and asks for a renewed ETA if needed instead of
leaving the user to restart the loop.
If Claude review is blocked by backpressure, context loss, or an unavailable
peer, record the full outbound review body in the pending ledger before any
final closeout.
After Claude or another peer returns a review result, Codex must send the
amended status/closeout back to the peer before calling the loop complete. The
bridge tracks this with `review-loop-state.jsonl` and the Codex final reminder
warns when a handled review result lacks a closeout handoff.
After one item is completed or parked, Codex should continue to the next safe
roadmap or ledger item without waiting for another prompt, unless the next step
requires an explicit user decision, external credentials, destructive action, or
other non-obvious risk tradeoff.

Finalize checklist:

- Codex rates the implementation 10/10 or records concrete blockers.
- Claude rates the implementation 10/10 or records concrete blockers.
- Two or more background stranger agents rate it 10/10 or record concrete
  blockers.
- The pending ledger is clean, or all remaining items are explicitly parked,
  blocked, or scoped out.
- The user accepts the final readiness/signoff state.

## Mandatory Vs Configurable Behavior

Agent Bridge has two kinds of workflow behavior:

- **Mandatory guardrails** prevent silent work loss, wrong-thread wake,
  false completion, state corruption, or unauthorized context sharing. They are
  not normal user preferences and should not get ordinary off switches.
- **Configurable preferences** control cadence, noise, UX style, and autonomy
  where different users can reasonably choose different behavior and bad values
  fail visibly.

Examples of mandatory guardrails:

- bootstrap paired sessions and surface any drained previous-session messages,
- keep `read` and `handled` separate,
- classify every surfaced `ACTION_REQUEST` as acting, parked, blocked,
  displaced, or rejected,
- record pending reply/work debt before ending a turn,
- close review loops by sending the amended status back to the reviewing peer,
- inspect durable logs before claiming root cause for strange bridge behavior,
- relay shared bridge design decisions, defects, remediations, breakthroughs,
  roadmap/status/next-step changes, and completed implementations to the peer,
- use the 10/10 completion routine for Agent Bridge implementation work, and
- fail closed on stale/ambiguous wake targets.

On Windows Desktop surfaces, the preferred wrong-thread check is read-only UIA
sidebar/active-title enumeration: read the visible project/chat title without
activating the window, compare it to the paired thread, and abort before any UI
write if it does not match. This is separate from the intrusive SetFocus/write
side of SendKeys-style wake.

Examples of configurable preferences:

- wake provider and visible/headless UX,
- toast retention and reminder cadence,
- default pairing intent and pending-pair timeout,
- auto-mirror versus manual review of received `HEURISTIC_SYNC` rules,
- status report format and verbosity, and
- review depth above the mandatory floor.

See `WORKFLOW_GUARDRAILS_SPEC.md` for the canonical tiered list and the
roadmap gaps where mandatory behavior is still reminder-backed rather than
structurally enforced.

## Pairing Chats

The default safe flow is one active primary Claude/Codex pair per project.
Extra same-project chats should start as pending or background and only become
primary when the user explicitly chooses that.

Useful pairing tools:

- `list_pairings(agent="<agent>", project="<project>")` shows active and
  non-primary lanes.
- `pairing_details(agent="<agent>", project="<project>", session_id="<id>")`
  shows whether a chat can become primary, stay background, or act as an
  observer/advisor/auditor.
- `start_guided_pairing(...)` returns the user-visible confirmation summary
  without changing active sessions.
- `confirm_guided_pairing(..., decision="active_primary", confirm=true)`
  promotes a pending/background chat to primary and supersedes the previous
  same-agent primary.
- `confirm_guided_pairing(..., decision="observer")` parks a pending chat as a
  non-primary observer/advisor/auditor role without changing the active pair.

Subagent-origin chats cannot become active primary through the guided path. If a
thread looks wrong, inspect the chat/audit logs first; do not repair by manually
editing session state.

## Wake Modes

`wake_provider` in `<bridge-root>\settings.json` controls Codex wake behavior.
The default bridge root is `%USERPROFILE%\.agent-bridge`.

| Value | Use |
|---|---|
| `targeted_sendkeys` | Default. Wakes the paired Codex Desktop thread and gives visible chat feedback. |
| `disabled` | Toast/log only. Use when you want no focus-stealing and no composer typing. |
| `app_server` | Background/headless wake. Useful for automation; Codex Desktop does not render the driven turn. |
| `app_server_then_redraw` | Experimental/dead on current tested Desktop builds; retained for diagnostics. |
| `sendkeys` | Unsafe legacy/debug-only broad SendKeys mode. Do not use for normal pairing; prefer `targeted_sendkeys`. |

Default settings are enough for interactive pairing. To opt out of visible
auto-nudge:

```json
{
  "wake_provider": "disabled"
}
```

After changing settings, restart or reload the watcher so it picks up the new
mode. The bridge tool/action is `ensure_watcher(reason="signature_changed")`.

## What To Expect

The default `targeted_sendkeys` path is safer than the old broad SendKeys path
because it is target-gated:

- It deeplinks to the recorded Codex Desktop thread id.
- It verifies the target shortly before typing.
- It verifies twice with a small gap to reduce stale UIA reads.
- It keeps the pre-send race window small.
- It types only the constant `check bridge inbox`.
- It verifies after typing and records diagnostics if the text did not land.

This is still a UI nudge, so the user may see focus move to Codex briefly. That
focus movement is intentional for the interactive UX: the user should see Codex
receive, read, and respond to the bridge message.

## Status And Troubleshooting

Useful bridge diagnostics:

- `session_status(project="<project>")` shows the active Claude/Codex sessions.
- `bridge_process_status()` shows watcher/process health.
- `wake_breaker_status()` shows whether recent wake failures opened a breaker.
- `message_status(id="<message-id>")` shows one message and its receipt state.
- `list_pending_receipts(limit=50, offset=0)` shows stuck or unread receipts.
- `bridge_health_panel(agent="codex", include_extended=true, format="markdown")`
  shows health plus recommended next actions.
- `dashboard_overview(agent="codex", format="markdown")` shows the same health
  recommendations alongside pairings, contracts, and pending actions.
- `receipt_debt_cleanup(agent="codex")` reports receipt debt without mutating
  inboxes; `apply=true` only backfills `seen_at` for already-read rows and
  `rearm_stale_unread=true` only requeues stale wake ids for normal retry.

Recommended actions are ordered hints, not guarantees. If a mutating recovery
returns `rejected`, `empty`, or `already_*`, do not force state manually; rerun
`bridge_health_panel(..., include_extended=true)` and follow the next reported
diagnostic or remediation.

If Claude sends a message and Codex does not visibly react:

1. Confirm `settings.json` is absent or has `"wake_provider": "targeted_sendkeys"`.
2. Run `ensure_watcher(reason="signature_changed")` so the watcher reloads.
3. Check `wake_breaker_status()` for recent foreground/target failures.
4. Confirm the Codex thread you want paired is the active bootstrapped thread.
5. Use `message_status` to confirm whether the message was delivered, read, and
   handled.
6. If the health panel reports receipt debt, use `receipt_debt_cleanup(...)`
   before mutating anything manually; it separates safe backfills from unread
   messages that still require a real agent read.

Use `app_server` only when a background bridge-driven Codex turn is acceptable.
It can deliver and consume messages, but current Desktop builds do not live-render
those externally driven turns.

If an MCP bridge tool call fails with a closed transport or reconnect error, run
`bridge_health_panel(agent="<agent>", include_extended=true)`. The `MCP
reconnect` row distinguishes benign inner-server hot reloads from wrapper
relaunches where the Desktop host likely needs to reconnect. Queued messages are
durable during this outage; only bridge tool access for the current turn is at
risk.

### Claude Monitor lifecycle

Claude-side notifications depend on the in-process Monitor for the current
Claude conversation. It is not the same as the watcher daemon and it does not
survive context compaction. If Codex says it sent a message but Claude does not
react, first confirm the Claude session was bootstrapped and the Monitor was
started after the most recent compaction or session rollover. Durable messages
remain in the inbox; what stops is the visible notification loop.

Do not replace the Monitor with a broad Claude SendKeys helper. Unlike Codex
Desktop, the bridge does not currently have a trustworthy Claude thread target,
so `wake_claude.ps1` only reports diagnostics and exits fail-closed.

### CLI diagnostics scope

`py -3 tools\agent-bridge\agent_bridge.py check-inbox --agent <agent>` reads
local bridge state files directly. It is scoped to the local filesystem only
and cannot reach inboxes on a remote machine. In a multi-machine or networked
deployment each agent should use the CLI only against its own locally-stored
inbox; use the MCP `check_inbox` tool for any cross-machine access.
