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
import shlex
import string
import subprocess
import sys
import threading
import time
import uuid
from datetime import datetime, timedelta, timezone
from pathlib import Path
from typing import Any, Callable, Dict, List, Optional, Sequence, Union

from agent_bridge import AgentBridge
from core.processes import acquire_singleton_lease, command_line_hash, heartbeat_lease, release_lease
from core.runtime import (
    build_runtime_breadcrumb,
    normalize_peer_runtime_breadcrumb,
    peer_runtime_path_for_state_dir,
    read_runtime_breadcrumb,
    write_runtime_breadcrumb,
)
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
WAKE_PERMANENT_EXIT_CODES = {3, 11}
WAKE_PREFIRE_LIMIT = 2
WAKE_PREFIRE_WINDOW_S = 10
WAKE_PREFIRE_DEFER_S = 10
WAKE_BREAKER_THRESHOLD = 5
WAKE_BREAKER_WINDOW_S = 5 * 60
WAKE_BREAKER_IDLE_CLOSE_S = 15 * 60
WAKE_BREAKER_SCHEMA_VERSION = 1
LEGACY_COMMAND_FORBIDDEN_CHARS = {"&", "|", ";", ">", "<", "`"}


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
        return {
            "seen_ids": [],
            "pending_wake_verifications": [],
            "paused_wake_messages": [],
            "unknown_origin_warnings": [],
            "wake_fire_history": [],
        }
    with state_path.open("r", encoding="utf-8") as f:
        data = json.load(f)
    data.setdefault("seen_ids", [])
    data.setdefault("pending_wake_verifications", [])
    data.setdefault("paused_wake_messages", [])
    data.setdefault("unknown_origin_warnings", [])
    data.setdefault("wake_fire_history", [])
    return data


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


def _watcher_state_dir(state_path: Path) -> Path:
    return state_path.parent / "state"


def _wake_breaker_path(state_path: Path) -> Path:
    return _watcher_state_dir(state_path) / "wake-failure-windows.json"


def _load_wake_breakers(state_path: Path) -> Dict[str, Any]:
    path = _wake_breaker_path(state_path)
    if not path.exists():
        return {"schema_version": WAKE_BREAKER_SCHEMA_VERSION, "sessions": {}, "updated_at": utc_now()}
    try:
        with path.open("r", encoding="utf-8") as handle:
            data = json.load(handle)
    except (OSError, json.JSONDecodeError):
        return {"schema_version": WAKE_BREAKER_SCHEMA_VERSION, "sessions": {}, "updated_at": utc_now()}
    data.setdefault("schema_version", WAKE_BREAKER_SCHEMA_VERSION)
    data.setdefault("sessions", {})
    data.setdefault("updated_at", utc_now())
    return data


def _save_wake_breakers(state_path: Path, payload: Dict[str, Any]) -> None:
    path = _wake_breaker_path(state_path)
    path.parent.mkdir(parents=True, exist_ok=True)
    tmp = path.with_suffix(".tmp")
    payload["schema_version"] = WAKE_BREAKER_SCHEMA_VERSION
    payload["updated_at"] = utc_now()
    with tmp.open("w", encoding="utf-8", newline="\n") as handle:
        json.dump(payload, handle, indent=2, sort_keys=True)
        handle.write("\n")
    tmp.replace(path)


def _normalize_breaker_session(record: Optional[Dict[str, Any]]) -> Dict[str, Any]:
    data = dict(record or {})
    data.setdefault("failures", [])
    data.setdefault("breaker_state", "closed")
    data.setdefault("opened_at", None)
    data.setdefault("last_failure_at", None)
    data.setdefault("last_success_at", None)
    data.setdefault("exit_code_distribution", {})
    data.setdefault("notified_open_at", None)
    return data


def _breaker_prune_failures(record: Dict[str, Any], now: datetime) -> Dict[str, Any]:
    failures = []
    for item in list(record.get("failures", [])):
        failure_at = _parse_dt(item.get("at"))
        if failure_at is None:
            continue
        if (now - failure_at).total_seconds() <= WAKE_BREAKER_WINDOW_S:
            failures.append({"at": failure_at.isoformat(timespec="seconds"), "code": str(item.get("code") or "unknown")})
    record["failures"] = failures
    distribution: Dict[str, int] = {}
    for item in failures:
        code = str(item.get("code") or "unknown")
        distribution[code] = distribution.get(code, 0) + 1
    record["exit_code_distribution"] = distribution
    return record


def _close_breaker(
    *,
    state_path: Path,
    session_id: str,
    reason: str,
    inbox_path: Optional[Path] = None,
) -> None:
    payload = _load_wake_breakers(state_path)
    sessions = payload.setdefault("sessions", {})
    record = sessions.get(session_id)
    if not record:
        return
    normalized = _normalize_breaker_session(record)
    if normalized.get("breaker_state") == "open":
        if inbox_path is not None:
            _append_wake_audit(
                inbox_path,
                {
                    "action": "wake_breaker_closed",
                    "session_id": session_id,
                    "reason": reason,
                },
            )
    sessions.pop(session_id, None)
    _save_wake_breakers(state_path, payload)


def _breaker_is_open(
    *,
    state_path: Path,
    session_id: str,
    inbox_path: Optional[Path] = None,
) -> bool:
    payload = _load_wake_breakers(state_path)
    record = payload.get("sessions", {}).get(session_id)
    if not record:
        return False
    normalized = _normalize_breaker_session(record)
    last_failure = _parse_dt(normalized.get("last_failure_at"))
    now = datetime.now(timezone.utc)
    if (
        normalized.get("breaker_state") == "open"
        and last_failure is not None
        and (now - last_failure).total_seconds() >= WAKE_BREAKER_IDLE_CLOSE_S
    ):
        _close_breaker(state_path=state_path, session_id=session_id, reason="idle", inbox_path=inbox_path)
        return False
    return normalized.get("breaker_state") == "open"


def _record_wake_failure(
    *,
    state_path: Path,
    agent: str,
    session_id: str,
    code: str,
    inbox_path: Path,
) -> bool:
    payload = _load_wake_breakers(state_path)
    sessions = payload.setdefault("sessions", {})
    record = _normalize_breaker_session(sessions.get(session_id))
    now = datetime.now(timezone.utc)
    record = _breaker_prune_failures(record, now)
    record["failures"].append({"at": now.isoformat(timespec="seconds"), "code": code})
    record["last_failure_at"] = now.isoformat(timespec="seconds")
    record = _breaker_prune_failures(record, now)
    was_open = record.get("breaker_state") == "open"
    opened = False
    if len(record["failures"]) >= WAKE_BREAKER_THRESHOLD:
        record["breaker_state"] = "open"
        if not was_open:
            record["opened_at"] = now.isoformat(timespec="seconds")
            record["notified_open_at"] = now.isoformat(timespec="seconds")
            _append_wake_audit(
                inbox_path,
                {
                    "action": "wake_breaker_open",
                    "session_id": session_id,
                    "threshold": WAKE_BREAKER_THRESHOLD,
                    "consecutive_failures": len(record["failures"]),
                    "exit_code_distribution": dict(record.get("exit_code_distribution", {})),
                    "opened_at": record["opened_at"],
                },
            )
            notify_terminal(
                agent,
                session_id,
                [{"id": "wake-breaker-open", "body": "Wake suppressed after repeated failures; investigate or resume_wake_for_session."}],
            )
            opened = True
    sessions[session_id] = record
    _save_wake_breakers(state_path, payload)
    return opened or was_open


def _record_wake_success(*, state_path: Path, session_id: str, inbox_path: Optional[Path] = None) -> None:
    _close_breaker(state_path=state_path, session_id=session_id, reason="success", inbox_path=inbox_path)


def _rate_limit_fire_history(
    *,
    seen_ids: set,
    pending: List[Dict[str, Any]],
    paused_messages: List[Dict[str, Any]],
    unknown_origin_warnings: List[str],
    wake_fire_history: List[Dict[str, Any]],
    state_path: Path,
) -> List[Dict[str, Any]]:
    now = datetime.now(timezone.utc)
    kept = []
    for item in wake_fire_history:
        fired_at = _parse_dt(item.get("at"))
        if fired_at is None:
            continue
        if (now - fired_at).total_seconds() <= WAKE_PREFIRE_WINDOW_S:
            kept.append({"session_id": str(item.get("session_id") or ""), "at": fired_at.isoformat(timespec="seconds")})
    if len(kept) != len(wake_fire_history):
        _save_watcher_state(
            state_path,
            seen_ids,
            pending,
            paused_messages,
            unknown_origin_warnings,
            kept,
        )
    return kept


def _wake_rate_limited(
    *,
    session_id: str,
    wake_fire_history: List[Dict[str, Any]],
    seen_ids: set,
    pending: List[Dict[str, Any]],
    paused_messages: List[Dict[str, Any]],
    unknown_origin_warnings: List[str],
    state_path: Path,
) -> bool:
    kept = _rate_limit_fire_history(
        seen_ids=seen_ids,
        pending=pending,
        paused_messages=paused_messages,
        unknown_origin_warnings=unknown_origin_warnings,
        wake_fire_history=wake_fire_history,
        state_path=state_path,
    )
    return sum(1 for item in kept if item.get("session_id") == session_id) >= WAKE_PREFIRE_LIMIT


def _record_wake_fire(
    *,
    session_id: str,
    seen_ids: set,
    pending: List[Dict[str, Any]],
    paused_messages: List[Dict[str, Any]],
    unknown_origin_warnings: List[str],
    wake_fire_history: List[Dict[str, Any]],
    state_path: Path,
) -> List[Dict[str, Any]]:
    kept = _rate_limit_fire_history(
        seen_ids=seen_ids,
        pending=pending,
        paused_messages=paused_messages,
        unknown_origin_warnings=unknown_origin_warnings,
        wake_fire_history=wake_fire_history,
        state_path=state_path,
    )
    kept.append({"session_id": session_id, "at": utc_now()})
    _save_watcher_state(
        state_path,
        seen_ids,
        pending,
        paused_messages,
        unknown_origin_warnings,
        kept,
    )
    return kept


def _save_watcher_state(
    state_path: Path,
    seen_ids: set,
    pending: List[Dict[str, Any]],
    paused_messages: Optional[List[Dict[str, Any]]] = None,
    unknown_origin_warnings: Optional[List[str]] = None,
    wake_fire_history: Optional[List[Dict[str, Any]]] = None,
) -> None:
    save_seen(
        state_path,
        {
            "seen_ids": list(seen_ids)[-500:],
            "pending_wake_verifications": pending[-200:],
            "paused_wake_messages": (paused_messages or [])[-200:],
            "unknown_origin_warnings": (unknown_origin_warnings or [])[-200:],
            "wake_fire_history": (wake_fire_history or [])[-200:],
        },
    )


def _bridge_is_paused(state_dir: Path) -> bool:
    state_path = state_dir / "state.json"
    if not state_path.exists():
        return False
    try:
        with state_path.open("r", encoding="utf-8") as handle:
            data = json.load(handle)
    except (OSError, json.JSONDecodeError):
        return False
    return bool(data.get("paused"))


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


def _safe_toast_tag(value: Any) -> str:
    tag = "".join(ch for ch in str(value or "") if ch.isalnum() or ch in {"-", "_", ".", ":"})[:64]
    return tag or str(uuid.uuid4())


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
    toast_tag = _safe_toast_tag(msg.get("id") or uuid.uuid4())

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


def _permanent_wake_event(
    *,
    agent: str,
    session_id: str,
    message_id: str,
    returncode: int,
    result: Dict[str, Any],
) -> Dict[str, Any]:
    reason = str(result.get("reason") or "")
    action = "wake_command_failed_permanently"
    event_reason = reason or "permanent_wake_failure"
    if reason == "no_peer_breadcrumb":
        action = "wake_skipped_no_peer"
    elif reason == "config_error":
        action = "wake_skipped_config_error"
    elif returncode == 3:
        action = "wake_skipped_wrong_chat"
        event_reason = "wrong_chat_detected"
    elif returncode == 11:
        action = "wake_skipped_bad_provenance"
        event_reason = reason or "bad_provenance"
    elif returncode == 2 and not reason:
        action = "wake_skipped_config_error"
        event_reason = "config_error"
    event = {
        "action": action,
        "agent": agent,
        "session_id": session_id,
        "message_id": message_id,
        "returncode": returncode,
        "reason": event_reason,
    }
    stdout = str(result.get("stdout") or "").strip()
    stderr = str(result.get("stderr") or "").strip()
    if stdout:
        event["stdout"] = stdout[-500:]
    if stderr:
        event["stderr"] = stderr[-500:]
    return event


def _mark_permanent_wake_failure(
    *,
    agent: str,
    session_id: str,
    inbox_path: Path,
    message_id: str,
    seen_ids: set,
    result: Dict[str, Any],
    state_path: Optional[Path] = None,
) -> None:
    returncode = int(result.get("returncode") or 0)
    seen_ids.add(message_id)
    if state_path is not None:
        _record_wake_failure(
            state_path=state_path,
            agent=agent,
            session_id=session_id,
            code=str(returncode or result.get("reason") or "unknown"),
            inbox_path=inbox_path,
        )
    _append_wake_audit(
        inbox_path,
        _permanent_wake_event(
            agent=agent,
            session_id=session_id,
            message_id=message_id,
            returncode=returncode,
            result=result,
        ),
    )
    print(
        f"[agent-bridge] permanent wake failure for {agent} id={message_id} session=...{session_id[-8:]} rc={returncode}; suppressing retries",
        flush=True,
    )


def _template_required_fields(template: Union[str, Sequence[str]]) -> set[str]:
    formatter = string.Formatter()
    values = [template] if isinstance(template, str) else list(template)
    required: set[str] = set()
    for value in values:
        for _, field_name, _, _ in formatter.parse(str(value)):
            if field_name:
                required.add(field_name)
    return required


def _format_template(template: Union[str, Sequence[str]], mapping: Dict[str, str]) -> Union[str, List[str]]:
    if isinstance(template, str):
        return template.format_map(mapping)
    return [str(value).format_map(mapping) for value in template]


def _legacy_command_to_argv(command: str) -> Dict[str, Any]:
    raw = str(command or "").strip()
    if not raw:
        return {
            "ok": False,
            "result": {
                "ok": False,
                "returncode": 2,
                "retryable": False,
                "stdout": "",
                "stderr": "legacy on_message_command is empty",
                "reason": "config_error",
            },
        }
    forbidden = sorted({ch for ch in raw if ch in LEGACY_COMMAND_FORBIDDEN_CHARS})
    if forbidden:
        return {
            "ok": False,
            "result": {
                "ok": False,
                "returncode": 2,
                "retryable": False,
                "stdout": "",
                "stderr": "legacy on_message_command contains forbidden shell metacharacter(s): %s" % "".join(forbidden),
                "reason": "config_error",
            },
        }
    try:
        argv = shlex.split(raw, posix=False)
    except ValueError as exc:
        return {
            "ok": False,
            "result": {
                "ok": False,
                "returncode": 2,
                "retryable": False,
                "stdout": "",
                "stderr": "legacy on_message_command could not be parsed safely: %s" % exc,
                "reason": "config_error",
            },
        }
    if not argv:
        return {
            "ok": False,
            "result": {
                "ok": False,
                "returncode": 2,
                "retryable": False,
                "stdout": "",
                "stderr": "legacy on_message_command produced empty argv",
                "reason": "config_error",
            },
        }
    return {"ok": True, "command": argv}


def _resolve_command_template(session_config: Dict[str, Any], inbox_path: Path) -> Dict[str, Any]:
    template = session_config.get("on_message_command_template")
    if not template:
        command = session_config.get("on_message_command")
        if not command:
            return {"ok": False, "command": None}
        return _legacy_command_to_argv(str(command))

    agent = str(session_config.get("agent") or "")
    state_dir = inbox_path.parent
    peer_path = peer_runtime_path_for_state_dir(state_dir, agent)
    peer = normalize_peer_runtime_breadcrumb(read_runtime_breadcrumb(peer_path))
    if not peer or peer.get("unreadable"):
        return {
            "ok": False,
            "command": None,
            "result": {
                "ok": False,
                "returncode": 2,
                "retryable": False,
                "stdout": "",
                "stderr": f"peer runtime breadcrumb unavailable: {peer_path}",
                "reason": "no_peer_breadcrumb",
            },
        }

    bootstrap_origin = str(peer.get("bootstrap_origin") or "unknown").strip().lower()
    if bootstrap_origin == "subagent":
        return {
            "ok": False,
            "command": None,
            "peer": peer,
            "result": {
                "ok": False,
                "returncode": 11,
                "retryable": False,
                "stdout": "",
                "stderr": f"peer runtime breadcrumb has subagent provenance: {peer_path}",
                "reason": "bad_provenance_subagent",
            },
        }
    if agent == "codex" and bootstrap_origin != "parent":
        return {
            "ok": False,
            "command": None,
            "peer": peer,
            "result": {
                "ok": False,
                "returncode": 11,
                "retryable": False,
                "stdout": "",
                "stderr": f"peer runtime breadcrumb lacks trusted parent provenance: {peer_path}",
                "reason": "bad_provenance_unknown",
            },
        }

    desktop_thread_id = str(peer.get("desktop_thread_id") or "").strip()
    if not desktop_thread_id:
        return {
            "ok": False,
            "command": None,
            "result": {
                "ok": False,
                "returncode": 2,
                "retryable": False,
                "stdout": "",
                "stderr": f"peer runtime breadcrumb missing desktop_thread_id: {peer_path}",
                "reason": "no_peer_breadcrumb",
            },
        }

    deeplink_template = str(peer.get("deeplink_template") or "")
    deeplink_uri = deeplink_template.format(thread_id=desktop_thread_id) if deeplink_template else ""
    mapping = {
        "desktop_thread_id": desktop_thread_id,
        "session_id": str(peer.get("session_id") or ""),
        "project": str(peer.get("project") or ""),
        "deeplink_uri": deeplink_uri,
    }

    required_fields = _template_required_fields(template)
    missing = sorted(name for name in required_fields if not mapping.get(name))
    if missing:
        return {
            "ok": False,
            "command": None,
            "result": {
                "ok": False,
                "returncode": 2,
                "retryable": False,
                "stdout": "",
                "stderr": "peer runtime breadcrumb missing field(s): %s" % ", ".join(missing),
                "reason": "no_peer_breadcrumb",
            },
        }

    return {"ok": True, "command": _format_template(template, mapping), "peer": peer}


def _warn_unknown_origin_once(
    *,
    state_path: Path,
    seen_ids: set,
    pending: List[Dict[str, Any]],
    paused_messages: List[Dict[str, Any]],
    unknown_origin_warnings: List[str],
    wake_fire_history: List[Dict[str, Any]],
    session_key: str,
    inbox_path: Path,
    agent: str,
    session_id: str,
) -> None:
    state = load_seen(state_path)
    warned = set(str(item) for item in state.get("unknown_origin_warnings", []))
    if session_key in warned:
        return
    warned.add(session_key)
    _append_wake_audit(
        inbox_path,
        {
            "action": "unknown_origin_warning",
            "agent": agent,
            "session_id": session_id,
            "reason": "peer_runtime_bootstrap_origin_unknown",
        },
    )
    _save_watcher_state(
        state_path,
        seen_ids,
        pending,
        paused_messages,
        sorted(warned),
        wake_fire_history,
    )


def _attempt_bad_provenance_repair(*, inbox_path: Path, agent: str, peer: Dict[str, Any]) -> Optional[Dict[str, Any]]:
    if str(peer.get("bootstrap_origin") or "").strip().lower() != "subagent":
        return None
    project = str(peer.get("project") or "").strip()
    bad_session = str(peer.get("session_id") or "").strip()
    if not project or not bad_session:
        return None
    bridge = AgentBridge(inbox_path.parent)
    result = bridge.repair_bootstrap_provenance(
        agent=agent,
        project=project,
        bad_session_id=bad_session,
        trusted_parent_session_id=str(peer.get("trusted_parent_session_id") or "").strip() or None,
        fallback_thread_id=str(peer.get("desktop_thread_id") or "").strip() or None,
        fallback_parent_thread_id=str(peer.get("bootstrap_parent_thread_id") or "").strip() or None,
    )
    return {"ok": result.ok, "status": result.status, "message": result.message, "data": result.data}


def run_command_for_session(
    cmd: Union[str, Sequence[str]],
    agent: str,
    session_id: str,
    messages: List[Dict[str, Any]],
    inbox_path: Path,
) -> Dict[str, Any]:
    import subprocess

    if not messages:
        return {"ok": False, "returncode": None, "retryable": False, "stdout": "", "stderr": ""}
    if isinstance(cmd, str):
        legacy = _legacy_command_to_argv(cmd)
        if not legacy.get("ok"):
            return legacy["result"]
        cmd = legacy["command"]
    command_text = " ".join(str(part) for part in cmd) if not isinstance(cmd, str) else cmd
    if "consume_inbox.py" in command_text:
        print(
            f"[agent-bridge] refusing destructive on_message_command for {agent} session=...{(session_id or '')[-8:]}",
            flush=True,
        )
        return {"ok": True, "returncode": 0, "retryable": False, "stdout": "", "stderr": ""}
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
            list(cmd),
            shell=False,
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
            retryable = proc.returncode not in WAKE_PERMANENT_EXIT_CODES
            print(
                f"[agent-bridge] on_message_command exited {proc.returncode}; "
                f"{'message remains retryable' if retryable else 'suppressing retries for this message'}",
                flush=True,
            )
            return {
                "ok": False,
                "returncode": proc.returncode,
                "retryable": retryable,
                "stdout": proc.stdout,
                "stderr": proc.stderr,
            }
        return {"ok": True, "returncode": 0, "retryable": False, "stdout": proc.stdout, "stderr": proc.stderr}
    except subprocess.TimeoutExpired:
        print("[agent-bridge] on_message_command timed out; message remains retryable", flush=True)
        return {"ok": False, "returncode": None, "retryable": True, "stdout": "", "stderr": "timeout"}
    except Exception as exc:
        print(f"[agent-bridge] on_message_command failed: {exc}", flush=True)
        return {"ok": False, "returncode": None, "retryable": True, "stdout": "", "stderr": str(exc)}


def _process_pending_wake_verifications(
    session_config: Dict[str, Any],
    *,
    pending: List[Dict[str, Any]],
    seen_ids: set,
    state_path: Path,
    toasts_enabled: bool,
    grace_period_seconds: int,
    max_retries: int,
    bridge_paused: bool,
    paused_messages: Optional[List[Dict[str, Any]]] = None,
    unknown_origin_warnings: Optional[List[str]] = None,
    wake_fire_history: Optional[List[Dict[str, Any]]] = None,
) -> List[Dict[str, Any]]:
    """Promote pending wake attempts to seen only after a receipt appears."""
    agent = session_config["agent"]
    session_id = session_config["session_id"]
    inbox_path = Path(session_config["inbox"])
    on_message_command: Optional[str] = session_config.get("on_message_command")
    now = datetime.now(timezone.utc)
    kept: List[Dict[str, Any]] = []
    changed = False
    unknown_origin_warnings = unknown_origin_warnings or []
    wake_fire_history = wake_fire_history or []

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
        deferred_until = _parse_dt(entry.get("deferred_until"))
        if deferred_until and now < deferred_until:
            kept.append(entry)
            continue
        if sent_at and (now - sent_at).total_seconds() < grace_period_seconds:
            kept.append(entry)
            continue

        if bridge_paused:
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

        if _breaker_is_open(state_path=state_path, session_id=session_id, inbox_path=inbox_path):
            seen_ids.add(message_id)
            changed = True
            _append_wake_audit(
                inbox_path,
                {
                    "action": "wake_skipped_breaker_open",
                    "agent": agent,
                    "session_id": session_id,
                    "message_id": message_id,
                    "reason": "wake_breaker_open",
                },
            )
            continue

        if _wake_rate_limited(
            session_id=session_id,
            wake_fire_history=wake_fire_history,
            seen_ids=seen_ids,
            pending=kept + [item for item in pending if item not in kept],
            paused_messages=paused_messages or [],
            unknown_origin_warnings=unknown_origin_warnings,
            state_path=state_path,
        ):
            entry["deferred_until"] = (now + timedelta(seconds=WAKE_PREFIRE_DEFER_S)).isoformat(timespec="seconds")
            kept.append(entry)
            changed = True
            _append_wake_audit(
                inbox_path,
                {
                    "action": "wake_rate_limited",
                    "agent": agent,
                    "session_id": session_id,
                    "message_id": message_id,
                    "deferred_seconds": WAKE_PREFIRE_DEFER_S,
                },
            )
            continue

        resolved = _resolve_command_template(session_config, inbox_path)
        if resolved.get("result") is not None:
            command_result = resolved["result"]
        elif resolved.get("command"):
            wake_fire_history = _record_wake_fire(
                session_id=session_id,
                seen_ids=seen_ids,
                pending=kept,
                paused_messages=paused_messages or [],
                unknown_origin_warnings=unknown_origin_warnings,
                wake_fire_history=wake_fire_history,
                state_path=state_path,
            )
            command_result = run_command_for_session(resolved["command"], agent, session_id, [row], inbox_path)
        else:
            command_result = {"ok": False, "returncode": None, "retryable": True, "stdout": "", "stderr": ""}
        if not command_result.get("ok") and not command_result.get("retryable", True):
            repair = _attempt_bad_provenance_repair(
                inbox_path=inbox_path,
                agent=agent,
                peer=resolved.get("peer") or command_result.get("peer") or {},
            )
            if repair:
                command_result["repair"] = repair
            _mark_permanent_wake_failure(
                agent=agent,
                session_id=session_id,
                inbox_path=inbox_path,
                message_id=message_id,
                seen_ids=seen_ids,
                result=command_result,
                state_path=state_path,
            )
            changed = True
            continue
        if command_result.get("ok"):
            _record_wake_success(state_path=state_path, session_id=session_id, inbox_path=inbox_path)
        else:
            _record_wake_failure(
                state_path=state_path,
                agent=agent,
                session_id=session_id,
                code=str(command_result.get("returncode") if command_result.get("returncode") is not None else "timeout"),
                inbox_path=inbox_path,
            )
        retry_count += 1
        entry["retry_count"] = retry_count
        entry["sent_at"] = utc_now()
        entry.pop("deferred_until", None)
        entry["last_retry_ok"] = bool(command_result.get("ok"))
        kept.append(entry)
        changed = True
        print(
            f"[agent-bridge] wake receipt pending for {agent} id={message_id}; retry {retry_count}/{max_retries}",
            flush=True,
        )

    if changed:
        _save_watcher_state(state_path, seen_ids, kept, paused_messages, unknown_origin_warnings, wake_fire_history)
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
    paused_messages: Optional[List[Dict[str, Any]]] = None,
    unknown_origin_warnings: Optional[List[str]] = None,
    wake_fire_history: Optional[List[Dict[str, Any]]] = None,
    deferred_seconds: int = 0,
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
                "deferred_until": (
                    datetime.now(timezone.utc) + timedelta(seconds=deferred_seconds)
                ).isoformat(timespec="seconds")
                if deferred_seconds > 0 else None,
            }
        )
        existing_ids.add(message_id)
        changed = True
    if changed:
        _save_watcher_state(
            state_path,
            seen_ids,
            pending,
            paused_messages,
            unknown_origin_warnings,
            wake_fire_history,
        )
    return pending


def _queue_paused_wake_messages(
    *,
    paused_messages: List[Dict[str, Any]],
    agent: str,
    session_id: str,
    inbox_path: Path,
    messages: List[Dict[str, Any]],
    pending: List[Dict[str, Any]],
    seen_ids: set,
    state_path: Path,
    unknown_origin_warnings: Optional[List[str]] = None,
    wake_fire_history: Optional[List[Dict[str, Any]]] = None,
) -> List[Dict[str, Any]]:
    existing_ids = {entry.get("message_id") for entry in paused_messages}
    changed = False
    for message in messages:
        message_id = str(message.get("id", ""))
        if not message_id or message_id in existing_ids:
            continue
        paused_messages.append(
            {
                "message_id": message_id,
                "agent": agent,
                "session_id": session_id,
                "inbox": str(inbox_path),
                "paused_at": utc_now(),
            }
        )
        existing_ids.add(message_id)
        _append_wake_audit(
            inbox_path,
            {
                "action": "wake_skipped_paused",
                "agent": agent,
                "session_id": session_id,
                "message_id": message_id,
                "reason": "bridge_paused",
            },
        )
        changed = True
    if changed:
        _save_watcher_state(
            state_path,
            seen_ids,
            pending,
            paused_messages,
            unknown_origin_warnings,
            wake_fire_history,
        )
    return paused_messages


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
    on_message_command_template: Optional[str] = session_config.get("on_message_command_template")

    effective_on_message = on_message
    if not toasts_enabled and on_message == "toast":
        effective_on_message = "log"

    state = load_seen(state_path)
    pending: List[Dict[str, Any]] = list(state.get("pending_wake_verifications", []))
    paused_messages: List[Dict[str, Any]] = list(state.get("paused_wake_messages", []))
    unknown_origin_warnings: List[str] = list(state.get("unknown_origin_warnings", []))
    wake_fire_history: List[Dict[str, Any]] = list(state.get("wake_fire_history", []))
    bridge_paused = _bridge_is_paused(inbox_path.parent)
    if not bridge_paused:
        filtered_paused_messages = [
            entry
            for entry in paused_messages
            if not (entry.get("agent") == agent and entry.get("session_id") == session_id)
        ]
        if len(filtered_paused_messages) != len(paused_messages):
            paused_messages = filtered_paused_messages
            _save_watcher_state(state_path, seen_ids, pending, paused_messages, unknown_origin_warnings, wake_fire_history)
    pending = _process_pending_wake_verifications(
        session_config,
        pending=pending,
        seen_ids=seen_ids,
        state_path=state_path,
        toasts_enabled=toasts_enabled,
        grace_period_seconds=grace_period_seconds,
        max_retries=max_retries,
        bridge_paused=bridge_paused,
        paused_messages=paused_messages,
        unknown_origin_warnings=unknown_origin_warnings,
        wake_fire_history=wake_fire_history,
    )
    wake_fire_history = list(load_seen(state_path).get("wake_fire_history", []))
    pending_ids = {
        entry.get("message_id")
        for entry in pending
        if entry.get("agent") == agent and entry.get("session_id") == session_id
    }
    paused_ids = {
        entry.get("message_id")
        for entry in paused_messages
        if entry.get("agent") == agent and entry.get("session_id") == session_id
    }

    unread = unread_for_session(inbox_path, session_id)
    new_msgs = [
        m for m in unread
        if m.get("id") not in seen_ids and m.get("id") not in pending_ids and m.get("id") not in paused_ids
    ]
    if not new_msgs:
        return []

    command_configured = bool(on_message_command or on_message_command_template)
    if effective_on_message == "log":
        if bridge_paused and command_configured:
            _queue_paused_wake_messages(
                paused_messages=paused_messages,
                agent=agent,
                session_id=session_id,
                inbox_path=inbox_path,
                messages=new_msgs,
                pending=pending,
                seen_ids=seen_ids,
                state_path=state_path,
                unknown_origin_warnings=unknown_origin_warnings,
                wake_fire_history=wake_fire_history,
            )
            for m in new_msgs:
                print(
                    f"[agent-bridge] {utc_now()} -- wake suppressed while paused for {agent} id={m.get('id')} session=...{(session_id or '')[-8:]}",
                    flush=True,
                )
            return []
        for m in new_msgs:
            print(
                f"[agent-bridge] {utc_now()} -- new {agent} message id={m.get('id')} session=...{(session_id or '')[-8:]}",
                flush=True,
            )
        for m in new_msgs:
            seen_ids.add(m["id"])
        _save_watcher_state(state_path, seen_ids, pending, paused_messages, unknown_origin_warnings, wake_fire_history)
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

    if bridge_paused and command_configured:
        _queue_paused_wake_messages(
            paused_messages=paused_messages,
            agent=agent,
            session_id=session_id,
            inbox_path=inbox_path,
            messages=new_msgs,
            pending=pending,
            seen_ids=seen_ids,
            state_path=state_path,
            unknown_origin_warnings=unknown_origin_warnings,
            wake_fire_history=wake_fire_history,
        )
        return []

    command_result = {"ok": False, "returncode": None, "retryable": True, "stdout": "", "stderr": ""}
    if command_configured:
        resolved = _resolve_command_template(session_config, inbox_path)
        peer = resolved.get("peer") or {}
        if agent != "codex" and str(peer.get("bootstrap_origin") or "").lower() == "unknown":
            session_key = "%s:%s" % (agent, session_id)
            _warn_unknown_origin_once(
                state_path=state_path,
                seen_ids=seen_ids,
                pending=pending,
                paused_messages=paused_messages,
                unknown_origin_warnings=unknown_origin_warnings,
                wake_fire_history=wake_fire_history,
                session_key=session_key,
                inbox_path=inbox_path,
                agent=agent,
                session_id=session_id,
            )
        if resolved.get("result") is not None:
            command_result = resolved["result"]
        elif resolved.get("command"):
            if _breaker_is_open(state_path=state_path, session_id=session_id, inbox_path=inbox_path):
                for message in new_msgs:
                    message_id = str(message.get("id", ""))
                    if not message_id:
                        continue
                    seen_ids.add(message_id)
                    _append_wake_audit(
                        inbox_path,
                        {
                            "action": "wake_skipped_breaker_open",
                            "agent": agent,
                            "session_id": session_id,
                            "message_id": message_id,
                            "reason": "wake_breaker_open",
                        },
                    )
                _save_watcher_state(state_path, seen_ids, pending, paused_messages, unknown_origin_warnings, wake_fire_history)
                return []
            if _wake_rate_limited(
                session_id=session_id,
                wake_fire_history=wake_fire_history,
                seen_ids=seen_ids,
                pending=pending,
                paused_messages=paused_messages,
                unknown_origin_warnings=unknown_origin_warnings,
                state_path=state_path,
            ):
                _queue_pending_wake_verifications(
                    pending=pending,
                    agent=agent,
                    session_id=session_id,
                    inbox_path=inbox_path,
                    messages=new_msgs,
                    seen_ids=seen_ids,
                    state_path=state_path,
                    paused_messages=paused_messages,
                    unknown_origin_warnings=unknown_origin_warnings,
                    wake_fire_history=wake_fire_history,
                    deferred_seconds=WAKE_PREFIRE_DEFER_S,
                )
                for message in new_msgs:
                    _append_wake_audit(
                        inbox_path,
                        {
                            "action": "wake_rate_limited",
                            "agent": agent,
                            "session_id": session_id,
                            "message_id": str(message.get("id", "")),
                            "deferred_seconds": WAKE_PREFIRE_DEFER_S,
                        },
                    )
                return []
            wake_fire_history = _record_wake_fire(
                session_id=session_id,
                seen_ids=seen_ids,
                pending=pending,
                paused_messages=paused_messages,
                unknown_origin_warnings=unknown_origin_warnings,
                wake_fire_history=wake_fire_history,
                state_path=state_path,
            )
            command_result = run_command_for_session(resolved["command"], agent, session_id, new_msgs, inbox_path)

    if command_configured and not command_result.get("ok") and not command_result.get("retryable", True):
        repair = _attempt_bad_provenance_repair(
            inbox_path=inbox_path,
            agent=agent,
            peer=resolved.get("peer") or command_result.get("peer") or {},
        )
        if repair:
            command_result["repair"] = repair
        for message in new_msgs:
            message_id = str(message.get("id", ""))
            if not message_id:
                continue
            _mark_permanent_wake_failure(
                agent=agent,
                session_id=session_id,
                inbox_path=inbox_path,
                message_id=message_id,
                seen_ids=seen_ids,
                result=command_result,
                state_path=state_path,
            )
        _save_watcher_state(state_path, seen_ids, pending, paused_messages, unknown_origin_warnings, wake_fire_history)
        return []

    if command_configured and command_result.get("ok"):
        _record_wake_success(state_path=state_path, session_id=session_id, inbox_path=inbox_path)
        _queue_pending_wake_verifications(
            pending=pending,
            agent=agent,
            session_id=session_id,
            inbox_path=inbox_path,
            messages=new_msgs,
            seen_ids=seen_ids,
            state_path=state_path,
            paused_messages=paused_messages,
            unknown_origin_warnings=unknown_origin_warnings,
            wake_fire_history=wake_fire_history,
        )
        return []

    if command_configured and not command_result.get("ok"):
        _record_wake_failure(
            state_path=state_path,
            agent=agent,
            session_id=session_id,
            code=str(command_result.get("returncode") if command_result.get("returncode") is not None else "timeout"),
            inbox_path=inbox_path,
        )

    if not command_configured:
        for m in new_msgs:
            seen_ids.add(m["id"])
        _save_watcher_state(state_path, seen_ids, pending, paused_messages, unknown_origin_warnings, wake_fire_history)
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


def _write_runtime_breadcrumb(config_path: Path, state_dir: Path, command: List[str]) -> Path:
    runtime_path = config_path.parent / "watcher.runtime.json"
    write_runtime_breadcrumb(
        runtime_path,
        build_runtime_breadcrumb(
            state_dir=state_dir,
            role="watcher",
            command=command,
            pid=os.getpid(),
            config_path=config_path,
        ),
    )
    return runtime_path


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
    runtime_path = _write_runtime_breadcrumb(config_path, state_dir, command)

    def _cleanup() -> None:
        try:
            if pid_path.exists() and pid_path.read_text(encoding="utf-8").strip() == str(os.getpid()):
                pid_path.unlink(missing_ok=True)
            if runtime_path.exists():
                runtime = json.loads(runtime_path.read_text(encoding="utf-8"))
                if int(runtime.get("pid") or 0) == os.getpid():
                    runtime_path.unlink(missing_ok=True)
        except Exception:
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
