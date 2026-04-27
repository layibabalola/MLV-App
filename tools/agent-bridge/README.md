# Agent Bridge MCP Server

Local MCP bridge for opt-in handoffs between Claude Desktop and Codex Desktop.

## Install

Install the Python MCP SDK for Python 3:

```powershell
py -3 -m pip install -r tools\agent-bridge\requirements.txt
```

## Codex Desktop

Add the server to `C:\Users\obabalola\.codex\config.toml`:

```toml
[mcp_servers.agent_bridge]
command = "py"
args = [
  "-3",
  "C:\\!Layi Wkspc\\MLV-App\\.claude\\worktrees\\festive-boyd-integration\\tools\\agent-bridge\\server.py",
  "--state-dir",
  "C:\\Users\\obabalola\\.agent-bridge\\state"
]
```

## Claude Desktop

Add the server to `claude_desktop_config.json`:

```json
{
  "mcpServers": {
    "agent-bridge": {
      "command": "py",
      "args": [
        "-3",
        "C:\\!Layi Wkspc\\MLV-App\\.claude\\worktrees\\festive-boyd-integration\\tools\\agent-bridge\\server.py",
        "--state-dir",
        "C:\\Users\\obabalola\\.agent-bridge\\state"
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

Use `clear_inbox` or `reset_session` to reset a session's hop counter and duplicate-tracking state.

For scheduled polling, use `peek_inbox` instead of `check_inbox`. `peek_inbox` is annotated read-only and never marks messages read. After a real message is handled, call `mark_read` with that message's `id`.

Runtime state and audit logs live under `%USERPROFILE%\.agent-bridge\state`.
The active cross-chat session registry lives at `%USERPROFILE%\.agent-bridge\session.json`.

Use `project_identity` to derive the canonical git-root-based rendezvous name.
In worktrees this resolves through `git rev-parse --git-common-dir`, so both the
main checkout and worktree sessions derive the same project name.

Use `bootstrap_session.py` to perform the startup handoff sequence:
- derive project identity,
- optionally drain the previous same-agent GUID once,
- call `activate_session`,
- retry `HANDSHAKE` control delivery up to 3 times.

```powershell
py -3 tools\agent-bridge\bootstrap_session.py --state-dir C:\Users\obabalola\.agent-bridge\state --agent claude --cwd C:\!Layi Wkspc\MLV-App --previous-session-id 84b53694-2cd6-4b01-a1ce-c6215bd61f9d
```

Use `send_control_message` for control-plane traffic such as `HANDSHAKE`,
`HANDSHAKE_ACK`, and `SESSION_UPDATE`. Control messages use replaceable control
slots so a newer handshake can supersede an older unread handshake instead of
being blocked by the normal one-unread work-message rule.

Use `consume_inbox.py` in a watcher/consumer path to detect control-plane halt
conditions such as `SESSION_UPDATE: superseded`:

```powershell
py -3 tools\agent-bridge\consume_inbox.py --state-dir C:\Users\obabalola\.agent-bridge\state --agent claude --session-id <guid>
```

## Active Session Supersede

When a new Claude or Codex chat starts while an older same-agent chat is still
open, call `activate_session` for the new chat's GUID:

```text
activate_session(agent="claude", session_id="<new-guid>", project="mlvapp")
activate_session(agent="codex", session_id="<new-guid>", project="mlvapp")
```

This does three things automatically:

1. marks the new chat as the active session for that agent/project,
2. supersedes the older same-agent session,
3. queues a control `SESSION_UPDATE` message into the older session's inbox so it
   knows to stop bridge communication.

If the opposite agent already has an active session, `activate_session` also
returns that peer GUID so the new chat can immediately talk to its most recent
"chatty cousin" without asking the user to relay anything.

Use `session_status(project="mlvapp")` to inspect the current active pair and
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
