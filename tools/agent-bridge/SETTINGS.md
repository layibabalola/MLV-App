# Agent Bridge Settings

`<bridge-root>\settings.json` is the only supported user settings file for
agent-bridge runtime tuning. The default bridge root is
`%USERPROFILE%\.agent-bridge`, but deployments may pass `--bridge-root` or set
`AGENT_BRIDGE_ROOT`. If the file is absent, the bridge uses the defaults below.
Start from `tools\agent-bridge\settings.example.json` when you need to override
a value.

Unsupported keys are rejected. Do not add new settings unless the full
`WORKFLOW_GUARDRAILS_SPEC.md` settings admission gate passes:

1. The behavior is configurable rather than a mandatory guardrail.
2. A real user/project has a legitimate reason to change it.
3. A bad value fails visibly in health output, logs, hook output, or UI.
4. Defaults are safe for visible bidirectional pairing.
5. If scoped to `both`, both Claude and Codex can read and respect it before
   the setting is considered complete.

## Agent Scope

Each setting is scoped to one or both agents. Settings scoped to `both` must be
read and respected by both Claude and Codex. **Parity rule:** if one agent adds
a `both`-scoped setting, the other agent must acknowledge and implement it before
the setting is considered complete. Agent-specific settings are prefixed with the
agent name (e.g. `codex_`).

## Supported Settings

| Key | Default | Range | Scope | Notes |
|---|---:|---:|---|---|
| `toast_expiry_minutes` | `5` | `1..60` | claude | Windows toast notification expiry. |
| `toast_max_in_tray` | `10` | `1..50` | claude | Best-effort Windows Action Center cap per bridge notification group. |
| `wake_idle_threshold_seconds` | `5` | `0..60` | claude | `wake_codex.ps1 -IdleThresholdSeconds`. |
| `wake_max_wait_seconds` | `60` | `1..3600` | claude | `wake_codex.ps1 -MaxWaitSeconds`. |
| `poll_interval_seconds` | `2` | `0.1..60` | claude | Watcher file-stat loop interval. Accepts float (e.g. `0.5`). |
| `compact_interval_hours` | `6` | `1..168` | claude | Watcher periodic compaction cadence. |
| `audit_log_retention_days` | `90` | `1..3650` | both | Rotated audit-log retention. |
| `inbox_read_retention_days` | `7` | `1..3650` | both | Read inbox row retention during compaction. |
| `toasts_enabled` | `true` | boolean | claude | Watcher toast/log mode. |
| `toast_retention_mode` | `latest_sticky` | `latest_sticky`, `all_sticky`, `all_expiring` | claude | `latest_sticky`: newest toast stays until dismissed, older ones expire after `toast_expiry_minutes`. `all_sticky`: nothing auto-expires. `all_expiring`: every toast expires. |
| `codex_bridge_reminder_toasts_enabled` | `false` | boolean | codex | Windows balloon toast for Codex workflow hygiene reminders; stdout reminder still prints either way. |
| `routing_rules_enabled` | `true` | boolean | both | Learned/suppressed routing-rule evaluation. |
| `heuristic_auto_mirror_enabled` | `true` | boolean | both | Agent behavioral flag. When true, each agent's default workflow is to mirror a received `HEURISTIC_SYNC` rule into local memory and ACK without requiring explicit user approval. When false, the agent treats received HEURISTIC_SYNCs as requiring manual review before mirroring. Does not mechanically execute mirroring — it governs agent intent and reminder behavior. |
| `default_pairing_intent` | `ask_first` | `ask_first`, `active_primary`, `background` | both | Default intent when a new session registers without an explicit intent. |
| `pending_pair_timeout_seconds` | `120` | `10..3600` | both | How long a pending-pair handshake waits before timing out. |
| `wake_provider` | `targeted_sendkeys` | `targeted_sendkeys`, `disabled`, `app_server`, `app_server_then_redraw`, `sendkeys` (legacy/debug-only) | claude | Codex wake backend. **`targeted_sendkeys` is the default**: wakes the paired Codex Desktop thread, types `check bridge inbox` in the composer, and produces visible chat feedback. Set to `disabled` to opt out of focus-stealing and compositor typing (toast-only). Use `app_server` for background/headless automation where Desktop visibility is not needed. `sendkeys` is retained only as a legacy/debug override; it carries the same target and delivery-priority displacement flags as `targeted_sendkeys`, but it uses the older outer helper path and is not a normal user mode. Default targeted wake may leave the visible Codex window on the bridge thread when no exact restore id is available, because unread inbox delivery is prioritized and the displacement is audited. |

## Example

```json
{
  "toast_expiry_minutes": 5,
  "toast_max_in_tray": 10,
  "wake_idle_threshold_seconds": 5,
  "wake_max_wait_seconds": 60,
  "poll_interval_seconds": 0.5,
  "compact_interval_hours": 6,
  "audit_log_retention_days": 90,
  "inbox_read_retention_days": 7,
  "toasts_enabled": true,
  "toast_retention_mode": "latest_sticky",
  "codex_bridge_reminder_toasts_enabled": false,
  "routing_rules_enabled": true,
  "heuristic_auto_mirror_enabled": true,
  "default_pairing_intent": "ask_first",
  "pending_pair_timeout_seconds": 120,
  "wake_provider": "targeted_sendkeys"
}
```

## Compatibility

Older watcher configs may still contain `toasts_enabled`. That legacy key is
used only when `settings.json` is absent. Once `settings.json` exists, it is the
canonical source for `toasts_enabled`.
