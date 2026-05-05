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
from core.paths import session_registry_path_for_state_dir
from core.processes import acquire_singleton_lease, command_line_hash, heartbeat_lease, is_process_alive, release_lease
from core.runtime import (
    MONITOR_RUNTIME_MIN_TTL_S,
    build_runtime_breadcrumb,
    monitor_runtime_path_for_state_dir,
    normalize_peer_runtime_breadcrumb,
    peer_runtime_path_for_state_dir,
    read_runtime_breadcrumb,
    write_runtime_breadcrumb,
)
from core.settings import BridgeSettings, load_settings, settings_path_for_state_dir
from core.storage import append_jsonl as storage_append_jsonl
from core.storage import read_json as storage_read_json
from core.storage import read_jsonl as storage_read_jsonl
from core.storage import update_json as storage_update_json
from core.storage import write_json as storage_write_json

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
WAKE_FOCUS_STEAL_EXIT_CODE = 12
WAKE_DEFERRED_EXIT_CODE = 16
WAKE_BREAKER_EXEMPT_EXIT_CODES = {WAKE_FOCUS_STEAL_EXIT_CODE, WAKE_DEFERRED_EXIT_CODE}
WAKE_BREAKER_EXEMPT_EXIT_CODE_STRINGS = {str(item) for item in WAKE_BREAKER_EXEMPT_EXIT_CODES}
WAKE_PREFIRE_LIMIT = 2
WAKE_PREFIRE_WINDOW_S = 10
WAKE_PREFIRE_DEFER_S = 10
WAKE_BREAKER_THRESHOLD = 5
WAKE_BREAKER_WINDOW_S = 5 * 60
WAKE_BREAKER_IDLE_CLOSE_S = 15 * 60
WAKE_BREAKER_SCHEMA_VERSION = 1
CLAUDE_MONITOR_UNREAD_ESCALATION_S = 60
STALE_UNREAD_WATCHDOG_REARM_S = 5 * 60
MONITOR_RESTART_CONTROL_TYPE = "MONITOR_RESTART_REQUIRED"
MCP_SERVER_RESTARTED_CONTROL_TYPE = "MCP_SERVER_RESTARTED"
SERVER_STATUS_FILENAME = "server-status.json"
MONITOR_RESTART_WATCH_FILES = (
    "tools/agent-bridge/bridge_monitor_poll.py",
    "tools/agent-bridge/server.py",
    "tools/agent-bridge/agent_bridge.py",
    "tools/agent-bridge/watcher.py",
)
WAKE_TELEMETRY_PREFIX = "AGENT_BRIDGE_WAKE_TELEMETRY "
OPTIONAL_TEMPLATE_FIELDS = {"restore_thread_id"}
LEGACY_COMMAND_FORBIDDEN_CHARS = {"&", "|", ";", ">", "<", "`"}
ACTIVE_SESSION_ID_SOURCE = "active_session"
_SESSION_REGISTRY_CACHE: Dict[str, Dict[str, Any]] = {}


def utc_now() -> str:
    return datetime.now(timezone.utc).isoformat(timespec="seconds")


def read_jsonl(path: Path) -> List[Dict[str, Any]]:
    return storage_read_jsonl(path)


def unread_for_session(inbox_path: Path, session_id: str) -> List[Dict[str, Any]]:
    rows = read_jsonl(inbox_path)
    return [
        r for r in rows
        if r.get("session_id") == session_id and not r.get("read_at")
    ]


def _load_session_registry_cached(registry_path: Path) -> Dict[str, Any]:
    """Read session.json with a cheap mtime/size cache for watcher poll loops."""
    key = str(registry_path)
    try:
        stat = registry_path.stat()
    except OSError:
        _SESSION_REGISTRY_CACHE.pop(key, None)
        return {}

    signature = (stat.st_mtime_ns, stat.st_size)
    cached = _SESSION_REGISTRY_CACHE.get(key)
    if cached and cached.get("signature") == signature:
        payload = cached.get("payload")
        return payload if isinstance(payload, dict) else {}

    try:
        with registry_path.open("r", encoding="utf-8") as handle:
            payload = json.load(handle)
    except (OSError, json.JSONDecodeError):
        payload = {}
    if not isinstance(payload, dict):
        payload = {}
    _SESSION_REGISTRY_CACHE[key] = {"signature": signature, "payload": payload}
    return payload


def _active_session_from_registry(registry: Dict[str, Any], *, project: str, agent: str) -> Optional[str]:
    projects = registry.get("projects", {})
    if not isinstance(projects, dict):
        return None
    project_entry = projects.get(project)
    if not isinstance(project_entry, dict):
        return None
    active = project_entry.get("active", {})
    if not isinstance(active, dict):
        return None
    session_id = str(active.get(agent) or "").strip()
    if not session_id:
        return None
    sessions = project_entry.get("sessions", {})
    if isinstance(sessions, dict):
        record = sessions.get(session_id)
        if isinstance(record, dict) and record.get("agent") not in {None, agent}:
            return None
    return session_id


def _resolve_session_config(session_config: Dict[str, Any]) -> Dict[str, Any]:
    """Return a copy with private entries rebound to the active session registry row.

    watcher-config.json may contain a stale private session_id snapshot.  For
    entries that opt into active-session resolution, session.json is the source
    of truth and the snapshot is only a fallback for bootstrap/recovery gaps.
    """
    resolved = dict(session_config)
    fallback_session_id = str(resolved.get("session_id") or "").strip()
    source = str(resolved.get("session_id_source") or "").strip().lower()
    if resolved.get("kind") != "private" or source != ACTIVE_SESSION_ID_SOURCE:
        resolved["session_id"] = fallback_session_id
        return resolved

    agent = str(resolved.get("agent") or "").strip()
    project = str(resolved.get("project") or "").strip()
    inbox_value = str(resolved.get("inbox") or "").strip()
    if not agent or not project or not inbox_value:
        resolved["session_id"] = fallback_session_id
        return resolved

    registry_value = str(resolved.get("session_registry") or resolved.get("session_registry_path") or "").strip()
    registry_path = Path(registry_value) if registry_value else session_registry_path_for_state_dir(Path(inbox_value).parent)
    active_session_id = _active_session_from_registry(
        _load_session_registry_cached(registry_path),
        project=project,
        agent=agent,
    )
    resolved["configured_session_id"] = fallback_session_id
    resolved["resolved_session_id"] = active_session_id or fallback_session_id
    resolved["session_registry_path"] = str(registry_path)
    resolved["session_id"] = active_session_id or fallback_session_id
    return resolved


def load_seen(state_path: Path) -> Dict[str, Any]:
    data = storage_read_json(
        state_path,
        {
            "seen_ids": [],
            "toasted_ids": [],
            "pending_wake_verifications": [],
            "paused_wake_messages": [],
            "unknown_origin_warnings": [],
            "wake_fire_history": [],
            "claude_monitor_escalations": [],
        },
    )
    data.setdefault("seen_ids", [])
    data.setdefault("toasted_ids", [])
    data.setdefault("pending_wake_verifications", [])
    data.setdefault("paused_wake_messages", [])
    data.setdefault("unknown_origin_warnings", [])
    data.setdefault("wake_fire_history", [])
    data.setdefault("claude_monitor_escalations", [])
    return data


def save_seen(state_path: Path, seen: Dict[str, Any]) -> None:
    storage_write_json(state_path, seen)


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


def _claude_monitor_runtime_status(state_dir: Path, session_id: str, project: str) -> Dict[str, Any]:
    runtime_path = monitor_runtime_path_for_state_dir(state_dir, "claude", session_id)
    data = read_runtime_breadcrumb(runtime_path)
    result: Dict[str, Any] = {"status": "missing", "path": str(runtime_path), "fresh": False}
    if not data:
        return result
    if data.get("unreadable"):
        result.update({"status": "unreadable", "reason": data.get("error") or "runtime_unreadable"})
        return result
    mismatches: List[str] = []
    if data.get("agent") != "claude":
        mismatches.append("agent")
    if data.get("session_id") != session_id:
        mismatches.append("session_id")
    if data.get("project") != project:
        mismatches.append("project")
    watched = {str(item) for item in data.get("watched_buckets") or [] if str(item)}
    if session_id not in watched or project not in watched:
        mismatches.append("watched_buckets")
    pid = int(data.get("monitor_pid") or 0)
    if pid and not is_process_alive(pid):
        mismatches.append("monitor_pid")
    heartbeat = _parse_dt(data.get("heartbeat_at"))
    try:
        ttl_seconds = max(MONITOR_RUNTIME_MIN_TTL_S, int(float(data.get("poll_interval_seconds") or 0) * 3))
    except (TypeError, ValueError):
        ttl_seconds = MONITOR_RUNTIME_MIN_TTL_S
    age_seconds = None
    if heartbeat:
        age_seconds = max(0, int((datetime.now(timezone.utc) - heartbeat).total_seconds()))
    else:
        mismatches.append("heartbeat_at")
    result.update({"age_seconds": age_seconds, "freshness_ttl_seconds": ttl_seconds})
    if mismatches:
        result.update({"status": "misbound", "reason": ",".join(mismatches), "mismatches": mismatches})
        return result
    if age_seconds is None or age_seconds > ttl_seconds:
        result.update({"status": "stale", "reason": "heartbeat_expired"})
        return result
    result.update({"status": "current", "fresh": True})
    return result


def _escalate_claude_unread_without_monitor(
    *,
    state_path: Path,
    inbox_path: Path,
    session_id: str,
    project: str,
    toasts_enabled: bool,
    toast_expiry_minutes: int,
    toast_max_in_tray: int,
    emit_user_visible: bool = True,
) -> List[str]:
    runtime = _claude_monitor_runtime_status(inbox_path.parent, session_id, project)
    if runtime.get("fresh"):
        return []
    now_dt = datetime.now(timezone.utc)
    unread = unread_for_session(inbox_path, session_id)
    candidates: List[Dict[str, Any]] = []
    for row in unread:
        created = _parse_dt(str(row.get("created_at") or ""))
        if not created:
            continue
        age_seconds = max(0, int((now_dt - created).total_seconds()))
        if age_seconds >= CLAUDE_MONITOR_UNREAD_ESCALATION_S:
            candidate = dict(row)
            candidate["age_seconds"] = age_seconds
            candidates.append(candidate)
    if not candidates:
        return []

    def _merge(existing: Dict[str, Any]) -> Dict[str, Any]:
        escalations = list(existing.get("claude_monitor_escalations") or [])
        escalated_ids = {str(item.get("message_id") or "") for item in escalations if isinstance(item, dict)}
        new_events: List[Dict[str, Any]] = []
        for row in candidates:
            message_id = str(row.get("id") or "")
            if not message_id or message_id in escalated_ids:
                continue
            event = {
                "message_id": message_id,
                "agent": "claude",
                "session_id": session_id,
                "project": project,
                "status": "CLAUDE_UNREAD_WITHOUT_MONITOR",
                "runtime_status": runtime.get("status"),
                "age_seconds": row.get("age_seconds"),
                "escalated_at": utc_now(),
            }
            escalations.append(event)
            new_events.append(event)
        if not new_events:
            return existing
        updated = dict(existing)
        updated["claude_monitor_escalations"] = escalations[-200:]
        updated["_new_claude_monitor_escalations"] = new_events
        return updated

    updated = storage_update_json(
        state_path,
        {
            "seen_ids": [],
            "toasted_ids": [],
            "pending_wake_verifications": [],
            "paused_wake_messages": [],
            "unknown_origin_warnings": [],
            "wake_fire_history": [],
            "claude_monitor_escalations": [],
        },
        _merge,
    )
    events = list(updated.get("_new_claude_monitor_escalations") or [])
    if "_new_claude_monitor_escalations" in updated:
        updated.pop("_new_claude_monitor_escalations", None)
        storage_write_json(state_path, updated)
    for event in events:
        _append_wake_audit(
            inbox_path,
            {
                "action": "claude_unread_without_monitor",
                "agent": "claude",
                "session_id": session_id,
                "project": project,
                "message_id": event.get("message_id"),
                "status": event.get("status"),
                "runtime_status": event.get("runtime_status"),
                "age_seconds": event.get("age_seconds"),
            },
        )
        if emit_user_visible:
            print(
                "[agent-bridge] %s -- Claude unread work id=%s has no fresh Monitor heartbeat for session=...%s"
                % (utc_now(), event.get("message_id"), session_id[-8:]),
                flush=True,
            )
    if events and toasts_enabled and emit_user_visible:
        notify_windows_toast(
            "claude",
            session_id,
            [
                {
                    "id": "claude-monitor-%s" % events[-1].get("message_id"),
                    "body": "Claude has unread bridge work but no fresh Monitor heartbeat. Start bridge_monitor_poll.py.",
                    "from": "agent-bridge",
                }
            ],
            toast_expiry_minutes=toast_expiry_minutes,
            toast_max_in_tray=toast_max_in_tray,
            display_label="Claude Monitor stale",
        )
    if not emit_user_visible:
        return []
    return [str(event.get("message_id") or "") for event in events if event.get("message_id")]


def _append_wake_audit(inbox_path: Path, event: Dict[str, Any]) -> None:
    audit_path = inbox_path.parent / "messages.jsonl"
    event.setdefault("id", str(uuid.uuid4()))
    event.setdefault("timestamp", utc_now())
    try:
        storage_append_jsonl(audit_path, event)
    except OSError as exc:
        print(f"[agent-bridge] failed to write wake audit event: {exc}", flush=True)


def _rearm_stale_unread_without_monitor(
    *,
    state_path: Path,
    inbox_path: Path,
    session_id: str,
    project: str,
    seen_ids: set,
    toasted_ids: set,
    pending_ids: set,
    paused_ids: set,
) -> List[str]:
    runtime = _claude_monitor_runtime_status(inbox_path.parent, session_id, project)
    if runtime.get("fresh"):
        return []
    now_dt = datetime.now(timezone.utc)
    candidates: List[Dict[str, Any]] = []
    for row in unread_for_session(inbox_path, session_id):
        message_id = str(row.get("id") or "")
        if not message_id or message_id in pending_ids or message_id in paused_ids:
            continue
        if row.get("marker_variant") == "control":
            continue
        if message_id not in seen_ids and message_id not in toasted_ids:
            continue
        created = _parse_dt(str(row.get("created_at") or ""))
        if not created:
            continue
        age_seconds = max(0, int((now_dt - created).total_seconds()))
        if age_seconds >= STALE_UNREAD_WATCHDOG_REARM_S:
            candidate = dict(row)
            candidate["age_seconds"] = age_seconds
            candidates.append(candidate)
    if not candidates:
        return []

    def _merge(existing: Dict[str, Any]) -> Dict[str, Any]:
        rearm_events = list(existing.get("stale_unread_watchdog_rearms") or [])
        rearmed_ids = {
            str(item.get("message_id") or "")
            for item in rearm_events
            if isinstance(item, dict) and item.get("message_id")
        }
        current_seen = {str(item) for item in existing.get("seen_ids", []) if str(item)}
        current_toasted = {str(item) for item in existing.get("toasted_ids", []) if str(item)}
        new_events: List[Dict[str, Any]] = []
        for row in candidates:
            message_id = str(row.get("id") or "")
            if not message_id or message_id in rearmed_ids:
                continue
            if message_id not in current_seen and message_id not in current_toasted:
                continue
            current_seen.discard(message_id)
            current_toasted.discard(message_id)
            event = {
                "message_id": message_id,
                "agent": "claude",
                "session_id": session_id,
                "project": project,
                "runtime_status": runtime.get("status"),
                "age_seconds": row.get("age_seconds"),
                "rearmed_at": utc_now(),
            }
            rearm_events.append(event)
            new_events.append(event)
        if not new_events:
            return existing
        updated = dict(existing)
        updated["seen_ids"] = sorted(current_seen)[-500:]
        updated["toasted_ids"] = sorted(current_toasted)[-500:]
        updated["stale_unread_watchdog_rearms"] = rearm_events[-200:]
        updated["_new_stale_unread_watchdog_rearms"] = new_events
        return updated

    updated = storage_update_json(
        state_path,
        {
            "seen_ids": [],
            "toasted_ids": [],
            "pending_wake_verifications": [],
            "paused_wake_messages": [],
            "unknown_origin_warnings": [],
            "wake_fire_history": [],
            "claude_monitor_escalations": [],
            "stale_unread_watchdog_rearms": [],
        },
        _merge,
    )
    events = list(updated.get("_new_stale_unread_watchdog_rearms") or [])
    if "_new_stale_unread_watchdog_rearms" in updated:
        updated.pop("_new_stale_unread_watchdog_rearms", None)
        storage_write_json(state_path, updated)
    seen_ids.clear()
    seen_ids.update(str(item) for item in updated.get("seen_ids", []) if str(item))
    toasted_ids.clear()
    toasted_ids.update(str(item) for item in updated.get("toasted_ids", []) if str(item))
    if events:
        message_ids = [str(event.get("message_id") or "") for event in events if event.get("message_id")]
        _append_wake_audit(
            inbox_path,
            {
                "action": "stale_unread_watchdog_rearmed",
                "agent": "claude",
                "session_id": session_id,
                "project": project,
                "message_ids": message_ids,
                "rearmed_count": len(message_ids),
                "runtime_status": runtime.get("status"),
                "oldest_age_seconds": max(int(event.get("age_seconds") or 0) for event in events),
            },
        )
    return [str(event.get("message_id") or "") for event in events if event.get("message_id")]


def _monitor_restart_command(state_dir: Path, session_id: str, project: str) -> str:
    script = Path(__file__).resolve().parent / "bridge_monitor_poll.py"
    return (
        'Monitor(persistent=True, command="%s -u \\"%s\\" --state-dir \\"%s\\" '
        '--agent claude --session-id %s --project %s --poll-interval-seconds 2")'
        % (sys.executable, script, state_dir, session_id, project)
    )


def _monitor_restart_control_body(
    *,
    trigger: str,
    reason: str,
    state_dir: Path,
    session_id: str,
    project: str,
    runtime: Optional[Dict[str, Any]] = None,
    commit: Optional[str] = None,
) -> str:
    lines = [
        "TYPE: CONTROL",
        "SUBJECT: MONITOR_RESTART_REQUIRED",
        "ACTION_REQUESTED: Stop any stale bridge Monitor task handle and start a fresh bridge_monitor_poll.py Monitor immediately.",
        "TRIGGER: %s" % trigger,
        "REASON: %s" % reason,
        "COMMAND: %s" % _monitor_restart_command(state_dir, session_id, project),
    ]
    if runtime:
        lines.append("RUNTIME_STATUS: %s" % (runtime.get("status") or "unknown"))
        if runtime.get("age_seconds") is not None:
            lines.append("RUNTIME_AGE_SECONDS: %s" % runtime.get("age_seconds"))
    if commit:
        lines.append("COMMIT: %s" % commit)
    return "\n".join(lines)


def _queue_monitor_restart_required_control(
    *,
    state_path: Path,
    state_dir: Path,
    session_id: str,
    project: str,
    trigger: str,
    dedupe_value: str,
    reason: str,
    runtime: Optional[Dict[str, Any]] = None,
    commit: Optional[str] = None,
) -> Optional[str]:
    key = "%s:%s:%s" % (session_id, trigger, dedupe_value)
    state = load_seen(state_path)
    controls = [item for item in state.get("monitor_restart_required_controls", []) if isinstance(item, dict)]
    if any(str(item.get("key") or "") == key for item in controls):
        return None
    bridge = AgentBridge(state_dir)
    body = _monitor_restart_control_body(
        trigger=trigger,
        reason=reason,
        state_dir=state_dir,
        session_id=session_id,
        project=project,
        runtime=runtime,
        commit=commit,
    )
    with bridge._locked():
        message_id = bridge._append_control_message(
            target_agent="claude",
            session_id=session_id,
            sender="agent-bridge",
            control_type=MONITOR_RESTART_CONTROL_TYPE,
            summary="Claude Monitor restart required",
            body=body,
            status="action_required",
            replace_existing_control=True,
            inbox_level="session",
            extra_fields={
                "monitor_restart_trigger": trigger,
                "monitor_restart_reason": reason,
                "monitor_restart_commit": commit,
            },
        )
        if message_id:
            bridge._audit(
                {
                    "id": str(uuid.uuid4()),
                    "timestamp": utc_now(),
                    "action": "monitor_restart_required_control_sent",
                    "agent": "claude",
                    "session_id": session_id,
                    "project": project,
                    "trigger": trigger,
                    "reason": reason,
                    "runtime_status": (runtime or {}).get("status"),
                    "commit": commit,
                    "message_id": message_id,
                    "accepted": True,
                }
            )
    if not message_id:
        return None
    controls.append(
        {
            "key": key,
            "session_id": session_id,
            "project": project,
            "trigger": trigger,
            "reason": reason,
            "message_id": message_id,
            "commit": commit,
            "created_at": utc_now(),
        }
    )
    state["monitor_restart_required_controls"] = controls[-200:]
    storage_write_json(state_path, state)
    return message_id


def _monitor_runtime_unhealthy_long_enough(
    *,
    state_path: Path,
    session_id: str,
    runtime: Dict[str, Any],
    threshold_seconds: int = MONITOR_RUNTIME_MIN_TTL_S,
) -> bool:
    if runtime.get("fresh"):
        state = load_seen(state_path)
        observations = dict(state.get("monitor_runtime_observations") or {})
        controls = [
            item
            for item in state.get("monitor_restart_required_controls", [])
            if not (
                isinstance(item, dict)
                and item.get("session_id") == session_id
                and item.get("trigger") == "monitor_stale"
            )
        ]
        key_prefix = "%s:" % session_id
        observations = {key: value for key, value in observations.items() if not str(key).startswith(key_prefix)}
        state["monitor_runtime_observations"] = observations
        state["monitor_restart_required_controls"] = controls
        storage_write_json(state_path, state)
        return False
    status = str(runtime.get("status") or "missing")
    age_seconds = runtime.get("age_seconds")
    if age_seconds is not None:
        try:
            return int(age_seconds) >= int(runtime.get("freshness_ttl_seconds") or threshold_seconds)
        except (TypeError, ValueError):
            return True
    state = load_seen(state_path)
    observations = dict(state.get("monitor_runtime_observations") or {})
    key = "%s:%s" % (session_id, status)
    now = datetime.now(timezone.utc)
    first_seen = _parse_dt(observations.get(key))
    if not first_seen:
        observations[key] = now.isoformat(timespec="seconds")
        state["monitor_runtime_observations"] = observations
        storage_write_json(state_path, state)
        return False
    return (now - first_seen).total_seconds() >= threshold_seconds


def _maybe_queue_monitor_stale_control(
    *,
    session_config: Dict[str, Any],
    state_path: Path,
    state_dir: Path,
) -> Optional[str]:
    resolved = _resolve_session_config(session_config)
    if resolved.get("agent") != "claude":
        return None
    session_id = str(resolved.get("session_id") or "")
    project = str(resolved.get("project") or "")
    if not session_id or not project or session_id == project:
        return None
    runtime = _claude_monitor_runtime_status(state_dir, session_id, project)
    if not _monitor_runtime_unhealthy_long_enough(state_path=state_path, session_id=session_id, runtime=runtime):
        return None
    status = str(runtime.get("status") or "missing")
    reason = "claude_monitor_%s" % status
    return _queue_monitor_restart_required_control(
        state_path=state_path,
        state_dir=state_dir,
        session_id=session_id,
        project=project,
        trigger="monitor_stale",
        dedupe_value=status,
        reason=reason,
        runtime=runtime,
    )


def _latest_bridge_watch_commit(repo_root: Path) -> Optional[str]:
    try:
        result = subprocess.run(
            ["git", "-C", str(repo_root), "log", "-1", "--format=%H", "--", *MONITOR_RESTART_WATCH_FILES],
            stdout=subprocess.PIPE,
            stderr=subprocess.DEVNULL,
            text=True,
            timeout=5,
            check=False,
        )
    except (OSError, subprocess.SubprocessError):
        return None
    commit = (result.stdout or "").strip().splitlines()
    return commit[0] if result.returncode == 0 and commit else None


def _maybe_queue_commit_monitor_restart_controls(
    *,
    config: Dict[str, Any],
    sessions: List[Dict[str, Any]],
    state_path: Path,
    state_dir: Path,
) -> List[str]:
    repo_value = config.get("repo_root") or config.get("canonical_root")
    if not repo_value:
        for session in sessions:
            repo_value = session.get("repo_root") or session.get("canonical_root")
            if repo_value:
                break
    if not repo_value:
        return []
    repo_root = Path(str(repo_value))
    commit = _latest_bridge_watch_commit(repo_root)
    if not commit:
        return []
    state = load_seen(state_path)
    watch = dict(state.get("monitor_restart_commit_watch") or {})
    previous = watch.get("last_commit")
    if previous == commit:
        return []
    watch.update({"repo_root": str(repo_root), "last_commit": commit, "updated_at": utc_now()})
    state["monitor_restart_commit_watch"] = watch
    storage_write_json(state_path, state)
    if not previous:
        return []
    queued: List[str] = []
    for session in sessions:
        resolved = _resolve_session_config(session)
        if resolved.get("agent") != "claude":
            continue
        session_id = str(resolved.get("session_id") or "")
        project = str(resolved.get("project") or "")
        if not session_id or not project or session_id == project:
            continue
        message_id = _queue_monitor_restart_required_control(
            state_path=state_path,
            state_dir=state_dir,
            session_id=session_id,
            project=project,
            trigger="bridge_code_commit",
            dedupe_value=commit,
            reason="bridge_monitor_related_code_changed",
            commit=commit,
        )
        if message_id:
            queued.append(message_id)
    return queued


def _watcher_state_dir(state_path: Path) -> Path:
    return state_path.parent / "state"


def _wake_breaker_path(state_path: Path) -> Path:
    return _watcher_state_dir(state_path) / "wake-failure-windows.json"


def _load_wake_breakers(state_path: Path) -> Dict[str, Any]:
    path = _wake_breaker_path(state_path)
    data = storage_read_json(
        path,
        {"schema_version": WAKE_BREAKER_SCHEMA_VERSION, "sessions": {}, "updated_at": utc_now()},
    )
    data.setdefault("schema_version", WAKE_BREAKER_SCHEMA_VERSION)
    data.setdefault("sessions", {})
    data.setdefault("updated_at", utc_now())
    return data


def _save_wake_breakers(state_path: Path, payload: Dict[str, Any]) -> None:
    path = _wake_breaker_path(state_path)
    payload["schema_version"] = WAKE_BREAKER_SCHEMA_VERSION
    payload["updated_at"] = utc_now()
    storage_write_json(path, payload)


def _normalize_breaker_session(record: Optional[Dict[str, Any]]) -> Dict[str, Any]:
    data = dict(record or {})
    data.setdefault("failures", [])
    data.setdefault("breaker_state", "closed")
    data.setdefault("opened_at", None)
    data.setdefault("last_failure_at", None)
    data.setdefault("last_success_at", None)
    data.setdefault("exit_code_distribution", {})
    data.setdefault("notified_open_at", None)
    try:
        data["bypass_grants"] = max(0, int(data.get("bypass_grants") or 0))
    except (TypeError, ValueError):
        data["bypass_grants"] = 0
    data.setdefault("last_bypass_granted_at", None)
    data.setdefault("last_bypass_consumed_at", None)
    data.setdefault("last_bypass_reason", None)
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


def _breaker_recovery_mode(
    *,
    state_path: Path,
    session_id: str,
) -> str:
    payload = _load_wake_breakers(state_path)
    record = payload.get("sessions", {}).get(session_id)
    if not record:
        return "closed"
    normalized = _normalize_breaker_session(record)
    last_failure = _parse_dt(normalized.get("last_failure_at"))
    now = datetime.now(timezone.utc)
    if (
        normalized.get("breaker_state") == "open"
        and last_failure is not None
        and (now - last_failure).total_seconds() >= WAKE_BREAKER_IDLE_CLOSE_S
    ):
        return "idle"
    if normalized.get("breaker_state") == "open" and int(normalized.get("bypass_grants") or 0) > 0:
        return "bypass"
    if normalized.get("breaker_state") == "open":
        return "open"
    return "closed"


def _consume_breaker_recovery(
    *,
    state_path: Path,
    session_id: str,
    mode: str,
    inbox_path: Optional[Path] = None,
    agent: Optional[str] = None,
    message_id: Optional[str] = None,
) -> None:
    if mode == "idle":
        _close_breaker(state_path=state_path, session_id=session_id, reason="idle", inbox_path=inbox_path)
        if inbox_path is not None:
            _append_wake_audit(
                inbox_path,
                {
                    "action": "wake_breaker_autoclose_retry",
                    "agent": agent,
                    "session_id": session_id,
                    "message_id": message_id,
                },
            )
        return
    if mode != "bypass":
        return
    payload = _load_wake_breakers(state_path)
    sessions = payload.setdefault("sessions", {})
    record = _normalize_breaker_session(sessions.get(session_id))
    grants = int(record.get("bypass_grants") or 0)
    if record.get("breaker_state") != "open" or grants <= 0:
        return
    record["bypass_grants"] = grants - 1
    record["last_bypass_consumed_at"] = utc_now()
    sessions[session_id] = record
    _save_wake_breakers(state_path, payload)
    if inbox_path is not None:
        _append_wake_audit(
            inbox_path,
            {
                "action": "wake_breaker_bypass_consumed",
                "agent": agent,
                "session_id": session_id,
                "message_id": message_id,
                "remaining_bypass_grants": record["bypass_grants"],
            },
        )


def _breaker_is_open(
    *,
    state_path: Path,
    session_id: str,
    inbox_path: Optional[Path] = None,
) -> bool:
    mode = _breaker_recovery_mode(state_path=state_path, session_id=session_id)
    if mode == "idle":
        _consume_breaker_recovery(state_path=state_path, session_id=session_id, mode=mode, inbox_path=inbox_path)
        return False
    return mode == "open"


def _record_wake_failure(
    *,
    state_path: Path,
    agent: str,
    session_id: str,
    code: str,
    inbox_path: Path,
) -> bool:
    code_text = str(code or "unknown")
    if code_text in WAKE_BREAKER_EXEMPT_EXIT_CODE_STRINGS:
        _append_wake_audit(
            inbox_path,
            {
                "action": "wake_failure_breaker_exempt",
                "agent": agent,
                "session_id": session_id,
                "code": code_text,
                "reason": "focus_steal_blocked",
            },
        )
        return False
    payload = _load_wake_breakers(state_path)
    sessions = payload.setdefault("sessions", {})
    record = _normalize_breaker_session(sessions.get(session_id))
    now = datetime.now(timezone.utc)
    record = _breaker_prune_failures(record, now)
    record["failures"].append({"at": now.isoformat(timespec="seconds"), "code": code_text})
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
    payload = _load_wake_breakers(state_path)
    sessions = payload.setdefault("sessions", {})
    record = _normalize_breaker_session(sessions.get(session_id))
    was_open = record.get("breaker_state") == "open"
    now = utc_now()
    if was_open and inbox_path is not None:
        _append_wake_audit(
            inbox_path,
            {
                "action": "wake_breaker_closed",
                "session_id": session_id,
                "reason": "success",
            },
        )
    record["breaker_state"] = "closed"
    record["opened_at"] = None
    record["last_success_at"] = now
    record["failures"] = []
    record["exit_code_distribution"] = {}
    sessions[session_id] = record
    _save_wake_breakers(state_path, payload)


def _truncate_ui_label(value: Any, limit: int = 240) -> str:
    text = str(value or "").replace("\r", " ").replace("\n", " ").strip()
    if len(text) <= limit:
        return text
    return text[: limit - 3] + "..."


def _runtime_session_display_label(
    state_dir: Path,
    agent: str,
    session_id: str,
    project: Optional[str] = None,
) -> str:
    tail = ("..." + session_id[-8:]) if session_id else ""
    peer = read_runtime_breadcrumb(peer_runtime_path_for_state_dir(state_dir, agent))
    if not isinstance(peer, dict) or peer.get("unreadable"):
        return tail
    if str(peer.get("agent") or "").strip().lower() not in {"", str(agent or "").strip().lower()}:
        return tail
    if str(peer.get("session_id") or "").strip() != str(session_id or "").strip():
        return tail
    project_text = str(project or "").strip()
    if project_text and str(peer.get("project") or "").strip() != project_text:
        return tail
    if peer.get("desktop_thread_title_project_match") is False:
        return tail
    title = _truncate_ui_label(peer.get("desktop_thread_title"), 80)
    if title and tail:
        return f"{title} ({tail})"
    return title or tail


def _is_generic_codex_thread_title(value: Any) -> bool:
    title = str(value or "").strip()
    return not title or title.lower() == "codex"


def _extract_wake_telemetry(stdout: Any) -> List[Dict[str, Any]]:
    events: List[Dict[str, Any]] = []
    for raw_line in str(stdout or "").splitlines():
        line = raw_line.strip()
        if not line.startswith(WAKE_TELEMETRY_PREFIX):
            continue
        payload = line[len(WAKE_TELEMETRY_PREFIX):].strip()
        if not payload:
            continue
        try:
            decoded = json.loads(payload)
        except json.JSONDecodeError:
            continue
        if isinstance(decoded, dict):
            events.append(decoded)
    return events


def _cache_wake_telemetry(
    *,
    inbox_path: Path,
    agent: str,
    session_id: str,
    message_id: str,
    command_result: Dict[str, Any],
) -> List[Dict[str, Any]]:
    events = _extract_wake_telemetry(command_result.get("stdout"))
    if not events:
        return []

    peer_path = peer_runtime_path_for_state_dir(inbox_path.parent, agent)
    peer = read_runtime_breadcrumb(peer_path)
    if not isinstance(peer, dict) or peer.get("unreadable"):
        return events

    peer_agent = str(peer.get("agent") or "").strip().lower()
    if peer_agent and peer_agent != str(agent or "").strip().lower():
        return events
    if str(peer.get("session_id") or "").strip() != str(session_id or "").strip():
        return events

    changed = False
    for event in events:
        action = str(event.get("action") or "")
        title = _truncate_ui_label(event.get("desktop_thread_title"))
        title_project_match = event.get("title_project_match")
        generic_title = _is_generic_codex_thread_title(title)
        title_event = action.startswith("thread_title")
        unresolved_title = title_event and (
            generic_title
            or action == "thread_title_unknown"
            or ("title_project_match" in event and title_project_match is None)
        )
        cached_title_project_match = None if unresolved_title else title_project_match
        if unresolved_title:
            peer.pop("desktop_thread_title", None)
            peer.pop("desktop_thread_title_source", None)
            peer.pop("desktop_thread_title_observed_at", None)
            peer.pop("desktop_window_title", None)
            if title:
                peer["last_unresolved_desktop_thread_title"] = title
                peer["last_unresolved_desktop_thread_title_source"] = _truncate_ui_label(
                    event.get("desktop_thread_title_source"),
                    80,
                )
                peer["last_unresolved_desktop_thread_title_observed_at"] = str(event.get("timestamp") or utc_now())
            changed = True
        elif title_project_match is False:
            if title:
                peer["last_mismatched_desktop_thread_title"] = title
                peer["last_mismatched_desktop_thread_title_source"] = _truncate_ui_label(event.get("desktop_thread_title_source"), 80)
                peer["last_mismatched_desktop_thread_title_observed_at"] = str(event.get("timestamp") or utc_now())
            peer.pop("desktop_thread_title", None)
            peer.pop("desktop_thread_title_source", None)
            peer.pop("desktop_thread_title_observed_at", None)
            peer.pop("desktop_window_title", None)
            changed = True
        elif title:
            peer["desktop_thread_title"] = title
            peer["desktop_thread_title_source"] = _truncate_ui_label(event.get("desktop_thread_title_source"), 80)
            peer["desktop_thread_title_observed_at"] = str(event.get("timestamp") or utc_now())
            window_title = _truncate_ui_label(event.get("desktop_window_title"))
            if window_title:
                peer["desktop_window_title"] = window_title
            changed = True
        if "title_project_match" in event:
            peer["desktop_thread_title_project_match"] = cached_title_project_match
            changed = True
        if "expected_project_token" in event:
            peer["desktop_thread_title_expected_project_token"] = _truncate_ui_label(
                event.get("expected_project_token"),
                80,
            )
            changed = True
        if str(event.get("action") or "").startswith("wake_postflight"):
            peer["last_wake_postflight_action"] = str(event.get("action") or "")
            peer["last_wake_postflight_reason"] = _truncate_ui_label(event.get("reason"), 120)
            peer["last_wake_postflight_at"] = str(event.get("timestamp") or utc_now())
            changed = True
        if action == "foreground_codex_delivery_priority_no_restore":
            peer["last_wake_delivery_priority_action"] = action
            peer["last_wake_delivery_priority_at"] = str(event.get("timestamp") or utc_now())
            peer["last_wake_delivery_priority_target_thread_id"] = str(event.get("desktop_thread_id") or "")
            peer["last_wake_delivery_priority_previous_thread_title"] = _truncate_ui_label(
                event.get("previous_desktop_thread_title"),
                120,
            )
            peer["last_wake_delivery_priority_expected_thread_title"] = _truncate_ui_label(
                event.get("expected_desktop_thread_title"),
                120,
            )
            changed = True

    if changed:
        write_runtime_breadcrumb(peer_path, peer)
        _append_wake_audit(
            inbox_path,
            {
                "action": "wake_telemetry_cached",
                "agent": agent,
                "session_id": session_id,
                "message_id": message_id,
                "desktop_thread_title": None
                if peer.get("desktop_thread_title_project_match") is False
                else peer.get("desktop_thread_title"),
                "postflight_action": peer.get("last_wake_postflight_action"),
                "delivery_priority_action": peer.get("last_wake_delivery_priority_action"),
            },
        )
    return events


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
    toasted_ids: Optional[set] = None,
) -> None:
    if toasted_ids is None:
        toasted_ids = set(str(item) for item in load_seen(state_path).get("toasted_ids", []))

    def _merge(existing: Dict[str, Any]) -> Dict[str, Any]:
        existing_seen = {str(item) for item in existing.get("seen_ids", []) if str(item)}
        existing_toasted = {str(item) for item in existing.get("toasted_ids", []) if str(item)}
        merged_seen = list(existing_seen.union(str(item) for item in seen_ids if str(item)))[-500:]
        merged_toasted = list(existing_toasted.union(str(item) for item in toasted_ids if str(item)))[-500:]
        merged = dict(existing)
        merged.update({
            "seen_ids": merged_seen,
            "toasted_ids": merged_toasted,
            "pending_wake_verifications": pending[-200:],
            "paused_wake_messages": (paused_messages or [])[-200:],
            "unknown_origin_warnings": (unknown_origin_warnings or [])[-200:],
            "wake_fire_history": (wake_fire_history or [])[-200:],
            "claude_monitor_escalations": (existing.get("claude_monitor_escalations") or [])[-200:],
        })
        return merged

    saved = storage_update_json(
        state_path,
        {
            "seen_ids": [],
            "toasted_ids": [],
            "pending_wake_verifications": [],
            "paused_wake_messages": [],
            "unknown_origin_warnings": [],
            "wake_fire_history": [],
            "claude_monitor_escalations": [],
        },
        _merge,
    )
    seen_ids.clear()
    seen_ids.update(str(item) for item in saved.get("seen_ids", []) if str(item))


def _clear_override_wake_message(state_path: Path) -> None:
    """Remove next_override_wake_message from watcher state after it has been consumed."""
    def _clear(existing: Dict[str, Any]) -> Dict[str, Any]:
        result = dict(existing)
        result.pop("next_override_wake_message", None)
        return result
    storage_update_json(state_path, {}, _clear)


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


def notify_terminal(agent: str, session_id: str, messages: List[Dict[str, Any]], display_label: Optional[str] = None) -> None:
    count = len(messages)
    summary = messages[0].get("body", "")[:80].replace("\n", " ")
    label = display_label or (("..." + session_id[-8:]) if session_id else "")
    print(
        f"[agent-bridge] {utc_now()} -- {count} unread for {agent} "
        f"(session {label}): {summary!r}",
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
    display_label: Optional[str] = None,
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
        notify_terminal(agent, session_id, messages, display_label=display_label)
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
    if display_label:
        session_tail = display_label
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
        _notify_windows_balloon(agent, session_id, messages, display_label=display_label)


def _notify_windows_balloon(agent: str, session_id: str, messages: List[Dict[str, Any]], display_label: Optional[str] = None) -> None:
    """Legacy NotifyIcon balloon fallback (Windows 7+ compatible)."""
    import base64

    body = messages[0].get("body", "")[:120].replace("\n", " ").replace("\r", " ")
    label = display_label or (("..." + session_id[-8:]) if session_id else "")
    title = f"agent-bridge: new message for {agent} {label}".strip()
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
        ps_b64 = base64.b64encode(ps_script.encode("utf-16-le")).decode("ascii")
        subprocess.Popen(
            ["powershell", "-NoProfile", "-WindowStyle", "Hidden", "-EncodedCommand", ps_b64],
            stdout=subprocess.DEVNULL,
            stderr=subprocess.DEVNULL,
            creationflags=getattr(subprocess, "CREATE_NO_WINDOW", 0),
        )
    except OSError:
        notify_terminal(agent, session_id, messages, display_label=display_label)


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


def _apply_message_override(cmd: List[str], override_message: str) -> List[str]:
    """Replace the value following -Message in a command argv list.

    Walks the list looking for the literal token ``-Message`` and replaces the
    immediately following element with *override_message*.  Returns a copy; the
    original list is unchanged.  If ``-Message`` is absent the list is returned
    as-is so callers never receive an error for a template that doesn't carry the
    flag.
    """
    result = list(cmd)
    for i, arg in enumerate(result):
        if arg == "-Message" and i + 1 < len(result):
            result[i + 1] = override_message
            return result
    return result


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


def _resolve_command_template(
    session_config: Dict[str, Any],
    inbox_path: Path,
    override_wake_message: Optional[str] = None,
) -> Dict[str, Any]:
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
        "restore_thread_id": str(peer.get("restore_thread_id") or ""),
    }

    required_fields = _template_required_fields(template)
    missing = sorted(name for name in required_fields if name not in OPTIONAL_TEMPLATE_FIELDS and not mapping.get(name))
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

    cmd = _format_template(template, mapping)
    if override_wake_message and isinstance(cmd, list):
        cmd = _apply_message_override(cmd, override_wake_message)
    return {"ok": True, "command": cmd, "peer": peer}


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
    session_config = _resolve_session_config(session_config)
    agent = session_config["agent"]
    session_id = str(session_config.get("session_id") or "")
    project = str(session_config.get("project") or "")
    if not session_id:
        return pending
    inbox_path = Path(session_config["inbox"])
    on_message_command: Optional[str] = session_config.get("on_message_command")
    now = datetime.now(timezone.utc)
    kept: List[Dict[str, Any]] = []
    changed = False
    unknown_origin_warnings = unknown_origin_warnings or []
    wake_fire_history = wake_fire_history or []

    # Read any pending initiator override so the wake message shows who triggered
    # this nudge ("Claude says…", "User says…", etc.) rather than always "Watcher says…".
    # The override is one-shot: cleared from state after the first successful fire.
    _ow_state = load_seen(state_path)
    _next_override_wake_message: Optional[str] = _ow_state.get("next_override_wake_message") or None
    _override_consumed = False

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
            notify_terminal(
                agent,
                session_id,
                [_message_by_id(inbox_path, message_id) or {"id": message_id, "body": "wake delivery failed"}],
                display_label=_runtime_session_display_label(inbox_path.parent, agent, session_id, project),
            )
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

        breaker_mode = _breaker_recovery_mode(state_path=state_path, session_id=session_id)
        if breaker_mode == "open":
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

        _this_override = _next_override_wake_message if not _override_consumed else None
        resolved = _resolve_command_template(session_config, inbox_path, override_wake_message=_this_override)
        if resolved.get("result") is not None:
            command_result = resolved["result"]
        elif resolved.get("command"):
            _consume_breaker_recovery(
                state_path=state_path,
                session_id=session_id,
                mode=breaker_mode,
                inbox_path=inbox_path,
                agent=agent,
                message_id=message_id,
            )
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
        _cache_wake_telemetry(
            inbox_path=inbox_path,
            agent=agent,
            session_id=session_id,
            message_id=message_id,
            command_result=command_result,
        )
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
            if _this_override:
                _override_consumed = True
            # Wake spawn is not delivery; keep the row pending until
            # check_inbox/writeback stamps seen_at/read_at or retries exhaust.
            retry_count += 1
            entry["retry_count"] = retry_count
            entry["sent_at"] = utc_now()
            entry.pop("deferred_until", None)
            entry["last_retry_ok"] = True
            kept.append(entry)
            changed = True
            print(
                f"[agent-bridge] wake delivered for {agent} id={message_id}; awaiting receipt",
                flush=True,
            )
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
            entry["last_retry_ok"] = False
            kept.append(entry)
            changed = True
            print(
                f"[agent-bridge] wake failed for {agent} id={message_id}; retry {retry_count}/{max_retries}",
                flush=True,
            )

    if changed:
        _save_watcher_state(state_path, seen_ids, kept, paused_messages, unknown_origin_warnings, wake_fire_history)
    if _override_consumed:
        _clear_override_wake_message(state_path)
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
        if not message_id or message_id in existing_ids or message_id in seen_ids:
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
        if not message_id or message_id in existing_ids or message_id in seen_ids:
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
    session_config = _resolve_session_config(session_config)
    agent = session_config["agent"]
    session_id = str(session_config.get("session_id") or "")
    project = str(session_config.get("project") or "")
    if not session_id:
        return []
    inbox_path = Path(session_config["inbox"])
    on_message = session_config.get("on_message", "notify")
    on_message_command: Optional[str] = session_config.get("on_message_command")
    on_message_command_template: Optional[str] = session_config.get("on_message_command_template")

    effective_on_message = on_message
    if not toasts_enabled and on_message == "toast":
        effective_on_message = "log"

    state = load_seen(state_path)
    seen_ids.clear()
    seen_ids.update(str(item) for item in state.get("seen_ids", []) if str(item))
    pending: List[Dict[str, Any]] = list(state.get("pending_wake_verifications", []))
    paused_messages: List[Dict[str, Any]] = list(state.get("paused_wake_messages", []))
    unknown_origin_warnings: List[str] = list(state.get("unknown_origin_warnings", []))
    wake_fire_history: List[Dict[str, Any]] = list(state.get("wake_fire_history", []))
    toasted_ids: set = set(str(item) for item in state.get("toasted_ids", []))
    bridge_paused = _bridge_is_paused(inbox_path.parent)
    if not bridge_paused:
        filtered_paused_messages = [
            entry
            for entry in paused_messages
            if not (entry.get("agent") == agent and entry.get("session_id") == session_id)
        ]
        if len(filtered_paused_messages) != len(paused_messages):
            paused_messages = filtered_paused_messages
            _save_watcher_state(state_path, seen_ids, pending, paused_messages, unknown_origin_warnings, wake_fire_history, toasted_ids)
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

    command_configured = bool(on_message_command or on_message_command_template)
    unread = unread_for_session(inbox_path, session_id)
    escalated_ids: set = set()
    if agent == "claude":
        escalated_ids = set(
            _escalate_claude_unread_without_monitor(
                state_path=state_path,
                inbox_path=inbox_path,
                session_id=session_id,
                project=project,
                toasts_enabled=toasts_enabled,
                toast_expiry_minutes=toast_expiry_minutes,
                toast_max_in_tray=toast_max_in_tray,
                emit_user_visible=effective_on_message == "toast",
            )
        )
        if escalated_ids:
            seen_ids.update(escalated_ids)
            _save_watcher_state(
                state_path,
                seen_ids,
                pending,
                paused_messages,
                unknown_origin_warnings,
                wake_fire_history,
                toasted_ids,
            )
        _rearm_stale_unread_without_monitor(
            state_path=state_path,
            inbox_path=inbox_path,
            session_id=session_id,
            project=project,
            seen_ids=seen_ids,
            toasted_ids=toasted_ids,
            pending_ids=pending_ids,
            paused_ids=paused_ids,
        )
    new_msgs = [
        m for m in unread
        if m.get("id") not in seen_ids and m.get("id") not in pending_ids and m.get("id") not in paused_ids
    ]
    if command_configured and not new_msgs:
        recovery_mode = _breaker_recovery_mode(state_path=state_path, session_id=session_id)
        if recovery_mode in {"idle", "bypass"}:
            recovery_candidates = [
                m for m in unread
                if m.get("id") not in pending_ids and m.get("id") not in paused_ids
            ]
            if recovery_candidates:
                recovery_message = recovery_candidates[0]
                recovery_id = str(recovery_message.get("id", ""))
                if recovery_id in seen_ids:
                    seen_ids.remove(recovery_id)
                    _save_watcher_state(
                        state_path,
                        seen_ids,
                        pending,
                        paused_messages,
                        unknown_origin_warnings,
                        wake_fire_history,
                        toasted_ids,
                    )
                _append_wake_audit(
                    inbox_path,
                    {
                        "action": "wake_recovery_backlog_selected",
                        "agent": agent,
                        "session_id": session_id,
                        "message_id": recovery_id,
                        "mode": recovery_mode,
                    },
                )
                new_msgs = [recovery_message]
    if not new_msgs:
        return []
    display_label = _runtime_session_display_label(inbox_path.parent, agent, session_id, project)

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
                    f"[agent-bridge] {utc_now()} -- wake suppressed while paused for {agent} id={m.get('id')} session={display_label}",
                    flush=True,
                )
            return []
        for m in new_msgs:
            print(
                f"[agent-bridge] {utc_now()} -- new {agent} message id={m.get('id')} session={display_label}",
                flush=True,
            )
        for m in new_msgs:
            seen_ids.add(m["id"])
        _save_watcher_state(state_path, seen_ids, pending, paused_messages, unknown_origin_warnings, wake_fire_history)
        return [m["id"] for m in new_msgs]

    if effective_on_message == "toast":
        toast_msgs = [m for m in new_msgs if str(m.get("id", "")) not in toasted_ids]
        if toast_msgs:
            notify_windows_toast(
                agent,
                session_id,
                toast_msgs,
                toast_expiry_minutes=toast_expiry_minutes,
                toast_max_in_tray=toast_max_in_tray,
                display_label=display_label,
            )
            for message in toast_msgs:
                message_id = str(message.get("id", ""))
                if message_id:
                    toasted_ids.add(message_id)
            _save_watcher_state(
                state_path,
                seen_ids,
                pending,
                paused_messages,
                unknown_origin_warnings,
                wake_fire_history,
                toasted_ids,
            )
    else:
        notify_terminal(agent, session_id, new_msgs, display_label=display_label)

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
            breaker_mode = _breaker_recovery_mode(state_path=state_path, session_id=session_id)
            if breaker_mode == "open":
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
            _consume_breaker_recovery(
                state_path=state_path,
                session_id=session_id,
                mode=breaker_mode,
                inbox_path=inbox_path,
                agent=agent,
                message_id=str(new_msgs[0].get("id", "")) if new_msgs else None,
            )
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

    if command_configured:
        _cache_wake_telemetry(
            inbox_path=inbox_path,
            agent=agent,
            session_id=session_id,
            message_id=str(new_msgs[0].get("id", "")) if new_msgs else "",
            command_result=command_result,
        )

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


def _maybe_notify_mcp_server_restart(
    *,
    state_dir: Path,
    sessions: List[Dict[str, Any]],
) -> None:
    """Send a bridge-inbox notification when the MCP server wrapper flags a restart.

    The wrapper writes server-status.json with needs_notification=true after each
    code-change-triggered restart.  We pick it up here, send a single control
    message to the Claude inbox so the user sees "server is back up" in chat,
    then clear the flag to prevent duplicate sends.
    """
    status_path = state_dir / SERVER_STATUS_FILENAME
    try:
        if not status_path.exists():
            return
        data = storage_read_json(status_path)
        if not data or not data.get("needs_notification"):
            return

        last_restart_at = str(data.get("last_restart_at") or "unknown")
        elapsed_ms = data.get("last_restart_elapsed_ms")
        child_pid = data.get("child_pid")
        elapsed_str = ("%dms" % elapsed_ms) if elapsed_ms is not None else "unknown"

        for s in sessions:
            resolved = _resolve_session_config(s)
            if str(resolved.get("agent") or "") != "claude":
                continue
            session_id = str(resolved.get("session_id") or "")
            project = str(resolved.get("project") or "")
            if not session_id:
                continue

            body = (
                "MCP server restarted and is back up (PID %s).\n"
                "Restarted at %s — took %s.\n"
                "The 'Server disconnected' banner can be dismissed safely."
            ) % (child_pid, last_restart_at, elapsed_str)

            bridge = AgentBridge(state_dir)
            with bridge._locked():
                message_id = bridge._append_control_message(
                    target_agent="claude",
                    session_id=session_id,
                    sender="agent-bridge",
                    control_type=MCP_SERVER_RESTARTED_CONTROL_TYPE,
                    summary="MCP server restarted — back up in %s" % elapsed_str,
                    body=body,
                    status="info",
                    replace_existing_control=False,
                    inbox_level="session",
                    extra_fields={
                        "mcp_restart_at": last_restart_at,
                        "mcp_restart_elapsed_ms": elapsed_ms,
                        "mcp_child_pid": child_pid,
                    },
                )
            if message_id:
                print(
                    "[agent-bridge] MCP_SERVER_RESTARTED notification sent to Claude "
                    "session ...%s (pid=%s elapsed=%s)" % (session_id[-8:], child_pid, elapsed_str),
                    flush=True,
                )
            break

        # Clear the flag regardless of whether we found a session — avoids
        # a retry storm if no Claude session is currently registered.
        data["needs_notification"] = False
        storage_write_json(status_path, data)
    except Exception as exc:
        print("[agent-bridge] failed to process MCP server restart notification: %s" % exc, flush=True)


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
        resolved = _resolve_session_config(s)
        session_id = str(resolved.get("session_id") or "")
        project = str(resolved.get("project") or "")
        source = str(s.get("session_id_source") or "static")
        display_label = _runtime_session_display_label(
            state_dir or Path(s["inbox"]).parent,
            str(s["agent"]),
            session_id,
            project,
        )
        print(
            f"  agent={s['agent']}  session={display_label}  source={source}  inbox={s['inbox']}",
            flush=True,
        )

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

                _maybe_queue_commit_monitor_restart_controls(
                    config=config,
                    sessions=sessions,
                    state_path=state_path,
                    state_dir=state_dir,
                )
                _maybe_notify_mcp_server_restart(
                    state_dir=state_dir,
                    sessions=sessions,
                )

            for s in sessions:
                if state_dir:
                    _maybe_queue_monitor_stale_control(
                        session_config=s,
                        state_path=state_path,
                        state_dir=state_dir,
                    )
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
