"""
agent-bridge watcher daemon

Watches bridge inbox JSONL files directly and triggers wake notification when
an unread message arrives. No model tokens are spent on empty checks.

Wake helper exit code is not treated as delivery. For helper-backed wake paths
(Codex), the watcher waits for receipt metadata (`seen_at` or `read_at`) before
recording a message id in watcher-state. If no receipt appears, it retries with
backoff and eventually records a visible delivery failure audit event.

Usage:
    py -3 tools/agent-bridge/watcher.py --config tools/agent-bridge/watcher-config.json

The config file lists sessions to watch. See watcher-config.example.json.
"""
import argparse
import json
import os
import subprocess
import sys
import threading
import time
import uuid
from datetime import datetime, timedelta, timezone
from pathlib import Path
from typing import Any, Callable, Dict, List, Optional

from core.processes import acquire_singleton_lease, command_line_hash, heartbeat_lease, release_lease
from core.settings import BridgeSettings, load_settings, settings_path_for_state_dir

# Compaction support (same directory -- import directly)
try:
    from compact import compact_inbox, prune_audit_logs, rotate_audit_log, should_compact
    _COMPACT_AVAILABLE = True
except ImportError:
    _COMPACT_AVAILABLE = False


COMPACT_SIZE_MB = 1.0          # also compact if inbox exceeds this size
WAKE_ACK_GRACE_PERIOD_S = 30
WAKE_MAX_RETRIES = 3


def utc_now() -> str:
    return datetime.now(timezone.utc).isoformat(timespec="seconds")


def read_jsonl(path: Path) -> List[Dict[str, Any]]:
    if not path.exists():
        return []
    rows: List[Dict[str, Any]] = []
    quarantine: List[str] = []
    with path.open("r", encoding="utf-8") as f:
        for line in f:
            line = line.strip()
            if line:
                try:
                    rows.append(json.loads(line))
                except json.JSONDecodeError:
                    quarantine.append(line)
    if quarantine:
        qpath = path.with_suffix(".quarantine.jsonl")
        with qpath.open("a", encoding="utf-8", newline="\n") as f:
            for bad in quarantine:
                f.write(bad + "\n")
    return rows


def unread_for_session(inbox_path: Path, session_id: str) -> List[Dict[str, Any]]:
    rows = read_jsonl(inbox_path)
    return [
        r for r in rows
        if r.get("session_id") == session_id and not r.get("read_at")
    ]


def load_seen(state_path: Path) -> Dict[str, Any]:
    if not state_path.exists():
        return {"seen_ids": []}
    with state_path.open("r", encoding="utf-8") as f:
        return json.load(f)


def save_seen(state_path: Path, seen: Dict[str, Any]) -> None:
    state_path.parent.mkdir(parents=True, exist_ok=True)
    tmp = state_path.with_suffix(".tmp")
    with tmp.open("w", encoding="utf-8", newline="\n") as f:
        json.dump(seen, f, indent=2, sort_keys=True)
        f.write("\n")
    tmp.replace(state_path)


def _parse_dt(value: Optional[str]) -> Optional[datetime]:
    if not value:
        return None
    try:
        parsed = datetime.fromisoformat(value)
    except ValueError:
        return None
    if parsed.tzinfo is None:
        parsed = parsed.replace(tzinfo=timezone.utc)
    return parsed


def _message_by_id(inbox_path: Path, message_id: str) -> Optional[Dict[str, Any]]:
    for row in read_jsonl(inbox_path):
        if row.get("id") == message_id:
            return row
    return None


def _message_has_wake_receipt(inbox_path: Path, message_id: str) -> bool:
    row = _message_by_id(inbox_path, message_id)
    if not row:
        return True
    return bool(row.get("seen_at") or row.get("read_at"))


def _append_wake_audit(inbox_path: Path, event: Dict[str, Any]) -> None:
    audit_path = inbox_path.parent / "messages.jsonl"
    event.setdefault("id", str(uuid.uuid4()))
    event.setdefault("timestamp", utc_now())
    try:
        with audit_path.open("a", encoding="utf-8", newline="\n") as handle:
            handle.write(json.dumps(event, sort_keys=True))
            handle.write("\n")
    except OSError as exc:
        print(f"[agent-bridge] failed to write wake audit event: {exc}", flush=True)


def _save_watcher_state(state_path: Path, seen_ids: set, pending: List[Dict[str, Any]]) -> None:
    save_seen(
        state_path,
        {
            "seen_ids": list(seen_ids)[-500:],
            "pending_wake_verifications": pending[-200:],
        },
    )


def run_compaction(state_dir: Path, settings: BridgeSettings) -> None:
    if not _COMPACT_AVAILABLE:
        return
    try:
        from compact import AGENTS as _AGENTS
        for agent in _AGENTS:
            result = compact_inbox(
                state_dir,
                agent,
                max_age_days=settings.inbox_read_retention_days,
            )
            if result["read_dropped"] > 0:
                print(
                    f"[agent-bridge] compact inbox-{agent}.jsonl: "
                    f"dropped {result['read_dropped']} read rows, "
                    f"{result['unread_preserved']} unread preserved",
                    flush=True,
                )
        rotate_audit_log(state_dir)
        prune_audit_logs(state_dir, retention_days=settings.audit_log_retention_days)
    except Exception as exc:
        print(f"[agent-bridge] compaction error: {exc}", flush=True)


def notify_terminal(agent: str, session_id: str, messages: List[Dict[str, Any]]) -> None:
    count = len(messages)
    summary = messages[0].get("body", "")[:80].replace("\n", " ")
    print(
        f"[agent-bridge] {utc_now()} -- {count} unread for {agent} "
        f"(session ...{session_id[-8:]}): {summary!r}",
        flush=True,
    )


def _parse_message_fields(body: str) -> Dict[str, str]:
    """Extract TYPE, SUMMARY, STATUS, ACTION_REQUESTED from a structured message body."""
    fields: Dict[str, str] = {}
    for line in body.splitlines():
        if ":" in line:
            key, _, val = line.partition(":")
            key = key.strip().upper()
            if key in {"TYPE", "SUMMARY", "STATUS", "ACTION_REQUESTED"}:
                fields[key] = val.strip()
                if len(fields) == 4:
                    break
    return fields


def _xml_esc(s: str) -> str:
    return s.replace("&", "&amp;").replace("<", "&lt;").replace(">", "&gt;").replace('"', "&quot;")


def notify_windows_toast(
    agent: str,
    session_id: str,
    messages: List[Dict[str, Any]],
    *,
    toast_expiry_minutes: int = 5,
    toast_max_in_tray: int = 10,
) -> None:
    """Surface a modern Windows 10/11 toast notification via WinRT.

    Uses Windows.UI.Notifications.ToastNotification (built into Windows 10/11,
    no third-party dependency).  Parses TYPE/SUMMARY/STATUS from structured
    message bodies so the notification title and body are human-readable rather
    than raw protocol text.

    Falls back to a legacy NotifyIcon balloon on WinRT failure (e.g. older OS,
    notification policy, PowerShell unavailable).
    """
    if sys.platform != "win32":
        notify_terminal(agent, session_id, messages)
        return

    import base64

    msg = messages[0]
    fields = _parse_message_fields(msg.get("body", ""))
    from_agent = msg.get("from", "unknown")

    msg_type = fields.get("TYPE", "")
    summary = fields.get("SUMMARY", "")
    status = fields.get("STATUS", "info").lower()
    action = fields.get("ACTION_REQUESTED", "").lower()

    # Per-agent identity: emoji + sound
    # 🤖 = Claude  |  👾 = Codex
    AGENT_EMOJI = {"claude": "🤖", "codex": "👾"}
    AGENT_SOUND = {"claude": "Notification.Reminder", "codex": "Notification.IM"}
    recipient_emoji = AGENT_EMOJI.get(agent, "🤖")
    sender_emoji = AGENT_EMOJI.get(from_agent, "👾")
    sound_uri = AGENT_SOUND.get(agent, "Notification.Default")
    toast_group = f"agent-bridge-{agent}"
    toast_tag = str(msg.get("id") or uuid.uuid4()).replace("'", "")[:64]

    # Title: sender → recipient with per-agent robots
    title = f"{sender_emoji} {from_agent.capitalize()} → {recipient_emoji} {agent.capitalize()}"

    # Body line 1: [TYPE] Summary, or raw body preview
    if msg_type and summary:
        line1 = f"[{msg_type}] {summary}"
    elif summary:
        line1 = summary
    elif msg_type:
        line1 = f"[{msg_type}]"
    else:
        line1 = msg.get("body", "").replace("\n", " ")[:140]

    # Status prefix and truncate
    status_prefix = {"pass": "✅ ", "fail": "❌ ", "blocked": "\U0001f6ab "}.get(status, "")
    line1 = (status_prefix + line1)[:160]

    # Footer: action hint + session fingerprint
    session_tail = ("…" + session_id[-8:]) if session_id else ""
    if action and action not in {"none", ""}:
        line2 = f"⚡ {action}  ·  agent-bridge {session_tail}"
    else:
        line2 = f"agent-bridge  ·  {session_tail}"

    # Build toast XML (ToastGeneric: three text nodes = title / body / footer)
    toast_xml = (
        f'<toast><audio src="ms-winsoundevent:{sound_uri}"/>'
        '<visual><binding template="ToastGeneric">'
        f"<text>{_xml_esc(title)}</text>"
        f"<text>{_xml_esc(line1)}</text>"
        f"<text>{_xml_esc(line2)}</text>"
        "</binding></visual></toast>"
    )

    # Use the registered Windows PowerShell app-id so Windows always allows the
    # toast without requiring our own app identity registration.
    ps_app_id = r"{1AC14E77-02E7-4E5D-B744-2EB1AE5198B7}\WindowsPowerShell\v1.0\powershell.exe"

    # Pass the script via -EncodedCommand (Base64 UTF-16LE) to avoid all
    # shell-escaping issues with the XML payload.
    ps_code = (
        "[Windows.UI.Notifications.ToastNotificationManager,"
        " Windows.UI.Notifications, ContentType=WindowsRuntime] | Out-Null\n"
        "[Windows.Data.Xml.Dom.XmlDocument,"
        " Windows.Data.Xml.Dom.XmlDocument, ContentType=WindowsRuntime] | Out-Null\n"
        f"$appId = '{ps_app_id}'\n"
        "$xmlStr = @'\n"
        + toast_xml + "\n"
        "'@\n"
        "$xml = New-Object Windows.Data.Xml.Dom.XmlDocument\n"
        "$xml.LoadXml($xmlStr.Trim())\n"
        "$toast = [Windows.UI.Notifications.ToastNotification]::new($xml)\n"
        f"$toast.ExpirationTime = [DateTimeOffset]::Now.AddMinutes({int(toast_expiry_minutes)})\n"
        f"$toast.Tag = '{toast_tag}'\n"
        f"$toast.Group = '{toast_group}'\n"
        f"$toastMaxInTray = {int(toast_max_in_tray)}\n"
        "if ($toastMaxInTray -gt 0) {\n"
        "  try {\n"
        "    $history = [Windows.UI.Notifications.ToastNotificationManager]::History\n"
        "    $existing = @($history.GetHistory($appId) | Where-Object { $_.Group -eq $toast.Group })\n"
        "    $overflow = $existing.Count - ($toastMaxInTray - 1)\n"
        "    if ($overflow -gt 0) {\n"
        "      $existing | Sort-Object -Property ExpirationTime | Select-Object -First $overflow | ForEach-Object {\n"
        "        if ($_.Tag) { $history.Remove($_.Tag, $_.Group, $appId) }\n"
        "      }\n"
        "    }\n"
        "  } catch {}\n"
        "}\n"
        "[Windows.UI.Notifications.ToastNotificationManager]::CreateToastNotifier($appId).Show($toast)\n"
    )
    try:
        ps_b64 = base64.b64encode(ps_code.encode("utf-16-le")).decode("ascii")
        subprocess.Popen(
            ["powershell", "-NoProfile", "-WindowStyle", "Hidden", "-EncodedCommand", ps_b64],
            stdout=subprocess.DEVNULL,
            stderr=subprocess.DEVNULL,
            creationflags=getattr(subprocess, "CREATE_NO_WINDOW", 0),
        )
    except OSError:
        _notify_windows_balloon(agent, session_id, messages)


def _notify_windows_balloon(agent: str, session_id: str, messages: List[Dict[str, Any]]) -> None:
    """Legacy NotifyIcon balloon fallback (Windows 7+ compatible)."""
    body = messages[0].get("body", "")[:120].replace("\n", " ").replace("\r", " ")
    title = f"agent-bridge: new message for {agent}"
    safe_title = title.replace("'", "''")
    safe_body = body.replace("'", "''")
    ps_script = (
        "Add-Type -AssemblyName System.Windows.Forms; "
        "$b = New-Object System.Windows.Forms.NotifyIcon; "
        "$b.Icon = [System.Drawing.SystemIcons]::Information; "
        f"$b.BalloonTipTitle = '{safe_title}'; "
        f"$b.BalloonTipText = '{safe_body}'; "
        "$b.Visible = $true; "
        "$b.ShowBalloonTip(5000); "
        "Start-Sleep -Seconds 6; "
        "$b.Dispose()"
    )
    try:
        subprocess.Popen(
            ["powershell", "-NoProfile", "-WindowStyle", "Hidden", "-Command", ps_script],
            stdout=subprocess.DEVNULL,
            stderr=subprocess.DEVNULL,
            creationflags=getattr(subprocess, "CREATE_NO_WINDOW", 0),
        )
    except OSError:
        notify_terminal(agent, session_id, messages)


def run_command_for_session(cmd: str, agent: str, session_id: str, messages: List[Dict[str, Any]], inbox_path: Path) -> bool:
    import subprocess

    if not messages:
        return False
    if "consume_inbox.py" in cmd:
        print(
            f"[agent-bridge] refusing destructive on_message_command for {agent} session=...{(session_id or '')[-8:]}",
            flush=True,
        )
        return True
    first = messages[0]
    env = {**__import__("os").environ}
    env["BRIDGE_AGENT"] = agent
    env["BRIDGE_SESSION"] = session_id
    env["BRIDGE_MESSAGE_ID"] = str(first.get("id", ""))
    env["BRIDGE_MESSAGE_FROM"] = str(first.get("from", ""))
    env["BRIDGE_MESSAGE_TYPE"] = str(first.get("control_type", ""))
    env["BRIDGE_MARKER_VARIANT"] = str(first.get("marker_variant", ""))
    env["BRIDGE_INBOX"] = str(inbox_path)
    env["BRIDGE_BODY"] = str(first.get("body", ""))
    env["BRIDGE_MESSAGE_COUNT"] = str(len(messages))
    env["BRIDGE_MESSAGE_IDS"] = json.dumps([msg.get("id") for msg in messages])
    try:
        proc = subprocess.run(
            cmd,
            shell=True,
            env=env,
            timeout=90,
            stdout=subprocess.PIPE,
            stderr=subprocess.PIPE,
            text=True,
        )
        if proc.stdout:
            print(proc.stdout.rstrip(), flush=True)
        if proc.stderr:
            print(proc.stderr.rstrip(), flush=True)
        if proc.returncode != 0:
            print(
                f"[agent-bridge] on_message_command exited {proc.returncode}; message remains retryable",
                flush=True,
            )
            return False
        return True
    except subprocess.TimeoutExpired:
        print("[agent-bridge] on_message_command timed out; message remains retryable", flush=True)
        return False
    except Exception as exc:
        print(f"[agent-bridge] on_message_command failed: {exc}", flush=True)
        return False


def _process_pending_wake_verifications(
    session_config: Dict[str, Any],
    *,
    pending: List[Dict[str, Any]],
    seen_ids: set,
    state_path: Path,
    toasts_enabled: bool,
    grace_period_seconds: int,
    max_retries: int,
) -> List[Dict[str, Any]]:
    """Promote pending wake attempts to seen only after a receipt appears."""
    agent = session_config["agent"]
    session_id = session_config["session_id"]
    inbox_path = Path(session_config["inbox"])
    on_message_command: Optional[str] = session_config.get("on_message_command")
    now = datetime.now(timezone.utc)
    kept: List[Dict[str, Any]] = []
    changed = False

    for entry in pending:
        if entry.get("agent") != agent or entry.get("session_id") != session_id:
            kept.append(entry)
            continue

        message_id = str(entry.get("message_id", ""))
        if not message_id:
            changed = True
            continue

        if _message_has_wake_receipt(inbox_path, message_id):
            seen_ids.add(message_id)
            changed = True
            print(
                f"[agent-bridge] wake receipt verified for {agent} id={message_id} session=...{session_id[-8:]}",
                flush=True,
            )
            continue

        sent_at = _parse_dt(entry.get("sent_at"))
        if sent_at and (now - sent_at).total_seconds() < grace_period_seconds:
            kept.append(entry)
            continue

        retry_count = int(entry.get("retry_count") or 0)
        if retry_count >= max_retries:
            seen_ids.add(message_id)
            changed = True
            event = {
                "action": "wake_delivery_failed",
                "agent": agent,
                "session_id": session_id,
                "message_id": message_id,
                "retry_count": retry_count,
                "reason": "seen_at_not_observed_after_wake_retries",
            }
            _append_wake_audit(inbox_path, event)
            notify_terminal(agent, session_id, [_message_by_id(inbox_path, message_id) or {"id": message_id, "body": "wake delivery failed"}])
            print(
                f"[agent-bridge] wake delivery failed for {agent} id={message_id} after {retry_count} retries",
                flush=True,
            )
            continue

        row = _message_by_id(inbox_path, message_id)
        if not row or row.get("read_at"):
            seen_ids.add(message_id)
            changed = True
            continue

        if on_message_command:
            command_ok = run_command_for_session(on_message_command, agent, session_id, [row], inbox_path)
        else:
            command_ok = False
        retry_count += 1
        entry["retry_count"] = retry_count
        entry["sent_at"] = utc_now()
        entry["last_retry_ok"] = command_ok
        kept.append(entry)
        changed = True
        print(
            f"[agent-bridge] wake receipt pending for {agent} id={message_id}; retry {retry_count}/{max_retries}",
            flush=True,
        )

    if changed:
        _save_watcher_state(state_path, seen_ids, kept)
    return kept


def _queue_pending_wake_verifications(
    *,
    pending: List[Dict[str, Any]],
    agent: str,
    session_id: str,
    inbox_path: Path,
    messages: List[Dict[str, Any]],
    seen_ids: set,
    state_path: Path,
) -> List[Dict[str, Any]]:
    existing_ids = {entry.get("message_id") for entry in pending}
    now = utc_now()
    changed = False
    for message in messages:
        message_id = str(message.get("id", ""))
        if not message_id or message_id in existing_ids:
            continue
        if _message_has_wake_receipt(inbox_path, message_id):
            seen_ids.add(message_id)
            changed = True
            continue
        pending.append(
            {
                "message_id": message_id,
                "agent": agent,
                "session_id": session_id,
                "inbox": str(inbox_path),
                "sent_at": now,
                "retry_count": 0,
            }
        )
        existing_ids.add(message_id)
        changed = True
    if changed:
        _save_watcher_state(state_path, seen_ids, pending)
    return pending


def process_session_once(
    session_config: Dict[str, Any],
    *,
    seen_ids: set,
    state_path: Path,
    toasts_enabled: bool,
    toast_expiry_minutes: int = 5,
    toast_max_in_tray: int = 10,
    grace_period_seconds: int = WAKE_ACK_GRACE_PERIOD_S,
    max_retries: int = WAKE_MAX_RETRIES,
) -> List[str]:
    """Process one configured watcher session once.

    Wake spawn is not delivery: ids are recorded in watcher-state only after
    notification succeeds and any wake command exits cleanly. Failed wake
    commands leave messages retryable for the next poll.
    """
    agent = session_config["agent"]
    session_id = session_config["session_id"]
    inbox_path = Path(session_config["inbox"])
    on_message = session_config.get("on_message", "notify")
    on_message_command: Optional[str] = session_config.get("on_message_command")

    effective_on_message = on_message
    if not toasts_enabled and on_message == "toast":
        effective_on_message = "log"

    state = load_seen(state_path)
    pending: List[Dict[str, Any]] = list(state.get("pending_wake_verifications", []))
    pending = _process_pending_wake_verifications(
        session_config,
        pending=pending,
        seen_ids=seen_ids,
        state_path=state_path,
        toasts_enabled=toasts_enabled,
        grace_period_seconds=grace_period_seconds,
        max_retries=max_retries,
    )
    pending_ids = {
        entry.get("message_id")
        for entry in pending
        if entry.get("agent") == agent and entry.get("session_id") == session_id
    }

    unread = unread_for_session(inbox_path, session_id)
    new_msgs = [m for m in unread if m.get("id") not in seen_ids and m.get("id") not in pending_ids]
    if not new_msgs:
        return []

    if effective_on_message == "log":
        for m in new_msgs:
            print(
                f"[agent-bridge] {utc_now()} -- new {agent} message id={m.get('id')} session=...{(session_id or '')[-8:]}",
                flush=True,
            )
        for m in new_msgs:
            seen_ids.add(m["id"])
        _save_watcher_state(state_path, seen_ids, pending)
        return [m["id"] for m in new_msgs]

    if effective_on_message == "toast":
        notify_windows_toast(
            agent,
            session_id,
            new_msgs,
            toast_expiry_minutes=toast_expiry_minutes,
            toast_max_in_tray=toast_max_in_tray,
        )
    else:
        notify_terminal(agent, session_id, new_msgs)

    command_ok = False
    if on_message_command:
        command_ok = run_command_for_session(on_message_command, agent, session_id, new_msgs, inbox_path)

    if on_message_command and command_ok:
        _queue_pending_wake_verifications(
            pending=pending,
            agent=agent,
            session_id=session_id,
            inbox_path=inbox_path,
            messages=new_msgs,
            seen_ids=seen_ids,
            state_path=state_path,
        )
        return []

    if not on_message_command:
        for m in new_msgs:
            seen_ids.add(m["id"])
        _save_watcher_state(state_path, seen_ids, pending)
        return [m["id"] for m in new_msgs]

    return []


def _load_config(config_path: Path) -> Dict[str, Any]:
    with config_path.open("r", encoding="utf-8") as f:
        return json.load(f)


def _effective_toasts_enabled(config: Dict[str, Any], settings: BridgeSettings, settings_path: Path) -> bool:
    if settings_path.exists():
        return settings.toasts_enabled
    return bool(config.get("toasts_enabled", settings.toasts_enabled))


def watch(
    config_path: Path,
    stop_event: Optional[threading.Event] = None,
    heartbeat: Optional[Callable[[], None]] = None,
) -> None:
    config = _load_config(config_path)
    config_mtime = config_path.stat().st_mtime

    sessions = config.get("sessions", [])
    if not sessions:
        print("[agent-bridge] No sessions configured. Exiting.", flush=True)
        return

    # Per-session seen-ID tracking stored alongside watcher config
    state_path = config_path.parent / "watcher-state.json"
    seen_data = load_seen(state_path)
    seen_ids: set = set(seen_data.get("seen_ids", []))

    # Derive state_dir from first inbox path (parent directory)
    state_dir = Path(sessions[0]["inbox"]).parent if sessions else None

    settings_path = settings_path_for_state_dir(state_dir) if state_dir else config_path.parent / "settings.json"
    settings = load_settings(state_dir or config_path.parent / "state", settings_path=settings_path)
    settings_mtime = settings_path.stat().st_mtime if settings_path.exists() else None

    toasts_enabled = _effective_toasts_enabled(config, settings, settings_path)
    print(
        f"[agent-bridge] Watcher started. Watching {len(sessions)} session(s). "
        f"Poll every {settings.poll_interval_seconds}s. Toasts: {'on' if toasts_enabled else 'off'}.",
        flush=True,
    )
    for s in sessions:
        print(f"  agent={s['agent']}  session=...{s['session_id'][-8:]}  inbox={s['inbox']}", flush=True)

    # Compact on startup
    if state_dir:
        run_compaction(state_dir, settings)

    last_compact = time.monotonic()

    try:
        while not (stop_event and stop_event.is_set()):
            if heartbeat:
                heartbeat()
            # Hot-reload config when the file changes.
            try:
                mtime = config_path.stat().st_mtime
                if mtime != config_mtime:
                    new_config = _load_config(config_path)
                    sessions = new_config.get("sessions", sessions)
                    config = new_config
                    config_mtime = mtime
            except Exception:
                pass  # keep running on transient read errors
            try:
                new_settings_mtime = settings_path.stat().st_mtime if settings_path.exists() else None
                if new_settings_mtime != settings_mtime:
                    settings = load_settings(state_dir or config_path.parent / "state", settings_path=settings_path)
                    settings_mtime = new_settings_mtime
                    print("[agent-bridge] settings.json reloaded", flush=True)
                new_toasts = _effective_toasts_enabled(config, settings, settings_path)
                if new_toasts != toasts_enabled:
                    print(f"[agent-bridge] toasts_enabled changed: {'on' if new_toasts else 'off'}", flush=True)
                    toasts_enabled = new_toasts
            except Exception as exc:
                print(f"[agent-bridge] settings reload error: {exc}", flush=True)

            # Periodic and size-triggered compaction
            if state_dir:
                now = time.monotonic()
                time_due = (now - last_compact) >= (settings.compact_interval_hours * 3600)
                size_due = _COMPACT_AVAILABLE and any(
                    should_compact(state_dir, s["agent"], COMPACT_SIZE_MB)
                    for s in sessions
                )
                if time_due or size_due:
                    run_compaction(state_dir, settings)
                    last_compact = now

            for s in sessions:
                process_session_once(
                    s,
                    seen_ids=seen_ids,
                    state_path=state_path,
                    toasts_enabled=toasts_enabled,
                    toast_expiry_minutes=settings.toast_expiry_minutes,
                    toast_max_in_tray=settings.toast_max_in_tray,
                )

                    # (the consumer would mark messages read silently — destructive).
            time.sleep(settings.poll_interval_seconds)

    except KeyboardInterrupt:
        print("\n[agent-bridge] Watcher stopped.", flush=True)

    print("[agent-bridge] Watcher exiting.", flush=True)


def _write_pid(pid_path: Path) -> None:
    pid_path.write_text(str(os.getpid()), encoding="utf-8")


def _kill_stale(pid_path: Path) -> None:
    """Kill any existing watcher process recorded in pid_path."""
    if not pid_path.exists():
        return
    try:
        old_pid = int(pid_path.read_text(encoding="utf-8").strip())
    except (ValueError, OSError):
        pid_path.unlink(missing_ok=True)
        return
    if old_pid == os.getpid():
        return
    try:
        if sys.platform == "win32":
            import ctypes
            PROCESS_TERMINATE = 0x0001
            handle = ctypes.windll.kernel32.OpenProcess(PROCESS_TERMINATE, False, old_pid)
            if handle:
                ctypes.windll.kernel32.TerminateProcess(handle, 0)
                ctypes.windll.kernel32.CloseHandle(handle)
                print(f"[agent-bridge] Killed stale watcher PID {old_pid}.", flush=True)
        else:
            import signal as _signal
            os.kill(old_pid, _signal.SIGTERM)
            print(f"[agent-bridge] Sent SIGTERM to stale watcher PID {old_pid}.", flush=True)
    except (ProcessLookupError, OSError):
        pass  # already gone
    pid_path.unlink(missing_ok=True)


def main() -> None:
    import signal
    import atexit

    parser = argparse.ArgumentParser(description="agent-bridge inbox watcher daemon")
    parser.add_argument(
        "--config",
        required=True,
        help="Path to watcher config JSON (see watcher-config.example.json)",
    )
    args = parser.parse_args()
    config_path = Path(args.config)
    if not config_path.exists():
        print(f"[agent-bridge] Config not found: {config_path}", file=sys.stderr)
        sys.exit(1)

    config = _load_config(config_path)
    sessions = config.get("sessions", [])
    state_dir = Path(sessions[0]["inbox"]).parent if sessions else config_path.parent / "state"
    command = [sys.executable, str(Path(__file__).resolve()), "--config", str(config_path)]
    lease_path = state_dir / "locks" / "watcher.lock"
    acquired = acquire_singleton_lease(
        lease_path,
        role="watcher",
        command=command,
        state_dir=state_dir,
        pid=os.getpid(),
    )
    if not acquired.get("acquired") and acquired.get("pid") != os.getpid():
        print(
            f"[agent-bridge] Watcher already running pid={acquired.get('pid')} hash={command_line_hash(command)}. Exiting.",
            flush=True,
        )
        return
    lease = acquired.get("lease", {})
    generation = lease.get("generation")

    # Compatibility PID marker next to the config.
    pid_path = config_path.parent / "watcher.pid"
    _write_pid(pid_path)

    def _cleanup() -> None:
        try:
            if pid_path.exists() and pid_path.read_text(encoding="utf-8").strip() == str(os.getpid()):
                pid_path.unlink(missing_ok=True)
        except OSError:
            pass
        if generation:
            release_lease(lease_path, os.getpid(), generation)

    atexit.register(_cleanup)

    _stop = threading.Event()

    def _handle_signal(signum, frame):  # noqa: ANN001
        print(f"\n[agent-bridge] Watcher received signal {signum}, shutting down.", flush=True)
        _stop.set()

    signal.signal(signal.SIGTERM, _handle_signal)
    signal.signal(signal.SIGINT, _handle_signal)

    def _heartbeat() -> None:
        if generation:
            heartbeat_lease(lease_path, os.getpid(), generation)

    watch(config_path, stop_event=_stop, heartbeat=_heartbeat)
    _cleanup()


if __name__ == "__main__":
    main()
