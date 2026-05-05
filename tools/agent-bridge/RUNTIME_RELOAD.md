# Agent Bridge Runtime Reload

Bridge MCP servers are normal Python processes. They import `agent_bridge.py`,
`server.py`, and `core/*` once at process start. Python does not hot-reload those
modules after a Git checkout, patch, or commit.

Desktop MCP configs launch `server_wrapper_trampoline.py`, which keeps the host
stdio pipe open while `server_wrapper.py` supervises the inner `server.py`
process. Ordinary bridge-code changes restart the child under the wrapper.
Changes to `server_wrapper.py` itself are handled by saving the code-watcher
snapshot and exiting with code 77; the trampoline relaunches the wrapper unless
its restart-loop guard trips.

The wrapper persists the host's MCP `initialize` / `notifications/initialized`
frames in `state/mcp-session-replay.json` and replays them into each fresh child
before forwarding new host requests. That keeps ordinary tool calls valid after
an inner child restart or an exit-77 wrapper relaunch without asking the host to
repeat initialization.

Known limitation: there is still a small theoretical byte-loss window during an
exit-77 wrapper relaunch. Once the wrapper has decided to exit, the stdin pump
may already be blocked in a low-level read; bytes the host sends during that
final handoff window can be consumed by the old wrapper before the trampoline
starts the new one. The risk is bounded by the idle gate and partial JSON-RPC
frame delay, and current smoke coverage exercises ordinary post-relaunch tool
calls plus split-frame delay, but it does not prove delivery for bytes sent after
the self-restart decision and before process exit.

Tool schema/list changes can still require a client/session refresh because the
host may cache the tool list it saw during MCP initialization. In that case the
wrapper sets `tool-refresh-status.json` to `refresh_required` and emits
`mcp_server_refresh_required` / `mcp_tools_refresh_required` audit rows.

## Rule

After changing bridge Python code, a fresh probe is still the strongest proof of
new behavior. Existing Desktop sessions should continue to answer ordinary tool
calls after wrapper/child self-heal, but restart the MCP client/session before
expecting newly added or renamed tools to appear in the host's cached tool list.

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
