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

`server_wrapper.py` also has a host-scoped live-process guard. MCP stdio remains
multi-instance by design, but one MCP host process should not own multiple live
Agent Bridge `server.py` children for the same bridge state dir. The wrapper
therefore counts only Agent Bridge's own live server markers, resolves them back
to their MCP host process, and refuses duplicate launches from the same host.
Override this with `--max-live-server-processes-per-host <n>` or set
`AGENT_BRIDGE_MAX_LIVE_MCP_SERVERS_PER_HOST`; use `0` only for targeted
diagnostics. The legacy total-process guard is disabled by default and can be
enabled explicitly with `--max-live-server-processes <n>` if an operator wants an
additional global circuit breaker.

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

## Which Tree Is Running (Claude Desktop)

Everything above concerns the wrong *code* in a live process. There is a second,
independent axis: the wrong *tree*.

Claude Desktop launches the bridge from the locked worktree
`.claude-state\worktrees\bridge-runtime` (branch `bridge-runtime`), **not** from this
checkout. Edits in the main checkout therefore do not reach the running bridge, and
they fail silently: the edit is real, the tests pass, the commit lands on `master`,
and the bridge keeps serving the old code from a different tree. Restarting Desktop
does not help — a restart re-launches from the config path, which is the worktree.

Do not guess which tree is live. Each bridge process reports its own:

```powershell
pwsh -NoProfile -File .claude-state\tools\Invoke-BridgeRuntimeLanding.ps1 -Phase Preflight
```

That emits one row per bridge process classified `runtime-worktree` / `main-tree` /
`other`, plus the tree the Desktop config currently points at. It is read-only.

To move the runtime forward:

```powershell
pwsh -NoProfile -File .claude-state\tools\Invoke-BridgeRuntimeLanding.ps1 -Phase UpdateRuntime
```

This is `git fetch fork --prune` plus `git merge --ff-only fork/master` inside the
worktree, so the work must already be on `fork/master` — a local-only commit will not
move the runtime. No Desktop restart is needed afterwards: the wrapper reloads itself
when its files change, by the same code-watcher and exit-77 paths described above.
Confirm with `-Phase Preflight`. Do not hand-edit the worktree and do not unlock it.

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
   singleton lock. If the duplicate-host guard trips, restart or reconnect the
   specific MCP host that still owns the older Agent Bridge connection.
4. After restart, rerun the same MCP tool call from the client before declaring
   a defect.

This is operational hygiene, not a bridge protocol failure.
