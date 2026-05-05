# Agent Bridge MCP Server

Local MCP bridge for opt-in handoffs between Claude Desktop and Codex Desktop.

Canonical docs:

- `USER_GUIDE.md` - user-facing setup, daily workflow, wake modes, and troubleshooting.
- `ARCHITECTURE.md` - current component model, protocol planes, receipts, process ownership, and recovery flow.
- `STATE_LAYOUT.md` - durable files under the configured bridge root
  (default `%USERPROFILE%\.agent-bridge`).
- `SETTINGS.md` - supported `<bridge-root>\settings.json` runtime tuning surface.
- `WORKFLOW_GUARDRAILS_SPEC.md` - mandatory vs configurable agent workflow/reminder behavior.
- `DOM_TELEMETRY_USE_CASE_CATALOG.md` - read-only UI/DOM telemetry use cases,
  overhead hypotheses, privacy rules, and investigation plan.
- `RUNTIME_RELOAD.md` - when MCP clients must be restarted to pick up bridge code changes.
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
  "<repo>\\tools\\agent-bridge\\server_wrapper_trampoline.py",
  "--bridge-root",
  "<bridge-root>"
]
```

Do not add `tool_timeout_sec` or `startup_timeout_sec` unless your Codex build
documents support for those keys. Codex Desktop 0.111.0 rejects them as an
invalid transport config.

Replace `<bridge-root>` with the absolute path to your shared bridge root. If
you use `%USERPROFILE%\.agent-bridge`, expand it to the real Windows path before
putting it in Codex or Claude config. The wrapper resolves redirects before
starting MCP and fails loudly if the configured root has moved.

## Claude Desktop

Add the server to `claude_desktop_config.json`:

```json
{
  "mcpServers": {
    "agent-bridge": {
      "command": "py",
      "args": [
        "-3",
        "<repo>\\tools\\agent-bridge\\server_wrapper_trampoline.py",
        "--bridge-root",
        "<bridge-root>"
      ]
    }
  }
}
```

## Windows MCP startup

On Windows, `server.py` defensively wraps `sys.stdout` so that every os-level
write to the stdout pipe is at most 4 KB. The MCP stdio transport flushes the
full JSON-RPC response in one write; once the registered tool list grew past
~25 KB, those flushes started intermittently failing with
`OSError: [Errno 22]` inside anyio's `to_thread.run_sync(self._fp.flush)`,
which crashed the server before Claude Desktop could finish initialization.
The workaround is unconditional on `win32`, has no settings knob, and is
implemented in `_install_windows_pipe_safety()` at the top of `server.py`.

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
Use `list_pending_receipts(limit=50, offset=0)` for bounded stuck-message
diagnostics; it returns receipt summaries with truncated previews. Use
`message_status(id)` to inspect a single message in full.
Use `bridge_health_panel(agent="codex", include_extended=true, format="markdown")`
for the single-glance bridge health snapshot. The snapshot includes
`recommended_actions` and a `recovery_hint`; the markdown view renders the next
safe command directly. If it reports stale unread wake gaps, run
`stale_unread_watchdog(agent="codex")` to inspect wake-delivered messages that
were never read; pass `rearm=true` only when you want the watcher to nudge
those message ids again.
Use `dashboard_overview(agent="codex", format="markdown")` for the local admin
view that combines health recommendations, pairings, contracts, pending
actions, and remote-authority rejection counts.
Use `receipt_debt_cleanup(agent="codex")` for a conservative receipt-debt
report that groups read-without-seen, old-unread, and stale-unread rows. It is
dry-run by default. With `apply=true`, it only backfills `seen_at` for rows that
were already read; add `rearm_stale_unread=true` to let normal watcher retry
re-nudge stale unread ids. It never marks old unread messages read.
Use `record_pending_bridge_action(...)` when you need to mark a surfaced bridge
message read but defer the actual follow-up until after the current work
checkpoint. `list_pending_bridge_actions(...)` shows the durable follow-up
ledger, and `resolve_pending_bridge_action(id)` closes an item after the reply,
review, or implementation follow-up is complete.

Runtime state and audit logs live under `<bridge-root>\state`.
The active cross-chat session registry lives at `<bridge-root>\session.json`.
Runtime settings, when present, live at `<bridge-root>\settings.json`.
The default bridge root is `%USERPROFILE%\.agent-bridge`, but new setup and
Desktop MCP configs should pass an explicit absolute `--bridge-root`.
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
- write the agent's runtime peer breadcrumb at the active bridge root, and
- optionally update `watcher-config.json` so the watcher follows the newly
  active private GUID plus the rendezvous/control-plane session.

```powershell
py -3 tools\agent-bridge\bootstrap_session.py --bridge-root <bridge-root> --agent claude --cwd <project-root> --previous-session-id <previous-guid>
```

To refresh a static watcher config independently of bootstrap, use
`configure_watcher.py`:

```powershell
py -3 tools\agent-bridge\configure_watcher.py --bridge-root <bridge-root> --agent codex --cwd <project-root>
```

Use `send_control_message` for control-plane traffic such as `HANDSHAKE`,
`HANDSHAKE_ACK`, and `SESSION_UPDATE`. Control messages use replaceable control
slots so a newer handshake can supersede an older unread handshake instead of
being blocked by the normal one-unread work-message rule.

Wake paths for inbox notification:

- **Claude**: per-context in-process `Monitor` (confirmed after session
  bootstrap; reads `inbox-claude.jsonl` and surfaces unread messages into the
  current Claude conversation). It does not survive compaction or a fresh
  Claude session. Bootstrap emits the reminder; the Monitor itself must be
  started or confirmed in the active Claude context. No broad external SendKeys
  wake script is safe today.
- **Codex**: managed watcher entries now prefer
  `on_message_command_template` over a fully inlined wake command. At fire
  time the watcher resolves placeholders from the active peer runtime
  breadcrumb (`peer-codex.runtime.json`), especially the current protected
  `desktop_thread_id`, then runs `wake_codex.ps1`. Managed templates are
  expanded as argv arrays rather than shell strings. Legacy inline
  `on_message_command` entries remain as a temporary compatibility form, but
  they are now coerced to argv or rejected as config errors instead of being
  run through a shell. The helper deeplinks to `codex://threads/<CODEX_THREAD_ID>` and
  synthesizes `check bridge inbox` + Enter into the Codex Desktop window via
  `[System.Windows.Forms.SendKeys]`.
  Codex then runs a turn, calls `check_inbox`, surfaces and handles the
  message. The title-marker verification experiment was removed after it
  failed closed on this Windows host; authoritative pairing now comes from
  direct thread navigation plus the peer breadcrumb.

Both wake paths are event-driven and zero-cost while idle, but they are not
equally strong: Claude Monitor is chat-scoped, while Codex wake depends on the
protected thread id recorded in the current peer breadcrumb. See
`BRIDGE_WATCH_LIFECYCLE.md` for explicit parked-watch guidance and
`AUTO_PAIR_SPEC.md` for the pairing/wake hardening roadmap.

### Mid-session MCP tool additions

Desktop MCP configs should launch `server_wrapper_trampoline.py`. The
trampoline keeps the host stdio pipe open while `server_wrapper.py` restarts the
inner `server.py` child for ordinary bridge-code changes or exits with code 77
when `server_wrapper.py` itself changed. The trampoline rate-limits repeated
exit-77 loops and relaunches the wrapper after a handled self-restart.
The wrapper records the MCP initialization frames in
`state/mcp-session-replay.json` and replays them into fresh children so ordinary
tool calls continue after a restart/relaunch.

`server.py` writes `state/tool-manifest.json` on startup, and
`server_wrapper.py` compares manifests across child self-restarts caused by
bridge code changes. If the tool signature changes during one live Desktop MCP
session, the wrapper writes `state/tool-refresh-status.json`, audits
`mcp_tools_refresh_required`, and `bridge_process_status()` returns
`tool_refresh.status = refresh_required`.

This is a fallback warning, not a live refresh guarantee. The current FastMCP
stdio surface used here exposes the `notifications/tools/list_changed` type at
the SDK level, but not an ergonomic active-session hook from this bridge
server. In practice, if `tool_refresh.refresh_required` is set, restart the
Desktop MCP client/session before expecting newly added bridge tools to appear.

Halt-condition detection (e.g. `SESSION_UPDATE: superseded`) is performed by
each agent inside its normal `check_inbox` flow — there is no longer a
separate watcher/consumer step for it. The previous `consume_inbox.py` helper
is retained only as a CLI diagnostic; do not wire it into the watcher path.

## Legacy Active Session Supersede

Normal users should prefer guided pairing (`start_guided_pairing` /
`confirm_guided_pairing`) so a new same-project chat starts as pending or
background unless the user explicitly promotes it. Direct `activate_session`
is a low-level/legacy path for bootstrap, tests, and intentional active-primary
takeover.

When a new Claude or Codex chat must intentionally become active while an older
same-agent chat is still open, call `activate_session` for the new chat's GUID:

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

When bridge wakeups route to the wrong root, repeated inbox nudges are ignored,
or a relocation may have partially completed, add `--scan-historical`:

```powershell
py -3 tools\agent-bridge\recover_state.py --state-dir %USERPROFILE%\.agent-bridge\state --scan-historical
```

Historical scan mode remains read-only. It reports stale roots with
`MOVED_TO.json`, unreadable or mismatched active-root manifests, and migration
history entries whose source root does not redirect to the active root.

Root migrations use `migrate_root.py`. Dry-run and apply output include
Claude/Codex Desktop MCP config snippets that point at `server_wrapper_trampoline.py
--bridge-root <target>`. Apply mode also validates the target root with the
historical scan. After applying a migration, update both Desktop MCP configs and
restart Claude Desktop and Codex Desktop so their MCP servers start from the new
root.

Use `compact.py` for read-row retention, audit rotation, and stale
`server-pids/` marker cleanup:

```powershell
py -3 tools\agent-bridge\compact.py --state-dir %USERPROFILE%\.agent-bridge\state --dry-run
```
