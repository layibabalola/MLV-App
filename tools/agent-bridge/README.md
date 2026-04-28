# Agent Bridge MCP Server

Local MCP bridge for opt-in handoffs between Claude Desktop and Codex Desktop.

Canonical docs:

- `ARCHITECTURE.md` - current component model, protocol planes, receipts, process ownership, and recovery flow.
- `STATE_LAYOUT.md` - durable files under `%USERPROFILE%\.agent-bridge`.
- `SETTINGS.md` - supported `%USERPROFILE%\.agent-bridge\settings.json` runtime tuning surface.
- `DEAD_CODE_DECISIONS.md` - explicit keep/wire/archive calls for ambiguous bridge helpers.
- `REFACTOR_PLAN.md` - approved v1.1 roadmap and acceptance criteria.

## Install

Install the Python MCP SDK for Python 3:

```powershell
py -3 -m pip install -r tools\agent-bridge\requirements.txt
```

## Codex Desktop

Add the server to `%USERPROFILE%\.codex\config.toml`:

```toml
[mcp_servers.agent_bridge]
command = "py"
args = [
  "-3",
  "<repo>\\tools\\agent-bridge\\server.py",
  "--state-dir",
  "<state-dir>"
]
```

Do not add `tool_timeout_sec` or `startup_timeout_sec` unless your Codex build
documents support for those keys. Codex Desktop 0.111.0 rejects them as an
invalid transport config.

Replace `<state-dir>` with the absolute path to your shared bridge state. If
you use `%USERPROFILE%\.agent-bridge\state`, expand it to the real Windows path
before putting it in Codex or Claude config.

## Claude Desktop

Add the server to `claude_desktop_config.json`:

```json
{
  "mcpServers": {
    "agent-bridge": {
      "command": "py",
      "args": [
        "-3",
        "<repo>\\tools\\agent-bridge\\server.py",
        "--state-dir",
        "<state-dir>"
      ]
    }
  }
}
```

## Protocol

Messages must include a matching handoff marker:

```text
[[handoff:codex]]
Please review this from Claude.
```

The bridge strips the marker and delivers:

```text
From Claude:
Please review this from Claude.
```

Stop markers such as `[[DONE]]` and `[[HANDOFF-TO-USER]]` are not relayed.
`[[pause-relay]]` pauses the bridge and is not relayed.

Use `clear_bucket` or `reset_bucket` to reset a named bucket's hop counter and duplicate-tracking state. `clear_inbox` and `reset_session` remain compatibility shims.

For scheduled polling, use `peek_inbox` instead of `check_inbox`. `peek_inbox` is annotated read-only and never marks messages read. After a real message is handled, call `mark_read` with that message's `id`.

Runtime state and audit logs live under `%USERPROFILE%\.agent-bridge\state`.
The active cross-chat session registry lives at `%USERPROFILE%\.agent-bridge\session.json`.
Runtime settings, when present, live at `%USERPROFILE%\.agent-bridge\settings.json`.
Copy `tools\agent-bridge\settings.example.json` as a starting point; unsupported
keys are rejected so the settings surface stays intentionally small.

Use `project_identity` to derive the canonical git-root-based rendezvous name.
In worktrees this resolves through `git rev-parse --git-common-dir`, so both the
main checkout and worktree sessions derive the same project name.

Use `bootstrap_session.py` to perform the startup handoff sequence:
- derive project identity,
- optionally drain the previous same-agent GUID once,
- call `activate_session`,
- retry `HANDSHAKE` control delivery up to 3 times,
- optionally update a static `watcher-config.json` so the watcher follows the
  newly active private GUID plus the rendezvous/control-plane session.

```powershell
py -3 tools\agent-bridge\bootstrap_session.py --state-dir %USERPROFILE%\.agent-bridge\state --agent claude --cwd <project-root> --previous-session-id <previous-guid>
```

To refresh a static watcher config independently of bootstrap, use
`configure_watcher.py`:

```powershell
py -3 tools\agent-bridge\configure_watcher.py --config %USERPROFILE%\.agent-bridge\watcher-config.json --state-dir %USERPROFILE%\.agent-bridge\state --agent codex --cwd <project-root>
```

Use `send_control_message` for control-plane traffic such as `HANDSHAKE`,
`HANDSHAKE_ACK`, and `SESSION_UPDATE`. Control messages use replaceable control
slots so a newer handshake can supersede an older unread handshake instead of
being blocked by the normal one-unread work-message rule.

Wake paths for inbox notification:

- **Claude**: persistent in-process `Monitor` (started at session bootstrap; reads
  `inbox-claude.jsonl` and surfaces unread messages into the next turn). No
  external wake script needed.
- **Codex**: `wake_codex.ps1` is wired into `watcher-config.json` as the
  `on_message_command` for Codex entries. When the watcher detects a new
  unread Codex message, it opens the active bridge thread with
  `codex://threads/<CODEX_THREAD_ID>` and then synthesizes `check bridge inbox`
  + Enter into the Codex Desktop window via `[System.Windows.Forms.SendKeys]`.
  Codex then runs a turn, calls `check_inbox`, surfaces and handles the
  message. The deeplink step prevents the wake trigger from landing in whichever
  Codex chat happened to be visible.

Both wake paths are event-driven and zero-cost while idle. See
`BRIDGE_WATCH_LIFECYCLE.md` for details.

Halt-condition detection (e.g. `SESSION_UPDATE: superseded`) is performed by
each agent inside its normal `check_inbox` flow — there is no longer a
separate watcher/consumer step for it. The previous `consume_inbox.py` helper
is retained only as a CLI diagnostic; do not wire it into the watcher path.

## Active Session Supersede

When a new Claude or Codex chat starts while an older same-agent chat is still
open, call `activate_session` for the new chat's GUID:

```text
activate_session(agent="claude", session_id="<new-guid>", project="mlv-app")
activate_session(agent="codex", session_id="<new-guid>", project="mlv-app")
```

This does three things automatically:

1. marks the new chat as the active session for that agent/project,
2. supersedes the older same-agent session,
3. queues a control `SESSION_UPDATE` message into the older session's inbox so it
   knows to stop bridge communication.

If the opposite agent already has an active session, `activate_session` also
returns that peer GUID so the new chat can immediately talk to its most recent
"chatty cousin" without asking the user to relay anything.

Use `session_status(project="mlv-app")` to inspect the current active pair and
historical session records.
Use `end_session(agent, session_id, project)` to cleanly retire a session and
notify the active peer that it should stop sending there.

## Routing Feedback

Use `routing_rules.py` to persist user-taught bridge routing rules:

```powershell
py -3 tools\agent-bridge\routing_rules.py learn --source codex --direction codex->claude --pattern "Bridge tooling changes need Claude review" --type AUDIT_REQUEST --reason "User manually pasted it"
py -3 tools\agent-bridge\routing_rules.py suppress --source claude --direction claude->codex --pattern "Routine ACK with no state change" --rule "Do not bridge routine ACKs unless they change state" --reason "User said stop bridging this"
py -3 tools\agent-bridge\routing_rules.py feedback --source codex --direction codex->claude --message "you should have sent that automatically as AUDIT_REQUEST" --pattern "Bridge tooling changes need Claude review"
py -3 tools\agent-bridge\routing_rules.py feedback --source claude --direction claude->codex --message "stop bridging this" --pattern "Routine ACK with no state change"
py -3 tools\agent-bridge\routing_rules.py prune --days 90
py -3 tools\agent-bridge\routing_rules.py status
```

By default this writes to `%USERPROFILE%\.agent-bridge\routing-rules.json`.

The `feedback` subcommand is for natural-language bridge feedback. It recognizes
phrases like `you should have sent that automatically`, `stop bridging this`,
and `bridge rule status`, then maps them to learned, suppressed, or status
actions.

Use `routing_policy.py evaluate ...` or the MCP `evaluate_routing` tool to
apply the persisted rules at decision time before auto-bridging:

```powershell
py -3 tools\agent-bridge\routing_policy.py evaluate --source codex --direction codex->claude --text "This bridge tooling change needs Claude review"
```

## Recovery And Housekeeping

Use `recover_state.py` first when state looks corrupt. It is validate-only by
default and requires `--repair` before it writes:

```powershell
py -3 tools\agent-bridge\recover_state.py --state-dir %USERPROFILE%\.agent-bridge\state
py -3 tools\agent-bridge\recover_state.py --state-dir %USERPROFILE%\.agent-bridge\state --repair
```

Repair mode creates `state\backups\recovery-<timestamp>\` before replacing
corrupt JSON object files or quarantining invalid JSONL rows.

Use `compact.py` for read-row retention, audit rotation, and stale
`server-pids/` marker cleanup:

```powershell
py -3 tools\agent-bridge\compact.py --state-dir %USERPROFILE%\.agent-bridge\state --dry-run
```
