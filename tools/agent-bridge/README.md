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

## Routing Feedback

Use `routing_rules.py` to persist user-taught bridge routing rules:

```powershell
py -3 tools\agent-bridge\routing_rules.py learn --source codex --direction codex->claude --pattern "Bridge tooling changes need Claude review" --type AUDIT_REQUEST --reason "User manually pasted it"
py -3 tools\agent-bridge\routing_rules.py suppress --source claude --direction claude->codex --pattern "Routine ACK with no state change" --rule "Do not bridge routine ACKs unless they change state" --reason "User said stop bridging this"
py -3 tools\agent-bridge\routing_rules.py status
```

By default this writes to `%USERPROFILE%\.agent-bridge\routing-rules.json`.
