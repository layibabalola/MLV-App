# Agent Bridge Dead-Code Decisions

**Status:** Current keep/wire/archive decisions after the refactor-plan hardening pass.

| Component | Decision | Rationale |
|---|---|---|
| `routing_rules.py` | Keep, opt-in policy store. | User-taught routing rules remain useful, but automatic bridging still requires explicit agent judgment. |
| `routing_policy.py` | Keep, wired through `evaluate_routing`. | Provides a safe read-only decision aid without mutating bridge traffic. |
| `evaluate_routing` MCP tool | Keep. | Useful for explaining whether a message should be bridged before sending. |
| `codex_pre_response.ps1` / `codex_pre_final.ps1` | Keep, best-effort reminders only. | They cannot enforce planned tool calls, but can keep bridge hygiene visible. |
| `codex_bridge_reminder.ps1` | Keep, reminder backend. | Supports the opt-in bridge-watch workflow and response-hook nudges. |
| `codex_bridge_watch_mode.ps1` | Keep, opt-in only. | Main work chats should not be trapped in blocking waits; watch mode is explicit test/parking behavior. |
| `bridge_watch_mode.flag` | Keep, opt-in state. | Useful for deliberate bridge-watch sessions, not normal coding turns. |
| `consume_inbox.py` | Keep as CLI diagnostic only. | It must not be wired into watcher wake paths because consuming without surfacing caused message loss. |
| `probe_server.py` | Keep, safe-by-default probe. | Live mutation requires `--mutate`; default probe uses temp state. |
| `ExpectedTitleMarker` / title-marker wake verification | Archive; do not rewire. | User-directed rollback after fail-closed behavior on this Windows host. Direct thread navigation plus peer breadcrumbs is the supported path. |
| Legacy inline `on_message_command` watcher entries | Keep temporarily as compatibility only. | Managed configs should migrate to `on_message_command_template` + peer breadcrumb resolution; inline commands remain a short deprecation buffer, not the forward path. |
| `clear_inbox` / `reset_session` | Keep as compatibility shims. | New code should prefer `clear_bucket` / `reset_bucket`; shims must not silently target `default`. |
| `default` bucket | Deprecated. | It has no stable protocol semantics. Use project bucket, session GUID, or agent-level control path. |

Anything not listed here should be treated as normal active code or removed in a
future dedicated cleanup if it is no longer referenced by tests, docs, or runtime
configuration.
