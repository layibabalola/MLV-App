import argparse
import hashlib
import json
import os
import subprocess
import sys
import time
import uuid
from datetime import datetime, timezone
from pathlib import Path
from typing import Any, Dict, List, Optional, Tuple

from agent_bridge import AgentBridge
from configure_watcher import PARENT_THREAD_ID_KEY, configure_watcher
from core.paths import ensure_bridge_root_manifest, expand_path_arg, resolve_bridge_paths
from core.processes import acquire_singleton_lease, build_lease, command_line_hash, is_process_alive, lease_status, read_lease, write_lease
from core.runtime import (
    MONITOR_RUNTIME_MIN_TTL_S,
    build_peer_runtime_breadcrumb,
    monitor_runtime_path_for_state_dir,
    peer_runtime_path_for_state_dir,
    read_runtime_breadcrumb,
    write_runtime_breadcrumb,
)
from core.settings import load_settings
from project_identity import derive_project_identity

SUBAGENT_ENV_MARKERS = {
    "codex": ("CODEX_SUBAGENT", "CODEX_SUBAGENT_ID"),
    "claude": ("CLAUDE_SUBAGENT", "CLAUDE_AGENT_DEPTH"),
}
THREAD_ENV_KEYS = {
    "codex": ("CODEX_THREAD_ID", "CODEX_PARENT_THREAD_ID"),
    "claude": ("CLAUDE_THREAD_ID", "CLAUDE_PARENT_THREAD_ID"),
}
MAX_NORMAL_BOOTSTRAP_DEPTH = 3
WATCHER_RESTART_CODE_FILES = (
    "watcher.py",
    "wake_codex.ps1",
    "bootstrap_session.py",
    "configure_watcher.py",
    "agent_bridge.py",
    "core/runtime.py",
)
PAIRING_INTENT_CHOICES = {"ask_first", "active_primary", "background"}


def detect_bootstrap_origin(
    *,
    agent: str,
    env: Optional[Dict[str, str]] = None,
    process_depth: Optional[int] = None,
) -> Tuple[str, Dict[str, Any]]:
    source_env = dict(os.environ) if env is None else env
    normalized_agent = "codex" if agent == "codex" else "claude"
    signals: Dict[str, Any] = {
        "env_marker": None,
        "process_depth": process_depth,
        "parent_thread_id_mismatch": False,
        "mcp_tag": None,
    }

    for marker in SUBAGENT_ENV_MARKERS.get(normalized_agent, ()):
        value = str(source_env.get(marker) or "").strip()
        if value and value != "0":
            signals["env_marker"] = f"{marker}={value}"
            return "subagent", signals

    thread_key, parent_thread_key = THREAD_ENV_KEYS[normalized_agent]
    thread_id = str(source_env.get(thread_key) or "").strip()
    parent_thread_id = str(source_env.get(parent_thread_key) or "").strip()
    if parent_thread_id and thread_id and parent_thread_id != thread_id:
        signals["parent_thread_id_mismatch"] = True
        return "subagent", signals

    if process_depth is not None and process_depth > MAX_NORMAL_BOOTSTRAP_DEPTH:
        signals["process_depth"] = process_depth
        return "unknown", signals

    if not thread_id:
        return "unknown", signals
    return "parent", signals


def _state_dir_from_watcher_config(watcher_config: Path) -> Path:
    try:
        data = json.loads(watcher_config.read_text(encoding="utf-8"))
        sessions = data.get("sessions", [])
        if sessions:
            return Path(sessions[0]["inbox"]).parent
    except Exception:
        pass
    return watcher_config.parent / "state"


def watcher_code_signature() -> Dict[str, Any]:
    base_dir = Path(__file__).resolve().parent
    digest = hashlib.sha256()
    files: List[Dict[str, Any]] = []
    for relative in WATCHER_RESTART_CODE_FILES:
        path = base_dir / relative
        entry: Dict[str, Any] = {"path": str(path), "relative_path": relative}
        try:
            data = path.read_bytes()
            stat = path.stat()
        except OSError as exc:
            entry["error"] = str(exc)
            data = b""
        else:
            entry["sha256"] = hashlib.sha256(data).hexdigest()
            entry["mtime_ns"] = stat.st_mtime_ns
            entry["size"] = stat.st_size
        digest.update(relative.encode("utf-8"))
        digest.update(b"\0")
        digest.update(data)
        digest.update(b"\0")
        files.append(entry)
    return {"schema_version": 1, "signature": digest.hexdigest(), "files": files}


def _watcher_code_restart_reason(lease_path: Path, current_signature: Dict[str, Any]) -> Optional[str]:
    if not lease_path.exists():
        return "missing_lease"
    try:
        lease = read_lease(lease_path)
    except Exception:
        return "unreadable_lease"
    previous = lease.get("watcher_code_signature")
    if not isinstance(previous, dict) or not previous.get("signature"):
        return "missing_signature"
    if previous.get("signature") != current_signature.get("signature"):
        return "signature_changed"
    return None


def _terminate_process(pid: int, *, timeout_seconds: float = 5.0) -> bool:
    if pid <= 0 or pid == os.getpid():
        return False
    try:
        if sys.platform == "win32":
            subprocess.run(
                ["taskkill", "/PID", str(pid), "/T", "/F"],
                stdout=subprocess.DEVNULL,
                stderr=subprocess.DEVNULL,
                timeout=timeout_seconds,
                check=False,
            )
        else:
            import signal as _signal

            os.kill(pid, _signal.SIGTERM)
    except (OSError, subprocess.TimeoutExpired):
        return False
    return True


def _command_line_contains_path(command_line: str, path: Path) -> bool:
    haystack = command_line.replace('"', "").replace("'", "").lower()
    needle = str(path).replace('"', "").replace("'", "").lower()
    return needle in haystack


def _enumerate_watcher_processes() -> List[Dict[str, Any]]:
    if sys.platform == "win32":
        script = (
            "Get-CimInstance Win32_Process | "
            "Where-Object { $_.CommandLine -like '*watcher.py*' } | "
            "Select-Object ProcessId,ParentProcessId,CommandLine,CreationDate | "
            "ConvertTo-Json -Compress"
        )
        try:
            proc = subprocess.run(
                ["powershell", "-NoProfile", "-Command", script],
                stdout=subprocess.PIPE,
                stderr=subprocess.DEVNULL,
                text=True,
                timeout=10,
                check=False,
            )
        except (OSError, subprocess.TimeoutExpired):
            return []
        if proc.returncode != 0 or not proc.stdout.strip():
            return []
        try:
            parsed = json.loads(proc.stdout)
        except json.JSONDecodeError:
            return []
        rows = parsed if isinstance(parsed, list) else [parsed]
        return [
            {
                "pid": row.get("ProcessId"),
                "parent_pid": row.get("ParentProcessId"),
                "command_line": row.get("CommandLine") or "",
                "started_at": row.get("CreationDate"),
            }
            for row in rows
            if isinstance(row, dict)
        ]

    try:
        proc = subprocess.run(
            ["ps", "-eo", "pid=,ppid=,args="],
            stdout=subprocess.PIPE,
            stderr=subprocess.DEVNULL,
            text=True,
            timeout=10,
            check=False,
        )
    except (OSError, subprocess.TimeoutExpired):
        return []
    if proc.returncode != 0:
        return []
    rows = []
    for line in proc.stdout.splitlines():
        parts = line.strip().split(None, 2)
        if len(parts) < 3 or "watcher.py" not in parts[2]:
            continue
        rows.append({"pid": parts[0], "parent_pid": parts[1], "command_line": parts[2], "started_at": None})
    return rows


def sweep_orphan_watchers(
    watcher_config: Path,
    state_dir: Optional[Path] = None,
    bridge: Optional[AgentBridge] = None,
) -> Dict[str, Any]:
    resolved_state_dir = state_dir or _state_dir_from_watcher_config(watcher_config)
    watcher_script_path = Path(__file__).with_name("watcher.py")
    watcher_script = watcher_script_path.resolve()
    resolved_config = watcher_config.resolve()
    lease_path = resolved_state_dir / "locks" / "watcher.lock"
    lease_pid: Optional[int] = None
    try:
        lease = read_lease(lease_path)
        lease_pid = int(lease.get("pid") or 0) or None
    except Exception:
        lease_pid = None

    bridge_for_audit = bridge or AgentBridge(resolved_state_dir)
    killed: List[Dict[str, Any]] = []
    candidates = 0
    for process in _enumerate_watcher_processes():
        try:
            pid = int(process.get("pid") or 0)
        except (TypeError, ValueError):
            continue
        if pid <= 0 or pid == os.getpid():
            continue
        command_line = str(process.get("command_line") or "")
        if not command_line or "watcher.py" not in command_line:
            continue
        if not (
            _command_line_contains_path(command_line, watcher_script)
            or _command_line_contains_path(command_line, watcher_script_path)
        ):
            continue
        if not (
            _command_line_contains_path(command_line, resolved_config)
            or _command_line_contains_path(command_line, watcher_config)
        ):
            continue
        candidates += 1
        if lease_pid is not None and pid == lease_pid:
            continue

        stopped = _terminate_process(pid)
        record = {
            "pid": pid,
            "parent_pid": process.get("parent_pid"),
            "started_at": process.get("started_at"),
            "stopped": stopped,
            "command_preview": command_line[:500],
        }
        killed.append(record)
        bridge_for_audit._audit(
            {
                "id": str(uuid.uuid4()),
                "timestamp": time.strftime("%Y-%m-%dT%H:%M:%S+00:00", time.gmtime()),
                "action": "orphan_watcher_killed",
                "accepted": True,
                "pid": pid,
                "parent_pid": process.get("parent_pid"),
                "started_at": process.get("started_at"),
                "stopped": stopped,
                "lease_pid": lease_pid,
                "watcher_config": str(resolved_config),
                "command_preview": command_line[:500],
            }
        )

    return {
        "status": "swept" if killed else "no_orphans",
        "candidate_count": candidates,
        "orphan_count": len(killed),
        "lease_pid": lease_pid,
        "killed": killed,
    }


def restart_watcher_for_code_change(watcher_config: Path, state_dir: Optional[Path] = None) -> Dict[str, Any]:
    resolved_state_dir = state_dir or _state_dir_from_watcher_config(watcher_config)
    pid_path = watcher_config.parent / "watcher.pid"
    lease_path = resolved_state_dir / "locks" / "watcher.lock"
    current_signature = watcher_code_signature()
    pid_path_exists = pid_path.exists()
    reason = _watcher_code_restart_reason(lease_path, current_signature)
    if reason == "missing_lease" and not pid_path_exists:
        return {"status": "no_existing_watcher", "reason": reason, "watcher_code_signature": current_signature}
    if not reason:
        return {"status": "current", "reason": None, "watcher_code_signature": current_signature}

    pid: Optional[int] = None
    if pid_path.exists():
        try:
            pid = int(pid_path.read_text(encoding="utf-8").strip())
        except (ValueError, OSError):
            pid = None
    if pid is None:
        try:
            lease = read_lease(lease_path)
            pid = int(lease.get("pid") or 0) or None
        except Exception:
            pid = None

    stopped = False
    if pid is not None and is_process_alive(pid):
        stopped = _terminate_process(pid)
    pid_path.unlink(missing_ok=True)
    lease_path.unlink(missing_ok=True)
    return {
        "status": "restart_required",
        "reason": reason,
        "pid": pid,
        "stopped": stopped,
        "watcher_code_signature": current_signature,
    }


def ensure_watcher(watcher_config: Path, state_dir: Optional[Path] = None) -> Dict[str, Any]:
    """Start the watcher daemon if it is not already running.

    Uses a role lease under state/locks plus watcher.pid compatibility marker.
    Returns a dict with status and PID for inclusion in bootstrap output.
    """
    pid_path = watcher_config.parent / "watcher.pid"
    watcher_script = Path(__file__).with_name("watcher.py")
    resolved_state_dir = state_dir or _state_dir_from_watcher_config(watcher_config)
    command = [sys.executable, str(watcher_script), "--config", str(watcher_config)]
    lease_path = resolved_state_dir / "locks" / "watcher.lock"

    if pid_path.exists():
        try:
            existing_pid = int(pid_path.read_text(encoding="utf-8").strip())
            if is_process_alive(existing_pid):
                acquired = acquire_singleton_lease(
                    lease_path,
                    role="watcher",
                    command=command,
                    state_dir=resolved_state_dir,
                    pid=existing_pid,
                )
                return {
                    "status": "already_running",
                    "pid": existing_pid,
                    "lease": acquired.get("lease"),
                    "command_line_hash": command_line_hash(command),
                }
        except (ValueError, OSError):
            pass

    # Stale or missing PID — spawn a fresh watcher
    current_lease = lease_status(lease_path, expected_command=command)
    if current_lease.get("status") == "running":
        return {
            "status": "already_running",
            "pid": current_lease.get("pid"),
            "lease": current_lease.get("lease"),
            "command_line_hash": command_line_hash(command),
        }

    proc = subprocess.Popen(
        command,
        stdout=subprocess.DEVNULL,
        stderr=subprocess.DEVNULL,
        start_new_session=True,
    )
    lease_record = build_lease(
        role="watcher",
        command=command,
        state_dir=resolved_state_dir,
        pid=proc.pid,
    )
    lease_record["watcher_code_signature"] = watcher_code_signature()
    write_lease(lease_path, lease_record)
    pid_path.write_text(str(proc.pid), encoding="utf-8")
    return {
        "status": "started",
        "pid": proc.pid,
        "lease": lease_record,
        "command_line_hash": command_line_hash(command),
    }


def _desktop_thread_id_from_env(agent: str) -> Optional[str]:
    candidates = []
    if agent == "codex":
        candidates.extend(["CODEX_THREAD_ID", "CODEX_PARENT_THREAD_ID"])
    else:
        candidates.extend(["CLAUDE_THREAD_ID", "CLAUDE_DESKTOP_THREAD_ID"])
    for key in candidates:
        value = os.environ.get(key)
        if value:
            return value
    return None


def _parent_thread_id_from_env(agent: str) -> Optional[str]:
    key = "CODEX_PARENT_THREAD_ID" if agent == "codex" else "CLAUDE_PARENT_THREAD_ID"
    value = os.environ.get(key)
    return value.strip() if value else None


def _thread_id_from_env(agent: str) -> Optional[str]:
    key = "CODEX_THREAD_ID" if agent == "codex" else "CLAUDE_THREAD_ID"
    value = os.environ.get(key)
    return value.strip() if value else None


def _desktop_thread_id_for_bootstrap(agent: str, watcher_config: Optional[Path]) -> Optional[str]:
    if watcher_config is not None and watcher_config.exists():
        try:
            data = json.loads(watcher_config.read_text(encoding="utf-8"))
            if agent == "codex":
                value = data.get(PARENT_THREAD_ID_KEY)
                if value:
                    return str(value)
        except Exception:
            pass
    return _desktop_thread_id_from_env(agent)


def _normalize_pairing_intent(value: Optional[str]) -> Optional[str]:
    if value is None:
        return None
    normalized = value.strip().lower().replace("-", "_")
    if not normalized:
        return None
    if normalized not in PAIRING_INTENT_CHOICES:
        raise ValueError("pairing intent must be one of: %s" % ", ".join(sorted(PAIRING_INTENT_CHOICES)))
    return normalized


def _resolve_pairing_intent(state_dir: Path, explicit_intent: Optional[str]) -> Dict[str, Any]:
    normalized_explicit = _normalize_pairing_intent(explicit_intent)
    if normalized_explicit:
        return {"intent": normalized_explicit, "source": "cli"}
    settings = load_settings(state_dir)
    return {
        "intent": settings.default_pairing_intent,
        "source": "settings.json" if (Path(state_dir).parent / "settings.json").exists() else "hardcoded_default",
        "pending_pair_timeout_seconds": settings.pending_pair_timeout_seconds,
    }


def _active_same_agent_session(bridge: AgentBridge, *, agent: str, project: str) -> Optional[str]:
    status = bridge.session_status(project)
    if not status.ok:
        return None
    active = status.data.get("active") or {}
    value = active.get(agent)
    return str(value) if value else None


def _pairing_prompt(*, agent: str, session_id: str, project: str, active_session: str, timeout_seconds: int) -> Dict[str, Any]:
    return {
        "status": "pending_pair",
        "prompt": (
            "Would you like this session to pair with the remote peer for project %s? "
            "It will supersede the existing %s pairing %s. Reply Yes / Pair this thread to continue, "
            "or No to keep this chat in background mode."
        )
        % (project, agent, active_session),
        "yes_examples": ["yes", "pair this thread", "pair this chat", "pair with peer"],
        "no_examples": ["no", "do not pair", "background chat", "incognito"],
        "fallback_after_seconds": timeout_seconds,
        "fallback_intent": "background",
        "session_id": session_id,
        "active_session_to_supersede": active_session,
    }


def _parse_runtime_dt(value: Any) -> Optional[datetime]:
    if not value:
        return None
    try:
        parsed = datetime.fromisoformat(str(value))
    except ValueError:
        return None
    if parsed.tzinfo is None:
        parsed = parsed.replace(tzinfo=timezone.utc)
    return parsed


def _claude_monitor_runtime_status(*, state_dir: Path, session_id: str, project: str) -> Dict[str, Any]:
    runtime_path = monitor_runtime_path_for_state_dir(state_dir, "claude", session_id)
    data = read_runtime_breadcrumb(runtime_path)
    base: Dict[str, Any] = {
        "path": str(runtime_path),
        "status": "missing",
        "fresh": False,
        "expected_buckets": [session_id, project],
        "repair_required": True,
    }
    if not data:
        return base
    base["data"] = data
    if data.get("unreadable"):
        base.update({"status": "unreadable", "reason": data.get("error") or "runtime_unreadable"})
        return base
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
    if str(data.get("script_name") or Path(str(data.get("script_path") or "")).name) != "bridge_monitor_poll.py":
        mismatches.append("script_path")
    pid = int(data.get("monitor_pid") or 0)
    if pid and not is_process_alive(pid):
        mismatches.append("monitor_pid")
    heartbeat = _parse_runtime_dt(data.get("heartbeat_at"))
    poll_interval = data.get("poll_interval_seconds")
    try:
        ttl_seconds = max(MONITOR_RUNTIME_MIN_TTL_S, int(float(poll_interval or 0) * 3))
    except (TypeError, ValueError):
        ttl_seconds = MONITOR_RUNTIME_MIN_TTL_S
    age_seconds = None
    if heartbeat:
        age_seconds = max(0, int((datetime.now(timezone.utc) - heartbeat).total_seconds()))
    else:
        mismatches.append("heartbeat_at")
    base["age_seconds"] = age_seconds
    base["freshness_ttl_seconds"] = ttl_seconds
    if mismatches:
        base.update({"status": "misbound", "reason": ",".join(mismatches), "mismatches": mismatches})
        return base
    if age_seconds is None or age_seconds > ttl_seconds:
        base.update({"status": "stale", "reason": "heartbeat_expired"})
        return base
    base.update({"status": "current", "fresh": True, "repair_required": False})
    return base


def _claude_monitor_reminder(*, state_dir: Path, session_id: str, project: str) -> Dict[str, Any]:
    inbox_path = state_dir / "inbox-claude.jsonl"
    monitor_script = Path(__file__).resolve().parent / "bridge_monitor_poll.py"
    command_hint = (
        "Monitor(persistent=True, command=\"%s -u \\\"%s\\\" --state-dir \\\"%s\\\" "
        "--agent claude --session-id %s --project %s --poll-interval-seconds 2\")"
        % (sys.executable, monitor_script, state_dir, session_id, project)
    )
    runtime_status = _claude_monitor_runtime_status(state_dir=state_dir, session_id=session_id, project=project)
    monitor_armed = runtime_status.get("status") == "current"
    return {
        "status": "required_until_thread_addressable_wake_exists",
        "banner": (
            "MONITOR ARMED - Claude bridge Monitor heartbeat is fresh."
            if monitor_armed
            else "MONITOR NOT YET ARMED - execute the Claude bridge Monitor now."
        ),
        "command_hint": command_hint,
        "monitor_script": str(monitor_script),
        "monitor_script_name": "bridge_monitor_poll.py",
        "monitor_armed": monitor_armed,
        "monitor_runtime": runtime_status,
        "not_probe_server": True,
        "wake_claude_status": "unsupported_fail_closed",
        "wake_claude_reason": (
            "Claude Desktop has no verified thread-addressable deeplink/target "
            "contract in Agent Bridge; wake_claude.ps1 is diagnostic-only and "
            "refuses SendKeys."
        ),
        "private_session_id": session_id,
        "project_bucket": project,
        "inbox": str(inbox_path),
    }


def _trusted_parent_thread_drift(
    bridge: AgentBridge,
    *,
    agent: str,
    project: str,
    incoming_session_id: str,
    incoming_thread_id: Optional[str],
) -> Optional[Dict[str, Any]]:
    if agent != "codex" or not incoming_thread_id:
        return None
    status = bridge.session_status(project)
    if not status.ok:
        return None
    trusted = (status.data.get("trusted_parent") or {}).get(agent) or {}
    trusted_session_id = trusted.get("session_id")
    if not trusted_session_id or trusted_session_id == incoming_session_id:
        return None
    trusted_record = (status.data.get("sessions") or {}).get(trusted_session_id) or {}
    trusted_thread_id = (
        trusted_record.get("desktop_thread_id")
        or trusted_record.get("bootstrap_parent_thread_id")
        or trusted_record.get("bootstrap_thread_id")
    )
    if not trusted_thread_id or str(trusted_thread_id) == incoming_thread_id:
        return None
    return {
        "trusted_session_id": trusted_session_id,
        "trusted_thread_id": str(trusted_thread_id),
        "incoming_session_id": incoming_session_id,
        "incoming_thread_id": incoming_thread_id,
    }


def bootstrap(
    *,
    state_dir: Path,
    agent: str,
    cwd: Optional[str],
    previous_session_id: Optional[str],
    session_id: Optional[str],
    project: Optional[str],
    handshake_retries: int,
    watcher_config: Optional[Path] = None,
    start_watcher: bool = True,
    restart_watcher_if_code_changed: bool = True,
    replace_trusted_parent: bool = False,
    pairing_intent: Optional[str] = None,
) -> Dict[str, Any]:
    bridge = AgentBridge(state_dir)
    identity = derive_project_identity(cwd)
    project_name = project or identity["rendezvous"]
    new_session = session_id or str(uuid.uuid4())
    peer_agent = "claude" if agent == "codex" else "codex"
    detected_bootstrap_origin, subagent_signals = detect_bootstrap_origin(agent=agent)
    bootstrap_origin = detected_bootstrap_origin
    bootstrap_thread_id = _thread_id_from_env(agent)
    bootstrap_parent_thread_id = _parent_thread_id_from_env(agent)
    retargeted_to_parent = False
    resolved_pairing = _resolve_pairing_intent(state_dir, pairing_intent)
    resolved_pairing.setdefault("pending_pair_timeout_seconds", load_settings(state_dir).pending_pair_timeout_seconds)

    bridge._audit(
        {
            "id": str(uuid.uuid4()),
            "timestamp": time.strftime("%Y-%m-%dT%H:%M:%S+00:00", time.gmtime()),
            "action": "bootstrap_origin_resolved",
            "agent": agent,
            "session_id": new_session,
            "project": project_name,
            "origin": detected_bootstrap_origin,
            "signals": subagent_signals,
            "accepted": True,
        }
    )
    bridge._audit(
        {
            "id": str(uuid.uuid4()),
            "timestamp": time.strftime("%Y-%m-%dT%H:%M:%S+00:00", time.gmtime()),
            "action": "pairing_intent_resolved",
            "agent": agent,
            "session_id": new_session,
            "project": project_name,
            "pairing_intent": resolved_pairing["intent"],
            "source": resolved_pairing["source"],
            "pending_pair_timeout_seconds": resolved_pairing.get("pending_pair_timeout_seconds"),
            "accepted": True,
        }
    )
    if detected_bootstrap_origin == "subagent" and bootstrap_parent_thread_id:
        retargeted_to_parent = True
        bootstrap_origin = "parent"
        bridge._audit(
            {
                "id": str(uuid.uuid4()),
                "timestamp": time.strftime("%Y-%m-%dT%H:%M:%S+00:00", time.gmtime()),
                "action": "bootstrap_subagent_retargeted_to_parent",
                "agent": agent,
                "session_id": new_session,
                "project": project_name,
                "bootstrap_thread_id": bootstrap_thread_id,
                "bootstrap_parent_thread_id": bootstrap_parent_thread_id,
                "signals": subagent_signals,
                "accepted": True,
            }
        )
    elif detected_bootstrap_origin == "subagent":
        bridge._audit(
            {
                "id": str(uuid.uuid4()),
                "timestamp": time.strftime("%Y-%m-%dT%H:%M:%S+00:00", time.gmtime()),
                "action": "bootstrap_subagent_refused",
                "agent": agent,
                "session_id": new_session,
                "project": project_name,
                "signals": subagent_signals,
                "accepted": False,
            }
        )
        return {
            "identity": identity,
            "project": project_name,
            "agent": agent,
            "session_id": new_session,
            "peer_agent": peer_agent,
            "bootstrap_origin": bootstrap_origin,
            "detected_bootstrap_origin": detected_bootstrap_origin,
            "subagent_signals": subagent_signals,
            "refused": True,
            "refusal_reason": "subagent bootstrap refused; only the parent thread should run bootstrap_session.py",
            "exit_code": 3,
        }

    desktop_thread_id = _desktop_thread_id_for_bootstrap(agent, watcher_config)
    if retargeted_to_parent and bootstrap_parent_thread_id:
        desktop_thread_id = bootstrap_parent_thread_id
    if bootstrap_origin == "parent" and not replace_trusted_parent:
        drift = _trusted_parent_thread_drift(
            bridge,
            agent=agent,
            project=project_name,
            incoming_session_id=new_session,
            incoming_thread_id=desktop_thread_id,
        )
        if drift is not None:
            bridge._audit(
                {
                    "id": str(uuid.uuid4()),
                    "timestamp": time.strftime("%Y-%m-%dT%H:%M:%S+00:00", time.gmtime()),
                    "action": "bootstrap_trusted_parent_drift_refused",
                    "agent": agent,
                    "session_id": new_session,
                    "project": project_name,
                    "accepted": False,
                    "reason": "trusted_parent_thread_drift_requires_explicit_repair",
                    **drift,
                }
            )
            return {
                "identity": identity,
                "project": project_name,
                "agent": agent,
                "session_id": new_session,
                "peer_agent": peer_agent,
                "bootstrap_origin": bootstrap_origin,
                "detected_bootstrap_origin": detected_bootstrap_origin,
                "subagent_signals": subagent_signals,
                "refused": True,
                "refusal_reason": (
                    "codex bootstrap refused because it would replace trusted parent thread "
                    f"{drift['trusted_thread_id']} with {drift['incoming_thread_id']}; "
                    "run from the trusted parent, say 'pair this chat', or use --replace-trusted-parent "
                    "for an intentional repair"
                ),
                "trusted_parent_drift": drift,
                "exit_code": 3,
            }

    active_same_agent = _active_same_agent_session(bridge, agent=agent, project=project_name)
    resolved_intent = resolved_pairing["intent"]
    should_gate_pairing = not previous_session_id and not replace_trusted_parent
    if should_gate_pairing and active_same_agent and active_same_agent != new_session and resolved_intent in {"ask_first", "background"}:
        consent_timeout = int(resolved_pairing.get("pending_pair_timeout_seconds") or 120)
        registration = bridge.register_non_primary_session(
            agent=agent,
            session_id=new_session,
            project=project_name,
            pairing_intent=resolved_intent,
            bootstrap_origin=bootstrap_origin,
            consent_timeout_seconds=consent_timeout if resolved_intent == "ask_first" else None,
            desktop_thread_id=desktop_thread_id,
            bootstrap_thread_id=bootstrap_thread_id,
            bootstrap_parent_thread_id=bootstrap_parent_thread_id,
        )
        prompt = (
            _pairing_prompt(
                agent=agent,
                session_id=new_session,
                project=project_name,
                active_session=active_same_agent,
                timeout_seconds=consent_timeout,
            )
            if resolved_intent == "ask_first"
            else None
        )
        bridge._audit(
            {
                "id": str(uuid.uuid4()),
                "timestamp": time.strftime("%Y-%m-%dT%H:%M:%S+00:00", time.gmtime()),
                "action": "pairing_intent_prompted" if prompt else "pairing_intent_backgrounded",
                "agent": agent,
                "session_id": new_session,
                "project": project_name,
                "pairing_intent": resolved_intent,
                "active_session_preserved": active_same_agent,
                "accepted": registration.ok,
            }
        )
        return {
            "identity": identity,
            "project": project_name,
            "agent": agent,
            "session_id": new_session,
            "peer_agent": peer_agent,
            "previous_session_id": previous_session_id,
            "drained_previous_messages": [],
            "activation": {
                "ok": registration.ok,
                "status": registration.status,
                "message": registration.message,
                "data": registration.data,
            },
            "handshake": {
                "ok": True,
                "status": "skipped_non_primary",
                "message": "HANDSHAKE skipped because this session did not become active_primary.",
                "data": {},
                "attempts": 0,
            },
            "watcher": None,
            "watcher_process": {
                "status": "not_started",
                "reason": "non_primary_pairing_intent",
            },
            "watcher_restart_check": None,
            "watcher_orphan_sweep": None,
            "peer_runtime": None,
            "bootstrap_origin": bootstrap_origin,
            "detected_bootstrap_origin": detected_bootstrap_origin,
            "subagent_signals": subagent_signals,
            "retargeted_to_parent": retargeted_to_parent,
            "pairing_intent": resolved_pairing,
            "pairing_prompt": prompt,
            "pairing_hint": None
            if prompt
            else {
                "status": "background",
                "message": "This chat is running in background mode. Type 'Pair this thread' later to promote it.",
            },
            "claude_monitor_reminder": None,
        }

    # activate_session auto-detects the previous same-agent session from the
    # registry, drains its unread messages BEFORE stamping superseded_at, and
    # returns them in data["drained_messages"].  This is atomic under the bridge
    # lock so there is no TOCTOU window between reading the registry and retiring.
    activation = bridge.activate_session(
        agent=agent,
        session_id=new_session,
        project=project_name,
        bootstrap_origin=bootstrap_origin,
        allow_supersede=bootstrap_origin != "unknown",
        trusted_parent_eligible=bootstrap_origin == "parent",
        pairing_intent=resolved_pairing["intent"],
    )
    drained: List[Dict[str, Any]] = activation.data.get("drained_messages", []) if activation.ok else []
    peer_session = activation.data.get("active_peer_session") if activation.ok else None

    # Mark every drained message read immediately.  activate_session may promote
    # an old private-session message into the project bucket, so mark by id
    # without a session filter instead of guessing which bucket now owns it.
    for msg in drained:
        msg_id = msg.get("id")
        if msg_id:
            bridge.mark_read(agent=agent, message_id=msg_id, session_id=None)

    active_session_unread_result = bridge.check_inbox(
        agent=agent,
        session_id=new_session,
        include_parents=False,
        mark_read=False,
        record_seen=True,
    )
    active_session_unread = {
        "ok": active_session_unread_result.ok,
        "status": active_session_unread_result.status,
        "message": active_session_unread_result.message,
        "count": int((active_session_unread_result.data or {}).get("count") or 0),
        "messages": (active_session_unread_result.data or {}).get("messages", []),
        "buckets": (active_session_unread_result.data or {}).get("buckets", []),
        "mark_read_required": active_session_unread_result.status == "messages",
    }

    peer_breadcrumb = build_peer_runtime_breadcrumb(
        state_dir=state_dir,
        agent=agent,
        session_id=new_session,
        project=project_name,
        desktop_thread_id=desktop_thread_id,
        bootstrap_command=[sys.executable, *sys.argv],
        bootstrap_origin=bootstrap_origin,
        bootstrap_thread_id=bootstrap_thread_id,
        bootstrap_parent_thread_id=bootstrap_parent_thread_id,
        trusted_parent_session_id=activation.data.get("trusted_parent_session") if activation.ok else None,
        subagent_signals=subagent_signals,
    )
    bridge.record_session_runtime_metadata(
        agent=agent,
        session_id=new_session,
        project=project_name,
        desktop_thread_id=desktop_thread_id,
        bootstrap_thread_id=bootstrap_thread_id,
        bootstrap_parent_thread_id=bootstrap_parent_thread_id,
    )
    write_runtime_breadcrumb(peer_runtime_path_for_state_dir(state_dir, agent), peer_breadcrumb)

    handshake = None
    delays = [2, 4, 8]
    for attempt in range(handshake_retries):
        handshake = bridge.send_control_message(
            from_agent=agent,
            to_agent=peer_agent,
            control_type="HANDSHAKE",
            summary="%s handshake for %s" % (agent, project_name),
            body=json.dumps(
                {
                    "agent": agent,
                    "session_id": new_session,
                    "project": project_name,
                    "peer_session_hint": peer_session,
                },
                sort_keys=True,
            ),
            session_id=project_name,
            replace_existing_control=True,
        )
        if handshake.ok:
            break
        if attempt < handshake_retries - 1:
            time.sleep(delays[min(attempt, len(delays) - 1)])

    watcher = None
    watcher_process = None
    watcher_restart_check = None
    watcher_orphan_sweep = None
    if watcher_config is not None:
        watcher = configure_watcher(
            config_path=watcher_config,
            state_dir=state_dir,
            agent=agent,
            project=project_name,
            cwd=cwd,
            python_executable=sys.executable,
        )
        if start_watcher:
            if restart_watcher_if_code_changed:
                watcher_restart_check = restart_watcher_for_code_change(watcher_config, state_dir=state_dir)
            watcher_process = ensure_watcher(watcher_config, state_dir=state_dir)
            if watcher_restart_check is not None:
                watcher_process["code_restart_check"] = watcher_restart_check
                if (
                    watcher_restart_check.get("status") == "restart_required"
                    and watcher_process.get("status") == "started"
                ):
                    watcher_process["status"] = "restarted_code_changed"
            watcher_orphan_sweep = sweep_orphan_watchers(watcher_config, state_dir=state_dir, bridge=bridge)
        else:
            watcher_orphan_sweep = sweep_orphan_watchers(watcher_config, state_dir=state_dir, bridge=bridge)
            watcher_process = {
                "status": "not_started",
                "reason": "start_watcher_false",
            }

    claude_monitor_reminder = (
        _claude_monitor_reminder(state_dir=state_dir, session_id=new_session, project=project_name)
        if agent == "claude"
        else None
    )
    return {
        "identity": identity,
        "project": project_name,
        "agent": agent,
        "session_id": new_session,
        "peer_agent": peer_agent,
        "peer_session_hint": peer_session,
        "previous_session_id": previous_session_id,
        "drained_previous_messages": drained,
        "active_session_unread": active_session_unread,
        "activation": {
            "ok": activation.ok,
            "status": activation.status,
            "message": activation.message,
            "data": activation.data,
        },
        "handshake": None
        if handshake is None
        else {
            "ok": handshake.ok,
            "status": handshake.status,
            "message": handshake.message,
            "data": handshake.data,
            "attempts": attempt + 1,
        },
        "watcher": watcher,
        "watcher_process": watcher_process,
        "watcher_restart_check": watcher_restart_check,
        "watcher_orphan_sweep": watcher_orphan_sweep,
        "peer_runtime": peer_breadcrumb,
        "bootstrap_origin": bootstrap_origin,
        "detected_bootstrap_origin": detected_bootstrap_origin,
        "subagent_signals": subagent_signals,
        "retargeted_to_parent": retargeted_to_parent,
        "pairing_intent": resolved_pairing,
        "claude_monitor_reminder": claude_monitor_reminder,
    }


def main() -> None:
    parser = argparse.ArgumentParser(description="Bootstrap an agent-bridge session takeover")
    parser.add_argument("--bridge-root", help="Bridge root directory; preferred over --state-dir")
    parser.add_argument("--state-dir", help="Legacy bridge state directory")
    parser.add_argument("--agent", required=True, choices=("claude", "codex"))
    parser.add_argument("--cwd", help="Workspace path used for project identity derivation")
    parser.add_argument("--previous-session-id", help="Old same-agent session GUID to drain before takeover")
    parser.add_argument("--session-id", help="Optional new session GUID; default generates one")
    parser.add_argument("--project", help="Optional explicit rendezvous/project name")
    parser.add_argument(
        "--pairing-intent",
        help="How this parent chat should enter the bridge: ask_first, active_primary, or background",
    )
    parser.add_argument("--handshake-retries", type=int, default=3)
    parser.add_argument("--watcher-config", help="Optional watcher-config.json to update for this active session")
    parser.add_argument(
        "--no-start-watcher",
        action="store_true",
        help="Update watcher config without spawning the watcher daemon",
    )
    parser.add_argument(
        "--replace-trusted-parent",
        action="store_true",
        help="Manual repair only: allow this Codex bootstrap to replace a different trusted parent thread",
    )
    restart_group = parser.add_mutually_exclusive_group()
    restart_group.add_argument(
        "--restart-watcher-if-code-changed",
        dest="restart_watcher_if_code_changed",
        action="store_true",
        help="Compatibility no-op: stale wake/bootstrap watcher code is restarted by default",
    )
    restart_group.add_argument(
        "--no-restart-watcher-if-code-changed",
        dest="restart_watcher_if_code_changed",
        action="store_false",
        help="Debug only: keep an existing watcher running even if its wake/bootstrap code signature is stale",
    )
    parser.set_defaults(restart_watcher_if_code_changed=True)
    args = parser.parse_args()
    paths = resolve_bridge_paths(
        bridge_root=expand_path_arg(args.bridge_root) if args.bridge_root else None,
        state_dir=expand_path_arg(args.state_dir) if args.state_dir else None,
    )
    if args.bridge_root:
        ensure_bridge_root_manifest(paths, reason="bootstrap")

    result = bootstrap(
        state_dir=paths.state_dir,
        agent=args.agent,
        cwd=args.cwd,
        previous_session_id=args.previous_session_id,
        session_id=args.session_id,
        project=args.project,
        handshake_retries=args.handshake_retries,
        watcher_config=expand_path_arg(args.watcher_config) if args.watcher_config else (paths.watcher_config if args.bridge_root else None),
        start_watcher=not args.no_start_watcher,
        restart_watcher_if_code_changed=args.restart_watcher_if_code_changed,
        replace_trusted_parent=args.replace_trusted_parent,
        pairing_intent=args.pairing_intent,
    )
    reminder = result.get("claude_monitor_reminder") if result.get("agent") == "claude" and not result.get("refused") else None
    if isinstance(reminder, dict):
        print(reminder.get("banner", "MONITOR NOT YET ARMED"), file=sys.stderr)
        print(reminder.get("command_hint", ""), file=sys.stderr)
    print(json.dumps(result, indent=2, sort_keys=True))
    if result.get("refused"):
        sys.exit(int(result.get("exit_code") or 3))


if __name__ == "__main__":
    main()
