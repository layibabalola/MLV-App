# Agent Bridge Settings

`%USERPROFILE%\.agent-bridge\settings.json` is the only supported user settings
file for agent-bridge runtime tuning. If the file is absent, the bridge uses the
defaults below. Start from `tools\agent-bridge\settings.example.json` when you
need to override a value.

Unsupported keys are rejected. Do not add new settings unless both questions are
answered yes:

1. Does a real user have a legitimate reason to override this?
2. If the value is wrong, is the failure visible?

## Supported Settings

| Key | Default | Range | Used by |
|---|---:|---:|---|
| `toast_expiry_minutes` | `5` | `1..60` | Windows toast notification expiry. |
| `toast_max_in_tray` | `10` | `1..50` | Best-effort Windows Action Center cap per bridge notification group. |
| `wake_idle_threshold_seconds` | `5` | `0..60` | `wake_codex.ps1 -IdleThresholdSeconds`. |
| `wake_max_wait_seconds` | `60` | `1..3600` | `wake_codex.ps1 -MaxWaitSeconds`. |
| `poll_interval_seconds` | `2` | `0.1..60` | Watcher file-stat loop interval. Accepts float (e.g. `0.5`). |
| `compact_interval_hours` | `6` | `1..168` | Watcher periodic compaction cadence. |
| `audit_log_retention_days` | `90` | `1..3650` | Rotated audit-log retention. |
| `inbox_read_retention_days` | `7` | `1..3650` | Read inbox row retention during compaction. |
| `toasts_enabled` | `true` | boolean | Watcher toast/log mode. |
| `codex_bridge_reminder_toasts_enabled` | `false` | boolean | Windows balloon toast for Codex workflow hygiene reminders; stdout reminder still prints either way. |
| `routing_rules_enabled` | `true` | boolean | Learned/suppressed routing-rule evaluation. |
| `wake_provider` | `targeted_sendkeys` | `targeted_sendkeys`, `disabled`, `app_server`, `sendkeys` (legacy), `app_server_then_redraw` | Codex wake backend. **`targeted_sendkeys` is the default**: wakes the paired Codex Desktop thread, types `check bridge inbox` in the composer, and produces visible chat feedback — the expected interactive UX. Set to `disabled` to opt out of focus-stealing and compositor typing (toast-only). Use `app_server` for background/headless automation where Desktop visibility is not needed. |

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
  "codex_bridge_reminder_toasts_enabled": false,
  "routing_rules_enabled": true,
  "wake_provider": "targeted_sendkeys"
}
```

## Compatibility

Older watcher configs may still contain `toasts_enabled`. That legacy key is
used only when `settings.json` is absent. Once `settings.json` exists, it is the
canonical source for `toasts_enabled`.
