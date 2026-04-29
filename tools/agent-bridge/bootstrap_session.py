import argparse
import hashlib
import json
import os
import subprocess
import sys
import time
import uuid
from pathlib import Path
from typing import Any, Dict, List, Optional, Tuple

from agent_bridge import AgentBridge
from configure_watcher import PARENT_THREAD_ID_KEY, configure_watcher
from core.paths import ensure_bridge_root_manifest, resolve_bridge_paths
from core.processes import acquire_singleton_lease, build_lease, command_line_hash, is_process_alive, lease_status, read_lease, write_lease
from core.runtime import build_peer_runtime_breadcrumb, peer_runtime_path_for_state_dir, write_runtime_breadcrumb
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

    desktop_thread_id = _desktop_thread_id_for_bootstrap(agent, watcher_config)
    if retargeted_to_parent and bootstrap_parent_thread_id:
        desktop_thread_id = bootstrap_parent_thread_id
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
        else:
            watcher_process = {
                "status": "not_started",
                "reason": "start_watcher_false",
            }

    return {
        "identity": identity,
        "project": project_name,
        "agent": agent,
        "session_id": new_session,
        "peer_agent": peer_agent,
        "peer_session_hint": peer_session,
        "previous_session_id": previous_session_id,
        "drained_previous_messages": drained,
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
        "peer_runtime": peer_breadcrumb,
        "bootstrap_origin": bootstrap_origin,
        "detected_bootstrap_origin": detected_bootstrap_origin,
        "subagent_signals": subagent_signals,
        "retargeted_to_parent": retargeted_to_parent,
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
    parser.add_argument("--handshake-retries", type=int, default=3)
    parser.add_argument("--watcher-config", help="Optional watcher-config.json to update for this active session")
    parser.add_argument(
        "--no-start-watcher",
        action="store_true",
        help="Update watcher config without spawning the watcher daemon",
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
        bridge_root=Path(args.bridge_root) if args.bridge_root else None,
        state_dir=Path(args.state_dir) if args.state_dir else None,
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
        watcher_config=Path(args.watcher_config) if args.watcher_config else (paths.watcher_config if args.bridge_root else None),
        start_watcher=not args.no_start_watcher,
        restart_watcher_if_code_changed=args.restart_watcher_if_code_changed,
    )
    print(json.dumps(result, indent=2, sort_keys=True))
    if result.get("refused"):
        sys.exit(int(result.get("exit_code") or 3))


if __name__ == "__main__":
    main()
