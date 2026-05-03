# Agent Bridge Runtime Reload

Bridge MCP servers are normal Python processes. They import `agent_bridge.py`,
`server.py`, and `core/*` once at process start. Python does not hot-reload those
modules after a Git checkout, patch, or commit.

The Desktop MCP wrapper must also keep the already-initialized `server.py` child
alive after code changes. MCP stdio initialization is stateful; replacing the
child process under a connected host can close the transport or leave the host
and child in different protocol states. Code changes therefore set
`tool-refresh-status.json` to `refresh_required` and emit
`mcp_server_refresh_required` / `mcp_tools_refresh_required` audit rows instead
of hot-restarting the child.

## Rule

After changing bridge Python code, restart each MCP client session before using
that client's MCP tools as proof of the new behavior.

Fresh direct probes such as `probe_server.py` spawn a new interpreter and test the
current files. Existing Claude Desktop, Claude Code, Codex Desktop, or Codex
probe sessions may still be talking to older `server.py` processes until those
clients restart their MCP server process.

## Symptoms

Stale MCP server processes can make a fixed bug look unfixed. Examples:

- `tools/list` omits newly added tools.
- A tool schema still has old parameters.
- A tool returns old response shapes, such as unbounded `list_pending_receipts`
  output after the pagination fix.
- Settings or watcher changes work through a fresh probe but not through an
  already-running desktop client.
- The client reports `Transport closed` after bridge Python files changed during
  the session. Treat this as an MCP host reconnect/reload requirement, not as
  durable message loss.

## What To Do

1. Run `probe_server.py` first to verify current code in a fresh process.
2. If a desktop/client MCP call disagrees with the probe, restart that client or
   its MCP session before debugging the bridge code.
3. Use `bridge_process_status()` to inspect server markers, but remember MCP
   servers are intentionally multi-instance. A marker is observability, not a
   singleton lock.
4. After restart, rerun the same MCP tool call from the client before declaring
   a defect.

This is operational hygiene, not a bridge protocol failure.
